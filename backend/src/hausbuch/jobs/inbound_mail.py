"""The poll_inbox periodic task: email-in from one label of the household's Gmail account
(docs/phases/phase-5.md §5, D121).

Every ten minutes the worker opens IMAP over TLS to HAUSBUCH_INBOX_IMAP_HOST, logs in with
the app password, opens only the label HAUSBUCH_INBOX_LABEL with EXAMINE (read-only: no
flag can change, nothing is marked seen) and lists the last 30 days with UID SEARCH SINCE.
For each message it fetches the BODYSTRUCTURE and the header fields of rules.inbound_mail's
HEADER_FIELDS, skips a message whose Message-ID hash already has a final inbound_mail row,
and decides with rules.inbound_mail.decide. Only an accepted message's attachments are then
fetched, one part per FETCH and always with BODY.PEEK, so a rejected mail's parts are never
read (§5.3) and at most one attachment is held in memory.

The guards (D121, §5.5):

- The client issues only the commands in ALLOWED_COMMANDS: CAPABILITY (imaplib, on
  connect), LOGIN, EXAMINE, UID SEARCH, UID FETCH and LOGOUT. Never SELECT, STORE, COPY,
  MOVE, EXPUNGE, CREATE, DELETE, APPEND, LIST or CLOSE, and no SMTP. A missing label fails
  the poll with email.label_missing, never a CREATE. tests/db/test_services_inbound_mail.py
  runs the poll against a fake server that fails on any other command.
- imaplib.Debug must be 0: at 1 or more imaplib prints the LOGIN line, password included,
  to stderr. A poll refuses to start otherwise.
- Nothing of the mail is logged: one line per poll with counts and codes
  (`inbound_mail_polled accepted=1 rejected=0 failed=0`), exception class names only. The
  user, the password and the address are repr=False settings and never leave this module
  but to the server.
- The TLS context verifies the server (ssl.create_default_context; imaplib's own default
  does not check the certificate) and the socket has a timeout.

Attempts: a mail whose part could not be stored is recorded `failed` and retried at the next
polls, at most MAX_ATTEMPTS times (the email_failed card follows). A connection lost in the
middle of a mail records nothing for it: the next poll starts it again without spending an
attempt. A FETCH the server answers with NO, or with an answer the parser cannot read, is
not a lost connection: a refused part fails that mail's attempt like a storage error, and a
refused listing batch is fetched again one UID at a time, the UIDs that still fail counted
as `unreadable` and skipped, so one bad mail never stops the others. A mail without a
Message-ID cannot be recorded (the hash is NOT NULL, §12.1); it is
counted as `unrecorded` in the log line and decided again at each poll, never fetched.

Workspace: the mailbox belongs to the deployment, and the members of one workspace are its
allowed senders, so the poll runs only when exactly one workspace is provisioned (this
household's deployment) and logs a skip otherwise. Poll-level failures (the login refused
after the app password was revoked, the label missing) are logged with their code; no table
holds them, so no card shows them (reported to the orchestrator as a spec gap).
"""

import contextlib
import imaplib
import logging
import ssl
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from hausbuch.auth.lookup import provisioned_workspace_ids
from hausbuch.db.session import workspace_transaction
from hausbuch.rules import errors as E
from hausbuch.rules.inbound_mail import (
    HEADER_FIELDS,
    BodyPart,
    body_parts,
    decide,
    decode_part,
    imap_mailbox,
    imap_since,
    mail_facts,
    message_id_hash,
    parse_fetch_response,
    parse_internaldate,
    section,
)
from hausbuch.schemas.enums import ActorType
from hausbuch.schemas.inbound_mail import MAX_ATTACHMENT_BYTES, InboundDecision, InboundPolicy
from hausbuch.services import inbound_mail as service
from hausbuch.services.context import Actor, new_command
from hausbuch.settings import Settings

from .document_read import JobDeps

POLL_TASK = "poll_inbox"
POLL_CRON = "*/10 * * * *"  # D121: every ten minutes
POLL_LOCK = "inbox:poll"  # one poll at a time
SEARCH_DAYS = 30  # D121: UID SEARCH SINCE today - 30 days
FETCH_BATCH = 50  # header and BODYSTRUCTURE fetches per command
IMAP_PORT = 993
IMAP_TIMEOUT_S = 60

# The IMAP commands this module may issue (D121's whitelist). "UID SEARCH" and "UID FETCH"
# are the UID command with its sub-command.
ALLOWED_COMMANDS = frozenset(
    {"CAPABILITY", "LOGIN", "EXAMINE", "UID SEARCH", "UID FETCH", "LOGOUT"}
)
_HEADER_ITEM = f"BODY.PEEK[HEADER.FIELDS ({' '.join(HEADER_FIELDS)})]"
_LISTING_ITEMS = f"(UID INTERNALDATE BODYSTRUCTURE {_HEADER_ITEM})"
SYSTEM_ACTOR = Actor(ActorType.SYSTEM, POLL_TASK)

logger = logging.getLogger("hausbuch.jobs.inbound_mail")

Connect = Callable[[Settings], imaplib.IMAP4]


def configured(settings: Settings) -> bool:
    """The poll is off until the four values are set (§5.2 step 4)."""
    return None not in (
        settings.inbox_imap_user,
        settings.inbox_imap_password,
        settings.inbox_address,
        settings.inbox_label,
    )


def connect_imap(settings: Settings) -> imaplib.IMAP4:
    """IMAP over TLS with a verifying context and a timeout. imaplib sends CAPABILITY."""
    return imaplib.IMAP4_SSL(
        settings.inbox_imap_host,
        IMAP_PORT,
        ssl_context=ssl.create_default_context(),
        timeout=IMAP_TIMEOUT_S,
    )


@dataclass
class PollResult:
    """What one poll did: `status` is done, or why it stopped (not_configured, skipped,
    or an email.* code); the counts are per message."""

    status: str
    accepted: int = 0
    rejected: int = 0
    failed: int = 0
    known: int = 0  # already final, skipped without a decision
    unrecorded: int = 0  # no Message-ID: rejected, not recordable
    unreadable: int = 0  # the server refused or garbled its listing even alone: skipped
    codes: Counter = field(default_factory=Counter)


class _ConnectionLost(Exception):
    """The server or the network went away mid-poll; nothing is recorded for the mail."""


class _FetchRefused(Exception):
    """The server answered one FETCH with NO, or with an answer the parser cannot read. The
    connection is fine: the poll goes on with the next mail (or the next UID)."""


def _log(result: PollResult, **extra: Any) -> None:
    fields = {
        "event": "inbound_mail_polled",
        "status": result.status,
        "accepted": result.accepted,
        "rejected": result.rejected,
        "failed": result.failed,
        "known": result.known,
        "unrecorded": result.unrecorded,
        "unreadable": result.unreadable,
        "codes": dict(sorted(result.codes.items())),
    } | extra
    level = logging.INFO if result.status in ("done", "not_configured") else logging.WARNING
    logger.log(level, "inbound mail polled", extra={"fields": fields})


def _single_workspace(deps: JobDeps) -> UUID | None:
    workspaces = provisioned_workspace_ids(deps.engine)
    return next(iter(workspaces)) if len(workspaces) == 1 else None


def run_poll_inbox(
    deps: JobDeps, *, connect: Connect = connect_imap, workspace_id: UUID | None = None
) -> PollResult:
    """One poll. Never raises: every outcome is a PollResult and one log line."""
    settings = deps.settings
    if not configured(settings):
        return PollResult("not_configured")  # silent: the switch is the settings, not a fault
    if imaplib.Debug:
        result = PollResult("skipped")
        _log(result, reason="imaplib_debug")  # it would print the LOGIN line
        return result
    ws = workspace_id or _single_workspace(deps)
    if ws is None:
        result = PollResult("skipped")
        _log(result, reason="workspaces")
        return result
    now = deps.clock()
    try:
        imap = connect(settings)
    except (OSError, imaplib.IMAP4.error) as exc:
        result = PollResult("connect_failed")
        _log(result, exc_type=type(exc).__name__)
        return result
    result = PollResult("done")
    try:
        _session(deps, imap, ws, now, result)
    except _ConnectionLost as exc:
        result.status = "connection_lost"
        _log(result, exc_type=type(exc.__cause__ or exc).__name__)
        return result
    except Exception as exc:  # noqa: BLE001 - the database, say; the next poll retries
        result.status = "failed"
        _log(result, exc_type=type(exc).__name__)
        return result
    finally:
        with contextlib.suppress(Exception):  # the socket may be gone; nothing to do
            imap.logout()
    _log(result)
    return result


def _session(deps: JobDeps, imap: imaplib.IMAP4, ws: UUID, now: datetime, result) -> None:
    settings = deps.settings
    try:
        try:
            imap.login(settings.inbox_imap_user, settings.inbox_imap_password)
        except imaplib.IMAP4.abort:
            raise
        except imaplib.IMAP4.error:
            result.status = E.EMAIL_AUTH_REJECTED  # the app password revoked, or wrong
            return
        typ, _ = imap.select(imap_mailbox(settings.inbox_label), readonly=True)  # EXAMINE
        if typ != "OK":
            result.status = E.EMAIL_LABEL_MISSING  # never a CREATE
            return
        since = imap_since((now - timedelta(days=SEARCH_DAYS)).date())
        typ, data = imap.uid("SEARCH", "SINCE", since)
        if typ != "OK":
            raise _ConnectionLost("search refused")
        uids = [uid.decode() for uid in (data[0] or b"").split()] if data else []
        if not uids:
            return
        with workspace_transaction(deps.engine, ws) as conn:
            policy = InboundPolicy(
                allowed_senders=service.allowed_senders(conn),
                token_address=settings.inbox_address,
                authserv_id=settings.inbox_authserv_id,
                media_types=settings.upload_media_types,
                # A part larger than an upload may be is refused before it is fetched.
                max_bytes=min(MAX_ATTACHMENT_BYTES, settings.max_upload_bytes),
            )
        for batch in _batches(uids, FETCH_BATCH):
            for message in _listing(imap, batch, result):
                try:
                    _one(deps, imap, ws, now, policy, message, result)
                except (_ConnectionLost, imaplib.IMAP4.abort, OSError):
                    raise
                except Exception as exc:  # noqa: BLE001 - a database error: next mail
                    result.failed += 1
                    fields = {"event": "inbound_mail_failed", "exc_type": type(exc).__name__}
                    logger.error("a mail could not be recorded", extra={"fields": fields})
    except (imaplib.IMAP4.abort, OSError) as exc:
        raise _ConnectionLost() from exc


def _batches(items: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _fetch(imap: imaplib.IMAP4, uid_set: str, items: str) -> list[dict[str, Any]]:
    """One UID FETCH. A NO (Gmail's "Some messages could not be FETCHed"; imaplib then drops
    the untagged data of the whole command) or an answer the parser cannot read raises
    _FetchRefused; a lost connection raises imaplib's abort or OSError, as before."""
    typ, data = imap.uid("FETCH", uid_set, items)
    if typ != "OK":
        raise _FetchRefused("fetch refused")
    try:
        return parse_fetch_response([d for d in data if d is not None])
    except ValueError as exc:  # one message the parser cannot read spoils the whole answer
        raise _FetchRefused("fetch unreadable") from exc


def _listing(imap: imaplib.IMAP4, batch: list[str], result: PollResult) -> list[dict[str, Any]]:
    """The listing items of a batch of UIDs. When the server refuses the batch, or its answer
    cannot be read, each UID is fetched alone, so one bad mail cannot hide the others; a UID
    that still fails is counted as unreadable and skipped (it is decided again next poll)."""
    try:
        return _fetch(imap, ",".join(batch), _LISTING_ITEMS)
    except _FetchRefused:
        pass
    messages: list[dict[str, Any]] = []
    for uid in batch:
        try:
            messages += _fetch(imap, uid, _LISTING_ITEMS)
        except _FetchRefused:
            result.unreadable += 1
    return messages


def _record(deps: JobDeps, ws: UUID, now: datetime, **kwargs: Any) -> None:
    with workspace_transaction(deps.engine, ws) as conn:
        service.record_mail(new_command(conn, ws, SYSTEM_ACTOR, now=now), **kwargs)


def _one(deps, imap, ws, now, policy, message: dict[str, Any], result: PollResult) -> None:
    header = section(message, "BODY[HEADER.FIELDS") or b""
    try:
        parts: list[BodyPart] | None = body_parts(message.get("BODYSTRUCTURE"))
    except ValueError:
        parts = None
    facts = mail_facts(header, [p.part for p in parts or []])
    if facts.message_id is None:
        result.unrecorded += 1
        result.codes[E.EMAIL_NO_MESSAGE_ID] += 1
        return
    digest = message_id_hash(facts.message_id)
    with workspace_transaction(deps.engine, ws) as conn:
        if service.final_message_hashes(conn, [digest]):
            result.known += 1
            return
    received_at = parse_internaldate(message.get("INTERNALDATE"))
    if parts is None:  # a BODYSTRUCTURE we cannot read: an attempt, retried, then a card
        _record(
            deps,
            ws,
            now,
            message_id_hash=digest,
            received_at=received_at,
            decision=InboundDecision.FAILED,
            code=E.EMAIL_FAILED,
        )
        result.failed += 1
        result.codes[E.EMAIL_FAILED] += 1
        return
    decision = decide(facts.facts, policy)
    if not decision.accept:
        _record(
            deps,
            ws,
            now,
            message_id_hash=digest,
            received_at=received_at,
            decision=InboundDecision.REJECTED,
            code=decision.code,
        )
        result.rejected += 1
        result.codes[decision.code] += 1
        return
    by_index = {p.part.index: p for p in parts}
    stored: list[service.StoredAttachment] = []
    part_failed = False
    for index in decision.attachments:
        part = by_index[index]
        try:
            raw = _fetch_part(imap, message["UID"], index)  # a lost connection propagates
        except _FetchRefused:  # refused or unreadable: this attempt fails, the poll goes on
            part_failed = True
            fields = {"event": "inbound_mail_part_refused"}
            logger.warning("the server refused an attachment", extra={"fields": fields})
            continue
        try:
            stored.append(_store(deps, ws, now, part, raw))
        except Exception as exc:  # noqa: BLE001 - class name only; the attempt is recorded
            part_failed = True
            fields = {"event": "inbound_mail_part_failed", "exc_type": type(exc).__name__}
            logger.warning("an attachment could not be stored", extra={"fields": fields})
    code = E.EMAIL_FAILED if part_failed else decision.code
    _record(
        deps,
        ws,
        now,
        message_id_hash=digest,
        received_at=received_at,
        decision=InboundDecision.FAILED if part_failed else InboundDecision.ACCEPTED,
        code=code,
        attachments_total=len(decision.attachments) + decision.skipped,
        stored=stored,
    )
    if part_failed:
        result.failed += 1
    else:
        result.accepted += 1
    result.codes[code] += 1


def _fetch_part(imap: imaplib.IMAP4, uid: str, index: str) -> bytes | None:
    """The encoded bytes of one part, fetched with BODY.PEEK (nothing is marked seen)."""
    messages = _fetch(imap, uid, f"(BODY.PEEK[{index}])")
    for message in messages:
        data = section(message, f"BODY[{index}]")
        if data is not None:
            return data
    return None


def _store(deps: JobDeps, ws: UUID, now: datetime, part: BodyPart, raw: bytes | None):
    if raw is None:
        raise ValueError("the server returned no bytes for the part")
    data = decode_part(raw, part.encoding)
    if len(data) > deps.settings.max_upload_bytes:
        raise ValueError("the attachment is larger than an upload may be")
    with workspace_transaction(deps.engine, ws) as conn:
        return service.store_attachment(
            new_command(conn, ws, SYSTEM_ACTOR, now=now),
            deps.blob_store,
            data,
            media_type=part.part.media_type,
            filename=part.part.filename,
            iban_key=deps.settings.iban_hmac_key,
            ai_enabled=deps.settings.ai_enabled and deps.model_client is not None,
        )
