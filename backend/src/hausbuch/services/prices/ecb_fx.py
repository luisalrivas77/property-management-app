"""The ECB's euro reference rates (docs/phases/phase-6.md D167 (3)).

`eurofxref-daily.xml` holds the latest day, `eurofxref-hist-90d.xml` the last 90 days (for a
first run or a gap). Both are a gesmes envelope with one Cube per day and one Cube per
currency (`rate` = units of the currency per euro; checked live 13 Nov 2026). The rates of
the currencies in FX_CURRENCIES are stored as `price` rows of the workspace's cash asset of
that currency: `price_eur` is 1 / rate (8 places, half up) and `raw` keeps the exact rate as
published, which is what conversions use (rules.valuation.to_eur divides by it).
"""

from datetime import date
from decimal import Decimal, InvalidOperation

from lxml import etree

from hausbuch.schemas.prices import FxRate

from .http import Fetch, SourceError, get

DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
HIST_90D_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"
FX_CURRENCIES = ("USD", "GBP", "CHF")  # D167 (3)
BACKFILL_AFTER_DAYS = 4  # the daily file holds one day: a longer gap reads the 90-day file

_NS = "{http://www.ecb.int/vocabulary/2002-08-01/eurofxref}"
_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    dtd_validation=False,
    huge_tree=False,
    remove_comments=True,
    remove_pis=True,
)


def parse(body: bytes) -> dict[str, list[FxRate]]:
    """Currency -> its rates, oldest first, for FX_CURRENCIES. SourceError("parse") when
    the file is not the ECB's envelope or names no day."""
    try:
        root = etree.fromstring(body, _PARSER)
    except etree.XMLSyntaxError:
        raise SourceError("parse") from None
    rates: dict[str, list[FxRate]] = {code: [] for code in FX_CURRENCIES}
    days = root.findall(f"{_NS}Cube/{_NS}Cube[@time]")
    if not days:
        raise SourceError("parse")
    for day in days:
        try:
            on = date.fromisoformat(day.get("time", ""))
        except ValueError:
            raise SourceError("parse") from None
        for cube in day.findall(f"{_NS}Cube[@currency]"):
            code = cube.get("currency")
            if code not in rates:
                continue
            try:
                rate = Decimal(cube.get("rate", ""))
            except InvalidOperation:
                raise SourceError("parse") from None
            if rate <= 0:
                raise SourceError("parse")
            rates[code].append(FxRate(on=on, per_eur=rate))
    for listed in rates.values():
        listed.sort(key=lambda r: r.on)
    return rates


def fetch(fetch: Fetch, *, backfill: bool) -> dict[str, list[FxRate]]:
    """The daily file, or the 90-day file when `backfill`."""
    return parse(get(fetch, HIST_90D_URL if backfill else DAILY_URL))
