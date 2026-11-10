"""Phase 4b step 2: the pre-fill records of the two pay documents (docs/phases/phase-4b.md
§8.2, D105, Q-O29) and return_input_value, the mapping from the confirmed values to a row's
value. All names, employers and figures are invented."""

from decimal import Decimal
from uuid import UUID

import pytest

from hausbuch.rules.prefill import (
    CERTIFICATE_LINES,
    _church_tax_marked,
    _month_start,
    _tax_class_parts,
    build_proposal,
    return_input_value,
)
from hausbuch.schemas.ai import ClassifyOutput, ExtractedValue, SourceRef
from hausbuch.schemas.employment import (
    CERTIFICATE_AMOUNTS,
    PAYSLIP_FIGURES,
    Payslip,
    WageCertificate,
)
from hausbuch.schemas.enums import DocumentType, ExtractionKind
from hausbuch.schemas.extraction import PayslipExtraction, WageCertificateExtraction
from hausbuch.schemas.proposal import (
    ExistingOwner,
    ExistingPayRow,
    PrefillContext,
    ProposalRecord,
)

DOC = UUID("00000000-0000-0000-0000-00000000d0c1")
OWNER_A = ExistingOwner(
    id=UUID("00000000-0000-0000-0000-0000000000a1"), full_name="Erika Musterfrau"
)
OWNER_B = ExistingOwner(id=UUID("00000000-0000-0000-0000-0000000000b2"), full_name="Max Mustermann")


def _v(value, quote: str | None = None) -> ExtractedValue:
    return ExtractedValue(
        value=value,
        source=SourceRef(page=1, quote=quote or str(value)),
        checks=["cited", "value_in_quote", "validates"],
        confidence="high",
    )


def _context(doc_type: DocumentType, **update) -> PrefillContext:
    classification = ClassifyOutput(
        type=doc_type, subtype=None, contains_tenant_data=False, language="de", confidence="high"
    )
    return PrefillContext(
        document_id=DOC, classification=classification, **({"owners": [OWNER_A, OWNER_B]} | update)
    )


def _certificate(**overrides) -> WageCertificateExtraction:
    values = {
        "employee_name": _v("Erika Musterfrau"),
        "employer_name": _v("Muster GmbH"),
        "tax_year": _v(2026),
        "period_from": _v("2026-01-01", "vom 01.01.2026"),
        "period_to": _v("2026-12-31", "bis 31.12.2026"),
        "tax_class": _v("4 / 0,950"),
        "church_tax_mark": _v("ev"),
        "child_allowance_count": _v("1.0", "1,0"),
        "gross": _v("60000.00", "60.000,00"),
        "income_tax": _v("9192.00", "9.192,00"),
        "pension_employer": _v("5580.00", "5.580,00"),
        "pension_employee": _v("5580.00", "5.580,00"),
        "health_employee": _v("4890.00", "4.890,00"),
        "care_employee": _v("1080.00", "1.080,00"),
        "unemployment_employee": _v("780.00", "780,00"),
    }
    values.update(overrides)
    return WageCertificateExtraction(**values)


def _payslip(**overrides) -> PayslipExtraction:
    values = {
        "employee_name": _v("Erika Musterfrau"),
        "employer_name": _v("Muster GmbH"),
        "period_month": _v("2026-07-01", "Juli 2026"),
        "tax_class": _v("IV"),
        "gross_month": _v("5000.00", "5.000,00"),
        "gross_ytd": _v("35000.00", "35.000,00"),
        "income_tax_month": _v("766.00", "766,00"),
        "income_tax_ytd": _v("5362.00", "5.362,00"),
    }
    values.update(overrides)
    return PayslipExtraction(**values)


def _by_key(records: list[ProposalRecord]) -> dict[str, ProposalRecord]:
    by_key = {r.key: r for r in records}
    assert len(by_key) == len(records)
    return by_key


def _cert_records(extraction=None, **context) -> dict[str, ProposalRecord]:
    return _by_key(
        build_proposal(
            ExtractionKind.LOHNSTEUERBESCHEINIGUNG,
            extraction or _certificate(),
            _context(DocumentType.LOHNSTEUERBESCHEINIGUNG, **context),
        )
    )


def _slip_records(extraction=None, **context) -> dict[str, ProposalRecord]:
    return _by_key(
        build_proposal(
            ExtractionKind.GEHALTSABRECHNUNG,
            extraction or _payslip(),
            _context(DocumentType.GEHALTSABRECHNUNG, **context),
        )
    )


def _values(record: ProposalRecord) -> dict:
    return {name: value.value for name, value in record.fields.items()}


# --- The certificate ----------------------------------------------------------------------


def test_a_certificate_proposes_the_owner_link_and_every_certificate_field():
    records = _cert_records()
    assert set(records) == {"typing", "link:owner", "return_input:wage_certificate"}
    assert records["link:owner"].target_id == OWNER_A.id
    row = records["return_input:wage_certificate"]
    assert (row.entity_type, row.mode, row.target_id) == ("return_input", "create", None)
    expected = {"owner_id", "tax_year"} | {
        f"value.{name}"
        for name in WageCertificate.model_fields
        if name != "unread"  # computed at apply, never proposed (Q-O29)
    }
    assert set(row.fields) == expected
    values = _values(row)
    assert values["owner_id"] == str(OWNER_A.id)
    assert values["tax_year"] == 2026
    assert values["value.tax_class"] == 4
    assert values["value.tax_class_factor"] is True
    assert values["value.church_tax_marked"] is True
    assert values["value.gross"] == "60000.00"
    assert values["value.employer"] == "Muster GmbH"
    assert records["typing"].fields["tax_year"].value == 2026


def test_every_read_value_keeps_its_page_and_quote():
    row = _cert_records()["return_input:wage_certificate"]
    gross = row.fields["value.gross"]
    assert gross.source == SourceRef(page=1, quote="60.000,00")
    assert gross.confidence == "high"
    owner = row.fields["owner_id"]
    assert owner.derived_from == ["employee_name"] and "derived" in owner.checks


@pytest.mark.parametrize("name", CERTIFICATE_AMOUNTS)
def test_q_o29_a_line_not_read_is_proposed_empty_and_lands_in_unread(name):
    """Q-O29 (a): every certificate line the model did not read is proposed as an empty
    field (never 0), named in the note by its line, and, left empty by the user, becomes 0
    named in `unread` on the row: never a silent 0."""
    # Every other line read; 15a at 0, since it is part of 15 (the schema checks 15a <= 15).
    read = {n: _v("0.00" if n == "short_time_allowance" else "10.00") for n in CERTIFICATE_AMOUNTS}
    read[name] = ExtractedValue.missing()
    extraction = _certificate(**read)
    row = _cert_records(extraction)["return_input:wage_certificate"]
    proposed = row.fields[f"value.{name}"]
    assert proposed.value is None, name
    assert any(CERTIFICATE_LINES[name] in note for note in row.notes), name
    confirmed = {key: value.value for key, value in row.fields.items()}
    value = return_input_value("wage_certificate", confirmed)
    assert value["unread"] == [name]
    assert value[name] == "0"
    certificate = WageCertificate.model_validate(value)
    assert certificate.unread == [name]
    assert getattr(certificate, name) == Decimal("0")


def test_q_o29_nothing_read_means_every_line_is_unread():
    extraction = _certificate(**{n: ExtractedValue.missing() for n in CERTIFICATE_AMOUNTS})
    row = _cert_records(extraction)["return_input:wage_certificate"]
    value = return_input_value("wage_certificate", _values(row))
    assert value["unread"] == list(CERTIFICATE_AMOUNTS)
    assert WageCertificate.model_validate(value).unread == list(CERTIFICATE_AMOUNTS)


def test_a_line_the_user_types_is_no_longer_unread_and_a_given_unread_is_ignored():
    row = _cert_records()["return_input:wage_certificate"]
    confirmed = _values(row) | {"value.soli": "0.00", "value.unread": ["gross"]}
    value = return_input_value("wage_certificate", confirmed)
    assert "soli" not in value["unread"]
    assert "gross" not in value["unread"]  # read and confirmed: the given list is ignored
    assert set(value["unread"]) == {
        n for n in CERTIFICATE_AMOUNTS if row.fields[f"value.{n}"].value is None and n != "soli"
    }


def test_all_read_means_no_unread_note():
    extraction = _certificate(**{n: _v("1.00") for n in CERTIFICATE_AMOUNTS})
    row = _cert_records(extraction)["return_input:wage_certificate"]
    assert not any(note.startswith("Nicht gelesen") for note in row.notes)
    assert return_input_value("wage_certificate", _values(row))["unread"] == []


def test_the_tax_year_comes_from_the_period_when_not_stated():
    extraction = _certificate(tax_year=ExtractedValue.missing())
    records = _cert_records(extraction)
    year = records["return_input:wage_certificate"].fields["tax_year"]
    assert year.value == 2026 and year.derived_from == ["period_from"]
    assert records["typing"].fields["tax_year"].value == 2026


def test_a_certificate_already_taken_over_or_of_the_same_employer_gets_a_note():
    row_same_doc = ExistingPayRow(
        id=UUID(int=7),
        owner_id=OWNER_A.id,
        kind="wage_certificate",
        tax_year=2026,
        employer="Muster GmbH",
        document_id=DOC,
    )
    notes = _cert_records(pay_rows=[row_same_doc])["return_input:wage_certificate"].notes
    assert any("schon übernommen" in n for n in notes)
    other_doc = row_same_doc.model_copy(update={"document_id": UUID(int=8)})
    notes = _cert_records(pay_rows=[other_doc])["return_input:wage_certificate"].notes
    assert any("berichtigte" in n for n in notes)
    other_owner = other_doc.model_copy(update={"owner_id": OWNER_B.id})
    notes = _cert_records(pay_rows=[other_owner])["return_input:wage_certificate"].notes
    assert not any("berichtigte" in n or "schon übernommen" in n for n in notes)


# --- The owner match -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("printed", "owner", "confidence"),
    [
        ("Erika Musterfrau", OWNER_A, "high"),
        ("Musterfrau, Erika", OWNER_A, "high"),
        ("Frau Dr. Erika Musterfrau", OWNER_A, "high"),
        ("ERIKA MUSTERFRAU", OWNER_A, "high"),
        ("Erika Maria Musterfrau", OWNER_A, "medium"),  # a middle name printed
        ("Max Mustermann", OWNER_B, "high"),
        ("Erika Beispiel", None, "low"),
        ("Erika", None, "low"),  # a first name alone is no match
    ],
)
def test_the_owner_is_matched_by_name_tokens(printed, owner, confidence):
    records = _cert_records(_certificate(employee_name=_v(printed)))
    target = records["link:owner"].target_id
    assert target == (None if owner is None else owner.id)
    owner_id = records["return_input:wage_certificate"].fields["owner_id"]
    assert owner_id.value == (None if owner is None else str(owner.id))
    assert owner_id.confidence == confidence


def test_the_owner_is_only_ever_one_of_the_contexts_owners():
    """prefill_context lists the workspace's own owners only (RLS); a name matching nobody
    there links nobody, whatever another workspace holds."""
    records = _cert_records(owners=[OWNER_B])
    assert records["link:owner"].target_id is None
    assert records["return_input:wage_certificate"].fields["owner_id"].value is None


def test_two_owners_of_the_same_name_link_nobody():
    twin = ExistingOwner(id=UUID(int=99), full_name="Erika Musterfrau")
    records = _cert_records(owners=[OWNER_A, twin])
    assert records["link:owner"].target_id is None


def test_no_employee_name_links_nobody():
    records = _cert_records(_certificate(employee_name=ExtractedValue.missing()))
    assert records["link:owner"].target_id is None


# --- The payslip ---------------------------------------------------------------------------


def test_a_payslip_proposes_month_and_ytd_pairs_and_the_remaining_months():
    records = _slip_records()
    assert set(records) == {"typing", "link:owner", "return_input:payslip"}
    row = records["return_input:payslip"]
    expected = {
        "owner_id",
        "tax_year",
        "value.period_month",
        "value.employer",
        "value.tax_class",
        "value.tax_class_factor",
        "value.months_remaining_paid",
    } | {f"value.{f}.{part}" for f in PAYSLIP_FIGURES for part in ("month", "ytd")}
    assert set(row.fields) == expected
    values = _values(row)
    assert values["value.period_month"] == "2026-07-01"
    assert values["value.months_remaining_paid"] == 5
    assert row.fields["value.months_remaining_paid"].derived_from == ["period_month"]
    assert values["tax_year"] == 2026
    assert values["value.tax_class"] == 4 and values["value.tax_class_factor"] is False
    assert values["value.gross.month"] == "5000.00" and values["value.gross.ytd"] == "35000.00"
    assert values["value.soli.month"] is None
    assert records["typing"].fields["tax_year"].value == 2026


def test_the_payslip_note_shows_the_projection_and_when_to_change_the_months():
    """payslip.yaml july_projection: ytd 35,000.00 + 5 × 5,000.00 = 60,000.00."""
    notes = _slip_records()["return_input:payslip"].notes
    (note,) = [n for n in notes if "Hochrechnung" in n]
    assert "Hochrechnung 2026: 60.000,00 €" in note
    assert "ändern, wenn das Gehalt vor Dezember endet (Mutterschutz, Jobwechsel)" in note


def test_without_a_ytd_the_note_still_names_the_months():
    notes = _slip_records(_payslip(gross_ytd=ExtractedValue.missing()))["return_input:payslip"]
    assert not any("Hochrechnung" in n for n in notes.notes)
    assert any("Mutterschutz, Jobwechsel" in n for n in notes.notes)


def test_a_payslip_value_validates_and_an_unread_month_figure_is_0():
    row = _slip_records()["return_input:payslip"]
    value = return_input_value("payslip", _values(row))
    assert "unread" not in value
    slip = Payslip.model_validate(value)
    assert slip.gross.month == Decimal("5000.00") and slip.gross.ytd == Decimal("35000.00")
    assert slip.soli.month == Decimal("0") and slip.soli.ytd is None
    assert slip.months_remaining_paid == 5


def test_a_payslip_of_the_same_owner_employer_and_month_is_replaced():
    existing = ExistingPayRow(
        id=UUID(int=5),
        owner_id=OWNER_A.id,
        kind="payslip",
        tax_year=2026,
        employer="Muster GmbH",
        period_month="2026-07-01",
        document_id=UUID(int=6),
    )
    notes = _slip_records(pay_rows=[existing])["return_input:payslip"].notes
    assert any("Juli 2026" in n and "ersetzt" in n for n in notes)
    june = existing.model_copy(update={"period_month": "2026-06-01"})
    notes = _slip_records(pay_rows=[june])["return_input:payslip"].notes
    assert not any("ersetzt" in n for n in notes)


def test_a_payslip_month_written_without_a_day_is_the_first_of_the_month():
    row = _slip_records(_payslip(period_month=_v("2026-08-15")))["return_input:payslip"]
    assert row.fields["value.period_month"].value == "2026-08-01"
    assert row.fields["value.months_remaining_paid"].value == 4


# --- The small parsers ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "parts"),
    [
        ("4 / 0,950", (4, True)),
        ("IV / 0,950", (4, True)),
        ("Steuerklasse/Faktor 4/0.95", (4, True)),
        ("IV", (4, False)),
        ("4", (4, False)),
        ("StKl 1", (1, False)),
        ("Steuerklasse III", (3, False)),
        ("1 / 0,950", (1, False)),  # a Faktor stands beside IV only
        ("VI", (6, False)),
        ("unbekannt", None),
    ],
)
def test_the_tax_class_and_its_faktor(text, parts):
    assert _tax_class_parts(text) == parts


@pytest.mark.parametrize(
    ("text", "marked"),
    [
        ("ev", True),
        ("rk", True),
        ("ev/rk", True),
        ("RK", True),
        ("--", False),
        ("-", False),
        ("keine", False),
        ("", False),
        ("Kirchensteuerpflicht unklar", None),
    ],
)
def test_the_church_tax_mark(text, marked):
    assert _church_tax_marked(text) is marked


@pytest.mark.parametrize(
    ("text", "first"),
    [
        ("2026-07-01", "2026-07-01"),
        ("2026-07-31", "2026-07-01"),
        ("07/2026", "2026-07-01"),
        ("7.2026", "2026-07-01"),
        ("Juli 2026", "2026-07-01"),
        ("März 2026", "2026-03-01"),
        ("13/2026", None),
        ("Abrechnung", None),
    ],
)
def test_the_payslip_month(text, first):
    assert _month_start(text) == first


def test_return_input_value_nests_dotted_keys_and_drops_other_keys():
    value = return_input_value(
        "payslip",
        {
            "owner_id": "x",
            "tax_year": 2026,
            "value.employer": "Muster GmbH",
            "value.gross.month": "1.00",
            "value.gross.ytd": None,
        },
    )
    assert value == {"employer": "Muster GmbH", "gross": {"month": "1.00"}}


def test_return_input_value_refuses_a_field_given_whole_and_by_part():
    with pytest.raises(ValueError, match="both whole and by part"):
        return_input_value("payslip", {"value.gross": {"month": "1"}, "value.gross.month": "2"})
