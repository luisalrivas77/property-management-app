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
from hausbuch.services.chain.base import RequestBudget, factory_for, reconcile_scope
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
    each address's tip balance. Counts the requests."""

    def __init__(self, tip: int) -> None:
        self.tip = tip
        self.records: dict[str, list[dict]] = {OWN: []}
        self.calls: list[str] = []

    def balance(self, address: str) -> int:
        funded = sum(
            o["value"]
            for r in self.records[address]
            for o in r["vout"]
            if o.get("scriptpubkey_address") == address
        )
        spent = sum(
            i["prevout"]["value"]
            for r in self.records[address]
            for i in r["vin"]
            if i["prevout"].get("scriptpubkey_address") == address
        )
        return funded - spent

    def __call__(self, url, headers, timeout, max_bytes):
        self.calls.append(url)
        path = url.removeprefix(BASE)
        if path == "/blocks/tip/height":
            return 200, f"{self.tip}\n".encode()
        parts = path.split("/")
        address = parts[2]
        if len(parts) == 3:
            stats = {"funded_txo_sum": self.balance(address), "spent_txo_sum": 0}
            return 200, json.dumps({"address": address, "chain_stats": stats}).encode()
        newest = sorted(
            self.records[address], key=lambda r: r["status"]["block_height"], reverse=True
        )
        if len(parts) == 6:
            ids = [r["txid"] for r in newest]
            newest = newest[ids.index(parts[5]) + 1 :]
        return 200, json.dumps(newest[:PAGE_SIZE]).encode()


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
