"""CoinGecko's daily coin price (docs/phases/phase-6.md D167 (3)).

`GET /api/v3/coins/{id}/history?date=dd-mm-yyyy&localization=false` answers the snapshot at
00:00 UTC of that date; `market_data.current_price.eur` is the EUR figure (a JSON number,
read as a Decimal and kept to 8 places). A date before the coin existed answers without
`market_data`: no price that day. The Demo tier reaches back 365 days, allows about 30 calls
a minute and 10,000 a month, and asks for attribution (the Anlagen screen's line).

The Demo key goes in the header `x-cg-demo-api-key` and nowhere else: never in the URL,
never in a log line or an exception (SourceError carries the class and the status only).
"""

import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from pydantic import SecretStr

from .http import Fetch, SourceError, get

API_URL = "https://api.coingecko.com/api/v3"
KEY_HEADER = "x-cg-demo-api-key"
HISTORY_DAYS = 365  # the Demo tier's reach
MIN_INTERVAL_S = 2.5  # under the Demo tier's 30 calls a minute
PLACES = Decimal("0.00000001")


def history_url(coin_id: str, on: date) -> str:
    return f"{API_URL}/coins/{coin_id}/history?date={on:%d-%m-%Y}&localization=false"


def parse(body: bytes) -> Decimal | None:
    """The EUR price of one history answer, or None when the coin had none that day."""
    try:
        data = json.loads(body, parse_float=Decimal, parse_int=Decimal)
    except ValueError:
        raise SourceError("parse") from None
    if not isinstance(data, dict):
        raise SourceError("parse")
    market = data.get("market_data")
    if market is None:
        return None
    try:
        eur = market["current_price"]["eur"]
    except (KeyError, TypeError):
        return None
    if not isinstance(eur, Decimal) or eur <= 0:
        return None
    price = eur.quantize(PLACES, rounding=ROUND_HALF_UP)
    return price if price > 0 else None


@dataclass
class CoinGeckoDaily:
    """One coin's price per day. `sleep` spaces the calls (tests pass a no-op)."""

    key: SecretStr = field(repr=False)
    fetch: Fetch = field(repr=False)
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)
    _calls: int = 0

    def oldest_day(self, today: date) -> date:
        return today - timedelta(days=HISTORY_DAYS - 1)

    def daily(
        self, coin_id: str, days: Iterable[date]
    ) -> tuple[dict[date, Decimal], SourceError | None]:
        """The price of each day CoinGecko has one for, and the error that stopped the
        calls (the first failed call ends them: the caller asks Kraken for the days left)."""
        prices: dict[date, Decimal] = {}
        headers = {"Accept": "application/json", KEY_HEADER: self.key.get_secret_value()}
        for on in days:
            if self._calls:
                self.sleep(MIN_INTERVAL_S)
            self._calls += 1
            try:
                price = parse(get(self.fetch, history_url(coin_id, on), headers))
            except SourceError as exc:
                return prices, exc
            if price is not None:
                prices[on] = price
        return prices, None
