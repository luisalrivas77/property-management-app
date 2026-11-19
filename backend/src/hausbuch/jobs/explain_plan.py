"""The explain_plan job (docs/phases/phase-9.md §8.2, §8.6, D252–D257; phase 9 step 4).

One run writes exactly one plan_explanation row and its event plan.explanation_stored:

1. The facts are rebuilt here from the job's ids (the job never carries a figure or a
   label), under the workspace's current advice mode. ai_private_accounts is checked again:
   switched off since the job was queued, the row is the template with reason `consent_off`
   and no request is built (D253).
2. One Haiku request (ai/requests.explain_plan_request), no second tier. Its answer is parsed
   and run through the validator (rules/advice/validator). A rejected answer is asked for
   once more, the request naming the violation codes only, never the rejected text (D257).
3. A second rejection stores the template with reason `rejected`; a refusal or a cut-off
   answer (stop_reason `refusal` or `max_tokens`) `refused`; the API's 402 (billing, the
   Console's spend limit) `limit`; any other failed call, an answer that is not the schema's
   JSON, or no model client `error`. None of these is retried: the template is always
   there, and „Neu erklären“ asks again.

Each attempt is an ai_call row (job `explain`), written in its own transaction as soon as the
call returns, so the provenance survives a crash before the explanation is stored. The
plan_explanation row keeps the codes and positions of the rejected attempts, never their
text (which is in ai_call.output and raw_text as for every call).

No retry of the task itself (jobs/tasks.py): every outcome is stored.
"""

import logging
from dataclasses import dataclass, field
from uuid import UUID

from hausbuch.ai.client import ModelCallError, ModelResponse
from hausbuch.ai.provenance import record_ai_call
from hausbuch.ai.requests import explain_plan_request, parse_explain_plan
from hausbuch.db.session import workspace_transaction
from hausbuch.rules.advice import template
from hausbuch.rules.advice.validator import validate
from hausbuch.schemas.advice import (
    ExplainFacts,
    ExplanationReason,
    ExplanationStatus,
    Paragraph,
    Violation,
)
from hausbuch.schemas.enums import ActorType
from hausbuch.services.advice import explanation_facts, store_explanation
from hausbuch.services.context import Actor, new_command
from hausbuch.services.documents import LOCAL_TIME_ZONE
from hausbuch.services.workspace_settings import private_ai_allowed

from .document_read import JobDeps

SYSTEM_ACTOR = Actor(ActorType.SYSTEM, "explain_plan")
ATTEMPTS = 2  # the first answer and the one retry after a rejection (D257)
BILLING_STATUS = 402  # the API's billing_error: the Console's monthly spend limit reached
OUTPUT_INVALID = "ai.output_invalid"

logger = logging.getLogger("hausbuch.jobs.explain_plan")


@dataclass
class ExplainResult:
    explanation_id: UUID
    status: ExplanationStatus
    reason: ExplanationReason | None
    ai_call_ids: list[UUID] = field(default_factory=list)


@dataclass
class _Outcome:
    status: ExplanationStatus = ExplanationStatus.TEMPLATE
    reason: ExplanationReason | None = ExplanationReason.ERROR
    paragraphs: list[Paragraph] | None = None
    violations: list[tuple[int, Violation]] = field(default_factory=list)
    ai_call_ids: list[UUID] = field(default_factory=list)
    prompt_version: str | None = None


def _record(
    deps: JobDeps,
    workspace_id: UUID,
    request,
    *,
    response: ModelResponse | None = None,
    failure: ModelCallError | None = None,
    output=None,
    error_code: str | None = None,
) -> UUID:
    with workspace_transaction(deps.engine, workspace_id) as conn:
        return record_ai_call(
            conn,
            workspace_id=workspace_id,
            request=request,
            response=response,
            failure=failure,
            output=output,
            error_code=error_code,
        )


def _ask(deps: JobDeps, workspace_id: UUID, facts: ExplainFacts) -> _Outcome:
    """Up to two calls; the outcome to store."""
    out = _Outcome()
    client = deps.model_client
    if not deps.settings.ai_enabled or client is None:
        return out  # template, error: no client for this workspace (no key, or unreadable)
    codes: list[str] = []
    for attempt in range(1, ATTEMPTS + 1):
        request = explain_plan_request(deps.settings, facts, rejected_codes=codes)
        out.prompt_version = request.prompt_version
        try:
            response = client.send(request)
        except ModelCallError as failure:
            out.ai_call_ids.append(_record(deps, workspace_id, request, failure=failure))
            out.reason = (
                ExplanationReason.LIMIT
                if failure.status == BILLING_STATUS
                else ExplanationReason.ERROR
            )
            return out
        if response.stop_reason in ("refusal", "max_tokens"):
            out.ai_call_ids.append(_record(deps, workspace_id, request, response=response))
            out.reason = ExplanationReason.REFUSED
            return out
        try:
            paragraphs = parse_explain_plan(response.text)
        except ValueError:
            out.ai_call_ids.append(
                _record(deps, workspace_id, request, response=response, error_code=OUTPUT_INVALID)
            )
            out.reason = ExplanationReason.ERROR
            return out
        result = validate(paragraphs, facts, facts.mode)
        out.ai_call_ids.append(
            _record(
                deps,
                workspace_id,
                request,
                response=response,
                output={
                    "paragraphs": [p.model_dump(mode="json") for p in paragraphs],
                    "violation_codes": sorted({v.code.value for v in result.violations}),
                },
            )
        )
        if result.ok:
            out.status, out.reason, out.paragraphs = ExplanationStatus.MODEL, None, paragraphs
            return out
        out.violations.extend((attempt, v) for v in result.violations)
        codes = [v.code.value for v in result.violations]
    out.reason = ExplanationReason.REJECTED
    return out


def run_explain_plan(
    deps: JobDeps,
    *,
    workspace_id: UUID,
    scenario_id: UUID | None,
    requested_by: UUID | None = None,
) -> ExplainResult:
    deps = deps.for_workspace(workspace_id)
    now = deps.clock()
    today = now.astimezone(LOCAL_TIME_ZONE).date()
    with workspace_transaction(deps.engine, workspace_id) as conn:
        allowed = private_ai_allowed(conn)
        facts = explanation_facts(conn, today, scenario_id)
    if allowed:
        outcome = _ask(deps, workspace_id, facts)
    else:
        outcome = _Outcome(reason=ExplanationReason.CONSENT_OFF)
    paragraphs = outcome.paragraphs
    if paragraphs is None:
        paragraphs = template.render(facts, facts.mode)
    with workspace_transaction(deps.engine, workspace_id) as conn:
        ctx = new_command(conn, workspace_id, SYSTEM_ACTOR, now=now)
        row_id = store_explanation(
            ctx,
            scenario_id=scenario_id,
            facts=facts,
            status=outcome.status,
            reason=outcome.reason,
            paragraphs=paragraphs,
            violations=outcome.violations,
            ai_call_ids=outcome.ai_call_ids,
            prompt_version=outcome.prompt_version,
            created_by=requested_by,
        )
    fields = {
        "event": "plan_explanation_stored",
        "status": outcome.status.value,
        "reason": None if outcome.reason is None else outcome.reason.value,
        "attempts": len(outcome.ai_call_ids),
    }
    logger.info("plan explanation stored", extra={"fields": fields})
    return ExplainResult(row_id, outcome.status, outcome.reason, outcome.ai_call_ids)
