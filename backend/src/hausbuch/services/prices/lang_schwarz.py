"""The Lang & Schwarz exchange's daily price of a fund (docs/phases/phase-6.md D264).

For a held fund whose issuer publishes no usable daily NAV: the exchange's chart service
answers the fund's whole history of daily mid quotes in euros. Two GETs through the app's
own client and user agent (`http.get`, no redirect followed): the search by ISIN for the
instrument id, then the chart of that id. The id is stored with every price row it gave
(price.raw), so after the first run a fund costs one request a day.

Stored as `exchange_close`, never as a NAV: the price is the market maker's quote as the
chart prints it for the day, which Lang & Schwarz itself says may differ from binding prices.
Personal use only (D264 (9), Luis 19 Nov 2026); a SaaS version needs a licensed feed.
"""

from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from hausbuch.schemas.prices import NavPoint

from .http import Fetch, SourceError, get
from .issuer_nav import ADAPTERS
from .issuer_nav.base import ISIN, parse_json

VENUE = "ls_tc"
VENUE_NAME = "Lang & Schwarz"
SEARCH_URL = "https://www.ls-tc.de/_rpc/json/.lstc/instrument/search/main?q={isin}&localeId=2"
HISTORY_URL = (
    "https://www.ls-tc.de/_rpc/json/instrument/chart/dataForInstrument"
    "?instrumentId={instrument_id}&marketId=1&quotetype=mid&series=history&type=mini&localeId=2"
)
PLACES = Decimal("0.000001")  # D264 (2): the answer prints binary floats
MAX_BYTES = 2 * 1024 * 1024  # a fund's whole history was about 60 kB (19 Nov 2026)


def wants_exchange(name: str) -> bool:
    """D264 (1): a fund whose name names no adapter's brand has no issuer source to wait for;
    a branded fund whose lookup missed stays on its report price."""
    return not any(adapter.names_brand(name) for adapter in ADAPTERS.values())


def _isin(isin: str) -> str:
    if not ISIN.fullmatch(isin):
        raise SourceError("fund_ref")
    return isin


def instrument(fetch: Fetch, isin: str) -> int:
    """The instrument id of the one listing whose ISIN is `isin`; none, or several distinct
    ids, is the lookup miss `not_listed`, an answer of another shape `parse`."""
    answer = parse_json(get(fetch, SEARCH_URL.format(isin=_isin(isin)), max_bytes=MAX_BYTES))
    if not isinstance(answer, list):
        raise SourceError("parse")
    ids = set()
    for entry in answer:
        if not isinstance(entry, dict) or entry.get("isin") != isin:
            continue
        value = entry.get("instrumentId")
        if not isinstance(value, Decimal) or value != value.to_integral_value() or value <= 0:
            raise SourceError("parse")
        ids.add(int(value))
    if len(ids) != 1:
        raise SourceError("not_listed")
    return ids.pop()


def history_url(instrument_id: int) -> str:
    if type(instrument_id) is not int or instrument_id <= 0:
        raise SourceError("fund_ref")
    return HISTORY_URL.format(instrument_id=instrument_id)


def _points(answer: Any, isin: str) -> list[tuple[Any, Any]]:
    if not isinstance(answer, dict) or not isinstance(answer.get("info"), dict):
        raise SourceError("parse")
    if answer["info"].get("isin") != isin:
        raise SourceError("isin_mismatch")
    try:
        data = answer["series"]["history"]["data"]
    except (KeyError, TypeError):
        raise SourceError("parse") from None
    if not isinstance(data, list):
        raise SourceError("parse")
    return data


def _live(point: Any) -> bool:
    """A well-formed point whose time is not 00:00 UTC (the chart's live quote)."""
    if not isinstance(point, list) or len(point) != 2 or not isinstance(point[0], Decimal):
        return False
    stamp = point[0]
    return stamp == stamp.to_integral_value() and int(stamp) % 86_400_000 != 0


def history(fetch: Fetch, instrument_id: int, isin: str, today: date) -> list[NavPoint]:
    """The past weekdays' prices in euros, oldest first (D264 (2)): each point read as its UTC
    day. The last point, when it is not at 00:00 UTC, is the live quote of the moment and is
    dropped whatever its day (before the venue opens it is still yesterday's evening quote,
    beside yesterday's own day point). A day on or after `today` is dropped (the chart's own
    point of today is no close yet), Saturdays and Sundays too (they repeat Friday). A past
    day twice, a price that is not positive, any other point not at 00:00 UTC or another
    shape is `parse`, so nothing of the answer is stored; an answer for another ISIN is
    `isin_mismatch`."""
    answer = parse_json(get(fetch, history_url(instrument_id), max_bytes=MAX_BYTES))
    points = _points(answer, _isin(isin))
    if points and _live(points[-1]):
        points = points[:-1]
    found: dict[date, Decimal] = {}
    for point in points:
        if not isinstance(point, list) or len(point) != 2:
            raise SourceError("parse")
        stamp, price = point
        if not isinstance(stamp, Decimal) or not isinstance(price, Decimal):
            raise SourceError("parse")
        try:
            midnight = stamp == stamp.to_integral_value() and int(stamp) % 86_400_000 == 0
            on = datetime.fromtimestamp(int(stamp) / 1000, UTC).date()
        except (ArithmeticError, ValueError, OSError):
            raise SourceError("parse") from None
        if not midnight:
            # D264 (2) amended: only the last point may be off midnight; read as its UTC day,
            # any other such point could shift a price by a day without a sign.
            raise SourceError("parse")
        if on >= today:
            continue
        if price <= 0:
            raise SourceError("parse")
        if on.weekday() >= 5:
            continue
        if on in found:
            raise SourceError("parse")
        try:
            found[on] = price.quantize(PLACES, ROUND_HALF_UP)
        except InvalidOperation:
            raise SourceError("parse") from None
    if not found:
        raise SourceError("parse")
    return [NavPoint(on=on, nav=found[on], currency="EUR") for on in sorted(found)]
