"""The depot's securities (docs/phases/phase-6.md D165 (2), (3), step 4): the movements and
holdings a confirmed depot reading writes, the link to the cash line, the pair of a transfer
between own depots, and the reconciliation of a report against the movements.

Nothing here is called by the model or the job: a reading's records are applied by
services.proposals.apply_proposal when the user presses „Übernehmen“ (D132), and every row is
written confirmed in that one transaction. Rows are append-only (the triggers of migrations
0025 and 0027): a holding statement read again with other figures is a new row that
supersedes the old one (record_holding); a movement is never corrected here, a document
applied twice is refused (record_movement). Every read of holdings ignores superseded rows.
"""

import hmac
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field, ValidationError
from sqlalchemy import Connection, and_, func, insert, or_, select, update

from hausbuch import i18n
from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import document as document_table
from hausbuch.db.tables import holding_statement as holding_table
from hausbuch.db.tables import price as price_table
from hausbuch.db.tables import proposal as proposal_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.rules import depot as rules
from hausbuch.rules.errors import DEPOT_HOLDINGS_MISMATCH
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.api import InboxItem, InboxItemKind
from hausbuch.schemas.common import Money, NonNegativeMoney, Schema
from hausbuch.schemas.enums import (
    AccountKind,
    AllocationStatus,
    EntityType,
    ExtractionKind,
    ImportStatus,
    ProposalStatus,
)
from hausbuch.schemas.events import (
    AssetRecorded,
    EventType,
    HoldingCorrected,
    HoldingsChecked,
    MovementRecorded,
    MovementSuperseded,
    PriceRecorded,
)
from hausbuch.schemas.extraction import DepotReportExtraction
from hausbuch.schemas.household import (
    AssetKind,
    DepotAccountPositions,
    DepotDocumentStatus,
    DepotPosition,
    EtfOverview,
    HoldingSource,
    Isin,
    MovementKind,
    MovementSource,
    MovementStatus,
    NonNegativeQuantity,
    PriceSourceKind,
    Quantity,
)
from hausbuch.schemas.proposal import BookedDepotTrade, ExistingDepotAccount, PrintedDepot
from hausbuch.storage import BlobStore

from .context import CommandContext
from .documents import document_media_type, read_original
from .errors import ConflictError, NotFoundError, RuleViolationError
from .events import append_event
from .iban import protect_iban, protect_ref

# Codes of this service (frontend/src/api/errors.ts has their German text).
DEPOT_ACCOUNT_REQUIRED = "depot.account_required"  # the account picked is no depot
# Same reference and fund, or (no reference) same fund, day and amount.
DEPOT_MOVEMENT_EXISTS = "depot.movement_exists"
DEPOT_HOLDING_EXISTS = "depot.holding_exists"  # a report of that date is applied already
# D292 (2): a trade confirmation whose units or amount differ from the trade its cash statement
# line booked already; nothing is replaced and the suggestion names the difference.
DEPOT_STATEMENT_TRADE_DIFFERS = "depot.statement_trade_differs"
# D292 amended (c): the cash line carries a trade booked from it.
LINE_BOOKS_TRADE = "depot.line_books_trade"

# The cash line of a trade or a distribution is booked on the Valuta, a few bank days after
# the trade or the payment date the document names.
CASH_LINK_DAYS = 10
# A report's price per unit: value ÷ units, to the price table's 8 decimals, half up.
PRICE_PLACES = Decimal("0.00000001")

_SOURCES = {
    MovementKind.ACQUISITION: MovementSource.TRADE_CONFIRMATION,
    MovementKind.DISPOSAL: MovementSource.TRADE_CONFIRMATION,
    MovementKind.DISTRIBUTION: MovementSource.DISTRIBUTION_NOTE,
    MovementKind.INTERNAL_TRANSFER_IN: MovementSource.SECURITIES_TRANSFER,
    MovementKind.INTERNAL_TRANSFER_OUT: MovementSource.SECURITIES_TRANSFER,
}
_TRADE_KINDS = (MovementKind.ACQUISITION, MovementKind.DISPOSAL)
_TRANSFER_PAIR = {
    MovementKind.INTERNAL_TRANSFER_IN: MovementKind.INTERNAL_TRANSFER_OUT,
    MovementKind.INTERNAL_TRANSFER_OUT: MovementKind.INTERNAL_TRANSFER_IN,
}


class DepotMovementValues(Schema):
    """A movement record's values as the user applies them (the proposal's `movement:<n>`)."""

    account_id: UUID
    isin: Isin
    name: str | None = Field(default=None, min_length=1, max_length=200)
    kind: MovementKind
    quantity: Quantity
    eur_amount: Money | None = None
    fee_eur: NonNegativeMoney | None = None
    tax_withheld_eur: NonNegativeMoney | None = None
    occurred_on: date
    external_ref: str | None = Field(default=None, min_length=1, max_length=200)


class DepotHoldingValues(Schema):
    """A holding record's values as the user applies them (the proposal's `holding:<n>`)."""

    account_id: UUID
    isin: Isin
    name: str | None = Field(default=None, min_length=1, max_length=200)
    as_of: date
    declared_units: NonNegativeQuantity
    declared_value_eur: NonNegativeMoney | None = None


def depot_accounts(conn: Connection) -> list[ExistingDepotAccount]:
    """The workspace's depot accounts, oldest first: the choices of a depot record's account,
    each named by its label and its masked number."""
    a = account_table.c
    rows = conn.execute(
        select(a.id, a.label, a.external_ref_masked)
        .where(a.kind == AccountKind.DEPOT.value)
        .order_by(a.created_at, a.id)
    ).all()
    return [
        ExistingDepotAccount(
            id=r.id,
            label=f"{r.label} ({r.external_ref_masked})" if r.external_ref_masked else r.label,
        )
        for r in rows
    ]


def printed_depot_account(
    conn: Connection, store: BlobStore, document_id: UUID, *, iban_key: bytes
) -> tuple[PrintedDepot, UUID | None]:
    """What the document's PDF text layer says about its depot (14 Nov 2026, D165 (a)
    amended; outcomes split in the review of 15 Nov 2026), and the depot account it names on a
    MATCH. The labelled depot numbers and the IBANs it prints (rules.depot
    printed_depot_numbers, printed_ibans) are masked and HMAC'd as on store and compared with
    each depot account's external_ref_hmac and iban_hmac. Read by code only; nothing printed is
    kept or sent anywhere.

    A labelled depot number decides alone: held by exactly one depot account, a MATCH; held by
    none while every depot account has a number, NAMED_ELSEWHERE (the document is another
    depot's, such as the receiving depot of an Einbuchung not created yet); more than one
    labelled number (a financial report over several depots) is SEVERAL. The IBANs
    count only when no labelled number is printed, and only a MATCH comes of them. NOTHING when
    nothing tells: no depot account with a number or IBAN, no PDF, no text, nothing printed
    that a depot holds, or a printed number while a depot account without a number could be
    the one. UNREADABLE when the original could not be read just now (a pre-selection is never
    worth a failed reading)."""
    from hausbuch.services.bank.card_pdf import pdf_pages

    a = account_table.c
    depots = conn.execute(
        select(a.id, a.external_ref_hmac, a.iban_hmac).where(a.kind == AccountKind.DEPOT.value)
    ).all()
    if not any(d.external_ref_hmac or d.iban_hmac for d in depots):
        return PrintedDepot.NOTHING, None
    if document_media_type(conn, document_id) != "application/pdf":
        return PrintedDepot.NOTHING, None
    try:
        pages = pdf_pages(read_original(conn, store, document_id))
    except Exception:  # noqa: BLE001 - the reading goes on without a pre-selection
        return PrintedDepot.UNREADABLE, None
    if not pages:
        return PrintedDepot.NOTHING, None
    text = "\n".join(pages)
    numbers = rules.printed_depot_numbers(text)
    if len(numbers) > 1:
        return PrintedDepot.SEVERAL, None
    if numbers:
        printed = protect_ref(next(iter(numbers)), iban_key).hmac
        matched = [
            d.id
            for d in depots
            if d.external_ref_hmac is not None and hmac.compare_digest(d.external_ref_hmac, printed)
        ]
        if len(matched) == 1:
            return PrintedDepot.MATCH, matched[0]
        if not matched and all(d.external_ref_hmac is not None for d in depots):
            return PrintedDepot.NAMED_ELSEWHERE, None
        return PrintedDepot.NOTHING, None
    ibans = [protect_iban(i, iban_key).hmac for i in rules.printed_ibans(text)]
    matched = [
        d.id
        for d in depots
        if d.iban_hmac is not None and any(hmac.compare_digest(d.iban_hmac, p) for p in ibans)
    ]
    if len(matched) == 1:
        return PrintedDepot.MATCH, matched[0]
    return PrintedDepot.NOTHING, None


def _require_depot(conn: Connection, account_id: UUID, *, lock: bool = False) -> None:
    """Refused unless the account is a depot. `lock` takes the account row's lock (FOR NO KEY
    UPDATE) first, so two bookings on one depot run one after the other (review of 15 Nov
    2026: two at once each found the trade unbooked and both wrote it)."""
    query = select(account_table.c.kind).where(account_table.c.id == account_id)
    if lock:
        query = query.with_for_update(key_share=True)
    kind = conn.execute(query).scalar_one_or_none()
    if kind != AccountKind.DEPOT.value:
        raise RuleViolationError([DEPOT_ACCOUNT_REQUIRED], "a depot record names a depot account")


def lock_depots(conn: Connection, account_ids: Iterable[UUID]) -> None:
    """The account rows' locks, in the order of their ids (review of 15 Nov 2026): an apply
    that books on two depots (a transfer between own depots) takes both before its first
    movement, so two such applies never wait on each other crosswise. An id no account has
    is passed over; the record's own check refuses it."""
    a = account_table.c
    for account_id in sorted(set(account_ids)):
        conn.execute(select(a.id).where(a.id == account_id).with_for_update(key_share=True))


def asset_for_isin(ctx: CommandContext, isin: str, name: str | None) -> UUID:
    """The asset of an ISIN, created (kind etf, named as the document names it) on first use."""
    found = ctx.conn.execute(
        select(asset_table.c.id).where(asset_table.c.isin == isin).order_by(asset_table.c.id)
    ).first()
    if found is not None:
        return found.id
    asset_id = ctx.conn.execute(
        insert(asset_table)
        .values(
            workspace_id=ctx.workspace_id,
            kind=AssetKind.ETF.value,
            isin=isin,
            name=name or isin,
            created_at=ctx.now,
            updated_at=ctx.now,
        )
        .returning(asset_table.c.id)
    ).scalar_one()
    append_event(
        ctx,
        EventType.ASSET_CREATED,
        asset_id,
        AssetRecorded(kind=AssetKind.ETF, isin=isin, name=name or isin),
    )
    return asset_id


def _at(day: date) -> datetime:
    return datetime.combine(day, time(), UTC)


def _cash_amount(values: DepotMovementValues) -> Decimal | None:
    """What the movement's cash line books: a trade's net, a distribution's gross − tax."""
    if values.eur_amount is None:
        return None
    if values.kind is MovementKind.DISTRIBUTION:
        return values.eur_amount - (values.tax_withheld_eur or Decimal(0))
    return values.eur_amount


def _cash_line(conn: Connection, values: DepotMovementValues) -> UUID | None:
    """The one line of the depot's own statement this movement's cash went over: the same
    amount within CASH_LINK_DAYS after the document's date, not yet linked by a movement; a
    line naming the movement's reference wins. None when none or several fit (the card then
    shows the movement without its cash line)."""
    amount = _cash_amount(values)
    if amount is None or amount == 0:
        return None
    t, m = transaction_table.c, movement_table.c
    linked = select(m.transaction_id).where(
        m.transaction_id.is_not(None), m.superseded_by.is_(None)
    )
    day = func.coalesce(t.value_date, t.booking_date)
    rows = conn.execute(
        select(t.id, t.purpose, t.booking_text).where(
            t.account_id == values.account_id,
            t.amount == amount,
            t.excluded_as_duplicate_of.is_(None),
            day >= values.occurred_on,
            day <= values.occurred_on + timedelta(days=CASH_LINK_DAYS),
            t.id.not_in(linked),
        )
    ).all()
    if values.external_ref:
        named = [
            r for r in rows if values.external_ref in f"{r.purpose or ''} {r.booking_text or ''}"
        ]
        if len(named) == 1:
            return named[0].id
    return rows[0].id if len(rows) == 1 else None


def record_movement(
    ctx: CommandContext,
    values: DepotMovementValues,
    document_id: UUID,
    *,
    source: MovementSource | None = None,
) -> UUID:
    """One confirmed movement read from a depot document, with its cash line when exactly one
    fits. Refused when the account is no depot, and when the document was applied already: a
    current movement of the account carries the same reference, fund and kind (an Einbuchung
    names one reference for all its positions, so the fund tells them apart; 14 Nov 2026), or,
    for a movement without a reference (a distribution note names none), the same fund, kind,
    day and amount.

    D165 amended, 15 Nov 2026: `source` REPORT books a trade of a report's Umsätze list
    (document_id the report). Its reference is the trade's identity, so a current movement of
    the account with that reference is the trade booked already, from its confirmation or an
    earlier report: its id is returned and nothing is written (a later report repeats the
    earlier trades, and a suggestion built before they were booked must still apply). The
    other way round, a trade confirmation whose trade a report booked already writes nothing
    either and returns that movement: the confirmation is its evidence, the report's units and
    amount stay. A second confirmation of a trade a confirmation booked is still refused.

    Every check runs under the account row's lock (review of 15 Nov 2026), so a second
    booking of the same trade waits for the first and then finds it."""
    _require_depot(ctx.conn, values.account_id, lock=True)
    m = movement_table.c
    current = [m.account_id == values.account_id, m.superseded_by.is_(None)]
    if source is MovementSource.REPORT:
        if values.external_ref is None:
            raise ValueError("a trade booked from a report carries its reference")
        booked = ctx.conn.execute(
            select(m.id)
            .where(*current, m.external_ref == values.external_ref)
            .order_by(m.created_at, m.id)
        ).first()
        if booked is not None:
            return booked.id
    known = ctx.conn.execute(
        select(asset_table.c.id).where(asset_table.c.isin == values.isin)
    ).all()
    same_fund = m.asset_id.in_([r.id for r in known])
    if values.kind in _TRADE_KINDS:
        statement = _statement_twins(ctx.conn, values, same_fund)
        if source is MovementSource.REPORT:
            # D292 (2): the trade its cash statement line booked already; the report is its
            # evidence, the statement's units and amount stay (nothing written).
            agreeing = [r for r in statement if r.quantity == values.quantity]
            if agreeing:
                return agreeing[0].id
        elif source is None or source is MovementSource.TRADE_CONFIRMATION:
            if statement:
                return _supersede_statement_trade(ctx, values, document_id, statement)
    if values.external_ref is not None:
        same = [m.external_ref == values.external_ref, same_fund]
    else:
        same = [
            same_fund,
            m.occurred_at >= _at(values.occurred_on),
            m.occurred_at < _at(values.occurred_on + timedelta(days=1)),
            m.eur_amount.is_(None)
            if values.eur_amount is None
            else m.eur_amount == values.eur_amount,
        ]
    twin = ctx.conn.execute(
        select(m.id, m.source).where(*current, m.kind == values.kind.value, *same)
    ).first()
    if twin is not None:
        if values.kind in _TRADE_KINDS and twin.source == MovementSource.REPORT.value:
            return twin.id
        raise RuleViolationError(
            [DEPOT_MOVEMENT_EXISTS], "this movement is booked on the account already"
        )
    asset_id = asset_for_isin(ctx, values.isin, values.name)
    source = source or _SOURCES.get(values.kind, MovementSource.USER)
    movement_id = ctx.conn.execute(
        insert(movement_table)
        .values(
            workspace_id=ctx.workspace_id,
            account_id=values.account_id,
            asset_id=asset_id,
            kind=values.kind.value,
            quantity=values.quantity,
            eur_amount=values.eur_amount,
            fee_eur=values.fee_eur,
            tax_withheld_eur=values.tax_withheld_eur,
            occurred_at=_at(values.occurred_on),
            external_ref=values.external_ref,
            source=source.value,
            document_id=document_id,
            transaction_id=_cash_line(ctx.conn, values),
            status=MovementStatus.CONFIRMED.value,
            created_at=ctx.now,
        )
        .returning(m.id)
    ).scalar_one()
    append_event(
        ctx,
        EventType.MOVEMENT_CONFIRMED,
        movement_id,
        MovementRecorded(
            account_id=values.account_id,
            asset_id=asset_id,
            kind=values.kind,
            source=source,
            document_id=document_id,
        ),
    )
    return movement_id


def _statement_twins(conn: Connection, values: DepotMovementValues, same_fund: Any) -> list[Any]:
    """D292 (2): the current movements read from a cash statement line that may be this
    trade's: the same account, fund and kind, and either the same reference or the line's
    value date on the trade day or up to rules.depot.STATEMENT_TRADE_DAYS after it. Those with
    the same reference first, then by day."""
    m = movement_table.c
    rows = conn.execute(
        select(m.id, m.quantity, m.eur_amount, m.external_ref, m.occurred_at, m.transaction_id)
        .where(
            m.account_id == values.account_id,
            m.superseded_by.is_(None),
            m.source == MovementSource.DEPOT_STATEMENT.value,
            m.kind == values.kind.value,
            same_fund,
        )
        .order_by(m.occurred_at, m.id)
    ).all()
    named = [
        r for r in rows if values.external_ref is not None and r.external_ref == values.external_ref
    ]
    near = [
        r
        for r in rows
        if r not in named and rules.statement_window(values.occurred_on, r.occurred_at.date())
    ]
    return named + near


def _supersede_statement_trade(
    ctx: CommandContext, values: DepotMovementValues, document_id: UUID, twins: list[Any]
) -> UUID:
    """D292 (2): a trade confirmation for a trade its cash statement line booked already. The
    first of `twins` whose units and amount agree with the confirmation is superseded by the
    confirmation's own movement (its fee and tax too), the cash line staying linked: the new
    row is written first with the line, then the old one points at it (movement.superseded).
    When none agrees, nothing is written and depot.statement_trade_differs is raised, so the
    confirmation's suggestion waits with the difference named (rules.prefill)."""
    agreeing = [
        r for r in twins if r.quantity == values.quantity and r.eur_amount == values.eur_amount
    ]
    if not agreeing:
        raise RuleViolationError(
            [DEPOT_STATEMENT_TRADE_DIFFERS],
            "the trade confirmation's units or amount differ from the statement line's",
        )
    old = agreeing[0]
    m = movement_table.c
    asset_id = asset_for_isin(ctx, values.isin, values.name)
    source = MovementSource.TRADE_CONFIRMATION
    movement_id = ctx.conn.execute(
        insert(movement_table)
        .values(
            workspace_id=ctx.workspace_id,
            account_id=values.account_id,
            asset_id=asset_id,
            kind=values.kind.value,
            quantity=values.quantity,
            eur_amount=values.eur_amount,
            fee_eur=values.fee_eur,
            tax_withheld_eur=values.tax_withheld_eur,
            occurred_at=_at(values.occurred_on),
            external_ref=values.external_ref,
            source=source.value,
            document_id=document_id,
            transaction_id=old.transaction_id,
            status=MovementStatus.CONFIRMED.value,
            created_at=ctx.now,
        )
        .returning(m.id)
    ).scalar_one()
    ctx.conn.execute(update(movement_table).where(m.id == old.id).values(superseded_by=movement_id))
    append_event(
        ctx,
        EventType.MOVEMENT_SUPERSEDED,
        old.id,
        MovementSuperseded(superseded_by=movement_id, document_id=document_id),
    )
    append_event(
        ctx,
        EventType.MOVEMENT_CONFIRMED,
        movement_id,
        MovementRecorded(
            account_id=values.account_id,
            asset_id=asset_id,
            kind=values.kind,
            source=source,
            document_id=document_id,
        ),
    )
    return movement_id


def booked_trades(conn: Connection) -> list[BookedDepotTrade]:
    """Every current purchase and sale with a reference on a depot account (D165 amended,
    15 Nov 2026): what a report's or a trade confirmation's pre-fill checks its trades
    against."""
    m, a, acc = movement_table.c, asset_table.c, account_table.c
    rows = conn.execute(
        select(
            m.account_id,
            a.isin,
            m.external_ref,
            m.kind,
            m.quantity,
            m.eur_amount,
            m.source,
            m.occurred_at,
        )
        .join(asset_table, a.id == m.asset_id)
        .join(account_table, acc.id == m.account_id)
        .where(
            acc.kind == AccountKind.DEPOT.value,
            m.superseded_by.is_(None),
            or_(
                m.external_ref.is_not(None),
                m.source == MovementSource.DEPOT_STATEMENT.value,
            ),
            m.kind.in_([k.value for k in _TRADE_KINDS]),
            a.isin.is_not(None),
        )
        .order_by(m.created_at, m.id)
    ).all()
    return [
        BookedDepotTrade(
            account_id=r.account_id,
            isin=r.isin,
            external_ref=r.external_ref,
            kind=MovementKind(r.kind),
            quantity=r.quantity,
            eur_amount=r.eur_amount,
            source=MovementSource(r.source),
            occurred_on=r.occurred_at.date(),
        )
        for r in rows
    ]


def transfer_pair(conn: Connection, movement_id: UUID) -> UUID | None:
    """The other side of a transfer between own depots (decision C, 13 Nov 2026): the current
    movement of the opposite kind on another account with the same reference, asset and
    units. Units moved this way keep their acquisition date; neither side is a disposal."""
    m = movement_table.c
    row = conn.execute(select(movement_table).where(m.id == movement_id)).one()
    pair_kind = _TRANSFER_PAIR.get(MovementKind(row.kind))
    if pair_kind is None or row.external_ref is None:
        return None
    pair = conn.execute(
        select(m.id).where(
            m.kind == pair_kind.value,
            m.external_ref == row.external_ref,
            m.asset_id == row.asset_id,
            m.account_id != row.account_id,
            m.quantity == -row.quantity,
            m.superseded_by.is_(None),
        )
    ).all()
    return pair[0].id if len(pair) == 1 else None


def record_holding(ctx: CommandContext, values: DepotHoldingValues, document_id: UUID) -> UUID:
    """One position of a report, reconciled against the account's confirmed movements up to
    its date (rules.depot.reconcile_holdings); a reconciled position with units stores the
    report's price (value ÷ units) as price(source = statement), when the value is above 0.
    A report's position of the asset on that account and date stored already is a twin: with
    the same units and value it is the stored row when it came from this same document (a
    report read again with „Neu auslesen“, where only some positions changed, so the
    unchanged ones need no „überspringen“) and refused when it came from another one (a second
    copy); with other figures the new row supersedes it (a corrected reading;
    account.holding_corrected)."""
    _require_depot(ctx.conn, values.account_id)
    asset_id = asset_for_isin(ctx, values.isin, values.name)
    h = holding_table.c
    twin = ctx.conn.execute(
        select(*_POSITION_COLUMNS).where(
            h.account_id == values.account_id,
            h.asset_id == asset_id,
            h.as_of == values.as_of,
            h.source == HoldingSource.REPORT.value,
            h.superseded_by.is_(None),
        )
    ).first()
    if twin is not None and (twin.declared_units, twin.declared_value_eur) == (
        values.declared_units,
        values.declared_value_eur,
    ):
        if twin.document_id == document_id:
            # „Neu auslesen“: a position that did not agree when it came in and agrees now
            # (its trade confirmation applied since) gets the price it never stored.
            _price_position(ctx, twin, _movements(ctx.conn, values.account_id, asset_id))
            return twin.id
        raise RuleViolationError(
            [DEPOT_HOLDING_EXISTS], "this report's position is stored on the account already"
        )
    [check] = rules.reconcile_holdings(
        values.as_of,
        {asset_id: values.declared_units},
        _movements(ctx.conn, values.account_id, asset_id),
    )
    holding_id = _insert_holding(
        ctx,
        values.account_id,
        asset_id,
        check,
        values.as_of,
        values.declared_value_eur,
        document_id,
    )
    if twin is not None:
        ctx.conn.execute(
            update(holding_table).where(h.id == twin.id).values(superseded_by=holding_id)
        )
        append_event(
            ctx,
            EventType.ACCOUNT_HOLDING_CORRECTED,
            values.account_id,
            HoldingCorrected(
                holding_statement_id=twin.id,
                superseded_by=holding_id,
                asset_id=asset_id,
                as_of=values.as_of,
                document_id=document_id,
            ),
            effective_date=values.as_of,
        )
    # A value of 0 for units held is no price (price_eur > 0); the position stays as stored.
    if check.price_stored and values.declared_value_eur:
        _store_price(
            ctx,
            asset_id,
            values.as_of,
            values.declared_units,
            values.declared_value_eur,
            document_id,
        )
    return holding_id


def _movements(
    conn: Connection, account_id: UUID, asset_id: UUID | None = None
) -> list[rules.UnitsMovement]:
    m = movement_table.c
    query = select(m.asset_id, m.quantity, m.occurred_at, m.status, m.external_ref).where(
        m.account_id == account_id, m.superseded_by.is_(None)
    )
    if asset_id is not None:
        query = query.where(m.asset_id == asset_id)
    return [
        rules.UnitsMovement(
            asset=r.asset_id,
            quantity=r.quantity,
            occurred_on=r.occurred_at.date(),
            status=MovementStatus(r.status),
            external_ref=r.external_ref,
        )
        for r in conn.execute(query).all()
    ]


class MovementReads:
    """Each depot account's movements as `_movements(conn, account_id)` reads them, read once
    per account within one read call (the Anlagen overview's guard and the latest report
    check both read them, and the net worth reads both for two days). Lives only as long as
    the call that made it; nothing is kept between requests."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._found: dict[UUID, list[rules.UnitsMovement]] = {}

    def __call__(self, account_id: UUID) -> list[rules.UnitsMovement]:
        if account_id not in self._found:
            self._found[account_id] = _movements(self._conn, account_id)
        return self._found[account_id]


def _insert_holding(
    ctx: CommandContext,
    account_id: UUID,
    asset_id: UUID,
    check: rules.PositionCheck,
    as_of: date,
    value_eur: Decimal | None,
    document_id: UUID,
) -> UUID:
    return ctx.conn.execute(
        insert(holding_table)
        .values(
            workspace_id=ctx.workspace_id,
            account_id=account_id,
            asset_id=asset_id,
            as_of=as_of,
            declared_units=check.declared_units,
            declared_value_eur=value_eur,
            source=HoldingSource.REPORT.value,
            document_id=document_id,
            reconciled=check.reconciled,
            difference_units=check.difference_units,
            created_at=ctx.now,
        )
        .returning(holding_table.c.id)
    ).scalar_one()


def _store_price(
    ctx: CommandContext,
    asset_id: UUID,
    as_of: date,
    units: Decimal,
    value_eur: Decimal,
    document_id: UUID | None,
) -> None:
    price = (value_eur / units).quantize(PRICE_PLACES, rounding=ROUND_HALF_UP)
    price_id = ctx.conn.execute(
        insert(price_table)
        .values(
            workspace_id=ctx.workspace_id,
            asset_id=asset_id,
            on=as_of,
            price_eur=price,
            source=PriceSourceKind.STATEMENT.value,
            fetched_at=ctx.now,
            document_id=document_id,
            created_at=ctx.now,
        )
        .returning(price_table.c.id)
    ).scalar_one()
    append_event(
        ctx,
        EventType.ASSET_PRICE_RECORDED,
        asset_id,
        PriceRecorded(
            price_id=price_id,
            on=as_of,
            source=PriceSourceKind.STATEMENT,
            document_id=document_id,
        ),
    )


_hc = holding_table.c
# A stored report position as the later pricing reads it.
_POSITION_COLUMNS = (
    _hc.id,
    _hc.account_id,
    _hc.asset_id,
    _hc.as_of,
    _hc.declared_units,
    _hc.declared_value_eur,
    _hc.document_id,
    _hc.created_at,
)


def _price_position(
    ctx: CommandContext, row: Any, movements: Sequence[rules.UnitsMovement]
) -> bool:
    """The report's price for one stored position (value ÷ units, as record_holding stores
    it) when the position agrees with the confirmed movements now and no price of this
    reading is stored yet: one from the same document, asset and day, fetched at or after the
    position's row was written (the price _positions shows; an older one belongs to a
    superseded reading). True when it stored one. The row's `reconciled` flag is not touched:
    it keeps what the check said when the report came in."""
    if not row.declared_units or not row.declared_value_eur:
        return False
    [check] = rules.reconcile_holdings(
        row.as_of,
        {row.asset_id: row.declared_units},
        [m for m in movements if m.asset == row.asset_id],
    )
    if not check.price_stored:
        return False
    p = price_table.c
    stored = ctx.conn.execute(
        select(p.id)
        .where(
            p.asset_id == row.asset_id,
            p.on == row.as_of,
            p.source == PriceSourceKind.STATEMENT.value,
            p.document_id.is_not_distinct_from(row.document_id),
            p.fetched_at >= row.created_at,
        )
        .limit(1)
    ).first()
    if stored is not None:
        return False
    _store_price(
        ctx, row.asset_id, row.as_of, row.declared_units, row.declared_value_eur, row.document_id
    )
    return True


def store_report_prices(ctx: CommandContext, account_id: UUID) -> int:
    """D167 amended (c), 15 Nov 2026: the price of every current report position of a depot
    account that agrees with its confirmed movements now and has none stored yet. A report
    usually arrives before some of its trade confirmations, so its position did not agree when
    it was taken over and stored no price; this runs after a depot movement is confirmed (a
    trade, distribution or transfer note applied through the pre-filled form) and from
    `hausbuch store-report-prices`. Idempotent. Returns how many prices it stored."""
    h = holding_table.c
    rows = ctx.conn.execute(
        select(*_POSITION_COLUMNS)
        .where(
            h.account_id == account_id,
            h.source == HoldingSource.REPORT.value,
            h.superseded_by.is_(None),
            h.declared_units > 0,
            h.declared_value_eur > 0,
        )
        .order_by(h.as_of, h.created_at, h.id)
    ).all()
    if not rows:
        return 0
    movements = _movements(ctx.conn, account_id)
    return sum(1 for row in rows if _price_position(ctx, row, movements))


def store_report_prices_after(ctx: CommandContext, movement_ids: Sequence[UUID]) -> int:
    """store_report_prices for every depot account one of these movements was written on."""
    if not movement_ids:
        return 0
    m = movement_table.c
    accounts = (
        ctx.conn.execute(select(m.account_id).where(m.id.in_(list(movement_ids))).distinct())
        .scalars()
        .all()
    )
    return sum(store_report_prices(ctx, a) for a in sorted(accounts, key=str))


def store_all_report_prices(ctx: CommandContext) -> int:
    """`hausbuch store-report-prices`: store_report_prices over every depot account of the
    workspace, oldest first."""
    return sum(store_report_prices(ctx, a.id) for a in depot_accounts(ctx.conn))


class ReportChecked(Schema):
    account_id: UUID
    as_of: date
    holding_ids: list[UUID]
    mismatched: int


def finish_report(ctx: CommandContext, document_id: UUID) -> list[ReportChecked]:
    """After a report's positions are applied: per account and date it names, one
    account.holdings_reconciled or account.holdings_mismatch event over every report
    position stored for that account and date, from this document or an earlier one
    (DEPOT_HOLDINGS_MISMATCH is the inbox card's code). An asset the confirmed movements
    still hold that no position lists is a position of 0 (§14.1, "0 by omission") and counts
    as a mismatch, but is not stored: it is recomputed on every read (holdings_inbox_items,
    _positions), so a position added later from a second reading of the report is not
    refused as a twin of it (review finding F2)."""
    h = holding_table.c
    report = HoldingSource.REPORT.value
    dates = ctx.conn.execute(
        select(h.account_id, h.as_of)
        .where(h.document_id == document_id, h.source == report, h.superseded_by.is_(None))
        .distinct()
    ).all()
    results = []
    for account_id, as_of in sorted(dates, key=str):
        mine = ctx.conn.execute(
            select(h.id, h.asset_id, h.declared_units)
            .where(
                h.account_id == account_id,
                h.as_of == as_of,
                h.source == report,
                h.superseded_by.is_(None),
            )
            .order_by(h.created_at, h.id)
        ).all()
        ids = [r.id for r in mine]
        checks = rules.reconcile_holdings(
            as_of, {r.asset_id: r.declared_units for r in mine}, _movements(ctx.conn, account_id)
        )
        mismatched = sum(1 for c in checks if not c.reconciled)
        append_event(
            ctx,
            EventType.ACCOUNT_HOLDINGS_MISMATCH
            if mismatched
            else EventType.ACCOUNT_HOLDINGS_RECONCILED,
            account_id,
            HoldingsChecked(
                holding_statement_ids=ids,
                as_of=as_of,
                source=HoldingSource.REPORT,
                document_id=document_id,
                assets_mismatched=mismatched,
            ),
        )
        results.append(
            ReportChecked(
                account_id=account_id, as_of=as_of, holding_ids=ids, mismatched=mismatched
            )
        )
    return results


def _units(units: Decimal) -> str:
    """4.8000000000 → de "4,8", en "4.8"; 0 → "0"."""
    text = f"{units.normalize():f}" if units else "0"
    return text if i18n.english() else text.replace(".", ",")


def _german_units(units: Decimal) -> str:
    """Always German (net_worth's stored texts): 4.8000000000 → "4,8"."""
    with i18n.use(i18n.DE):
        return _units(units)


def _asset_names(conn: Connection, ids: list[Any]) -> dict[UUID, str]:
    a = asset_table.c
    return dict(conn.execute(select(a.id, a.name).where(a.id.in_(ids))).tuples().all())


def _confirmed_report(conn: Connection, document_id: UUID) -> DepotReportExtraction | None:
    """The reading of the report's most recently applied depot-report suggestion (review of
    15 Nov 2026): what the person confirmed, never a re-read they rejected or one still
    waiting (documents.current_extraction). None when no suggestion of it was applied."""
    from .documents import extraction_by_id  # it imports the job queue

    p = proposal_table.c
    extraction_id = conn.execute(
        select(p.extraction_id)
        .where(
            p.document_id == document_id,
            p.kind == ExtractionKind.DEPOT_REPORT.value,
            p.status == ProposalStatus.APPLIED.value,
            p.extraction_id.is_not(None),
        )
        .order_by(p.decided_at.desc().nulls_last(), p.created_at.desc(), p.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    extraction = None if extraction_id is None else extraction_by_id(conn, extraction_id)
    fields = None if extraction is None else extraction.fields
    return fields if isinstance(fields, DepotReportExtraction) else None


def _report_reading(
    conn: Connection, document_id: UUID | None, as_of: date
) -> tuple[list[rules.ProposedTrade], dict[str, str]]:
    """The trades a report's confirmed reading lists (rules.depot.propose's parse), and the
    fund names its positions give by ISIN; empty when it has no such reading."""
    fields = None if document_id is None else _confirmed_report(conn, document_id)
    if fields is None:
        return [], {}
    proposal = rules.propose(
        "depot_report",
        {
            "report_date": as_of.isoformat(),
            "positions": fields.positions.value or [],
            "trades": fields.trades.value or [],
        },
    )
    if proposal is None:
        return [], {}
    names = {h.isin: h.name for h in proposal.holdings if h.name}
    return list(proposal.trades), names


@dataclass(frozen=True)
class ReportTrade:
    """A trade of a report's Umsätze list no movement of the depot carries (D165 amended,
    15 Nov 2026), with the report that lists it and the movement it books."""

    trade: rules.ProposedTrade
    document_id: UUID
    values: DepotMovementValues


@dataclass(frozen=True)
class UnreadableTrade:
    """A row of a report's Umsätze list that is not booked because it cannot be read as a
    trade (review of 15 Nov 2026): named by its fund and day only."""

    name: str
    trade_date: date


@dataclass(frozen=True)
class ReportTradesPlan:
    """What „Umsätze aus dem Bericht buchen“ books on one depot and what it leaves out, one
    computation for the button and the holdings card."""

    trades: list[ReportTrade]
    # Funds that disagree with the latest report and would still disagree with their
    # trades booked: none of their trades is booked.
    left_out: list[str]
    unreadable: list[UnreadableTrade]


def _trade_values(
    account_id: UUID, trade: rules.ProposedTrade, name: str | None
) -> DepotMovementValues | None:
    """A report's row as the movement it books, None when it is not readable: its units and
    amount contradict each other (rules.depot.trade_signs_agree) or the values fail
    DepotMovementValues (an amount with more than two decimals, a name too long). Never
    rounded or repaired."""
    if not rules.trade_signs_agree(trade):
        return None
    movement = rules.report_trade_movement(trade, name)
    try:
        return DepotMovementValues(
            account_id=account_id,
            isin=movement.isin,
            name=movement.name,
            kind=movement.kind,
            quantity=movement.quantity,
            eur_amount=movement.eur_amount,
            occurred_on=movement.occurred_on,
            external_ref=movement.external_ref,
        )
    except ValidationError:
        return None


def report_trades_plan(conn: Connection, account_id: UUID) -> ReportTradesPlan:
    """D165 amended, 15 Nov 2026, with the review's fixes: the trades „Umsätze aus dem Bericht
    buchen“ books and the holdings card counts, one computation for both. Every current
    report of the depot account (source = report, not superseded), newest first, gives the
    trades of its confirmed reading (_confirmed_report) up to its own date
    (rules.depot.bookable_trades); a reference counts once (a later report repeats the earlier
    trades, the newest report's row wins) and none that a current movement of the account
    carries. A row that is not readable (_trade_values) is left out and named. Then per fund,
    all or nothing (rules.depot.fund_agreement): its trades are booked only when, with them
    booked, the fund's units on this depot equal the units the depot's latest report states
    on its date (the current holding rows, 0 for a fund it does not list); otherwise none is,
    and the fund is named when it disagrees with the report now (a fund that agrees already
    needs nothing). That also leaves out a trade booked under another reference, a corrected
    reference and a doubled reading. Ordered by trade date, then reference."""
    h, m, a = holding_table.c, movement_table.c, asset_table.c
    rows = conn.execute(
        select(h.document_id, h.asset_id, h.as_of, h.declared_units, h.created_at)
        .where(
            h.account_id == account_id,
            h.source == HoldingSource.REPORT.value,
            h.superseded_by.is_(None),
        )
        .order_by(h.as_of.desc(), h.created_at.desc(), h.id)
    ).all()
    if not rows:
        return ReportTradesPlan([], [], [])
    seen = set(
        conn.execute(
            select(m.external_ref).where(
                m.account_id == account_id,
                m.superseded_by.is_(None),
                m.external_ref.is_not(None),
            )
        )
        .scalars()
        .all()
    )
    statement = _statement_trades(conn, account_id)
    reports: list[tuple[UUID, date]] = []
    for r in rows:
        if (r.document_id, r.as_of) not in reports:
            reports.append((r.document_id, r.as_of))
    candidates: list[ReportTrade] = []
    unreadable: list[UnreadableTrade] = []
    names: dict[str, str] = {}
    for document_id, as_of in reports:
        trades, report_names = _report_reading(conn, document_id, as_of)
        for isin, name in report_names.items():
            names.setdefault(isin, name)
        for trade in rules.bookable_trades(trades, as_of, seen):
            seen.add(trade.reference.strip())
            if (found := rules.statement_booked(trade, statement)) is not None:
                statement.remove(found)  # D292 (2): booked from its cash statement line
                continue
            values = _trade_values(account_id, trade, report_names.get(trade.isin))
            if values is None:
                unreadable.append(
                    UnreadableTrade(_fund_name(conn, names, trade.isin), trade.trade_date)
                )
            else:
                candidates.append(ReportTrade(trade, document_id, values))
    if not candidates:
        return ReportTradesPlan([], [], _ordered(unreadable))
    latest_on = rows[0].as_of
    movements = _movements(conn, account_id)
    assets = {r.asset_id for r in rows} | {mv.asset for mv in movements}
    isin_of = dict(
        conn.execute(select(a.id, a.isin).where(a.id.in_(assets), a.isin.is_not(None)))
        .tuples()
        .all()
    )
    declared: dict[Any, Decimal] = {}
    for r in rows:
        if r.as_of == latest_on:
            key = isin_of.get(r.asset_id, r.asset_id)
            declared[key] = declared.get(key, Decimal(0)) + r.declared_units
    agreement = rules.fund_agreement(
        latest_on,
        declared,
        [mv.model_copy(update={"asset": isin_of.get(mv.asset, mv.asset)}) for mv in movements],
        [c.trade for c in candidates],
    )
    booked = [c for c in candidates if agreement[c.trade.isin].after]
    left_out = sorted(
        {
            _fund_name(conn, names, isin)
            for isin, fund in agreement.items()
            if not fund.after and not fund.now
        }
    )
    return ReportTradesPlan(
        sorted(booked, key=lambda t: (t.trade.trade_date, t.trade.reference)),
        left_out,
        _ordered(unreadable),
    )


def _statement_trades(conn: Connection, account_id: UUID) -> list[tuple[str, Decimal, date]]:
    """D292 (2): (ISIN, units, value date) of the depot's current trades read from its cash
    statement lines, by day: what a report's trade is checked against before it is booked."""
    m, a = movement_table.c, asset_table.c
    rows = conn.execute(
        select(a.isin, m.quantity, m.occurred_at)
        .join(asset_table, a.id == m.asset_id)
        .where(
            m.account_id == account_id,
            m.superseded_by.is_(None),
            m.source == MovementSource.DEPOT_STATEMENT.value,
            a.isin.is_not(None),
        )
        .order_by(m.occurred_at, m.id)
    ).all()
    return [(r.isin, r.quantity, r.occurred_at.date()) for r in rows]


def _fund_name(conn: Connection, names: dict[str, str], isin: str) -> str:
    """The fund as the report names it, else as the asset is named, else „Wertpapier“."""
    if isin in names:
        return names[isin]
    a = asset_table.c
    found = conn.execute(
        select(a.name).where(a.isin == isin, a.name.is_not(None)).order_by(a.id)
    ).scalar()
    return found or "Wertpapier"


def _ordered(rows: list[UnreadableTrade]) -> list[UnreadableTrade]:
    return sorted(rows, key=lambda r: (r.trade_date, r.name))


class ReportTradesBooked(Schema):
    """POST /accounts/{id}/report-trades: how many trades were booked from the reports, how
    many report prices that stored (D167 amended (c)), and the funds left out because their
    holdings would not match the report (review of 15 Nov 2026)."""

    booked: int
    prices_stored: int
    left_out: list[str] = Field(default_factory=list)


def book_report_trades(ctx: CommandContext, account_id: UUID) -> ReportTradesBooked:
    """„Umsätze aus dem Bericht buchen“ on the holdings card (D165 amended, 15 Nov 2026): every
    trade report_trades_plan lists, recomputed here under the depot account's lock (a second
    press, or a report's „Übernehmen“ at the same time, waits and then finds them booked),
    booked as a confirmed movement with source = report and the report as its document,
    through record_movement (the cash line linked as a confirmation's would be, one
    movement.confirmed event each), the person the actor; then the reports' prices that now
    agree (store_report_prices). One transaction, the caller's. The person's press is the
    confirmation (principle 3); nothing books on its own. Nothing to book books nothing."""
    _require_depot(ctx.conn, account_id, lock=True)
    plan = report_trades_plan(ctx.conn, account_id)
    for item in plan.trades:
        record_movement(ctx, item.values, item.document_id, source=MovementSource.REPORT)
    booked = len(plan.trades)
    prices = store_report_prices(ctx, account_id) if booked else 0
    return ReportTradesBooked(booked=booked, prices_stored=prices, left_out=plan.left_out)


def _latest_report_rows(conn: Connection, on: date | None = None) -> list[Any]:
    """Every depot account's current positions of its latest report (source = report); with
    `on`, of its latest report dated on or before it (the Anlagen overview's guard reads the
    same report, services.prices.overview._depot_bounds)."""
    h = holding_table.c
    report = HoldingSource.REPORT.value
    current = [h.source == report, h.superseded_by.is_(None)]
    if on is not None:
        current.append(h.as_of <= on)
    latest = (
        select(h.account_id, func.max(h.as_of).label("as_of"))
        .where(*current)
        .group_by(h.account_id)
        .subquery()
    )
    return list(
        conn.execute(
            select(h.account_id, h.asset_id, h.as_of, h.declared_units, h.document_id, h.created_at)
            .join(latest, (h.account_id == latest.c.account_id) & (h.as_of == latest.c.as_of))
            .where(h.source == report, h.superseded_by.is_(None))
        ).all()
    )


@dataclass(frozen=True)
class LatestReportCheck:
    """One depot account's latest current report checked against its confirmed movements to
    the report's date: `rows` the report's positions (holding_statement rows), `checks` the
    report's positions and every fund the movements hold that it omits (reconcile_holdings).
    """

    account_id: UUID
    as_of: date
    rows: list[Any]
    checks: list[rules.PositionCheck]

    @property
    def wrong(self) -> list[rules.PositionCheck]:
        return [c for c in self.checks if not c.reconciled]


def latest_report_checks(conn: Connection, on: date | None = None) -> list[LatestReportCheck]:
    """Per depot account with a current report, its latest one (dated on or before `on` when
    given) re-checked on read against today's confirmed movements, by account id. The one
    check behind the holdings card (holdings_inbox_items), the price cards
    (latest_report_agreement) and the net worth's unbooked funds (services.net_worth)."""
    return report_checks_with(conn, on, MovementReads(conn))


def report_checks_with(
    conn: Connection, on: date | None, movements: MovementReads
) -> list[LatestReportCheck]:
    """`latest_report_checks` reading each account's movements through `movements`."""
    rows = _latest_report_rows(conn, on)
    out = []
    for account_id in sorted({r.account_id for r in rows}, key=str):
        mine = [r for r in rows if r.account_id == account_id]
        checks = rules.reconcile_holdings(
            mine[0].as_of,
            {r.asset_id: r.declared_units for r in mine},
            movements(account_id),
        )
        out.append(LatestReportCheck(account_id, mine[0].as_of, mine, checks))
    return out


def latest_report_agreement(conn: Connection) -> dict[UUID, bool]:
    """Per fund on a depot account's latest report: whether it agrees with the confirmed
    movements to the report's date, re-checked on read as holdings_inbox_items does (False
    when any account's latest report disagrees about it). A fund the movements hold that the
    report omits (0 by omission) is in it as False: the holdings card names it too."""
    agreed: dict[UUID, bool] = {}
    for report in latest_report_checks(conn):
        for c in report.checks:
            agreed[c.asset] = agreed.get(c.asset, True) and c.reconciled
    return agreed


# The holdings card's texts, German beside English (phase 10 D284); chosen when Zu prüfen is
# read.
TEXTS = i18n.Texts(
    "depot",
    {
        "days.range": ("zwischen dem {first} und dem {last}", "between {first} and {last}"),
        "days.list": ("vom {days}", "of {days}"),
        "and": (" und ", " and "),
        "left_out": (
            " Nicht aus dem Bericht gebucht: {names} (Bestand würde nicht zum Bericht passen)."
            " Dafür bitte die Wertpapierabrechnungen unter Dokumente hochladen.",
            " Not booked from the report: {names} (the holding would not match the report)."
            " For these, please upload the trade confirmations under Documents.",
        ),
        "unreadable.row": ("vom {day} ({name})", "of {day} ({name})"),
        "unreadable.one": (
            " Ein Umsatz des Berichts ist nicht lesbar: {rows}. Bitte die Wertpapierabrechnung"
            " dieses Umsatzes unter Dokumente hochladen.",
            " One trade of the report cannot be read: {rows}. Please upload the trade"
            " confirmation of this trade under Documents.",
        ),
        "unreadable.many": (
            " Umsätze des Berichts sind nicht lesbar: {rows}. Bitte die Wertpapierabrechnung"
            " dieser Umsätze unter Dokumente hochladen.",
            " Trades of the report cannot be read: {rows}. Please upload the trade"
            " confirmation of these trades under Documents.",
        ),
        "fund": (
            "{name}: laut Bericht {declared}, gebucht {held} Anteile",
            "{name}: {declared} per the report, {held} units booked",
        ),
        "fund.unnamed": ("Wertpapier", "Security"),
        "count.one": ("einen Kauf oder Verkauf", "one purchase or sale"),
        "count.many": ("{n} Käufe und Verkäufe", "{n} purchases and sales"),
        "unbooked.question": (
            "Der Bericht nennt {count} {days}, zu denen nichts gebucht ist. „Umsätze aus dem"
            " Bericht buchen“ bucht sie mit Stückzahl und Betrag aus dem Bericht (Gebühren und"
            " Steuern stehen in der Jahressteuerbescheinigung); einzelne Wertpapierabrechnungen"
            " unter Dokumente hochladen geht auch. Der Abgleich läuft dann von selbst neu. Sobald"
            " der Bericht stimmt, liefert er auch die Kurse der Fonds.",
            "The report lists {count} {days} for which nothing is booked. “Book trades from the"
            " report” books them with units and amount from the report (fees and taxes are in"
            " the Jahressteuerbescheinigung, the annual tax certificate); uploading single trade"
            " confirmations under Documents works too. The check then runs again by itself."
            " Once the report agrees, it also provides the funds' prices.",
        ),
        "missing.question": (
            "Fehlt eine Wertpapierabrechnung oder eine Umbuchung zwischen eigenen Depots? Bitte"
            " das Dokument unter Dokumente hochladen und übernehmen; der Abgleich läuft dann von"
            " selbst neu. Sobald der Bericht stimmt, liefert er auch die Kurse der Fonds.",
            "Is a trade confirmation or a transfer between own depots missing? Please upload the"
            " document under Documents and apply it; the check then runs again by itself. Once"
            " the report agrees, it also provides the funds' prices.",
        ),
        "differs.reasoning": (
            "Der Kontoauszug des Depots nennt einen {side} vom {day}: {units} Anteile über"
            " {amount}. {document} nennt für denselben Umsatz {other_units} Anteile{other_amount}."
            " Bis geklärt ist, welche Angabe stimmt, zählt der Bestand nur die gebuchte.",
            "The depot's cash statement lists a {side} of {day}: {units} units for {amount}."
            " {document} lists {other_units} units{other_amount} for the same trade. Until it"
            " is clear which is right, the holdings count only the booked one.",
        ),
        "differs.document": (
            "Die gebuchte Wertpapierabrechnung",
            "The booked trade confirmation",
        ),
        "differs.report": ("Der gebuchte Quartalsbericht", "The booked quarterly report"),
        "differs.amount": (" über {amount}", " for {amount}"),
        "differs.question": (
            "Welche Angabe stimmt? „Kontoauszug übernehmen“ bucht Anteile und Betrag des"
            " Kontoauszugs, „Abrechnung behalten“ die des Dokuments; der Umsatz wird danach mit"
            " dem Kauf verknüpft, und diese Karte verschwindet.",
            "Which is right? “Use the statement” books the statement's units and amount, “Keep"
            " the document” the document's; the cash line is then linked to the trade and this"
            " card goes away.",
        ),
        "statement.purchase": ("Kauf", "purchase"),
        "statement.sale": ("Verkauf", "sale"),
        "statement.reasoning": (
            "Der Kontoauszug des Depots nennt einen {side} vom {day} über"
            " {amount}, aber kein lesbares Wertpapier (ISIN) oder keine Stückzahl. Deshalb ist"
            " der Umsatz noch nicht im Bestand gebucht.",
            "The depot's cash statement lists a {side} of {day} for"
            " {amount}, but no readable security (ISIN) or no units. So the trade is not yet"
            " booked in the holdings.",
        ),
        "statement.question": (
            "Der nächste Quartalsbericht des Depots nennt diesen Umsatz mit Stückzahl: Sobald er"
            " unter Dokumente liegt, bucht „Umsätze aus dem Bericht buchen“ ihn, und diese Karte"
            " verschwindet von selbst. Liegt eine Wertpapierabrechnung vor, kann sie auch"
            " hochgeladen werden.",
            "The depot's next quarterly report lists this trade with its units: once it is under"
            " Documents, “Book trades from the report” books it, and this card goes away by"
            " itself. If there is a trade confirmation, it can be uploaded too.",
        ),
        "mismatch": (
            "Der Depotbericht zum {day} nennt eine andere Stückzahl als die gebuchten"
            " Abrechnungen ({funds}).",
            "The depot report of {day} states other units than the booked confirmations ({funds}).",
        ),
    },
)


def _days_text(days: Sequence[date]) -> str:
    """Up to three days listed, more as their range."""
    if len(days) > 3:
        return TEXTS("days.range", first=i18n.day(days[0]), last=i18n.day(days[-1]))
    return TEXTS("days.list", days=", ".join(i18n.day(day) for day in days))


def _left_out_text(plan: ReportTradesPlan) -> str:
    """The card's sentences on what „Umsätze aus dem Bericht buchen“ does not book (review of
    15 Nov 2026), by fund and day only; empty when it books everything."""
    text = ""
    if plan.left_out:
        text += TEXTS("left_out", names=_names_text(plan.left_out))
    if plan.unreadable:
        rows = _names_text(
            [
                TEXTS("unreadable.row", day=i18n.day(r.trade_date), name=r.name)
                for r in plan.unreadable
            ]
        )
        text += TEXTS(
            "unreadable.one" if len(plan.unreadable) == 1 else "unreadable.many", rows=rows
        )
    return text


def _names_text(names: Sequence[str]) -> str:
    """["A"] -> "A"; ["A", "B", "C"] -> "A, B und C" ("A, B and C")."""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])}{TEXTS('and')}{names[-1]}"


def holdings_inbox_items(conn: Connection) -> list[InboxItem]:
    """One card per depot account whose latest report (source = report) disagrees with the
    confirmed movements to its date (§14.1, principle 7). Re-checked on every read against
    today's movements, so a trade confirmation applied later clears the card without a
    person closing it; the stored rows keep what the check said when the report came in.
    D165 (3): the card names each fund with the report's and the booked units, and the
    days of the trades the reports list that no movement covers; never an amount, a
    reference or an account number. Since D167 amended (c) the question also says that the
    report prices its funds once it agrees (the fund gets no „Kein Kurs“ card meanwhile).
    D165 amended, 15 Nov 2026: report_trades counts those trades (report_trades_plan),
    which „Umsätze aus dem Bericht buchen“ books; the review of the same day added the funds
    it leaves out (their holdings would not match the report) and the rows it cannot read,
    by fund and day."""
    items = []
    for report in latest_report_checks(conn):
        account_id, as_of, wrong = report.account_id, report.as_of, report.wrong
        if not wrong:
            continue
        newest = max(report.rows, key=lambda r: r.created_at)
        plan = report_trades_plan(conn, account_id)
        unbooked = plan.trades
        names = _asset_names(conn, [c.asset for c in wrong])
        funds = "; ".join(
            TEXTS(
                "fund",
                name=names[c.asset] if c.asset in names else TEXTS("fund.unnamed"),
                declared=_units(c.declared_units),
                held=_units(c.held_units),
            )
            for c in wrong
        )
        if unbooked:
            days = _days_text(sorted({t.trade.trade_date for t in unbooked}))
            count = (
                TEXTS("count.one") if len(unbooked) == 1 else TEXTS("count.many", n=len(unbooked))
            )
            question = TEXTS("unbooked.question", count=count, days=days)
        else:
            question = TEXTS("missing.question")
        question += _left_out_text(plan)
        items.append(
            InboxItem(
                kind=InboxItemKind.HOLDINGS_MISMATCH,
                created_at=newest.created_at,
                reasoning=TEXTS("mismatch", day=i18n.day(as_of), funds=funds),
                question=question,
                document_id=newest.document_id,
                account_id=account_id,
                report_trades=len(unbooked) or None,
            )
        )
    return items


# --- D292: trades booked from the depot's cash statement --------------------------------------

_TRADE_CATEGORIES = (AllocationCategory.SECURITY_PURCHASE, AllocationCategory.SECURITY_SALE)
_TRADE_CATEGORY = {
    MovementKind.ACQUISITION.value: AllocationCategory.SECURITY_PURCHASE,
    MovementKind.DISPOSAL.value: AllocationCategory.SECURITY_SALE,
}


def line_trade_category(conn: Connection, transaction_id: UUID) -> AllocationCategory | None:
    """The trade category a cash line must keep while a current movement booked from it
    (source depot_statement) carries its units (D292 amended (c), 23 Nov 2026); None when no
    such movement exists."""
    m = movement_table.c
    kind = conn.execute(
        select(m.kind).where(
            m.transaction_id == transaction_id,
            m.source == MovementSource.DEPOT_STATEMENT.value,
            m.superseded_by.is_(None),
        )
    ).scalar_one_or_none()
    return None if kind is None else _TRADE_CATEGORY[kind]


def refuse_unbooking_trade_line(
    conn: Connection,
    transaction_id: UUID,
    categories: Sequence[AllocationCategory] | None = None,
) -> None:
    """Refuse to re-book a cash line that booked a trade (D292 amended (c)) to anything but
    its trade category, or (categories None) to merge it away as a duplicate: its units
    would stay counted under a line booked as something else. RuleViolationError
    depot.line_books_trade, nothing written."""
    category = line_trade_category(conn, transaction_id)
    if category is None:
        return
    if categories is not None and all(c is category for c in categories):
        return
    raise RuleViolationError(
        [LINE_BOOKS_TRADE],
        "the line booked a trade on its depot; it keeps its trade category",
    )


@dataclass(frozen=True)
class _StatementLine:
    """A confirmed trade line of a depot's cash statement no current movement is linked to."""

    id: UUID
    account_id: UUID
    amount: Decimal
    day: date  # the value date, else the booking date
    reference: str | None
    purpose: str
    document_id: UUID
    created_at: datetime
    trade: rules.StatementTrade | None  # None: its ISIN or units cannot be read
    kind: MovementKind


def _statement_lines(
    conn: Connection,
    *,
    account_ids: Collection[UUID] | None = None,
    transaction_ids: Collection[UUID] | None = None,
) -> list[_StatementLine]:
    """D292 (1): the trade lines of the depot accounts' cash statements that may become a
    movement: on a depot account, of a reconciled import, not merged away as a duplicate, no
    current movement linked to it (`asset_movement.transaction_id`), and every live allocation
    of it confirmed and of one category, `security_purchase` or `security_sale` (a line still
    proposed waits for its person, D165 (1)). By day, then id."""
    t, i, acc, m, al = (
        transaction_table.c,
        import_table.c,
        account_table.c,
        movement_table.c,
        allocation_table.c,
    )
    linked = select(m.transaction_id).where(
        m.transaction_id.is_not(None), m.superseded_by.is_(None)
    )
    query = (
        select(
            t.id,
            t.account_id,
            t.amount,
            func.coalesce(t.value_date, t.booking_date).label("day"),
            t.bank_reference,
            t.purpose,
            t.raw,
            t.created_at,
            i.document_id,
        )
        .join(import_table, i.id == t.import_id)
        .join(account_table, acc.id == t.account_id)
        .where(
            acc.kind == AccountKind.DEPOT.value,
            i.status == ImportStatus.RECONCILED.value,
            t.excluded_as_duplicate_of.is_(None),
            t.id.not_in(linked),
            t.id.in_(
                select(al.transaction_id).where(
                    al.category.in_([c.value for c in _TRADE_CATEGORIES]),
                    al.status == AllocationStatus.CONFIRMED.value,
                )
            ),
        )
        .order_by(func.coalesce(t.value_date, t.booking_date), t.id)
    )
    if account_ids is not None:
        query = query.where(t.account_id.in_(list(account_ids)))
    if transaction_ids is not None:
        query = query.where(t.id.in_(list(transaction_ids)))
    rows = conn.execute(query).all()
    if not rows:
        return []
    live: dict[UUID, list[tuple[str, str]]] = {}
    for tx_id, category, status in conn.execute(
        select(al.transaction_id, al.category, al.status).where(
            al.transaction_id.in_([r.id for r in rows]),
            al.status.in_([AllocationStatus.CONFIRMED.value, AllocationStatus.PROPOSED.value]),
        )
    ):
        live.setdefault(tx_id, []).append((category, status))
    found = []
    for r in rows:
        parts = live.get(r.id, [])
        categories = {c for c, _ in parts}
        if len(categories) != 1 or any(s != AllocationStatus.CONFIRMED.value for _, s in parts):
            continue
        category = AllocationCategory(next(iter(categories)))
        kind = rules.statement_trade_kind(category)
        if kind is None:
            continue
        found.append(
            _StatementLine(
                id=r.id,
                account_id=r.account_id,
                amount=r.amount,
                day=r.day,
                reference=r.bank_reference,
                purpose=r.purpose or "",
                document_id=r.document_id,
                created_at=r.created_at,
                trade=rules.statement_trade(category, r.amount, r.raw or {}),
                kind=kind,
            )
        )
    return found


@dataclass(frozen=True)
class _Counterpart:
    """A current trade movement booked from a confirmation or a report that is a cash
    statement line's trade (D292 (2) and amended (b)), and whether the two agree."""

    movement_id: UUID
    agrees: bool
    asset_id: UUID
    quantity: Decimal
    eur_amount: Decimal | None
    source: MovementSource
    document_id: UUID | None


def _counterpart(
    conn: Connection, line: _StatementLine, asset_id: UUID | None, used: set[UUID]
) -> _Counterpart | None:
    """D292 (2), the other way round, and amended (b), 23 Nov 2026: the current trade movement
    of the line's depot that no cash line is linked to and that is this line's trade (a
    confirmation or a report booked before the statement, its cash line not found then): the
    same kind (and fund, for a line whose ISIN is read) and rules.depot.statement_twin, the
    same reference when both carry one, else the trade day up to STATEMENT_TRADE_DAYS before
    the line's value date (a report's movement by the day alone, since a report prints its
    own references, as rules.statement_booked reads it the other way round). It agrees when
    the units are the line's and its amount, when it has one, too; a line whose units cannot
    be read is covered only by one of its amount and never disagrees (it has its own card).
    An agreeing movement is preferred, then the one of the same reference, then by day. Each
    movement is one line's (`used`). The booking, the inbox's cards and
    resolve_statement_trade all read this one predicate."""
    m = movement_table.c
    near = and_(
        m.occurred_at >= _at(line.day - timedelta(days=rules.STATEMENT_TRADE_DAYS)),
        m.occurred_at < _at(line.day + timedelta(days=1)),
    )
    conditions = [
        m.account_id == line.account_id,
        m.superseded_by.is_(None),
        m.transaction_id.is_(None),
        m.kind == line.kind.value,
        or_(near, m.external_ref == line.reference) if line.reference else near,
    ]
    if line.trade is not None:
        if asset_id is None:
            return None
        conditions.append(m.asset_id == asset_id)
    rows = conn.execute(
        select(
            m.id,
            m.asset_id,
            m.quantity,
            m.eur_amount,
            m.external_ref,
            m.occurred_at,
            m.source,
            m.document_id,
        )
        .where(*conditions)
        .order_by(m.occurred_at, m.id)
    ).all()

    def agrees(r: Any) -> bool:
        if line.trade is None:
            return r.eur_amount == line.amount
        return r.quantity == line.trade.units and r.eur_amount in (None, line.amount)

    def twin(r: Any) -> bool:
        if r.source == MovementSource.REPORT.value:
            # A report prints its own references, not the statement's (rules.statement_booked
            # ties the two by fund, units and day only), so a report's trade is the line's by
            # its day alone, whatever its reference.
            return rules.statement_window(r.occurred_at.date(), line.day)
        return rules.statement_twin(line.reference, line.day, r.external_ref, r.occurred_at.date())

    twins = [r for r in rows if r.id not in used and twin(r)]
    twins.sort(key=lambda r: (not agrees(r), r.external_ref != line.reference))
    if not twins or (line.trade is None and not agrees(twins[0])):
        return None
    found = twins[0]
    used.add(found.id)
    return _Counterpart(
        movement_id=found.id,
        agrees=agrees(found),
        asset_id=found.asset_id,
        quantity=found.quantity,
        eur_amount=found.eur_amount,
        source=MovementSource(found.source),
        document_id=found.document_id,
    )


def _counterparts(
    conn: Connection, lines: Sequence[_StatementLine]
) -> list[tuple[_StatementLine, UUID | None, _Counterpart | None]]:
    """Each line with its fund (the asset of its ISIN, None when unknown or unreadable) and
    its _counterpart, in the lines' order, one `used` set over them all: what the booking,
    the cards and the resolution decide on."""
    a = asset_table.c
    used: set[UUID] = set()
    out = []
    for line in lines:
        known = (
            None
            if line.trade is None
            else conn.execute(
                select(a.id).where(a.isin == line.trade.isin).order_by(a.id)
            ).scalar_one_or_none()
        )
        out.append((line, known, _counterpart(conn, line, known, used)))
    return out


def _fund_name_of(line: _StatementLine) -> str | None:
    """The fund as the statement names it: the first detail after a trade's headline
    (services.bank.depot_pdf), unless that is the ISIN line."""
    first = line.purpose.split("; ")[0].strip()
    if not first or (line.trade is not None and first.startswith(line.trade.isin)):
        return None
    return first[:200]


class StatementTradesBooked(Schema):
    """`hausbuch book-statement-trades` and the import hook (D292 (1)): trade lines booked as
    movements, lines whose trade a confirmation or a report had booked already (`covered`),
    lines whose ISIN or units cannot be read (named on the card „Kauf ohne Stückzahl“), lines
    left because a confirmation or report booked their trade with other units or another
    amount (`differs`, the card „Kontoauszug und Abrechnung weichen ab“, D292 amended (b)),
    and report prices stored after the bookings (D167 amended (c))."""

    booked: int = 0
    covered: int = 0
    unreadable: int = 0
    differs: int = 0
    prices_stored: int = 0


def book_statement_trades(
    ctx: CommandContext,
    *,
    account_ids: Collection[UUID] | None = None,
    transaction_ids: Collection[UUID] | None = None,
) -> StatementTradesBooked:
    """D292 (1): every confirmed `security_purchase` or `security_sale` line of a depot's cash
    statement with an ISIN and units and no current movement linked becomes one confirmed
    movement (acquisition or disposal; the asset by ISIN through asset_for_isin; `quantity`
    the printed units; `eur_amount` the line's amount, D166's signs; `occurred_at` the value
    date; source depot_statement; the line and the statement's document), with one
    movement.confirmed event each. Every value is printed on the bank's own statement and read
    by code (services.bank.depot_pdf), so code confirms it (D165 (1)); no model. A sale's
    withheld tax (raw.tax_withheld) is kept as the confirmation would keep it.

    A line whose trade a confirmation or a report booked already without its cash line is
    left (_counterpart agrees); one whose trade they booked with other units or another
    amount is not booked either and waits on the card „Kontoauszug und Abrechnung weichen
    ab“ (D292 amended (b)), so the fund is never counted twice; a line whose ISIN or units
    cannot be read is never guessed and is counted (the card „Kauf ohne Stückzahl“,
    statement_trade_inbox_items). Each depot's row is
    locked (lock_depots) before its lines are read, so the match job, the import hook and the
    CLI never book one line twice; the partial unique index of migration 0052 is the
    backstop. One transaction, the caller's. Running it again books nothing new."""
    first = _statement_lines(ctx.conn, account_ids=account_ids, transaction_ids=transaction_ids)
    if not first:
        return StatementTradesBooked()
    accounts = sorted({line.account_id for line in first}, key=str)
    lock_depots(ctx.conn, accounts)
    lines = _statement_lines(ctx.conn, account_ids=accounts, transaction_ids=transaction_ids)
    booked: list[UUID] = []
    covered = unreadable = differs = 0
    for line, known, found in _counterparts(ctx.conn, lines):
        if line.trade is None:
            unreadable += 1
            continue
        if found is not None:
            if found.agrees:
                covered += 1
            else:
                differs += 1
            continue
        asset_id = known or asset_for_isin(ctx, line.trade.isin, _fund_name_of(line))
        booked.append(_book_statement_line(ctx, line, asset_id))
    prices = store_report_prices_after(ctx, booked)
    return StatementTradesBooked(
        booked=len(booked),
        covered=covered,
        unreadable=unreadable,
        differs=differs,
        prices_stored=prices,
    )


def _book_statement_line(ctx: CommandContext, line: _StatementLine, asset_id: UUID) -> UUID:
    """The movement a readable statement line books (source depot_statement, the line and
    its statement's document), with its movement.confirmed event."""
    trade = line.trade
    assert trade is not None
    tax = trade.tax_withheld if trade.kind is MovementKind.DISPOSAL else None
    movement_id = ctx.conn.execute(
        insert(movement_table)
        .values(
            workspace_id=ctx.workspace_id,
            account_id=line.account_id,
            asset_id=asset_id,
            kind=trade.kind.value,
            quantity=trade.units,
            eur_amount=line.amount,
            tax_withheld_eur=tax or None,
            occurred_at=_at(line.day),
            external_ref=(line.reference or None) and line.reference[:200],
            source=MovementSource.DEPOT_STATEMENT.value,
            document_id=line.document_id,
            transaction_id=line.id,
            status=MovementStatus.CONFIRMED.value,
            created_at=ctx.now,
        )
        .returning(movement_table.c.id)
    ).scalar_one()
    append_event(
        ctx,
        EventType.MOVEMENT_CONFIRMED,
        movement_id,
        MovementRecorded(
            account_id=line.account_id,
            asset_id=asset_id,
            kind=trade.kind,
            source=MovementSource.DEPOT_STATEMENT,
            document_id=line.document_id,
        ),
    )
    return movement_id


class StatementTradeKeep(StrEnum):
    """Which reading „Kontoauszug und Abrechnung weichen ab“ keeps (D292 amended (b))."""

    STATEMENT = "statement"  # „Kontoauszug übernehmen“
    DOCUMENT = "document"  # „Abrechnung behalten“


class StatementTradeResolve(Schema):
    keep: StatementTradeKeep


class StatementTradeResolved(Schema):
    movement_id: UUID  # the movement now carrying the trade, linked to the line


def resolve_statement_trade(
    ctx: CommandContext, transaction_id: UUID, keep: StatementTradeKeep
) -> StatementTradeResolved:
    """D292 amended (b), 23 Nov 2026: the one decision on the card „Kontoauszug und
    Abrechnung weichen ab“. A booked movement has no correction of its own, so the person
    says which reading is right. `statement`: the confirmation's or report's movement is
    superseded by the statement line's (its units and amount, source depot_statement, the
    statement as its document); `document`: it is superseded by an identical movement
    linked to the line. Either way the line is linked and the card goes; both rows stay
    (append-only), with movement.superseded and movement.confirmed events. The pair is
    found again here under the depot's lock (_counterparts over the depot's lines, as the
    card found it), never sent by the page. ConflictError (409) when the line has no
    disagreement to decide (booked meanwhile, or the two agree now)."""
    account_id = ctx.conn.execute(
        select(transaction_table.c.account_id).where(transaction_table.c.id == transaction_id)
    ).scalar_one_or_none()
    if account_id is None:
        raise NotFoundError(EntityType.TRANSACTION, transaction_id)
    lock_depots(ctx.conn, [account_id])
    pair = next(
        (
            (line, found)
            for line, _, found in _counterparts(
                ctx.conn, _statement_lines(ctx.conn, account_ids=[account_id])
            )
            if line.id == transaction_id
        ),
        None,
    )
    if pair is None or pair[1] is None or pair[1].agrees or pair[0].trade is None:
        raise ConflictError(f"transaction {transaction_id} has no differing trade to decide")
    line, found = pair
    assert found is not None
    m = movement_table.c
    if keep is StatementTradeKeep.STATEMENT:
        movement_id = _book_statement_line(ctx, line, found.asset_id)
        document_id: UUID | None = line.document_id
    else:
        old = ctx.conn.execute(select(movement_table).where(m.id == found.movement_id)).one()
        copied = {
            k: v
            for k, v in old._mapping.items()
            if k not in ("id", "superseded_by", "created_at", "transaction_id", "movement_hash")
        }
        movement_id = ctx.conn.execute(
            insert(movement_table)
            .values(**copied, transaction_id=line.id, created_at=ctx.now)
            .returning(m.id)
        ).scalar_one()
        document_id = found.document_id
        append_event(
            ctx,
            EventType.MOVEMENT_CONFIRMED,
            movement_id,
            MovementRecorded(
                account_id=line.account_id,
                asset_id=found.asset_id,
                kind=line.kind,
                source=found.source,
                document_id=found.document_id,
            ),
        )
    ctx.conn.execute(
        update(movement_table).where(m.id == found.movement_id).values(superseded_by=movement_id)
    )
    append_event(
        ctx,
        EventType.MOVEMENT_SUPERSEDED,
        found.movement_id,
        MovementSuperseded(superseded_by=movement_id, document_id=document_id),
    )
    return StatementTradeResolved(movement_id=movement_id)


def book_all_statement_trades(ctx: CommandContext) -> StatementTradesBooked:
    """`hausbuch book-statement-trades` (D292 (1)): book_statement_trades over every depot of
    the workspace, for the lines stored before it was built."""
    return book_statement_trades(ctx)


def statement_trade_inbox_items(conn: Connection) -> list[InboxItem]:
    """D292 (1), principle 7: one card „Kauf ohne Stückzahl“ per confirmed trade line of a
    depot's cash statement whose ISIN or units cannot be read and whose trade no movement
    carries (none linked to the line, none of the same kind and amount booked from a
    confirmation or a report in the days before it, _counterpart). Computed on read, so the
    report's „Umsätze aus dem Bericht buchen“ or a confirmation clears it without a person
    closing it. It names the day and the amount, never a reference or an account number.
    No „Abrechnung hochladen“: the app keeps no flag of which provider issues trade
    confirmations (quirion issues none for a savings plan or a rebalancing).

    D292 amended (b), 23 Nov 2026: one card „Kontoauszug und Abrechnung weichen ab“ per
    readable line whose trade a confirmation or a report booked with other units or another
    amount (_counterpart), naming both readings, with „Kontoauszug übernehmen“ and
    „Abrechnung behalten“ (resolve_statement_trade)."""
    items = []
    for line, _, found in _counterparts(conn, _statement_lines(conn)):
        if line.trade is not None:
            if found is not None and not found.agrees:
                items.append(_differs_item(line, found))
            continue
        if found is not None:
            continue
        items.append(
            InboxItem(
                kind=InboxItemKind.DEPOT_TRADE_WITHOUT_UNITS,
                created_at=line.created_at,
                reasoning=TEXTS(
                    "statement.reasoning",
                    side=TEXTS(
                        "statement.purchase"
                        if line.kind is MovementKind.ACQUISITION
                        else "statement.sale"
                    ),
                    day=i18n.day(line.day),
                    amount=i18n.money(abs(line.amount)),
                ),
                question=TEXTS("statement.question"),
                transaction_id=line.id,
                account_id=line.account_id,
                document_id=line.document_id,
            )
        )
    return items


def _differs_item(line: _StatementLine, found: _Counterpart) -> InboxItem:
    trade = line.trade
    assert trade is not None
    other = abs(found.eur_amount) if found.eur_amount is not None else None
    return InboxItem(
        kind=InboxItemKind.DEPOT_STATEMENT_TRADE_DIFFERS,
        created_at=line.created_at,
        reasoning=TEXTS(
            "differs.reasoning",
            side=TEXTS(
                "statement.purchase" if line.kind is MovementKind.ACQUISITION else "statement.sale"
            ),
            day=i18n.day(line.day),
            units=_units(abs(trade.units)),
            amount=i18n.money(abs(line.amount)),
            document=TEXTS(
                "differs.report" if found.source is MovementSource.REPORT else "differs.document"
            ),
            other_units=_units(abs(found.quantity)),
            other_amount="" if other is None else TEXTS("differs.amount", amount=i18n.money(other)),
        ),
        question=TEXTS("differs.question"),
        transaction_id=line.id,
        account_id=line.account_id,
        document_id=found.document_id,
        movement_id=found.movement_id,
    )


# The Postbox list's length on the ETF tab, newest first.
POSTBOX_LIMIT = 30


def etf_overview(conn: Connection) -> EtfOverview:
    """The Anlagen ETF tab (§13, mockup 09b): per depot account its funds, with the latest
    report's figures and whether they still hold, and the depot's documents with the status
    of their reading. Read only; every figure is a stored one or a sum of stored ones."""
    from hausbuch.rules.extraction_parse import PRIVATE_SUBTYPES  # it imports the readers

    accounts = []
    for account in depot_accounts(conn):
        positions = _positions(conn, account.id)
        if positions:
            accounts.append(
                DepotAccountPositions(
                    account_id=account.id, label=account.label, positions=positions
                )
            )
    d, p = document_table.c, proposal_table.c
    latest = (
        select(p.status)
        .where(p.document_id == d.id)
        .order_by(p.created_at.desc())
        .limit(1)
        .scalar_subquery()
    )
    rows = conn.execute(
        select(d.id, d.subtype, d.title, d.document_date, d.created_at, latest.label("reading"))
        .where(d.subtype.in_([s.value for s in PRIVATE_SUBTYPES]))
        .order_by(d.document_date.desc().nulls_last(), d.created_at.desc())
        .limit(POSTBOX_LIMIT)
    ).all()
    documents = [
        DepotDocumentStatus(
            document_id=r.id,
            subtype=r.subtype,
            title=r.title,
            document_date=r.document_date,
            created_at=r.created_at,
            reading=r.reading,
        )
        for r in rows
    ]
    return EtfOverview(accounts=accounts, documents=documents)


@dataclass(frozen=True)
class _Omitted:
    """A fund's position of 0 on a report that does not list it (never stored)."""

    as_of: date
    declared_units: Decimal = Decimal(0)
    declared_value_eur: Decimal | None = None
    created_at: datetime | None = None


def _positions(conn: Connection, account_id: UUID) -> list[DepotPosition]:
    m, h, a, pr = movement_table.c, holding_table.c, asset_table.c, price_table.c
    moves = conn.execute(
        select(m.asset_id, m.kind, m.quantity, m.eur_amount, m.occurred_at).where(
            m.account_id == account_id,
            m.superseded_by.is_(None),
            m.status == MovementStatus.CONFIRMED.value,
        )
    ).all()
    reports = conn.execute(
        select(h.asset_id, h.as_of, h.declared_units, h.declared_value_eur, h.created_at)
        .where(
            h.account_id == account_id,
            h.source == HoldingSource.REPORT.value,
            h.superseded_by.is_(None),
        )
        .order_by(h.as_of.desc(), h.created_at.desc())
    ).all()
    latest: dict[UUID, Any] = {}
    for row in reports:
        latest.setdefault(row.asset_id, row)
    ids = {r.asset_id for r in moves} | set(latest)
    # A fund the movements hold that the account's latest report does not list is 0 on that
    # report (§14.1, "0 by omission"); the row is not stored (finish_report), so it is made
    # here: no stated value and no price, so it never shows a value.
    last_on = reports[0].as_of if reports else None
    for asset_id in ids:
        row = latest.get(asset_id)
        if last_on is not None and (row is None or row.as_of < last_on):
            latest[asset_id] = _Omitted(as_of=last_on)
    if not ids:
        return []
    assets = {r.id: r for r in conn.execute(select(a.id, a.isin, a.name).where(a.id.in_(ids)))}
    positions = []
    for asset_id in sorted(ids, key=lambda i: (assets[i].name, str(i))):
        mine = [r for r in moves if r.asset_id == asset_id]
        units = sum((r.quantity for r in mine), Decimal(0))
        report = latest.get(asset_id)
        if not mine and (report is None or report.declared_units == 0):
            continue
        reconciled, moved, value, price = None, False, None, None
        if report is not None:
            held = sum(
                (r.quantity for r in mine if r.occurred_at.date() <= report.as_of), Decimal(0)
            )
            reconciled = abs(report.declared_units - held) <= rules.UNITS_TOLERANCE
            moved = any(r.quantity != 0 and r.occurred_at.date() > report.as_of for r in mine)
            if reconciled and not moved:
                value = report.declared_value_eur
        if report is not None and not isinstance(report, _Omitted):
            # The price is stored with its position (one ctx.now): one stored before the
            # current row is a superseded reading's, which a corrected position that stored
            # none (it no longer reconciles) must not show. The newest wins.
            price = conn.execute(
                select(pr.price_eur)
                .where(
                    pr.asset_id == asset_id,
                    pr.on == report.as_of,
                    pr.source == PriceSourceKind.STATEMENT.value,
                    pr.fetched_at >= report.created_at,
                )
                .order_by(pr.fetched_at.desc())
                .limit(1)
            ).scalar_one_or_none()
        # A sale or a transfer of units needs the lots (phase 7); a purchase without its
        # amount leaves the sum unknown.
        bought = [r for r in mine if r.kind == MovementKind.ACQUISITION.value]
        only_bought = all(
            r.kind in (MovementKind.ACQUISITION.value, MovementKind.DISTRIBUTION.value)
            for r in mine
        ) and all(r.eur_amount is not None for r in bought)
        cost = -sum((r.eur_amount for r in bought), Decimal(0)) if bought and only_bought else None
        positions.append(
            DepotPosition(
                asset_id=asset_id,
                isin=assets[asset_id].isin,
                name=assets[asset_id].name,
                units=units,
                report_on=None if report is None else report.as_of,
                report_units=None if report is None else report.declared_units,
                report_value_eur=None if report is None else report.declared_value_eur,
                report_price_eur=price,
                reconciled=reconciled,
                moved_since_report=moved,
                value_eur=value,
                cost_eur=cost,
                gain_eur=None if value is None or cost is None else value - cost,
            )
        )
    return positions


__all__ = [
    "MovementReads",
    "report_checks_with",
    "CASH_LINK_DAYS",
    "DEPOT_ACCOUNT_REQUIRED",
    "DEPOT_HOLDINGS_MISMATCH",
    "DEPOT_HOLDING_EXISTS",
    "DEPOT_MOVEMENT_EXISTS",
    "DEPOT_STATEMENT_TRADE_DIFFERS",
    "StatementTradesBooked",
    "book_all_statement_trades",
    "book_statement_trades",
    "resolve_statement_trade",
    "statement_trade_inbox_items",
    "DepotHoldingValues",
    "DepotMovementValues",
    "ReportTrade",
    "ReportTradesBooked",
    "ReportTradesPlan",
    "UnreadableTrade",
    "asset_for_isin",
    "book_report_trades",
    "lock_depots",
    "depot_accounts",
    "etf_overview",
    "finish_report",
    "holdings_inbox_items",
    "LatestReportCheck",
    "latest_report_agreement",
    "latest_report_checks",
    "record_holding",
    "record_movement",
    "store_all_report_prices",
    "store_report_prices",
    "store_report_prices_after",
    "transfer_pair",
    "report_trades_plan",
]
