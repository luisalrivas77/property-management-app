"""Phase 10 D272 (2), PR #490 review: a budget class override is refused on save or in the
preview only when it is new or altered against what it is carried from. A save carries from
the row it supersedes and from the default in force (a new scenario beside the default with
the default's overrides copied in); the preview from the row it previews (`scenario_id`),
else the default. Here two codes are pretended to stop being movable after they were saved
(rules.budget.budget.movable monkeypatched). Invented data only."""

import pytest
from db.matching_support import World
from db.test_services_health_snapshots import NOW, TODAY

from hausbuch.rules.budget import budget as budget_rules
from hausbuch.rules.errors import HOUSEHOLD_SCENARIO_BUDGET_CLASS_INVALID
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.household import BudgetClass
from hausbuch.schemas.plan import HouseholdLevers, HouseholdScenarioCreate
from hausbuch.services import household_scenarios, plan
from hausbuch.services.errors import RuleViolationError

pytestmark = pytest.mark.db

C = AllocationCategory
RESTAURANT_NEED = {C.HH_RESTAURANT: BudgetClass.NEED}
SUPERMARKET_WANT = {C.HH_SUPERMARKET: BudgetClass.WANT}


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


def _levers(overrides) -> HouseholdLevers:
    return HouseholdLevers(budget_class_overrides=overrides)


def _create(world, name: str, overrides, *, is_default: bool = False):
    return world.cmd(
        household_scenarios.create_scenario,
        HouseholdScenarioCreate(name=name, is_default=is_default, levers=_levers(overrides)),
    )


def _refused(call) -> None:
    with pytest.raises(RuleViolationError) as refused:
        call()
    assert refused.value.codes == (HOUSEHOLD_SCENARIO_BUDGET_CLASS_INVALID,)


def test_an_override_carried_from_the_default_or_the_row_previewed_is_not_refused(
    world, monkeypatch
):
    _create(world, "Basis", RESTAURANT_NEED, is_default=True)
    other = _create(world, "Variante", SUPERMARKET_WANT)
    movable = budget_rules.movable
    monkeypatch.setattr(
        budget_rules,
        "movable",
        lambda code: code not in (C.HH_RESTAURANT, C.HH_SUPERMARKET) and movable(code),
    )

    # A save: the default's override copied into a new scenario is carried; one the default
    # does not hold (only another, unrelated row does) is refused, as is an altered one.
    copy = _create(world, "Kopie", RESTAURANT_NEED)
    assert copy.levers.budget_class_overrides == RESTAURANT_NEED
    _refused(lambda: _create(world, "Neu", SUPERMARKET_WANT))
    _refused(lambda: _create(world, "Anders", {C.HH_RESTAURANT: BudgetClass.WANT}))

    # The preview: compared with the row named, else the default in force.
    world.query(plan.preview_plan, TODAY, _levers(RESTAURANT_NEED))
    world.query(plan.preview_plan, TODAY, _levers(SUPERMARKET_WANT), other.id)
    _refused(lambda: world.query(plan.preview_plan, TODAY, _levers(SUPERMARKET_WANT)))
    _refused(lambda: world.query(plan.preview_plan, TODAY, _levers(RESTAURANT_NEED), other.id))
