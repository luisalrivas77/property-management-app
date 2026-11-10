"""The general reading's scrub in rules.extraction_parse (docs/phases/phase-2.md D131, decided
10 Nov 2026): every line and quote of a `general` or `management_contract` reading loses the
identifiers of D103/D129 and every address but the property's it read, while the values it
links by (the property's address, the lender, the contract number) survive.

All names, numbers and addresses are invented; the identifiers have the printed shapes only."""

import pytest

from hausbuch.rules.extraction_parse import (
    GENERAL_SCRUBBED_KINDS,
    SCRUBBED,
    SCRUBBED_KINDS,
    LinkValues,
    parse_lines,
    scrub_general,
    scrub_general_answer,
    scrub_identifiers,
)
from hausbuch.schemas.ai import CitedTextBlock, PageCitation
from hausbuch.schemas.enums import DocumentSubtype, ExtractionKind
from hausbuch.schemas.extraction import asked_fields

GENERAL = ExtractionKind.GENERAL
STEUERNUMMER = "045/815/08154"
ELSTER_NUMBER = "2181508150123"
ID_NR = "12 345 678 903"
SV_NR = "65 140388 M 00 3"
IBAN = "DE89 3704 0044 0532 0130 00"
BIRTH = "14.03.1988"
HOME = "Beispielallee 4"
HOME_TOWN = "34117 Kassel"
FLAT = "Lindenstraße 7"
FLAT_TOWN = "10115 Berlin"


def _cited(text: str) -> PageCitation:
    return PageCitation(cited_text=text, start_page_number=1, end_page_number=2)


def _answer(page: str, **lines: str) -> list[CitedTextBlock]:
    """An answer whose every value cites the whole page, as the model does."""
    blocks: list[CitedTextBlock] = []
    for index, (key, value) in enumerate(lines.items()):
        blocks.append(CitedTextBlock(text=("\n" if index else "") + f"{key}: "))
        blocks.append(CitedTextBlock(text=value, citations=[_cited(page)]))
    return blocks


def _stored(values) -> str:
    return " ".join(f"{v.value} {v.source.quote if v.source else ''}" for v in values.values())


def test_the_two_kinds_are_scrubbed_and_the_owners_kinds_keep_their_scrub():
    assert {ExtractionKind.GENERAL, ExtractionKind.MANAGEMENT_CONTRACT} == GENERAL_SCRUBBED_KINDS
    assert not GENERAL_SCRUBBED_KINDS & SCRUBBED_KINDS


PREPAYMENT_PAGE = "\n".join(
    [
        "Finanzamt Musterstadt",
        f"Steuernummer {STEUERNUMMER}",
        f"ELSTER-Steuernummer {ELSTER_NUMBER}",
        "Datum 20.06.2027",
        "Jonas und Lea Beispielmann",
        HOME,
        HOME_TOWN,
        f"Identifikationsnummer {ID_NR}, Versicherungsnummer {SV_NR}",
        f"Geburtsdatum {BIRTH}",
        f"Erstattungen auf {IBAN}",
        "Vorauszahlungen 10.09.2027 1.200,00 EUR",
    ]
)


def test_a_vorauszahlungsbescheid_keeps_no_identifier_or_home_address():
    values = parse_lines(
        _answer(
            PREPAYMENT_PAGE,
            title=f"Vorauszahlungsbescheid {STEUERNUMMER}",
            document_date="20.06.2027",
        ),
        GENERAL,
    )
    stored = _stored(values)
    for fragment in (
        STEUERNUMMER,
        "08154",
        ELSTER_NUMBER,
        "345 678",
        "140388",
        "0532 0130",
        BIRTH,
        "Beispielallee",
        "34117",
        "Kassel",
    ):
        assert fragment not in stored, fragment
    assert values["title"].value == f"Vorauszahlungsbescheid {SCRUBBED}"
    assert values["document_date"].value == "2027-06-20"
    assert values["document_date"].confidence == "high"
    assert "1.200,00 EUR" in values["document_date"].source.quote


SELF_DISCLOSURE_PAGE = "\n".join(
    [
        "Mieterselbstauskunft",
        f"für die Wohnung {FLAT}, {FLAT_TOWN}, WE 3",
        "Derzeitige Anschrift: Ahornweg 5, 50667 Köln",
        "Monatliches Nettoeinkommen: 2.400,00 EUR",
        "Köln, den 10.07.2026",
    ]
)


def test_every_quote_loses_the_home_address_and_keeps_the_flats():
    # The street field's own quote spans the whole page, the applicant's address with it.
    values = parse_lines(
        _answer(
            SELF_DISCLOSURE_PAGE,
            title="Mieterselbstauskunft",
            document_date="10.07.2026",
            street="Lindenstraße",
            house_number="7",
            postal_code="10115",
        ),
        GENERAL,
        subtype=DocumentSubtype.TENANT_SELF_DISCLOSURE,
    )
    for key, value in values.items():
        quote = value.source.quote
        assert "Ahornweg" not in quote and "50667" not in quote, key
        assert f"{FLAT}, {FLAT_TOWN}" in quote, key
        # A tenant's income stays: tenant data under the tenant switch, scrubbed after 90 days
        # with the proposal (D19).
        assert "2.400,00 EUR" in quote, key
    assert (values["street"].value, values["house_number"].value) == ("Lindenstraße", "7")
    assert values["postal_code"].value == "10115"
    assert all(values[k].confidence == "high" for k in ("street", "house_number", "postal_code"))


def test_without_a_read_address_every_address_goes():
    values = parse_lines(
        _answer(SELF_DISCLOSURE_PAGE, title="Mieterselbstauskunft", document_date="10.07.2026"),
        GENERAL,
    )
    quote = values["title"].source.quote
    assert "Lindenstraße" not in quote and "10115" not in quote and "Ahornweg" not in quote


def test_a_volunteered_address_keeps_nothing():
    # A Vorauszahlungsbescheid asks no address: an owner's home address the model writes as
    # the street anyway neither stays in its own line nor protects itself in a quote.
    page = PREPAYMENT_PAGE
    values = parse_lines(
        _answer(
            page,
            title="Vorauszahlungsbescheid",
            document_date="20.06.2027",
            street="Beispielallee",
            house_number="4",
            postal_code="34117",
        ),
        GENERAL,
        subtype=DocumentSubtype.PREPAYMENT_NOTICE,
    )
    assert "Beispielallee" not in _stored(values) and "34117" not in _stored(values)
    raw = "street: Beispielallee\nhouse_number: 4\npostal_code: 34117 Kassel"
    asked = frozenset(asked_fields(GENERAL, DocumentSubtype.PREPAYMENT_NOTICE))
    assert "Beispielallee" not in scrub_general_answer(raw, asked)
    assert "34117" not in scrub_general_answer(raw, asked)


def test_a_street_of_two_words_is_kept_only_where_both_stand():
    kept = LinkValues(street="Berliner Straße", house_number="7", postal_code="10115")
    text = "Objekt Berliner Straße 7, 10115 Berlin; Vermieter wohnt Hamburger Straße 7"
    assert scrub_general(text, kept) == (
        f"Objekt Berliner Straße 7, 10115 Berlin; Vermieter wohnt Hamburger {SCRUBBED}"
    )


def test_another_house_number_in_the_same_street_goes():
    kept = LinkValues(street="Lindenstraße", house_number="7", postal_code="10115")
    assert scrub_general("Lindenstraße 7 und Lindenstraße 9", kept) == (
        f"Lindenstraße 7 und {SCRUBBED}"
    )


def test_an_old_unlabelled_date_is_the_documents_own_and_stays():
    # A Teilungserklärung from 1972: the pay documents' birth-date rule would take its date.
    page = "Teilungserklärung\nUrkundenrolle Nr. 812/1972\nMusterstadt, den 12.05.1972"
    values = parse_lines(
        _answer(page, title="Teilungserklärung", document_date="12.05.1972"), GENERAL
    )
    assert values["document_date"].value == "1972-05-12"
    assert values["document_date"].confidence == "high"
    assert "12.05.1972" in values["document_date"].source.quote


def test_a_labelled_birth_date_goes():
    assert scrub_general(f"Max Muster, geb. {BIRTH}") == f"Max Muster, geb. {SCRUBBED}"


LOAN_PAGE = "\n".join(
    [
        "Sparkasse Musterstadt, Hauptstraße 1, 12345 Musterstadt",
        "Darlehensnummer 1234567890",
        f"Darlehensnehmer: Jonas Beispielmann, {HOME}, {HOME_TOWN}",
        f"Steuer-ID {ID_NR}",
        f"Belastungskonto {IBAN}",
        "Mitteilung über die Zinsanpassung vom 01.07.2026",
    ]
)


def test_the_loan_link_survives_the_scrub():
    # A ten-digit contract number is a "run of nine or more digits" to the owners' scrub; the
    # general reading keeps the number it read, in its value and in every quote.
    values = parse_lines(
        _answer(
            LOAN_PAGE,
            title="Zinsanpassung",
            document_date="01.07.2026",
            lender="Sparkasse Musterstadt",
            contract_number="1234567890",
        ),
        GENERAL,
        subtype=DocumentSubtype.LOAN_TERMS_CHANGE,
    )
    assert values["contract_number"].value == "1234567890"
    assert values["contract_number"].confidence == "high"
    assert values["lender"].value == "Sparkasse Musterstadt"
    assert values["lender"].confidence == "high"
    for key, value in values.items():
        quote = value.source.quote
        assert "Darlehensnummer 1234567890" in quote, key
        for fragment in ("345 678", "0532 0130", "Beispielallee", "34117", "Hauptstraße"):
            assert fragment not in quote, (key, fragment)


def test_a_short_contract_number_is_not_protected():
    kept = LinkValues(contract_number="12")
    assert scrub_general(f"Nr. 12, IdNr {ID_NR}", kept) == f"Nr. 12, IdNr {SCRUBBED}"


def test_a_contract_number_inside_a_longer_number_does_not_protect_it():
    kept = LinkValues(contract_number="4711")
    assert scrub_general("Vertrag 4711, Konto 9947110000123", kept) == (
        f"Vertrag 4711, Konto {SCRUBBED}"
    )


def test_a_verwaltervertrag_keeps_the_managed_flat_and_loses_the_owners_address():
    page = "\n".join(
        [
            "Mietverwaltungsvertrag",
            "zwischen Jonas Beispielmann, " + f"{HOME}, {HOME_TOWN}",
            "und Beispiel Hausverwaltung GmbH",
            f"für die Wohnung {FLAT}, {FLAT_TOWN}",
            f"Steuernummer {STEUERNUMMER}",
            "Vergütung 30,00 € zzgl. MwSt. monatlich, Beginn 01.08.2026",
        ]
    )
    values = parse_lines(
        _answer(
            page,
            manager="Beispiel Hausverwaltung GmbH",
            street="Lindenstraße",
            house_number="7",
            postal_code="10115",
            start_date="01.08.2026",
            monthly_fee="30,00 € zzgl. MwSt. monatlich",
        ),
        ExtractionKind.MANAGEMENT_CONTRACT,
    )
    for key, value in values.items():
        quote = value.source.quote
        assert f"{FLAT}, {FLAT_TOWN}" in quote, key
        for fragment in ("Beispielallee", "34117", STEUERNUMMER):
            assert fragment not in quote, (key, fragment)
    assert values["monthly_fee"].value == "30,00 € zzgl. MwSt. monatlich"
    assert values["start_date"].value == "2026-08-01"


def test_the_raw_answer_keeps_the_link_lines_and_loses_the_rest():
    raw = "\n".join(
        [
            f"title: Bescheid {STEUERNUMMER}",
            "street: Lindenstraße",
            "house_number: 7",
            "postal_code: 10115",
            "contract_number: 1234567890",
            f"notes: wohnt {HOME}, {HOME_TOWN}; Darlehen 1234567890; {FLAT}, {FLAT_TOWN}",
        ]
    )
    asked = frozenset(asked_fields(GENERAL, DocumentSubtype.LOAN_TERMS_CHANGE)) | {
        "street",
        "house_number",
        "postal_code",
    }
    assert scrub_general_answer(raw, asked).split("\n") == [
        f"title: Bescheid {SCRUBBED}",
        "street: Lindenstraße",
        "house_number: 7",
        "postal_code: 10115",
        "contract_number: 1234567890",
        f"notes: wohnt {SCRUBBED}, {SCRUBBED}; Darlehen 1234567890; {FLAT}, {FLAT_TOWN}",
    ]


@pytest.mark.parametrize("text", [PREPAYMENT_PAGE, SELF_DISCLOSURE_PAGE, LOAN_PAGE])
def test_the_scrub_is_idempotent_and_keeps_lines(text):
    kept = LinkValues(
        street="Lindenstraße", house_number="7", postal_code="10115", contract_number="1234567890"
    )
    once = scrub_general(text, kept)
    assert scrub_general(once, kept) == once
    assert once.count("\n") == text.count("\n")


def test_the_owners_scrub_is_unchanged():
    # scrub_identifiers keeps every address pattern and the birth-date rule (D103, D129).
    text = f"{FLAT}, {FLAT_TOWN}, den 12.05.1972, {STEUERNUMMER}"
    assert scrub_identifiers(text, reference_year=2026) == (
        f"{SCRUBBED}, {SCRUBBED}, den {SCRUBBED}, {SCRUBBED}"
    )
