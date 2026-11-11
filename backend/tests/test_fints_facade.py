"""services.bank.fints, the read-only FinTS facade (docs/phases/phase-5.md §4, D118, D119).

The three guards one by one (no write on the facade, the client's write methods refused, the
wire whitelist inside the encryption envelope too), the HTTP timeout, the logging pin with a
real python-fints message at DEBUG, the state blob without secrets, the error mapping, and
the fake client's signatures against the real client's. No network: the HTTP post is
patched. Every bank code, login, PIN, URL and product id is synthetic."""

import base64
import contextlib
import inspect
import io
import json
import logging
import re
import socket
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
from fints.formals import (
    KTI1,
    AccountInformation,
    BankIdentifier,
    Language2,
    Response,
    SystemIDStatus,
    UPDUsage,
)
from fints.message import FinTSInstituteMessage
from fints.segments.auth import HKIDN2, HKTAB5, HKTAN7, HKVVB3
from fints.segments.bank import HIUPA4, HIUPD6
from fints.segments.dialog import HIRMG2, HIRMS2, HISYN4
from fints.segments.transfer import HKCCS1
from fints.utils import decompress_datablob
from fints_fake import CHIPTAN, DECOUPLED, FakeClient, Script, factory, fake_account
from urllib3.exceptions import MaxRetryError, NameResolutionError

from hausbuch.rules import errors as E
from hausbuch.services.bank import fints as F
from hausbuch.services.bank import fints_trace

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
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)
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


# --- The trace (D148) -----------------------------------------------------------------------

IBAN = "DE89370400440532013000"
ACCOUNT_NUMBER = "0532013000"
SYSTEM_ID = "SYSID-0001-SECRET"
DIALOG_ID = "DIALOG-77-SECRET"
NEVER_LOGGED = (*SECRETS, IBAN, ACCOUNT_NUMBER, SYSTEM_ID, DIALOG_ID)


def _answer(code: str, text: str, parameters: list[str] | None = None) -> Response:
    return Response(code=code, reference_element="", text=text, parameters=parameters)


def _institute_answer(*segments, dialog_id: str = DIALOG_ID):
    """The bank's answer as a real rendered message: the customer's identification (login,
    product id) and signature (PIN) echoed inside the envelope, a dialog id, plus `segments`.
    Everything a whitelist mistake could leak is on it."""
    builder = _client()
    dialog = builder._new_dialog()
    dialog.open = True
    dialog.dialog_id = dialog_id
    message = dialog.new_customer_message()
    message += HKIDN2(
        builder.bank_identifier, builder.customer_id, SYSTEM_ID, SystemIDStatus.ID_NECESSARY
    )
    message += HKVVB3(0, 0, Language2.DE, builder.product_name, builder.product_version)
    for segment in segments:
        message += segment
    dialog.finish_message(message)
    return message


def _account_data():
    """The UPD a bank sends at login: the login, an IBAN, an account number."""
    return [
        HIUPA4(user_identifier=LOGIN, upd_version=7, upd_usage=UPDUsage.UPD_CONCLUSIVE),
        HIUPD6(
            account_information=AccountInformation(
                ACCOUNT_NUMBER,
                "",
                BankIdentifier(BankIdentifier.COUNTRY_ALPHA_TO_NUMERIC["DE"], BANK_CODE),
            ),
            iban=IBAN,
            customer_id=LOGIN,
            account_type=1,
            account_currency="EUR",
            name_account_owner_1="Owner A",
        ),
    ]


def _real_session() -> F.FintsSession:
    return F.open_session(
        bank_code=BANK_CODE,
        login_name=LOGIN,
        pin=PIN,
        url=URL,
        product_id=PRODUCT_ID,
        client_data=None,
        tan_mechanism=None,
        log_fields={"sync_id": "sync-0001"},
    )


def _trace_records(log, message: str = "fints exchange") -> list[dict]:
    return [
        r.fields
        for r in log.records
        if r.name == "hausbuch.services.bank.fints_trace" and r.getMessage() == message
    ]


def _assert_nothing_secret(log) -> None:
    text = log.getvalue()
    assert not [s for s in NEVER_LOGGED if s in text]
    for record in log.records:  # the arguments and the fields, each on its own
        for part in (repr(record.args), repr(getattr(record, "fields", None))):
            assert not [s for s in NEVER_LOGGED if s in part]


def test_the_trace_of_a_bootstrap_logs_no_secret(debug_log):
    """A real client through the bootstrap (HKSYN, then HKEND) against a bank whose answers
    carry the login, the PIN, the product id, the system id, a dialog id, an IBAN and an
    account number: every record of every logger, at DEBUG, holds none of them."""
    session = _real_session()
    answer = _institute_answer(
        HIRMG2(responses=[_answer("3060", f"Produkt {PRODUCT_ID} beachten")]),
        HIRMS2(responses=[_answer("3920", f"Verfahren für {LOGIN} PIN {PIN}", ["946"])]),
        HISYN4(system_id=SYSTEM_ID),
        *_account_data(),
    )
    with (
        mock.patch.object(requests.Session, "request", return_value=_bank_answer(answer)),
        pytest.raises(F.BankError) as raised,
    ):
        session.open()
    assert raised.value.code == E.BANK_TAN_NOT_DECOUPLED  # no HITANS offered: the run stops

    _assert_nothing_secret(debug_log)
    [facts] = _trace_records(debug_log, "fints session")
    assert facts["mode"] == "bootstrap" and facts["system_id"] == "0"
    assert facts["product_id"] == "set (25 chars)" and facts["host"] == "fints.example.org"
    assert facts["sync_id"] == "sync-0001" and facts["python_fints"]

    init, end = _trace_records(debug_log)
    assert (init["step"], init["exchange"], init["sync_id"]) == ("bootstrap", 1, "sync-0001")
    assert end["exchange"] == 2
    kinds = [entry.split(":")[0] for entry in init["sent"]]
    assert kinds[:3] == ["HNHBK", "HNVSK", "HNVSD"]
    assert {"HNSHK", "HKIDN", "HKVVB", "HKSYN", "HNSHA", "HNHBS"} <= set(kinds)
    settings = {key.split(":")[0]: value for key, value in init["settings"].items()}
    assert settings["HKIDN"] == {"bank_code": BANK_CODE, "system_id": "0", "system_id_status": "1"}
    assert settings["HKVVB"]["product_id"] == "set (25 chars)"
    assert settings["HKSYN"] == {"mode": "0"}
    assert settings["HNSHK"] == {"security_function": "999"}
    assert set(settings) == {"HKIDN", "HKVVB", "HKSYN", "HNSHK"}
    assert init["http_status"] == 200 and init["http_bytes"] > 0 and init["outcome"] == "answered"
    assert "3060 → message: Produkt <hidden> beachten" in init["answers"]
    assert "HIUPD:" in " ".join(init["received"]) and init["facts"] == {"upd_version": "7"}
    assert "HKEND" in {e.split(":")[0] for e in end["sent"]}
    assert [k.split(":")[0] for k in end["settings"]] == ["HNSHK"]  # HKEND: its header only
    json.dumps(init)  # the production formatter's JSON


def test_a_refused_init_names_the_segment_the_bank_refused(debug_log):
    """The first real syncs (11 Nov 2026): 9050 and 9800 for the message, 9010 for one of our
    segments. The trace resolves HIRMS's reference to the segment we sent."""
    session = _real_session()
    numbers = {}

    def bank(method, url, data=None, **kwargs):
        sent = FinTSInstituteMessage(segments=base64.b64decode(data))
        numbers["HKVVB"] = sent.find_segment_first("HKVVB").header.number
        hirms = HIRMS2(responses=[_answer("9010", "Initialisierung fehlgeschlagen.")])
        hirms.header.reference = numbers["HKVVB"]
        hirmg = HIRMG2(
            responses=[
                _answer("9050", "Die Nachricht enthält Fehler."),
                _answer("9800", "Dialog abgebrochen"),
            ]
        )
        return _bank_answer(_institute_answer(hirmg, hirms))

    with (
        mock.patch.object(requests.Session, "request", side_effect=bank),
        pytest.raises(F.BankError) as raised,
    ):
        session.open()
    assert (raised.value.code, raised.value.response_code) == (E.BANK_REFUSED, "9010")
    assert raised.value.exc_chain is None  # the bank's code decided it

    [record] = _trace_records(debug_log)
    assert record["answers"] == [
        "9050 → message: Die Nachricht enthält Fehler.",
        "9800 → message: Dialog abgebrochen",
        f"9010 → HKVVB:{numbers['HKVVB']}: Initialisierung fehlgeschlagen.",
    ]
    _assert_nothing_secret(debug_log)


def test_a_transport_failure_logs_class_names_only_and_is_unreachable(debug_log):
    def unreachable(*args, **kwargs):
        try:
            try:
                gai = socket.gaierror(-2, "Name or service not known")
                raise NameResolutionError("fints.example.org", None, gai)
            except NameResolutionError as reason:
                raise MaxRetryError(None, URL, reason) from reason
        except MaxRetryError as exc:
            # as requests' adapter does: the MaxRetryError is the context, not the cause
            raise requests.ConnectionError(exc)  # noqa: B904

    session = _real_session()
    with (
        mock.patch.object(requests.Session, "request", side_effect=unreachable),
        pytest.raises(F.BankError) as raised,
    ):
        session.open()
    chain = "FinTSConnectionError ← ConnectionError ← MaxRetryError ← NameResolutionError"
    assert raised.value.code == E.BANK_UNREACHABLE
    assert raised.value.exc_chain.startswith(chain)

    [record] = _trace_records(debug_log)
    assert record["outcome"] == "failed" and record["exc_chain"].startswith(chain)
    assert "http_status" not in record and "answers" not in record
    text = debug_log.getvalue()
    assert "Name or service" not in text and "Failed to resolve" not in text
    assert "Could not connect" not in text


def test_the_trace_prints_no_field_of_a_segment_off_its_whitelist():
    """An unknown sent segment (a transfer, with an IBAN) and the received data segments (the
    system id, the UPD with IBAN and account number) print their header only."""
    client = _client()
    transfer = HKCCS1(
        account=KTI1(iban=IBAN, bic="TESTDEFFXXX"),
        sepa_descriptor="urn:iso:std:iso:20022:tech:xsd:pain.001.001.03",
        sepa_pain_message=b"<Document/>",
    )
    message = _message(client, transfer)
    sent = fints_trace.describe_sent(message.segments)
    assert any(entry.startswith("HKCCS:") for entry in sent["sent"])
    assert not [key for key in sent["settings"] if key.startswith(("HKCCS", "HNSHA", "HNVSK"))]

    answer = _institute_answer(HISYN4(system_id=SYSTEM_ID), *_account_data())
    received = fints_trace.describe_received(
        answer.segments, fints_trace.segment_map(message.segments), (LOGIN, PIN, PRODUCT_ID)
    )
    assert {"HISYN", "HIUPA", "HIUPD", "HKIDN"} <= {e.split(":")[0] for e in received["received"]}
    everything = repr(sent) + repr(received)
    assert not [s for s in NEVER_LOGGED if s in everything]
    assert set(fints_trace._SENT_SETTINGS) == {"HKIDN", "HKVVB", "HKSYN", "HKTAN", "HNSHK"}
    assert {"HIBPA", "HIUPA", "HITANS", "HICAZS"} == fints_trace._RECEIVED_FACTS


def test_the_trace_names_whether_a_tan_medium_is_set_never_the_name():
    for name, shown in ((None, "absent"), ("", "empty"), ("Mein Handy", "set")):
        hktan = HKTAN7(tan_process="4", segment_type="HKIDN", tan_medium_name=name)
        settings = fints_trace.describe_sent([hktan])["settings"]
        assert list(settings.values()) == [
            {"version": "7", "tan_process": "4", "segment_type": "HKIDN", "tan_medium": shown}
        ]


def test_the_session_facts_of_a_fake_client_are_logged_without_failing(debug_log):
    session, _ = _session(Script(approval_polls=None))
    session.open()
    [facts] = _trace_records(debug_log, "fints session")
    assert facts["mode"] == "bootstrap" and facts["product_id"] == "missing"


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


def test_a_general_code_before_the_reason_keeps_the_reason():
    """The bank's message-level 9050 comes before the segment's own code (the first real
    sync, 11 Nov 2026, kept only the 9050)."""
    session, _ = _session(Script(refuse_at={"accounts": "9050,9942"}))
    session.open()
    with pytest.raises(F.BankError) as raised:
        session.accounts()
    assert (raised.value.code, raised.value.response_code) == (E.BANK_REFUSED, "9942")
    assert raised.value.response_codes == ("9050", "9942")


def test_a_code_the_bootstrap_tolerates_does_not_refuse_the_login():
    """python-fints runs fetch_tan_mechanisms in bootstrap mode and lets a 9xxx answer pass
    there; only the codes after it count (PR #160 review)."""
    session, client = _session(Script(bootstrap_codes=["9955"], approval_polls=0))
    assert isinstance(session.open(), F.Approval)
    assert client.calls[:2] == ["fetch_tan_mechanisms", "open"]
    assert session.tan_mechanism == DECOUPLED


def test_a_code_after_the_bootstrap_still_refuses_the_login():
    session, _ = _session(Script(bootstrap_codes=["9955"], refuse_at={"open": "9942"}))
    with pytest.raises(F.BankError) as raised:
        session.open()
    assert (raised.value.code, raised.value.response_code) == (E.BANK_REFUSED, "9942")


def test_a_pin_error_in_the_bootstrap_is_refused():
    session, _ = _session(Script(refuse_at={"bootstrap": "9931"}))
    with pytest.raises(F.BankError) as raised:
        session.open()
    assert (raised.value.code, raised.value.response_code) == (E.BANK_REFUSED, "9931")
    assert LOGIN not in str(raised.value)


def test_a_pin_exception_in_the_bootstrap_without_a_code_is_refused():
    session, _ = _session(Script(fail_at={"bootstrap": FinTSClientPINError("PIN wrong?")}))
    with pytest.raises(F.BankError) as raised:
        session.open()
    assert (raised.value.code, raised.value.response_code) == (E.BANK_REFUSED, None)


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


def test_the_client_keeps_the_banks_answers_redacted():
    client = object.__new__(F._ReadOnlyClient)
    client.refused_codes, client.answers = [], []
    client.user_id, client.customer_id = LOGIN, LOGIN
    client.pin = SimpleNamespace(value=PIN)
    answers = (
        ("3920", "Zugelassene Verfahren"),
        ("9010", f"Auftrag für {LOGIN} mit {PIN} abgelehnt"),
    )
    for code, text in answers:
        with contextlib.suppress(Exception):  # python-fints' own handling needs a dialog
            client._process_response(None, None, SimpleNamespace(code=code, text=text))
    assert client.refused_codes == ["9010"]
    assert client.answers == [
        "3920 Zugelassene Verfahren",
        "9010 Auftrag für <hidden> mit <hidden> abgelehnt",
    ]


def test_a_bank_text_quoting_the_product_id_keeps_it_hidden():
    """The product id is not all digits, so the long-number mask would miss it (D148)."""
    client = object.__new__(F._ReadOnlyClient)
    client.refused_codes, client.answers = [], []
    client.user_id, client.customer_id = LOGIN, LOGIN
    client.pin = SimpleNamespace(value=PIN)
    client.product_name = PRODUCT_ID
    client.system_id = "0"
    text = f"Produkt {PRODUCT_ID.lower()} nicht zugelassen"
    with contextlib.suppress(Exception):
        client._process_response(None, None, SimpleNamespace(code="9078", text=text))
    assert client.answers == ["9078 Produkt <hidden> nicht zugelassen"]
