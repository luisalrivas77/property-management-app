"""Phase 10 §7.11 (a) 2, D301, D304: the investment split and the reserve's yearly rate as levers
of a household scenario.

A row stored before either existed must still validate and dump byte for byte as before (both
are left out of the dump while unset); the split's shares are percents with one place adding up
to exactly 100, and the reserve's rate has the depot's and crypto's shape (no default, a typed 0
is a rate)."""

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError
from test_goal_levers import STORED

from hausbuch.schemas.plan import LEVER_INVALID, HouseholdLevers, InvestmentSplit, ValueChange


def test_a_row_stored_before_dumps_unchanged() -> None:
    levers = HouseholdLevers.model_validate(STORED)
    assert levers.investment_split is None and levers.value_change.reserve is None
    dumped = levers.model_dump(mode="json")
    assert "investment_split" not in dumped
    assert dumped["value_change"] == {"depot": None, "crypto": None}
    assert json.dumps(dumped) == json.dumps(STORED)
    assert "investment_split" not in HouseholdLevers().model_dump_json()
    assert "reserve" not in ValueChange().model_dump_json()


def test_both_levers_round_trip() -> None:
    body = {
        **STORED,
        "value_change": {"depot": "0.050", "crypto": None, "reserve": "0.000"},
        "investment_split": {"depot": "70.5", "crypto": "29.5"},
    }
    levers = HouseholdLevers.model_validate(body)
    assert levers.investment_split == InvestmentSplit(depot=Decimal("70.5"), crypto=Decimal("29.5"))
    assert levers.value_change.reserve == Decimal("0.000")  # a typed 0 is a rate
    dumped = levers.model_dump(mode="json")
    assert dumped["value_change"] == {"depot": "0.050", "crypto": None, "reserve": "0.000"}
    assert dumped["investment_split"] == {"depot": "70.5", "crypto": "29.5"}
    assert HouseholdLevers.model_validate(dumped) == levers


def test_the_json_schema_keeps_both_fields() -> None:
    schema = HouseholdLevers.model_json_schema(mode="serialization")
    assert "investment_split" in schema["properties"]
    value_change = ValueChange.model_json_schema(mode="serialization")
    assert "reserve" in value_change["properties"]


@pytest.mark.parametrize(
    ("split", "fragment"),
    [
        ({"depot": "70.0", "crypto": "20.0"}, f"{LEVER_INVALID}: the split's shares add up to 100"),
        ({"depot": "60.05", "crypto": "39.95"}, "1 decimal place"),
        ({"depot": "101.0", "crypto": "-1.0"}, "less than or equal to 100"),
        ({"depot": 70, "crypto": 30}, "string"),
    ],
)
def test_a_split_of_the_wrong_shape_is_refused(split: dict, fragment: str) -> None:
    with pytest.raises(ValidationError, match=fragment):
        HouseholdLevers.model_validate({"investment_split": split})


@pytest.mark.parametrize("rate", ["0.6", "-0.51", "0.0505"])
def test_a_reserve_rate_out_of_bounds_is_refused(rate: str) -> None:
    with pytest.raises(ValidationError):
        ValueChange.model_validate({"reserve": rate})
