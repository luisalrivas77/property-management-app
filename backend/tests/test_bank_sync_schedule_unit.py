"""jobs/bank_sync_schedule.py without a database (docs/phases/phase-5.md D120): the task's
registration (06:30 with its own periodic id and a lock) and that it is inert without the
FinTS secrets; and a grep that its log line carries no secret."""

import logging
from pathlib import Path

import procrastinate
import pytest
from procrastinate import testing

from hausbuch.jobs import bank_sync_schedule as job
from hausbuch.jobs import tasks
from hausbuch.jobs.document_read import JobDeps
from hausbuch.settings import Settings

SOURCE = Path(job.__file__).read_text()


def test_the_task_is_registered_at_half_past_six_with_its_own_id_and_a_lock():
    app = tasks.register_tasks(procrastinate.App(connector=testing.InMemoryConnector()))
    task = app.tasks[job.SCHEDULE_TASK]
    assert job.SCHEDULE_TASK == "bank_sync_scheduled"
    assert task.lock == job.SCHEDULE_LOCK
    periodic = [p for p in app.periodic_registry.periodic_tasks.values() if p.task is task]
    assert [p.cron for p in periodic] == [job.SCHEDULE_CRON] == ["30 6 * * *"]
    assert [p.periodic_id for p in periodic] == [job.SCHEDULE_TASK]


def _settings(**overrides) -> Settings:
    values = {
        "database_url": "postgresql+psycopg://hausbuch_app@localhost/unused",
        "iban_hmac_key": bytes(range(32)),
        "session_key": bytes(range(1, 33)),
        "rp_id": "testserver",
        "origin": "https://testserver",
        "blob_store": "local",
        "blob_root": "/tmp/unused",
    } | overrides
    return Settings(**values)


@pytest.mark.parametrize(
    "overrides",
    [
        {},  # neither secret set (Settings refuses one without the other)
        {"fints_pin": "1234", "fints_product_id": " padded "},  # a product id HKVVB refuses
    ],
)
def test_inert_without_the_fints_secrets_and_reads_nothing(caplog, overrides):
    # engine None: a run that touched the database would fail.
    deps = JobDeps(engine=None, settings=_settings(**overrides), blob_store=None, model_client=None)
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.bank_sync_schedule"):
        result = job.run_bank_sync_scheduled(deps)
    assert result.status == "not_configured" and not result.outcomes
    [record] = caplog.records
    assert record.fields["status"] == "not_configured"


def test_the_log_carries_counts_and_codes_only():
    # The one log call passes the fields dict built from counters; no settings value, no
    # login, no IBAN is ever put into it.
    assert SOURCE.count("logger.") == 1
    for secret in ("fints_pin", "fints_product_id", "login_name", "iban"):
        assert secret not in SOURCE
