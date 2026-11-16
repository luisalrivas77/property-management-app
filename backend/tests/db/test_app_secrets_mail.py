"""Mail settings typed in the app (docs/phases/phase-6.md D260): the reminder digest builds
its Transactional Email client from the workspace's typed key, project and sender, an
incomplete set sends nothing, a typed piece that no longer decrypts sends nothing (never the
container's piece); the email-in poll logs in with the typed user and password, reads the
typed label, and stays off when a typed piece cannot be read. Over the fakes of
test_jobs_notify and test_services_inbound_mail; invented values only."""

import dataclasses

import pytest
from db.test_jobs_notify import (  # noqa: F401  (setup is a fixture)
    FakeMailer,
    _deps,
    _expected_due,
    setup,
)
from db.test_services_inbound_mail import (  # noqa: F401  (inbox is a fixture)
    IMAP_PASSWORD,
    IMAP_USER,
    LABEL,
    TOKEN,
    _mail,
    _pdf,
    inbox,
)
from pydantic import SecretStr

from hausbuch.jobs import notify
from hausbuch.schemas.app_secrets import AppSecretName as N
from hausbuch.services import app_secrets

pytestmark = pytest.mark.db

TEM_KEY = "typed-tem-secret-0001"
PROJECT = "7c1a2c56-58c1-4a34-9fd3-0b2d1d5f1e01"
SENDER = "erinnerung@typed.example"
OTHER_KEY = bytes(range(101, 133))


@pytest.fixture
def built(monkeypatch):
    """The settings each tem_client call was handed, and the one fake mailer it returns."""
    calls: list = []
    mailer = FakeMailer()

    def fake(settings, transport=None):
        calls.append(settings)
        return mailer if notify.tem_client.__wrapped__(settings) is not None else None

    fake.__wrapped__ = notify.tem_client
    monkeypatch.setattr(notify, "tem_client", fake)
    return calls, mailer


def _type(world, name, value, settings):
    world.cmd(app_secrets.set_secret, name, SecretStr(value), settings=settings)


def _run(world, deps):
    return notify.run_send_reminders(deps, workspace_ids=[world.ws])


def test_the_digest_uses_the_typed_trio(setup, app_engine, tmp_path, built):  # noqa: F811
    world, _, members, _ = setup
    calls, mailer = built
    deps = _deps(app_engine, tmp_path)
    assert _run(world, deps).status == "not_configured" and mailer.sent == []
    for name, value in ((N.TEM_SECRET_KEY, TEM_KEY), (N.TEM_PROJECT_ID, PROJECT)):
        _type(world, name, value, deps.settings)
    assert _run(world, deps).status == "not_configured"  # no sender yet
    _type(world, N.MAIL_FROM, SENDER, deps.settings)
    result = _run(world, deps)
    assert result.status == "done" and len(mailer.sent) == len(members)  # one digest each
    used = calls[-1]
    assert (used.tem_secret_key, used.tem_project_id, used.mail_from) == (TEM_KEY, PROJECT, SENDER)


def test_a_typed_piece_wins_and_an_unreadable_one_sends_nothing(
    setup,  # noqa: F811
    app_engine,
    tmp_path,
    built,
):
    world, *_ = setup
    calls, mailer = built
    container = {
        "tem_secret_key": "container-tem-key",
        "tem_project_id": "1b4e28ba-2fa1-4d3b-a3f5-ef19b5a7633b",
        "mail_from": "container@hausbuch.example",
    }
    deps = _deps(app_engine, tmp_path, **container)
    _type(world, N.MAIL_FROM, SENDER, deps.settings)
    assert _run(world, deps).status == "done"
    assert (calls[-1].tem_secret_key, calls[-1].mail_from) == ("container-tem-key", SENDER)
    sent = len(mailer.sent)
    rotated = dataclasses.replace(
        deps, settings=dataclasses.replace(deps.settings, session_key=OTHER_KEY)
    )
    assert _run(world, rotated).status == "not_configured"
    assert calls[-1].mail_from is None and len(mailer.sent) == sent


def _bare(deps):
    return dataclasses.replace(
        deps,
        settings=dataclasses.replace(
            deps.settings,
            inbox_imap_user=None,
            inbox_imap_password=None,
            inbox_address=None,
            inbox_label=None,
        ),
    )


def test_the_poll_logs_in_with_the_typed_inbox(inbox, run):  # noqa: F811
    poll, server, ws, deps = inbox
    bare = _bare(deps)
    assert poll(bare).status == "not_configured" and poll.connects == []
    for name, value in (
        (N.INBOX_IMAP_USER, IMAP_USER),
        (N.INBOX_IMAP_PASSWORD, IMAP_PASSWORD),
        (N.INBOX_ADDRESS, TOKEN),
        (N.INBOX_LABEL, LABEL),
    ):
        run(ws, app_secrets.set_secret, name, SecretStr(value), settings=bare.settings)
    server.add(LABEL, _mail(("rechnung.pdf", _pdf("invoice"), "application/pdf")))
    result = poll(bare)
    assert (result.status, result.accepted) == ("done", 1)
    assert poll.connects == [1]


def test_an_unreadable_typed_password_keeps_the_poll_off(inbox, run):  # noqa: F811
    poll, server, ws, deps = inbox
    run(
        ws,
        app_secrets.set_secret,
        N.INBOX_IMAP_PASSWORD,
        SecretStr("typed pw"),
        settings=deps.settings,
    )
    rotated = dataclasses.replace(
        deps, settings=dataclasses.replace(deps.settings, session_key=OTHER_KEY)
    )
    assert poll(rotated).status == "not_configured"
    assert poll.connects == []  # never the container's password
