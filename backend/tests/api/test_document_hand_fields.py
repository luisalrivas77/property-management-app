"""The hand-entry fields over an AI reading (phase 2 D37), as pure functions: no database.

services.documents.hand_fields is what GET /documents/{id} returns as `hand_fields`;
hand_fields_into is what PUT /documents/{id}/typing writes when the current extraction is an
AI kind. The database round trip is in tests/db/test_services_documents.py.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from hausbuch.rules.errors import RuleError
from hausbuch.schemas.ai import ExtractedValue, SourceRef
from hausbuch.schemas.document import DocumentExtraction, ManualDocumentFields
from hausbuch.schemas.enums import ExtractionKind
from hausbuch.schemas.extraction import (
    EXTRACTION_MODELS,
    BescheidExtraction,
    DepositRecordExtraction,
    GrundsteuerbescheidExtraction,
    InvoiceExtraction,
    LoanContractExtraction,
    StatementExtraction,
    ZinsbescheinigungExtraction,
    value_kind,
)
from hausbuch.services.documents import (
    EXTRACTION_KIND_MISMATCH,
    HAND_FIELD_MAP,
    hand_fields,
    hand_fields_into,
)

ALL_SIX = ["amount_total", "period_start", "period_end", "counterparty", "reference", "notes"]


def _cited(value) -> ExtractedValue:
    return ExtractedValue(
        value=value,
        source=SourceRef(page=1, quote="…"),
        checks=["cited", "value_in_quote", "validates"],
        confidence="high",
    )


def _extraction(kind: ExtractionKind, fields) -> DocumentExtraction:
    return DocumentExtraction(
        id=uuid4(),
        workspace_id=uuid4(),
        document_id=uuid4(),
        version=1,
        source="ai",
        fields=fields,
        created_at=datetime(2026, 11, 7, tzinfo=UTC),
        kind=kind,
        ai_call_id=uuid4(),
    )


INVOICE = InvoiceExtraction(
    sender=_cited("Notar Dr. Beispiel"),
    subject=_cited("Beurkundung Kaufvertrag"),
    amount_total=_cited("4321.10"),
    amount_net=_cited("3631.18"),
    reference=_cited("UR 123/2024"),
    iban=_cited("DE89370400440532013000"),
)
INVOICE_SHOWN = ManualDocumentFields(
    amount_total=Decimal("4321.10"),
    counterparty="Notar Dr. Beispiel",
    reference="UR 123/2024",
    notes="Beurkundung Kaufvertrag",
)


# The value kind a hand field's target must hold: what services.documents._stored writes.
HAND_VALUE_KIND = {"amount_total": "money", "period_start": "date", "period_end": "date"}


def test_the_map_names_only_fields_the_kinds_have():
    for kind, mapping in HAND_FIELD_MAP.items():
        model = EXTRACTION_MODELS[kind]
        assert set(mapping) <= set(ALL_SIX)
        assert set(mapping.values()) <= set(model.model_fields), kind


def test_each_map_target_holds_the_value_kind_of_its_hand_field():
    # _stored knows money, date and text only: a target of another kind (an iban, a year)
    # would fail on the first save, so it must fail here first.
    for kind, mapping in HAND_FIELD_MAP.items():
        model = EXTRACTION_MODELS[kind]
        for hand, target in mapping.items():
            expected = HAND_VALUE_KIND.get(hand, "text")
            assert value_kind(model, target) == expected, (kind, hand, target)


@pytest.mark.parametrize(
    ("kind", "reading", "shown"),
    [
        (ExtractionKind.INVOICE, INVOICE, INVOICE_SHOWN),
        (
            ExtractionKind.BESCHEID,
            BescheidExtraction(
                sender=_cited("Finanzamt Mitte"),
                amount_total=_cited("17850.00"),
                reference=_cited("St.-Nr. 12/345"),
            ),
            ManualDocumentFields(
                amount_total=Decimal("17850.00"),
                counterparty="Finanzamt Mitte",
                reference="St.-Nr. 12/345",
            ),
        ),
        (
            ExtractionKind.DEPOSIT_RECORD,
            DepositRecordExtraction(amount=_cited("2400.00")),
            ManualDocumentFields(amount_total=Decimal("2400.00")),
        ),
        (
            ExtractionKind.ZINSBESCHEINIGUNG,
            ZinsbescheinigungExtraction(
                lender=_cited("Sparkasse"),
                contract_number=_cited("DL-77"),
                interest_paid=_cited("6123.45"),
            ),
            ManualDocumentFields(
                amount_total=Decimal("6123.45"), counterparty="Sparkasse", reference="DL-77"
            ),
        ),
        (
            ExtractionKind.LOAN_CONTRACT,
            LoanContractExtraction(lender=_cited("Sparkasse"), contract_number=_cited("DL-77")),
            ManualDocumentFields(counterparty="Sparkasse", reference="DL-77"),
        ),
        (
            ExtractionKind.GRUNDSTEUERBESCHEID,
            GrundsteuerbescheidExtraction(reference=_cited("GS-1")),
            ManualDocumentFields(reference="GS-1"),
        ),
        (
            ExtractionKind.STATEMENT,
            StatementExtraction(period_start=_cited("2026-10-01"), period_end=_cited("2026-10-31")),
            ManualDocumentFields(period_start=date(2026, 10, 1), period_end=date(2026, 10, 31)),
        ),
    ],
)
def test_each_kind_shows_its_mapped_fields(kind, reading, shown):
    view = hand_fields(_extraction(kind, reading))
    assert view.values == shown
    assert view.available == [name for name in ALL_SIX if name in HAND_FIELD_MAP[kind]]
    assert hand_fields_into(kind, reading, shown) is reading  # shown back unchanged


@pytest.mark.parametrize(
    "kind",
    [
        ExtractionKind.PURCHASE_CONTRACT,
        ExtractionKind.LEASE,
        ExtractionKind.HANDOVER_PROTOCOL,
        ExtractionKind.WIRTSCHAFTSPLAN,
        ExtractionKind.HAUSGELDABRECHNUNG,
    ],
)
def test_a_kind_without_hand_fields_shows_none_and_refuses_any(kind):
    reading = EXTRACTION_MODELS[kind]()
    view = hand_fields(_extraction(kind, reading))
    assert (view.values, view.available) == (ManualDocumentFields(), [])
    assert hand_fields_into(kind, reading, ManualDocumentFields()) is reading
    with pytest.raises(RuleError) as caught:
        hand_fields_into(kind, reading, ManualDocumentFields(notes="x"))
    assert caught.value.code == EXTRACTION_KIND_MISMATCH


def test_no_extraction_and_a_manual_one_show_all_six():
    assert hand_fields(None).available == ALL_SIX
    assert hand_fields(None).values == ManualDocumentFields()
    manual = ManualDocumentFields(amount_total=Decimal("412.30"), notes="Wartung")
    extraction = DocumentExtraction(
        id=uuid4(),
        workspace_id=uuid4(),
        document_id=uuid4(),
        version=1,
        source="manual",
        fields=manual,
        created_at=datetime(2026, 11, 7, tzinfo=UTC),
    )
    assert (hand_fields(extraction).values, hand_fields(extraction).available) == (manual, ALL_SIX)


def test_a_changed_field_is_the_typed_value_and_the_rest_stay_as_read():
    typed = INVOICE_SHOWN.model_copy(
        update={"amount_total": Decimal("4300"), "counterparty": "Notar  Dr. Muster "}
    )
    written = hand_fields_into(ExtractionKind.INVOICE, INVOICE, typed)
    assert type(written) is InvoiceExtraction
    assert written.amount_total == ExtractedValue(
        value="4300.00", checks=["validates"], confidence="high"
    )
    assert written.sender == ExtractedValue(
        value="Notar Dr. Muster", checks=["validates"], confidence="high"
    )
    for name in ("subject", "amount_net", "reference", "iban", "title", "document_date"):
        assert getattr(written, name) is getattr(INVOICE, name)


def test_typing_the_same_text_with_other_spacing_is_no_change():
    typed = INVOICE_SHOWN.model_copy(update={"counterparty": "  Notar   Dr. Beispiel"})
    assert hand_fields_into(ExtractionKind.INVOICE, INVOICE, typed) is INVOICE


def test_clearing_a_shown_field_makes_it_missing():
    typed = INVOICE_SHOWN.model_copy(update={"notes": None})
    written = hand_fields_into(ExtractionKind.INVOICE, INVOICE, typed)
    assert written.subject == ExtractedValue.missing()
    assert written.sender is INVOICE.sender


def test_six_empty_hand_fields_over_a_reading_clear_only_the_mapped_fields():
    written = hand_fields_into(ExtractionKind.INVOICE, INVOICE, ManualDocumentFields())
    missing = ExtractedValue.missing()
    assert (written.amount_total, written.sender, written.reference, written.subject) == (
        missing,
        missing,
        missing,
        missing,
    )
    assert (written.amount_net, written.iban) == (INVOICE.amount_net, INVOICE.iban)


def test_a_non_null_field_the_kind_has_no_place_for_is_refused():
    typed = INVOICE_SHOWN.model_copy(update={"period_end": date(2024, 3, 31)})
    with pytest.raises(RuleError) as caught:
        hand_fields_into(ExtractionKind.INVOICE, INVOICE, typed)
    assert caught.value.code == "document.extraction_kind_mismatch"


def test_a_reading_the_hand_types_cannot_hold_shows_empty_and_is_kept():
    # A low-confidence reading keeps the raw text (rules.extraction_parse): not a Money value.
    raw = ExtractedValue(value="ca. 400 Euro", confidence="low")
    reading = INVOICE.model_copy(update={"amount_total": raw})
    view = hand_fields(_extraction(ExtractionKind.INVOICE, reading))
    assert view.values.amount_total is None
    assert "amount_total" in view.available
    edited = view.values.model_copy(update={"reference": "UR 124/2024"})
    written = hand_fields_into(ExtractionKind.INVOICE, reading, edited)
    assert written.amount_total is raw
    assert written.reference.value == "UR 124/2024"


def test_a_statement_period_out_of_order_shows_neither_end():
    reading = StatementExtraction(
        period_start=_cited("2026-10-31"), period_end=_cited("2026-10-01")
    )
    view = hand_fields(_extraction(ExtractionKind.STATEMENT, reading))
    assert (view.values.period_start, view.values.period_end) == (None, None)
    assert hand_fields_into(ExtractionKind.STATEMENT, reading, view.values) is reading
