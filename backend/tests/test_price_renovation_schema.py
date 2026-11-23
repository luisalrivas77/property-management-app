"""Phase 3 D287: the `price_renovation` tax input's value and scope, and its event payload
without the Kaufvertrag's quoted clause. Invented values only."""

import uuid

import pytest
from pydantic import ValidationError

from hausbuch.schemas.events import TaxInputRecorded
from hausbuch.schemas.tax import (
    DOCUMENT_REQUIRED,
    YEAR_SCOPED,
    PriceRenovation,
    TaxInputCreate,
    TaxInputKind,
)

VALUE = {
    "status": "claimed",
    "amount": "11900.00",
    "amount_net": "10000.00",
    "period_start": "2026-07-01",
    "period_end": "2026-09-30",
    "paid_on": "2026-06-15",
    "clause": "Der Verkäufer renoviert die Wohnung nach Übergabe auf eigene Kosten.",
}


def _create(**overrides) -> dict:
    return {
        "property_id": str(uuid.uuid4()),
        "kind": "price_renovation",
        "document_id": str(uuid.uuid4()),
        "value": VALUE,
    } | overrides


def test_the_kind_is_per_property_and_needs_its_document():
    kind = TaxInputKind.PRICE_RENOVATION
    assert kind not in YEAR_SCOPED and kind in DOCUMENT_REQUIRED
    row = TaxInputCreate.model_validate(_create())
    assert type(row.value) is PriceRenovation and row.value.counts
    for bad in (_create(tax_year=2026), _create(document_id=None)):
        with pytest.raises(ValidationError, match="tax_input.scope_invalid"):
            TaxInputCreate.model_validate(bad)


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"amount_net": "12000.00"}, "amount_net"),
        ({"period_end": "2026-06-30"}, "period_end"),
        ({"status": "maybe"}, "status"),
        ({"amount": "0.00"}, "amount"),
    ],
)
def test_a_value_out_of_shape_is_refused_at_its_field(change, field):
    with pytest.raises(ValidationError) as refused:
        TaxInputCreate.model_validate(_create(value=VALUE | change))
    assert ("value", field) in {e["loc"][:2] for e in refused.value.errors()}


def test_declined_does_not_count():
    assert not PriceRenovation.model_validate(VALUE | {"status": "declined"}).counts
    assert PriceRenovation.model_validate(VALUE | {"status": "accepted"}).counts


def test_the_event_keeps_the_figures_and_drops_the_quoted_clause():
    row = TaxInputCreate.model_validate(_create())
    event = TaxInputRecorded(
        tax_input_id=uuid.uuid4(),
        kind=row.kind,
        document_id=row.document_id,
        value=row.value,
    )
    assert event.value.clause is None and event.value.amount == row.value.amount
    payload = event.model_dump(mode="json")
    assert "Verkäufer" not in str(payload)
    again = TaxInputRecorded.model_validate(payload)
    assert again.value == event.value
