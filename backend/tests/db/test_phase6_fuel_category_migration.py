"""Migration 0031 (docs/phases/phase-6.md D159 amended 14 Nov 2026): hh_fuel in
allocation.category, matching_rule.category and matching_rule.categories, and the downgrade
guard. The enum CHECKs against AllocationCategory are test_schema_consistency.py's. Synthetic
data only."""

import pytest
from db.db_support import OWNER, _admin, alembic, create_database, drop_database
from db.test_phase2_migrations import _allocation, _rule, _transaction
from db.test_phase6_migration import _account, _as_owner, _forced, _import, _new_workspace
from psycopg import errors
from psycopg.types.json import Jsonb
from sqlalchemy.exc import DBAPIError

HEAD = "0031_fuel_category"
PREVIOUS = "0030_balance_correction"


def test_fuel_is_accepted_and_no_other(connect, workspaces):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        private = _account(conn, a, purpose="private")
        imported = _import(conn, a, private)
        tx = _transaction(conn, a, private, imported, amount="-64.20", purpose="Tankstelle")
        _allocation(conn, a, tx, amount="-64.20", category="hh_fuel", method="user")
        _rule(conn, a, {"purpose_pattern": "tankstelle"}, category="hh_fuel")
        _rule(
            conn,
            a,
            {"purpose_pattern": "raststaette"},
            split_method="as_last_split",
            category=None,
            categories=Jsonb(["hh_fuel", "hh_dining"]),
        )
        other = _transaction(conn, a, private, imported, b"l2", amount="-5.00", purpose="Wäsche")
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _allocation(conn, a, other, amount="-5.00", category="hh_car_wash", method="user")


def _category_check(conn) -> str:
    return conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE conname = 'allocation_category_check'"
    ).fetchone()[0]


def test_0031_runs_up_down_and_up_on_an_empty_database():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        with _admin(name) as conn:
            assert "hh_fuel" in _category_check(conn)
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            check = _category_check(conn)
            assert "hh_refund" in check and "hh_fuel" not in check
        alembic(name, "upgrade", HEAD)
    finally:
        drop_database(name)


def _seed(conn, ws, data) -> None:
    private = _account(conn, ws, purpose="private")
    if data == "category":
        tx = _transaction(conn, ws, private, _import(conn, ws, private))
        _allocation(conn, ws, tx, category="hh_fuel", method="user")
    elif data == "rule":
        _rule(conn, ws, {"purpose_pattern": "tankstelle"}, category="hh_fuel")
    elif data == "rule_parts":
        _rule(
            conn,
            ws,
            {"purpose_pattern": "raststaette"},
            split_method="as_last_split",
            category=None,
            categories=Jsonb(["hh_dining", "hh_fuel"]),
        )


DATA = {
    "category": "an allocation uses a category of 0031",
    "rule": "a learned rule uses a category of 0031",
    "rule_parts": "a learned rule uses a category of 0031",
}


@pytest.mark.parametrize(("data", "reason"), DATA.items())
def test_0031_downgrade_refuses_while_fuel_is_used(data, reason):
    """A downgrade never leaves a row the old CHECKs refuse; the guard sees rows under forced
    RLS."""
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            _seed(conn, ws, data)
            forced = _forced(conn)
        with pytest.raises(DBAPIError, match=f"downgrade of {HEAD} refused: {reason}") as refused:
            alembic(name, "downgrade", PREVIOUS)
        assert isinstance(refused.value.orig, errors.RestrictViolation)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
            assert _forced(conn) == forced
    finally:
        drop_database(name)
