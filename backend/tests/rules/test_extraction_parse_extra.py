"""Edge cases of rules.extraction_parse beyond the fixture (which is the contract)."""

import pytest

from hausbuch.rules.extraction_parse import extraction_kind, normalize_value, parse_lines
from hausbuch.schemas.ai import CitedTextBlock, PageCitation
from hausbuch.schemas.enums import DocumentSubtype, DocumentType, ExtractionKind


@pytest.mark.parametrize(
    ("raw", "kind", "expected"),
    [
        ("289.000 EUR", "money", "289000.00"),
        ("1234,5 €", "money", "1234.50"),
        ("-412,10 €", "money", "-412.10"),
        ("1234.56", "money", "1234.56"),
        ("2 %", "rate", "0.02"),
        ("3,650 % p.a.", "rate", "0.0365"),
        ("1.3.2027", "date", "2027-03-01"),
        ("12. März 2026", "date", "2026-03-12"),
        ("2026-03-12", "date", "2026-03-12"),
        ("Baujahr ca. 1994", "year", 1994),
        ("85/1.000stel", "fraction", {"numerator": 85, "denominator": 1000}),
        ("IBAN: de89 3704 0044 0532 0130 00", "iban", "DE89370400440532013000"),
        ("  Notar   Dr. Muster ", "text", "Notar Dr. Muster"),
        ("a; ; b", "text_list", ["a", "b"]),
    ],
)
def test_normalize_value(raw, kind, expected):
    assert normalize_value(raw, kind) == expected


@pytest.mark.parametrize(
    ("raw", "kind"),
    [
        ("1,234 €", "money"),  # three decimal places
        ("zweihundert Euro", "money"),
        ("120 %", "rate"),
        ("31.02.2026", "date"),
        ("1700", "year"),
        ("1001/1000", "fraction"),
        ("DE00 3704 0044 0532 0130 00", "iban"),
    ],
)
def test_normalize_value_rejects(raw, kind):
    with pytest.raises(ValueError):
        normalize_value(raw, kind)


def test_invalid_value_keeps_raw_text_and_is_low():
    blocks = [
        CitedTextBlock(text="purchase_price: "),
        CitedTextBlock(
            text="zweihundert  Euro",
            citations=[
                PageCitation(cited_text="zweihundert Euro", start_page_number=2, end_page_number=3)
            ],
        ),
    ]
    got = parse_lines(blocks, ExtractionKind.PURCHASE_CONTRACT)["purchase_price"]
    assert got.value == "zweihundert Euro"
    assert got.checks == ["cited"]
    assert got.confidence == "low"


def test_value_without_digits_is_never_in_quote():
    blocks = [
        CitedTextBlock(
            text="build_year: neu",
            citations=[PageCitation(cited_text="neu", start_page_number=1, end_page_number=2)],
        )
    ]
    got = parse_lines(blocks, ExtractionKind.PURCHASE_CONTRACT)["build_year"]
    assert got.checks == ["cited"]


@pytest.mark.parametrize(
    ("type_", "subtype", "kind"),
    [
        (
            DocumentType.CONTRACT,
            DocumentSubtype.PURCHASE_CONTRACT,
            ExtractionKind.PURCHASE_CONTRACT,
        ),
        (DocumentType.CONTRACT, DocumentSubtype.LOAN_CONTRACT, ExtractionKind.LOAN_CONTRACT),
        (DocumentType.CONTRACT, None, None),
        (DocumentType.CONTRACT, DocumentSubtype.APPRAISAL, None),
        (DocumentType.INVOICE, DocumentSubtype.COST_BASIS_EVIDENCE, ExtractionKind.INVOICE),
        (DocumentType.BESCHEID, DocumentSubtype.COST_BASIS_EVIDENCE, ExtractionKind.BESCHEID),
        (
            DocumentType.BESCHEID,
            DocumentSubtype.GRUNDSTEUERBESCHEID,
            ExtractionKind.GRUNDSTEUERBESCHEID,
        ),
        (DocumentType.WIRTSCHAFTSPLAN, None, ExtractionKind.WIRTSCHAFTSPLAN),
        (DocumentType.LETTER, DocumentSubtype.HANDOVER_PROTOCOL, ExtractionKind.HANDOVER_PROTOCOL),
        (DocumentType.LETTER, None, None),
        (DocumentType.OTHER, None, None),
    ],
)
def test_extraction_kind(type_, subtype, kind):
    assert extraction_kind(type_, subtype) == kind
