"""Migration 0029 (docs/phases/phase-6.md D159 amended 13 Nov 2026): the finer household
categories in allocation.category, matching_rule.category and matching_rule.categories, and the
downgrade guard. The enum CHECKs against AllocationCategory are test_schema_consistency.py's.
Synthetic data only."""

import pytest
from db.db_support import OWNER, _admin, alembic, create_database, drop_database
from db.test_phase2_migrations import _allocation, _rule, _transaction
from db.test_phase6_migration import _account, _as_owner, _forced, _import, _new_workspace
from psycopg import errors
from psycopg.types.json import Jsonb
from sqlalchemy.exc import DBAPIError

HEAD = "0029_household_categories"
PREVIOUS = "0028_binance_csv"


def test_the_new_categories_are_accepted_and_no_other(connect, workspaces):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        private = _account(conn, a, purpose="private")
        imported = _import(conn, a, private)
        tx = _transaction(conn, a, private, imported, amount="-24.00", purpose="Friseur")
        _allocation(conn, a, tx, amount="-24.00", category="hh_personal_care", method="user")
        refund = _transaction(conn, a, private, imported, b"l2", amount="19.99", purpose="Retoure")
        _allocation(conn, a, refund, amount="19.99", category="hh_refund", method="user")
        _rule(conn, a, {"purpose_pattern": "friseur"}, category="hh_personal_care")
        _rule(
            conn,
            a,
            {"purpose_pattern": "reise"},
            split_method="as_last_split",
            category=None,
            categories=Jsonb(["hh_travel", "hh_dining"]),
        )
        other = _transaction(conn, a, private, imported, b"l3", amount="-5.00", purpose="Kiosk")
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _allocation(conn, a, other, amount="-5.00", category="hh_snacks", method="user")


def _category_check(conn) -> str:
    return conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE conname = 'allocation_category_check'"
    ).fetchone()[0]


def test_0029_runs_up_down_and_up_on_an_empty_database():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        with _admin(name) as conn:
            assert "hh_refund" in _category_check(conn)
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            check = _category_check(conn)
            assert "hh_interest_fees" in check and "hh_refund" not in check
        alembic(name, "upgrade", HEAD)
    finally:
        drop_database(name)


def _seed(conn, ws, data) -> None:
    private = _account(conn, ws, purpose="private")
    if data == "category":
        tx = _transaction(conn, ws, private, _import(conn, ws, private))
        _allocation(conn, ws, tx, category="hh_dining", method="user")
    elif data == "rule":
        _rule(conn, ws, {"purpose_pattern": "sport"}, category="hh_sport")
    elif data == "rule_parts":
        _rule(
            conn,
            ws,
            {"purpose_pattern": "markt"},
            split_method="as_last_split",
            category=None,
            categories=Jsonb(["hh_groceries", "hh_home"]),
        )


DATA = {
    "category": "an allocation uses a category of 0029",
    "rule": "a learned rule uses a category of 0029",
    "rule_parts": "a learned rule uses a category of 0029",
}


@pytest.mark.parametrize(("data", "reason"), DATA.items())
def test_0029_downgrade_refuses_while_a_new_category_is_used(data, reason):
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
