"""The matching jobs: match_import, match_transaction, rematch_on_evidence, rematch_waiting
(phase-2.md §5.1, §5.3, §8.1, D17, D23, D25, D26).

Plain synchronous code over the engine like jobs.document_read (jobs/tasks.py wraps it), so it
is tested with a fake model client and no queue.

- match_import: refuses an import that is not reconciled (§3.4), else queues one
  match_transaction per line, in one transaction.
- match_transaction: services.matching.match_line runs the deterministic order (rules,
  matchers, documents) and writes what it can. When nothing matched and the AI layer is on,
  the line is categorized by the model (Sonnet; one retry on the hard model when the answer
  is marked ambiguous, D17), the set is checked by code (rules.allocations.check_sum) and
  written as an `llm` set, auto-confirmed only when the workspace's auto_accept_llm is on and
  the category's threshold is met (rules.review.route). Code scopes the model's parts: the
  one property, and for a loan category the one loan, a payout with that loan's one
  Darlehensvertrag as its Beleg (D51). When a matcher's set lands in review
  and the AI layer is on, the model words its reasoning and question (Haiku); any failure
  there falls back to the matcher's template text.
- rematch_on_evidence: a new reading of an invoice, Bescheid, Grundsteuerbescheid or
  Wirtschaftsplan re-runs the deterministic order on the lines it may change (never the
  model); a change is written as a supersession (services.allocations).
- rematch_waiting: a change to what the deterministic order reads (a newly learned rule, a
  loan, a tenant's IBAN, a tenancy's parties or rent rows, an owner account) re-runs it
  (never the model) on the lines waiting in the inbox that the change may book
  (services.matching.waiting_lines): no rows, or a proposed set, never a line with confirmed
  rows. A rule or exact matcher hit auto-confirms by the usual routing, superseding a
  proposed set; a split that does not fit leaves the line as the order finds it. So an answer
  given once, or master data entered after the import, books the lines imported earlier.

The AI-enabled switch: with `HAUSBUCH_AI_ENABLED` off (or no client), no request is built and
an unmatched line stays without rows (inbox "Nicht zugeordnet"). No transaction is held open
across a model call; each call's ai_call row (with transaction_id) is committed at once, so
provenance exists before the answer is used. The model sees services.matching's minimized
input only (no IBAN beyond the last four characters, no tenant name).

Retries (§5.1): a database error or a retryable API error raises RetryLater (10 s, 60 s,
5 min) while attempts remain; after that, and on a 4xx, the line stays without rows (the dead
letter is the inbox item). A refusal or max_tokens stop, an answer that does not validate and
an answer whose parts do not sum to the line are sent once more; a second one leaves the line
unmatched. No log line carries a bank value, a model answer or an exception message: only
ids, codes and exception class names.
"""

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select

from hausbuch.ai.client import ModelCallError, ModelRequest, ModelResponse
from hausbuch.ai.provenance import record_ai_call
from hausbuch.ai.requests import (
    LLM_CONFIDENCE,
    categorize_household_request,
    categorize_request,
    explain_request,
    parse_explain,
    parse_match,
)
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.rules.allocations import check_sum
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
    LlmMatchOutput,
)
from hausbuch.schemas.enums import ActorType, ImportStatus
from hausbuch.services import matching
from hausbuch.services.context import Actor, new_command

from .document_read import MAX_RETRIES, RETRY_WAITS_S, JobDeps, RetryLater
from .enqueue import (
    MATCH_IMPORT_TASK,
    MATCH_TRANSACTION_TASK,
    REMATCH_TASK,
    REMATCH_WAITING_TASK,
    enqueue_match_transaction,
)

UNPARSABLE = "ai.unparsable"
REFUSED = "ai.refused"
SUM_MISMATCH = "allocation.sum_mismatch"
_RETRY_STOPS = frozenset({"refusal", "max_tokens"})
SYSTEM_ACTOR = Actor(ActorType.SYSTEM, MATCH_TRANSACTION_TASK)

logger = logging.getLogger("hausbuch.jobs.matching")


class _Retry(Exception):
    """A retryable failure (database, 429/5xx); class name only in the log."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class _GiveUp(Exception):
    """The model gave no usable answer; the line stays without rows."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _ai_on(deps: JobDeps) -> bool:
    return deps.settings.ai_enabled and deps.model_client is not None


def _retry_or_log(log: dict, attempt: int, kind: str) -> None:
    if attempt < MAX_RETRIES:
        wait_s = RETRY_WAITS_S[attempt]
        logger.warning("matching retried", extra={"fields": log | {"retry_in_s": wait_s}})
        raise RetryLater(wait_s)
    logger.error("matching gave up", extra={"fields": log | {"exc_type": kind}})


# --- match_import ---------------------------------------------------------------------------


def run_match_import(deps: JobDeps, *, workspace_id: UUID, import_id: UUID, attempt: int) -> int:
    """Queue match_transaction for every line of a reconciled import; returns how many. An
    import that is not reconciled is refused (nothing queued, §3.4)."""
    log = {"event": MATCH_IMPORT_TASK, "import_id": str(import_id), "attempt": attempt}
    kind = None
    try:
        with workspace_transaction(deps.engine, workspace_id) as conn:
            status = conn.execute(
                select(import_table.c.status).where(import_table.c.id == import_id)
            ).scalar_one_or_none()
            if status != ImportStatus.RECONCILED.value:
                logger.info("import not matched", extra={"fields": log | {"status": status}})
                return 0
            t = transaction_table.c
            ids = conn.execute(
                select(t.id)
                .where(t.import_id == import_id, t.excluded_as_duplicate_of.is_(None))
                .order_by(t.booking_date, t.id)
            ).scalars()
            count = 0
            for transaction_id in ids:
                enqueue_match_transaction(
                    conn, workspace_id=workspace_id, transaction_id=transaction_id
                )
                count += 1
    except Exception as exc:
        kind = type(exc).__name__
    if kind is not None:
        _retry_or_log(log, attempt, kind)
        return 0
    logger.info("import queued for matching", extra={"fields": log | {"lines": count}})
    return count


# --- match_transaction ----------------------------------------------------------------------


@dataclass(frozen=True)
class _Prepared:
    plan: matching.MatchPlan
    data: dict[str, Any] | None = None  # the categorize input, minimized
    amount: Any = None
    tx: Any = None
    property_id: UUID | None = None
    loan_id: UUID | None = None  # the one loan, for a loan category (D51)
    loan_contract_id: UUID | None = None  # its one Darlehensvertrag, for a payout (D51)
    private: bool = False  # a private account's line: the household request (D168)


def run_match_transaction(
    deps: JobDeps,
    *,
    workspace_id: UUID,
    transaction_id: UUID,
    attempt: int,
    rematch: bool = False,
) -> matching.MatchPlan:
    """One attempt for one line; returns what was done (for tests and the log)."""
    log = {"event": MATCH_TRANSACTION_TASK, "transaction_id": str(transaction_id)}
    log["attempt"] = attempt
    kind = None
    plan = matching.MatchPlan("skipped", "matching.failed")
    try:
        plan = _match(deps, workspace_id, transaction_id, rematch=rematch, log=log)
    except _Retry as retry:
        kind = retry.kind
    except _GiveUp as stop:  # no usable answer: the line stays unmatched (inbox item)
        plan = matching.MatchPlan("skipped", stop.code)
    except Exception as exc:  # a bug or the database: class name only
        kind = type(exc).__name__
    if kind is not None:
        _retry_or_log(log, attempt, kind)
        return plan
    logger.info(
        "line matched", extra={"fields": log | {"outcome": plan.outcome, "reason": plan.reason}}
    )
    return plan


def _match(
    deps: JobDeps, workspace_id: UUID, transaction_id: UUID, *, rematch: bool, log: dict
) -> matching.MatchPlan:
    key = deps.settings.iban_hmac_key
    ai = _ai_on(deps) and not rematch
    with workspace_transaction(deps.engine, workspace_id) as conn:
        ctx = new_command(conn, workspace_id, SYSTEM_ACTOR, now=deps.clock())
        plan = matching.match_line(
            ctx, transaction_id, iban_key=key, ai_available=ai, rematch=rematch
        )
        prepared = _Prepared(plan)
        if plan.outcome == "categorize":
            line = matching.load_line(conn, transaction_id)
            assert line is not None
            if line.private:
                # D168: the household input; code scopes only hh_property_cost to the one
                # property (D161), and no part of a private line gets a loan.
                prepared = _Prepared(
                    plan,
                    data=_payload(matching.household_categorize_input(conn, line)),
                    amount=line.tx.amount,
                    tx=line.tx,
                    property_id=matching.only_property(conn),
                    private=True,
                )
            else:
                data = matching.categorize_input(conn, line)
                property_id = matching.only_property(conn)
                loan_id, contract_id = matching.only_loan(conn, property_id)
                prepared = _Prepared(
                    plan,
                    data=_payload(data),
                    amount=line.tx.amount,
                    tx=line.tx,
                    property_id=property_id,
                    loan_id=loan_id,
                    loan_contract_id=contract_id,
                )
    if plan.outcome == "categorize":
        found, model = _categorize(deps, workspace_id, transaction_id, prepared, log)
        return _finish(deps, workspace_id, transaction_id, found, Actor(ActorType.AI, model))
    if plan.outcome == "explain":
        assert plan.allocation_set is not None
        found = _explain(deps, workspace_id, transaction_id, plan.allocation_set, log)
        return _finish(deps, workspace_id, transaction_id, found, SYSTEM_ACTOR)
    return plan


def _finish(
    deps: JobDeps, workspace_id: UUID, transaction_id: UUID, found: AllocationSet, actor: Actor
) -> matching.MatchPlan:
    with workspace_transaction(deps.engine, workspace_id) as conn:
        ctx = new_command(conn, workspace_id, actor, now=deps.clock())
        return matching.finish_line(ctx, transaction_id, found)


def _payload(data: matching.CategorizeInput) -> dict[str, Any]:
    payload = asdict(data)
    payload.pop("transaction_id")  # an id is no help to the model
    return payload


# --- The model calls ------------------------------------------------------------------------


def _categorize(
    deps: JobDeps, workspace_id: UUID, transaction_id: UUID, prepared: _Prepared, log: dict
) -> tuple[AllocationSet, str]:
    """The model's set (method llm), or _GiveUp. The ambiguous answer is asked once more of
    the hard model; when that one is unusable, the first answer stands."""
    assert prepared.data is not None

    def parse(response: ModelResponse) -> tuple[Any, Any, str | None]:
        try:
            out = parse_match(response.text)
        except ValueError:  # includes pydantic's ValidationError and JSON errors
            return None, None, UNPARSABLE
        drafts = _drafts(out, prepared)
        try:
            check_sum(prepared.amount, drafts)
        except RuleError:
            return None, out.model_dump(mode="json"), SUM_MISMATCH
        return (out, drafts), out.model_dump(mode="json"), None

    build = categorize_household_request if prepared.private else categorize_request
    request = build(deps.settings, prepared.data, hard=False)
    (out, drafts), call_id, model = _with_one_retry(
        deps, workspace_id, transaction_id, request, parse
    )
    if out.ambiguous:
        hard = build(deps.settings, prepared.data, hard=True)
        try:
            (out, drafts), call_id, model = _with_one_retry(
                deps, workspace_id, transaction_id, hard, parse
            )
        except _GiveUp as stop:
            log["hard_model"] = stop.code
    found = AllocationSet(
        transaction_id=transaction_id,
        allocations=drafts,
        method=AllocationMethod.LLM,
        confidence=LLM_CONFIDENCE[out.confidence],
        reasoning=out.reasoning,
        question=out.question,
        ai_call_id=call_id,
    )
    return found, model


_LOAN_CATEGORIES = frozenset(
    {
        AllocationCategory.LOAN_INTEREST,
        AllocationCategory.LOAN_PRINCIPAL,
        AllocationCategory.LOAN_FEES,
        AllocationCategory.LOAN_DISBURSEMENT,
    }
)


def _drafts(out: LlmMatchOutput, prepared: _Prepared) -> list[AllocationDraft]:
    """The model's parts as drafts, scoped by code: the one property, and for a loan category
    the one loan, a payout also carrying that loan's one contract as its Beleg (D51). The
    model never picks a record; with two loans the part has none and the user picks it."""
    drafts = []
    for part in out.allocations:
        period = matching.llm_period(part.service_period, prepared.tx, part.recurring)
        if prepared.private:
            property_cost = part.category is AllocationCategory.HH_PROPERTY_COST
            drafts.append(
                AllocationDraft(
                    amount=part.amount,
                    category=part.category,
                    property_id=prepared.property_id if property_cost else None,
                    service_period_start=None if period is None else period.start,
                    service_period_end=None if period is None else period.end,
                    recurring=part.recurring,
                )
            )
            continue
        loan_part = part.category in _LOAN_CATEGORIES
        payout = part.category is AllocationCategory.LOAN_DISBURSEMENT
        drafts.append(
            AllocationDraft(
                amount=part.amount,
                category=part.category,
                property_id=prepared.property_id,
                loan_id=prepared.loan_id if loan_part else None,
                document_id=prepared.loan_contract_id if payout else None,
                service_period_start=None if period is None else period.start,
                service_period_end=None if period is None else period.end,
                recurring=part.recurring,
            )
        )
    return drafts


def _explain(
    deps: JobDeps, workspace_id: UUID, transaction_id: UUID, found: AllocationSet, log: dict
) -> AllocationSet:
    """The matcher's set with the model's reasoning and question; the set unchanged (its
    template text) on any failure."""
    data = {
        "buchung": {"betrag": str(sum(a.amount for a in found.allocations))},
        "zuordnung": [
            {
                "kategorie": a.category.value,
                "betrag": str(a.amount),
                "zeitraum": None
                if a.service_period_start is None
                else f"{a.service_period_start} bis {a.service_period_end}",
            }
            for a in found.allocations
        ],
        "methode": found.method.value,
        "vorlage_begruendung": found.reasoning,
        "vorlage_frage": found.question,
    }

    def parse(response: ModelResponse) -> tuple[Any, Any, str | None]:
        try:
            out = parse_explain(response.text)
        except ValueError:
            return None, None, UNPARSABLE
        return out, out.model_dump(mode="json"), None

    request = explain_request(deps.settings, data)
    try:
        parsed, _, _ = _send(deps, workspace_id, transaction_id, request, parse)
    except (_Retry, _GiveUp) as failed:
        log["explain"] = failed.args[0]
        return found
    if parsed[0] is None:
        log["explain"] = parsed[2]
        return found
    out = parsed[0]
    return found.model_copy(update={"reasoning": out.reasoning, "question": out.question})


def _with_one_retry(
    deps: JobDeps,
    workspace_id: UUID,
    transaction_id: UUID,
    request: ModelRequest,
    parse: Callable[[ModelResponse], tuple[Any, Any, str | None]],
) -> tuple[Any, UUID, str]:
    """Send, record, parse; one more send on a refusal, max_tokens stop, invalid answer or
    sum mismatch (§5.3); a second one is _GiveUp."""
    code = UNPARSABLE
    for _ in range(2):
        (result, _, code), call_id, model = _send(
            deps, workspace_id, transaction_id, request, parse
        )
        if code is None:
            return result, call_id, model
    raise _GiveUp(code or UNPARSABLE)


def _send(
    deps: JobDeps,
    workspace_id: UUID,
    transaction_id: UUID,
    request: ModelRequest,
    parse: Callable[[ModelResponse], tuple[Any, Any, str | None]],
) -> tuple[tuple[Any, Any, str | None], UUID, str]:
    """One call and its ai_call row, committed before the answer is used. A retryable API
    error is _Retry (the job's retry table), any other API error _GiveUp."""
    assert deps.model_client is not None
    try:
        response = deps.model_client.send(request)
    except ModelCallError as error:
        with workspace_transaction(deps.engine, workspace_id) as conn:
            record_ai_call(
                conn,
                workspace_id=workspace_id,
                request=request,
                transaction_id=transaction_id,
                failure=error,
            )
        if error.retryable:
            raise _Retry(error.error_code) from None
        raise _GiveUp(error.error_code) from None
    if response.stop_reason in _RETRY_STOPS:
        parsed: tuple[Any, Any, str | None] = (None, None, REFUSED)
    else:
        parsed = parse(response)
    with workspace_transaction(deps.engine, workspace_id) as conn:
        call_id = record_ai_call(
            conn,
            workspace_id=workspace_id,
            request=request,
            transaction_id=transaction_id,
            response=response,
            output=parsed[1],
            error_code=parsed[2],
        )
    return parsed, call_id, response.model or request.model


# --- rematch_on_evidence --------------------------------------------------------------------


def run_rematch_on_evidence(
    deps: JobDeps, *, workspace_id: UUID, document_id: UUID, attempt: int
) -> list[matching.MatchPlan]:
    """Re-run the deterministic order on the lines this document may change, one
    transaction per line. Never the model; a change is a supersession."""
    log = {"event": REMATCH_TASK, "document_id": str(document_id), "attempt": attempt}
    kind = None
    plans: list[matching.MatchPlan] = []
    try:
        with workspace_transaction(deps.engine, workspace_id) as conn:
            ids = matching.lines_for_evidence(
                conn,
                document_id,
                iban_key=deps.settings.iban_hmac_key,
                today=deps.clock().date(),
            )
        for transaction_id in ids:
            with workspace_transaction(deps.engine, workspace_id) as conn:
                ctx = new_command(conn, workspace_id, SYSTEM_ACTOR, now=deps.clock())
                plans.append(
                    matching.match_line(
                        ctx,
                        transaction_id,
                        iban_key=deps.settings.iban_hmac_key,
                        ai_available=False,
                        rematch=True,
                    )
                )
    except Exception as exc:
        kind = type(exc).__name__
    if kind is not None:
        _retry_or_log(log, attempt, kind)
        return plans
    written = sum(1 for p in plans if p.outcome == "written")
    logger.info(
        "evidence rematched", extra={"fields": log | {"lines": len(plans), "written": written}}
    )
    return plans


# --- rematch_waiting ------------------------------------------------------------------------


def run_rematch_waiting(
    deps: JobDeps, *, workspace_id: UUID, trigger: str, attempt: int
) -> list[matching.MatchPlan]:
    """Re-run the deterministic order on the waiting lines the trigger ("<kind>:<id>",
    jobs.enqueue) may book, one transaction per line. Never the model. A line answered (or
    booked by another job) since the lines were picked is no longer waiting once its row is
    locked, and is left as it is. A rule disabled before the job runs picks no line; an
    unreadable trigger picks none either (logged, not retried)."""
    log = {"event": REMATCH_WAITING_TASK, "trigger": trigger, "attempt": attempt}
    kind, _, raw = trigger.partition(":")
    try:
        entity_id = UUID(raw)
    except ValueError:
        logger.error("rematch trigger unreadable", extra={"fields": log})
        return []
    failed = None
    plans: list[matching.MatchPlan] = []
    try:
        with workspace_transaction(deps.engine, workspace_id) as conn:
            ids = matching.waiting_lines(conn, kind, entity_id)
        for transaction_id in ids:
            with workspace_transaction(deps.engine, workspace_id) as conn:
                line = matching.load_line(conn, transaction_id, lock=True)
                if line is None or not line.waiting:
                    plans.append(matching.MatchPlan("skipped", "matching.answered_meanwhile"))
                    continue
                ctx = new_command(conn, workspace_id, SYSTEM_ACTOR, now=deps.clock())
                plans.append(
                    matching.match_line(
                        ctx,
                        transaction_id,
                        iban_key=deps.settings.iban_hmac_key,
                        ai_available=False,
                        rematch=True,
                    )
                )
    except Exception as exc:
        failed = type(exc).__name__
    if failed is not None:
        _retry_or_log(log, attempt, failed)
        return plans
    written = sum(1 for p in plans if p.outcome == "written")
    logger.info(
        "waiting lines rematched",
        extra={"fields": log | {"lines": len(plans), "written": written}},
    )
    return plans
