"""Sparkasse CSV-CAMT reader (§7.1): the fallback format, with no balances (D21).

`;`-separated with a header row; fields usually quoted. Encoding: UTF-8 when the bytes are
valid UTF-8 (with or without BOM), else Windows-1252 (the real export; §7.1 says
ISO-8859-15, which differs only in a few symbols such as €). Dates `dd.mm.yy` or
`dd.mm.yyyy`; amounts with a decimal comma and optional thousands dots, signed (debits
negative), parsed into Decimal from the text. Lines whose Info is "Umsatz vorgemerkt" are
pending and skipped. Columns are found by name, so their order does not matter; the names
with umlauts and the shorter V1 names are accepted as variants.

`raw` holds the columns that are not stored as their own column, by their header name, and
never an IBAN: Auftragskonto and Kontonummer/IBAN are left out.
"""

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal

from hausbuch.rules.errors import RuleError
from hausbuch.rules.statements import UNPARSABLE, UNSUPPORTED_CURRENCY
from hausbuch.schemas.bank import ParsedStatement, StatementFormat, StatementLine

PENDING_INFO = "Umsatz vorgemerkt"
_FIRST_COLUMN = "Auftragskonto"
# Bounded at 12 integer digits, inside the Money column's range, so a longer number is the
# file's error (statement.unparsable), not a database or validation error.
_AMOUNT = re.compile(r"^-?(?:[0-9]{1,3}(?:\.[0-9]{3}){1,3}|[0-9]{1,12})(?:,[0-9]{1,2})?$")
_DATE_FORMATS = ("%d.%m.%y", "%d.%m.%Y")

# Canonical column name -> accepted header spellings.
_COLUMNS: dict[str, tuple[str, ...]] = {
    "Auftragskonto": ("Auftragskonto",),
    "Buchungstag": ("Buchungstag",),
    "Valutadatum": ("Valutadatum",),
    "Buchungstext": ("Buchungstext",),
    "Verwendungszweck": ("Verwendungszweck",),
    "Glaeubiger ID": ("Glaeubiger ID", "Gläubiger ID"),
    "Mandatsreferenz": ("Mandatsreferenz",),
    "Kundenreferenz (End-to-End)": ("Kundenreferenz (End-to-End)",),
    "Sammlerreferenz": ("Sammlerreferenz",),
    "Lastschrift Ursprungsbetrag": ("Lastschrift Ursprungsbetrag",),
    "Auslagenersatz Ruecklastschrift": (
        "Auslagenersatz Ruecklastschrift",
        "Auslagenersatz Rücklastschrift",
    ),
    "Beguenstigter/Zahlungspflichtiger": (
        "Beguenstigter/Zahlungspflichtiger",
        "Begünstigter/Zahlungspflichtiger",
    ),
    "Kontonummer/IBAN": ("Kontonummer/IBAN", "Kontonummer", "IBAN"),
    "BIC (SWIFT-Code)": ("BIC (SWIFT-Code)", "BIC"),
    "Betrag": ("Betrag",),
    "Waehrung": ("Waehrung", "Währung"),
    "Info": ("Info",),
}
_REQUIRED = (
    "Auftragskonto",
    "Buchungstag",
    "Valutadatum",
    "Buchungstext",
    "Verwendungszweck",
    "Beguenstigter/Zahlungspflichtiger",
    "Kontonummer/IBAN",
    "Betrag",
    "Waehrung",
)
# Stored in their own columns, or an IBAN: never copied into raw.
_NOT_RAW = {
    "Auftragskonto",
    "Buchungstag",
    "Valutadatum",
    "Buchungstext",
    "Verwendungszweck",
    "Mandatsreferenz",
    "Kundenreferenz (End-to-End)",
    "Beguenstigter/Zahlungspflichtiger",
    "Kontonummer/IBAN",
    "Betrag",
    "Waehrung",
}


class CsvCamtSource:
    """BankSource for the Sparkasse CSV-CAMT export."""

    def parse(self, data: bytes, media_type: str) -> ParsedStatement:
        return parse_csv_camt(data)


def looks_like(head: bytes) -> bool:
    """Format sniff (§7.1): a header row starting with Auftragskonto, quoted or not."""
    first = head.removeprefix(b"\xef\xbb\xbf").lstrip().split(b"\n", 1)[0]
    return first.lstrip(b'"').startswith(_FIRST_COLUMN.encode()) and b";" in first


def decode(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        try:
            return data[3:].decode("utf-8")
        except UnicodeDecodeError:
            raise RuleError(UNPARSABLE, "the file is not valid UTF-8") from None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1252")
    except UnicodeDecodeError:
        raise RuleError(UNPARSABLE, "the file is neither UTF-8 nor Windows-1252") from None


def parse_csv_camt(data: bytes) -> ParsedStatement:
    reader = csv.reader(io.StringIO(decode(data), newline=""), delimiter=";", quotechar='"')
    try:
        rows = [row for row in reader if any(cell.strip() for cell in row)]
    except csv.Error:
        raise RuleError(UNPARSABLE, "the CSV is malformed") from None
    if not rows:
        raise RuleError(UNPARSABLE, "the file is empty")
    index = _header(rows[0])

    lines: list[StatementLine] = []
    accounts: set[str] = set()
    pending = 0
    for number, row in enumerate(rows[1:], start=2):
        if len(row) != len(rows[0]):
            raise RuleError(UNPARSABLE, f"row {number} has {len(row)} columns, not {len(rows[0])}")
        cell = {name: row[i].strip() for name, i in index.items()}
        if cell.get("Info") == PENDING_INFO:
            pending += 1
            continue
        if cell["Waehrung"] != "EUR":
            raise RuleError(UNSUPPORTED_CURRENCY, f"row {number} is not in EUR")
        if cell["Auftragskonto"]:
            accounts.add("".join(cell["Auftragskonto"].split()).upper())
        lines.append(_line(cell, number))
    if len(accounts) > 1:
        raise RuleError(UNPARSABLE, "the rows belong to more than one account")

    warnings = [f"{pending} pending line(s) (Umsatz vorgemerkt) skipped"] if pending else []
    return ParsedStatement(
        format=StatementFormat.CSV_CAMT,
        account_iban=accounts.pop() if accounts else None,
        opening_balance=None,
        closing_balance=None,
        lines=lines,
        warnings=warnings,
    )


def _header(row: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    names = [cell.strip().removeprefix("﻿") for cell in row]
    for canonical, spellings in _COLUMNS.items():
        positions = [i for i, name in enumerate(names) if name in spellings]
        if len(positions) > 1:
            raise RuleError(UNPARSABLE, f"the header names {canonical} twice")
        if positions:
            found[canonical] = positions[0]
    missing = [name for name in _REQUIRED if name not in found]
    if missing:
        raise RuleError(UNPARSABLE, f"the header lacks {', '.join(missing)}")
    return found


def _line(cell: dict[str, str], number: int) -> StatementLine:
    raw = {
        name: value for name, value in sorted(cell.items()) if name not in _NOT_RAW and value != ""
    }
    return StatementLine(
        booking_date=_date(cell["Buchungstag"], number, required=True),
        value_date=_date(cell["Valutadatum"], number, required=False),
        amount=_amount(cell["Betrag"], number),
        counterparty_name=_text(cell["Beguenstigter/Zahlungspflichtiger"]),
        counterparty_iban=_text(cell["Kontonummer/IBAN"]),
        purpose=" ".join(cell["Verwendungszweck"].split()),
        booking_text=_text(cell["Buchungstext"]),
        bank_reference=None,
        end_to_end_ref=_text(cell.get("Kundenreferenz (End-to-End)", "")),
        mandate_ref=_text(cell.get("Mandatsreferenz", "")),
        raw=raw,
    )


def _text(value: str) -> str | None:
    value = " ".join(value.split())
    return value or None


def _amount(value: str, number: int) -> Decimal:
    """German decimal text into Decimal: "-1.234,56" -> Decimal("-1234.56"); never float."""
    compact = value.replace(" ", "")
    if not _AMOUNT.match(compact):
        raise RuleError(UNPARSABLE, f"row {number} has a malformed amount")
    return Decimal(compact.replace(".", "").replace(",", ".")).quantize(Decimal("0.01"))


def _date(value: str, number: int, *, required: bool) -> date | None:
    if not value:
        if required:
            raise RuleError(UNPARSABLE, f"row {number} has no booking date")
        return None
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(value, fmt).date()
        except ValueError:
            continue
        # %Y would read "01.04.26" as the year 26: each format only for its own length.
        if (fmt == "%d.%m.%y") == (len(value) == 8):
            return parsed
    raise RuleError(UNPARSABLE, f"row {number} has a malformed date")
