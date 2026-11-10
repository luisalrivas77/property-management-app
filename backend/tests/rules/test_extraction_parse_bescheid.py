"""The Einkommensteuerbescheid in rules.extraction_parse (docs/phases/phase-4b.md D129, §8.4):
its routing from the classifier's subtype, and the identifier scrub of every line and quote,
D103's patterns plus the Steuernummer formats.

All names and numbers are invented; the identifiers have the printed shapes only."""

import pytest

from hausbuch.rules.extraction_parse import (
    SCRUBBED,
    SCRUBBED_KINDS,
    extraction_kind,
    parse_lines,
    scrub_identifiers,
)
from hausbuch.schemas.ai import CitedTextBlock, PageCitation
from hausbuch.schemas.enums import DocumentSubtype, DocumentType, ExtractionKind

KIND = ExtractionKind.EINKOMMENSTEUERBESCHEID
ID_NR = "12 345 678 903"
STEUERNUMMER = "143/123/45678"
ELSTER_NUMBER = "2143012345678"  # the 13-digit ELSTER form


def test_the_subtype_gets_its_field_set_and_the_scrub():
    assert extraction_kind(DocumentType.BESCHEID, DocumentSubtype.EINKOMMENSTEUERBESCHEID) is KIND
    assert KIND in SCRUBBED_KINDS
    # Another Bescheid keeps the generic field set and is not scrubbed.
    assert extraction_kind(DocumentType.BESCHEID, None) is ExtractionKind.BESCHEID
    assert ExtractionKind.BESCHEID not in SCRUBBED_KINDS


@pytest.mark.parametrize(
    ("text", "gone"),
    [
        (f"Steuernummer {STEUERNUMMER}", [STEUERNUMMER, "45678"]),
        (f"Steuernummer: {STEUERNUMMER} Datum 12.05.2027", [STEUERNUMMER]),
        (f"St.-Nr. {STEUERNUMMER}", [STEUERNUMMER]),
        ("Steuernr. 21/815/08150", ["21/815/08150"]),
        (f"Finanzamt Musterstadt {STEUERNUMMER}", [STEUERNUMMER]),
        ("Steuernummer 12/345/67890", ["12/345/67890"]),
        (f"Steuernummer {ELSTER_NUMBER}", [ELSTER_NUMBER]),
        (f"ELSTER {ELSTER_NUMBER} Bescheid", [ELSTER_NUMBER]),
        (f"Identifikationsnummer Ehemann {ID_NR}", ["345 678"]),
        (f"IdNr. Ehefrau {ID_NR.replace(' ', '')}", ["12345678903"]),
        (
            "Herrn und Frau Max und Erika Mustermann, Musterweg 9, 60311 Frankfurt am Main",
            ["Musterweg", "60311", "Frankfurt"],
        ),
        ("Erstattung auf DE89 3704 0044 0532 0130 00", ["3704", "0532"]),
    ],
)
def test_the_scrub_removes_the_bescheids_identifiers(text, gone):
    scrubbed = scrub_identifiers(text, reference_year=2027)
    for fragment in gone:
        assert fragment not in scrubbed, (text, scrubbed)
    assert SCRUBBED in scrubbed


@pytest.mark.parametrize(
    "text",
    [
        "Bescheid für 2026 über Einkommensteuer, Solidaritätszuschlag und Kirchensteuer",
        "Summe der Einkünfte 92.500",
        "Gesamtbetrag der Einkünfte 91.200",
        "Einkommen 86.400",
        "zu versteuerndes Einkommen 84.300 €",
        "Zusammenveranlagung",
        "Datum 12.05.2027",
        "Max und Erika Mustermann",
    ],
)
def test_the_scrub_keeps_the_figures_dates_and_names(text):
    assert scrub_identifiers(text, reference_year=2027) == text


def _cited(line: str, page: int = 1) -> PageCitation:
    return PageCitation(cited_text=line, start_page_number=page, end_page_number=page + 1)


def test_a_quote_beside_the_steuernummer_is_scrubbed_and_the_zve_stays_high():
    line = f"Steuernummer {STEUERNUMMER}  IdNr. {ID_NR}\nzu versteuerndes Einkommen 84.300 €"
    blocks = [
        CitedTextBlock(text="taxable_income: "),
        CitedTextBlock(text="84.300 €", citations=[_cited(line, page=2)]),
        CitedTextBlock(text="\ntax_year: "),
        CitedTextBlock(text="2026", citations=[_cited("Bescheid für 2026 über Einkommensteuer")]),
        CitedTextBlock(text="\naddressee: "),
        CitedTextBlock(
            text="Max und Erika Mustermann",
            citations=[_cited("Max und Erika Mustermann, Musterweg 9, 60311 Frankfurt")],
        ),
    ]
    values = parse_lines(blocks, KIND, reference_year=2027)
    zve = values["taxable_income"]
    assert zve.value == "84300.00" and zve.confidence == "high"
    assert zve.source is not None and zve.source.page == 2
    assert STEUERNUMMER not in zve.source.quote and "345 678" not in zve.source.quote
    assert "84.300" in zve.source.quote
    assert values["tax_year"].value == 2026
    addressee = values["addressee"]
    assert addressee.value == "Max und Erika Mustermann" and addressee.confidence == "high"
    assert addressee.source is not None
    assert "Musterweg" not in addressee.source.quote and "60311" not in addressee.source.quote


def test_a_steuernummer_the_model_wrote_into_a_value_is_scrubbed():
    blocks = [CitedTextBlock(text=f"addressee: Max Mustermann ({STEUERNUMMER})")]
    value = parse_lines(blocks, KIND, reference_year=2027)["addressee"].value
    assert STEUERNUMMER not in value and "Max Mustermann" in value


@pytest.mark.parametrize(
    "written", ["./. 5.000", "./.5.000 €", "- 5.000,00", "−5.000", "5.000,00-"]
)
def test_a_negative_zve_written_apart_from_its_digits_keeps_its_sign(written):
    # Review of PR #128: a Bescheid prints a negative zvE as "./. 5.000"; the parser took
    # only a sign glued to the first digit and stored +5000.00 at high confidence.
    blocks = [
        CitedTextBlock(text="taxable_income: "),
        CitedTextBlock(text=written, citations=[_cited(f"zu versteuerndes Einkommen {written}")]),
    ]
    zve = parse_lines(blocks, KIND, reference_year=2027)["taxable_income"]
    assert zve.value == "-5000.00" and zve.confidence == "high"


@pytest.mark.parametrize("written", ["84.300 €", "+84.300", "84.300 - laut Anlage"])
def test_a_dash_elsewhere_never_makes_a_figure_negative(written):
    blocks = [
        CitedTextBlock(text="taxable_income: "),
        CitedTextBlock(text=written, citations=[_cited(f"zu versteuerndes Einkommen {written}")]),
    ]
    zve = parse_lines(blocks, KIND, reference_year=2027)["taxable_income"]
    assert zve.value == "84300.00"
