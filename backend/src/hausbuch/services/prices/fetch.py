"""The daily price fetch of one workspace (docs/phases/phase-6.md D167 (3), D174).

In the order D167 (3) gives: the ECB rates first (the NAVs in another currency need them),
then each held coin, then each held ETF's NAV. "Held" is a positive sum of confirmed,
current movements on at least one of the workspace's accounts; the first of those movements bounds
every source (no price before it). Three phases, so no network call runs inside a database
transaction:

1. `plan` reads what is held and which days each daily source has already stored;
2. `collect` asks the sources for the missing days, through `Sources`, which keeps every
   answer for the run: the ECB file, a Kraken pair, a CoinGecko day and an issuer file are
   fetched once however many workspaces need them;
3. `write` stores the new rows (ON CONFLICT DO NOTHING on the daily key: a second run of the
   day stores nothing twice) and appends one `workspace.prices_fetched` event, plus
   `asset.price_source_failed` once when an ETF's NAV has failed on seven days in a row.

A chain asset priced as a symbol-level asset (phase 7 D180 (3), §10) is never fetched itself:
its `priced_as` asset is, once however many chain assets point at it (SOL on two wallets, ETH
on Ethereum and Arbitrum), from the earliest first day of its own held movements and of the
chain assets' current movements, proposed ones included (a swap waiting for its price is
priced before it is confirmed, D186), and whether or not the chain asset is still held (its
lots need the day's price, D188 (4)). A spam token is never fetched (D187). A chain asset
without a `priced_as` is fetched by its own ids only, never the registry's for its symbol.

Bounds per run and asset: CoinGecko at most 31 days (D167 (3)), newest first, so today's
price comes before the backlog, and only within its 365 days; Kraken (one request answers
720 days) and an issuer file (a full history) at most 400 rows (D174 (3)), newest first.
A failed source is logged by class and status only (the job's line) and leaves the price
stale; no card, except D174's.

A held fund the registry does not list (D174 amended 16 Nov 2026) is looked up by its ISIN
in the catalogue of the issuer its name names, each catalogue asked at most once a run;
a reference found there prices the fund like a registry one, and the rows it stores keep it
(`raw.issuer`, `raw.fund_ref`), so the next runs need no catalogue for it. A fund no
catalogue names is priced from the depot's reports as before, with no card.

Follow-ups of the same day's review:

- (A) A stored reference that stops working (HTTP 400, 404 or 410, or a file stating another
  ISIN: the issuer moved or renumbered the fund) asks that issuer's catalogue once in the
  same run, and a different reference listed there for the ISIN is read at once; its rows
  keep the new reference. A timeout or a 5xx is an outage, not a moved fund: no catalogue.
- (B) Every fund whose issuer file fails (or whose currency, (C), cannot be converted) is
  named in the run's event with the issuer source asked (`PricesFetched.fund_sources`; a
  file read fine with no new day names nothing), so D174's card („Kurs kam nicht von …“) reaches
  a fund whose catalogue reference never stored a row (services.prices.overview).
- (C) A NAV in a currency other than EUR and FX_CURRENCIES is converted by the ECB's rate of
  that currency, read from the same file and stored as its cash asset's row (the 90-day
  file for a currency with no recent stored rate), and stored on every later run like the
  dollar's; a currency without a rate on the newest day of the ECB's answer (one the ECB
  does not publish, or stopped publishing) stores nothing and is named in the event, which
  raises a card naming the fund and the currency. A price is never stored in another
  currency than the euro, nor converted at a frozen rate.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
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
from hausbuch.schemas.events import EventType, FundSourceNote, PricesFetched, PriceSourceFailed
from hausbuch.schemas.household import AssetKind, MovementStatus
from hausbuch.schemas.prices import FxRate, PriceSourceKind
from hausbuch.services.context import Actor, CommandContext, new_command
from hausbuch.services.events import append_event

from . import ecb_fx, kraken
from .assets import (
    FundSource,
    NavSource,
    PriceIds,
    cash_asset,
    fund_source,
    nav_source,
    price_ids,
    stored_nav_sources,
)
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
    fund: FundSource | None  # the registry's entry or the stored catalogue reference


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
    discovered: dict[UUID, NavSource] = field(default_factory=dict)  # found this run
    unconverted: dict[UUID, str] = field(default_factory=dict)  # NAV currency the ECB lacks
    notes: list[FundSourceNote] = field(default_factory=list)  # failed or unconverted funds


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
    _catalogues: dict[str, dict[str, str] | SourceError] = field(default_factory=dict)

    def _failed(self, source: str, exc: SourceError) -> SourceError:
        self.errors[(source, exc.kind, exc.status)] += 1
        return exc

    def ecb(self, backfill: bool) -> dict[str, list[FxRate]] | SourceError:
        """The file's rates of every currency it lists (follow-up C): `_ecb` stores those of
        FX_CURRENCIES, `Fx.cover` those of a fund's NAV currency."""
        if backfill not in self._ecb:
            try:
                self._ecb[backfill] = ecb_fx.fetch(self.fetch, backfill=backfill, currencies=None)
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

    def _adapter(self, issuer: str) -> IssuerAdapter:
        adapter = self.adapters.get(issuer) or ADAPTERS[issuer](self.fetch)
        self.adapters[issuer] = adapter
        return adapter

    def listed(self, issuer: str, isin: str) -> NavSource | None:
        """The reference the issuer's catalogue lists for the ISIN; each catalogue is asked
        once a run, a failed one counted once. None without a catalogue or a listing."""
        if issuer not in ADAPTERS or ADAPTERS[issuer].catalogue_url is None:
            return None
        if issuer not in self._catalogues:
            try:
                self._catalogues[issuer] = self._adapter(issuer).catalogue()
            except SourceError as exc:
                self._catalogues[issuer] = self._failed(
                    f"{S.ISSUER_NAV.value}:{issuer}:catalogue", exc
                )
        found = self._catalogues[issuer]
        if isinstance(found, SourceError) or isin not in found:
            return None
        return NavSource(issuer=issuer, fund_ref=found[isin], isin=isin)

    def discover(self, isin: str, name: str) -> NavSource | None:
        """The fund's reference from the catalogue of the issuer its name names (an iShares
        fund's from iShares'). A fund whose name names no issuer with a catalogue asks
        nothing."""
        for issuer, cls in ADAPTERS.items():
            if cls.catalogue_url is None or not cls.names_brand(name):
                continue
            found = self.listed(issuer, isin)
            if found is not None:
                return found
        return None

    def nav(self, fund: FundSource) -> NavFile | SourceError:
        key = (fund.issuer, fund.fund_ref or "", fund.isin)
        if key not in self._nav:
            adapter = self._adapter(fund.issuer)
            try:
                self._nav[key] = read_points(adapter, fund.fund_ref or "", fund.isin)
            except SourceError as exc:
                self._nav[key] = self._failed(f"{S.ISSUER_NAV.value}:{fund.issuer}", exc)
        return self._nav[key]


# --- 1. plan ------------------------------------------------------------------------------


def held_subquery():
    """(asset_id, first_at) of every asset some account holds: a positive sum of confirmed,
    current movements on at least one account, as the Übersicht shows positions per account.
    Not the sum over all accounts (16 Nov 2026): units moved from one own depot to another,
    when the first depot's purchases came before the app, net to zero across both and would
    leave the position the Übersicht shows unpriced. `first_at` is the asset's first movement
    on any account."""
    m = movement_table.c
    current = [m.status == MovementStatus.CONFIRMED.value, m.superseded_by.is_(None)]
    per_account = (
        select(m.asset_id, func.sum(m.quantity).label("units"))
        .where(*current)
        .group_by(m.account_id, m.asset_id)
        .subquery()
    )
    held_ids = select(per_account.c.asset_id).where(per_account.c.units > 0)
    return (
        select(m.asset_id, func.min(m.occurred_at).label("first_at"))
        .where(*current, m.asset_id.in_(held_ids))
        .group_by(m.asset_id)
        .subquery()
    )


def _held(row: Any, first_at: datetime, stored: dict[UUID, NavSource]) -> Held:
    return Held(
        id=row.id,
        kind=AssetKind(row.kind),
        symbol=row.symbol,
        isin=row.isin,
        name=row.name,
        first_on=first_at.astimezone(UTC).date(),
        ids=price_ids(row.symbol, row.coingecko_id, row.kraken_pair, chain=row.chain),
        fund=nav_source(fund_source(row.isin), row.isin, stored.get(row.id))
        if row.kind == AssetKind.ETF.value
        else None,
    )


def _with_priced_as(
    conn: Connection, held: list[Held], stored: dict[UUID, NavSource]
) -> list[Held]:
    """The priced_as assets of the chain assets with a current movement, each once, from the
    earliest first day (its own held movements' or the chain assets')."""
    m, a = movement_table.c, asset_table.c
    twins = dict(
        conn.execute(
            select(a.priced_as_asset_id, func.min(m.occurred_at))
            .join(movement_table, m.asset_id == a.id)
            .where(
                a.priced_as_asset_id.is_not(None),
                a.spam.is_(False),
                m.superseded_by.is_(None),
            )
            .group_by(a.priced_as_asset_id)
        ).all()
    )
    if not twins:
        return held
    # The held assets keep their order; a priced_as asset not held itself comes after them.
    out = [
        replace(h, first_on=min(h.first_on, twins[h.id].astimezone(UTC).date()))
        if h.id in twins
        else h
        for h in held
    ]
    known = {h.id for h in held}
    missing = [twin_id for twin_id in twins if twin_id not in known]
    if missing:
        rows = conn.execute(
            select(a.id, a.kind, a.symbol, a.isin, a.name, a.coingecko_id, a.kraken_pair, a.chain)
            .where(a.id.in_(missing), a.spam.is_(False))
            .order_by(a.name, a.id)
        )
        out += [_held(row, twins[row.id], stored) for row in rows]
    return out


def held_assets(conn: Connection) -> list[Held]:
    a = asset_table.c
    sums = held_subquery()
    stored = stored_nav_sources(conn)
    rows = conn.execute(
        select(
            a.id,
            a.kind,
            a.symbol,
            a.isin,
            a.name,
            a.coingecko_id,
            a.kraken_pair,
            a.chain,
            sums.c.first_at,
        )
        .join(sums, sums.c.asset_id == a.id)
        .where(
            # Phase 7: priced through its priced_as asset (below), or spam: never fetched.
            a.priced_as_asset_id.is_(None),
            a.spam.is_(False),
        )
        .order_by(a.name, a.id)
    ).all()
    return _with_priced_as(conn, [_held(r, r.first_at, stored) for r in rows], stored)


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
        .join(asset_table, a.id == p.asset_id)
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


def _rate_draft(code: str, rate: FxRate) -> Draft:
    return Draft(
        asset_id=None,
        currency=code,
        on=rate.on,
        price_eur=(Decimal(1) / rate.per_eur).quantize(PLACES, rounding=ROUND_HALF_UP),
        source=S.ECB_FX,
        raw={"per_eur": str(rate.per_eur)},
    )


def _gap(stored: list[FxRate], today: date) -> bool:
    """True when the daily file cannot close the gap since the last stored rate."""
    last = max((r.on for r in stored), default=None)
    return last is None or (today - last).days > ecb_fx.BACKFILL_AFTER_DAYS


@dataclass
class Fx:
    """The run's euro rates per currency: the workspace's stored ones plus the day's new
    ones. A currency outside FX_CURRENCIES is stored on every run once the workspace holds a
    rate of it, like the dollar, and on its first need for a NAV (follow-up C); the 90-day
    file is read too when its last stored rate is older than the daily file can bridge."""

    sources: Sources
    today: date
    out: Collected
    stored: dict[str, list[FxRate]]  # the workspace's stored rates, as planned
    backfill: bool  # the run's first ECB call read the 90-day file
    base: dict[str, list[FxRate]] | SourceError
    merged: dict[str, list[FxRate]] = field(default_factory=dict)
    covered: dict[str, bool | None] = field(default_factory=dict)

    def answers(self, code: str) -> list[dict[str, list[FxRate]]]:
        """The run's readable ECB answers for `code`: the first call's, plus the 90-day
        file when the code's stored rates leave a gap the daily file cannot close."""
        answers = [self.base]
        if not self.backfill and _gap(self.stored.get(code, []), self.today):
            answers.append(self.sources.ecb(True))
        return [a for a in answers if not isinstance(a, SourceError)]

    def add(self, code: str, answers: list[dict[str, list[FxRate]]]) -> None:
        """Draft and merge the code's rates not stored yet, each day once."""
        listed = self.merged.setdefault(code, [])
        known = {r.on for r in listed}
        for answer in answers:
            for rate in answer.get(code, []):
                if rate.on in known or rate.on > self.today:
                    continue
                known.add(rate.on)
                self.out.drafts.append(_rate_draft(code, rate))
                listed.append(rate)
        listed.sort(key=lambda r: r.on)

    def cover(self, code: str) -> bool | None:
        """Whether a NAV in `code` can be converted: True when the run's ECB answer lists a
        rate of it on the answer's newest day (its rates are then merged); False when the
        answer was read and has none that day, so a currency the ECB stopped publishing is
        never converted at a frozen rate (review of PR #391); None when the ECB could not be
        read (the stored rates then convert, as for the dollar, and the next run tries
        again)."""
        if code == "EUR" or code in ecb_fx.FX_CURRENCIES:
            return True
        if code not in self.covered:
            read = self.answers(code)
            if not read:
                self.covered[code] = None
            else:
                newest = max(
                    (r.on for a in read for listed in a.values() for r in listed), default=None
                )
                current = any(r.on == newest for a in read for r in a.get(code, []))
                if current:
                    self.add(code, read)
                self.covered[code] = current
        return self.covered[code]


def _ecb(
    plan: Plan, sources: Sources, today: date, rates: dict[str, list[FxRate]], out: Collected
) -> Fx:
    """The run's first ECB call, before any other source: the new rates of FX_CURRENCIES and
    of every other currency the workspace already holds a rate of, whether or not a fund's
    file is read this run (review of PR #391: a NAV day a failed file leaves to a later run
    must find its own day's rate), each currency against its own stored days (`rates`)."""
    last = max(plan.ecb_days, default=None)
    backfill = last is None or (today - last).days > ecb_fx.BACKFILL_AFTER_DAYS
    answer = sources.ecb(backfill)
    out.sources.add(S.ECB_FX)
    fx = Fx(
        sources=sources,
        today=today,
        out=out,
        stored=rates,
        backfill=backfill,
        base=answer,
        merged={code: sorted(listed, key=lambda r: r.on) for code, listed in rates.items()},
    )
    if not isinstance(answer, SourceError):
        for code in ecb_fx.FX_CURRENCIES:
            fx.add(code, [answer])
        for code in sorted(rates):
            if code != "EUR" and code not in ecb_fx.FX_CURRENCIES:
                fx.add(code, fx.answers(code))
    return fx


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


def _moved(error: SourceError) -> bool:
    """A file gone (404, 410; 400, which iShares answers for a product id it does not know)
    or naming another fund: the issuer moved or renumbered the fund (follow-up A). A
    timeout, a 5xx or an unreadable file is not. A wrong guess costs one catalogue lookup,
    and the file read through the catalogue's reference must state the ISIN anyway."""
    if error.kind == "isin_mismatch":
        return True
    return error.kind == "HTTPError" and error.status in (400, 404, 410)


def _read(
    held: Held, fund: FundSource, sources: Sources, out: Collected
) -> tuple[NavFile | SourceError, FundSource]:
    """The fund's file. A stored catalogue reference that stopped working is looked up again
    in its issuer's catalogue, and a different reference listed there is read at once."""
    answer = sources.nav(fund)
    if (
        isinstance(answer, SourceError)
        and isinstance(fund, NavSource)
        and fund is held.fund  # the stored reference, not one the catalogue gave this run
        and _moved(answer)
    ):
        found = sources.listed(fund.issuer, fund.isin)
        if found is not None and found.fund_ref != fund.fund_ref:
            out.discovered[held.id] = found
            return sources.nav(found), found
    return answer, fund


def _note(held: Held, fund: FundSource, currency: str | None = None) -> FundSourceNote:
    return FundSourceNote(
        asset_id=held.id, issuer=fund.issuer, fund_ref=fund.fund_ref or None, currency=currency
    )


def _etf(
    held: Held, fund: FundSource, plan: Plan, sources: Sources, fx: Fx, out: Collected
) -> None:
    out.assets += 1
    out.sources.add(S.ISSUER_NAV)
    answer, fund = _read(held, fund, sources, out)
    if isinstance(answer, SourceError):
        out.failed.append(held.id)
        out.notes.append(_note(held, fund))
        return
    if fx.cover(answer.currency) is False:
        # Never a price in another currency: the card names the fund and the currency.
        out.unconverted[held.id] = answer.currency
        out.notes.append(_note(held, fund, answer.currency))
        return
    stored = plan.stored.get((held.id, S.ISSUER_NAV.value), set())
    rows = valuation.nav_rows(
        answer.points, held.first_on, fx.merged.get(answer.currency, []), stored_days=stored
    )
    for row in sorted(rows, key=lambda r: r.on, reverse=True)[:MAX_ROWS]:
        raw: dict[str, Any] = {"nav": str(row.nav), "currency": row.currency}
        if row.rate_on is not None:
            raw["rate_on"] = row.rate_on.isoformat()
        if isinstance(fund, NavSource):  # kept for the next runs and the screens
            raw |= {"issuer": fund.issuer, "fund_ref": fund.fund_ref}
        out.drafts.append(Draft(held.id, row.on, row.price_eur, S.ISSUER_NAV, raw))


def collect(plan: Plan, sources: Sources, today: date, rates: dict[str, list[FxRate]]) -> Collected:
    """`rates` are the workspace's stored ECB rates; the day's new ones are added to them."""
    out = Collected()
    if not plan.held:
        return out
    fx = _ecb(plan, sources, today, rates, out)
    for held in plan.held:
        if held.kind is AssetKind.CRYPTO and held.ids.any:
            _coin(held, plan, sources, today, out)
    for held in plan.held:
        if held.kind is not AssetKind.ETF:
            continue
        fund = held.fund
        if fund is None and held.isin:
            fund = sources.discover(held.isin, held.name)
            if fund is not None:
                out.discovered[held.id] = fund
        if fund is not None:
            _etf(held, fund, plan, sources, fx, out)
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
        .returning(p.id)
    )
    return len(ctx.conn.execute(statement).all())  # rowcount is -1 for a multi-row VALUES


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


@dataclass(frozen=True)
class RunNote:
    """What the recent runs said of a failed or unconverted fund (FundSourceNote, read back):
    the issuer source last asked, and, when the latest run could not convert its NAV, the
    currency and the first day of the runs in a row up to the latest that said so."""

    issuer: str
    fund_ref: str | None
    currency: str | None = None
    unconverted_since: date | None = None


def fund_notes(conn: Connection, since: datetime) -> dict[UUID, RunNote]:
    """Per fund the notes of the prices_fetched runs since `since` (follow-ups B and C)."""
    e = event_table.c
    rows = conn.execute(
        select(e.recorded_at, e.payload)
        .where(e.type == EventType.WORKSPACE_PRICES_FETCHED.value, e.recorded_at >= since)
        .order_by(e.recorded_at.desc())
    ).all()
    found: dict[UUID, RunNote] = {}
    open_streak: set[UUID] = set()
    for i, row in enumerate(rows):
        day = row.recorded_at.astimezone(UTC).date()
        notes = {UUID(n["asset_id"]): n for n in row.payload.get("fund_sources") or []}
        for asset_id, n in notes.items():
            if asset_id in found:
                continue
            currency = n.get("currency") if i == 0 else None
            found[asset_id] = RunNote(
                issuer=n["issuer"],
                fund_ref=n.get("fund_ref"),
                currency=currency,
                unconverted_since=day if currency else None,
            )
            if currency:
                open_streak.add(asset_id)
        if i == 0:
            continue
        for asset_id in list(open_streak):
            note = notes.get(asset_id)
            if note is not None and note.get("currency") == found[asset_id].currency:
                found[asset_id] = replace(found[asset_id], unconverted_since=day)
            else:
                open_streak.discard(asset_id)
    return found


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
            fund_sources=out.notes,
        ),
    )
    since = ctx.now - timedelta(days=STREAK_WINDOW_DAYS)
    held = {h.id: h for h in plan.held}
    for asset_id in out.failed:
        if held[asset_id].kind is not AssetKind.ETF:
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
    "Fx",
    "Plan",
    "RunNote",
    "Sources",
    "collect",
    "failure_runs",
    "fund_notes",
    "held_assets",
    "held_subquery",
    "plan",
    "run_workspace",
    "stored_rates",
    "write",
]
