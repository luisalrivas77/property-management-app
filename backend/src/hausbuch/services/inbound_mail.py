"""Email-in, the database side (docs/phases/phase-5.md §5.4, §8, D121).

The poll (jobs/inbound_mail.py) reads the label and decides; this module stores what an
accepted attachment brings and records each attempt at a mail:

- `store_attachment`, in one transaction per attachment (§5.4: one bad part does not lose
  the others). The bytes become `upload_document(source=email)` first, so the document keeps
  that source whatever follows (the vault deduplicates on the bytes, D9). Then:
  - a camt or CSV-CAMT statement whose account IBAN matches exactly one account that is
    not an owner account is imported there with `import_statement`, unchanged (a CSV then
    waits at pending_balance with its usual card); the import runs in a savepoint, so a
    refusal (an empty file, a statement.* code) leaves the uploaded document in place;
  - a statement that matches no account, several, or cannot be imported stays a document,
    typed `statement` when untyped, and is listed as `email.statement_unmatched`;
  - anything else gets the upload's proposal (`proposal_for_upload`, when the AI layer is
    on), so the classifier's tenant-data gate applies exactly as for an upload (phase 2
    §5.5: the gate sits in the document_read job, after classification).
- `record_mail`: one `inbound_mail` row per attempt, with the attachment links and one
  `document.received_by_email` event per document. A message is final after an accepted or
  rejected row, or MAX_ATTEMPTS failed ones (`final_message_hashes`).

Nothing of the mail is stored but its attachments: a row carries the SHA-256 of the
Message-ID, the time, the decision, the code and the counts (§5.5).
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import Connection, and_, func, or_, select

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import app_user as app_user_table
from hausbuch.db.tables import document as document_table
from hausbuch.db.tables import inbound_mail as mail_table
from hausbuch.db.tables import inbound_mail_attachment as link_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.rules import errors as E
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.api import InboxItem, InboxItemKind
from hausbuch.schemas.bank import StatementFormat
from hausbuch.schemas.document import DocumentTyping
from hausbuch.schemas.enums import DocumentSource, DocumentType
from hausbuch.schemas.events import DocumentReceivedByEmail, EventType
from hausbuch.schemas.inbound_mail import MAX_ATTEMPTS, InboundDecision, InboundMail
from hausbuch.storage import BlobStore

from .bank import detect_format, parse_statement
from .context import CommandContext
from .documents import type_document, upload_document
from .events import append_event
from .iban import protect_iban
from .proposals import proposal_for_upload
from .statements import import_statement

RECEIVED_CARD_FOR = timedelta(hours=24)  # §5.4: email_received lists the last 24 h
LISTED_FOR = timedelta(days=30)  # GET /inbound-mail, and how long a final failure is shown


# --- One attachment ------------------------------------------------------------------------


@dataclass(frozen=True)
class StoredAttachment:
    """What one accepted attachment became: its document, and the import when it was a
    statement of a known account. `unmatched`: a statement no account took."""

    document_id: UUID
    import_id: UUID | None = None
    unmatched: bool = False


def _statement_iban(data: bytes, media_type: str) -> tuple[bool, str | None]:
    """(is a camt / CSV-CAMT statement, the IBAN of the account it is for). A PDF or an
    unknown format is no statement here: PDF statements are not imported (phase 2), so a PDF
    goes the document path with its proposal. A camt or CSV file that does not parse is a
    statement without an account."""
    try:
        fmt = detect_format(data, media_type)
    except RuleError:
        return False, None
    if fmt not in (StatementFormat.CAMT, StatementFormat.CSV_CAMT):
        return False, None
    try:
        return True, parse_statement(data, media_type).statement.account_iban
    except RuleError:
        return True, None


def _account_for(conn: Connection, iban: str | None, iban_key: bytes) -> UUID | None:
    """The one account that is not an owner account with this IBAN, else None (none, or
    more than one)."""
    if iban is None:
        return None
    try:
        digest = protect_iban(iban, iban_key).hmac
    except RuleError:
        return None
    ids = (
        conn.execute(
            select(account_table.c.id).where(
                account_table.c.iban_hmac == digest, account_table.c.is_owner_account.is_(False)
            )
        )
        .scalars()
        .all()
    )
    return ids[0] if len(ids) == 1 else None


def store_attachment(
    ctx: CommandContext,
    store: BlobStore,
    data: bytes,
    *,
    media_type: str,
    filename: str | None,
    iban_key: bytes,
    ai_enabled: bool,
) -> StoredAttachment:
    """One accepted attachment, in the caller's transaction (the poll opens one per part).
    Raises on a storage or database error; the poll then records the attempt as failed."""
    # Whether the bytes are new, asked before the upload: the poll's parts share one `now`,
    # so the upload route's created_at == now test would call a repeat within one poll new.
    known = ctx.conn.execute(
        select(document_table.c.id).where(
            document_table.c.file_sha256 == hashlib.sha256(data).digest()
        )
    ).first()
    document = upload_document(
        ctx,
        store,
        data,
        media_type=media_type,
        source=DocumentSource.EMAIL,
        original_filename=filename,
    )
    created = known is None
    is_statement, iban = _statement_iban(data, media_type)
    if is_statement:
        account_id = _account_for(ctx.conn, iban, iban_key)
        if account_id is not None:
            try:
                with ctx.conn.begin_nested():
                    imported = import_statement(
                        ctx,
                        store,
                        account_id,
                        data,
                        media_type=media_type,
                        iban_key=iban_key,
                        original_filename=filename,
                    )
            except RuleError:
                pass  # refused (an empty file, a statement.* code): stays a document
            else:
                return StoredAttachment(document.id, imported.id)
        if document.typing is None:
            # Typed only when untyped, as import_statement does: a person's typing stays.
            type_document(ctx, document.id, DocumentTyping(type=DocumentType.STATEMENT))
        return StoredAttachment(document.id, unmatched=True)
    if ai_enabled:
        proposal_for_upload(ctx, document.id, created=created, contains_tenant_data=False)
    return StoredAttachment(document.id)


# --- The attempts --------------------------------------------------------------------------


def allowed_senders(conn: Connection) -> list[str]:
    """The workspace members' emails (D121 (a)); stored lower-cased by the invite path."""
    return list(conn.execute(select(app_user_table.c.email)).scalars())


def final_message_hashes(conn: Connection, hashes: list[bytes]) -> set[bytes]:
    """Of these Message-ID hashes, those whose mail is final: an accepted or rejected row,
    or MAX_ATTEMPTS failed rows."""
    if not hashes:
        return set()
    t = mail_table
    rows = conn.execute(
        select(t.c.message_id_hash).where(
            t.c.message_id_hash.in_(hashes),
            or_(
                t.c.decision.in_([InboundDecision.ACCEPTED, InboundDecision.REJECTED]),
                t.c.attempt == MAX_ATTEMPTS,
            ),
        )
    )
    return {bytes(h) for h in rows.scalars()}


def _next_attempt(conn: Connection, message_id_hash: bytes) -> int:
    t = mail_table
    last = conn.execute(
        select(func.max(t.c.attempt)).where(t.c.message_id_hash == message_id_hash)
    ).scalar_one()
    return 1 if last is None else last + 1


def _linked_before(conn: Connection, message_id_hash: bytes) -> set[UUID]:
    """Documents an earlier attempt at the same mail already linked (a failed attempt keeps
    the parts that worked; the retry links and announces only the rest)."""
    rows = conn.execute(
        select(link_table.c.document_id)
        .join(mail_table, mail_table.c.id == link_table.c.mail_id)
        .where(mail_table.c.message_id_hash == message_id_hash)
    )
    return set(rows.scalars())


def record_mail(
    ctx: CommandContext,
    *,
    message_id_hash: bytes,
    received_at: datetime | None,
    decision: InboundDecision,
    code: str,
    attachments_total: int | None = None,
    stored: list[StoredAttachment] | None = None,
) -> UUID:
    """One attempt's row, its attachment links and their events. The attempt number is the
    next free one for the hash (a rejection is normally attempt 1)."""
    stored = stored or []
    attempt = _next_attempt(ctx.conn, message_id_hash)
    if attempt > MAX_ATTEMPTS:
        raise ValueError("the mail is final already")
    mail_id = ctx.conn.execute(
        mail_table.insert()
        .values(
            workspace_id=ctx.workspace_id,
            message_id_hash=message_id_hash,
            attempt=attempt,
            received_at=received_at,
            decision=decision.value,
            code=code,
            attachments_total=attachments_total,
            attachments_accepted=None if attachments_total is None else len(stored),
            recorded_at=ctx.now,
        )
        .returning(mail_table.c.id)
    ).scalar_one()
    before = _linked_before(ctx.conn, message_id_hash) if attempt > 1 else set()
    seen: set[UUID] = set()
    for item in stored:
        if item.document_id in before or item.document_id in seen:
            continue  # the same bytes twice in one mail are one document (D9)
        seen.add(item.document_id)
        ctx.conn.execute(
            link_table.insert().values(
                workspace_id=ctx.workspace_id,
                mail_id=mail_id,
                document_id=item.document_id,
                import_id=item.import_id,
                recorded_at=ctx.now,
            )
        )
        append_event(
            ctx,
            EventType.DOCUMENT_RECEIVED_BY_EMAIL,
            item.document_id,
            DocumentReceivedByEmail(inbound_mail_id=mail_id, imported=item.import_id is not None),
        )
    return mail_id


# --- Reads -----------------------------------------------------------------------------------


def list_inbound_mail(conn: Connection, now: datetime) -> list[InboundMail]:
    """GET /inbound-mail: the last 30 days' rows, newest first, with what they brought."""
    rows = conn.execute(
        select(mail_table)
        .where(mail_table.c.recorded_at >= now - LISTED_FOR)
        .order_by(mail_table.c.recorded_at.desc(), mail_table.c.attempt.desc())
    ).all()
    links: dict[UUID, list] = {}
    if rows:
        for link in conn.execute(
            select(link_table)
            .where(link_table.c.mail_id.in_([r.id for r in rows]))
            .order_by(link_table.c.recorded_at, link_table.c.id)
        ):
            links.setdefault(link.mail_id, []).append(link)
    return [
        InboundMail(
            id=r.id,
            workspace_id=r.workspace_id,
            recorded_at=r.recorded_at,
            received_at=r.received_at,
            attempt=r.attempt,
            decision=InboundDecision(r.decision),
            code=r.code,
            attachments_total=r.attachments_total,
            attachments_accepted=r.attachments_accepted,
            documents=[link.document_id for link in links.get(r.id, [])],
            imports=[link.import_id for link in links.get(r.id, []) if link.import_id],
        )
        for r in rows
    ]


def inbound_mail_items(conn: Connection, now: datetime) -> list[InboxItem]:
    """The Zu prüfen cards of §5.4, computed on read; the texts name no sender or subject.

    - email_received, one per attachment linked in the last 24 h, with its document (and
      import);
    - email_received for a statement no account took (email.statement_unmatched), shown
      until an import of that document exists, for at most 30 days;
    - email_failed for a mail whose third attempt failed in the last 30 days."""
    imported_docs = select(import_table.c.document_id)
    statement_doc = and_(
        link_table.c.import_id.is_(None),
        document_table.c.type == DocumentType.STATEMENT.value,
    )
    links = conn.execute(
        select(
            link_table.c.document_id,
            link_table.c.import_id,
            link_table.c.recorded_at,
            statement_doc.label("is_statement"),
            link_table.c.document_id.in_(imported_docs).label("imported_since"),
        )
        .join(document_table, document_table.c.id == link_table.c.document_id)
        .where(link_table.c.recorded_at >= now - LISTED_FOR)
    ).all()
    items: list[InboxItem] = []
    for link in links:
        if link.is_statement and not link.imported_since:
            items.append(
                InboxItem(
                    kind=InboxItemKind.EMAIL_RECEIVED,
                    created_at=link.recorded_at,
                    reasoning=(
                        "Ein weitergeleiteter Kontoauszug passt zu keinem Konto und wurde nur"
                        " als Dokument abgelegt."
                    ),
                    question="Zu welchem Konto gehört er? Dort importieren.",
                    document_id=link.document_id,
                )
            )
        elif link.recorded_at >= now - RECEIVED_CARD_FOR:
            imported = link.import_id is not None
            items.append(
                InboxItem(
                    kind=InboxItemKind.EMAIL_RECEIVED,
                    created_at=link.recorded_at,
                    reasoning=(
                        "Kontoauszug aus einer weitergeleiteten Mail importiert."
                        if imported
                        else "Anhang aus einer weitergeleiteten Mail übernommen."
                    ),
                    document_id=link.document_id,
                    import_id=link.import_id,
                )
            )
    failed = conn.execute(
        select(mail_table.c.recorded_at).where(
            mail_table.c.decision == InboundDecision.FAILED.value,
            mail_table.c.attempt == MAX_ATTEMPTS,
            mail_table.c.recorded_at >= now - LISTED_FOR,
        )
    ).scalars()
    for recorded_at in failed:
        items.append(
            InboxItem(
                kind=InboxItemKind.EMAIL_FAILED,
                created_at=recorded_at,
                reasoning=(
                    "Eine weitergeleitete Mail konnte dreimal nicht verarbeitet werden"
                    f" ({E.EMAIL_FAILED})."
                ),
                question="Bitte die Anhänge direkt hochladen.",
            )
        )
    return items
