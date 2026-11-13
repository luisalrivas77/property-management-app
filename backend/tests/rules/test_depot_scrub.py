"""The depot documents' scrub and the code typing of their kinds (phase 6 step 4, D168).

Every text here is invented in the shape of the depot bank's documents (value before label,
holders as „Name, Vorname oder Vorname Name“, an address block under „Herrn“); no number, name
or ISIN is a real one."""

import pytest

from hausbuch.rules import depot
from hausbuch.rules.extraction_parse import (
    DEPOT_KINDS,
    PRIVATE_SUBTYPES,
    SCRUBBED,
    SCRUBBED_KINDS,
    scrub_depot,
    scrubber,
)
from hausbuch.schemas.enums import DocumentSubtype as S
from hausbuch.schemas.enums import DocumentType as T

LETTER = "\n".join(
    [
        "Quirin Privatbank AG · Musterallee 1 · 10000 Musterstadt",
        "Herrn",
        "Anton Beispiel",
        "Probeweg 12",
        "12345 Neustadt",
        "Wertpapierabrechnung",
        "1234567890Depot",
        "87654321Kunde",
        "Beispiel, Anton oder",
        "Berta Beispiel",
        "Sehr geehrter Herr Beispiel,",
        "IE00BEISPL01ISIN",
        "700000010Referenz-Nr",
        "über Ihr Konto 1122334455 EUR gebucht.",
        "IBAN DE12 3456 7890 1234 5678 90",
        "Steuer-IdNr. 12345678901",
        "Stück 12,345678 zu 101,23 EUR am 02.01.2026",
    ]
)


def test_the_holders_numbers_and_address_are_scrubbed():
    out = scrub_depot(LETTER)
    for leaked in (
        "Anton",
        "Berta",
        "Beispiel",
        "Probeweg",
        "12345",
        "Neustadt",
        "1234567890",
        "87654321",
        "1122334455",
        "DE12",
        "12345678901",
    ):
        assert leaked not in out, leaked


def test_the_isin_reference_amounts_units_and_dates_stay():
    out = scrub_depot(LETTER)
    for kept in (
        "IE00BEISPL01ISIN",
        "700000010Referenz-Nr",
        "12,345678",
        "101,23",
        "02.01.2026",
        "Wertpapierabrechnung",
        "über Ihr Konto",
        f"{SCRUBBED}Depot",
        f"{SCRUBBED}Kunde",
    ):
        assert kept in out, kept


def test_the_scrub_keeps_lines_and_is_idempotent():
    out = scrub_depot(LETTER)
    assert out.count("\n") == LETTER.count("\n")
    assert scrub_depot(out) == out


def test_a_transfer_note_keeps_its_head_only():
    text = "\n".join(
        [
            "Ausbuchung von Wertpapieren an 87654321 Beispiel, Anton",
            "Berta Beispiel",
            "Depot 1234567890",
            "Hinweistext",
            "IE00BEISPL01ISIN",
        ]
    )
    out = scrub_depot(text).split("\n")
    assert out[0] == f"Ausbuchung von Wertpapieren an {SCRUBBED}"
    assert out[1] == out[2] == SCRUBBED
    assert out[3:] == ["Hinweistext", "IE00BEISPL01ISIN"]


def test_every_depot_kind_is_scrubbed_with_the_depot_scrub():
    assert DEPOT_KINDS <= SCRUBBED_KINDS
    for kind in DEPOT_KINDS:
        assert scrubber(kind, reference_year=2026) is scrub_depot


@pytest.mark.parametrize(
    ("text", "subtype", "type_"),
    [
        ("Einbuchung von Wertpapieren von …\nHinweistext", S.SECURITIES_TRANSFER, T.LETTER),
        ("Ausbuchung von Wertpapieren an …", S.SECURITIES_TRANSFER, T.LETTER),
        ("Detailreport\nWertpapierumsätze\nWertpapierabrechnung", S.DEPOT_REPORT, T.OTHER),
        ("Erträgnisabrechnung\nAusschüttung", S.DISTRIBUTION_NOTE, T.LETTER),
        ("Wertpapierabrechnung\nKauf", S.TRADE_CONFIRMATION, T.LETTER),
    ],
)
def test_the_four_kinds_are_typed_by_their_text(text, subtype, type_):
    assert depot.document_subtype(text) is subtype
    assert depot.DOCUMENT_TYPES[subtype] is type_
    assert depot.is_depot_document(text)
    assert subtype in PRIVATE_SUBTYPES


@pytest.mark.parametrize(
    "text",
    [
        "Quartalsbericht\nquirion Vermögensverwaltung",
        "Jahressteuerbescheinigung\nQuirin Privatbank AG",
        "Kosteninformation\n1234567890 Depot",
        "Depot-Nr. 1234567890",
        # A cost statement refers to the trade notes in its prose; it is none of them.
        "Kostenübersicht\nquirion\nDie Kosten je Kauf können Sie der entsprechenden "
        "Wertpapierabrechnung entnehmen.",
    ],
)
def test_any_other_depot_document_is_recognised(text):
    assert depot.document_subtype(text) is None
    assert depot.is_depot_document(text)


@pytest.mark.parametrize(
    "text",
    ["Rechnung Nr. 42\nHandwerker GmbH", "Mietvertrag\nWohnung WE 3", "Kontoauszug Sparkasse", ""],
)
def test_a_document_of_another_kind_is_not_a_depot_document(text):
    assert not depot.is_depot_document(text)


def test_the_private_subtypes_are_the_depots_documents():
    assert {
        S.TRADE_CONFIRMATION,
        S.DISTRIBUTION_NOTE,
        S.DEPOT_REPORT,
        S.SECURITIES_TRANSFER,
        S.DEPOT_STATEMENT,
        S.CAPITAL_INCOME_TAX_CERTIFICATE,
    } == PRIVATE_SUBTYPES
