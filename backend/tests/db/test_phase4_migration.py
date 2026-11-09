"""Migration 0016 (docs/phases/phase-4.md §6): the two owner columns, income_input (append-only,
scope per kind), projection_scenario (append-only), their grants, the carry-over of
owner.marginal_tax_rate (D90) and the downgrade guard.

RLS, workspace_id and the table list are covered for every table by test_migration.py; the
enum CHECKs (owner.tax_state, income_input.kind) by test_schema_consistency.py."""

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

from hausbuch.schemas.performance import IncomeInputCreate, Levers, ScenarioCreate

PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
EXPECTED_GRANTS = {
    "income_input": {"SELECT", "INSERT"},  # append-only
    "projection_scenario": {"SELECT", "INSERT"},  # append-only
}
PREVIOUS = "0015_movable_asset"
# Built at import, so a schema drift fails collection even where the DB tests skip.
LEVERS = Levers(sale_year=2036).model_dump(mode="json")


@pytest.mark.parametrize("table", sorted(EXPECTED_GRANTS))
def test_app_role_grants(connect, table):
    with connect(OWNER) as conn:
        granted = {
            p
            for p in PRIVILEGES
            if conn.execute("SELECT has_table_privilege(%s, %s, %s)", (APP, table, p)).fetchone()[0]
        }
        assert granted == EXPECTED_GRANTS[table]


def test_the_typed_rate_column_is_gone(connect):
    with connect(OWNER) as conn:
        columns = {
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'owner'"
            )
        }
    assert "marginal_tax_rate" not in columns
    assert {"church_tax", "tax_state"} <= columns


# --- Seed helpers ----------------------------------------------------------------------


def _document(conn, ws) -> uuid.UUID:
    digest = hashlib.sha256(uuid.uuid4().bytes).digest()
    conn.execute(
        "INSERT INTO document_file (workspace_id, sha256, size_bytes, media_type, storage_key)"
        " VALUES (%s, %s, 11, 'application/pdf', %s)",
        (ws, digest, f"{ws}/sha256/{digest.hex()}"),
    )
    row = {"workspace_id": ws, "file_sha256": digest, "source": "upload"}
    return _insert(conn, "document", row)


def _owner(conn, ws, **overrides) -> uuid.UUID:
    return _insert(conn, "owner", {"workspace_id": ws, "full_name": "Erika Muster"} | overrides)


@pytest.fixture
def scope(connect, workspaces):
    """(conn, workspace, ids) as the app role, rolled back after the test."""
    a, _ = workspaces
    with connect(APP, a) as conn:
        ids = {
            "owner": _owner(conn, a),
            "document": _document(conn, a),
            "property": insert_property(conn, a),
        }
        yield conn, a, ids


# One valid row per kind: its scope ids and its value.
VALID = {
    "expected_gross_pay": ({"owner"}, {"amount": "60000.00"}),
    "deductions_override": ({"owner"}, {"amount": "12966.00"}),
    "bescheid_zve": ({"document"}, {"amount": "87948.66"}),
    "marginal_rate_override": (set(), {"rate": "0.4200"}),
}


def _valid(ws, ids, kind, **overrides) -> dict:
    keys, value = VALID[kind]
    row = {"workspace_id": ws, "kind": kind, "tax_year": 2026, "value": Jsonb(value)}
    return row | {f"{k}_id": ids[k] for k in keys} | overrides


def _as_schema(row: dict) -> dict:
    return {k: v.obj if isinstance(v, Jsonb) else v for k, v in row.items() if k != "workspace_id"}


@pytest.mark.parametrize("kind", sorted(VALID))
def test_each_kind_with_its_scope_is_accepted_by_the_table_and_the_schema(scope, kind):
    conn, a, ids = scope
    row = _valid(a, ids, kind)
    _insert(conn, "income_input", row)
    IncomeInputCreate.model_validate(_as_schema(row))


@pytest.mark.parametrize(
    ("kind", "change"),
    [
        ("expected_gross_pay", {"owner_id": None}),  # a per-owner kind without its owner
        ("deductions_override", {"owner_id": None}),
        ("bescheid_zve", {"owner": True}),  # a household kind with an owner
        ("marginal_rate_override", {"owner": True}),
        ("bescheid_zve", {"document_id": None}),  # the Bescheid is required
        ("marginal_rate_override", {"tax_year": None}),  # every row has its year
        ("marginal_rate_override", {"tax_year": 1999}),
        ("marginal_rate_override", {"withdrawn": True}),  # withdrawn without supersedes_id
        ("marginal_rate_override", {"kind": "church_tax_rate"}),
    ],
)
def test_a_row_outside_its_kinds_scope_is_refused_by_the_table_and_the_schema(scope, kind, change):
    conn, a, ids = scope
    row = _valid(a, ids, kind)
    if change.pop("owner", False):
        row["owner_id"] = ids["owner"]
    row |= change
    with pytest.raises((errors.CheckViolation, errors.NotNullViolation)), conn.transaction():
        _insert(conn, "income_input", row)
    with pytest.raises(ValueError):
        IncomeInputCreate.model_validate(_as_schema(row))


def test_value_must_be_a_json_object(scope):
    conn, a, ids = scope
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(conn, "income_input", _valid(a, ids, "bescheid_zve", value=Jsonb(["87948.66"])))


def test_income_input_is_append_only_even_for_the_owner(connect, workspaces):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        first = _insert(conn, "income_input", _valid(a, {}, "marginal_rate_override"))
        for statement in (
            "UPDATE income_input SET note = 'x' WHERE id = %s",
            "UPDATE income_input SET withdrawn = true WHERE id = %s",
            "DELETE FROM income_input WHERE id = %s",
        ):
            with pytest.raises(errors.RestrictViolation, match="append-only"), conn.transaction():
                conn.execute(statement, (first,))
        with pytest.raises(errors.RestrictViolation, match="append-only"), conn.transaction():
            conn.execute("TRUNCATE income_input CASCADE")


def test_an_income_correction_supersedes_one_row_of_the_same_kind_and_year(scope):
    conn, a, ids = scope
    first = _insert(conn, "income_input", _valid(a, ids, "expected_gross_pay"))
    _insert(
        conn,
        "income_input",
        _valid(a, ids, "expected_gross_pay", supersedes_id=first, withdrawn=True),
    )
    with pytest.raises(errors.UniqueViolation), conn.transaction():  # superseded once only
        _insert(conn, "income_input", _valid(a, ids, "expected_gross_pay", supersedes_id=first))
    other = _insert(conn, "income_input", _valid(a, ids, "marginal_rate_override"))
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():  # another kind
        _insert(conn, "income_input", _valid(a, ids, "expected_gross_pay", supersedes_id=other))
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():  # another year
        _insert(
            conn,
            "income_input",
            _valid(a, ids, "marginal_rate_override", tax_year=2027, supersedes_id=other),
        )
    with pytest.raises(errors.CheckViolation), conn.transaction():  # not itself
        own = uuid.uuid4()
        _insert(
            conn,
            "income_input",
            _valid(a, ids, "marginal_rate_override", id=own, supersedes_id=own),
        )


def _scenario(ws, property_id, **overrides) -> dict:
    row = {
        "workspace_id": ws,
        "property_id": property_id,
        "name": "Basis",
        "is_default": True,
        "levers": Jsonb(LEVERS),
    }
    return row | overrides


def test_a_scenario_row_is_accepted_by_the_table_and_the_schema(scope):
    conn, a, ids = scope
    row = _scenario(a, ids["property"])
    _insert(conn, "projection_scenario", row)
    ScenarioCreate.model_validate(_as_schema(row))


@pytest.mark.parametrize(
    "bad",
    [
        {"name": ""},
        {"name": "x" * 101},
        {"levers": Jsonb([])},
        {"withdrawn": True},  # withdrawn without supersedes_id
    ],
)
def test_scenario_checks(scope, bad):
    conn, a, ids = scope
    row = _scenario(a, ids["property"], **bad)
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(conn, "projection_scenario", row)


def test_a_scenario_correction_stays_on_its_property_and_rows_are_append_only(connect, workspaces):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        prop = insert_property(conn, a)
        first = _insert(conn, "projection_scenario", _scenario(a, prop))
        _insert(conn, "projection_scenario", _scenario(a, prop, supersedes_id=first))
        with pytest.raises(errors.UniqueViolation), conn.transaction():
            _insert(conn, "projection_scenario", _scenario(a, prop, supersedes_id=first))
        # an uncorrected row, so the UNIQUE on supersedes_id cannot fire before the FK
        open_row = _insert(conn, "projection_scenario", _scenario(a, prop))
        second = insert_property(conn, a, label="Zweite Wohnung")
        with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
            _insert(conn, "projection_scenario", _scenario(a, second, supersedes_id=open_row))
        for statement in (
            "UPDATE projection_scenario SET is_default = false WHERE id = %s",
            "DELETE FROM projection_scenario WHERE id = %s",
        ):
            with pytest.raises(errors.RestrictViolation, match="append-only"), conn.transaction():
                conn.execute(statement, (first,))
        with pytest.raises(errors.RestrictViolation, match="append-only"), conn.transaction():
            conn.execute("TRUNCATE projection_scenario CASCADE")


def test_owner_church_tax_needs_a_known_state(scope):
    conn, a, ids = scope
    read = "SELECT church_tax, tax_state FROM owner WHERE id = %s"
    assert conn.execute(read, (ids["owner"],)).fetchone() == (False, None)
    conn.execute(
        "UPDATE owner SET church_tax = true, tax_state = 'BY' WHERE id = %s", (ids["owner"],)
    )
    assert conn.execute(read, (ids["owner"],)).fetchone() == (True, "BY")
    for bad in ({"church_tax": True}, {"tax_state": "XX"}, {"tax_state": "by"}):
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _owner(conn, a, **bad)


# --- D90: the carry-over, and the downgrade guard --------------------------------------


def test_0016_carries_one_agreed_rate_per_workspace_over_then_drops_the_column():
    """One marginal_rate_override for 2026 where the owners agree on one rate (nulls do not
    count); none where they disagree or hold none. The row validates as an IncomeInputCreate."""
    name = create_database()
    try:
        alembic(name, "upgrade", PREVIOUS)
        agreed, split, empty = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        with _admin(name) as conn:  # the superuser bypasses RLS
            for ws, rates in (
                (agreed, ("0.4200", "0.42", None)),
                (split, ("0.4200", "0.3500")),
                (empty, (None,)),
            ):
                conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'W')", (ws,))
                for rate in rates:
                    _owner(conn, ws, marginal_tax_rate=rate)
        alembic(name, "upgrade", "0016_performance")
        with _admin(name) as conn:
            rows = conn.execute(
                "SELECT workspace_id, owner_id, kind, tax_year, document_id, value, note,"
                " supersedes_id, withdrawn FROM income_input"
            ).fetchall()
            assert rows == [
                (
                    agreed,
                    None,
                    "marginal_rate_override",
                    2026,
                    None,
                    {"rate": "0.4200"},
                    "übernommen aus Eigentümer",
                    None,
                    False,
                )
            ]
            fact = dict(
                zip(
                    ("owner_id", "kind", "tax_year", "document_id", "value", "note"),
                    rows[0][1:7],
                    strict=True,
                )
            )
            IncomeInputCreate.model_validate(fact)
            column = conn.execute(
                "SELECT 1 FROM information_schema.columns"
                " WHERE table_name = 'owner' AND column_name = 'marginal_tax_rate'"
            ).fetchone()
            assert column is None
            forced = conn.execute(
                "SELECT relforcerowsecurity FROM pg_class WHERE oid = 'owner'::regclass"
            ).fetchone()
            assert forced == (True,)  # the carry-over's NO FORCE was undone
    finally:
        drop_database(name)


@pytest.mark.parametrize("data", ["income_input", "scenario", "church_tax", "tax_state"])
def test_0016_downgrade_refuses_while_it_would_drop_data(data):
    """A downgrade never drops data: income inputs and scenarios are records, and an owner's
    church tax and Bundesland would be lost. The guard must see rows under forced RLS."""
    name = create_database()
    try:
        alembic(name, "upgrade", "0016_performance")
        ws = uuid.uuid4()
        with _admin(name) as conn:
            conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'A')", (ws,))
            conn.execute(f"SET ROLE {OWNER}")
            conn.execute("SELECT set_config('app.workspace_id', %s, false)", (str(ws),))
            owner = _owner(conn, ws)
            if data == "income_input":
                _insert(conn, "income_input", _valid(ws, {}, "marginal_rate_override"))
            elif data == "scenario":
                _insert(conn, "projection_scenario", _scenario(ws, insert_property(conn, ws)))
            elif data == "church_tax":
                conn.execute(
                    "UPDATE owner SET church_tax = true, tax_state = 'NW' WHERE id = %s", (owner,)
                )
            else:
                conn.execute("UPDATE owner SET tax_state = 'NW' WHERE id = %s", (owner,))
        with pytest.raises(DBAPIError, match="downgrade of 0016_performance refused"):
            alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [
                ("0016_performance",)
            ]
            for table in ("income_input", "projection_scenario", "owner"):
                forced = conn.execute(
                    "SELECT relforcerowsecurity FROM pg_class WHERE oid = %s::regclass", (table,)
                ).fetchone()
                assert forced == (True,)  # the refused downgrade's NO FORCE was rolled back
            if data in ("church_tax", "tax_state"):
                conn.execute("UPDATE owner SET church_tax = false, tax_state = NULL")
        if data in ("church_tax", "tax_state"):  # income inputs and scenarios are append-only
            alembic(name, "downgrade", PREVIOUS)
            with _admin(name) as conn:
                tables = conn.execute(
                    "SELECT count(*) FROM pg_tables"
                    " WHERE tablename IN ('income_input', 'projection_scenario')"
                ).fetchone()
                assert tables == (0,)
                restored = conn.execute(
                    "SELECT marginal_tax_rate FROM owner WHERE id = %s", (owner,)
                ).fetchone()
                assert restored == (None,)  # restored empty
            alembic(name, "upgrade", "head")
    finally:
        drop_database(name)
