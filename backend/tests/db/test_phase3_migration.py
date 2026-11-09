"""Migration 0013 (docs/phases/phase-3.md §6): the three columns, tax_input (append-only,
scope per kind) and tax_export (immutable), their grants, and the downgrade guard.

RLS, workspace_id and the table list are covered for every table by test_migration.py; the
enum CHECKs by test_schema_consistency.py."""

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
    insert_tenancy,
    insert_unit,
)
from psycopg import errors
from psycopg.types.json import Jsonb
from sqlalchemy.exc import DBAPIError

from hausbuch.schemas.tax import TaxInputCreate

PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
EXPECTED_GRANTS = {
    "tax_input": {"SELECT", "INSERT"},  # append-only: never UPDATE or DELETE
    "tax_export": {"SELECT", "INSERT", "DELETE"},  # immutable; the retention sweeper deletes
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


# --- Seed helpers ----------------------------------------------------------------------


def _document(conn, ws) -> tuple[uuid.UUID, bytes]:
    digest = hashlib.sha256(uuid.uuid4().bytes).digest()
    conn.execute(
        "INSERT INTO document_file (workspace_id, sha256, size_bytes, media_type, storage_key)"
        " VALUES (%s, %s, 11, 'application/pdf', %s)",
        (ws, digest, f"{ws}/sha256/{digest.hex()}"),
    )
    doc = _insert(conn, "document", {"workspace_id": ws, "file_sha256": digest, "source": "upload"})
    return doc, digest


def _loan(conn, ws, property_id) -> uuid.UUID:
    row = {
        "workspace_id": ws,
        "property_id": property_id,
        "lender": "Beispielbank",
        "principal": "240000.00",
        "nominal_rate": "0.035",
        "monthly_payment": "1000.00",
        "disbursed_on": "2026-06-29",
        "first_payment_on": "2026-08-01",
    }
    return _insert(conn, "loan", row)


def _allocation(conn, ws) -> uuid.UUID:
    account = _insert(
        conn,
        "account",
        {
            "workspace_id": ws,
            "label": "Mietkonto",
            "bank": "Beispielbank",
            "iban_masked": "DE00 **** 0000",
            "iban_hmac": hashlib.sha256(uuid.uuid4().bytes).digest(),
            "opening_balance": "0.00",
            "opening_balance_on": "2026-03-31",
        },
    )
    imp = _insert(
        conn,
        "statement_import",
        {
            "workspace_id": ws,
            "account_id": account,
            "document_id": _document(conn, ws)[0],
            "format": "csv_camt",
        },
    )
    tx = _insert(
        conn,
        "transaction",
        {
            "workspace_id": ws,
            "account_id": account,
            "import_id": imp,
            "line_hash": hashlib.sha256(uuid.uuid4().bytes).digest(),
            "booking_date": "2026-09-21",
            "amount": "-1190.00",
            "purpose": "Rechnung Malerarbeiten",
        },
    )
    return _insert(
        conn,
        "allocation",
        {
            "workspace_id": ws,
            "transaction_id": tx,
            "amount": "-1190.00",
            "category": "repairs",
            "method": "user",
            "confidence": "1.000",
        },
    )


@pytest.fixture
def scope(connect, workspaces):
    """(conn, workspace, ids) as the app role, rolled back after the test."""
    a, _ = workspaces
    with connect(APP, a) as conn:
        prop = insert_property(conn, a)
        ids = {
            "property": prop,
            "unit": insert_unit(conn, a, prop),
            "loan": _loan(conn, a, prop),
            "allocation": _allocation(conn, a),
            "document": _document(conn, a)[0],
        }
        yield conn, a, ids


def _row(ws, ids, kind, **scope) -> dict:
    return {"workspace_id": ws, "property_id": ids["property"], "kind": kind} | scope


# One valid row per kind: its scope ids and its value.
VALID = {
    "spread_82b": ({"allocation"}, None, {"years": 3}),
    "renovation_treatment": ({"allocation"}, None, {"treatment": "erhaltung"}),
    "exclude_from_15pct": ({"allocation"}, None, {"reason": "annual_maintenance"}),
    "market_rent": (
        set(),
        None,
        {"valid_from": "2025-08-01", "cold_rent_per_m2": "9.50", "source": "mietspiegel"},
    ),
    "ruecklage_spent": ({"document"}, 2026, {"amount": "350.00"}),
    "interest_certified": ({"loan", "document"}, 2026, {"amount": "3724.56"}),
    "pre_handover_income": (set(), None, {"treatment": "rent"}),
}


def _valid(ws, ids, kind) -> dict:
    keys, year, value = VALID[kind]
    row = _row(ws, ids, kind, value=Jsonb(value), tax_year=year)
    return row | {f"{k}_id": ids[k] for k in keys}


@pytest.mark.parametrize("kind", sorted(VALID))
def test_each_kind_with_its_scope_is_accepted_by_the_table_and_the_schema(scope, kind):
    conn, a, ids = scope
    row = _valid(a, ids, kind)
    _insert(conn, "tax_input", row)
    TaxInputCreate.model_validate(
        {k: v.obj if isinstance(v, Jsonb) else v for k, v in row.items() if k != "workspace_id"}
    )


@pytest.mark.parametrize(
    ("kind", "change"),
    [
        ("spread_82b", {"allocation_id": None}),  # a per-allocation kind without one
        ("market_rent", {"allocation": True}),  # an allocation on a per-flat kind
        ("spread_82b", {"unit": True}),  # a unit outside market_rent
        ("interest_certified", {"loan_id": None}),
        ("ruecklage_spent", {"loan": True}),  # a loan outside interest_certified
        ("interest_certified", {"tax_year": None}),
        ("ruecklage_spent", {"tax_year": None}),
        ("market_rent", {"tax_year": 2026}),  # a year outside the certificate kinds
        ("ruecklage_spent", {"document_id": None}),
        ("interest_certified", {"document_id": None}),
        ("interest_certified", {"tax_year": 1999}),
        ("pre_handover_income", {"withdrawn": True}),  # withdrawn without supersedes_id
        ("market_rent", {"value": Jsonb(["not", "an", "object"])}),
        ("market_rent", {"kind": "rent_guarantee"}),
    ],
)
def test_a_row_outside_its_kinds_scope_is_refused_by_the_table_and_the_schema(scope, kind, change):
    conn, a, ids = scope
    row = _valid(a, ids, kind)
    for key in ("allocation", "unit", "loan"):
        if change.pop(key, False):
            row[f"{key}_id"] = ids[key]
    row |= change
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(conn, "tax_input", row)
    with pytest.raises(ValueError):
        TaxInputCreate.model_validate(
            {k: v.obj if isinstance(v, Jsonb) else v for k, v in row.items() if k != "workspace_id"}
        )


def test_tax_input_is_append_only_even_for_the_owner(connect, workspaces):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        prop = insert_property(conn, a)
        ids = {"property": prop}
        first = _insert(conn, "tax_input", _valid(a, ids, "pre_handover_income"))
        for statement in (
            "UPDATE tax_input SET note = 'x' WHERE id = %s",
            "UPDATE tax_input SET withdrawn = true WHERE id = %s",
            "DELETE FROM tax_input WHERE id = %s",
        ):
            with pytest.raises(errors.RestrictViolation, match="append-only"), conn.transaction():
                conn.execute(statement, (first,))
        with pytest.raises(errors.RestrictViolation, match="append-only"), conn.transaction():
            conn.execute("TRUNCATE tax_input CASCADE")


def test_a_correction_supersedes_one_row_of_the_same_property_and_kind(scope):
    conn, a, ids = scope
    first = _insert(conn, "tax_input", _valid(a, ids, "market_rent"))
    # a withdrawal supersedes the row it ends
    _insert(
        conn,
        "tax_input",
        _valid(a, ids, "market_rent") | {"supersedes_id": first, "withdrawn": True},
    )
    with pytest.raises(errors.UniqueViolation), conn.transaction():  # superseded once only
        _insert(conn, "tax_input", _valid(a, ids, "market_rent") | {"supersedes_id": first})
    other = _insert(conn, "tax_input", _valid(a, ids, "pre_handover_income"))
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():  # another kind
        _insert(conn, "tax_input", _valid(a, ids, "market_rent") | {"supersedes_id": other})
    second_property = insert_property(conn, a, label="Zweite Wohnung")
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():  # another property
        _insert(
            conn,
            "tax_input",
            _valid(a, ids, "pre_handover_income")
            | {"property_id": second_property, "supersedes_id": other},
        )


def test_the_new_columns_have_defaults_and_bounds(scope):
    conn, a, ids = scope
    tenancy = insert_tenancy(conn, a, ids["unit"], "2026-07-01")
    read = "SELECT rent_due_day, operating_costs_terms FROM tenancy WHERE id = %s"
    assert conn.execute(read, (tenancy,)).fetchone() == (3, "prepayment")
    conn.execute(
        "UPDATE tenancy SET rent_due_day = 1, operating_costs_terms = 'included' WHERE id = %s",
        (tenancy,),
    )
    assert conn.execute(read, (tenancy,)).fetchone() == (1, "included")
    for bad in ({"rent_due_day": 0}, {"rent_due_day": 29}, {"operating_costs_terms": "warm"}):
        with pytest.raises(errors.CheckViolation), conn.transaction():
            insert_tenancy(conn, a, ids["unit"], "2027-01-01", **bad)
    area = "SELECT living_area_m2 FROM property WHERE id = %s"
    assert conn.execute(area, (ids["property"],)).fetchone() == (None,)
    conn.execute("UPDATE property SET living_area_m2 = 62.50 WHERE id = %s", (ids["property"],))
    with pytest.raises(errors.CheckViolation), conn.transaction():
        conn.execute("UPDATE property SET living_area_m2 = 0 WHERE id = %s", (ids["property"],))


def _export(conn, ws, property_id, **overrides) -> uuid.UUID:
    _, digest = _document(conn, ws)
    row = {
        "workspace_id": ws,
        "property_id": property_id,
        "tax_year": 2026,
        "kind": "field_sheet",
        "rule_set_version": "2026.1",
        "form_year": 2025,
        "form_status": "provisional",
        "draft": Jsonb({"tax_year": 2026}),
        "file_sha256": digest,
        "size_bytes": 2048,
    } | overrides
    return _insert(conn, "tax_export", row)


def test_tax_export_is_immutable_but_may_be_deleted(connect, workspaces):
    a, _ = workspaces
    with connect(OWNER, a) as conn:  # the owner holds UPDATE; the trigger still refuses
        export = _export(conn, a, insert_property(conn, a))
        with pytest.raises(errors.RestrictViolation, match="append-only"), conn.transaction():
            conn.execute("UPDATE tax_export SET size_bytes = 1 WHERE id = %s", (export,))
    with connect(APP, a) as conn:
        export = _export(conn, a, insert_property(conn, a))
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            conn.execute("UPDATE tax_export SET size_bytes = 1 WHERE id = %s", (export,))
        conn.execute("DELETE FROM tax_export WHERE id = %s", (export,))  # the retention sweeper


@pytest.mark.parametrize(
    "bad",
    [
        {"form_status": "published"},  # published is the tax year's own form
        {"form_year": 2027},  # a form from after the tax year
        {"form_status": "draft"},
        {"kind": "zip"},
        {"rule_set_version": ""},
        {"size_bytes": 0},
        {"draft": Jsonb([])},
        {"file_sha256": b"short"},
    ],
)
def test_tax_export_checks(scope, bad):
    conn, a, ids = scope
    with pytest.raises((errors.CheckViolation, errors.ForeignKeyViolation)), conn.transaction():
        _export(conn, a, ids["property"], **bad)


@pytest.mark.parametrize("data", ["tax_input", "tax_export", "living_area", "due_day", "terms"])
def test_0013_downgrade_refuses_while_it_would_drop_data(data):
    """A downgrade never drops data: tax inputs and exports are records, and the three
    columns' non-default values would be lost. The guard must see rows under forced RLS."""
    name = create_database()
    try:
        alembic(name, "upgrade", "0013_tax_year")
        ws = uuid.uuid4()
        with _admin(name) as conn:
            conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'A')", (ws,))
            conn.execute(f"SET ROLE {OWNER}")
            conn.execute("SELECT set_config('app.workspace_id', %s, false)", (str(ws),))
            prop = insert_property(conn, ws)
            unit = insert_unit(conn, ws, prop)
            if data == "tax_input":
                _insert(conn, "tax_input", _valid(ws, {"property": prop}, "pre_handover_income"))
            elif data == "tax_export":
                _export(conn, ws, prop)
            elif data == "living_area":
                conn.execute("UPDATE property SET living_area_m2 = 62.50")
            elif data == "due_day":
                insert_tenancy(conn, ws, unit, "2026-07-01", rent_due_day=1)
            else:
                insert_tenancy(conn, ws, unit, "2026-07-01", operating_costs_terms="flat_rate")
        with pytest.raises(DBAPIError, match="downgrade of 0013_tax_year refused"):
            alembic(name, "downgrade", "0012_mgmt_category_last_split")
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [
                ("0013_tax_year",)
            ]
            for table in ("tax_input", "tax_export", "property", "tenancy"):
                forced = conn.execute(
                    "SELECT relforcerowsecurity FROM pg_class WHERE oid = %s::regclass", (table,)
                ).fetchone()
                assert forced == (True,)  # the refused downgrade's NO FORCE was rolled back
            if data == "tax_export":
                conn.execute("DELETE FROM tax_export")
            elif data == "living_area":
                conn.execute("UPDATE property SET living_area_m2 = NULL")
            elif data in ("due_day", "terms"):
                conn.execute("UPDATE tenancy SET rent_due_day = 3, operating_costs_terms = DEFAULT")
        if data != "tax_input":  # tax inputs are append-only, even for the owner
            alembic(name, "downgrade", "0012_mgmt_category_last_split")
            with _admin(name) as conn:
                tables = conn.execute(
                    "SELECT count(*) FROM pg_tables WHERE tablename IN ('tax_input', 'tax_export')"
                ).fetchone()
                assert tables == (0,)
            alembic(name, "upgrade", "head")
    finally:
        drop_database(name)
