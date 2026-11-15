"""Migration 0037 (docs/phases/phase-6.md D159 amended (7) and (8) 15 Nov 2026): hh_car_purchase
and hh_wage_replacement in allocation.category, matching_rule.category and
matching_rule.categories, and the downgrade guard. The enum CHECKs against AllocationCategory
are test_schema_consistency.py's. Synthetic data only."""

import pytest
from db.db_support import OWNER, _admin, alembic, create_database, drop_database
from db.test_phase2_migrations import _allocation, _rule, _transaction
from db.test_phase6_migration import _account, _as_owner, _forced, _import, _new_workspace
from psycopg import errors
from psycopg.types.json import Jsonb
from sqlalchemy.exc import DBAPIError

HEAD = "0037_car_and_wage_replacement"
PREVIOUS = "0036_investment_subcategories"


def test_both_codes_are_accepted_and_no_other(connect, workspaces):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        private = _account(conn, a, purpose="private")
        imported = _import(conn, a, private)
        car = _transaction(conn, a, private, imported, amount="-2400.00", purpose="Schlussrate")
        _allocation(conn, a, car, amount="-2400.00", category="hh_car_purchase", method="user")
        pay = _transaction(
            conn, a, private, imported, b"l2", amount="812.40", purpose="Elterngeld 03/2026"
        )
        _allocation(conn, a, pay, amount="812.40", category="hh_wage_replacement", method="user")
        _rule(conn, a, {"purpose_pattern": "autobank"}, category="hh_car_purchase")
        _rule(conn, a, {"purpose_pattern": "elterngeld"}, category="hh_wage_replacement")
        _rule(
            conn,
            a,
            {"purpose_pattern": "autohaus"},
            split_method="as_last_split",
            category=None,
            categories=Jsonb(["hh_car_purchase", "hh_car_service"]),
        )
        other = _transaction(conn, a, private, imported, b"l3", amount="-5.00", purpose="Kino")
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _allocation(conn, a, other, amount="-5.00", category="hh_car_loan", method="user")


def _category_check(conn) -> str:
    return conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE conname = 'allocation_category_check'"
    ).fetchone()[0]


def test_0037_runs_up_down_and_up_on_an_empty_database():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        with _admin(name) as conn:
            check = _category_check(conn)
            assert "hh_car_purchase" in check and "hh_wage_replacement" in check
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            check = _category_check(conn)
            assert "hh_investment_savings" in check
            assert "hh_car_purchase" not in check and "hh_wage_replacement" not in check
        alembic(name, "upgrade", HEAD)
    finally:
        drop_database(name)


def _seed(conn, ws, data) -> None:
    private = _account(conn, ws, purpose="private")
    if data == "car":
        tx = _transaction(conn, ws, private, _import(conn, ws, private))
        _allocation(conn, ws, tx, category="hh_car_purchase", method="user")
    elif data == "wage":
        tx = _transaction(conn, ws, private, _import(conn, ws, private))
        _allocation(conn, ws, tx, category="hh_wage_replacement", method="user")
    elif data == "rule":
        _rule(conn, ws, {"purpose_pattern": "elterngeld"}, category="hh_wage_replacement")
    elif data == "rule_parts":
        _rule(
            conn,
            ws,
            {"purpose_pattern": "autohaus"},
            split_method="as_last_split",
            category=None,
            categories=Jsonb(["hh_car_service", "hh_car_purchase"]),
        )


DATA = {
    "car": "an allocation uses a category of 0037",
    "wage": "an allocation uses a category of 0037",
    "rule": "a learned rule uses a category of 0037",
    "rule_parts": "a learned rule uses a category of 0037",
}


@pytest.mark.parametrize(("data", "reason"), DATA.items())
def test_0037_downgrade_refuses_while_a_new_code_is_used(data, reason):
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
