"""A Wohnflächenberechnung's dwellings (docs/phases/phase-2.md D139): the general reading asks
them of `building_records` only, keeps them with their page and quote through the general
scrub, and rules.extraction_parse.dwellings reads each row. The pick of the household's row is
in tests/fixtures/phase2/prefill.yaml. All values are invented."""

from hausbuch.rules.extraction_parse import (
    Dwelling,
    PropertyAddress,
    dwellings,
    lone_dwelling,
    parse_lines,
)
from hausbuch.schemas.ai import CitedTextBlock, PageCitation
from hausbuch.schemas.enums import DocumentSubtype, ExtractionKind

GENERAL = ExtractionKind.GENERAL
PROPERTIES = (PropertyAddress("Lindenstraße", "7", "10115"),)
PAGE = "\n".join(
    [
        "Wohnflächenberechnung nach WoFlV",
        "Lindenstraße 7, 10115 Berlin",
        "WE 1  EG links     48,20 m²   62/1000",
        "WE 2  1. OG links  68,40 m²   85/1000",
        "Summe             116,60 m²",
    ]
)
ROWS = "WE 1 | 48,20 m² | 62/1000 | EG links; WE 2 | 68,40 m² | 85/1000 | 1. OG links"


def _answer(**lines: str) -> list[CitedTextBlock]:
    blocks: list[CitedTextBlock] = []
    for index, (key, value) in enumerate(lines.items()):
        cited = PageCitation(cited_text=PAGE, start_page_number=1, end_page_number=2)
        blocks.append(CitedTextBlock(text=("\n" if index else "") + f"{key}: "))
        blocks.append(CitedTextBlock(text=value, citations=[cited]))
    return blocks


def _parse(subtype: DocumentSubtype):
    return parse_lines(
        _answer(title="Wohnflächenberechnung", dwellings=ROWS),
        GENERAL,
        subtype=subtype,
        properties=PROPERTIES,
    )


def test_a_wohnflaechenberechnung_keeps_its_dwellings_with_page_and_quote():
    read = _parse(DocumentSubtype.BUILDING_RECORDS)["dwellings"]
    assert read.value == [
        "WE 1 | 48,20 m² | 62/1000 | EG links",
        "WE 2 | 68,40 m² | 85/1000 | 1. OG links",
    ]
    assert read.source is not None and read.source.page == 1
    assert "68,40 m²" in read.source.quote
    assert "Lindenstraße 7" in read.source.quote  # the property's own address is kept
    assert "validates" in read.checks


def test_another_subtype_never_keeps_dwellings():
    values = _parse(DocumentSubtype.ENERGY_CERTIFICATE)
    assert values["dwellings"].value is None
    assert values["dwellings"].source is None


def test_each_row_is_read_and_never_summed():
    rows = dwellings(
        [
            "WE 1 | 48,20 m² | 62/1000 | EG links",
            "Wohnung Nr. 3 | 58,8 | unbekannt | unbekannt",
            "WE 4 | 72 m² | 120/1.000stel |",
            "WE 5 | 61,40 m² | keine Angabe | 3. OG",
        ]
    )
    assert rows == [
        Dwelling("WE 1", "48.20", {"numerator": 62, "denominator": 1000}, "EG links"),
        Dwelling("Wohnung Nr. 3", "58.80", None, None),
        Dwelling("WE 4", "72.00", {"numerator": 120, "denominator": 1000}, None),
        Dwelling("WE 5", "61.40", None, "3. OG"),
    ]


def test_a_row_without_a_label_or_a_readable_area_is_left_out():
    assert dwellings(["| 48,20 m² | 62/1000 | EG", "WE 2 | unbekannt | 85/1000 | 1. OG"]) == []
    assert dwellings(["WE 2 | 0 m² | 85/1000 | 1. OG", "WE 3 | 61,405 m² | | "]) == []
    assert dwellings("WE 1 | 48,20 m²") == []
    assert dwellings(None) == []


def test_a_list_of_rooms_is_no_dwelling():
    # The household's units are rooms ("Zimmer 1"): a Grundriss's room list must never match
    # one by its label (D139 guard).
    rows = dwellings(
        [
            "Zimmer 1 | 14,20 m² | |",
            "Küche | 8,10 m² | |",
            "Bad/WC | 4,60 m² | |",
            "flur | 6,30 m² | |",
        ]
    )
    assert rows == []


def test_a_total_beside_a_flat_is_left_out():
    rows = dwellings(["WE 1 | 48,20 m² | 62/1000 | EG", "Summe: | 48,20 m² | |"])
    assert [row.label for row in rows] == ["WE 1"]


def test_a_lone_total_is_the_one_flats_wohnflaeche():
    rows = dwellings(["Zimmer 1 | 14,20 m² | |", "Summe | 61,40 m² | |"])
    assert rows == [Dwelling("Summe", "61.40", None, None)]


def test_one_wohnung_beside_rooms_and_totals_is_the_lone_dwelling():
    assert lone_dwelling(["WE 1 | 48,20 m² | 62/1000 | EG"])
    assert lone_dwelling(["Zimmer 1 | 14,20 m² | |", "Summe | 61,40 m² | |"])
    assert lone_dwelling(["WE 1 | 48,20 m² | |", "Summe | 48,20 m² | |"])
    # Two flats, or an unreadable row that may be another flat: not alone.
    assert not lone_dwelling(["WE 1 | 48,20 m² | |", "WE 2 | 68,40 m² | |"])
    assert not lone_dwelling(["WE 1 | 48,20 m² | |", "WE 2 | unbekannt | |"])
    assert not lone_dwelling(["Zimmer 1 | 14,20 m² | |"])
    assert not lone_dwelling(None)
