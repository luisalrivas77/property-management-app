"""Same-origin check for unsafe methods (docs/phases/phase-1-step-3.md §6.6).

Defence in depth on top of SameSite=Lax cookies: every POST, PUT, PATCH and DELETE must
carry an Origin header equal to HAUSBUCH_ORIGIN, or it is refused with 403
forbidden_origin before routing, authentication or body parsing. Browsers send Origin on
every unsafe request, same-origin ones included; a request without it is not from the web
app. The public WebAuthn endpoints are covered too. There is no CORS middleware: the web
app proxies /api, so browser and API share one origin (§9.3).
"""

from starlette.types import ASGIApp, Receive, Scope, Send

from hausbuch.schemas.api import ErrorKind

from .errors import ApiError, render

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class OriginCheckMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in UNSAFE_METHODS:
            expected = scope["app"].state.settings.origin
            origins = [v for k, v in scope.get("headers", ()) if k == b"origin"]
            if origins != [expected.encode()]:
                refused = ApiError(403, ErrorKind.FORBIDDEN_ORIGIN, "cross-origin request refused")
                rendered = render(refused, scope.get("state", {}).get("request_id"))
                scope.setdefault("state", {})["error_log"] = rendered.log
                await rendered.response()(scope, receive, send)
                return
        await self.app(scope, receive, send)
