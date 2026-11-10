"""Email-in: whether a mail of the household's label is taken in, and the parsing of what the
IMAP server says about it (docs/phases/phase-5.md D121, §5.3, §5.5, §12.1).

Pure functions: no I/O, no network, no database. Fixture: tests/fixtures/phase5/inbound_mail.yaml.

- `decide(facts, policy)`: the decision rule. It reads only the header fields and the
  BODYSTRUCTURE, so a rejected mail's parts are never fetched (D121).
- `parse_fetch_response(data)`: imaplib's FETCH answer (bytes and (bytes, literal) tuples)
  as one dict per message, keyed by the data item names (UID, INTERNALDATE, BODYSTRUCTURE,
  BODY[...]).
- `body_parts(bodystructure)`: the BODYSTRUCTURE's leaves and message/rfc822 parts, numbered
  as RFC 3501 §6.4.5 numbers them, each with its transfer encoding.
- `mail_facts(header, parts)`: the InboundMailFacts and the Message-ID from the fetched
  header fields. Only the topmost Authentication-Results header is read (Gmail prepends its
  own at delivery, so a header written by the sender sits below it; a forged one with
  Gmail's authserv-id therefore never counts while Gmail adds its own).
- `parse_authentication_results(value)`: one header (RFC 8601) as AuthResult rows.
- `decode_part`, `message_id_hash`, `imap_since`, `parse_internaldate`: the small rest.

Nothing here logs. A ValueError from a parser carries no header text.
"""

import base64
import binascii
import hashlib
import quopri
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.parser import BytesHeaderParser
from email.policy import compat32
from email.utils import decode_rfc2231, getaddresses
from typing import Any
from urllib.parse import unquote_to_bytes

from hausbuch.schemas.inbound_mail import (
    AuthResult,
    InboundMailFacts,
    InboundPolicy,
    MailDecision,
    MailPart,
)

from . import errors as E

# The header fields the listing fetches (BODY.PEEK[HEADER.FIELDS (...)]): exactly what
# `decide` needs, nothing of the subject or the body.
HEADER_FIELDS = ("MESSAGE-ID", "FROM", "TO", "CC", "DELIVERED-TO", "AUTHENTICATION-RESULTS")
_AUTH_METHODS = frozenset({"dkim", "spf", "dmarc"})
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# --- The decision (fixture inbound_mail.yaml) -------------------------------------------


def _path(index: str) -> tuple[int, ...]:
    return tuple(int(n) for n in index.split("."))


def _rfc822_depth(part: MailPart, parts: list[MailPart]) -> int:
    """How many forwarded messages (message/rfc822 parts) the part sits inside."""
    path = _path(part.index)
    depth = 0
    for other in parts:
        if not other.nested_rfc822:
            continue
        prefix = _path(other.index)
        if len(prefix) < len(path) and path[: len(prefix)] == prefix:
            depth += 1
    return depth


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].casefold()


def decide(facts: InboundMailFacts, policy: InboundPolicy) -> MailDecision:
    """Accept or reject one mail (§5.3; the order of checks is the fixture header's, the
    first failure wins): Message-ID present → the token address among the recipients → the
    sender a member → Gmail's own authentication passes for the From domain → at least one
    attachment of an accepted type → the limits. An accepted mail lists the part indexes to
    fetch, in BODYSTRUCTURE order, and counts the attachments of other types in `skipped`."""

    def reject(code: str) -> MailDecision:
        return MailDecision(accept=False, code=code)

    if not facts.message_id_present:
        return reject(E.EMAIL_NO_MESSAGE_ID)
    if policy.token_address.casefold() not in {r.casefold() for r in facts.recipients}:
        return reject(E.EMAIL_RECIPIENT_MISMATCH)
    sender = facts.from_address.casefold()
    if sender not in {s.casefold() for s in policy.allowed_senders}:
        return reject(E.EMAIL_SENDER_NOT_ALLOWED)
    trusted = [a for a in facts.auth_results if a.authserv_id == policy.authserv_id]
    if not trusted:
        return reject(E.EMAIL_AUTH_MISSING)
    from_domain = _domain(sender)
    if not any(
        a.method in ("dkim", "spf")
        and a.result == "pass"
        and (a.domain or "").casefold() == from_domain
        for a in trusted
    ):
        return reject(E.EMAIL_AUTH_FAILED)
    fetch: list[MailPart] = []
    skipped = 0
    for part in sorted(facts.parts, key=lambda p: _path(p.index)):
        if part.filename is None or part.nested_rfc822 or _rfc822_depth(part, facts.parts) > 1:
            continue
        if part.media_type in policy.media_types:
            fetch.append(part)
        else:
            skipped += 1
    if not fetch:
        return reject(E.EMAIL_NO_ATTACHMENT)
    if len(fetch) > policy.max_attachments or any(p.size_bytes > policy.max_bytes for p in fetch):
        return reject(E.EMAIL_TOO_LARGE)
    return MailDecision(
        accept=True,
        code=E.EMAIL_ACCEPTED,
        attachments=[p.index for p in fetch],
        skipped=skipped,
    )


# --- Authentication-Results (RFC 8601) ---------------------------------------------------


def _strip_comments(value: str) -> str:
    """The header without its (possibly nested) comments; quoted strings are kept."""
    out: list[str] = []
    depth = 0
    quoted = escaped = False
    for ch in value:
        if escaped:
            escaped = False
            if depth == 0:
                out.append(ch)
            continue
        if ch == "\\":
            escaped = True
            if depth == 0:
                out.append(ch)
            continue
        if quoted:
            if ch == '"':
                quoted = False
            out.append(ch)
            continue
        if ch == '"' and depth == 0:
            quoted = True
            out.append(ch)
        elif ch == "(":
            depth += 1
            out.append(" ")
        elif ch == ")" and depth > 0:
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def _split_unquoted(value: str, sep: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quoted = escaped = False
    for ch in value:
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == '"':
            quoted = not quoted
        elif ch == sep and not quoted:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    parts.append("".join(current))
    return parts


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return re.sub(r"\\(.)", r"\1", value[1:-1])
    return value


def _after_at(value: str) -> str:
    return value.rsplit("@", 1)[-1]


def parse_authentication_results(value: str) -> list[AuthResult]:
    """One Authentication-Results header as AuthResult rows, one per dkim, spf or dmarc
    result (other methods, such as arc, are left out). The domain is dkim's header.d (else
    header.i's domain), spf's smtp.mailfrom domain, dmarc's header.from. A header without an
    authserv-id gives no row."""
    text = " ".join(_strip_comments(value).split())
    segments = _split_unquoted(text, ";")
    head = segments[0].split()
    if not head:
        return []
    authserv_id = head[0].casefold()
    results: list[AuthResult] = []
    for segment in segments[1:]:
        tokens = segment.split()
        if not tokens or "=" not in tokens[0]:
            continue  # "none", or an empty segment
        method, _, result = tokens[0].partition("=")
        method = method.split("/", 1)[0].casefold()
        if method not in _AUTH_METHODS or not result:
            continue
        props: dict[str, str] = {}
        for token in tokens[1:]:
            key, sep, prop = token.partition("=")
            if sep and key.casefold() not in props:
                props[key.casefold()] = _unquote(prop)
        if method == "dkim":
            domain = props.get("header.d") or (
                _after_at(props["header.i"]) if "header.i" in props else None
            )
        elif method == "spf":
            domain = _after_at(props["smtp.mailfrom"]) if "smtp.mailfrom" in props else None
        else:
            domain = props.get("header.from")
        results.append(
            AuthResult(
                authserv_id=authserv_id,
                method=method,  # type: ignore[arg-type]  # one of _AUTH_METHODS
                result=_unquote(result).casefold(),
                domain=domain or None,
            )
        )
    return results


# --- The IMAP FETCH answer ---------------------------------------------------------------


@dataclass(frozen=True)
class Literal:
    """An IMAP literal ({n} followed by n octets): header bytes, a part's content."""

    data: bytes


_ATOM_END = frozenset(b" ()\r\n")


class _Tokens:
    """A tiny reader of IMAP response syntax: atoms (with [...] sections kept whole),
    quoted strings, literals, NIL and parenthesised lists."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def _skip_space(self) -> None:
        while self.pos < len(self.data) and self.data[self.pos] in b" \r\n":
            self.pos += 1

    def at_end(self) -> bool:
        self._skip_space()
        return self.pos >= len(self.data)

    def value(self) -> Any:
        self._skip_space()
        if self.pos >= len(self.data):
            raise ValueError("the IMAP response ended early")
        ch = self.data[self.pos]
        if ch == ord("("):
            self.pos += 1
            items: list[Any] = []
            while True:
                self._skip_space()
                if self.pos >= len(self.data):
                    raise ValueError("an IMAP list is not closed")
                if self.data[self.pos] == ord(")"):
                    self.pos += 1
                    return items
                items.append(self.value())
        if ch == ord(")"):
            raise ValueError("an unexpected ')' in the IMAP response")
        if ch == ord('"'):
            return self._quoted()
        if ch == ord("{"):
            return self._literal()
        atom = self._atom()
        return None if atom.upper() == "NIL" else atom

    def _quoted(self) -> str:
        self.pos += 1
        out = bytearray()
        while self.pos < len(self.data):
            ch = self.data[self.pos]
            if ch == ord("\\") and self.pos + 1 < len(self.data):
                out.append(self.data[self.pos + 1])
                self.pos += 2
                continue
            if ch == ord('"'):
                self.pos += 1
                return out.decode("utf-8", errors="replace")
            out.append(ch)
            self.pos += 1
        raise ValueError("an IMAP quoted string is not closed")

    def _literal(self) -> Literal:
        end = self.data.find(b"}", self.pos)
        if end < 0:
            raise ValueError("a malformed IMAP literal")
        size = int(self.data[self.pos + 1 : end].rstrip(b"+"))
        start = end + 1
        if self.data[start : start + 2] == b"\r\n":
            start += 2
        if start + size > len(self.data):
            raise ValueError("an IMAP literal is shorter than announced")
        self.pos = start + size
        return Literal(self.data[start : start + size])

    def _atom(self) -> str:
        start = self.pos
        depth = 0
        while self.pos < len(self.data):
            ch = self.data[self.pos]
            if ch == ord("["):
                depth += 1
            elif ch == ord("]") and depth:
                depth -= 1
            elif depth == 0 and ch in _ATOM_END:
                break
            self.pos += 1
        if self.pos == start:
            raise ValueError("an empty IMAP atom")
        return self.data[start : self.pos].decode("ascii", errors="replace")


def _wire(data: list[Any]) -> bytes:
    """imaplib's answer back as the bytes on the wire: a literal comes as (prefix ending in
    {n}, data) and the rest of its line as the next bytes item."""
    chunks: list[bytes] = []
    for item in data:
        if item is None:
            continue
        if isinstance(item, tuple):
            prefix, literal = item[0], item[1]
            chunks.append(prefix + b"\r\n" + literal)
        else:
            chunks.append(b" " + item + b"\r\n")
    return b"".join(chunks)


def parse_fetch_response(data: list[Any]) -> list[dict[str, Any]]:
    """Every message of a FETCH answer as {item name: value}; the names are upper-cased
    (UID, INTERNALDATE, BODYSTRUCTURE, BODY[HEADER.FIELDS (...)], BODY[2.1])."""
    tokens = _Tokens(_wire(data))
    messages: list[dict[str, Any]] = []
    while not tokens.at_end():
        tokens.value()  # the message sequence number
        items = tokens.value()
        if not isinstance(items, list) or len(items) % 2:
            raise ValueError("a FETCH response is not a list of name/value pairs")
        message: dict[str, Any] = {}
        for name, value in zip(items[::2], items[1::2], strict=True):
            if not isinstance(name, str):
                raise ValueError("a FETCH item name is not an atom")
            message[name.upper()] = value
        messages.append(message)
    return messages


def section(message: dict[str, Any], prefix: str) -> bytes | None:
    """The bytes of the first BODY[...] item whose name starts with `prefix` (e.g.
    "BODY[HEADER.FIELDS", "BODY[2.1]"); None when the message has none."""
    for name, value in message.items():
        if name.startswith(prefix):
            if isinstance(value, Literal):
                return value.data
            if isinstance(value, str):
                return value.encode("utf-8")
            return None
    return None


# --- BODYSTRUCTURE ------------------------------------------------------------------------


@dataclass(frozen=True)
class BodyPart:
    """A MailPart (what decide reads) plus its transfer encoding (what the fetch decodes)."""

    part: MailPart
    encoding: str


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Literal):
        return value.data.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    return None


def _params(value: Any) -> dict[str, str]:
    """A BODYSTRUCTURE parameter list ("name" "value" ...) as a dict with lower-case keys."""
    if not isinstance(value, list):
        return {}
    out: dict[str, str] = {}
    for key, val in zip(value[::2], value[1::2], strict=False):
        k, v = _text(key), _text(val)
        if k is not None and v is not None:
            out.setdefault(k.casefold(), v)
    return out


def _decode_words(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except (ValueError, LookupError, UnicodeError):
        return value


def _param_value(params: dict[str, str], name: str) -> str | None:
    """A parameter, decoded from RFC 2231 (name*, name*0*, ...) or RFC 2047 words."""
    if name in params:
        return _decode_words(params[name])
    pieces = sorted(
        (int(m.group(1)), key, val)
        for key, val in params.items()
        if (m := re.fullmatch(rf"{re.escape(name)}\*(\d+)\*?", key))
    )
    if f"{name}*" in params:
        pieces = [(0, f"{name}*", params[f"{name}*"])]
    if not pieces:
        return None
    raw = "".join(val for _, _, val in pieces)
    if any(key.endswith("*") for _, key, _ in pieces):
        try:
            # decode_rfc2231 splits charset'language'text; the text stays %-encoded bytes.
            charset, _language, text = decode_rfc2231(raw)
            data = unquote_to_bytes(text)
            return data.decode(charset or "us-ascii", errors="replace") or None
        except (ValueError, LookupError, UnicodeError):
            return None
    return raw or None


def _filename(disposition: Any, ctype_params: dict[str, str]) -> str | None:
    if isinstance(disposition, list) and len(disposition) >= 2:
        name = _param_value(_params(disposition[1]), "filename")
        if name:
            return name
    name = _param_value(ctype_params, "name")
    return name or None


def _is_multipart(node: Any) -> bool:
    return isinstance(node, list) and bool(node) and isinstance(node[0], list)


def _multipart_subtype(node: list[Any]) -> str:
    for item in node:
        if not isinstance(item, list):
            return (_text(item) or "").casefold()
    return ""


def _size(value: Any) -> int:
    text = _text(value)
    if text is None or not text.isdigit():
        raise ValueError("a BODYSTRUCTURE part has no size")
    return int(text)


def _leaf(node: list[Any], index: str, out: list[BodyPart], *, resource: bool) -> None:
    if len(node) < 7:
        raise ValueError("a BODYSTRUCTURE part is too short")
    media = f"{(_text(node[0]) or '').casefold()}/{(_text(node[1]) or '').casefold()}"
    ctype_params = _params(node[2])
    encoding = (_text(node[5]) or "7bit").casefold()
    size = _size(node[6])
    if media == "message/rfc822":
        disposition = node[11] if len(node) > 11 else None
        out.append(
            BodyPart(
                MailPart(
                    index=index,
                    filename=_filename(disposition, ctype_params),
                    media_type=media,
                    size_bytes=size,
                    nested_rfc822=True,
                ),
                encoding,
            )
        )
        body = node[8] if len(node) > 8 else None
        if _is_multipart(body):
            _children(body, index, out)
        elif isinstance(body, list):
            _leaf(body, f"{index}.1", out, resource=False)
        return
    ext = 8 if media.startswith("text/") else 7  # text/* has a line count before the ext
    disposition = node[ext + 1] if len(node) > ext + 1 else None
    # A resource of an HTML body (a multipart/related part after the first: a logo in a
    # signature) is part of the body, not an attachment: no filename.
    filename = None if resource else _filename(disposition, ctype_params)
    out.append(
        BodyPart(
            MailPart(index=index, filename=filename, media_type=media, size_bytes=size),
            encoding,
        )
    )


def _children(node: list[Any], prefix: str, out: list[BodyPart]) -> None:
    related = _multipart_subtype(node) == "related"
    # The children are the leading lists; the subtype follows, then extension data (the
    # parameter list, disposition, language), which holds lists too and is not a part.
    children: list[list[Any]] = []
    for item in node:
        if not isinstance(item, list):
            break
        children.append(item)
    for number, child in enumerate(children, start=1):
        index = f"{prefix}.{number}" if prefix else str(number)
        if _is_multipart(child):
            _children(child, index, out)
        else:
            _leaf(child, index, out, resource=related and number > 1)


def body_parts(bodystructure: Any) -> list[BodyPart]:
    """The parts of a BODYSTRUCTURE in RFC 3501 numbering: a single-part message is "1";
    a multipart's children are 1, 2, …; a message/rfc822 part n lists itself, then its body's
    parts as n.1, n.2, … (every level is listed; decide follows one)."""
    if not isinstance(bodystructure, list):
        raise ValueError("BODYSTRUCTURE is not a list")
    out: list[BodyPart] = []
    if _is_multipart(bodystructure):
        _children(bodystructure, "", out)
    else:
        _leaf(bodystructure, "1", out, resource=False)
    return out


# --- Header fields -----------------------------------------------------------------------


@dataclass(frozen=True)
class HeaderFacts:
    facts: InboundMailFacts
    message_id: str | None  # stripped; only ever hashed (message_id_hash), never stored


def _addresses(values: list[str]) -> list[str]:
    return [addr for _, addr in getaddresses(values) if "@" in addr]


def mail_facts(header: bytes, parts: list[MailPart]) -> HeaderFacts:
    """The facts decide reads, from the fetched header fields. The From address is "" unless
    the header names exactly one address (a mail "from" two people is no member's)."""
    message = BytesHeaderParser(policy=compat32).parsebytes(header)
    senders = _addresses(message.get_all("From") or [])
    recipients = _addresses(
        (message.get_all("To") or [])
        + (message.get_all("Cc") or [])
        + (message.get_all("Delivered-To") or [])
    )
    auth_headers = message.get_all("Authentication-Results") or []
    auth = parse_authentication_results(str(auth_headers[0])) if auth_headers else []
    raw_id = message.get("Message-ID")
    message_id = str(raw_id).strip() if raw_id is not None else ""
    return HeaderFacts(
        facts=InboundMailFacts(
            from_address=senders[0] if len(senders) == 1 else "",
            recipients=recipients,
            auth_results=auth,
            parts=parts,
            message_id_present=bool(message_id),
        ),
        message_id=message_id or None,
    )


def message_id_hash(message_id: str) -> bytes:
    """The SHA-256 of the stripped Message-ID (§5.5): what inbound_mail stores."""
    return hashlib.sha256(message_id.strip().encode("utf-8", errors="surrogateescape")).digest()


# --- Small rest --------------------------------------------------------------------------


def decode_part(data: bytes, encoding: str) -> bytes:
    """A fetched part's content from its transfer encoding. ValueError on an encoding the
    app does not decode or a malformed base64 body."""
    encoding = encoding.casefold()
    if encoding == "base64":
        try:
            return base64.b64decode(b"".join(data.split()), validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("a base64 part does not decode") from None
    if encoding == "quoted-printable":
        return quopri.decodestring(data)
    if encoding in ("7bit", "8bit", "binary"):
        return data
    raise ValueError("an unknown transfer encoding")


def imap_since(day: date) -> str:
    """The SEARCH date (RFC 3501 date: 1-Nov-2026), with English month names whatever the
    process locale."""
    return f"{day.day}-{_MONTHS[day.month - 1]}-{day.year}"


_INTERNALDATE = re.compile(
    r"\s*(\d{1,2})-([A-Za-z]{3})-(\d{4}) (\d{2}):(\d{2}):(\d{2}) ([+-])(\d{2})(\d{2})\s*"
)


def parse_internaldate(value: str | None) -> datetime | None:
    """INTERNALDATE ("10-Nov-2026 12:34:56 +0000") as an aware UTC datetime; None when
    absent or malformed (the column is nullable)."""
    if value is None:
        return None
    match = _INTERNALDATE.fullmatch(value)
    if match is None:
        return None
    day, mon, year, hh, mm, ss, sign, oh, om = match.groups()
    try:
        month = [m.casefold() for m in _MONTHS].index(mon.casefold()) + 1
        offset = timedelta(hours=int(oh), minutes=int(om)) * (1 if sign == "+" else -1)
        local = datetime(
            int(year), month, int(day), int(hh), int(mm), int(ss), tzinfo=timezone(offset)
        )
    except ValueError:
        return None
    return local.astimezone(UTC)


def imap_mailbox(label: str) -> str:
    """The label as an IMAP mailbox argument: modified UTF-7 (RFC 3501 §5.1.3, so a label
    "Hausbücher" works) and quoted, since imaplib sends arguments as given."""
    out: list[str] = []
    pending: list[str] = []

    def flush() -> None:
        if pending:
            utf16 = "".join(pending).encode("utf-16-be")
            out.append("&" + base64.b64encode(utf16).decode().rstrip("=").replace("/", ",") + "-")
            pending.clear()

    for char in label:
        if 0x20 <= ord(char) <= 0x7E:
            flush()
            out.append("&-" if char == "&" else char)
        else:
            pending.append(char)
    flush()
    escaped = "".join(out).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
