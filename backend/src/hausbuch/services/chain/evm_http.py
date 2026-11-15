"""The HTTP of the EVM sources (docs/phases/phase-7.md D181 (b), (c), D182 (1)): Etherscan's GET
through services/prices/http.get and a JSON-RPC POST with the same guarantees (a verifying
TLS context, no redirect followed, a 20 s timeout, a 5 MB cap), behind callables so the tests
replay recorded answers without a network.

A failure is a ChainSourceError carrying an error code and the HTTP status only: never the
URL (Etherscan's key is a query parameter, an RPC provider's often sits in its path), a body
or an exception's message. A 429 is `wallet.source_throttled`, a 401 or 403
`wallet.source_refused`; anything else `wallet.source_failed` (SOURCE_FAILED below: errors.py
has no such code yet, the report asks for it).
"""

import json
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable

from hausbuch.rules import errors as E
from hausbuch.services.chain.base import ChainSourceError
from hausbuch.services.prices.http import (
    MAX_BYTES,
    TIMEOUT_S,
    USER_AGENT,
    Fetch,
    ResponseTooLarge,
    SourceError,
    get,
)

SOURCE_FAILED = "wallet.source_failed"  # no answer the sync can use; errors.py lacks the code

# (url, headers, body, timeout, max_bytes) -> (HTTP status, body); an HTTP error status is
# returned, not raised; a body over max_bytes raises ResponseTooLarge.
Post = Callable[[str, dict[str, str], bytes, float, int], tuple[int, bytes]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def urllib_post(
    url: str, headers: dict[str, str], body: bytes, timeout: float, max_bytes: int
) -> tuple[int, bytes]:
    opener = urllib.request.build_opener(
        _NoRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context())
    )
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with opener.open(request, timeout=timeout) as response:
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ResponseTooLarge
            return response.status, data
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        return status, b""


def by_status(status: int | None) -> ChainSourceError:
    if status == 429:
        return ChainSourceError(E.WALLET_SOURCE_THROTTLED, status)
    if status in (401, 403):
        return ChainSourceError(E.WALLET_SOURCE_REFUSED, status)
    return ChainSourceError(SOURCE_FAILED, status)


def _json(body: bytes) -> object:
    try:
        return json.loads(body)
    except ValueError:
        raise ChainSourceError(SOURCE_FAILED) from None


def get_json(fetch: Fetch, url: str) -> object:
    try:
        body = get(fetch, url)
    except SourceError as exc:
        raise by_status(exc.status) from None
    return _json(body)


def post_json(post: Post, url: str, payload: object) -> object:
    headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
    try:
        status, body = post(url, headers, json.dumps(payload).encode(), TIMEOUT_S, MAX_BYTES)
    except Exception:  # noqa: BLE001 - nothing of it kept: a message can quote the URL
        raise ChainSourceError(SOURCE_FAILED) from None
    if status != 200:
        raise by_status(status)
    return _json(body)
