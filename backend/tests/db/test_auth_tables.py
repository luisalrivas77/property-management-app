"""Migration 0003: auth tables, the auth_lookup policy and the grants (phase 1 step 3, §6.2).

The auth_lookup policy is the one place where rows are visible without a workspace. These
tests pin down that it is SELECT-only, transaction-local, limited to the three auth tables,
and that it leaves workspace isolation and the composite FKs of those tables intact.

Under row-level security a forbidden INSERT raises "new row violates row-level security
policy", while a forbidden UPDATE or DELETE raises nothing and touches no rows: the rows are
simply not visible to the command. So "rejected" means an error for INSERT and rowcount 0
(plus unchanged data) for UPDATE, DELETE and SELECT ... FOR UPDATE.
"""

import os
import uuid
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from conftest import APP, OWNER, _insert, insert_event, insert_property, make_engine
from psycopg import errors
from psycopg.types.json import Jsonb
from sqlalchemy import text
from test_migration import MIGRATION_TABLES

from hausbuch.db.session import workspace_transaction

AUTH_TABLES = ("auth_passkey", "auth_session", "auth_invite")
OTHER_TABLES = sorted(MIGRATION_TABLES - set(AUTH_TABLES))
LOOKUP_ON = "SELECT set_config('app.auth_lookup', 'on', true)"
RLS_ERROR = "row-level security"

# One harmless write per auth table, the kind sign-in really does.
TOUCH = {
    "auth_passkey": "sign_count = sign_count + 1",
    "auth_session": "revoked_at = now()",
    "auth_invite": "used_at = now()",
}


# --- Seed rows ---------------------------------------------------------------


@dataclass
class Seed:
    """One workspace's auth rows (plus a few domain rows, so other tables are not empty)."""

    ws: uuid.UUID
    user: uuid.UUID
    ids: dict[str, uuid.UUID] = field(default_factory=dict)


def _user(conn, ws) -> uuid.UUID:
    return _insert(
        conn,
        "app_user",
        {"workspace_id": ws, "email": f"{uuid.uuid4().hex}@example.org", "display_name": "Luis"},
    )


def _passkey(conn, ws, user, **overrides) -> uuid.UUID:
    row = {
        "workspace_id": ws,
        "user_id": user,
        "credential_id": os.urandom(32),
        "public_key": b"\xa5\x01\x02\x03\x26",
        "transports": Jsonb(["internal", "hybrid"]),
        "backup_eligible": True,
        "backed_up": True,
        "label": "iPhone von Luis",
    } | overrides
    return _insert(conn, "auth_passkey", row)


def _session(conn, ws, user, passkey=None, **overrides) -> uuid.UUID:
    now = datetime.now(UTC)
    row = {
        "workspace_id": ws,
        "user_id": user,
        "passkey_id": passkey,
        "token_hash": os.urandom(32),
        "expires_at": now + timedelta(hours=12),
        "idle_expires_at": now + timedelta(hours=2),
    } | overrides
    return _insert(conn, "auth_session", row)


def _invite(conn, ws, user, **overrides) -> uuid.UUID:
    row = {
        "workspace_id": ws,
        "user_id": user,
        "token_hash": os.urandom(32),
        "expires_at": datetime.now(UTC) + timedelta(hours=24),
    } | overrides
    return _insert(conn, "auth_invite", row)


def _seed(conn, ws) -> Seed:
    user = _user(conn, ws)
    seed = Seed(ws, user)
    seed.ids["auth_passkey"] = _passkey(conn, ws, user)
    seed.ids["auth_session"] = _session(conn, ws, user, seed.ids["auth_passkey"])
    seed.ids["auth_invite"] = _invite(conn, ws, user, created_by=user)
    insert_property(conn, ws)
    insert_event(conn, ws)
    _insert(conn, "owner", {"workspace_id": ws, "full_name": "Luis"})
    _insert(conn, "tenant", {"workspace_id": ws, "full_name": "Erika Muster"})
    return seed


@pytest.fixture
def seeded(connect, fresh_workspaces) -> tuple[Seed, Seed]:
    """Auth and domain rows in two fresh workspaces, written by the app role and committed."""
    seeds = []
    for ws in fresh_workspaces:
        with connect(APP, ws) as conn:
            seeds.append(_seed(conn, ws))
            conn.commit()
    return seeds[0], seeds[1]


def _ids(conn, table: str, *, for_update: bool = False) -> set[uuid.UUID]:
    lock = " FOR UPDATE" if for_update else ""
    return {r[0] for r in conn.execute(f"SELECT id FROM {table}{lock}")}


def _snapshot(connect, seed: Seed, table: str) -> tuple:
    """The row as its own workspace sees it (to prove a write changed nothing)."""
    with connect(APP, seed.ws) as conn:
        return conn.execute(f"SELECT * FROM {table} WHERE id = %s", (seed.ids[table],)).fetchone()


# --- (a) the flag allows SELECT, and nothing else ------------------------------


@pytest.mark.parametrize("table", AUTH_TABLES)
def test_lookup_flag_reads_every_workspace(connect, seeded, table):
    a, b = seeded
    with connect(APP) as conn:
        assert _ids(conn, table) == set(), "without the flag, no workspace means no rows"
        conn.execute(LOOKUP_ON)
        assert {a.ids[table], b.ids[table]} <= _ids(conn, table)


@pytest.mark.parametrize("table", AUTH_TABLES)
def test_lookup_flag_does_not_allow_update_delete_or_locking(connect, seeded, table):
    a, b = seeded
    before = [_snapshot(connect, s, table) for s in (a, b)]
    with connect(APP) as conn:
        conn.execute(LOOKUP_ON)
        targets = [a.ids[table], b.ids[table]]
        touched = f"UPDATE {table} SET {TOUCH[table]}"
        assert conn.execute(f"{touched} WHERE id = ANY(%s)", (targets,)).rowcount == 0
        assert conn.execute(touched).rowcount == 0  # unfiltered: no row of any workspace
        assert conn.execute(f"DELETE FROM {table} WHERE id = ANY(%s)", (targets,)).rowcount == 0
        assert conn.execute(f"DELETE FROM {table}").rowcount == 0
        assert _ids(conn, table, for_update=True) == set()  # nothing can be locked either
        assert {a.ids[table], b.ids[table]} <= _ids(conn, table)  # still readable
        conn.commit()
    assert [_snapshot(connect, s, table) for s in (a, b)] == before


@pytest.mark.parametrize("table", AUTH_TABLES)
def test_lookup_flag_does_not_allow_insert(connect, seeded, table):
    a, _ = seeded
    insert = {
        "auth_passkey": lambda c: _passkey(c, a.ws, a.user),
        "auth_session": lambda c: _session(c, a.ws, a.user),
        "auth_invite": lambda c: _invite(c, a.ws, a.user),
    }[table]
    with connect(APP) as conn:
        conn.execute(LOOKUP_ON)
        with pytest.raises(errors.InsufficientPrivilege, match=RLS_ERROR):
            insert(conn)


@pytest.mark.parametrize("table", AUTH_TABLES)
def test_lookup_flag_does_not_widen_writes_beyond_the_set_workspace(connect, seeded, table):
    """Flag on while workspace A is set: A's rows are writable as always, B's still are not."""
    a, b = seeded
    before = _snapshot(connect, b, table)
    insert_into_b = {
        "auth_passkey": lambda c: _passkey(c, b.ws, b.user),
        "auth_session": lambda c: _session(c, b.ws, b.user),
        "auth_invite": lambda c: _invite(c, b.ws, b.user),
    }[table]
    with connect(APP, a.ws) as conn:
        conn.execute(LOOKUP_ON)
        assert b.ids[table] in _ids(conn, table)  # readable under the flag
        assert (
            conn.execute(
                f"UPDATE {table} SET {TOUCH[table]} WHERE id = %s", (b.ids[table],)
            ).rowcount
            == 0
        )
        assert conn.execute(f"DELETE FROM {table} WHERE id = %s", (b.ids[table],)).rowcount == 0
        assert (
            conn.execute(
                f"UPDATE {table} SET {TOUCH[table]} WHERE id = %s", (a.ids[table],)
            ).rowcount
            == 1
        )
        with pytest.raises(errors.InsufficientPrivilege, match=RLS_ERROR):
            insert_into_b(conn)
    assert _snapshot(connect, b, table) == before


@pytest.mark.parametrize("value", ["true", "ON", "1", "yes", ""])
def test_only_the_exact_value_on_enables_the_lookup(connect, seeded, value):
    with connect(APP) as conn:
        conn.execute("SELECT set_config('app.auth_lookup', %s, true)", (value,))
        for table in AUTH_TABLES:
            assert _ids(conn, table) == set(), table


def test_auth_lookup_policy_is_select_only_and_only_on_the_auth_tables(connect):
    """The catalog view of the same property: a FOR ALL policy, a WITH CHECK, a role-limited
    or restrictive variant, or the policy on any other table fails here."""
    with connect(OWNER) as conn:
        rows = conn.execute("""
            SELECT c.relname, p.polname, p.polcmd, p.polpermissive, p.polroles,
                   pg_get_expr(p.polqual, p.polrelid), p.polwithcheck IS NULL
            FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid
            ORDER BY c.relname, p.polname
        """).fetchall()
    lookup = [r for r in rows if r[1] == "auth_lookup"]
    assert sorted(r[0] for r in lookup) == sorted(AUTH_TABLES)
    for relname, _, cmd, permissive, roles, qual, no_check in lookup:
        assert (cmd, permissive, roles, no_check) == ("r", True, [0], True), relname
        assert qual == "(current_setting('app.auth_lookup'::text, true) = 'on'::text)", relname
    assert {(r[0], r[1]) for r in rows} == {
        (t, "workspace_isolation") for t in MIGRATION_TABLES
    } | {(t, "auth_lookup") for t in AUTH_TABLES}


# --- (b) the flag is transaction-local ------------------------------------------


@pytest.fixture
def single_connection_engine(dbname):
    """A pool of exactly one connection, so every transaction reuses the same backend."""
    engine = make_engine(dbname, pool_size=1, max_overflow=0)
    yield engine
    engine.dispose()


@pytest.mark.parametrize("fail", [False, True], ids=["after-commit", "after-rollback"])
def test_lookup_flag_does_not_leak_to_the_next_transaction(single_connection_engine, seeded, fail):
    a, b = seeded
    engine = single_connection_engine

    class Boom(Exception):
        pass

    with pytest.raises(Boom) if fail else nullcontext(), engine.begin() as conn:
        conn.execute(text(LOOKUP_ON))
        pid = conn.execute(text("SELECT pg_backend_pid()")).scalar_one()
        visible = set(conn.execute(text("SELECT id FROM auth_session")).scalars())
        assert {a.ids["auth_session"], b.ids["auth_session"]} <= visible
        if fail:
            raise Boom

    with engine.begin() as conn:
        setting, pid_after = conn.execute(
            text("SELECT current_setting('app.auth_lookup', true), pg_backend_pid()")
        ).one()
        assert pid_after == pid, "the pool should have reused the same connection"
        assert setting != "on"
        for table in AUTH_TABLES:
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0, table

    # The next request on that connection is another workspace's: it sees only its own rows.
    with workspace_transaction(engine, b.ws) as conn:
        assert conn.execute(text("SELECT pg_backend_pid()")).scalar_one() == pid
        for table in AUTH_TABLES:
            assert set(conn.execute(text(f"SELECT id FROM {table}")).scalars()) == {b.ids[table]}


# --- (c) the flag opens no other table --------------------------------------------


def test_lookup_flag_makes_no_other_table_readable(connect, seeded):
    a, _ = seeded
    with connect(APP, a.ws) as conn:  # the seed is there for its own workspace
        populated = {t for t in OTHER_TABLES if conn.execute(f"SELECT 1 FROM {t}").fetchone()}
    assert {"workspace", "app_user", "owner", "property", "tenant", "event"} <= populated
    with connect(APP) as conn:
        conn.execute(LOOKUP_ON)
        assert {a.ids["auth_session"]} <= _ids(conn, "auth_session")  # the flag is on
        for table in OTHER_TABLES:
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table


# --- (d) workspace isolation still holds on the auth tables -------------------------


@pytest.mark.parametrize("table", AUTH_TABLES)
def test_auth_tables_are_isolated_by_workspace(connect, seeded, table):
    a, b = seeded
    before = _snapshot(connect, b, table)
    insert_into_b = {
        "auth_passkey": lambda c: _passkey(c, b.ws, b.user),
        "auth_session": lambda c: _session(c, b.ws, b.user),
        "auth_invite": lambda c: _invite(c, b.ws, b.user),
    }[table]
    with connect(APP, a.ws) as conn:
        rows = conn.execute(f"SELECT id, workspace_id FROM {table}").fetchall()
        assert {r[1] for r in rows} == {a.ws}
        assert a.ids[table] in {r[0] for r in rows}
        assert b.ids[table] not in {r[0] for r in rows}
        assert (
            conn.execute(
                f"UPDATE {table} SET {TOUCH[table]} WHERE id = %s", (b.ids[table],)
            ).rowcount
            == 0
        )
        assert conn.execute(f"DELETE FROM {table} WHERE id = %s", (b.ids[table],)).rowcount == 0
        with pytest.raises(errors.InsufficientPrivilege, match=RLS_ERROR):
            insert_into_b(conn)
    assert _snapshot(connect, b, table) == before


# --- (e) composite FKs: no cross-workspace references --------------------------------

CROSS_REFERENCES = {
    "passkey-of-another-workspaces-user": lambda c, a, b: _passkey(c, b.ws, a.user),
    "session-of-another-workspaces-user": lambda c, a, b: _session(c, b.ws, a.user),
    "session-with-another-workspaces-passkey": lambda c, a, b: _session(
        c, b.ws, b.user, a.ids["auth_passkey"]
    ),
    "invite-for-another-workspaces-user": lambda c, a, b: _invite(c, b.ws, a.user),
    "invite-created-by-another-workspaces-user": lambda c, a, b: _invite(
        c, b.ws, b.user, created_by=a.user
    ),
}


@pytest.mark.parametrize("case", sorted(CROSS_REFERENCES))
def test_auth_rows_cannot_reference_another_workspaces_rows(connect, seeded, case):
    a, b = seeded
    with connect(APP, b.ws) as conn:
        with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
            CROSS_REFERENCES[case](conn, a, b)
        # The same row with B's own references is fine.
        _session(conn, b.ws, b.user, b.ids["auth_passkey"])
        _invite(conn, b.ws, b.user, created_by=b.user)


def test_references_must_exist(connect, seeded):
    _, b = seeded
    with connect(APP, b.ws) as conn:
        for insert in (
            lambda: _passkey(conn, b.ws, uuid.uuid4()),
            lambda: _session(conn, b.ws, b.user, uuid.uuid4()),
            lambda: _invite(conn, b.ws, b.user, created_by=uuid.uuid4()),
        ):
            with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
                insert()


# --- Constraints ---------------------------------------------------------------


def test_credential_id_is_unique_across_workspaces(connect, seeded):
    a, b = seeded
    with connect(APP, a.ws) as conn:
        credential = conn.execute(
            "SELECT credential_id FROM auth_passkey WHERE id = %s", (a.ids["auth_passkey"],)
        ).fetchone()[0]
    with connect(APP, b.ws) as conn, pytest.raises(errors.UniqueViolation):
        _passkey(conn, b.ws, b.user, credential_id=credential)


CHECKS = {
    "session-token-hash-not-32-bytes": lambda c, s: _session(c, s.ws, s.user, token_hash=b"x"),
    "invite-token-hash-not-32-bytes": lambda c, s: _invite(c, s.ws, s.user, token_hash=b"x" * 33),
    "invite-unknown-purpose": lambda c, s: _invite(c, s.ws, s.user, purpose="login"),
    "invite-null-purpose": lambda c, s: _invite(c, s.ws, s.user, purpose=None),
    "passkey-empty-label": lambda c, s: _passkey(c, s.ws, s.user, label=""),
    "passkey-long-label": lambda c, s: _passkey(c, s.ws, s.user, label="x" * 101),
    "passkey-negative-sign-count": lambda c, s: _passkey(c, s.ws, s.user, sign_count=-1),
    "passkey-empty-credential-id": lambda c, s: _passkey(c, s.ws, s.user, credential_id=b""),
    "passkey-transports-not-an-array": lambda c, s: _passkey(
        c, s.ws, s.user, transports=Jsonb({"usb": True})
    ),
    "passkey-transports-not-strings": lambda c, s: _passkey(
        c, s.ws, s.user, transports=Jsonb(["usb", 1])
    ),
}


@pytest.mark.parametrize("case", sorted(CHECKS))
def test_check_constraints(connect, seeded, case):
    a, _ = seeded
    expected = errors.NotNullViolation if case == "invite-null-purpose" else errors.CheckViolation
    with connect(APP, a.ws) as conn, pytest.raises(expected):
        CHECKS[case](conn, a)


def test_invite_purpose_defaults_to_enrollment(connect, seeded):
    a, _ = seeded
    with connect(APP, a.ws) as conn:
        invite = _invite(conn, a.ws, a.user)
        purpose = conn.execute(
            "SELECT purpose FROM auth_invite WHERE id = %s", (invite,)
        ).fetchone()[0]
    assert purpose == "enrollment"


# --- Grants --------------------------------------------------------------------

PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
EXPECTED_GRANTS = {
    "auth_passkey": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "auth_session": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "auth_invite": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "app_user": {"SELECT", "INSERT"},  # gains INSERT in 0003; never UPDATE or DELETE
    "workspace": {"SELECT", "UPDATE"},  # unchanged: the app cannot create a workspace
}


@pytest.mark.parametrize("table", sorted(EXPECTED_GRANTS))
def test_app_role_grants(connect, table):
    with connect(OWNER) as conn:
        granted = {
            p
            for p in PRIVILEGES
            if conn.execute("SELECT has_table_privilege(%s, %s, %s)", (APP, table, p)).fetchone()[0]
        }
    assert granted == EXPECTED_GRANTS[table]


def test_app_role_inserts_users_only_into_its_own_workspace(connect, seeded):
    a, b = seeded
    with connect(APP, a.ws) as conn:
        _user(conn, a.ws)
        with pytest.raises(errors.InsufficientPrivilege, match=RLS_ERROR), conn.transaction():
            _user(conn, b.ws)
        for statement in (
            "UPDATE app_user SET display_name = 'x' WHERE workspace_id = %s",
            "DELETE FROM app_user WHERE workspace_id = %s",
        ):
            denied = pytest.raises(errors.InsufficientPrivilege, match="permission denied")
            with denied, conn.transaction():
                conn.execute(statement, (a.ws,))


def test_app_role_cannot_insert_a_workspace(connect):
    """The Scaleway finding (§7), as a regression test: even with the new id set as the
    workspace, the app role has no INSERT on workspace."""
    new = uuid.uuid4()
    with connect(APP, new) as conn, pytest.raises(errors.InsufficientPrivilege, match="permission"):
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'X')", (new,))


def test_owner_inserts_a_workspace_only_with_its_id_set(connect):
    """What provisioning relies on (§7): the schema owner may insert a workspace, but RLS
    still applies to it, so only with app.workspace_id set to the new id."""
    new = uuid.uuid4()
    with connect(OWNER) as conn, pytest.raises(errors.InsufficientPrivilege, match=RLS_ERROR):
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'X')", (new,))
    with connect(OWNER, new) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'X')", (new,))
        user = _user(conn, new)
        _invite(conn, new, user)


def test_the_role_survives_a_rollback_inside_a_test(connect, fresh_workspaces):
    """Guards the connect fixture: after a mid-test rollback the test still runs as the app
    role, never as the superuser, so RLS tests cannot pass vacuously."""
    a, _ = fresh_workspaces
    with connect(APP, a) as conn:
        conn.rollback()
        assert conn.execute("SELECT current_user").fetchone()[0] == APP


def test_a_passkey_with_sessions_cannot_be_deleted_bare(connect, fresh_workspaces):
    """Step 3 contract (orchestrator, 5 Nov 2026, after the chunk 0 review): deleting a passkey
    must end the sessions it opened. The session FK is deliberately NO ACTION, so code that
    deletes a passkey without first revoking and detaching its sessions fails loudly."""
    a, _ = fresh_workspaces
    with connect(OWNER, a) as conn:
        user = _user(conn, a)
        passkey = _passkey(conn, a, user)
        session = _session(conn, a, user, passkey=passkey)
        conn.commit()
    with connect(APP, a) as conn:
        with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
            conn.execute("DELETE FROM auth_passkey WHERE id = %s", (passkey,))
        # The required order: revoke and detach the sessions, then delete the passkey.
        conn.execute(
            "UPDATE auth_session SET revoked_at = coalesce(revoked_at, now()), passkey_id = NULL"
            " WHERE passkey_id = %s",
            (passkey,),
        )
        conn.execute("DELETE FROM auth_passkey WHERE id = %s", (passkey,))
        revoked, linked = conn.execute(
            "SELECT revoked_at IS NOT NULL, passkey_id FROM auth_session WHERE id = %s", (session,)
        ).fetchone()
        assert revoked and linked is None
