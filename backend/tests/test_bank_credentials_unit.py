"""The cipher of a bank PIN and login without a database (docs/phases/phase-6.md D196): the
keys are HKDF-SHA256 from the session key with one label per use, AES-256-GCM round trips,
the AAD binds a ciphertext to its workspace and slot (or connection), a wrong key or a
foreign AAD reads as unreadable (None), never a crash, and the PIN's request model never shows
the PIN. Every PIN and login is invented."""

import uuid

import pytest
from pydantic import ValidationError

from hausbuch.schemas.bank_sync import BankPinSet
from hausbuch.services import bank_credentials as C
from hausbuch.services.bank_sync import state_key

SESSION_KEY = bytes(range(1, 33))
OTHER_KEY = bytes(range(101, 133))
PIN = "app-PIN-90210-secret"
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
        BankPinSet(pin=pin)
    (error,) = refused.value.errors(include_input=False)
    assert error["msg"] == "Value error, bank.pin_invalid: the PIN has 5 to 64 characters"
    if pin:
        assert pin not in error["msg"]


def test_the_pin_request_never_shows_the_pin():
    body = BankPinSet(pin=PIN)
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
