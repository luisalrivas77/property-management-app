"""State Street (SPDR) NAV history (docs/phases/phase-6.md D174 (1)).

URL (read live 13 Nov 2026, the „NAV History“ download of the fund's ssga.com page):
    https://www.ssga.com/library-content/products/fund-data/etfs/emea/navhist-emea-en-<slug>.xlsx
where <slug> is the fund's ticker slug (`sppw-gy`), the registry's fund_ref.

Layout (one sheet `navhist`, every text a shared string):
    row 1  Fund Name:     | <name>
    row 2  ISIN:          | <ISIN>
    row 3  Report Date:   | 13-Nov-2026
    row 4  NAV Currency:  | USD
    row 6  Date | NAV | Shares Outstanding | Share Class Assets | Total Fund Assets (USD)
    row 7+ 12-Nov-2026 | 52.9838 (a number cell) | ...
    last   a disclaimer in column A only
Newest first. The NAV is in the currency the file names (one column; the step-0 reading of
three currency columns did not hold for the real file). Read with zipfile and lxml, no
external entities, and the unpacked size capped so a crafted ZIP cannot exhaust memory.
"""

import io
import re
import zipfile
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import ClassVar

from lxml import etree

from hausbuch.schemas.prices import NavPoint

from ..http import SourceError
from .base import IssuerAdapter, NavFile, nav

URL = (
    "https://www.ssga.com/library-content/products/fund-data/etfs/emea/navhist-emea-en-{slug}.xlsx"
)
MAX_UNPACKED = 40 * 1024 * 1024
_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
_SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_CELL = re.compile(r"^([A-Z]+)\d+$")
_MONTHS = {
    m: i
    for i, m in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1
    )
}
_EXCEL_EPOCH = date(1899, 12, 30)


def _xml(archive: zipfile.ZipFile, name: str) -> etree._Element:
    return etree.fromstring(archive.read(name), _PARSER)


def _sheet_path(archive: zipfile.ZipFile) -> str:
    """The first sheet's part, by the workbook's relationship (the real file: sheet1.xml)."""
    workbook = _xml(archive, "xl/workbook.xml")
    sheet = workbook.find(f"{_MAIN}sheets/{_MAIN}sheet")
    if sheet is None:
        raise SourceError("parse")
    rel_id = sheet.get(f"{_REL}id")
    rels = _xml(archive, "xl/_rels/workbook.xml.rels")
    for rel in rels.findall(f"{_PKG_REL}Relationship"):
        if rel.get("Id") == rel_id:
            target = rel.get("Target", "").lstrip("/")
            return target if target.startswith("xl/") else f"xl/{target}"
    raise SourceError("parse")


def _day(value: str) -> date | None:
    """12-Nov-2026, or an Excel day number."""
    parts = value.split("-")
    if len(parts) == 3 and parts[1] in _MONTHS:
        try:
            return date(int(parts[2]), _MONTHS[parts[1]], int(parts[0]))
        except ValueError:
            return None
    try:
        serial = Decimal(value)
    except InvalidOperation:
        return None
    if serial != serial.to_integral_value() or not 20000 < serial < 80000:
        return None
    return _EXCEL_EPOCH + timedelta(days=int(serial))


def _rows(archive: zipfile.ZipFile) -> list[dict[str, str]]:
    strings: list[str] = []
    if "xl/sharedStrings.xml" in archive.namelist():
        sst = _xml(archive, "xl/sharedStrings.xml")
        strings = ["".join(si.itertext()) for si in sst.findall(f"{_MAIN}si")]
    sheet = _xml(archive, _sheet_path(archive))
    rows = []
    for row in sheet.iter(f"{_MAIN}row"):
        cells: dict[str, str] = {}
        for cell in row.findall(f"{_MAIN}c"):
            match = _CELL.match(cell.get("r", ""))
            v = cell.find(f"{_MAIN}v")
            if match is None:
                continue
            if cell.get("t") == "inlineStr":
                text = "".join(cell.itertext())
            elif v is None or v.text is None:
                continue
            elif cell.get("t") == "s":
                text = strings[int(v.text)]
            else:
                text = v.text
            cells[match.group(1)] = text.strip()
        rows.append(cells)
    return rows


class SpdrAdapter(IssuerAdapter):
    issuer: ClassVar[str] = "spdr"

    def url(self, fund_ref: str) -> str:
        if not _SLUG.fullmatch(fund_ref):
            raise SourceError("fund_ref")
        return URL.format(slug=fund_ref)

    @staticmethod
    def parse(body: bytes) -> NavFile:
        try:
            archive = zipfile.ZipFile(io.BytesIO(body))
        except zipfile.BadZipFile:
            raise SourceError("parse") from None
        if sum(i.file_size for i in archive.infolist()) > MAX_UNPACKED:
            raise SourceError("parse")
        rows = _rows(archive)
        facts = {r["A"]: r.get("B", "") for r in rows if r.get("A", "").endswith(":")}
        isin, currency = facts.get("ISIN:", ""), facts.get("NAV Currency:", "")
        if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin) or not re.fullmatch(
            r"[A-Z]{3}", currency
        ):
            raise SourceError("parse")
        header = next(
            (i for i, r in enumerate(rows) if r.get("A") == "Date" and r.get("B") == "NAV"),
            None,
        )
        if header is None:
            raise SourceError("parse")
        points: list[NavPoint] = []
        for row in rows[header + 1 :]:
            on = _day(row.get("A", ""))
            if on is None:
                continue  # the blank row and the disclaimer
            try:
                value = Decimal(row.get("B", ""))
            except InvalidOperation:
                continue
            point = nav(on, value, currency)
            if point is not None:
                points.append(point)
        if not points:
            raise SourceError("parse")
        return NavFile(isin=isin, currency=currency, points=points)
