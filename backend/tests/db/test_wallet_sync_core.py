"""Phase 7 step 2 core: one address's run end to end against the database, with a FAKE chain
source and a FAKE family classifier (docs/phases/phase-7.md D182, D186).

The fakes stand in for the family tasks' adapters: Solana's native coin (9 decimals) is the
only asset, the addresses are Solana's system and stake program ids (valid in format, owned
by nobody) and every amount, hash and record is invented.
"""

import hashlib
import itertools
import logging
from datetime import UTC, datetime, timedelta

import pytest
from db.db_support import _admin
from db.test_phase7_migration import BECH32, EVM, _address, _wallet

from hausbuch.db.session import workspace_transaction
from hausbuch.rules.chain.classify import ChainGroup, ChainLeg, register_family
from hausbuch.rules.errors import (
    WALLET_KEY_MISSING,
    WALLET_SOURCE_THROTTLED,
    WALLET_SOURCE_UNAVAILABLE,
    WALLET_SYNC_DEFERRED,
)
from hausbuch.rules.review import CHAIN_UNSTORABLE
from hausbuch.schemas.chain import (
    AssetBalance,
    AssetKey,
    Chain,
    ChainFamily,
    ChainSourceKind,
    ChainTx,
    GroupKind,
    Page,
    WalletSyncStatus,
)
from hausbuch.schemas.enums import ActorType
from hausbuch.schemas.household import MovementKind
from hausbuch.services.chain.base import (
    ChainSourceError,
    RequestBudget,
    register_source,
)
from hausbuch.services.chain.sync import WALLET_SYNC_ERROR, sync_address, sync_wallets
from hausbuch.services.context import Actor
from hausbuch.services.prices.assets import price_ids
from hausbuch.services.prices.fetch import held_assets
from hausbuch.services.prices.http import SourceError
from hausbuch.settings import Settings

pytestmark = pytest.mark.db

SOL_A = "11111111111111111111111111111111"
SOL_B = "Stake11111111111111111111111111111111111111"
SOL = AssetKey(chain=Chain.SOLANA)
ACTOR = Actor(ActorType.SYSTEM, "sync_wallets")
SETTINGS = Settings(
    database_url="postgresql+psycopg://hausbuch_app@localhost/unused",
    iban_hmac_key=bytes(range(32)),
    session_key=bytes(range(1, 33)),
    rp_id="testserver",
    origin="https://testserver",
    blob_store="local",
    blob_root="/tmp/unused",
)
T0 = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)


def _tx(n: int, **raw) -> ChainTx:
    digest = hashlib.sha256(f"invented tx {n}".encode()).hexdigest()
    return ChainTx(
        hash=digest,
        block=1000 + n,
        occurred_at=T0 + timedelta(hours=n),
        status="ok",
        raw={"n": n, **raw},
    )


class World:
    """The fake chain: per address its transactions, balance and an error to raise."""

    def __init__(self) -> None:
        self.txs: dict[str, list[ChainTx]] = {SOL_A: [], SOL_B: []}
        self.balance: dict[str, int] = {SOL_A: 0, SOL_B: 0}
        self.tokens: dict[str, dict[str, int]] = {}  # address -> mint -> units
        self.error: dict[str, Exception] = {}
        self.scope = "address"
        self.key_missing = False


class FakeSource:
    kind = ChainSourceKind.SOLANA_RPC
    host = "rpc.example.test"

    def __init__(self, world: World, budget: RequestBudget) -> None:
        self.world, self.budget = world, budget
        self.reconcile_scope = world.scope

    def transactions(self, address: str, cursor: str | None) -> Page[ChainTx]:
        if address in self.world.error:
            raise self.world.error[address]
        if not self.budget.take():
            return Page[ChainTx](items=[], next_cursor=cursor)
        start = int(cursor or 0)
        items = self.world.txs[address][start : start + 2]
        return Page[ChainTx](items=items, next_cursor=str(start + len(items)))

    def balances(self, address: str) -> list[AssetBalance]:
        tokens = [
            AssetBalance(
                asset_key=AssetKey(chain=Chain.SOLANA, contract=mint), units=units, at_block=2000
            )
            for mint, units in self.world.tokens.get(address, {}).items()
        ]
        native = AssetBalance(asset_key=SOL, units=self.world.balance[address], at_block=2000)
        return [native, *tokens]


def fake_classifier(tx, ctx):
    """raw: in / out / fee units for the synced address, `on` another own address for the
    legs, `question` for a legless card, `ask` a question beside the legs, `tok` token legs
    (dicts of ChainLeg's fields: mint, units, index, symbol, decimals, spam), `linked` a
    transfer D186 (1) confirms after the first sync."""
    if "question" in tx.raw:
        return ChainGroup(
            tx.hash, tx.occurred_at, tx.block, GroupKind.UNKNOWN, question=tx.raw["question"]
        )
    on = tx.raw.get("on")
    legs = []
    if tx.raw.get("in"):
        kind = MovementKind.INTERNAL_TRANSFER_IN
        legs.append(ChainLeg(SOL, tx.raw["in"], "in", kind, "0", address=on))
    if tx.raw.get("out"):
        kind = MovementKind.INTERNAL_TRANSFER_OUT
        legs.append(ChainLeg(SOL, tx.raw["out"], "out", kind, "0", address=on))
    if tx.raw.get("fee"):
        legs.append(ChainLeg(SOL, tx.raw["fee"], "fee", MovementKind.FEE, "fee", address=on))
    for t in tx.raw.get("tok", []):
        key = AssetKey(chain=Chain.SOLANA, contract=t["mint"])
        kind = MovementKind.SPAM if t.get("spam") else MovementKind.AIRDROP
        legs.append(
            ChainLeg(
                key,
                t["units"],
                "in",
                kind,
                t.get("index", "1"),
                symbol=t.get("symbol"),
                decimals=t.get("decimals"),
                spam=bool(t.get("spam")),
            )
        )
    if tx.raw.get("boom"):
        raise RuntimeError(f"classifier failed on {ctx.address}")  # must never be logged
    return ChainGroup(
        tx.hash,
        tx.occurred_at,
        tx.block,
        GroupKind.TRANSFER,
        legs,
        linked=bool(tx.raw.get("linked")),
        question=tx.raw.get("ask"),
    )


@pytest.fixture
def world():
    w = World()

    def factory(row, settings, budget):
        if w.key_missing:
            raise ChainSourceError(WALLET_KEY_MISSING)
        return FakeSource(w, budget)

    register_source(ChainSourceKind.SOLANA_RPC, factory)
    register_family(ChainFamily.SOLANA, fake_classifier)
    yield w
    register_source(ChainSourceKind.SOLANA_RPC, None)
    register_family(ChainFamily.SOLANA, None)


@pytest.fixture
def wallet(dbname, fresh_workspaces):
    ws = fresh_workspaces[0]
    with _admin(dbname) as conn:
        account = _wallet(conn, ws)
        a = _address(conn, ws, account, chain="solana", address=SOL_A, address_masked="1111…1111")
        b = _address(conn, ws, account, chain="solana", address=SOL_B, address_masked="Stak…1111")
    return ws, account, a, b


@pytest.fixture
def sync(app_engine, blob_store):
    ticks = itertools.count()

    def clock():
        return T0 + timedelta(days=1, seconds=next(ticks))

    def _sync(ws, address_id, **kw):
        return sync_address(
            app_engine,
            ws,
            address_id,
            settings=SETTINGS,
            store=blob_store,
            actor=ACTOR,
            clock=clock,
            **kw,
        )

    return _sync


def _rows(dbname, query, *params):
    with _admin(dbname) as conn:
        return conn.execute(query, params).fetchall()


def test_two_runs_the_second_from_the_cursor_writes_nothing(dbname, world, wallet, sync):
    ws, account, a, _ = wallet
    world.txs[SOL_A] = [_tx(1, **{"in": 1_000_000_000}), _tx(2, out=200_000_000, fee=5000)]
    world.txs[SOL_A].append(_tx(3, **{"in": 50}))
    world.balance[SOL_A] = 1_000_000_000 - 200_000_000 - 5000 + 50

    first = sync(ws, a)
    assert (first.status, first.movements_new, first.txs_seen) == (
        WalletSyncStatus.SUCCEEDED,
        4,
        3,
    )
    assert first.reconciled is True and not first.deferred
    (row,) = _rows(
        dbname,
        "SELECT cursor_before, cursor_after, document_id, import_id, reconciled, balances"
        " FROM wallet_sync WHERE address_id = %s",
        a,
    )
    assert row[0] is None and row[1] == "3" and row[2] is not None and row[3] is not None
    assert row[4] is True and row[5] == {"native": str(world.balance[SOL_A])}
    quantities = _rows(
        dbname,
        "SELECT m.quantity, m.status, m.address_id, g.status FROM asset_movement m"
        " JOIN movement_group g ON g.id = m.group_id WHERE m.account_id = %s"
        " ORDER BY m.occurred_at, m.quantity",
        account,
    )
    assert [str(q[0].normalize()) for q in quantities] == ["1", "-0.2", "-0.000005", "5E-8"]
    # the address's first sync: every group waits for a person (D186 (1))
    assert {(q[1], q[3]) for q in quantities} == {("proposed", "proposed")}
    assert {q[2] for q in quantities} == {a}

    second = sync(ws, a)
    assert (second.status, second.movements_new, second.txs_seen) == (
        WalletSyncStatus.SUCCEEDED,
        0,
        0,
    )
    assert second.reconciled is True
    rows = _rows(
        dbname,
        "SELECT cursor_before, cursor_after, document_id, import_id FROM wallet_sync"
        " WHERE address_id = %s ORDER BY started_at",
        a,
    )
    assert rows[1] == ("3", "3", None, None)
    originals = _rows(
        dbname,
        "SELECT count(*) FROM document WHERE subtype = 'chain_sync' AND workspace_id = %s",
        ws,
    )
    assert originals[0][0] == 1

    # after the first sync, a fee-only transaction is confirmed by code (D186 (1))
    world.txs[SOL_A].append(_tx(4, fee=5000))
    world.balance[SOL_A] -= 5000
    third = sync(ws, a)
    assert (third.movements_new, third.reconciled) == (1, True)
    (group,) = _rows(
        dbname,
        "SELECT g.status FROM movement_group g WHERE g.account_id = %s AND g.tx_hash = %s",
        account,
        world.txs[SOL_A][-1].hash,
    )
    assert group[0] == "confirmed"


def test_a_failing_address_fails_only_its_own_row(
    dbname, world, wallet, sync, app_engine, blob_store
):
    ws, account, a, b = wallet
    world.txs[SOL_A] = [_tx(1, **{"in": 10})]
    world.balance[SOL_A] = 10
    world.error[SOL_B] = SourceError("HTTPError", 429)

    outcomes = sync_wallets(
        app_engine,
        ws,
        settings=SETTINGS,
        store=blob_store,
        actor=ACTOR,
        clock=lambda: T0 + timedelta(days=1),
        account_id=account,
    )
    by_address = {o.address_id: o for o in outcomes}
    assert by_address[a].status is WalletSyncStatus.SUCCEEDED
    assert by_address[b].status is WalletSyncStatus.THROTTLED
    assert by_address[b].error_code == WALLET_SOURCE_THROTTLED
    (row,) = _rows(
        dbname,
        "SELECT status, error_code, cursor_after, reconciled, finished_at IS NOT NULL"
        " FROM wallet_sync WHERE address_id = %s",
        b,
    )
    assert row == ("throttled", WALLET_SOURCE_THROTTLED, None, None, True)

    # a classifier's failure fails the row with the sync's own code, the cursor unmoved
    world.error.clear()
    world.txs[SOL_B] = [_tx(9, boom=True, **{"in": 1})]
    failed = sync(ws, b)
    assert (failed.status, failed.error_code) == (WalletSyncStatus.FAILED, WALLET_SYNC_ERROR)
    assert _rows(dbname, "SELECT count(*) FROM asset_movement WHERE address_id = %s", b)[0][0] == 0


def test_a_missing_key_writes_no_row(dbname, world, wallet, sync):
    ws, _, a, _ = wallet
    world.key_missing = True
    outcome = sync(ws, a)
    assert (outcome.sync_id, outcome.error_code) == (None, WALLET_KEY_MISSING)
    assert _rows(dbname, "SELECT count(*) FROM wallet_sync WHERE address_id = %s", a)[0][0] == 0


def test_a_spent_budget_defers_without_reconciling(dbname, world, wallet, sync):
    ws, _, a, _ = wallet
    world.txs[SOL_A] = [_tx(n, **{"in": n}) for n in range(1, 6)]
    world.balance[SOL_A] = 15
    budgets = {ChainSourceKind.SOLANA_RPC: RequestBudget(1)}
    first = sync(ws, a, budgets=budgets)
    assert first.deferred and first.reconciled is None and first.movements_new == 2
    (row,) = _rows(
        dbname, "SELECT status, balances, cursor_after FROM wallet_sync WHERE address_id = %s", a
    )
    assert row == ("succeeded", None, "2")
    rest = sync(ws, a)  # the next day: from the cursor, to the end
    assert (rest.movements_new, rest.reconciled, rest.deferred) == (3, True, False)


def test_account_scope_and_a_waiting_question(dbname, world, wallet, sync):
    """Bitcoin's case (fixture wallet_reconcile.yaml bitcoin_two_addresses): a spend's legs
    sit on another own address and only Σ over the account's addresses equals the balance;
    a legless question makes a difference „wartet auf Antwort“, not a mismatch."""
    ws, account, a, b = wallet
    world.scope = "account"
    world.txs[SOL_A] = [_tx(1, on=SOL_B, **{"in": 700}), _tx(2, **{"in": 300})]
    world.balance[SOL_A], world.balance[SOL_B] = 300, 700
    outcome = sync(ws, a)
    assert outcome.reconciled is True
    on_b = _rows(dbname, "SELECT count(*) FROM asset_movement WHERE address_id = %s", b)
    assert on_b[0][0] == 1

    world.txs[SOL_A].append(_tx(3, question="Ist die Ausgabe 2 eine eigene Adresse?"))
    world.balance[SOL_B] = 600  # 100 left to an output the card asks about
    waiting = sync(ws, a)
    assert (waiting.reconciled, waiting.waiting) == (None, True)
    (row,) = _rows(
        dbname,
        "SELECT reconciled, difference, balances FROM wallet_sync WHERE address_id = %s"
        " ORDER BY started_at DESC LIMIT 1",
        a,
    )
    assert row == (None, None, {"native": "900"})
    groups = _rows(
        dbname,
        "SELECT kind, status, question IS NOT NULL FROM movement_group WHERE account_id = %s"
        " AND kind = 'unknown'",
        account,
    )
    assert groups == [("unknown", "proposed", True)]


def test_account_scope_the_last_address_of_the_run_decides(
    dbname, world, wallet, app_engine, blob_store
):
    """A receive on B is in B's history only: A's run sees the account's balance before B's
    movement is stored, so in one job A leaves the verdict to B, which reconciles."""
    ws, account, a, b = wallet
    world.scope = "account"
    world.txs[SOL_A] = [_tx(1, **{"in": 300})]
    world.txs[SOL_B] = [_tx(2, **{"in": 700})]
    world.balance[SOL_A], world.balance[SOL_B] = 300, 700
    run = sync_wallets(
        app_engine,
        ws,
        settings=SETTINGS,
        store=blob_store,
        actor=ACTOR,
        clock=lambda: T0 + timedelta(days=1),
        account_id=account,
    )
    assert [(o.address_id, o.reconciled, o.waiting) for o in run] == [
        (a, None, False),
        (b, True, False),
    ]
    rows = _rows(
        dbname,
        "SELECT address_id, reconciled, difference, balances IS NOT NULL FROM wallet_sync"
        " WHERE account_id = %s ORDER BY started_at, created_at",
        account,
    )
    assert rows == [(a, None, None, True), (b, True, None, True)]


def test_no_plaintext_address_in_events_logs_or_rows(dbname, world, wallet, sync, caplog):
    ws, account, a, b = wallet
    world.txs[SOL_A] = [_tx(1, **{"in": 5})]
    world.balance[SOL_A] = 5
    world.txs[SOL_B] = [_tx(2, boom=True, **{"in": 1})]
    with caplog.at_level(logging.DEBUG):
        sync(ws, a)
        sync(ws, b)
    logged = " ".join(
        f"{r.getMessage()} {getattr(r, 'fields', '')} {r.exc_text or ''}" for r in caplog.records
    )
    events = _rows(dbname, "SELECT type, payload::text FROM event WHERE workspace_id = %s", ws)
    types = {e[0] for e in events}
    assert {"wallet.sync_completed", "wallet.sync_failed", "movement_group.proposed"} <= types
    syncs = _rows(
        dbname, "SELECT row_to_json(s)::text FROM wallet_sync s WHERE account_id = %s", account
    )
    groups = _rows(
        dbname, "SELECT row_to_json(g)::text FROM movement_group g WHERE account_id = %s", account
    )
    for text in [logged, *(e[1] for e in events), *(s[0] for s in syncs), *(g[0] for g in groups)]:
        assert SOL_A not in text and SOL_B not in text
    assert "RuntimeError" in logged  # the class name only


# --- Review fixes (PR #375): one address, one asset or one transaction never blocks the rest -

# Invented mints: Solana program ids, valid base58, no token the registry lists.
MINT_X = "Vote111111111111111111111111111111111111111"
MINT_Y = "Config1111111111111111111111111111111111111"


def _run(app_engine, blob_store, ws, account, *, day=1, budgets=None):
    return sync_wallets(
        app_engine,
        ws,
        settings=SETTINGS,
        store=blob_store,
        actor=ACTOR,
        clock=lambda: T0 + timedelta(days=day),
        account_id=account,
        budgets=budgets,
    )


def test_an_address_without_a_source_is_skipped_and_the_others_run(
    dbname, world, wallet, app_engine, blob_store
):
    """Finding 1: a chain whose reader is not built (base.factory_for's NotImplementedError)
    is skipped with an outcome and no row, like a missing key; any other failure of an
    address's start ends that address only."""
    ws, account, a, b = wallet
    with _admin(dbname) as conn:
        btc = _address(
            conn, ws, account, chain="bitcoin", address=BECH32, address_masked="bc1q…f3t4"
        )
        eth = _address(conn, ws, account, chain="ethereum", address=EVM)
    world.txs[SOL_A] = [_tx(1, **{"in": 5})]
    world.txs[SOL_B] = [_tx(2, **{"in": 7})]
    world.balance[SOL_A], world.balance[SOL_B] = 5, 7

    def not_built(row, settings, budget):
        raise NotImplementedError("services/chain: no make_source in bitcoin_esplora")

    def broken(row, settings, budget):
        raise RuntimeError("a start that fails for another reason")

    register_source(ChainSourceKind.BITCOIN_ESPLORA, not_built)
    register_source(ChainSourceKind.EVM_ETHERSCAN, broken)
    try:
        run = _run(app_engine, blob_store, ws, account)
    finally:
        register_source(ChainSourceKind.BITCOIN_ESPLORA, None)
        register_source(ChainSourceKind.EVM_ETHERSCAN, None)
    by_address = {o.address_id: o for o in run}
    assert set(by_address) == {a, b, btc, eth}
    assert (by_address[a].status, by_address[a].reconciled) == (WalletSyncStatus.SUCCEEDED, True)
    assert (by_address[b].status, by_address[b].reconciled) == (WalletSyncStatus.SUCCEEDED, True)
    assert (by_address[btc].sync_id, by_address[btc].status, by_address[btc].error_code) == (
        None,
        None,
        WALLET_SOURCE_UNAVAILABLE,
    )
    assert (by_address[eth].sync_id, by_address[eth].error_code) == (None, WALLET_SYNC_ERROR)
    rows = _rows(
        dbname,
        "SELECT count(*) FROM wallet_sync WHERE address_id IN (%s, %s)",
        btc,
        eth,
    )
    assert rows[0][0] == 0


def test_a_spent_budget_defers_and_an_address_it_never_reached_writes_no_row(
    dbname, world, wallet, app_engine, blob_store
):
    """Finding 2: a page refused by a spent budget comes back with the cursor unmoved, which
    reads like the tip. Deferred is the budget's verdict: no balance read with a spent
    budget, never a first sync finished by it, and an address the budget never reached
    writes no row, so it runs first the next day and its first sync waits for a person."""
    ws, account, a, b = wallet
    world.txs[SOL_A] = [_tx(n, linked=True, **{"in": n}) for n in range(1, 5)]
    world.balance[SOL_A] = 10
    world.txs[SOL_B] = [_tx(9, linked=True, **{"in": 700})]
    world.balance[SOL_B] = 700
    budgets = {ChainSourceKind.SOLANA_RPC: RequestBudget(2)}

    first = {o.address_id: o for o in _run(app_engine, blob_store, ws, account, budgets=budgets)}
    assert (first[a].txs_seen, first[a].deferred, first[a].reconciled) == (4, True, None)
    assert (first[b].sync_id, first[b].status, first[b].deferred, first[b].error_code) == (
        None,
        None,
        True,
        WALLET_SYNC_DEFERRED,
    )
    assert _rows(dbname, "SELECT count(*) FROM wallet_sync WHERE address_id = %s", b)[0][0] == 0
    (row,) = _rows(
        dbname, "SELECT balances, cursor_after FROM wallet_sync WHERE address_id = %s", a
    )
    assert row == (None, "4")

    second = _run(app_engine, blob_store, ws, account, day=2)
    assert [o.address_id for o in second] == [b, a]  # B, never synced, first
    by_address = {o.address_id: o for o in second}
    assert (by_address[b].movements_new, by_address[b].reconciled) == (1, True)
    assert (by_address[a].movements_new, by_address[a].reconciled) == (0, True)
    statuses = _rows(
        dbname,
        "SELECT g.status, m.status FROM asset_movement m JOIN movement_group g"
        " ON g.id = m.group_id WHERE m.address_id = %s",
        b,
    )
    # B's first sync, though D186 (1) would confirm a linked transfer after it
    assert statuses == [("proposed", "proposed")]


def test_an_unlisted_token_has_no_symbol_and_its_told_name_is_cleaned(
    dbname, world, wallet, sync, app_engine
):
    """Finding 3 (D180 amended (a)): every token the registry does not list is stored with
    symbol NULL, so a token calling itself „BTC“ never reaches a price by that symbol; what
    it tells is kept inside its name only, without control characters, capped."""
    ws, account, a, _ = wallet
    world.txs[SOL_A] = [
        _tx(1, **{"in": 5}),
        _tx(2, tok=[{"mint": MINT_X, "units": 5, "symbol": "BTC", "decimals": 6}]),
        _tx(3, tok=[{"mint": MINT_Y, "units": 5, "symbol": "AB\x85C\x00", "decimals": 6}]),
        _tx(
            4,
            tok=[
                {
                    "mint": "Sysvar1111111111111111111111111111111111111",
                    "units": 1,
                    "symbol": "FREE\x00\x00" + "x" * 40,
                    "decimals": 0,
                    "spam": True,
                }
            ],
        ),
    ]
    world.balance[SOL_A] = 5
    world.tokens[SOL_A] = {MINT_X: 5, MINT_Y: 5}
    outcome = sync(ws, a)
    assert (outcome.status, outcome.movements_new, outcome.reconciled) == (
        WalletSyncStatus.SUCCEEDED,
        4,
        True,
    )
    assets = dict(
        _rows(
            dbname,
            "SELECT contract_address, row(symbol, name, spam)::text FROM asset"
            " WHERE contract_address IS NOT NULL AND workspace_id = %s",
            ws,
        )
    )
    assert assets[MINT_X] == '(,"Token Vote…1111 (nennt sich BTC)",f)'
    assert assets[MINT_Y] == '(,"Token Conf…1111 (nennt sich ABC)",f)'
    spam_name = "Token Sysv…1111 (nennt sich FREE" + "x" * 28 + ")"
    assert assets["Sysvar1111111111111111111111111111111111111"] == f'(,"{spam_name}",t)'
    assert price_ids(None, None, None, chain="solana") == price_ids(None, None, None)
    with _admin(dbname) as conn:  # a person's „Bestätigen“ on the airdrop cards
        conn.execute(
            "UPDATE asset_movement SET status = 'confirmed' WHERE account_id = %s", (account,)
        )
    with workspace_transaction(app_engine, ws) as conn:
        held = {h.name: h for h in held_assets(conn)}
    told_btc = held["Token Vote…1111 (nennt sich BTC)"]
    assert told_btc.symbol is None
    assert (told_btc.ids.coingecko_id, told_btc.ids.kraken_pair) == (None, None)


def test_an_unstorable_spam_leg_is_left_out_and_the_history_goes_on(dbname, world, wallet, sync):
    """Finding 4: a spam leg beyond numeric(38,18) (10^30 units of a 0-decimal token, or a
    token telling 24 decimals) is dropped and counted (D187: hidden, never priced, never a
    lot); the address's other transactions are stored and the cursor moves past it."""
    ws, account, a, _ = wallet
    world.txs[SOL_A] = [
        _tx(1, **{"in": 5}),
        _tx(2, tok=[{"mint": MINT_X, "units": 10**30, "decimals": 0, "spam": True}]),
        _tx(3, tok=[{"mint": MINT_Y, "units": 1, "decimals": 24, "spam": True}], fee=1),
        _tx(4, **{"in": 7}),
    ]
    world.balance[SOL_A] = 11
    outcome = sync(ws, a)
    assert (outcome.status, outcome.spam_dropped, outcome.unstorable) == (
        WalletSyncStatus.SUCCEEDED,
        2,
        0,
    )
    assert (outcome.movements_new, outcome.reconciled) == (3, True)
    assert _rows(dbname, "SELECT cursor_after FROM wallet_sync WHERE address_id = %s", a) == [
        ("4",)
    ]
    assets = _rows(
        dbname,
        "SELECT count(*) FROM asset WHERE contract_address IS NOT NULL AND workspace_id = %s",
        ws,
    )
    assert assets[0][0] == 0  # neither spam token became an asset
    groups = _rows(dbname, "SELECT tx_hash FROM movement_group WHERE account_id = %s", account)
    assert world.txs[SOL_A][1].hash not in {g[0] for g in groups}
    assert world.txs[SOL_A][2].hash in {g[0] for g in groups}  # its fee leg stays


def test_an_unstorable_transaction_becomes_a_group_without_movements(dbname, world, wallet, sync):
    """Finding 4: a non-spam transaction Hausbuch cannot store (a quantity beyond the column,
    two assets on one leg's place, a group the database refuses) becomes a proposed group
    with no movements, no question and reason routing.chain_unstorable; the cursor moves on,
    the run's original is kept, and its difference stays a mismatch, never „wartet“."""
    ws, account, a, _ = wallet
    world.txs[SOL_A] = [
        _tx(1, **{"in": 5}),
        _tx(2, tok=[{"mint": MINT_X, "units": 10**30, "decimals": 0}]),
        _tx(3, tok=[{"mint": MINT_Y, "units": 2, "decimals": 6, "index": "0"}], **{"in": 1}),
        _tx(4, ask="x" * 600, **{"in": 3}),  # a question the column cannot hold
        _tx(5, **{"in": 7}),
    ]
    world.balance[SOL_A] = 5 + 1 + 3 + 7
    world.tokens[SOL_A] = {MINT_X: 10**30}
    outcome = sync(ws, a)
    assert (outcome.status, outcome.unstorable, outcome.movements_new) == (
        WalletSyncStatus.SUCCEEDED,
        3,
        2,
    )
    assert (outcome.reconciled, outcome.waiting) == (False, False)
    (row,) = _rows(
        dbname,
        "SELECT cursor_after, difference, document_id IS NOT NULL FROM wallet_sync"
        " WHERE address_id = %s",
        a,
    )
    assert row == ("5", {"native": "4", MINT_X: str(10**30)}, True)
    groups = _rows(
        dbname,
        "SELECT g.tx_hash, g.status, g.question, e.payload->>'reason',"
        " (SELECT count(*) FROM asset_movement m WHERE m.group_id = g.id)"
        " FROM movement_group g JOIN event e ON e.entity_id = g.id"
        " AND e.type = 'movement_group.proposed' WHERE g.account_id = %s",
        account,
    )
    unstorable = {g[0] for g in groups if g[3] == CHAIN_UNSTORABLE}
    assert unstorable == {t.hash for t in world.txs[SOL_A][1:4]}
    assert {g[1:3] + (g[4],) for g in groups if g[0] in unstorable} == {("proposed", None, 0)}

    again = sync(ws, a)  # from the cursor: nothing new, no second card
    assert (again.movements_new, again.unstorable, again.reconciled) == (0, 0, False)
    count = _rows(dbname, "SELECT count(*) FROM movement_group WHERE account_id = %s", account)
    assert count[0][0] == len(groups)


def test_a_quantity_of_more_than_28_digits_is_stored_exactly(dbname, world, wallet, sync):
    """numeric(38,18) holds 38 digits; Decimal arithmetic rounds to 28, so the conversion
    builds the quantity from its digits both ways and the balance reconciles to the unit."""
    ws, account, a, _ = wallet
    units = 10**37 + 1  # 10,000,000,000,000,000,000.000000000000000001 at 18 decimals
    world.txs[SOL_A] = [_tx(1, tok=[{"mint": MINT_X, "units": units, "decimals": 18}])]
    world.tokens[SOL_A] = {MINT_X: units}
    outcome = sync(ws, a)
    assert (outcome.movements_new, outcome.reconciled, outcome.unstorable) == (1, True, 0)
    (quantity,) = _rows(
        dbname, "SELECT quantity::text FROM asset_movement WHERE address_id = %s", a
    )
    assert quantity[0] == "10000000000000000000.000000000000000001"


def test_only_the_assets_a_question_names_wait(dbname, world, wallet, sync):
    """Finding 5: „wartet auf Antwort“ is narrow: a legless question on A never hides B's
    mismatch, and on one address only the assets of a question's legs wait; a difference in
    any other asset makes the run a mismatch with the whole difference stored."""
    ws, account, a, b = wallet
    world.txs[SOL_A] = [_tx(1, **{"in": 300}), _tx(3, question="Ist die Ausgabe 2 eigen?")]
    world.balance[SOL_A] = 300
    world.txs[SOL_B] = [_tx(2, **{"in": 700})]
    world.balance[SOL_B] = 123
    on_a, on_b = sync(ws, a), sync(ws, b)
    assert (on_a.reconciled, on_a.waiting) == (True, False)
    assert (on_b.reconciled, on_b.waiting) == (False, False)
    (row,) = _rows(
        dbname, "SELECT reconciled, difference FROM wallet_sync WHERE address_id = %s", b
    )
    assert row == (False, {"native": str(123 - 700)})

    # B: a question beside a token leg; only the token's difference waits
    world.txs[SOL_B].append(
        _tx(4, ask="Ist das ein Airdrop?", tok=[{"mint": MINT_X, "units": 5, "decimals": 6}])
    )
    world.balance[SOL_B] = 700
    world.tokens[SOL_B] = {MINT_X: 9}
    waiting = sync(ws, b)
    assert (waiting.reconciled, waiting.waiting) == (None, True)
    world.txs[SOL_B].append(_tx(5, **{"in": 1}))  # and a native difference beside it
    mixed = sync(ws, b)
    assert (mixed.reconciled, mixed.waiting) == (False, False)
    (row,) = _rows(
        dbname,
        "SELECT reconciled, difference FROM wallet_sync WHERE address_id = %s"
        " ORDER BY started_at DESC LIMIT 1",
        b,
    )
    assert row == (False, {"native": "-1", MINT_X: "4"})


def test_account_scope_the_last_address_failing_leaves_the_earlier_rows_open(
    dbname, world, wallet, app_engine, blob_store
):
    """Finding 6 (pinned, no change): when the account's last address of the run fails, the
    earlier addresses' rows keep reconciled NULL and the failed row is what the account
    shows; WalletSyncRun's verdict is the newest non-NULL row of the account on the chain."""
    ws, account, a, b = wallet
    world.scope = "account"
    world.txs[SOL_A] = [_tx(1, **{"in": 300})]
    world.balance[SOL_A], world.balance[SOL_B] = 999, 0
    world.error[SOL_B] = SourceError("HTTPError", 500)
    run = _run(app_engine, blob_store, ws, account)
    assert [(o.address_id, o.status, o.reconciled) for o in run] == [
        (a, WalletSyncStatus.SUCCEEDED, None),
        (b, WalletSyncStatus.FAILED, None),
    ]
    rows = _rows(
        dbname,
        "SELECT address_id, status, reconciled, difference FROM wallet_sync"
        " WHERE account_id = %s ORDER BY started_at, created_at",
        account,
    )
    assert rows == [(a, "succeeded", None, None), (b, "failed", None, None)]


def test_a_leg_joining_a_confirmed_group_is_routed_on_its_own(dbname, world, wallet, sync):
    """Finding 7: A (the fee payer) writes a fee-only group, confirmed by code; B's leg of the
    same transaction joins that group but is routed on its own: proposed on B's first sync,
    confirmed only when D186 (1) would confirm it."""
    ws, account, a, b = wallet
    shared = _tx(1, fee=5000)
    shared_b = ChainTx(
        hash=shared.hash,
        block=shared.block,
        occurred_at=shared.occurred_at,
        status="ok",
        raw={"n": 1, "in": 700},
    )
    world.txs[SOL_A], world.txs[SOL_B] = [shared], [shared_b]
    world.balance[SOL_A], world.balance[SOL_B] = 0, 700  # A's fee is a mismatch here
    sync(ws, a)
    sync(ws, b)
    rows = _rows(
        dbname,
        "SELECT m.address_id, m.status, g.status FROM asset_movement m"
        " JOIN movement_group g ON g.id = m.group_id WHERE m.account_id = %s",
        account,
    )
    assert sorted(rows) == sorted([(a, "confirmed", "confirmed"), (b, "proposed", "confirmed")])

    # after B's first sync, a linked transfer leg joining a confirmed group is confirmed
    later = _tx(2, fee=5000)
    later_b = ChainTx(
        hash=later.hash,
        block=later.block,
        occurred_at=later.occurred_at,
        status="ok",
        raw={"n": 2, "in": 300, "linked": True},
    )
    world.txs[SOL_A].append(later)
    world.txs[SOL_B].append(later_b)
    world.balance[SOL_B] = 1000
    sync(ws, a)
    sync(ws, b)
    (status,) = _rows(
        dbname,
        "SELECT m.status FROM asset_movement m WHERE m.address_id = %s AND m.tx_hash = %s",
        b,
        later.hash,
    )
    assert status == ("confirmed",)
