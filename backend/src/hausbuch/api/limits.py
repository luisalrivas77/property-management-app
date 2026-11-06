"""Request body limits (§4.7, Q13).

FastAPI parses a multipart body (spooling the file to disk) before the route runs, so a cap
checked in the route alone would still accept any size first. This middleware bounds every
body at the transport level:

- POST /api/documents and POST /api/accounts/{id}/imports (a statement upload is a document):
  HAUSBUCH_MAX_UPLOAD_BYTES plus room for the multipart framing; the route then checks the
  file part itself against HAUSBUCH_MAX_UPLOAD_BYTES exactly;
- every other request: JSON_BODY_LIMIT (no JSON body of this API comes near it).

A Content-Length over the limit is refused with 413 before anything is read. A body without
one (chunked) is counted as it streams; past the limit the read fails with a 413
HTTPException, which FastAPI's body parsing passes through to the error handler.
"""

import re

from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from hausbuch.schemas.api import ErrorKind

from .errors import ApiError, render

UPLOAD_PATH = "/api/documents"
IMPORT_PATH = re.compile(r"/api/accounts/[0-9a-fA-F-]{36}/imports")
MULTIPART_OVERHEAD = 64 * 1024  # boundaries, part headers, the `source` field
JSON_BODY_LIMIT = 1024 * 1024


def body_limit(scope: Scope) -> int:
    path = scope["path"]
    if scope["method"] == "POST" and (path == UPLOAD_PATH or IMPORT_PATH.fullmatch(path)):
        return scope["app"].state.settings.max_upload_bytes + MULTIPART_OVERHEAD
    return JSON_BODY_LIMIT


def too_large() -> ApiError:
    return ApiError(413, ErrorKind.PAYLOAD_TOO_LARGE, "the request body is too large")


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = body_limit(scope)
        declared = _content_length(scope)
        if declared is not None and declared > limit:
            rendered = render(too_large(), scope.get("state", {}).get("request_id"))
            scope.setdefault("state", {})["error_log"] = rendered.log
            await rendered.response()(scope, receive, send)
            return

        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise HTTPException(status_code=413)
            return message

        await self.app(scope, counting_receive, send)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", ()):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None
