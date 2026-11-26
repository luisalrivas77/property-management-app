"""The property scenario (docs/phases/phase-10.md §7.9 (e), „(e) as decided for the build“
items 1 and 2; D289, D298). Pure functions over the goal's one month walk (rules/plan/
projection, D301) and route 3's window count (rules/goal/history); nothing here reads the
database or calls a model, and nothing proposes a rate, a sale or a property.

- Shown in `own_use` mode only (D289), for a net-worth goal the walk can answer: a sum saved
  or the reserve counts no property, and a goal without a projection, past its month, beyond
  the projection or with the reserve used up on the way has nothing to vary.
- One card per property the walk runs with its default scenario and still holds at the due
  month, or sells in it (its net proceeds then count); none for a property kept without a
  scenario (no growth to vary) or sold before the due month (its value no longer counts).
- The needed growth: the walk to the due month with nothing else changed (the typed fund and
  crypto rates kept), only this property's value growth replaced: each projected year's value
  phase 4's, base × (1 + g)^(year − today's year) to the cent, half up (the loans and the
  rental cash do not depend on it); in the sale year's December, phase 4's net proceeds at g.
  The smallest g to 0.1 % between −50 % and +100 % whose walk reaches the target at the due
  month, by route 3's bisection.
- The history: the German house price index's yearly returns Q4 to Q4, every window of the
  goal's months left in whole years, counted as route 3 counts the mix (median, best, N of M,
  the verdict; under 5 windows thin). No window: the growth is still shown.
"""

from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from uuid import UUID

from hausbuch.rules.plan.levers import months_after
from hausbuch.rules.tax.rounding import cents
from hausbuch.schemas.advice import AdviceMode
from hausbuch.schemas.goal import (
    GoalProperties,
    GoalPropertiesReason,
    GoalPropertyGrowth,
    GoalReferenceSource,
    GoalRouteReason,
)
from hausbuch.schemas.plan import GoalKind

from . import history as H
from .goal import GoalInputs, current_month, smallest_rate, target_of, value_of
from .walk import Projection, last_month, walk

ONE = Decimal(1)


@dataclass(frozen=True)
class PropertyGrowth:
    """A property's default scenario as phase 4 values it: `base` the value its growth
    compounds from (the scenario's value today, else the price), `typed_growth` the scenario's
    yearly value growth; `proceeds(g)` phase 4's net proceeds of the sale at growth g, asked
    only when the property is sold in the due month."""

    base: Decimal
    typed_growth: Decimal
    proceeds: Callable[[Decimal], Decimal] | None = None


def value_at(base: Decimal, growth: Decimal, years: int) -> Decimal:
    """Phase 4's projected value (rules/performance/projection: base × (1 + g)^n, to the cent)."""
    return cents(base * (ONE + growth) ** years)


def goal_properties(
    i: GoalInputs, properties: Mapping[Hashable, PropertyGrowth], house: H.Series
) -> GoalProperties:
    """The cards of Anlagen › Immobilie, by the walk's property keys (property ids)."""
    if i.mode is AdviceMode.GENERIC:
        return GoalProperties(shown=False, reason=GoalPropertiesReason.GENERIC_MODE)
    goal = i.goal
    if goal.kind is not GoalKind.NET_WORTH:
        return GoalProperties(shown=False, reason=GoalPropertiesReason.NOT_NET_WORTH)
    left = months_after(current_month(i.today), goal.due_month)
    p = i.projection
    issue = GoalProperties(shown=False, reason=GoalPropertiesReason.GOAL_ISSUE)
    if left <= 0 or p is None or goal.due_month > last_month(p):
        return issue
    if walk(p, until=goal.due_month).at(goal.due_month) is None:  # the reserve used up
        return issue
    target = target_of(goal, i.need)
    years = H.window_years(left)
    products = H.window_products(house.returns, years)
    cards = []
    for key, flat in p.flats.items():
        prop = properties.get(key)
        if prop is None or flat.sale_year is None or f"{flat.sale_year}-12" < goal.due_month:
            continue
        needed = _needed_growth(i, p, key, prop, target)
        cards.append(_card(key, flat.sale_year, prop, needed, products, years, house))
    return GoalProperties(shown=True, properties=cards)


def _at_growth(
    p: Projection,
    key: Hashable,
    prop: PropertyGrowth,
    growth: Decimal,
    today_year: int,
    with_proceeds: bool,
) -> Projection:
    flat = p.flats[key]
    years = {
        year: replace(fy, value=value_at(prop.base, growth, year - today_year))
        for year, fy in flat.years.items()
    }
    changed = replace(flat, years=years)
    if with_proceeds and prop.proceeds is not None:
        changed = replace(changed, net_proceeds=prop.proceeds(growth))
    return replace(p, flats={**p.flats, key: changed})


def _needed_growth(
    i: GoalInputs, p: Projection, key: Hashable, prop: PropertyGrowth, target: Decimal
) -> Decimal | None:
    due = i.goal.due_month
    flat = p.flats[key]
    sold_in_due = flat.sale_year is not None and due == f"{flat.sale_year}-12"

    def ok(k: int) -> bool:
        changed = _at_growth(p, key, prop, Decimal(k) / 1000, i.today.year, sold_in_due)
        month = walk(changed, until=due).at(due)
        return month is not None and value_of(i.goal, month) >= target

    return smallest_rate(ok)


def _card(
    key: Hashable,
    sale_year: int,
    prop: PropertyGrowth,
    needed: Decimal | None,
    products: list,
    years: int,
    house: H.Series,
) -> GoalPropertyGrowth:
    assert isinstance(key, UUID), "the walk's property keys are property ids"
    shown = {
        "property_id": key,
        "typed_growth": prop.typed_growth,
        "needed_growth": needed,
        "sale_year": sale_year,
        "window_years": years,
        "windows": len(products),
        "thin": len(products) < H.THIN_BELOW,
    }
    if not products:
        reason = GoalRouteReason.OUT_OF_RANGE if needed is None else GoalRouteReason.HISTORY_SHORT
        return GoalPropertyGrowth(reason=reason, **shown)
    shown |= {
        "median": H.annualised(H.median_of(products), years),
        "best": H.annualised(max(products), years),
        "source": GoalReferenceSource(
            key=house.key,
            source=house.source,
            url=house.url,
            licence=house.licence,
            attribution=house.attribution,
            read_on=house.read_on,
            first_year=min(house.returns),
            last_year=max(house.returns),
        ),
    }
    if needed is None:
        return GoalPropertyGrowth(reason=GoalRouteReason.OUT_OF_RANGE, **shown)
    return GoalPropertyGrowth(
        available=True,
        reached_windows=H.reached(needed, products, years),
        verdict=H.verdict_of(needed, products, years),
        **shown,
    )


__all__ = ["PropertyGrowth", "goal_properties", "value_at"]
