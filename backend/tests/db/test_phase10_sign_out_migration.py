"""Migration 0055 (docs/phases/phase-10.md §7.5 (a)): `user.signed_out` and
`user.sessions_revoked` move no workspace data version, like 0051's sign-in and passkey
events, and the downgrade gives them their bump back. The behaviour at head is pinned by
tests/db/test_data_version.py; this file runs the migration up, down and up. Invented
values only."""

import uuid

from db.db_support import _admin, alembic, create_database, drop_database, insert_event

HEAD = "0055_sign_out_keeps_data_version"
PREVIOUS = "0054_price_renovation"


def _bumps(name: str, type: str) -> bool:
    """Whether one event of `type` (entity `user`) moves a new workspace's version."""
    ws = uuid.uuid4()
    with _admin(name) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'W')", (ws,))
    with _admin(name) as conn:
        (before,) = conn.execute(
            "SELECT version FROM workspace_data_version WHERE workspace_id = %s", (ws,)
        ).fetchone()
    with _admin(name) as conn:
        insert_event(conn, ws, type=type, entity_type="user")
    with _admin(name) as conn:
        (after,) = conn.execute(
            "SELECT version FROM workspace_data_version WHERE workspace_id = %s", (ws,)
        ).fetchone()
    return after != before


def test_0055_runs_up_down_and_up_on_an_empty_database():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        assert not _bumps(name, "user.signed_out")
        assert not _bumps(name, "user.sessions_revoked")
        assert not _bumps(name, "user.signed_in")
        alembic(name, "downgrade", PREVIOUS)
        assert _bumps(name, "user.signed_out")
        assert _bumps(name, "user.sessions_revoked")
        assert not _bumps(name, "user.signed_in")  # 0051's four stay exempt
        alembic(name, "upgrade", HEAD)
        assert not _bumps(name, "user.signed_out")
    finally:
        drop_database(name)
