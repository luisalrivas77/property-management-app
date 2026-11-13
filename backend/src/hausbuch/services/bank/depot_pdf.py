"""A depot's cash statement (Kontoauszug) as a PDF (docs/phases/phase-6.md D165 (1) as amended
in step 4, §14.1 „The depot's cash statement“).

quirion keeps every strategy's money on its own Vermögensverwaltungskonto and the money between
the strategies and the household's bank accounts on a Verrechnungskonto; each prints its own
Kontoauszug as a digital PDF. It is read by code from its text layer (pypdf), never by the
model and with no consent gate, like the card statement (D176 (3)): the layout is fixed and the
file names the account holders. `parse_depot_pages` reads the extracted text, one string per
page; `parse_depot_pdf` is the thin wrapper over the bytes.

The layout, as pypdf extracts it (values before their labels):

- Each page's table starts with the header „UmsatzValutaBuchungsdatumBuchungshinweise“.
- A row starts with „<Umsatz> EUR<Valuta><Buchungsdatum><headline>“: the signed amount in
  German notation, the value date and the booking date run together, then the headline
  („Wertpapier Kauf, Ref.: <n>“, „Wertpapier Verkauf, Ref.: <n>“,
  „Vermögensverwaltungshonorar <Konto>, <von> - <bis>“, …). Its detail lines follow: a
  trade's fund name, „<ISIN>, ST <units>“ and the tax it withheld („KEST: EUR -<n>“); a fee's
  „Bemessungsgrundlage …“, „Betrag inkl. USt: …“ and „Ref.: <n>“.
- After the last row: „<n> EURalter Saldo<date>letzter Auszug“, „<n> EURUmsatz“ and
  „<n> EURneuer Saldo…“.
- The header block: „<Kontonummer> EURKonto“, „<IBAN>IBAN“, „Vermögensverwaltungskonto in EUR“
  (or the Verrechnungskonto), „<date>Datum“, „<n>Auszug-Nr“, and the holders' names and
  address, which this reader never reads.

Reading rules:

- The account number routes the file (D164 by `external_ref`) and is returned as
  `account_ref`; it is masked and HMAC'd on store, never stored in clear. The IBAN the
  statement prints is returned as `account_iban`; the service only proposes it for the depot
  account (D156 amended, the D176 (4) pattern) and routes by the number.
- A row's headline is its `booking_text`, with any number of seven or more digits that is not
  a reference („Ref.: <n>“) masked to „**** “ + its last four (the fee's headline names the
  account number). `rules.depot.categorise` reads the headline only (§14.1).
- Of a row's detail lines only the known forms are kept, in `purpose` (joined by „; “): the
  fund name directly after a trade's headline, the ISIN and units line, a withheld tax, the
  fee's basis and VAT lines and its reference. The first line of any other form ends the row's
  details, so a page's address block or footer never joins a row. The ISIN, the units and the
  withheld tax are also kept in `raw`.
- The reference („Ref.: <n>“ on the headline or a detail line) is the line's
  `bank_reference`, so a re-downloaded statement's lines are skipped (D22).
- The file must agree with itself: alter Saldo + Σ rows = neuer Saldo, and the printed Umsatz
  equals Σ rows. Anything else is statement.report_unbalanced, stored and reviewed as a whole
  (phase 2 D34, as for the card).
"""

import re
from datetime import date, datetime
from decimal import Decimal

from hausbuch.rules.errors import RuleError
from hausbuch.rules.statements import EMPTY, REPORT_UNBALANCED, UNPARSABLE
from hausbuch.schemas.bank import ParsedStatement, StatementFormat, StatementLine

from .card_pdf import pdf_pages

__all__ = ["DepotParse", "looks_like", "parse_depot_pages", "parse_depot_pdf", "pdf_pages"]

_AMOUNT = r"-?\d{1,3}(?:\.\d{3})*,\d{2}"
_DATE = r"\d{2}\.\d{2}\.\d{4}"
_TABLE_HEADER = re.compile(r"Umsatz\s*Valuta\s*Buchungsdatum\s*Buchungshinweise")
_ROW = re.compile(
    rf"^(?P<amount>{_AMOUNT}) EUR\s*(?P<valuta>{_DATE})\s*(?P<booked>{_DATE})\s*(?P<head>\S.*)$"
)
_OPENING = re.compile(rf"^(?P<amount>{_AMOUNT}) EUR\s*alter Saldo")
_TOTAL = re.compile(rf"^(?P<amount>{_AMOUNT}) EUR\s*Umsatz$")
_CLOSING = re.compile(rf"^(?P<amount>{_AMOUNT}) EUR\s*neuer Saldo")
_KONTO = re.compile(r"^(?P<number>\d{6,}) EUR\s*Konto$")
_IBAN = re.compile(r"^(?P<iban>[A-Z]{2}\d{2}[A-Z0-9]{11,30})\s*IBAN$")
_REFERENCE = re.compile(r"\bRef\.:\s*(?P<ref>\d{4,})\b")
# A number of seven or more digits that is not a reference: an account or customer number.
_ACCOUNT_NUMBER = re.compile(r"(?<!Ref\.: )(?<!Ref\.:)(?<!\d)(\d{7,})(?!\d)")
_TRADE_HEADLINE = re.compile(r"\b(Kauf|Verkauf)\b", re.IGNORECASE)
_ISIN_UNITS = re.compile(
    r"^(?P<isin>[A-Z]{2}[A-Z0-9]{9}\d), ST (?P<units>-?\d{1,3}(?:\.\d{3})*,\d+)$"
)
_TAX = re.compile(
    rf"^(?P<kind>KEST|KAPST|SOLI|SOLZ|KIST|KIRCHENSTEUER|KAPITALERTRAGSTEUER|"
    rf"SOLIDARITÄTSZUSCHLAG)[^:]*:\s*EUR\s*(?P<amount>{_AMOUNT})$",
    re.IGNORECASE,
)
_KNOWN_DETAILS = (
    _ISIN_UNITS,
    _TAX,
    re.compile(rf"^Bemessungsgrundlage {_AMOUNT} EUR$"),
    re.compile(r"^Betrag inkl\. USt: "),
    re.compile(r"^Ref\.:\s*\d+$"),
)
# A fund name: letters, digits and the punctuation fund names use, never an IBAN or a date.
_FUND_NAME = re.compile(r"^[A-Za-zÄÖÜäöüß0-9 .,\-()/&'+%]{3,120}$")


class DepotParse:
    """A parsed statement and, when the file disagrees with itself, its file error."""

    def __init__(self, statement: ParsedStatement, balance_error: str | None) -> None:
        self.statement = statement
        self.balance_error = balance_error


class DepotPdfSource:
    """BankSource for a depot's cash statement PDF."""

    def parse(self, data: bytes, media_type: str) -> ParsedStatement:
        return parse_depot_pdf(data).statement


def looks_like(pages: list[str]) -> bool:
    """A depot cash statement: the row table's header, both balances and an account number.
    A Rechnungsabschluss (the interest summary of the same account) has the account number
    but no row table, so it is not one."""
    text = "\n".join(pages)
    lines = [line.strip() for line in text.splitlines()]
    return (
        _TABLE_HEADER.search(text) is not None
        and any(_OPENING.match(line) for line in lines)
        and any(_CLOSING.match(line) for line in lines)
        and any(_KONTO.match(line) for line in lines)
    )


def parse_depot_pdf(data: bytes) -> DepotParse:
    pages = pdf_pages(data)
    if pages is None:
        raise RuleError(UNPARSABLE, "the PDF cannot be read")
    return parse_depot_pages(pages)


def parse_depot_pages(pages: list[str]) -> DepotParse:
    lines = [_clean(line) for page in pages for line in page.splitlines()]
    lines = [line for line in lines if line]
    number = _single(lines, _KONTO, "number", "account number")
    iban = _optional_single(lines, _IBAN, "iban", "IBAN")
    opening = _money(_single(lines, _OPENING, "amount", "opening balance"))
    closing = _money(_single(lines, _CLOSING, "amount", "closing balance"))
    total_text = _optional_single(lines, _TOTAL, "amount", "Umsatz")
    rows = _rows(lines)
    if not rows:
        if opening != closing:
            raise RuleError(UNPARSABLE, "the balances differ but no row was read")
        raise RuleError(EMPTY, "the statement holds no line")
    total = sum((line.amount for line in rows), Decimal(0))
    printed_total = _money(total_text) if total_text is not None else total
    error = REPORT_UNBALANCED if opening + total != closing or printed_total != total else None
    statement = ParsedStatement(
        format=StatementFormat.DEPOT_PDF,
        account_iban=iban,
        account_ref=number,
        opening_balance=opening,
        closing_balance=closing,
        lines=rows,
    )
    return DepotParse(statement, error)


def _clean(line: str) -> str:
    return " ".join(line.replace(" ", " ").split())


def _single(lines: list[str], pattern: re.Pattern[str], group: str, what: str) -> str:
    found = _optional_single(lines, pattern, group, what)
    if found is None:
        raise RuleError(UNPARSABLE, f"the statement names no {what}")
    return found


def _optional_single(
    lines: list[str], pattern: re.Pattern[str], group: str, what: str
) -> str | None:
    """The one value a line of this form names; every page repeats the header block, so the
    same value may appear more than once, but two different values are refused."""
    found = {m.group(group) for line in lines if (m := pattern.match(line))}
    if len(found) > 1:
        raise RuleError(UNPARSABLE, f"the statement names more than one {what}")
    return found.pop() if found else None


def _rows(lines: list[str]) -> list[StatementLine]:
    rows: list[StatementLine] = []
    current: re.Match[str] | None = None
    details: list[str] = []
    open_details = False
    for line in lines:
        if row := _ROW.match(line):
            if current is not None:
                rows.append(_line(current, details))
            current, details, open_details = row, [], True
            continue
        if current is None or not open_details:
            continue
        if _is_detail(line, current, details):
            details.append(line)
        else:
            open_details = False  # an address block, a footer or the balances: the row ends
    if current is not None:
        rows.append(_line(current, details))
    return rows


def _is_detail(line: str, row: re.Match[str], details: list[str]) -> bool:
    if any(pattern.match(line) for pattern in _KNOWN_DETAILS):
        return True
    # The fund name follows a trade's headline directly, before its ISIN line.
    return (
        not details
        and _TRADE_HEADLINE.search(row["head"]) is not None
        and _FUND_NAME.match(line) is not None
        and _OPENING.match(line) is None
    )


def _line(row: re.Match[str], details: list[str]) -> StatementLine:
    headline = row["head"].strip()
    reference = None
    raw: dict[str, str] = {}
    for text in (headline, *details):
        if reference is None and (m := _REFERENCE.search(text)):
            reference = m.group("ref")
        if m := _ISIN_UNITS.match(text):
            raw["isin"] = m.group("isin")
            raw["units"] = str(_units(m.group("units")))
        elif m := _TAX.match(text):
            withheld = Decimal(raw.get("tax_withheld", "0")) + abs(_money(m.group("amount")))
            raw["tax_withheld"] = str(withheld)
    return StatementLine(
        booking_date=_day(row["booked"]),
        value_date=_day(row["valuta"]),
        amount=_money(row["amount"]),
        purpose="; ".join(_mask_numbers(d) for d in details),
        booking_text=_mask_numbers(headline),
        bank_reference=reference,
        raw=raw,
    )


def _mask_numbers(text: str) -> str:
    return _ACCOUNT_NUMBER.sub(lambda m: f"**** {m.group(1)[-4:]}", text)


def _money(text: str) -> Decimal:
    return Decimal(text.replace(".", "").replace(",", ".")).quantize(Decimal("0.01"))


def _units(text: str) -> Decimal:
    return Decimal(text.replace(".", "").replace(",", "."))


def _day(text: str) -> date:
    try:
        return datetime.strptime(text, "%d.%m.%Y").date()
    except ValueError:
        raise RuleError(UNPARSABLE, "a date is malformed") from None
