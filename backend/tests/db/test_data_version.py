"""Phase 10 §7.4: the workspace data version every committed write moves (migration 0050).

The API keeps a computed answer only under the version it was computed at, so a write that
does not move the version would leave a stale answer on screen. These tests pin: insert,
update and delete each bump; one bump per transaction; a workspace's write never moves
another's; every table with workspace_id carries the three mark triggers (a table added
later without them fails here); a bump row-level security refuses raises instead of being
skipped; the excluded session table never bumps; the version can only grow; the pending
table is empty after every commit; and a written table stays free for TRUNCATE and ALTER
TABLE in the same transaction (the deferred event sits on the pending table only). Since
migration 0051 (phase 10 §7.5 (a)): a sign-in's own event and the passkey events move no
version, and since migration 0055 neither do the sign-out events; every other event still does.
"""

import os
import uuid
from datetime import UTC, datetime

import psycopg
import pytest
from db.db_support import ADMIN_URL, APP, _admin, insert_event, insert_property, insert_unit
from psycopg import sql
from sqlalchemy import text

from hausbuch.auth.events import append_user_event
from hausbuch.db.session import workspace_transaction
from hausbuch.schemas.events import (
    Empty,
    EventType,
    UserInvited,
    UserPasskeyEnrolled,
    UserPasskeyRef,
    UserSessionsRevoked,
    UserSignedIn,
)

pytestmark = pytest.mark.db

# The migration's own lists, so the test and the migration cannot drift apart silently.
_MIGRATION = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "migrations",
    "versions",
    "0050_workspace_data_version.py",
)
TRIGGER = "bump_data_version"
MARK = "mark_data_version"
PENDING = "workspace_data_version_pending"
EXCLUDED = {"auth_session", "auth_passkey"}


def _version(dbname: str, ws: uuid.UUID) -> int | None:
    with _admin(dbname) as conn:
        row = conn.execute(
            "SELECT version FROM workspace_data_version WHERE workspace_id = %s", (ws,)
        ).fetchone()
    return None if row is None else row[0]


@pytest.fixture
def app_conn(dbname):
    """app_conn(ws) -> a psycopg connection acting as the app role with `ws` set for the
    session; the caller commits (the bump runs at COMMIT)."""
    opened: list[psycopg.Connection] = []

    def _open(ws: uuid.UUID) -> psycopg.Connection:
        conn = psycopg.connect(ADMIN_URL, dbname=dbname, autocommit=True)
        opened.append(conn)
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(APP)))
        conn.execute("SELECT set_config('app.workspace_id', %s, false)", (str(ws),))
        conn.autocommit = False
        return conn

    yield _open
    for conn in opened:
        conn.close()


def test_a_new_workspace_starts_at_one(dbname, fresh_workspaces):
    a, b = fresh_workspaces
    assert _version(dbname, a) == 1
    assert _version(dbname, b) == 1


def test_insert_update_and_delete_each_bump(dbname, fresh_workspaces, app_engine):
    ws, _ = fresh_workspaces
    start = _version(dbname, ws)
    with workspace_transaction(app_engine, ws) as conn:
        owner = conn.execute(
            text("INSERT INTO owner (workspace_id, full_name) VALUES (:ws, 'A') RETURNING id"),
            {"ws": ws},
        ).scalar_one()
    assert _version(dbname, ws) == start + 1
    with workspace_transaction(app_engine, ws) as conn:
        conn.execute(text("UPDATE owner SET full_name = 'B' WHERE id = :id"), {"id": owner})
    assert _version(dbname, ws) == start + 2
    # The app role may not delete an owner; the trigger fires for any role.
    with _admin(dbname) as conn:
        conn.execute("DELETE FROM owner WHERE id = %s", (owner,))
    assert _version(dbname, ws) == start + 3


def test_an_update_that_matches_no_row_does_not_bump(dbname, fresh_workspaces, app_engine):
    ws, _ = fresh_workspaces
    start = _version(dbname, ws)
    with workspace_transaction(app_engine, ws) as conn:
        conn.execute(text("UPDATE owner SET full_name = 'B' WHERE id = :id"), {"id": uuid.uuid4()})
    assert _version(dbname, ws) == start


def test_one_bump_per_transaction(dbname, fresh_workspaces, app_conn):
    ws, _ = fresh_workspaces
    start = _version(dbname, ws)
    conn = app_conn(ws)
    for n in range(3):
        prop = insert_property(conn, ws, label=f"Wohnung {n}")
        insert_unit(conn, ws, prop)
    conn.execute("UPDATE property SET label = 'Neu' WHERE workspace_id = %s", (ws,))
    conn.commit()
    assert _version(dbname, ws) == start + 1


def test_a_rolled_back_write_does_not_bump(dbname, fresh_workspaces, app_conn):
    ws, _ = fresh_workspaces
    start = _version(dbname, ws)
    conn = app_conn(ws)
    insert_property(conn, ws)
    conn.rollback()
    assert _version(dbname, ws) == start


def test_a_write_in_one_workspace_never_moves_another(dbname, fresh_workspaces, app_conn):
    a, b = fresh_workspaces
    start_a, start_b = _version(dbname, a), _version(dbname, b)
    conn = app_conn(b)
    insert_property(conn, b)
    conn.commit()
    assert _version(dbname, a) == start_a
    assert _version(dbname, b) == start_b + 1


def test_a_superuser_writing_two_workspaces_bumps_both_once(dbname, fresh_workspaces):
    a, b = fresh_workspaces
    start_a, start_b = _version(dbname, a), _version(dbname, b)
    with _admin(dbname) as conn, conn.transaction():
        insert_property(conn, a)
        insert_property(conn, b)
        insert_property(conn, a)
    assert _version(dbname, a) == start_a + 1
    assert _version(dbname, b) == start_b + 1


def test_a_settings_change_on_the_workspace_bumps(dbname, fresh_workspaces, app_conn):
    ws, _ = fresh_workspaces
    start = _version(dbname, ws)
    with _admin(dbname) as conn:
        conn.execute("UPDATE workspace SET name = 'Neu' WHERE id = %s", (ws,))
    assert _version(dbname, ws) == start + 1


def test_a_bump_row_level_security_refuses_raises(dbname, fresh_workspaces, app_conn):
    """The row is written while the transaction is scoped to A; at COMMIT it is scoped to B,
    so the policy on workspace_data_version refuses A's bump. The commit must fail, and the
    write with it, rather than commit without a bump."""
    a, b = fresh_workspaces
    start = _version(dbname, a)
    conn = app_conn(a)
    prop = insert_property(conn, a)
    conn.execute("SELECT set_config('app.workspace_id', %s, true)", (str(b),))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.commit()
    conn.rollback()
    assert _version(dbname, a) == start
    with _admin(dbname) as admin:
        assert admin.execute("SELECT 1 FROM property WHERE id = %s", (prop,)).fetchone() is None


def test_the_session_slide_does_not_bump(dbname, fresh_workspaces, app_conn):
    """auth_session is excluded: every request slides its session, which must not discard
    the kept answers."""
    ws, _ = fresh_workspaces
    with _admin(dbname) as conn, conn.transaction():
        user = conn.execute(
            "INSERT INTO app_user (workspace_id, email, display_name) VALUES (%s, %s, 'A') "
            "RETURNING id",
            (ws, f"{uuid.uuid4().hex}@example.org"),
        ).fetchone()[0]
        session = conn.execute(
            "INSERT INTO auth_session (workspace_id, user_id, token_hash, expires_at, "
            "idle_expires_at) VALUES (%s, %s, %s, now() + interval '1 hour', "
            "now() + interval '1 hour') RETURNING id",
            (ws, user, os.urandom(32)),
        ).fetchone()[0]
    start = _version(dbname, ws)
    conn = app_conn(ws)
    conn.execute("UPDATE auth_session SET last_seen_at = now() WHERE id = %s", (session,))
    conn.commit()
    assert _version(dbname, ws) == start


def _user_event(conn, ws: uuid.UUID, type: EventType, payload) -> None:
    """One user.* event as the sign-in and passkey paths write it (auth/events.py)."""
    append_user_event(
        conn, type, payload, workspace_id=ws, user_id=uuid.uuid4(), now=datetime.now(UTC)
    )


_PASSKEY = uuid.UUID(int=1)
_SIGN_IN_EVENTS = (
    (EventType.USER_SIGNED_IN, UserSignedIn(passkey_id=_PASSKEY)),
    (EventType.USER_PASSKEY_ENROLLED, UserPasskeyEnrolled(passkey_id=_PASSKEY, label="A")),
    (EventType.USER_PASSKEY_RENAMED, UserPasskeyRef(passkey_id=_PASSKEY)),
    (EventType.USER_PASSKEY_REMOVED, UserPasskeyRef(passkey_id=_PASSKEY)),
    # Migration 0055: signing out, from one session or from all.
    (EventType.USER_SIGNED_OUT, Empty()),
    (EventType.USER_SESSIONS_REVOKED, UserSessionsRevoked(count=2)),
)


@pytest.mark.parametrize(
    ("type", "payload"), _SIGN_IN_EVENTS, ids=[t.value for t, _ in _SIGN_IN_EVENTS]
)
def test_a_sign_in_event_does_not_bump(dbname, fresh_workspaces, app_engine, type, payload):
    """Phase 10 §7.5 (a), migration 0051: a sign-in's own event and the passkey events move
    no version (no kept answer shows them), so the first load after a sign-in is kept; since
    migration 0055 the sign-out events neither."""
    ws, _ = fresh_workspaces
    start = _version(dbname, ws)
    with workspace_transaction(app_engine, ws) as conn:
        _user_event(conn, ws, type, payload)
    assert _version(dbname, ws) == start


def test_any_other_event_still_bumps(dbname, fresh_workspaces, app_engine):
    ws, _ = fresh_workspaces
    start = _version(dbname, ws)
    with workspace_transaction(app_engine, ws) as conn:
        invited = UserInvited(invite_id=uuid.uuid4(), invited_user_id=uuid.uuid4())
        _user_event(conn, ws, EventType.USER_INVITED, invited)
    assert _version(dbname, ws) == start + 1
    with _admin(dbname) as conn:
        insert_event(conn, ws)
    assert _version(dbname, ws) == start + 2


def test_a_sign_in_event_does_not_hide_a_later_write_of_its_transaction(
    dbname, fresh_workspaces, app_engine
):
    """The exempt event records nothing as queued, so the write after it in the same
    transaction still bumps, once."""
    ws, _ = fresh_workspaces
    start = _version(dbname, ws)
    with workspace_transaction(app_engine, ws) as conn:
        _user_event(conn, ws, EventType.USER_SIGNED_IN, UserSignedIn(passkey_id=_PASSKEY))
        conn.execute(
            text("INSERT INTO owner (workspace_id, full_name) VALUES (:ws, 'A')"), {"ws": ws}
        )
        _user_event(conn, ws, EventType.USER_SIGNED_IN, UserSignedIn(passkey_id=_PASSKEY))
    assert _version(dbname, ws) == start + 1


def test_the_version_only_grows(dbname, fresh_workspaces, app_conn):
    ws, _ = fresh_workspaces
    conn = app_conn(ws)
    with pytest.raises(psycopg.errors.RestrictViolation):
        conn.execute(
            "UPDATE workspace_data_version SET version = version - 1 WHERE workspace_id = %s",
            (ws,),
        )
    conn.rollback()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("DELETE FROM workspace_data_version WHERE workspace_id = %s", (ws,))
    conn.rollback()


def test_the_app_role_cannot_see_another_workspaces_version(dbname, fresh_workspaces, app_conn):
    a, b = fresh_workspaces
    conn = app_conn(a)
    rows = conn.execute("SELECT workspace_id FROM workspace_data_version").fetchall()
    assert rows == [(a,)]
    conn.rollback()


def test_every_table_with_workspace_id_carries_the_mark_triggers(dbname):
    """Coverage: a table added later with workspace_id and without the marks fails here,
    unless it is added to EXCLUDED with a reason (and api/kept_answers.py keeps no answer
    reading it). Each mark is a plain AFTER row trigger for one event whose WHEN asks
    data_version_queue_first; the one deferred trigger sits on the pending table."""
    with _admin(dbname) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT c.relname FROM pg_class c JOIN pg_attribute a ON a.attrelid = c.oid "
                "AND a.attname = 'workspace_id' AND NOT a.attisdropped "
                "WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')"
            )
        }
        marks: dict[str, list[tuple[bool, int, str]]] = {}
        for relname, deferrable, tgtype, definition, enabled in conn.execute(
            "SELECT c.relname, t.tgdeferrable, t.tgtype, pg_get_triggerdef(t.oid), "
            "t.tgenabled "
            "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE t.tgname LIKE %s AND t.tgfoid = 'mark_workspace_data_version'::regproc",
            (MARK + "\\_%",),
        ):
            assert enabled == "O", relname  # fires (a disabled mark would bump nothing)
            marks.setdefault(relname, []).append((deferrable, tgtype, definition))
        deferred = conn.execute(
            "SELECT c.relname, t.tgdeferrable, t.tginitdeferred, t.tgtype, t.tgenabled "
            "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE t.tgfoid = 'bump_workspace_data_version'::regproc"
        ).fetchall()
    # Both hold workspace_id but are the counter itself.
    assert {"workspace_data_version", PENDING} <= tables
    expected = tables - EXCLUDED - {"workspace_data_version", PENDING}
    assert len(expected) >= 60
    missing = sorted(expected - set(marks))
    assert missing == [], f"tables with workspace_id but no data-version marks: {missing}"
    assert sorted(set(marks) - expected) == ["workspace"]
    for table, triggers in marks.items():
        events = 0
        for deferrable, tgtype, definition in triggers:
            assert not deferrable, table  # immediate: a written table stays truncatable
            assert tgtype & 1 and not tgtype & 2, table  # row-level, AFTER
            event = tgtype & (4 | 8 | 16)
            assert event in (4, 8, 16), table  # one event per trigger
            assert not events & event, table
            events |= event
            assert "WHEN (data_version_queue_first(" in definition, table
        # INSERT (4) + DELETE (8) + UPDATE (16); a workspace is never deleted.
        assert events == (4 | 16 if table == "workspace" else 4 | 8 | 16), table
    assert len(deferred) == 1
    relname, deferrable, initdeferred, tgtype, enabled = deferred[0]
    assert relname == PENDING
    assert enabled == "O"
    assert deferrable and initdeferred
    assert tgtype & 1 and tgtype & (4 | 8 | 16) == 4  # row-level, AFTER INSERT


def _pending_rows(dbname: str) -> int:
    with _admin(dbname) as conn:
        return conn.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(PENDING))
        ).fetchone()[0]


def test_the_pending_table_is_empty_after_every_commit(dbname, fresh_workspaces, app_conn):
    a, b = fresh_workspaces
    conn = app_conn(a)
    for n in range(3):
        insert_property(conn, a, label=f"Wohnung {n}")
    conn.commit()
    with _admin(dbname) as admin, admin.transaction():
        insert_property(admin, a)
        insert_property(admin, b)
    assert _pending_rows(dbname) == 0


def test_a_write_after_a_rolled_back_savepoint_still_bumps(dbname, fresh_workspaces, app_conn):
    """The savepoint takes the queued mark and its pending row back together, so the next
    write of the same transaction marks again."""
    ws, _ = fresh_workspaces
    start = _version(dbname, ws)
    conn = app_conn(ws)
    with pytest.raises(RuntimeError), conn.transaction():
        insert_property(conn, ws, label="verworfen")
        raise RuntimeError
    insert_property(conn, ws, label="bleibt")
    conn.commit()
    assert _version(dbname, ws) == start + 1


def test_a_rolled_back_savepoint_alone_does_not_bump(dbname, fresh_workspaces, app_conn):
    ws, _ = fresh_workspaces
    start = _version(dbname, ws)
    conn = app_conn(ws)
    with pytest.raises(RuntimeError), conn.transaction():
        insert_property(conn, ws)
        raise RuntimeError
    conn.commit()
    assert _version(dbname, ws) == start


def test_a_written_table_stays_free_for_truncate_and_alter(dbname, fresh_workspaces):
    """Postgres refuses TRUNCATE and ALTER TABLE on a table holding pending deferred trigger
    events; the marks are immediate, so the tables a transaction wrote are not held (the
    append-only TRUNCATE guards of phases 2, 4 and 4b test exactly this shape)."""
    ws, _ = fresh_workspaces

    class Undo(Exception):
        pass

    with _admin(dbname) as conn, pytest.raises(Undo), conn.transaction():
        prop = insert_property(conn, ws)
        insert_unit(conn, ws, prop)
        conn.execute("ALTER TABLE property ADD COLUMN probe_7_4 integer")
        conn.execute("ALTER TABLE unit ADD COLUMN probe_7_4 integer")
        raise Undo  # roll the probe columns back


def test_the_migration_lists_agree_with_the_test(dbname):
    """The migration refuses to run on an unlisted table; its exclusions are the test's."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("m0050", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert set(module.EXCLUDED) == EXCLUDED
    assert len(module.WORKSPACE_TABLES) == len(set(module.WORKSPACE_TABLES))
