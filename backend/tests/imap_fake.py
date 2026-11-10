"""An IMAP server in memory for the email-in tests (docs/phases/phase-5.md D121, §13 step 4:
"the command-whitelist test double").

`FakeImap` is a real imaplib.IMAP4 whose socket is replaced: imaplib builds every command
itself (so `select(readonly=True)` really goes out as EXAMINE), and the fake server answers
it. Any command outside jobs.inbound_mail.ALLOWED_COMMANDS, a FETCH without PEEK (it would
set \\Seen) or a FETCH item the poll has no business asking for is recorded in
`server.violations` and answered BAD, so a test that ends with `violations == []` proves the
mailbox saw nothing but reads.

The messages are real MIME (built with the email package); the server computes their
BODYSTRUCTURE and sections the way RFC 3501 §6.4.5 numbers them, which also cross-checks
rules.inbound_mail's parser against mails it did not write itself. Every address is
synthetic.
"""

import email
import imaplib
import re
from dataclasses import dataclass, field
from email.message import Message
from email.policy import compat32

from hausbuch.jobs.inbound_mail import ALLOWED_COMMANDS
from hausbuch.rules.inbound_mail import HEADER_FIELDS


def _q(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _params(pairs: list[tuple[str, str]]) -> str:
    if not pairs:
        return "NIL"
    return "(" + " ".join(f"{_q(k.upper())} {_q(str(v))}" for k, v in pairs) + ")"


def _disposition(msg: Message) -> str:
    if msg.get("Content-Disposition") is None:
        return "NIL"
    params = msg.get_params(header="content-disposition", unquote=True) or []
    kind = params[0][0] if params else "attachment"
    return f"({_q(kind.upper())} {_params(params[1:])})"


ENVELOPE = '(NIL "x" NIL NIL NIL NIL NIL NIL NIL "<inner@example.net>")'


def _structure(msg: Message, index: str, sections: dict[str, bytes]) -> str:
    """BODYSTRUCTURE of `msg` as part `index` ("" for the top of a multipart message)."""
    if msg.get_content_maintype() == "multipart":
        children = msg.get_payload()
        inner = "".join(
            _structure(child, f"{index}.{n}" if index else str(n), sections)
            for n, child in enumerate(children, start=1)
        )
        return f"({inner} {_q(msg.get_content_subtype().upper())})"
    ctype_params = (msg.get_params(unquote=True) or [])[1:]
    disposition = _disposition(msg)
    number = index or "1"
    if msg.get_content_type() == "message/rfc822":
        nested = msg.get_payload(0)
        raw = nested.as_bytes()
        sections[number] = raw
        if nested.get_content_maintype() == "multipart":
            body = _structure(nested, number, sections)
        else:
            body = _structure(nested, f"{number}.1", sections)
        return (
            f'("MESSAGE" "RFC822" {_params(ctype_params)} NIL NIL "7BIT" {len(raw)} '
            f"{ENVELOPE} {body} {raw.count(b'\n')} NIL {disposition} NIL NIL)"
        )
    payload = msg.get_payload()
    data = payload.encode() if isinstance(payload, str) else payload
    sections[number] = data
    encoding = (msg.get("Content-Transfer-Encoding") or "7bit").upper()
    lines = f" {data.count(b'\n')}" if msg.get_content_maintype() == "text" else ""
    return (
        f"({_q(msg.get_content_maintype().upper())} {_q(msg.get_content_subtype().upper())} "
        f"{_params(ctype_params)} NIL NIL {_q(encoding)} {len(data)}{lines} NIL "
        f"{disposition} NIL NIL)"
    )


@dataclass
class StoredMessage:
    uid: int
    raw: bytes
    internaldate: str = "10-Nov-2026 09:00:00 +0000"
    bodystructure: str = ""
    sections: dict[str, bytes] = field(default_factory=dict)
    header: bytes = b""

    def __post_init__(self) -> None:
        msg = email.message_from_bytes(self.raw, policy=compat32)
        top = "" if msg.get_content_maintype() == "multipart" else "1"
        self.bodystructure = _structure(msg, top, self.sections)
        wanted = {name.upper() for name in HEADER_FIELDS}
        lines = [
            f"{name}: {value}".replace("\r\n", "\n").replace("\n", "\r\n")
            for name, value in msg.items()
            if name.upper() in wanted
        ]
        self.header = ("\r\n".join(lines) + "\r\n\r\n").encode()


@dataclass
class FakeServer:
    """The mailbox: labels with their messages, the credentials, and what was asked."""

    user: str = "haushalt@example.com"
    password: str = "app-password-test-0000"
    labels: dict[str, list[StoredMessage]] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)  # e.g. "UID FETCH"
    fetches: list[tuple[str, str]] = field(default_factory=list)  # (uid set, items)
    violations: list[str] = field(default_factory=list)
    selected: str | None = None
    drop_after_fetches: int | None = None  # simulate a lost connection

    def add(self, label: str, raw: bytes, **kwargs) -> StoredMessage:
        messages = self.labels.setdefault(label, [])
        message = StoredMessage(uid=100 + sum(len(m) for m in self.labels.values()), raw=raw)
        for key, value in kwargs.items():
            setattr(message, key, value)
        messages.append(message)
        return message

    # --- the protocol ------------------------------------------------------------------

    def handle(self, line: bytes) -> bytes:
        tag, _, rest = line.decode().partition(" ")
        words = rest.split(" ", 2)
        name = words[0].upper()
        if name == "UID" and len(words) > 1:
            name = f"UID {words[1].upper()}"
            args = words[2] if len(words) > 2 else ""
        else:
            args = rest[len(words[0]) :].strip()
        self.commands.append(name)
        if name not in ALLOWED_COMMANDS:
            self.violations.append(name)
            return f"{tag} BAD not allowed\r\n".encode()
        handler = getattr(self, "_" + name.lower().replace(" ", "_"))
        return handler(tag, args)

    def _capability(self, tag: str, args: str) -> bytes:
        return f"* CAPABILITY IMAP4rev1 UIDPLUS\r\n{tag} OK done\r\n".encode()

    def _login(self, tag: str, args: str) -> bytes:
        user, _, password = args.partition(" ")
        password = password.strip('"').replace('\\"', '"').replace("\\\\", "\\")
        if user.strip('"') == self.user and password == self.password:
            return f"{tag} OK logged in\r\n".encode()
        return f"{tag} NO [AUTHENTICATIONFAILED] Invalid credentials\r\n".encode()

    def _examine(self, tag: str, args: str) -> bytes:
        label = args.strip('"')
        if label not in self.labels:
            return f"{tag} NO [NONEXISTENT] Unknown Mailbox\r\n".encode()
        self.selected = label
        count = len(self.labels[label])
        return f"* {count} EXISTS\r\n{tag} OK [READ-ONLY] EXAMINE completed\r\n".encode()

    def _logout(self, tag: str, args: str) -> bytes:
        return f"* BYE\r\n{tag} OK bye\r\n".encode()

    def _uid_search(self, tag: str, args: str) -> bytes:
        if not re.fullmatch(r"SINCE \d{1,2}-[A-Z][a-z]{2}-\d{4}", args):
            self.violations.append(f"SEARCH {args}")
            return f"{tag} BAD search\r\n".encode()
        uids = " ".join(str(m.uid) for m in self.labels.get(self.selected or "", []))
        return f"* SEARCH {uids}\r\n{tag} OK done\r\n".encode()

    def _uid_fetch(self, tag: str, args: str) -> bytes:
        uid_set, _, items = args.partition(" ")
        self.fetches.append((uid_set, items))
        if self.drop_after_fetches is not None and len(self.fetches) > self.drop_after_fetches:
            raise ConnectionResetError("dropped")
        wanted = {int(u) for u in uid_set.split(",")}
        inner = items.strip("()")
        if re.search(r"BODY\[", inner) or re.search(r"\b(RFC822|FLAGS|ENVELOPE)\b", inner):
            self.violations.append(f"FETCH {items}")  # BODY[ without PEEK sets \Seen
            return f"{tag} BAD fetch\r\n".encode()
        out = b""
        for seq, message in enumerate(self.labels.get(self.selected or "", []), start=1):
            if message.uid not in wanted:
                continue
            body = f"* {seq} FETCH (UID {message.uid}".encode()
            if "INTERNALDATE" in inner:
                body += f' INTERNALDATE "{message.internaldate}"'.encode()
            if "BODYSTRUCTURE" in inner:
                body += f" BODYSTRUCTURE {message.bodystructure}".encode()
            for spec in re.findall(r"BODY\.PEEK\[([^\]]*)\]", inner):
                if spec.startswith("HEADER.FIELDS"):
                    data = message.header
                elif re.fullmatch(r"[1-9][0-9]*(\.[1-9][0-9]*)*", spec):
                    data = message.sections[spec]
                else:
                    self.violations.append(f"FETCH {spec}")
                    return f"{tag} BAD section\r\n".encode()
                body += f" BODY[{spec}] {{{len(data)}}}\r\n".encode() + data
            out += body + b")\r\n"
        return out + f"{tag} OK done\r\n".encode()


class FakeImap(imaplib.IMAP4):
    """imaplib.IMAP4 over the FakeServer instead of a socket."""

    def __init__(self, server: FakeServer) -> None:
        self.server = server
        super().__init__("imap.example.com", 993)

    def open(self, host="", port=993, timeout=None) -> None:
        self.host, self.port = host, port
        self._in = b"* OK fake ready\r\n"
        self._out = b""

    def send(self, data: bytes) -> None:
        self._out += data
        while b"\r\n" in self._out:
            line, self._out = self._out.split(b"\r\n", 1)
            self._in += self.server.handle(line)

    def read(self, size: int) -> bytes:
        data, self._in = self._in[:size], self._in[size:]
        return data

    def readline(self) -> bytes:
        line, sep, rest = self._in.partition(b"\n")
        self._in = rest
        return line + sep

    def shutdown(self) -> None:
        pass
