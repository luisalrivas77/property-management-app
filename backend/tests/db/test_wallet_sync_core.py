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
from db.test_phase7_migration import _address, _wallet

from hausbuch.rules.chain.classify import ChainGroup, ChainLeg, register_family
from hausbuch.rules.errors import WALLET_KEY_MISSING, WALLET_SOURCE_THROTTLED
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
        return [AssetBalance(asset_key=SOL, units=self.world.balance[address], at_block=2000)]


def fake_classifier(tx, ctx):
    """raw: in / out / fee units for the synced address, `on` another own address for the
    legs, `question` for a legless card."""
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
    if tx.raw.get("boom"):
        raise RuntimeError(f"classifier failed on {ctx.address}")  # must never be logged
    return ChainGroup(tx.hash, tx.occurred_at, tx.block, GroupKind.TRANSFER, legs)


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
