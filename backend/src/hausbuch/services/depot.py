"""The depot's securities (docs/phases/phase-6.md D165 (2), (3), step 4): the movements and
holdings a confirmed depot reading writes, the link to the cash line, the pair of a transfer
between own depots, and the reconciliation of a report against the movements.

Nothing here is called by the model or the job: a reading's records are applied by
services.proposals.apply_proposal when the user presses „Übernehmen“ (D132), and every row is
written confirmed in that one transaction. Rows are append-only (migration 0025's triggers): a
correction is a new row that supersedes the old one.
"""

from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from pydantic import Field
from sqlalchemy import Connection, func, insert, select

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import holding_statement as holding_table
from hausbuch.db.tables import price as price_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.rules import depot as rules
from hausbuch.rules.errors import DEPOT_HOLDINGS_MISMATCH
from hausbuch.schemas.common import Money, NonNegativeMoney, Schema
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.events import (
    AssetRecorded,
    EventType,
    HoldingsChecked,
    MovementRecorded,
    PriceRecorded,
)
from hausbuch.schemas.household import (
    AssetKind,
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
DEPOT_MOVEMENT_EXISTS = "depot.movement_exists"  # the same reference is booked already
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
    fits. Refused when the account is no depot, and when a current movement of the account
    already carries the same reference and kind (the document applied twice)."""
    _require_depot(ctx.conn, values.account_id)
    m = movement_table.c
    if values.external_ref is not None:
        twin = ctx.conn.execute(
            select(m.id).where(
                m.account_id == values.account_id,
                m.external_ref == values.external_ref,
                m.kind == values.kind.value,
                m.superseded_by.is_(None),
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
    report's price (value ÷ units) as price(source = statement). Refused when a report's
    position of the asset on that account and date is stored already (the report applied
    twice, after „Neu auslesen“ or from a second copy): holdings and prices are append-only,
    so a second row would double the event's count and the day's price."""
    _require_depot(ctx.conn, values.account_id)
    asset_id = asset_for_isin(ctx, values.isin, values.name)
    h = holding_table.c
    twin = ctx.conn.execute(
        select(h.id).where(
            h.account_id == values.account_id,
            h.asset_id == asset_id,
            h.as_of == values.as_of,
            h.source == HoldingSource.REPORT.value,
        )
    ).first()
    if twin is not None:
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
    if check.price_stored and values.declared_value_eur is not None:
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
    """After a report's positions are applied: per account and date, every asset the
    confirmed movements still hold that the report does not list gets a position of 0 (§14.1,
    "0 by omission"), and one account.holdings_reconciled or account.holdings_mismatch event
    names the rows (DEPOT_HOLDINGS_MISMATCH is the inbox card's code)."""
    h = holding_table.c
    rows = ctx.conn.execute(
        select(h.id, h.account_id, h.asset_id, h.as_of, h.reconciled).where(
            h.document_id == document_id, h.source == HoldingSource.REPORT.value
        )
    ).all()
    results = []
    for account_id, as_of in sorted({(r.account_id, r.as_of) for r in rows}, key=str):
        mine = [r for r in rows if (r.account_id, r.as_of) == (account_id, as_of)]
        listed = {r.asset_id: Decimal(0) for r in mine}
        ids = [r.id for r in mine]
        checks = rules.reconcile_holdings(as_of, listed, _movements(ctx.conn, account_id))
        for check in checks:
            if not check.listed:
                ids.append(
                    _insert_holding(ctx, account_id, check.asset, check, as_of, None, document_id)
                )
        mismatched = sum(1 for r in mine if r.reconciled is False) + sum(
            1 for c in checks if not c.listed and not c.reconciled
        )
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
    "finish_report",
    "record_holding",
    "record_movement",
    "transfer_pair",
]
