"""The health_daily periodic task's registration and its run without a workspace
(docs/phases/phase-9.md §11.5; phase 9 step 1). The storing itself is
tests/db/test_services_health_snapshots.py."""

import logging
from datetime import UTC, datetime

import procrastinate
from procrastinate import testing

from hausbuch.jobs import health_daily as job
from hausbuch.jobs import tasks
from hausbuch.jobs.document_read import JobDeps


def test_the_task_is_registered_daily_at_half_past_eight_with_a_lock():
    app = tasks.register_tasks(procrastinate.App(connector=testing.InMemoryConnector()))
    task = app.tasks[job.HEALTH_DAILY_TASK]
    assert task.lock == job.HEALTH_DAILY_LOCK
    periodic = [p for p in app.periodic_registry.periodic_tasks.values() if p.task is task]
    assert [p.cron for p in periodic] == [job.HEALTH_DAILY_CRON] == ["30 8 * * *"]
    assert [p.periodic_id for p in periodic] == [job.HEALTH_DAILY_TASK]


def test_no_workspace_logs_one_line(caplog):
    deps = JobDeps(
        engine=None,
        settings=None,
        blob_store=None,
        model_client=None,
        clock=lambda: datetime(2027, 4, 10, 8, 30, tzinfo=UTC),
    )
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.health_daily"):
        result = job.run_health_daily(deps, workspace_ids=[])
    assert (result.stored, result.failed_workspaces) == (0, 0)
    [record] = caplog.records
    assert record.fields == {
        "event": "health_snapshots_stored",
        "stored": 0,
        "failed_workspaces": 0,
    }
