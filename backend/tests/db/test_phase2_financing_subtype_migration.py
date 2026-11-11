"""Migration 0022 (docs/phases/phase-2.md D138): the subtype `financing_invoice`, and the
downgrade guard.

That the CHECK matches the enum is covered by test_schema_consistency.py, the round trip by
test_migration.py. Synthetic data only."""

import hashlib
import uuid

import pytest
from db.db_support import OWNER, _admin, _insert, alembic, create_database, drop_database
from psycopg import errors
from sqlalchemy.exc import DBAPIError

HEAD = "0022_financing_invoice_subtype"
PREVIOUS = "0021_document_catalogue"


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
    row = {"workspace_id": ws, "file_sha256": digest, "source": "upload", "type": "invoice"}
    return _insert(conn, "document", row | overrides)


def _forced(conn) -> bool:
    return conn.execute(
        "SELECT relforcerowsecurity FROM pg_class WHERE oid = 'document'::regclass"
    ).fetchone()[0]


def test_0022_accepts_the_subtype_on_an_invoice_and_a_bescheid():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            _document(conn, ws, subtype="financing_invoice")
            _document(conn, ws, subtype="financing_invoice", type="bescheid")
            # the earlier values still pass
            _document(conn, ws, subtype="cost_basis_evidence")
            _document(conn, ws, subtype="capital_income_tax_certificate", type="letter")
            with pytest.raises(errors.CheckViolation):
                _document(conn, ws, subtype="financing")
    finally:
        drop_database(name)


def test_0022_downgrade_refuses_while_a_document_is_a_financing_bill():
    """A downgrade never leaves a value its CHECK refuses; the guard sees rows under forced
    RLS (the owner has no workspace set while migrating)."""
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            _document(conn, ws, subtype="financing_invoice")
        with pytest.raises(DBAPIError, match=f"downgrade of {HEAD} refused") as refused:
            alembic(name, "downgrade", PREVIOUS)
        # the guard's own exception, not a syntax error that quotes the guard's text
        assert isinstance(refused.value.orig, errors.RestrictViolation)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
            assert _forced(conn) is True  # the NO FORCE was rolled back
    finally:
        drop_database(name)


def test_0022_downgrade_without_such_rows_restores_the_old_check():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            _document(conn, ws, subtype="cost_basis_evidence")
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [
                (PREVIOUS,)
            ]
            assert _forced(conn) is True
            _as_owner(conn, ws)
            with pytest.raises(errors.CheckViolation):
                _document(conn, ws, subtype="financing_invoice")
            _document(conn, ws, subtype="management_contract", type="contract")  # 0021's stay
        alembic(name, "upgrade", "head")
    finally:
        drop_database(name)
