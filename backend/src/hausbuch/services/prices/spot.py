"""A coin's current price read on demand (docs/phases/phase-10.md §7.7, D291).

„Jetzt aktualisieren“ (jobs.refresh_crypto) asks, for every coin some account holds today
(`fetch.held_assets`, a chain token through its `priced_as` coin):

1. Kraken's public ticker, `GET /0/public/Ticker?pair=<pair>` (no key), for a coin with a
   Kraken pair, the pairs the daily fetch already knows (`assets.price_ids`). One request per
   pair, so an unknown pair fails that coin alone. Kraken answers `{"error": [], "result":
   {<its name for the pair>: {"c": [<last trade price>, <its volume>], ...}}}`; the last trade
   (`c[0]`, a string) is the price.
2. CoinGecko's `GET /api/v3/simple/price?ids=<ids>&vs_currencies=eur`, one request for every
   coin Kraken did not price and that has a CoinGecko id, only when the workspace's CoinGecko
   key resolves (phase 6 D260). The key goes in the header `x-cg-demo-api-key` and nowhere
   else, never in a URL, a log line or an exception.
3. Nothing else: a coin neither priced keeps its last daily close („Kurs von gestern“).

A Kraken pair not quoted in euros is converted with the newest stored ECB rate of its quote
currency (`fetch.stored_rates`, `valuation.to_eur`); a currency without a stored rate prices
nothing. Each price is stored as one `price` row of source `spot`, dated the Berlin day it was
read, `fetched_at` the moment it was read and `raw = {pair, last, read_at}` (CoinGecko: its id
as the pair). Several a day are allowed (migration 0053); rules.valuation reads them for today
only. This is not the daily fetch: no `prices_fetched` event, so a spot read never counts in a
source's failure streak (D174), and no ON CONFLICT (the daily index leaves spot out).

No retry and no model call. Logged by the job: counts and the failed sources by name, error
class and HTTP status only, never a pair's price, a URL or the key.
"""

import json
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import Engine, insert

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import price as price_table
from hausbuch.rules import valuation
from hausbuch.schemas.household import AssetKind, PriceSourceKind
from hausbuch.schemas.prices import FxRate
from hausbuch.services.documents import LOCAL_TIME_ZONE

from . import coingecko, kraken
from .fetch import held_assets, stored_rates
from .http import Fetch, SourceError, get

PLACES = Decimal("0.00000001")
# The quote currencies a Kraken pair may end in, its legacy names with a leading Z first.
QUOTES = ("EUR", "USD", "GBP", "CHF", "JPY", "CAD", "AUD")


def ticker_url(pair: str) -> str:
    return f"{kraken.API_URL}/Ticker?pair={pair}"


def simple_url(coin_ids: Iterable[str]) -> str:
    return f"{coingecko.API_URL}/simple/price?ids={','.join(coin_ids)}&vs_currencies=eur"


def quote_currency(pair: str) -> str | None:
    """The currency a Kraken pair is quoted in (XXBTZEUR → EUR, SOLUSD → USD), or None."""
    upper = pair.upper()
    for quote in QUOTES:
        if upper.endswith(quote) and len(upper) > len(quote):
            return quote
    return None


def _price(value: Any) -> Decimal | None:
    try:
        price = Decimal(str(value)).quantize(PLACES, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return None
    return price if price.is_finite() and price > 0 else None


def parse_ticker(body: bytes, pair: str) -> Decimal:
    """The last trade of one ticker answer. SourceError("parse") for an answer that is not
    Kraken's shape, reports an error (an unknown pair) or carries no positive last trade."""
    try:
        data = json.loads(body)
    except ValueError:
        raise SourceError("parse") from None
    if not isinstance(data, dict) or data.get("error"):
        raise SourceError("parse")
    result = data.get("result")
    if not isinstance(result, dict) or not result:
        raise SourceError("parse")
    entry = result.get(pair)
    if entry is None:  # Kraken may answer under its own name for the pair
        if len(result) != 1:
            raise SourceError("parse")
        (entry,) = result.values()
    try:
        last = _price(entry["c"][0])
    except (KeyError, IndexError, TypeError):
        raise SourceError("parse") from None
    if last is None:
        raise SourceError("parse")
    return last


def parse_simple(body: bytes) -> dict[str, Decimal]:
    """CoinGecko id → EUR price; an id without a positive price is left out."""
    try:
        data = json.loads(body, parse_float=Decimal, parse_int=Decimal)
    except ValueError:
        raise SourceError("parse") from None
    if not isinstance(data, dict):
        raise SourceError("parse")
    prices: dict[str, Decimal] = {}
    for coin_id, quotes in data.items():
        eur = quotes.get("eur") if isinstance(quotes, dict) else None
        price = _price(eur) if isinstance(eur, Decimal) else None
        if price is not None:
            prices[coin_id] = price
    return prices


@dataclass(frozen=True)
class Coin:
    asset_id: UUID
    kraken_pair: str | None
    coingecko_id: str | None


@dataclass(frozen=True)
class SpotRow:
    asset_id: UUID
    price_eur: Decimal
    pair: str
    last: Decimal
    read_at: datetime


@dataclass
class SpotResult:
    coins: int = 0  # held coins with a Kraken pair or a CoinGecko id
    kraken: int = 0  # priced by the ticker
    coingecko: int = 0  # priced by simple/price
    unpriced: int = 0  # keep their last daily close
    errors: Counter = field(default_factory=Counter)  # (source, kind, status) -> n
    stored: int = 0


def held_coins(engine: Engine, workspace_id: UUID) -> tuple[list[Coin], dict[str, list[FxRate]]]:
    """The coins to price and the stored ECB rates, in one read-only transaction."""
    with workspace_transaction(engine, workspace_id) as conn:
        held = held_assets(conn)
        rates = stored_rates(conn)
    coins = [
        Coin(h.id, h.ids.kraken_pair, h.ids.coingecko_id)
        for h in held
        if h.kind is AssetKind.CRYPTO and h.ids.any
    ]
    return coins, rates


def read_prices(
    coins: list[Coin],
    rates: dict[str, list[FxRate]],
    *,
    fetch: Fetch,
    clock: Callable[[], datetime],
    coingecko_key: SecretStr | None,
    result: SpotResult,
) -> list[SpotRow]:
    """Ask the sources (no database); every failure is counted, none raises."""
    rows: list[SpotRow] = []
    left: list[Coin] = []
    asked: dict[str, tuple[Decimal, datetime] | None] = {}  # one request per pair
    for coin in coins:
        pair = coin.kraken_pair
        currency = quote_currency(pair) if pair else None
        if pair is None or currency is None:
            left.append(coin)
            continue
        if pair not in asked:
            try:
                last = parse_ticker(
                    get(fetch, ticker_url(pair), {"Accept": "application/json"}), pair
                )
                asked[pair] = (last, clock())
            except SourceError as exc:
                result.errors[("kraken_ticker", exc.kind, exc.status)] += 1
                asked[pair] = None
        answer = asked[pair]
        eur = None
        if answer is not None:
            last, read_at = answer
            if currency == "EUR":
                eur = last
            else:
                converted = valuation.to_eur(
                    last, read_at.astimezone(LOCAL_TIME_ZONE).date(), rates.get(currency, [])
                )
                eur = None if converted is None else converted[1]
        if eur is None:
            left.append(coin)
            continue
        rows.append(SpotRow(coin.asset_id, eur, pair, last, read_at))
        result.kraken += 1
    gecko = [c for c in left if c.coingecko_id]
    found: dict[str, Decimal] = {}
    read_at = None
    if gecko and coingecko_key is not None:
        headers = {
            "Accept": "application/json",
            coingecko.KEY_HEADER: coingecko_key.get_secret_value(),
        }
        ids = sorted({c.coingecko_id for c in gecko if c.coingecko_id})
        try:
            found = parse_simple(get(fetch, simple_url(ids), headers))
            read_at = clock()
        except SourceError as exc:
            result.errors[("coingecko_simple", exc.kind, exc.status)] += 1
    for coin in left:
        price = found.get(coin.coingecko_id or "")
        if price is None or read_at is None:
            result.unpriced += 1
            continue
        assert coin.coingecko_id is not None
        rows.append(SpotRow(coin.asset_id, price, coin.coingecko_id, price, read_at))
        result.coingecko += 1
    return rows


def store(engine: Engine, workspace_id: UUID, now: datetime, rows: list[SpotRow]) -> int:
    """One `spot` row per price read, in one transaction (its commit moves the data version,
    phase 10 §7.4)."""
    if not rows:
        return 0
    with workspace_transaction(engine, workspace_id) as conn:
        conn.execute(
            insert(price_table),
            [
                {
                    "workspace_id": workspace_id,
                    "asset_id": r.asset_id,
                    "on": r.read_at.astimezone(LOCAL_TIME_ZONE).date(),
                    "price_eur": r.price_eur,
                    "source": PriceSourceKind.SPOT.value,
                    "fetched_at": r.read_at,
                    "raw": {"pair": r.pair, "last": str(r.last), "read_at": r.read_at.isoformat()},
                    "created_at": now,
                }
                for r in rows
            ],
        )
    return len(rows)


def refresh_prices(
    engine: Engine,
    workspace_id: UUID,
    *,
    clock: Callable[[], datetime],
    fetch: Fetch,
    coingecko_key: SecretStr | None,
) -> SpotResult:
    """Read and store a current price for every held coin of the workspace."""
    result = SpotResult()
    coins, rates = held_coins(engine, workspace_id)
    result.coins = len(coins)
    rows = read_prices(
        coins, rates, fetch=fetch, clock=clock, coingecko_key=coingecko_key, result=result
    )
    result.stored = store(engine, workspace_id, clock(), rows)
    return result
