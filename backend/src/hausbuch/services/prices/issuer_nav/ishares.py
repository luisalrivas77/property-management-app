"""iShares (BlackRock) NAV history (docs/phases/phase-6.md D174 (1)).

URL (read live 13 Nov 2026: the „Download“ of the fund's ishares.com product page, e.g.
https://www.ishares.com/de/privatanleger/de/produkte/264659/ishares-msci-emerging-markets-imi-ucits-etf):
    https://www.blackrock.com/varnish-api/uk-retail01-product-data/product-data/api/v1/
    get-fund-document?appType=PRODUCT_PAGE&appSubType=ISHARES&targetSite=de-ishares-v2
    &locale=de_DE&portfolioId=<id>&component=fundDownloadV2&userType=individual
where <id> is the product number in the page's address, the registry's fund_ref.

Layout: an XML Spreadsheet 2003 workbook (`urn:schemas-microsoft-com:office:spreadsheet`,
every cell a String, German formats), 1 to 4 MB:
    sheet „Fund Header“      row 1 the fund's name; row 4 „USD 58,82“ (the NAV's currency
                             and the latest NAV)
    sheet „Historical NAVs“  row 1 the header „Historische NAVs“; then one row per day,
                             newest first: „12.Nov.2026“ | „58,82“ (two decimals, a decimal
                             comma); German month abbreviations (Jan., Feb., März, Apr., Mai,
                             Juni, Juli, Aug., Sept., Okt., Nov., Dez.)
    sheet „Key Facts“        rows label | value, among them „ISIN“ and „Basiswährung“
                             (the fund's, not always the NAV's: a hedged class's NAV is EUR)
The NAV's currency is the Fund Header's prefix, else Key Facts' „Währung der Reihe“.
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import ClassVar

from lxml import etree

from hausbuch.schemas.prices import NavPoint

from ..http import SourceError
from .base import IssuerAdapter, NavFile, nav

URL = (
    "https://www.blackrock.com/varnish-api/uk-retail01-product-data/product-data/api/v1/"
    "get-fund-document?appType=PRODUCT_PAGE&appSubType=ISHARES&targetSite=de-ishares-v2"
    "&locale=de_DE&portfolioId={ref}&component=fundDownloadV2&userType=individual"
)
_SS = "urn:schemas-microsoft-com:office:spreadsheet"
_NS = {"ss": _SS}
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mär": 3,
    "märz": 3,
    "apr": 4,
    "mai": 5,
    "jun": 6,
    "juni": 6,
    "jul": 7,
    "juli": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "okt": 10,
    "nov": 11,
    "dez": 12,
}
_DAY = re.compile(r"^(\d{1,2})\.([^\d.]+)\.?(\d{4})$")
_PREFIX = re.compile(r"^([A-Z]{3})\s+[\d.,]+$")


def _day(value: str) -> date | None:
    match = _DAY.match(value.strip())
    if match is None:
        return None
    month = _MONTHS.get(match.group(2).strip().lower())
    if month is None:
        return None
    try:
        return date(int(match.group(3)), month, int(match.group(1)))
    except ValueError:
        return None


def _number(value: str) -> Decimal | None:
    """58,82 or 1.234,56 (German), never a bare thousands separator."""
    text = value.strip().replace(".", "").replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d+)?", text):
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _sheets(root: etree._Element) -> dict[str, list[list[str]]]:
    sheets: dict[str, list[list[str]]] = {}
    for ws in root.findall("ss:Worksheet", _NS):
        rows = []
        for row in ws.findall("ss:Table/ss:Row", _NS):
            cells = []
            for cell in row.findall("ss:Cell", _NS):
                data = cell.find("ss:Data", _NS)
                cells.append((data.text or "").strip() if data is not None else "")
            rows.append(cells)
        sheets[ws.get(f"{{{_SS}}}Name", "")] = rows
    return sheets


class ISharesAdapter(IssuerAdapter):
    issuer: ClassVar[str] = "ishares"
    # The files are 1 to 4 MB today (13 Nov 2026) and grow by a row a day: 5 MB would fail
    # the larger ones within a few years, so this issuer reads up to 10 MB.
    max_bytes: ClassVar[int] = 10 * 1024 * 1024

    def url(self, fund_ref: str) -> str:
        if not fund_ref.isdigit():
            raise SourceError("fund_ref")
        return URL.format(ref=fund_ref)

    @staticmethod
    def parse(body: bytes) -> NavFile:
        start = body.find(b"<")
        if start < 0:
            raise SourceError("parse")
        try:
            root = etree.fromstring(body[start:], _PARSER)
        except etree.XMLSyntaxError:
            raise SourceError("parse") from None
        sheets = _sheets(root)
        facts = {r[0]: r[1] for r in sheets.get("Key Facts", []) if len(r) >= 2 and r[0]}
        isin = facts.get("ISIN", "")
        currency = None
        for row in sheets.get("Fund Header", [])[:8]:
            match = _PREFIX.match(row[0]) if row else None
            if match is not None:
                currency = match.group(1)
                break
        currency = currency or facts.get("Währung der Reihe")
        if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin) or not (
            currency and re.fullmatch(r"[A-Z]{3}", currency)
        ):
            raise SourceError("parse")
        points: list[NavPoint] = []
        for row in sheets.get("Historical NAVs", []):
            if len(row) < 2:
                continue
            on, value = _day(row[0]), _number(row[1])
            if on is None or value is None:
                continue  # the header and the disclaimer rows
            point = nav(on, value, currency)
            if point is not None:
                points.append(point)
        if not points:
            raise SourceError("parse")
        return NavFile(isin=isin, currency=currency, points=points)
