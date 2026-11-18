"""The household plan and its scenarios (docs/phases/phase-9.md §13, D248; phase 9 step 2).

Scenarios are append-only (D248): a correction or a withdrawal is a new row naming the row it
replaces (supersedes_id); services/household_scenarios.py holds the checks.

- GET /household-scenarios?include_history=: the scenarios in force, oldest first.
- POST /household-scenarios: one new row (201); 422 household_scenario.not_current (the
  superseded row is not in force, or another default is), .account_invalid (a reserve account
  of another kind), .one_off_outside (a one-off outside the horizon) or .withdrawal_invalid;
  404 for an unknown superseded row or account.
- POST /household-scenarios/{id}/withdraw: a withdrawal row (201); 404, or 422
  household_scenario.not_current when the row is no longer in force.
"""

from uuid import UUID

from hausbuch.schemas.plan import HouseholdScenario, HouseholdScenarioCreate
from hausbuch.services import household_scenarios as scenarios

from ..deps import Cmd, domain_router

router = domain_router()


@router.get("/household-scenarios")
def household_scenarios_list(cmd: Cmd, include_history: bool = False) -> list[HouseholdScenario]:
    return cmd.read(scenarios.list_scenarios, include_history=include_history)


@router.post("/household-scenarios", status_code=201)
def household_scenario_create(body: HouseholdScenarioCreate, cmd: Cmd) -> HouseholdScenario:
    return cmd.run(scenarios.create_scenario, body)


@router.post("/household-scenarios/{scenario_id}/withdraw", status_code=201)
def household_scenario_withdraw(scenario_id: UUID, cmd: Cmd) -> HouseholdScenario:
    return cmd.run(scenarios.withdraw_scenario, scenario_id)
