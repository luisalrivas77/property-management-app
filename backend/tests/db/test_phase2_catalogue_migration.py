"""Migration 0021 (docs/phases/phase-2.md D130, D131): the document catalogue's subtypes and the
kinds `general` and `management_contract`, and the downgrade guard.

That the three CHECKs match the enums is covered by test_schema_consistency.py, the round trip
by test_migration.py. Synthetic data only."""

import hashlib
import uuid

import pytest
from db.db_support import OWNER, _admin, _insert, alembic, create_database, drop_database
from psycopg import errors
from sqlalchemy.exc import DBAPIError

HEAD = "0021_document_catalogue"
PREVIOUS = "0020_income_tax_assessment"
TABLES = ("document", "document_extraction", "proposal")
# One subtype per group of §6.2.1, and the two kinds.
SUBTYPES = (
    "management_contract",
    "land_register_extract",
    "weg_minutes",
    "repayment_schedule",
    "rent_increase",
    "utility_bill",
    "prepayment_notice",
    "depot_statement",
)
KINDS = ("general", "management_contract")


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
    row = {"workspace_id": ws, "file_sha256": digest, "source": "upload", "type": "contract"}
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


def _seed(conn, ws, data, value) -> None:
    if data == "document":
        _document(conn, ws, subtype=value)
    elif data == "document_extraction":
        _extraction(conn, ws, _document(conn, ws), value)
    elif data == "proposal":
        _proposal(conn, ws, _document(conn, ws), value)


def _forced(conn) -> list[bool]:
    return [
        conn.execute(
            "SELECT relforcerowsecurity FROM pg_class WHERE oid = %s::regclass", (table,)
        ).fetchone()[0]
        for table in TABLES
    ]


def test_0021_accepts_the_subtypes_and_the_kinds():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            for subtype in SUBTYPES:
                _document(conn, ws, subtype=subtype)
            document = _document(conn, ws, subtype="management_contract")
            for kind in KINDS:
                _extraction(conn, ws, _document(conn, ws), kind)
                _proposal(conn, ws, document, kind)
            # the earlier values still pass
            _document(conn, ws, subtype="einkommensteuerbescheid", type="bescheid")
            _proposal(conn, ws, document, "einkommensteuerbescheid")
            with pytest.raises(errors.CheckViolation):
                _document(conn, ws, subtype="identity_card")  # not a type on purpose (D131)
    finally:
        drop_database(name)


@pytest.mark.parametrize(
    ("data", "value"),
    [
        ("document", "management_contract"),
        ("document", "tenant_self_disclosure"),
        ("document_extraction", "general"),
        ("document_extraction", "management_contract"),
        ("proposal", "general"),
        ("proposal", "management_contract"),
    ],
)
def test_0021_downgrade_refuses_while_a_row_uses_a_new_value(data, value):
    """A downgrade never leaves a value its CHECK refuses; the guard sees rows under forced
    RLS (the owner has no workspace set while migrating)."""
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            _seed(conn, ws, data, value)
        with pytest.raises(DBAPIError, match=f"downgrade of {HEAD} refused") as refused:
            alembic(name, "downgrade", PREVIOUS)
        # the guard's own exception, not a syntax error that quotes the guard's text
        assert isinstance(refused.value.orig, errors.RestrictViolation)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
            assert _forced(conn) == [True, True, True]  # the NO FORCE was rolled back
    finally:
        drop_database(name)


def test_0021_downgrade_without_such_rows_restores_the_old_checks():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            document = _document(conn, ws, subtype="einkommensteuerbescheid", type="bescheid")
            _proposal(conn, ws, document, "einkommensteuerbescheid")
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [
                (PREVIOUS,)
            ]
            assert _forced(conn) == [True, True, True]
            _as_owner(conn, ws)
            with pytest.raises(errors.CheckViolation):
                _document(conn, ws, subtype="management_contract")
            with pytest.raises(errors.CheckViolation):
                _extraction(conn, ws, document, "general")
            with pytest.raises(errors.CheckViolation):
                _proposal(conn, ws, document, "management_contract")
            _proposal(conn, ws, document, "gehaltsabrechnung")  # 0020's kinds stay
        alembic(name, "upgrade", "head")
    finally:
        drop_database(name)
