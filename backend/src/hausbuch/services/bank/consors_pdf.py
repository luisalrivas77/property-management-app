"""A Consors Finanz (BNP Paribas) credit card's monthly PDF statement: the second card issuer
read by code (docs/phases/phase-6.md D176 (3); fixture tests/fixtures/phase6/card_pdf_consors.yaml).

Like the first issuer's statement (card_pdf.py) it is read from the PDF's text layer, never by
the model, and gives the same ParsedStatement contract under the same format `card_pdf`
(statement_import.format is a CHECK list, so a new value would need a migration; the two
layouts are told apart by their text). Unlike that one, this PDF draws its table column by
column, so pypdf's plain extraction gives all dates, then all texts, then all amounts: the
rows are read from pypdf's layout extraction (`layout_pages`), which puts each printed row on
one line. `parse_consors_pages` reads that text, one string per page, so the fixture pins the
layout as text; `parse_consors_pdf` is the thin wrapper over the bytes. The sniff
(`looks_like`) needs only labels, so it runs on the plain text the format sniff already has.

The layout, as pypdf's layout mode extracts it (one real statement read on Luis's computer,
never committed; a month holding only the payment of the previous balance):

- Page 1: „Kundennummer: <n>“ and „Kontonummer: <n>“ (digit groups split by spaces), „IBAN:
  <IBAN>“ with „Für Überweisungen auf das Kartenkonto …“ (the card account's own IBAN, the one
  the household pays the card to), the heading „Kontoauszug zu Consors Finanz <brand> Nr.:
  dddd XXXX XXXX dddd“, and the sentence „… alle Umsätze vom dd.mm.yyyy bis einschl.
  dd.mm.yyyy“ (the period).
- From page 2: the column header „Umsatzdatum Buchungsdatum Verwendungszweck Soll/Haben in
  EUR“, then „ALTER SALDO VOM dd.mm.yy“ with its amount, one row per line (dates dd.mm.yy; the
  payment row prints its Buchungsdatum only), then the totals: an amount alone in the amount
  column on the line above „GESAMTUMSÄTZE“ and one on that line. Amounts carry their sign
  („-“ owed / debit, „+“ credit), a point as thousands separator and a comma as decimal mark;
  a sign alone is an empty total. No Neuer Saldo is printed and no row carries a reference.

Reading rules:

- The Kontonummer routes the file (D164 by `external_ref`, D176 (3)) and is returned as
  `account_ref`, digits only; it is masked and HMAC'd on store, never stored in clear. Not
  the Kundennummer: that names the customer, who can hold other Consors Finanz products, while
  the Kontonummer names this card account (the printed IBAN ends with it, which is checked).
- Of the card number only the last four digits are kept (`card_last4`); no message quotes
  any part of it.
- The opening balance is ALTER SALDO (owed amounts negative, as printed). The closing balance
  is the opening plus the printed totals (GESAMTUMSÄTZE): the statement prints no new balance.
  The file must agree with itself: two totals (the one above the label and the one on it)
  are the debits and the credits, one total is their sum, and the lines must give the same;
  a printed NEUER SALDO (not seen yet) must equal the opening plus the totals. Anything else
  is statement.report_unbalanced, stored and reviewed as a whole with none of its lines
  matched (phase 2 D34). This check is weaker than the first issuer's (which prints both
  balances and a summary): the closing is only as good as the printed totals, and the chain
  to the next statement's ALTER SALDO is what proves it (the import's opening check).
- No row prints a reference, so the line hash takes D22's position path: the same statement
  downloaded again gives the same hashes, and its lines are skipped.
- A line: booking_date = Buchungsdatum (the one date when a row prints one), value_date =
  Umsatzdatum when a row prints both; the issuer's own text „EINGEGANGENE ZAHLUNG“ is the
  booking_text with no counterparty (the payment, booked by code as the card's transfer leg,
  rules.household.card); every other row's text is its merchant (counterparty_name) and its
  purpose, with no booking_text. A text-only line indented to the Verwendungszweck column is
  taken as the previous row's text continuing (not seen yet); any other text line (a footer, a
  page number) is not part of a row.
- The printed IBAN is returned as `payment_iban`; the service only proposes it (D176 (4)).
"""

import io
import re
from datetime import date, datetime
from decimal import Decimal

from hausbuch.rules.errors import RuleError
from hausbuch.rules.statements import EMPTY, REPORT_UNBALANCED, UNPARSABLE
from hausbuch.schemas.bank import ParsedStatement, StatementFormat, StatementLine

from .card_pdf import CardParse

# What the sniff needs, all present in the plain extraction too.
_SNIFF_TEXTS = ("Kontoauszug zu Consors Finanz", "ALTER SALDO", "GESAMTUMSÄTZE", "Verwendungszweck")
_AMOUNT = r"[+-]\d{1,3}(?:\.\d{3})*,\d{2}"
_TOKEN = rf"(?:{_AMOUNT}|[+-])"  # a sign alone: an empty total
_SHORT_DATE = r"\d{2}\.\d{2}\.\d{2}"
_DATE = r"\d{2}\.\d{2}\.\d{4}"
_GAP = r"\s{2,}"
_HEADER = ("Umsatzdatum", "Verwendungszweck", "Soll/Haben")
_OPENING = re.compile(rf"^ALTER SALDO VOM {_SHORT_DATE}{_GAP}(?P<amount>{_AMOUNT})$")
_CLOSING = re.compile(rf"^NEUER SALDO\b.*?{_GAP}(?P<amount>{_AMOUNT})$")
_TOTALS = re.compile(rf"^GESAMTUMSÄTZE{_GAP}(?P<token>{_TOKEN})$")
_TOKEN_ONLY = re.compile(rf"^(?P<token>{_TOKEN})$")
_ROW = re.compile(
    rf"^(?:(?P<first>{_SHORT_DATE})\s+)?(?:(?P<second>{_SHORT_DATE})\s+)?"
    rf"(?P<text>\S.*?){_GAP}(?P<amount>{_AMOUNT})$"
)
_PAGE_NUMBER = re.compile(r"^\d+ / \d+$")
_ACCOUNT = re.compile(r"^Kontonummer:\s+(\d[\d ]*\d)$")
_IBAN = re.compile(r"^IBAN:\s+([A-Z]{2}[0-9]{2}(?: ?[A-Z0-9]){11,30})$")
_CARD = re.compile(r"^Kontoauszug zu .*Nr\.:\s*(?:\d{4}|X{4}) X{4} X{4} (\d{4})$")
_PERIOD = re.compile(rf"Umsätze vom ({_DATE}) bis einschl\. ({_DATE})")
# The issuer's own texts: rows without a merchant.
_ISSUER_TEXTS = frozenset({"EINGEGANGENE ZAHLUNG"})


def looks_like(pages: list[str]) -> bool:
    """A Consors Finanz card statement: its heading, the table's labels and a Kontonummer."""
    text = "\n".join(pages)
    return all(label in text for label in _SNIFF_TEXTS) and any(
        line.strip().startswith("Kontonummer:") for line in text.splitlines()
    )


def layout_pages(data: bytes) -> list[str] | None:
    """The text of each page as pypdf's layout mode places it (one printed row per line), or
    None when the bytes are no readable PDF."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        return [page.extract_text(extraction_mode="layout") or "" for page in reader.pages]
    except (PdfReadError, ValueError, KeyError, TypeError, OSError):
        return None


def parse_consors_pdf(data: bytes) -> CardParse:
    pages = layout_pages(data)
    if pages is None:
        raise RuleError(UNPARSABLE, "the PDF cannot be read")
    return parse_consors_pages(pages)


def parse_consors_pages(pages: list[str]) -> CardParse:
    facts = [line.replace("\xa0", " ").strip() for page in pages for line in page.splitlines()]
    facts = [line for line in facts if line]
    account = _account(facts)
    payment_iban = _payment_iban(facts, account)
    period = _period(facts)
    table = _table(pages)
    if not table.rows:
        if any(token != Decimal(0) for token in table.totals):
            raise RuleError(UNPARSABLE, "the totals have movements but no row was read")
        raise RuleError(EMPTY, "the statement holds no line")
    lines = [_line(row) for row in table.rows]
    warnings = []
    if period is not None and any(not period[0] <= ln.booking_date <= period[1] for ln in lines):
        warnings.append("a line is booked outside the statement's period")
    printed = sum(table.totals, Decimal(0))
    statement = ParsedStatement(
        format=StatementFormat.CARD_PDF,
        account_iban=None,
        account_ref=account,
        card_last4=_card_last4(facts),
        payment_iban=payment_iban,
        opening_balance=table.opening,
        closing_balance=table.closing if table.closing is not None else table.opening + printed,
        lines=lines,
        warnings=warnings,
    )
    return CardParse(statement, _balance_error(table, lines))


# --- The page 1 facts ----------------------------------------------------------------------


def _account(lines: list[str]) -> str:
    found = {"".join(m.group(1).split()) for line in lines if (m := _ACCOUNT.match(line))}
    if len(found) != 1:
        raise RuleError(UNPARSABLE, "the statement names no single account number")
    return found.pop()


def _payment_iban(lines: list[str], account: str) -> str | None:
    """The card account's own IBAN; its last digits must be the Kontonummer."""
    found = {"".join(m.group(1).split()) for line in lines if (m := _IBAN.match(line))}
    if len(found) > 1:
        raise RuleError(UNPARSABLE, "the statement names more than one IBAN")
    if not found:
        return None
    iban = found.pop()
    if len(account) > 10 or not iban.endswith(account.zfill(10)):
        raise RuleError(UNPARSABLE, "the IBAN names another account number")
    return iban


def _card_last4(lines: list[str]) -> str | None:
    """Only the last four digits; the visible first four are never kept."""
    found = {m.group(1) for line in lines if (m := _CARD.match(line))}
    if len(found) > 1:
        raise RuleError(UNPARSABLE, "the statement names more than one card")
    return found.pop() if found else None


def _period(lines: list[str]) -> tuple[date, date] | None:
    if (m := _PERIOD.search(" ".join(lines))) is None:
        return None
    start, end = _day(m.group(1)), _day(m.group(2))
    if end < start:
        raise RuleError(UNPARSABLE, "the statement's period ends before it starts")
    return start, end


# --- The table -----------------------------------------------------------------------------


class _Row:
    def __init__(self, first: str | None, second: str | None, text: str, amount: str) -> None:
        self.first, self.second, self.amount = first, second, amount
        self.texts = [text]


class _Table:
    def __init__(self) -> None:
        self.opening: Decimal | None = None
        self.closing: Decimal | None = None
        self.totals: list[Decimal] = []
        self.signs: list[str] = []
        self.rows: list[_Row] = []


def _table(pages: list[str]) -> _Table:
    """Each page's lines under its column header: ALTER SALDO, the rows, the totals."""
    table = _Table()
    pending: list[str] = []  # amounts alone on a line, waiting for GESAMTUMSÄTZE
    ended = False
    for page in pages:
        column = None  # the Verwendungszweck column of this page's header
        for raw in page.splitlines():
            raw = raw.replace("\xa0", " ").rstrip()
            line = raw.strip()
            if column is None:
                if all(label in line for label in _HEADER):
                    column = raw.index("Verwendungszweck")
                continue
            if not line or _PAGE_NUMBER.match(line):
                continue
            if m := _OPENING.match(line):
                if table.opening is not None or table.rows or ended:
                    raise RuleError(UNPARSABLE, "a second or late ALTER SALDO")
                table.opening = _money(m["amount"])
            elif m := _TOTALS.match(line):
                if ended:
                    raise RuleError(UNPARSABLE, "a second GESAMTUMSÄTZE")
                for token in [*pending, m["token"]]:
                    table.signs.append(token[0])
                    table.totals.append(_token(token))
                pending, ended = [], True
            elif m := _CLOSING.match(line):
                if not ended or table.closing is not None:
                    raise RuleError(UNPARSABLE, "a NEUER SALDO outside its place")
                table.closing = _money(m["amount"])
            elif m := _TOKEN_ONLY.match(line):
                if ended or len(pending) >= 1:
                    raise RuleError(UNPARSABLE, "an amount without its row")
                pending.append(m["token"])
            elif m := _ROW.match(line):
                if ended or pending:
                    raise RuleError(UNPARSABLE, "a row outside the table")
                table.rows.append(_Row(m["first"], m["second"], m["text"], m["amount"]))
            elif table.rows and not ended and not pending and _indent(raw) >= column - 2:
                table.rows[-1].texts.append(line)  # the previous row's text continuing
            # Anything else (a footer, the next page's heading) is not part of the table.
    if table.opening is None:
        raise RuleError(UNPARSABLE, "the statement prints no ALTER SALDO")
    if not ended or pending:
        raise RuleError(UNPARSABLE, "the statement prints no GESAMTUMSÄTZE")
    return table


def _indent(raw: str) -> int:
    return len(raw) - len(raw.lstrip())


def _line(row: _Row) -> StatementLine:
    text = " ".join(" ".join(row.texts).split())
    issuer = text in _ISSUER_TEXTS
    booked, made = (row.second, row.first) if row.second is not None else (row.first, None)
    if booked is None:
        raise RuleError(UNPARSABLE, "a row has no date")
    return StatementLine(
        booking_date=_short_day(booked),
        value_date=_short_day(made) if made is not None else None,
        amount=_money(row.amount),
        counterparty_name=None if issuer else text,
        purpose=text,
        booking_text=text if issuer else None,
    )


def _balance_error(table: _Table, lines: list[StatementLine]) -> str | None:
    assert table.opening is not None
    total = sum((line.amount for line in lines), Decimal(0))
    debits = sum((line.amount for line in lines if line.amount < 0), Decimal(0))
    credits = sum((line.amount for line in lines if line.amount > 0), Decimal(0))
    printed = sum(table.totals, Decimal(0))
    if total != printed:
        return REPORT_UNBALANCED
    if len(table.totals) == 2:
        by_sign = {"-": Decimal(0), "+": Decimal(0)}
        for sign, value in zip(table.signs, table.totals, strict=True):
            by_sign[sign] += value
        if (by_sign["-"], by_sign["+"]) != (debits, credits):
            return REPORT_UNBALANCED
    if table.closing is not None and table.opening + total != table.closing:
        return REPORT_UNBALANCED
    return None


def _token(text: str) -> Decimal:
    return Decimal("0.00") if text in ("+", "-") else _money(text)


def _money(text: str) -> Decimal:
    return Decimal(text.replace(".", "").replace(",", ".")).quantize(Decimal("0.01"))


def _day(text: str) -> date:
    try:
        return datetime.strptime(text, "%d.%m.%Y").date()
    except ValueError:
        raise RuleError(UNPARSABLE, "a date is malformed") from None


def _short_day(text: str) -> date:
    """The table's dd.mm.yy; the statement's period is printed with four digits."""
    try:
        return datetime.strptime(text, "%d.%m.%y").date()
    except ValueError:
        raise RuleError(UNPARSABLE, "a date is malformed") from None
