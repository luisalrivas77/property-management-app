"""Invesco NAV history (docs/phases/phase-6.md D174 amended 19 Nov 2026 (b)).

Two requests per fund (read live 19 Nov 2026, the services invesco.com's fund pages call):
    GET https://dng-api.invesco.com/cache/v1/accounts/de_DE/shareclasses/<ISIN>?expand=nav&idType=isin
        JSON: `aliases.isin`, `shareClassCurrency`, `shareClassFullName`,
        `nav = {isin, effectiveDate, navPerShare, shareclassCurrency}`
    GET https://dng-api.invesco.com/cache/v1/accounts/de_DE/shareclasses/<ISIN>/performance?idType=isin
        XLSX, sheet „End of day data“: the share class's full name in one row, the creation
        time, then `Date | Index | AUM | NAV | Outstanding Shares | MCAP`, the day an Excel
        day number (46203.0), numbers possibly in E notation, newest first.
The workbook states no ISIN and no currency, so the JSON supplies both and the workbook is
tied to it twice: its block must be headed by the JSON's full name, and its NAV on the
JSON's day must equal the JSON's NAV. Either differing is another share class's file
(`isin_mismatch`); a workbook without the JSON's day cannot be tied (`parse`).
"""

from datetime import date
from decimal import Decimal
from typing import Any, ClassVar

from hausbuch.schemas.prices import NavPoint

from ..http import SourceError, get
from . import xlsx
from .base import ISIN, IssuerAdapter, NavFile, nav, parse_json

BASE = "https://dng-api.invesco.com/cache/v1/accounts/de_DE/shareclasses/{isin}"
SHARECLASS_URL = BASE + "?expand=nav&idType=isin"
PERFORMANCE_URL = BASE + "/performance?idType=isin"


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _blocks(rows: list[dict[str, str]]) -> list[tuple[str, dict[date, Decimal]]]:
    """(the name above the header, NAV per day) per table in the sheet."""
    blocks: list[tuple[str, dict[date, Decimal]]] = []
    name = ""
    current: dict[date, Decimal] | None = None
    column = ""
    for row in rows:
        if row.get("A") == "Date":
            columns = [col for col, text in row.items() if text == "NAV"]
            if len(columns) != 1:
                raise SourceError("parse")
            column, current = columns[0], {}
            blocks.append((name, current))
            continue
        on = xlsx.day(row.get("A", ""))
        if on is None:
            if set(row) == {"A"} and not row["A"].startswith("Spreadsheet created"):
                name, current = row["A"], None
            continue
        if current is None:
            continue
        value = xlsx.number(row.get(column, ""))
        if value is None:
            continue
        if on in current and current[on] != value:
            raise SourceError("parse")
        current[on] = value
    return blocks


class InvescoAdapter(IssuerAdapter):
    issuer: ClassVar[str] = "invesco"
    isin_keyed: ClassVar[bool] = True
    brands: ClassVar[tuple[str, ...]] = ("invesco",)

    def url(self, fund_ref: str) -> str:
        if not ISIN.fullmatch(fund_ref):
            raise SourceError("fund_ref")
        return SHARECLASS_URL.format(isin=fund_ref)

    def load(self, fund_ref: str, since: date | None, isin: str | None = None) -> NavFile:
        record = parse_json(get(self.fetcher, self.url(fund_ref), max_bytes=self.max_bytes))
        if not isinstance(record, dict):
            raise SourceError("parse")
        latest = record.get("nav") if isinstance(record.get("nav"), dict) else {}
        stated = _text((record.get("aliases") or {}).get("isin"))
        if not ISIN.fullmatch(stated) or _text(latest.get("isin")) not in ("", stated):
            raise SourceError("parse")
        if isin is not None and stated != isin:
            raise SourceError("isin_mismatch")  # its workbook is never asked
        currency = _text(record.get("shareClassCurrency"))
        name = _text(record.get("shareClassFullName"))
        value = latest.get("navPerShare")
        try:
            on = date.fromisoformat(_text(latest.get("effectiveDate")))
        except ValueError:
            raise SourceError("parse") from None
        if len(currency) != 3 or not currency.isupper() or not name:
            raise SourceError("parse")
        if _text(latest.get("shareclassCurrency")) not in ("", currency):
            raise SourceError("parse")
        if not isinstance(value, Decimal) or nav(on, value, currency) is None:
            raise SourceError("parse")
        workbook = get(self.fetcher, PERFORMANCE_URL.format(isin=stated), max_bytes=self.max_bytes)
        named = [days for title, days in _blocks(xlsx.rows(workbook)) if title == name]
        if not named:
            raise SourceError("isin_mismatch")  # another share class's workbook
        if len(named) > 1:
            raise SourceError("parse")
        days = named[0]
        if on not in days:
            raise SourceError("parse")
        if nav(on, days[on], currency) != nav(on, value, currency):
            raise SourceError("isin_mismatch")
        points: list[NavPoint] = [
            p for p in (nav(d, v, currency) for d, v in days.items()) if p is not None
        ]
        return NavFile(isin=stated, currency=currency, points=points)
