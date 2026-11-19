"""Xtrackers (DWS) NAV history (docs/phases/phase-6.md D174 amended 19 Nov 2026 (b)).

Request (read live 19 Nov 2026, the „Historische Daten“ download of etf.dws.com's fund page):
    GET https://etf.deutsche-am.com/api/pdp/de-de/etf/<ISIN>/historicaldata/download
        ?startDate=<yyyy-mm-dd>&endDate=<yyyy-mm-dd>&includeTax=false
Keyed by the ISIN, no catalogue. The answer is an XLSX of one sheet named after the ISIN:
disclaimer rows, then `Name`, `ISIN`, `Currency`, `As of` (label in A, value in B), then the
header `Date | Asset Under Management | NAV | Index Level | Interim Profits` and one row per
day, newest first, the day as dd.mm.yyyy. The NAV column is found by its header; a row whose
NAV is „N/A“ or empty is skipped.
"""

from datetime import UTC, date, datetime
from typing import ClassVar

from hausbuch.schemas.prices import NavPoint

from ..http import SourceError, get
from . import xlsx
from .base import ISIN, IssuerAdapter, NavFile, nav

URL = (
    "https://etf.deutsche-am.com/api/pdp/de-de/etf/{isin}/historicaldata/download"
    "?startDate={start}&endDate={end}&includeTax=false"
)
FIRST_DAY = date(2000, 1, 1)  # before any Xtrackers fund (the first launched in 2007)


class XtrackersAdapter(IssuerAdapter):
    issuer: ClassVar[str] = "xtrackers"
    isin_keyed: ClassVar[bool] = True
    brands: ClassVar[tuple[str, ...]] = ("xtrackers", "xtr")

    def url(self, fund_ref: str, since: date | None = None) -> str:
        if not ISIN.fullmatch(fund_ref):
            raise SourceError("fund_ref")
        today = datetime.now(UTC).date()
        return URL.format(
            isin=fund_ref, start=(since or FIRST_DAY).isoformat(), end=today.isoformat()
        )

    def load(self, fund_ref: str, since: date | None, isin: str | None = None) -> NavFile:
        return self.parse(get(self.fetcher, self.url(fund_ref, since), max_bytes=self.max_bytes))

    @staticmethod
    def parse(body: bytes) -> NavFile:
        rows = xlsx.rows(body)
        header = next((i for i, r in enumerate(rows) if r.get("A") == "Date"), None)
        if header is None:
            raise SourceError("parse")
        facts = {r["A"]: r.get("B", "") for r in rows[:header] if "A" in r}
        isin, currency = facts.get("ISIN", ""), facts.get("Currency", "")
        if not ISIN.fullmatch(isin) or len(currency) != 3 or not currency.isupper():
            raise SourceError("parse")
        columns = [col for col, text in rows[header].items() if text == "NAV"]
        if len(columns) != 1:
            raise SourceError("parse")
        column = columns[0]
        points: dict[date, NavPoint] = {}
        for row in rows[header + 1 :]:
            on = xlsx.day(row.get("A", ""))
            value = xlsx.number(row.get(column, ""))
            if on is None or value is None:
                continue
            point = nav(on, value, currency)
            if point is None:
                continue
            if on in points and points[on] != point:
                raise SourceError("parse")  # one day with two NAVs
            points[on] = point
        if not points:
            raise SourceError("parse")
        return NavFile(isin=isin, currency=currency, points=list(points.values()))
