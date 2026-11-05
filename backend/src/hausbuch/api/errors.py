"""Exceptions -> ErrorResponse (docs/phases/phase-1-step-3.md §3.5, §3.6, §10.5).

One function, render(), maps every exception the API can meet to a status, an ErrorResponse
body and the fields the request log line may carry. It is used by the exception handlers
(RequestValidationError, HTTPException) and by the request middleware's catch-all, so every
error body has the same shape and no path falls back to a framework default.

Redaction rules, enforced here and tested by tests/api/test_no_echo.py:

- 422 bodies carry `type`, `loc` and `msg` per error, nothing else. FastAPI's errors already
  contain `input` (an IBAN, a tax number), `ctx` and `url`, so they are whitelisted; a
  pydantic.ValidationError raised inside a service is read with
  errors(include_input=False, include_url=False, include_context=False) and every entry is
  validated as a FieldError, which forbids extra keys: a leaked key fails loudly instead of
  reaching the client.
- A DBAPI error's text contains the offending values (a duplicate email names the email).
  Neither its text nor any traceback is returned or logged: the log gets its class, its
  constraint name and SQLSTATE, and the hausbuch frame it surfaced in.
- An exception message is logged only for exception classes defined in hausbuch itself,
  whose messages carry ids and stored dates by construction. Anything else (a KeyError, a
  framework ValidationException that holds the request body) is logged by class only.
"""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException

from hausbuch.rules.errors import RuleError
from hausbuch.schemas.api import ErrorKind, ErrorResponse, FieldError
from hausbuch.services.documents import DuplicateLinkError, LinkNotFoundError
from hausbuch.services.errors import (
    ConflictError,
    NotFoundError,
    RetentionHoldError,
    RuleViolationError,
    TenancyOverlapError,
)
from hausbuch.services.events import EventNotFoundError, InvalidEventError

# ValueErrors raised here are refusals with PII-free messages (§3.5); from anywhere else a
# ValueError is a bug, so a 500.
_REFUSAL_MODULES = ("hausbuch.services", "hausbuch.auth")
_OWN_PACKAGE = "hausbuch"
_RETRY_AFTER_S = "2"  # §3.7: a deploy's drain window
TENANCY_OVERLAP = "tenancy.overlap"
# The keys a 422 entry may carry (FieldError). Everything else in FastAPI's error dicts
# (input, ctx, url) is dropped.
_FIELD_ERROR_KEYS = ("type", "loc", "msg")


class ApiError(Exception):
    """Raised by the API layer itself for a response it decides (413, 415, 503, the
    schedule 409). `fields` are ErrorResponse fields; `message` must be PII-free."""

    def __init__(
        self,
        status: int,
        error: ErrorKind,
        message: str | None = None,
        *,
        headers: dict[str, str] | None = None,
        **fields: Any,
    ) -> None:
        super().__init__(message or error.value)
        self.status = status
        self.error = error
        self.message = message
        self.headers = headers or {}
        self.fields = fields


@dataclass
class Rendered:
    """What render() decided: the response, and the fields the request log line gets."""

    status: int
    body: ErrorResponse
    headers: dict[str, str] = field(default_factory=dict)
    log: dict[str, Any] = field(default_factory=dict)

    def response(self) -> JSONResponse:
        return JSONResponse(
            status_code=self.status,
            content=self.body.model_dump(mode="json", exclude_none=True),
            headers=self.headers,
        )


def render(exc: BaseException, request_id: UUID | None) -> Rendered:
    """The ErrorResponse for `exc` (§3.5). Order matters: several service errors subclass
    ValueError or LookupError, and pydantic's ValidationError is a ValueError too."""
    rendered = _classify(exc)
    rendered.body = rendered.body.model_copy(update={"request_id": request_id})
    rendered.log = {"error": rendered.body.error.value} | rendered.log
    if rendered.body.error is ErrorKind.INTERNAL:
        rendered.log |= describe_failure(exc)
    elif rendered.body.codes:
        rendered.log["codes"] = rendered.body.codes
    return rendered


def _classify(exc: BaseException) -> Rendered:
    kind = ErrorKind
    match exc:
        case ApiError():
            body = ErrorResponse(error=exc.error, message=exc.message, **exc.fields)
            return Rendered(exc.status, body, dict(exc.headers))
        case RequestValidationError():
            detail = [
                FieldError.model_validate({k: e[k] for k in _FIELD_ERROR_KEYS})
                for e in exc.errors()
            ]
            return Rendered(422, ErrorResponse(error=kind.VALIDATION, detail=detail))
        case ValidationError():
            detail = [
                FieldError.model_validate(e)
                for e in exc.errors(include_input=False, include_url=False, include_context=False)
            ]
            return Rendered(422, ErrorResponse(error=kind.VALIDATION, detail=detail))
        case RuleViolationError():
            body = ErrorResponse(
                error=kind.RULE_VIOLATION, codes=list(exc.codes), code=exc.code, message=str(exc)
            )
            return Rendered(422, body)
        case RuleError():
            body = ErrorResponse(
                error=kind.RULE_VIOLATION,
                codes=[exc.code],
                code=exc.code,
                at_number=exc.at_number,
                message=str(exc),
            )
            return Rendered(422, body)
        case InvalidEventError() if exc.code is not None:
            body = ErrorResponse(
                error=kind.RULE_VIOLATION, codes=[exc.code], code=exc.code, message=str(exc)
            )
            return Rendered(422, body)
        case InvalidEventError() if isinstance(exc.__cause__, ValidationError):
            # An event the services built wrongly: a bug, not a conflict. Its message embeds
            # the ValidationError's text, so it is not returned.
            return Rendered(500, ErrorResponse(error=kind.INTERNAL))
        case InvalidEventError():  # a concurrent correction of the same event
            return Rendered(409, ErrorResponse(error=kind.CONFLICT, message=str(exc)))
        case NotFoundError():
            body = ErrorResponse(
                error=kind.NOT_FOUND,
                message=str(exc),
                entity_type=str(exc.entity_type),
                entity_id=exc.entity_id,
            )
            return Rendered(404, body)
        case LinkNotFoundError() | EventNotFoundError():
            return Rendered(404, ErrorResponse(error=kind.NOT_FOUND, message=str(exc)))
        case TenancyOverlapError():
            body = ErrorResponse(
                error=kind.CONFLICT,
                code=TENANCY_OVERLAP,
                message=str(exc),
                constraint=exc.constraint,
            )
            return Rendered(409, body)
        case RetentionHoldError():
            body = ErrorResponse(error=kind.RETENTION_HOLD, message=str(exc), result=exc.result)
            return Rendered(409, body)
        case ConflictError():
            body = ErrorResponse(error=kind.CONFLICT, message=str(exc), constraint=exc.constraint)
            return Rendered(409, body)
        case DuplicateLinkError():
            return Rendered(409, ErrorResponse(error=kind.CONFLICT, message=str(exc)))
        case ValueError() if _raised_in(exc, _REFUSAL_MODULES):
            return Rendered(422, ErrorResponse(error=kind.INVALID, message=str(exc)))
        case StarletteHTTPException():
            return _http_exception(exc)
        case _:
            return Rendered(500, ErrorResponse(error=kind.INTERNAL))


# Framework HTTPExceptions: our own 401, the router's 404/405, the body parser's 400, the
# body limit's 413. Their `detail` is never passed on: a parser's text may quote the body.
_HTTP_KINDS = {
    401: (ErrorKind.UNAUTHENTICATED, None),
    403: (ErrorKind.FORBIDDEN_ORIGIN, None),
    404: (ErrorKind.NOT_FOUND, "no such endpoint"),
    405: (ErrorKind.NOT_FOUND, "no such endpoint for this method"),
    413: (ErrorKind.PAYLOAD_TOO_LARGE, None),
    415: (ErrorKind.UNSUPPORTED_MEDIA_TYPE, None),
    503: (ErrorKind.SHUTTING_DOWN, None),
}


def _http_exception(exc: StarletteHTTPException) -> Rendered:
    status = exc.status_code
    if status in _HTTP_KINDS:
        kind, message = _HTTP_KINDS[status]
    elif 400 <= status < 500:
        kind, message = ErrorKind.INVALID, "the request could not be read"
    else:
        status, kind, message = 500, ErrorKind.INTERNAL, None
    headers = {"Retry-After": _RETRY_AFTER_S} if kind is ErrorKind.SHUTTING_DOWN else {}
    return Rendered(status, ErrorResponse(error=kind, message=message), headers)


def shutting_down() -> ApiError:
    """503 while the process drains (§3.7)."""
    return ApiError(
        503,
        ErrorKind.SHUTTING_DOWN,
        "the server is restarting; retry shortly",
        headers={"Retry-After": _RETRY_AFTER_S},
    )


# --- Describing a failure for the log, without its data -----------------------------


def describe_failure(exc: BaseException) -> dict[str, Any]:
    """Log fields for a 500: the class, where it surfaced in hausbuch, and, only for
    hausbuch's own exception classes, the message. Never a traceback, never a DBAPI text,
    and no message built from one (`InvalidEventError(f"...: {exc}") from exc` embeds a
    ValidationError's text, input values included)."""
    fields: dict[str, Any] = {"exc_type": _qualified_name(type(exc))}
    where = _innermost_own_frame(exc)
    if where is not None:
        fields["exc_where"] = where
    if isinstance(exc, DBAPIError):
        orig = getattr(exc, "orig", None)
        diag = getattr(orig, "diag", None)
        fields["constraint"] = getattr(diag, "constraint_name", None)
        fields["sqlstate"] = getattr(orig, "sqlstate", None)
    elif _is_own(cls_module=type(exc).__module__) and not isinstance(
        exc.__cause__, ValidationError | DBAPIError
    ):
        fields["exc_message"] = str(exc)
    cause = exc.__cause__
    if cause is None and not exc.__suppress_context__:
        cause = exc.__context__
    if cause is not None:
        fields["exc_cause"] = _qualified_name(type(cause))  # its class only, never its text
    return fields


def _is_own(*, cls_module: str) -> bool:
    return cls_module == _OWN_PACKAGE or cls_module.startswith(_OWN_PACKAGE + ".")


def _qualified_name(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def _frames(exc: BaseException):
    tb = exc.__traceback__
    while tb is not None:
        yield tb
        tb = tb.tb_next


def _module_of(tb) -> str:
    return tb.tb_frame.f_globals.get("__name__", "")


def _raised_in(exc: BaseException, prefixes: tuple[str, ...]) -> bool:
    """Whether the innermost frame of the traceback (where `raise` ran) is in one of the
    modules (or their submodules)."""
    innermost = None
    for tb in _frames(exc):
        innermost = tb
    if innermost is None:
        return False
    module = _module_of(innermost)
    return any(module == p or module.startswith(p + ".") for p in prefixes)


def _innermost_own_frame(exc: BaseException) -> str | None:
    found = None
    for tb in _frames(exc):
        module = _module_of(tb)
        if _is_own(cls_module=module):
            found = f"{module}:{tb.tb_frame.f_code.co_name}:{tb.tb_lineno}"
    return found


# --- Wiring ---------------------------------------------------------------------------


def request_id_of(request: Request) -> UUID | None:
    return getattr(request.state, "request_id", None)


async def _handle(request: Request, exc: Exception) -> JSONResponse:
    rendered = render(exc, request_id_of(request))
    request.state.error_log = rendered.log
    return rendered.response()


def install_error_handlers(app: FastAPI) -> None:
    """Replace FastAPI's default 422 and HTTPException handlers, which echo input and
    `detail`. Every other exception reaches the request middleware's catch-all (logging.py),
    which renders it with the same render()."""
    app.add_exception_handler(RequestValidationError, _handle)
    app.add_exception_handler(StarletteHTTPException, _handle)


# OpenAPI: every API operation documents ErrorResponse as its error body, so the generated
# client types `error` (and FastAPI's own HTTPValidationError default disappears).
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    "default": {
        "model": ErrorResponse,
        "description": "Error (ErrorResponse; docs/phases/phase-1-step-3.md §3.5)",
    }
}
