"""jobs/notify.py without a database (docs/phases/phase-5.md D123): the task's registration
(06:00 with a queueing lock), the day and link it uses, inert without TEM, and greps over the
module that nothing it logs carries an address or a mail body."""

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

import procrastinate
from procrastinate import testing

from hausbuch.jobs import notify as job
from hausbuch.jobs import tasks
from hausbuch.jobs.document_read import JobDeps
from hausbuch.settings import Settings

SOURCE = Path(job.__file__).read_text()


def test_the_task_is_registered_at_six_with_a_lock():
    app = tasks.register_tasks(procrastinate.App(connector=testing.InMemoryConnector()))
    task = app.tasks[job.NOTIFY_TASK]
    assert task.lock == job.NOTIFY_LOCK
    periodic = [p for p in app.periodic_registry.periodic_tasks.values() if p.task is task]
    assert [p.cron for p in periodic] == [job.NOTIFY_CRON] == ["0 6 * * *"]


def test_today_is_the_day_in_berlin():
    # 23:30 UTC on 31 Dec is already 1 Jan in Berlin, as on the Übersicht.
    late = datetime(2026, 12, 31, 23, 30, tzinfo=UTC)
    assert job.local_today(late).isoformat() == "2027-01-01"
    assert job.local_today(datetime(2026, 5, 10, 6, 0, tzinfo=UTC)).isoformat() == "2026-05-10"


def test_the_link_is_the_overview():
    assert job.app_link("https://hausbuch.example.org") == "https://hausbuch.example.org/"
    assert job.app_link("https://hausbuch.example.org/") == "https://hausbuch.example.org/"


def _settings() -> Settings:
    return Settings(
        database_url="postgresql+psycopg://hausbuch_app@localhost/unused",
        iban_hmac_key=bytes(range(32)),
        session_key=bytes(range(1, 33)),
        rp_id="testserver",
        origin="https://testserver",
        blob_store="local",
        blob_root="/tmp/unused",
    )


def test_inert_without_tem_and_reads_nothing(caplog):
    # engine None: a run that touched the database would fail.
    deps = JobDeps(engine=None, settings=_settings(), blob_store=None, model_client=None)
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.notify"):
        result = job.run_send_reminders(deps)
    assert result.status == "not_configured" and result.mails == 0
    [record] = caplog.records
    assert record.fields["status"] == "not_configured"


def test_the_module_logs_counts_and_class_names_only():
    calls = re.findall(r"logger\.\w+\((.*?)\)\n", SOURCE, flags=re.S)
    assert calls
    for call in calls:
        for word in ("address", "email", "text", "html", "members", "str(exc)"):
            assert word not in call, call
    assert "exc_info" not in SOURCE
    assert "print(" not in SOURCE
