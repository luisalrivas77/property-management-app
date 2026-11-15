"""The month_end periodic task's registration and its run without a workspace
(docs/phases/phase-6.md D167 (5); phase 6 step 6). The capture itself is
tests/db/test_services_net_worth.py."""

import logging
from datetime import UTC, datetime

import procrastinate
from procrastinate import testing

from hausbuch.jobs import month_end as job
from hausbuch.jobs import tasks
from hausbuch.jobs.document_read import JobDeps


def test_the_task_is_registered_on_the_first_at_five_with_a_lock():
    app = tasks.register_tasks(procrastinate.App(connector=testing.InMemoryConnector()))
    task = app.tasks[job.MONTH_END_TASK]
    assert task.lock == job.MONTH_END_LOCK
    periodic = [p for p in app.periodic_registry.periodic_tasks.values() if p.task is task]
    assert [p.cron for p in periodic] == [job.MONTH_END_CRON] == ["0 5 1 * *"]


def test_no_workspace_logs_one_line_for_the_last_month_end(caplog):
    deps = JobDeps(
        engine=None,
        settings=None,
        blob_store=None,
        model_client=None,
        clock=lambda: datetime(2026, 12, 1, 5, tzinfo=UTC),
    )
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.month_end"):
        result = job.run_month_end(deps, workspace_ids=[])
    assert (result.as_of, result.outcomes, result.failed_workspaces) == ("2026-11-30", {}, 0)
    [record] = caplog.records
    assert record.fields["event"] == "month_end_stored" and record.fields["written"] == 0
