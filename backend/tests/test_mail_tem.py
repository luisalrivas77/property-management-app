"""The Scaleway Transactional Email client (docs/phases/phase-5.md D123): the request it sends,
checked against a fake transport, and what a failure keeps (a short code, never the key, the
address or the body). Invented values only."""

import json

import pytest

from hausbuch.services.mail import MailSendError, OutgoingMail, TemClient, configured, tem_client
from hausbuch.settings import Settings

KEY = "tem-secret-0123456789abcdef"
PROJECT = "11111111-2222-3333-4444-555555555555"
SENDER = "hausbuch@mail.example.org"
RECIPIENT = "eigentuemer.a@example.org"
MAIL = OutgoingMail(
    to=RECIPIENT,
    subject="Hausbuch: Erinnerungen",
    text="15.02.2027 · Grundsteuer Wohnung A überweisen",
    html="<p>15.02.2027 · Grundsteuer Wohnung A überweisen</p>",
)


class FakeTransport:
    def __init__(self, status: int = 200, raises: Exception | None = None) -> None:
        self.status = status
        self.raises = raises
        self.calls: list[tuple[str, dict[str, str], bytes, float]] = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, body, timeout))
        if self.raises is not None:
            raise self.raises
        return self.status, b'{"emails": [{"id": "x", "status": "new"}]}'


def _client(transport, region: str = "fr-par") -> TemClient:
    return TemClient(
        secret_key=KEY, project_id=PROJECT, region=region, mail_from=SENDER, transport=transport
    )


def test_the_request_is_scaleways_create_email():
    transport = FakeTransport()
    _client(transport).send(MAIL)
    [(url, headers, body, timeout)] = transport.calls
    assert url == "https://api.scaleway.com/transactional-email/v1alpha1/regions/fr-par/emails"
    assert headers["X-Auth-Token"] == KEY
    assert headers["Content-Type"].startswith("application/json")
    assert json.loads(body.decode("utf-8")) == {
        "from": {"email": SENDER, "name": "Hausbuch"},
        "to": [{"email": RECIPIENT}],
        "subject": "Hausbuch: Erinnerungen",
        "text": MAIL.text,
        "html": MAIL.html,
        "project_id": PROJECT,
    }
    assert 0 < timeout <= 60


def test_the_region_is_in_the_path():
    transport = FakeTransport()
    _client(transport, region="nl-ams").send(MAIL)
    assert "/regions/nl-ams/emails" in transport.calls[0][0]


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 503])
def test_an_error_status_is_a_code_only(status):
    with pytest.raises(MailSendError) as caught:
        _client(FakeTransport(status=status)).send(MAIL)
    assert caught.value.code == f"http_{status}"
    for secret in (KEY, RECIPIENT, MAIL.text):
        assert secret not in str(caught.value)


def test_a_transport_failure_keeps_the_class_name_only():
    failure = TimeoutError(f"timed out sending to {RECIPIENT} with {KEY}")
    with pytest.raises(MailSendError) as caught:
        _client(FakeTransport(raises=failure)).send(MAIL)
    assert caught.value.code == "exc:TimeoutError"
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert KEY not in str(caught.value) and RECIPIENT not in str(caught.value)


def test_a_short_subject_is_refused_before_sending():
    transport = FakeTransport()
    with pytest.raises(ValueError):
        _client(transport).send(
            OutgoingMail(to=RECIPIENT, subject="Kurz", text="x", html="<p>x</p>")
        )
    assert transport.calls == []


def test_nothing_secret_in_repr():
    client = _client(FakeTransport())
    for secret in (KEY, SENDER):
        assert secret not in repr(client)
    for secret in (RECIPIENT, MAIL.text, MAIL.subject):
        assert secret not in repr(MAIL)


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


def test_the_client_needs_all_three_settings():
    assert not configured(_settings())
    assert tem_client(_settings()) is None
    full = _settings(tem_secret_key=KEY, tem_project_id=PROJECT, mail_from=SENDER)
    assert configured(full)
    client = tem_client(full, transport=FakeTransport())
    assert client is not None
    assert (client.project_id, client.region) == (PROJECT, "fr-par")
