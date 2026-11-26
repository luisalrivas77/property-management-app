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
- GET /household-goal (phase 10 §7.9 (d), D289, D298): the goal of the default scenario in
  force with its figures, word and spending scenarios on read for today (Berlin time); word
  `none` without a default scenario or a goal. A goal is saved, and a spending scenario
  adopted („Als Budget übernehmen“: its `spending_changes` and `goal_choice`), through
  POST /household-scenarios superseding the default (422 household_scenario.goal_invalid for
  a new or altered goal not due after the current month).
- GET /household-goal/plan?view=own|share&round_amounts=&accounts=&wants=&risks=&assumptions=
  (phase 10 §7.11 (h), D306, „Euer Plan“): the goal's seven cards from its one walk, on read for
  today; `own` („Für euch“) with everything, `share` („Zum Teilen“) rounded by default and built
  from rules/goal/share with the five switches (defaults: rounded, no accounts, no wants, risks
  and assumptions shown), a part a switch leaves out absent from the JSON and the to-dos and the
  properties' labels never in it. Word `none` without a default scenario or a goal. The
  switches change nothing stored.
- GET /household-goal/tracking (phase 10 §7.9 (e), D298 (5)–(7)): „Für euer Ziel“ on Anlagen ›
  Übersicht, the return followed against the money put in and the value change since the day
  chosen (services.goal_tracking); `shown` false in `generic` mode, without a goal or without
  a return followed.
- GET /household-goal/properties (phase 10 §7.9 (e) items 1, 2, D289, D298): per property the
  goal's walk sells at or after the due month with its default scenario, the yearly value
  growth it alone would need beside the German house price index's windows
  (services.goal_properties); `shown` false in `generic` mode, without a goal, for a goal that
  is not a net worth or one the walk cannot answer.
- POST /household-goal/preview {levers, scenario_id?} (phase 10 §7.10 (a), D299): the goal
  for typed levers, checked and run on the same row as POST /household-plan/preview (same
  422s and 404s); word `none` when the levers hold no goal; stores nothing, so it runs as a
  read („Durchspielen“ shows the goal with the changes beside the saved one).
- POST /household-goal/reference-returns/refresh (phase 10 §7.11 (c) 3, point 8): 202; queues
  one refresh_reference_returns job, which downloads route 3's funds series again, due or
  not; `queued` false and the open job's id while one waits or runs. Writes nothing else:
  the download's insert moves the data version, so the goal is computed again.
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
from hausbuch.schemas.goal import (
    GoalProperties,
    GoalTracking,
    HouseholdGoal,
    ReferenceRefreshQueued,
)
from hausbuch.schemas.plan import (
    HouseholdPlan,
    HouseholdPlanPreview,
    HouseholdScenario,
    HouseholdScenarioCreate,
    SpendingComparison,
)
from hausbuch.schemas.plan_page import PlanPage, PlanSwitches, PlanView
from hausbuch.services import advice as advice_service
from hausbuch.services import budget as budget_service
from hausbuch.services import goal as goal_service
from hausbuch.services import goal_properties, goal_tracking, reference_returns
from hausbuch.services import household_scenarios as scenarios
from hausbuch.services import plan as plan_service
from hausbuch.services import plan_page as plan_page_service
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


@router.get("/household-goal")
def household_goal_get(cmd: Cmd) -> HouseholdGoal:
    return english(cmd.read(goal_service.goal, _today()))


@router.get("/household-goal/tracking")
def household_goal_tracking(cmd: Cmd) -> GoalTracking:
    return cmd.read(goal_tracking.goal_tracking, _today())


@router.get("/household-goal/properties")
def household_goal_properties(cmd: Cmd) -> GoalProperties:
    return cmd.read(goal_properties.goal_properties, _today())


@router.get("/household-goal/plan")
def household_goal_plan(
    cmd: Cmd,
    view: PlanView = PlanView.OWN,
    round_amounts: bool = True,
    accounts: bool = False,
    wants: bool = False,
    risks: bool = True,
    assumptions: bool = True,
) -> PlanPage:
    switches = PlanSwitches(
        round_amounts=round_amounts,
        accounts=accounts,
        wants=wants,
        risks=risks,
        assumptions=assumptions,
    )
    return english(cmd.read(plan_page_service.plan_page, _today(), view, switches))


@router.post("/household-goal/preview")
def household_goal_preview(body: HouseholdPlanPreview, cmd: Cmd) -> HouseholdGoal:
    return english(cmd.read(goal_service.preview_goal, _today(), body.levers, body.scenario_id))


@router.post("/household-goal/reference-returns/refresh", status_code=202)
def household_goal_reference_refresh(cmd: Cmd) -> ReferenceRefreshQueued:
    job_id, queued = cmd.run(reference_returns.queue_refresh)
    return ReferenceRefreshQueued(job_id=job_id, queued=queued)
