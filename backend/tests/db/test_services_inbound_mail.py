"""Email-in end to end (docs/phases/phase-5.md §5, D121, §13 step 4), as hausbuch_app under
RLS, against the in-memory IMAP server of tests/imap_fake.py.

The server is a real imaplib.IMAP4 over a scripted socket, so these tests see the commands
imaplib actually sends: only the whitelist goes out (EXAMINE, never SELECT; BODY.PEEK, never
BODY[), a rejected mail's parts are never fetched, and nothing of a mail reaches a log line.
Every address and name is synthetic.
"""

import hashlib
import imaplib
import io
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from email.message import EmailMessage

import pytest
from db.db_support import TEST_IBAN_KEY, _admin
from imap_fake import FakeImap, FakeServer
from pypdf import PdfWriter
from sqlalchemy import select, update
from test_bank_parsers import CAMT, build

from hausbuch.ai.fake import FakeModelClient, text_response
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import document as document_table
from hausbuch.db.tables import inbound_mail as mail_table
from hausbuch.db.tables import inbound_mail_attachment as link_table
from hausbuch.db.tables import proposal as proposal_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import workspace as workspace_table
from hausbuch.jobs import inbound_mail as job
from hausbuch.jobs.document_read import JobDeps, run_document_read
from hausbuch.rules import errors as E
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.bank import AccountCreate
from hausbuch.schemas.enums import ActorType, DocumentSource, DocumentType, ProposalStatus
from hausbuch.schemas.events import EventType
from hausbuch.services import inbound_mail as service
from hausbuch.services import review
from hausbuch.services.accounts import create_account
from hausbuch.services.context import Actor, new_command
from hausbuch.services.documents import upload_document
from hausbuch.services.events import list_events
from hausbuch.services.proposals import proposal_for_upload
from hausbuch.settings import Settings

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
KEY = "sk-ant-api03-SECRET-inbox-test-key"
MEMBER = "eigentuemer.a@example.org"
STRANGER = "fremd@example.net"
IMAP_USER = "haushalt@example.com"
IMAP_PASSWORD = "app-password-test-0000"
TOKEN = "haushalt+x7k2q9@example.com"
LABEL = "Hausbuch"
SUBJECT = "Rechnung Malerarbeiten Wohnung"
BODY = "Hallo, anbei die Rechnung. Vertraulicher Mailtext 4711."
SAMPLE_IBAN = "DE50100000000000000001"  # the camt fixtures' account
ZIP = build(next(c for c in CAMT.cases if c.id == "sparkasse_zip_of_daily_reports").input)


def _pdf(marker: str) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.add_metadata({"/Title": marker})  # distinct bytes per mail
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _mail(
    *attachments: tuple[str, bytes, str],
    sender: str = MEMBER,
    to: str = TOKEN,
    auth: str | None = "dkim=pass header.i=@example.org header.d=example.org",
    message_id: str | None = None,
) -> bytes:
    msg = EmailMessage()
    if auth is not None:
        msg["Authentication-Results"] = f"mx.google.com; {auth}"
    msg["From"] = f"Eigentuemer <{sender}>"
    msg["To"] = to
    msg["Subject"] = SUBJECT
    msg["Message-ID"] = message_id or f"<{uuid.uuid4().hex}@mail.example.org>"
    msg.set_content(BODY)
    for filename, data, media_type in attachments:
        maintype, subtype = media_type.split("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return msg.as_bytes()


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "database_url": "postgresql+psycopg://hausbuch_app@localhost/unused",
        "iban_hmac_key": TEST_IBAN_KEY,
        "session_key": bytes(range(1, 33)),
        "rp_id": "testserver",
        "origin": "https://testserver",
        "blob_store": "local",
        "blob_root": tmp_path / "blobs",
        "ai_enabled": True,
        "anthropic_api_key": KEY,
        "inbox_imap_user": IMAP_USER,
        "inbox_imap_password": IMAP_PASSWORD,
        "inbox_address": TOKEN,
        "inbox_label": LABEL,
    } | overrides
    return Settings(**values)


@pytest.fixture
def inbox(app_engine, fresh_workspaces, blob_store, tmp_path, dbname):
    """(poll, server, ws, deps): poll() runs one poll of the fake server's label."""
    ws, _ = fresh_workspaces
    with _admin(dbname) as conn:
        # Emails are unique across workspaces: the member moves to this test's workspace.
        conn.execute("DELETE FROM app_user WHERE email = %s", (MEMBER,))
        conn.execute(
            "INSERT INTO app_user (id, workspace_id, email, display_name) VALUES (%s, %s, %s, %s)",
            (uuid.uuid4(), ws, MEMBER, "Eigentümer A"),
        )
    server = FakeServer(user=IMAP_USER, password=IMAP_PASSWORD, labels={LABEL: []})
    deps = JobDeps(
        engine=app_engine,
        settings=_settings(tmp_path),
        blob_store=blob_store,
        model_client=FakeModelClient([]),
        clock=lambda: NOW,
    )
    connects: list[int] = []

    def connect(settings):
        connects.append(1)
        return FakeImap(server)

    def poll(deps_=None, at=None):
        d = deps_ or deps
        if at is not None:
            d = JobDeps(d.engine, d.settings, d.blob_store, d.model_client, clock=lambda: at)
        return job.run_poll_inbox(d, connect=connect, workspace_id=ws)

    poll.connects = connects
    return poll, server, ws, deps


def _rows(engine, ws, table, *where):
    with workspace_transaction(engine, ws) as conn:
        return conn.execute(select(table).where(*where)).all()


def _part_fetches(server) -> list[str]:
    """The FETCH commands that asked for a part's bytes (not the header listing)."""
    return [items for _, items in server.fetches if "HEADER.FIELDS" not in items]


def _assert_read_only(server) -> None:
    assert server.violations == []
    assert set(server.commands) <= job.ALLOWED_COMMANDS
    assert "EXAMINE" in server.commands


# --- An invoice ------------------------------------------------------------------------------


def test_a_forwarded_invoice_becomes_a_document_with_a_proposal(inbox, app_engine):
    poll, server, ws, _ = inbox
    pdf = _pdf("invoice")
    message_id = "<invoice-1@mail.example.org>"
    server.add(LABEL, _mail(("rechnung.pdf", pdf, "application/pdf"), message_id=message_id))

    result = poll()

    assert (result.status, result.accepted, result.rejected) == ("done", 1, 0)
    [mail] = _rows(app_engine, ws, mail_table)
    assert mail.message_id_hash == hashlib.sha256(message_id.encode()).digest()
    assert (mail.decision, mail.code, mail.attempt) == ("accepted", E.EMAIL_ACCEPTED, 1)
    assert (mail.attachments_total, mail.attachments_accepted) == (1, 1)
    [link] = _rows(app_engine, ws, link_table)
    [doc] = _rows(app_engine, ws, document_table, document_table.c.id == link.document_id)
    assert doc.file_sha256 == hashlib.sha256(pdf).digest()
    assert (doc.source, doc.original_filename) == (DocumentSource.EMAIL.value, "rechnung.pdf")
    [proposal] = _rows(app_engine, ws, proposal_table, proposal_table.c.document_id == doc.id)
    assert proposal.status == ProposalStatus.QUEUED.value  # its document_read job is queued
    with workspace_transaction(app_engine, ws) as conn:
        events = [e for e in list_events(conn) if e.entity_id == doc.id]
    received = [e for e in events if e.type is EventType.DOCUMENT_RECEIVED_BY_EMAIL]
    assert [(e.payload["inbound_mail_id"], e.payload["imported"]) for e in received] == [
        (str(mail.id), False)
    ]
    assert received[0].actor_type is ActorType.SYSTEM
    _assert_read_only(server)
    assert _part_fetches(server) == ["(BODY.PEEK[2])"]  # the body (part 1) is never fetched


def test_the_attachments_of_a_mail_forwarded_as_attachment_are_taken(inbox, app_engine):
    poll, server, ws, _ = inbox
    inner = EmailMessage()
    inner["From"] = "maler@example.net"
    inner["Subject"] = "Rechnung"
    inner.set_content("Text des Malers")
    inner.add_attachment(_pdf("nested"), maintype="application", subtype="pdf", filename="r.pdf")
    outer = EmailMessage()
    outer["Authentication-Results"] = "mx.google.com; spf=pass smtp.mailfrom=" + MEMBER
    outer["From"], outer["To"] = MEMBER, TOKEN
    outer["Message-ID"] = "<fwd@mail.example.org>"
    outer.set_content(BODY)
    outer.add_attachment(inner)  # message/rfc822

    server.add(LABEL, outer.as_bytes())
    result = poll()

    assert result.accepted == 1
    [doc] = _rows(app_engine, ws, document_table)
    assert doc.original_filename == "r.pdf"  # the nested PDF, never the forwarded mail itself
    assert _part_fetches(server) == ["(BODY.PEEK[2.2])"]
    _assert_read_only(server)


# --- Who may send ------------------------------------------------------------------------------


def test_a_strangers_mail_is_recorded_by_hash_and_never_fetched(inbox, app_engine):
    poll, server, ws, _ = inbox
    message_id = "<spam@mail.example.net>"
    server.add(
        LABEL,
        _mail(
            ("rechnung.pdf", _pdf("stranger"), "application/pdf"),
            sender=STRANGER,
            auth="dkim=pass header.d=example.net",
            message_id=message_id,
        ),
    )

    result = poll()

    assert (result.accepted, result.rejected) == (0, 1)
    [mail] = _rows(app_engine, ws, mail_table)
    assert mail.message_id_hash == hashlib.sha256(message_id.encode()).digest()
    assert (mail.decision, mail.code) == ("rejected", E.EMAIL_SENDER_NOT_ALLOWED)
    assert mail.attachments_total is None and mail.attachments_accepted is None
    assert _rows(app_engine, ws, document_table) == []
    assert _rows(app_engine, ws, link_table) == []
    assert _part_fetches(server) == []  # only the listing: BODYSTRUCTURE and header fields
    _assert_read_only(server)


def test_a_forged_authentication_result_of_another_server_is_ignored(inbox, app_engine):
    poll, server, ws, _ = inbox
    server.add(
        LABEL,
        _mail(
            ("rechnung.pdf", _pdf("forged"), "application/pdf"),
            auth=None,  # no Gmail header; the sender wrote one with a foreign authserv-id
        ).replace(b"From:", b"Authentication-Results: evil.example.net; dkim=pass\r\nFrom:", 1),
    )

    poll()

    [mail] = _rows(app_engine, ws, mail_table)
    assert (mail.decision, mail.code) == ("rejected", E.EMAIL_AUTH_MISSING)
    assert _part_fetches(server) == []


def test_a_part_above_the_upload_limit_is_rejected_unfetched(inbox, app_engine, tmp_path):
    """The accept limit is the smaller of 25 MiB and HAUSBUCH_MAX_UPLOAD_BYTES."""
    poll, server, ws, deps = inbox
    pdf = _pdf("too-large")
    small = JobDeps(
        deps.engine,
        _settings(tmp_path, max_upload_bytes=len(pdf) // 2),
        deps.blob_store,
        deps.model_client,
        clock=lambda: NOW,
    )
    server.add(LABEL, _mail(("gross.pdf", pdf, "application/pdf")))

    result = poll(small)

    assert (result.accepted, result.rejected) == (0, 1)
    [mail] = _rows(app_engine, ws, mail_table)
    assert (mail.decision, mail.code) == ("rejected", E.EMAIL_TOO_LARGE)
    assert _rows(app_engine, ws, document_table) == []
    assert _part_fetches(server) == []
    _assert_read_only(server)


def test_a_mail_without_a_message_id_is_neither_recorded_nor_fetched(inbox, app_engine):
    poll, server, ws, _ = inbox
    raw = _mail(("rechnung.pdf", _pdf("no-id"), "application/pdf"))
    raw = b"\n".join(line for line in raw.split(b"\n") if not line.startswith(b"Message-ID"))
    server.add(LABEL, raw)

    result = poll()

    assert (result.unrecorded, result.codes[E.EMAIL_NO_MESSAGE_ID]) == (1, 1)
    assert _rows(app_engine, ws, mail_table) == []
    assert _part_fetches(server) == []


# --- Statements ------------------------------------------------------------------------------


def _account(run, ws, *, owner=False):
    data = AccountCreate(
        label="Mietkonto",
        bank="Sparkasse",
        iban=SAMPLE_IBAN,
        opening_balance=Decimal("0.00"),
        opening_balance_on=date(2026, 3, 31),
        is_owner_account=owner,
    )
    return run(ws, create_account, data, iban_key=TEST_IBAN_KEY, now=NOW)


def test_a_forwarded_camt_zip_imports_to_its_account(inbox, app_engine, run):
    poll, server, ws, _ = inbox
    account = _account(run, ws)
    server.add(LABEL, _mail(("umsaetze.zip", ZIP, "application/zip")))

    result = poll()

    assert result.accepted == 1
    [link] = _rows(app_engine, ws, link_table)
    assert link.import_id is not None
    [imported] = _rows(app_engine, ws, import_table)
    assert (imported.id, imported.account_id) == (link.import_id, account.id)
    assert imported.document_id == link.document_id
    [doc] = _rows(app_engine, ws, document_table)
    assert (doc.source, doc.type) == (DocumentSource.EMAIL.value, DocumentType.STATEMENT.value)
    assert _rows(app_engine, ws, proposal_table) == []  # a statement is imported, not read
    with workspace_transaction(app_engine, ws) as conn:
        [received] = [
            e for e in list_events(conn) if e.type is EventType.DOCUMENT_RECEIVED_BY_EMAIL
        ]
        items = service.inbound_mail_items(conn, NOW)
    assert received.payload["imported"] is True
    assert [(i.kind, i.import_id) for i in items] == [(InboxItemKind.EMAIL_RECEIVED, imported.id)]


@pytest.mark.parametrize("owner", [None, True], ids=["no-account", "owner-account-only"])
def test_a_statement_no_account_takes_stays_a_typed_document_with_a_card(
    inbox, app_engine, run, owner
):
    poll, server, ws, _ = inbox
    if owner:
        _account(run, ws, owner=True)
    server.add(LABEL, _mail(("umsaetze.zip", ZIP, "application/zip")))

    poll()

    assert _rows(app_engine, ws, import_table) == []
    [link] = _rows(app_engine, ws, link_table)
    assert link.import_id is None
    [doc] = _rows(app_engine, ws, document_table)
    assert doc.type == DocumentType.STATEMENT.value
    assert _rows(app_engine, ws, proposal_table) == []
    with workspace_transaction(app_engine, ws) as conn:
        [item] = service.inbound_mail_items(conn, NOW + timedelta(days=3))  # past the 24 h
    assert (item.kind, item.document_id) == (InboxItemKind.EMAIL_RECEIVED, doc.id)
    assert "keinem Konto" in item.reasoning


# --- The tenant gate ------------------------------------------------------------------------


def _classify_as_lease() -> str:
    import json

    return json.dumps(
        {
            "type": "contract",
            "subtype": "lease",
            "contains_tenant_data": False,
            "language": "de",
            "confidence": "high",
        }
    )


def _read(deps, ws, engine, document_id) -> tuple[str, int]:
    """Run the document's queued document_read job with a model that says: a lease."""
    [proposal] = _rows(engine, ws, proposal_table, proposal_table.c.document_id == document_id)
    client = FakeModelClient([text_response(_classify_as_lease())])
    run_document_read(
        JobDeps(deps.engine, deps.settings, deps.blob_store, client, clock=lambda: NOW),
        workspace_id=ws,
        document_id=document_id,
        proposal_id=proposal.id,
        attempt=0,
    )
    [after] = _rows(engine, ws, proposal_table, proposal_table.c.id == proposal.id)
    return after.status, len(client.requests)


def test_a_forwarded_lease_is_blocked_by_the_tenant_gate_as_an_upload_is(
    inbox, app_engine, blob_store
):
    poll, server, ws, deps = inbox
    with workspace_transaction(app_engine, ws) as conn:  # the default, said explicitly
        conn.execute(
            update(workspace_table)
            .where(workspace_table.c.id == ws)
            .values(ai_tenant_documents=False)
        )
    server.add(LABEL, _mail(("mietvertrag.pdf", _pdf("lease-mail"), "application/pdf")))
    poll()
    [link] = _rows(app_engine, ws, link_table)

    # The same as POST /documents with ai on and no tenant flag (api/routes/documents._upload).
    with workspace_transaction(app_engine, ws) as conn:
        ctx = new_command(conn, ws, Actor(ActorType.USER, "user:test"), now=NOW)
        uploaded = upload_document(
            ctx,
            blob_store,
            _pdf("lease-upload"),
            media_type="application/pdf",
            source=DocumentSource.UPLOAD,
        )
        proposal_for_upload(ctx, uploaded.id, created=True, contains_tenant_data=False)

    by_mail = _read(deps, ws, app_engine, link.document_id)
    by_upload = _read(deps, ws, app_engine, uploaded.id)
    assert by_mail == by_upload == (ProposalStatus.BLOCKED_TENANT_DATA.value, 1)  # no extract


# --- Idempotence and attempts ---------------------------------------------------------------


def test_a_second_poll_records_and_fetches_nothing_new(inbox, app_engine):
    poll, server, ws, _ = inbox
    server.add(LABEL, _mail(("a.pdf", _pdf("twice"), "application/pdf")))
    server.add(LABEL, _mail(("b.pdf", _pdf("stranger-twice"), "application/pdf"), sender=STRANGER))
    poll()
    rows, docs = _rows(app_engine, ws, mail_table), _rows(app_engine, ws, document_table)
    part_fetches = len(_part_fetches(server))

    second = poll()

    assert (second.known, second.accepted, second.rejected) == (2, 0, 0)
    assert _rows(app_engine, ws, mail_table) == rows
    assert _rows(app_engine, ws, document_table) == docs
    assert len(_part_fetches(server)) == part_fetches
    _assert_read_only(server)


def test_a_failing_part_is_retried_three_times_then_carded(inbox, app_engine, monkeypatch):
    poll, server, ws, _ = inbox
    server.add(LABEL, _mail(("a.pdf", _pdf("fails"), "application/pdf")))

    def broken(*args, **kwargs):
        raise RuntimeError("storage down")

    monkeypatch.setattr(service, "store_attachment", broken)
    results = [poll(at=NOW + timedelta(minutes=10 * n)) for n in range(4)]

    assert [r.failed for r in results] == [1, 1, 1, 0]
    assert results[3].known == 1
    rows = sorted(_rows(app_engine, ws, mail_table), key=lambda r: r.attempt)
    assert [(r.attempt, r.decision, r.code) for r in rows] == [
        (n, "failed", E.EMAIL_FAILED) for n in (1, 2, 3)
    ]
    with workspace_transaction(app_engine, ws) as conn:
        items = service.inbound_mail_items(conn, NOW + timedelta(hours=1))
    assert [i.kind for i in items] == [InboxItemKind.EMAIL_FAILED]


def test_a_failed_attempt_then_a_good_one_links_each_document_once(inbox, app_engine, monkeypatch):
    poll, server, ws, _ = inbox
    server.add(
        LABEL,
        _mail(
            ("a.pdf", _pdf("ok-part"), "application/pdf"),
            ("b.pdf", _pdf("bad-part"), "application/pdf"),
        ),
    )
    real = service.store_attachment
    calls = {"n": 0}

    def second_part_fails_once(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("once")
        return real(*args, **kwargs)

    monkeypatch.setattr(service, "store_attachment", second_part_fails_once)
    first = poll()
    second = poll(at=NOW + timedelta(minutes=10))

    assert (first.failed, second.accepted) == (1, 1)
    rows = sorted(_rows(app_engine, ws, mail_table), key=lambda r: r.attempt)
    assert [(r.decision, r.attachments_accepted) for r in rows] == [("failed", 1), ("accepted", 2)]
    links = _rows(app_engine, ws, link_table)
    assert len(links) == 2 and len({link.document_id for link in links}) == 2
    with workspace_transaction(app_engine, ws) as conn:
        events = [e for e in list_events(conn) if e.type is EventType.DOCUMENT_RECEIVED_BY_EMAIL]
    assert len(events) == 2


def test_a_connection_lost_mid_mail_records_nothing(inbox, app_engine):
    poll, server, ws, _ = inbox
    server.add(LABEL, _mail(("a.pdf", _pdf("dropped"), "application/pdf")))
    server.drop_after_fetches = 1  # the listing works, the part's fetch drops

    result = poll()

    assert result.status == "connection_lost"
    assert _rows(app_engine, ws, mail_table) == []  # no attempt spent
    server.drop_after_fetches = None
    assert poll().accepted == 1


def test_a_refused_part_fails_its_mail_and_the_next_mail_is_taken(inbox, app_engine):
    """A NO to one part's FETCH is that mail's failed attempt, not a lost connection."""
    poll, server, ws, _ = inbox
    bad = server.add(LABEL, _mail(("a.pdf", _pdf("refused-part"), "application/pdf")))
    server.add(LABEL, _mail(("b.pdf", _pdf("healthy-after-part"), "application/pdf")))
    server.refuse_part_uids = {bad.uid}

    result = poll()

    assert (result.status, result.accepted, result.failed) == ("done", 1, 1)
    rows = _rows(app_engine, ws, mail_table)
    assert sorted((r.decision, r.code, r.attempt) for r in rows) == [
        ("accepted", E.EMAIL_ACCEPTED, 1),
        ("failed", E.EMAIL_FAILED, 1),
    ]
    [link] = _rows(app_engine, ws, link_table)
    [doc] = _rows(app_engine, ws, document_table)
    assert (link.document_id, doc.original_filename) == (doc.id, "b.pdf")
    _assert_read_only(server)


@pytest.mark.parametrize("how", ["refused", "unreadable"])
def test_a_bad_listing_batch_is_fetched_again_uid_by_uid(inbox, app_engine, how):
    """A NO (or an answer the parser cannot read) for the listing of a batch would hide every
    mail of the batch: each UID is fetched alone, and the one that still fails is skipped."""
    poll, server, ws, _ = inbox
    bad = server.add(LABEL, _mail(("a.pdf", _pdf(f"listing-{how}"), "application/pdf")))
    server.add(LABEL, _mail(("b.pdf", _pdf(f"healthy-{how}"), "application/pdf")))
    if how == "refused":
        server.refuse_listing_uids = {bad.uid}
    else:
        bad.bodystructure = bad.bodystructure[:-1]  # one ')' short: the parser raises

    result = poll()

    assert (result.status, result.accepted, result.unreadable) == ("done", 1, 1)
    [mail] = _rows(app_engine, ws, mail_table)  # nothing recorded for the bad one
    assert mail.decision == "accepted"
    [doc] = _rows(app_engine, ws, document_table)
    assert doc.original_filename == "b.pdf"
    listings = [uids for uids, items in server.fetches if "HEADER.FIELDS" in items]
    assert len(listings) == 3  # the batch, then each UID alone
    _assert_read_only(server)


# --- Poll-level outcomes -----------------------------------------------------------------------


def test_a_missing_label_fails_the_poll_and_creates_nothing(inbox, app_engine):
    poll, server, ws, _ = inbox
    server.labels = {}

    result = poll()

    assert result.status == E.EMAIL_LABEL_MISSING
    assert "CREATE" not in server.commands and server.violations == []
    assert server.commands[-1] == "LOGOUT"


def test_a_revoked_app_password_fails_the_poll(inbox):
    poll, server, _, _ = inbox
    server.password = "revoked"

    result = poll()

    assert result.status == E.EMAIL_AUTH_REJECTED
    assert "EXAMINE" not in server.commands and server.violations == []


_POLL_EVENTS = (EventType.WORKSPACE_INBOX_POLL_FAILED, EventType.WORKSPACE_INBOX_POLL_RECOVERED)


def _poll_events(engine, ws) -> list[tuple[str, dict]]:
    with workspace_transaction(engine, ws) as conn:
        return [(e.type.value, e.payload) for e in list_events(conn) if e.type in _POLL_EVENTS]


def _poll_cards(engine, ws, at=NOW) -> list:
    with workspace_transaction(engine, ws) as conn:
        items = review.inbox(conn, today=at.date(), now=at)
    return [i for i in items if i.kind is InboxItemKind.EMAIL_INBOX_FAILED]


def test_a_revoked_app_password_shows_a_card_until_a_poll_logs_in_again(inbox, app_engine):
    """§5.5: the revoke path ends in a card. One event per change, not one per poll."""
    poll, server, ws, _ = inbox
    server.password = "revoked"

    for n in range(3):
        assert poll(at=NOW + timedelta(minutes=10 * n)).status == E.EMAIL_AUTH_REJECTED

    assert _poll_events(app_engine, ws) == [
        ("workspace.inbox_poll_failed", {"code": E.EMAIL_AUTH_REJECTED})
    ]
    [card] = _poll_cards(app_engine, ws, NOW + timedelta(hours=1))
    assert card.created_at == NOW
    assert "nicht mehr bei Gmail anmelden" in card.reasoning
    assert card.question == "Ein neues App-Passwort erstellen und als Secret hinterlegen."
    for text in (IMAP_USER, IMAP_PASSWORD, TOKEN, LABEL):
        assert text not in f"{card.reasoning} {card.question}"

    server.password = IMAP_PASSWORD  # a new app password, pasted as the secret
    assert poll(at=NOW + timedelta(minutes=40)).status == "done"
    assert poll(at=NOW + timedelta(minutes=50)).status == "done"

    assert _poll_events(app_engine, ws) == [
        ("workspace.inbox_poll_failed", {"code": E.EMAIL_AUTH_REJECTED}),
        ("workspace.inbox_poll_recovered", {"previous_code": E.EMAIL_AUTH_REJECTED}),
    ]
    assert _poll_cards(app_engine, ws, NOW + timedelta(hours=1)) == []


def test_a_missing_label_shows_its_own_card(inbox, app_engine):
    poll, server, ws, _ = inbox
    server.password = "revoked"
    poll()
    server.password, server.labels = IMAP_PASSWORD, {}  # logged in again, but no label
    poll(at=NOW + timedelta(minutes=10))

    assert _poll_events(app_engine, ws) == [
        ("workspace.inbox_poll_failed", {"code": E.EMAIL_AUTH_REJECTED}),
        ("workspace.inbox_poll_failed", {"code": E.EMAIL_LABEL_MISSING}),
    ]
    [card] = _poll_cards(app_engine, ws, NOW + timedelta(hours=1))
    assert "Gmail-Label nicht" in card.reasoning


def test_a_completed_poll_without_an_earlier_failure_writes_nothing(inbox, app_engine):
    poll, _, ws, _ = inbox
    assert poll().status == "done"
    assert _poll_events(app_engine, ws) == []
    assert _poll_cards(app_engine, ws) == []


def test_the_server_unreachable_three_polls_in_a_row_shows_a_card(inbox, app_engine):
    poll, server, ws, deps = inbox

    def unreachable(settings):
        raise OSError("network down")

    def attempt(n, connect):
        at = NOW + timedelta(minutes=10 * n)
        d = JobDeps(deps.engine, deps.settings, deps.blob_store, deps.model_client, lambda: at)
        return job.run_poll_inbox(d, connect=connect, workspace_id=ws).status

    # Two failed connects, then one that works: a blip, no card, and the run starts again.
    assert [attempt(n, unreachable) for n in range(2)] == ["connect_failed"] * 2
    assert attempt(2, lambda s: FakeImap(server)) == "done"
    assert [attempt(n, unreachable) for n in range(3, 5)] == ["connect_failed"] * 2
    assert _poll_events(app_engine, ws) == []

    assert [attempt(n, unreachable) for n in range(5, 8)] == ["connect_failed"] * 3

    assert _poll_events(app_engine, ws) == [
        ("workspace.inbox_poll_failed", {"code": E.EMAIL_UNREACHABLE})
    ]
    [card] = _poll_cards(app_engine, ws, NOW + timedelta(hours=2))
    assert card.created_at == NOW + timedelta(minutes=50)  # the third in a row
    assert attempt(8, lambda s: FakeImap(server)) == "done"
    assert _poll_cards(app_engine, ws, NOW + timedelta(hours=2)) == []


def test_the_poll_is_off_until_configured_and_refuses_imaplib_debug(inbox, tmp_path, monkeypatch):
    poll, _, _, deps = inbox
    off = JobDeps(
        deps.engine,
        _settings(tmp_path, inbox_imap_user=None, inbox_imap_password=None),
        deps.blob_store,
        None,
    )
    assert poll(off).status == "not_configured"
    monkeypatch.setattr(imaplib, "Debug", 4)
    assert poll().status == "skipped"
    assert poll.connects == []  # neither connected


def test_nothing_of_a_mail_or_a_secret_reaches_a_log(inbox, caplog, capsys):
    """D121, §5.5: the log line carries counts and codes. imaplib.Debug stays 0 (it would
    print the LOGIN line with the password to stderr)."""
    poll, server, _, _ = inbox
    assert imaplib.Debug == 0
    server.add(LABEL, _mail(("rechnung-maler.pdf", _pdf("log-a"), "application/pdf")))
    server.add(LABEL, _mail(("x.pdf", _pdf("log-b"), "application/pdf"), sender=STRANGER))
    server.add(LABEL, _mail(("y.pdf", _pdf("log-c"), "application/pdf"), to="other@example.com"))
    caplog.set_level(logging.DEBUG)

    result = poll()

    assert (result.accepted, result.rejected) == (1, 2)
    logged = "\n".join(f"{record.getMessage()} {record.__dict__}" for record in caplog.records)
    printed = capsys.readouterr()
    secrets = [
        MEMBER,
        STRANGER,
        IMAP_USER,
        IMAP_PASSWORD,
        TOKEN,
        "x7k2q9",
        "other@example.com",
        SUBJECT,
        "Vertraulicher",
        "rechnung-maler",
        "mail.example.org",
    ]
    for secret in secrets:
        assert secret not in logged, secret
        assert secret not in printed.out and secret not in printed.err, secret
    [line] = [r for r in caplog.records if r.name == job.logger.name]
    assert line.fields["event"] == "inbound_mail_polled"
    assert line.fields["codes"] == {
        E.EMAIL_ACCEPTED: 1,
        E.EMAIL_RECIPIENT_MISMATCH: 1,
        E.EMAIL_SENDER_NOT_ALLOWED: 1,
    }


def test_the_inbox_lists_received_attachments_for_a_day(inbox, app_engine):
    poll, server, ws, _ = inbox
    server.add(LABEL, _mail(("a.pdf", _pdf("card"), "application/pdf")))
    poll()
    [link] = _rows(app_engine, ws, link_table)

    with workspace_transaction(app_engine, ws) as conn:
        today = review.inbox(conn, today=NOW.date(), now=NOW + timedelta(hours=23))
        later = review.inbox(conn, today=NOW.date(), now=NOW + timedelta(hours=25))
    received = [i for i in today if i.kind is InboxItemKind.EMAIL_RECEIVED]
    assert [i.document_id for i in received] == [link.document_id]
    assert [i for i in later if i.kind is InboxItemKind.EMAIL_RECEIVED] == []
