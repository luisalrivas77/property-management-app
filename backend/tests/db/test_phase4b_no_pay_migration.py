"""Migration 0038 (docs/phases/phase-4b.md D195, 15 Nov 2026): the return_input kind `no_pay`
(„Kein Arbeitslohn in <Jahr>“) in the kind CHECK and the not-carried CHECK, and the downgrade
guard. The kind CHECK against ReturnInputKind is test_schema_consistency.py's. Synthetic data
only."""

import pytest
from db.db_support import APP, _admin, _insert, alembic, create_database, drop_database
from db.test_phase4b_migration import _as_schema, _owner
from db.test_phase6_migration import _as_owner, _forced, _new_workspace
from psycopg import errors
from psycopg.types.json import Jsonb
from sqlalchemy.exc import DBAPIError

from hausbuch.schemas.employment import ReturnInputCreate

HEAD = "0038_no_pay_marker"
PREVIOUS = "0037_car_and_wage_replacement"


def _no_pay(ws, owner, **overrides) -> dict:
    return {
        "workspace_id": ws,
        "owner_id": owner,
        "kind": "no_pay",
        "tax_year": 2026,
        "value": Jsonb({}),
    } | overrides


def test_a_marker_is_accepted_withdrawn_and_never_carried(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        owner = _owner(conn, a)
        first = _insert(conn, "return_input", _no_pay(a, owner))
        _insert(conn, "return_input", _no_pay(a, owner, supersedes_id=first, withdrawn=True))
        last_year = _insert(conn, "return_input", _no_pay(a, owner, tax_year=2025))
        row = _no_pay(a, owner, carried_from_id=last_year)
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _insert(conn, "return_input", row)
        with pytest.raises(ValueError):
            ReturnInputCreate.model_validate(_as_schema(row))
        with pytest.raises(errors.CheckViolation), conn.transaction():  # an owner kind
            _insert(conn, "return_input", _no_pay(a, None))


def _checks(conn) -> tuple[str, str]:
    rows = dict(
        conn.execute(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conname IN ('return_input_kind_check', 'return_input_check5')"
        ).fetchall()
    )
    return rows["return_input_kind_check"], rows["return_input_check5"]


def test_0038_runs_up_down_and_up_on_an_empty_database():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        with _admin(name) as conn:
            kinds, not_carried = _checks(conn)
            assert "no_pay" in kinds and "no_pay" in not_carried
            assert "special_expense" in kinds and "child" in not_carried
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            kinds, not_carried = _checks(conn)
            assert "no_pay" not in kinds and "no_pay" not in not_carried
            assert "special_expense" in kinds and "child" in not_carried
        alembic(name, "upgrade", HEAD)
    finally:
        drop_database(name)


def test_0038_downgrade_refuses_while_a_marker_exists():
    """A downgrade never leaves a row the old CHECK refuses; the guard sees rows under forced
    RLS, and a withdrawn marker still counts (both rows stay)."""
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            owner = _owner(conn, ws)
            first = _insert(conn, "return_input", _no_pay(ws, owner))
            _insert(conn, "return_input", _no_pay(ws, owner, supersedes_id=first, withdrawn=True))
            forced = _forced(conn)
        with pytest.raises(
            DBAPIError, match=f"downgrade of {HEAD} refused: a return input of kind no_pay"
        ) as refused:
            alembic(name, "downgrade", PREVIOUS)
        assert isinstance(refused.value.orig, errors.RestrictViolation)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
            assert _forced(conn) == forced
            assert "return_input" in forced
    finally:
        drop_database(name)
