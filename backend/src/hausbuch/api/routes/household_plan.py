"""The household plan and its scenarios (docs/phases/phase-9.md §13, D248; phase 9 step 2).

Scenarios are append-only (D248): a correction or a withdrawal is a new row naming the row it
replaces (supersedes_id); services/household_scenarios.py holds the checks.

- GET /household-scenarios?include_history=: the scenarios in force, oldest first.
- POST /household-scenarios: one new row (201); 422 household_scenario.not_current (the
  superseded row is not in force, or another default is), .account_invalid (a reserve account
  of another kind), .one_off_outside (a one-off after the horizon's end) or .withdrawal_invalid;
  404 for an unknown superseded row or account.
- POST /household-scenarios/{id}/withdraw: a withdrawal row (201); 404, or 422
  household_scenario.not_current when the row is no longer in force.
- GET /household-plan?scenario=: the plan on read for today (Berlin time), with the levers
  of the scenario named (any row, a withdrawn one too), else of the default in force, else
  the defaults; 404 for an unknown scenario. Missing inputs are to-dos, never a 422.
- POST /household-plan/preview {levers, scenario_id?} (D263 (c)): the plan for typed levers
  on the row named (else the default in force), checked as a new scenario row superseding it
  is (422 household_scenario.account_invalid, .one_off_outside, .spending_invalid,
  .budget_class_invalid; 404 for an unknown account or scenario); stores nothing and writes
  no event, so it runs as a read.
- GET /household-plan/comparison?month=YYYY-MM (D263 (d), §7.7): the month's spending beside
  the default scenario in force on its last day, today for the running month.
- GET /household-plan/explanation?scenario= (§8.6, D257; step 4): the newest stored
  explanation of the workspace's advice mode for the scenario (else the default in force),
  rendered from its own stored facts, with `stale`; with none stored, the template text
  rendered on read (`id` null; reason `consent_off` while ai_private_accounts is off); a
  stored model text the current validator rejects is shown as its template (`rejected`). 404
  for an unknown, superseded or withdrawn scenario. Never asks the model.
- GET /household-budget?scenario_id= (phase 10 §6, D272–D277): the Budget over today's window
  of closed months with the targets and class overrides of the scenario named, else of the
  default in force, else none; 404 for an unknown scenario. Phase 10 names the parameter
  `scenario_id` (the plan's endpoints call it `scenario`).
- POST /household-plan/explanation {scenario} („Neu erklären“): 202 with `queued`; queues one
  explain_plan job, nothing while ai_private_accounts is off (D253); 404 and nothing queued
  for an unknown, superseded or withdrawn scenario.
"""

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Query

from hausbuch.schemas.advice import (
    PlanExplanation,
    PlanExplanationQueued,
    PlanExplanationRequest,
)
from hausbuch.schemas.budget import BudgetView
from hausbuch.schemas.plan import (
    HouseholdPlan,
    HouseholdPlanPreview,
    HouseholdScenario,
    HouseholdScenarioCreate,
    SpendingComparison,
)
from hausbuch.services import advice as advice_service
from hausbuch.services import budget as budget_service
from hausbuch.services import household_scenarios as scenarios
from hausbuch.services import plan as plan_service
from hausbuch.services.documents import LOCAL_TIME_ZONE
from hausbuch.services.todo_texts import english

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


def _today() -> date:
    return datetime.now(LOCAL_TIME_ZONE).date()


@router.get("/household-plan")
def household_plan_get(cmd: Cmd, scenario: UUID | None = None) -> HouseholdPlan:
    return english(cmd.read(plan_service.household_plan, _today(), scenario))


@router.post("/household-plan/preview")
def household_plan_preview(body: HouseholdPlanPreview, cmd: Cmd) -> HouseholdPlan:
    return english(cmd.read(plan_service.preview_plan, _today(), body.levers, body.scenario_id))


@router.get("/household-plan/comparison")
def household_plan_comparison(
    cmd: Cmd, month: Annotated[str, Query(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]
) -> SpendingComparison:
    return english(cmd.read(plan_service.spending_comparison, _today(), month))


@router.get("/household-plan/explanation")
def household_plan_explanation(cmd: Cmd, scenario: UUID | None = None) -> PlanExplanation:
    return cmd.read(advice_service.explanation, _today(), scenario)


@router.post("/household-plan/explanation", status_code=202)
def household_plan_explain(body: PlanExplanationRequest, cmd: Cmd) -> PlanExplanationQueued:
    return cmd.run(advice_service.request, body.scenario)


@router.get("/household-budget")
def household_budget_get(cmd: Cmd, scenario_id: UUID | None = None) -> BudgetView:
    return english(cmd.read(budget_service.household_budget, _today(), scenario_id))
