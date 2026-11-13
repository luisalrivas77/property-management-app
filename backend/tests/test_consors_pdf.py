"""A second card issuer's PDF statement read by code: Consors Finanz (phase 6 D176 (3);
fixture tests/fixtures/phase6/card_pdf_consors.yaml, its parse half; the import half is in
tests/db/test_services_consors_import.py).

Each case's page text is drawn into a PDF (reportlab, one physical line per text line), so the
bytes path through pypdf's layout text and the format sniff on its plain text are replayed
too. Invented statements only.
"""

import re
from decimal import Decimal
from uuid import uuid4

import fixture_models_phase6 as phase6
import pytest
from test_card_pdf import CARD, draw
from test_card_pdf import case_bytes as avarda_bytes

from hausbuch.rules import statements as rules
from hausbuch.rules.errors import RuleError
from hausbuch.rules.household import card as card_rules
from hausbuch.schemas.allocation import AllocationCategory, AllocationMethod, MatchTransaction
from hausbuch.schemas.bank import StatementFormat
from hausbuch.services.bank import (
    card_pdf,
    consors_pdf,
    detect_format,
    is_card_statement,
    parse_statement,
)

CONSORS = phase6.load("card_pdf_consors")
assert isinstance(CONSORS, phase6.ConsorsFixture)
BUILT = [c for c in CONSORS.cases if c.pending and c.pending.startswith("phase 6 step 2b")]


def case_bytes(case: phase6.ConsorsCase) -> bytes:
    return draw(CONSORS.pages(case.id), salt="again" if case.input.fresh_file else "")


def _check(statement, balance_error, expect: phase6.ConsorsExpect) -> None:
    assert statement.format is StatementFormat.CARD_PDF
    assert statement.account_iban is None
    assert statement.account_ref == expect.account_ref
    assert statement.card_last4 == expect.card_last4
    assert statement.payment_iban == expect.payment_iban
    assert (statement.opening_balance, statement.closing_balance) == (
        expect.opening_balance,
        expect.closing_balance,
    )
    assert balance_error == expect.balance_error
    assert len(statement.lines) == expect.line_count
    assert sum(line.amount for line in statement.lines) == expect.total
    dates = [line.booking_date for line in statement.lines]
    assert (min(dates), max(dates)) == (expect.first_booking_on, expect.last_booking_on)
    assert statement.warnings == expect.warnings
    for pinned in expect.lines:
        assert statement.lines[pinned.index] == pinned.line, pinned.description


@pytest.mark.rule
@pytest.mark.parametrize("case", CONSORS.cases, ids=lambda c: c.id)
def test_consors_pages(case):
    if case.pending and not case.pending.startswith("phase 6 step 2b"):
        pytest.skip(case.pending)
    result = consors_pdf.parse_consors_pages(CONSORS.pages(case.id))
    _check(result.statement, result.balance_error, case.expect)


@pytest.mark.rule
@pytest.mark.parametrize("case", BUILT, ids=lambda c: c.id)
def test_consors_bytes_are_sniffed_and_parsed(case):
    data = case_bytes(case)
    assert card_pdf.looks_like(card_pdf.pdf_pages(data)) is False  # not the first issuer's
    assert detect_format(data, "application/pdf") is StatementFormat.CARD_PDF
    outcome = parse_statement(data, "application/pdf")
    _check(outcome.statement, outcome.balance_error, case.expect)


def test_every_case_is_built():
    assert len(BUILT) == len(CONSORS.cases)


def test_the_first_issuers_statement_still_goes_to_its_own_reader():
    case = next(c for c in CARD.cases if c.input.pages is not None)
    data = avarda_bytes(case)
    assert consors_pdf.looks_like(card_pdf.pdf_pages(data)) is False
    assert is_card_statement(card_pdf.pdf_pages(data))
    statement = parse_statement(data, "application/pdf").statement
    assert statement.account_ref == case.expect.account_ref
    assert statement.lines[0].bank_reference is not None  # read by card_pdf, Bew.ID kept


def test_any_other_pdf_is_still_refused():
    data = draw(["Kontoauszug zu Beispielkarte\nALTER SALDO VOM 05.05.26  -1,00\nGESAMTUMSÄTZE"])
    assert detect_format(data, "application/pdf") is StatementFormat.PDF
    with pytest.raises(RuleError) as caught:
        parse_statement(data, "application/pdf")
    assert caught.value.code == rules.UNKNOWN_FORMAT


def _plain() -> list[str]:
    return CONSORS.pages(BUILT[0].id)


def _refused(pages: list[str]) -> str:
    with pytest.raises(RuleError) as caught:
        consors_pdf.parse_consors_pages(pages)
    return caught.value.code


def test_no_part_of_the_card_number_but_its_last_four_is_kept():
    """D176 (2), (3): the visible first four digits never reach the parsed statement."""
    pages = _plain()
    first_four = next(m.group(1) for p in pages if (m := re.search(r"(\d{4}) XXXX XXXX", p)))
    statement = consors_pdf.parse_consors_pages(pages).statement
    assert first_four not in statement.model_dump_json()
    assert "XXXX" not in statement.model_dump_json()


def test_an_iban_of_another_account_number_is_the_files_error():
    pages = [
        p.replace("DE23 1000 0000 1234 5678 90", "DE76 1000 0000 1234 5678 09") for p in _plain()
    ]
    assert pages != _plain()
    assert _refused(pages) == rules.UNPARSABLE


def test_no_account_number_is_the_files_error():
    pages = [p.replace("Kontonummer:", "Konto:") for p in _plain()]
    assert _refused(pages) == rules.UNPARSABLE


def test_no_totals_is_the_files_error():
    """Without GESAMTUMSÄTZE the closing balance is unknown."""
    pages = [re.sub(r"^ *GESAMTUMSÄTZE .*$", "", p, flags=re.M) for p in _plain()]
    assert _refused(pages) == rules.UNPARSABLE


def test_no_opening_is_the_files_error():
    pages = [re.sub(r"^ *ALTER SALDO VOM .*$", "", p, flags=re.M) for p in _plain()]
    assert _refused(pages) == rules.UNPARSABLE


def test_a_row_below_the_totals_is_the_files_error():
    pages = [
        p.replace(
            "Für Umsatzsteuerzwecke",
            "      01.02.26    02.02.26                NACHZUEGLER                     -1,00\n"
            "Für Umsatzsteuerzwecke",
        )
        for p in _plain()
    ]
    assert _refused(pages) == rules.UNPARSABLE


def test_a_month_without_bookings_is_an_empty_statement():
    """No row and empty totals: statement.empty, as for the other formats."""
    pages = [re.sub(r"^ +(\d{2}\.\d{2}\.\d{2} +)+[A-Z].*$\n", "", p, flags=re.M) for p in _plain()]
    pages = [
        p.replace("-134,55", "-").replace(
            "GESAMTUMSÄTZE" + " " * 78 + "+312,75", "GESAMTUMSÄTZE" + " " * 84 + "+"
        )
        for p in pages
    ]
    assert "+312,75" not in pages[1] and "-134,55" not in pages[1]
    assert _refused(pages) == rules.EMPTY


def test_rows_the_reader_misses_are_the_files_error():
    """Totals with movements but no row read: a layout the reader does not know."""
    pages = [re.sub(r"^ +(\d{2}\.\d{2}\.\d{2} +)+[A-Z].*$\n", "", p, flags=re.M) for p in _plain()]
    assert _refused(pages) == rules.UNPARSABLE


def test_a_printed_new_balance_must_agree():
    """Not seen yet: a NEUER SALDO under the totals is checked against them."""
    good = [
        p.replace("Für Umsatzsteuerzwecke", "NEUER SALDO  -134,55\nFür Umsatzsteuerzwecke")
        for p in _plain()
    ]
    result = consors_pdf.parse_consors_pages(good)
    assert result.balance_error is None and result.statement.closing_balance == Decimal("-134.55")
    bad = [
        p.replace("Für Umsatzsteuerzwecke", "NEUER SALDO  -143,55\nFür Umsatzsteuerzwecke")
        for p in _plain()
    ]
    result = consors_pdf.parse_consors_pages(bad)
    assert result.balance_error == rules.REPORT_UNBALANCED
    assert result.statement.closing_balance == Decimal("-143.55")


def test_debit_and_credit_totals_are_checked_each():
    """Two totals are the debits and the credits: a misread that keeps the net is caught."""
    pages = [
        p.replace("-45,10", "-54,10").replace("-89,45", "-80,45") for p in _plain()
    ]  # the debits still sum to -134,55
    assert consors_pdf.parse_consors_pages(pages).balance_error is None
    # A debit and the payment each misread by 9,00: the lines still sum to the net total.
    both = [p.replace("-45,10", "-54,10").replace("+312,75", "+321,75", 1) for p in _plain()]
    lines = consors_pdf.parse_consors_pages(both).statement.lines
    assert sum(line.amount for line in lines) == Decimal("178.20")
    assert consors_pdf.parse_consors_pages(both).balance_error == rules.REPORT_UNBALANCED


def test_a_text_line_under_a_row_continues_its_text():
    """Not seen yet (assumed): a text-only line at the Verwendungszweck column continues the
    row above; a footer at the margin is not part of any row."""
    pages = [
        p.replace(
            "BEISPIEL MARKT MUSTERSTADT" + " " * 66 + "-45,10",
            "BEISPIEL MARKT MUSTERSTADT" + " " * 66 + "-45,10\n" + " " * 42 + "FILIALE 0815",
        )
        for p in _plain()
    ]
    assert pages != _plain()
    line = consors_pdf.parse_consors_pages(pages).statement.lines[0]
    assert line.counterparty_name == line.purpose == "BEISPIEL MARKT MUSTERSTADT FILIALE 0815"


def _payment_tx(**overrides) -> MatchTransaction:
    line = consors_pdf.parse_consors_pages(_plain()).statement.lines[1]
    values = {
        "id": uuid4(),
        "booking_date": line.booking_date,
        "value_date": line.value_date,
        "amount": line.amount,
        "counterparty_name": line.counterparty_name,
        "purpose": line.purpose,
        "booking_text": line.booking_text,
    } | overrides
    return MatchTransaction(**values)


def test_the_payment_is_booked_by_code_as_the_cards_transfer_leg():
    found = card_rules.payment_set(_payment_tx())
    assert found is not None
    assert found.method is AllocationMethod.MATCHER_CARD and found.confidence == 1
    [part] = found.allocations
    assert part.category is AllocationCategory.INTERNAL_TRANSFER_IN


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount": "-312.75"},  # a debit is never the payment received
        {"counterparty_name": "EINGEGANGENE ZAHLUNG"},  # a merchant: a purchase
        {"booking_text": None},
    ],
)
def test_other_lines_are_not_the_payment(overrides):
    assert card_rules.payment_set(_payment_tx(**overrides)) is None
