"""Phase 7 step 2: the Solana ChainSource the sync reads through (services/chain/solana_rpc
SolanaSource and make_source; docs/phases/phase-7.md D181 (d), D182, D186 (2), D192), over a
fake chain of the recorded records of tests/fixtures/phase7/solana_tx.yaml.

Replayed here: chains.yaml `solana_two_urls`, `solana_replay_without_source` and
`refused_provider` (its untouched history in tests/db/test_wallet_sync_solana.py), and
wallet_reconcile.yaml `solana_rent_in_token_accounts`. Every key, signature and amount is
invented."""

import dataclasses
import json
from copy import deepcopy
from types import SimpleNamespace

import fixture_models_phase7 as phase7
import pytest
from pydantic import SecretStr
from solana_chain_fake import FakeChain, signature, token_account

from hausbuch.rules import errors as E
from hausbuch.rules.chain import solana
from hausbuch.rules.chain.classify import ClassifyContext
from hausbuch.rules.chain.reconcile import reconcile, sum_balance_parts, sum_movements
from hausbuch.rules.chain.registry import registry
from hausbuch.schemas.chain import AssetKey, Chain, ChainSourceKind, DecoderRow, GroupKind
from hausbuch.services.chain.base import ChainSourceError, RequestBudget
from hausbuch.services.chain.solana_rpc import SolanaRpc, SourceCursor, make_source
from hausbuch.settings import Settings

SECRET = "k3yInv3nt3dF0rTh1sT3st"
A = "BU8oH89B6z89SV3HfU1FBa5p8aE43HhZz3F6BQBtjsJP"
A_USDC = "Dd2nAL9RJk7UUMMSAWszC9oJTi3jGoPGpjStUZ3ZH8ZU"  # A's USDC token account
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOL = AssetKey(chain=Chain.SOLANA)
CASES = {c.id: c for c in phase7.load("solana_tx").cases}
PROVIDERS = {p.id: p for p in phase7.load("chains").providers}
RECONCILE = {c.id: c for c in phase7.load("wallet_reconcile").cases}
AMM_ROW = next(c.row for c in phase7.load("decoders").rows if c.id == "sol_swap")
SETTINGS = Settings(
    database_url="postgresql+psycopg://hausbuch_app@localhost/unused",
    iban_hmac_key=bytes(range(32)),
    session_key=bytes(range(1, 33)),
    rp_id="testserver",
    origin="https://testserver",
    blob_store="local",
    blob_root="/tmp/unused",
)


def _record(case_id: str) -> dict:
    return deepcopy(CASES[case_id].tx)


def _source(chain: FakeChain, url="https://rpc.example.invalid", budget=None, **settings):
    configured = dataclasses.replace(SETTINGS, solana_rpc_url=SecretStr(url), **settings)
    return make_source(
        registry().chain(Chain.SOLANA),
        configured,
        budget or RequestBudget(10_000),
        post=chain,
        sleep=lambda _: None,
    )


def _read_all(source, address, cursor=None):
    """The sync's loop (services/chain/sync._read): pages until the cursor stays."""
    items = []
    while True:
        page = source.transactions(address, cursor)
        items.extend(page.items)
        if page.next_cursor is None or page.next_cursor == cursor:
            return items, cursor
        cursor = page.next_cursor


def _ctx(**kw) -> ClassifyContext:
    return ClassifyContext(chain=Chain.SOLANA, address=A, own_addresses=frozenset({A}), **kw)


def _replay(line: str):
    """A stored original's line through the classifier's input, with no source configured."""
    raw = json.loads(line)
    return SolanaRpc.chain_tx(raw, {A}, solana.recorded_token_accounts(raw), strict=False)


def _stored(tx) -> str:
    """As services/chain/sync._store_original writes a line of the original."""
    return json.dumps(tx.raw, sort_keys=True, separators=(",", ":"))


def _legs(group):
    return [
        (leg.kind, leg.role, leg.units, leg.asset_key.contract, leg.index) for leg in group.legs
    ]


def _want(case_id):
    return [(m.kind, m.role, m.units, m.mint, m.index) for m in CASES[case_id].expect.movements]


# --- D192: two base URLs, a replay without a source ----------------------------------------


def test_two_base_urls_give_the_same_page():
    case = PROVIDERS["solana_two_urls"]
    assert case.pending is None and case.expect.same_as_first
    _, case_id = case.records_from.split(".", 1)
    pages = []
    for url in case.urls:
        chain = FakeChain([_record(case_id)])
        source = _source(chain, url=url)
        page = source.transactions(A, None)
        assert set(chain.urls) == {url}
        assert source.host == url.removeprefix("https://")
        pages.append(page)
    assert all(page == pages[0] for page in pages)
    (tx,) = pages[0].items
    assert tx.hash == signature(_record(case_id))
    assert pages[0].next_cursor == f"{tx.block}:{tx.hash}"


def test_a_replay_of_the_stored_original_needs_no_source():
    case = PROVIDERS["solana_replay_without_source"]
    assert case.pending is None and case.urls == [] and case.expect.same_as_first
    _, case_id = case.records_from.split(".", 1)
    chain = FakeChain([_record(case_id)])
    (tx,) = _source(chain).transactions(A, None).items
    asked = len(chain.requests)
    ctx = _ctx(decoders=(AMM_ROW,))
    replayed = _replay(_stored(tx))
    assert len(chain.requests) == asked  # the replay asks nobody
    assert replayed == tx
    group = solana.classify_tx(replayed, ctx)
    assert group == solana.classify_tx(tx, ctx)
    assert _legs(group) == _want(case_id)
    assert group.decoder_listed


def test_a_token_row_without_an_owner_replays_from_the_stored_accounts():
    """`spl_in` with the owner dropped from the rows: the run knew A's token account from
    getTokenAccountsByOwner, and the stored original carries it, so a replay without the
    provider reads the same leg (solana.SYNC_KEY)."""
    record = _record("spl_in")
    for key in ("preTokenBalances", "postTokenBalances"):
        for row in record["meta"][key]:
            row.pop("owner")
    chain = FakeChain([record])
    chain.tokens[A] = [token_account(A, A_USDC, USDC, 50_000_000, 2_039_280)]
    (tx,) = _source(chain).transactions(A, None).items
    assert tx.raw[solana.SYNC_KEY] == {"own_token_accounts": [A_USDC]}
    group = solana.classify_tx(_replay(_stored(tx)), _ctx())
    assert _legs(group) == _want("spl_in")
    # a record whose rows name their owner is stored exactly as the provider sent it
    plain = FakeChain([_record("spl_in")])
    plain.tokens[A] = chain.tokens[A]
    (tx,) = _source(plain).transactions(A, None).items
    assert tx.raw == _record("spl_in")


# --- the listing, the page and the cursor ---------------------------------------------------

ORDER = ["native_receive", "native_send_with_fee", "spl_in", "spl_out", "failed_transaction"]


def _chain(*case_ids, open_account=True) -> FakeChain:
    chain = FakeChain([_record(i) for i in case_ids])
    if open_account:
        chain.tokens[A] = [token_account(A, A_USDC, USDC, 30_000_000, 2_039_280)]
    return chain


def test_the_address_and_its_token_accounts_oldest_first_by_pages():
    """`spl_in` names A's token account alone: it is found by listing the token account."""
    chain = _chain(*ORDER)
    source = _source(chain)
    source.page_size = 2
    pages, cursor = [], None
    while True:
        page = source.transactions(A, cursor)
        pages.append([tx.hash for tx in page.items])
        if page.next_cursor == cursor:
            break
        cursor = page.next_cursor
    sigs = [signature(_record(i)) for i in ORDER]
    assert pages == [sigs[:2], sigs[2:4], sigs[4:], []]
    last = _record("failed_transaction")
    assert cursor == f"{last['slot']}:{signature(last)}"
    assert {c[0] for c in chain.calls("getSignaturesForAddress")} == {A, A_USDC}
    assert len(cursor) <= 200

    # the next run lists from the cursor's slot: its transaction is read again (the movement
    # hashes keep it once) and the cursor comes back unchanged, which ends the sync's loop
    again = _chain(*ORDER)
    items, end = _read_all(_source(again), A, cursor)
    assert [tx.hash for tx in items] == [signature(last)] and end == cursor
    assert len(again.calls("getTransaction")) == 1


def test_a_page_never_splits_a_slot():
    first, second = _record("native_receive"), _record("native_send_with_fee")
    second["slot"] = first["slot"]
    chain = FakeChain([first, second, _record("spl_out")])
    source = _source(chain)
    source.page_size = 1
    page = source.transactions(A, None)
    assert {tx.hash for tx in page.items} == {signature(first), signature(second)}


def test_a_token_account_found_late_moves_the_cursor_back():
    """The token account is closed (getTokenAccountsByOwner no longer names it): `spl_out`,
    listed under A, names it as A's; listing it then finds the older `spl_in`, which comes
    in the same run although its slot is below the highest one served."""
    chain = _chain(
        "native_receive", "native_send_with_fee", "spl_in", "spl_out", open_account=False
    )
    items, cursor = _read_all(_source(chain), A)
    by_hash = [tx.hash for tx in items]
    assert sorted(by_hash) == sorted(signature(_record(i)) for i in ORDER[:4])
    assert by_hash[-1] == signature(_record("spl_in"))
    spl_out = _record("spl_out")
    assert cursor == f"{spl_out['slot']}:{signature(spl_out)}"


def test_a_spent_budget_inside_a_discovery_is_finished_by_the_next_run():
    """The budget ends while the token account `spl_out` revealed is being listed: the page
    ends there, the cursor stands on `spl_out`'s slot and owes the account's listing from
    the start, so the next run, listing only from that slot, still finds the older `spl_in`."""
    ids = ["native_receive", "native_send_with_fee", "spl_in", "spl_out"]
    budget = RequestBudget(7)  # 2 listings of A, 2 token programs, 3 transactions
    chain = _chain(*ids, open_account=False)
    page = _source(chain, budget=budget).transactions(A, None)
    assert budget.exhausted
    assert [tx.hash for tx in page.items] == [
        signature(_record(i)) for i in ("native_receive", "native_send_with_fee", "spl_out")
    ]
    slot = _record("spl_out")["slot"]
    assert page.next_cursor == f"{slot}:;;{slot}"
    assert SourceCursor.parse(page.next_cursor) == SourceCursor(slot, "", None, slot)

    items, cursor = _read_all(_source(_chain(*ids, open_account=False)), A, page.next_cursor)
    assert [tx.hash for tx in items] == [signature(_record(i)) for i in ("spl_out", "spl_in")]
    assert cursor == f"{slot}:{signature(_record('spl_out'))}"


def test_a_spent_budget_ends_the_page_without_an_error():
    budget = RequestBudget(7)  # 6 listings (A and its token account), one transaction
    page = _source(_chain(*ORDER), budget=budget).transactions(A, None)
    assert budget.exhausted and len(page.items) == 1
    first = _record("native_receive")
    assert page.next_cursor == f"{first['slot']}:{signature(first)}"
    # nothing at all: the cursor it was given
    spent = RequestBudget(0)
    assert _source(_chain(*ORDER), budget=spent).transactions(A, "12:x").next_cursor == "12:x"
    assert _source(_chain(*ORDER), budget=RequestBudget(0)).balances(A) == []


def test_a_cursor_it_did_not_write_starts_over():
    assert SourceCursor.parse(None) == SourceCursor()
    assert SourceCursor.parse("5vBy…") == SourceCursor()
    assert SourceCursor.parse("12:abc;x;7") == SourceCursor(12, "abc")
    assert SourceCursor(12, "abc", 3, 9).text() == "12:abc;3;9"


# --- the balance (D186 (2)) -----------------------------------------------------------------


def test_rent_in_token_accounts_is_the_address_sol():
    """wallet_reconcile.yaml `solana_rent_in_token_accounts`: the main account and the rent of
    two token accounts are the SOL compared; each mint sums over its token accounts."""
    case = RECONCILE["solana_rent_in_token_accounts"]
    assert case.pending is None
    (main,) = case.balance["SOL"][:1]
    chain = FakeChain()
    chain.lamports[A] = main
    mints = {"USDC": USDC}
    chain.tokens[A] = [
        token_account(A, f"TokenAccount{n}Invented1111111111111111111", mints["USDC"], units, rent)
        for n, (units, rent) in enumerate(
            zip(case.balance["USDC"], case.balance["SOL"][1:], strict=True)
        )
    ]
    balances = _source(chain).balances(A)
    key = {"SOL": SOL, "USDC": AssetKey(chain=Chain.SOLANA, contract=USDC)}
    held = sum_balance_parts((b.asset_key, b.units) for b in balances)
    moved = sum_movements((key[m.asset], m.units) for m in case.movements)
    assert held == sum_balance_parts(
        (key[a], u) for a, parts in case.balance.items() for u in parts
    )
    assert (not reconcile(moved, held)) is case.expect.reconciled


# --- configuration and errors ---------------------------------------------------------------


def test_an_empty_url_is_a_missing_key():
    """Settings refuses an empty HAUSBUCH_SOLANA_RPC_URL at start-up and an unset one reads
    as the public endpoint, so this is the factory's own guard (base.py's contract)."""
    configured = SimpleNamespace(solana_rpc_url=SecretStr(""), solana_rpc_rps=4)
    with pytest.raises(ChainSourceError) as caught:
        make_source(registry().chain(Chain.SOLANA), configured, RequestBudget(10))
    assert caught.value.code == E.WALLET_KEY_MISSING


def test_the_source_names_its_host_only():
    url = f"https://rpc.example.invalid/v1/{SECRET}?api-key={SECRET}"
    source = _source(FakeChain(), url=url, solana_rpc_rps=2)
    assert source.kind is ChainSourceKind.SOLANA_RPC and source.reconcile_scope == "address"
    assert source.host == "rpc.example.invalid"
    assert SECRET not in repr(source) and source.rpc.rps == 2.0


def test_a_refused_provider_is_the_sync_code_and_never_the_url():
    case = PROVIDERS["refused_provider"]
    assert case.pending is None
    _, case_id = case.records_from.split(".", 1)
    chain = FakeChain([_record(case_id)])
    chain.status = case.status
    url = f"{case.urls[0]}/{SECRET}"
    with pytest.raises(ChainSourceError) as caught:
        _source(chain, url=url).transactions(A, None)
    assert (caught.value.code, caught.value.status) == (case.expect.error, case.status)
    for text in (str(caught.value), repr(caught.value)):
        assert SECRET not in text


@pytest.mark.parametrize(
    "fail_on", ["getSignaturesForAddress", "getTransaction", "getTokenAccountsByOwner"]
)
def test_a_provider_error_is_source_failed(fail_on):
    chain = _chain(*ORDER)
    chain.fail_on = fail_on
    with pytest.raises(ChainSourceError) as caught:
        _source(chain).transactions(A, None)
    assert caught.value.code == E.WALLET_SOURCE_FAILED and caught.value.status is None
    assert caught.value.__cause__ is None


def test_a_transaction_the_provider_no_longer_serves_is_source_failed():
    """The listing names it, getTransaction answers null (Q-O93)."""
    chain = _chain(*ORDER)
    chain.missing.add(signature(_record("spl_out")))
    with pytest.raises(ChainSourceError) as caught:
        _read_all(_source(chain), A)
    assert caught.value.code == E.WALLET_SOURCE_FAILED and caught.value.status is None


def test_a_throttled_provider_is_throttled():
    chain = _chain(*ORDER)
    chain.status = 429
    with pytest.raises(ChainSourceError) as caught:
        _source(chain).balances(A)
    assert caught.value.code == E.WALLET_SOURCE_THROTTLED


# --- the sync's classifier over every recorded case ------------------------------------------


def _workspace_row(row) -> DecoderRow:
    """A workspace's decoder row as services/chain/sync._context reads it: no legs, no name."""
    return DecoderRow(chain=Chain.SOLANA, program=row.program, method=row.method, kind=row.kind)


@pytest.mark.parametrize("case", list(CASES.values()), ids=lambda c: c.id)
def test_classify_tx_replays_every_case(case):
    """classify_tx, the sync's entry, over each `solana_tx.yaml` record as the source hands
    it on (chain_tx, not strict) and with a decoder row shaped as the sync reads it: the same
    kind and legs as the fixture. A provider label is not read (D192), so it changes
    nothing."""
    if case.pending:
        pytest.skip(case.pending)
    tx = SolanaRpc.chain_tx(case.tx, {case.own}, strict=False)
    rows = (_workspace_row(AMM_ROW),) if case.decoder_listed else ()
    ctx = ClassifyContext(
        chain=Chain.SOLANA, address=case.own, own_addresses=frozenset({case.own}), decoders=rows
    )
    group = solana.classify_tx(tx, ctx)
    assert group.kind is case.expect.group
    assert _legs(group) == [
        (m.kind, m.role, m.units, m.mint, m.index) for m in case.expect.movements
    ]
    if case.decoder_listed and case.expect.group is GroupKind.SWAP:
        assert group.decoder_listed  # the workspace row reaches routing (route_group)


def test_a_record_without_meta_or_block_time_is_a_card_and_never_blocks_the_address():
    """An old slot's record may come back with `meta: null` and `blockTime: null`. It is the
    unreadable card (no legs, its question), placed in time by getBlockTime and that time kept
    beside the record, and the address's other transactions are served as usual."""
    old = _record("native_receive")
    old["transaction"]["signatures"] = ["3oLdRec0rdW1th0utMetaInv3nted" + "1" * 59]
    old["slot"] = 300_000_001
    old["meta"] = None
    old["blockTime"] = None
    chain = _chain("native_receive", "spl_out")
    chain.add(old)
    chain.block_times[300_000_001] = 1_767_225_600
    items, _ = _read_all(_source(chain), A)
    assert {signature(_record(i)) for i in ("native_receive", "spl_out")} <= {
        tx.hash for tx in items
    }
    (first,) = [tx for tx in items if tx.hash == signature(old)]
    assert first.raw[solana.SYNC_KEY] == {"block_time": 1_767_225_600}
    assert first.transfers == [] and first.fee is None
    group = solana.classify_tx(_replay(_stored(first)), _ctx())
    assert (group.kind, group.legs, group.question) == (
        GroupKind.UNKNOWN,
        [],
        solana.QUESTION_UNREADABLE,
    )
    assert group.occurred_at.timestamp() == 1_767_225_600


def test_a_slot_without_any_block_time_fails_the_run_as_the_ledger_s_end():
    old = _record("native_receive")
    old["blockTime"] = None
    with pytest.raises(ChainSourceError) as caught:
        _read_all(_source(FakeChain([old])), A)
    assert caught.value.code == E.WALLET_SOURCE_FAILED
