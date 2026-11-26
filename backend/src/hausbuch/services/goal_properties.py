"""GET /household-goal/properties (docs/phases/phase-10.md §7.9 (e), „(e) as decided for the
build“ items 1 and 2; D289, D298): the property cards on Anlagen › Immobilie. The arithmetic is
rules/goal/property.py; this module reads:

- the mode (D251) and the default household scenario's goal: in `generic` mode, without a goal
  or for a goal that is not a net worth nothing else is read;
- the goal's inputs and its walk (services.goal.goal_inputs, the same reads GET /household-goal
  keeps);
- per property the walk runs with its default scenario: that scenario's levers, the value its
  growth compounds from, and phase 4's projection at another value growth for the sale's net
  proceeds (services.performance.growth_projector), projected only when the property is sold in
  the due month;
- the German house price index committed in rules/goal/history.yaml (Eurostat prc_hpi_q).
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Connection

from hausbuch.rules.goal import property as R
from hausbuch.rules.goal import series as reference_series
from hausbuch.schemas.advice import AdviceMode
from hausbuch.schemas.goal import GoalProperties, GoalPropertiesReason, HouseholdGoal
from hausbuch.schemas.plan import GoalKind

from .goal import goal_inputs
from .health import levers_one_off_codes
from .performance import growth_projector
from .plan import _scenario, advice_mode
from .plan_inputs import PlanInputs
from .scenarios import default_scenario


def goal_properties(conn: Connection, today: date) -> GoalProperties:
    inputs = PlanInputs(conn, today)
    mode = inputs.part(("advice_mode",), advice_mode)
    if mode is AdviceMode.GENERIC:
        return GoalProperties(shown=False, reason=GoalPropertiesReason.GENERIC_MODE)
    row = _scenario(conn, None, inputs)
    levers = None if row is None else row.levers
    if levers is None or levers.goal is None:
        return GoalProperties(shown=False, reason=GoalPropertiesReason.NO_GOAL)
    if levers.goal.kind is not GoalKind.NET_WORTH:
        return GoalProperties(shown=False, reason=GoalPropertiesReason.NOT_NET_WORTH)
    reads = inputs.ended_reads(levers_one_off_codes(levers))
    built = goal_inputs(conn, today, row, levers, reads, inputs)
    if isinstance(built, HouseholdGoal):
        return GoalProperties(shown=False, reason=GoalPropertiesReason.GOAL_ISSUE)
    p = built.goal.projection
    properties: dict[UUID, R.PropertyGrowth] = {}
    for key, flat in {} if p is None else p.flats.items():
        assert isinstance(key, UUID)
        scenario = default_scenario(conn, key)
        if flat.sale_year is None or scenario is None:
            continue  # kept without a scenario (D301 (a) 5): no growth to vary
        scenario_levers, base, at = growth_projector(
            conn, key, today=today, scenario_id=scenario.id
        )

        def proceeds(growth: Decimal, at=at) -> Decimal:
            return at(growth).sale.net_proceeds

        properties[key] = R.PropertyGrowth(
            base=base, typed_growth=scenario_levers.value_growth, proceeds=proceeds
        )
    return R.goal_properties(built.goal, properties, reference_series.house_price_series())
