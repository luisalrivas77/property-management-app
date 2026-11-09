"""Performance of a property (docs/phases/phase-4.md §6, §10): the projection's scenarios.

Scenarios are append-only rows (D87): a correction or a withdrawal is a new row naming the row
it replaces (supersedes_id); services/scenarios.py holds the checks.
"""

from uuid import UUID

from hausbuch.schemas.performance import Scenario, ScenarioCreate
from hausbuch.services.scenarios import create_scenario, list_scenarios

from ..deps import Cmd, domain_router

router = domain_router()


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
