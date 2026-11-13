"""The one GET every price source uses (docs/phases/phase-6.md D167 (3), D174 (2)).

The standard library's urllib with a verifying TLS context, a timeout and a size cap, like
the TEM client (services/mail/tem.py): no HTTP client is a direct dependency of the API.
Behind a `Fetch` callable so the tests replay recorded files without a network.

A failure raises SourceError carrying the exception class and the HTTP status only, which
is all a caller may log (D167 (3), D174 (4)): a body can echo the request, and the
CoinGecko key travels in a header.
"""

import ssl
import urllib.error
import urllib.request
from collections.abc import Callable

USER_AGENT = "Hausbuch/1.0 (household finance workspace; one request a day per asset, own use)"
TIMEOUT_S = 20.0  # D174 (2)
MAX_BYTES = 5 * 1024 * 1024  # D174 (2): 5 MB

# (url, headers, timeout, max_bytes) -> (HTTP status, body). An HTTP error status is
# returned, not raised; a body over max_bytes raises ResponseTooLarge.
Fetch = Callable[[str, dict[str, str], float, int], tuple[int, bytes]]


class ResponseTooLarge(Exception):
    """The answer is longer than the cap; nothing of it is used."""


class SourceError(Exception):
    """A price source gave no usable answer. `kind` is an exception class name (or
    `parse` for a file the reader does not understand), `status` the HTTP status when
    there was one: the only two things logged."""

    def __init__(self, kind: str, status: int | None = None) -> None:
        super().__init__(kind if status is None else f"{kind} {status}")
        self.kind = kind
        self.status = status


def urllib_fetch(
    url: str, headers: dict[str, str], timeout: float, max_bytes: int
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(  # noqa: S310 - every URL is a fixed https endpoint
            request, timeout=timeout, context=ssl.create_default_context()
        ) as response:
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ResponseTooLarge
            return response.status, body
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
) -> bytes:
    """The body of a 200 answer, or SourceError (class name and status only)."""
    sent = {"User-Agent": USER_AGENT} | (headers or {})
    try:
        status, body = fetch(url, sent, TIMEOUT_S, max_bytes)
    except Exception as exc:  # noqa: BLE001 - the class name only: a message can quote data
        raise SourceError(type(exc).__name__) from None
    if status != 200:
        raise SourceError("HTTPError", status)
    return body
