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
from solana_chain_fake import FakeChain, Records, signature, token_account

from hausbuch.rules import errors as E
from hausbuch.rules.chain import solana
from hausbuch.rules.chain.classify import ClassifyContext
from hausbuch.rules.chain.reconcile import reconcile, sum_balance_parts, sum_movements
from hausbuch.rules.chain.registry import registry
from hausbuch.rules.review import route_group
from hausbuch.schemas.allocation import RoutingDecision
from hausbuch.schemas.chain import (
    AssetKey,
    Chain,
    ChainSourceKind,
    DecoderRow,
    GroupKind,
    GroupRoutingInput,
)
from hausbuch.schemas.household import MovementKind
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
    ends there, and the cursor says the closed token accounts are owed from their first, so
    the next run, finding the account again in `spl_out`, lists it whole and serves the older
    `spl_in`."""
    ids = ["native_receive", "native_send_with_fee", "spl_in", "spl_out"]
    budget = RequestBudget(8)  # the height, 2 listings of A, 2 token programs, 3 transactions
    chain = _chain(*ids, open_account=False)
    page = _source(chain, budget=budget).transactions(A, None)
    assert budget.exhausted
    assert [tx.hash for tx in page.items] == [
        signature(_record(i)) for i in ("native_receive", "native_send_with_fee", "spl_out")
    ]
    spl_out = _record("spl_out")
    slot = spl_out["slot"]
    assert page.next_cursor == f"{slot}:{signature(spl_out)};"
    assert SourceCursor.parse(page.next_cursor) == SourceCursor(
        slot, signature(spl_out), None, True
    )

    items, cursor = _read_all(_source(_chain(*ids, open_account=False)), A, page.next_cursor)
    assert [tx.hash for tx in items] == [signature(_record(i)) for i in ("spl_out", "spl_in")]
    assert cursor == f"{slot}:{signature(spl_out)}"


def test_a_spent_budget_ends_the_page_without_an_error():
    budget = RequestBudget(8)  # the height, 6 listings (A and its token account), one transaction
    page = _source(_chain(*ORDER), budget=budget).transactions(A, None)
    assert budget.exhausted and len(page.items) == 1
    # the next run starts at the first transaction still unserved
    following = _record("native_send_with_fee")
    assert page.next_cursor == f"{following['slot']}:"
    # nothing at all: the cursor it was given
    spent = RequestBudget(0)
    assert _source(_chain(*ORDER), budget=spent).transactions(A, "12:x").next_cursor == "12:x"
    assert _source(_chain(*ORDER), budget=RequestBudget(0)).balances(A) == []


def test_a_cursor_it_did_not_write_starts_over():
    assert SourceCursor.parse(None) == SourceCursor()
    assert SourceCursor.parse("5vBy…") == SourceCursor()
    assert SourceCursor.parse("12:abc;x") == SourceCursor()
    assert SourceCursor.parse("12:abc;3;9") == SourceCursor()
    assert SourceCursor.parse("12:abc;") == SourceCursor(12, "abc", None, True)
    assert SourceCursor.parse("12:abc;3") == SourceCursor(12, "abc", 3, True)
    assert SourceCursor.parse("12:abc;12") == SourceCursor(12, "abc")  # nothing owed below
    assert SourceCursor(12, "abc", 3, True).text() == "12:abc;3"
    assert SourceCursor(12, "abc", None, True).text() == "12:abc;"
    assert SourceCursor(12, "abc").text() == "12:abc" and SourceCursor(12, "abc").low == 12


# --- runs the budget cuts (the step 2 review's findings 1, 3 and 4) -------------------------

T1 = "T1c1osedTokenAccountInventedA"  # an own token account closed before any run
T2 = "T2openTokenAccountInventedAaa"  # an own token account still open
T3 = "T3c1osedLateFoundInventedAaa"  # closed, opened by a third party, found late


@dataclasses.dataclass
class _Daily:
    """The sync's runs over one address (services/chain/sync._read and its stored originals):
    each run has its own budget, ends deferred when the budget is spent, reads the balances
    at the tip, and stores what it read when it brought something new (every record here
    makes a movement), which is what the next run is handed as `stored`."""

    chain: FakeChain
    page_size: int | None = None
    cursor: str | None = None
    store: list = dataclasses.field(default_factory=list)
    served: dict = dataclasses.field(default_factory=dict)  # hash -> ChainTx
    runs: int = 0
    tip: bool = False

    def run(self, limit: int) -> bool:
        """One run; whether it read to the tip."""
        budget = RequestBudget(limit)
        source = _source(self.chain, budget=budget)
        if self.page_size:
            source.page_size = self.page_size
        items, cursor = [], self.cursor
        while True:
            page = source.transactions(A, cursor, stored=lambda: list(self.store))
            items.extend(page.items)
            moved = page.next_cursor is not None and page.next_cursor != cursor
            cursor = page.next_cursor if moved else cursor
            if budget.exhausted or not moved:
                break
        if not budget.exhausted:
            source.balances(A)
        if any(tx.hash not in self.served for tx in items):
            self.store.extend(tx.raw for tx in items)
        for tx in items:
            self.served.setdefault(tx.hash, tx)
        self.runs += 1
        self.cursor, self.tip = cursor, not budget.exhausted
        return self.tip

    def to_tip(self, *limits: int, most: int = 400) -> "_Daily":
        """Runs with these budgets, the last one repeated daily, until one reads to the tip."""
        for n in range(most):
            if self.run(limits[min(n, len(limits) - 1)]):
                return self
        raise AssertionError(f"no tip after {most} runs, cursor {self.cursor}")


def _legs_by_hash(daily: _Daily) -> dict:
    return {h: _legs(solana.classify_tx(tx, _ctx())) for h, tx in daily.served.items()}


def _mixed() -> FakeChain:
    """SOL both ways, an own token account closed before the first run, one still open, and
    one a third party opened and that is closed too: credits name the token account alone."""
    r = Records(A)
    slots = (1, 2, 6, 10, 14, 22, 22, 27, 31, 35, 38, 42, 47, 52, 55, 58, 60)
    records = [r.sol_in(s) if s % 2 else r.sol_out(s) for s in slots]
    records += [r.open(3, T1), r.token_in(7, T1), r.token_in(12, T1), r.token_out(15, T1)]
    records += [r.token_in(18, T1), r.token_in(22, T1), r.close(25, T1)]
    records += [r.open(4, T2), r.token_in(9, T2), r.token_out(20, T2)]
    records += [r.token_in(40, T2), r.token_in(59, T2)]
    records += [r.open(28, T3, payer=Records.PAYER), r.token_in(30, T3), r.token_in(33, T3)]
    records += [r.token_out(45, T3), r.token_in(45, T3), r.token_in(46, T3), r.close(50, T3)]
    chain = FakeChain(records)
    chain.tokens[A] = [token_account(A, T2, Records.MINT, 10, Records.RENT)]
    return chain


def _three_hundred_and_six() -> FakeChain:
    """The review's sequence 1 (a): a token account opened, credited without naming A, debited
    and closed early, then 300 SOL credits."""
    r = Records(A)
    records = [r.sol_in(1), r.open(3, T1), r.token_in(7, T1), r.sol_in(9)]
    records += [r.token_out(15, T1), r.close(25, T1)]
    records += [r.sol_in(100 + i) for i in range(300)]
    return FakeChain(records)


# Invented own token accounts for the many-accounts histories (Q-O162), never a real key.
MANY = [f"T{i:02d}c1osedManyInventedAaaaa" for i in range(40)]


def _thirty_closed(count: int = 30) -> FakeChain:
    """Q-O162: `count` own token accounts, each opened, credited without naming A and closed
    early, then SOL credits; once a cut run stored them, one run's start used to list every
    one of them, which a daily budget of 25 never covered."""
    r = Records(A)
    records = [r.sol_in(1)]
    for i, account in enumerate(MANY[:count]):
        records += [r.open(10 + 3 * i, account), r.token_in(11 + 3 * i, account)]
        records += [r.close(12 + 3 * i, account)]
    records += [r.sol_in(200 + j) for j in range(40)]
    return FakeChain(records)


def _one_busy_closed() -> FakeChain:
    """Q-O162: one closed own token account credited 60 times without naming A, more than a
    run at the floor serves, then SOL credits; the cursor's `at` carries how far the sweep
    got inside it, so a cut run does not start that account over."""
    r = Records(A)
    records = [r.sol_in(1), r.open(3, MANY[0])]
    records += [r.token_in(10 + i, MANY[0]) for i in range(60)]
    records += [r.close(80, MANY[0]), r.open(81, MANY[1]), r.close(82, MANY[1])]
    records += [r.sol_in(100 + j) for j in range(30)]
    return FakeChain(records)


def test_thirty_closed_token_accounts_at_a_budget_of_25_reach_the_tip():
    """Q-O162 reproduced: 30 closed own token accounts, a daily budget of 25. The start no
    longer lists every stored token account at once; the cursor carries an index into them
    (never a token account itself), so every run moves on and the history is served whole,
    read as one unlimited run reads it."""
    want = _legs_by_hash(_Daily(_thirty_closed()).to_tip(10**6))
    daily = _Daily(_thirty_closed()).to_tip(25, most=200)
    assert _legs_by_hash(daily) == want
    assert len(daily.served) == len(_thirty_closed().records)


def test_a_cursor_names_no_token_account_and_stays_short():
    """D179: a cursor carries an index into the stored token accounts, never one of them."""
    daily = _Daily(_thirty_closed())
    seen = []
    for _ in range(200):
        tip = daily.run(25)
        seen.append(daily.cursor)
        if tip:
            break
    assert daily.tip
    for cursor in seen:
        assert len(cursor) <= 130
        assert not any(account in cursor for account in MANY)
        assert "c1osed" not in cursor


# The pinned floors: before the sweep (Q-O162) the mixed history needed 12, a start then
# listing every stored token account; the 30 closed accounts froze below about 34.
FLOORS = {"_mixed": 10, "_three_hundred_and_six": 9, "_thirty_closed": 9, "_one_busy_closed": 9}


def test_a_sweep_inside_one_busy_account_moves_on_by_its_at():
    """Q-O162: a run cut inside one stored token account's transactions writes how far it got
    in that account (`at`), and the next run lists it from there; it never starts it over."""
    daily = _Daily(_one_busy_closed())
    ats = []
    for _ in range(200):
        tip = daily.run(9)
        cursor = SourceCursor.parse(daily.cursor)
        if cursor.split and cursor.index == 0 and cursor.at != cursor.closed:
            ats.append(cursor.at)
        if tip:
            break
    assert daily.tip and len(daily.served) == len(_one_busy_closed().records)
    assert len(ats) >= 2 and ats == sorted(ats) and len(set(ats)) == len(ats)


def test_the_sweep_cursor_round_trips_and_a_malformed_one_starts_over():
    sweep = SourceCursor(90, "abc", 12, True, 3, 40, 95)
    assert sweep.text() == "90:abc;12;3;40;95"
    assert SourceCursor.parse("90:abc;12;3;40;95") == sweep
    assert SourceCursor.parse("90:abc;;0;;") == SourceCursor(90, "abc", None, True)
    assert SourceCursor(90, "abc", 12, True, 0, 12).text() == "90:abc;12"  # the old form
    assert SourceCursor.parse("90:abc;12;0;30;") == SourceCursor(90, "abc", 12, True, 0, 30)
    for bad in ("90:abc;12;3;40;", "90:abc;12;0;40;95", "90:abc;12;;40;95", "90:abc;1;2;x;4"):
        assert SourceCursor.parse(bad) == SourceCursor(), bad


def _smallest_workable(build) -> int:
    """The smallest budget every run can work with: the costliest start (a cursor at the tip
    that owes every token account an earlier run stored from its first transaction, which the
    sweep lists one at a time, Q-O162) up to its first transaction; and at the tip one
    request more than the run takes, since the sync counts a budget used to its last request
    as spent."""
    full = _Daily(build()).to_tip(10**6)
    slot = full.cursor.split(":")[0]
    starts = []
    for cursor in (f"{slot}:;", full.cursor):
        chain = build()
        _Daily(chain, cursor=cursor, store=list(full.store)).to_tip(10**6)
        methods = [r["method"] for r in chain.requests]
        starts.append(methods.index("getTransaction") + 1)
        tip_run = len(methods)
    return max(*starts, tip_run) + 1


@pytest.mark.parametrize(
    ("build", "page_size"),
    [
        (_mixed, None),
        (_mixed, 1),
        (_mixed, 3),
        (_three_hundred_and_six, None),
        (_thirty_closed, None),
        (_thirty_closed, 2),
        (_one_busy_closed, None),
    ],
    ids=["mixed", "mixed-page-1", "mixed-page-3", "306", "30-closed", "30-closed-page-2", "busy"],
)
def test_every_daily_budget_reaches_the_tip_with_what_one_run_serves(build, page_size):
    """Property (finding 1): whatever the same budget every day, from the smallest one a run
    can work with to an unlimited one, the runs reach the tip and serve the same transactions,
    read the same way, as one unlimited run; no run hands back its cursor while something it
    listed waits, and the cursor never cycles (the long sequence at a sample of budgets).
    The floor is pinned, so a run's start growing dearer fails here, not silently; since the
    sweep (Q-O162) it no longer grows with the stored token accounts (30 closed ones: 9)."""
    full = _Daily(build(), page_size).to_tip(10**6)
    want = _legs_by_hash(full)
    assert len(want) == len(build().records)
    needed = len(full.chain.requests)
    smallest = _smallest_workable(build)
    assert smallest == FLOORS[build.__name__]
    limits = range(smallest, needed + 3)
    if build is _three_hundred_and_six:
        limits = [smallest, smallest + 1, smallest + 3, 12, 60, 2400]
    elif build is not _mixed:  # the long sweeps, at a sample of budgets
        limits = [smallest, smallest + 1, smallest + 2, smallest + 5, 25, 40, needed + 2]
    for limit in limits:
        daily = _Daily(build(), page_size).to_tip(limit)
        assert _legs_by_hash(daily) == want, limit


@pytest.mark.parametrize("first", [8, 9, 12, 60])
def test_a_first_run_cut_short_loses_no_transaction_of_a_closed_token_account(first):
    """Sequence 1 (a): the first run is cut while the closed account is being found or listed;
    the next run, with the default budget, serves every transaction and stands at the tip
    (the cursor no longer freezes on a false tip)."""
    daily = _Daily(_three_hundred_and_six())
    assert not daily.run(first)
    assert daily.run(2400) and len(daily.served) == 306
    cursor = daily.cursor
    assert daily.run(2400) and daily.cursor == cursor


def test_two_cut_runs_still_serve_a_late_found_account_s_credit():
    """Sequence 1 (b): the credit at slot 7 names the closed T1 alone, and T1 is found only by
    the debit at slot 200; two runs are cut before the third reaches the tip."""
    r = Records(A)
    credit = r.token_in(7, T1)
    records = [r.sol_in(1), r.open(3, T1), credit] + [r.sol_in(10 + i) for i in range(150)]
    records += [r.token_out(200, T1), r.close(210, T1)] + [r.sol_in(300 + i) for i in range(20)]
    daily = _Daily(FakeChain(records)).to_tip(9, 130, 2400)
    assert signature(credit) in daily.served and len(daily.served) == len(records)


@pytest.mark.parametrize("limits", [(20, 10**6), (20, 80, 10**6), (45, 10**6)])
def test_one_ordinary_cut_leaves_no_gap_in_a_token_account_closed_later(limits):
    """Sequence 1 (c): a run cut on an ordinary cursor, before the debit that names T1 is
    reached; T1 is closed by the next run, which finds it again and serves the credit."""
    r = Records(A)
    credit = r.token_in(50, T1)
    records = [r.sol_in(1), r.open(3, T1)] + [r.sol_in(10 + i) for i in range(30)] + [credit]
    records += [r.sol_in(60 + i) for i in range(60)]
    records += [r.token_out(200, T1), r.close(210, T1), r.sol_in(220)]
    daily = _Daily(FakeChain(records)).to_tip(*limits)
    assert signature(credit) in daily.served and len(daily.served) == len(records)


@pytest.mark.parametrize("first", range(6, 14))
def test_a_refused_block_time_keeps_the_record_for_the_next_run(first):
    """Sequence 4: the record carries no block time and the budget refuses getBlockTime; the
    record stays unserved, and the next run serves it with the block's time."""
    r = Records(A)
    late = r.token_in(7, T1, block_time=False)
    records = [r.sol_in(1), r.open(3, T1), late, r.sol_in(9), r.sol_in(12)]
    chain = FakeChain(records)
    chain.block_times[7] = 1_770_000_007
    daily = _Daily(chain).to_tip(first, 2400)
    assert len(daily.served) == len(records)
    assert daily.served[signature(late)].occurred_at.timestamp() == 1_770_000_007


def test_one_finalized_height_per_run():
    """Finding 3: the run fixes the finalized slot first and serves nothing above it, so
    listings read moments apart agree; the next run serves the rest."""
    chain = _chain(*ORDER)
    chain.finalized = _record("spl_in")["slot"]
    items, cursor = _read_all(_source(chain), A)
    assert [tx.hash for tx in items] == [signature(_record(i)) for i in ORDER[:3]]
    assert chain.calls("getSlot") == [[{"commitment": "finalized"}]]
    chain.finalized = None
    later, _ = _read_all(_source(chain), A, cursor)
    assert [tx.hash for tx in later] == [signature(_record(i)) for i in ORDER[2:]]


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


def test_an_unreadable_card_keeps_the_fee_the_address_paid():
    """`spl_out` without its token rows: no token leg is guessed, the fee A paid stays beside
    the question, and the group is never booked as „a fee“ (rules/review.route_group)."""
    record = _record("spl_out")
    for key in ("preTokenBalances", "postTokenBalances"):
        record["meta"].pop(key)
    group = solana.classify_tx(SolanaRpc.chain_tx(record, {A}, strict=False), _ctx())
    assert (group.kind, group.question) == (GroupKind.UNKNOWN, solana.QUESTION_UNREADABLE)
    assert _legs(group) == [(MovementKind.FEE, "fee", record["meta"]["fee"], None, "fee")]
    assert group.fee_only
    routing = route_group(GroupRoutingInput(kind=group.kind, fee_only=True, question=True))
    assert routing.decision is RoutingDecision.REVIEW
    # the same record received by another address: that address paid nothing, no leg
    other = solana.classify_tx(
        SolanaRpc.chain_tx(record, {"ANgPywB9VnrmKJ9oXqByisxMxB564F4CoDY4HWZd4Y5J"}, strict=False),
        ClassifyContext(
            chain=Chain.SOLANA,
            address="ANgPywB9VnrmKJ9oXqByisxMxB564F4CoDY4HWZd4Y5J",
            own_addresses=frozenset({"ANgPywB9VnrmKJ9oXqByisxMxB564F4CoDY4HWZd4Y5J"}),
        ),
    )
    assert (other.legs, other.question) == ([], solana.QUESTION_UNREADABLE)


def test_an_old_send_from_a_closed_token_account_is_a_card_with_its_fee():
    """Review fix C: `spl_out` from before rows named their owner, its token account closed and
    unknown to the run. Nothing is guessed: the card with A's fee, waiting for a person."""
    record = _record("spl_out")
    for key in ("preTokenBalances", "postTokenBalances"):
        for row in record["meta"][key]:
            row.pop("owner")
    # as SolanaSource keeps it beside the record: the own token accounts the run knew
    record[solana.SYNC_KEY] = {"own_token_accounts": []}
    tx = SolanaRpc.chain_tx(record, {A}, set(), strict=False)
    assert tx.transfers == []
    group = solana.classify_tx(tx, _ctx())
    assert (group.kind, group.question) == (GroupKind.UNKNOWN, solana.QUESTION_UNREADABLE)
    assert _legs(group) == [(MovementKind.FEE, "fee", record["meta"]["fee"], None, "fee")]
    # the same record once the run knows the account: the send it is
    record[solana.SYNC_KEY] = {"own_token_accounts": [A_USDC]}
    known = SolanaRpc.chain_tx(record, {A}, {A_USDC}, strict=False)
    assert _legs(solana.classify_tx(known, _ctx())) == _want("spl_out")


def test_a_slot_without_any_block_time_fails_the_run_as_the_ledger_s_end():
    old = _record("native_receive")
    old["blockTime"] = None
    with pytest.raises(ChainSourceError) as caught:
        _read_all(_source(FakeChain([old])), A)
    assert caught.value.code == E.WALLET_SOURCE_FAILED
