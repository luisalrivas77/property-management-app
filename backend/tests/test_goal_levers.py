"""Phase 10 §7.9 (d): the goal and the goal choice as levers of a household scenario.

A row stored before the goal existed must still validate and dump byte for byte as before
(the goal fields are left out of the dump while unset), and the goal's shape is checked by the
schema: a target above 0, a reserve goal in half months up to the reserve target's bound.
"""

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from hausbuch.schemas.plan import (
    Goal,
    GoalChoice,
    GoalKind,
    GoalSpending,
    HouseholdLevers,
    HouseholdScenarioCreate,
)

# A row as phase 9 and phase 10 stored it before §7.9 (d): every lever written out.
STORED = {
    "reserve_target_months": "6.0",
    "buffer_percent": "10",
    "horizon_months": 60,
    "reserve_accounts": {"added": [], "removed": []},
    "plan_overrides": [],
    "income_changes": [],
    "one_offs": [],
    "value_change": {"depot": None, "crypto": None},
    "net_worth_years": 10,
    "bands": [],
    "spending_changes": [],
    "budget_targets": {
        "needs_max": None,
        "wants_max": None,
        "saving_min": None,
        "housing_max": None,
    },
    "budget_class_overrides": {},
}

GOAL = {"kind": "net_worth", "target": "120000.00", "due_month": "2028-12", "set_on": "2026-11-20"}


def test_a_row_stored_before_the_goal_dumps_unchanged() -> None:
    levers = HouseholdLevers.model_validate(STORED)
    assert levers.goal is None and levers.goal_choice is None
    dumped = levers.model_dump(mode="json")
    assert "goal" not in dumped and "goal_choice" not in dumped
    assert dumped == STORED
    assert json.dumps(dumped) == json.dumps(STORED)


def test_the_default_levers_dump_without_goal_keys() -> None:
    assert "goal" not in HouseholdLevers().model_dump_json()
    assert "goal" not in HouseholdScenarioCreate(name="Plan").model_dump_json()


def test_a_goal_round_trips() -> None:
    levers = HouseholdLevers.model_validate(
        {
            **STORED,
            "goal": GOAL,
            "goal_choice": {"spending": "wants_only", "spending_chosen_on": "2026-11-24"},
        }
    )
    assert levers.goal == Goal(
        kind=GoalKind.NET_WORTH,
        target=Decimal("120000.00"),
        due_month="2028-12",
        set_on=levers.goal.set_on,
    )
    assert levers.goal_choice is not None
    assert levers.goal_choice.spending is GoalSpending.WANTS_ONLY
    again = HouseholdLevers.model_validate(levers.model_dump(mode="json"))
    assert again == levers


def test_the_json_schema_keeps_the_goal_fields() -> None:
    schema = HouseholdLevers.model_json_schema(mode="serialization")
    assert "goal" in schema["properties"] and "goal_choice" in schema["properties"]


@pytest.mark.parametrize(
    ("goal", "fragment"),
    [
        ({**GOAL, "target": "0"}, "greater than 0"),
        ({**GOAL, "target": "-5.00"}, "greater than 0"),
        ({**GOAL, "kind": "reserve_months", "target": "24.5"}, "up to 24 months"),
        ({**GOAL, "kind": "reserve_months", "target": "3.3"}, "in half months"),
        ({**GOAL, "due_month": "2028-13"}, "pattern"),
    ],
)
def test_a_goal_of_the_wrong_shape_is_refused(goal: dict, fragment: str) -> None:
    with pytest.raises(ValidationError, match=fragment):
        HouseholdLevers.model_validate({"goal": goal})


def test_a_reserve_goal_in_half_months_is_accepted() -> None:
    goal = Goal.model_validate({**GOAL, "kind": "reserve_months", "target": "13.5"})
    assert goal.target == Decimal("13.5")


@pytest.mark.parametrize(
    "choice",
    [
        {"spending": "wants_only"},
        {"spending_chosen_on": "2026-11-24"},
        {"investment": "etf", "investment_chosen_on": None},
    ],
)
def test_a_choice_comes_with_its_day(choice: dict) -> None:
    with pytest.raises(ValidationError, match="comes with its day"):
        GoalChoice.model_validate(choice)
