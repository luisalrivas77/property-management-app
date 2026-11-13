"""BankSource implementations and format detection (docs/phases/phase-2.md §7.1, D33, D34).

Every parser is pure (bytes in, ParsedStatement out): no database, no network, no model.
Amounts are parsed from their text into Decimal, never through float. Parse errors raise
hausbuch.rules.errors.RuleError with a statement.* code; messages never quote file content.

- camt.py: camt.052 (BkToCstmrAcctRpt/Rpt) and camt.053 (BkToCstmrStmt/Stmt), one .xml file
  or a ZIP of daily reports. Balances come from the file.
- csv_camt.py: Sparkasse CSV-CAMT (V2). No balances.
- dkb_csv.py: DKB's CSV export (phase 6 D173 (3)). No balances; its „Kontostand vom“ is a
  stated balance that only pre-fills the typed closing.
- card_pdf.py: the credit card's monthly PDF statement (phase 6 D176 (3)), read by code from
  its text layer; balances from the file.
- depot_pdf.py: a depot's cash statement (phase 6 step 4, D165 (1) amended), read by code from
  its text layer; balances from the file.
- Any other PDF Kontoauszug: not imported (phase 2 built only the extraction schema), refused
  with statement.unknown_format.
"""

from dataclasses import dataclass

from hausbuch.rules.errors import RuleError
from hausbuch.rules.statements import UNKNOWN_FORMAT
from hausbuch.schemas.bank import BankSource, ParsedStatement, StatementFormat

from . import camt, card_pdf, csv_camt, depot_pdf, dkb_csv

__all__ = ["BankSource", "ParseOutcome", "detect_format", "parse_statement"]

_ZIP_MAGIC = b"PK\x03\x04"
_SNIFF_BYTES = 64 * 1024
_CAMT_ROOTS = (b"BkToCstmrAcctRpt", b"BkToCstmrStmt")


@dataclass(frozen=True)
class ParseOutcome:
    """A parsed file plus, for a camt file, the first per-report sum or chain break
    (statement.report_unbalanced / statement.chain_break, D34): the import is stored but goes
    to review as a whole."""

    statement: ParsedStatement
    balance_error: str | None = None


def detect_format(data: bytes, media_type: str | None = None) -> StatementFormat:
    """The statement format from the bytes (§7.1). The declared media type is not trusted:
    a ZIP or camt XML may arrive as application/octet-stream or text/plain."""
    if data.startswith(_ZIP_MAGIC):
        return StatementFormat.CAMT
    head = data[:_SNIFF_BYTES]
    body = head.removeprefix(b"\xef\xbb\xbf").lstrip()
    if body.startswith(b"<") and any(root in head for root in _CAMT_ROOTS):
        return StatementFormat.CAMT
    if csv_camt.looks_like(head):
        return StatementFormat.CSV_CAMT
    if dkb_csv.looks_like(head):
        return StatementFormat.DKB_CSV
    if body.startswith(b"%PDF") or media_type == "application/pdf":
        # D176 (3): a PDF whose text layer is a card statement is one; any other is not read.
        pages = card_pdf.pdf_pages(data)
        if pages is not None and card_pdf.looks_like(pages):
            return StatementFormat.CARD_PDF
        # Phase 6 step 4: a depot's cash statement, by its own layout.
        if pages is not None and depot_pdf.looks_like(pages):
            return StatementFormat.DEPOT_PDF
        return StatementFormat.PDF
    raise RuleError(
        UNKNOWN_FORMAT, "not a camt.052/053 file, camt ZIP, Sparkasse CSV-CAMT or DKB CSV"
    )


def parse_statement(data: bytes, media_type: str | None = None) -> ParseOutcome:
    """Detect the format and parse. Raises RuleError (statement.*) on anything unreadable."""
    fmt = detect_format(data, media_type)
    if fmt is StatementFormat.CAMT:
        result = camt.read_camt(data)
        return ParseOutcome(result.statement, result.balance_error)
    if fmt is StatementFormat.CSV_CAMT:
        return ParseOutcome(csv_camt.parse_csv_camt(data))
    if fmt is StatementFormat.DKB_CSV:
        return ParseOutcome(dkb_csv.parse_dkb_csv(data))
    if fmt is StatementFormat.CARD_PDF:
        result = card_pdf.parse_card_pdf(data)
        return ParseOutcome(result.statement, result.balance_error)
    if fmt is StatementFormat.DEPOT_PDF:
        depot = depot_pdf.parse_depot_pdf(data)
        return ParseOutcome(depot.statement, depot.balance_error)
    # Only a card or depot statement is read from a PDF; a bank's PDF statement is not.
    raise RuleError(UNKNOWN_FORMAT, "PDF statements are not imported yet; use camt or CSV-CAMT")
