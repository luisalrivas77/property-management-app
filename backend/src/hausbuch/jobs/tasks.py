"""Procrastinate task registration (phase-2.md §5.1).

Tasks are registered on an App by register_tasks, so the worker's app (async connector, opened)
knows them; enqueueing goes through configure_task by name (jobs/enqueue.py) and needs no
registration. Task arguments are JSON: ids travel as strings.

The task gets the process's JobDeps from the worker's additional_context, and the attempt
number from its job (0 on the first run). Retrying is decided by run_document_read, which
raises RetryLater; the strategy below only turns that into Procrastinate's retry.
"""

from uuid import UUID

import procrastinate
from procrastinate.job_context import JobContext
from procrastinate.jobs import Job
from procrastinate.retry import BaseRetryStrategy, RetryDecision

from .document_read import TASK_NAME, JobDeps, RetryLater, run_document_read
from .enqueue import MATCH_IMPORT_TASK, MATCH_TRANSACTION_TASK, REMATCH_RULE_TASK, REMATCH_TASK
from .matching import (
    run_match_import,
    run_match_transaction,
    run_rematch_on_evidence,
    run_rematch_on_rule,
)
from .scrub import SCRUB_CRON, SCRUB_TASK, run_scrub_proposals

DEPS_KEY = "hausbuch_deps"


class RetryWhenAsked(BaseRetryStrategy):
    """Retry exactly when the task raised RetryLater, after its wait."""

    def get_retry_decision(self, *, exception: BaseException, job: Job) -> RetryDecision | None:
        if isinstance(exception, RetryLater):
            return RetryDecision(retry_in={"seconds": exception.wait_s})
        return None


def document_read(
    context: JobContext,
    *,
    workspace_id: str,
    document_id: str,
    proposal_id: str,
) -> None:
    deps: JobDeps = context.additional_context[DEPS_KEY]
    assert context.job is not None
    run_document_read(
        deps,
        workspace_id=UUID(workspace_id),
        document_id=UUID(document_id),
        proposal_id=UUID(proposal_id),
        attempt=context.job.attempts,
    )


def match_import(context: JobContext, *, workspace_id: str, import_id: str) -> None:
    deps: JobDeps = context.additional_context[DEPS_KEY]
    assert context.job is not None
    run_match_import(
        deps,
        workspace_id=UUID(workspace_id),
        import_id=UUID(import_id),
        attempt=context.job.attempts,
    )


def match_transaction(context: JobContext, *, workspace_id: str, transaction_id: str) -> None:
    deps: JobDeps = context.additional_context[DEPS_KEY]
    assert context.job is not None
    run_match_transaction(
        deps,
        workspace_id=UUID(workspace_id),
        transaction_id=UUID(transaction_id),
        attempt=context.job.attempts,
    )


def rematch_on_evidence(context: JobContext, *, workspace_id: str, document_id: str) -> None:
    deps: JobDeps = context.additional_context[DEPS_KEY]
    assert context.job is not None
    run_rematch_on_evidence(
        deps,
        workspace_id=UUID(workspace_id),
        document_id=UUID(document_id),
        attempt=context.job.attempts,
    )


def rematch_on_rule(context: JobContext, *, workspace_id: str, rule_id: str) -> None:
    deps: JobDeps = context.additional_context[DEPS_KEY]
    assert context.job is not None
    run_rematch_on_rule(
        deps,
        workspace_id=UUID(workspace_id),
        rule_id=UUID(rule_id),
        attempt=context.job.attempts,
    )


def scrub_proposals(context: JobContext, *, timestamp: int) -> None:
    """D19/D41, daily. `timestamp` is the scheduled time Procrastinate's periodic deferrer
    passes; the cutoff uses the process clock (deps.clock) instead."""
    deps: JobDeps = context.additional_context[DEPS_KEY]
    run_scrub_proposals(deps)


def register_tasks(app: procrastinate.App) -> procrastinate.App:
    """The queued tasks, plus scrub_proposals as a periodic task: the worker's periodic
    deferrer queues it at SCRUB_CRON. Procrastinate skips a schedule missed by more than
    10 minutes (a worker that was down at 03:15), so that day's scrub moves to the next day;
    the API container runs at min-scale 1, so that is rare and only delays a scrub."""
    retry = RetryWhenAsked()
    app.task(name=TASK_NAME, pass_context=True, retry=retry)(document_read)
    app.task(name=MATCH_IMPORT_TASK, pass_context=True, retry=retry)(match_import)
    app.task(name=MATCH_TRANSACTION_TASK, pass_context=True, retry=retry)(match_transaction)
    app.task(name=REMATCH_TASK, pass_context=True, retry=retry)(rematch_on_evidence)
    app.task(name=REMATCH_RULE_TASK, pass_context=True, retry=retry)(rematch_on_rule)
    # No retry: a failed scrub runs again the next day. No queueing lock (jobs/enqueue.py
    # says why); Procrastinate's periodic_defers table already defers one job per schedule
    # however many instances run a deferrer, and a duplicate run would scrub nothing.
    scrub = app.task(name=SCRUB_TASK, pass_context=True)(scrub_proposals)
    app.periodic(cron=SCRUB_CRON, periodic_id=SCRUB_TASK)(scrub)
    return app
