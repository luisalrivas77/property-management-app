"""The two pay documents in rules.extraction_parse (docs/phases/phase-4b.md D96, D103, §8.3):
their routing from the classifier's type, and the identifier scrub of every line and quote.

All names and numbers are invented; the identifiers have the printed shapes only."""

import pytest

from hausbuch.ai.requests import field_list
from hausbuch.rules.extraction_parse import (
    SCRUBBED,
    SCRUBBED_KINDS,
    extraction_kind,
    parse_lines,
    scrub_identifiers,
)
from hausbuch.schemas.ai import CitedTextBlock, PageCitation
from hausbuch.schemas.enums import DocumentType, ExtractionKind

ID_NR = "12 345 678 903"
SV_NR = "65 140388 M 00 3"
ETIN = "MSTRERKA88C14K"
IBAN = "DE89 3704 0044 0532 0130 00"
BIRTH = "14.03.1988"
PERSONNEL = "004711"


@pytest.mark.parametrize(
    ("type_", "kind"),
    [
        (DocumentType.LOHNSTEUERBESCHEINIGUNG, ExtractionKind.LOHNSTEUERBESCHEINIGUNG),
        (DocumentType.GEHALTSABRECHNUNG, ExtractionKind.GEHALTSABRECHNUNG),
    ],
)
def test_a_classified_pay_document_gets_its_field_set(type_, kind):
    assert extraction_kind(type_, None) is kind
    assert kind in SCRUBBED_KINDS


def test_the_field_list_names_no_identifier():
    for kind in SCRUBBED_KINDS:
        text = field_list(kind).casefold()
        for word in ("identifikation", "geburt", "personalnummer", "iban", "sozialvers"):
            assert word not in text, (kind, word)


@pytest.mark.parametrize(
    ("text", "gone"),
    [
        (f"Erika Musterfrau, geb. {BIRTH}, IdNr. {ID_NR}", [BIRTH, "12 345", "678 903"]),
        (f"Identifikationsnummer: {ID_NR.replace(' ', '')} Steuerklasse 4", ["12345678903"]),
        (f"Steuerliche Identifikationsnummer {ID_NR}", ["345 678"]),
        (f"Sozialversicherungsnummer {SV_NR}", ["140388", "M 00 3"]),
        (f"SV-Nr.: {SV_NR.replace(' ', '')}", ["140388M003"]),
        (f"Versicherungsnummer {SV_NR.replace(' ', '')} Krankenkasse", ["140388"]),
        (f"eTIN {ETIN}", [ETIN]),
        (f"Lohnsteuerbescheinigung {ETIN} 2026", [ETIN]),
        (f"Personalnummer {PERSONNEL} Abrechnung", [PERSONNEL]),
        (f"Pers.-Nr. {PERSONNEL}", [PERSONNEL]),
        (f"Konto {IBAN} Auszahlung", ["3704", "0532"]),
        (f"IBAN: {IBAN.replace(' ', '')}", ["DE893704"]),
        ("Steuernummer 12/345/67890", ["12/345/67890"]),
        ("Finanzamt 143/123/45678", ["143/123/45678"]),
        ("Geburtsdatum 14. März 1988", ["14.", "März", "1988"]),
        ("geboren am 14.03.1988", [BIRTH]),
        ("Transferticket abc12345xyz", ["abc12345xyz"]),
        (
            "Muster GmbH, Industriestraße 5, 12345 Musterstadt",
            ["Industrie", "12345", "Musterstadt"],
        ),
        ("Erika Musterfrau, Musterweg 9a, 60311 Frankfurt am Main", ["Musterweg", "60311", "Main"]),
        ("Am Ring 12, 10115 Berlin", ["Ring 12", "10115", "Berlin"]),
        ("Kundennummer 123456789012", ["123456789012"]),
        # The short label spellings (review of step 2).
        (f"Geb.-Dat.: {BIRTH}", [BIRTH]),
        (f"Geb.dat. {BIRTH}", [BIRTH]),
        (f"Geburtsdat. {BIRTH}", [BIRTH]),
        (f"Gebdat {BIRTH}", [BIRTH]),
        (f"MA-Nr. {PERSONNEL}", [PERSONNEL]),
        (f"MANr {PERSONNEL}", [PERSONNEL]),
        (f"Mitarb.-Nr.: {PERSONNEL}", [PERSONNEL]),
        (f"Mitarb.Nr. {PERSONNEL}", [PERSONNEL]),
        (f"AN-Nr. {PERSONNEL}", [PERSONNEL]),
        (f"ANNr: {PERSONNEL}", [PERSONNEL]),
        # The values under a line of labels only, as on a payslip's header.
        (f"Geburtsdatum   Personalnummer\n{BIRTH}     731942", [BIRTH, "731942"]),
        (f"Geb.-Dat. / Pers.-Nr.:\n\n  {BIRTH}  {PERSONNEL}  ", [BIRTH, PERSONNEL]),
        (f"MA-Nr.\n{PERSONNEL} Abteilung 7", [PERSONNEL, " 7"]),
        # A birth date without its label: a full date at least 14 years back.
        (f"employee_name: Erika Musterfrau, {BIRTH}", [BIRTH]),
        (
            f"Erika Musterfrau\n{BIRTH}\nMusterweg 9a\n60311 Frankfurt am Main",
            [BIRTH, "Musterweg", "60311"],
        ),
        ("Erika Musterfrau (14. März 1988)", ["1988"]),
        ("Erika Musterfrau 1988-03-14", ["1988-03-14"]),
    ],
)
def test_the_scrub_removes_identifiers(text, gone):
    scrubbed = scrub_identifiers(text, reference_year=2026)
    for fragment in gone:
        assert fragment not in scrubbed, (text, scrubbed)
    assert SCRUBBED in scrubbed
    assert scrubbed.count("\n") == text.count("\n")  # lines are kept
    assert scrub_identifiers(scrubbed, reference_year=2026) == scrubbed  # idempotent


def test_the_name_beside_a_birth_date_stays():
    scrubbed = scrub_identifiers(f"employee_name: Erika Musterfrau, {BIRTH}", reference_year=2026)
    assert scrubbed == f"employee_name: Erika Musterfrau, {SCRUBBED}"


@pytest.mark.parametrize(
    ("year", "kept"), [(2026, False), (2025, True), (2016, True), (2027, False)]
)
def test_the_birth_date_rule_counts_back_from_the_reference_year(year, kept):
    # A date in 2012 is 14 years before 2026: a birth date then, an old payslip's date before.
    text = "Eintritt 01.04.2012"
    assert (scrub_identifiers(text, reference_year=year) == text) is kept


@pytest.mark.parametrize(
    "text",
    [
        "3. Bruttoarbeitslohn einschl. Sachbezüge 60.000,00",
        "Steuer-Brutto 5.000,00 35.000,00",
        "1. Dauer des Dienstverhältnisses vom 01.01.2026 bis 31.12.2026",
        "Steuerklasse/Faktor 4 / 0,950",
        "Kirchensteuermerkmale ev",
        "Zahl der Kinderfreibeträge 1,0",
        "22. a) Arbeitgeberanteil zur gesetzlichen Rentenversicherung 5.580,00",
        "Marketing GmbH Abrechnungszeitraum 01.08.2026 - 31.08.2026",
        "Muster GmbH",
        "Erika Musterfrau",
        "Nr. 27 Arbeitnehmerbeiträge zur Arbeitslosenversicherung 780,00 EUR",
        "Jahreswerte kumuliert 12345 EUR",
        "Stand 01.01.2026",
        "vom 01.01.2026 bis 31.12.2026",
        "Abrechnungszeitraum 01.07.2026 - 31.07.2026",
        "Abrechnung 2026-07-31",
        "Abrechnung für Juli 2026, ausgezahlt am 30. Juli 2026",
        # A line of labels with another word is no label line: the next line stays.
        "Geburtsdatum Eintritt Abteilung\n2.450,00 7",
        "Steuerklasse Faktor\n4 0,950",
    ],
)
def test_the_scrub_keeps_figures_dates_and_names(text):
    assert scrub_identifiers(text, reference_year=2026) == text


def _cited(line: str, page: int = 1) -> PageCitation:
    return PageCitation(cited_text=line, start_page_number=page, end_page_number=page + 1)


def test_a_quote_beside_an_identifier_is_scrubbed_and_the_value_stays_high():
    line = f"Erika Musterfrau  IdNr. {ID_NR}  3. Bruttoarbeitslohn 60.000,00"
    blocks = [
        CitedTextBlock(text="gross: "),
        CitedTextBlock(text="60.000,00", citations=[_cited(line)]),
        CitedTextBlock(text="\nemployee_name: "),
        CitedTextBlock(text="Erika Musterfrau", citations=[_cited(line)]),
    ]
    values = parse_lines(blocks, ExtractionKind.LOHNSTEUERBESCHEINIGUNG)
    gross, name = values["gross"], values["employee_name"]
    assert gross.value == "60000.00" and gross.confidence == "high"
    assert name.value == "Erika Musterfrau" and name.confidence == "high"
    for value in (gross, name):
        assert value.source is not None
        assert "345" not in value.source.quote and "678" not in value.source.quote
        assert "60.000,00" in value.source.quote


def test_a_value_carrying_an_identifier_is_scrubbed_even_across_blocks():
    # The model wrote more than the name, split over two text blocks: the line is joined
    # before the scrub, so the number cannot slip through between them.
    blocks = [
        CitedTextBlock(text="employee_name: Erika Musterfrau (IdNr. 12 345"),
        CitedTextBlock(text=" 678 903)\nemployer_name: Muster GmbH, Industriestraße 5"),
    ]
    values = parse_lines(blocks, ExtractionKind.LOHNSTEUERBESCHEINIGUNG)
    assert "345" not in values["employee_name"].value
    assert "678" not in values["employee_name"].value
    assert values["employer_name"].value == f"Muster GmbH, {SCRUBBED}"


def test_other_kinds_are_not_scrubbed():
    # A loan contract's account IBAN is a field there (phase 2): never scrubbed.
    blocks = [CitedTextBlock(text=f"account_iban: {IBAN}", citations=[_cited(f"Konto {IBAN}")])]
    value = parse_lines(blocks, ExtractionKind.LOAN_CONTRACT)["account_iban"]
    assert value.value == IBAN.replace(" ", "")
    assert value.source is not None and IBAN in value.source.quote


def test_a_payslip_line_keeps_month_and_year_to_date():
    line = f"Personalnummer {PERSONNEL}  Steuer-Brutto 5.000,00 35.000,00"
    blocks = [
        CitedTextBlock(text="gross_month: "),
        CitedTextBlock(text="5.000,00", citations=[_cited(line)]),
        CitedTextBlock(text="\ngross_ytd: "),
        CitedTextBlock(text="35.000,00", citations=[_cited(line)]),
    ]
    values = parse_lines(blocks, ExtractionKind.GEHALTSABRECHNUNG)
    assert values["gross_month"].value == "5000.00"
    assert values["gross_ytd"].value == "35000.00"
    for value in values.values():
        assert value.confidence == "high"
        assert value.source is not None and PERSONNEL not in value.source.quote


def test_a_value_under_a_line_of_labels_is_scrubbed_in_the_lines_and_the_quote():
    quote = f"Geburtsdatum   Personalnummer\n{BIRTH}     731942\nSteuer-Brutto 5.000,00"
    blocks = [
        CitedTextBlock(text="Personalnummer\n"),
        CitedTextBlock(text="employee_name: Erika Musterfrau 731942\ngross_month: "),
        CitedTextBlock(text="5.000,00", citations=[_cited(quote)]),
    ]
    values = parse_lines(blocks, ExtractionKind.GEHALTSABRECHNUNG, reference_year=2026)
    assert values["employee_name"].value == f"Erika Musterfrau {SCRUBBED}"
    gross = values["gross_month"]
    assert gross.value == "5000.00" and gross.source is not None
    assert BIRTH not in gross.source.quote and "731942" not in gross.source.quote
    assert "5.000,00" in gross.source.quote


def test_parse_lines_takes_the_reference_year_for_the_birth_date_rule():
    blocks = [CitedTextBlock(text="employee_name: Erika Musterfrau, 01.04.2012")]
    kind = ExtractionKind.GEHALTSABRECHNUNG
    assert "2012" not in parse_lines(blocks, kind, reference_year=2026)["employee_name"].value
    assert "2012" in parse_lines(blocks, kind, reference_year=2025)["employee_name"].value
