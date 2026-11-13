"""Migration 0026 (docs/phases/phase-6.md D176): the credit card account kind, its payment IBAN
columns, the category hh_interest_fees, the method matcher_card, the format card_pdf, and the
downgrade guard. The enum CHECKs against the schemas are test_schema_consistency.py's.
Synthetic data only: invented references."""

import pytest
from db.db_support import OWNER, _admin, alembic, create_database, drop_database
from db.test_phase2_migrations import _allocation, _rule, _transaction
from db.test_phase6_migration import _account, _as_owner, _forced, _hmac, _import, _new_workspace
from psycopg import errors
from psycopg.types.json import Jsonb
from sqlalchemy.exc import DBAPIError

HEAD = "0026_credit_card"
PREVIOUS = "0025_household_accounts"


def _card(conn, ws, **overrides):
    row = {
        "kind": "credit_card",
        "label": "Kreditkarte",
        "bank": "Beispielbank",
        "iban_masked": None,
        "iban_hmac": None,
        "external_ref_masked": "**** 4567",
        "external_ref_hmac": _hmac(),
    } | overrides
    return _account(conn, ws, **row)


def test_a_card_account_has_no_iban_and_only_a_card_has_a_payment_iban(connect, workspaces):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        card = _card(conn, a, payment_iban_masked="DE34 **** 3005", payment_iban_hmac=_hmac())
        assert card is not None
        _card(conn, a, external_ref_masked=None, external_ref_hmac=None)  # set by the 1st import
        for bad in (
            {"iban_masked": "DE00 **** 0001", "iban_hmac": _hmac()},  # a card has no IBAN
            {"payment_iban_masked": "DE34 **** 3005", "payment_iban_hmac": None},  # both or none
            {"payment_iban_masked": "DE34100000000000003005", "payment_iban_hmac": _hmac()},
        ):
            with pytest.raises(errors.CheckViolation), conn.transaction():
                _card(conn, a, external_ref_hmac=_hmac(), **bad)
        with pytest.raises(errors.CheckViolation), conn.transaction():
            # a bank account never carries a payment IBAN
            _account(conn, a, payment_iban_masked="DE34 **** 3005", payment_iban_hmac=_hmac())


def test_the_card_format_category_and_method_are_accepted(connect, workspaces):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        card = _card(conn, a)
        imported = _import(conn, a, card, format="card_pdf")
        tx = _transaction(conn, a, card, imported, amount="-9.84", purpose="Zinsen")
        _allocation(conn, a, tx, amount="-9.84", category="hh_interest_fees", method="matcher_card")
        _rule(
            conn,
            a,
            {"purpose_pattern": "zinsen"},
            split_method="single_category",
            category="hh_interest_fees",
        )


def test_0026_runs_up_down_and_up_on_an_empty_database():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            columns = {
                r[0]
                for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = 'account'"
                )
            }
            assert not columns & {"payment_iban_masked", "payment_iban_hmac"}
            kinds = conn.execute(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conname = 'account_kind_check'"
            ).fetchone()[0]
            assert "credit_card" not in kinds
        alembic(name, "upgrade", HEAD)
    finally:
        drop_database(name)


def _seed(conn, ws, data) -> None:
    if data == "card_account":
        _card(conn, ws)
        return
    bank = _account(conn, ws, purpose="rental")
    if data == "card_pdf":
        _import(conn, ws, bank, format="card_pdf")
    elif data == "category":
        tx = _transaction(conn, ws, bank, _import(conn, ws, bank))
        _allocation(conn, ws, tx, category="hh_interest_fees", method="user")
    elif data == "method":
        tx = _transaction(conn, ws, bank, _import(conn, ws, bank))
        _allocation(conn, ws, tx, category="bank_fees", method="matcher_card")
    elif data == "rule":
        _rule(
            conn,
            ws,
            {"purpose_pattern": "zinsen"},
            split_method="as_last_split",
            category=None,
            categories=Jsonb(["hh_interest_fees", "bank_fees"]),
        )


DATA = {
    "card_account": "an account is a credit card",
    "card_pdf": "an import is of format card_pdf",
    "category": "an allocation uses hh_interest_fees or matcher_card",
    "method": "an allocation uses hh_interest_fees or matcher_card",
    "rule": "a learned rule uses hh_interest_fees",
}


@pytest.mark.parametrize(("data", "reason"), DATA.items())
def test_0026_downgrade_refuses_while_it_would_drop_data(data, reason):
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
