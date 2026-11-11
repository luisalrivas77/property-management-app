"""Migration 0023 (docs/phases/phase-3.md D143): a payment proof linked to its bill through
`document_link.target_document_id`, the value `payment_proof`, and the downgrade guard.

That the CHECKs match the enums is covered by test_schema_consistency.py, the round trip by
test_migration.py. Synthetic data only."""

import hashlib
import uuid

import pytest
from db.db_support import (
    OWNER,
    _admin,
    _insert,
    alembic,
    create_database,
    drop_database,
    insert_property,
)
from psycopg import errors
from sqlalchemy.exc import DBAPIError

HEAD = "0023_payment_proof"
PREVIOUS = "0022_financing_invoice_subtype"


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


def _link(conn, ws, document_id, target_document_id) -> uuid.UUID:
    return _insert(
        conn,
        "document_link",
        {
            "workspace_id": ws,
            "document_id": document_id,
            "target_document_id": target_document_id,
        },
    )


def _links(conn) -> int:
    return conn.execute("SELECT count(*) FROM document_link").fetchone()[0]


def _forced(conn) -> bool:
    return conn.execute(
        "SELECT relforcerowsecurity FROM pg_class WHERE oid = 'document_link'::regclass"
    ).fetchone()[0]


def test_0023_links_a_payment_proof_to_its_bill():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            bill = _document(conn, ws)
            proof = _document(conn, ws, type="other", subtype="payment_proof")
            _link(conn, ws, proof, bill)
            assert _links(conn) == 1
            # the earlier values still pass
            _document(conn, ws, subtype="financing_invoice")
            with pytest.raises(errors.CheckViolation):
                _document(conn, ws, type="other", subtype="payment")
    finally:
        drop_database(name)


def test_0023_refuses_a_document_linked_to_itself_twice_or_with_two_targets():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            bill = _document(conn, ws)
            proof = _document(conn, ws, type="other", subtype="payment_proof")
            with pytest.raises(errors.CheckViolation):
                _link(conn, ws, proof, proof)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            _link(conn, ws, proof, bill)
            with pytest.raises(errors.UniqueViolation):
                _link(conn, ws, proof, bill)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            prop = insert_property(conn, ws)
            with pytest.raises(errors.CheckViolation):  # one target per link
                _insert(
                    conn,
                    "document_link",
                    {
                        "workspace_id": ws,
                        "document_id": proof,
                        "target_document_id": bill,
                        "property_id": prop,
                    },
                )
    finally:
        drop_database(name)


def test_0023_removing_either_document_removes_the_link():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            bill = _document(conn, ws)
            proof = _document(conn, ws, type="other", subtype="payment_proof")
            _link(conn, ws, proof, bill)
            conn.execute("DELETE FROM document WHERE id = %s", (bill,))
            assert _links(conn) == 0
            bill = _document(conn, ws)
            _link(conn, ws, proof, bill)
            conn.execute("DELETE FROM document WHERE id = %s", (proof,))
            assert _links(conn) == 0
    finally:
        drop_database(name)


def test_0023_a_link_cannot_reach_another_workspaces_document():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws, other = _new_workspace(name), _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, other)
            foreign = _document(conn, other)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            proof = _document(conn, ws, type="other", subtype="payment_proof")
            with pytest.raises(errors.ForeignKeyViolation):
                _link(conn, ws, proof, foreign)
    finally:
        drop_database(name)


@pytest.mark.parametrize("row", ["link", "subtype"])
def test_0023_downgrade_refuses_while_a_proof_or_its_link_exists(row):
    """A downgrade never leaves a value its CHECK refuses or drops a link; the guard sees rows
    under forced RLS (the owner has no workspace set while migrating)."""
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            bill = _document(conn, ws)
            if row == "link":
                _link(conn, ws, _document(conn, ws, type="other"), bill)
            else:
                _document(conn, ws, type="other", subtype="payment_proof")
        with pytest.raises(DBAPIError, match=f"downgrade of {HEAD} refused") as refused:
            alembic(name, "downgrade", PREVIOUS)
        assert isinstance(refused.value.orig, errors.RestrictViolation)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
            assert _forced(conn) is True  # the NO FORCE was rolled back
    finally:
        drop_database(name)


def test_0023_downgrade_without_such_rows_restores_the_old_constraints():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            bill = _document(conn, ws)
            prop = insert_property(conn, ws)
            _insert(
                conn,
                "document_link",
                {"workspace_id": ws, "document_id": bill, "property_id": prop},
            )
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [
                (PREVIOUS,)
            ]
            assert _forced(conn) is True
            _as_owner(conn, ws)
            with pytest.raises(errors.CheckViolation):
                _document(conn, ws, type="other", subtype="payment_proof")
            with pytest.raises(errors.UniqueViolation):  # 0001's uniqueness is back
                _insert(
                    conn,
                    "document_link",
                    {"workspace_id": ws, "document_id": bill, "property_id": prop},
                )
        alembic(name, "upgrade", "head")
    finally:
        drop_database(name)
