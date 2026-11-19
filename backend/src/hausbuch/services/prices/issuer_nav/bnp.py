"""BNP Paribas Asset Management NAV history (docs/phases/phase-6.md D174 amended 19 Nov
2026 (b)).

Request (read live 19 Nov 2026, the service bnpparibas-am.com's fund pages call):
    GET https://api.bnpparibas-am.com/push-raw/navs/fast?currency=<CUR>&product_gids=<ISIN>&granularity=daily
Answer: JSON `{"<ISIN>": [{"date": "YYYY-MM-DD", "value": <NAV>, "type": "PRICE_NAV"}, ...]}`,
rows not in date order, other types (dividends) beside the NAV. The answer states no
currency, and a currency the share class is not quoted in answers an empty list, so the
reference is `<CUR>:<ISIN>`: found once by asking EUR, USD, GBP and CHF in turn, then kept.
"""

import re
from datetime import date
from decimal import Decimal
from typing import ClassVar

from hausbuch.schemas.prices import NavPoint

from ..http import SourceError, get
from .base import ISIN, IssuerAdapter, NavFile, nav, parse_json

URL = (
    "https://api.bnpparibas-am.com/push-raw/navs/fast?currency={currency}"
    "&product_gids={isin}&granularity=daily"
)
CURRENCIES = ("EUR", "USD", "GBP", "CHF")  # the order a fund's currency is looked for
_REF = re.compile(r"^([A-Z]{3}):([A-Z]{2}[A-Z0-9]{9}[0-9])$")


def _ref(fund_ref: str) -> tuple[str, str]:
    match = _REF.fullmatch(fund_ref)
    if match is None:
        raise SourceError("fund_ref")
    return match.group(1), match.group(2)


class BnpAdapter(IssuerAdapter):
    issuer: ClassVar[str] = "bnp"
    isin_keyed: ClassVar[bool] = True
    brands: ClassVar[tuple[str, ...]] = ("bnp p", "bnpp")

    def candidates(self, isin: str, name: str) -> list[str]:
        return [f"{currency}:{isin}" for currency in CURRENCIES] if ISIN.fullmatch(isin) else []

    def url(self, fund_ref: str) -> str:
        currency, isin = _ref(fund_ref)
        return URL.format(currency=currency, isin=isin)

    def load(self, fund_ref: str, since: date | None, isin: str | None = None) -> NavFile:
        currency, _ = _ref(fund_ref)
        return self.read_body(
            get(self.fetcher, self.url(fund_ref), max_bytes=self.max_bytes), currency
        )

    @staticmethod
    def read_body(body: bytes, currency: str) -> NavFile:
        answer = parse_json(body)
        if not isinstance(answer, dict) or len(answer) != 1:
            raise SourceError("parse")
        [(isin, rows)] = answer.items()
        if not isinstance(isin, str) or not ISIN.fullmatch(isin) or not isinstance(rows, list):
            raise SourceError("parse")
        points: dict[date, NavPoint] = {}
        for row in rows:
            if not isinstance(row, dict) or row.get("type") != "PRICE_NAV":
                continue
            value = row.get("value")
            if not isinstance(value, Decimal) or not value.is_finite():
                continue
            point = nav(date.fromisoformat(str(row.get("date"))), value, currency)
            if point is None:
                continue
            if point.on in points and points[point.on] != point:
                raise SourceError("parse")
            points[point.on] = point
        if not points:
            raise SourceError("not_listed")  # no such share class in this currency
        return NavFile(isin=isin, currency=currency, points=list(points.values()))
