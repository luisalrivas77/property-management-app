"""Kraken's daily candles, the fallback coin price (docs/phases/phase-6.md D167 (3)).

`GET /0/public/OHLC?pair=<pair>&interval=1440` (no key) answers `{"error": [], "result":
{<pair>: [[time, open, high, low, close, vwap, volume, count], ...], "last": n}}` with the
720 most recent daily candles (checked live 13 Nov 2026). `time` is the candle's opening at
00:00 UTC, so a candle is that UTC day and its close (a string, read as a Decimal) is the
day's price. The newest candle is still open and is skipped: a day is stored once it closed.
"""

import json
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .http import Fetch, SourceError, get

API_URL = "https://api.kraken.com/0/public"
DAILY_MINUTES = 1440
PLACES = Decimal("0.00000001")


def ohlc_url(pair: str) -> str:
    return f"{API_URL}/OHLC?pair={pair}&interval={DAILY_MINUTES}"


def parse(body: bytes, pair: str) -> dict[date, Decimal]:
    """Day -> close for every closed candle. SourceError("parse") for an answer that is not
    Kraken's shape or reports an error (an unknown pair answers `EQuery:Unknown asset pair`)."""
    try:
        data = json.loads(body)
    except ValueError:
        raise SourceError("parse") from None
    if not isinstance(data, dict) or data.get("error"):
        raise SourceError("parse")
    result = data.get("result")
    if not isinstance(result, dict):
        raise SourceError("parse")
    candles = result.get(pair)
    if candles is None:  # Kraken may answer under its own key for the pair
        candles = next((v for k, v in result.items() if k != "last"), None)
    if not isinstance(candles, list):
        raise SourceError("parse")
    prices: dict[date, Decimal] = {}
    for candle in candles[:-1]:  # the last candle is still open
        try:
            on = datetime.fromtimestamp(int(candle[0]), UTC).date()
            close = Decimal(str(candle[4])).quantize(PLACES, rounding=ROUND_HALF_UP)
        except (IndexError, TypeError, ValueError, InvalidOperation, OverflowError):
            raise SourceError("parse") from None
        if close > 0:
            prices[on] = close
    return prices


def daily(fetch: Fetch, pair: str) -> dict[date, Decimal]:
    return parse(get(fetch, ohlc_url(pair), {"Accept": "application/json"}), pair)
