"""The credit card's PDF statement read by code (phase 6 step 2b, D176 (3); fixture
tests/fixtures/phase6/card_pdf.yaml, its parse half; the import half is in
tests/db/test_services_card_import.py).

Each case's page text is drawn into a PDF (reportlab, one physical line per text line), so the
bytes path through pypdf and the format sniff are replayed too, not only the text reader.
"""

import io
import re
from uuid import uuid4

import fixture_models_phase6 as phase6
import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from hausbuch.rules import statements as rules
from hausbuch.rules.errors import RuleError
from hausbuch.rules.household import card as card_rules
from hausbuch.schemas.allocation import AllocationCategory, AllocationMethod, MatchTransaction
from hausbuch.schemas.bank import StatementFormat
from hausbuch.services.bank import card_pdf, detect_format, parse_statement

CARD = phase6.load("card_pdf")
assert isinstance(CARD, phase6.CardFixture)
BUILT = [c for c in CARD.cases if c.pending and c.pending.startswith("phase 6 step 2b")]


def draw(pages: list[str], *, salt: str = "") -> bytes:
    """The pages as a PDF whose text layer pypdf gives back line by line. `salt` changes the
    bytes (a title in the metadata), not the text: the same statement downloaded again."""
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4, invariant=1)
    if salt:
        pdf.setTitle(salt)
    for page in pages:
        y = 800
        for line in page.splitlines():
            pdf.setFont("Helvetica", 8)
            pdf.drawString(30, y, line)
            y -= 11
        pdf.showPage()
    pdf.save()
    return buf.getvalue()


def case_bytes(case: phase6.CardCase) -> bytes:
    return draw(CARD.pages(case.id), salt="again" if case.input.fresh_file else "")


def _check(statement, balance_error, expect: phase6.CardExpect) -> None:
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
@pytest.mark.parametrize("case", CARD.cases, ids=lambda c: c.id)
def test_card_pdf_pages(case):
    if case.pending and not case.pending.startswith("phase 6 step 2b"):
        pytest.skip(case.pending)
    result = card_pdf.parse_card_pages(CARD.pages(case.id))
    _check(result.statement, result.balance_error, case.expect)


@pytest.mark.rule
@pytest.mark.parametrize("case", BUILT, ids=lambda c: c.id)
def test_card_pdf_bytes_are_sniffed_and_parsed(case):
    data = case_bytes(case)
    assert detect_format(data, "application/pdf") is StatementFormat.CARD_PDF
    outcome = parse_statement(data, "application/pdf")
    _check(outcome.statement, outcome.balance_error, case.expect)


def test_no_part_of_the_card_number_but_its_last_four_is_kept():
    """D176 (2), (3): the first six digits never reach the parsed statement."""
    pages = CARD.pages(BUILT[0].id)
    first_six = next(m.group(1) for p in pages if (m := re.search(r"(\d{6})\*{6}\d{4}", p)))
    statement = card_pdf.parse_card_pages(pages).statement
    # The invented customer number starts with the same digits, so it is left out here.
    assert first_six not in statement.model_dump_json(exclude={"account_ref"})
    assert "*" not in statement.model_dump_json()


def test_any_other_pdf_is_still_refused():
    data = draw(["Kontoauszug Nr. 1\nAlter Kontostand 100.00\nNeuer Kontostand 90.00"])
    assert detect_format(data, "application/pdf") is StatementFormat.PDF
    with pytest.raises(RuleError) as caught:
        parse_statement(data, "application/pdf")
    assert caught.value.code == rules.UNKNOWN_FORMAT


def test_a_row_without_its_amount_is_the_files_error():
    pages = CARD.pages(BUILT[0].id)
    broken = [re.sub(r" -?[\d ]+\.\d{2} D$", "", p, count=1, flags=re.M) for p in pages]
    assert broken != pages
    with pytest.raises(RuleError) as caught:
        card_pdf.parse_card_pages(broken)
    assert caught.value.code == rules.UNPARSABLE


def test_a_statement_number_of_another_customer_is_the_files_error():
    pages = CARD.pages(BUILT[0].id)
    broken = [re.sub(r"^Nummer: \d+-", "Nummer: 1111111-", p, flags=re.M) for p in pages]
    assert broken != pages
    with pytest.raises(RuleError) as caught:
        card_pdf.parse_card_pages(broken)
    assert caught.value.code == rules.UNPARSABLE


def _tx(**overrides) -> MatchTransaction:
    case = next(c for c in BUILT if c.import_.booked)
    line = card_pdf.parse_card_pages(CARD.pages(case.id)).statement.lines[
        case.import_.booked[0].index
    ]
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


def test_an_interest_line_is_booked_by_code():
    found = card_rules.interest_fee_set(_tx())
    assert found is not None
    assert found.method is AllocationMethod.MATCHER_CARD and found.confidence == 1
    [part] = found.allocations
    assert part.category is AllocationCategory.HH_INTEREST_FEES


@pytest.mark.parametrize(
    "overrides",
    [
        {"counterparty_name": "GEBÜHRENFREI SHOP"},  # a merchant: a purchase, whatever its name
        {"amount": "9.84"},  # a credit (a refunded fee) is never booked by code
        {"booking_text": "Kauf", "purpose": "BEISPIEL MARKT"},
    ],
)
def test_other_lines_are_not_interest(overrides):
    assert card_rules.interest_fee_set(_tx(**overrides)) is None
