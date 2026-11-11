"""services.bank.fints, the read-only FinTS facade (docs/phases/phase-5.md §4, D118, D119).

The three guards one by one (no write on the facade, the client's write methods refused, the
wire whitelist inside the encryption envelope too), the HTTP timeout, the logging pin with a
real python-fints message at DEBUG, the state blob without secrets, the error mapping, and
the fake client's signatures against the real client's. No network: the HTTP post is
patched. Every bank code, login, PIN, URL and product id is synthetic."""

import base64
import inspect
import io
import logging
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
import requests
from fints.client import DATA_BLOB_MAGIC
from fints.exceptions import (
    FinTSClientPINError,
    FinTSConnectionError,
    FinTSDialogInitError,
    FinTSNoResponseError,
    FinTSUnsupportedOperation,
)
from fints.formals import KTI1, Language2, SystemIDStatus
from fints.segments.auth import HKIDN2, HKTAB5, HKVVB3
from fints.segments.transfer import HKCCS1
from fints.utils import decompress_datablob
from fints_fake import CHIPTAN, DECOUPLED, FakeClient, Script, factory, fake_account

from hausbuch.rules import errors as E
from hausbuch.services.bank import fints as F

BANK_CODE = "10000000"
LOGIN = "loginname-xyz"
PIN = "PIN-4711-secret"
URL = "https://fints.example.org/fints"
PRODUCT_ID = "PRODUCTID0123456789ABCDEF"  # 25 characters, like the DK registration number
SECRETS = (PIN, LOGIN, PRODUCT_ID)


def _client() -> F._ReadOnlyClient:
    return F.make_client(
        bank_code=BANK_CODE,
        login_name=LOGIN,
        pin=PIN,
        url=URL,
        product_id=PRODUCT_ID,
        client_data=None,
    )


def _message(client: F._ReadOnlyClient, *extra):
    """A real, encrypted customer message: identification and product, plus `extra`."""
    dialog = client._new_dialog()
    dialog.open = True
    message = dialog.new_customer_message()
    message += HKIDN2(
        client.bank_identifier, client.customer_id, client.system_id, SystemIDStatus.ID_NECESSARY
    )
    message += HKVVB3(0, 0, Language2.DE, client.product_name, client.product_version)
    for segment in extra:
        message += segment
    dialog.finish_message(message)
    return message


def _bank_answer(message) -> requests.Response:
    """The customer's own bytes as the bank's answer: python-fints parses them as an
    institute message, enough for a round trip through connection.send."""
    response = requests.Response()
    response.status_code = 200
    response._content = base64.b64encode(message.render_bytes())
    return response


# --- Guard 1: the facade has no write ------------------------------------------------------

_WRITE_PREFIXES = ("sepa_", "transfer", "debit", "standing")


def test_the_facade_has_no_write_method():
    session = F.FintsSession(FakeClient(Script(), {}), tan_mechanism=None, restored=False)
    for owner in (F.FintsSession, session):
        names = [n.lower().lstrip("_") for n in dir(owner)]
        assert not [n for n in names if n.startswith(_WRITE_PREFIXES)]
    public = {n for n in dir(F.FintsSession) if not n.startswith("_")}
    assert public == {
        "open",
        "poll",
        "accounts",
        "camt_formats",
        "transactions_xml",
        "balance",
        "close",
        "state",
        "tan_mechanism",
    }


# --- Guard 2: the client's own writes are refused -----------------------------------------

_REFUSED_METHODS = (
    "simple_sepa_transfer",
    "sepa_transfer",
    "sepa_debit",
    "_continue_sepa_transfer",
    "_continue_sepa_debit",
    "_send_pay_with_possible_retry",
    "approve_vop_response",
    "pause_dialog",
    "resume_dialog",
    "get_tan_media",
    "set_tan_medium",
)


@pytest.mark.parametrize("name", _REFUSED_METHODS)
def test_every_write_of_the_client_is_refused(name):
    client = _client()
    with (
        mock.patch.object(requests.Session, "request") as request,
        pytest.raises(F.WriteRefused),
    ):
        getattr(client, name)(None, None)
    request.assert_not_called()


def test_every_payment_method_python_fints_has_is_refused():
    """A python-fints upgrade that adds a payment method fails here until it is refused."""
    payment = re.compile(r"(sepa|transfer|debit|standing|pay|vop)", re.IGNORECASE)
    # Reads and helpers, not writes (get_scheduled_debits' segment is off the whitelist, so
    # guard 3 refuses it on the wire anyway).
    reads = {
        "get_sepa_accounts",
        "_get_sepa_accounts",
        "get_scheduled_debits",
        "_find_supported_sepa_version",
        "_find_vop_format_for_segment",
    }
    names = {n for n in dir(F.FinTS3PinTanClient) if payment.search(n)}
    assert names - reads <= set(_REFUSED_METHODS)


# --- Guard 3: the wire whitelist ------------------------------------------------------------


@pytest.mark.parametrize(
    "segment",
    [
        HKCCS1(
            account=KTI1(iban="DE89370400440532013000", bic="TESTDEFFXXX"),
            sepa_descriptor="urn:iso:std:iso:20022:tech:xsd:pain.001.001.03",
            sepa_pain_message=b"<Document/>",
        ),
        HKTAB5(),
    ],
    ids=["transfer", "tan_media"],
)
def test_the_connection_refuses_a_segment_off_the_whitelist_inside_the_envelope(segment):
    client = _client()
    message = _message(client, segment)
    outer = [s.header.type for s in message.segments]
    assert "HNVSD" in outer and segment.header.type not in outer  # it sits inside HNVSD

    with (
        mock.patch.object(requests.Session, "request") as request,
        pytest.raises(F.WriteRefused) as refused,
    ):
        client.connection.send(message)
    request.assert_not_called()
    assert segment.header.type in str(refused.value)


def test_an_allowed_message_is_posted_with_a_timeout():
    client = _client()
    message = _message(client)
    with mock.patch.object(
        requests.Session, "request", return_value=_bank_answer(message)
    ) as request:
        answer = client.connection.send(message)
    assert request.call_args.kwargs["timeout"] == F.HTTP_TIMEOUT_S
    assert request.call_args.args[:2] == ("POST", URL)
    assert "HNVSD" in [s.header.type for s in answer.segments]


def test_the_whitelist_has_only_the_envelope_the_dialog_and_four_reads():
    assert {
        "HNHBK",
        "HNHBS",
        "HNVSK",
        "HNVSD",
        "HNSHK",
        "HNSHA",
        "HKIDN",
        "HKVVB",
        "HKSYN",
        "HKTAN",
        "HKEND",
        "HKSPA",
        "HKCAZ",
        "HKSAL",
    } == F.ALLOWED_SEGMENTS


# --- Logging (D119) -------------------------------------------------------------------------


class _Everything(logging.Handler):
    """Keeps every record as text: its formatted message and all its attributes (extras
    such as fints_response_text included)."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(f"{record.getMessage()} {record.__dict__!r}")

    def getvalue(self) -> str:
        return "\n".join(self.lines)


@pytest.fixture
def debug_log():
    """Every record that reaches the root logger at DEBUG."""
    handler = _Everything()
    root = logging.getLogger()
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(level)


def test_a_debug_run_logs_no_pin_login_or_product_id(debug_log):
    client = _client()  # pins the fints loggers
    message = _message(client)
    assert all(s.encode() in message.render_bytes() for s in SECRETS)  # they are on the wire

    with mock.patch.object(requests.Session, "request", return_value=_bank_answer(message)):
        client.connection.send(message)
    for code in ("0010", "3955", "9800"):  # logged at INFO, WARNING and ERROR
        answer = SimpleNamespace(code=code, text=f"Benutzer {LOGIN} PIN {PIN}", parameters=[LOGIN])
        client._log_response(None, answer)

    text = debug_log.getvalue()
    assert not [s for s in SECRETS if s in text]
    assert "fints answer 3955" in text and "fints answer 9800" in text
    assert "fints answer 0010" not in text  # INFO is below the pin


def test_an_unpinned_python_fints_would_log_the_login(debug_log):
    """The control for the test above: without the pin, DEBUG shows the login."""
    client = _client()
    message = _message(client)
    loggers = list(F._fints_loggers())
    saved = [(lg, lg.level, list(lg.filters)) for lg in loggers]
    try:
        for lg in loggers:
            lg.setLevel(logging.NOTSET)
            lg.filters[:] = []
        with mock.patch.object(requests.Session, "request", return_value=_bank_answer(message)):
            client.connection.send(message)
    finally:
        for lg, level, filters in saved:
            lg.setLevel(level)
            lg.filters[:] = filters
    assert LOGIN in debug_log.getvalue()


def test_configure_logging_pins_the_fints_loggers():
    from hausbuch.api.logging import configure_logging

    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    fints_logger = logging.getLogger(F.FINTS_LOGGER)
    fints_logger.setLevel(logging.DEBUG)
    try:
        configure_logging(io.StringIO(), logging.DEBUG)
        assert fints_logger.level == logging.WARNING
        assert F._FILTER in fints_logger.filters
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)
        F.pin_fints_logging()


# --- The state blob (D119) ------------------------------------------------------------------


def test_the_state_blob_holds_no_pin_login_or_product_id():
    client = _client()
    client.system_id = "SYSID-0001"
    client.selected_security_function = DECOUPLED
    blob = client.deconstruct(including_private=False)

    seen = {}

    class Into:
        def _set_data_v1(self, data):
            seen.update(data)

    decompress_datablob(DATA_BLOB_MAGIC, blob, Into())
    plain = repr(seen).encode()
    assert b"SYSID-0001" in plain
    assert not [s for s in SECRETS if s.encode() in plain or s.encode() in blob]


# --- The fake client mirrors the real one ---------------------------------------------------

_SESSION_CALLS = (
    "fetch_tan_mechanisms",
    "get_tan_mechanisms",
    "set_tan_mechanism",
    "__enter__",
    "__exit__",
    "send_tan",
    "get_sepa_accounts",
    "supported_camt_formats",
    "get_transactions_xml",
    "get_balance",
    "deconstruct",
)


def _shape(function) -> list[tuple[str, object, object]]:
    return [(p.name, p.kind, p.default) for p in inspect.signature(function).parameters.values()]


@pytest.mark.parametrize("name", _SESSION_CALLS)
def test_the_fake_client_has_the_real_signatures(name):
    assert _shape(getattr(FakeClient, name)) == _shape(getattr(F._ReadOnlyClient, name))


def test_the_session_calls_only_what_the_fake_mirrors():
    source = Path(F.__file__).read_text()
    session = source[source.index("class FintsSession") : source.index("def open_session")]
    called = set(re.findall(r"\b(?:c|self\._client)\.(\w+)\(", session))
    called |= set(re.findall(r"self\._client\.(\w+),", session))
    assert called <= set(_SESSION_CALLS)


# --- The session ----------------------------------------------------------------------------


def _session(script: Script, *, restored: bool = False, mechanism: str | None = None):
    made: list[FakeClient] = []
    session = F.open_session(
        bank_code=BANK_CODE,
        login_name=LOGIN,
        pin=PIN,
        url=URL,
        product_id=PRODUCT_ID,
        client_data=b"blob" if restored else None,
        tan_mechanism=mechanism,
        client_factory=factory(script, made),
    )
    return session, made[0]


def test_a_first_login_picks_the_decoupled_mechanism_and_returns_the_approval():
    session, client = _session(
        Script(mechanisms={CHIPTAN: False, DECOUPLED: True}, approval_polls=1)
    )
    approval = session.open()
    assert isinstance(approval, F.Approval)
    assert session.tan_mechanism == DECOUPLED
    assert client.selected_security_function == DECOUPLED
    assert isinstance(session.poll(approval), F.Approval)  # 3956: not yet
    assert isinstance(session.poll(approval), F.Confirmed)
    assert client.init["pin"] == PIN and client.init["product_id"] == PRODUCT_ID


def test_a_restored_state_seeds_the_stored_mechanism_without_fetching():
    session, client = _session(Script(approval_polls=0), restored=True, mechanism=DECOUPLED)
    assert session.open() is None  # no approval asked after a restore in this script
    assert "fetch_tan_mechanisms" not in client.calls
    assert client.allowed_security_functions == [DECOUPLED]


def test_a_bank_without_a_decoupled_mechanism_is_refused():
    session, _ = _session(Script(mechanisms={CHIPTAN: False}))
    with pytest.raises(F.BankError) as raised:
        session.open()
    assert raised.value.code == E.BANK_TAN_NOT_DECOUPLED


def test_a_non_decoupled_challenge_is_refused():
    session, _ = _session(Script(approval_polls=0, challenge_decoupled=False))
    with pytest.raises(F.BankError) as raised:
        session.open()
    assert raised.value.code == E.BANK_TAN_NOT_DECOUPLED


def test_the_reads_return_booked_streams_and_a_decimal_balance():
    script = Script(streams=[b"<a/>", b"<b/>"], balance=Decimal("-3028.5"))
    session, client = _session(script)
    assert session.open() is None
    account = session.accounts()[0]
    assert account == fake_account()
    streams = session.transactions_xml(account, date(2026, 10, 1), date(2026, 11, 11))
    assert streams == [b"<a/>", b"<b/>"]  # the pending stream is dropped
    assert client.window[2] == ["urn:iso:std:iso:20022:tech:xsd:camt.052.001.08"]
    balance = session.balance(account)
    assert balance == Decimal("-3028.50") and str(balance) == "-3028.50"
    assert session.state() == script.state
    assert client.deconstructed_private is False
    session.close()
    session.close()
    assert client.calls.count("close") == 1


def test_a_bank_offering_no_readable_camt_is_unsupported():
    session, _ = _session(Script(camt_formats=["urn:iso:std:iso:20022:tech:xsd:camt.054.001.02"]))
    session.open()
    with pytest.raises(F.BankError) as raised:
        session.camt_formats()
    assert raised.value.code == E.BANK_UNSUPPORTED_FORMAT


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (FinTSConnectionError("down"), E.BANK_UNREACHABLE),
        (FinTSNoResponseError("silent"), E.BANK_UNREACHABLE),
        (requests.ConnectionError("dns"), E.BANK_UNREACHABLE),
        (TimeoutError("slow"), E.BANK_UNREACHABLE),
        (FinTSUnsupportedOperation("no HKCAZ"), E.BANK_UNSUPPORTED_FORMAT),
        (FinTSClientPINError(LOGIN), E.BANK_REFUSED),
        (FinTSDialogInitError(LOGIN), E.BANK_REFUSED),
        (ValueError(LOGIN), E.BANK_FAILED),
    ],
)
def test_failures_map_to_codes_and_drop_their_text(failure, code):
    session, _ = _session(Script(fail_at={"accounts": failure}))
    session.open()
    with pytest.raises(F.BankError) as raised:
        session.accounts()
    assert (raised.value.code, raised.value.response_code) == (code, None)
    assert str(raised.value) == code
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


def test_a_dialog_init_failure_caused_by_the_network_is_unreachable():
    init = FinTSDialogInitError("init")
    init.__cause__ = FinTSConnectionError("down")
    session, _ = _session(Script(fail_at={"open": init}))
    with pytest.raises(F.BankError) as raised:
        session.open()
    assert raised.value.code == E.BANK_UNREACHABLE


def test_a_9xxx_answer_is_refused_with_its_four_digits_only():
    session, _ = _session(Script(refuse_at={"accounts": "9942"}))
    session.open()
    with pytest.raises(F.BankError) as raised:
        session.accounts()
    assert (raised.value.code, raised.value.response_code) == (E.BANK_REFUSED, "9942")
    assert LOGIN not in str(raised.value)


def test_a_write_refusal_is_never_turned_into_a_bank_code():
    session, _ = _session(Script(fail_at={"accounts": F.WriteRefused("HKCCS")}))
    session.open()
    with pytest.raises(F.WriteRefused):
        session.accounts()


def test_an_unreadable_state_blob_is_state_rejected():
    def broken(**kwargs):
        raise ValueError("bad blob")

    with pytest.raises(F.BankError) as raised:
        F.open_session(
            bank_code=BANK_CODE,
            login_name=LOGIN,
            pin=PIN,
            url=URL,
            product_id=PRODUCT_ID,
            client_data=b"garbage",
            tan_mechanism=DECOUPLED,
            client_factory=broken,
        )
    assert raised.value.code == E.BANK_STATE_REJECTED
