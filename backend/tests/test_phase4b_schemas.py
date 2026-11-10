"""Phase 4b step 0 schema guards (docs/phases/phase-4b.md §6, §7, D96, D110, D111): no identifier
field on the pay documents, no free text in an event payload, and (since step 2) the classifier
offering the two pay documents."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from hausbuch.ai.requests import classify_schema
from hausbuch.schemas import employment as N
from hausbuch.schemas.enums import DocumentType, ExtractionKind, ReturnInputKind
from hausbuch.schemas.events import (
    EVENT_CATALOG,
    EventType,
    PrivateCostRecorded,
    ReturnInputRecorded,
    TaxInputRecorded,
)
from hausbuch.schemas.extraction import (
    EXTRACTION_MODELS,
    PayslipExtraction,
    WageCertificateExtraction,
)
from hausbuch.schemas.tax import PrivateCostValue, TaxInputCreate, TaxInputKind

# D96: an identifier is never a field, nor named in a field's description.
FORBIDDEN = (
    "identifikationsnummer",
    "geburtsdatum",
    "personalnummer",
    "steuernummer",
    "iban",
    "sozialversicherung",
    "adresse",
)
PAY_MODELS = [
    WageCertificateExtraction,
    PayslipExtraction,
    N.WageCertificate,
    N.Payslip,
    N.MonthAndYtd,
]


@pytest.mark.parametrize("model", PAY_MODELS, ids=lambda m: m.__name__)
def test_no_identifier_field_on_a_pay_document(model):
    for name, field in model.model_fields.items():
        text = f"{name} {field.description or ''}".lower()
        for word in FORBIDDEN:
            assert word not in text, (name, word)
    assert not {"tax_id", "social_security", "birth_date", "address"} & set(model.model_fields)


def test_the_pay_documents_have_their_extraction_models():
    assert EXTRACTION_MODELS[ExtractionKind.LOHNSTEUERBESCHEINIGUNG] is WageCertificateExtraction
    assert EXTRACTION_MODELS[ExtractionKind.GEHALTSABRECHNUNG] is PayslipExtraction


def test_the_classifier_offers_every_type_with_the_pay_documents_since_step_2():
    # Step 0 kept the two pay documents out of the classify request; step 2 added their
    # extraction and let the classifier answer them (§12).
    offered = set(classify_schema()["$defs"]["DocumentType"]["enum"])
    assert offered == {t.value for t in DocumentType}
    assert {"lohnsteuerbescheinigung", "gehaltsabrechnung"} <= offered


# --- D111: a private cost's label and trip dates never reach the event log ---------------


def _private(**overrides) -> dict:
    return {
        "cost_class": "travel",
        "label": "Fahrten zur Wohnung, Herr X",
        "trips": 2,
        "km_round_trip": 60,
        "trip_dates": ["2026-03-02", "2026-05-11"],
    } | overrides


@pytest.mark.parametrize("as_model", [False, True])
def test_a_private_cost_event_carries_amounts_and_codes_only(as_model):
    value = PrivateCostValue.model_validate(_private()) if as_model else _private()
    event = TaxInputRecorded.model_validate(
        {
            "tax_input_id": uuid.uuid4(),
            "kind": "private_cost",
            "tax_year": 2026,
            "value": value,
        }
    )
    assert type(event.value) is PrivateCostRecorded
    dumped = event.model_dump(mode="json")
    assert set(dumped["value"]) == {"cost_class", "amount", "trips", "km_round_trip", "share"}
    text = event.model_dump_json()
    assert "Herr X" not in text and "2026-03-02" not in text


def test_a_private_cost_value_needs_its_class_fields():
    PrivateCostValue.model_validate({"cost_class": "postage", "label": "Porto", "amount": "12.00"})
    for bad in (
        _private(trips=None),  # travel without trips
        _private(amount="10.00"),  # travel with an amount
        {"cost_class": "postage", "label": "Porto"},  # no amount
        {"cost_class": "postage", "label": "Porto", "amount": "1.00", "trips": 2},
    ):
        with pytest.raises(ValidationError):
            PrivateCostValue.model_validate(bad)


def test_only_a_private_cost_is_carried_from_last_year():
    prop = uuid.uuid4()
    TaxInputCreate.model_validate(
        {
            "property_id": prop,
            "kind": "private_cost",
            "tax_year": 2026,
            "value": _private(),
            "carried_from_id": uuid.uuid4(),
        }
    )
    with pytest.raises(ValidationError):
        TaxInputCreate.model_validate(
            {
                "property_id": prop,
                "kind": "market_rent",
                "value": {"rent_per_m2": "10.00"},
                "carried_from_id": uuid.uuid4(),
            }
        )


# --- Return input events -----------------------------------------------------------------


def test_return_input_amounts_are_money_fields_only():
    payslip = N.Payslip.model_validate(
        {
            "period_month": "2026-03-01",
            "employer": "Arbeitgeber A",
            "tax_class": 4,
            "months_remaining_paid": 9,
            "gross": {"month": "4000.00", "ytd": "12000.00"},
            "income_tax": {"month": "500.00"},
        }
    )
    amounts = N.return_input_amounts(payslip)
    assert amounts["gross.month"] == Decimal("4000.00")
    assert amounts["gross.ytd"] == Decimal("12000.00")
    assert "income_tax.ytd" not in amounts
    assert not any("employer" in k or "period" in k for k in amounts)


def test_the_faktor_flag_never_enters_the_amounts():
    cert = N.WageCertificate.model_validate(
        {
            "period_from": "2026-01-01",
            "period_to": "2026-12-31",
            "employer": "Arbeitgeber A",
            "tax_class": 4,
            "tax_class_factor": True,
            "gross": "30000.00",
        }
    )
    amounts = N.return_input_amounts(cert)
    assert amounts["gross"] == Decimal("30000.00")
    assert not any("tax_class" in k for k in amounts)
    payslip = N.Payslip.model_validate(
        {
            "period_month": "2026-03-01",
            "employer": "Arbeitgeber A",
            "tax_class": 4,
            "tax_class_factor": True,
            "months_remaining_paid": 9,
        }
    )
    assert not any("tax_class" in k for k in N.return_input_amounts(payslip))
    with pytest.raises(ValidationError, match="Faktor"):
        N.WageCertificate.model_validate(
            {
                "period_from": "2026-01-01",
                "period_to": "2026-12-31",
                "employer": "Arbeitgeber A",
                "tax_class": 3,
                "tax_class_factor": True,
            }
        )


def test_a_child_event_carries_months_never_the_birth_date():
    event = ReturnInputRecorded(
        return_input_id=uuid.uuid4(),
        kind=ReturnInputKind.CHILD,
        tax_year=2026,
        months_in_year=7,
    )
    assert "born_on" not in event.model_dump()
    with pytest.raises(ValidationError):
        ReturnInputRecorded(
            return_input_id=uuid.uuid4(),
            kind=ReturnInputKind.TELECOM,
            tax_year=2026,
            months_in_year=3,
        )
    with pytest.raises(ValidationError):
        ReturnInputRecorded(return_input_id=uuid.uuid4(), kind=ReturnInputKind.CHILD, tax_year=2026)


@pytest.mark.parametrize(
    "event_type",
    [
        EventType.OWNER_RETURN_INPUT_RECORDED,
        EventType.OWNER_ANLAGE_N_EXPORTED,
        EventType.WORKSPACE_RETURN_INPUT_RECORDED,
        EventType.WORKSPACE_QUESTIONNAIRE_ANSWERED,
    ],
)
def test_the_new_events_are_in_the_catalog(event_type):
    assert event_type in EVENT_CATALOG


def test_tax_input_kind_private_cost_is_year_scoped():
    assert TaxInputKind.PRIVATE_COST.value == "private_cost"
    with pytest.raises(ValidationError):
        TaxInputCreate.model_validate(
            {"property_id": uuid.uuid4(), "kind": "private_cost", "value": _private()}
        )


def test_child_fact_kindergeld_not_before_birth_month():
    N.ChildFact(born_on=date(2026, 6, 14), kindergeld_from=date(2026, 6, 1))
    with pytest.raises(ValidationError):
        N.ChildFact(born_on=date(2026, 6, 14), kindergeld_from=date(2026, 5, 1))
