"""The daily price fetch of one workspace (docs/phases/phase-6.md D167 (3), D174).

In the order D167 (3) gives: the ECB rates first (the NAVs in another currency need them),
then each held coin, then each held ETF's NAV. "Held" is a positive sum of confirmed,
current movements over all the workspace's accounts; the first of those movements bounds
every source (no price before it). Three phases, so no network call runs inside a database
transaction:

1. `plan` reads what is held and which days each daily source has already stored;
2. `collect` asks the sources for the missing days, through `Sources`, which keeps every
   answer for the run: the ECB file, a Kraken pair, a CoinGecko day and an issuer file are
   fetched once however many workspaces need them;
3. `write` stores the new rows (ON CONFLICT DO NOTHING on the daily key: a second run of the
   day stores nothing twice) and appends one `workspace.prices_fetched` event, plus
   `asset.price_source_failed` once when an ETF's NAV has failed on seven days in a row.

Bounds per run and asset: CoinGecko at most 31 days (D167 (3)), newest first, so today's
price comes before the backlog, and only within its 365 days; Kraken (one request answers
720 days) and an issuer file (a full history) at most 400 rows (D174 (3)), newest first.
A failed source is logged by class and status only (the job's line) and leaves the price
stale; no card, except D174's.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import price as price_table
from hausbuch.rules import valuation
from hausbuch.rules.household.assets import FundEntry
from hausbuch.schemas.events import EventType, PricesFetched, PriceSourceFailed
from hausbuch.schemas.household import AssetKind, MovementStatus
from hausbuch.schemas.prices import FxRate, PriceSourceKind
from hausbuch.services.context import Actor, CommandContext, new_command
from hausbuch.services.events import append_event

from . import ecb_fx, kraken
from .assets import PriceIds, cash_asset, fund_source, price_ids
from .coingecko import CoinGeckoDaily
from .http import Fetch, SourceError
from .issuer_nav import ADAPTERS, IssuerAdapter, NavFile, read_points

S = PriceSourceKind
DAILY_SOURCES = (S.ECB_FX, S.COINGECKO_DAILY, S.KRAKEN_DAILY, S.ISSUER_NAV)
MAX_COIN_DAYS = 31  # D167 (3): CoinGecko calls per asset and run
MAX_ROWS = 400  # D174 (3): rows per asset and run from one file
PLACES = Decimal("0.00000001")
STREAK_WINDOW_DAYS = 31  # the prices_fetched events read for a failure streak


@dataclass(frozen=True)
class Held:
    id: UUID
    kind: AssetKind
    symbol: str | None
    isin: str | None
    name: str
    first_on: date
    ids: PriceIds
    fund: FundEntry | None


@dataclass
class Plan:
    held: list[Held]
    stored: dict[tuple[UUID, str], set[date]]
    ecb_days: set[date]  # the days of the workspace's stored ECB rows (any currency)


@dataclass(frozen=True)
class Draft:
    """A row to store; `asset_id` None means the cash asset of `currency` (ECB rates)."""

    asset_id: UUID | None
    on: date
    price_eur: Decimal
    source: PriceSourceKind
    raw: dict[str, Any]
    currency: str | None = None


@dataclass
class Collected:
    drafts: list[Draft] = field(default_factory=list)
    sources: set[PriceSourceKind] = field(default_factory=set)
    assets: int = 0
    failed: list[UUID] = field(default_factory=list)


@dataclass
class Sources:
    """Every source of one run, each answer kept for the run. `coingecko` is None without
    the key (Kraken alone then). `errors` counts (source, class, status) for the log line."""

    fetch: Fetch
    coingecko: CoinGeckoDaily | None
    adapters: dict[str, IssuerAdapter] = field(default_factory=dict)
    errors: Counter = field(default_factory=Counter)
    _ecb: dict[bool, dict[str, list[FxRate]] | SourceError] = field(default_factory=dict)
    _kraken: dict[str, dict[date, Decimal] | SourceError] = field(default_factory=dict)
    _coingecko: dict[tuple[str, date], Decimal | None] = field(default_factory=dict)
    _coingecko_error: SourceError | None = None
    _nav: dict[tuple[str, str, str], NavFile | SourceError] = field(default_factory=dict)

    def _failed(self, source: str, exc: SourceError) -> SourceError:
        self.errors[(source, exc.kind, exc.status)] += 1
        return exc

    def ecb(self, backfill: bool) -> dict[str, list[FxRate]] | SourceError:
        if backfill not in self._ecb:
            try:
                self._ecb[backfill] = ecb_fx.fetch(self.fetch, backfill=backfill)
            except SourceError as exc:
                self._ecb[backfill] = self._failed(S.ECB_FX.value, exc)
        return self._ecb[backfill]

    def kraken(self, pair: str) -> dict[date, Decimal] | SourceError:
        if pair not in self._kraken:
            try:
                self._kraken[pair] = kraken.daily(self.fetch, pair)
            except SourceError as exc:
                self._kraken[pair] = self._failed(S.KRAKEN_DAILY.value, exc)
        return self._kraken[pair]

    def coingecko_days(
        self, coin_id: str, days: list[date]
    ) -> tuple[dict[date, Decimal], SourceError | None]:
        """The days asked, from the run's answers first; a failed call ends CoinGecko for the
        run (a rate limit or a refused key would fail every later call too)."""
        assert self.coingecko is not None
        found = {d: self._coingecko[(coin_id, d)] for d in days if (coin_id, d) in self._coingecko}
        missing = [d for d in days if (coin_id, d) not in self._coingecko]
        if missing and self._coingecko_error is None:
            prices, error = self.coingecko.daily(coin_id, missing)
            answered = missing if error is None else [d for d in missing if d in prices]
            for d in answered:
                self._coingecko[(coin_id, d)] = prices.get(d)
                found[d] = prices.get(d)
            if error is not None:
                self._coingecko_error = self._failed(S.COINGECKO_DAILY.value, error)
        return {d: p for d, p in found.items() if p is not None}, self._coingecko_error

    def nav(self, fund: FundEntry) -> NavFile | SourceError:
        key = (fund.issuer, fund.fund_ref or "", fund.isin)
        if key not in self._nav:
            adapter = self.adapters.get(fund.issuer) or ADAPTERS[fund.issuer](self.fetch)
            self.adapters[fund.issuer] = adapter
            try:
                self._nav[key] = read_points(adapter, fund.fund_ref or "", fund.isin)
            except SourceError as exc:
                self._nav[key] = self._failed(f"{S.ISSUER_NAV.value}:{fund.issuer}", exc)
        return self._nav[key]


# --- 1. plan ------------------------------------------------------------------------------


def held_assets(conn: Connection) -> list[Held]:
    m, a = movement_table.c, asset_table.c
    sums = (
        select(
            m.asset_id,
            func.sum(m.quantity).label("units"),
            func.min(m.occurred_at).label("first_at"),
        )
        .where(m.status == MovementStatus.CONFIRMED.value, m.superseded_by.is_(None))
        .group_by(m.asset_id)
        .subquery()
    )
    rows = conn.execute(
        select(
            a.id, a.kind, a.symbol, a.isin, a.name, a.coingecko_id, a.kraken_pair, sums.c.first_at
        )
        .join(sums, sums.c.asset_id == a.id)
        .where(sums.c.units > 0)
        .order_by(a.name, a.id)
    ).all()
    return [
        Held(
            id=r.id,
            kind=AssetKind(r.kind),
            symbol=r.symbol,
            isin=r.isin,
            name=r.name,
            first_on=r.first_at.astimezone(UTC).date(),
            ids=price_ids(r.symbol, r.coingecko_id, r.kraken_pair),
            fund=fund_source(r.isin) if r.kind == AssetKind.ETF.value else None,
        )
        for r in rows
    ]


def plan(conn: Connection) -> Plan:
    p = price_table.c
    stored: dict[tuple[UUID, str], set[date]] = defaultdict(set)
    ecb_days: set[date] = set()
    for row in conn.execute(
        select(p.asset_id, p.source, p.on).where(p.source.in_([s.value for s in DAILY_SOURCES]))
    ):
        stored[(row.asset_id, row.source)].add(row.on)
        if row.source == S.ECB_FX.value:
            ecb_days.add(row.on)
    return Plan(held=held_assets(conn), stored=dict(stored), ecb_days=ecb_days)


def stored_rates(conn: Connection) -> dict[str, list[FxRate]]:
    """The ECB rates stored as the cash assets' rows, by currency, from `raw.per_eur` (the
    rate as published; price_eur is its rounded inverse)."""
    p, a = price_table.c, asset_table.c
    rates: dict[str, list[FxRate]] = defaultdict(list)
    for row in conn.execute(
        select(a.symbol, p.on, p.raw)
        .join(a, a.id == p.asset_id)
        .where(p.source == S.ECB_FX.value)
        .order_by(p.on)
    ):
        per_eur = row.raw.get("per_eur")
        if row.symbol and per_eur:
            rates[row.symbol].append(FxRate(on=row.on, per_eur=Decimal(per_eur)))
    return dict(rates)


# --- 2. collect ---------------------------------------------------------------------------


def _days(start: date, end: date) -> list[date]:
    """Every day from start to end, newest first."""
    return [end - timedelta(days=n) for n in range((end - start).days + 1)]


def _ecb(plan: Plan, sources: Sources, today: date, out: Collected) -> dict[str, list[FxRate]]:
    last = max(plan.ecb_days, default=None)
    backfill = last is None or (today - last).days > ecb_fx.BACKFILL_AFTER_DAYS
    answer = sources.ecb(backfill)
    out.sources.add(S.ECB_FX)
    if isinstance(answer, SourceError):
        return {}
    for code, rates in answer.items():
        for rate in rates:
            if rate.on in plan.ecb_days or rate.on > today:
                continue
            out.drafts.append(
                Draft(
                    asset_id=None,
                    currency=code,
                    on=rate.on,
                    price_eur=(Decimal(1) / rate.per_eur).quantize(PLACES, rounding=ROUND_HALF_UP),
                    source=S.ECB_FX,
                    raw={"per_eur": str(rate.per_eur)},
                )
            )
    return answer


def _coin(held: Held, plan: Plan, sources: Sources, today: date, out: Collected) -> None:
    stored = plan.stored.get((held.id, S.COINGECKO_DAILY.value), set()) | plan.stored.get(
        (held.id, S.KRAKEN_DAILY.value), set()
    )
    missing = [d for d in _days(held.first_on, today) if d not in stored]
    if not missing:
        return
    out.assets += 1
    left, added, errors = missing, 0, False
    if sources.coingecko is not None and held.ids.coingecko_id is not None:
        out.sources.add(S.COINGECKO_DAILY)
        reach = sources.coingecko.oldest_day(today)
        asked = [d for d in missing if d >= reach][:MAX_COIN_DAYS]
        prices, error = sources.coingecko_days(held.ids.coingecko_id, asked)
        for on, price in prices.items():
            out.drafts.append(Draft(held.id, on, price, S.COINGECKO_DAILY, {"eur": str(price)}))
        added += len(prices)
        if error is None:
            # Kraken: the days CoinGecko had no price for, and those beyond its 365 days;
            # the rest of the backlog is CoinGecko's on the next runs.
            left = [d for d in asked if d not in prices] + [d for d in missing if d < reach]
        else:
            errors = True
            left = [d for d in missing if d not in prices]
    if held.ids.kraken_pair is not None and left:
        out.sources.add(S.KRAKEN_DAILY)
        answer = sources.kraken(held.ids.kraken_pair)
        if isinstance(answer, SourceError):
            errors = True
        else:
            for on in [d for d in left if d in answer][:MAX_ROWS]:
                out.drafts.append(
                    Draft(held.id, on, answer[on], S.KRAKEN_DAILY, {"close": str(answer[on])})
                )
                added += 1
    if errors and not added:
        out.failed.append(held.id)


def _etf(
    held: Held, plan: Plan, sources: Sources, rates: dict[str, list[FxRate]], out: Collected
) -> None:
    assert held.fund is not None
    out.assets += 1
    out.sources.add(S.ISSUER_NAV)
    answer = sources.nav(held.fund)
    if isinstance(answer, SourceError):
        out.failed.append(held.id)
        return
    stored = plan.stored.get((held.id, S.ISSUER_NAV.value), set())
    rows = valuation.nav_rows(
        answer.points, held.first_on, rates.get(answer.currency, []), stored_days=stored
    )
    for row in sorted(rows, key=lambda r: r.on, reverse=True)[:MAX_ROWS]:
        raw: dict[str, Any] = {"nav": str(row.nav), "currency": row.currency}
        if row.rate_on is not None:
            raw["rate_on"] = row.rate_on.isoformat()
        out.drafts.append(Draft(held.id, row.on, row.price_eur, S.ISSUER_NAV, raw))


def collect(plan: Plan, sources: Sources, today: date, rates: dict[str, list[FxRate]]) -> Collected:
    """`rates` are the workspace's stored ECB rates; the day's new ones are added to them."""
    out = Collected()
    if not plan.held:
        return out
    fresh = _ecb(plan, sources, today, out)
    merged = {code: list(listed) for code, listed in rates.items()}
    for code, listed in fresh.items():
        known = {r.on for r in merged.get(code, [])}
        merged.setdefault(code, []).extend(r for r in listed if r.on not in known)
    for code in merged:
        merged[code].sort(key=lambda r: r.on)
    for held in plan.held:
        if held.kind is AssetKind.CRYPTO and held.ids.any:
            _coin(held, plan, sources, today, out)
    for held in plan.held:
        if held.kind is AssetKind.ETF and held.fund is not None:
            _etf(held, plan, sources, merged, out)
    return out


# --- 3. write -----------------------------------------------------------------------------


def _insert(ctx: CommandContext, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    p = price_table.c
    statement = (
        pg_insert(price_table)
        .values(rows)
        .on_conflict_do_nothing(
            index_elements=[p.workspace_id, p.asset_id, p.on, p.source],
            index_where=p.source.in_([s.value for s in DAILY_SOURCES]),
        )
    )
    return ctx.conn.execute(statement).rowcount or 0


def failure_runs(conn: Connection, asset_id: UUID, since: datetime) -> list[tuple[date, bool]]:
    """(day, failed) per prices_fetched run since `since`: the asset failed in a run when the
    run's event lists it (rules.valuation.failed_days counts the days in a row)."""
    e = event_table.c
    rows = conn.execute(
        select(e.recorded_at, e.payload).where(
            e.type == EventType.WORKSPACE_PRICES_FETCHED.value, e.recorded_at >= since
        )
    ).all()
    wanted = str(asset_id)
    return [
        (r.recorded_at.astimezone(UTC).date(), wanted in (r.payload.get("failed_asset_ids") or []))
        for r in rows
    ]


def _raised_since(conn: Connection, asset_id: UUID, since: datetime) -> bool:
    e = event_table.c
    return (
        conn.execute(
            select(e.id)
            .where(
                e.type == EventType.ASSET_PRICE_SOURCE_FAILED.value,
                e.entity_id == asset_id,
                e.recorded_at >= since,
            )
            .limit(1)
        ).first()
        is not None
    )


def write(ctx: CommandContext, plan: Plan, out: Collected) -> int:
    """Store the rows and the run's event; raise D174's card event once per failing ETF."""
    if not out.sources:
        return 0
    cash: dict[str, UUID] = {}
    rows = []
    for d in out.drafts:
        asset_id = d.asset_id
        if asset_id is None:
            assert d.currency is not None
            if d.currency not in cash:
                cash[d.currency] = cash_asset(ctx, d.currency)
            asset_id = cash[d.currency]
        rows.append(
            {
                "workspace_id": ctx.workspace_id,
                "asset_id": asset_id,
                "on": d.on,
                "price_eur": d.price_eur,
                "source": d.source.value,
                "fetched_at": ctx.now,
                "raw": d.raw,
                "created_at": ctx.now,
            }
        )
    new = sum(_insert(ctx, rows[i : i + 500]) for i in range(0, len(rows), 500))
    append_event(
        ctx,
        EventType.WORKSPACE_PRICES_FETCHED,
        ctx.workspace_id,
        PricesFetched(
            sources=sorted(out.sources, key=lambda s: DAILY_SOURCES.index(s)),
            assets=out.assets,
            rows_new=new,
            assets_failed=len(out.failed),
            failed_asset_ids=out.failed,
        ),
    )
    since = ctx.now - timedelta(days=STREAK_WINDOW_DAYS)
    held = {h.id: h for h in plan.held}
    for asset_id in out.failed:
        if held[asset_id].fund is None:
            continue  # a coin's failure never raises a card (D167 (3))
        runs = failure_runs(ctx.conn, asset_id, since)
        days = valuation.failed_days(runs)
        if days < valuation.SOURCE_FAILED_AFTER_DAYS:
            continue
        first = ctx.now.astimezone(UTC).date() - timedelta(days=days - 1)
        start = datetime.combine(first, datetime.min.time(), UTC)
        if _raised_since(ctx.conn, asset_id, start):
            continue
        last = max(plan.stored.get((asset_id, S.ISSUER_NAV.value), set()), default=None)
        append_event(
            ctx,
            EventType.ASSET_PRICE_SOURCE_FAILED,
            asset_id,
            PriceSourceFailed(source=S.ISSUER_NAV, failed_days=days, last_price_on=last),
        )
    return new


def run_workspace(
    engine: Engine, workspace_id: UUID, actor: Actor, sources: Sources, now: datetime
) -> tuple[int, Collected]:
    """Plan, collect and write one workspace: two short transactions around the network."""
    today = now.astimezone(UTC).date()
    with workspace_transaction(engine, workspace_id) as conn:
        planned = plan(conn)
        rates = stored_rates(conn)
    out = collect(planned, sources, today, rates)
    if not out.sources:
        return 0, out
    with workspace_transaction(engine, workspace_id) as conn:
        new = write(new_command(conn, workspace_id, actor, now=now), planned, out)
    return new, out


__all__ = [
    "DAILY_SOURCES",
    "MAX_COIN_DAYS",
    "MAX_ROWS",
    "Collected",
    "Held",
    "Plan",
    "Sources",
    "collect",
    "failure_runs",
    "held_assets",
    "plan",
    "run_workspace",
    "stored_rates",
    "write",
]
