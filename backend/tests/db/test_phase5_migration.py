"""Migration 0019 (docs/phases/phase-5.md §8): bank_connection and bank_sync (mutable), the four
append-only tables (inbound_mail, inbound_mail_attachment, reminder_ack, notification), the
`bank` balance source, the grundsteuer_schedule tax input, workspace.reminder_emails, the
grants and the downgrade guard.

RLS, workspace_id and the table list are covered for every table by test_migration.py; the
enum CHECKs (bank_sync.trigger/status/balance_source, inbound_mail.decision,
reminder_ack.scope_type/action, notification.channel/status, statement_import.balance_source,
tax_input.kind) by test_schema_consistency.py. Synthetic data only."""

import hashlib
import uuid

import pytest
from db.db_support import (
    APP,
    OWNER,
    _admin,
    _insert,
    alembic,
    create_database,
    drop_database,
    insert_property,
)
from psycopg import errors
from psycopg.types.json import Jsonb
from sqlalchemy.exc import DBAPIError

from hausbuch.schemas.tax import TaxInputCreate

PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
EXPECTED_GRANTS = {
    "bank_connection": {"SELECT", "INSERT", "UPDATE"},  # mutable + events
    "bank_sync": {"SELECT", "INSERT", "UPDATE"},  # mutable + events
    "inbound_mail": {"SELECT", "INSERT"},  # append-only
    "inbound_mail_attachment": {"SELECT", "INSERT"},
    "reminder_ack": {"SELECT", "INSERT"},
    "notification": {"SELECT", "INSERT"},
}
PHASE5_TABLES = tuple(EXPECTED_GRANTS)
APPEND_ONLY = ("inbound_mail", "inbound_mail_attachment", "reminder_ack", "notification")
HEAD = "0019_automation"
PREVIOUS = "0018_income_tax_return"

# D119: no column may hold a secret. tan_mechanism is the bank's security-function code
# (e.g. 946 for pushTAN), never a TAN.
SECRET_SHAPED = ("pin", "tan", "password", "token", "secret", "imap", "product")


@pytest.mark.parametrize("table", sorted(EXPECTED_GRANTS))
def test_app_role_grants(connect, table):
    with connect(OWNER) as conn:
        granted = {
            p
            for p in PRIVILEGES
            if conn.execute("SELECT has_table_privilege(%s, %s, %s)", (APP, table, p)).fetchone()[0]
        }
        assert granted == EXPECTED_GRANTS[table]


def _columns(conn, table) -> set[str]:
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = 'public' AND table_name = %s",
        (table,),
    )
    return {r[0] for r in rows}


def test_no_column_is_named_like_a_secret(connect):
    with connect(OWNER) as conn:
        found = {
            (table, column)
            for table in PHASE5_TABLES
            for column in _columns(conn, table)
            if column != "tan_mechanism" and any(word in column for word in SECRET_SHAPED)
        }
    assert not found


def test_inbound_mail_stores_no_address_subject_body_or_header(connect):
    """D121, §5.5: the hash of the Message-ID, the time, the decision and the counts only."""
    with connect(OWNER) as conn:
        assert _columns(conn, "inbound_mail") == {
            "id",
            "workspace_id",
            "message_id_hash",
            "attempt",
            "received_at",
            "decision",
            "code",
            "attachments_total",
            "attachments_accepted",
            "recorded_at",
        }


# --- Seed helpers ----------------------------------------------------------------------


def _document(conn, ws, **overrides) -> uuid.UUID:
    digest = hashlib.sha256(uuid.uuid4().bytes).digest()
    conn.execute(
        "INSERT INTO document_file (workspace_id, sha256, size_bytes, media_type, storage_key)"
        " VALUES (%s, %s, 11, 'application/zip', %s)",
        (ws, digest, f"{ws}/sha256/{digest.hex()}"),
    )
    row = {"workspace_id": ws, "file_sha256": digest, "source": "upload"} | overrides
    return _insert(conn, "document", row)


def _user(conn, ws) -> uuid.UUID:
    return _insert(
        conn,
        "app_user",
        {"workspace_id": ws, "email": f"{uuid.uuid4().hex}@example.org", "display_name": "A"},
    )


def _account(conn, ws, **overrides) -> uuid.UUID:
    row = {
        "workspace_id": ws,
        "label": "Mietkonto",
        "bank": "Testbank",
        "iban_masked": "DE00 **** 0000",
        "iban_hmac": hashlib.sha256(uuid.uuid4().bytes).digest(),
        "opening_balance": "1000.00",
        "opening_balance_on": "2026-03-31",
    } | overrides
    return _insert(conn, "account", row)


def _import(conn, ws, account_id, **overrides) -> uuid.UUID:
    row = {
        "workspace_id": ws,
        "account_id": account_id,
        "document_id": _document(conn, ws),
        "format": "camt",
    } | overrides
    return _insert(conn, "statement_import", row)


def _connection(ws, account_id, **overrides) -> dict:
    return {
        "workspace_id": ws,
        "account_id": account_id,
        "bank_code": "12345678",
        "fints_url": "https://banking.example/fints",
        "login_name": "anmeldename42",
    } | overrides


def _sync(ws, account_id, connection_id, **overrides) -> dict:
    return {
        "workspace_id": ws,
        "account_id": account_id,
        "connection_id": connection_id,
        "trigger": "manual",
    } | overrides


def _mail(ws, **overrides) -> dict:
    return {
        "workspace_id": ws,
        "message_id_hash": hashlib.sha256(uuid.uuid4().bytes).digest(),
        "attempt": 1,
        "decision": "rejected",
        "code": "email.auth_failed",
    } | overrides


def _ack(ws, **overrides) -> dict:
    return {
        "workspace_id": ws,
        "reminder_key": "tax.return_deadline",
        "scope_type": "workspace",
        "due_on": "2027-08-02",
        "action": "done",
    } | overrides


def _notification(ws, user, **overrides) -> dict:
    return {
        "workspace_id": ws,
        "reminder_id": "tax.return_deadline:workspace:2027-08-02",
        "lead_days": 60,
        "channel": "email",
        "recipient_user_id": user,
        "status": "sent",
        "attempt": 1,
    } | overrides


@pytest.fixture
def scope(connect, workspaces):
    """(conn, workspace, ids) as the app role, rolled back after the test."""
    a, _ = workspaces
    with connect(APP, a) as conn:
        account = _account(conn, a)
        ids = {"account": account, "user": _user(conn, a), "other_account": _account(conn, a)}
        ids["connection"] = _insert(conn, "bank_connection", _connection(a, account))
        yield conn, a, ids


# --- bank_connection -------------------------------------------------------------------


def test_a_connection_defaults_and_one_per_account(scope):
    conn, a, ids = scope
    row = conn.execute(
        "SELECT auto_sync, client_data, tan_mechanism FROM bank_connection WHERE id = %s",
        (ids["connection"],),
    ).fetchone()
    assert row == (False, None, None)
    with pytest.raises(errors.UniqueViolation), conn.transaction():
        _insert(conn, "bank_connection", _connection(a, ids["account"]))
    mac = hashlib.sha256(b"state").digest()
    _insert(
        conn,
        "bank_connection",
        _connection(
            a,
            ids["other_account"],
            tan_mechanism="946",
            client_data=b"opaque",
            client_data_mac=mac,
        ),
    )


@pytest.mark.parametrize(
    "change",
    [
        {"bank_code": "1234567"},  # seven digits
        {"bank_code": "1234567X"},
        {"fints_url": "http://banking.example/fints"},  # https only
        {"fints_url": "https://" + "x" * 500},
        {"login_name": ""},
        {"login_name": "x" * 101},
        {"tan_mechanism": "123456789"},  # ≤ 8
        {"client_data": b"opaque"},  # the state is stored only with its MAC (D119)
        {"client_data_mac": hashlib.sha256(b"x").digest()},
        {"client_data": b"opaque", "client_data_mac": b"short"},  # HMAC-SHA256 is 32 bytes
    ],
)
def test_a_connection_outside_its_checks_is_refused(scope, change):
    conn, a, ids = scope
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(conn, "bank_connection", _connection(a, ids["other_account"], **change))


def test_a_connection_is_updatable_but_not_deletable_by_the_app(scope):
    conn, _, ids = scope
    conn.execute(
        "UPDATE bank_connection SET auto_sync = true, last_sync_at = now() WHERE id = %s",
        (ids["connection"],),
    )
    with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
        conn.execute("DELETE FROM bank_connection WHERE id = %s", (ids["connection"],))


def test_a_connection_is_invisible_to_another_workspace(connect, workspaces):
    a, b = workspaces
    with connect(APP, a) as conn:
        _insert(conn, "bank_connection", _connection(a, _account(conn, a)))
        assert conn.execute("SELECT count(*) FROM bank_connection").fetchone() == (1,)
        conn.execute("SELECT set_config('app.workspace_id', %s, false)", (str(b),))
        assert conn.execute("SELECT count(*) FROM bank_connection").fetchone() == (0,)
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():  # WITH CHECK
            _insert(conn, "bank_connection", _connection(a, uuid.uuid4()))


# --- bank_sync -------------------------------------------------------------------------


def test_a_sync_runs_from_queued_to_done_with_its_import(scope):
    conn, a, ids = scope
    sync = _insert(
        conn,
        "bank_sync",
        _sync(a, ids["account"], ids["connection"], created_by=ids["user"]),
    )
    status, checked = conn.execute(
        "SELECT status, balance_checked FROM bank_sync WHERE id = %s", (sync,)
    ).fetchone()
    assert (status, checked) == ("queued", False)
    statement = _import(conn, a, ids["account"], balance_source="bank", declared_closing="1100.00")
    conn.execute(
        "UPDATE bank_sync SET status = 'running', started_at = now(),"
        " requested_from = '2026-04-01', requested_to = '2026-11-09' WHERE id = %s",
        (sync,),
    )
    conn.execute(
        "UPDATE bank_sync SET status = 'importing', import_id = %s, lines_total = 5,"
        " lines_new = 2, balance_source = 'bank' WHERE id = %s",
        (statement, sync),
    )
    conn.execute(
        "UPDATE bank_sync SET status = 'done', finished_at = now(), balance_checked = true"
        " WHERE id = %s",
        (sync,),
    )


@pytest.mark.parametrize(
    "change",
    [
        {"requested_from": "2026-11-09", "requested_to": "2026-04-01"},
        {"requested_from": "2026-04-01"},  # both bounds or neither
        {"lines_total": 1, "lines_new": 2},
        {"lines_total": -1},
        {"status": "done"},  # a final status has finished_at
        {"status": "failed", "error_code": "bank.refused"},
        {"status": "running", "finished_at": "2026-11-09T08:00:00Z"},  # and only a final one
        {"status": "nothing_new", "lines_total": 3, "lines_new": 1, "finished_at": "now()"},
        {"balance_source": "typed"},
        {"trigger": "webhook"},
        {"error_code": "bank." + "x" * 60},
        {
            "status": "failed",
            "started_at": "2026-11-09T09:00:00Z",
            "finished_at": "2026-11-09T08:00:00Z",
        },
    ],
)
def test_a_sync_outside_its_checks_is_refused(scope, change):
    conn, a, ids = scope
    if change.get("finished_at") == "now()":
        change["finished_at"] = "2026-11-09T08:00:00Z"
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(conn, "bank_sync", _sync(a, ids["account"], ids["connection"], **change))


def test_a_sync_import_is_only_for_a_run_with_new_lines(scope):
    """D127: importing and done carry their import; a run that failed after writing it keeps
    the link to the lines it stored; queued and nothing_new never have one."""
    conn, a, ids = scope
    statement = _import(conn, a, ids["account"])
    for status, finished in (
        ("importing", None),
        ("done", "2026-11-09T08:00:00Z"),
        ("failed", "2026-11-09T08:00:00Z"),
    ):
        _insert(
            conn,
            "bank_sync",
            _sync(
                a,
                ids["account"],
                ids["connection"],
                status=status,
                import_id=statement,
                finished_at=finished,
                **({"error_code": "statement.not_reconciled"} if status == "failed" else {}),
            ),
        )
    for status, finished in (("queued", None), ("nothing_new", "2026-11-09T08:00:00Z")):
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _insert(
                conn,
                "bank_sync",
                _sync(
                    a,
                    ids["account"],
                    ids["connection"],
                    status=status,
                    import_id=statement,
                    finished_at=finished,
                ),
            )


def test_a_sync_connection_and_import_are_the_same_accounts(scope):
    conn, a, ids = scope
    other_connection = _insert(conn, "bank_connection", _connection(a, ids["other_account"]))
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
        _insert(conn, "bank_sync", _sync(a, ids["account"], other_connection))
    other_import = _import(conn, a, ids["other_account"])
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
        _insert(
            conn,
            "bank_sync",
            _sync(a, ids["account"], ids["connection"], status="importing", import_id=other_import),
        )


def test_an_import_may_declare_the_banks_balance(scope):
    conn, a, ids = scope
    _import(conn, a, ids["account"], balance_source="bank", declared_closing="1100.00")
    with pytest.raises(errors.CheckViolation), conn.transaction():  # a balance is declared
        _import(conn, a, ids["account"], balance_source="bank")


# --- The append-only tables ------------------------------------------------------------


def test_inbound_mail_checks_and_one_row_per_attempt(scope):
    conn, a, _ = scope
    row = _mail(a)
    _insert(conn, "inbound_mail", row)
    _insert(conn, "inbound_mail", row | {"attempt": 2, "decision": "failed", "code": None})
    with pytest.raises(errors.UniqueViolation), conn.transaction():
        _insert(conn, "inbound_mail", row)
    for bad in (
        _mail(a, message_id_hash=b"short"),
        _mail(a, attempt=0),
        _mail(a, attempt=4),
        _mail(a, decision="spam"),
        _mail(a, code="Absender nicht erlaubt"),  # a code, never free text
        _mail(a, attachments_total=1, attachments_accepted=2),
        _mail(a, attachments_total=-1),
    ):
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _insert(conn, "inbound_mail", bad)


def test_an_attachment_names_its_mail_document_and_import(scope):
    conn, a, ids = scope
    mail = _insert(conn, "inbound_mail", _mail(a, decision="accepted", code="email.accepted"))
    document = _document(conn, a)
    statement = _import(conn, a, ids["account"], document_id=document)
    row = {"workspace_id": a, "mail_id": mail, "document_id": document, "import_id": statement}
    _insert(conn, "inbound_mail_attachment", row)
    with pytest.raises(errors.UniqueViolation), conn.transaction():
        _insert(conn, "inbound_mail_attachment", row)
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
        _insert(conn, "inbound_mail_attachment", row | {"document_id": uuid.uuid4()})
    with pytest.raises(errors.NotNullViolation), conn.transaction():
        _insert(conn, "inbound_mail_attachment", row | {"document_id": None})


def test_reminder_ack_shapes(scope):
    conn, a, ids = scope
    loan_id = uuid.uuid4()  # scope_id is polymorphic: no FK
    _insert(conn, "reminder_ack", _ack(a))
    _insert(conn, "reminder_ack", _ack(a, action="snoozed", snoozed_until="2027-07-01"))
    _insert(
        conn,
        "reminder_ack",
        _ack(
            a,
            reminder_key="loan.fixed_rate_end",
            scope_type="loan",
            scope_id=loan_id,
            due_on=None,
            action="muted",
            recorded_by=ids["user"],
        ),
    )
    for bad in (
        _ack(a, scope_id=loan_id),  # the workspace scope has no id
        _ack(a, scope_type="loan"),  # every other scope has one
        _ack(a, action="snoozed"),  # snoozed needs its date
        _ack(a, snoozed_until="2027-07-01"),  # and only snoozed has one
        _ack(a, action="muted"),  # muted is for every instance: no due_on
        _ack(a, due_on=None),  # done is for one instance
        _ack(a, reminder_key="Steuererklärung"),
        _ack(a, reminder_key="tax." + "x" * 61),
        _ack(a, scope_type="tenant", scope_id=loan_id),
    ):
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _insert(conn, "reminder_ack", bad)


def test_a_notification_is_sent_once_per_item_lead_and_recipient(scope):
    """At most one sent row per item, lead, channel and recipient; a failed send is retried
    with the next attempt, three in all, and two rows never claim one attempt."""
    conn, a, ids = scope
    row = _notification(a, ids["user"])
    _insert(conn, "notification", row)
    _insert(conn, "notification", row | {"lead_days": 14})
    with pytest.raises(errors.UniqueViolation), conn.transaction():  # a second success
        _insert(conn, "notification", row | {"attempt": 2})

    failed = row | {"lead_days": 7, "status": "failed", "error_code": "notification.failed"}
    _insert(conn, "notification", failed)
    _insert(conn, "notification", failed | {"attempt": 2})
    with pytest.raises(errors.UniqueViolation), conn.transaction():  # attempt 2 claimed twice
        _insert(conn, "notification", failed | {"attempt": 2})
    _insert(conn, "notification", row | {"lead_days": 7, "attempt": 3})  # the retry succeeded
    for attempt in (0, 4):
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _insert(conn, "notification", failed | {"lead_days": 3, "attempt": attempt})

    for bad in (
        row | {"lead_days": 0, "status": "failed"},  # a failure names its code
        row | {"lead_days": 0, "error_code": "notification.failed"},  # a success names none
        row | {"lead_days": 0, "channel": "push"},
        row | {"lead_days": 0, "reminder_id": "x" * 161},
    ):
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _insert(conn, "notification", bad)
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
        _insert(conn, "notification", row | {"lead_days": 0, "recipient_user_id": uuid.uuid4()})


def _seed_append_only(conn, ws) -> dict[str, uuid.UUID]:
    account = _account(conn, ws)
    mail = _insert(conn, "inbound_mail", _mail(ws, decision="accepted", code="email.accepted"))
    document = _document(conn, ws)
    return {
        "inbound_mail": mail,
        "inbound_mail_attachment": _insert(
            conn,
            "inbound_mail_attachment",
            {
                "workspace_id": ws,
                "mail_id": mail,
                "document_id": document,
                "import_id": _import(conn, ws, account, document_id=document),
            },
        ),
        "reminder_ack": _insert(conn, "reminder_ack", _ack(ws)),
        "notification": _insert(conn, "notification", _notification(ws, _user(conn, ws))),
    }


def test_the_four_new_append_only_tables_refuse_change_even_for_the_owner(connect, workspaces):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        rows = _seed_append_only(conn, a)
        for table in APPEND_ONLY:
            for statement in (
                f"UPDATE {table} SET recorded_at = now() WHERE id = %s"
                if table != "notification"
                else "UPDATE notification SET sent_at = now() WHERE id = %s",
                f"DELETE FROM {table} WHERE id = %s",
            ):
                with (
                    pytest.raises(errors.RestrictViolation, match="append-only"),
                    conn.transaction(),
                ):
                    conn.execute(statement, (rows[table],))
            with pytest.raises(errors.RestrictViolation, match="append-only"), conn.transaction():
                conn.execute(f"TRUNCATE {table} CASCADE")


# --- tax_input grundsteuer_schedule, workspace.reminder_emails ---------------------------

SCHEDULE = {
    "annual_amount": "400.00",
    "instalments": [
        {"due_on": "2027-02-15", "amount": "100.00"},
        {"due_on": "2027-05-15", "amount": "100.00"},
        {"due_on": "2027-08-15", "amount": "100.00"},
        {"due_on": "2027-11-15", "amount": "100.00"},
    ],
    "rhythm": "quarterly",
}


def _schedule_row(ws, prop, document, **overrides) -> dict:
    return {
        "workspace_id": ws,
        "property_id": prop,
        "kind": "grundsteuer_schedule",
        "tax_year": 2027,
        "document_id": document,
        "value": Jsonb(SCHEDULE),
    } | overrides


def test_a_grundsteuer_schedule_is_year_scoped_and_needs_the_bescheid(scope):
    conn, a, _ = scope
    prop = insert_property(conn, a)
    document = _document(conn, a)
    row = _schedule_row(a, prop, document)
    _insert(conn, "tax_input", row)
    TaxInputCreate.model_validate(
        {k: v.obj if isinstance(v, Jsonb) else v for k, v in row.items() if k != "workspace_id"}
    )
    for bad in (
        _schedule_row(a, prop, document, tax_year=None),
        _schedule_row(a, prop, None),
    ):
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _insert(conn, "tax_input", bad)


def test_reminder_emails_starts_off(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        assert conn.execute("SELECT reminder_emails FROM workspace").fetchone() == (False,)


# --- The downgrade guard ----------------------------------------------------------------

DATA = [
    *PHASE5_TABLES,
    "bank_balance_source",
    "grundsteuer_schedule",
    "fints_account",
    "reminder_emails",
]


def _seed(conn, ws, data) -> None:
    account = _account(conn, ws)
    if data in ("bank_connection", "bank_sync"):
        connection = _insert(conn, "bank_connection", _connection(ws, account))
        if data == "bank_sync":
            _insert(conn, "bank_sync", _sync(ws, account, connection))
    elif data in APPEND_ONLY:
        rows = _seed_append_only(conn, ws)
        assert data in rows
    elif data == "bank_balance_source":
        _import(conn, ws, account, balance_source="bank", declared_closing="1100.00")
    elif data == "grundsteuer_schedule":
        _insert(
            conn, "tax_input", _schedule_row(ws, insert_property(conn, ws), _document(conn, ws))
        )
    elif data == "fints_account":
        conn.execute("UPDATE account SET source = 'fints' WHERE id = %s", (account,))
    elif data == "reminder_emails":
        conn.execute("UPDATE workspace SET reminder_emails = true WHERE id = %s", (ws,))


def _new_workspace(name) -> uuid.UUID:
    ws = uuid.uuid4()
    with _admin(name) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'A')", (ws,))
    return ws


def _as_owner(conn, ws) -> None:
    conn.execute(f"SET ROLE {OWNER}")
    conn.execute("SELECT set_config('app.workspace_id', %s, false)", (str(ws),))


@pytest.mark.parametrize("data", DATA)
def test_0019_downgrade_refuses_while_it_would_drop_data(data):
    """A downgrade never drops data; the guard must see rows under forced RLS."""
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            _seed(conn, ws, data)
        with pytest.raises(DBAPIError, match=f"downgrade of {HEAD} refused") as refused:
            alembic(name, "downgrade", PREVIOUS)
        # the guard's own exception, not a syntax error that quotes the guard's text
        assert isinstance(refused.value.orig, errors.RestrictViolation)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
            for table in (*PHASE5_TABLES, "statement_import", "tax_input", "account"):
                forced = conn.execute(
                    "SELECT relforcerowsecurity FROM pg_class WHERE oid = %s::regclass", (table,)
                ).fetchone()
                assert forced == (True,)  # the refused downgrade's NO FORCE was rolled back
    finally:
        drop_database(name)


def test_0019_downgrade_after_the_mutable_data_is_cleared_restores_the_old_checks():
    """A fints account and the reminder switch are mutable: once undone, the downgrade drops
    the six tables and the column and restores the 0018 CHECKs; the upgrade then runs again."""
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            _seed(conn, ws, "fints_account")
            _seed(conn, ws, "reminder_emails")
            account = conn.execute("SELECT id FROM account LIMIT 1").fetchone()[0]
        with pytest.raises(DBAPIError, match=f"downgrade of {HEAD} refused") as refused:
            alembic(name, "downgrade", PREVIOUS)
        # the guard's own exception, not a syntax error that quotes the guard's text
        assert isinstance(refused.value.orig, errors.RestrictViolation)
        with _admin(name) as conn:
            conn.execute("UPDATE account SET source = 'upload'")
            conn.execute("UPDATE workspace SET reminder_emails = false")
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            tables = conn.execute(
                "SELECT count(*) FROM pg_tables WHERE tablename = ANY(%s)", (list(PHASE5_TABLES),)
            ).fetchone()
            assert tables == (0,)
            column = conn.execute(
                "SELECT 1 FROM information_schema.columns"
                " WHERE table_name = 'workspace' AND column_name = 'reminder_emails'"
            ).fetchone()
            assert column is None
            _as_owner(conn, ws)
            with pytest.raises(errors.CheckViolation):
                _import(conn, ws, account, balance_source="bank", declared_closing="1.00")
            with pytest.raises(errors.CheckViolation):
                _insert(
                    conn,
                    "tax_input",
                    _schedule_row(ws, insert_property(conn, ws), _document(conn, ws)),
                )
        alembic(name, "upgrade", "head")
    finally:
        drop_database(name)
