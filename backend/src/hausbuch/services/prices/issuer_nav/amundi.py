"""Amundi (with the former Lyxor funds) NAV history (docs/phases/phase-6.md D174 amended
19 Nov 2026 (b)).

Request (read live 19 Nov 2026, the service amundietf.de's own product page script calls):
    POST https://www.amundietf.de/mapi/ProductAPI/getProductsData
    {"context": {"countryCode": "DEU", "languageCode": "de", "userProfileName": "RETAIL"},
     "productIds": ["<ISIN>"], "characteristics": ["ISIN", "SHARE_MARKETING_NAME", "CURRENCY"],
     "historics": [{"indicator": "officialNav", "startDate": ..., "endDate": ...},
                   {"indicator": "navCurrency", ...}], "productType": "PRODUCT"}
`historics` must be a list (an object answers 400), a GET answers 405. Keyed by the ISIN, no
catalogue. The date range is honoured; the page itself asks 1000-01-01 to 2050-01-01.

Answer: `products[0].characteristics` names the ISIN and currency, `historics[]` one entry
per indicator, `historicalData[]` = {date: epoch milliseconds at 00:00 UTC, data}. An ISIN
Amundi does not know answers a product without characteristics or history (`not_listed`).
A point dated on a weekend (an overnight fund) keeps its own day.
"""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, ClassVar

from hausbuch.schemas.prices import NavPoint

from ..http import SourceError, get
from .base import ISIN, IssuerAdapter, NavFile, nav, parse_json

URL = "https://www.amundietf.de/mapi/ProductAPI/getProductsData"
FIRST_DAY = date(1000, 1, 1)  # what the product page asks
LAST_DAY = date(2050, 1, 1)
_MS_PER_DAY = 86_400_000


def request_body(isin: str, since: date | None) -> bytes:
    start, end = (since or FIRST_DAY).isoformat(), LAST_DAY.isoformat()
    return json.dumps(
        {
            "context": {"countryCode": "DEU", "languageCode": "de", "userProfileName": "RETAIL"},
            "productIds": [isin],
            "characteristics": ["ISIN", "SHARE_MARKETING_NAME", "CURRENCY"],
            "historics": [
                {"indicator": "officialNav", "startDate": start, "endDate": end},
                {"indicator": "navCurrency", "startDate": start, "endDate": end},
            ],
            "productType": "PRODUCT",
        },
        separators=(",", ":"),
    ).encode()


def _series(product: dict[str, Any], indicator: str) -> dict[date, Any]:
    """Day -> value of one indicator; a day listed twice with two values is ambiguous."""
    found: dict[date, Any] = {}
    for entry in product.get("historics") or []:
        if not isinstance(entry, dict) or entry.get("indicator") != indicator:
            continue
        for point in entry.get("historicalData") or []:
            stamp = point.get("date") if isinstance(point, dict) else None
            if not isinstance(stamp, Decimal) or stamp % _MS_PER_DAY != 0:
                raise SourceError("parse")  # a point at another time than midnight UTC
            on = datetime.fromtimestamp(int(stamp) // 1000, UTC).date()
            value = point.get("data")
            if on in found and found[on] != value:
                raise SourceError("parse")
            found[on] = value
    return found


class AmundiAdapter(IssuerAdapter):
    issuer: ClassVar[str] = "amundi"
    isin_keyed: ClassVar[bool] = True
    brands: ClassVar[tuple[str, ...]] = ("amundi", "amu", "lyxor")

    def url(self, fund_ref: str) -> str:
        if not ISIN.fullmatch(fund_ref):
            raise SourceError("fund_ref")
        return URL

    def load(self, fund_ref: str, since: date | None, isin: str | None = None) -> NavFile:
        body = get(
            self.fetcher,
            self.url(fund_ref),
            {"Content-Type": "application/json", "Accept": "application/json"},
            max_bytes=self.max_bytes,
            body=request_body(fund_ref, since),
        )
        return self.parse(body)

    @staticmethod
    def parse(body: bytes) -> NavFile:
        products = parse_json(body).get("products")
        if not isinstance(products, list) or len(products) != 1:
            raise SourceError("not_listed" if products == [] else "parse")
        product = products[0]
        facts = product.get("characteristics") or {}
        navs = _series(product, "officialNav")
        if not facts or not navs:
            raise SourceError("not_listed")
        isin, currency = facts.get("ISIN"), facts.get("CURRENCY")
        if not isinstance(isin, str) or not ISIN.fullmatch(isin):
            raise SourceError("parse")
        if not isinstance(currency, str) or len(currency) != 3 or not currency.isupper():
            raise SourceError("parse")
        currencies = _series(product, "navCurrency")
        points: list[NavPoint] = []
        for on, value in navs.items():
            if currencies.get(on, currency) != currency:
                raise SourceError("parse")  # a NAV in another currency than the fund's
            if not isinstance(value, Decimal) or not value.is_finite():
                continue
            point = nav(on, value, currency)
            if point is not None:
                points.append(point)
        if not points:
            raise SourceError("not_listed")
        return NavFile(isin=isin, currency=currency, points=points)
