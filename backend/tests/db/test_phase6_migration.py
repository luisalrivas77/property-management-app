"""Migration 0025 (docs/phases/phase-6.md §10, D156, D157, D162, D163, D165-D169, D171): the
account kinds and purpose, the data step, the eight household tables with their append-only
triggers, the credential slot, the private-accounts switch, the grants and the downgrade guard.

RLS, workspace_id and the table list are covered for every table by test_migration.py; the
enum CHECKs (account.kind/purpose/currency, asset.kind, asset_movement.kind/source/status,
movement_import.format/status, holding_statement.source, transfer_link.kind/matched_by,
price.source, valuation_snapshot.price_source) by test_schema_consistency.py. Synthetic data
only: owners A and B, invented references."""

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
    insert_event,
)
from psycopg import errors
from psycopg.types.json import Jsonb
from sqlalchemy.exc import DBAPIError

PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
EXPECTED_GRANTS = {
    "account_ownership": {"SELECT", "INSERT", "UPDATE"},  # superseded_by set once
    "asset": {"SELECT", "INSERT", "UPDATE"},  # mutable + events
    "movement_import": {"SELECT", "INSERT", "UPDATE"},  # mutable with status
    "asset_movement": {"SELECT", "INSERT", "UPDATE"},  # status forward, superseded_by once
    "holding_statement": {"SELECT", "INSERT", "UPDATE"},  # superseded_by set once (0027)
    "transfer_link": {"SELECT", "INSERT", "UPDATE"},  # superseded_by set once
    "price": {"SELECT", "INSERT"},
    "valuation_snapshot": {"SELECT", "INSERT"},
}
PHASE6_TABLES = tuple(EXPECTED_GRANTS)
HEAD = "0025_household_accounts"
PREVIOUS = "0024_bank_statements"


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


def _hmac() -> bytes:
    return hashlib.sha256(uuid.uuid4().bytes).digest()


def _document(conn, ws, **overrides) -> uuid.UUID:
    digest = hashlib.sha256(uuid.uuid4().bytes).digest()
    conn.execute(
        "INSERT INTO document_file (workspace_id, sha256, size_bytes, media_type, storage_key)"
        " VALUES (%s, %s, 11, 'application/pdf', %s)",
        (ws, digest, f"{ws}/sha256/{digest.hex()}"),
    )
    row = {"workspace_id": ws, "file_sha256": digest, "source": "upload"} | overrides
    return _insert(conn, "document", row)


def _owner(conn, ws, name="Eigentümer A") -> uuid.UUID:
    return _insert(conn, "owner", {"workspace_id": ws, "full_name": name})


def _account(conn, ws, **overrides) -> uuid.UUID:
    """A bank account as before 0025 (the IBAN columns are set)."""
    row = {
        "workspace_id": ws,
        "label": "Girokonto",
        "bank": "Testbank",
        "iban_masked": "DE00 **** 0000",
        "iban_hmac": _hmac(),
        "opening_balance": "1000.00",
        "opening_balance_on": "2026-03-31",
    } | overrides
    return _insert(conn, "account", row)


def _depot(conn, ws, **overrides) -> uuid.UUID:
    row = {
        "workspace_id": ws,
        "label": "Depot",
        "bank": "Testdepot",
        "kind": "depot",
        "iban_masked": None,
        "iban_hmac": None,
        "external_ref_masked": "**** 4711",
        "external_ref_hmac": _hmac(),
    } | overrides
    return _account(conn, ws, **row)


def _import(conn, ws, account_id, **overrides) -> uuid.UUID:
    row = {
        "workspace_id": ws,
        "account_id": account_id,
        "document_id": _document(conn, ws),
        "format": "camt",
    } | overrides
    return _insert(conn, "statement_import", row)


def _asset(conn, ws, **overrides) -> uuid.UUID:
    row = {"workspace_id": ws, "kind": "crypto", "symbol": "TST", "name": "Testcoin"} | overrides
    return _insert(conn, "asset", row)


def _movement(ws, account_id, asset_id, **overrides) -> dict:
    return {
        "workspace_id": ws,
        "account_id": account_id,
        "asset_id": asset_id,
        "kind": "acquisition",
        "quantity": "1.5000000000",
        "eur_amount": "-150.00",
        "occurred_at": "2026-05-04T10:00:00Z",
        "source": "user",
    } | overrides


def _price(ws, asset_id, **overrides) -> dict:
    return {
        "workspace_id": ws,
        "asset_id": asset_id,
        "on": "2026-05-04",
        "price_eur": "100.00000000",
        "source": "coingecko_daily",
    } | overrides


@pytest.fixture
def scope(connect, workspaces):
    """(conn, workspace, ids) as the app role, rolled back after the test."""
    a, _ = workspaces
    with connect(APP, a) as conn:
        ids = {
            "owner_a": _owner(conn, a),
            "owner_b": _owner(conn, a, "Eigentümer B"),
            "bank": _account(conn, a),
            "depot": _depot(conn, a),
            "exchange": _depot(
                conn, a, kind="exchange", external_ref_masked="**** 0815", label="Börse"
            ),
        }
        ids["asset"] = _asset(conn, a)
        yield conn, a, ids


# --- account ---------------------------------------------------------------------------


def test_a_new_account_is_a_private_current_account_in_euro(scope):
    conn, _, ids = scope
    row = conn.execute(
        "SELECT kind, purpose, currency, provider FROM account WHERE id = %s",
        (ids["bank"],),
    ).fetchone()
    assert row == ("bank_current", "private", "EUR", None)


@pytest.mark.parametrize(
    "change",
    [
        {"iban_masked": None, "iban_hmac": None},  # a bank kind has an IBAN
        {"iban_hmac": None},  # masked and HMAC together
        {"external_ref_masked": "**** 1234", "external_ref_hmac": b"\x00" * 32},  # bank: no ref
        {"kind": "exchange"},  # any other kind has no IBAN (a depot may since 0027, D156)
        {"currency": "USD"},  # EUR only in this phase
        {"provider": ""},
        {"kind": "savings"},
        {"purpose": "business"},
    ],
)
def test_an_account_outside_its_identity_checks_is_refused(scope, change):
    conn, a, _ = scope
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _account(conn, a, **change)


@pytest.mark.parametrize(
    "change",
    [
        {"external_ref_masked": "1234"},  # masked like an IBAN tail
        {"external_ref_hmac": b"short"},  # HMAC-SHA256 is 32 bytes
        {"external_ref_masked": None},  # masked and HMAC together
    ],
)
def test_a_depot_reference_outside_its_checks_is_refused(scope, change):
    conn, a, _ = scope
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _depot(conn, a, **change)


def test_a_depot_without_a_reference_is_allowed_and_the_reference_is_unique(scope):
    conn, a, _ = scope
    _depot(conn, a, external_ref_masked=None, external_ref_hmac=None, kind="wallet")
    ref = _hmac()
    _depot(conn, a, external_ref_hmac=ref)
    with pytest.raises(errors.UniqueViolation), conn.transaction():
        _depot(conn, a, external_ref_hmac=ref)


def test_a_connection_slot_defaults_to_primary_and_is_a_label(scope):
    conn, a, ids = scope
    row = {
        "workspace_id": a,
        "account_id": ids["bank"],
        "bank_code": "12345678",
        "fints_url": "https://banking.example/fints",
        "login_name": "anmeldename42",
    }
    connection = _insert(conn, "bank_connection", row)
    slot = conn.execute(
        "SELECT credential_slot FROM bank_connection WHERE id = %s", (connection,)
    ).fetchone()
    assert slot == ("primary",)
    other = _account(conn, a)
    for bad in ("Primary", "dkb-login", "", "1st", "x" * 33):
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _insert(conn, "bank_connection", row | {"account_id": other, "credential_slot": bad})
    _insert(conn, "bank_connection", row | {"account_id": other, "credential_slot": "dkb"})


def test_the_private_accounts_switch_is_off_by_default(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        row = conn.execute("SELECT ai_private_accounts FROM workspace WHERE id = %s", (a,))
        assert row.fetchone() == (False,)


# --- account_ownership -----------------------------------------------------------------


def _share(ws, account_id, owner_id, **overrides) -> dict:
    return {
        "workspace_id": ws,
        "account_id": account_id,
        "owner_id": owner_id,
        "share_numerator": 1,
        "share_denominator": 2,
        "effective_from": "2026-01-01",
    } | overrides


def test_an_ownership_row_is_superseded_once_and_never_changed(scope):
    conn, a, ids = scope
    old = _insert(conn, "account_ownership", _share(a, ids["depot"], ids["owner_a"]))
    new = _insert(
        conn,
        "account_ownership",
        _share(a, ids["depot"], ids["owner_a"], share_numerator=1, share_denominator=1),
    )
    with pytest.raises(errors.RestrictViolation), conn.transaction():
        conn.execute(
            "UPDATE account_ownership SET share_numerator = 2 WHERE id = %s",
            (old,),
        )
    with pytest.raises(errors.RestrictViolation), conn.transaction():  # nothing to set
        conn.execute("UPDATE account_ownership SET reason = 'x' WHERE id = %s", (old,))
    conn.execute("UPDATE account_ownership SET superseded_by = %s WHERE id = %s", (new, old))
    with pytest.raises(errors.RestrictViolation), conn.transaction():  # once only
        conn.execute("UPDATE account_ownership SET superseded_by = NULL WHERE id = %s", (old,))
    with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
        conn.execute("DELETE FROM account_ownership WHERE id = %s", (old,))


def test_an_ownership_row_is_superseded_by_a_row_of_the_same_account(scope):
    conn, a, ids = scope
    old = _insert(conn, "account_ownership", _share(a, ids["depot"], ids["owner_a"]))
    elsewhere = _insert(conn, "account_ownership", _share(a, ids["bank"], ids["owner_a"]))
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
        conn.execute(
            "UPDATE account_ownership SET superseded_by = %s WHERE id = %s", (elsewhere, old)
        )


@pytest.mark.parametrize(
    "change",
    [
        {"share_numerator": 3, "share_denominator": 2},
        {"share_numerator": 0},
        {"share_denominator": 0},
        {"reason": ""},
    ],
)
def test_an_ownership_row_outside_its_checks_is_refused(scope, change):
    conn, a, ids = scope
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(conn, "account_ownership", _share(a, ids["depot"], ids["owner_a"], **change))


# --- asset -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        {"symbol": None},  # a symbol or an ISIN
        {"kind": "etf", "symbol": None, "isin": None},
        {"kind": "etf", "symbol": "ETF"},  # an ETF or a stock has its ISIN
        {"isin": "DE000000000X"},  # the check digit is a digit
        {"symbol": "tst"},
        {"decimals": 11},
        {"currency": "euro"},
    ],
)
def test_an_asset_outside_its_checks_is_refused(scope, change):
    conn, a, _ = scope
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _asset(conn, a, **change | {"symbol": change.get("symbol", "OTHER")})


def test_an_asset_is_unique_by_isin_and_symbol_and_mutable(scope):
    conn, a, ids = scope
    _asset(conn, a, kind="etf", symbol=None, isin="XX0000000018", name="Test-ETF")
    with pytest.raises(errors.UniqueViolation), conn.transaction():
        _asset(conn, a, kind="etf", symbol=None, isin="XX0000000018", name="Zweitname")
    with pytest.raises(errors.UniqueViolation), conn.transaction():
        _asset(conn, a)  # TST again
    conn.execute(
        "UPDATE asset SET name = 'Testcoin (neu)', coingecko_id = 'testcoin' WHERE id = %s",
        (ids["asset"],),
    )


def test_an_asset_is_invisible_to_another_workspace(connect, workspaces):
    a, b = workspaces
    with connect(APP, a) as conn:
        _asset(conn, a, symbol="ISO")
        assert conn.execute("SELECT count(*) FROM asset WHERE symbol = 'ISO'").fetchone() == (1,)
        conn.execute("SELECT set_config('app.workspace_id', %s, false)", (str(b),))
        assert conn.execute("SELECT count(*) FROM asset WHERE symbol = 'ISO'").fetchone() == (0,)
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():  # WITH CHECK
            _asset(conn, a, symbol="ISO2")


# --- asset_movement --------------------------------------------------------------------


def test_a_movement_moves_forward_only(scope):
    conn, a, ids = scope
    old = _insert(conn, "asset_movement", _movement(a, ids["exchange"], ids["asset"]))
    status = conn.execute("SELECT status FROM asset_movement WHERE id = %s", (old,)).fetchone()
    assert status == ("proposed",)
    with pytest.raises(errors.RestrictViolation), conn.transaction():
        conn.execute("UPDATE asset_movement SET quantity = 2 WHERE id = %s", (old,))
    with pytest.raises(errors.RestrictViolation), conn.transaction():  # no change at all
        conn.execute("UPDATE asset_movement SET status = 'proposed' WHERE id = %s", (old,))
    conn.execute("UPDATE asset_movement SET status = 'confirmed' WHERE id = %s", (old,))
    with pytest.raises(errors.RestrictViolation), conn.transaction():
        conn.execute("UPDATE asset_movement SET status = 'proposed' WHERE id = %s", (old,))
    new = _insert(
        conn,
        "asset_movement",
        _movement(a, ids["exchange"], ids["asset"], quantity="1.2500000000"),
    )
    conn.execute("UPDATE asset_movement SET superseded_by = %s WHERE id = %s", (new, old))
    with pytest.raises(errors.RestrictViolation), conn.transaction():  # once only
        conn.execute("UPDATE asset_movement SET superseded_by = NULL WHERE id = %s", (old,))
    with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
        conn.execute("DELETE FROM asset_movement WHERE id = %s", (old,))


def test_a_movement_is_superseded_by_a_movement_of_the_same_account(scope):
    conn, a, ids = scope
    old = _insert(conn, "asset_movement", _movement(a, ids["exchange"], ids["asset"]))
    elsewhere = _insert(conn, "asset_movement", _movement(a, ids["depot"], ids["asset"]))
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
        conn.execute("UPDATE asset_movement SET superseded_by = %s WHERE id = %s", (elsewhere, old))


def test_a_movement_hash_is_the_dedup_key_per_account(scope):
    conn, a, ids = scope
    digest = hashlib.sha256(b"row 1").hexdigest()
    row = _movement(a, ids["exchange"], ids["asset"], source="csv", movement_hash=digest)
    _insert(conn, "asset_movement", row)
    with pytest.raises(errors.UniqueViolation), conn.transaction():
        _insert(conn, "asset_movement", row)
    _insert(conn, "asset_movement", row | {"account_id": ids["depot"]})
    # a movement without a hash (typed or read) is never a duplicate of another
    _insert(conn, "asset_movement", _movement(a, ids["exchange"], ids["asset"]))
    _insert(conn, "asset_movement", _movement(a, ids["exchange"], ids["asset"]))


@pytest.mark.parametrize(
    "change",
    [
        {"source": "trade_confirmation"},  # a documented source names its document
        {"source": "report"},
        {"movement_hash": "ABC"},
        {"fee_eur": "-1.00"},
        {"tax_withheld_eur": "-0.01"},
        {"kind": "gift"},
    ],
)
def test_a_movement_outside_its_checks_is_refused(scope, change):
    conn, a, ids = scope
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(conn, "asset_movement", _movement(a, ids["depot"], ids["asset"], **change))


def test_a_documented_movement_names_its_document(scope):
    conn, a, ids = scope
    document = _document(conn, a)
    _insert(
        conn,
        "asset_movement",
        _movement(a, ids["depot"], ids["asset"], source="report", document_id=document),
    )


# --- movement_import and holding_statement ---------------------------------------------


def test_a_holding_statement_is_append_only_and_typed_for_its_own_account(scope):
    conn, a, ids = scope
    imported = _insert(
        conn,
        "movement_import",
        {
            "workspace_id": a,
            "account_id": ids["exchange"],
            "document_id": _document(conn, a),
            "format": "bison_csv",
        },
    )
    conn.execute(
        "UPDATE movement_import SET status = 'reconciled', rows_total = 3, rows_new = 3"
        " WHERE id = %s",
        (imported,),
    )
    row = {
        "workspace_id": a,
        "account_id": ids["exchange"],
        "asset_id": ids["asset"],
        "as_of": "2026-06-30",
        "declared_units": "1.5000000000",
        "source": "user",
        "import_id": imported,
    }
    statement = _insert(conn, "holding_statement", row)
    # Since 0027 only superseded_by may be set, once (test_phase6_depot_migration.py).
    with pytest.raises(errors.RestrictViolation), conn.transaction():
        conn.execute("UPDATE holding_statement SET reconciled = true WHERE id = %s", (statement,))
    with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
        conn.execute("DELETE FROM holding_statement WHERE id = %s", (statement,))
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
        _insert(conn, "holding_statement", row | {"account_id": ids["depot"]})


@pytest.mark.parametrize(
    "change",
    [
        {"rows_total": 1, "rows_new": 2},
        {"first_at": "2026-05-01T00:00:00Z"},  # both bounds or neither
        {"status": "failed"},  # a failure has its code
        {"error_code": "x"},  # and only a failure
        {"format": "csv"},
    ],
)
def test_a_movement_import_outside_its_checks_is_refused(scope, change):
    conn, a, ids = scope
    row = {
        "workspace_id": a,
        "account_id": ids["exchange"],
        "document_id": _document(conn, a),
        "format": "bison_csv",
    } | change
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(conn, "movement_import", row)


# --- transfer_link ---------------------------------------------------------------------


def test_a_transfer_link_has_one_leg_each_side_and_is_superseded_once(scope):
    conn, a, ids = scope
    out = _insert(
        conn,
        "asset_movement",
        _movement(a, ids["exchange"], ids["asset"], kind="internal_transfer_out", quantity="-1"),
    )
    into = _insert(
        conn,
        "asset_movement",
        _movement(a, ids["depot"], ids["asset"], kind="internal_transfer_in", quantity="1"),
    )
    row = {
        "workspace_id": a,
        "kind": "exchange_eur",
        "out_movement_id": out,
        "in_movement_id": into,
        "matched_by": "matcher",
    }
    for bad in ({"in_movement_id": None}, {"out_transaction_id": uuid.uuid4()}):
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _insert(conn, "transfer_link", row | bad)
    old = _insert(conn, "transfer_link", row)
    new = _insert(conn, "transfer_link", row | {"matched_by": "user"})  # the service's rule
    with pytest.raises(errors.RestrictViolation), conn.transaction():
        conn.execute("UPDATE transfer_link SET kind = 'bank' WHERE id = %s", (old,))
    conn.execute("UPDATE transfer_link SET superseded_by = %s WHERE id = %s", (new, old))
    with pytest.raises(errors.RestrictViolation), conn.transaction():
        conn.execute("UPDATE transfer_link SET superseded_by = %s WHERE id = %s", (old, old))


# --- price and valuation_snapshot ------------------------------------------------------


def test_a_daily_price_is_once_a_day_and_a_trade_price_is_not(scope):
    conn, a, ids = scope
    _insert(conn, "price", _price(a, ids["asset"]))
    with pytest.raises(errors.UniqueViolation), conn.transaction():
        _insert(conn, "price", _price(a, ids["asset"], price_eur="101.00000000"))
    _insert(conn, "price", _price(a, ids["asset"], source="kraken_daily"))
    for _ in range(2):
        _insert(conn, "price", _price(a, ids["asset"], source="trade"))
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(conn, "price", _price(a, ids["asset"], source="trade", price_eur="0"))
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(conn, "price", _price(a, ids["asset"], source="user", raw=Jsonb([1])))


def test_a_price_is_append_only_even_for_the_owner(connect, workspaces):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        price = _insert(conn, "price", _price(a, _asset(conn, a, symbol="OWN")))
        with pytest.raises(errors.RestrictViolation), conn.transaction():
            conn.execute("UPDATE price SET price_eur = 1 WHERE id = %s", (price,))
        with pytest.raises(errors.RestrictViolation), conn.transaction():
            conn.execute("DELETE FROM price WHERE id = %s", (price,))


def test_a_valuation_run_values_each_position_once(scope):
    conn, a, ids = scope
    run = uuid.uuid4()
    row = {
        "workspace_id": a,
        "account_id": ids["bank"],
        "as_of": "2026-06-30",
        "value_eur": "1200.00",
        "net_worth_run_id": run,
    }
    _insert(conn, "valuation_snapshot", row)  # the bank balance: no asset
    with pytest.raises(errors.UniqueViolation), conn.transaction():
        _insert(conn, "valuation_snapshot", row)
    position = row | {
        "account_id": ids["exchange"],
        "asset_id": ids["asset"],
        "units": "1.5000000000",
        "price_eur": "100.00000000",
        "price_source": "coingecko_daily",
        "value_eur": "150.00",
    }
    _insert(conn, "valuation_snapshot", position)
    _insert(conn, "valuation_snapshot", position | {"net_worth_run_id": uuid.uuid4()})
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(
            conn,
            "valuation_snapshot",
            position | {"net_worth_run_id": uuid.uuid4(), "price_source": None},
        )


# --- The data step and the downgrade (each on its own database) ------------------------


def _new_workspace(name) -> uuid.UUID:
    ws = uuid.uuid4()
    with _admin(name) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'A')", (ws,))
    return ws


def _as_owner(conn, ws) -> None:
    conn.execute(f"SET ROLE {OWNER}")
    conn.execute("SELECT set_config('app.workspace_id', %s, false)", (str(ws),))


def _forced(conn) -> set[str]:
    rows = conn.execute(
        "SELECT relname FROM pg_class WHERE relkind = 'r' AND relforcerowsecurity"
        " AND relnamespace = 'public'::regnamespace"
    )
    return {r[0] for r in rows}


def test_0025_data_step_names_the_rental_account_and_every_account_the_households():
    """D156, D171: the account with imports is rental, every other private; every account is
    a current account and one of the household's own (is_owner_account)."""
    name = create_database()
    try:
        alembic(name, "upgrade", PREVIOUS)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            rental = _account(conn, ws, label="Mietkonto")
            _import(conn, ws, rental)
            owner = _account(conn, ws, label="Eigenes Konto", is_owner_account=True)
            other = _account(conn, ws, label="Fremdkonto")
            forced_before = _forced(conn)
        alembic(name, "upgrade", HEAD)
        with _admin(name) as conn:
            rows = dict(
                (r[0], r[1:])
                for r in conn.execute(
                    "SELECT id, kind, purpose, is_owner_account, currency FROM account"
                )
            )
            assert rows == {
                rental: ("bank_current", "rental", True, "EUR"),
                owner: ("bank_current", "private", True, "EUR"),
                other: ("bank_current", "private", True, "EUR"),
            }
            assert forced_before | set(PHASE6_TABLES) == _forced(conn)
    finally:
        drop_database(name)


def test_0025_refuses_a_workspace_with_two_accounts_that_have_imports():
    name = create_database()
    try:
        alembic(name, "upgrade", PREVIOUS)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            for _ in range(2):
                _import(conn, ws, _account(conn, ws))
        with pytest.raises(DBAPIError, match=f"migration {HEAD} refused") as refused:
            alembic(name, "upgrade", HEAD)
        assert isinstance(refused.value.orig, errors.RestrictViolation)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [
                (PREVIOUS,)
            ]
    finally:
        drop_database(name)


def test_0025_runs_up_down_and_up_on_an_empty_database():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            for table in PHASE6_TABLES:
                assert conn.execute("SELECT to_regclass(%s)", (table,)).fetchone() == (None,)
            nullable = conn.execute(
                "SELECT column_name, is_nullable FROM information_schema.columns"
                " WHERE table_name = 'account' AND column_name IN ('iban_masked', 'iban_hmac')"
            ).fetchall()
            assert sorted(nullable) == [("iban_hmac", "NO"), ("iban_masked", "NO")]
            columns = {
                r[0]
                for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name IN ('account', 'bank_connection', 'workspace')"
                )
            }
            assert not columns & {"kind", "purpose", "credential_slot", "ai_private_accounts"}
        alembic(name, "upgrade", HEAD)
    finally:
        drop_database(name)


def test_0025_downgrade_gives_the_flag_back_to_the_owners_accounts_only():
    """The inverse of the data step: before 0025 only the owners' accounts carried it."""
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            rental = _account(conn, ws, purpose="rental", is_owner_account=True)
            _import(conn, ws, rental)
            private = _account(conn, ws, is_owner_account=True)
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            flags = dict(conn.execute("SELECT id, is_owner_account FROM account").fetchall())
            assert flags == {rental: False, private: True}
        alembic(name, "upgrade", HEAD)  # and the data step puts it back
        with _admin(name) as conn:
            rows = dict(
                (r[0], r[1:])
                for r in conn.execute("SELECT id, purpose, is_owner_account FROM account")
            )
            assert rows == {rental: ("rental", True), private: ("private", True)}
    finally:
        drop_database(name)


# The data and the refusal it must meet. A row of the eight tables needs an asset or a depot
# first, so those are refused by the first table that holds rows, in the guard's order.
DATA = {
    **{table: "holds rows" for table in PHASE6_TABLES},
    "depot_account": "an account is of another kind than bank_current",
    "private_account_with_import": "a private account holds imported statements",
    "dkb_csv": "an import is of format dkb_csv",
    "credential_slot": "a connection uses another slot than primary",
    "ai_private_accounts": "a workspace sends private accounts to the model",
    "asset_event": "an event is about an asset, a movement or a transfer",
}


def _seed(conn, ws, data) -> None:
    bank = _account(conn, ws)
    if data == "depot_account":
        _depot(conn, ws)
    elif data in PHASE6_TABLES:
        depot = _depot(conn, ws)
        asset = _asset(conn, ws)
        movement = _insert(conn, "asset_movement", _movement(ws, depot, asset))
        seeds = {
            "account_ownership": lambda: _insert(
                conn, "account_ownership", _share(ws, depot, _owner(conn, ws))
            ),
            "movement_import": lambda: _insert(
                conn,
                "movement_import",
                {
                    "workspace_id": ws,
                    "account_id": depot,
                    "document_id": _document(conn, ws),
                    "format": "bison_csv",
                },
            ),
            "holding_statement": lambda: _insert(
                conn,
                "holding_statement",
                {
                    "workspace_id": ws,
                    "account_id": depot,
                    "asset_id": asset,
                    "as_of": "2026-06-30",
                    "declared_units": "1",
                    "source": "user",
                },
            ),
            "transfer_link": lambda: _insert(
                conn,
                "transfer_link",
                {
                    "workspace_id": ws,
                    "kind": "depot_cash",
                    "out_movement_id": movement,
                    "in_movement_id": _insert(conn, "asset_movement", _movement(ws, depot, asset)),
                    "matched_by": "user",
                },
            ),
            "price": lambda: _insert(conn, "price", _price(ws, asset)),
            "valuation_snapshot": lambda: _insert(
                conn,
                "valuation_snapshot",
                {
                    "workspace_id": ws,
                    "account_id": bank,
                    "as_of": "2026-06-30",
                    "value_eur": "1.00",
                    "net_worth_run_id": uuid.uuid4(),
                },
            ),
        }
        seeds.get(data, lambda: None)()
    elif data == "private_account_with_import":
        _import(conn, ws, bank)
    elif data == "dkb_csv":
        conn.execute("UPDATE account SET purpose = 'rental' WHERE id = %s", (bank,))
        _import(conn, ws, bank, format="dkb_csv")
    elif data == "credential_slot":
        _insert(
            conn,
            "bank_connection",
            {
                "workspace_id": ws,
                "account_id": bank,
                "bank_code": "12345678",
                "fints_url": "https://banking.example/fints",
                "login_name": "anmeldename42",
                "credential_slot": "dkb",
            },
        )
    elif data == "ai_private_accounts":
        conn.execute("UPDATE workspace SET ai_private_accounts = true WHERE id = %s", (ws,))
    elif data == "asset_event":
        insert_event(conn, ws, type="asset.created", entity_type="asset")


@pytest.mark.parametrize(("data", "reason"), DATA.items())
def test_0025_downgrade_refuses_while_it_would_drop_data(data, reason):
    """A downgrade never drops data; the guard must see rows under forced RLS."""
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            _seed(conn, ws, data)
            forced = _forced(conn)
        with pytest.raises(DBAPIError, match=f"downgrade of {HEAD} refused: .*{reason}") as refused:
            alembic(name, "downgrade", PREVIOUS)
        # the guard's own exception, not a syntax error that quotes the guard's text
        assert isinstance(refused.value.orig, errors.RestrictViolation)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
            assert _forced(conn) == forced  # the refused downgrade's NO FORCE was rolled back
            assert set(PHASE6_TABLES) <= forced
    finally:
        drop_database(name)
