"""Phase 9 step 3b, PR #436 review: a stored percent change whose category has no measured
spending any more (§7.6 (1) amended). Two ways it happens: the category's lines are re-booked
out of the window, or one line waiting in Zu prüfen opens a month and the window falls below
three closed months. Either way the change is carried, never refused: a save of another
lever (the limits on Übersicht, the buffer) goes through and keeps the change's stored
`mean_at_save`; the plan names the change in a „Szenario prüfen“ to-do and lets it count for
nothing; a percent typed now on a category without a mean is still refused."""

from datetime import date
from decimal import Decimal

import pytest
from db.matching_support import World
from db.test_household_plan import _plan, _private_and_depot
from db.test_household_plan_spending import _change, _save
from db.test_services_health import _book
from db.test_services_health_snapshots import NOW, TODAY

from hausbuch.rules.errors import HOUSEHOLD_SCENARIO_SPENDING_INVALID
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.health import BandLimits, HealthTodoCode, MarkedFigure
from hausbuch.schemas.plan import HouseholdLevers, HouseholdScenario, HouseholdScenarioCreate
from hausbuch.services import household_scenarios, plan
from hausbuch.services.errors import RuleViolationError

pytestmark = pytest.mark.db

C = AllocationCategory
SAVED_MEAN = Decimal("1200.00")


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


def _supermarket_lines(world, private) -> list:
    """1,200 € at the supermarket in each of January to March 2027 (the window), booked."""
    lines = []
    for month in (1, 2, 3):
        line = private.line(date(2027, month, 12), "-1200.00", name="Supermarkt")
        _book(world, line, "-1200.00", C.HH_SUPERMARKET)
        lines.append(line)
    return lines


def _carried() -> list:
    """The stored change as the screen sends it back: −10 % at the supermarket from April,
    with a `mean_at_save` the server must not read."""
    return [_change(percent=Decimal("-0.10"), mean_at_save=Decimal("1.00"))]


def _supersede(world, old: HouseholdScenario, **levers) -> HouseholdScenario:
    base = old.levers.model_dump() | {"spending_changes": _carried()}
    return world.cmd(
        household_scenarios.create_scenario,
        HouseholdScenarioCreate(
            name=old.name,
            is_default=True,
            levers=HouseholdLevers.model_validate(base | levers),
            supersedes_id=old.id,
        ),
    )


def _todo_texts(world) -> list[str]:
    got = _plan(world)
    return [
        t.text
        for t in got.todos
        if t.code is HealthTodoCode.SCENARIO_CHECK and t.href == plan.SPENDING_PLAY_HREF
    ]


def _assert_carried_and_refused_when_typed(world, saved: HouseholdScenario) -> None:
    """The four checks of the review, on a saved default whose supermarket change has lost
    its mean."""
    # 1. A bands-only save and a save of the buffer both go through.
    bands = [BandLimits(figure=MarkedFigure.RESERVE, red=Decimal("0.5"))]
    banded = _supersede(world, saved, bands=bands)
    buffered = _supersede(world, banded, buffer_percent=Decimal("15"))
    assert buffered.levers.bands == bands and buffered.levers.buffer_percent == Decimal("15")
    # 2. The carried change keeps the mean it was saved with, through both saves.
    for row in (banded, buffered):
        (change,) = row.levers.spending_changes
        assert change.mean_at_save == SAVED_MEAN
    # The preview of the levers as saved is not refused either (compared with the default).
    world.query(plan.preview_plan, TODAY, buffered.levers)
    # 3. The plan names the category in a „Szenario prüfen“ to-do on „Ausgaben durchspielen“.
    (text,) = _todo_texts(world)
    assert "„Supermarkt“" in text and "zählt gerade nicht mit" in text
    # 4. A percent typed now on a category without a mean is still refused: an altered
    # supermarket change and a new one, on save and in the preview.
    altered = _change(percent=Decimal("-0.20"))
    new = _change(code=C.HH_CHILDCARE, percent=Decimal("0.10"))
    for typed in ([altered], [*_carried(), new]):
        with pytest.raises(RuleViolationError) as refused:
            world.cmd(
                household_scenarios.create_scenario,
                HouseholdScenarioCreate(
                    name=buffered.name,
                    is_default=True,
                    levers=buffered.levers.model_copy(update={"spending_changes": typed}),
                    supersedes_id=buffered.id,
                ),
            )
        assert refused.value.codes == (HOUSEHOLD_SCENARIO_SPENDING_INVALID,)
        with pytest.raises(RuleViolationError):
            world.query(
                plan.preview_plan,
                TODAY,
                buffered.levers.model_copy(update={"spending_changes": typed}),
            )


def test_a_category_rebooked_out_of_the_window_keeps_its_carried_change(world):
    """The supermarket's three lines are re-booked as restaurant after the save: the
    supermarket has no mean, the change is carried with its 1,200 € and counts for nothing."""
    private, _ = _private_and_depot(world)
    lines = _supermarket_lines(world, private)
    saved = _save(world, *_carried())
    assert saved.levers.spending_changes[0].mean_at_save == SAVED_MEAN
    assert _plan(world).months[0].spending_delta == Decimal("-120.00")
    assert _todo_texts(world) == []
    for line in lines:
        _book(world, line, "-1200.00", C.HH_RESTAURANT)
    assert _plan(world).months[0].spending_delta == Decimal("0.00")
    _assert_carried_and_refused_when_typed(world, saved)


def test_a_waiting_line_below_three_closed_months_keeps_the_carried_change(world):
    """One line waiting in Zu prüfen in February opens that month, so the window falls below
    three closed months and no code has a mean: the change is carried all the same."""
    private, _ = _private_and_depot(world)
    _supermarket_lines(world, private)
    saved = _save(world, *_carried())
    assert saved.levers.spending_changes[0].mean_at_save == SAVED_MEAN
    waiting = private.line(date(2027, 2, 20), "-30.00", name="Bäckerei")
    _book(world, waiting, "-30.00", C.HH_SUPERMARKET, confirm=False)
    assert _plan(world).months == []
    _assert_carried_and_refused_when_typed(world, saved)
