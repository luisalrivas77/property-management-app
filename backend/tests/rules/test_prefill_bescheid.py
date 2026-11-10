"""Phase 4b step 6: the pre-fill records of an Einkommensteuerbescheid (docs/phases/phase-4b.md
D129, §8.4): typing and income_input:bescheid_zve, the note when it replaces an earlier
Bescheid, and the two warnings. All names and figures are invented."""

from datetime import date
from uuid import UUID

import pytest

from hausbuch.rules.prefill import build_proposal
from hausbuch.schemas.ai import ClassifyOutput, ExtractedValue, SourceRef
from hausbuch.schemas.enums import DocumentSubtype, DocumentType, ExtractionKind
from hausbuch.schemas.extraction import IncomeTaxAssessmentExtraction
from hausbuch.schemas.proposal import (
    ExistingBescheidZve,
    ExistingOwner,
    PrefillContext,
    ProposalRecord,
)

DOC = UUID("00000000-0000-0000-0000-00000000d0c1")
EARLIER = UUID("00000000-0000-0000-0000-00000000d0c0")
ROW = UUID("00000000-0000-0000-0000-0000000001e1")
KIND = ExtractionKind.EINKOMMENSTEUERBESCHEID
OWNER_A = ExistingOwner(id=UUID("00000000-0000-0000-0000-0000000000a1"), full_name="Erika Muster")
OWNER_B = ExistingOwner(id=UUID("00000000-0000-0000-0000-0000000000b2"), full_name="Max Muster")


def _v(value, quote: str | None = None, page: int = 1) -> ExtractedValue:
    return ExtractedValue(
        value=value,
        source=SourceRef(page=page, quote=quote or str(value)),
        checks=["cited", "value_in_quote", "validates"],
        confidence="high",
    )


def _context(**update) -> PrefillContext:
    classification = ClassifyOutput(
        type=DocumentType.BESCHEID,
        subtype=DocumentSubtype.EINKOMMENSTEUERBESCHEID,
        contains_tenant_data=False,
        language="de",
        confidence="high",
    )
    return PrefillContext(
        document_id=DOC, classification=classification, **({"owners": [OWNER_A, OWNER_B]} | update)
    )


def _bescheid(**overrides) -> IncomeTaxAssessmentExtraction:
    values = {
        "title": _v("Bescheid für 2026 über Einkommensteuer"),
        "document_date": _v("2027-05-12", "12.05.2027"),
        "tax_year": _v(2026, "Bescheid für 2026"),
        "taxable_income": _v("84300.00", "zu versteuerndes Einkommen 84.300", page=2),
        "assessment_type": _v("Zusammenveranlagung"),
        "addressee": _v("Max und Erika Muster"),
    }
    values.update(overrides)
    return IncomeTaxAssessmentExtraction(**values)


def _records(extraction=None, **context) -> dict[str, ProposalRecord]:
    records = build_proposal(KIND, extraction or _bescheid(), _context(**context))
    return {r.key: r for r in records}


def test_a_bescheid_proposes_its_typing_and_the_years_zve():
    records = _records()
    assert list(records) == ["typing", "income_input:bescheid_zve"]
    typing = records["typing"].fields
    assert typing["subtype"].value == "einkommensteuerbescheid"
    assert typing["tax_year"].value == 2026
    assert typing["document_date"].value == "2027-05-12"
    zve = records["income_input:bescheid_zve"]
    assert zve.entity_type == "income_input" and zve.mode == "create"
    assert set(zve.fields) == {"tax_year", "value.amount"}
    assert zve.fields["tax_year"].value == 2026
    amount = zve.fields["value.amount"]
    assert amount.value == "84300.00"
    assert amount.source is not None and amount.source.page == 2
    assert "84.300" in amount.source.quote
    assert zve.notes == []  # joint, addressed to the owners, the first Bescheid of the year


def test_an_unread_zve_is_proposed_empty_with_a_note():
    zve = _records(_bescheid(taxable_income=ExtractedValue.missing()))["income_input:bescheid_zve"]
    assert zve.fields["value.amount"].value is None
    assert any("nicht gelesen" in n for n in zve.notes)


def test_a_second_bescheid_for_the_year_replaces_the_first_with_its_date():
    row = ExistingBescheidZve(
        id=ROW, tax_year=2026, document_id=EARLIER, document_date=date(2027, 4, 2)
    )
    notes = _records(bescheid_rows=[row])["income_input:bescheid_zve"].notes
    assert any("ersetzt den Bescheid vom 02.04.2027" in n for n in notes)


def test_a_replaced_bescheid_without_a_date_is_still_named():
    row = ExistingBescheidZve(id=ROW, tax_year=2026, document_id=EARLIER)
    notes = _records(bescheid_rows=[row])["income_input:bescheid_zve"].notes
    assert any("ersetzt den bisher übernommenen Bescheid" in n for n in notes)


def test_a_row_of_another_year_is_not_replaced():
    row = ExistingBescheidZve(
        id=ROW, tax_year=2025, document_id=EARLIER, document_date=date(2026, 4, 2)
    )
    assert _records(bescheid_rows=[row])["income_input:bescheid_zve"].notes == []


def test_this_bescheid_already_recorded():
    same_year = ExistingBescheidZve(id=ROW, tax_year=2026, document_id=DOC)
    notes = _records(bescheid_rows=[same_year])["income_input:bescheid_zve"].notes
    assert any("schon übernommen" in n for n in notes)
    other_year = ExistingBescheidZve(id=ROW, tax_year=2025, document_id=DOC)
    notes = _records(bescheid_rows=[other_year])["income_input:bescheid_zve"].notes
    assert any("schon als zvE für 2025 übernommen" in n for n in notes)


@pytest.mark.parametrize(
    "text", ["Zusammenveranlagung", "zusammen veranlagt", "Zusammen-Veranlagung (Splitting)"]
)
def test_a_joint_assessment_gives_no_warning(text):
    records = _records(_bescheid(assessment_type=_v(text)))
    assert records["income_input:bescheid_zve"].notes == []


@pytest.mark.parametrize("text", ["Einzelveranlagung", "getrennte Veranlagung"])
def test_another_assessment_warns_but_never_blocks(text):
    zve = _records(_bescheid(assessment_type=_v(text)))["income_input:bescheid_zve"]
    assert any("Splittingtarif" in n for n in zve.notes)
    assert zve.fields["value.amount"].value == "84300.00"


def test_an_unread_assessment_type_gives_no_warning():
    zve = _records(_bescheid(assessment_type=ExtractedValue.missing()))
    assert zve["income_input:bescheid_zve"].notes == []


@pytest.mark.parametrize(
    "addressee",
    ["Max und Erika Muster", "Erika Muster", "Herrn Max Muster", "Muster, Max", "Dr. Erika Muster"],
)
def test_an_addressee_naming_an_owner_gives_no_warning(addressee):
    records = _records(_bescheid(addressee=_v(addressee)))
    assert records["income_input:bescheid_zve"].notes == []


@pytest.mark.parametrize("addressee", ["Jonas Beispiel", "Erika Beispiel", "Familie Beispiel"])
def test_an_addressee_naming_neither_owner_warns_without_the_name(addressee):
    zve = _records(_bescheid(addressee=_v(addressee)))["income_input:bescheid_zve"]
    (note,) = zve.notes
    assert "nicht an die Eigentümer gerichtet" in note
    assert "Beispiel" not in note and "Jonas" not in note and "Erika" not in note


def test_no_owners_yet_or_no_addressee_read_gives_no_warning():
    assert _records(owners=[])["income_input:bescheid_zve"].notes == []
    zve = _records(_bescheid(addressee=ExtractedValue.missing()))
    assert zve["income_input:bescheid_zve"].notes == []


def test_no_note_quotes_a_name_or_the_assessment_text():
    row = ExistingBescheidZve(id=ROW, tax_year=2026, document_id=EARLIER)
    zve = _records(
        _bescheid(assessment_type=_v("Einzelveranlagung"), addressee=_v("Jonas Beispiel")),
        bescheid_rows=[row],
    )["income_input:bescheid_zve"]
    assert len(zve.notes) == 3
    text = " ".join(zve.notes)
    for name in ("Jonas", "Beispiel", "Erika", "Max", "Muster", "Einzelveranlagung"):
        assert name not in text
