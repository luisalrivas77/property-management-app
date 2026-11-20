"""The Anlagen screen's reads and the two price commands (docs/phases/phase-6.md D167, D174,
§12, §13; phase 6 step 5b).

- `assets_overview(conn, on)`: the Übersicht tab (mockup 09): per depot, exchange and wallet
  account its positions on `on` (units = Σ confirmed, current movements through that day),
  each with the price `rules.valuation.price_on` picks (date first, then source), its source
  and day, the value, the stale marker after seven days, and „Kurs aus dem letzten Bericht“
  for a fund whose issuer has no adapter and is priced by a depot report (D174 (4)). EUR is a
  cash asset at 1.0; USD, GBP and CHF are valued by their ECB rows. On an exchange account
  a trade's EUR leg is never its own movement but the eur_amount of the coin's acquisition or
  disposal (D178 (3)), so the account's EUR also takes those (`rules.exchange.positions`, as
  the exchange page reconciles; 15 Nov 2026, Luis: „It says that Binance has 4k in euros
  which is not the case“: the sum of movements alone showed every EUR deposit and none of
  the EUR spent). A depot's cash is its bank lines, not movements, so a depot takes none.
- `list_assets`, `update_asset` (PATCH: the name, and a coin's CoinGecko id and Kraken pair:
  „Kursquelle wählen“), `record_price` (POST: a typed `user` price, „Kurs eintragen“).
- `price_inbox_items(conn, now)`: the Zu prüfen cards, computed on read like the depot's:
  `price_source_failed` (D174 (4): a held ETF whose issuer's file failed on seven daily runs
  in a row, until a run succeeds or a person types a price; since 16 Nov 2026 also a fund
  whose source is a catalogue reference no row stored yet, named by the runs' events, and,
  at once, a fund whose NAV is in a currency the ECB publishes no rate for, naming the
  currency) and `price_no_source` (D167 (1):
  a held coin with neither a CoinGecko id nor a Kraken pair, „Kursquelle wählen“; a held fund
  or stock with no adapter and no price at all, which the next depot report or a typed price
  values), and `price_report_differs` (D264 (7): on the newest day a depot report priced a
  fund, its issuer NAV, else its Lang & Schwarz price, more than 2 % from the report's price;
  it only alerts, and ends with a newer report or a price typed for that day). A fund priced
  by Lang & Schwarz (D264) says so: `AssetRow.price_venue`, and its failure card names the
  exchange, which the runs' notes carry as the issuer `ls_tc`.

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
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, and_, func, insert, select, update

from hausbuch import i18n
from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import holding_statement as holding_table
from hausbuch.db.tables import price as price_table
from hausbuch.rules import depot as depot_rules
from hausbuch.rules import errors as E
from hausbuch.rules import exchange as exchange_rules
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
    EtfAccountToday,
    EtfPositionToday,
    EtfToday,
    OverviewAccount,
    OverviewPosition,
    PriceLabel,
    PricePoint,
    PriceSourceKind,
    TypedPriceBody,
)
from hausbuch.services.context import CommandContext
from hausbuch.services.depot import _movements as depot_movements
from hausbuch.services.depot import etf_overview, latest_report_agreement
from hausbuch.services.events import append_event, diff_changes
from hausbuch.services.lookup import require_row
from hausbuch.services.wallet_groups import waiting

from . import lang_schwarz
from . import texts as price_texts
from .assets import FundSource, NavSource, fund_source, nav_source, price_ids, stored_nav_sources
from .fetch import STREAK_WINDOW_DAYS, exchange_ids, failure_runs, fund_notes, held_subquery
from .issuer_nav import issuer_name
from .texts import TEXTS as T

S = PriceSourceKind
ASSET_ACCOUNT_KINDS = (AccountKind.DEPOT, AccountKind.EXCHANGE, AccountKind.WALLET)
# A person's or a document's price: it values the fund while its issuer's file fails.
_HAND_SOURCES = (S.USER, S.TRADE, S.STATEMENT)


# --- reads ----------------------------------------------------------------------------------


def _latest_prices(conn: Connection, asset_ids: set[UUID], on: date) -> dict[UUID, PricePoint]:
    """Per asset the row price_on picks on `on`: only the rows of each asset's latest day on
    or before `on` are read (date first, D167 (2)). A chain asset with a `priced_as` asset
    (phase 7 D180 (3)) is valued from its own rows and that asset's together; the row chosen
    keeps the asset_id it was stored under."""
    if not asset_ids:
        return {}
    a = asset_table.c
    twin_of: dict[UUID, UUID] = dict(
        conn.execute(
            select(a.id, a.priced_as_asset_id).where(
                a.id.in_(asset_ids), a.priced_as_asset_id.is_not(None)
            )
        ).all()
    )
    read = set(asset_ids) | set(twin_of.values())
    p = price_table.c
    day = (
        select(p.asset_id, func.max(p.on).label("on"))
        .where(p.asset_id.in_(read), p.on <= on)
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
    chosen = {
        asset_id: valuation.price_on(
            points.get(asset_id, []),
            on,
            priced_as=points.get(twin_of[asset_id], []) if asset_id in twin_of else (),
        )
        for asset_id in asset_ids
    }
    return {k: v for k, v in chosen.items() if v is not None}


def _fund(kind: AssetKind, isin: str | None, stored: NavSource | None) -> FundSource | None:
    """A fund's issuer source as the fetch sees it: the registry, else the stored reference
    from the issuer's catalogue (D174 amended 16 Nov 2026)."""
    if kind is not AssetKind.ETF:
        return None
    return nav_source(fund_source(isin), isin, stored)


def _label(point: PricePoint, fund: FundSource | None) -> PriceLabel:
    issuer = None
    if point.source is S.ISSUER_NAV and fund is not None:
        issuer = issuer_name(fund.issuer)
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
    # A wallet's read movements that wait for a person count nowhere below (16 Nov 2026).
    pending = waiting(conn, on)
    end = datetime.combine(on + timedelta(days=1), datetime.min.time(), UTC)
    units = conn.execute(
        select(m.account_id, m.asset_id, func.sum(m.quantity).label("units"))
        .where(
            m.account_id.in_([r.id for r in accounts]),
            m.status == MovementStatus.CONFIRMED.value,
            m.superseded_by.is_(None),
            m.occurred_at < end,
            # A spam token is hidden from Anlagen and the net worth, never priced (D187 (1)).
            m.asset_id.not_in(select(a.id).where(a.spam.is_(True))),
        )
        .group_by(m.account_id, m.asset_id)
    ).all()
    sums: dict[tuple[UUID, UUID], Decimal] = {(u.account_id, u.asset_id): u.units for u in units}
    exchanges = [r.id for r in accounts if r.kind == AccountKind.EXCHANGE.value]
    euro = conn.execute(
        select(a.id).where(
            a.kind == AssetKind.CASH.value, a.symbol == exchange_rules.EUR, a.chain.is_(None)
        )
    ).scalar()
    if exchanges and euro is not None:
        legs = conn.execute(
            select(m.account_id, func.sum(m.eur_amount).label("eur"))
            .where(
                m.account_id.in_(exchanges),
                m.kind.in_([k.value for k in exchange_rules.TRADE_KINDS]),
                m.eur_amount.is_not(None),
                m.status == MovementStatus.CONFIRMED.value,
                m.superseded_by.is_(None),
                m.occurred_at < end,
            )
            .group_by(m.account_id)
        ).all()
        for leg in legs:
            key = (leg.account_id, euro)
            sums[key] = sums.get(key, Decimal(0)) + leg.eur
    held = [
        SimpleNamespace(account_id=account_id, asset_id=asset_id, units=n)
        for (account_id, asset_id), n in sums.items()
        if n != 0
    ]
    asset_ids = {u.asset_id for u in held}
    assets = {
        r.id: r
        for r in conn.execute(
            select(a.id, a.kind, a.symbol, a.isin, a.name, a.currency).where(a.id.in_(asset_ids))
        )
    }
    prices = _latest_prices(conn, asset_ids, on)
    stored = stored_nav_sources(conn, asset_ids)
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
            fund = _fund(kind, asset.isin, stored.get(u.asset_id))
            from_report = (
                point is not None
                and point.source is S.STATEMENT
                and kind in (AssetKind.ETF, AssetKind.STOCK)
                and fund is None
            )
            positions.append(
                OverviewPosition(
                    asset_id=u.asset_id,
                    kind=kind,
                    name=asset.name,
                    symbol=asset.symbol,
                    isin=asset.isin,
                    units=u.units,
                    price=None if valued.price is None else _label(valued.price, fund),
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
        waiting=pending,
    )


def etf_today(conn: Connection, on: date) -> EtfToday:
    """The Anlagen ETF tab at the newest daily price (phase 10 D280): `depot.etf_overview`'s
    funds and documents, each fund joined by account and asset to the Übersicht's position
    on `on` (`assets_overview`, the net worth's valuation, D167), whose units, price, value,
    stale marker, reason and report marker it copies; nothing is valued here. A fund the
    Übersicht does not hold on `on` keeps its row with no value: with the reason
    `valuation.not_valued` when its latest report disagrees with the bookings (a fund on
    the report with nothing booked), else none (a fund sold in full). An account's value is Σ of
    its valued rows, which is the Übersicht's value of that account, since every position
    the Übersicht holds on a depot is a row here (the ETF tab lists every asset with a
    confirmed movement)."""
    seen = assets_overview(conn, on)
    today = {(acc.account_id, p.asset_id): p for acc in seen.accounts for p in acc.positions}
    tab = etf_overview(conn)
    accounts = []
    for account in tab.accounts:
        rows = []
        for position in account.positions:
            match = today.get((account.account_id, position.asset_id))
            fields = position.model_dump()
            if match is None:
                # A fund the latest report lists while nothing is booked (or the bookings
                # disagree) is the net worth's „not valued“ line (D167 (c)), so its reason
                # is the same and the screen says „siehe Zu prüfen“; a fund sold in full
                # keeps its row with no value and no reason.
                reason = E.VALUATION_NOT_VALUED if position.reconciled is False else None
                rows.append(EtfPositionToday(**fields, not_valued_reason=reason))
                continue
            fields["units"] = match.units
            value = match.value_eur
            rows.append(
                EtfPositionToday(
                    **fields,
                    price=match.price,
                    value_today_eur=value,
                    gain_today_eur=(
                        None
                        if value is None or position.cost_eur is None
                        else value - position.cost_eur
                    ),
                    stale=match.stale,
                    not_valued_reason=match.not_valued_reason,
                    from_report=match.from_report,
                )
            )
        accounts.append(
            EtfAccountToday(
                account_id=account.account_id,
                label=account.label,
                positions=rows,
                value_eur=sum(
                    (r.value_today_eur for r in rows if r.value_today_eur is not None),
                    Decimal("0.00"),
                ),
            )
        )
    return EtfToday(
        on=on,
        accounts=accounts,
        total_eur=sum((a.value_eur for a in accounts), Decimal("0.00")),
        documents=tab.documents,
    )


def _asset_row(
    row: Any, latest: PricePoint | None, stored: NavSource | None, exchange: bool = False
) -> AssetRow:
    """`exchange`: an exchange_close row of the asset is stored (D264 (8)). A fund with
    no issuer source names Lang & Schwarz only once such a row is stored, never from its
    name alone: a fund the venue does not list would otherwise say it is fetched daily
    while no price ever comes."""
    kind = AssetKind(row.kind)
    ids = price_ids(row.symbol, row.coingecko_id, row.kraken_pair, chain=row.chain)
    fund = _fund(kind, row.isin, stored)
    etf = kind is AssetKind.ETF
    venue = lang_schwarz.VENUE_NAME if etf and fund is None and exchange else None
    if kind is AssetKind.CRYPTO:
        # A chain asset is also priced through its priced_as asset (phase 7 D180 (3)).
        has_source = ids.any or row.priced_as_asset_id is not None
    elif kind is AssetKind.CASH:
        has_source = True  # EUR is 1.0; other currencies by the ECB
    else:
        has_source = fund is not None or venue is not None
    return AssetRow(
        id=row.id,
        kind=kind,
        name=row.name,
        symbol=row.symbol,
        chain=row.chain,
        contract_address=row.contract_address,
        priced_as_asset_id=row.priced_as_asset_id,
        spam=row.spam,
        isin=row.isin,
        currency=row.currency,
        coingecko_id=ids.coingecko_id if kind is AssetKind.CRYPTO else None,
        kraken_pair=ids.kraken_pair if kind is AssetKind.CRYPTO else None,
        issuer=fund.issuer if fund else None,
        price_venue=venue,
        has_source=has_source,
        latest_price=None if latest is None else _label(latest, fund),
        updated_at=row.updated_at,
    )


def list_assets(conn: Connection, today: date) -> list[AssetRow]:
    rows = conn.execute(select(asset_table).order_by(asset_table.c.kind, asset_table.c.name)).all()
    latest = _latest_prices(conn, {r.id for r in rows}, today)
    stored = stored_nav_sources(conn)
    venue = exchange_ids(conn)
    return [_asset_row(r, latest.get(r.id), stored.get(r.id), r.id in venue) for r in rows]


# --- commands -------------------------------------------------------------------------------


def update_asset(ctx: CommandContext, asset_id: UUID, body: AssetPatch) -> AssetRow:
    """The name, and a coin's own CoinGecko id or Kraken pair („Kursquelle wählen“): the
    next 07:00 run fetches by them. A fund or a cash asset takes no ids (422
    asset.not_crypto); an ETF's issuer comes from the registry, else from the issuer's
    catalogue, found by the daily fetch (D174 amended 16 Nov 2026), never typed."""
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
    stored = stored_nav_sources(ctx.conn, [asset_id]).get(asset_id)
    return _asset_row(row, latest, stored)


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
    a = asset_table.c
    # Held today: positive on some account. The fetch also prices an asset sold since for
    # its held days (D174 amended 20 Nov 2026); such an asset gets no card here.
    sums = held_subquery()
    return conn.execute(
        select(
            a.id,
            a.kind,
            a.symbol,
            a.isin,
            a.name,
            a.coingecko_id,
            a.kraken_pair,
            a.created_at,
            a.chain,
            a.priced_as_asset_id,
        )
        .join(sums, sums.c.asset_id == a.id)
        # Phase 7: a spam token is hidden, never a card (D187).
        .where(a.spam.is_(False))
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


def _in_any_report(conn: Connection, asset_id: UUID) -> bool:
    h = holding_table.c
    return (
        conn.execute(
            select(h.id)
            .where(
                h.asset_id == asset_id,
                h.source == HoldingSource.REPORT.value,
                h.superseded_by.is_(None),
            )
            .limit(1)
        ).first()
        is not None
    )


def _last_run_at(conn: Connection) -> datetime | None:
    e = event_table.c
    return conn.execute(
        select(func.max(e.recorded_at)).where(e.type == EventType.WORKSPACE_PRICES_FETCHED.value)
    ).scalar_one()


_REPORT_SOURCES = (S.STATEMENT, S.ISSUER_NAV, S.EXCHANGE_CLOSE, S.USER)


def _report_day_rows(conn: Connection, asset_ids: list[UUID]) -> dict[UUID, list[Any]]:
    """Per fund the rows of the newest day a depot report priced it, of the sources
    valuation.report_gap reads (one query; never the fund's whole history)."""
    if not asset_ids:
        return {}
    p = price_table.c
    newest = (
        select(p.asset_id, func.max(p.on).label("on"))
        .where(p.source == S.STATEMENT.value, p.asset_id.in_(asset_ids))
        .group_by(p.asset_id)
        .subquery()
    )
    rows = conn.execute(
        select(p.asset_id, p.on, p.price_eur, p.source, p.fetched_at, p.document_id)
        .join(newest, and_(newest.c.asset_id == p.asset_id, newest.c.on == p.on))
        .where(p.source.in_([s.value for s in _REPORT_SOURCES]))
        .order_by(p.fetched_at, p.id)
    ).all()
    found: dict[UUID, list[Any]] = defaultdict(list)
    for row in rows:
        found[row.asset_id].append(row)
    return dict(found)


def _report_differs(
    conn: Connection, held: list[Any], stored: dict[UUID, NavSource]
) -> list[InboxItem]:
    """D264 (7): one card per held fund whose newest report day's issuer NAV, else Lang &
    Schwarz price, is more than 2 % from the report's price. Only alerts."""
    funds = [r for r in held if AssetKind(r.kind) is AssetKind.ETF]
    by_asset = _report_day_rows(conn, [r.id for r in funds])
    items: list[InboxItem] = []
    for row in funds:
        rows = by_asset.get(row.id)
        if not rows:
            continue
        points = [
            PricePoint(
                asset_id=r.asset_id,
                on=r.on,
                price_eur=r.price_eur,
                source=PriceSourceKind(r.source),
                fetched_at=r.fetched_at,
            )
            for r in rows
        ]
        gap = valuation.report_gap(points)
        if gap is None:
            continue
        report = [r for r in rows if r.source == S.STATEMENT.value][-1]  # the latest fetched
        if gap.source is S.ISSUER_NAV:
            fund = _fund(AssetKind.ETF, row.isin, stored.get(row.id))
            issuer = issuer_name(fund.issuer) if fund is not None else T("issuer.unknown")
            other = T("differs.nav", issuer=issuer)
            valued = T("differs.nav.valued", issuer=issuer)
        else:
            other = T("differs.exchange", venue=lang_schwarz.VENUE_NAME)
            valued = T("differs.exchange.valued")
        items.append(
            InboxItem(
                kind=InboxItemKind.PRICE_REPORT_DIFFERS,
                created_at=max(r.fetched_at for r in rows),
                reasoning=T(
                    "differs",
                    name=row.name,
                    day=i18n.day(gap.on),
                    report_price=price_texts.price(gap.report_price),
                    other=other,
                    price=price_texts.price(gap.price),
                    percent=price_texts.percent(gap.gap_percent),
                    valued=valued,
                ),
                question=T("differs.question"),
                asset_id=row.id,
                document_id=report.document_id,
            )
        )
    return items


def price_inbox_items(conn: Connection, now: datetime) -> list[InboxItem]:
    items: list[InboxItem] = []
    held = _held(conn)
    if not held:
        return items
    last_run = _last_run_at(conn)
    since = now - timedelta(days=STREAK_WINDOW_DAYS)
    # D167 amended (c): a fund a depot's latest report lists while that report disagrees
    # gets no card of its own; the holdings card names the fix, which also prices the fund.
    in_reports = latest_report_agreement(conn)
    stored = stored_nav_sources(conn, [r.id for r in held])
    notes = fund_notes(conn, since)  # the sources the recent runs asked (follow-ups B, C)
    for row in held:
        kind = AssetKind(row.kind)
        if kind is AssetKind.CRYPTO:
            if row.priced_as_asset_id is not None:
                continue  # priced through its priced_as asset (phase 7 D180 (3))
            if price_ids(row.symbol, row.coingecko_id, row.kraken_pair, chain=row.chain).any:
                continue
            items.append(
                InboxItem(
                    kind=InboxItemKind.PRICE_NO_SOURCE,
                    created_at=row.created_at,
                    reasoning=T("coin.no_source", name=row.name),
                    question=T("coin.no_source.question"),
                    asset_id=row.id,
                )
            )
            continue
        if kind not in (AssetKind.ETF, AssetKind.STOCK):
            continue
        fund = _fund(kind, row.isin, stored.get(row.id))
        note = notes.get(row.id) if kind is AssetKind.ETF else None
        issuer = issuer_name(fund.issuer) if fund is not None else None
        if fund is None and note is not None:
            issuer = issuer_name(note.issuer)  # a catalogue reference no row stored yet (B)
        days = 0
        if kind is AssetKind.ETF:
            days = valuation.failed_days(failure_runs(conn, row.id, since))
        if note is not None and note.currency and note.unconverted_since is not None:
            # Follow-up C: the file was read, but no ECB rate converts its currency.
            start = datetime.combine(note.unconverted_since, datetime.min.time(), UTC)
            if _priced_by_hand_since(conn, row.id, start):
                continue
            priced = _any_price(conn, row.id)
            items.append(
                InboxItem(
                    kind=InboxItemKind.PRICE_SOURCE_FAILED,
                    created_at=last_run or now,
                    reasoning=T(
                        "currency",
                        issuer=issuer,
                        name=row.name,
                        currency=note.currency,
                        value=T("value.older" if priced else "value.missing"),
                    ),
                    question=T("currency.question"),
                    asset_id=row.id,
                )
            )
            continue
        if fund is None and days > 0:
            # Follow-up B: the latest run asked an issuer and failed (only a fund it asked
            # an issuer for is ever a failed asset), so the card is D174's, never „keinen
            # Kurs“. An older note only names the issuer (review of PR #391).
            issuer = issuer or T("issuer.unknown")
        elif fund is None:
            if _any_price(conn, row.id):
                continue  # „Kurs aus dem letzten Bericht“, no card (D174 (4))
            if in_reports.get(row.id) is False:
                continue  # the holdings card („Depotbericht stimmt nicht“) covers it
            if row.id in in_reports:
                report = T("report.silent")
            elif _in_any_report(conn, row.id):
                report = T("report.stale")
            else:
                report = T("report.none")
            if issuer is not None:
                # An older run asked this issuer (a note), the latest did not fail it: the
                # issuer may well publish a price, so the card never says it does not.
                reasoning = T("fund.not_delivered", name=row.name, issuer=issuer, report=report)
            else:
                reasoning = T("fund.no_issuer", name=row.name, report=report)
            items.append(
                InboxItem(
                    kind=InboxItemKind.PRICE_NO_SOURCE,
                    created_at=row.created_at,
                    reasoning=reasoning,
                    question=T("fund.question"),
                    asset_id=row.id,
                )
            )
            continue
        if days < valuation.SOURCE_FAILED_AFTER_DAYS:
            continue
        first = now.astimezone(UTC).date() - timedelta(days=days - 1)
        if _priced_by_hand_since(conn, row.id, datetime.combine(first, datetime.min.time(), UTC)):
            continue
        value = T("value.older" if _any_price(conn, row.id) else "value.missing")
        items.append(
            InboxItem(
                kind=InboxItemKind.PRICE_SOURCE_FAILED,
                created_at=last_run or now,
                reasoning=T(
                    "failed",
                    name=row.name,
                    days=days,
                    issuer=issuer,
                    first=i18n.day(first),
                    value=value,
                ),
                question=T("failed.question"),
                asset_id=row.id,
            )
        )
    items += _report_differs(conn, held, stored)
    return items


__all__ = [
    "assets_overview",
    "etf_today",
    "list_assets",
    "price_inbox_items",
    "record_price",
    "update_asset",
]
