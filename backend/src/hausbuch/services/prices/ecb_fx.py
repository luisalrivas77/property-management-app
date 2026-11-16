"""The ECB's euro reference rates (docs/phases/phase-6.md D167 (3)).

`eurofxref-daily.xml` holds the latest day, `eurofxref-hist-90d.xml` the last 90 days (for a
first run or a gap). Both are a gesmes envelope with one Cube per day and one Cube per
currency (`rate` = units of the currency per euro; checked live 13 Nov 2026). The rates of
the currencies in FX_CURRENCIES are stored as `price` rows of the workspace's cash asset of
that currency: `price_eur` is 1 / rate (8 places, half up) and `raw` keeps the exact rate as
published, which is what conversions use (rules.valuation.to_eur divides by it).

The fetch job reads every currency the file lists (`currencies=None`) and stores, besides
FX_CURRENCIES, the rates of any other currency a held fund's NAV is quoted in (D174 amended
16 Nov 2026, follow-up C): a NAV in yen or Swedish crowns is converted like one in dollars.
A rate the reader cannot read fails the file for FX_CURRENCIES; for any other currency it
only drops that currency, so a currency no held fund needs never stops the prices.
"""

import re
from collections.abc import Iterable
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


_CODE = re.compile(r"[A-Z]{3}")


def parse(body: bytes, currencies: Iterable[str] | None = FX_CURRENCIES) -> dict[str, list[FxRate]]:
    """Currency -> its rates, oldest first, for `currencies` (None: every currency the file
    lists; FX_CURRENCIES are always keys). SourceError("parse") when the file is not the
    ECB's envelope or names no day, or a rate of FX_CURRENCIES is not a positive number."""
    try:
        root = etree.fromstring(body, _PARSER)
    except etree.XMLSyntaxError:
        raise SourceError("parse") from None
    wanted = None if currencies is None else set(currencies)
    rates: dict[str, list[FxRate]] = {code: [] for code in FX_CURRENCIES}
    if wanted is not None:
        rates = {code: [] for code in wanted}
    dropped: set[str] = set()
    days = root.findall(f"{_NS}Cube/{_NS}Cube[@time]")
    if not days:
        raise SourceError("parse")
    for day in days:
        try:
            on = date.fromisoformat(day.get("time", ""))
        except ValueError:
            raise SourceError("parse") from None
        for cube in day.findall(f"{_NS}Cube[@currency]"):
            code = cube.get("currency") or ""
            if (wanted is not None and code not in wanted) or not _CODE.fullmatch(code):
                continue
            try:
                rate = Decimal(cube.get("rate", ""))
            except InvalidOperation:
                rate = Decimal(0)
            if not rate.is_finite() or rate <= 0:
                if code in FX_CURRENCIES:
                    raise SourceError("parse")
                dropped.add(code)
                continue
            rates.setdefault(code, []).append(FxRate(on=on, per_eur=rate))
    for code in dropped:
        rates.pop(code, None)
    for listed in rates.values():
        listed.sort(key=lambda r: r.on)
    return rates


def fetch(
    fetch: Fetch, *, backfill: bool, currencies: Iterable[str] | None = FX_CURRENCIES
) -> dict[str, list[FxRate]]:
    """The daily file, or the 90-day file when `backfill`."""
    return parse(get(fetch, HIST_90D_URL if backfill else DAILY_URL), currencies)
