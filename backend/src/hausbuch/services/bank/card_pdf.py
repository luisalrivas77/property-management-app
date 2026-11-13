"""The credit card's monthly PDF statement (docs/phases/phase-6.md D176 (3), §14.1 „The card
statement“; fixture tests/fixtures/phase6/card_pdf.yaml).

Read by code from the PDF's text layer (pypdf), never by the model and with no consent gate:
the issuer's portal offers PDF only and the PDF is digital (Q-O77). `parse_card_pages` reads
the extracted text, one string per page, so the fixture pins the layout as text;
`parse_card_pdf` is the thin wrapper over the bytes.

The layout (§14.1), as pypdf extracts it:

- Page 1: „Nummer: <Kundennummer>-<Kontonummer>-<yymmdd>-<yymmdd>“ (the statement number),
  „Zeitraum: dd.mm.yyyy - dd.mm.yyyy“, „Kundennummer: <n>“, the summary „Kontoauszug“ (Alter
  Saldo vom letzten Monat, Rückzahlungen, Einkauf, Bargeldabhebungen & Quasi-Geld, Zinsen und
  sonstige Gebühren, Neuer Saldo; owed amounts negative), the card row starting with the card
  number masked to its first six and last four digits, and under „Angaben bei Einzahlungen“
  the issuer's IBAN for payments to the card.
- Page 2 on: one row per line: Bew.ID (ten digits), Buchung (dd.mm.yyyy), Bew.Datum (absent on
  the payment and on an interest line), the kind („Kauf“, „Bezahlung“; two spaces before the
  description) or the interest text „Zinsen für einen genutzten Kredit“, the description
  (merchant and place, wrapping over a second physical line), „Wechselkurs <rate>“, the
  original amount and its currency, the amount in EUR and D (debit) or C (credit). A row's
  pieces wrap over up to three physical lines; they are joined with one space. Amounts use a
  space (or a no-break space) as thousands separator and a point as decimal mark.

Reading rules:

- The customer number routes the file (D164 by `external_ref`, D176 (3)); the statement
  number's first part must agree with it. It is returned as `account_ref` and masked and
  HMAC'd on store, never stored in clear.
- Of the card number only the last four digits are kept (`card_last4`); the first six never
  leave this module, and no message quotes any of it.
- The opening and closing balance are Alter and Neuer Saldo. The file must agree with itself:
  the summary's parts sum from Alter to Neuer Saldo, the lines sum from Alter to Neuer Saldo,
  and the totals printed under the lines (Verwendeter / Bezahlter Gesamtbetrag), when there,
  equal the debits and the credits. Anything else is a misread or a broken file:
  statement.report_unbalanced, stored and reviewed as a whole with none of its lines matched
  (phase 2 D34, the camt report precedent; ARCHITECTURE "a statement that fails goes to
  review as a whole").
- Each line's Bew.ID is its bank_reference, so the line hash takes D22's reference path: the
  same line in a re-downloaded statement is skipped.
- A line: booking_text = the kind word, counterparty_name = the description (a purchase's
  merchant; none on the payment or an interest line), purpose = the description, plus
  „<original amount> <currency>, Kurs <rate>“ for a purchase in another currency, whose
  original amount, currency and rate are also kept in `raw`. Bew.Datum is the value_date.
- The payment IBAN (Angaben bei Einzahlungen) is returned as `payment_iban`; the service
  only proposes it (D176 (4)).
"""

import io
import re
from datetime import date, datetime
from decimal import Decimal

from hausbuch.rules.errors import RuleError
from hausbuch.rules.statements import EMPTY, REPORT_UNBALANCED, UNPARSABLE
from hausbuch.schemas.bank import ParsedStatement, StatementFormat, StatementLine

# The label the summary block starts with and the text a card statement always carries; the
# format sniff needs both, together with a Bew.ID row header.
_SUMMARY_LABELS = ("Alter Saldo", "Neuer Saldo", "Bew.ID")
_AMOUNT = r"-?\d{1,3}(?:[  ]\d{3})*\.\d{2}"
_DATE = r"\d{2}\.\d{2}\.\d{4}"
_ROW_START = re.compile(rf"^(\d{{10}}) ({_DATE})\b")
_ROW = re.compile(
    rf"^(?P<id>\d{{10}}) (?P<booked>{_DATE})(?: (?P<tx>{_DATE}))? (?P<body>.*?)"
    rf"(?: Wechselkurs (?P<rate>\d+\.\d+))?(?: (?P<orig>{_AMOUNT}) (?P<cur>[A-Z]{{3}}))?"
    rf" (?P<eur>{_AMOUNT}) (?P<dc>[DC])$"
)
# A row's pieces never span more physical lines than this; more means the layout changed.
_MAX_ROW_LINES = 4
_STATEMENT_NUMBER = re.compile(r"^Nummer:\s*(\d+)-(\d+)-(\d{6})-(\d{6})\b")
_CUSTOMER = re.compile(r"^Kundennummer:\s*(\d+)\s*$")
_PERIOD = re.compile(rf"^Zeitraum:\s*({_DATE})\s*-\s*({_DATE})\s*$")
_CARD = re.compile(r"^\d{6}\*{6}(\d{4})\b")
_IBAN_LINE = re.compile(r"^IBAN\s+([A-Z]{2}[0-9]{2}(?:\s?[A-Z0-9]){11,30})\s*$")
_PAYMENT_DETAILS = "Angaben bei Einzahlungen"
# Summary label -> its key; the label text runs up to the colon.
_SUMMARY = {
    "Alter Saldo": "opening",
    "Rückzahlungen": "repayments",
    "Einkauf": "purchases",
    "Bargeldabhebungen & Quasi-Geld": "cash",
    "Zinsen und sonstige Gebühren": "interest_fees",
    "Neuer Saldo": "closing",
}
_PARTS = ("repayments", "purchases", "cash", "interest_fees")
_TOTALS = {"Verwendeter Gesamtbetrag": "debits", "Bezahlter Gesamtbetrag": "credits"}


class CardPdfSource:
    """BankSource for the card statement PDF."""

    def parse(self, data: bytes, media_type: str) -> ParsedStatement:
        return parse_card_pdf(data).statement


class CardParse:
    """A parsed statement and, when the file disagrees with itself, its file error."""

    def __init__(self, statement: ParsedStatement, balance_error: str | None) -> None:
        self.statement = statement
        self.balance_error = balance_error


def pdf_pages(data: bytes) -> list[str] | None:
    """The text of each page, or None when the bytes are no readable PDF."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        return [page.extract_text() or "" for page in reader.pages]
    except (PdfReadError, ValueError, KeyError, TypeError, OSError):
        return None


def looks_like(pages: list[str]) -> bool:
    """A card statement: its summary labels, a Bew.ID header and a customer number."""
    text = "\n".join(pages)
    return all(label in text for label in _SUMMARY_LABELS) and any(
        _CUSTOMER.match(line.strip()) for line in text.splitlines()
    )


def parse_card_pdf(data: bytes) -> CardParse:
    pages = pdf_pages(data)
    if pages is None:
        raise RuleError(UNPARSABLE, "the PDF cannot be read")
    return parse_card_pages(pages)


def parse_card_pages(pages: list[str]) -> CardParse:
    lines = [_clean(line) for page in pages for line in page.splitlines()]
    lines = [line for line in lines if line]
    customer = _customer(lines)
    period = _period(lines)
    summary = _summary(lines)
    totals = _totals(lines)
    rows = _rows(lines)
    if not rows:
        if any(summary[part] for part in _PARTS):
            # The summary counts movements the rows did not yield: a layout the reader missed.
            raise RuleError(UNPARSABLE, "the summary has movements but no row was read")
        # A month without a booking (review F6): the code every other format gives an empty file.
        raise RuleError(EMPTY, "the statement holds no line")
    statement_lines = [_line(row) for row in rows]
    warnings = []
    if period is not None and any(
        not period[0] <= line.booking_date <= period[1] for line in statement_lines
    ):
        warnings.append("a line is booked outside the statement's period")
    statement = ParsedStatement(
        format=StatementFormat.CARD_PDF,
        account_iban=None,
        account_ref=customer,
        card_last4=_card_last4(lines),
        payment_iban=_payment_iban(lines),
        opening_balance=summary["opening"],
        closing_balance=summary["closing"],
        lines=statement_lines,
        warnings=warnings,
    )
    return CardParse(statement, _balance_error(summary, totals, statement_lines))


# --- The page 1 facts ----------------------------------------------------------------------


def _clean(line: str) -> str:
    """No-break spaces as spaces, trailing space dropped; inner double spaces are kept (the
    gap between a row's kind and its description)."""
    return line.replace(" ", " ").rstrip().lstrip()


def _customer(lines: list[str]) -> str:
    found = {m.group(1) for line in lines if (m := _CUSTOMER.match(line))}
    numbers = {m.group(1) for line in lines if (m := _STATEMENT_NUMBER.match(line))}
    if len(found) != 1:
        raise RuleError(UNPARSABLE, "the statement names no single customer number")
    (customer,) = found
    if numbers and numbers != {customer}:
        raise RuleError(UNPARSABLE, "the statement number names another customer number")
    return customer


def _period(lines: list[str]) -> tuple[date, date] | None:
    for line in lines:
        if m := _PERIOD.match(line):
            start, end = _day(m.group(1)), _day(m.group(2))
            if end < start:
                raise RuleError(UNPARSABLE, "the statement's period ends before it starts")
            return start, end
    return None


def _summary(lines: list[str]) -> dict[str, Decimal]:
    found: dict[str, Decimal] = {}
    for line in lines:
        label, sep, value = line.partition(":")
        if not sep:
            continue
        key = next((k for name, k in _SUMMARY.items() if label.startswith(name)), None)
        if key is None or key in found:
            continue
        amount = re.fullmatch(_AMOUNT, value.strip())
        if amount is None:
            raise RuleError(UNPARSABLE, f"the summary's {key} is not an amount")
        found[key] = _money(amount.group(0))
    missing = [key for key in _SUMMARY.values() if key not in found]
    if missing:
        raise RuleError(UNPARSABLE, f"the summary lacks {', '.join(missing)}")
    return found


def _totals(lines: list[str]) -> dict[str, Decimal]:
    found: dict[str, Decimal] = {}
    for line in lines:
        label, sep, value = line.partition(":")
        key = _TOTALS.get(label.strip()) if sep else None
        if key is not None and (amount := re.fullmatch(_AMOUNT, value.strip())):
            found[key] = _money(amount.group(0))
    return found


def _card_last4(lines: list[str]) -> str | None:
    """Only the last four digits; the masked number's first six are never kept."""
    found = {m.group(1) for line in lines if (m := _CARD.match(line))}
    if len(found) > 1:
        raise RuleError(UNPARSABLE, "the statement names more than one card")
    return found.pop() if found else None


def _payment_iban(lines: list[str]) -> str | None:
    """The first IBAN line after „Angaben bei Einzahlungen“."""
    after = next((i for i, line in enumerate(lines) if line.startswith(_PAYMENT_DETAILS)), None)
    if after is None:
        return None
    for line in lines[after + 1 : after + 6]:
        if m := _IBAN_LINE.match(line):
            return "".join(m.group(1).split())
    return None


# --- The rows ------------------------------------------------------------------------------


def _rows(lines: list[str]) -> list[re.Match[str]]:
    """Join each row's physical lines until the row's tail (EUR amount and D/C) is read.
    Lines between one row's tail and the next row's start (totals, footers, the next page's
    header) are not part of any row."""
    rows: list[re.Match[str]] = []
    pending: list[str] = []
    for line in lines:
        if _ROW_START.match(line):
            if pending:
                raise RuleError(UNPARSABLE, f"row {len(rows) + 1} has no amount")
            pending = [line]
        elif pending:
            pending.append(line)
        else:
            continue
        found = _ROW.match(" ".join(pending))
        if found is not None:
            rows.append(found)
            pending = []
        elif len(pending) >= _MAX_ROW_LINES:
            raise RuleError(UNPARSABLE, f"row {len(rows) + 1} has no amount")
    if pending:
        raise RuleError(UNPARSABLE, f"row {len(rows) + 1} has no amount")
    return rows


def _line(row: re.Match[str]) -> StatementLine:
    eur = _money(row["eur"])
    if (eur < 0 and row["dc"] == "C") or (eur > 0 and row["dc"] == "D"):
        raise RuleError(UNPARSABLE, "a row's sign disagrees with its D/C mark")
    amount = -abs(eur) if row["dc"] == "D" else abs(eur)
    body = row["body"].strip()
    if "  " in body:
        kind, description = (part.strip() for part in body.split("  ", 1))
    else:
        kind, description = body.split(" ", 1)[0], None
    description = " ".join(description.split()) if description else None
    raw: dict[str, str] = {}
    purpose = description or " ".join(body.split())
    currency = row["cur"]
    if currency is not None and currency != "EUR":
        original = str(_money(row["orig"]))
        raw = {"original_amount": original, "original_currency": currency}
        purpose = f"{purpose} ({original} {currency}"
        if row["rate"] is not None:
            raw["exchange_rate"] = row["rate"]
            purpose += f", Kurs {row['rate']}"
        purpose += ")"
    merchant = description if kind not in _NO_MERCHANT else None
    return StatementLine(
        booking_date=_day(row["booked"]),
        value_date=_day(row["tx"]) if row["tx"] else None,
        amount=amount,
        counterparty_name=merchant,
        purpose=purpose,
        booking_text=kind,
        bank_reference=row["id"],
        raw=raw,
    )


# The payment's description is the payment method, not a payee.
_NO_MERCHANT = frozenset({"Bezahlung"})


def _balance_error(
    summary: dict[str, Decimal], totals: dict[str, Decimal], lines: list[StatementLine]
) -> str | None:
    opening, closing = summary["opening"], summary["closing"]
    parts = sum((summary[key] for key in _PARTS), Decimal(0))
    total = sum((line.amount for line in lines), Decimal(0))
    debits = sum((line.amount for line in lines if line.amount < 0), Decimal(0))
    credits = sum((line.amount for line in lines if line.amount > 0), Decimal(0))
    if opening + parts != closing or opening + total != closing:
        return REPORT_UNBALANCED
    if totals.get("debits", debits) != debits or totals.get("credits", credits) != credits:
        return REPORT_UNBALANCED
    return None


def _money(text: str) -> Decimal:
    return Decimal(text.replace(" ", "").replace(" ", "")).quantize(Decimal("0.01"))


def _day(text: str) -> date:
    try:
        return datetime.strptime(text, "%d.%m.%Y").date()
    except ValueError:
        raise RuleError(UNPARSABLE, "a date is malformed") from None
