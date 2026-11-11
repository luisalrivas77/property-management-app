"""The FinTS exchange trace (docs/phases/phase-5.md D148): one INFO record per message the
app sends to the bank, so a refusal can be read from the container log.

Built as a whitelist. Every segment, sent or received, is named by its type, number and
version (`HKVVB:4v3`); a value is printed only for the segments and fields listed in
`_SENT_SETTINGS` and `_RECEIVED_FACTS`, and only the non-secret ones:

- sent: HKIDN's bank code (a public BLZ), whether the system id is assigned ("0" or "set",
  never the value) and its status; HKVVB's BPD and UPD versions, language, product version and
  the product id's length ("set (25 chars)" or "missing", never the value); HKSYN's mode;
  HKTAN's version, process, segment type and whether a TAN medium name is absent, empty or
  set (never the name); HNSHK's security function (its key name holds the login);
- received: every answer of HIRMG (the whole message) and HIRMS (the customer segment its
  header refers to, resolved by number), its text through `rules.bank_sync.redact_bank_text`
  with the login, customer id, PIN, product id, the system id and the dialog id hidden (the
  last two also taken from the message itself: HISYN and HNHBK of the sent and received
  message, since python-fints stores them only after the trace has run); its parameters
  printed only for 3920 (the allowed security functions), for every other code counted
  (`[2 parameters]`: 3072 carries the new login, 3040 a touchdown point); HIBPA's BPD
  version; HIUPA's UPD version (not its user id); HITANS's security functions with their
  names and whether each is decoupled; HICAZS's camt formats.

Never, at any level: the PIN, the login, the customer id, the product id, the system id, a
dialog id, an IBAN or account number, a balance, camt content, a TAN challenge. Data segments
(HICAZ, HISAL, HISPA, HITAN, HISYN, HIUPD) and the envelope (HNHBK, HNVSK, HNVSD, HNSHA)
print their header only. A transport failure prints the exception chain as class names only,
never an exception's text.

The trace's context (the sync id, the session step) is a ContextVar that FintsSession sets
around each call, so the connection can tag its record without knowing the job.
"""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from urllib.parse import urlsplit

from fints import version as FINTS_VERSION

from hausbuch.rules.bank_sync import redact_bank_text

logger = logging.getLogger(__name__)

SYSTEM_ID_UNASSIGNED = "0"
_CHAIN_LIMIT = 8

_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar("fints_trace_context", default=None)

Hidden = Callable[[], tuple[str | None, ...]]


@contextmanager
def trace_context(**fields: Any) -> Iterator[None]:
    """Add `fields` (e.g. sync_id, step) to every trace record written inside the block."""
    token = _CONTEXT.set((_CONTEXT.get() or {}) | fields)
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def context() -> dict[str, Any]:
    return dict(_CONTEXT.get() or {})


# --- Segments --------------------------------------------------------------------------------


def walk(segments: Any) -> Iterator[Any]:
    """Every segment of a message, the ones inside the encryption envelope (HNVSD) too."""
    for segment in segments:
        yield segment
        if str(segment.header.type) == "HNVSD":
            yield from walk(segment.data.segments)


def label(segment: Any) -> str:
    header = segment.header
    return f"{header.type}:{header.number}v{header.version}"


def _text(value: Any) -> str | None:
    """A field value as plain text (an enum as its code), None when unset."""
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _system_id(value: Any) -> str:
    text = _text(value)
    return SYSTEM_ID_UNASSIGNED if text in (None, SYSTEM_ID_UNASSIGNED) else "set"


def _presence(value: Any) -> str:
    """A secret's presence: "missing" or "set (N chars)", never the value."""
    text = _text(value)
    return f"set ({len(text)} chars)" if text else "missing"


def _tan_medium(value: Any) -> str:
    text = _text(value)
    return "absent" if text is None else ("empty" if text == "" else "set")


def _hkidn(seg: Any) -> dict[str, Any]:
    bank = getattr(seg, "bank_identifier", None)
    return {
        "bank_code": _text(getattr(bank, "bank_code", None)),
        "system_id": _system_id(seg.system_id),
        "system_id_status": _text(seg.system_id_status),
    }


def _hkvvb(seg: Any) -> dict[str, Any]:
    return {
        "bpd_version": _text(seg.bpd_version),
        "upd_version": _text(seg.upd_version),
        "language": _text(seg.language),
        "product_id": _presence(seg.product_name),
        "product_version": _text(seg.product_version),
    }


def _hktan(seg: Any) -> dict[str, Any]:
    return {
        "version": _text(seg.header.version),
        "tan_process": _text(getattr(seg, "tan_process", None)),
        "segment_type": _text(getattr(seg, "segment_type", None)),
        "tan_medium": _tan_medium(getattr(seg, "tan_medium_name", None)),
    }


# The sent segments whose non-secret settings are printed; every other segment (HNSHA with
# the PIN, HNVSK and HNHBK with the login, system id and dialog id, HKEND, the reads) prints
# its header only.
_SENT_SETTINGS: dict[str, Callable[[Any], dict[str, Any]]] = {
    "HKIDN": _hkidn,
    "HKVVB": _hkvvb,
    "HKSYN": lambda seg: {"mode": _text(seg.synchronization_mode)},
    "HKTAN": _hktan,
    "HNSHK": lambda seg: {"security_function": _text(seg.security_function)},
}


def segment_map(segments: Any) -> dict[int, str]:
    """Our segments by number (`HKVVB:4`), for resolving the bank's HIRMS references."""
    return {int(s.header.number): f"{s.header.type}:{s.header.number}" for s in walk(segments)}


def describe_sent(segments: Any) -> dict[str, Any]:
    sent, settings = [], {}
    for segment in walk(segments):
        sent.append(label(segment))
        describe = _SENT_SETTINGS.get(str(segment.header.type))
        if describe is not None:
            try:
                settings[label(segment)] = describe(segment)
            except Exception as exc:  # a shape python-fints changed: the header only
                settings[label(segment)] = {"trace_error": type(exc).__name__}
    return {"sent": sent, "settings": settings}


# Answer codes whose parameters are printed: 3920's are the allowed security functions
# (three-digit codes). Every other code's parameters are counted only: 3072 carries the new
# login and customer id, 3040 a touchdown point that may be an account number (D148 review).
_PARAMETER_CODES = frozenset({"3920"})


def _answers(segment: Any, sent: dict[int, str], hidden: tuple[str | None, ...]) -> list[str]:
    kind = str(segment.header.type)
    if kind == "HIRMG":
        target = "message"
    else:
        reference = segment.header.reference
        target = sent.get(int(reference), f"segment {reference}") if reference else "unknown"
    out = []
    for response in segment.responses or ():
        code = _text(response.code)
        line = f"{code} → {target}: {redact_bank_text(response.text, *hidden)}"
        parameters = list(response.parameters or ())
        if parameters and code in _PARAMETER_CODES:
            line += f" [{', '.join(redact_bank_text(p, *hidden) for p in parameters)}]"
        elif parameters:  # 3072 carries the new login, 3040 a touchdown point: count only
            line += f" [{len(parameters)} parameter{'s' if len(parameters) > 1 else ''}]"
        out.append(line)
    return out


def _secrets_in(segments: Any) -> tuple[str, ...]:
    """The system id (HISYN) and dialog id (HNHBK) a message carries, other than "0": python-fints
    stores them only after the trace has run, and the dialog id never on the client."""
    found = []
    for segment in walk(segments):
        kind = str(segment.header.type)
        value = (
            getattr(segment, "system_id", None)
            if kind == "HISYN"
            else getattr(segment, "dialog_id", None)
            if kind == "HNHBK"
            else None
        )
        text = _text(value)
        if text and text != SYSTEM_ID_UNASSIGNED:
            found.append(text)
    return tuple(found)


def _tan_methods(segment: Any, hidden: tuple[str | None, ...]) -> list[str]:
    out = []
    for parameter in segment.parameter.twostep_parameters or ():
        decoupled = getattr(parameter, "decoupled_max_poll_number", None) is not None
        name = redact_bank_text(getattr(parameter, "name", None), *hidden)
        kind = "decoupled" if decoupled else "not decoupled"
        function = _text(parameter.security_function)
        out.append(f"{function} {name} ({kind}, HITANS{segment.header.version})")
    return out


def _received_fact(segment: Any, facts: dict[str, Any], hidden: tuple[str | None, ...]) -> None:
    kind = str(segment.header.type)
    if kind == "HIBPA":
        facts["bpd_version"] = _text(segment.bpd_version)
    elif kind == "HIUPA":
        facts["upd_version"] = _text(segment.upd_version)
    elif kind == "HITANS":
        facts.setdefault("tan_methods", []).extend(_tan_methods(segment, hidden))
    elif kind == "HICAZS":
        formats = segment.parameter.supported_camt_formats or ()
        facts.setdefault("camt_formats", []).extend(str(f) for f in formats)


_RECEIVED_FACTS = frozenset({"HIBPA", "HIUPA", "HITANS", "HICAZS"})


def describe_received(
    segments: Any, sent: dict[int, str], hidden: tuple[str | None, ...]
) -> dict[str, Any]:
    received, answers, facts = [], [], {}
    for segment in walk(segments):
        received.append(label(segment))
        kind = str(segment.header.type)
        try:
            if kind in ("HIRMG", "HIRMS"):
                answers.extend(_answers(segment, sent, hidden))
            elif kind in _RECEIVED_FACTS:
                _received_fact(segment, facts, hidden)
        except Exception as exc:  # never fail a run over its trace
            facts.setdefault("trace_errors", []).append(f"{label(segment)} {type(exc).__name__}")
    return {"received": received, "answers": answers} | ({"facts": facts} if facts else {})


# --- Exceptions ------------------------------------------------------------------------------


def exc_chain(exc: BaseException | None) -> str:
    """The exception and what caused it, as class names only (`FinTSConnectionError ←
    ConnectionError ← MaxRetryError ← NameResolutionError`): __cause__, else __context__, else
    an exception in requests' args[0] or urllib3's `reason`. Never an exception's text."""
    names: list[str] = []
    seen: set[int] = set()
    current = exc
    while current is not None and id(current) not in seen and len(names) < _CHAIN_LIMIT:
        seen.add(id(current))
        names.append(type(current).__name__)
        following = current.__cause__ or current.__context__
        if following is None:
            reason = getattr(current, "reason", None)
            first = current.args[0] if current.args else None
            if isinstance(reason, BaseException):
                following = reason
            elif isinstance(first, BaseException):
                following = first
        current = following
    return " ← ".join(names)


# --- The records -----------------------------------------------------------------------------


def log_exchange(
    *,
    exchange: int,
    segments: Any,
    http: dict[str, Any],
    response: Any = None,
    error: BaseException | None = None,
    hidden: Hidden,
) -> None:
    """The one record of an exchange. Never raises: a trace that fails says so by class."""
    fields: dict[str, Any] = {"event": "fints_exchange"} | context() | {"exchange": exchange}
    try:
        fields |= describe_sent(segments)
        fields |= {k: v for k, v in http.items() if v is not None}
        if response is not None:
            secrets = (*hidden(), *_secrets_in(segments), *_secrets_in(response.segments))
            fields |= describe_received(response.segments, segment_map(segments), secrets)
        if error is not None:
            fields["outcome"] = "failed"
            fields["exc_chain"] = exc_chain(error)
        else:
            fields["outcome"] = "answered"
    except Exception as exc:
        fields["trace_error"] = type(exc).__name__
    logger.info("fints exchange", extra={"fields": fields})


def client_facts(client: Any, *, restored: bool, tan_mechanism: str | None) -> dict[str, Any]:
    """The session's starting facts, without a secret (D148)."""
    connection = getattr(client, "connection", None)
    url = getattr(connection, "url", None)
    return {
        "event": "fints_session",
        "mode": "restored" if restored else "bootstrap",
        "security_function": tan_mechanism
        or _text(getattr(client, "selected_security_function", None)),
        "system_id": _system_id(getattr(client, "system_id", None)),
        "bpd_version": _text(getattr(client, "bpd_version", None)),
        "upd_version": _text(getattr(client, "upd_version", None)),
        "python_fints": FINTS_VERSION,
        "product_id": _presence(getattr(client, "product_name", None)),
        "product_version": _text(getattr(client, "product_version", None)),
        "host": urlsplit(url).hostname if isinstance(url, str) else None,
    }


def log_session(client: Any, *, restored: bool, tan_mechanism: str | None) -> None:
    fields: dict[str, Any] = context()
    try:
        fields |= client_facts(client, restored=restored, tan_mechanism=tan_mechanism)
    except Exception as exc:
        fields |= {"event": "fints_session", "trace_error": type(exc).__name__}
    logger.info("fints session", extra={"fields": fields})
