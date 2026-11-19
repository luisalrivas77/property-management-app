"""The one GET every price source uses (docs/phases/phase-6.md D167 (3), D174 (2)).

The standard library's urllib with a verifying TLS context, a timeout and a size cap, like
the TEM client (services/mail/tem.py): no HTTP client is a direct dependency of the API.
Behind a `Fetch` callable so the tests replay recorded files without a network.

A failure raises SourceError carrying the exception class and the HTTP status only, which
is all a caller may log (D167 (3), D174 (4)): a body can echo the request, and the
CoinGecko key travels in a header.

A source may POST a body (Amundi's product service, D174 amended 19 Nov 2026 (c)): the body
is passed to the Fetch only when there is one, so a Fetch written for GETs keeps working.

Redirects are never followed: a 3xx is a failed status like any other (SourceError
"HTTPError" with its status), so a header, the CoinGecko key among them, is only ever sent
to the URL the source names.
"""

import ssl
import urllib.error
import urllib.request
from collections.abc import Callable

USER_AGENT = "Hausbuch/1.0 (household finance workspace; one request a day per asset, own use)"
TIMEOUT_S = 20.0  # D174 (2)
MAX_BYTES = 5 * 1024 * 1024  # D174 (2): 5 MB

# (url, headers, timeout, max_bytes[, body]) -> (HTTP status, body). An HTTP error status is
# returned, not raised; a body over max_bytes raises ResponseTooLarge. A request body, when
# there is one, is the fifth argument and makes the request a POST.
Fetch = Callable[..., tuple[int, bytes]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect: urllib then raises the 3xx as an HTTPError."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        _NoRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context())
    )


class ResponseTooLarge(Exception):
    """The answer is longer than the cap; nothing of it is used."""


class SourceError(Exception):
    """A price source gave no usable answer. `kind` is an exception class name (or
    `parse` for a file the reader does not understand, `isin_mismatch` for one stating
    another fund, `not_listed` for an issuer that answered and lists no such fund), `status`
    the HTTP status when there was one: the only two things logged."""

    def __init__(self, kind: str, status: int | None = None) -> None:
        super().__init__(kind if status is None else f"{kind} {status}")
        self.kind = kind
        self.status = status


def urllib_fetch(
    url: str, headers: dict[str, str], timeout: float, max_bytes: int, body: bytes | None = None
) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url, data=body, headers=headers, method="GET" if body is None else "POST"
    )
    try:
        with _opener().open(request, timeout=timeout) as response:
            answer = response.read(max_bytes + 1)
            if len(answer) > max_bytes:
                raise ResponseTooLarge
            return response.status, answer
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        return status, b""


def get(
    fetch: Fetch,
    url: str,
    headers: dict[str, str] | None = None,
    *,
    max_bytes: int = MAX_BYTES,
    body: bytes | None = None,
) -> bytes:
    """The body of a 200 answer, or SourceError (class name and status only). With `body`
    the request is a POST of it."""
    sent = {"User-Agent": USER_AGENT} | (headers or {})
    extra = () if body is None else (body,)
    try:
        status, answer = fetch(url, sent, TIMEOUT_S, max_bytes, *extra)
    except Exception as exc:  # noqa: BLE001 - the class name only: a message can quote data
        raise SourceError(type(exc).__name__) from None
    if status != 200:
        raise SourceError("HTTPError", status)
    return answer
