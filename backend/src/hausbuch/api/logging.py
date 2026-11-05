"""Request id, the one log line per request, and the catch-all for unhandled errors (§3.6).

RequestMiddleware is the outermost of the API's middlewares:

- request id: X-Request-Id from the client if it is a UUID, else a fresh uuid4; kept on
  request.state.request_id (Commands uses it as every event's correlation_id) and echoed in
  the X-Request-Id response header;
- catch-all: an exception nothing else handled is rendered by errors.render() as a 500
  ErrorResponse here, so it never reaches Starlette's ServerErrorMiddleware, which would
  re-raise it to the server and have the traceback (with a DBAPI error's values) logged;
- one log line, JSON: ts, level, request_id, method, route (the template, never the raw path
  or a query string), status, duration_ms, user_id, workspace_id, and for errors the fields
  errors.render() allows. Never a body, a header value or an exception's text beyond that.
"""

import json
import logging
import sys
import time
from datetime import UTC, datetime
from typing import Any, TextIO
from uuid import UUID, uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from hausbuch.schemas.api import ErrorKind, ErrorResponse

from .errors import Rendered, render

REQUEST_LOGGER = "hausbuch.api.request"
REQUEST_ID_HEADER = "x-request-id"
_MAX_REQUEST_ID_LENGTH = 64

logger = logging.getLogger(REQUEST_LOGGER)


def request_id_from(scope: Scope) -> UUID:
    """The client's X-Request-Id if it is a UUID (any spelling), else a fresh one."""
    for name, value in scope.get("headers", ()):
        if name == REQUEST_ID_HEADER.encode():
            if len(value) <= _MAX_REQUEST_ID_LENGTH:
                try:
                    return UUID(value.decode("ascii"))
                except ValueError:
                    pass
            break
    return uuid4()


class RequestMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = request_id_from(scope)
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        started = time.perf_counter()
        status: int | None = None
        header = (REQUEST_ID_HEADER.encode(), str(request_id).encode())

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = message | {"headers": [*message.get("headers", ()), header]}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception as exc:  # the catch-all: rendered, logged without its data
            rendered = _render_safely(exc, request_id)
            state["error_log"] = rendered.log
            if status is None:
                await rendered.response()(scope, receive, send_with_id)
            else:
                # The response had started: nothing can be sent. Not re-raised, so the server
                # does not log the traceback; the client sees a truncated response.
                state["error_log"] = rendered.log | {"response_started": True}
                status = 500
        finally:
            _log_request(scope, state, status, started)


def _render_safely(exc: Exception, request_id: UUID) -> Rendered:
    """render(), or a bare 500 if rendering itself fails (e.g. a validation error entry
    with a key FieldError does not allow): the failure is logged by class only."""
    try:
        return render(exc, request_id)
    except Exception as failure:
        body = ErrorResponse(error=ErrorKind.INTERNAL, request_id=request_id)
        log = {
            "error": ErrorKind.INTERNAL.value,
            "exc_type": type(exc).__name__,
            "render_failure": type(failure).__name__,
        }
        return Rendered(500, body, log=log)


def route_template(scope: Scope) -> str | None:
    """The matched route's path template with its router prefix (/api/tenants/{tenant_id}),
    or None when no route matched. Never the raw path: ids are fine, but a template groups
    the lines and cannot carry anything a client typed.

    FastAPI 0.162 resolves included routers lazily: scope["route"] is the router's own route
    (path without the /api prefix), and the full template is on the effective route context
    it records under scope["fastapi"]. tests/api/test_no_echo.py pins this."""
    context = scope.get("fastapi", {}).get("effective_route_context")
    template = getattr(context, "path_format", None)
    if isinstance(template, str) and template:
        return template
    return getattr(scope.get("route"), "path", None)


def _log_request(scope: Scope, state: dict, status: int | None, started: float) -> None:
    auth = state.get("auth")
    fields: dict[str, Any] = {
        "request_id": str(state["request_id"]),
        "method": scope.get("method"),
        "route": route_template(scope),
        "status": status,
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        "user_id": None if auth is None else str(auth.user_id),
        "workspace_id": None if auth is None else str(auth.workspace_id),
    }
    error = state.get("error_log")
    if error:
        fields |= error
    level = logging.ERROR if status is None or status >= 500 else logging.INFO
    logger.log(level, "request", extra={"fields": fields})


# --- Output: JSON lines on stdout (what Scaleway's log collector reads) ----------------


class JsonFormatter(logging.Formatter):
    """One JSON object per record: ts, level, logger, msg, plus the record's `fields`.

    Redaction: a record's exc_info is reduced to the exception's class name. A traceback
    carries every frame's exception text, which for a DBAPI error is the offending values;
    no logger in the process can put one on stdout through this formatter.
    """

    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            data |= fields
        if record.exc_info and record.exc_info[0] is not None:
            data["exc_type"] = record.exc_info[0].__name__
        return json.dumps(data, default=str, ensure_ascii=False)


def configure_logging(stream: TextIO = sys.stdout, level: int = logging.INFO) -> None:
    """Route every log record (hausbuch's, uvicorn's error log) through JsonFormatter on
    `stream`. For the ASGI entry point; run uvicorn with --no-access-log, since the request
    line above replaces its access log (which would print raw paths with query strings)."""
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers[:] = []
        uvicorn_logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
