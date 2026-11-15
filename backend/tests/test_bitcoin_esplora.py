"""Phase 7 step 4: the `bitcoin_esplora` ChainSource over recorded Esplora JSON (docs/phases/
phase-7.md D181 (a), D185 (3), D192), through a fake transport: both base URLs replay the same
records to the same ChainTx (chains.yaml `esplora_two_urls`), paging by 25, the Blockstream
fallback, a 429 (btc_utxo.yaml `errors`), a refusal, the pacing, nothing of the URL in an
error."""

import json

import fixture_models_phase7 as phase7
import pytest

from hausbuch.schemas.chain import AssetKey, Chain, ChainSourceKind
from hausbuch.services.chain.base import ChainSourceError
from hausbuch.services.chain.bitcoin_esplora import (
    BLOCKSTREAM_URL,
    MEMPOOL_URL,
    PAGE_SIZE,
    SOURCE_FAILED,
    BitcoinEsplora,
    from_settings,
    to_chain_tx,
)

BTC_RAW = phase7.raw("btc_utxo")
CASES = {c["id"]: c for c in BTC_RAW["cases"]}
CHAINS = phase7.load("chains")
BTC = phase7.load("btc_utxo")
OWN = "bc1q62vj3efj43p06q74rjqe2kmvfs4yya99s7rtxz"


class FakeEsplora:
    """Answers recorded bodies by URL; an unknown URL is a 404. `routes` maps a URL to
    (status, body) or to an exception to raise."""

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, url, headers, timeout, max_bytes):
        self.calls.append(url)
        answer = self.routes.get(url, (404, b""))
        if isinstance(answer, Exception):
            raise answer
        return answer


def _body(value) -> tuple[int, bytes]:
    return 200, json.dumps(value).encode()


def _source(fetch, base=MEMPOOL_URL, fallback=BLOCKSTREAM_URL, sleeps=None):
    return BitcoinEsplora(
        base_url=base,
        fallback_url=fallback,
        fetch=fetch,
        sleep=(sleeps.append if sleeps is not None else lambda s: None),
    )


@pytest.mark.parametrize(
    "case", [p for p in CHAINS.providers if p.chain is Chain.BITCOIN], ids=lambda c: c.id
)
def test_two_base_urls_give_the_same_chain_tx(case):
    assert case.pending is None
    _, case_id = case.records_from.split(".", 1)
    record = CASES[case_id]["tx"]
    read = []
    for base in case.urls:
        fetch = FakeEsplora({f"{base}/address/{OWN}/txs/chain": _body([record])})
        source = _source(fetch, base=base, fallback=None)
        page = source.transactions(OWN, None)
        assert fetch.calls == [f"{base}/address/{OWN}/txs/chain"]
        read.append(page)
    assert case.expect.same_as_first
    assert all(page == read[0] for page in read)
    (tx,) = read[0].items
    assert tx == to_chain_tx(record, OWN)
    assert tx.hash == record["txid"] and tx.block == 900102
    assert tx.fee == (AssetKey(chain=Chain.BITCOIN), 5000)  # the address spent an input
    assert [t.index for t in tx.transfers] == ["vin:0", "0", "1"]
    assert tx.raw == record  # the stored original is the protocol's own shape


def test_a_receive_carries_no_fee_for_the_receiver():
    tx = to_chain_tx(CASES["receive"]["tx"], OWN)
    assert tx.fee is None


def test_paging_by_25_from_the_last_txid():
    records = []
    for n in range(PAGE_SIZE):
        record = json.loads(json.dumps(CASES["receive"]["tx"]))
        record["txid"] = f"{n:064x}"
        records.append(record)
    base = "https://esplora.example.invalid/api"
    last = records[-1]["txid"]
    fetch = FakeEsplora(
        {
            f"{base}/address/{OWN}/txs/chain": _body(records),
            f"{base}/address/{OWN}/txs/chain/{last}": _body([]),
        }
    )
    sleeps: list[float] = []
    source = _source(fetch, base=base, fallback=None, sleeps=sleeps)
    first = source.transactions(OWN, None)
    assert len(first.items) == PAGE_SIZE and first.next_cursor == last
    second = source.transactions(OWN, first.next_cursor)
    assert second.items == [] and second.next_cursor is None
    assert sleeps == [1.0]  # one request a second, the first one not delayed
    assert len(source.pages) == PAGE_SIZE  # every record read kept for the original
    assert source.host == "esplora.example.invalid"


def test_a_short_page_is_the_last():
    base = "https://esplora.example.invalid/api"
    fetch = FakeEsplora({f"{base}/address/{OWN}/txs/chain": _body([CASES["receive"]["tx"]])})
    page = _source(fetch, base=base).transactions(OWN, None)
    assert len(page.items) == 1 and page.next_cursor is None


@pytest.mark.parametrize("case", BTC.balances, ids=lambda c: c.id)
def test_balances(case):
    assert case.pending is None
    base = "https://esplora.example.invalid/api"
    stats = case.chain_stats.model_dump()
    fetch = FakeEsplora(
        {
            f"{base}/blocks/tip/height": (200, b"900600\n"),
            f"{base}/address/{case.address}": _body(
                {"address": case.address, "chain_stats": stats, "mempool_stats": stats}
            ),
        }
    )
    (balance,) = _source(fetch, base=base).balances(case.address)
    assert balance.asset_key == AssetKey(chain=Chain.BITCOIN)
    assert balance.units == case.expect_sats and balance.at_block == 900600


def test_one_transaction_by_id():
    record = CASES["spend_from_two_own_addresses"]["tx"]
    fetch = FakeEsplora({f"{MEMPOOL_URL}/tx/{record['txid']}": _body(record)})
    tx = _source(fetch).transaction(record["txid"])
    assert tx == to_chain_tx(record) and tx.fee is None


@pytest.mark.parametrize("failure", [(502, b""), OSError("down"), (200, b"<html>")])
def test_the_fallback_answers_when_the_first_fails(failure):
    record = CASES["receive"]["tx"]
    path = f"/address/{OWN}/txs/chain"
    fetch = FakeEsplora({MEMPOOL_URL + path: failure, BLOCKSTREAM_URL + path: _body([record])})
    source = _source(fetch)
    page = source.transactions(OWN, None)
    assert page.items == [to_chain_tx(record, OWN)]
    assert fetch.calls == [MEMPOOL_URL + path, BLOCKSTREAM_URL + path]
    assert source.host == "blockstream.info"


def test_both_failing_is_a_source_failure_without_the_url():
    fetch = FakeEsplora({})  # 404 everywhere
    with pytest.raises(ChainSourceError) as caught:
        _source(fetch).balances(OWN)
    assert caught.value.code == SOURCE_FAILED and caught.value.status == 404
    assert OWN not in str(caught.value) and "mempool" not in str(caught.value)
    assert len(fetch.calls) == 2


@pytest.mark.parametrize("case", BTC.errors, ids=lambda c: c.id)
def test_throttled_and_refused_stop_without_the_fallback(case):
    assert case.pending is None
    path = f"/address/{OWN}/txs/chain"
    fetch = FakeEsplora(
        {
            MEMPOOL_URL + path: (case.status, b"slow down " + OWN.encode()),
            BLOCKSTREAM_URL + path: _body([]),
        }
    )
    with pytest.raises(ChainSourceError) as caught:
        _source(fetch).transactions(OWN, None)
    assert caught.value.code == case.expect_error and caught.value.status == case.status
    assert fetch.calls == [MEMPOOL_URL + path]
    assert OWN not in str(caught.value)


def test_an_unconfirmed_or_unbalanced_record_is_unreadable():
    record = dict(CASES["receive"]["tx"], status={"confirmed": False})
    path = f"/tx/{record['txid']}"
    fetch = FakeEsplora({MEMPOOL_URL + path: _body(record), BLOCKSTREAM_URL + path: _body(record)})
    with pytest.raises(ChainSourceError) as caught:
        _source(fetch).transaction(record["txid"])
    assert caught.value.code == SOURCE_FAILED
    unbalanced = dict(CASES["receive"]["tx"], fee=1)
    fetch = FakeEsplora({MEMPOOL_URL + path: _body(unbalanced)})
    with pytest.raises(ChainSourceError):
        _source(fetch, fallback=None).transaction(record["txid"])


def test_from_settings():
    class Settings:
        esplora_url = "https://esplora.example.invalid/api"

    source = from_settings(Settings())
    assert source.base_url == "https://esplora.example.invalid/api"
    assert source.fallback_url == BLOCKSTREAM_URL
    assert source.kind is ChainSourceKind.BITCOIN_ESPLORA
    assert from_settings(object()).base_url == MEMPOOL_URL
