"""Phase 10 §7.10 (b) on the database: the household plan's reads kept for a data version
(services.plan_inputs). Every answer through the kept reads, cold and warm, equals the one
computed with the store bypassed; a committed write, a new day and a transaction that has
written each read afresh; two workspaces never share a part."""

from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from db.matching_support import USER, World
from db.test_household_goal import _goal, _save
from db.test_household_plan import _bought_in_2026, _flat_scenario, _savings_history
from db.test_services_health import _book
from db.test_services_health_snapshots import NOW, TODAY
from sqlalchemy import event

from hausbuch.db.session import workspace_transaction
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.plan import HouseholdLevers, HouseholdScenarioCreate
from hausbuch.services import budget, goal, household_scenarios, net_worth, plan, plan_inputs
from hausbuch.services.context import new_command
from hausbuch.services.errors import NotFoundError, RuleViolationError

pytestmark = pytest.mark.db

C = AllocationCategory


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    plan_inputs.clear()
    yield World(app_engine, fresh_workspaces[0], now=NOW)
    plan_inputs.clear()


@contextmanager
def _counted(world: World):
    """The number of statements sent through the world's engine inside the block."""
    seen = [0]

    def count(*_args, **_kwargs):
        seen[0] += 1

    event.listen(world.engine, "before_cursor_execute", count)
    try:
        yield seen
    finally:
        event.remove(world.engine, "before_cursor_execute", count)


def _start(newest: str) -> str:
    year, month = (int(x) for x in newest.split("-"))
    return f"{year + month // 12:04d}-{month % 12 + 1:02d}"


def _previews(levers: HouseholdLevers, newest: str) -> dict[str, HouseholdLevers]:
    """The perf probe's four preview bodies (perf_probe.PageRun.plan_reads)."""
    base = levers.model_dump(mode="json")
    start = _start(newest)
    return {
        name: HouseholdLevers.model_validate(body)
        for name, body in {
            "unchanged": base,
            "income": {
                **base,
                "income_changes": [
                    *base["income_changes"],
                    {"monthly_net": "-500.00", "from_month": start},
                ],
            },
            "spending": {
                **base,
                "spending_changes": [
                    *base["spending_changes"],
                    {"code": "hh_restaurant", "monthly": "50.00", "from_month": start},
                ],
            },
            "one_off": {
                **base,
                "one_offs": [*base["one_offs"], {"month": start, "amount": "-2000.00"}],
            },
        }.items()
    }


def _json(world: World, read, *args, today: date = TODAY) -> str:
    return world.query(read, today, *args).model_dump_json()


def _uncached(world: World, read, *args, today: date = TODAY) -> str:
    with plan_inputs.bypass():
        return _json(world, read, *args, today=today)


def _projected(world: World):
    """tests/db/test_household_goal._projected_world (twelve closed months to March 2027, the
    flat with its default scenario, the month-end of 31 March 2027) with a default household
    scenario holding a goal; the scenario's levers and the private account."""
    private, _ = _savings_history(world)
    for month in (1, 2, 3):
        line = private.line(date(2027, month, 18), "-300.00", name="Trattoria Muster")
        _book(world, line, "-300.00", C.HH_RESTAURANT)
    _bought_in_2026(world)
    _flat_scenario(world, None, NOW)
    world.cmd(net_worth.capture_month_end, date(2027, 3, 31))
    row = _save(world, _goal("10000000.00"))
    return row.levers, private


def test_each_preview_equals_the_uncached_computation_cold_and_warm(world):
    """The four previews of the probe, each cold (nothing kept), warm, and again after the
    others ran (a kept object changed by one call would show here), against the store
    bypassed; GET /household-plan, the Budget and the goal the same."""
    levers, _ = _projected(world)
    newest = world.query(plan.household_plan, TODAY).newest_closed
    assert newest == "2027-03"
    previews = _previews(levers, newest)
    expected = {name: _uncached(world, plan.preview_plan, lv) for name, lv in previews.items()}
    assert len(set(expected.values())) == 4  # each lever changes the answer

    plan_inputs.clear()
    with _counted(world) as cold:
        first = _json(world, plan.preview_plan, previews["unchanged"])
    assert first == expected["unchanged"]
    for name, lv in previews.items():
        with _counted(world) as warm:
            assert _json(world, plan.preview_plan, lv) == expected[name], name
        assert warm[0] < cold[0]
        assert warm[0] <= 4, (name, warm[0])  # set_config, the stamp, the commit's own
    for name, lv in reversed(previews.items()):
        assert _json(world, plan.preview_plan, lv) == expected[name], name

    for read in (plan.household_plan, budget.household_budget, goal.goal):
        want = _uncached(world, read)
        assert _json(world, read) == want
        assert _json(world, read) == want
    assert world.query(goal.goal, TODAY).word is not None


def test_a_committed_write_between_two_previews_is_seen(world):
    levers, private = _projected(world)
    previews = _previews(levers, "2027-03")
    before = _json(world, plan.preview_plan, previews["spending"])
    assert _json(world, plan.preview_plan, previews["spending"]) == before  # kept

    line = private.line(date(2027, 2, 22), "-450.00", name="Trattoria Muster")
    _book(world, line, "-450.00", C.HH_RESTAURANT)

    after = _json(world, plan.preview_plan, previews["spending"])
    assert after != before
    assert after == _uncached(world, plan.preview_plan, previews["spending"])
    assert _json(world, goal.goal) == _uncached(world, goal.goal)


def test_a_new_day_is_seen(world, monkeypatch):
    """A new `today` is read afresh: on 31 March 2027 March has not ended, so the newest
    closed month is February; on 10 April it is March. The UTC day the store is keyed by
    moves it too."""
    levers, _ = _projected(world)
    previews = _previews(levers, "2027-03")
    early = date(2027, 3, 31)
    got = world.query(plan.household_plan, early)
    assert got.newest_closed == "2027-02"
    assert got.model_dump_json() == _uncached(world, plan.household_plan, today=early)
    assert world.query(plan.household_plan, early).model_dump_json() == got.model_dump_json()
    now = world.query(plan.household_plan, TODAY)
    assert now.newest_closed == "2027-03"
    assert now.model_dump_json() == _uncached(world, plan.household_plan)

    # Same `today`, a new UTC day: nothing kept is served.
    _json(world, plan.preview_plan, previews["unchanged"])
    with _counted(world) as warm:
        _json(world, plan.preview_plan, previews["income"])
    assert warm[0] <= 4

    class Tomorrow(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(UTC) + timedelta(days=1)

    monkeypatch.setattr(plan_inputs, "datetime", Tomorrow)
    want = _uncached(world, plan.preview_plan, previews["income"])
    with _counted(world) as next_day:
        assert _json(world, plan.preview_plan, previews["income"]) == want
    assert next_day[0] > warm[0] + 10  # read afresh
    with _counted(world) as again:
        assert _json(world, plan.preview_plan, previews["income"]) == want
    assert again[0] <= 4  # and kept for the new day


def test_a_transaction_that_has_written_reads_its_own_rows_and_keeps_nothing(world):
    """Inside a transaction that wrote a new default scenario the preview of the default
    sees it (nothing kept is served there); rolled back, nothing it read was kept."""
    levers, _ = _projected(world)
    previews = _previews(levers, "2027-03")
    kept = _json(world, plan.household_plan)
    assert _json(world, plan.household_plan) == kept

    plan_inputs.clear()
    changed = levers.model_copy(update={"net_worth_years": 5})
    with pytest.raises(_Rollback), workspace_transaction(world.engine, world.ws) as conn:
        ctx = new_command(conn, world.ws, USER, now=NOW)
        row = household_scenarios.create_scenario(
            ctx,
            HouseholdScenarioCreate(
                name="Entwurf",
                is_default=True,
                supersedes_id=plan.default_scenario(conn).id,
                levers=changed,
            ),
        )
        inside = plan.household_plan(conn, TODAY)
        assert inside.scenario_id == row.id
        assert inside.levers.net_worth_years == 5
        raise _Rollback

    assert len(plan_inputs.STORE) == 0  # nothing read after the write was kept
    assert _json(world, plan.household_plan) == kept
    assert _json(world, plan.preview_plan, previews["one_off"]) == _uncached(
        world, plan.preview_plan, previews["one_off"]
    )


class _Rollback(Exception):
    pass


def test_errors_are_the_same_cold_and_warm(world):
    levers, _ = _projected(world)
    unknown = uuid4()
    outside = HouseholdLevers.model_validate(
        {
            **levers.model_dump(mode="json"),
            "one_offs": [{"month": "2040-01", "amount": "-1.00"}],
        }
    )
    account = HouseholdLevers.model_validate(
        {
            **levers.model_dump(mode="json"),
            "reserve_accounts": {"added": [str(unknown)], "removed": []},
        }
    )
    for _ in range(2):  # cold, then warm
        with pytest.raises(NotFoundError) as scenario:
            world.query(plan.preview_plan, TODAY, levers, unknown)
        assert scenario.value.entity_id == unknown
        with pytest.raises(NotFoundError) as acc:
            world.query(plan.preview_plan, TODAY, account)
        assert acc.value.entity_id == unknown
        with pytest.raises(RuleViolationError) as one_off:
            world.query(plan.preview_plan, TODAY, outside)
        assert one_off.value.codes
        with pytest.raises(NotFoundError):
            world.query(plan.household_plan, TODAY, unknown)
        # A scenario id nobody has is never kept, so unknown ids cannot grow the store.
        [entry] = plan_inputs.STORE._entries.values()
        assert not any(key[0] == "scenario" for key in entry.parts)


def test_two_workspaces_never_share_a_part(app_engine, fresh_workspaces):
    plan_inputs.clear()
    a = World(app_engine, fresh_workspaces[0], now=NOW)
    b = World(app_engine, fresh_workspaces[1], now=NOW)
    _projected(a)
    _savings_history(b)
    try:
        for _ in range(2):
            assert _json(a, plan.household_plan) == _uncached(a, plan.household_plan)
            assert _json(b, plan.household_plan) == _uncached(b, plan.household_plan)
        assert _json(a, plan.household_plan) != _json(b, plan.household_plan)
        assert len(plan_inputs.STORE) == 2
    finally:
        plan_inputs.clear()
