"""A Restnutzungsdauer-Gutachten (docs/phases/phase-2.md D147): an appraisal is read by the
general reading whatever its type, in the property group, and asked its remaining useful life
and Stichtag, kept with their page and quote through the general scrub. The years the pre-fill
derives are in tests/fixtures/phase2/prefill.yaml. All values are invented."""

from datetime import date
from decimal import Decimal

import pytest

from hausbuch.rules import prefill
from hausbuch.rules.extraction_parse import PropertyAddress, extraction_kind, parse_lines
from hausbuch.schemas.ai import CitedTextBlock, PageCitation
from hausbuch.schemas.enums import DocumentSubtype, DocumentType, ExtractionKind
from hausbuch.schemas.extraction import GENERAL_LINK, asked_fields

GENERAL = ExtractionKind.GENERAL
APPRAISAL = DocumentSubtype.APPRAISAL
PROPERTIES = (PropertyAddress("Lindenstraße", "12", "10115"),)
PAGE = "\n".join(
    [
        "Gutachten zum Nachweis einer kürzeren Nutzungsdauer (§ 7 Abs. 4 S. 2 EStG)",
        "Bewertungsobjekt: Lindenstraße 12, 10115 Berlin",
        "Wertermittlungsstichtag: 15.03.2025",
        "Gesamtnutzungsdauer: 80 Jahre, Alter: 58 Jahre",
        "Restnutzungsdauer: 21,5 Jahre",
    ]
)


def _answer(**lines: str) -> list[CitedTextBlock]:
    blocks: list[CitedTextBlock] = []
    for index, (key, value) in enumerate(lines.items()):
        cited = PageCitation(cited_text=PAGE, start_page_number=1, end_page_number=2)
        blocks.append(CitedTextBlock(text=("\n" if index else "") + f"{key}: "))
        blocks.append(CitedTextBlock(text=value, citations=[cited]))
    return blocks


def _parse(subtype: DocumentSubtype, life: str = "21,5 Jahre", stichtag: str = "15.03.2025"):
    return parse_lines(
        _answer(
            title="Gutachten Restnutzungsdauer",
            street="Lindenstraße",
            house_number="12",
            postal_code="10115",
            remaining_useful_life=life,
            valuation_date=stichtag,
        ),
        GENERAL,
        subtype=subtype,
        properties=PROPERTIES,
    )


@pytest.mark.parametrize("type_", list(DocumentType))
def test_an_appraisal_is_read_by_the_general_reading_whatever_its_type(type_):
    assert extraction_kind(type_, APPRAISAL) is GENERAL


def test_an_appraisal_is_in_the_property_group_and_asked_its_life_and_stichtag():
    assert GENERAL_LINK[APPRAISAL] == "property"
    assert set(asked_fields(GENERAL, APPRAISAL)) == {
        "title",
        "document_date",
        "street",
        "house_number",
        "postal_code",
        "remaining_useful_life",
        "valuation_date",
    }


@pytest.mark.parametrize(
    "subtype", [None, DocumentSubtype.BUILDING_RECORDS, DocumentSubtype.ENERGY_CERTIFICATE]
)
def test_no_other_request_asks_the_life(subtype):
    asked = asked_fields(GENERAL, subtype)
    assert "remaining_useful_life" not in asked and "valuation_date" not in asked


def test_the_life_and_the_stichtag_are_kept_with_page_and_quote():
    values = _parse(APPRAISAL)
    life, stichtag = values["remaining_useful_life"], values["valuation_date"]
    assert life.value == "21.5"
    assert stichtag.value == "2025-03-15"
    for read in (life, stichtag):
        assert read.source is not None and read.source.page == 1
        assert read.checks == ["cited", "value_in_quote", "validates"]
    assert "Restnutzungsdauer: 21,5 Jahre" in life.source.quote
    assert "Lindenstraße 12" in life.source.quote  # the property's own address is kept
    assert values["street"].value == "Lindenstraße"


def test_a_whole_life_is_read_as_a_number():
    assert _parse(APPRAISAL, life="24 Jahre")["remaining_useful_life"].value == "24"


def test_a_life_that_is_no_number_does_not_validate():
    read = _parse(APPRAISAL, life="rund zwei Jahrzehnte")["remaining_useful_life"]
    assert "validates" not in read.checks
    assert read.confidence == "low"


def test_another_subtype_never_keeps_the_life():
    values = _parse(DocumentSubtype.ENERGY_CERTIFICATE)
    for name in ("remaining_useful_life", "valuation_date"):
        assert values[name].value is None and values[name].source is None


@pytest.mark.parametrize(
    ("start", "end", "months"),
    [
        (
            date(2024, 5, 30),
            date(2026, 10, 2),
            28,
        ),  # 2 years and 4 months: the 2nd is before the 30th
        (date(2025, 3, 15), date(2026, 5, 1), 13),
        (date(2025, 3, 1), date(2026, 5, 1), 14),  # the day reached: the month counts
        (date(2026, 4, 30), date(2026, 5, 1), 0),
    ],
)
def test_whole_months_count_a_month_once_its_day_is_reached(start, end, months):
    assert prefill._whole_months(start, end) == months


def test_the_example_of_the_spec_gives_nineteen_years():
    """D147's own example: 21 years at 30.05.2024, handover 02.10.2026: 252 - 28 = 224 months,
    rounded up to 19 years."""
    months = Decimal(21) * 12 - prefill._whole_months(date(2024, 5, 30), date(2026, 10, 2))
    assert months == 224
    assert prefill._span_de(28) == "2 Jahre und 4 Monate"


@pytest.mark.parametrize(
    ("months", "text"),
    [
        (28, "2 Jahre und 4 Monate"),
        (12, "1 Jahr"),
        (13, "1 Jahr und 1 Monat"),
        (5, "5 Monate"),
        (0, "0 Monate"),
    ],
)
def test_spans_read_in_german(months, text):
    assert prefill._span_de(months) == text
