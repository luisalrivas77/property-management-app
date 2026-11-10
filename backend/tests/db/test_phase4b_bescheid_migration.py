"""Migration 0020 (docs/phases/phase-4b.md D129): the Einkommensteuerbescheid as a document
subtype and an extraction and proposal kind, and the downgrade guard.

That the three CHECKs match the enums is covered by test_schema_consistency.py, the round trip
by test_migration.py. Synthetic data only."""

import hashlib
import uuid

import pytest
from db.db_support import OWNER, _admin, _insert, alembic, create_database, drop_database
from psycopg import errors
from sqlalchemy.exc import DBAPIError

HEAD = "0020_income_tax_assessment"
PREVIOUS = "0019_automation"
NEW = "einkommensteuerbescheid"
TABLES = ("document", "document_extraction", "proposal")


def _new_workspace(name) -> uuid.UUID:
    ws = uuid.uuid4()
    with _admin(name) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'A')", (ws,))
    return ws


def _as_owner(conn, ws) -> None:
    conn.execute(f"SET ROLE {OWNER}")
    conn.execute("SELECT set_config('app.workspace_id', %s, false)", (str(ws),))


def _document(conn, ws, **overrides) -> uuid.UUID:
    digest = hashlib.sha256(uuid.uuid4().bytes).digest()
    conn.execute(
        "INSERT INTO document_file (workspace_id, sha256, size_bytes, media_type, storage_key)"
        " VALUES (%s, %s, 11, 'application/pdf', %s)",
        (ws, digest, f"{ws}/sha256/{digest.hex()}"),
    )
    row = {"workspace_id": ws, "file_sha256": digest, "source": "upload", "type": "bescheid"}
    return _insert(conn, "document", row | overrides)


def _extraction(conn, ws, document, kind) -> uuid.UUID:
    row = {
        "workspace_id": ws,
        "document_id": document,
        "version": 1,
        "source": "manual",
        "fields": "{}",
        "kind": kind,
    }
    return _insert(conn, "document_extraction", row)


def _proposal(conn, ws, document, kind) -> uuid.UUID:
    return _insert(conn, "proposal", {"workspace_id": ws, "document_id": document, "kind": kind})


def _seed(conn, ws, data) -> None:
    if data == "document":
        _document(conn, ws, subtype=NEW)
    elif data == "document_extraction":
        _extraction(conn, ws, _document(conn, ws), NEW)
    elif data == "proposal":
        _proposal(conn, ws, _document(conn, ws), NEW)


def _forced(conn) -> list[bool]:
    return [
        conn.execute(
            "SELECT relforcerowsecurity FROM pg_class WHERE oid = %s::regclass", (table,)
        ).fetchone()[0]
        for table in TABLES
    ]


def test_0020_accepts_the_subtype_and_the_kind():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            document = _document(conn, ws, subtype=NEW)
            _extraction(conn, ws, document, NEW)
            _proposal(conn, ws, document, NEW)
            # the earlier values still pass
            _document(conn, ws, subtype="grundsteuerbescheid")
            _proposal(conn, ws, document, "gehaltsabrechnung")
            with pytest.raises(errors.CheckViolation):
                _document(conn, ws, subtype="einkommensteuer")
    finally:
        drop_database(name)


@pytest.mark.parametrize("data", TABLES)
def test_0020_downgrade_refuses_while_a_row_uses_the_new_value(data):
    """A downgrade never leaves a value its CHECK refuses; the guard sees rows under forced
    RLS (the owner has no workspace set while migrating)."""
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
            assert _forced(conn) == [True, True, True]  # the NO FORCE was rolled back
    finally:
        drop_database(name)


def test_0020_downgrade_without_such_rows_restores_the_old_checks():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            document = _document(conn, ws, subtype="grundsteuerbescheid")
            _proposal(conn, ws, document, "lohnsteuerbescheinigung")
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [
                (PREVIOUS,)
            ]
            assert _forced(conn) == [True, True, True]
            _as_owner(conn, ws)
            with pytest.raises(errors.CheckViolation):
                _document(conn, ws, subtype=NEW)
            with pytest.raises(errors.CheckViolation):
                _extraction(conn, ws, document, NEW)
            with pytest.raises(errors.CheckViolation):
                _proposal(conn, ws, document, NEW)
            _proposal(conn, ws, document, "gehaltsabrechnung")  # 0018's kinds stay
        alembic(name, "upgrade", "head")
    finally:
        drop_database(name)
