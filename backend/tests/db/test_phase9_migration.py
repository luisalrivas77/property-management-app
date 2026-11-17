"""Migration 0048 (docs/phases/phase-9.md §11): `workspace.advice_mode`, the append-only tables
`household_scenario`, `health_snapshot` and `plan_explanation`, their CHECKs, the grants, the
widened event entity types, workspace isolation and the downgrade guard.

RLS, workspace_id and the table list are covered for every table by test_migration.py, the
round trip there too. Invented values only."""

import hashlib
import uuid
from datetime import date

import pytest
from db.db_support import (
    APP,
    OWNER,
    _admin,
    _insert,
    alembic,
    create_database,
    drop_database,
    insert_event,
)
from psycopg import errors
from psycopg.types.json import Jsonb
from sqlalchemy.exc import DBAPIError

HEAD = "0048_household_plan"
PREVIOUS = "0047_elster_hersteller_id"
TABLES = ("household_scenario", "health_snapshot", "plan_explanation")
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")


def _scenario(ws, **overrides) -> dict:
    return {"workspace_id": ws, "name": "Standard", "levers": Jsonb({})} | overrides


def _snapshot(ws, **overrides) -> dict:
    return {
        "workspace_id": ws,
        "month_end": date(2027, 5, 31),
        "net_worth_run_id": uuid.uuid4(),
        "figures": Jsonb({"month": "2027-05"}),
        "rule_version": "health-1",
    } | overrides


def _explanation(ws, **overrides) -> dict:
    return {
        "workspace_id": ws,
        "advice_mode": "own_use",
        "status": "model",
        "facts": Jsonb({"mode": "own_use"}),
        "facts_sha256": hashlib.sha256(b"facts").digest(),
        "paragraphs": Jsonb([]),
        "validator_version": "v1",
        "lexicon_version": "v1",
        "vocabulary_version": "v1",
    } | overrides


ROWS = {
    "household_scenario": _scenario,
    "health_snapshot": _snapshot,
    "plan_explanation": _explanation,
}


@pytest.fixture
def app_conn(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        yield conn, a


@pytest.mark.parametrize("table", TABLES)
def test_app_role_may_only_read_and_insert(connect, table):
    with connect(OWNER) as conn:
        granted = {
            p
            for p in PRIVILEGES
            if conn.execute("SELECT has_table_privilege(%s, %s, %s)", (APP, table, p)).fetchone()[0]
        }
    assert granted == {"SELECT", "INSERT"}


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize(
    "statement",
    ["UPDATE {t} SET created_at = now() WHERE id = %s", "DELETE FROM {t} WHERE id = %s"],
)
def test_rows_are_append_only_even_for_the_owner(connect, workspaces, table, statement):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        row = _insert(conn, table, ROWS[table](a))
        with pytest.raises(errors.RestrictViolation, match="append-only"):
            conn.execute(statement.format(t=table), (row,))


@pytest.mark.parametrize("table", TABLES)
def test_a_table_cannot_be_truncated(connect, workspaces, table):
    with connect(OWNER, workspaces[0]) as conn, pytest.raises(errors.RestrictViolation):
        conn.execute(f"TRUNCATE {table} CASCADE")  # past the foreign key


@pytest.mark.parametrize("table", TABLES)
def test_a_row_is_invisible_to_another_workspace(connect, workspaces, table):
    a, b = workspaces
    with connect(APP, a) as conn:
        _insert(conn, table, ROWS[table](a))
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone() == (1,)
        conn.execute("SELECT set_config('app.workspace_id', %s, false)", (str(b),))
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            _insert(conn, table, ROWS[table](a))


def test_advice_mode_defaults_to_own_use_and_takes_two_values(connect, workspaces):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        assert conn.execute("SELECT advice_mode FROM workspace WHERE id = %s", (a,)).fetchone() == (
            "own_use",
        )
        conn.execute("UPDATE workspace SET advice_mode = 'generic' WHERE id = %s", (a,))
        with pytest.raises(errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE workspace SET advice_mode = 'advice' WHERE id = %s", (a,))


def test_a_scenario_supersedes_and_withdraws_within_its_workspace(app_conn, connect, workspaces):
    conn, a = app_conn
    first = _insert(conn, "household_scenario", _scenario(a, is_default=True))
    _insert(conn, "household_scenario", _scenario(a, supersedes_id=first, withdrawn=True))
    with pytest.raises(errors.UniqueViolation), conn.transaction():
        _insert(conn, "household_scenario", _scenario(a, supersedes_id=first))
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(conn, "household_scenario", _scenario(a, withdrawn=True))
    _, b = workspaces
    with connect(APP, b) as other, pytest.raises(errors.ForeignKeyViolation):
        _insert(other, "household_scenario", _scenario(b, supersedes_id=uuid.uuid4()))


@pytest.mark.parametrize(
    "change",
    [
        {"name": ""},
        {"name": "x" * 101},
        {"levers": Jsonb([])},
    ],
    ids=["empty_name", "long_name", "levers_not_an_object"],
)
def test_a_scenario_outside_its_checks_is_refused(app_conn, change):
    conn, a = app_conn
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(conn, "household_scenario", _scenario(a, **change))


@pytest.mark.parametrize(
    "change",
    [
        {"month_end": date(2027, 5, 30)},
        {"month_end": date(2027, 2, 27)},
        {"figures": Jsonb([])},
        {"rule_version": ""},
    ],
    ids=["not_a_month_end", "february_27", "figures_not_an_object", "no_rule_version"],
)
def test_a_snapshot_outside_its_checks_is_refused(app_conn, change):
    conn, a = app_conn
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(conn, "health_snapshot", _snapshot(a, **change))


def test_a_snapshot_takes_every_month_end(app_conn):
    conn, a = app_conn
    for month_end in (date(2027, 2, 28), date(2028, 2, 29), date(2027, 4, 30), date(2027, 12, 31)):
        _insert(conn, "health_snapshot", _snapshot(a, month_end=month_end))


@pytest.mark.parametrize(
    "change",
    [
        {"advice_mode": "advice"},
        {"status": "template"},  # a template without a reason
        {"reason": "rejected"},  # a model text with a reason
        {"status": "template", "reason": "unknown"},
        {"facts_sha256": b"short"},
        {"facts": Jsonb([])},
        {"paragraphs": Jsonb({})},
        {"violations": Jsonb({})},
        {"labels": Jsonb([])},
    ],
    ids=[
        "mode",
        "template_without_reason",
        "model_with_reason",
        "unknown_reason",
        "hash_not_32_bytes",
        "facts_not_an_object",
        "paragraphs_not_a_list",
        "violations_not_a_list",
        "labels_not_an_object",
    ],
)
def test_an_explanation_outside_its_checks_is_refused(app_conn, change):
    conn, a = app_conn
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(conn, "plan_explanation", _explanation(a, **change))


def test_an_explanation_names_a_scenario_of_its_workspace(app_conn):
    conn, a = app_conn
    scenario = _insert(conn, "household_scenario", _scenario(a))
    _insert(
        conn,
        "plan_explanation",
        _explanation(a, scenario_id=scenario, status="template", reason="consent_off"),
    )
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
        _insert(conn, "plan_explanation", _explanation(a, scenario_id=uuid.uuid4()))
    defaults = conn.execute(
        "SELECT labels, violations, ai_call_ids FROM plan_explanation WHERE scenario_id = %s",
        (scenario,),
    ).fetchone()
    assert defaults == ({}, [], [])


@pytest.mark.parametrize("entity_type", ["household_scenario", "health", "plan"])
def test_events_take_the_new_entity_types(app_conn, entity_type):
    conn, a = app_conn
    insert_event(conn, a, type=f"{entity_type}.created", entity_type=entity_type)


# --- the downgrade -------------------------------------------------------------------------


def _new_workspace(name) -> uuid.UUID:
    ws = uuid.uuid4()
    with _admin(name) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'A')", (ws,))
    return ws


def _as_owner(conn, ws) -> None:
    conn.execute(f"SET ROLE {OWNER}")
    conn.execute("SELECT set_config('app.workspace_id', %s, false)", (str(ws),))


def _refused(name) -> None:
    with pytest.raises(DBAPIError, match=f"downgrade of {HEAD} refused") as refused:
        alembic(name, "downgrade", PREVIOUS)
    assert isinstance(refused.value.orig, errors.RestrictViolation)
    with _admin(name) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
        for table in TABLES:
            forced = conn.execute(
                "SELECT relforcerowsecurity FROM pg_class WHERE oid = %s::regclass", (table,)
            ).fetchone()
            assert forced == (True,), table  # the refused downgrade's NO FORCE was rolled back


@pytest.mark.parametrize("table", TABLES)
def test_0048_downgrade_refuses_while_a_table_holds_a_row(table):
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            _insert(conn, table, ROWS[table](ws))
        _refused(name)
    finally:
        drop_database(name)


def test_0048_downgrade_refuses_while_an_event_names_a_new_entity_type():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            insert_event(conn, ws, type="health.snapshot_stored", entity_type="health")
        _refused(name)
    finally:
        drop_database(name)


def test_0048_downgrade_refuses_while_a_workspace_is_generic():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            conn.execute("UPDATE workspace SET advice_mode = 'generic' WHERE id = %s", (ws,))
        _refused(name)
    finally:
        drop_database(name)


def test_0048_downgrade_without_data_drops_the_tables_and_the_column():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        _new_workspace(name)  # an own_use workspace is no reason to refuse
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            assert conn.execute(
                "SELECT count(*) FROM pg_tables WHERE tablename = ANY(%s)", (list(TABLES),)
            ).fetchone() == (0,)
            assert conn.execute(
                "SELECT count(*) FROM information_schema.columns"
                " WHERE table_name = 'workspace' AND column_name = 'advice_mode'"
            ).fetchone() == (0,)
        alembic(name, "upgrade", HEAD)
    finally:
        drop_database(name)
