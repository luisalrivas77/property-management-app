"""The read-only FinTS facade (docs/phases/phase-5.md §4, D115, D118, D119).

python-fints' FinTS3PinTanClient can do everything online banking can, transfers included.
Hausbuch needs four reads and the decoupled-TAN approval, so the job never touches the client
itself: it gets a `FintsSession`, whose whole surface is

    open() -> Approval | None          the standing dialog (and the pushTAN it may need)
    poll(approval) -> Approval | Confirmed   one HKTAN process S status query (D118)
    accounts() / transactions_xml() / balance()   HKSPA, HKCAZ (booked camt only), HKSAL
    camt_formats()                     what HICAZS offers that the parser reads
    close()                            HKEND
    state() -> bytes                   deconstruct(including_private=False) (D119)
    tan_mechanism                      the decoupled security function in use
    statements_offered() / statement_pdf() / reopen()   the bank's PDF statements by
                                       number (HKEKP v1, D154); they never raise a
                                       BankError: the answers go to
                                       rules.bank_statements.read_answer

Three guards keep it read-only, each tested on its own (tests/test_fints_facade.py):

1. The facade has no method that writes; no attribute reachable on it starts with `sepa_`,
   `transfer`, `debit` or `standing`.
2. `_ReadOnlyClient` overrides every python-fints method that sends a payment, a debit or
   pauses a dialog so that it raises `WriteRefused` before building a segment.
3. `_GuardedConnection` checks every message on the wire, inside the encryption envelope
   (HNVSD) too, against ALLOWED_SEGMENTS, and refuses anything else before the HTTP post.
   It also gives the post a timeout (python-fints' own has none).

Secrets (D119): the PIN and the product id come from Settings into the client's constructor
and nowhere else; the client state is `deconstruct(including_private=False)`, which keeps the
system id, the bank parameter data and the chosen TAN mechanism, never the PIN, the login or
the account list. The `fints` loggers are pinned to WARNING with a filter that reduces every
record to its FinTS answer code (`pin_fints_logging`), since python-fints at DEBUG logs whole
messages (login and product id in clear) and at WARNING/ERROR the bank's answer text, which
may quote the login.

The trace (D148, 11 Nov 2026, after the first real syncs were refused in 0.4 s with only
9050/9800/9010 in the log): always on, at INFO under the app's own logger
`hausbuch.services.bank.fints_trace`, never under `fints.*` (whose pin is unchanged).
`_GuardedConnection.send` writes one record per exchange: the segments sent and received as
`HKVVB:4v3` (envelope included), a whitelist of non-secret settings (HKIDN's bank code and
whether the system id is assigned; HKVVB's BPD/UPD versions, language, product version and the
product id's length only; HKSYN's mode; HKTAN's version, process, segment type and whether a
TAN medium is named; HNSHK's security function), the HTTP status, size and time, and every
HIRMG/HIRMS answer as `9010 → HKVVB:4: text`, its HIRMS reference resolved to the segment we
sent, the text redacted (login, customer id, PIN, product id, system id and dialog id hidden,
numbers of five digits or more blanked), the parameters printed only for 3920 and otherwise
counted (3072 carries a new login, 3040 a touchdown point). From the bank's parameters: the
BPD and UPD versions, the offered security functions with their names and whether each is
decoupled, and the camt formats. A transport failure logs the exception chain as class names
only and is re-raised unchanged. `open()` adds one record of the session's facts (bootstrap
or restored, the security function, whether the system id is assigned, BPD/UPD versions, the
python-fints version, the product id's length, the product version, the URL's host). Each
record carries
the step (`bootstrap`, `open`, `poll`, `accounts`, `transactions`, `balance`, `statements`,
`close`), the
exchange's number in the session and the job's `log_fields` (the sync id).
It never logs, at any level: the PIN, the login, the customer id, the product id, the system
id or a dialog id value, an IBAN, an account number, a balance, camt content or a TAN
challenge. It is a whitelist: an unknown segment or field prints only its type, number and
version, and the data segments (HISPA, HICAZ, HISAL, HITAN, HISYN, HIUPD) only that. D119's
rule (no secret in a log, an event or the database) holds unchanged; the trace only adds
facts the bank's public parameters and our segment headers already state.

Errors: every failure leaves this module as `BankError(code, response_code)` with a fixed
message. A python-fints exception's text is never kept (the generic Exception it raises for
a 9xxx answer to HKTAN holds the whole response); the code comes from the bank's answers,
else from the exception's type. The answers are read from the raw response by the trace's
parser in `_GuardedConnection.send`, before python-fints processes it (D151, 11 Nov 2026):
python-fints walks HIRMG before HIRMS and raises on a message-level 9010, so a hook in its
`_process_response` saw 9050/9800/9010 and never the segment's 9078 that said why.
`rules.deciding_code` picks the reason: the first specific 9xxx code, segment level first.

Only checked against the real bank in step 1 (§16 Q-O34 to Q-O36): after a restored state
`allowed_security_functions` is empty (python-fints keeps it only with private data), so
`open()` seeds it with the stored mechanism for the init's HKTAN; the TAN medium is left
to python-fints (an empty name when the bank requires one, as its own Sparkasse workaround
does; HKTAB is never sent).
"""

import functools
import logging
import re
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager, suppress
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import requests
from fints.client import FinTS3PinTanClient, NeedTANResponse
from fints.connection import FinTSHTTPSConnection
from fints.exceptions import (
    FinTSClientPINError,
    FinTSClientTemporaryAuthError,
    FinTSConnectionError,
    FinTSDialogInitError,
    FinTSError,
    FinTSNoResponseError,
    FinTSSCARequiredError,
    FinTSUnsupportedOperation,
)
from fints.fields import DataElementField, DataElementGroupField
from fints.formals import KTI1, DataElementGroup
from fints.models import SEPAAccount
from fints.segments.base import FinTS3Segment, ParameterSegment

from hausbuch.rules import errors as E
from hausbuch.rules.bank_sync import BankAnswer, deciding_code, refused_code
from hausbuch.schemas.bank_sync import BankParameters

from . import fints_trace
from .fints_trace import trace_context

FINTS_LOGGER = "fints"
HTTP_TIMEOUT_S = 60

# The segments a message of this app may carry (D119): the envelope (message head and
# tail, signature, encryption), the dialog (identification, product, system id, TAN, end)
# and the four reads (SEPA accounts, camt transactions, balance). Never a payment, a debit,
# a standing order, a TAN medium change or anything else.
ALLOWED_SEGMENTS = frozenset(
    {
        "HNHBK",  # Nachrichtenkopf
        "HNHBS",  # Nachrichtenabschluss
        "HNVSK",  # Verschlüsselungskopf
        "HNVSD",  # verschlüsselte Daten (checked inside)
        "HNSHK",  # Signaturkopf
        "HNSHA",  # Signaturabschluss
        "HKIDN",  # Identifikation
        "HKVVB",  # Verarbeitungsvorbereitung (the product id)
        "HKSYN",  # Synchronisierung (the system id)
        "HKTAN",  # Zwei-Schritt-TAN (process 4 at init, S for the decoupled status)
        "HKEND",  # Dialogende
        "HKSPA",  # SEPA-Kontoverbindungen
        "HKCAZ",  # Kontoumsätze camt
        "HKSAL",  # Saldenabfrage
        # Kontoauszug PDF (D154): only ever with a statement number and year, which
        # re-delivers a statement the bank already cut; `_check_statement_request` refuses
        # one without them (an empty number would cut a new statement) or with a count.
        # HKQTG, the receipt the bank asks for, is never on this list: it changes state.
        "HKEKP",
    }
)

# camt messages phase 2's parser reads (D33): camt.052 and camt.053, versions 02 and 08.
_READABLE_CAMT = re.compile(r"camt\.05[23]\.001\.0[28]")
_ANSWER_CODE = re.compile(r"[0-9]{4}")


# --- Kontoauszug PDF (D154; DK Messages, Multibankfähige Geschäftsvorfälle C.2.5.1) ---------
# python-fints 5.0.1 defines no HKEKP, so these three are ours. Defining them registers them
# with python-fints' parser (it finds a segment class by TYPE and VERSION among
# FinTS3Segment's subclasses), so the bank's HIEKPS and HIEKP are read into them.


class StatementPdfParameter1(DataElementGroup):
    """Parameter Kontoauszug PDF, version 1 (HIEKPS v1)."""

    number_allowed = DataElementField(type="jn", _d="Kontoauszugsnummer erlaubt")
    receipt_required = DataElementField(type="jn", _d="Quittierung benötigt")
    entries_allowed = DataElementField(type="jn", _d="Eingabe Anzahl Einträge erlaubt")


class HIEKPS1(ParameterSegment):
    """Kontoauszug PDF Parameter, version 1."""

    parameter = DataElementGroupField(type=StatementPdfParameter1, _d="Parameter Kontoauszug PDF")


class HKEKP1(FinTS3Segment):
    """Kontoauszug PDF anfordern, version 1. The facade fills the number and the year always
    and the number of entries never (the guard checks both on the wire)."""

    account = DataElementGroupField(type=KTI1, _d="Kontoverbindung international")
    statement_number = DataElementField(
        type="num", max_length=5, required=False, _d="Kontoauszugsnummer"
    )
    statement_year = DataElementField(type="num", length=4, required=False, _d="Kontoauszugsjahr")
    max_number_responses = DataElementField(
        type="num", max_length=4, required=False, _d="Maximale Anzahl Einträge"
    )
    touchdown_point = DataElementField(type="an", max_length=35, required=False, _d="Aufsetzpunkt")


class HIEKP1(FinTS3Segment):
    """Kontoauszug PDF, version 1. „Gebuchte Umsätze“ holds the PDF, base64-encoded by the
    DK's note for this version. The Quittungscode is read by the parser but never by us: it
    only serves an HKQTG, which is never sent."""

    statement_booked = DataElementField(type="bin", _d="Gebuchte Umsätze")
    receipt_code = DataElementField(type="bin", required=False, _d="Quittungscode")


def _positive(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _check_statement_request(segments: Any) -> None:
    """D154 (1) on the wire: an HKEKP leaves only with a number and a year above 0 and
    without a number of entries, whoever built it."""
    for segment in fints_trace.walk(segments):
        if str(segment.header.type) != "HKEKP":
            continue
        number = getattr(segment, "statement_number", None)
        year = getattr(segment, "statement_year", None)
        if not (_positive(number) and _positive(year)):
            raise WriteRefused("HKEKP without a statement number and year is not allowed")
        if getattr(segment, "max_number_responses", None) is not None:
            raise WriteRefused("HKEKP with a number of entries is not allowed")


@dataclass(frozen=True)
class StatementAnswer:
    """What one HKEKP brought back, for rules.bank_statements.read_answer: the answers of the
    message (both levels), the HIEKP data, the 3040's Aufsetzpunkt (only to send it back,
    never logged) and, when the exchange failed without an answer to read, the failure's
    exception chain as class names (D148). `parts` is never logged."""

    answers: tuple[BankAnswer, ...]
    parts: tuple[bytes, ...] = field(default=(), repr=False)
    touchdown: str | None = field(default=None, repr=False)
    failure: str | None = None


class StatementTanRequired(Exception):
    """The bank's parameters mark HKEKP as needing a TAN: the facade does not send it."""


class BankError(Exception):
    """A sync failure as a bank.* code, plus for bank.refused the bank's answers:
    `response_code` the one that says why (rules.deciding_code, D151), `answers` every 3xxx
    and 9xxx answer of the failed step in the order the bank sent them, message level first,
    each with its level, the segment it refers to and its redacted text (for the log only,
    never an event or the database), and `response_codes` their codes. The message is the
    code: never the bank's or the library's text."""

    def __init__(
        self,
        code: str,
        response_code: str | None = None,
        *,
        answers: tuple[BankAnswer, ...] = (),
        exc_chain: str | None = None,
    ):
        super().__init__(code)
        self.code = code
        self.response_code = response_code
        self.answers = answers
        self.response_codes = tuple(a.code for a in answers) or (
            (response_code,) if response_code else ()
        )
        # A failure that carried no 9xxx code: the python-fints exception and its causes as
        # class names only (D148), e.g. no FinTS endpoint, or an answer it could not parse.
        self.exc_chain = exc_chain


def _is_warning_or_error(answer: BankAnswer) -> bool:
    return len(answer.code) == 4 and answer.code[0] in "39"


def _refusal_in(answers: list[BankAnswer]) -> bool:
    return any(refused_code(a.code) is not None for a in answers)


def _refused(answers: list[BankAnswer]) -> BankError:
    """bank.refused with the deciding code and every warning and error the bank sent (phase
    5, 11 Nov 2026: the first real sync kept only the message-level 9050/9800/9010 and lost
    the segment's 9078, D151)."""
    return BankError(
        E.BANK_REFUSED,
        deciding_code(answers),
        answers=tuple(a for a in answers if _is_warning_or_error(a)),
    )


def lock_refusal(answers: Sequence[BankAnswer]) -> BankError:
    """bank.refused for a lock code in a statement fetch's answers (D154 (4): a lock still
    ends the run as D151 says), with the same deciding code and answers as any refusal."""
    return _refused(list(answers))


class WriteRefused(RuntimeError):
    """A write was attempted through the read-only client: a bug, never a bank answer."""


# --- Logging (D119) ------------------------------------------------------------------------


class _RedactFintsRecord(logging.Filter):
    """Reduce a python-fints record to its answer code: the message becomes `fints answer
    <code>` (or `fints log line`), its arguments, the bank's text and parameters and any
    exception are dropped."""

    def filter(self, record: logging.LogRecord) -> bool:
        code = str(getattr(record, "fints_response_code", None) or "")
        record.msg = f"fints answer {code}" if _ANSWER_CODE.fullmatch(code) else "fints log line"
        record.args = ()
        for name in ("fints_response_text", "fints_response_parameters"):
            if hasattr(record, name):
                setattr(record, name, None)
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


_FILTER = _RedactFintsRecord()


def _fints_loggers() -> Iterator[logging.Logger]:
    yield logging.getLogger(FINTS_LOGGER)
    for module in ("client", "connection", "dialog", "message", "security", "types", "utils"):
        yield logging.getLogger(f"{FINTS_LOGGER}.{module}")
    for name in list(logging.root.manager.loggerDict):
        if name.startswith(f"{FINTS_LOGGER}."):
            yield logging.getLogger(name)


def pin_fints_logging() -> None:
    """Pin every `fints` logger to WARNING and redact what it still writes (D119). Called by
    api.logging.configure_logging and by every session, so a test or a CLI that never
    configured logging is covered too."""
    for fints_logger in _fints_loggers():
        fints_logger.setLevel(logging.WARNING)
        if _FILTER not in fints_logger.filters:
            fints_logger.addFilter(_FILTER)


# --- The guarded client ----------------------------------------------------------------------


def _segment_types(segments: Any) -> Iterator[str]:
    for segment in segments:
        kind = str(segment.header.type)
        yield kind
        if kind == "HNVSD":
            yield from _segment_types(segment.data.segments)


class _TimeoutSession(requests.Session):
    """A session with a timeout that keeps the last post's outcome for the trace (D148): the
    HTTP status, the answer's size in bytes and the time it took, never its content."""

    def __init__(self, timeout_s: float):
        super().__init__()
        self._timeout_s = timeout_s
        self.last_http: dict[str, Any] = {}

    def request(self, *args: Any, **kwargs: Any) -> requests.Response:  # type: ignore[override]
        kwargs.setdefault("timeout", self._timeout_s)
        self.last_http = {}
        started = time.perf_counter()
        try:
            response = super().request(*args, **kwargs)
        finally:
            self.last_http["http_ms"] = round((time.perf_counter() - started) * 1000)
        self.last_http["http_status"] = response.status_code
        self.last_http["http_bytes"] = len(response.content or b"")
        return response


def _no_hidden() -> tuple[str | None, ...]:
    return ()


class _GuardedConnection(FinTSHTTPSConnection):
    """python-fints' HTTPS connection with a timeout, the segment whitelist and the trace
    (D148): one INFO record per exchange, written after the answer or the failure. It keeps
    every answer of every received message in `answers` (D151), parsed by the trace before
    python-fints processes the message, so an answer python-fints never reaches (every HIRMS
    answer once it raised on a HIRMG 9010) is kept too."""

    def __init__(
        self,
        url: str,
        timeout_s: float = HTTP_TIMEOUT_S,
        *,
        hidden: fints_trace.Hidden = _no_hidden,
    ):
        super().__init__(url)
        self.session = _TimeoutSession(timeout_s)
        self._hidden = hidden
        self._exchanges = 0
        self.answers: list[BankAnswer] = []

    def send(self, msg: Any) -> Any:
        refused = sorted({t for t in _segment_types(msg.segments) if t not in ALLOWED_SEGMENTS})
        if refused:
            raise WriteRefused(f"segment not allowed: {', '.join(refused)}")
        _check_statement_request(msg.segments)
        self._exchanges += 1
        self.session.last_http = {}
        try:
            response = super().send(msg)
        except Exception as exc:  # traced by class names only, then re-raised unchanged
            self._trace(msg, error=exc)
            raise
        self.answers.extend(self._trace(msg, response=response))
        return response

    def _trace(
        self, msg: Any, *, response: Any = None, error: Exception | None = None
    ) -> list[BankAnswer]:
        return fints_trace.log_exchange(
            exchange=self._exchanges,
            segments=msg.segments,
            http=dict(getattr(self.session, "last_http", {})),
            response=response,
            error=error,
            hidden=self._hidden,
        )


def _refuse(name: str) -> Callable[..., Any]:
    def refused(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise WriteRefused(f"{name} is not available in Hausbuch")

    refused.__name__ = name
    return refused


class _ReadOnlyClient(FinTS3PinTanClient):
    """FinTS3PinTanClient without its writes, with the guarded connection, whose answers
    (every HIRMG and HIRMS answer of the session, redacted, D151) it shows as
    `bank_answers`."""

    def __init__(self, *args: Any, timeout_s: float = HTTP_TIMEOUT_S, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.connection = _GuardedConnection(
            self.connection.url, timeout_s, hidden=self._hidden_values
        )

    @property
    def bank_answers(self) -> list[BankAnswer]:
        return self.connection.answers

    def _hidden_values(self) -> tuple[str | None, ...]:
        """What a bank text must never show in the log: the login, the customer id, the PIN
        (should a bank echo it), the product id ("Produkt … nicht zugelassen"; it is not all
        digits, so the long-number mask would miss it, D148) and an assigned system id."""
        system_id = getattr(self, "system_id", None)
        return (
            getattr(self, "user_id", None),
            getattr(self, "customer_id", None),
            getattr(getattr(self, "pin", None), "value", None),
            getattr(self, "product_name", None),
            None if system_id in (None, fints_trace.SYSTEM_ID_UNASSIGNED) else str(system_id),
        )

    def supported_camt_formats(self) -> list[str]:
        """The camt messages HICAZS (the bank's parameters) lists, as the bank spells them."""
        hicazs = self.bpd.find_segment_first("HICAZS")
        return [] if not hicazs else [str(m) for m in hicazs.parameter.supported_camt_formats]

    def bank_parameters(self) -> BankParameters | None:
        """Phase 6 D173 (7): what the bank's parameters (BPD) say, None before any BPD was
        read (a bootstrap that failed). The bank's public parameters only: HIBPA's name and
        version, HICAZS's camt formats, HIEKPS, HITANS's decoupled functions and versions."""
        if self.bpa is None and not self.bpd.find_segment_first("HIBPA"):
            return None
        bpa = self.bpa or self.bpd.find_segment_first("HIBPA")
        raw_name = getattr(bpa, "bank_name", None)
        name = None if raw_name is None else (" ".join(str(raw_name).split())[:60] or None)
        version = getattr(bpa, "bpd_version", None)
        formats = [f[:100] for f in self.supported_camt_formats()][:20]
        decoupled: list[str] = []
        versions: list[int] = []
        for segment in self.bpd.find_segments("HITANS"):
            versions.append(int(segment.header.version))
            for parameter in getattr(segment.parameter, "twostep_parameters", None) or ():
                function = str(parameter.security_function)[:8]
                if _is_decoupled(parameter) and function not in decoupled:
                    decoupled.append(function)
        return BankParameters(
            bank_name=name,
            bpd_version=None if version is None else int(version),
            camt_formats=formats,
            camt_readable=any(_READABLE_CAMT.search(f) for f in formats),
            statement_pdf_by_number=self.statement_pdf_offered(),
            decoupled_methods=decoupled[:20],
            approval_versions=sorted(set(versions))[:10],
        )

    def statement_pdf_offered(self) -> bool:
        """D154 (1): the bank's parameters hold HIEKPS v1 with „Kontoauszugsnummer erlaubt“
        = J. A segment python-fints could not read into HIEKPS1 counts as not offered."""
        for segment in self.bpd.find_segments("HIEKPS"):
            parameter = getattr(segment, "parameter", None)
            if isinstance(segment, HIEKPS1) and getattr(parameter, "number_allowed", None):
                return True
        return False

    def get_statement_pdf(
        self, account: SEPAAccount, number: int, year: int, touchdown: str | None = None
    ) -> tuple[list[bytes], str | None]:
        """One HKEKP v1 for statement `number` of `year` in the standing dialog (one order
        per message, HIEKPS: at most 1). Returns the HIEKP data and the 3040's Aufsetzpunkt.
        Never sends a TAN segment: when HIPINS marks HKEKP as needing one, it raises
        StatementTanRequired before building anything."""
        if not (_positive(number) and _positive(year)):
            raise WriteRefused("HKEKP without a statement number and year is not allowed")
        if not self._standing_dialog:
            raise FinTSError("no standing dialog")
        segment = HKEKP1(
            account=KTI1.from_sepa_account(account),
            statement_number=number,
            statement_year=year,
            touchdown_point=touchdown,
        )
        if self._need_twostep_tan_for_segment(segment):
            raise StatementTanRequired
        with self._get_dialog() as dialog:
            response = dialog.send(segment)
        parts = []
        for answer in response.response_segments(segment, "HIEKP"):
            data = getattr(answer, "statement_booked", None)
            if data:
                parts.append(bytes(data))
        following = None
        for answer in response.responses(segment, "3040"):
            parameters = list(answer.parameters or ())
            following = str(parameters[0]) if parameters and parameters[0] else None
        return parts, following

    def restart_dialog(self) -> NeedTANResponse | None:
        """A new standing dialog after the bank ended the last one (9800, D154 (4)): the
        ended one is dropped without HKEND (the bank closed it), the new one opened as
        `open()` does. Returns the TAN demand of its opening, if any (the caller then
        closes it and never polls)."""
        self._standing_dialog = None
        self.init_tan_response = None
        self.__enter__()
        return self.init_tan_response

    simple_sepa_transfer = _refuse("simple_sepa_transfer")
    sepa_transfer = _refuse("sepa_transfer")
    sepa_debit = _refuse("sepa_debit")
    _continue_sepa_transfer = _refuse("_continue_sepa_transfer")
    _continue_sepa_debit = _refuse("_continue_sepa_debit")
    _send_pay_with_possible_retry = _refuse("_send_pay_with_possible_retry")
    approve_vop_response = _refuse("approve_vop_response")
    pause_dialog = _refuse("pause_dialog")
    resume_dialog = _refuse("resume_dialog")
    get_tan_media = _refuse("get_tan_media")
    set_tan_medium = _refuse("set_tan_medium")


ClientFactory = Callable[..., Any]


def make_client(
    *,
    bank_code: str,
    login_name: str,
    pin: str,
    url: str,
    product_id: str,
    client_data: bytes | None,
) -> _ReadOnlyClient:
    """The read-only client. `client_data` is a verified state blob (or None)."""
    pin_fints_logging()
    return _ReadOnlyClient(
        bank_code, login_name, pin, url, product_id=product_id, from_data=client_data
    )


# --- The facade ----------------------------------------------------------------------------


@dataclass
class Approval:
    """A pending decoupled (pushTAN) approval. Opaque to the job: it is polled, never read."""

    _response: NeedTANResponse | Any
    _finish: Callable[[Any], Any]


@dataclass
class Confirmed:
    """The approval arrived; `value` is what the approved request returned."""

    value: Any


def _is_decoupled(parameter: Any) -> bool:
    """A TwoStepParameters7 entry with a decoupled poll limit is a decoupled method (pushTAN
    and the like); chipTAN and smsTAN have none (D118)."""
    return getattr(parameter, "decoupled_max_poll_number", None) is not None


def _amount(balance: Any) -> Decimal:
    return Decimal(balance.amount.amount).quantize(Decimal("0.01"))


class FintsSession:
    """One FinTS dialog for one sync run. See the module docstring for the surface."""

    def __init__(
        self,
        client: Any,
        *,
        tan_mechanism: str | None,
        restored: bool,
        log_fields: Mapping[str, Any] | None = None,
    ):
        pin_fints_logging()
        self._client = client
        self._tan_mechanism = tan_mechanism
        self._restored = restored
        self._entered = False
        self._log_fields = dict(log_fields or {})

    @property
    def tan_mechanism(self) -> str | None:
        return self._tan_mechanism

    def _traced(self, step: str) -> AbstractContextManager[None]:
        """The trace's tag for the exchanges of `step` (D148), with the caller's fields."""
        return trace_context(**self._log_fields, step=step)

    @contextmanager
    def _mapped(self, *, codes_refuse: bool = True) -> Iterator[None]:
        """Every python-fints failure as a BankError, raised from None (no chained text).

        A 9xxx code the bank sent inside the block refuses it even when python-fints raised
        nothing, except with codes_refuse=False: the bootstrap fetch_tan_mechanisms only, a
        dialog python-fints runs in bootstrap mode where it tolerates a 9xxx answer (an
        exception there still maps as everywhere, so a PIN error stays bank.refused)."""
        seen = len(self._client.bank_answers)
        try:
            yield
        except (BankError, WriteRefused):
            raise
        except Exception as exc:  # the type decides the code; the exception's text is dropped
            answers = self._client.bank_answers[seen:]
            error = self._error(exc, answers)
            if error.response_code is None:  # not the bank's code: what python-fints raised
                error.exc_chain = fints_trace.exc_chain(exc)  # (D148)
                if error.code == E.BANK_REFUSED:  # its warnings may still say why
                    error.answers = tuple(a for a in answers if _is_warning_or_error(a))
            raise error from None
        answers = self._client.bank_answers[seen:]
        if codes_refuse and _refusal_in(answers):
            raise _refused(answers) from None

    def _error(self, exc: Exception, answers: list[BankAnswer]) -> BankError:
        """The bank's answers decide when one of them refuses (a 9xxx code, or 3938 that
        python-fints raises on); else the exception's type."""
        if deciding_code(answers) is not None:
            return _refused(answers)
        if isinstance(exc, FinTSDialogInitError) and isinstance(
            exc.__cause__, FinTSConnectionError | requests.RequestException | OSError
        ):
            return BankError(E.BANK_UNREACHABLE)
        if isinstance(
            exc, FinTSConnectionError | FinTSNoResponseError | requests.RequestException | OSError
        ):
            return BankError(E.BANK_UNREACHABLE)
        if isinstance(exc, FinTSUnsupportedOperation):
            return BankError(E.BANK_UNSUPPORTED_FORMAT)
        if isinstance(
            exc, FinTSClientPINError | FinTSClientTemporaryAuthError | FinTSSCARequiredError
        ):
            return BankError(E.BANK_REFUSED)
        if isinstance(exc, FinTSError):
            return BankError(E.BANK_REFUSED)
        return BankError(E.BANK_FAILED)

    def _seeded(self) -> bool:
        return self._restored and self._tan_mechanism is not None

    def _choose_mechanism(self) -> None:
        c = self._client
        if self._seeded():
            # The restored state has the bank's parameters but not the allowed list (private
            # data), so the init's HKTAN would find no mechanism: seed it with the stored one.
            c.allowed_security_functions = [self._tan_mechanism]
            if not _is_decoupled(c.get_tan_mechanisms().get(self._tan_mechanism)):
                raise BankError(E.BANK_TAN_NOT_DECOUPLED)
            c.set_tan_mechanism(self._tan_mechanism)
            return
        mechanisms = c.get_tan_mechanisms()  # filled by open()'s bootstrap
        decoupled = [sf for sf, p in mechanisms.items() if _is_decoupled(p)]
        if not decoupled:
            raise BankError(E.BANK_TAN_NOT_DECOUPLED)
        chosen = self._tan_mechanism if self._tan_mechanism in decoupled else decoupled[0]
        c.set_tan_mechanism(chosen)
        self._tan_mechanism = chosen

    def open(self) -> Approval | None:
        """Choose the decoupled mechanism and open the standing dialog. Returns the approval
        the bank asks for at login, or None when it serves without one."""
        with self._traced("open"):
            fints_trace.log_session(
                self._client, restored=self._restored, tan_mechanism=self._tan_mechanism
            )
        if not self._seeded():
            with self._traced("bootstrap"), self._mapped(codes_refuse=False):
                # a dialog without TAN, in python-fints' bootstrap mode: the system id and the
                # allowed list; a code it tolerates here does not refuse the run (PR #160)
                self._client.fetch_tan_mechanisms()
        # the codes counted from here: the bootstrap's are behind us
        with self._traced("open"), self._mapped():
            self._choose_mechanism()
            self._client.init_tan_response = None
            self._client.__enter__()
            self._entered = True
            response = self._client.init_tan_response
        if response is None:
            return None
        if not getattr(response, "decoupled", False):
            raise BankError(E.BANK_TAN_NOT_DECOUPLED)
        return Approval(response, lambda value: None)

    def poll(self, approval: Approval) -> Approval | Confirmed:
        """One status query of a decoupled approval (HKTAN process S): the approval again
        while the bank answers 3956 (not yet), else Confirmed with the request's result."""
        with self._traced("poll"), self._mapped():
            result = self._client.send_tan(approval._response, "")
        if isinstance(result, NeedTANResponse):
            return Approval(result, approval._finish)
        return Confirmed(approval._finish(result))

    def _read(self, call: Callable[[], Any], finish: Callable[[Any], Any], step: str) -> Any:
        with self._traced(step), self._mapped():
            result = call()
        if isinstance(result, NeedTANResponse):
            if not result.decoupled:
                raise BankError(E.BANK_TAN_NOT_DECOUPLED)
            return Approval(result, finish)
        return finish(result)

    def accounts(self) -> list[SEPAAccount] | Approval:
        return self._read(self._client.get_sepa_accounts, list, "accounts")

    def bank_parameters(self) -> BankParameters | None:
        """The bank's public parameters for the record (phase 6 D173 (7)); None when they
        could not be read. Never raises: the record is a fact for §14.1, never the run's
        failure."""
        try:
            return self._client.bank_parameters()
        except Exception:
            return None

    def camt_formats(self) -> list[str]:
        """The bank's camt messages the parser reads; bank.unsupported_format if none."""
        readable = [m for m in self._client.supported_camt_formats() if _READABLE_CAMT.search(m)]
        if not readable:
            raise BankError(E.BANK_UNSUPPORTED_FORMAT)
        return readable

    def transactions_xml(self, account: SEPAAccount, start: date, end: date) -> Any:
        """The booked camt streams of [start, end]; pending streams are discarded (D115)."""
        formats = self.camt_formats()

        def booked(result: Any) -> list[bytes]:
            streams = result[0] if result else []
            return [s if isinstance(s, bytes) else bytes(s) for s in streams]

        return self._read(
            functools.partial(
                self._client.get_transactions_xml,
                account,
                start,
                end,
                supported_camt_messages=formats,
            ),
            booked,
            "transactions",
        )

    def balance(self, account: SEPAAccount) -> Decimal | None | Approval:
        """HKSAL's booked balance, or None when the bank sent none."""
        return self._read(
            functools.partial(self._client.get_balance, account),
            lambda balance: None if balance is None else _amount(balance),
            "balance",
        )

    # --- Kontoauszug PDF (D154): never raises a BankError; the job reads every answer through
    # rules.bank_statements.read_answer, never through deciding_code. -----------------------

    def statements_offered(self) -> bool:
        """The bank's parameters offer HKEKP v1 by number (D154 (1))."""
        try:
            return bool(self._client.statement_pdf_offered())
        except Exception:  # parameters python-fints could not read: not offered
            return False

    def statement_pdf(
        self, account: SEPAAccount, number: int, year: int, touchdown: str | None = None
    ) -> StatementAnswer:
        """One HKEKP for statement `number`/`year` (with the Aufsetzpunkt of a 3040 when it
        continues one). Only a write the facade refuses (WriteRefused, a bug) raises; any
        other failure comes back as `failure` with the bank's answers kept so far."""
        seen = len(self._client.bank_answers)
        parts: list[bytes] = []
        following: str | None = None
        failure: str | None = None
        with self._traced("statements"):
            try:
                parts, following = self._client.get_statement_pdf(account, number, year, touchdown)
            except WriteRefused:
                raise
            except Exception as exc:  # the text is dropped: class names only (D148)
                failure = fints_trace.exc_chain(exc)
        answers = tuple(self._client.bank_answers[seen:])
        return StatementAnswer(answers, tuple(parts), following, failure)

    def reopen(self) -> StatementAnswer:
        """A new dialog after the bank ended the last one (D154 (4)). `failure` is set when it
        did not open, or when its opening asked for a pushTAN (then it is closed at once and
        never polled: nobody is asked to approve a statement fetch)."""
        seen = len(self._client.bank_answers)
        failure: str | None = None
        with self._traced("statements"):
            try:
                demand = self._client.restart_dialog()
            except Exception as exc:
                demand, failure = None, fints_trace.exc_chain(exc)
        self._entered = True  # whatever happened, close() ends what may be open
        if demand is not None:
            failure = "approval_required"
        answers = tuple(self._client.bank_answers[seen:])
        if failure is not None:
            self.close()
        return StatementAnswer(answers, failure=failure)

    def close(self) -> None:
        """End the dialog (HKEND). Never raises: a dialog the bank already closed is fine."""
        if not self._entered:
            return
        self._entered = False
        # The dialog is gone either way; nothing of a failure here is kept.
        with self._traced("close"), suppress(Exception):
            self._client.__exit__(None, None, None)

    def state(self) -> bytes:
        """The client state to store MAC'd (D119): no PIN, no login, no account list."""
        with self._mapped():
            return self._client.deconstruct(including_private=False)


def open_session(
    *,
    bank_code: str,
    login_name: str,
    pin: str,
    url: str,
    product_id: str,
    client_data: bytes | None,
    tan_mechanism: str | None,
    client_factory: ClientFactory = make_client,
    log_fields: Mapping[str, Any] | None = None,
) -> FintsSession:
    """A session over a new read-only client. Building the client sends nothing.
    `log_fields` (the job's sync id) tag every trace record of the session (D148)."""
    try:
        client = client_factory(
            bank_code=bank_code,
            login_name=login_name,
            pin=pin,
            url=url,
            product_id=product_id,
            client_data=client_data,
        )
    except Exception:  # a state blob python-fints cannot read: start without it
        if client_data is None:
            raise BankError(E.BANK_FAILED) from None
        raise BankError(E.BANK_STATE_REJECTED) from None
    return FintsSession(
        client,
        tan_mechanism=tan_mechanism,
        restored=client_data is not None,
        log_fields=log_fields,
    )
