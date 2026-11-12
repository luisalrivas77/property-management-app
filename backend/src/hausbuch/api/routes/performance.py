"""Performance of a property (docs/phases/phase-4.md §6, §8, §10): the actual years, the
projection to a sale year, and the projection's scenarios.

Actuals and projections are computed on every read and never stored (D59, D86); only the
scenarios are rows. Scenarios are append-only (D87): a correction or a withdrawal is a new row
naming the row it replaces (supersedes_id); services/scenarios.py holds the checks.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Query

from hausbuch.schemas.common import NonNegativeMoney, PositiveMoney, Rate, TaxYear, VacancyMonths
from hausbuch.schemas.performance import (
    Actuals,
    FlatRateCheck,
    Projection,
    Scenario,
    ScenarioCreate,
)
from hausbuch.services.documents import LOCAL_TIME_ZONE
from hausbuch.services.performance import (
    performance_actuals,
    performance_projection,
    property_flat_rate_check,
)
from hausbuch.services.scenarios import create_scenario, list_scenarios

from ..deps import Cmd, domain_router

router = domain_router()


@router.get("/properties/{property_id}/performance/actuals")
def property_actuals(property_id: UUID, cmd: Cmd) -> Actuals:
    """Every owned year from the purchase year through today's, from confirmed allocations
    only (D85); the current year is marked so_far. 422 projection.no_rule_set for a purchase
    year before the first rule set, tariff.no_file for one before the first tariff file (the
    projection answers the same)."""
    today = datetime.now(LOCAL_TIME_ZONE).date()
    return cmd.read(performance_actuals, property_id, today=today)


@router.get("/properties/{property_id}/performance/projection")
def property_projection(
    property_id: UUID,
    cmd: Cmd,
    scenario_id: UUID | None = None,
    value_growth: Annotated[Rate | None, Query()] = None,
    rent_growth: Annotated[Rate | None, Query()] = None,
    cost_inflation: Annotated[Rate | None, Query()] = None,
    sale_year: Annotated[TaxYear | None, Query()] = None,
    today_value: Annotated[PositiveMoney | None, Query()] = None,
    upkeep_per_year: Annotated[NonNegativeMoney | None, Query()] = None,
    selling_costs_rate: Annotated[Rate | None, Query()] = None,
    vacancy_months: Annotated[VacancyMonths | None, Query()] = None,
) -> Projection:
    """Next January through the sale year, with the sale at 31 Dec of that year. The levers
    are scenario_id's (a scenario of this property, else 404), else the default scenario in
    force, else the defaults with the sale year = the first year on or after the §23 date;
    each lever given here overrides it for this read only (nothing is written). 422
    scenario.sale_year_before_today (the sale year is not after today's year)."""
    today = datetime.now(LOCAL_TIME_ZONE).date()
    overrides = {
        "value_growth": value_growth,
        "rent_growth": rent_growth,
        "cost_inflation": cost_inflation,
        "sale_year": sale_year,
        "today_value": today_value,
        "upkeep_per_year": upkeep_per_year,
        "selling_costs_rate": selling_costs_rate,
        "vacancy_months": vacancy_months,
    }
    return cmd.read(
        performance_projection,
        property_id,
        today=today,
        scenario_id=scenario_id,
        overrides=overrides,
    )


@router.get("/properties/{property_id}/flat-rate-check")
def property_flat_rate(property_id: UUID, cmd: Cmd) -> FlatRateCheck:
    """The Nebenkostenpauschale rise test (D155): the monthly costs when each flat_rate
    tenancy's Pauschale was set and today, each rate with its sources, and per tenancy its share
    of the rise and the verdict (raise above 10.00 € a month, lower on any fall). Information,
    not advice; computed on read, nothing stored."""
    today = datetime.now(LOCAL_TIME_ZONE).date()
    return cmd.read(property_flat_rate_check, property_id, today=today)


@router.get("/properties/{property_id}/scenarios")
def scenarios_list(property_id: UUID, cmd: Cmd, include_history: bool = False) -> list[Scenario]:
    """The scenarios in force (not superseded, not withdrawn), oldest first;
    include_history=true adds the rest."""
    return cmd.read(list_scenarios, property_id, include_history=include_history)


@router.post("/properties/{property_id}/scenarios", status_code=201)
def scenario_create(property_id: UUID, body: ScenarioCreate, cmd: Cmd) -> Scenario:
    """One new row and its property.scenario_recorded event. 422 scenario.scope_invalid (the
    body's property is not the path's, a loan lever names another property's loan, a correction
    supersedes another property's scenario, or a withdrawal does not repeat the levers),
    scenario.not_current (supersedes_id is not in force, or another default is in force) and
    scenario.sale_year_before_today (the sale year is not after today's year)."""
    return cmd.run(create_scenario, property_id, body)
