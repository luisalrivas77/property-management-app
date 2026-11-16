"""Phase 7 step 2: a Solana address synced end to end through the REAL source and classifier
(services/chain/solana_rpc.make_source, rules/chain/solana.classify_tx) over a fake chain of
the recorded records of tests/fixtures/phase7/solana_tx.yaml (docs/phases/phase-7.md D182,
D183, D186, D192).

Only the transport is fake (tests/solana_chain_fake.py). The expected movements are the
fixture's, never the code's output; the balances the fake chain answers are the sums of those
expectations (the main account's lamports plus the rent held in the token account), so a run
reconciles exactly when every expected movement was written. Every key, signature and amount
is invented."""

import itertools
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import fixture_models_phase7 as phase7
import pytest
from db.db_support import _admin, _insert
from db.test_phase7_migration import _address, _hmac, _wallet
from pydantic import SecretStr
from solana_chain_fake import FakeChain, signature, token_account

from hausbuch.rules.chain import solana
from hausbuch.rules.chain.classify import ClassifyContext, register_family
from hausbuch.rules.errors import WALLET_SOURCE_REFUSED
from hausbuch.schemas.chain import (
    Chain,
    ChainFamily,
    ChainSourceKind,
    DecoderRow,
    WalletSyncStatus,
)
from hausbuch.schemas.enums import ActorType
from hausbuch.services.chain import solana_rpc
from hausbuch.services.chain.base import RequestBudget, register_source
from hausbuch.services.chain.solana_rpc import SolanaRpc
from hausbuch.services.chain.sync import sync_address
from hausbuch.services.context import Actor
from hausbuch.settings import Settings
from hausbuch.storage import blob_key

pytestmark = pytest.mark.db

A = "BU8oH89B6z89SV3HfU1FBa5p8aE43HhZz3F6BQBtjsJP"
F = "ANgPywB9VnrmKJ9oXqByisxMxB564F4CoDY4HWZd4Y5J"  # the fixture's counterparty
A_USDC = "Dd2nAL9RJk7UUMMSAWszC9oJTi3jGoPGpjStUZ3ZH8ZU"  # A's USDC token account
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
RENT = 2_039_280  # the token account's lamports in the records
DECIMALS = {None: 9, USDC: 6}
CASES = {c.id: c for c in phase7.load("solana_tx").cases}
AMM = next(c.row for c in phase7.load("decoders").rows if c.id == "sol_swap")
SECRET = "k3yInv3nt3dF0rTh1sT3st"
ACTOR = Actor(ActorType.SYSTEM, "sync_wallets")
SETTINGS = Settings(
    database_url="postgresql+psycopg://hausbuch_app@localhost/unused",
    iban_hmac_key=bytes(range(32)),
    session_key=bytes(range(1, 33)),
    rp_id="testserver",
    origin="https://testserver",
    blob_store="local",
    blob_root="/tmp/unused",
    solana_rpc_url=SecretStr(f"https://rpc.example.invalid/v1/{SECRET}"),
)
T0 = datetime(2026, 11, 16, 6, 0, tzinfo=UTC)


def _record(case_id: str) -> dict:
    return deepcopy(CASES[case_id].tx)


def _signed(case_id: str) -> set[tuple[str, str, Decimal]]:
    """The fixture's movements of A in the record: (signature, kind, signed quantity)."""
    sig = signature(_record(case_id))
    out = set()
    for m in CASES[case_id].expect.movements:
        units = m.units if m.role == "in" else -m.units
        out.add((sig, m.kind.value, Decimal(units).scaleb(-DECIMALS[m.mint]).normalize()))
    return out


def _sums(*case_ids: str) -> tuple[int, int]:
    """Σ of the fixture's SOL and USDC movements (base units)."""
    sol = usdc = 0
    for case_id in case_ids:
        for m in CASES[case_id].expect.movements:
            units = m.units if m.role == "in" else -m.units
            if m.mint is None:
                sol += units
            else:
                usdc += units
    return sol, usdc


def _unreadable() -> dict:
    """`spl_out` without its token balance rows: a Token program call no reader can place
    (solana.solana_deltas raises wallet.record_unreadable), at a later slot."""
    record = _record("spl_out")
    for key in ("preTokenBalances", "postTokenBalances"):
        record["meta"].pop(key)
    sig = signature(record)
    record["transaction"]["signatures"][0] = sig[:-1] + ("2" if sig[-1] != "2" else "3")
    record["slot"] = 300_001_250
    return record


@pytest.fixture
def chain():
    fake = FakeChain()

    def factory(row, settings, budget):
        return solana_rpc.make_source(row, settings, budget, post=fake, sleep=lambda _: None)

    register_source(ChainSourceKind.SOLANA_RPC, factory)
    register_family(ChainFamily.SOLANA, None)  # the module's classify_tx
    yield fake
    register_source(ChainSourceKind.SOLANA_RPC, None)


@pytest.fixture
def wallet(dbname, fresh_workspaces):
    ws = fresh_workspaces[0]
    with _admin(dbname) as conn:
        account = _wallet(conn, ws)
        a = _address(
            conn,
            ws,
            account,
            chain="solana",
            address=A,
            address_masked="BU8o…tsJP",
            address_hmac=_hmac("a"),
        )
    return ws, account, a


@pytest.fixture
def sync(app_engine, blob_store):
    ticks = itertools.count()

    def _sync(ws, address_id, **kw):
        return sync_address(
            app_engine,
            ws,
            address_id,
            settings=SETTINGS,
            store=blob_store,
            actor=ACTOR,
            clock=lambda: T0 + timedelta(seconds=next(ticks)),
            **kw,
        )

    return _sync


def _rows(dbname, query, *params):
    with _admin(dbname) as conn:
        return conn.execute(query, params).fetchall()


def _movements(dbname, account, address=None) -> set[tuple[str, str, Decimal]]:
    rows = _rows(
        dbname,
        "SELECT tx_hash, kind, quantity, address_id FROM asset_movement"
        " WHERE account_id = %s AND superseded_by IS NULL",
        account,
    )
    return {(r[0], r[1], r[2].normalize()) for r in rows if address is None or r[3] == address}


def _groups(dbname, account) -> dict[str, tuple[str, str, bool]]:
    rows = _rows(
        dbname,
        "SELECT tx_hash, kind, status, question IS NOT NULL FROM movement_group"
        " WHERE account_id = %s AND superseded_by IS NULL",
        account,
    )
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def _set_balance(chain: FakeChain, sol: int, usdc: int) -> None:
    """A's SOL is its main account plus the token account's rent (D186 (2))."""
    chain.lamports[A] = sol - RENT
    chain.tokens[A] = [token_account(A, A_USDC, USDC, usdc, RENT)]


FIRST = ["native_receive", "native_send_with_fee", "spl_in", "spl_out"]
LATER = ["swap_listed_amm", "failed_transaction"]


def test_three_runs_from_a_first_sync_to_the_tip(dbname, chain, wallet, sync, blob_store):
    ws, account, a = wallet
    with _admin(dbname) as conn:  # the workspace's own row for the invented AMM (D191)
        _insert(
            conn,
            "chain_decoder",
            {"workspace_id": ws, "chain": "solana", "program": AMM.program, "kind": "swap"},
        )
    for case_id in FIRST:
        chain.add(_record(case_id))
    _set_balance(chain, *_sums(*FIRST))

    first = sync(ws, a)
    assert (first.status, first.reconciled, first.deferred) == (
        WalletSyncStatus.SUCCEEDED,
        True,
        False,
    )
    want = set().union(*(_signed(i) for i in FIRST))
    assert _movements(dbname, account) == want
    assert first.movements_new == len(want)
    # spl_in names A's token account alone: it came by listing the token account
    assert A_USDC in {c[0] for c in chain.calls("getSignaturesForAddress")}
    # a first sync: every group waits for a person (D186 (1))
    assert {g[1] for g in _groups(dbname, account).values()} == {"proposed"}
    spl_out = _record("spl_out")
    (row,) = _rows(
        dbname,
        "SELECT cursor_before, cursor_after, source_host, reconciled FROM wallet_sync"
        " WHERE address_id = %s",
        a,
    )
    assert row == (None, f"{spl_out['slot']}:{signature(spl_out)}", "rpc.example.invalid", True)

    # the second run: a listed swap, a failed transaction and a record no reader can place
    unreadable = _unreadable()
    for record in [*(_record(i) for i in LATER), unreadable]:
        chain.add(record)
    sol, usdc = _sums(*FIRST, *LATER)
    _set_balance(chain, sol - unreadable["meta"]["fee"], usdc)
    second = sync(ws, a)
    # the card has no leg, not even its fee (solana._unreadable_group): the chain's SOL is
    # that fee short of the movements, and the SOL waits for the answer, never a plug
    assert (second.status, second.reconciled, second.waiting) == (
        WalletSyncStatus.SUCCEEDED,
        None,
        True,
    )
    (difference,) = _rows(
        dbname, "SELECT difference FROM wallet_sync WHERE id = %s", second.sync_id
    )[0]
    assert difference is None
    want |= set().union(*(_signed(i) for i in LATER))
    assert _movements(dbname, account) == want
    groups = _groups(dbname, account)
    assert groups[signature(_record("failed_transaction"))] == ("failed", "confirmed", False)
    assert groups[signature(_record("swap_listed_amm"))] == ("swap", "confirmed", False)
    # one record never blocks its address: it is a card with its question, the rest booked
    assert groups[signature(unreadable)] == ("unknown", "proposed", True)
    (question,) = _rows(
        dbname, "SELECT question FROM movement_group WHERE tx_hash = %s", signature(unreadable)
    )
    assert question[0] == solana.QUESTION_UNREADABLE

    # the third run: nothing new, the cursor stays, nothing stored
    third = sync(ws, a)
    assert (third.movements_new, third.reconciled, third.waiting) == (0, None, True)
    rows = _rows(
        dbname,
        "SELECT cursor_before, cursor_after, document_id FROM wallet_sync"
        " WHERE address_id = %s ORDER BY started_at",
        a,
    )
    assert rows[2][0] == rows[2][1] == rows[1][1] and rows[2][2] is None
    assert rows[1][1] == f"{unreadable['slot']}:{signature(unreadable)}"

    # D192: the stored originals replayed without any source give the same movements
    replayed = _replay(dbname, ws, account, a, blob_store)
    assert replayed == want
    for text in _rows(dbname, "SELECT cursor_after, source_host FROM wallet_sync"):
        assert SECRET not in json.dumps(text)


def _replay(dbname, ws, account, a, blob_store) -> set[tuple[str, str, Decimal]]:
    decoders = tuple(  # as the sync reads the workspace's rows
        DecoderRow(chain=Chain.SOLANA, program=r[0], method=r[1], kind=r[2])
        for r in _rows(
            dbname, "SELECT program, method, kind FROM chain_decoder WHERE workspace_id = %s", ws
        )
    )
    ctx = ClassifyContext(
        chain=Chain.SOLANA, address=A, own_addresses=frozenset({A}), decoders=decoders
    )
    found: set[tuple[str, str, Decimal]] = set()
    shas = _rows(
        dbname,
        "SELECT d.file_sha256 FROM wallet_sync s JOIN document d ON d.id = s.document_id"
        " WHERE s.address_id = %s",
        a,
    )
    assert len(shas) == 2  # one original per run that wrote something
    for (sha,) in shas:
        data = blob_store.get(blob_key(ws, bytes(sha).hex()))
        for line in data.decode().splitlines():
            raw = json.loads(line)
            tx = SolanaRpc.chain_tx(raw, {A}, solana.recorded_token_accounts(raw), strict=False)
            group = solana.classify_tx(tx, ctx)
            for leg in group.legs:
                units = leg.units if leg.role == "in" else -leg.units
                quantity = Decimal(units).scaleb(-DECIMALS[leg.asset_key.contract]).normalize()
                found.add((tx.hash, leg.kind.value, quantity))
    return found


def test_a_payment_between_two_own_addresses_is_one_leg_on_each(dbname, chain, wallet, sync):
    """`native_send_with_fee`: A pays F, both the wallet's. A's run writes its out and the fee,
    F's run its in, each once; neither reads the other's leg as its own. A's balance before
    the recorded history is not in it, so only F's run is asked to reconcile."""
    ws, account, a = wallet
    with _admin(dbname) as conn:
        f = _address(
            conn,
            ws,
            account,
            chain="solana",
            address=F,
            address_masked="ANgP…4Y5J",
            address_hmac=_hmac("f"),
        )
    record = _record("native_send_with_fee")
    chain.add(record)
    (sent,) = [m for m in CASES["native_send_with_fee"].expect.movements if m.role == "out"]
    chain.lamports[F] = sent.units

    on_a, on_f = sync(ws, a), sync(ws, f)
    assert on_a.status is WalletSyncStatus.SUCCEEDED and on_f.reconciled is True
    assert _movements(dbname, account, a) == _signed("native_send_with_fee")
    one = Decimal(sent.units).scaleb(-9).normalize()
    (on_f_row,) = _movements(dbname, account, f)
    assert on_f_row[0] == signature(record) and on_f_row[2] == one
    fees = _rows(
        dbname,
        "SELECT count(*) FROM asset_movement WHERE account_id = %s AND kind = 'fee'",
        account,
    )
    assert fees[0][0] == 1
    assert (sync(ws, a).movements_new, sync(ws, f).movements_new) == (0, 0)


def test_a_refused_provider_leaves_the_history_untouched(dbname, chain, wallet, sync):
    """chains.yaml `refused_provider`: a 401 fails the run as wallet.source_refused, writes
    nothing, and the next run goes on from the last cursor."""
    ws, account, a = wallet
    chain.add(_record("native_receive"))
    _set_balance(chain, *_sums("native_receive"))
    done = sync(ws, a)
    before = _movements(dbname, account)
    chain.status = 401
    refused = sync(ws, a)
    assert (refused.status, refused.error_code) == (WalletSyncStatus.REFUSED, WALLET_SOURCE_REFUSED)
    assert _movements(dbname, account) == before
    chain.status = None
    again = sync(ws, a)
    assert (again.status, again.movements_new, again.reconciled) == (
        WalletSyncStatus.SUCCEEDED,
        0,
        True,
    )
    rows = _rows(
        dbname,
        "SELECT status, cursor_before, cursor_after FROM wallet_sync WHERE address_id = %s"
        " ORDER BY started_at",
        a,
    )
    assert rows[1][0] == "refused" and rows[2][1] == rows[0][2]
    assert done.status is WalletSyncStatus.SUCCEEDED


def test_a_spent_budget_inside_a_discovery_loses_nothing(dbname, chain, wallet, sync):
    """The token account is closed (getTokenAccountsByOwner no longer names it) and the
    budget ends while listing it after `spl_out` named it: the run is deferred, and the next
    run, from the cursor, still finds the older `spl_in` through it."""
    ws, account, a = wallet
    for case_id in FIRST:
        chain.add(_record(case_id))
    chain.lamports[A] = _sums(*FIRST)[0]
    budgets = {ChainSourceKind.SOLANA_RPC: RequestBudget(7)}
    first = sync(ws, a, budgets=budgets)
    assert first.deferred and first.reconciled is None
    got = set().union(*(_signed(i) for i in ("native_receive", "native_send_with_fee", "spl_out")))
    assert _movements(dbname, account) == got
    rest = sync(ws, a)
    assert not rest.deferred
    assert _movements(dbname, account) == got | _signed("spl_in")
