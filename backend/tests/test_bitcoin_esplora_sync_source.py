"""Phase 7 step 4 wiring: `bitcoin_esplora.make_source`, the ChainSource the sync calls
(docs/phases/phase-7.md D181 (a), D182 (1), (4), D185 (3)), over a fake Esplora that pages
an address's history newest first by 25 like the real API: the cursor walks forward across
runs although Esplora pages backward, nothing above tip − 6 is read, a deferred walk
resumes, the balance is the one at the final height, the budget is asked before every
request. Every address, txid and amount is invented (btc_utxo.yaml's generated addresses)."""

import hashlib
import json

import pytest

from hausbuch.rules.chain.registry import registry
from hausbuch.schemas.chain import AssetKey, Chain, ChainSourceKind
from hausbuch.services.chain.base import (
    ChainSourceError,
    RequestBudget,
    factory_for,
    reconcile_scope,
)
from hausbuch.services.chain.bitcoin_esplora import (
    BLOCKSTREAM_URL,
    MEMPOOL_URL,
    PAGE_SIZE,
    EsploraSource,
    make_source,
)

OWN = "bc1q62vj3efj43p06q74rjqe2kmvfs4yya99s7rtxz"
PAYER = "bc1qfrg03s5v98wyvu393dcfdeun6pvwthnt2gatuf"
BASE = "https://esplora.example.invalid/api"
ROW = registry().chain(Chain.BITCOIN)


def txid(n: int) -> str:
    return hashlib.sha256(f"invented btc tx {n}".encode()).hexdigest()


def receive(n: int, block: int, sats: int = 1000) -> dict:
    """A payment of `sats` from a stranger to OWN, confirmed in `block`."""
    return {
        "txid": txid(n),
        "vin": [
            {
                "txid": txid(10_000 + n),
                "vout": 0,
                "prevout": {"scriptpubkey_address": PAYER, "value": sats + 100},
            }
        ],
        "vout": [{"scriptpubkey_address": OWN, "value": sats}],
        "fee": 100,
        "status": {"confirmed": True, "block_height": block, "block_time": 1_772_000_000 + block},
    }


class Chainstate:
    """A fake Esplora: per address its records (any order; served newest first), the tip, and
    each address's tip balance. Counts the requests. Its index holds the blocks up to its tip
    only (a host behind the chain does not know a later transaction); an anchor it does not
    know gives an empty page, as both public hosts answer (checked in the review of PR #387),
    and its /tx/{txid}/status a 404. `failing` paths (by their first segment after the base,
    such as "address") answer 503; `base` is the host's URL."""

    def __init__(self, tip: int, base: str = BASE) -> None:
        self.tip = tip
        self.base = base
        self.records: dict[str, list[dict]] = {OWN: []}
        self.calls: list[str] = []
        self.failing: set[str] = set()

    def indexed(self, address: str) -> list[dict]:
        return [r for r in self.records[address] if r["status"]["block_height"] <= self.tip]

    def balance(self, address: str) -> int:
        funded = sum(
            o["value"]
            for r in self.indexed(address)
            for o in r["vout"]
            if o.get("scriptpubkey_address") == address
        )
        spent = sum(
            i["prevout"]["value"]
            for r in self.indexed(address)
            for i in r["vin"]
            if i["prevout"].get("scriptpubkey_address") == address
        )
        return funded - spent

    def __call__(self, url, headers, timeout, max_bytes):
        self.calls.append(url)
        path = url.removeprefix(self.base)
        if path == "/blocks/tip/height":
            return 200, f"{self.tip}\n".encode()
        parts = path.split("/")
        if parts[1] in self.failing:
            return 503, b""
        if parts[1] == "tx":  # /tx/{txid}/status
            known = any(r["txid"] == parts[2] for a in self.records for r in self.indexed(a))
            return (200, b'{"confirmed": true}') if known else (404, b"Transaction not found")
        address = parts[2]
        if len(parts) == 3:
            stats = {"funded_txo_sum": self.balance(address), "spent_txo_sum": 0}
            return 200, json.dumps({"address": address, "chain_stats": stats}).encode()
        newest = sorted(
            self.indexed(address), key=lambda r: r["status"]["block_height"], reverse=True
        )
        if len(parts) == 6:
            ids = [r["txid"] for r in newest]
            newest = newest[ids.index(parts[5]) + 1 :] if parts[5] in ids else []
        return 200, json.dumps(newest[:PAGE_SIZE]).encode()


ONE = "https://one.example.invalid/api"
TWO = "https://two.example.invalid/api"


class Hosts:
    """Two fake Esplora hosts over one chain, tried in order (`esplora_urls`)."""

    def __init__(self, tip: int) -> None:
        self.one, self.two = Chainstate(tip, ONE), Chainstate(tip, TWO)
        self.two.records = self.one.records  # one chain, two indexes

    def __call__(self, url, headers, timeout, max_bytes):
        host = self.one if url.startswith(ONE) else self.two
        return host(url, headers, timeout, max_bytes)

    def source(self, budget: RequestBudget | None = None) -> EsploraSource:
        class S:
            esplora_urls = (ONE, TWO)

        return make_source(ROW, S(), budget or RequestBudget(600), fetch=self, sleep=lambda s: None)


def _source(world: Chainstate, budget: RequestBudget | None = None) -> EsploraSource:
    class S:
        esplora_urls = (BASE,)

    return make_source(ROW, S(), budget or RequestBudget(600), fetch=world, sleep=lambda s: None)


def _run(source, cursor):
    """What services/chain/sync._read does with the pages: until the cursor stops moving."""
    seen = []
    while True:
        page = source.transactions(OWN, cursor)
        seen.extend(page.items)
        if page.next_cursor is None or page.next_cursor == cursor:
            return seen, cursor
        cursor = page.next_cursor


def test_make_source_is_found_and_reads_the_settings():
    assert factory_for(ChainSourceKind.BITCOIN_ESPLORA) is make_source

    class S:
        esplora_urls = (
            "https://one.example.invalid/api",
            "https://two.example.invalid/api",
            "https://three.example.invalid/api",
        )

    source = make_source(ROW, S(), RequestBudget(1))
    assert source.host == "one.example.invalid"  # before any request (the sync's row)
    assert reconcile_scope(source) == "account"
    assert source.confirmations_final == 6
    assert source.client._urls() == list(S.esplora_urls)

    class Old:
        esplora_url = "https://esplora.example.invalid/api"

    assert make_source(ROW, Old(), RequestBudget(1)).client._urls() == [
        "https://esplora.example.invalid/api",
        BLOCKSTREAM_URL,
    ]
    assert make_source(ROW, object(), RequestBudget(1)).client._urls() == [
        MEMPOOL_URL,
        BLOCKSTREAM_URL,
    ]


def test_the_cursor_walks_forward_across_runs_over_more_than_one_page():
    world = Chainstate(tip=135)
    world.records[OWN] = [receive(n, 100 + n) for n in range(30)]  # blocks 100..129
    seen, cursor = _run(_source(world), None)
    assert sorted(tx.block for tx in seen) == list(range(100, 130))
    assert cursor == "129"  # tip 135 − 6
    # A second run the same day: nothing new is final, no request at all but the tip.
    world.calls.clear()
    again, cursor = _run(_source(world), cursor)
    assert again == [] and cursor == "129"
    assert world.calls == [f"{BASE}/blocks/tip/height"]
    # A new payment, and the next day's tip: only it is read, from the newest page alone.
    world.records[OWN].append(receive(99, 131))
    world.tip = 140
    world.calls.clear()
    new, cursor = _run(_source(world), cursor)
    assert [tx.hash for tx in new] == [txid(99)] and cursor == "134"
    assert world.calls == [f"{BASE}/blocks/tip/height", f"{BASE}/address/{OWN}/txs/chain"]


def test_nothing_above_the_final_height_is_read_until_it_is_final():
    world = Chainstate(tip=110)
    world.records[OWN] = [receive(1, 100), receive(2, 105)]  # 105 = tip − 5: not final
    seen, cursor = _run(_source(world), None)
    assert [tx.block for tx in seen] == [100] and cursor == "104"
    world.tip = 111
    seen, cursor = _run(_source(world), cursor)
    assert [tx.block for tx in seen] == [105] and cursor == "105"


def test_a_deferred_walk_resumes_where_it_stood():
    world = Chainstate(tip=200)
    world.records[OWN] = [receive(n, 100 + n) for n in range(30)]
    budget = RequestBudget(2)  # the tip and one page
    source = _source(world, budget)
    first = source.transactions(OWN, None)
    assert len(first.items) == PAGE_SIZE and first.next_cursor == f"-1:194:{txid(5)}"
    refused = source.transactions(OWN, first.next_cursor)
    assert refused.items == [] and refused.next_cursor == first.next_cursor
    assert budget.exhausted
    world.records[OWN].append(receive(77, 199))  # a newer block, final by the next run
    world.tip = 210
    rest, cursor = _run(_source(world), first.next_cursor)
    assert sorted(tx.block for tx in rest) == [100, 101, 102, 103, 104, 199]
    assert cursor == "204"


def test_the_balance_is_the_one_at_the_final_height():
    world = Chainstate(tip=110)
    world.records[OWN] = [receive(1, 100, 5000), receive(2, 108, 700)]
    source = _source(world)
    (balance,) = source.balances(OWN)
    assert balance.asset_key == AssetKey(chain=Chain.BITCOIN)
    assert balance.at_block == 104 and balance.units == 5000  # block 108 backed out


@pytest.mark.parametrize("limit", [0, 1, 2])
def test_a_spent_budget_returns_no_balance(limit):
    world = Chainstate(tip=110)
    world.records[OWN] = [receive(1, 100)]
    budget = RequestBudget(limit)
    assert _source(world, budget).balances(OWN) == []
    assert budget.exhausted and len(world.calls) == limit


def test_the_tip_and_the_history_come_from_one_host():
    """Review of PR #387 (sim_cursor 1): host one answers the tip but fails its history; host
    two is ten blocks behind. The tip was read from one and the pages from two, so a block
    two had not indexed read as empty and the cursor passed it for good. Now the run fails
    on the host that gave the tip (or moves the pair to the next host), and the next run
    reads the transaction."""
    hosts = Hosts(tip=1000)
    hosts.one.records[OWN] = [receive(1, 950), receive(2, 993)]
    hosts.two.tip = 990
    hosts.one.failing = {"address"}
    seen, cursor = _run(hosts.source(), "940")
    # the pair moved to host two: its own window (984), never one's 994 over two's pages
    assert [tx.block for tx in seen] == [950] and cursor == "984"
    hosts.one.failing = set()
    hosts.one.tip = hosts.two.tip = 1010
    seen, cursor = _run(hosts.source(), cursor)
    assert [tx.block for tx in seen] == [993] and cursor == "1004"


def test_an_unknown_anchor_walks_again_from_the_top():
    """Review of PR #387 (sim_cursor 2): a walk deferred by the budget on host one resumes on
    host two, which has not indexed the anchor's block and answers an empty page. That page
    is no end: two does not confirm the anchor, so the walk starts again from two's newest
    page down to `done`, and every transaction is read."""
    hosts = Hosts(tip=146)
    hosts.one.records[OWN] = [receive(n, 100 + n) for n in range(1, 41)]  # blocks 101..140
    first = hosts.source(RequestBudget(2)).transactions(OWN, None)  # the tip and one page
    assert len(first.items) == PAGE_SIZE and first.next_cursor.startswith("-1:140:")
    hosts.one.failing = {"address"}
    hosts.two.tip = 110  # behind: the anchor (block 116) is not in its index
    seen, cursor = _run(hosts.source(), first.next_cursor)
    assert sorted(tx.block for tx in seen) == list(range(101, 105)) and cursor == "104"
    hosts.one.failing = set()
    hosts.one.tip = hosts.two.tip = 146
    rest, cursor = _run(hosts.source(), cursor)
    read = {tx.block for tx in first.items + seen + rest}
    assert read == set(range(101, 141)) and cursor == "140"


@pytest.mark.parametrize("count", [PAGE_SIZE, 2 * PAGE_SIZE])
def test_a_history_of_whole_pages_ends(count):
    """The other empty anchored page: the page before was full and the oldest. The host
    confirms the anchor, so the walk ends there and the cursor reaches the window."""
    world = Chainstate(tip=300)
    world.records[OWN] = [receive(n, 100 + n) for n in range(count)]
    seen, cursor = _run(_source(world), None)
    assert len(seen) == count and cursor == "294"
    assert world.calls[-1] == f"{BASE}/tx/{txid(0)}/status"


def test_a_host_behind_what_is_stored_gives_no_balance():
    """A host whose final height is below what an earlier run read hands the run to the
    next host; when none has reached it, the balance is refused (a failed run), never a
    figure at a lower height that would read as a mismatch."""
    hosts = Hosts(tip=120)
    hosts.one.records[OWN] = [receive(1, 100, 5000)]
    hosts.one.tip = 110  # one is behind the cursor's 114, two is not
    source = hosts.source()
    assert _run(source, "114") == ([], "114")
    (balance,) = source.balances(OWN)
    assert (balance.at_block, balance.units) == (114, 5000)
    assert hosts.two.calls  # the pair moved to two
    hosts.two.tip = 110
    source = hosts.source()
    assert _run(source, "114") == ([], "114")
    with pytest.raises(ChainSourceError):
        source.balances(OWN)
