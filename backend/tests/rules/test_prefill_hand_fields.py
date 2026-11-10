"""D132 (one pre-filled form): the typing record carries the document's hand-entry fields.

prefill.yaml pins the records per case; these pin the rule's edges: a value computed by
code keeps its derived_from and no source, a raw text the hand field cannot hold is
proposed empty with its quote, and a hand field whose reading field another record
proposes (or a new record's card asks for) is left out, so no value is asked twice.
"""

from decimal import Decimal
from uuid import uuid4

import pytest

from hausbuch.rules.extraction_parse import derive_fields
from hausbuch.rules.prefill import build_proposal
from hausbuch.schemas.ai import ClassifyOutput, ExtractedValue, SourceRef
from hausbuch.schemas.document import HandFields, ManualDocumentFields
from hausbuch.schemas.enums import DocumentSubtype, DocumentType, ExtractionKind
from hausbuch.schemas.extraction import (
    DepositRecordExtraction,
    LoanContractExtraction,
    ManagementContractExtraction,
    PurchaseContractExtraction,
    ZinsbescheinigungExtraction,
    _Typing,
)
from hausbuch.schemas.proposal import ExistingLoan, PrefillContext


def _cited(value, quote: str = "…") -> ExtractedValue:
    return ExtractedValue(
        value=value,
        source=SourceRef(page=1, quote=quote),
        checks=["cited", "value_in_quote", "validates"],
        confidence="high",
    )


def _context(type_: DocumentType, subtype: DocumentSubtype | None, **lists) -> PrefillContext:
    return PrefillContext(
        document_id=uuid4(),
        classification=ClassifyOutput(
            type=type_,
            subtype=subtype,
            contains_tenant_data=False,
            language="de",
            confidence="high",
        ),
        **lists,
    )


def _typing(records):
    (typing,) = [r for r in records if r.key == "typing"]
    return typing


@pytest.mark.rule
def test_a_management_fee_computed_by_code_is_proposed_as_computed():
    values = derive_fields(
        ExtractionKind.MANAGEMENT_CONTRACT,
        {
            "manager": _cited("Muster Hausverwaltung GmbH"),
            "fee_net": _cited("30.00", "30,00 € zzgl. MwSt."),
            "monthly_fee": _cited("30,00 € zzgl. MwSt. je Einheit"),
        },
    )
    reading = ManagementContractExtraction.model_validate(values)
    records = build_proposal(
        ExtractionKind.MANAGEMENT_CONTRACT,
        reading,
        _context(DocumentType.CONTRACT, DocumentSubtype.MANAGEMENT_CONTRACT),
    )
    fields = _typing(records).fields
    assert set(fields) >= {"amount_total", "period_start", "period_end", "counterparty"}
    amount = fields["amount_total"]
    assert amount.value == "35.70"
    assert amount.source is None
    assert amount.derived_from == ["fee_net", "monthly_fee"]
    assert "derived" in amount.checks
    assert fields["counterparty"].value == "Muster Hausverwaltung GmbH"
    # Never read: proposed empty, so the card shows a box to type it.
    assert fields["reference"].value is None
    assert fields["notes"].value is None


@pytest.mark.rule
def test_a_raw_text_the_hand_field_cannot_hold_is_proposed_empty_with_its_quote():
    quote = SourceRef(page=1, quote="Kaution ca. 3000 Euro")
    reading = DepositRecordExtraction(
        amount=ExtractedValue(
            value="ca. 3000 Euro", source=quote, checks=["cited"], confidence="low"
        ),
        received_on=_cited("2026-05-02"),
    )
    records = build_proposal(
        ExtractionKind.DEPOSIT_RECORD,
        reading,
        _context(DocumentType.LETTER, DocumentSubtype.DEPOSIT_RECORD),
    )
    assert [r.key for r in records] == ["typing"]
    amount = _typing(records).fields["amount_total"]
    assert amount.value is None
    assert amount.source == quote
    assert amount.confidence == "low"


@pytest.mark.rule
def test_a_value_another_record_proposes_is_not_asked_on_typing():
    reading = ZinsbescheinigungExtraction(
        lender=_cited("Musterbank AG"),
        interest_paid=ExtractedValue(
            value="ca. 400 Euro",
            source=SourceRef(page=1, quote="ca. 400 Euro"),
            checks=["cited"],
            confidence="low",
        ),
    )
    # Two loans, none matching: no link, but the tax input still proposes the interest.
    loans = [
        ExistingLoan(id=uuid4(), property_id=uuid4(), lender="Andere Bank"),
        ExistingLoan(id=uuid4(), property_id=uuid4(), lender="Dritte Bank"),
    ]
    records = build_proposal(
        ExtractionKind.ZINSBESCHEINIGUNG,
        reading,
        _context(DocumentType.ZINSBESCHEINIGUNG, None, loans=loans),
    )
    fields = _typing(records).fields
    # The interest is the tax input's value.amount: not asked a second time on typing.
    assert "amount_total" not in fields
    assert fields["counterparty"].value == "Musterbank AG"
    assert fields["reference"].value is None


@pytest.mark.rule
def test_a_new_loan_asks_for_lender_and_number_itself():
    reading = LoanContractExtraction(
        lender=_cited("Musterbank AG"),
        principal=_cited("200000.00"),
        nominal_rate=_cited("0.035"),
        monthly_payment=_cited("750.00"),
    )
    records = build_proposal(
        ExtractionKind.LOAN_CONTRACT, reading, _context(DocumentType.CONTRACT, None)
    )
    (loan,) = [r for r in records if r.key == "loan"]
    assert loan.mode == "create"
    assert "contract_number" not in loan.fields  # unstated, yet the new loan's card asks
    fields = _typing(records).fields
    assert "counterparty" not in fields
    assert "reference" not in fields


@pytest.mark.rule
def test_a_kind_without_hand_fields_proposes_none():
    records = build_proposal(
        ExtractionKind.PURCHASE_CONTRACT,
        PurchaseContractExtraction(),
        _context(DocumentType.CONTRACT, DocumentSubtype.PURCHASE_CONTRACT),
    )
    assert set(_typing(records).fields) == {"type", "subtype"}


@pytest.mark.rule
def test_without_a_reading_the_typing_record_carries_what_the_form_offers():
    """Typing only (kind None): the document's current hand fields (context.hand_fields),
    a stored value as a code value, the others empty; none given, none proposed."""
    section = HandFields(
        values=ManualDocumentFields(amount_total=Decimal("120.00"), notes="von Hand"),
        available=[
            "amount_total",
            "period_start",
            "period_end",
            "counterparty",
            "reference",
            "notes",
        ],
    )
    context = _context(DocumentType.LETTER, None).model_copy(update={"hand_fields": section})
    fields = _typing(build_proposal(None, _Typing(), context)).fields
    assert {name: value.value for name, value in fields.items() if name != "type"} == {
        "amount_total": "120.00",
        "period_start": None,
        "period_end": None,
        "counterparty": None,
        "reference": None,
        "notes": "von Hand",
    }
    assert fields["amount_total"].source is None
    assert set(
        _typing(build_proposal(None, _Typing(), _context(DocumentType.LETTER, None))).fields
    ) == {"type"}
