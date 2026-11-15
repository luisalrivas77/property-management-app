"""14 Nov 2026 (docs/phases/phase-6.md D165 (a) amended): a depot document's pre-fill picks the
depot whose number it prints (read by code from its text layer) and files the document by its
own date's year. Every number, IBAN, ISIN, name and date below is invented (the IBAN is the
public documentation example)."""

from uuid import UUID

import pytest

from hausbuch.rules.depot import printed_depot_numbers, printed_ibans
from hausbuch.rules.prefill import build_proposal
from hausbuch.schemas.ai import ClassifyOutput, ExtractedValue, SourceRef
from hausbuch.schemas.enums import (
    DepotDocumentKind,
    DocumentSubtype,
    DocumentType,
    ExtractionKind,
)
from hausbuch.schemas.extraction import (
    DepotReportExtraction,
    DistributionNoteExtraction,
    GeneralExtraction,
    SecuritiesTransferExtraction,
    TradeConfirmationExtraction,
)
from hausbuch.schemas.proposal import ExistingDepotAccount, PrefillContext, PrintedDepot

DOC = UUID("00000000-0000-0000-0000-00000000d0c1")
DEPOT_A = ExistingDepotAccount(
    id=UUID("00000000-0000-0000-0000-0000000000d1"), label="Depot Eins (**** 1111)"
)
DEPOT_B = ExistingDepotAccount(
    id=UUID("00000000-0000-0000-0000-0000000000d2"), label="Depot Zwei (**** 2222)"
)
ISIN = "IE00BEISPL01"
IBAN = "DE89370400440532013000"


def _v(value, quote: str | None = None) -> ExtractedValue:
    return ExtractedValue(
        value=value,
        source=SourceRef(page=1, quote=quote or str(value)),
        checks=["cited", "value_in_quote", "validates"],
        confidence="high",
    )


def _context(
    subtype,
    *,
    accounts=(DEPOT_A, DEPOT_B),
    printed=None,
    outcome=None,
    depot_kind=DepotDocumentKind.REBALANCING,
):
    return PrefillContext(
        document_id=DOC,
        classification=ClassifyOutput(
            type=DocumentType.LETTER,
            subtype=subtype,
            contains_tenant_data=False,
            language="de",
            confidence="high",
        ),
        depot_accounts=list(accounts),
        printed_depot=outcome or (PrintedDepot.MATCH if printed else PrintedDepot.NOTHING),
        printed_depot_account_id=printed,
        depot_kind=depot_kind,
    )


def _trade(**overrides) -> TradeConfirmationExtraction:
    values = {
        "isin": _v(ISIN),
        "security_name": _v("Beispiel Welt ETF"),
        "trade_date": _v("2026-05-14", "14.05.2026"),
        "side": _v("Kauf"),
        "units": _v("2.5000", "2,5000"),
        "gross_amount": _v("250.00", "250,00"),
        "fees": _v("0.00", "0,00"),
        "taxes": _v("0.00", "0,00"),
        "net_amount": _v("250.00", "250,00"),
        "order_reference": _v("REF0001"),
    }
    return TradeConfirmationExtraction(**(values | overrides))


def _build(kind, extraction, subtype, **context):
    return build_proposal(kind, extraction, _context(subtype, **context))


# --- the numbers a text prints ------------------------------------------------------------


def test_labelled_depot_numbers_are_found():
    text = (
        "1234567890Depot\nDepot-Nr. 2345678901\n"
        "Der Abrechnungsbetrag wird über Ihr Konto 3456789012 EUR gebucht.\n"
        "4567890123 EURKonto\nDepotnummer: 5678901234"
    )
    assert printed_depot_numbers(text) == {
        "1234567890",
        "2345678901",
        "3456789012",
        "4567890123",
        "5678901234",
    }


def test_an_unlabelled_number_is_none():
    # A transfer prints the other depot's number beside the holder's name, unlabelled.
    text = "Beispiel, Erika 9876543210\nLU1234567890ISIN\n700000001Referenz-Nr\nPLZ 12345"
    assert printed_depot_numbers(text) == set()


def test_a_number_before_depot_nr_is_not_read_as_the_depot():
    assert printed_depot_numbers("1111111111 Depot-Nr. 2222222222") == {"2222222222"}


def test_an_iban_is_found_spaced_or_glued_to_a_label():
    assert printed_ibans(f"Vermögensverwaltungskonto in EUR{IBAN}") == {IBAN}
    assert printed_ibans(f"{IBAN}IBAN") == {IBAN}
    assert printed_ibans("IBAN DE89 3704 0044 0532 0130 00 BIC BEISPIEL") == {IBAN}


def test_an_iban_with_wrong_check_digits_is_none():
    assert printed_ibans("DE88370400440532013000") == set()


def test_an_ibans_body_is_no_depot_number():
    assert printed_depot_numbers(f"{IBAN} Depot") == set()


# --- the depot it prints, pre-selected ----------------------------------------------------


def test_the_printed_depot_is_pre_selected_among_two():
    records = _build(
        ExtractionKind.TRADE_CONFIRMATION,
        _trade(),
        DocumentSubtype.TRADE_CONFIRMATION,
        printed=DEPOT_B.id,
    )
    movement = next(r for r in records if r.key == "movement:1")
    assert movement.fields["account_id"].value == str(DEPOT_B.id)
    assert movement.fields["account_id"].confidence == "high"
    assert movement.notes[0] == (
        "Das Dokument nennt die Nummer des Depots Depot Zwei (**** 2222); es ist deshalb "
        "vorausgewählt."
    )
    assert [o.value for o in movement.options["account_id"]] == [str(DEPOT_A.id), str(DEPOT_B.id)]


def test_without_a_printed_depot_two_depots_are_left_to_the_user():
    records = _build(
        ExtractionKind.TRADE_CONFIRMATION, _trade(), DocumentSubtype.TRADE_CONFIRMATION
    )
    movement = next(r for r in records if r.key == "movement:1")
    assert movement.fields["account_id"].value is None


@pytest.mark.parametrize("outcome", [PrintedDepot.NOTHING, PrintedDepot.UNREADABLE])
def test_a_document_that_names_no_depot_falls_back_to_the_only_depot(outcome):
    records = _build(
        ExtractionKind.TRADE_CONFIRMATION,
        _trade(),
        DocumentSubtype.TRADE_CONFIRMATION,
        accounts=(DEPOT_A,),
        outcome=outcome,
    )
    movement = next(r for r in records if r.key == "movement:1")
    assert movement.fields["account_id"].value == str(DEPOT_A.id)
    assert movement.fields["account_id"].confidence == "low"


def test_a_report_over_several_depots_is_not_put_on_the_only_one_and_has_no_note():
    records = _build(
        ExtractionKind.TRADE_CONFIRMATION,
        _trade(),
        DocumentSubtype.TRADE_CONFIRMATION,
        accounts=(DEPOT_A,),
        outcome=PrintedDepot.SEVERAL,
    )
    movement = next(r for r in records if r.key == "movement:1")
    assert movement.fields["account_id"].value is None
    assert movement.notes == []


def test_a_document_that_names_another_depot_is_not_put_on_the_only_one():
    # Review, 15 Nov 2026: the receiving depot of an Einbuchung is not created yet, and the
    # only depot under Konten is another one; the user creates the right depot first.
    records = _build(
        ExtractionKind.SECURITIES_TRANSFER,
        SecuritiesTransferExtraction(
            direction=_v("Einbuchung"),
            transfer_date=_v("2026-03-02", "02.03.2026"),
            reference=_v("REF0002"),
            positions=_v([f"{ISIN} | Beispiel Welt ETF | 2,5000"]),
        ),
        DocumentSubtype.SECURITIES_TRANSFER,
        accounts=(DEPOT_A,),
        outcome=PrintedDepot.NAMED_ELSEWHERE,
    )
    movement = next(r for r in records if r.key == "movement:1")
    assert movement.fields["account_id"].value is None
    assert [o.value for o in movement.options["account_id"]] == [str(DEPOT_A.id)]
    assert movement.notes[0] == (
        "Dieses Dokument nennt ein anderes Depot, als unter Konten angelegt ist. Bitte zuerst "
        "dieses Depot anlegen (Konten › „Konto anlegen“, Kontoart „Depot“), dann hier wählen."
    )


# --- the Steuerjahr by the document's own date --------------------------------------------


def _tax_year(records) -> ExtractedValue | None:
    return records[0].fields.get("tax_year")


@pytest.mark.parametrize(
    ("kind", "extraction", "subtype", "year", "source"),
    [
        (
            ExtractionKind.TRADE_CONFIRMATION,
            _trade(document_date=_v("2027-01-02", "02.01.2027")),
            DocumentSubtype.TRADE_CONFIRMATION,
            2026,
            "trade_date",
        ),
        (
            ExtractionKind.DISTRIBUTION_NOTE,
            DistributionNoteExtraction(
                record_date=_v("2025-12-30", "30.12.2025"),
                payment_date=_v("2026-01-05", "05.01.2026"),
            ),
            DocumentSubtype.DISTRIBUTION_NOTE,
            2026,
            "payment_date",
        ),
        (
            ExtractionKind.DISTRIBUTION_NOTE,
            DistributionNoteExtraction(record_date=_v("2025-12-30", "30.12.2025")),
            DocumentSubtype.DISTRIBUTION_NOTE,
            2025,
            "record_date",
        ),
        (
            ExtractionKind.SECURITIES_TRANSFER,
            SecuritiesTransferExtraction(transfer_date=_v("2026-03-02", "02.03.2026")),
            DocumentSubtype.SECURITIES_TRANSFER,
            2026,
            "transfer_date",
        ),
        (
            ExtractionKind.DEPOT_REPORT,
            DepotReportExtraction(
                report_date=_v("2025-12-31", "31.12.2025"),
                document_date=_v("2026-01-10", "10.01.2026"),
            ),
            DocumentSubtype.DEPOT_REPORT,
            2025,
            "report_date",
        ),
        (
            ExtractionKind.DEPOT_REPORT,
            DepotReportExtraction(document_date=_v("2026-01-10", "10.01.2026")),
            DocumentSubtype.DEPOT_REPORT,
            2026,
            "document_date",
        ),
        (
            ExtractionKind.GENERAL,
            GeneralExtraction(document_date=_v("2026-04-01", "01.04.2026")),
            None,
            2026,
            "document_date",
        ),
    ],
)
def test_a_depot_document_is_filed_by_its_own_date(kind, extraction, subtype, year, source):
    tax_year = _tax_year(build_proposal(kind, extraction, _context(subtype)))
    assert tax_year is not None
    assert tax_year.value == year
    assert tax_year.derived_from == [source]


def test_a_general_reading_of_another_document_gets_no_year_by_code():
    records = build_proposal(
        ExtractionKind.GENERAL,
        GeneralExtraction(document_date=_v("2026-04-01", "01.04.2026")),
        _context(None, depot_kind=None),
    )
    assert _tax_year(records) is None


# --- a year-end document by the year it reports on (review, 15 Nov 2026) ------------------

ANNUAL_KINDS = [
    DepotDocumentKind.JAHRESSTEUERBESCHEINIGUNG,
    DepotDocumentKind.ERTRAGSUEBERSICHT,
    DepotDocumentKind.KOSTENAUSWEIS,
]


@pytest.mark.parametrize("depot_kind", ANNUAL_KINDS)
def test_a_year_end_document_takes_the_year_its_title_names(depot_kind):
    records = build_proposal(
        ExtractionKind.GENERAL,
        GeneralExtraction(
            title=_v("Jahressteuerbescheinigung 2026"),
            document_date=_v("2027-02-15", "15.02.2027"),
        ),
        _context(None, depot_kind=depot_kind),
    )
    tax_year = _tax_year(records)
    assert tax_year is not None
    assert tax_year.value == 2026
    assert tax_year.derived_from == ["title"]
    assert tax_year.confidence == "medium"


@pytest.mark.parametrize(
    "title",
    [
        None,
        "Jahressteuerbescheinigung",
        # two years named: the title does not decide
        "Ertragsübersicht 2026, erstellt 2027",
    ],
)
@pytest.mark.parametrize("depot_kind", ANNUAL_KINDS)
def test_a_year_end_document_without_one_year_in_its_title_takes_the_year_before_its_date(
    depot_kind, title
):
    extraction = GeneralExtraction(
        document_date=_v("2027-02-15", "15.02.2027"),
        **({"title": _v(title)} if title is not None else {}),
    )
    records = build_proposal(
        ExtractionKind.GENERAL, extraction, _context(None, depot_kind=depot_kind)
    )
    tax_year = _tax_year(records)
    assert tax_year is not None
    assert tax_year.value == 2026
    assert tax_year.derived_from == ["document_date"]
    assert tax_year.confidence == "low"


def test_a_year_end_document_without_title_year_or_date_gets_no_year():
    records = build_proposal(
        ExtractionKind.GENERAL,
        GeneralExtraction(title=_v("Kostenausweis")),
        _context(None, depot_kind=DepotDocumentKind.KOSTENAUSWEIS),
    )
    assert _tax_year(records) is None


@pytest.mark.parametrize(
    "depot_kind",
    [k for k in DepotDocumentKind if k not in ANNUAL_KINDS],
)
def test_every_other_depot_document_keeps_its_own_dates_year(depot_kind):
    records = build_proposal(
        ExtractionKind.GENERAL,
        GeneralExtraction(
            title=_v("Mitteilung 2025"),
            document_date=_v("2026-04-01", "01.04.2026"),
        ),
        _context(None, depot_kind=depot_kind),
    )
    tax_year = _tax_year(records)
    assert tax_year is not None
    assert tax_year.value == 2026
    assert tax_year.derived_from == ["document_date"]


def test_a_depot_document_without_any_date_gets_no_year():
    records = build_proposal(
        ExtractionKind.SECURITIES_TRANSFER,
        SecuritiesTransferExtraction(),
        _context(DocumentSubtype.SECURITIES_TRANSFER),
    )
    assert _tax_year(records) is None
