"""jobs/inbound_mail.py without a database (docs/phases/phase-5.md D121, §8 invariants): the
task's registration, the TLS connection's settings, imaplib.Debug, and greps over the module
that it carries ids only into a job and calls no imaplib method that writes."""

import imaplib
import re
import ssl
from pathlib import Path

import procrastinate
from procrastinate import testing

from hausbuch.jobs import inbound_mail as job
from hausbuch.jobs import tasks

SOURCE = Path(job.__file__).read_text()


def test_the_task_is_registered_every_ten_minutes_with_a_lock():
    app = tasks.register_tasks(procrastinate.App(connector=testing.InMemoryConnector()))
    task = app.tasks[job.POLL_TASK]
    assert task.lock == job.POLL_LOCK
    periodic = [p for p in app.periodic_registry.periodic_tasks.values() if p.task is task]
    assert [p.cron for p in periodic] == [job.POLL_CRON] == ["*/10 * * * *"]


def test_the_whitelist_is_the_spec_list():
    assert {
        "CAPABILITY",
        "LOGIN",
        "EXAMINE",
        "UID SEARCH",
        "UID FETCH",
        "LOGOUT",
    } == job.ALLOWED_COMMANDS


def test_imaplib_debug_is_off():
    assert imaplib.Debug == 0


def test_the_connection_verifies_the_server_and_has_a_timeout(monkeypatch):
    seen = {}

    class Recorder:
        def __init__(self, host, port, *, ssl_context, timeout):
            seen.update(host=host, port=port, context=ssl_context, timeout=timeout)

    monkeypatch.setattr(imaplib, "IMAP4_SSL", Recorder)

    class S:
        inbox_imap_host = "imap.gmail.com"

    job.connect_imap(S())
    assert (seen["host"], seen["port"], seen["timeout"]) == ("imap.gmail.com", 993, 60)
    assert seen["context"].verify_mode is ssl.CERT_REQUIRED
    assert seen["context"].check_hostname is True


def test_the_module_defers_no_job_and_calls_no_writing_imap_method():
    assert "defer(" not in SOURCE
    code = "\n".join(line.split("#", 1)[0] for line in SOURCE.splitlines())
    # The IMAP object is only ever logged in, examined, searched/fetched and logged out.
    assert set(re.findall(r"\bimap\.(\w+)\(", code)) == {"login", "select", "uid", "logout"}
    assert re.findall(r"\bimap\.uid\(\s*\"(\w+)\"", code) == ["SEARCH", "FETCH"]
    # select() only read-only (EXAMINE); never the plain SELECT.
    assert re.findall(r"\bimap\.select\((.*)\)", code) == [
        "imap_mailbox(settings.inbox_label), readonly=True"
    ]
    assert "smtplib" not in code
