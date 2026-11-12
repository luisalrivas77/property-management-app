"""Phase 6 step 3 without a database (docs/phases/phase-6.md D163, D173): the cards name a
slot's own PIN variable and the DKB app, the bank's parameters are logged without a secret,
and the new models and the new event carry no field named like a credential (D119)."""

import logging
import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from hausbuch.jobs import bank_sync as job
from hausbuch.rules import errors as E
from hausbuch.schemas import bank_sync as B
from hausbuch.schemas.bank_sync import SyncTrigger
from hausbuch.schemas.events import EVENT_CATALOG, BankParametersRead, EventType
from hausbuch.services import bank_sync as service
from hausbuch.services.bank_sync import (
    _FAILED_CARD,
    _REFUSED_CARD,
    APPROVAL_NEEDED_QUESTION,
    APPROVAL_NEEDED_REASONING,
    failed_card_text,
    for_bank,
)

PIN = "PIN-4711-secret"
LOGIN = "loginname-xyz"
PRODUCT_ID = "PRODUCTID0123456789ABCDEF"
SECRETS = (PIN, LOGIN, PRODUCT_ID)
DKB = "DKB AG"


# --- A slot's PIN variable (D163) ------------------------------------------------------------


@pytest.mark.parametrize(
    ("slot", "variable"),
    [("primary", "HAUSBUCH_FINTS_PIN"), ("dkb", "HAUSBUCH_FINTS_PIN_DKB")],
)
def test_a_missing_pin_card_names_the_slots_variable_and_where_it_goes(slot, variable):
    reasoning, question = failed_card_text(E.BANK_PIN_MISSING, None, credential_slot=slot)
    assert variable in reasoning and variable in question
    assert "Scaleway" in question and "Schritt 17" in question
    assert reasoning.endswith("(bank.pin_missing)")
    if slot != "primary":
        assert "HAUSBUCH_FINTS_PIN " not in question  # never the primary's name alone


@pytest.mark.parametrize("code", ["9942", "9340"])
def test_a_wrong_pin_on_a_slot_names_that_slots_variable(code):
    _, primary = failed_card_text(E.BANK_REFUSED, code)
    assert "(HAUSBUCH_FINTS_PIN)" in primary
    _, slot = failed_card_text(E.BANK_REFUSED, code, credential_slot="sparkasse_privat")
    assert "(HAUSBUCH_FINTS_PIN_SPARKASSE_PRIVAT)" in slot
    assert "(HAUSBUCH_FINTS_PIN)" not in slot


def test_an_invalid_slot_name_is_never_echoed():
    reasoning, question = failed_card_text(E.BANK_PIN_MISSING, None, credential_slot="x y;")
    assert "x y;" not in reasoning + question


# --- DKB's wording (D173 (1), (2)) -----------------------------------------------------------

ALL_CARDS = [(code, None) for code in _FAILED_CARD] + [
    (E.BANK_REFUSED, code) for code in _REFUSED_CARD
]


@pytest.mark.parametrize(("error_code", "response_code"), ALL_CARDS)
def test_no_dkb_card_names_the_sparkasse_app(error_code, response_code):
    reasoning, question = failed_card_text(error_code, response_code, bank_name=DKB)
    assert "S-pushTAN" not in reasoning + question
    assert "pushTAN-App" not in reasoning + question


@pytest.mark.parametrize(("error_code", "response_code"), ALL_CARDS)
def test_any_other_bank_keeps_the_phase_5_text(error_code, response_code):
    assert failed_card_text(error_code, response_code, bank_name="Testbank") == (
        failed_card_text(error_code, response_code)
    )


def test_the_dkb_approval_card_names_the_dkb_app():
    for text in (APPROVAL_NEEDED_REASONING, APPROVAL_NEEDED_QUESTION):
        assert "S-pushTAN" not in for_bank(text, DKB)
    assert "DKB-App" in for_bank(APPROVAL_NEEDED_REASONING, DKB) + for_bank(
        APPROVAL_NEEDED_QUESTION, DKB
    )
    assert for_bank(APPROVAL_NEEDED_QUESTION, None) == APPROVAL_NEEDED_QUESTION


def test_a_dkb_without_camt_points_to_its_csv_export():
    reasoning, question = failed_card_text(E.BANK_UNSUPPORTED_FORMAT, None, bank_name=DKB)
    assert "CSV" in question
    assert reasoning.endswith("(bank.unsupported_format)")
    _, sparkasse = failed_card_text(E.BANK_UNSUPPORTED_FORMAT, None)
    assert "DKB" not in sparkasse


# --- The bank's parameters (D173 (7)) and D119 ----------------------------------------------

PARAMETERS = B.BankParameters(
    bank_name=DKB,
    bpd_version=7,
    camt_formats=["urn:iso:std:iso:20022:tech:xsd:camt.052.001.08"],
    camt_readable=True,
    statement_pdf_by_number=False,
    decoupled_methods=["946"],
    approval_versions=[7],
)


def _plan() -> service.RunPlan:
    return service.RunPlan(
        sync_id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        connection_id=uuid.uuid4(),
        trigger=SyncTrigger.MANUAL,
        token=datetime(2026, 11, 12, 9, 0, tzinfo=UTC),
        requested_from=date(2026, 4, 1),
        requested_to=date(2026, 11, 12),
        bank_code="10000000",
        fints_url="https://fints.example.org/fints",
        login_name=LOGIN,
        client_data=None,
        tan_mechanism=None,
        iban_hmac=b"\x00" * 32,
        created_by=None,
        credential_slot="dkb",
    )


@pytest.mark.parametrize("write_fails", [False, True], ids=["recorded", "write_failed"])
def test_the_parameters_log_line_carries_no_secret_and_never_fails_the_run(caplog, write_fails):
    session = SimpleNamespace(bank_parameters=lambda: PARAMETERS)
    run = job._Run(_plan(), log={"event": "bank_sync", "sync_id": "s", "attempt": 0})
    written = []

    def write(step, plan, parameters):
        if write_fails:
            raise RuntimeError(f"refused for {LOGIN}")  # the message is never logged
        written.append((step, parameters))
        return True

    with caplog.at_level(logging.INFO, logger=job.logger.name):
        job._record_parameters(session, run, write)
    [record] = [r for r in caplog.records if r.getMessage() == "bank parameters"]
    assert record.fields["bank_parameters"] == PARAMETERS.model_dump()
    if write_fails:
        assert record.fields["record_error"] == "RuntimeError" and not written
    else:
        assert record.fields["recorded"] is True
        assert written == [(service.record_bank_parameters, PARAMETERS)]
    logged = repr(record.fields) + record.getMessage()
    assert not [s for s in SECRETS if s in logged]


def test_no_parameters_no_log_and_no_write(caplog):
    session = SimpleNamespace(bank_parameters=lambda: None)
    run = job._Run(_plan())
    with caplog.at_level(logging.INFO, logger=job.logger.name):
        job._record_parameters(session, run, lambda *a: pytest.fail("nothing to record"))
    assert not [r for r in caplog.records if r.getMessage() == "bank parameters"]


SECRET_SHAPED = ("pin", "tan", "password", "token", "secret", "imap_user", "product", "login")
STEP3_MODELS = [
    B.BankParameters,
    B.BankCredentialSlots,
    B.BankConnection,
    B.BankConnectionPatch,
    BankParametersRead,
]


@pytest.mark.parametrize("model", STEP3_MODELS, ids=lambda m: m.__name__)
def test_no_secret_shaped_field_in_the_step_3_models(model):
    fields = [
        name
        for name in model.model_fields
        if name not in ("tan_mechanism", "login_masked", "login_name")
        and any(word in name for word in SECRET_SHAPED)
    ]
    assert not fields
    if model is not B.BankConnectionPatch:  # the patch takes a new login; nothing reads it back
        assert "login_name" not in model.model_fields


def test_the_parameters_event_is_on_the_account_and_forbids_extra_fields():
    spec = EVENT_CATALOG[EventType.ACCOUNT_BANK_PARAMETERS_READ]
    assert spec.payload is BankParametersRead
    payload = PARAMETERS.model_dump() | {
        "connection_id": str(uuid.uuid4()),
        "sync_id": str(uuid.uuid4()),
    }
    BankParametersRead.model_validate(payload)
    with pytest.raises(ValueError):
        BankParametersRead.model_validate(payload | {"pin": PIN})
