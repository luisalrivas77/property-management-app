"""The general reading's scrub in rules.extraction_parse (docs/phases/phase-2.md D131, decided
10 Nov 2026): every line and quote of a `general` or `management_contract` reading loses the
identifiers of D103/D129 and every address but the workspace's own property addresses (PR #135
review: never what the model read), while the values it links by (a property's address, the
bank's name, a contract number that is no identifier) survive.

All names, numbers and addresses are invented; the identifiers have the printed shapes only."""

import pytest

from hausbuch.jobs.document_read import merge_values
from hausbuch.rules.extraction_parse import (
    GENERAL_SCRUBBED_KINDS,
    SCRUBBED,
    SCRUBBED_KINDS,
    UNKNOWN,
    PropertyAddress,
    parse_lines,
    scrub_general,
    scrub_general_answer,
    scrub_identifiers,
)
from hausbuch.rules.prefill import build_proposal
from hausbuch.schemas.ai import CitedTextBlock, ExtractedValue, PageCitation, SourceRef
from hausbuch.schemas.enums import DocumentSubtype, ExtractionKind
from hausbuch.schemas.extraction import EXTRACTION_MODELS, GeneralExtraction, asked_fields
from hausbuch.schemas.proposal import PrefillContext

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
PROPERTIES = (PropertyAddress("Lindenstraße", "7", "10115"),)  # the workspace's flat


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
        properties=PROPERTIES,
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


def test_the_property_address_stays_in_a_quote_without_a_read_address():
    # The kept address comes from the workspace's properties, not from the model's lines.
    values = parse_lines(
        _answer(SELF_DISCLOSURE_PAGE, title="Mieterselbstauskunft", document_date="10.07.2026"),
        GENERAL,
        properties=PROPERTIES,
    )
    quote = values["title"].source.quote
    assert f"{FLAT}, {FLAT_TOWN}" in quote and "Ahornweg" not in quote and "50667" not in quote


def test_without_properties_every_address_goes():
    values = parse_lines(
        _answer(SELF_DISCLOSURE_PAGE, title="Mieterselbstauskunft", document_date="10.07.2026"),
        GENERAL,
    )
    quote = values["title"].source.quote
    assert "Lindenstraße" not in quote and "10115" not in quote and "Ahornweg" not in quote


def test_a_volunteered_address_keeps_nothing():
    # A Vorauszahlungsbescheid asks no address: an owner's home address the model writes as
    # the street anyway neither stays in its own line nor in a quote.
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
        properties=PROPERTIES,
    )
    assert "Beispielallee" not in _stored(values) and "34117" not in _stored(values)
    raw = "street: Beispielallee\nhouse_number: 4\npostal_code: 34117 Kassel"
    asked = frozenset(asked_fields(GENERAL, DocumentSubtype.PREPAYMENT_NOTICE))
    assert "Beispielallee" not in scrub_general_answer(raw, asked, PROPERTIES)
    assert "34117" not in scrub_general_answer(raw, asked, PROPERTIES)


INSURANCE_PAGE = "\n".join(
    [
        "Wohngebäudeversicherung Versicherungsschein",
        f"Versicherungsnehmer: Jonas Beispielmann, {HOME}, {HOME_TOWN}",
        "Beitrag 240,00 EUR jährlich",
        "Musterstadt, den 02.01.2027",
    ]
)


def test_a_home_address_read_as_the_property_is_dropped_with_its_source_kept():
    # The model reads the Versicherungsnehmer's home as the property: no property has it, so
    # its value goes (the pre-fill then links nothing), and nowhere does it stay.
    values = parse_lines(
        _answer(
            INSURANCE_PAGE,
            title=f"Versicherungsschein {HOME}",
            document_date="02.01.2027",
            street="Beispielallee",
            house_number="4",
            postal_code="34117",
        ),
        GENERAL,
        subtype=DocumentSubtype.INSURANCE_POLICY,
        properties=PROPERTIES,
    )
    for key in ("street", "house_number", "postal_code"):
        assert values[key].value is None, key
        assert values[key].source is not None and values[key].checks == ["cited"], key
    stored = _stored(values)
    assert "Beispielallee" not in stored and "34117" not in stored and "Kassel" not in stored
    assert values["title"].value == f"Versicherungsschein {SCRUBBED}"
    raw = "street: Beispielallee\nhouse_number: 4\npostal_code: 34117"
    asked = frozenset(asked_fields(GENERAL, DocumentSubtype.INSURANCE_POLICY))
    assert scrub_general_answer(raw, asked, PROPERTIES).split("\n") == [
        f"street: {SCRUBBED}",
        f"house_number: {SCRUBBED}",
        f"postal_code: {SCRUBBED}",
    ]


def test_the_propertys_street_in_another_town_is_no_property():
    # Same street and number, another postal code: the address read fits no property.
    values = parse_lines(
        _answer(
            "Versicherungsnehmer: Lindenstraße 7, 80331 München",
            title="Versicherungsschein",
            street="Lindenstraße",
            house_number="7",
            postal_code="80331",
        ),
        GENERAL,
        subtype=DocumentSubtype.INSURANCE_POLICY,
        properties=PROPERTIES,
    )
    assert values["street"].value is None and values["postal_code"].value is None
    assert "80331" not in _stored(values)


def test_an_address_the_model_did_not_find_is_missing_not_dropped():
    # "unbekannt" is no address read: missing, so the pre-fill may still use its fallback.
    values = parse_lines(
        _answer(INSURANCE_PAGE, title="Versicherungsschein", street=UNKNOWN, postal_code=UNKNOWN),
        GENERAL,
        subtype=DocumentSubtype.INSURANCE_POLICY,
        properties=PROPERTIES,
    )
    assert values["street"] == ExtractedValue.missing()
    assert values["postal_code"] == ExtractedValue.missing()


def test_a_street_of_two_words_is_kept_only_where_both_stand():
    properties = (PropertyAddress("Berliner Straße", "7", "10115"),)
    text = "Objekt Berliner Straße 7, 10115 Berlin; Vermieter wohnt Hamburger Straße 7"
    assert scrub_general(text, properties) == (
        f"Objekt Berliner Straße 7, 10115 Berlin; Vermieter wohnt Hamburger {SCRUBBED}"
    )


def test_another_house_number_in_the_same_street_goes():
    assert scrub_general("Lindenstraße 7 und Lindenstraße 9", PROPERTIES) == (
        f"Lindenstraße 7 und {SCRUBBED}"
    )


def test_a_property_street_is_kept_by_key():
    properties = (PropertyAddress("Lindenstraße", "12 a", "10115"),)
    assert scrub_general("Lindenstr. 12a, 10115 Berlin", properties) == (
        "Lindenstr. 12a, 10115 Berlin"
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
        properties=PROPERTIES,
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


@pytest.mark.parametrize("number", [IBAN, IBAN.replace(" ", ""), STEUERNUMMER])
def test_an_identifier_read_as_the_contract_number_is_not_read(number):
    # The model writes the Belastungskonto's IBAN (or a Steuernummer) as the contract number:
    # it is no loan's number, so it protects nothing and is stored nowhere.
    values = parse_lines(
        _answer(
            LOAN_PAGE + f"\nSteuernummer {STEUERNUMMER}",
            title="Zinsanpassung",
            lender="Sparkasse Musterstadt",
            contract_number=number,
        ),
        GENERAL,
        subtype=DocumentSubtype.LOAN_TERMS_CHANGE,
        properties=PROPERTIES,
    )
    assert values["contract_number"].value is None
    assert values["contract_number"].source is None
    for key, value in values.items():
        quote = value.source.quote if value.source else ""
        assert "0532 0130" not in quote and "05320130" not in quote, key
        assert STEUERNUMMER not in quote, key
    raw = f"title: Zinsanpassung\nlender: Sparkasse Musterstadt\ncontract_number: {number}"
    asked = frozenset(asked_fields(GENERAL, DocumentSubtype.LOAN_TERMS_CHANGE))
    answer = scrub_general_answer(raw, asked, PROPERTIES)
    assert "0532 0130" not in answer and "05320130" not in answer and STEUERNUMMER not in answer
    assert answer.split("\n")[-1] == f"contract_number: {SCRUBBED}"


def test_the_lender_keeps_the_banks_name_and_loses_its_numbers():
    lender = "Sparkasse Musterstadt Kundennummer 123456789"
    page = f"{lender}\nDarlehensnummer 1234567890\nMitteilung vom 01.07.2026"
    values = parse_lines(
        _answer(page, title="Zinsanpassung", lender=lender, contract_number="1234567890"),
        GENERAL,
        subtype=DocumentSubtype.LOAN_TERMS_CHANGE,
        properties=PROPERTIES,
    )
    assert values["lender"].value == f"Sparkasse Musterstadt Kundennummer {SCRUBBED}"
    assert values["contract_number"].value == "1234567890"
    assert "123456789" not in values["lender"].source.quote.replace("1234567890", "")
    raw = f"lender: {lender}\ncontract_number: 1234567890"
    asked = frozenset(asked_fields(GENERAL, DocumentSubtype.LOAN_TERMS_CHANGE))
    assert scrub_general_answer(raw, asked, PROPERTIES).split("\n") == [
        f"lender: Sparkasse Musterstadt Kundennummer {SCRUBBED}",
        "contract_number: 1234567890",
    ]


def test_a_short_contract_number_is_not_protected():
    assert scrub_general(f"Nr. 12, IdNr {ID_NR}", contract_number="12") == (
        f"Nr. 12, IdNr {SCRUBBED}"
    )


def test_a_contract_number_inside_a_longer_number_does_not_protect_it():
    assert scrub_general("Vertrag 4711, Konto 9947110000123", contract_number="4711") == (
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
        properties=PROPERTIES,
    )
    for key, value in values.items():
        quote = value.source.quote
        assert f"{FLAT}, {FLAT_TOWN}" in quote, key
        for fragment in ("Beispielallee", "34117", STEUERNUMMER):
            assert fragment not in quote, (key, fragment)
    assert values["monthly_fee"].value == "30,00 € zzgl. MwSt. monatlich"
    assert values["start_date"].value == "2026-08-01"
    assert values["street"].value == "Lindenstraße"


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
    assert scrub_general_answer(raw, asked, PROPERTIES).split("\n") == [
        f"title: Bescheid {SCRUBBED}",
        "street: Lindenstraße",
        "house_number: 7",
        "postal_code: 10115",
        "contract_number: 1234567890",
        f"notes: wohnt {SCRUBBED}, {SCRUBBED}; Darlehen 1234567890; {FLAT}, {FLAT_TOWN}",
    ]


@pytest.mark.parametrize("text", [PREPAYMENT_PAGE, SELF_DISCLOSURE_PAGE, LOAN_PAGE])
def test_the_scrub_is_idempotent_and_keeps_lines(text):
    once = scrub_general(text, PROPERTIES, "1234567890")
    assert scrub_general(once, PROPERTIES, "1234567890") == once
    assert once.count("\n") == text.count("\n")


def _cited_none() -> ExtractedValue:
    source = SourceRef(page=1, quote="Versicherungsnehmer: …, …")
    return ExtractedValue(value=None, source=source, checks=["cited"], confidence="low")


def test_a_later_chunk_that_read_nothing_keeps_the_dropped_address():
    # Chunk 1 read the home as the street (value dropped, source kept); chunk 2 read nothing.
    merged = {"street": _cited_none()}
    merge_values(merged, {"street": ExtractedValue.missing()})
    assert merged["street"].source is not None
    merged = {"street": ExtractedValue.missing()}
    merge_values(merged, {"street": _cited_none()})
    assert merged["street"].source is not None


def test_the_owners_scrub_is_unchanged():
    # scrub_identifiers keeps every address pattern and the birth-date rule (D103, D129).
    text = f"{FLAT}, {FLAT_TOWN}, den 12.05.1972, {STEUERNUMMER}"
    assert scrub_identifiers(text, reference_year=2026) == (
        f"{SCRUBBED}, {SCRUBBED}, den {SCRUBBED}, {SCRUBBED}"
    )


_FLAT_ID = "00000000-0000-4000-8000-000000000001"


def _proposal_for(street: str, number: str, postal: str) -> list:
    """The general reading of the insurance page with this address read, through the
    pre-fill, in a workspace whose only property is the flat."""
    values = parse_lines(
        _answer(
            INSURANCE_PAGE + f"\nVersichertes Objekt: {FLAT}, {FLAT_TOWN}",
            title="Versicherungsschein",
            document_date="02.01.2027",
            street=street,
            house_number=number,
            postal_code=postal,
        ),
        GENERAL,
        subtype=DocumentSubtype.INSURANCE_POLICY,
        properties=PROPERTIES,
    )
    fields = EXTRACTION_MODELS[GENERAL].model_fields
    extraction = GeneralExtraction.model_validate(
        {name: values.get(name, ExtractedValue.missing()) for name in fields}
    )
    context = PrefillContext.model_validate(
        {
            "document_id": "00000000-0000-4000-8000-0000000000d0",
            "classification": {
                "type": "contract",
                "subtype": "insurance_policy",
                "contains_tenant_data": False,
                "language": "de",
                "confidence": "high",
            },
            "properties": [
                {
                    "id": _FLAT_ID,
                    "kind": "etw",
                    "address": {
                        "street": "Lindenstraße",
                        "house_number": "7",
                        "postal_code": "10115",
                        "city": "Berlin",
                    },
                    "build_year": 1994,
                    "notarized_on": "2026-03-10",
                    "purchase_date": "2026-05-01",
                    "purchase_price": "289000.00",
                }
            ],
        }
    )
    return build_proposal(GENERAL, extraction, context)


def test_a_home_address_read_as_the_property_links_nothing():
    # Without F2 the only property would be linked: the address was read and dropped, so the
    # pre-fill must not fall back to the workspace's only property.
    records = _proposal_for("Beispielallee", "4", "34117")
    assert [r.key for r in records] == ["typing"]
    assert any("kein Objekt gefunden" in note for note in records[0].notes)


def test_the_propertys_own_address_still_links_it():
    records = _proposal_for("Lindenstraße", "7", "10115")
    assert [r.key for r in records] == ["typing", "link:property"]
    assert str(records[1].target_id) == _FLAT_ID
