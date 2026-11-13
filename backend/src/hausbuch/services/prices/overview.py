"""The Anlagen screen's reads and the two price commands (docs/phases/phase-6.md D167, D174,
§12, §13; phase 6 step 5b).

- `assets_overview(conn, on)`: the Übersicht tab (mockup 09): per depot, exchange and wallet
  account its positions on `on` (units = Σ confirmed, current movements through that day),
  each with the price `rules.valuation.price_on` picks (date first, then source), its source
  and day, the value, the stale marker after seven days, and „Kurs aus dem letzten Bericht“
  for a fund whose issuer has no adapter and is priced by a depot report (D174 (4)). EUR is a
  cash asset at 1.0; USD, GBP and CHF are valued by their ECB rows.
- `list_assets`, `update_asset` (PATCH: the name, and a coin's CoinGecko id and Kraken pair:
  „Kursquelle wählen“), `record_price` (POST: a typed `user` price, „Kurs eintragen“).
- `price_inbox_items(conn, now)`: the Zu prüfen cards, computed on read like the depot's:
  `price_source_failed` (D174 (4): a held ETF whose issuer's file failed on seven daily runs
  in a row, until a run succeeds or a person types a price) and `price_no_source` (D167 (1):
  a held coin with neither a CoinGecko id nor a Kraken pair, „Kursquelle wählen“; a held fund
  or stock with no adapter and no price at all, which the next depot report or a typed price
  values).

The reconciled date (D167 (4), D165 (3)): units come from the confirmed movements, which
the trade confirmations and the exchange imports write. On a depot account the latest
current depot report on or before `on` is checked on read against today's movements
(`rules.depot.reconcile_holdings`, the holdings card's check, a fund held but not listed
counting as 0 by omission; never the stored `reconciled` flag). A fund that disagrees is
bounded by the newest earlier report at which it agreed, or by NEVER_RECONCILED, so
`valuation.value` leaves it unvalued (valuation.not_valued), out of the total and counted
in `unvalued_positions`; a trade confirmation applied later that makes it agree values it
again without a new report. A fund that agrees, and every position of an account with no
report, keeps `reconciled_through` None and is valued on any later day (units x price): a
report is not a ceiling the day after it. Step 6's net worth may bound the exchange
accounts once their imports (5a) state their reconciliation.
"""

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, and_, func, insert, select, update

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import holding_statement as holding_table
from hausbuch.db.tables import price as price_table
from hausbuch.rules import depot as depot_rules
from hausbuch.rules import errors as E
from hausbuch.rules import valuation
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.api import InboxItem, InboxItemKind
from hausbuch.schemas.enums import AccountKind, EntityType
from hausbuch.schemas.events import EventType, PriceRecorded, Updated
from hausbuch.schemas.household import (
    AssetKind,
    AssetPatch,
    HoldingSource,
    MovementStatus,
    Position,
)
from hausbuch.schemas.prices import (
    AssetPriceRow,
    AssetRow,
    AssetsOverview,
    OverviewAccount,
    OverviewPosition,
    PriceLabel,
    PricePoint,
    PriceSourceKind,
    TypedPriceBody,
)
from hausbuch.services.context import CommandContext
from hausbuch.services.depot import _movements as depot_movements
from hausbuch.services.events import append_event, diff_changes
from hausbuch.services.lookup import require_row

from .assets import fund_source, price_ids
from .fetch import STREAK_WINDOW_DAYS, failure_runs
from .issuer_nav import issuer_name

S = PriceSourceKind
ASSET_ACCOUNT_KINDS = (AccountKind.DEPOT, AccountKind.EXCHANGE, AccountKind.WALLET)
# A person's or a document's price: it values the fund while its issuer's file fails.
_HAND_SOURCES = (S.USER, S.TRADE, S.STATEMENT)


def _german_day(day: date) -> str:
    return day.strftime("%d.%m.%Y")


# --- reads ----------------------------------------------------------------------------------


def _latest_prices(conn: Connection, asset_ids: set[UUID], on: date) -> dict[UUID, PricePoint]:
    """Per asset the row price_on picks on `on`: only the rows of each asset's latest day on
    or before `on` are read (date first, D167 (2))."""
    if not asset_ids:
        return {}
    p = price_table.c
    day = (
        select(p.asset_id, func.max(p.on).label("on"))
        .where(p.asset_id.in_(asset_ids), p.on <= on)
        .group_by(p.asset_id)
        .subquery()
    )
    rows = conn.execute(
        select(p.asset_id, p.on, p.price_eur, p.source, p.fetched_at).join(
            day, and_(p.asset_id == day.c.asset_id, p.on == day.c.on)
        )
    ).all()
    points: dict[UUID, list[PricePoint]] = defaultdict(list)
    for r in rows:
        points[r.asset_id].append(
            PricePoint(
                asset_id=r.asset_id,
                on=r.on,
                price_eur=r.price_eur,
                source=S(r.source),
                fetched_at=r.fetched_at,
            )
        )
    chosen = {asset_id: valuation.price_on(listed, on) for asset_id, listed in points.items()}
    return {k: v for k, v in chosen.items() if v is not None}


def _label(point: PricePoint, isin: str | None) -> PriceLabel:
    issuer = None
    if point.source is S.ISSUER_NAV and isin:
        fund = fund_source(isin)
        issuer = issuer_name(fund.issuer) if fund else None
    return PriceLabel(source=point.source, on=point.on, price_eur=point.price_eur, issuer=issuer)


def _is_euro(row: Any) -> bool:
    return row.kind == AssetKind.CASH.value and (row.symbol or row.currency) == "EUR"


# A fund whose latest report disagrees and no earlier report agreed: never valued.
NEVER_RECONCILED = date.min


def _depot_bounds(conn: Connection, account_id: UUID, on: date) -> dict[UUID, date]:
    """The depot's funds that its latest current report on or before `on` contradicts, each
    with the newest earlier report date at which it agreed (else NEVER_RECONCILED). Re-run
    on read against today's movements, as the holdings card does (services.depot)."""
    h = holding_table.c
    rows = conn.execute(
        select(h.asset_id, h.as_of, h.declared_units).where(
            h.account_id == account_id,
            h.source == HoldingSource.REPORT.value,
            h.superseded_by.is_(None),
            h.as_of <= on,
        )
    ).all()
    if not rows:
        return {}
    declared: dict[date, dict[UUID, Decimal]] = defaultdict(dict)
    for r in rows:
        declared[r.as_of][r.asset_id] = r.declared_units
    days = sorted(declared, reverse=True)
    movements = depot_movements(conn, account_id)
    agreed: dict[date, dict[UUID, bool]] = {}

    def agrees(day: date) -> dict[UUID, bool]:
        if day not in agreed:
            checks = depot_rules.reconcile_holdings(day, declared[day], movements)
            agreed[day] = {c.asset: c.reconciled for c in checks}
        return agreed[day]

    return {
        asset: next((day for day in days[1:] if agrees(day).get(asset)), NEVER_RECONCILED)
        for asset, reconciled in agrees(days[0]).items()
        if not reconciled
    }


def assets_overview(conn: Connection, on: date) -> AssetsOverview:
    a, m, acc = asset_table.c, movement_table.c, account_table.c
    accounts = conn.execute(
        select(acc.id, acc.label, acc.kind)
        .where(acc.kind.in_([k.value for k in ASSET_ACCOUNT_KINDS]))
        .order_by(acc.label, acc.id)
    ).all()
    if not accounts:
        return AssetsOverview(on=on, total_eur=Decimal("0.00"))
    end = datetime.combine(on + timedelta(days=1), datetime.min.time(), UTC)
    units = conn.execute(
        select(m.account_id, m.asset_id, func.sum(m.quantity).label("units"))
        .where(
            m.account_id.in_([r.id for r in accounts]),
            m.status == MovementStatus.CONFIRMED.value,
            m.superseded_by.is_(None),
            m.occurred_at < end,
        )
        .group_by(m.account_id, m.asset_id)
    ).all()
    held = [u for u in units if u.units != 0]
    asset_ids = {u.asset_id for u in held}
    assets = {
        r.id: r
        for r in conn.execute(
            select(a.id, a.kind, a.symbol, a.isin, a.name, a.currency).where(a.id.in_(asset_ids))
        )
    }
    prices = _latest_prices(conn, asset_ids, on)
    by_account: dict[UUID, list[Any]] = defaultdict(list)
    for u in held:
        by_account[u.account_id].append(u)
    out_accounts: list[OverviewAccount] = []
    by_kind: dict[str, Decimal] = defaultdict(Decimal)
    stale = unvalued = 0
    for account in accounts:
        bounds = (
            _depot_bounds(conn, account.id, on) if account.kind == AccountKind.DEPOT.value else {}
        )
        positions = []
        for u in sorted(
            by_account.get(account.id, []),
            key=lambda u: (
                assets[u.asset_id].kind != AssetKind.CASH.value,
                assets[u.asset_id].name,
            ),
        ):
            asset = assets[u.asset_id]
            point = prices.get(u.asset_id)
            valued = valuation.value(
                Position(
                    account_id=account.id,
                    asset_id=u.asset_id,
                    units=u.units,
                    reconciled_through=bounds.get(u.asset_id),
                ),
                point,
                on,
                cash=_is_euro(asset),
            )
            kind = AssetKind(asset.kind)
            from_report = (
                point is not None
                and point.source is S.STATEMENT
                and kind in (AssetKind.ETF, AssetKind.STOCK)
                and fund_source(asset.isin) is None
            )
            positions.append(
                OverviewPosition(
                    asset_id=u.asset_id,
                    kind=kind,
                    name=asset.name,
                    symbol=asset.symbol,
                    isin=asset.isin,
                    units=u.units,
                    price=None if valued.price is None else _label(valued.price, asset.isin),
                    value_eur=valued.value_eur,
                    stale=valued.stale,
                    not_valued_reason=valued.not_valued_reason,
                    from_report=from_report,
                    reconciled_through=bounds.get(u.asset_id),
                )
            )
            if valued.value_eur is not None:
                by_kind[kind.value] += valued.value_eur
                stale += valued.stale
            else:
                unvalued += 1
        if positions:
            out_accounts.append(
                OverviewAccount(
                    account_id=account.id,
                    label=account.label,
                    kind=account.kind,
                    positions=positions,
                    value_eur=sum(
                        (p.value_eur for p in positions if p.value_eur is not None), Decimal("0.00")
                    ),
                )
            )
    return AssetsOverview(
        on=on,
        accounts=out_accounts,
        total_eur=sum(by_kind.values(), Decimal("0.00")),
        by_kind=dict(by_kind),
        stale_positions=stale,
        unvalued_positions=unvalued,
    )


def _asset_row(row: Any, latest: PricePoint | None) -> AssetRow:
    kind = AssetKind(row.kind)
    ids = price_ids(row.symbol, row.coingecko_id, row.kraken_pair)
    fund = fund_source(row.isin) if kind is AssetKind.ETF else None
    if kind is AssetKind.CRYPTO:
        has_source = ids.any
    elif kind is AssetKind.CASH:
        has_source = True  # EUR is 1.0; other currencies by the ECB
    else:
        has_source = fund is not None
    return AssetRow(
        id=row.id,
        kind=kind,
        name=row.name,
        symbol=row.symbol,
        isin=row.isin,
        currency=row.currency,
        coingecko_id=ids.coingecko_id if kind is AssetKind.CRYPTO else None,
        kraken_pair=ids.kraken_pair if kind is AssetKind.CRYPTO else None,
        issuer=fund.issuer if fund else None,
        has_source=has_source,
        latest_price=None if latest is None else _label(latest, row.isin),
        updated_at=row.updated_at,
    )


def list_assets(conn: Connection, today: date) -> list[AssetRow]:
    rows = conn.execute(select(asset_table).order_by(asset_table.c.kind, asset_table.c.name)).all()
    latest = _latest_prices(conn, {r.id for r in rows}, today)
    return [_asset_row(r, latest.get(r.id)) for r in rows]


# --- commands -------------------------------------------------------------------------------


def update_asset(ctx: CommandContext, asset_id: UUID, body: AssetPatch) -> AssetRow:
    """The name, and a coin's own CoinGecko id or Kraken pair („Kursquelle wählen“): the
    next 07:00 run fetches by them. A fund or a cash asset takes no ids (422
    asset.not_crypto); an ETF's issuer comes from the registry only (a fund reference per
    workspace would need a column, so it is a repo change, D174 (2))."""
    current = require_row(ctx.conn, asset_table, EntityType.ASSET, asset_id, for_update=True)
    requested = body.model_dump(exclude_unset=True)
    if current.kind != AssetKind.CRYPTO.value and (
        requested.get("coingecko_id") or requested.get("kraken_pair")
    ):
        raise RuleError(E.ASSET_NOT_CRYPTO, "only a coin is priced by CoinGecko or Kraken")
    if "name" in requested and requested["name"] is None:
        del requested["name"]
    old = {name: getattr(current, name) for name in requested}
    changes = diff_changes(old, requested)
    if changes:
        ctx.conn.execute(
            update(asset_table)
            .where(asset_table.c.id == asset_id)
            .values(**requested, updated_at=ctx.now)
        )
        append_event(ctx, EventType.ASSET_UPDATED, asset_id, Updated(changes=changes))
    row = require_row(ctx.conn, asset_table, EntityType.ASSET, asset_id)
    latest = _latest_prices(ctx.conn, {asset_id}, ctx.now.astimezone(UTC).date()).get(asset_id)
    return _asset_row(row, latest)


def record_price(ctx: CommandContext, asset_id: UUID, body: TypedPriceBody) -> AssetPriceRow:
    """„Kurs eintragen“: a `user` price, which wins on its own day (D167 amended 13 Nov
    2026: it replaces that day's fetched price, report or trade value, which the append-only
    table never corrects); a price of a later day wins over it."""
    current = require_row(ctx.conn, asset_table, EntityType.ASSET, asset_id)
    if _is_euro(current):
        raise RuleError(E.ASSET_IS_EURO, "the euro is 1.0")
    row = ctx.conn.execute(
        insert(price_table)
        .values(
            workspace_id=ctx.workspace_id,
            asset_id=asset_id,
            on=body.on,
            price_eur=body.price_eur,
            source=S.USER.value,
            fetched_at=ctx.now,
            raw={},
            created_at=ctx.now,
        )
        .returning(price_table.c.id, price_table.c.price_eur)
    ).one()
    append_event(
        ctx,
        EventType.ASSET_PRICE_RECORDED,
        asset_id,
        PriceRecorded(price_id=row.id, on=body.on, source=S.USER),
    )
    return AssetPriceRow(
        id=row.id,
        asset_id=asset_id,
        on=body.on,
        price_eur=row.price_eur,
        source=S.USER,
        fetched_at=ctx.now,
    )


# --- Zu prüfen ------------------------------------------------------------------------------


def _held(conn: Connection) -> list[Any]:
    a, m = asset_table.c, movement_table.c
    sums = (
        select(m.asset_id, func.sum(m.quantity).label("units"))
        .where(m.status == MovementStatus.CONFIRMED.value, m.superseded_by.is_(None))
        .group_by(m.asset_id)
        .subquery()
    )
    return conn.execute(
        select(a.id, a.kind, a.symbol, a.isin, a.name, a.coingecko_id, a.kraken_pair, a.created_at)
        .join(sums, sums.c.asset_id == a.id)
        .where(sums.c.units > 0)
        .order_by(a.name, a.id)
    ).all()


def _priced_by_hand_since(conn: Connection, asset_id: UUID, since: datetime) -> bool:
    p = price_table.c
    return (
        conn.execute(
            select(p.id)
            .where(
                p.asset_id == asset_id,
                p.source.in_([s.value for s in _HAND_SOURCES]),
                p.fetched_at >= since,
            )
            .limit(1)
        ).first()
        is not None
    )


def _any_price(conn: Connection, asset_id: UUID) -> bool:
    p = price_table.c
    return conn.execute(select(p.id).where(p.asset_id == asset_id).limit(1)).first() is not None


def _last_run_at(conn: Connection) -> datetime | None:
    e = event_table.c
    return conn.execute(
        select(func.max(e.recorded_at)).where(e.type == EventType.WORKSPACE_PRICES_FETCHED.value)
    ).scalar_one()


def price_inbox_items(conn: Connection, now: datetime) -> list[InboxItem]:
    items: list[InboxItem] = []
    held = _held(conn)
    if not held:
        return items
    last_run = _last_run_at(conn)
    since = now - timedelta(days=STREAK_WINDOW_DAYS)
    for row in held:
        kind = AssetKind(row.kind)
        if kind is AssetKind.CRYPTO:
            if price_ids(row.symbol, row.coingecko_id, row.kraken_pair).any:
                continue
            items.append(
                InboxItem(
                    kind=InboxItemKind.PRICE_NO_SOURCE,
                    created_at=row.created_at,
                    reasoning=(
                        f"Für {row.name} kennt Hausbuch keine Kursquelle, daher fehlt sein "
                        "Wert in der Vermögensübersicht."
                    ),
                    question=(
                        "Bitte unter Anlagen › Übersicht „Kursquelle wählen“: die CoinGecko-ID "
                        "oder das Kraken-Paar der Münze eintragen. Bis dahin kann ein Kurs "
                        "von Hand eingetragen werden."
                    ),
                    asset_id=row.id,
                )
            )
            continue
        if kind not in (AssetKind.ETF, AssetKind.STOCK):
            continue
        fund = fund_source(row.isin) if kind is AssetKind.ETF else None
        if fund is None:
            if _any_price(conn, row.id):
                continue  # „Kurs aus dem letzten Bericht“, no card (D174 (4))
            items.append(
                InboxItem(
                    kind=InboxItemKind.PRICE_NO_SOURCE,
                    created_at=row.created_at,
                    reasoning=(
                        f"Für {row.name} veröffentlicht die Fondsgesellschaft keinen Kurs, den "
                        "Hausbuch abrufen kann, und es liegt noch kein Depotbericht vor."
                    ),
                    question=(
                        "Der nächste Depotbericht unter Dokumente bewertet den Fonds von "
                        "selbst („Kurs aus dem letzten Bericht“). Bis dahin kann unter "
                        "Anlagen › Übersicht „Kurs eintragen“ ein Kurs von Hand eingetragen "
                        "werden."
                    ),
                    asset_id=row.id,
                )
            )
            continue
        days = valuation.failed_days(failure_runs(conn, row.id, since))
        if days < valuation.SOURCE_FAILED_AFTER_DAYS:
            continue
        first = now.astimezone(UTC).date() - timedelta(days=days - 1)
        if _priced_by_hand_since(conn, row.id, datetime.combine(first, datetime.min.time(), UTC)):
            continue
        items.append(
            InboxItem(
                kind=InboxItemKind.PRICE_SOURCE_FAILED,
                created_at=last_run or now,
                reasoning=(
                    f"Der Kurs von {row.name} kam seit {days} Tagen nicht von "
                    f"{issuer_name(fund.issuer)} (seit dem {_german_day(first)}); der Wert in "
                    "der Vermögensübersicht beruht auf einem älteren Kurs."
                ),
                question=(
                    "Hausbuch versucht es jeden Morgen erneut. Soll ein aktueller Kurs von Hand "
                    "eingetragen werden („Kurs eintragen“)? Der nächste Depotbericht oder eine "
                    "Wertpapierabrechnung bewertet den Fonds ebenfalls neu."
                ),
                asset_id=row.id,
            )
        )
    return items


__all__ = [
    "assets_overview",
    "list_assets",
    "price_inbox_items",
    "record_price",
    "update_asset",
]
