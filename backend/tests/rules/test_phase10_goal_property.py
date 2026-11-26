"""rules/goal/property against tests/fixtures/phase10/goal_property.yaml (docs/phases/phase-10.md
§7.9 (e), „(e) as decided for the build“ items 1 and 2): each property's needed value growth on
the goal's walk, and the INVENTED house price index's windows as route 3 counts them."""

import uuid
from dataclasses import replace
from decimal import Decimal

import fixture_models_phase10 as phase10
import pytest
from test_phase10_goal import _route_inputs

from hausbuch.rules.goal.property import PropertyGrowth, goal_properties
from hausbuch.rules.goal.series import house_price_series

CASES = phase10.load("goal_property").cases


def _id(key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"hausbuch-test-flat:{key}")


def _inputs(case):
    inputs, p, _target = _route_inputs(case)
    if p is not None:  # the walk's property keys are property ids in the service
        p = replace(p, flats={_id(k): f for k, f in p.flats.items()})
        inputs = replace(inputs, projection=p)
    properties = {}
    for key, prop in case.properties.items():
        flat = case.flats.get(key)
        proceeds = None
        if flat is not None and flat.sale_year is not None and prop.selling_rate is not None:
            n = flat.sale_year - case.today.year
            loan = flat.years[flat.sale_year][1]

            def proceeds(g: Decimal, prop=prop, n=n, loan=loan) -> Decimal:
                return prop.proceeds(g, n, loan)

        properties[_id(key)] = PropertyGrowth(
            base=prop.base, typed_growth=prop.typed_growth, proceeds=proceeds
        )
    return inputs, properties


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_goal_property(case) -> None:
    inputs, properties = _inputs(case)
    got = goal_properties(inputs, properties, house_price_series(case.index))
    want = case.expect
    assert got.shown is want.shown
    assert (got.reason.value if got.reason is not None else None) == want.reason
    cards = {c.property_id: c for c in got.properties}
    assert set(cards) == {_id(k) for k in want.properties}
    for key, w in want.properties.items():
        c = cards[_id(key)]
        assert c.available is w.available, key
        assert (c.reason.value if c.reason is not None else None) == w.reason, key
        assert c.typed_growth == w.typed_growth, key
        assert c.needed_growth == w.needed_growth, key
        assert (c.sale_year, c.window_years, c.windows) == (
            w.sale_year,
            w.window_years,
            w.windows,
        ), key
        assert c.reached_windows == w.reached_windows, key
        assert (c.median, c.best) == (w.median, w.best), key
        assert (c.verdict.value if c.verdict is not None else None) == w.verdict, key
        assert c.thin is w.thin, key
        assert (c.source is None) == (w.windows == 0), key


def test_the_needed_growth_reaches_and_one_step_less_does_not() -> None:
    """The bisection's answer is the smallest 0.1 %: the walk at it reaches the target, at
    0.1 % less it does not (within_median)."""
    from hausbuch.rules.goal import walk
    from hausbuch.rules.goal.goal import target_of, value_of
    from hausbuch.rules.goal.property import _at_growth

    case = next(c for c in CASES if c.id == "within_median")
    inputs, properties = _inputs(case)
    got = goal_properties(inputs, properties, house_price_series(case.index))
    card = got.properties[0]
    p, due = inputs.projection, inputs.goal.due_month
    target = target_of(inputs.goal, inputs.need)
    prop = properties[card.property_id]
    for g, reaches in ((card.needed_growth, True), (card.needed_growth - Decimal("0.001"), False)):
        changed = _at_growth(p, card.property_id, prop, g, inputs.today.year, False)
        month = walk(changed, until=due).at(due)
        assert (value_of(inputs.goal, month) >= target) is reaches
