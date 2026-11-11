"""Phase 3 D137: the schema side of a repair paid from another account in the 15 % test.
A counted cost and a basis addition name exactly one source (an allocation or a tax input);
a private cost row carries a work date and a net only in a category the test counts, and
its net never exceeds its amount. The rule behaviour is in the fixtures
(phase3/anschaffungsnah.yaml and anlage_v_2026.yaml, cases private_repair_*)."""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from hausbuch.schemas.tax import (
    BasisAddition,
    CountedCost,
    PrivateCostValue,
    SourceRef,
    TaxInputCreate,
)

ALLOCATION = UUID("00000a11-0000-4000-8000-000000000001")
TAX_INPUT = UUID("000000f1-0000-4000-8000-000000000001")


def _counted(**ids) -> CountedCost:
    return CountedCost(
        **ids, tax_year=2026, gross=Decimal("119.00"), net=Decimal("100.00"), net_source="invoice"
    )


def test_a_counted_cost_names_exactly_one_source():
    assert _counted(allocation_id=ALLOCATION).source_key == ("allocation", ALLOCATION)
    assert _counted(tax_input_id=TAX_INPUT).source_ref() == SourceRef(
        kind="tax_input", id=TAX_INPUT
    )
    with pytest.raises(ValidationError):
        _counted()
    with pytest.raises(ValidationError):
        _counted(allocation_id=ALLOCATION, tax_input_id=TAX_INPUT)


def test_a_basis_addition_from_a_tax_input_is_keyed_apart_from_an_allocation():
    same = UUID(int=7)
    a = BasisAddition(allocation_id=same, tax_year=2026, amount="1.00", reason="limit_exceeded")
    b = BasisAddition(tax_input_id=same, tax_year=2026, amount="1.00", reason="limit_exceeded")
    assert a.source_key != b.source_key
    assert b.source_ref() == SourceRef(kind="tax_input", id=same)


def _repair(**extra) -> PrivateCostValue:
    return PrivateCostValue(
        cost_class="other", label="Reparatur", amount="119.00", category="repairs", **extra
    )


def test_a_repair_may_carry_its_work_date_and_net():
    value = _repair(performed_on=date(2026, 6, 10), amount_net="100.00")
    assert (value.performed_on, value.amount_net) == (date(2026, 6, 10), Decimal("100.00"))
    assert _repair().performed_on is None  # optional: counted conservatively without it


@pytest.mark.parametrize(
    "value",
    [
        {"category": "insurance", "performed_on": "2026-06-10"},
        {"category": None, "amount_net": "10.00"},
        {"category": "repairs", "amount_net": "119.01"},
    ],
)
def test_a_work_date_or_net_is_refused_where_it_does_not_belong(value):
    with pytest.raises(ValidationError):
        PrivateCostValue(cost_class="other", label="Kosten", amount="119.00", **value)


def test_a_net_above_the_amount_is_refused_at_the_net_field():
    """Review F4: the 422 names amount_net, so the card shows the message at Netto."""
    with pytest.raises(ValidationError) as refused:
        _repair(amount_net="119.01")
    (error,) = refused.value.errors()
    assert error["loc"] == ("amount_net",)
    assert "Der Nettobetrag kann nicht höher sein als der Betrag." in error["msg"]
    # Inside a tax input row (the API body, the proposal's apply) it names value.amount_net.
    with pytest.raises(ValidationError) as refused:
        TaxInputCreate(
            property_id=UUID(int=1),
            kind="private_cost",
            tax_year=2026,
            value={
                "cost_class": "other",
                "label": "Reparatur",
                "amount": "119.00",
                "category": "repairs",
                "amount_net": "119.01",
            },
        )
    assert [e["loc"] for e in refused.value.errors()] == [("value", "amount_net")]
