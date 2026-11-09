"""D80 review F1 and F3: the movable_asset guards of services.tax_inputs that refuse before any
database read. No database: a property row without a handover date cannot be stored
(property.purchase_date is NOT NULL since 0001), so the guard is called with a stand-in row."""

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest

from hausbuch.rules.errors import (
    TAX_INPUT_MOVABLE_ASSET_NO_PURCHASE_DATE,
    TAX_INPUT_MOVABLE_ASSET_SEPARATE_UNSUPPORTED,
)
from hausbuch.schemas.tax import TaxInputCreate
from hausbuch.services.errors import RuleViolationError
from hausbuch.services.tax_inputs import _check_movable_asset


def _kitchen(property_id, *, in_price: bool) -> TaxInputCreate:
    return TaxInputCreate.model_validate(
        {
            "property_id": property_id,
            "kind": "movable_asset",
            "tax_year": 2026,
            "value": {"label": "einbaukueche", "amount": "8500.00", "in_purchase_price": in_price},
        }
    )


@pytest.mark.parametrize("in_price", [True, False])
def test_a_kitchen_needs_the_handover_date_first(in_price):
    """F3: without a handover date the reason is the date, not the price split."""
    prop = SimpleNamespace(id=uuid4(), purchase_date=None)
    with pytest.raises(RuleViolationError) as exc:
        _check_movable_asset(None, prop, _kitchen(prop.id, in_price=in_price))  # type: ignore[arg-type]
    assert exc.value.codes == (TAX_INPUT_MOVABLE_ASSET_NO_PURCHASE_DATE,)


def test_a_kitchen_bought_separately_is_refused_before_any_lookup():
    """F1: its own acquisition date is not recorded yet, so it is refused outright."""
    prop = SimpleNamespace(id=uuid4(), purchase_date=date(2026, 7, 1))
    with pytest.raises(RuleViolationError) as exc:
        _check_movable_asset(None, prop, _kitchen(prop.id, in_price=False))  # type: ignore[arg-type]
    assert exc.value.codes == (TAX_INPUT_MOVABLE_ASSET_SEPARATE_UNSUPPORTED,)
