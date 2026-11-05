"""Spec amendment 1, item 2: no raw tax number is accepted, and no validation error echoes a
tax number or an IBAN (they would otherwise reach a log)."""

import re

import pytest
from pydantic import ValidationError

from hausbuch.schemas.loan import LoanCreate
from hausbuch.schemas.property import OwnerCreate
from hausbuch.schemas.tenancy import TenancyCreate, TenantCreate


def _rendered(error: ValidationError) -> str:
    return str(error) + "\n" + repr(error)


def _assert_not_echoed(error: ValidationError, value: str) -> None:
    text = _rendered(error)
    assert value not in text
    digits = re.sub(r"\D", "", value)
    if digits:
        assert digits not in text
    # Every group of 3+ digits: shorter ones could collide with the error count or the
    # pydantic docs URL that every error carries.
    for group in re.findall(r"\d{3,}", value):
        assert group not in text, group


@pytest.mark.parametrize(
    "raw",
    ["12345678901", "12 345 678 901", "123/456/78901", "21/815/08150"],
    ids=["steuer-id", "steuer-id-spaced", "steuernummer-12", "steuernummer-elster"],
)
def test_owner_rejects_a_raw_tax_number_without_echoing_it(raw):
    with pytest.raises(ValidationError) as excinfo:
        OwnerCreate(full_name="Luis", tax_id_ref=raw)
    error = excinfo.value
    assert [e["loc"] for e in error.errors()] == [("tax_id_ref",)]
    assert "secrets-manager reference" in str(error)
    _assert_not_echoed(error, raw)


@pytest.mark.parametrize("ref", ["secret://owners/luis", "secret://owners/123", None])
def test_owner_accepts_a_secrets_reference_or_none(ref):
    assert OwnerCreate(full_name="Luis", tax_id_ref=ref).tax_id_ref == ref


@pytest.mark.parametrize(
    "iban",
    ["DE89 3704 0044", "DE89370400440532013000" + "0" * 21],
    ids=["too-short", "too-long"],
)
def test_tenant_iban_length_error_does_not_echo_the_iban(iban):
    with pytest.raises(ValidationError) as excinfo:
        TenantCreate(full_name="Max Mieter", iban=iban)
    error = excinfo.value
    assert [e["loc"] for e in error.errors()] == [("iban",)]
    _assert_not_echoed(error, iban)


@pytest.mark.parametrize("model", [OwnerCreate, TenantCreate, TenancyCreate, LoanCreate])
def test_create_schemas_hide_input_in_errors(model):
    assert model.model_config.get("hide_input_in_errors") is True
    assert model.model_config.get("extra") == "forbid"
