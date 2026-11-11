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

Errors: every failure leaves this module as `BankError(code, response_code)` with a fixed
message. A python-fints exception's text is never kept (the generic Exception it raises for
a 9xxx answer to HKTAN holds the whole response); the code comes from the exception's type
and from the 9xxx answer codes `_ReadOnlyClient._process_response` records as the bank
sends them.

Only checked against the real bank in step 1 (§16 Q-O34 to Q-O36): after a restored state
`allowed_security_functions` is empty (python-fints keeps it only with private data), so
`open()` seeds it with the stored mechanism for the init's HKTAN; the TAN medium is left
to python-fints (an empty name when the bank requires one, as its own Sparkasse workaround
does; HKTAB is never sent).
"""

import functools
import logging
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
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
from fints.models import SEPAAccount

from hausbuch.rules import errors as E
from hausbuch.rules.bank_sync import refused_code

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
    }
)

# camt messages phase 2's parser reads (D33): camt.052 and camt.053, versions 02 and 08.
_READABLE_CAMT = re.compile(r"camt\.05[23]\.001\.0[28]")
_ANSWER_CODE = re.compile(r"[0-9]{4}")


class BankError(Exception):
    """A sync failure as a bank.* code, plus the four digits of a 9xxx answer for
    bank.refused. The message is the code: never the bank's or the library's text."""

    def __init__(self, code: str, response_code: str | None = None):
        super().__init__(code)
        self.code = code
        self.response_code = response_code


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
    def __init__(self, timeout_s: float):
        super().__init__()
        self._timeout_s = timeout_s

    def request(self, *args: Any, **kwargs: Any) -> requests.Response:  # type: ignore[override]
        kwargs.setdefault("timeout", self._timeout_s)
        return super().request(*args, **kwargs)


class _GuardedConnection(FinTSHTTPSConnection):
    """python-fints' HTTPS connection with a timeout and the segment whitelist."""

    def __init__(self, url: str, timeout_s: float = HTTP_TIMEOUT_S):
        super().__init__(url)
        self.session = _TimeoutSession(timeout_s)

    def send(self, msg: Any) -> Any:
        refused = sorted({t for t in _segment_types(msg.segments) if t not in ALLOWED_SEGMENTS})
        if refused:
            raise WriteRefused(f"segment not allowed: {', '.join(refused)}")
        return super().send(msg)


def _refuse(name: str) -> Callable[..., Any]:
    def refused(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise WriteRefused(f"{name} is not available in Hausbuch")

    refused.__name__ = name
    return refused


class _ReadOnlyClient(FinTS3PinTanClient):
    """FinTS3PinTanClient without its writes, with the guarded connection, recording every
    9xxx answer code the bank sends (the codes only)."""

    def __init__(self, *args: Any, timeout_s: float = HTTP_TIMEOUT_S, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.connection = _GuardedConnection(self.connection.url, timeout_s)
        self.refused_codes: list[str] = []

    def _process_response(self, dialog: Any, segment: Any, response: Any) -> None:
        code = refused_code(getattr(response, "code", None))
        if code is not None:
            self.refused_codes.append(code)
        super()._process_response(dialog, segment, response)

    def supported_camt_formats(self) -> list[str]:
        """The camt messages HICAZS (the bank's parameters) lists, as the bank spells them."""
        hicazs = self.bpd.find_segment_first("HICAZS")
        return [] if not hicazs else [str(m) for m in hicazs.parameter.supported_camt_formats]

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

    def __init__(self, client: Any, *, tan_mechanism: str | None, restored: bool):
        pin_fints_logging()
        self._client = client
        self._tan_mechanism = tan_mechanism
        self._restored = restored
        self._entered = False

    @property
    def tan_mechanism(self) -> str | None:
        return self._tan_mechanism

    @contextmanager
    def _mapped(self, *, codes_refuse: bool = True) -> Iterator[None]:
        """Every python-fints failure as a BankError, raised from None (no chained text).

        A 9xxx code the bank sent inside the block refuses it even when python-fints raised
        nothing, except with codes_refuse=False: the bootstrap fetch_tan_mechanisms only, a
        dialog python-fints runs in bootstrap mode where it tolerates a 9xxx answer (an
        exception there still maps as everywhere, so a PIN error stays bank.refused)."""
        seen = len(self._client.refused_codes)
        try:
            yield
        except (BankError, WriteRefused):
            raise
        except Exception as exc:  # the type decides the code; the text is dropped
            raise self._error(exc, seen) from None
        if codes_refuse and len(self._client.refused_codes) > seen:
            raise BankError(E.BANK_REFUSED, self._client.refused_codes[seen]) from None

    def _error(self, exc: Exception, seen: int) -> BankError:
        codes = self._client.refused_codes[seen:]
        if codes:
            return BankError(E.BANK_REFUSED, codes[0])
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
        if not self._seeded():
            with self._mapped(codes_refuse=False):
                # a dialog without TAN, in python-fints' bootstrap mode: the system id and the
                # allowed list; a code it tolerates here does not refuse the run (PR #160)
                self._client.fetch_tan_mechanisms()
        with self._mapped():  # the codes counted from here: the bootstrap's are behind us
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
        with self._mapped():
            result = self._client.send_tan(approval._response, "")
        if isinstance(result, NeedTANResponse):
            return Approval(result, approval._finish)
        return Confirmed(approval._finish(result))

    def _read(self, call: Callable[[], Any], finish: Callable[[Any], Any]) -> Any:
        with self._mapped():
            result = call()
        if isinstance(result, NeedTANResponse):
            if not result.decoupled:
                raise BankError(E.BANK_TAN_NOT_DECOUPLED)
            return Approval(result, finish)
        return finish(result)

    def accounts(self) -> list[SEPAAccount] | Approval:
        return self._read(self._client.get_sepa_accounts, list)

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
        )

    def balance(self, account: SEPAAccount) -> Decimal | None | Approval:
        """HKSAL's booked balance, or None when the bank sent none."""
        return self._read(
            functools.partial(self._client.get_balance, account),
            lambda balance: None if balance is None else _amount(balance),
        )

    def close(self) -> None:
        """End the dialog (HKEND). Never raises: a dialog the bank already closed is fine."""
        if not self._entered:
            return
        self._entered = False
        # The dialog is gone either way; nothing of a failure here is kept.
        with suppress(Exception):
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
) -> FintsSession:
    """A session over a new read-only client. Building the client sends nothing."""
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
    return FintsSession(client, tan_mechanism=tan_mechanism, restored=client_data is not None)
