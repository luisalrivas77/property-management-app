"""The sync_wallets periodic task and the sync_wallet task: registration and a run without a
workspace (docs/phases/phase-7.md D182 (1)). The run itself is
tests/db/test_wallet_sync_core.py."""

import logging
from datetime import UTC, datetime

import procrastinate
from procrastinate import testing

from hausbuch.jobs import sync_wallets as job
from hausbuch.jobs import tasks
from hausbuch.jobs.document_read import JobDeps


def test_the_tasks_are_registered_daily_at_six_forty_five_under_one_lock():
    app = tasks.register_tasks(procrastinate.App(connector=testing.InMemoryConnector()))
    daily = app.tasks[job.SYNC_WALLETS_TASK]
    now = app.tasks[job.SYNC_WALLET_TASK]
    assert daily.lock == now.lock == job.SYNC_WALLETS_LOCK
    periodic = [p for p in app.periodic_registry.periodic_tasks.values() if p.task is daily]
    assert [p.cron for p in periodic] == [job.SYNC_WALLETS_CRON] == ["45 6 * * *"]
    assert not [p for p in app.periodic_registry.periodic_tasks.values() if p.task is now]


def test_no_workspace_logs_one_line(caplog):
    deps = JobDeps(
        engine=None,
        settings=None,
        blob_store=None,
        model_client=None,
        clock=lambda: datetime(2026, 12, 1, 6, 45, tzinfo=UTC),
    )
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.sync_wallets"):
        result = job.run_sync_wallets(deps, workspace_ids=[])
    assert (result.addresses, result.failed_workspaces) == (0, 0)
    [record] = caplog.records
    assert record.fields["event"] == "sync_wallets" and record.fields["movements_new"] == 0
