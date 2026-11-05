"""No input echo, no PII in logs (§3.6, §10.5, phase-1.md spec amendment 1).

Each case submits a value that must never come back: a labelled tax number, an IBAN (too
long for the schema, or with wrong check digits), a loan contract number inside a
service-side validation error, an email inside a database error. The test asserts the value
appears neither in the response body nor anywhere in the captured log records: their
message, their `fields`, their JSON rendering, and the repr of their whole __dict__ (which
would include an exc_info traceback).
"""

import json
import logging
from uuid import uuid4

import pytest
from api_support import (
    BAD_CHECKSUM_IBAN,
    GOOD_IBAN,
    create,
    flat,
    loan_body,
    new_property,
    new_tenant,
    ok,
    tenancy_body,
)
from pydantic import ValidationError
from sqlalchemy import insert

from hausbuch.api.errors import render
from hausbuch.api.logging import REQUEST_LOGGER, JsonFormatter
from hausbuch.db.tables import app_user
from hausbuch.schemas.property import OwnerCreate
from hausbuch.services.events import InvalidEventError

pytestmark = pytest.mark.db

TAX_NUMBER = "12345678901"
LABELLED_TAX_NUMBER = f"IdNr. {TAX_NUMBER[:2]} {TAX_NUMBER[2:5]} {TAX_NUMBER[5:8]} {TAX_NUMBER[8:]}"
TOO_LONG_IBAN = "DE89 3704 0044 0532 0130 0000 0000 0000 0000 1"  # 45 characters
CONTRACT_NUMBER = "KTO-4711-GEHEIM"
EMAIL = "geheim.mieterin@example.org"


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isalnum())


def forbidden_forms(value: str) -> list[str]:
    """The value as submitted, and without separators (how a normalizer would print it)."""
    return sorted({value, _digits(value)})


# The test client's own logger prints the URL it requested; it is not the server's log.
CLIENT_LOGGERS = ("httpx", "httpx2")


@pytest.fixture
def logs(caplog):
    caplog.set_level(logging.DEBUG)  # root: everything any logger emits
    formatter = JsonFormatter()

    def text() -> str:
        parts = []
        for record in caplog.records:
            if record.name.split(".")[0] in CLIENT_LOGGERS:
                continue
            parts.append(caplog.handler.format(record))
            parts.append(formatter.format(record))
            parts.append(repr(record.__dict__))
        return "\n".join(parts)

    def request_lines() -> list[dict]:
        return [r.fields for r in caplog.records if r.name == REQUEST_LOGGER]

    text.request_lines = request_lines
    return text


def assert_not_echoed(response, logs, *values: str) -> None:
    body = response.text
    logged = logs()
    assert logs.request_lines(), "the request was not logged"
    for value in values:
        for form in forbidden_forms(value):
            assert form not in body, f"{form!r} echoed in the response body"
            assert form not in logged, f"{form!r} reached the log"


# --- 422 validation: FastAPI's errors carry `input` -------------------------------------


def test_a_labelled_tax_number_is_not_echoed_on_create(client_a, logs):
    response = client_a.post(
        "/api/owners", json={"full_name": "Luis Rivas", "tax_id_ref": LABELLED_TAX_NUMBER}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "validation"
    [error] = body["detail"]
    assert set(error) == {"loc", "msg", "type"}
    assert error["loc"] == ["body", "tax_id_ref"]
    assert_not_echoed(response, logs, LABELLED_TAX_NUMBER, TAX_NUMBER)


def test_a_labelled_tax_number_is_not_echoed_on_patch(client_a, logs):
    owner = create(client_a, "/api/owners", {"full_name": "Luis Rivas"})
    response = client_a.patch(
        f"/api/owners/{owner['id']}", json={"tax_id_ref": LABELLED_TAX_NUMBER}
    )
    assert response.status_code == 422
    assert_not_echoed(response, logs, LABELLED_TAX_NUMBER, TAX_NUMBER)


def test_an_iban_too_long_for_the_schema_is_not_echoed(client_a, logs):
    response = client_a.post("/api/tenants", json={"full_name": "Anna", "iban": TOO_LONG_IBAN})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "iban"]
    assert_not_echoed(response, logs, TOO_LONG_IBAN)


# --- 422 rule_violation: protect_iban refuses the check digits -------------------------


def test_a_bad_iban_is_not_echoed_for_a_tenant(client_a, logs):
    response = client_a.post("/api/tenants", json={"full_name": "Anna", "iban": BAD_CHECKSUM_IBAN})
    assert response.status_code == 422
    body = response.json()
    assert (body["error"], body["code"], body["codes"]) == (
        "rule_violation",
        "iban.bad_checksum",
        ["iban.bad_checksum"],
    )
    assert_not_echoed(response, logs, BAD_CHECKSUM_IBAN)

    tenant = new_tenant(client_a)
    response = client_a.patch(f"/api/tenants/{tenant['id']}", json={"iban": BAD_CHECKSUM_IBAN})
    assert response.status_code == 422
    assert_not_echoed(response, logs, BAD_CHECKSUM_IBAN)


def test_a_bad_iban_is_not_echoed_for_a_deposit_or_a_loan(client_a, logs):
    setup = flat(client_a)
    other_unit = create(
        client_a,
        "/api/units",
        {"property_id": setup["property"]["id"], "label": "WE 4", "floor_area_m2": "50.00"},
    )
    body = tenancy_body(
        other_unit["id"],
        [setup["tenant"]["id"]],
        deposit_amount="2850.00",
        deposit_form="cash_account",
        deposit_iban=BAD_CHECKSUM_IBAN,
    )
    response = client_a.post("/api/tenancies", json=body)
    assert (response.status_code, response.json()["code"]) == (422, "iban.bad_checksum")
    assert_not_echoed(response, logs, BAD_CHECKSUM_IBAN)

    loan = loan_body(setup["property"]["id"], account_iban=BAD_CHECKSUM_IBAN)
    response = client_a.post("/api/loans", json=loan)
    assert (response.status_code, response.json()["code"]) == (422, "iban.bad_checksum")
    assert_not_echoed(response, logs, BAD_CHECKSUM_IBAN)


def test_a_good_iban_is_stored_masked_and_never_returned(client_a, logs):
    tenant = new_tenant(client_a, iban=GOOD_IBAN)
    assert tenant["iban_masked"] == "DE89 **** 3000"
    assert "iban" not in tenant
    response = client_a.get(f"/api/tenants/{tenant['id']}")
    assert_not_echoed(response, logs, GOOD_IBAN)


# --- 422 validation raised inside a service: pydantic.ValidationError -------------------


def test_a_service_validation_error_does_not_echo_the_stored_row(client_a, logs):
    """update_loan revalidates the whole loan with LoanCreate; the model-level error's
    `input` is the whole row, contract number included."""
    prop = new_property(client_a)
    loan = create(client_a, "/api/loans", loan_body(prop["id"], contract_number=CONTRACT_NUMBER))
    response = client_a.patch(f"/api/loans/{loan['id']}", json={"first_payment_on": "2024-04-01"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "validation"
    assert [set(e) for e in body["detail"]] == [{"loc", "msg", "type"}]
    assert_not_echoed(response, logs, CONTRACT_NUMBER, "Sparkasse")


def test_render_reads_a_validation_error_without_its_input():
    """The pydantic.ValidationError branch on its own: errors(include_input=False, ...)."""
    with pytest.raises(Exception) as excinfo:
        OwnerCreate.model_validate({"full_name": "x", "tax_id_ref": LABELLED_TAX_NUMBER})
    rendered = render(excinfo.value, uuid4())
    payload = json.dumps(rendered.body.model_dump(mode="json")) + json.dumps(rendered.log)
    assert rendered.status == 422
    assert [e.loc for e in rendered.body.detail] == [["tax_id_ref"]]
    for form in forbidden_forms(LABELLED_TAX_NUMBER) + [TAX_NUMBER]:
        assert form not in payload


# --- 500: a database error's text names the offending value -----------------------------


def test_a_database_error_is_logged_by_constraint_only(client_a, logs, monkeypatch):
    """A unique violation's text is `Key (email)=(<the email>) already exists`. The route's
    service is replaced by one that hits it (as the invite flow could)."""

    def duplicate_user(ctx, data):
        for _ in range(2):
            ctx.conn.execute(
                insert(app_user).values(
                    workspace_id=ctx.workspace_id, email=EMAIL, display_name="Mieterin"
                )
            )

    monkeypatch.setattr("hausbuch.api.routes.owners.create_owner", duplicate_user)
    request_id = str(uuid4())
    response = client_a.post(
        "/api/owners", json={"full_name": "x"}, headers={"X-Request-Id": request_id}
    )
    assert response.status_code == 500
    assert response.json() == {"error": "internal", "request_id": request_id}
    assert_not_echoed(response, logs, EMAIL)
    [line] = logs.request_lines()
    assert line["status"] == 500
    assert line["error"] == "internal"
    assert line["constraint"] == "app_user_email_key"
    assert line["sqlstate"] == "23505"
    assert line["exc_type"] == "sqlalchemy.exc.IntegrityError"
    assert line["exc_where"].startswith("hausbuch.api.commands:run:")  # innermost hausbuch frame
    assert "exc_message" not in line


def test_a_message_built_from_a_validation_error_is_not_logged(client_a, logs, monkeypatch):
    """append_event wraps an Event ValidationError as InvalidEventError(f"...: {exc}"): a
    hausbuch class, but its text carries the payload. It is a 500, logged by class only."""

    def bad_event(ctx, data):
        try:
            OwnerCreate.model_validate({"full_name": "x", "extra": CONTRACT_NUMBER})
        except ValidationError as exc:
            raise InvalidEventError(f"invalid owner.created event: {exc}") from exc

    monkeypatch.setattr("hausbuch.api.routes.owners.create_owner", bad_event)
    response = client_a.post("/api/owners", json={"full_name": "x"})
    assert (response.status_code, response.json()["error"]) == (500, "internal")
    [line] = logs.request_lines()
    assert line["exc_type"] == "hausbuch.services.events.InvalidEventError"
    assert line["exc_cause"] == "pydantic_core._pydantic_core.ValidationError"
    assert "exc_message" not in line
    assert_not_echoed(response, logs, CONTRACT_NUMBER)


def test_the_request_line_carries_no_body_and_the_route_template(client_a, logs, auth_a):
    tenant = new_tenant(client_a, full_name="Anna Geheim", email=EMAIL, iban=GOOD_IBAN)
    response = client_a.get(f"/api/tenants/{tenant['id']}?x={EMAIL}")
    ok(response)
    line = logs.request_lines()[-1]
    assert line["route"] == "/api/tenants/{tenant_id}"
    assert (line["method"], line["status"]) == ("GET", 200)
    assert line["user_id"] == str(auth_a.user_id)
    assert line["workspace_id"] == str(auth_a.workspace_id)
    assert set(line) == {
        "request_id",
        "method",
        "route",
        "status",
        "duration_ms",
        "user_id",
        "workspace_id",
    }
    logged = logs()
    for value in ("Anna Geheim", EMAIL, tenant["id"]):
        assert value not in logged
