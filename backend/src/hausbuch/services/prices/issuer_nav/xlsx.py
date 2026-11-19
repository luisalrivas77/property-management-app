"""Reading an issuer's XLSX workbook without a spreadsheet library (D174 (2)).

The first sheet's cells as text per row, shared strings resolved (rich text joined), the
namespace matched by URI so a prefixed part (DWS's `x:`) reads like an unprefixed one. The
unpacked size is capped, and the XML parser resolves no entity and loads no DTD. Moved out
of spdr.py on 19 Nov 2026 (D174 amended) for the Xtrackers, Invesco and HANetf workbooks.
"""

import io
import re
import zipfile
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from lxml import etree

from ..http import SourceError

MAX_UNPACKED = 40 * 1024 * 1024
_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    dtd_validation=False,
    huge_tree=False,
    remove_comments=True,
    remove_pis=True,
)
_CELL = re.compile(r"^([A-Z]+)\d+$")
_MONTHS = {
    m: i
    for i, m in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1
    )
}
_DOTTED = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")
_EXCEL_EPOCH = date(1899, 12, 30)


def _xml(archive: zipfile.ZipFile, name: str) -> etree._Element:
    return etree.fromstring(archive.read(name), _PARSER)


def _sheet_path(archive: zipfile.ZipFile) -> str:
    """The first sheet's part, by the workbook's relationship (sheet1.xml or another)."""
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


def day(value: str) -> date | None:
    """12-Nov-2026, 12.11.2026, or an Excel day number (46203 or 46203.0)."""
    parts = value.split("-")
    if len(parts) == 3 and parts[1] in _MONTHS:
        try:
            return date(int(parts[2]), _MONTHS[parts[1]], int(parts[0]))
        except ValueError:
            return None
    dotted = _DOTTED.match(value)
    if dotted:
        try:
            return date(int(dotted.group(3)), int(dotted.group(2)), int(dotted.group(1)))
        except ValueError:
            return None
    try:
        serial = Decimal(value)
    except InvalidOperation:
        return None
    if not serial.is_finite() or serial != serial.to_integral_value():
        return None
    if not 20000 < serial < 80000:
        return None
    return _EXCEL_EPOCH + timedelta(days=int(serial))


def number(value: str) -> Decimal | None:
    """A finite decimal (E notation too), or None for text such as „N/A“."""
    try:
        found = Decimal(value)
    except InvalidOperation:
        return None
    return found if found.is_finite() else None


def rows(body: bytes) -> list[dict[str, str]]:
    """The first sheet's rows, each a column letter -> text map; SourceError("parse") for a
    file that is not a workbook or unpacks to more than MAX_UNPACKED."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile:
        raise SourceError("parse") from None
    if sum(i.file_size for i in archive.infolist()) > MAX_UNPACKED:
        raise SourceError("parse")
    strings: list[str] = []
    if "xl/sharedStrings.xml" in archive.namelist():
        sst = _xml(archive, "xl/sharedStrings.xml")
        strings = ["".join(si.itertext()) for si in sst.findall(f"{_MAIN}si")]
    sheet = _xml(archive, _sheet_path(archive))
    found = []
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
        found.append(cells)
    return found
