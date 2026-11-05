"""The error envelope (§3.5): the rows the router tests do not already show, the Origin
check (§6.6), the catch-all for unexpected exceptions, and the JSON log formatter."""

import io
import json
import logging
import sys
from uuid import uuid4

import pytest
from api_support import ORIGIN, TOKEN_A, client_for, ok
from fastapi import HTTPException

from hausbuch.api.errors import render
from hausbuch.api.logging import JsonFormatter, configure_logging
from hausbuch.storage import BlobConflictError

pytestmark = pytest.mark.db

SECRET = "DE89370400440532013000"


def test_unknown_paths_and_methods(client_a):
    response = client_a.get("/api/nothing-here")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"
    response = client_a.delete("/api/owners")
    assert response.status_code == 405
    assert response.json()["error"] == "not_found"
    response = client_a.get("/api/owners/not-a-uuid")
    assert (response.status_code, response.json()["error"]) == (422, "validation")


def _rendered_body(exc: BaseException) -> dict:
    return json.loads(render(exc, None).response().body)


@pytest.mark.parametrize(("status", "kind"), [(409, "conflict"), (404, "not_found")])
def test_an_http_exception_naming_its_kind_carries_no_generic_message(status, kind):
    """The auth routes raise HTTPException(detail=<ErrorKind>): the kind is kept and the
    status's generic text ("no such endpoint", "could not be read") is not added."""
    assert _rendered_body(HTTPException(status, detail=kind)) == {"error": kind}


def test_a_framework_404_keeps_its_fixed_text(client_a):
    assert _rendered_body(HTTPException(404)) == {
        "error": "not_found",
        "message": "no such endpoint",
    }
    response = client_a.get("/api/nothing-here")
    assert (response.status_code, response.json()["message"]) == (404, "no such endpoint")


def test_malformed_json_is_a_validation_error(client_a):
    response = client_a.post(
        "/api/owners",
        content=b'{"full_name": "' + SECRET.encode(),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["detail"][0]["type"] == "json_invalid"
    assert SECRET not in response.text


def test_every_error_body_carries_the_request_id(client_a):
    request_id = str(uuid4())
    response = client_a.get(f"/api/owners/{uuid4()}", headers={"X-Request-Id": request_id})
    assert response.json()["request_id"] == request_id
    assert response.headers["X-Request-Id"] == request_id


# --- Same-origin check (§6.6) ------------------------------------------------------------


@pytest.mark.parametrize("origin", [None, "https://evil.example", "null", ORIGIN + "/"])
def test_unsafe_methods_need_the_origin(api_app, origin):
    client = client_for(api_app, TOKEN_A, origin=origin)
    for method, path in (
        ("POST", "/api/owners"),
        ("PATCH", f"/api/owners/{uuid4()}"),
        ("PUT", f"/api/properties/{uuid4()}/ownership"),
        ("DELETE", f"/api/documents/{uuid4()}/links/owner/{uuid4()}"),
        ("POST", "/api/auth/login/options"),  # the public auth endpoints are covered too
    ):
        response = client.request(method, path, json={"full_name": "x"})
        assert response.status_code == 403, (method, path)
        assert response.json()["error"] == "forbidden_origin"
    assert ok(client.get("/api/owners")) == []  # safe methods need no Origin


def test_the_origin_check_runs_before_authentication(api_app):
    client = client_for(api_app, None, origin="https://evil.example")
    response = client.post("/api/owners", json={"full_name": "x"})
    assert (response.status_code, response.json()["error"]) == (403, "forbidden_origin")


# --- 500: anything else -------------------------------------------------------------------


def test_an_unexpected_exception_is_a_bare_500(client_a, monkeypatch, caplog):
    def boom(ctx, data):
        raise KeyError(SECRET)

    monkeypatch.setattr("hausbuch.api.routes.owners.create_owner", boom)
    caplog.set_level(logging.INFO)
    response = client_a.post("/api/owners", json={"full_name": "x"})
    assert response.status_code == 500
    assert set(response.json()) == {"error", "request_id"}
    [line] = [r.fields for r in caplog.records if r.name == "hausbuch.api.request"]
    assert line["exc_type"] == "builtins.KeyError"
    assert "exc_message" not in line  # not a hausbuch exception: its text is not logged
    assert SECRET not in caplog.text + json.dumps(line)


def test_a_value_error_outside_the_services_is_a_500_not_a_422(client_a, monkeypatch):
    def bug(ctx, data):
        int("not a number")

    monkeypatch.setattr("hausbuch.api.routes.owners.create_owner", bug)
    response = client_a.post("/api/owners", json={"full_name": "x"})
    assert (response.status_code, response.json()["error"]) == (500, "internal")


def test_a_hausbuch_exception_is_logged_with_its_message(client_a, monkeypatch, caplog):
    def conflict(ctx, data):
        raise BlobConflictError("'ws/sha256/abc' holds different bytes")

    monkeypatch.setattr("hausbuch.api.routes.owners.create_owner", conflict)
    caplog.set_level(logging.INFO)
    response = client_a.post("/api/owners", json={"full_name": "x"})
    assert response.status_code == 500
    assert "holds different bytes" not in response.text
    [line] = [r.fields for r in caplog.records if r.name == "hausbuch.api.request"]
    assert line["exc_type"] == "hausbuch.storage.BlobConflictError"
    assert line["exc_message"] == "'ws/sha256/abc' holds different bytes"


# --- The JSON log line ----------------------------------------------------------------------


def test_the_formatter_drops_tracebacks():
    try:
        raise ValueError(f"Key (iban)=({SECRET}) already exists")
    except ValueError:
        record = logging.getLogger("x").makeRecord(
            "x", logging.ERROR, __file__, 1, "failed", (), exc_info=sys.exc_info()
        )
    line = json.loads(JsonFormatter().format(record))
    assert line["exc_type"] == "ValueError"
    assert line["msg"] == "failed"
    assert SECRET not in json.dumps(line)


def test_configure_logging_writes_json_lines(client_a):
    stream = io.StringIO()
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    try:
        configure_logging(stream)
        ok(client_a.get("/api/owners"))
    finally:
        root.handlers[:], level = saved
        root.setLevel(level)
        logging.getLogger("uvicorn.access").disabled = False
    [line] = [json.loads(x) for x in stream.getvalue().splitlines() if '"request"' in x]
    assert (line["level"], line["route"], line["status"]) == ("info", "/api/owners", 200)
    assert line["logger"] == "hausbuch.api.request"
