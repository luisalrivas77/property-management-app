"""DKB's web-banking CSV export (docs/phases/phase-6.md D173 (3), D164; fixture
tests/fixtures/phase6/dkb_csv.yaml).

The 2025 layout: UTF-8 with a BOM, `;`-separated, every field quoted. A few metadata lines come
first (the account type with the IBAN, „Kontostand vom DD.MM.YYYY:“ with the balance, in the
older layout also a period and a status line), then the header, found by its first column
name (Buchungsdatum) wherever it is. Columns are found by name, so their order does not
matter. Dates `dd.mm.yy` or `dd.mm.yyyy`; German amounts parsed into Decimal from the text,
never through float.

Reading rules (D173 (3)):

- A line whose Status is not „Gebucht“ is pending and skipped, with one warning counting them
  (the camt PDNG analogue).
- The counterparty is the payee on a debit and the payer on a credit; the IBAN column is the
  counterparty's (masked and HMAC'd on store). The account holder's own name column is not
  stored, and `raw` stays empty.
- Gläubiger-ID, Mandatsreferenz and Kundenreferenz are appended to the purpose, each only when
  filled and in that order, for the rules and the model; the mandate also goes to mandate_ref,
  the Kundenreferenz also to end_to_end_ref. Umsatztyp (Eingang/Ausgang) is the booking_text.
- The file states no opening and no closing balance. Its „Kontostand vom“ is returned as
  stated_balance / stated_balance_on only, which pre-fill the closing the user confirms; the
  import waits at pending_balance until then (Q-O75).
- No bank reference: the line hash takes D22's fallback, so two identical lines are kept by
  their position.
- The IBAN of the metadata lines is the file's account (D164's routing).
"""

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal

from hausbuch.rules.errors import RuleError
from hausbuch.rules.statements import UNPARSABLE
from hausbuch.schemas.bank import ParsedStatement, StatementFormat, StatementLine

from .csv_camt import _amount, _date, decode

BOOKED = "Gebucht"
_FIRST_COLUMN = "Buchungsdatum"
# The header must come within the first lines; the metadata block is four to six lines.
_HEADER_SEARCH = 12
_IBAN = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$")
_BALANCE_LABEL = re.compile(r"^Kontostand vom ([0-9]{2}\.[0-9]{2}\.[0-9]{4}):?$")

# Canonical column name -> accepted header spellings.
_COLUMNS: dict[str, tuple[str, ...]] = {
    "Buchungsdatum": ("Buchungsdatum",),
    "Wertstellung": ("Wertstellung",),
    "Status": ("Status",),
    "Zahlungspflichtige*r": ("Zahlungspflichtige*r", "Zahlungspflichtiger"),
    "Zahlungsempfänger*in": ("Zahlungsempfänger*in", "Zahlungsempfänger"),
    "Verwendungszweck": ("Verwendungszweck",),
    "Umsatztyp": ("Umsatztyp",),
    "IBAN": ("IBAN",),
    "Betrag": ("Betrag (€)", "Betrag (EUR)", "Betrag"),
    "Gläubiger-ID": ("Gläubiger-ID", "Gläubiger ID"),
    "Mandatsreferenz": ("Mandatsreferenz",),
    "Kundenreferenz": ("Kundenreferenz",),
}
_REQUIRED = (
    "Buchungsdatum",
    "Status",
    "Zahlungspflichtige*r",
    "Zahlungsempfänger*in",
    "Verwendungszweck",
    "IBAN",
    "Betrag",
)
# Appended to the purpose, in this order, each only when filled.
_REFERENCES = ("Gläubiger-ID", "Mandatsreferenz", "Kundenreferenz")


class DkbCsvSource:
    """BankSource for DKB's CSV export."""

    def parse(self, data: bytes, media_type: str) -> ParsedStatement:
        return parse_dkb_csv(data)


def looks_like(head: bytes) -> bool:
    """Format sniff: one of the first lines, quoted or not, starts with Buchungsdatum and is
    `;`-separated, and names a DKB column (Sparkasse's CSV starts with Auftragskonto)."""
    text = head.removeprefix(b"\xef\xbb\xbf").decode("utf-8", errors="ignore")
    for line in text.splitlines()[:_HEADER_SEARCH]:
        stripped = line.strip().lstrip('"')
        if stripped.startswith(_FIRST_COLUMN) and ";" in line and "Umsatztyp" in line:
            return True
    return False


def parse_dkb_csv(data: bytes) -> ParsedStatement:
    reader = csv.reader(io.StringIO(decode(data), newline=""), delimiter=";", quotechar='"')
    try:
        rows = [[cell.strip() for cell in row] for row in reader]
    except csv.Error:
        raise RuleError(UNPARSABLE, "the CSV is malformed") from None
    rows = [row for row in rows if any(row)]
    header_at = next(
        (
            i
            for i, row in enumerate(rows[:_HEADER_SEARCH])
            if row and row[0].removeprefix("﻿") == _FIRST_COLUMN
        ),
        None,
    )
    if header_at is None:
        raise RuleError(UNPARSABLE, "no header row starting with Buchungsdatum")
    account_iban, stated, stated_on = _metadata(rows[:header_at])
    header = rows[header_at]
    index = _header(header)

    lines: list[StatementLine] = []
    pending = 0
    for number, row in enumerate(rows[header_at + 1 :], start=header_at + 2):
        if len(row) != len(header):
            raise RuleError(UNPARSABLE, f"row {number} has {len(row)} columns, not {len(header)}")
        cell = {name: row[i] for name, i in index.items()}
        if cell["Status"] != BOOKED:
            pending += 1
            continue
        lines.append(_line(cell, number))

    warnings = [f"{pending} pending line(s) (Vorgemerkt) skipped"] if pending else []
    return ParsedStatement(
        format=StatementFormat.DKB_CSV,
        account_iban=account_iban,
        opening_balance=None,
        closing_balance=None,
        stated_balance=stated,
        stated_balance_on=stated_on,
        lines=lines,
        warnings=warnings,
    )


def _metadata(rows: list[list[str]]) -> tuple[str | None, Decimal | None, date | None]:
    """The account's IBAN and the „Kontostand vom“ balance with its day, from the lines
    before the header. Two different IBANs are the file's error."""
    ibans: set[str] = set()
    balance: Decimal | None = None
    balance_on: date | None = None
    for number, row in enumerate(rows, start=1):
        label = row[0].removeprefix("﻿") if row else ""
        stated = _BALANCE_LABEL.match(label)
        if stated is not None and len(row) > 1:
            balance_on = datetime.strptime(stated.group(1), "%d.%m.%Y").date()
            balance = _amount(row[1].replace("€", "").replace("\xa0", " ").strip(), number)
            continue
        for value in row:
            compact = "".join(value.split()).upper()
            if _IBAN.match(compact):
                ibans.add(compact)
    if len(ibans) > 1:
        raise RuleError(UNPARSABLE, "the file names more than one account")
    return (ibans.pop() if ibans else None), balance, balance_on


def _header(row: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    names = [cell.removeprefix("﻿") for cell in row]
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
    amount = _amount(cell["Betrag"], number)
    counterparty = cell["Zahlungsempfänger*in"] if amount < 0 else cell["Zahlungspflichtige*r"]
    references = [
        f"{name}: {_text(cell.get(name, ''))}" for name in _REFERENCES if _text(cell.get(name, ""))
    ]
    purpose = " ".join(part for part in (_text(cell["Verwendungszweck"]), *references) if part)
    iban = "".join(cell["IBAN"].split()).upper()
    return StatementLine(
        booking_date=_date(cell["Buchungsdatum"], number, required=True),
        value_date=_date(cell.get("Wertstellung", ""), number, required=False),
        amount=amount,
        counterparty_name=_text(counterparty),
        counterparty_iban=iban or None,
        purpose=purpose,
        booking_text=_text(cell.get("Umsatztyp", "")),
        bank_reference=None,
        end_to_end_ref=_text(cell.get("Kundenreferenz", "")),
        mandate_ref=_text(cell.get("Mandatsreferenz", "")),
    )


def _text(value: str) -> str | None:
    value = " ".join(value.split())
    return value or None
