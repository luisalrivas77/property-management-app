"""The daily price fetch of one workspace (docs/phases/phase-6.md D167 (3), D174).

In the order D167 (3) gives: the ECB rates first (the NAVs in another currency need them),
then each held coin, then each held ETF's NAV. "Held" is a positive sum of confirmed,
current movements on at least one of the workspace's accounts; the first of those movements bounds
every source (no price before it). Since D174 amended 20 Nov 2026 an asset held earlier and
sold or moved out since is planned too, for the days it was held only (`planned_assets`,
`Held.last_on`, its last movement): every source, the first-read check and „no guess“ as for
a held asset, nothing stored after its last held day. It leaves the plan once a run
settled its held stretch (read every source it asked, stored nothing new, cut nothing for
MAX_ROWS, skipped nothing for a missing rate) or MAX_SOLD_RUNS runs asked it, both read from
the runs' events (`sold_stretches`), never from its stored rows or the sale's date; a coin
with every reachable held day priced has nothing to ask (`_nothing_to_ask`). It raises no
card and no `asset.price_source_failed` event, since no account holds it today, and a
fund's failure streak counts only the runs after its latest sold one.
The cards keep reading `held_subquery` („held today“). Three phases, so no network call runs
inside a database transaction:

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

The Lang & Schwarz exchange price (D264, `lang_schwarz`): a held fund with an ISIN whose name
names no adapter's brand is asked every run; a fund whose issuer's adapter is `latest_only`
(L&G) is asked once for its history, while no `exchange_close` row of it is stored, and that
one read stores every missing day from the first movement on (no MAX_ROWS). The instrument
id comes from the fund's latest `exchange_close` row (`raw.instrument_id`), else the search
by ISIN; a stored id whose history is gone or names another ISIN is searched again once in
the run (as follow-up A). A first read more than 2 % from the report's price on a report day
stores nothing (`PricesFetched.exchange_disagreed`). A daily fund whose read fails is a
failed fetch naming `ls_tc` (D174's card after seven days, `source = exchange_close`); the
one-off read failing is only counted in the log line. A search that finds no one listing is
a lookup miss, never a failure.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, case, func, null, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import price as price_table
from hausbuch.rules import valuation
from hausbuch.schemas.events import (
    EventType,
    FundSourceNote,
    PricesFetched,
    PriceSourceFailed,
    SoldStretchNote,
)
from hausbuch.schemas.household import AssetKind, MovementStatus
from hausbuch.schemas.prices import FxRate, NavPoint, PriceSourceKind
from hausbuch.services.context import Actor, CommandContext, new_command
from hausbuch.services.events import append_event

from . import ecb_fx, kraken, lang_schwarz
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
from .coingecko import HISTORY_DAYS, CoinGeckoDaily
from .http import Fetch, SourceError
from .issuer_nav import ADAPTERS, IssuerAdapter, NavFile, read_points

S = PriceSourceKind
DAILY_SOURCES = (S.ECB_FX, S.COINGECKO_DAILY, S.KRAKEN_DAILY, S.ISSUER_NAV, S.EXCHANGE_CLOSE)
MAX_COIN_DAYS = 31  # D167 (3): CoinGecko calls per asset and run
MAX_ROWS = 400  # D174 (3): rows per asset and run from one file
KRAKEN_DAYS = 720  # kraken.daily: the 720 most recent daily candles
COINGECKO_DAYS = HISTORY_DAYS  # the Demo tier's reach
MAX_SOLD_RUNS = 10  # D174 amended 20 Nov 2026: runs a sold asset's held stretch is asked
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
    # D174 amended 20 Nov 2026: the last day an asset no account holds today was held (its
    # last movement); None while some account holds it (priced to today).
    last_on: date | None = None

    def end(self, today: date) -> date:
        """The last day the asset is priced for: today while held, else its last held day."""
        return today if self.last_on is None else min(self.last_on, today)

    def within(self, on: date) -> bool:
        """Whether `on` is not after the last held day (always, while held)."""
        return self.last_on is None or on <= self.last_on


@dataclass
class Plan:
    held: list[Held]
    stored: dict[tuple[UUID, str], set[date]]
    ecb_days: set[date]  # the days of the workspace's stored ECB rows (any currency)
    # The depot reports' prices (EUR) per held fund and day, for the first-read check of a
    # new NAV source (D174 amended 19 Nov 2026 (g)).
    reports: dict[UUID, dict[date, Decimal]] = field(default_factory=dict)
    # D264: per fund the Lang & Schwarz instrument id of its latest exchange_close row
    exchange_ids: dict[UUID, int] = field(default_factory=dict)


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
    nav_disagreed: list[UUID] = field(default_factory=list)  # first reads the check refused
    nav_unresolved: list[UUID] = field(default_factory=list)  # no one reference (h)
    exchange_disagreed: list[UUID] = field(default_factory=list)  # D264 (5)
    # D264 (6): the source a failed asset was asked from, when not its issuer's NAV
    failed_sources: dict[UUID, PriceSourceKind] = field(default_factory=dict)
    # D174 amended 20 Nov 2026: per asset no account holds today, whether this run settled
    # its held stretch (every source it asked read, nothing new stored, nothing cut for the
    # row limit, nothing skipped for a missing rate); absent means not settled
    settled: dict[UUID, bool] = field(default_factory=dict)


@dataclass
class Sources:
    """Every source of one run, each answer kept for the run. `coingecko` is None without
    the key (Kraken alone then). `errors` counts (source, class, status) for the log line."""

    fetch: Fetch
    coingecko: CoinGeckoDaily | None
    adapters: dict[str, IssuerAdapter] = field(default_factory=dict)
    errors: Counter = field(default_factory=Counter)
    # D174 amended (d), (k): a lookup's miss (no such fund, another fund), counted for
    # the log line apart from `errors`, since a miss is no failure
    misses: Counter = field(default_factory=Counter)
    # (issuer, ISIN, the stored reference or "") of the lookups that found no one
    # reference for an adapter whose candidates exclude each other (D174 amended (h))
    unresolved: set[tuple[str, str, str]] = field(default_factory=set)
    _ecb: dict[bool, dict[str, list[FxRate]] | SourceError] = field(default_factory=dict)
    _kraken: dict[str, dict[date, Decimal] | SourceError] = field(default_factory=dict)
    _coingecko: dict[tuple[str, date], Decimal | None] = field(default_factory=dict)
    _coingecko_error: SourceError | None = None
    _nav: dict[tuple[str, str, str], NavFile | SourceError] = field(default_factory=dict)
    _catalogues: dict[str, dict[str, str] | SourceError] = field(default_factory=dict)
    _history: dict[str, list[FxRate]] | SourceError | None = None
    _exchange_ids: dict[str, int | SourceError] = field(default_factory=dict)
    _exchange: dict[tuple[int, str], list[NavPoint] | SourceError] = field(default_factory=dict)

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

    def ecb_history(self) -> dict[str, list[FxRate]] | SourceError:
        """The ECB's full history ZIP (D174 amended 19 Nov 2026 (a)), asked once a run."""
        if self._history is None:
            try:
                self._history = ecb_fx.history(self.fetch)
            except SourceError as exc:
                self._history = self._failed(f"{S.ECB_FX.value}:history", exc)
        return self._history

    def exchange_instrument(self, isin: str) -> int | SourceError:
        """D264 (2): the fund's Lang & Schwarz instrument id, searched once a run; a search
        that finds no one listing is a miss, counted apart from the failures."""
        if isin not in self._exchange_ids:
            try:
                self._exchange_ids[isin] = lang_schwarz.instrument(self.fetch, isin)
            except SourceError as exc:
                key = f"{S.EXCHANGE_CLOSE.value}:search"
                if exc.kind == "not_listed":
                    self.misses[(key, exc.kind, exc.status)] += 1
                else:
                    exc = self._failed(key, exc)
                self._exchange_ids[isin] = exc
        return self._exchange_ids[isin]

    def exchange_history(
        self, instrument_id: int, isin: str, today: date
    ) -> list[NavPoint] | SourceError:
        """D264 (2): the instrument's past weekdays, read once a run."""
        key = (instrument_id, isin)
        if key not in self._exchange:
            try:
                self._exchange[key] = lang_schwarz.history(self.fetch, instrument_id, isin, today)
            except SourceError as exc:
                self._exchange[key] = self._failed(S.EXCHANGE_CLOSE.value, exc)
        return self._exchange[key]

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

    def probe(
        self, issuer: str, isin: str, name: str, since: date | None = None, skip: str = ""
    ) -> NavSource | None:
        """D174 amended 19 Nov 2026: an issuer without a catalogue is asked by the fund
        itself. Each candidate reference (the ISIN; BNP's per currency; HANetf's pages for
        the name) is read until one states the ISIN; the file read is kept for the run. A
        miss (no such fund, another fund) is no failure and is not counted: the fund stays
        on its report price.
        An answer that is neither „no such fund“ nor „another fund“ ends the lookup."""
        adapter = self._adapter(issuer)
        try:
            refs = adapter.candidates(isin, name)
        except SourceError as exc:
            self._failed(f"{S.ISSUER_NAV.value}:{issuer}:find", exc)
            return None
        except Exception:  # noqa: BLE001 - a lookup that cannot read the issuer finds nothing
            self._failed(f"{S.ISSUER_NAV.value}:{issuer}:find", SourceError("parse"))
            return None
        if not refs:
            self.misses[(f"{S.ISSUER_NAV.value}:{issuer}", "no_candidate", None)] += 1
            return None
        if adapter.one_answer:
            return self._one_answer(issuer, isin, refs, since, skip)
        for ref in refs:
            if ref == skip:
                continue
            found = NavSource(issuer=issuer, fund_ref=ref, isin=isin)
            if adapter.checked_candidates:
                return found  # its page stated the ISIN; the run reads its file next
            answer = self.nav(found, since, count_missing=False)
            if not isinstance(answer, SourceError):
                return found
            if not _missing(answer):
                break
        return None

    def _one_answer(
        self, issuer: str, isin: str, refs: list[str], since: date | None, skip: str
    ) -> NavSource | None:
        """D174 amended after the review (h): every candidate is read once. Discovery
        keeps the one that answers when exactly one does; none or several leave the fund
        unresolved. A stored reference (`skip`) is never replaced: another candidate that
        answers leaves it unresolved, none answering is a failed fetch as before. An
        answer that cannot tell (a timeout, a 5xx) ends the lookup and names nothing."""
        answered: list[NavSource] = []
        for ref in refs:
            if ref == skip:
                continue
            found = NavSource(issuer=issuer, fund_ref=ref, isin=isin)
            answer = self.nav(found, since, count_missing=False)
            if isinstance(answer, SourceError):
                if _missing(answer):
                    continue
                return None  # counted in errors; asked again the next run
            answered.append(found)
        if not skip and len(answered) == 1:
            return answered[0]
        if answered or not skip:
            self.unresolved.add((issuer, isin, skip))
        return None

    def is_unresolved(self, fund: NavSource | None, isin: str) -> bool:
        """Whether this run's lookup for `fund`'s stored reference (or, with None, for
        the fund's discovery) found no one reference (D174 amended (h))."""
        if fund is None:
            return any(i == isin and not s for _, i, s in self.unresolved)
        return (fund.issuer, isin, fund.fund_ref) in self.unresolved

    def relocate(self, fund: NavSource, name: str, since: date | None = None) -> NavSource | None:
        """Another reference for a stored one that stopped working (follow-up A): the
        issuer's catalogue, else its other candidates."""
        if fund.issuer not in ADAPTERS:
            return None
        if ADAPTERS[fund.issuer].catalogue_url is not None:
            return self.listed(fund.issuer, fund.isin)
        return self.probe(fund.issuer, fund.isin, name, since, skip=fund.fund_ref)

    def discover(self, isin: str, name: str, since: date | None = None) -> NavSource | None:
        """The fund's reference from the issuer its name names: the issuer's catalogue (an
        iShares fund's from iShares'), or the issuer asked by the fund itself (D174 amended
        19 Nov 2026). A fund whose name names no adapter's brand asks nothing."""
        for issuer, cls in ADAPTERS.items():
            if not cls.names_brand(name):
                continue
            if cls.catalogue_url is not None:
                found = self.listed(issuer, isin)
            else:
                found = self.probe(issuer, isin, name, since)
            if found is not None:
                return found
        return None

    def nav(
        self, fund: FundSource, since: date | None = None, *, count_missing: bool = True
    ) -> NavFile | SourceError:
        """The fund's file, read once a run (from `since`, the first movement, where the
        issuer takes a date range). A lookup's miss (`count_missing` False) is not counted."""
        key = (fund.issuer, fund.fund_ref or "", fund.isin)
        if key not in self._nav:
            adapter = self._adapter(fund.issuer)
            try:
                self._nav[key] = read_points(adapter, fund.fund_ref or "", fund.isin, since)
            except SourceError as exc:
                if count_missing or not _missing(exc):
                    exc = self._failed(f"{S.ISSUER_NAV.value}:{fund.issuer}", exc)
                else:
                    self.misses[(f"{S.ISSUER_NAV.value}:{fund.issuer}", exc.kind, exc.status)] += 1
                self._nav[key] = exc
        return self._nav[key]


# --- 1. plan ------------------------------------------------------------------------------


def _current() -> list[Any]:
    m = movement_table.c
    return [m.status == MovementStatus.CONFIRMED.value, m.superseded_by.is_(None)]


def _held_ids():
    """The ids of the assets some account holds today: a positive sum of confirmed, current
    movements on at least one account."""
    m = movement_table.c
    per_account = (
        select(m.asset_id, func.sum(m.quantity).label("units"))
        .where(*_current())
        .group_by(m.account_id, m.asset_id)
        .subquery()
    )
    return select(per_account.c.asset_id).where(per_account.c.units > 0)


def held_subquery():
    """(asset_id, first_at) of every asset some account holds: a positive sum of confirmed,
    current movements on at least one account, as the Übersicht shows positions per account.
    Not the sum over all accounts (16 Nov 2026): units moved from one own depot to another,
    when the first depot's purchases came before the app, net to zero across both and would
    leave the position the Übersicht shows unpriced. `first_at` is the asset's first movement
    on any account."""
    m = movement_table.c
    current = _current()
    held_ids = _held_ids()
    return (
        select(m.asset_id, func.min(m.occurred_at).label("first_at"))
        .where(*current, m.asset_id.in_(held_ids))
        .group_by(m.asset_id)
        .subquery()
    )


def _held(
    row: Any, first_at: datetime, stored: dict[UUID, NavSource], last_at: datetime | None = None
) -> Held:
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
        last_on=None if last_at is None else last_at.astimezone(UTC).date(),
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
    # A twin is priced to today whether or not the symbol-level asset is still held itself.
    out = [
        replace(h, first_on=min(h.first_on, twins[h.id].astimezone(UTC).date()), last_on=None)
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


def _asset_columns() -> list[Any]:
    a = asset_table.c
    return [a.id, a.kind, a.symbol, a.isin, a.name, a.coingecko_id, a.kraken_pair, a.chain]


def _fetched_where() -> list[Any]:
    """Phase 7: priced through its priced_as asset (`_with_priced_as`), or spam: never
    fetched."""
    a = asset_table.c
    return [a.priced_as_asset_id.is_(None), a.spam.is_(False)]


def held_assets(conn: Connection) -> list[Held]:
    """The assets some account holds today (`held_subquery`), each with `last_on` None."""
    a = asset_table.c
    sums = held_subquery()
    stored = stored_nav_sources(conn)
    rows = conn.execute(
        select(*_asset_columns(), sums.c.first_at)
        .join(sums, sums.c.asset_id == a.id)
        .where(*_fetched_where())
        .order_by(a.name, a.id)
    ).all()
    return _with_priced_as(conn, [_held(r, r.first_at, stored) for r in rows], stored)


def held_ever_subquery():
    """(asset_id, first_at, last_at) of every asset with confirmed, current movements on some
    own account (D174 amended 20 Nov 2026): `first_at` its first movement on any account;
    `last_at` None while some account holds a unit (`held_subquery`'s rule), else its last
    movement with a quantity, after which no own account holds a unit (a later movement of
    0 units, such as a distribution note, does not extend it). An asset held again after a
    sale is priced from its first movement on, never beyond its last held day."""
    m = movement_table.c
    current = _current()
    held_ids = _held_ids()
    last_moved = func.max(m.occurred_at).filter(m.quantity != 0)
    return (
        select(
            m.asset_id,
            func.min(m.occurred_at).label("first_at"),
            case(
                (func.bool_or(m.asset_id.in_(held_ids)), null()),
                else_=func.coalesce(last_moved, func.max(m.occurred_at)),
            ).label("last_at"),
        )
        .where(*current)
        .group_by(m.asset_id)
        .subquery()
    )


def planned_assets(conn: Connection) -> list[Held]:
    """The assets the daily fetch prices (D174 amended 20 Nov 2026): every asset held at
    some time, each for the days it was held (`Held.last_on`), the ones some account holds
    today first, so a past asset's failing source never ends a source (CoinGecko) before a
    held asset was asked. `held_assets` keeps meaning „held today“ for the cards."""
    a = asset_table.c
    sums = held_ever_subquery()
    stored = stored_nav_sources(conn)
    rows = conn.execute(
        select(*_asset_columns(), sums.c.first_at, sums.c.last_at)
        .join(sums, sums.c.asset_id == a.id)
        .where(*_fetched_where())
        .order_by(a.name, a.id)
    ).all()
    planned = _with_priced_as(conn, [_held(r, r.first_at, stored, r.last_at) for r in rows], stored)
    return sorted(planned, key=lambda h: h.last_on is not None)  # stable: by name within


def _nothing_to_ask(
    held: Held, stored: dict[tuple[UUID, str], set[date]], today: date | None = None
) -> bool:
    """Whether `collect` has nothing to ask for an asset no account holds today, so it never
    enters the plan (and a workspace that holds nothing today stays a run that asks
    nothing): an asset collect never prices (a stock, a coin without ids), and a coin every
    day of whose held days its sources still reach (Kraken's 720 days with a pair, else
    CoinGecko's 365, counted back from `today`; every held day without `today`) has a
    CoinGecko or Kraken price. Coins trade every day, so that is the exact set of days a
    read could add, never a nearness to stored rows; a fund's days are never judged from
    its rows (D174 amended 20 Nov 2026, „When a sold asset is done“: its weekends and
    holidays make no such set): it leaves the plan by its runs only (`sold_stretches`)."""
    if held.last_on is None:
        return False
    if held.kind is AssetKind.CRYPTO:
        if not held.ids.any:
            return True  # never asked (no source), as a held coin without ids
        days = stored.get((held.id, S.COINGECKO_DAILY.value), set()) | stored.get(
            (held.id, S.KRAKEN_DAILY.value), set()
        )
        start = held.first_on
        if today is not None:
            reach = KRAKEN_DAYS if held.ids.kraken_pair is not None else COINGECKO_DAYS
            start = max(start, today - timedelta(days=reach - 1))
        return all(d in days for d in _days(start, held.last_on))
    return held.kind is not AssetKind.ETF  # collect prices coins and funds only


StretchKey = tuple[UUID, date, date]


def sold_stretches(conn: Connection) -> dict[StretchKey, tuple[int, bool]]:
    """Per (asset, first held day, last held day) of an asset no account holds today: how
    many daily runs asked it and whether one of them settled it, from every
    workspace.prices_fetched event's `sold_stretches` (D174 amended 20 Nov 2026). A
    stretch whose first or last day changed is another key, asked afresh."""
    e = event_table.c
    found: dict[StretchKey, tuple[int, bool]] = {}
    for (listed,) in conn.execute(
        select(e.payload["sold_stretches"]).where(
            e.type == EventType.WORKSPACE_PRICES_FETCHED.value,
            e.payload.has_key("sold_stretches"),
        )
    ):
        for note in listed or []:
            key = (
                UUID(note["asset_id"]),
                date.fromisoformat(note["first_on"]),
                date.fromisoformat(note["last_on"]),
            )
            runs, settled = found.get(key, (0, False))
            found[key] = (runs + 1, settled or bool(note.get("settled")))
    return found


def _asked_out(held: Held, stretches: dict[StretchKey, tuple[int, bool]]) -> bool:
    """A sold asset's stretch that a run settled, or that MAX_SOLD_RUNS runs asked whatever
    happened (a lookup miss, a refused first read, a currency the ECB lacks, a file that
    keeps failing): it leaves the plan, the month-end keeping its to-do."""
    if held.last_on is None:
        return False
    runs, settled = stretches.get((held.id, held.first_on, held.last_on), (0, False))
    return settled or runs >= MAX_SOLD_RUNS


def plan(conn: Connection, today: date | None = None) -> Plan:
    """`today` bounds how far back a sold coin's sources still reach (`_nothing_to_ask`)."""
    p = price_table.c
    stored: dict[tuple[UUID, str], set[date]] = defaultdict(set)
    ecb_days: set[date] = set()
    for row in conn.execute(
        select(p.asset_id, p.source, p.on).where(p.source.in_([s.value for s in DAILY_SOURCES]))
    ):
        stored[(row.asset_id, row.source)].add(row.on)
        if row.source == S.ECB_FX.value:
            ecb_days.add(row.on)
    stretches = sold_stretches(conn)
    held = [
        h
        for h in planned_assets(conn)
        if not _nothing_to_ask(h, stored, today) and not _asked_out(h, stretches)
    ]
    return Plan(
        held=held,
        stored=dict(stored),
        ecb_days=ecb_days,
        reports=report_prices(conn, held),
        exchange_ids=exchange_ids(conn),
    )


def exchange_ids(conn: Connection) -> dict[UUID, int]:
    """D264 (3): per fund the instrument id its latest exchange_close row names."""
    p = price_table.c
    rows = conn.execute(
        select(p.asset_id, p.raw["instrument_id"].astext)
        .where(p.source == S.EXCHANGE_CLOSE.value, p.raw.has_key("instrument_id"))
        .order_by(p.asset_id, p.on.desc(), p.created_at.desc())
        .distinct(p.asset_id)
    )
    found: dict[UUID, int] = {}
    for asset_id, value in rows:
        if value and value.isdigit() and int(value) > 0:
            found[asset_id] = int(value)
    return found


def report_prices(conn: Connection, held: list[Held]) -> dict[UUID, dict[date, Decimal]]:
    """The held funds' `statement` prices (the depot reports'), per day, the latest fetched
    row of a day winning, as price_on reads one source's rows."""
    funds = [h.id for h in held if h.kind is AssetKind.ETF]
    if not funds:
        return {}
    p = price_table.c
    found: dict[UUID, dict[date, Decimal]] = defaultdict(dict)
    for row in conn.execute(
        select(p.asset_id, p.on, p.price_eur)
        .where(p.source == S.STATEMENT.value, p.asset_id.in_(funds))
        .order_by(p.fetched_at, p.id)
    ):
        found[row.asset_id][row.on] = row.price_eur
    return dict(found)


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
    history: dict[str, list[FxRate]] | None = None  # the history ZIP's rates, when read

    def answers(self, code: str) -> list[dict[str, list[FxRate]]]:
        """The run's readable ECB answers for `code`: the first call's, plus the 90-day
        file when the code's stored rates leave a gap the daily file cannot close."""
        answers = [self.base]
        if not self.backfill and _gap(self.stored.get(code, []), self.today):
            answers.append(self.sources.ecb(True))
        if self.history is not None:
            answers.append(self.history)
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


def _history(plan: Plan, sources: Sources, fx: Fx) -> None:
    """D174 amended 19 Nov 2026 (a): the ECB's full history when the run's earliest rate of
    FX_CURRENCIES is later than the earliest first movement of a held fund, so a foreign NAV
    before the 90-day file finds its own day's rate. Only the days from that first movement
    on (plus the last rate before it) pass through the run's FX, so only missing days are
    stored. A failure is counted in the log line and never fails the run; the next run asks
    again while the gap lasts."""
    first = min((h.first_on for h in plan.held if h.kind is AssetKind.ETF), default=None)
    if first is None:
        return
    need = max(first, ecb_fx.HISTORY_START)
    earliest = [
        min((r.on for r in fx.merged.get(code, [])), default=None) for code in ecb_fx.FX_CURRENCIES
    ]
    if all(on is not None and on <= need for on in earliest):
        return
    answer = sources.ecb_history()
    if isinstance(answer, SourceError):
        return
    trimmed: dict[str, list[FxRate]] = {}
    for code, listed in answer.items():
        before = [r for r in listed if r.on <= need]
        start = max(before, key=lambda r: r.on).on if before else need
        trimmed[code] = [r for r in listed if r.on >= start]
    fx.history = trimmed
    for code in ecb_fx.FX_CURRENCIES:
        fx.add(code, [trimmed])
    for code in sorted(fx.merged):
        if code != "EUR" and code not in ecb_fx.FX_CURRENCIES:
            fx.add(code, [trimmed])


def _coin(held: Held, plan: Plan, sources: Sources, today: date, out: Collected) -> bool:
    """Whether the run settled the coin's days (D174 amended 20 Nov 2026; read for a coin
    no account holds today only): a source read, no error, nothing new, nothing cut."""
    stored = plan.stored.get((held.id, S.COINGECKO_DAILY.value), set()) | plan.stored.get(
        (held.id, S.KRAKEN_DAILY.value), set()
    )
    missing = [d for d in _days(held.first_on, held.end(today)) if d not in stored]
    if not missing:
        return True
    out.assets += 1
    left, added, errors, read, cut = missing, 0, False, False, False
    if sources.coingecko is not None and held.ids.coingecko_id is not None:
        out.sources.add(S.COINGECKO_DAILY)
        reach = sources.coingecko.oldest_day(today)
        reachable = [d for d in missing if d >= reach]
        asked = reachable[:MAX_COIN_DAYS]
        cut = len(reachable) > MAX_COIN_DAYS
        prices, error = sources.coingecko_days(held.ids.coingecko_id, asked)
        for on, price in prices.items():
            out.drafts.append(Draft(held.id, on, price, S.COINGECKO_DAILY, {"eur": str(price)}))
        added += len(prices)
        if error is None:
            read = True
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
            read = True
            found = [d for d in left if d in answer]
            cut = cut or len(found) > MAX_ROWS
            for on in found[:MAX_ROWS]:
                out.drafts.append(
                    Draft(held.id, on, answer[on], S.KRAKEN_DAILY, {"close": str(answer[on])})
                )
                added += 1
    if errors and not added:
        out.failed.append(held.id)
    return read and not errors and not added and not cut


def _moved(error: SourceError) -> bool:
    """A file gone (404, 410; 400, which iShares answers for a product id it does not know)
    or naming another fund: the issuer moved or renumbered the fund (follow-up A). A
    timeout, a 5xx or an unreadable file is not. A wrong guess costs one catalogue lookup,
    and the file read through the catalogue's reference must state the ISIN anyway."""
    if error.kind in ("isin_mismatch", "not_listed"):
        return True
    return error.kind == "HTTPError" and error.status in (400, 404, 410)


def _missing(error: SourceError) -> bool:
    """The issuer answered and has no such fund under this reference (D174 amended 19 Nov
    2026): the next candidate may still be it."""
    return _moved(error)


def _read(
    held: Held, fund: FundSource, sources: Sources, out: Collected
) -> tuple[NavFile | SourceError, FundSource]:
    """The fund's file. A stored catalogue reference that stopped working is looked up again
    in its issuer's catalogue, and a different reference listed there is read at once."""
    answer = sources.nav(fund, held.first_on)
    if (
        isinstance(answer, SourceError)
        and isinstance(fund, NavSource)
        and fund is held.fund  # the stored reference, not one the catalogue gave this run
        and _moved(answer)
    ):
        found = sources.relocate(fund, held.name, held.first_on)
        if found is not None and found.fund_ref != fund.fund_ref:
            out.discovered[held.id] = found
            return sources.nav(found, held.first_on), found
    return answer, fund


def _note(held: Held, fund: FundSource, currency: str | None = None) -> FundSourceNote:
    return FundSourceNote(
        asset_id=held.id, issuer=fund.issuer, fund_ref=fund.fund_ref or None, currency=currency
    )


def _etf(
    held: Held, fund: FundSource, plan: Plan, sources: Sources, fx: Fx, out: Collected
) -> bool:
    """Whether the run settled the fund's held days from its issuer (D174 amended 20 Nov
    2026; read for a fund no account holds today only): the file read and converted, the
    first read not refused, no new row (so nothing cut for MAX_ROWS either) and no held day
    skipped for a missing ECB rate."""
    out.assets += 1
    out.sources.add(S.ISSUER_NAV)
    answer, fund = _read(held, fund, sources, out)
    if (
        isinstance(answer, SourceError)
        and isinstance(fund, NavSource)
        and sources.is_unresolved(fund, fund.isin)
    ):
        # D174 amended (h): another currency answers; the stored reference is kept and
        # nothing is stored, no failed fetch
        out.nav_unresolved.append(held.id)
        return False
    if isinstance(answer, SourceError):
        out.failed.append(held.id)
        out.notes.append(_note(held, fund))
        return False
    if fx.cover(answer.currency) is False:
        # Never a price in another currency: the card names the fund and the currency.
        out.unconverted[held.id] = answer.currency
        out.notes.append(_note(held, fund, answer.currency))
        return False
    stored = plan.stored.get((held.id, S.ISSUER_NAV.value), set())
    if not stored:
        # The first read of this fund from its issuer (D174 amended 19 Nov 2026 (g)): the
        # whole file's newest day the depot report also priced decides whether it is stored.
        check = valuation.nav_check(
            answer.points, fx.merged.get(answer.currency, []), plan.reports.get(held.id, {})
        )
        if check is not None and check.refused:
            out.nav_disagreed.append(held.id)
            return False
    # D174 amended 20 Nov 2026: a sold fund's rows end with its last held day; the check
    # above reads the whole file.
    held_points = [n for n in answer.points if held.within(n.on)]
    rows = valuation.nav_rows(
        held_points, held.first_on, fx.merged.get(answer.currency, []), stored_days=stored
    )
    for row in sorted(rows, key=lambda r: r.on, reverse=True)[:MAX_ROWS]:
        raw: dict[str, Any] = {"nav": str(row.nav), "currency": row.currency}
        if row.rate_on is not None:
            raw["rate_on"] = row.rate_on.isoformat()
        if isinstance(fund, NavSource):  # kept for the next runs and the screens
            raw |= {"issuer": fund.issuer, "fund_ref": fund.fund_ref}
        out.drafts.append(Draft(held.id, row.on, row.price_eur, S.ISSUER_NAV, raw))
    # Settled only when no held day the file prices is still missing: a new row (stored or
    # cut for MAX_ROWS) or a day nav_rows left out for want of an ECB rate keeps it asked.
    return not any(n.on >= held.first_on and n.on not in stored for n in held_points)


def _exchange_failed(held: Held, instrument_id: int | None, out: Collected) -> None:
    out.failed.append(held.id)
    out.failed_sources[held.id] = S.EXCHANGE_CLOSE
    out.notes.append(
        FundSourceNote(
            asset_id=held.id,
            issuer=lang_schwarz.VENUE,
            fund_ref=None if instrument_id is None else str(instrument_id),
        )
    )


def _exchange(
    held: Held, plan: Plan, sources: Sources, today: date, out: Collected, *, once: bool
) -> bool:
    """D264: the fund's Lang & Schwarz prices. `once`: the one-off history of a fund whose
    issuer gives its latest NAV only; it stores every missing day and its failure is only
    counted in the log line. Otherwise the fund's daily source, at most MAX_ROWS a run.
    Whether the run settled its held days (D174 amended 20 Nov 2026): the history read, the
    first read not refused, no new row."""
    assert held.isin is not None
    out.sources.add(S.EXCHANGE_CLOSE)
    if not once:
        out.assets += 1
    stored = plan.stored.get((held.id, S.EXCHANGE_CLOSE.value), set())
    instrument_id: int | SourceError | None = plan.exchange_ids.get(held.id)
    searched = instrument_id is None
    if instrument_id is None:
        instrument_id = sources.exchange_instrument(held.isin)
    if isinstance(instrument_id, SourceError):
        if instrument_id.kind != "not_listed" and not once:
            _exchange_failed(held, None, out)
        return False
    answer = sources.exchange_history(instrument_id, held.isin, today)
    if isinstance(answer, SourceError) and not searched and _moved(answer):
        # A stored id gone or naming another ISIN: the venue renumbered it (as follow-up A).
        found = sources.exchange_instrument(held.isin)
        if not isinstance(found, SourceError) and found != instrument_id:
            instrument_id = found
            answer = sources.exchange_history(instrument_id, held.isin, today)
    if isinstance(answer, SourceError):
        if not once:
            _exchange_failed(held, instrument_id, out)
        return False
    if not stored:
        check = valuation.nav_check(answer, [], plan.reports.get(held.id, {}))
        if check is not None and check.refused:
            out.exchange_disagreed.append(held.id)
            return False
    held_points = [n for n in answer if held.within(n.on)]  # D174 amended 20 Nov 2026
    rows = valuation.nav_rows(held_points, held.first_on, [], stored_days=stored)
    newest = sorted(rows, key=lambda r: r.on, reverse=True)
    for row in newest if once else newest[:MAX_ROWS]:
        raw = {
            "close": str(row.nav),
            "currency": "EUR",
            "venue": lang_schwarz.VENUE,
            "instrument_id": instrument_id,
            "quote": "mid",
        }
        out.drafts.append(Draft(held.id, row.on, row.price_eur, S.EXCHANGE_CLOSE, raw))
    return not rows


def _latest_only(fund: FundSource) -> bool:
    adapter = ADAPTERS.get(fund.issuer)
    return adapter is not None and adapter.latest_only


def collect(plan: Plan, sources: Sources, today: date, rates: dict[str, list[FxRate]]) -> Collected:
    """`rates` are the workspace's stored ECB rates; the day's new ones are added to them."""
    out = Collected()
    if not plan.held:
        return out
    fx = _ecb(plan, sources, today, rates, out)
    _history(plan, sources, fx)
    for held in plan.held:
        if held.kind is AssetKind.CRYPTO and held.ids.any:
            _settle(out, held, _coin(held, plan, sources, today, out))
    for held in plan.held:
        if held.kind is not AssetKind.ETF:
            continue
        settled = False  # a fund no source was asked for (a lookup that found none)
        fund = held.fund
        if fund is None and held.isin:
            fund = sources.discover(held.isin, held.name, held.first_on)
            if fund is not None:
                out.discovered[held.id] = fund
            elif sources.is_unresolved(None, held.isin):
                out.nav_unresolved.append(held.id)  # D174 amended (h)
        if fund is not None:
            settled = _etf(held, fund, plan, sources, fx, out)
            if (
                held.isin
                and _latest_only(fund)
                and not plan.stored.get((held.id, S.EXCHANGE_CLOSE.value))
            ):
                once = _exchange(held, plan, sources, today, out, once=True)  # D264 (1)
                settled = settled and once
        elif held.isin and lang_schwarz.wants_exchange(held.name):
            settled = _exchange(held, plan, sources, today, out, once=False)  # D264 (1)
        _settle(out, held, settled)
    return out


def _settle(out: Collected, held: Held, settled: bool) -> None:
    """Record a sold asset's outcome for the run's event (D174 amended 20 Nov 2026)."""
    if held.last_on is not None:
        out.settled[held.id] = settled


# --- 3. write -----------------------------------------------------------------------------


# The partial unique index's predicate as SQL text, never as bind parameters: psycopg prepares
# a statement server-side after five executions, and a generic plan's parameters cannot prove
# the index's predicate, so the sixth prepared chunk of one write failed with „no unique or
# exclusion constraint matching the ON CONFLICT specification“ and rolled back the whole run.
# The values are the enum's own constants, never input.
_DAILY_KEY_WHERE = text("source IN (" + ", ".join(f"'{s.value}'" for s in DAILY_SOURCES) + ")")


def _insert(ctx: CommandContext, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    p = price_table.c
    statement = (
        pg_insert(price_table)
        .values(rows)
        .on_conflict_do_nothing(
            index_elements=[p.workspace_id, p.asset_id, p.on, p.source],
            index_where=_DAILY_KEY_WHERE,
        )
        .returning(p.id)
    )
    return len(ctx.conn.execute(statement).all())  # rowcount is -1 for a multi-row VALUES


def failure_runs(conn: Connection, asset_id: UUID, since: datetime) -> list[tuple[date, bool]]:
    """(day, failed) per prices_fetched run since `since`: the asset failed in a run when the
    run's event lists it (rules.valuation.failed_days counts the days in a row). Only the
    runs after the latest one that asked it as held by no account (`sold_stretches`), so a
    fund bought again counts its failures from its current held stretch (D174 amended 20
    Nov 2026)."""
    e = event_table.c
    rows = conn.execute(
        select(e.recorded_at, e.payload).where(
            e.type == EventType.WORKSPACE_PRICES_FETCHED.value, e.recorded_at >= since
        )
    ).all()
    return failure_runs_from(rows, asset_id)


def fetched_runs(conn: Connection, since: datetime) -> list:
    """The prices_fetched events since `since` (recorded_at, payload), the newest first: read
    once for every asset's `failure_runs_from` and for `fund_notes_from`."""
    e = event_table.c
    return conn.execute(
        select(e.recorded_at, e.payload)
        .where(e.type == EventType.WORKSPACE_PRICES_FETCHED.value, e.recorded_at >= since)
        .order_by(e.recorded_at.desc())
    ).all()


def failure_runs_from(rows: Iterable[Any], asset_id: UUID) -> list[tuple[date, bool]]:
    """`failure_runs` over the events already read (`fetched_runs`), in the rows' order."""
    rows = list(rows)
    wanted = str(asset_id)
    after = max((r.recorded_at for r in rows if wanted in _sold_in(r.payload)), default=None)
    return [
        (r.recorded_at.astimezone(UTC).date(), wanted in (r.payload.get("failed_asset_ids") or []))
        for r in rows
        if after is None or r.recorded_at > after
    ]


def _sold_in(payload: dict[str, Any]) -> set[str]:
    """The ids a run's event asked as held by no account (D174 amended 20 Nov 2026)."""
    return {n["asset_id"] for n in payload.get("sold_stretches") or []}


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
    """Per fund the notes of the prices_fetched runs since `since` (follow-ups B and C), each
    fund's only from the runs after the latest one that asked it as held by no account
    (D174 amended 20 Nov 2026), as `failure_runs` counts them."""
    return fund_notes_from(fetched_runs(conn, since))


def fund_notes_from(rows: Sequence[Any]) -> dict[UUID, RunNote]:
    """`fund_notes` over the events `fetched_runs` read (the newest first)."""
    found: dict[UUID, RunNote] = {}
    open_streak: set[UUID] = set()
    sold: set[UUID] = set()  # asked as sold in this run or a later one: older notes ignored
    for i, row in enumerate(rows):
        day = row.recorded_at.astimezone(UTC).date()
        sold |= {UUID(a) for a in _sold_in(row.payload)}
        open_streak -= sold
        notes = {
            UUID(n["asset_id"]): n
            for n in row.payload.get("fund_sources") or []
            if UUID(n["asset_id"]) not in sold
        }
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
            nav_disagreed=out.nav_disagreed,
            nav_unresolved=out.nav_unresolved,
            exchange_disagreed=out.exchange_disagreed,
            sold_stretches=[
                SoldStretchNote(
                    asset_id=h.id,
                    first_on=h.first_on,
                    last_on=h.last_on,
                    settled=out.settled.get(h.id, False),
                )
                for h in plan.held
                if h.last_on is not None
            ],
        ),
    )
    since = ctx.now - timedelta(days=STREAK_WINDOW_DAYS)
    held = {h.id: h for h in plan.held}
    fetched: list | None = None  # the runs' events, read once for every failed fund
    for asset_id in out.failed:
        if held[asset_id].kind is not AssetKind.ETF:
            continue  # a coin's failure never raises a card (D167 (3))
        if held[asset_id].last_on is not None:
            continue  # held by no account today: no card (D174 amended 20 Nov 2026)
        if fetched is None:
            fetched = fetched_runs(ctx.conn, since)
        days = valuation.failed_days(failure_runs_from(fetched, asset_id))
        if days < valuation.SOURCE_FAILED_AFTER_DAYS:
            continue
        first = ctx.now.astimezone(UTC).date() - timedelta(days=days - 1)
        start = datetime.combine(first, datetime.min.time(), UTC)
        if _raised_since(ctx.conn, asset_id, start):
            continue
        source = out.failed_sources.get(asset_id, S.ISSUER_NAV)
        last = max(plan.stored.get((asset_id, source.value), set()), default=None)
        append_event(
            ctx,
            EventType.ASSET_PRICE_SOURCE_FAILED,
            asset_id,
            PriceSourceFailed(source=source, failed_days=days, last_price_on=last),
        )
    return new


def run_workspace(
    engine: Engine, workspace_id: UUID, actor: Actor, sources: Sources, now: datetime
) -> tuple[int, Collected]:
    """Plan, collect and write one workspace: two short transactions around the network."""
    today = now.astimezone(UTC).date()
    with workspace_transaction(engine, workspace_id) as conn:
        planned = plan(conn, today)
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
    "MAX_SOLD_RUNS",
    "Collected",
    "Held",
    "Fx",
    "Plan",
    "RunNote",
    "Sources",
    "collect",
    "exchange_ids",
    "failure_runs",
    "failure_runs_from",
    "fetched_runs",
    "fund_notes",
    "fund_notes_from",
    "held_assets",
    "held_ever_subquery",
    "held_subquery",
    "plan",
    "planned_assets",
    "run_workspace",
    "sold_stretches",
    "stored_rates",
    "write",
]
