"""Migration 0041 (docs/phases/phase-7.md §11, D179-D191): the six wallet tables with their
triggers and grants, the asset and asset_movement columns, the per-chain address CHECKs, the
chain kinds of transfer_link, and the downgrade guard.

RLS, workspace_id and the table list are covered for every table by test_migration.py; the
enum CHECKs (asset.chain, wallet_address.chain/kind, wallet_sync.status,
movement_group.chain/kind/status, chain_decoder.chain/kind, chain_pair.kind/from_chain/
to_chain, and the widened asset_movement.kind/source, movement_import.format,
transfer_link.kind, document.subtype, event.entity_type) by test_schema_consistency.py.

Synthetic data only: the addresses are published test vectors (EIP-55's examples, BIP-173's
example, the Bitcoin wiki's example address) and Solana's system and stake program ids, which
are valid in format and belong to nobody; every hash, balance and label is invented."""

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
from db.test_phase6_migration import (
    _account,
    _as_owner,
    _asset,
    _document,
    _forced,
    _movement,
    _new_workspace,
)
from psycopg import errors
from psycopg.types.json import Jsonb
from sqlalchemy.exc import DBAPIError

HEAD = "0041_wallets"
PREVIOUS = "0038_no_pay_marker"

PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
EXPECTED_GRANTS = {
    "wallet_address": {"SELECT", "INSERT", "UPDATE"},  # active and label
    "movement_group": {"SELECT", "INSERT", "UPDATE"},  # status forward, superseded_by once
    "wallet_sync": {"SELECT", "INSERT", "UPDATE"},  # finished once
    "chain_decoder": {"SELECT", "INSERT", "UPDATE"},  # superseded_by once
    "chain_pair": {"SELECT", "INSERT", "UPDATE"},  # superseded_by once
    "holdings_export": {"SELECT", "INSERT"},
}

EVM = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"  # EIP-55's first example
EVM_2 = "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359"  # EIP-55's second example
BECH32 = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"  # BIP-173's example
LEGACY = "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"  # the Bitcoin wiki's example address
SOLANA = "11111111111111111111111111111111"  # the system program: 32 zero bytes
SOLANA_2 = "Stake11111111111111111111111111111111111111"  # the stake program


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


def _hmac(text: str = "") -> bytes:
    return hashlib.sha256((text or uuid.uuid4().hex).encode()).digest()


def _wallet(conn, ws, **overrides) -> uuid.UUID:
    row = {
        "label": "Hardware-Wallet",
        "bank": "Testwallet",
        "kind": "wallet",
        "provider": "Testwallet",
        "iban_masked": None,
        "iban_hmac": None,
    } | overrides
    return _account(conn, ws, **row)


def _address_row(ws, account_id, **overrides) -> dict:
    return {
        "workspace_id": ws,
        "account_id": account_id,
        "chain": "ethereum",
        "address": EVM,
        "address_masked": "0x5a…eAed",
        "address_hmac": _hmac(),
    } | overrides


def _address(conn, ws, account_id, **overrides) -> uuid.UUID:
    return _insert(conn, "wallet_address", _address_row(ws, account_id, **overrides))


def _group_row(ws, account_id, **overrides) -> dict:
    return {
        "workspace_id": ws,
        "account_id": account_id,
        "chain": "ethereum",
        "tx_hash": "0x" + hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        "occurred_at": "2026-05-04T10:00:00Z",
        "kind": "transfer",
    } | overrides


def _chain_movement(ws, account_id, asset_id, address_id, **overrides) -> dict:
    return (
        _movement(
            ws,
            account_id,
            asset_id,
            kind="internal_transfer_in",
            quantity="0.5000000000",
            eur_amount=None,
            source="chain",
            address_id=address_id,
            index="7",
            movement_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        )
        | overrides
    )


@pytest.fixture
def scope(connect, workspaces):
    """(conn, workspace, ids) as the app role, rolled back after the test."""
    a, _ = workspaces
    with connect(APP, a) as conn:
        ids = {"wallet": _wallet(conn, a), "bank": _account(conn, a)}
        ids["coin"] = _asset(conn, a, symbol="ETH", name="Ether", chain="ethereum")
        ids["address"] = _address(conn, a, ids["wallet"])
        yield conn, a, ids


# --- wallet_address ----------------------------------------------------------------------


def test_an_address_belongs_to_a_wallet_account(scope):
    conn, a, ids = scope
    with pytest.raises(errors.CheckViolation, match="no wallet"), conn.transaction():
        _address(conn, a, ids["bank"], address=EVM_2, address_hmac=_hmac(EVM_2))


@pytest.mark.parametrize(
    ("chain", "address"),
    [
        ("ethereum", EVM),
        ("arbitrum", EVM.lower()),  # lower-case hex is accepted (D179 (2))
        ("bsc", EVM_2),
        ("bitcoin", BECH32),
        ("bitcoin", LEGACY),
        ("solana", SOLANA),
        ("solana", SOLANA_2),
    ],
)
def test_an_address_of_its_chains_format_is_accepted(scope, chain, address):
    conn, a, ids = scope
    _address(conn, a, ids["wallet"], chain=chain, address=address)


@pytest.mark.parametrize(
    "change",
    [
        {"chain": "bitcoin"},  # a 0x address on Bitcoin
        {"chain": "solana"},
        {"address": EVM[:-1]},  # 39 hex digits
        {"address": "0x" + "g" * 40},
        {"chain": "ethereum", "address": BECH32},
        {"chain": "bitcoin", "address": BECH32.upper()},  # bech32 is stored lower case
        {"chain": "bitcoin", "address": "bc1" + "b" * 39},  # b is no bech32 character
        {"chain": "solana", "address": "0" + SOLANA[1:]},  # 0 is no base58 character
        {"chain": "solana", "address": "1" * 31},  # too short for 32 bytes
        {"chain": "dogecoin"},
        {"address_masked": "0x5aAeb6…eAed"},
        {"address_hmac": b"short"},
        {"label": ""},
        {"kind": "change"},  # change is Bitcoin's
        {"kind": "stake"},  # a stake account is Solana's
        {"kind": "deposit"},
    ],
)
def test_an_address_outside_its_checks_is_refused(scope, change):
    conn, a, ids = scope
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _address(conn, a, ids["wallet"], **change)


def test_a_change_and_a_stake_address_on_their_chains(scope):
    conn, a, ids = scope
    _address(
        conn, a, ids["wallet"], chain="bitcoin", address=BECH32, address_hmac=_hmac(), kind="change"
    )
    _address(
        conn, a, ids["wallet"], chain="solana", address=SOLANA, address_hmac=_hmac(), kind="stake"
    )


def test_an_address_is_unique_per_chain_not_per_hmac(scope):
    """D179 (2): one 0x address is three rows on the three EVM chains, once per chain."""
    conn, a, ids = scope
    same = _hmac()
    for chain in ("ethereum", "arbitrum", "bsc"):
        _address(conn, a, ids["wallet"], chain=chain, address_hmac=same)
    with pytest.raises(errors.UniqueViolation), conn.transaction():
        _address(conn, a, ids["wallet"], chain="ethereum", address_hmac=same)


def test_an_address_changes_only_its_label_and_active(scope):
    conn, _, ids = scope
    address = ids["address"]
    conn.execute(
        "UPDATE wallet_address SET label = 'Ledger', active = false WHERE id = %s", (address,)
    )
    for change in (
        "address = '0x" + "0" * 40 + "'",
        "chain = 'arbitrum'",
        "kind = 'receive', address_masked = '0x00…0000'",
    ):
        with pytest.raises(errors.RestrictViolation), conn.transaction():
            conn.execute(f"UPDATE wallet_address SET {change} WHERE id = %s", (address,))
    with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
        conn.execute("DELETE FROM wallet_address WHERE id = %s", (address,))


def test_an_address_is_invisible_to_another_workspace(scope, workspaces):
    conn, a, ids = scope
    _, b = workspaces
    assert conn.execute("SELECT count(*) FROM wallet_address").fetchone() == (1,)
    conn.execute("SELECT set_config('app.workspace_id', %s, false)", (str(b),))
    assert conn.execute("SELECT count(*) FROM wallet_address").fetchone() == (0,)
    # the wallet trigger, which runs before WITH CHECK, no longer sees the account
    with pytest.raises(errors.CheckViolation, match="no wallet"), conn.transaction():
        _address(conn, a, ids["wallet"], chain="arbitrum")


# --- wallet_sync -------------------------------------------------------------------------


def _sync_row(ws, ids, **overrides) -> dict:
    return {
        "workspace_id": ws,
        "account_id": ids["wallet"],
        "address_id": ids["address"],
        "source_host": "api.etherscan.io",
        "cursor_before": "100",
    } | overrides


def test_a_sync_is_finished_once(scope):
    conn, a, ids = scope
    sync = _insert(conn, "wallet_sync", _sync_row(a, ids))
    with pytest.raises(errors.RestrictViolation), conn.transaction():
        conn.execute("UPDATE wallet_sync SET source_host = 'other.example' WHERE id = %s", (sync,))
    with pytest.raises(errors.CheckViolation), conn.transaction():  # finished needs a status
        conn.execute("UPDATE wallet_sync SET finished_at = now() WHERE id = %s", (sync,))
    conn.execute(
        "UPDATE wallet_sync SET status = 'succeeded', finished_at = now(), cursor_after = '120',"
        " block = 120, txs_seen = 2, balances = %s, reconciled = true WHERE id = %s",
        (Jsonb({"ethereum:native": "1000"}), sync),
    )
    with pytest.raises(errors.RestrictViolation), conn.transaction():
        conn.execute("UPDATE wallet_sync SET reconciled = false WHERE id = %s", (sync,))
    with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
        conn.execute("DELETE FROM wallet_sync WHERE id = %s", (sync,))


@pytest.mark.parametrize(
    "change",
    [
        {"status": "queued"},
        {"status": "failed"},  # a finished run has its finished_at and a failure its code
        {"source_host": "https://api.etherscan.io/api?apikey=x"},  # a host, never a URL or key
        {"txs_seen": -1},
        {"reconciled": True},  # reconciled against balances it does not have
        {"difference": Jsonb({"ethereum:native": "-1"})},  # a difference only when unreconciled
        {"error_code": "wallet.source_refused"},  # an error only on a failed run
        {"balances": Jsonb([1])},
    ],
)
def test_a_sync_outside_its_checks_is_refused(scope, change):
    conn, a, ids = scope
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(conn, "wallet_sync", _sync_row(a, ids, **change))


def test_a_sync_stores_an_original_only_with_new_movements(scope):
    conn, a, ids = scope
    document = _document(conn, a, type="statement", subtype="chain_sync")
    imported = _insert(
        conn,
        "movement_import",
        {
            "workspace_id": a,
            "account_id": ids["wallet"],
            "document_id": document,
            "format": "chain_sync",
        },
    )
    row = _sync_row(a, ids, import_id=imported, document_id=document)
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(conn, "wallet_sync", row)  # no new movement
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(conn, "wallet_sync", row | {"document_id": None, "movements_new": 1})
    _insert(conn, "wallet_sync", row | {"movements_new": 1})


def test_a_sync_reads_an_address_of_its_own_account(scope):
    conn, a, ids = scope
    other = _wallet(conn, a, label="Zweite Wallet")
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
        _insert(conn, "wallet_sync", _sync_row(a, ids, account_id=other))


# --- movement_group ----------------------------------------------------------------------


def test_a_group_moves_forward_and_is_superseded_once(scope):
    conn, a, ids = scope
    group = _insert(conn, "movement_group", _group_row(a, ids["wallet"], kind="unknown"))
    with pytest.raises(errors.RestrictViolation), conn.transaction():
        conn.execute("UPDATE movement_group SET kind = 'swap' WHERE id = %s", (group,))
    with pytest.raises(errors.RestrictViolation), conn.transaction():  # no change at all
        conn.execute("UPDATE movement_group SET status = 'proposed' WHERE id = %s", (group,))
    conn.execute("UPDATE movement_group SET status = 'confirmed' WHERE id = %s", (group,))
    with pytest.raises(errors.RestrictViolation), conn.transaction():
        conn.execute("UPDATE movement_group SET status = 'proposed' WHERE id = %s", (group,))
    answered = _insert(conn, "movement_group", _group_row(a, ids["wallet"], kind="swap"))
    conn.execute("UPDATE movement_group SET superseded_by = %s WHERE id = %s", (answered, group))
    with pytest.raises(errors.RestrictViolation), conn.transaction():  # once only
        conn.execute("UPDATE movement_group SET superseded_by = NULL WHERE id = %s", (group,))
    with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
        conn.execute("DELETE FROM movement_group WHERE id = %s", (group,))


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "trade"},
        {"status": "rejected"},
        {"chain": "dogecoin"},
        {"tx_hash": ""},
        {"question": ""},
    ],
)
def test_a_group_outside_its_checks_is_refused(scope, change):
    conn, a, ids = scope
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(conn, "movement_group", _group_row(a, ids["wallet"], **change))


def test_a_chain_movement_names_its_address_index_and_hash_and_its_groups_account(scope):
    conn, a, ids = scope
    group = _insert(conn, "movement_group", _group_row(a, ids["wallet"]))
    row = _chain_movement(a, ids["wallet"], ids["coin"], ids["address"], group_id=group)
    moved = _insert(conn, "asset_movement", row)
    for change in ({"address_id": None}, {"index": None}, {"movement_hash": None}):
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _insert(conn, "asset_movement", row | change)
    other = _wallet(conn, a, label="Zweite Wallet")
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():  # another account's
        _insert(
            conn,
            "asset_movement",
            row
            | {"account_id": other, "movement_hash": None, "source": "user", "address_id": None},
        )
    with pytest.raises(errors.RestrictViolation), conn.transaction():  # the group is fixed
        conn.execute("UPDATE asset_movement SET group_id = NULL WHERE id = %s", (moved,))


@pytest.mark.parametrize("kind", ["airdrop", "spam", "lp_deposit", "lp_withdrawal"])
def test_a_chain_kind_comes_from_a_chain_or_a_person(scope, kind):
    conn, a, ids = scope
    _insert(
        conn,
        "asset_movement",
        _chain_movement(a, ids["wallet"], ids["coin"], ids["address"], kind=kind),
    )
    _insert(
        conn, "asset_movement", _movement(a, ids["wallet"], ids["coin"], kind=kind, eur_amount=None)
    )
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(
            conn,
            "asset_movement",
            _movement(a, ids["wallet"], ids["coin"], kind=kind, source="csv", eur_amount=None),
        )


# --- chain_decoder, chain_pair -----------------------------------------------------------


def test_a_decoder_row_is_superseded_once_and_names_a_program_of_its_chain(scope):
    conn, a, _ = scope
    row = {"workspace_id": a, "chain": "solana", "program": SOLANA_2, "kind": "stake"}
    decoder = _insert(conn, "chain_decoder", row)
    newer = _insert(conn, "chain_decoder", row | {"kind": "reward"})
    with pytest.raises(errors.RestrictViolation), conn.transaction():
        conn.execute("UPDATE chain_decoder SET kind = 'swap' WHERE id = %s", (decoder,))
    conn.execute("UPDATE chain_decoder SET superseded_by = %s WHERE id = %s", (newer, decoder))
    with pytest.raises(errors.RestrictViolation), conn.transaction():
        conn.execute("UPDATE chain_decoder SET superseded_by = NULL WHERE id = %s", (decoder,))
    for change in (
        {"program": EVM},  # a 0x program on Solana
        {"chain": "bitcoin", "program": BECH32},  # Bitcoin has no programs
        {"kind": "transfer"},  # a decoder never makes a transfer
    ):
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _insert(conn, "chain_decoder", row | change)


def test_a_pair_is_a_bridge_across_chains_or_a_wrap_on_one(scope):
    conn, a, ids = scope
    on_arbitrum = _asset(conn, a, symbol="ETH", name="Ether", chain="arbitrum")
    wrapped = _asset(
        conn,
        a,
        symbol="WETH",
        name="Wrapped Ether",
        chain="ethereum",
        contract_address="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        priced_as_asset_id=ids["coin"],
    )
    bridge = {
        "workspace_id": a,
        "kind": "bridge",
        "from_chain": "ethereum",
        "from_asset_id": ids["coin"],
        "to_chain": "arbitrum",
        "to_asset_id": on_arbitrum,
        "max_fee_share": "0.0100",
    }
    pair = _insert(conn, "chain_pair", bridge)
    wrap = bridge | {"kind": "wrap", "to_chain": "ethereum", "to_asset_id": wrapped}
    with pytest.raises(errors.CheckViolation), conn.transaction():  # a wrap keeps no fee
        _insert(conn, "chain_pair", wrap)
    _insert(conn, "chain_pair", wrap | {"max_fee_share": None})
    for change in (
        {"to_chain": "ethereum"},  # a bridge on one chain
        {"max_fee_share": "1.0000"},
        {"to_asset_id": ids["coin"]},
    ):
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _insert(conn, "chain_pair", bridge | change)
    with pytest.raises(errors.RestrictViolation), conn.transaction():
        conn.execute("UPDATE chain_pair SET max_fee_share = 0.02 WHERE id = %s", (pair,))


# --- holdings_export ---------------------------------------------------------------------


def test_a_holdings_export_is_immutable(scope):
    conn, a, ids = scope
    digests = []
    for media in ("application/pdf", "text/csv"):
        digest = hashlib.sha256(uuid.uuid4().bytes).digest()
        conn.execute(
            "INSERT INTO document_file (workspace_id, sha256, size_bytes, media_type, storage_key)"
            " VALUES (%s, %s, 11, %s, %s)",
            (a, digest, media, f"{a}/sha256/{digest.hex()}"),
        )
        digests.append(digest)
    row = {
        "workspace_id": a,
        "account_id": ids["wallet"],
        "as_of": "2026-12-31",
        "pdf_sha256": digests[0],
        "csv_sha256": digests[1],
        "statement": Jsonb({"on": "2026-12-31", "accounts": []}),
    }
    export = _insert(conn, "holdings_export", row)
    with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
        conn.execute("UPDATE holdings_export SET as_of = '2026-12-30' WHERE id = %s", (export,))
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(conn, "holdings_export", row | {"statement": Jsonb([])})
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
        _insert(conn, "holdings_export", row | {"pdf_sha256": _hmac()})


def test_a_holdings_export_is_immutable_for_the_owner_too(connect, workspaces):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        digest = hashlib.sha256(uuid.uuid4().bytes).digest()
        conn.execute(
            "INSERT INTO document_file (workspace_id, sha256, size_bytes, media_type, storage_key)"
            " VALUES (%s, %s, 11, 'application/pdf', %s)",
            (a, digest, f"{a}/sha256/{digest.hex()}"),
        )
        export = _insert(
            conn,
            "holdings_export",
            {
                "workspace_id": a,
                "account_id": _wallet(conn, a),
                "as_of": "2026-12-31",
                "pdf_sha256": digest,
                "csv_sha256": digest,
                "statement": Jsonb({}),
            },
        )
        with pytest.raises(errors.RestrictViolation), conn.transaction():
            conn.execute("UPDATE holdings_export SET as_of = '2026-12-30' WHERE id = %s", (export,))
        with pytest.raises(errors.RestrictViolation), conn.transaction():
            conn.execute("DELETE FROM holdings_export WHERE id = %s", (export,))


# --- asset ---------------------------------------------------------------------------------


def test_a_symbol_is_unique_per_chain(scope):
    """D180 (2): ETH on Ethereum, ETH on Arbitrum and an exchange's chainless ETH are three."""
    conn, a, ids = scope
    _asset(conn, a, symbol="ETH", name="Ether", chain="arbitrum")
    _asset(conn, a, symbol="ETH", name="Ether")
    with pytest.raises(errors.UniqueViolation), conn.transaction():
        _asset(conn, a, symbol="ETH", name="Ether", chain="ethereum")
    with pytest.raises(errors.UniqueViolation), conn.transaction():
        _asset(conn, a, symbol="ETH", name="Ether")
    # a fund without a symbol still repeats freely
    _asset(conn, a, kind="etf", symbol=None, isin="IE00B4L5Y983", name="Fonds A")
    _asset(conn, a, kind="etf", symbol=None, isin="IE00B5BMR087", name="Fonds B")


def test_a_contract_is_unique_per_chain_and_of_its_chains_format(scope):
    conn, a, ids = scope
    usdc = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
    token = {"symbol": "USDC", "name": "USD Coin", "chain": "ethereum", "contract_address": usdc}
    _asset(conn, a, **token)
    with pytest.raises(errors.UniqueViolation), conn.transaction():
        _asset(conn, a, **token | {"symbol": "USDC2"})
    _asset(conn, a, **token | {"chain": "arbitrum"})
    for change in (
        {"chain": "solana"},  # a 0x contract on Solana
        {"chain": "bitcoin"},
        {"chain": None},  # a contract names its chain
        {"contract_address": "0x1234"},
    ):
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _asset(conn, a, **token | {"symbol": "USDX"} | change)


@pytest.mark.parametrize(
    "change",
    [
        {"spam": True},  # spam is a chain's
        {"chain": "dogecoin"},
    ],
)
def test_an_asset_outside_its_chain_checks_is_refused(scope, change):
    conn, a, _ = scope
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _asset(conn, a, symbol="NEW", **change)


def test_an_asset_is_priced_as_another_on_a_chain_only(scope):
    conn, a, ids = scope
    exchange_eth = _asset(conn, a, symbol="ETH", name="Ether")
    with pytest.raises(errors.CheckViolation), conn.transaction():  # chainless
        _asset(conn, a, symbol="WETH", priced_as_asset_id=exchange_eth)
    wrapped = _asset(conn, a, symbol="WETH", chain="ethereum", priced_as_asset_id=exchange_eth)
    with pytest.raises(errors.CheckViolation), conn.transaction():
        conn.execute("UPDATE asset SET priced_as_asset_id = id WHERE id = %s", (wrapped,))


# --- transfer_link -------------------------------------------------------------------------


def test_a_chain_link_joins_two_movements(scope):
    conn, a, ids = scope
    out = _insert(
        conn,
        "asset_movement",
        _chain_movement(
            a,
            ids["wallet"],
            ids["coin"],
            ids["address"],
            kind="internal_transfer_out",
            quantity="-0.5000000000",
        ),
    )
    other = _wallet(conn, a, label="Zweite Wallet")
    second = _address(conn, a, other, chain="arbitrum")
    on_arbitrum = _asset(conn, a, symbol="ETH", name="Ether", chain="arbitrum")
    into = _insert(conn, "asset_movement", _chain_movement(a, other, on_arbitrum, second))
    for kind in ("chain", "bridge", "wrap"):
        link = {
            "workspace_id": a,
            "kind": kind,
            "out_movement_id": out,
            "in_movement_id": into,
            "matched_by": "matcher",
        }
        _insert(conn, "transfer_link", link)
        # a chain kind never links a bank line (the CHECK fires before the FK)
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _insert(
                conn,
                "transfer_link",
                link | {"in_movement_id": None, "in_transaction_id": uuid.uuid4()},
            )


# --- Up, down, up and the refused downgrade (each on its own database) ---------------------


def _column_exists(conn, table: str, column: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
            (table, column),
        ).fetchone()
        is not None
    )


def _check(conn, name: str) -> str:
    return conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = %s", (name,)
    ).fetchone()[0]


def test_0041_runs_up_down_and_up_on_an_empty_database():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        with _admin(name) as conn:
            assert _column_exists(conn, "asset", "chain")
            assert "chain" in _check(conn, "transfer_link_kind_check")
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            for table in EXPECTED_GRANTS:
                assert conn.execute("SELECT to_regclass(%s)", (table,)).fetchone() == (None,)
            for column in ("chain", "contract_address", "priced_as_asset_id", "spam"):
                assert not _column_exists(conn, "asset", column)
            for column in ("group_id", "address_id", "index"):
                assert not _column_exists(conn, "asset_movement", column)
            assert "'chain'" not in _check(conn, "transfer_link_kind_check")
            assert "'chain'" not in _check(conn, "asset_movement_source_check")
            assert "airdrop" not in _check(conn, "asset_movement_kind_check")
            assert "chain_sync" not in _check(conn, "movement_import_format_check")
            assert "chain_sync" not in _check(conn, "document_subtype_check")
            assert "wallet" not in _check(conn, "event_entity_type_check")
            assert _check(conn, "asset_workspace_id_symbol_key") == "UNIQUE (workspace_id, symbol)"
        alembic(name, "upgrade", HEAD)
    finally:
        drop_database(name)


@pytest.mark.parametrize(
    ("seed", "what"),
    [
        ("address", "table wallet_address holds rows"),
        ("chain_asset", "an asset is a chain asset"),
        ("chain_event", "an event is about a wallet, an address or a movement group"),
    ],
)
def test_0041_downgrade_refuses_while_data_would_be_lost(seed, what):
    """A downgrade never drops a row nor leaves one the old CHECK refuses; the guard sees rows
    under forced RLS and restores FORCE afterwards."""
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            if seed == "address":
                _address(conn, ws, _wallet(conn, ws))
            elif seed == "chain_asset":
                _asset(conn, ws, symbol="ETH", name="Ether", chain="ethereum")
            else:
                insert_event(conn, ws, type="wallet.created", entity_type="wallet")
            forced = _forced(conn)
        with pytest.raises(DBAPIError, match=f"downgrade of {HEAD} refused: {what}") as refused:
            alembic(name, "downgrade", PREVIOUS)
        assert isinstance(refused.value.orig, errors.RestrictViolation)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
            assert _forced(conn) == forced
    finally:
        drop_database(name)
