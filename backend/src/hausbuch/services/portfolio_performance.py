"""Anlagen's gain since the start and yearly return per portfolio (docs/phases/phase-10.md
§7.6 Anlagen, items 1–3, build decision (b)); the arithmetic is
rules/performance/portfolio.py. Read only.

**Today's value** of each portfolio (a depot, an exchange, a wallet) is its `total_eur` on
GET /assets/overview (`prices.overview.assets_overview` on today with the live day), the
figure Anlagen and the net worth show; an account the overview leaves out (nothing held, no
cash) is worth 0. A position the overview cannot value (no price, or units a depot report
contradicts) is left out of that total, so the portfolio's gain, percent and rate are not
computed (`unvalued`), nor those of a total that holds it.

**Money in and out** (+ paid in, − taken out), from confirmed rows only, through today:

1. *A depot with a cash balance* (its statements or an opening balance; the net worth's
   balance line, `net_worth.depot_cash_basis`):
   - START: on the balance's anchor day, the anchor plus the funds held that day at that
     day's price (`assets_overview_with` on that day): the first day the app knows it
     (item 3). A fund not valued that day leaves the figures unknown, never 0.
   - TRANSFER: each cash line after the anchor day up to the day the balance reaches, by its
     live rows of a transfer category (Einlage +, Auszahlung −). Every other line (a
     purchase, a sale, a distribution, a fee, interest) moves money inside the depot. A line
     with no booked row is counted in `unbooked_lines`.
   - IN_TRANSIT: a bank payment the net worth counts „unterwegs zu <Depot>“, at the euros it
     counts there, on its payment day.
   - UNITS: funds moved in or out after the anchor day (an Einbuchung, an Ausbuchung).
2. *A depot without a cash balance* (its cash is in no figure): from its first movement,
   TRADE for each purchase (+) and sale (−) at its booked euros, DISTRIBUTION (−, net of the
   tax withheld) for each distribution, UNITS for funds moved.
3. *An exchange or a wallet*: each movement that is a leg of a current transfer link
   (phase 6 D162, phase 7 D184), or of kind internal_transfer_in/out: euros at face value
   (TRANSFER), coins at that day's price (UNITS). Trades, rewards, airdrops, fees and
   corrections stay inside the value.

A move between two own portfolios is out of one and in to the other; the totals (ETF, Krypto,
both) leave it out, keeping only money from or to outside (rules: `pair`, the other leg's
row: the link's other leg, or `depot.transfer_pair` for funds moved between depots).

Units are valued at the price `rules.valuation.price_on` picks for that day, at most
STALE_AFTER_DAYS old; none, and the euros are unknown (None), never 0.
"""

from collections import defaultdict
from collections.abc import Hashable, Iterable, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, extract, func, select

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import price as price_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.rules import valuation
from hausbuch.rules.matching import TRANSFER_CATEGORIES
from hausbuch.rules.performance import portfolio as R
from hausbuch.schemas.enums import AccountKind, AllocationStatus
from hausbuch.schemas.household import (
    MovementKind,
    MovementStatus,
    PricePoint,
    PriceSourceKind,
)
from hausbuch.schemas.portfolio import (
    AssetsPerformance,
    NoRateReason,
    PortfolioFigures,
    PortfolioFlow,
    PortfolioFlowKind,
    PortfolioPerformance,
)

from .depot import MovementReads, transfer_pair
from .net_worth import depot_cash_basis, property_net_on
from .prices.overview import ASSET_ACCOUNT_KINDS, assets_overview, assets_overview_with
from .transfers import _current_links

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
K = PortfolioFlowKind
_TRANSFERS = (MovementKind.INTERNAL_TRANSFER_IN.value, MovementKind.INTERNAL_TRANSFER_OUT.value)
_LIVE = (AllocationStatus.CONFIRMED.value, AllocationStatus.PROPOSED.value)
_TRADE = {MovementKind.ACQUISITION.value: 1, MovementKind.DISPOSAL.value: -1}
_ETF_KINDS = (AccountKind.DEPOT,)
_CRYPTO_KINDS = (AccountKind.EXCHANGE, AccountKind.WALLET)


def _end(day: date) -> datetime:
    return datetime.combine(day + timedelta(days=1), datetime.min.time(), UTC)


def _euro(asset: Any) -> bool:
    return asset.kind == "cash" and (asset.symbol or asset.currency) == "EUR"


class _Prices:
    """Every stored price of the assets asked (and their `priced_as` twins), read once; a
    day's price is the row `valuation.price_on` picks, at most STALE_AFTER_DAYS old."""

    def __init__(self, conn: Connection, assets: dict[UUID, Any]) -> None:
        self._assets = assets
        ids = set(assets) | {a.priced_as_asset_id for a in assets.values() if a.priced_as_asset_id}
        p = price_table.c
        self._points: dict[UUID, list[PricePoint]] = defaultdict(list)
        if ids:
            for r in conn.execute(
                select(p.asset_id, p.on, p.price_eur, p.source, p.fetched_at).where(
                    p.asset_id.in_(ids), p.source != PriceSourceKind.SPOT.value
                )
            ):
                self._points[r.asset_id].append(
                    PricePoint(
                        asset_id=r.asset_id,
                        on=r.on,
                        price_eur=r.price_eur,
                        source=PriceSourceKind(r.source),
                        fetched_at=r.fetched_at,
                    )
                )

    def value(self, asset_id: UUID, units: Decimal, on: date) -> Decimal | None:
        asset = self._assets[asset_id]
        if _euro(asset):
            return units.quantize(CENT)
        twin = asset.priced_as_asset_id
        point = valuation.price_on(
            self._points.get(asset_id, []),
            on,
            priced_as=self._points.get(twin, []) if twin else (),
        )
        if point is None or valuation.is_stale(point, on):
            return None
        return (units * point.price_eur).quantize(CENT)


def _links(conn: Connection) -> dict[UUID, UUID]:
    """Each leg of a current transfer link (a bank line or a movement) → its other leg."""
    other: dict[UUID, UUID] = {}
    for link in _current_links(conn):
        out = link.out_transaction_id or link.out_movement_id
        into = link.in_transaction_id or link.in_movement_id
        if out is not None and into is not None:
            other[out], other[into] = into, out
    return other


def _movements(conn: Connection, account_ids: Sequence[UUID], today: date) -> list[Any]:
    """The confirmed, current movements of the accounts through today, spam left out (as
    `assets_overview_with` counts units)."""
    if not account_ids:
        return []
    m, a = movement_table.c, asset_table.c
    return list(
        conn.execute(
            select(
                m.id,
                m.account_id,
                m.asset_id,
                m.kind,
                m.quantity,
                m.eur_amount,
                m.tax_withheld_eur,
                m.occurred_at,
            )
            .where(
                m.account_id.in_(account_ids),
                m.status == MovementStatus.CONFIRMED.value,
                m.superseded_by.is_(None),
                m.occurred_at < _end(today),
                m.asset_id.not_in(select(a.id).where(a.spam.is_(True))),
            )
            .order_by(m.occurred_at, m.id)
        ).all()
    )


def _cash_lines(
    conn: Connection, windows: dict[UUID, tuple[Decimal, date, date]]
) -> tuple[dict[UUID, list[tuple[UUID, date, Decimal]]], dict[UUID, int]]:
    """Per depot with a cash balance: its lines inside the balance (after the anchor day, up
    to `through`) with the sum of their live transfer rows, and how many lines have no booked
    row (none, or only proposed ones)."""
    if not windows:
        return {}, {}
    t, al = transaction_table.c, allocation_table.c
    rows = conn.execute(
        select(t.id, t.account_id, t.booking_date).where(
            t.account_id.in_(list(windows)), t.excluded_as_duplicate_of.is_(None)
        )
    ).all()
    inside = [
        r for r in rows if windows[r.account_id][1] < r.booking_date <= windows[r.account_id][2]
    ]
    if not inside:
        return {}, {}
    ids = [r.id for r in inside]
    transfer: dict[UUID, Decimal] = defaultdict(Decimal)
    confirmed: set[UUID] = set()
    for a in conn.execute(
        select(al.transaction_id, al.amount, al.category, al.status).where(
            al.transaction_id.in_(ids), al.status.in_(_LIVE)
        )
    ):
        if a.status == AllocationStatus.CONFIRMED.value:
            confirmed.add(a.transaction_id)
        if a.category in {c.value for c in TRANSFER_CATEGORIES}:
            transfer[a.transaction_id] += a.amount
    lines: dict[UUID, list[tuple[UUID, date, Decimal]]] = defaultdict(list)
    unbooked: dict[UUID, int] = defaultdict(int)
    for r in inside:
        if r.id not in confirmed:
            unbooked[r.account_id] += 1
        if transfer.get(r.id):
            lines[r.account_id].append((r.id, r.booking_date, transfer[r.id]))
    return lines, unbooked


class _Flows:
    """The flows read, each as the rule's Flow and the screen's PortfolioFlow."""

    def __init__(self) -> None:
        self.rule: list[R.Flow] = []
        self.shown: dict[Hashable, PortfolioFlow] = {}

    def add(
        self,
        account_id: UUID,
        on: date,
        amount: Decimal | None,
        kind: PortfolioFlowKind,
        key: Hashable,
        pair: Hashable | None = None,
        *,
        transaction_id: UUID | None = None,
        movement_id: UUID | None = None,
        asset_id: UUID | None = None,
        units: Decimal | None = None,
    ) -> None:
        if amount is not None and amount == 0:
            return
        self.rule.append(R.Flow(account_id, on, amount, key, pair))
        self.shown[key] = PortfolioFlow(
            on=on,
            amount_eur=amount,
            kind=kind,
            transaction_id=transaction_id,
            movement_id=movement_id,
            asset_id=asset_id,
            units=units,
        )


def _figures(p: R.Performance) -> PortfolioFigures:
    rate = p.yearly_return
    reason = None if p.no_rate is None else NoRateReason(p.no_rate.value)
    if rate is not None and (rate <= -1 or abs(rate) >= 1000):
        rate, reason = None, NoRateReason.NO_RATE  # outside what a rate can be shown as
    return PortfolioFigures(
        start=p.start,
        value_eur=p.value,
        paid_in_eur=p.paid_in,
        taken_out_eur=p.taken_out,
        gain_eur=p.gain,
        gain_ratio=p.gain_ratio,
        yearly_return=rate,
        no_rate=reason,
        unpriced_flows=p.unpriced,
        unvalued_positions=p.unvalued,
    )


def _depot_start(
    conn: Connection, windows: dict[UUID, tuple[Decimal, date, date]]
) -> dict[UUID, Decimal | None]:
    """Per depot with a cash balance, its value on the anchor day: the anchor plus the funds
    held that day at that day's price; None when a fund held that day has no value."""
    by_day: dict[date, list[UUID]] = defaultdict(list)
    for account_id, (_, anchor_on, _) in windows.items():
        by_day[anchor_on].append(account_id)
    out: dict[UUID, Decimal | None] = {}
    reads = MovementReads(conn)
    for day, ids in by_day.items():
        seen = {a.account_id: a for a in assets_overview_with(conn, day, reads).accounts}
        for account_id in ids:
            anchor = windows[account_id][0]
            held = seen.get(account_id)
            if held is not None and any(p.value_eur is None for p in held.positions):
                out[account_id] = None
            else:
                out[account_id] = anchor + (held.value_eur if held is not None else ZERO)
    return out


def assets_performance(conn: Connection, today: date) -> AssetsPerformance:
    """GET /assets/performance on `today` (Berlin): see the module doc."""
    acc = account_table.c
    accounts = conn.execute(
        select(acc.id, acc.label, acc.kind)
        .where(acc.kind.in_([k.value for k in ASSET_ACCOUNT_KINDS]))
        .order_by(acc.label, acc.id)
    ).all()
    ids = [a.id for a in accounts]
    kinds = {a.id: AccountKind(a.kind) for a in accounts}
    overview = assets_overview(conn, today, today=today)
    values = {a.account_id: a.total_eur for a in overview.accounts}
    unvalued = {
        a.account_id: n
        for a in overview.accounts
        if (n := sum(1 for p in a.positions if p.value_eur is None))
    }
    waiting = {w.account_id for w in overview.waiting}
    flows = _Flows()
    other = _links(conn)
    movements = _movements(conn, ids, today)
    a = asset_table.c
    asset_ids = {mv.asset_id for mv in movements}
    assets = {
        r.id: r
        for r in conn.execute(
            select(a.id, a.kind, a.symbol, a.currency, a.priced_as_asset_id).where(
                a.id.in_(asset_ids)
            )
        )
    }
    prices = _Prices(conn, assets)

    # 1. Depots with a cash balance: the anchor, the transfer lines, money in transit.
    basis = depot_cash_basis(conn, today)
    windows = {k: v.window for k, v in basis.items() if v.window is not None and k in kinds}
    starts = _depot_start(conn, windows)
    lines, unbooked = _cash_lines(conn, windows)
    for account_id, (_, anchor_on, _) in windows.items():
        flows.add(account_id, anchor_on, starts[account_id], K.START, ("start", account_id))
        for tx_id, on, amount in lines.get(account_id, []):
            flows.add(
                account_id, on, amount, K.TRANSFER, tx_id, other.get(tx_id), transaction_id=tx_id
            )
    for account_id, cash in basis.items():
        for tx_id, paid_on, counted in cash.transit:
            flows.add(
                account_id, paid_on, counted, K.IN_TRANSIT, ("transit", tx_id), transaction_id=tx_id
            )

    # 2. and 3. Movements.
    for mv in movements:
        kind = kinds[mv.account_id]
        on = mv.occurred_at.astimezone(UTC).date()
        is_transfer = mv.kind in _TRANSFERS or mv.id in other
        if kind is AccountKind.DEPOT:
            window = windows.get(mv.account_id)
            if window is not None and on <= window[1]:
                continue  # inside the START value
            if mv.kind in _TRANSFERS:
                pair = other.get(mv.id) or transfer_pair(conn, mv.id)
                amount = prices.value(mv.asset_id, mv.quantity, on)
                if mv.eur_amount is not None and mv.eur_amount != 0:
                    amount = abs(mv.eur_amount) * (1 if mv.quantity > 0 else -1)
                flows.add(
                    mv.account_id, on, amount, K.UNITS, mv.id, pair,
                    movement_id=mv.id, asset_id=mv.asset_id, units=mv.quantity,
                )  # fmt: skip
            elif window is not None:
                continue  # a trade or a distribution moves the depot's own cash
            elif mv.kind in _TRADE:
                sign = _TRADE[mv.kind]
                if mv.eur_amount is not None:
                    amount = abs(mv.eur_amount) * sign
                else:
                    worth = prices.value(mv.asset_id, abs(mv.quantity), on)
                    amount = None if worth is None else worth * sign
                flows.add(
                    mv.account_id, on, amount, K.TRADE, mv.id,
                    movement_id=mv.id, asset_id=mv.asset_id, units=mv.quantity,
                )  # fmt: skip
            elif mv.kind == MovementKind.DISTRIBUTION.value and mv.eur_amount is not None:
                net = abs(mv.eur_amount) - (mv.tax_withheld_eur or ZERO)
                flows.add(
                    mv.account_id, on, -net, K.DISTRIBUTION, mv.id,
                    movement_id=mv.id, asset_id=mv.asset_id,
                )  # fmt: skip
            continue
        if not is_transfer:
            continue
        euro = _euro(assets[mv.asset_id])
        amount = prices.value(mv.asset_id, mv.quantity, on)
        flows.add(
            mv.account_id, on, amount, K.TRANSFER if euro else K.UNITS, mv.id, other.get(mv.id),
            movement_id=mv.id, asset_id=mv.asset_id, units=None if euro else mv.quantity,
        )  # fmt: skip

    # The screen's rows: which are internal and where they go.
    owner = {f.key: f.portfolio for f in flows.rule}
    pair_tx = [f.pair for f in flows.rule if isinstance(f.pair, UUID) and f.pair not in owner]
    t = transaction_table.c
    tx_account = (
        dict(conn.execute(select(t.id, t.account_id).where(t.id.in_(pair_tx))).all())
        if pair_tx
        else {}
    )
    by_account: dict[UUID, list[PortfolioFlow]] = defaultdict(list)
    for f in flows.rule:
        counterpart = owner.get(f.pair) if f.pair is not None else None
        shown = flows.shown[f.key].model_copy(
            update={
                "internal": counterpart is not None,
                "counterpart_account_id": counterpart or tx_account.get(f.pair),
            }
        )
        by_account[f.portfolio].append(shown)

    portfolios = [
        PortfolioPerformance(
            account_id=r.id,
            label=r.label,
            kind=kinds[r.id],
            figures=_figures(R.combined(flows.rule, values, {r.id}, today, unvalued)),
            flows=sorted(by_account.get(r.id, []), key=lambda f: f.on),
            unbooked_lines=unbooked.get(r.id, 0),
            waiting=r.id in waiting,
        )
        for r in accounts
        if r.id in values or by_account.get(r.id)
    ]
    etf_ids = {i for i in ids if kinds[i] in _ETF_KINDS}
    crypto_ids = {i for i in ids if kinds[i] in _CRYPTO_KINDS}
    etf = _figures(R.combined(flows.rule, values, etf_ids, today, unvalued))
    crypto = _figures(R.combined(flows.rule, values, crypto_ids, today, unvalued))
    invested = _figures(R.combined(flows.rule, values, etf_ids | crypto_ids, today, unvalued))
    estimate, loans = property_net_on(conn, today)
    gross, tax = _distributions(conn, etf_ids, today)
    return AssetsPerformance(
        as_of=today,
        portfolios=portfolios,
        etf=etf,
        crypto=crypto,
        invested=invested,
        property_estimate_eur=estimate,
        property_loans_eur=loans,
        property_equity_eur=estimate - loans,
        total_eur=invested.value_eur + estimate - loans,
        distributions_year=today.year,
        distributions_eur=gross,
        distributions_tax_eur=tax,
    )


def _distributions(
    conn: Connection, depot_ids: Iterable[UUID], today: date
) -> tuple[Decimal, Decimal]:
    """„Ausschüttungen <Jahr>“: Σ the booked distributions on the depots in today's year
    (gross, and the tax withheld from them)."""
    ids = list(depot_ids)
    if not ids:
        return ZERO, ZERO
    m = movement_table.c
    row = conn.execute(
        select(
            func.coalesce(func.sum(func.abs(m.eur_amount)), 0),
            func.coalesce(func.sum(m.tax_withheld_eur), 0),
        ).where(
            m.account_id.in_(ids),
            m.kind == MovementKind.DISTRIBUTION.value,
            m.status == MovementStatus.CONFIRMED.value,
            m.superseded_by.is_(None),
            m.occurred_at < _end(today),
            extract("year", m.occurred_at) == today.year,
        )
    ).one()
    return Decimal(row[0]).quantize(CENT), Decimal(row[1]).quantize(CENT)


__all__ = ["assets_performance"]
