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

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from pydantic import Field
from sqlalchemy import Connection, func, insert, select, update

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import document as document_table
from hausbuch.db.tables import holding_statement as holding_table
from hausbuch.db.tables import price as price_table
from hausbuch.db.tables import proposal as proposal_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.rules import depot as rules
from hausbuch.rules.errors import DEPOT_HOLDINGS_MISMATCH
from hausbuch.schemas.api import InboxItem, InboxItemKind
from hausbuch.schemas.common import Money, NonNegativeMoney, Schema
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.events import (
    AssetRecorded,
    EventType,
    HoldingCorrected,
    HoldingsChecked,
    MovementRecorded,
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
from hausbuch.schemas.proposal import ExistingDepotAccount

from .context import CommandContext
from .errors import RuleViolationError
from .events import append_event

# Codes of this service (frontend/src/api/errors.ts has their German text).
DEPOT_ACCOUNT_REQUIRED = "depot.account_required"  # the account picked is no depot
DEPOT_MOVEMENT_EXISTS = "depot.movement_exists"  # same reference, or same day and amount
DEPOT_HOLDING_EXISTS = "depot.holding_exists"  # a report of that date is applied already

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


def _require_depot(conn: Connection, account_id: UUID) -> None:
    kind = conn.execute(
        select(account_table.c.kind).where(account_table.c.id == account_id)
    ).scalar_one_or_none()
    if kind != AccountKind.DEPOT.value:
        raise RuleViolationError([DEPOT_ACCOUNT_REQUIRED], "a depot record names a depot account")


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


def record_movement(ctx: CommandContext, values: DepotMovementValues, document_id: UUID) -> UUID:
    """One confirmed movement read from a depot document, with its cash line when exactly one
    fits. Refused when the account is no depot, and when the document was applied already: a
    current movement of the account carries the same reference and kind, or, for a movement
    without a reference (a distribution note names none), the same fund, kind, day and
    amount."""
    _require_depot(ctx.conn, values.account_id)
    m = movement_table.c
    if values.external_ref is not None:
        same = [m.external_ref == values.external_ref]
    else:
        known = ctx.conn.execute(
            select(asset_table.c.id).where(asset_table.c.isin == values.isin)
        ).all()
        same = [
            m.asset_id.in_([r.id for r in known]),
            m.occurred_at >= _at(values.occurred_on),
            m.occurred_at < _at(values.occurred_on + timedelta(days=1)),
            m.eur_amount.is_(None)
            if values.eur_amount is None
            else m.eur_amount == values.eur_amount,
        ]
    twin = ctx.conn.execute(
        select(m.id).where(
            m.account_id == values.account_id,
            m.kind == values.kind.value,
            m.superseded_by.is_(None),
            *same,
        )
    ).first()
    if twin is not None:
        raise RuleViolationError(
            [DEPOT_MOVEMENT_EXISTS], "this movement is booked on the account already"
        )
    asset_id = asset_for_isin(ctx, values.isin, values.name)
    source = _SOURCES.get(values.kind, MovementSource.USER)
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
    the same units and value it is refused (the report applied twice, after „Neu auslesen“
    or from a second copy), with other figures the new row supersedes it (a corrected
    reading; account.holding_corrected)."""
    _require_depot(ctx.conn, values.account_id)
    asset_id = asset_for_isin(ctx, values.isin, values.name)
    h = holding_table.c
    twin = ctx.conn.execute(
        select(h.id, h.declared_units, h.declared_value_eur).where(
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
        _store_price(ctx, asset_id, values, document_id)
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
    ctx: CommandContext, asset_id: UUID, values: DepotHoldingValues, document_id: UUID
) -> None:
    assert values.declared_value_eur is not None
    price = (values.declared_value_eur / values.declared_units).quantize(
        PRICE_PLACES, rounding=ROUND_HALF_UP
    )
    price_id = ctx.conn.execute(
        insert(price_table)
        .values(
            workspace_id=ctx.workspace_id,
            asset_id=asset_id,
            on=values.as_of,
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
            on=values.as_of,
            source=PriceSourceKind.STATEMENT,
            document_id=document_id,
        ),
    )


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


def _german_day(day: date) -> str:
    return day.strftime("%d.%m.%Y")


def _german_units(units: Decimal) -> str:
    """4.8000000000 → "4,8"; 0 → "0"."""
    text = f"{units.normalize():f}" if units else "0"
    return text.replace(".", ",")


def _asset_names(conn: Connection, ids: list[Any]) -> dict[UUID, str]:
    a = asset_table.c
    return dict(conn.execute(select(a.id, a.name).where(a.id.in_(ids))).tuples().all())


def _missing_trades(
    conn: Connection, document_id: UUID | None, as_of: date
) -> dict[UUID, list[rules.ProposedTrade]]:
    """The trades the report's current reading lists, by asset (looked up by ISIN), up to its
    date: the references a mismatch may be missing."""
    if document_id is None:
        return {}
    from .documents import current_extraction  # it imports the job queue

    extraction = current_extraction(conn, document_id)
    fields = None if extraction is None else extraction.fields
    if not isinstance(fields, DepotReportExtraction) or not fields.trades.value:
        return {}
    proposal = rules.propose(
        "depot_report",
        {"report_date": as_of.isoformat(), "positions": [], "trades": fields.trades.value},
    )
    trades = [] if proposal is None else [t for t in proposal.trades if t.trade_date <= as_of]
    if not trades:
        return {}
    a = asset_table.c
    by_isin = {
        r.isin: r.id
        for r in conn.execute(
            select(a.id, a.isin).where(a.isin.in_({t.isin for t in trades}))
        ).all()
    }
    found: dict[UUID, list[rules.ProposedTrade]] = {}
    for trade in trades:
        if trade.isin in by_isin:
            found.setdefault(by_isin[trade.isin], []).append(trade)
    return found


def holdings_inbox_items(conn: Connection) -> list[InboxItem]:
    """One card per depot account whose latest report (source = report) disagrees with the
    confirmed movements to its date (§14.1, principle 7). Re-checked on every read against
    today's movements, so a trade confirmation applied later clears the card without a
    person closing it; the stored rows keep what the check said when the report came in.
    D165 (3): the card names each fund with the report's and the booked units, and the
    days of the trades the report lists that no confirmation covers; never an amount, a
    reference or an account number."""
    h = holding_table.c
    report = HoldingSource.REPORT.value
    latest = (
        select(h.account_id, func.max(h.as_of).label("as_of"))
        .where(h.source == report, h.superseded_by.is_(None))
        .group_by(h.account_id)
        .subquery()
    )
    rows = conn.execute(
        select(h.account_id, h.asset_id, h.as_of, h.declared_units, h.document_id, h.created_at)
        .join(latest, (h.account_id == latest.c.account_id) & (h.as_of == latest.c.as_of))
        .where(h.source == report, h.superseded_by.is_(None))
    ).all()
    items = []
    for account_id in sorted({r.account_id for r in rows}, key=str):
        mine = [r for r in rows if r.account_id == account_id]
        as_of = mine[0].as_of
        newest = max(mine, key=lambda r: r.created_at)
        trades = _missing_trades(conn, newest.document_id, as_of)
        checks = rules.reconcile_holdings(
            as_of,
            {r.asset_id: r.declared_units for r in mine},
            _movements(conn, account_id),
            {asset: [t.reference for t in listed] for asset, listed in trades.items()},
        )
        wrong = [c for c in checks if not c.reconciled]
        if not wrong:
            continue
        missing = sorted(
            {
                t.trade_date
                for c in wrong
                for t in trades.get(c.asset, ())
                if t.reference in c.missing_references
            }
        )
        names = _asset_names(conn, [c.asset for c in wrong])
        funds = "; ".join(
            f"{names.get(c.asset, 'Wertpapier')}: laut Bericht {_german_units(c.declared_units)}"
            f", gebucht {_german_units(c.held_units)} Anteile"
            for c in wrong
        )
        if missing:
            days = ", ".join(_german_day(day) for day in missing)
            question = (
                f"Der Bericht nennt Umsätze vom {days}, zu denen keine Wertpapierabrechnung "
                "gebucht ist. Bitte die Abrechnung unter Dokumente hochladen und übernehmen; "
                "der Abgleich läuft dann von selbst neu."
            )
        else:
            question = (
                "Fehlt eine Wertpapierabrechnung oder eine Umbuchung zwischen eigenen Depots? "
                "Bitte das Dokument unter Dokumente hochladen und übernehmen; der Abgleich "
                "läuft dann von selbst neu."
            )
        items.append(
            InboxItem(
                kind=InboxItemKind.HOLDINGS_MISMATCH,
                created_at=newest.created_at,
                reasoning=(
                    f"Der Depotbericht zum {_german_day(as_of)} nennt eine andere "
                    f"Stückzahl als die gebuchten Abrechnungen ({funds})."
                ),
                question=question,
                document_id=newest.document_id,
                account_id=account_id,
            )
        )
    return items


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
    "CASH_LINK_DAYS",
    "DEPOT_ACCOUNT_REQUIRED",
    "DEPOT_HOLDINGS_MISMATCH",
    "DEPOT_HOLDING_EXISTS",
    "DEPOT_MOVEMENT_EXISTS",
    "DepotHoldingValues",
    "DepotMovementValues",
    "asset_for_isin",
    "depot_accounts",
    "etf_overview",
    "finish_report",
    "holdings_inbox_items",
    "record_holding",
    "record_movement",
    "transfer_pair",
]
