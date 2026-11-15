"""Migration 0035 (docs/phases/phase-6.md D162 amended 15 Nov 2026): an exchange account may
carry its deposit IBAN, unique with the workspace's other IBANs, and the downgrade guard.
Synthetic data only: invented references."""

import pytest
from db.db_support import OWNER, _admin, alembic, create_database, drop_database
from db.test_phase6_migration import _account, _as_owner, _forced, _hmac, _new_workspace
from psycopg import errors
from sqlalchemy.exc import DBAPIError

HEAD = "0035_exchange_deposit_iban"
PREVIOUS = "0034_bar_category"


def _exchange(conn, ws, **overrides):
    row = {
        "label": "Börse",
        "bank": "Testbörse",
        "kind": "exchange",
        "provider": "Binance",
        "iban_masked": None,
        "iban_hmac": None,
    } | overrides
    return _account(conn, ws, **row)


def _iban(hmac: bytes | None = None) -> dict:
    return {"iban_masked": "DE00 **** 0035", "iban_hmac": hmac or _hmac()}


def test_an_exchange_may_carry_an_iban_and_a_wallet_still_may_not(connect, workspaces):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        assert _exchange(conn, a, **_iban()) is not None
        assert _exchange(conn, a) is not None  # or none, as before
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _exchange(conn, a, iban_masked="DE00 **** 0035", iban_hmac=None)  # both or none
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _exchange(conn, a, kind="wallet", provider=None, **_iban())


def test_an_exchange_iban_is_unique_with_the_bank_accounts(connect, workspaces):
    """The IBAN joins the workspace's own IBANs (D162): one IBAN, one account."""
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        shared = _hmac()
        _account(conn, a, iban_hmac=shared)
        with pytest.raises(errors.UniqueViolation), conn.transaction():
            _exchange(conn, a, **_iban(shared))


def _identity_check(conn) -> str:
    return conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE conname = 'account_identity_check'"
    ).fetchone()[0]


def test_0035_runs_up_down_and_up_on_an_empty_database():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        with _admin(name) as conn:
            assert "'exchange'" in _identity_check(conn)
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            check = _identity_check(conn)
            assert "'depot'" in check and "'exchange'" not in check
        alembic(name, "upgrade", HEAD)
    finally:
        drop_database(name)


def test_0035_downgrade_refuses_while_an_exchange_carries_an_iban():
    """A downgrade never leaves a row the old CHECK refuses; the guard sees rows under forced
    RLS."""
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            _exchange(conn, ws, **_iban())
            forced = _forced(conn)
        with pytest.raises(
            DBAPIError, match=f"downgrade of {HEAD} refused: an exchange account carries an IBAN"
        ) as refused:
            alembic(name, "downgrade", PREVIOUS)
        assert isinstance(refused.value.orig, errors.RestrictViolation)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
            assert _forced(conn) == forced
    finally:
        drop_database(name)


def test_0035_downgrades_with_an_exchange_without_an_iban():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            _exchange(conn, ws)
        alembic(name, "downgrade", PREVIOUS)
    finally:
        drop_database(name)
