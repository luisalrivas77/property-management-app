"""Phase 2 D138: a financing bill's typing proposes the subtype financing_invoice, set by code,
with the confidence of the text that made it a financing bill. The cases with every value read
at high confidence are in prefill.yaml; these vary the confidence. Invented figures."""

from uuid import UUID

import pytest

from hausbuch.rules.prefill import build_proposal
from hausbuch.schemas.ai import ClassifyOutput, ExtractedValue, SourceRef
from hausbuch.schemas.enums import DocumentSubtype, DocumentType, ExtractionKind
from hausbuch.schemas.extraction import BescheidExtraction, InvoiceExtraction
from hausbuch.schemas.proposal import PrefillContext

DOC = UUID("00000000-0000-0000-0000-00000000d0c2")


def _v(value, confidence="high") -> ExtractedValue:
    return ExtractedValue(
        value=value,
        source=SourceRef(page=1, quote=str(value)),
        checks=["cited", "value_in_quote", "validates"],
        confidence=confidence,
    )


def _context(type_=DocumentType.INVOICE, subtype=DocumentSubtype.COST_BASIS_EVIDENCE):
    classification = ClassifyOutput(
        type=type_, subtype=subtype, contains_tenant_data=False, language="de", confidence="low"
    )
    return PrefillContext(document_id=DOC, classification=classification)


def _subtype(kind, extraction, context=None) -> ExtractedValue | None:
    records = build_proposal(kind, extraction, context or _context())
    typing = next(r for r in records if r.key == "typing")
    return typing.fields.get("subtype")


def _invoice(**values) -> InvoiceExtraction:
    base = {
        "document_date": _v("2026-03-20"),
        "sender": _v("Notarin Beispiel"),
        "amount_total": _v("238.00"),
    }
    return InvoiceExtraction(**(base | values))


@pytest.mark.parametrize(
    ("subject", "title", "expected"),
    [
        (_v("Grundschuldbestellung", "high"), _v("Notarrechnung", "low"), "high"),
        (_v("Kostenrechnung", "high"), _v("Notarrechnung Grundschuld", "medium"), "medium"),
        (_v("Grundschuldbestellung", "medium"), _v("Grundschuld", "low"), "medium"),
    ],
)
def test_the_confidence_is_the_named_texts(subject, title, expected):
    got = _subtype(ExtractionKind.INVOICE, _invoice(subject=subject, title=title))
    assert got is not None
    assert got.value == "financing_invoice"
    assert got.source is None and got.checks == ["validates"]
    assert got.confidence == expected  # not the classifier's low


def test_a_bescheid_is_decided_by_its_title():
    extraction = BescheidExtraction(
        title=_v("Kostenrechnung Grundschuldeintragung", "medium"),
        document_date=_v("2026-04-10"),
        amount_total=_v("250.00"),
    )
    got = _subtype(ExtractionKind.BESCHEID, extraction, _context(DocumentType.BESCHEID))
    assert got is not None and got.value == "financing_invoice"
    assert got.confidence == "medium"


def test_lines_that_are_all_loan_costs_give_their_confidence():
    """Neither subject nor title names a financing term, but every line does: the lines made
    it a financing bill (sort_bill), so their confidence is the subtype's."""
    lines = ExtractedValue(
        value=[
            "Grundschuldbestellung | 200,00 € | acquisition_cost",
            "Umsatzsteuer | 38,00 € | vat",
        ],
        source=SourceRef(page=1, quote="Grundschuldbestellung 200,00 € Umsatzsteuer 38,00 €"),
        checks=["cited", "validates"],
        confidence="medium",
    )
    extraction = _invoice(subject=_v("Kostenrechnung"), title=_v("Notarrechnung"), cost_lines=lines)
    got = _subtype(ExtractionKind.INVOICE, extraction)
    assert got is not None and got.value == "financing_invoice"
    assert got.confidence == "medium"


def test_a_bill_naming_no_financing_term_keeps_the_classifiers_subtype():
    extraction = _invoice(subject=_v("Beurkundung Kaufvertrag"), title=_v("Notarrechnung Kauf"))
    got = _subtype(ExtractionKind.INVOICE, extraction)
    assert got is not None
    assert got.value == "cost_basis_evidence"
    assert got.confidence == "low"
