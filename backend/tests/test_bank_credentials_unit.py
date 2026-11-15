"""The cipher of a bank PIN and login without a database (docs/phases/phase-6.md D196): the
keys are HKDF-SHA256 from the session key with one label per use, AES-256-GCM round trips,
the AAD binds a ciphertext to its workspace and slot (or connection), a wrong key or a
foreign AAD reads as unreadable (None), never a crash, and the PIN's request model never shows
the PIN. Every PIN and login is invented."""

import uuid
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from hausbuch.jobs.bank_sync import _Run
from hausbuch.schemas.bank_sync import (
    BankConnectionCreate,
    BankConnectionPatch,
    BankPinSet,
    SyncTrigger,
)
from hausbuch.services import bank_credentials as C
from hausbuch.services.bank_sync import RunPlan, state_key

SESSION_KEY = bytes(range(1, 33))
OTHER_KEY = bytes(range(101, 133))
PIN = "app-PIN-90210-secret"
FINTS_URL = "https://fints.example.org/fints"
WS, OTHER_WS = uuid.uuid4(), uuid.uuid4()


def test_each_use_gets_its_own_key_from_the_session_key():
    pin_key = C.derive_key(SESSION_KEY, C.PIN_INFO)
    login_key = C.derive_key(SESSION_KEY, C.LOGIN_INFO)
    assert len(pin_key) == len(login_key) == 32
    assert len({pin_key, login_key, state_key(SESSION_KEY), SESSION_KEY}) == 4
    assert C.derive_key(SESSION_KEY, C.PIN_INFO) == pin_key  # deterministic
    assert C.derive_key(OTHER_KEY, C.PIN_INFO) != pin_key


def test_a_pin_round_trips_with_a_fresh_nonce_each_time():
    key = C.derive_key(SESSION_KEY, C.PIN_INFO)
    aad = C.pin_aad(WS, "primary")
    first, second = C.seal(key, PIN, aad), C.seal(key, PIN, aad)
    assert len(first.nonce) == C.NONCE_BYTES == 12
    assert first.nonce != second.nonce and first.ciphertext != second.ciphertext
    assert PIN.encode() not in first.ciphertext
    assert len(first.ciphertext) == len(PIN.encode()) + 16  # the GCM tag
    assert C.unseal(key, first.nonce, first.ciphertext, aad) == PIN
    assert PIN not in repr(first) and first.nonce.hex() not in repr(first)


@pytest.mark.parametrize(
    "aad",
    [C.pin_aad(WS, "dkb"), C.pin_aad(OTHER_WS, "primary"), C.login_aad(WS, uuid.uuid4())],
    ids=["another_slot", "another_workspace", "a_login"],
)
def test_a_ciphertext_does_not_open_under_another_slot_or_workspace(aad):
    key = C.derive_key(SESSION_KEY, C.PIN_INFO)
    sealed = C.seal(key, PIN, C.pin_aad(WS, "primary"))
    assert C.unseal(key, sealed.nonce, sealed.ciphertext, aad) is None


def test_a_wrong_key_or_damaged_bytes_read_as_unreadable_never_raise():
    aad = C.pin_aad(WS, "primary")
    sealed = C.seal(C.derive_key(SESSION_KEY, C.PIN_INFO), PIN, aad)
    other = C.derive_key(OTHER_KEY, C.PIN_INFO)
    assert C.unseal(other, sealed.nonce, sealed.ciphertext, aad) is None
    login_key = C.derive_key(SESSION_KEY, C.LOGIN_INFO)  # the right session, the wrong use
    assert C.unseal(login_key, sealed.nonce, sealed.ciphertext, aad) is None
    damaged = bytes([sealed.ciphertext[0] ^ 1]) + sealed.ciphertext[1:]
    key = C.derive_key(SESSION_KEY, C.PIN_INFO)
    assert C.unseal(key, sealed.nonce, damaged, aad) is None
    assert C.unseal(key, b"short", sealed.ciphertext, aad) is None


def test_the_aad_parts_cannot_be_shifted_into_each_other():
    assert C._aad("ab", "c") != C._aad("a", "bc")


def test_a_login_round_trips_bound_to_its_connection():
    connection_id = uuid.uuid4()
    values = C.sealed_login(_settings(), WS, connection_id, "loginname-xyz")
    assert values["login_name"] is None and values["login_key_version"] == C.KEY_VERSION
    row = _Row(id=connection_id, workspace_id=WS, **values)
    assert C.connection_login(row, _settings()) == "loginname-xyz"
    moved = _Row(id=uuid.uuid4(), workspace_id=WS, **values)
    assert C.connection_login(moved, _settings()) is None
    assert C.connection_login(row, _settings(OTHER_KEY)) is None
    newer = _Row(**(row.__dict__ | {"login_key_version": C.KEY_VERSION + 1}))
    assert C.connection_login(newer, _settings()) is None
    readable = _Row(id=connection_id, workspace_id=WS, login_name="plain-login")
    assert C.connection_login(readable, _settings()) == "plain-login"


@pytest.mark.parametrize("pin", ["1234", "x" * 65, ""], ids=["four", "sixty_five", "empty"])
def test_a_pin_outside_5_to_64_characters_is_refused_without_naming_it(pin):
    with pytest.raises(ValidationError) as refused:
        BankPinSet(pin=pin, fints_url=FINTS_URL)
    (error,) = refused.value.errors(include_input=False)
    assert error["msg"] == "Value error, bank.pin_invalid: the PIN has 5 to 64 characters"
    if pin:
        assert pin not in error["msg"]


def test_a_pin_is_trimmed_and_only_blanks_count_as_empty():
    assert BankPinSet(pin=f"  {PIN}\t\n", fints_url=FINTS_URL).pin.get_secret_value() == PIN
    assert BankPinSet(pin=" 12345 ", fints_url=FINTS_URL).pin.get_secret_value() == "12345"
    for pin in ("      ", " 1234 "):  # nothing, or four characters, once trimmed
        with pytest.raises(ValidationError) as refused:
            BankPinSet(pin=pin, fints_url=FINTS_URL)
        (error,) = refused.value.errors(include_input=False)
        assert error["msg"] == "Value error, bank.pin_invalid: the PIN has 5 to 64 characters"


def test_a_login_name_is_trimmed_and_a_blank_one_refused():
    create = {"account_id": WS, "bank_code": "10000000", "fints_url": "https://fints.example.org"}
    body = BankConnectionCreate(**create, login_name="  loginname-xyz \t")
    assert body.login_name == "loginname-xyz"
    assert BankConnectionPatch(login_name=" other-login ").login_name == "other-login"
    with pytest.raises(ValidationError):
        BankConnectionCreate(**create, login_name="   ")


def test_a_run_plan_and_the_jobs_run_never_show_the_login():
    plan = RunPlan(
        sync_id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        connection_id=uuid.uuid4(),
        trigger=SyncTrigger.MANUAL,
        token=datetime(2026, 11, 15, tzinfo=UTC),
        requested_from=date(2026, 11, 1),
        requested_to=date(2026, 11, 15),
        bank_code="10000000",
        fints_url="https://fints.example.org",
        login_name="loginname-xyz",
        client_data=None,
        tan_mechanism=None,
        iban_hmac=b"\x00" * 32,
        created_by=None,
    )
    assert plan.login_name == "loginname-xyz"
    assert "loginname-xyz" not in repr(plan) and "loginname-xyz" not in repr(_Run(plan))


@pytest.mark.parametrize(
    ("url", "address"),
    [
        ("https://fints.example.org/fints", "https://fints.example.org:443/fints"),
        ("HTTPS://FINTS.Example.ORG:443/fints", "https://fints.example.org:443/fints"),
        ("https://fints.example.org", "https://fints.example.org:443/"),
        ("https://fints.example.org/fints?x=1#top", "https://fints.example.org:443/fints"),
        ("https://fints.example.org:8443/fints", "https://fints.example.org:8443/fints"),
        ("https://fints.example.org@other.example/fints", "https://other.example:443/fints"),
        ("https://[2001:db8::1]/fints", "https://[2001:db8::1]:443/fints"),
    ],
    ids=["plain", "case_and_port", "no_path", "query", "other_port", "user_info", "ipv6"],
)
def test_a_pins_server_is_its_scheme_host_port_and_path(url, address):
    """D196 amended: the address a PIN is bound to is read by urlsplit, so a user-info part
    before @ is not the host, and the path is kept (another path is another endpoint)."""
    assert C.server_address(url) == address


def test_another_host_port_or_path_is_another_server():
    base = C.server_hash("https://fints.example.org/fints")
    for other in (
        "https://fints.example.net/fints",
        "https://fints.example.org:8443/fints",
        "https://fints.example.org/other",
        "https://fints.example.org/FINTS",  # a path is case-sensitive
        "https://fints.example.org:99999/fints",  # a port that does not parse
    ):
        assert C.server_hash(other) != base, other
    assert C.server_hash("https://FINTS.example.org:443/fints") == base
    assert len(base) == C.SERVER_HASH_BYTES


def test_the_pin_request_needs_an_https_address():
    with pytest.raises(ValidationError):
        BankPinSet(pin=PIN)
    with pytest.raises(ValidationError) as refused:
        BankPinSet(pin=PIN, fints_url="http://fints.example.org/fints")
    assert PIN not in str(refused.value)


def test_the_pin_request_never_shows_the_pin():
    body = BankPinSet(pin=PIN, fints_url=FINTS_URL)
    assert body.pin.get_secret_value() == PIN
    assert PIN not in repr(body) and PIN not in str(body) and PIN not in body.model_dump_json()


# --- helpers ---------------------------------------------------------------------------------


class _Row:
    def __init__(self, **values):
        defaults = {
            "login_name": None,
            "login_nonce": None,
            "login_ciphertext": None,
            "login_key_version": None,
        }
        self.__dict__.update(defaults | values)


def _settings(session_key=SESSION_KEY):
    class _S:
        pass

    s = _S()
    s.session_key = session_key
    return s
