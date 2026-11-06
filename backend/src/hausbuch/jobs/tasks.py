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


def register_tasks(app: procrastinate.App) -> procrastinate.App:
    app.task(name=TASK_NAME, pass_context=True, retry=RetryWhenAsked())(document_read)
    return app
