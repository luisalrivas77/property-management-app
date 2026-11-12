"""The Scaleway Transactional Email client (docs/phases/phase-5.md D123).

One JSON POST per mail to the API's create-email endpoint, as Scaleway's own SDK and docs
send it (checked 12 Nov 2026 against scaleway-sdk-python's `tem/v1alpha1` api.py and
marshalling.py, and docs-content's "generate API keys for TEM with IAM" page; the www pages
answered 403):

    POST https://api.scaleway.com/transactional-email/v1alpha1/regions/{region}/emails
    X-Auth-Token: <secret key>
    {"from": {"email", "name"}, "to": [{"email"}], "subject", "text", "html", "project_id"}

A subject has at least ten characters (Scaleway's send-emails page). The answer
(`{"emails": [...]}`, status `new`) is not read: TEM queues the mail and delivers it later.

Nothing here logs. A failure raises MailSendError with a short code only, `http_<status>` or
`exc:<exception class>`, which the job stores as `notification.error_code` (64 characters at
most); never the secret key, the recipient's address, the subject or the body. The HTTP call
is the standard library's (no HTTP client is a direct dependency of the API), behind a
`Transport` so the unit test checks the request without a network.
"""

import json
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from hausbuch.settings import Settings

API_URL = "https://api.scaleway.com"
SENDER_NAME = "Hausbuch"
MIN_SUBJECT_LENGTH = 10  # Scaleway refuses a shorter subject
TIMEOUT_S = 30.0
MAX_ERROR_CODE = 64  # notification.error_code CHECK

# (url, headers, body, timeout) -> (HTTP status, response body)
Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, bytes]]


@dataclass(frozen=True)
class OutgoingMail:
    to: str = field(repr=False)
    subject: str = field(repr=False)
    text: str = field(repr=False)
    html: str = field(repr=False)


class MailSendError(Exception):
    """A mail was not accepted. `code` is all a caller may record or log."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code[:MAX_ERROR_CODE]


class Mailer(Protocol):
    def send(self, mail: OutgoingMail) -> None:
        """Hand the mail to the provider; raise MailSendError when it is not accepted."""
        ...


def urllib_transport(
    url: str, headers: dict[str, str], body: bytes, timeout: float
) -> tuple[int, bytes]:
    """POST with a verifying TLS context and a timeout. An HTTP error status is returned, not
    raised; its body is not read (it can echo the request)."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(  # noqa: S310 - the URL is API_URL, always https
            request, timeout=timeout, context=ssl.create_default_context()
        ) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        return status, b""


@dataclass(frozen=True)
class TemClient:
    secret_key: str = field(repr=False)
    project_id: str
    region: str
    mail_from: str = field(repr=False)
    transport: Transport = field(default=urllib_transport, repr=False)
    timeout_s: float = TIMEOUT_S

    @property
    def url(self) -> str:
        return f"{API_URL}/transactional-email/v1alpha1/regions/{self.region}/emails"

    def payload(self, mail: OutgoingMail) -> dict:
        return {
            "from": {"email": self.mail_from, "name": SENDER_NAME},
            "to": [{"email": mail.to}],
            "subject": mail.subject,
            "text": mail.text,
            "html": mail.html,
            "project_id": self.project_id,
        }

    def send(self, mail: OutgoingMail) -> None:
        if len(mail.subject) < MIN_SUBJECT_LENGTH:
            raise ValueError("a TEM subject has at least ten characters")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "X-Auth-Token": self.secret_key,
        }
        body = json.dumps(self.payload(mail), ensure_ascii=False).encode("utf-8")
        try:
            status, _ = self.transport(self.url, headers, body, self.timeout_s)
        except Exception as exc:  # noqa: BLE001 - the class name only: a message can quote
            raise MailSendError(f"exc:{type(exc).__name__}") from None
        if not 200 <= status < 300:
            raise MailSendError(f"http_{status}")


def configured(settings: Settings) -> bool:
    """Sending is set up: the secret key, the project and the sender address. Settings
    refuses a key without the other two, so the key alone decides in practice."""
    return None not in (settings.tem_secret_key, settings.tem_project_id, settings.mail_from)


def tem_client(settings: Settings, transport: Transport = urllib_transport) -> TemClient | None:
    """The client from the container's settings, or None while sending is not set up."""
    if not configured(settings):
        return None
    assert settings.tem_secret_key and settings.tem_project_id and settings.mail_from
    return TemClient(
        secret_key=settings.tem_secret_key,
        project_id=settings.tem_project_id,
        region=settings.tem_region,
        mail_from=settings.mail_from,
        transport=transport,
    )
