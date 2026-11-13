"""Migration 0027 (docs/phases/phase-6.md D156 and D165 as amended in step 4): a depot account
may carry an IBAN, the format depot_pdf, the method matcher_depot, the subtype, reading kind,
proposal kind and movement source securities_transfer, a holding statement corrected once by
a row of the same position, and the downgrade guard. The enum CHECKs
against the schemas are test_schema_consistency.py's. Synthetic data only: invented
references."""

import pytest
from db.db_support import APP, OWNER, _admin, alembic, create_database, drop_database
from db.test_phase2_catalogue_migration import _document, _extraction, _proposal
from db.test_phase2_migrations import _allocation, _transaction
from db.test_phase6_migration import (
    _account,
    _as_owner,
    _asset,
    _depot,
    _forced,
    _hmac,
    _import,
    _insert,
    _movement,
    _new_workspace,
)
from psycopg import errors
from sqlalchemy.exc import DBAPIError

HEAD = "0027_depot"
PREVIOUS = "0026_credit_card"
IBAN = {"iban_masked": "DE00 **** 0027", "iban_hmac": None}


def _iban() -> dict:
    return IBAN | {"iban_hmac": _hmac()}


def test_a_depot_may_carry_an_iban_and_only_a_bank_or_depot_does(connect, workspaces):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        assert _depot(conn, a, **_iban()) is not None
        assert _depot(conn, a, external_ref_hmac=_hmac()) is not None  # or none, as in 0025
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _depot(conn, a, iban_masked="DE00 **** 0027", iban_hmac=None)  # both or none
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _depot(conn, a, kind="exchange", external_ref_hmac=_hmac(), **_iban())
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _account(conn, a, iban_masked=None, iban_hmac=None)  # a bank account still needs one
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _account(conn, a, external_ref_masked="**** 4711", external_ref_hmac=_hmac())


def test_a_depot_iban_is_unique_with_the_bank_accounts(connect, workspaces):
    """The IBAN joins the workspace's own IBANs (D162): one IBAN, one account."""
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        shared = _hmac()
        _account(conn, a, iban_hmac=shared)
        with pytest.raises(errors.UniqueViolation), conn.transaction():
            _depot(conn, a, iban_masked="DE00 **** 0027", iban_hmac=shared)


def test_the_depot_format_method_subtype_and_source_are_accepted(connect, workspaces):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        depot = _depot(conn, a, **_iban())
        imported = _import(conn, a, depot, format="depot_pdf")
        tx = _transaction(conn, a, depot, imported, amount="-1.20", purpose="Gebühr")
        _allocation(conn, a, tx, amount="-1.20", category="depot_fee", method="matcher_depot")
        document = _document(conn, a, type="letter", subtype="securities_transfer")
        _extraction(conn, a, document, "securities_transfer")
        _proposal(conn, a, document, "securities_transfer")
        asset = _asset(conn, a, kind="etf", symbol=None, isin="IE00BEISPL01", name="Beispiel-ETF")
        _insert(
            conn,
            "asset_movement",
            _movement(
                a,
                depot,
                asset,
                kind="internal_transfer_in",
                eur_amount=None,
                source="securities_transfer",
                document_id=document,
            ),
        )


def _holding(ws, account, asset, **overrides) -> dict:
    return {
        "workspace_id": ws,
        "account_id": account,
        "asset_id": asset,
        "as_of": "2026-06-30",
        "declared_units": "4.8000000000",
        "declared_value_eur": "501.60",
        "source": "report",
    } | overrides


def test_a_holding_statement_is_corrected_once_by_a_row_of_the_same_position(connect, workspaces):
    """0027: a report read again with other figures supersedes its position; nothing else
    of the row changes, and a delete stays refused (as the app role, under RLS)."""
    a, _ = workspaces
    with connect(APP, a) as conn:
        depot = _depot(conn, a)
        asset = _asset(conn, a, kind="etf", symbol=None, isin="IE00BEISPL01", name="Beispiel-ETF")
        old = _insert(conn, "holding_statement", _holding(a, depot, asset))
        new = _insert(
            conn, "holding_statement", _holding(a, depot, asset, declared_value_eur="510.00")
        )
        with pytest.raises(errors.RestrictViolation), conn.transaction():
            conn.execute("UPDATE holding_statement SET reconciled = true WHERE id = %s", (old,))
        with pytest.raises(errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE holding_statement SET superseded_by = id WHERE id = %s", (old,))
        # Another date, asset or account is no correction of this position.
        other_day = _insert(
            conn, "holding_statement", _holding(a, depot, asset, as_of="2026-09-30")
        )
        other_depot = _insert(conn, "holding_statement", _holding(a, _depot(conn, a), asset))
        for elsewhere in (other_day, other_depot):
            with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
                conn.execute(
                    "UPDATE holding_statement SET superseded_by = %s WHERE id = %s",
                    (elsewhere, old),
                )
        conn.execute("UPDATE holding_statement SET superseded_by = %s WHERE id = %s", (new, old))
        with pytest.raises(errors.RestrictViolation), conn.transaction():  # once only
            conn.execute("UPDATE holding_statement SET superseded_by = NULL WHERE id = %s", (old,))
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            conn.execute("DELETE FROM holding_statement WHERE id = %s", (old,))
    with connect(OWNER, a) as conn:  # not even the owner deletes one
        asset = _asset(conn, a, kind="etf", symbol=None, isin="IE00BEISPL01", name="Beispiel-ETF")
        row = _insert(conn, "holding_statement", _holding(a, _depot(conn, a), asset))
        with pytest.raises(errors.RestrictViolation), conn.transaction():
            conn.execute("DELETE FROM holding_statement WHERE id = %s", (row,))


def test_0027_runs_up_down_and_up_on_an_empty_database():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            formats = conn.execute(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conname = 'statement_import_format_check'"
            ).fetchone()[0]
            assert "depot_pdf" not in formats
            columns = conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'holding_statement'"
            ).fetchall()
            assert ("superseded_by",) not in columns
            triggers = conn.execute(
                "SELECT tgname FROM pg_trigger"
                " WHERE tgrelid = 'holding_statement'::regclass AND NOT tgisinternal"
            ).fetchall()
            assert sorted(t for (t,) in triggers) == [
                "holding_statement_append_only",
                "holding_statement_no_truncate",
            ]
        alembic(name, "upgrade", HEAD)
    finally:
        drop_database(name)


def _seed(conn, ws, data) -> None:
    if data == "depot_iban":
        _depot(conn, ws, **_iban())
        return
    depot = _depot(conn, ws)
    if data == "depot_pdf":
        _import(conn, ws, depot, format="depot_pdf")
    elif data == "method":
        tx = _transaction(conn, ws, depot, _import(conn, ws, depot))
        _allocation(conn, ws, tx, category="depot_fee", method="matcher_depot")
    elif data == "subtype":
        _document(conn, ws, type="letter", subtype="securities_transfer")
    elif data == "reading":
        _extraction(conn, ws, _document(conn, ws), "securities_transfer")
    elif data == "proposal":
        _proposal(conn, ws, _document(conn, ws), "securities_transfer")
    elif data == "corrected":
        asset = _asset(conn, ws, kind="etf", symbol=None, isin="IE00BEISPL01", name="Beispiel-ETF")
        old = _insert(conn, "holding_statement", _holding(ws, depot, asset))
        new = _insert(conn, "holding_statement", _holding(ws, depot, asset, declared_units="5"))
        conn.execute("UPDATE holding_statement SET superseded_by = %s WHERE id = %s", (new, old))
    elif data == "movement":
        document = _document(conn, ws, type="letter")
        asset = _asset(conn, ws, kind="etf", symbol=None, isin="IE00BEISPL01", name="Beispiel-ETF")
        _insert(
            conn,
            "asset_movement",
            _movement(
                ws,
                depot,
                asset,
                kind="internal_transfer_in",
                eur_amount=None,
                source="securities_transfer",
                document_id=document,
            ),
        )


DATA = {
    "depot_iban": "a depot account carries an IBAN",
    "depot_pdf": "an import is of format depot_pdf",
    "method": "an allocation was booked by matcher_depot",
    "subtype": "a securities_transfer",
    "reading": "a securities_transfer reading",
    "proposal": "a securities_transfer proposal",
    "movement": "a movement read from a securities_transfer",
    "corrected": "a holding statement was corrected",
}


@pytest.mark.parametrize(("data", "reason"), DATA.items())
def test_0027_downgrade_refuses_while_it_would_drop_data(data, reason):
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
