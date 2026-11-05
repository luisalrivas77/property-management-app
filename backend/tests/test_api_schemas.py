"""The API-only models (schemas/api.py, schemas/auth.py; phase 1 step 3 §4.9): their shape,
the PATCH semantics, and that each body matches the service it feeds, so a route can pass
`body.changes()` or the body's fields as keyword arguments without translation."""

import importlib
import inspect
import secrets
import subprocess
import sys
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from hausbuch.schemas import api, auth
from hausbuch.schemas.common import Schema
from hausbuch.schemas.enums import PropertyKind, RentModel, TenancyRole
from hausbuch.schemas.loan import LoanCreate
from hausbuch.schemas.tenancy import InitialRent, TenancyCreate
from hausbuch.services import documents, loans, owners, properties, tenancies, tenants, units

IBAN = "DE89370400440532013000"


def _models(module) -> list[type[BaseModel]]:
    return [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, BaseModel) and obj.__module__ == module.__name__
    ]


@pytest.mark.parametrize("model", _models(api) + _models(auth), ids=lambda m: m.__name__)
def test_every_model_is_a_strict_frozen_schema(model):
    assert issubclass(model, Schema)
    assert model.model_config["extra"] == "forbid"
    assert model.model_config["frozen"] is True


def test_the_named_models_exist():
    """§4.9's list, plus PasskeyVerifyRequest (the body of POST /auth/passkeys/verify)."""
    for name in [
        "ErrorResponse",
        "FieldError",
        "OwnerPatch",
        "PropertyPatch",
        "UnitPatch",
        "TenantPatch",
        "LoanPatch",
        "RentRowCorrection",
        "NotarizedOnCorrection",
        "DateCorrection",
        "OwnershipBody",
        "DepositReceivedBody",
        "NoticeBody",
        "EndTenancyBody",
        "ErasureBody",
        "DocumentTypingBody",
        "LinkBody",
        "DocumentDetail",
        "DepositReceipt",  # §10.8 item 7
        "TenancyCreateRequest",
        "LoanCreateRequest",
    ]:
        assert issubclass(getattr(api, name), Schema), name
    for name in [
        "Passkey",
        "Session",
        "Invite",
        "InviteCreate",
        "Me",
        "Member",  # §10.8 item 7
        "EnrollmentOptionsRequest",
        "EnrollmentVerifyRequest",
        "LoginVerifyRequest",
        "PasskeyVerifyRequest",
        "PasskeyLabel",
    ]:
        assert issubclass(getattr(auth, name), Schema), name


def test_schemas_import_nothing_from_services_db_api_or_auth():
    """hausbuch.schemas stays the pure contract (§2), checked in a fresh interpreter."""
    modules = ["api", "auth", "common", "document", "enums", "events", "loan", "property"]
    code = (
        "import sys\n"
        + "".join(f"import hausbuch.schemas.{m}\n" for m in modules + ["tenancy", "workspace"])
        + "bad = sorted(m for m in sys.modules if m.split('.')[:2] in "
        "(['hausbuch', 'services'], ['hausbuch', 'db'], ['hausbuch', 'api'], "
        "['hausbuch', 'auth']) or m.split('.')[0] in ('sqlalchemy', 'fastapi'))\n"
        "print(bad)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_initial_rent_moved_to_the_schemas_and_is_re_exported():
    assert InitialRent.__module__ == "hausbuch.schemas.tenancy"
    assert tenancies.InitialRent is InitialRent


# --- Errors ----------------------------------------------------------------------


def test_error_kinds_are_the_documented_strings():
    assert {k.value for k in api.ErrorKind} == {
        "validation",
        "rule_violation",
        "invalid",
        "unauthenticated",
        "forbidden_origin",
        "not_found",
        "conflict",
        "retention_hold",
        "payload_too_large",
        "unsupported_media_type",
        "shutting_down",
        "internal",
    }


def test_field_error_carries_no_input():
    assert set(api.FieldError.model_fields) == {"loc", "msg", "type"}
    with pytest.raises(ValidationError):
        api.FieldError(loc=["body", "iban"], msg="x", type="string_too_short", input=IBAN)


def test_error_response_serialises_only_what_is_set():
    body = api.ErrorResponse(error=api.ErrorKind.NOT_FOUND, entity_type="tenant", entity_id=uuid4())
    dumped = body.model_dump(mode="json", exclude_none=True)
    assert set(dumped) == {"error", "entity_type", "entity_id"}
    assert dumped["error"] == "not_found"


# --- PATCH semantics -----------------------------------------------------------------

PATCHES = {
    api.OwnerPatch: owners.update_owner,
    api.PropertyPatch: properties.update_property,
    api.UnitPatch: units.update_unit,
    api.TenantPatch: tenants.update_tenant,
    api.LoanPatch: loans.update_loan,
    api.RentRowCorrection: tenancies.correct_rent_row,
}


def _keyword_only(function) -> set[str]:
    return {
        name
        for name, p in inspect.signature(function).parameters.items()
        if p.kind is inspect.Parameter.KEYWORD_ONLY
    }


@pytest.mark.parametrize(
    "patch", sorted(PATCHES, key=lambda m: m.__name__), ids=lambda m: m.__name__
)
def test_patch_fields_are_the_services_keyword_arguments(patch):
    assert set(patch.model_fields) == _keyword_only(PATCHES[patch]) - {"iban_key"}


def test_absent_is_unchanged_and_null_clears():
    assert api.OwnerPatch.model_validate({}).changes() == {}
    assert api.OwnerPatch.model_validate({"tax_id_ref": None}).changes() == {"tax_id_ref": None}
    patch = api.UnitPatch.model_validate({"rooms": None, "label": "WE 4"})
    assert patch.changes() == {"label": "WE 4", "rooms": None}
    loan = api.LoanPatch.model_validate({"fixed_until": None, "follow_up_rate": None})
    assert loan.changes() == {"fixed_until": None, "follow_up_rate": None}


@pytest.mark.parametrize(
    ("patch", "field"),
    [
        (api.OwnerPatch, "full_name"),
        (api.PropertyPatch, "label"),
        (api.PropertyPatch, "kind"),
        (api.PropertyPatch, "address"),
        (api.PropertyPatch, "build_year"),
        (api.UnitPatch, "label"),
        (api.UnitPatch, "floor_area_m2"),
        (api.TenantPatch, "full_name"),
        (api.LoanPatch, "lender"),
        (api.LoanPatch, "nominal_rate"),
        (api.LoanPatch, "monthly_payment"),
        (api.LoanPatch, "first_payment_on"),
        (api.RentRowCorrection, "effective_from"),
        (api.RentRowCorrection, "cold_rent"),
        (api.RentRowCorrection, "operating_costs_prepayment"),
    ],
    ids=lambda v: v if isinstance(v, str) else v.__name__,
)
def test_null_is_refused_for_a_field_that_cannot_be_cleared(patch, field):
    with pytest.raises(ValidationError) as excinfo:
        patch.model_validate({field: None})
    assert [e["loc"] for e in excinfo.value.errors()][0][0] == field


def test_patches_keep_the_create_constraints():
    with pytest.raises(ValidationError):
        api.PropertyPatch.model_validate({"label": "x" * 101})
    with pytest.raises(ValidationError):
        api.UnitPatch.model_validate({"floor_area_m2": "0"})
    with pytest.raises(ValidationError):
        api.LoanPatch.model_validate({"monthly_payment": 1400.0})  # amendment 2
    assert api.PropertyPatch.model_validate({"kind": "efh"}).kind is PropertyKind.EFH


@pytest.mark.parametrize("raw", ["12345678901", "IdNr. 12 345 678 901"])
def test_owner_patch_refuses_a_raw_tax_number_without_echoing_it(raw):
    with pytest.raises(ValidationError) as excinfo:
        api.OwnerPatch.model_validate({"tax_id_ref": raw})
    assert "secrets-manager reference" in str(excinfo.value)
    assert "345" not in str(excinfo.value) + repr(excinfo.value)


@pytest.mark.parametrize("patch", [api.TenantPatch, api.LoanPatch], ids=lambda m: m.__name__)
def test_iban_patches_hide_input_in_errors(patch):
    field = "iban" if patch is api.TenantPatch else "account_iban"
    with pytest.raises(ValidationError) as excinfo:
        patch.model_validate({field: "DE89 3704 0044 0532 0130 00 " * 2})
    assert "0532" not in str(excinfo.value) + repr(excinfo.value)
    assert patch.model_config.get("hide_input_in_errors") is True


# --- Action bodies match their services ----------------------------------------------

ACTIONS = {
    api.NotarizedOnCorrection: properties.correct_notarized_on,
    api.DateCorrection: properties.correct_purchase_date,
    api.OwnershipBody: properties.set_ownership,
    api.DepositReceivedBody: tenancies.record_deposit_received,
    api.NoticeBody: tenancies.give_notice,
    api.EndTenancyBody: tenancies.end_tenancy,
    api.ErasureBody: tenants.erase_tenant,
    api.DocumentTypingBody: documents.type_document,
    api.LinkBody: documents.link_document,
}


@pytest.mark.parametrize(
    "body", sorted(ACTIONS, key=lambda m: m.__name__), ids=lambda m: m.__name__
)
def test_action_body_fields_are_parameters_of_the_service(body):
    assert set(body.model_fields) <= set(inspect.signature(ACTIONS[body]).parameters)


def test_date_correction_also_fits_correct_move_in():
    assert set(api.DateCorrection.model_fields) <= set(
        inspect.signature(tenancies.correct_move_in).parameters
    )


# --- Create requests --------------------------------------------------------------


def test_tenancy_create_request_splits_into_the_service_arguments():
    body = {
        "unit_id": str(uuid4()),
        "parties": [{"tenant_id": str(uuid4()), "role": "primary"}],
        "start_date": "2024-07-01",
        "rent_model": "fixed",
        "deposit_amount": "2850.00",
        "deposit_form": "cash_account",
        "deposit_iban": IBAN,
        "initial_rent": {"cold_rent": "950.00", "operating_costs_prepayment": "220.00"},
    }
    request = api.TenancyCreateRequest.model_validate(body)
    data = request.as_create()
    assert type(data) is TenancyCreate
    assert data.model_dump() == request.model_dump(exclude={"initial_rent"})
    assert data.deposit_amount == Decimal("2850.00")
    assert (data.parties[0].role, data.rent_model) == (TenancyRole.PRIMARY, RentModel.FIXED)
    assert request.initial_rent == InitialRent(
        cold_rent=Decimal("950.00"), operating_costs_prepayment=Decimal("220.00")
    )
    assert (
        api.TenancyCreateRequest.model_validate(body | {"initial_rent": None}).initial_rent is None
    )
    assert set(api.TenancyCreateRequest.model_fields) - set(TenancyCreate.model_fields) == {
        "initial_rent"
    }
    assert api.TenancyCreateRequest.model_config.get("hide_input_in_errors") is True


def test_loan_create_request_splits_into_the_service_arguments():
    body = {
        "property_id": str(uuid4()),
        "lender": "Sparkasse",
        "principal": "250000.00",
        "nominal_rate": "0.036",
        "monthly_payment": "1150.00",
        "disbursed_on": "2024-04-30",
        "first_payment_on": "2024-05-30",
        "signed_on": "2024-03-01",
    }
    request = api.LoanCreateRequest.model_validate(body)
    data = request.as_create()
    assert type(data) is LoanCreate
    assert data.model_dump() == request.model_dump(exclude={"signed_on"})
    assert request.signed_on == date(2024, 3, 1)
    assert "signed_on" in inspect.signature(loans.create_loan).parameters
    assert api.LoanCreateRequest.model_config.get("hide_input_in_errors") is True
    with pytest.raises(ValidationError):
        api.LoanCreateRequest.model_validate(body | {"principal": 250000})  # amendment 2


# --- Auth models --------------------------------------------------------------------


@pytest.mark.parametrize(
    "model", [auth.Passkey, auth.Session, auth.Invite, auth.Me], ids=lambda m: m.__name__
)
def test_read_shapes_carry_no_secret(model):
    names = set(model.model_fields)
    assert not names & {"token", "token_hash", "public_key", "credential_id", "sign_count"}


def test_enrollment_token_is_a_32_byte_base64url_token_and_never_echoed():
    token = secrets.token_urlsafe(32)
    assert auth.EnrollmentOptionsRequest(token=token).token == token
    for bad in (token[:-1], token + "A", token[:-1] + "="):
        with pytest.raises(ValidationError) as excinfo:
            auth.EnrollmentVerifyRequest(token=bad, credential={}, label="iPhone")
        assert bad not in str(excinfo.value) + repr(excinfo.value)


def test_passkey_labels_follow_the_column_check():
    assert auth.PasskeyLabel(label="iPhone von Luis").label == "iPhone von Luis"
    for bad in ("", "x" * 101):
        with pytest.raises(ValidationError):
            auth.PasskeyLabel(label=bad)


def test_invite_create_stores_the_email_lower_case():
    invite = auth.InviteCreate(email="Maria.Rivas@Example.org", display_name="Maria")
    assert invite.email == "maria.rivas@example.org"
    with pytest.raises(ValidationError):
        auth.InviteCreate(email="no-at-sign", display_name="Maria")


def test_credential_payloads_are_opaque_json():
    credential = {"id": "abc", "rawId": "abc", "response": {"clientDataJSON": "e30"}, "n": [1]}
    assert auth.LoginVerifyRequest(credential=credential).credential == credential
    importlib.import_module("hausbuch.schemas.auth")  # no side effects on import
