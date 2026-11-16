"""Phase 7 step 4 wiring: a Bitcoin wallet read and booked end to end (docs/phases/phase-7.md
D182, D185, D186): `sync_wallets` with the real `bitcoin_esplora.make_source` and the real
`utxo.classify_tx`, over a fake Esplora HTTP layer, for a wallet with two Bitcoin addresses;
then „Nein, Empfänger“ on the change card (services/chain/change_card).

Every address and txid is invented (tests/fixtures/phase7/btc_utxo.yaml's generated
addresses, BIP-173's example address); no household fact."""

import hashlib
import itertools
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from db.db_support import _admin
from db.test_phase7_migration import BECH32, _address, _wallet
from db.test_wallet_sync_core import ACTOR, SETTINGS
from psycopg.rows import namedtuple_row

from hausbuch.rules.errors import WALLET_CHANGE_ANSWERED, WALLET_ORIGINAL_MISSING, RuleError
from hausbuch.schemas.chain import ChainSourceKind
from hausbuch.services.chain.base import register_source
from hausbuch.services.chain.bitcoin_esplora import PAGE_SIZE, make_source
from hausbuch.services.chain.change_card import answer_payee
from hausbuch.services.chain.sync import sync_wallets

pytestmark = pytest.mark.db

OWN = "bc1q62vj3efj43p06q74rjqe2kmvfs4yya99s7rtxz"  # the wallet's first address
SECOND = "bc1q0phhhvur8dmwqv48kmf6d7ck3s3zkfjksuzqrl"  # its second
CHANGE = "bc1qsdqk2xmw06qgn2589yjzthjlk368wfpq4pgz2k"  # an unregistered change address
CHANGE2 = BECH32
CHANGE3 = "bc1qmc3ct8j5r6se3lnsrd8g9xmhw6p909s5l7kyhg"
PAYEE = "bc1qfrg03s5v98wyvu393dcfdeun6pvwthnt2gatuf"
STRANGER = "bc1qn8duhwxyj87x0d2efhvv44p5r0f48jr6t94ngw"
BASE = "https://esplora.example.invalid/api"
T0 = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)


def txid(n: int) -> str:
    return hashlib.sha256(f"invented wired btc tx {n}".encode()).hexdigest()


def record(n, block, ins, outs):
    fee = sum(v for _, v in ins) - sum(v for _, v in outs)
    return {
        "txid": txid(n),
        "vin": [
            {"txid": txid(900 + n), "vout": i, "prevout": {"scriptpubkey_address": a, "value": v}}
            for i, (a, v) in enumerate(ins)
        ],
        "vout": [{"scriptpubkey_address": a, "value": v} for a, v in outs],
        "fee": fee,
        "status": {
            "confirmed": True,
            "block_height": block,
            "block_time": int((T0 + timedelta(hours=block - 100)).timestamp()),
        },
    }


class Esplora:
    """The fake Esplora HTTP layer: one chain of records, each served to every address it
    touches, newest first by PAGE_SIZE; the tip; each address's tip balance."""

    def __init__(self) -> None:
        self.tip = 111
        self.records: list[dict] = []
        self.calls = 0

    def _of(self, address):
        return [
            r
            for r in self.records
            if any(i["prevout"]["scriptpubkey_address"] == address for i in r["vin"])
            or any(o["scriptpubkey_address"] == address for o in r["vout"])
        ]

    def __call__(self, url, headers, timeout, max_bytes):
        self.calls += 1
        path = url.removeprefix(BASE)
        if path == "/blocks/tip/height":
            return 200, str(self.tip).encode()
        parts = path.split("/")
        address, mine = parts[2], self._of(parts[2])
        if len(parts) == 3:
            funded = sum(
                o["value"] for r in mine for o in r["vout"] if o["scriptpubkey_address"] == address
            )
            spent = sum(
                i["prevout"]["value"]
                for r in mine
                for i in r["vin"]
                if i["prevout"]["scriptpubkey_address"] == address
            )
            stats = {"funded_txo_sum": funded, "spent_txo_sum": spent}
            return 200, json.dumps({"address": address, "chain_stats": stats}).encode()
        newest = sorted(mine, key=lambda r: r["status"]["block_height"], reverse=True)
        if len(parts) == 6:
            newest = newest[[r["txid"] for r in newest].index(parts[5]) + 1 :]
        return 200, json.dumps(newest[:PAGE_SIZE]).encode()


@pytest.fixture
def esplora():
    chain = Esplora()

    class Urls:
        esplora_urls = (BASE,)

    def factory(row, settings, budget):
        return make_source(row, Urls(), budget, fetch=chain, sleep=lambda s: None)

    register_source(ChainSourceKind.BITCOIN_ESPLORA, factory)
    yield chain
    register_source(ChainSourceKind.BITCOIN_ESPLORA, None)


@pytest.fixture
def btc_wallet(dbname, fresh_workspaces):
    ws = fresh_workspaces[0]
    with _admin(dbname) as conn:
        account = _wallet(conn, ws)
        a = _address(conn, ws, account, chain="bitcoin", address=OWN, address_masked="bc1q…rtxz")
        b = _address(conn, ws, account, chain="bitcoin", address=SECOND, address_masked="bc1q…zqrl")
    return ws, account, a, b


@pytest.fixture
def run(app_engine, blob_store):
    ticks = itertools.count()

    def clock():
        return T0 + timedelta(days=1, seconds=next(ticks))

    def sync(ws):
        return sync_wallets(
            app_engine, ws, settings=SETTINGS, store=blob_store, actor=ACTOR, clock=clock
        )

    def answer(ws, group_id, vout):
        return answer_payee(
            app_engine,
            ws,
            group_id,
            vout,
            settings=SETTINGS,
            store=blob_store,
            actor=ACTOR,
            clock=clock,
        )

    return sync, answer


def _rows(dbname, query, *params):
    with _admin(dbname) as conn:
        return conn.cursor(row_factory=namedtuple_row).execute(query, params).fetchall()


def _live_groups(dbname, account):
    return _rows(
        dbname,
        "SELECT id, tx_hash, kind, status, question FROM movement_group"
        " WHERE account_id = %s AND superseded_by IS NULL ORDER BY occurred_at, created_at",
        account,
    )


def _movements(dbname, account):
    return _rows(
        dbname,
        'SELECT tx_hash, "index", quantity, address_id FROM asset_movement'
        ' WHERE account_id = %s AND superseded_by IS NULL ORDER BY occurred_at, "index"',
        account,
    )


def _last_runs(dbname, account):
    return _rows(
        dbname,
        "SELECT DISTINCT ON (address_id) address_id, status, cursor_after, reconciled,"
        " difference, document_id FROM wallet_sync WHERE account_id = %s"
        " ORDER BY address_id, started_at DESC",
        account,
    )


def test_a_two_address_bitcoin_wallet_is_read_booked_and_its_change_card_answered(
    dbname, esplora, btc_wallet, run
):
    ws, account, a, b = btc_wallet
    sync, answer = run
    esplora.records = [
        record(1, 100, [(STRANGER, 101_000)], [(OWN, 100_000)]),  # a receive
        # a send with its change to the second own address: no change movement
        record(2, 101, [(OWN, 100_000)], [(PAYEE, 30_000), (SECOND, 69_000)]),
        record(3, 102, [(STRANGER, 51_000)], [(OWN, 50_000)]),
        # a send with its change to an address the app does not know: the card
        record(4, 103, [(OWN, 50_000)], [(PAYEE, 20_000), (CHANGE, 29_500)]),
        record(6, 104, [(STRANGER, 41_000)], [(OWN, 40_000)]),
        # a second card for a spend to the same payee
        record(7, 105, [(OWN, 40_000)], [(PAYEE, 10_000), (CHANGE3, 29_000)]),
        record(9, 106, [(STRANGER, 9_000)], [(OWN, 8_000)]),  # tip 111 − 6 = 105: not final
    ]
    sync(ws)

    groups = _live_groups(dbname, account)
    assert [(g.tx_hash, g.kind, g.status) for g in groups] == [
        (txid(1), "transfer", "proposed"),  # a first sync proposes everything (D186 (1))
        (txid(2), "transfer", "proposed"),
        (txid(3), "transfer", "proposed"),
        (txid(4), "unknown", "proposed"),
        (txid(6), "transfer", "proposed"),
        (txid(7), "unknown", "proposed"),
    ]
    card, second = groups[3], groups[5]
    assert second.question == (
        "Ist eine dieser Adressen Ihr Wechselgeld? bc1q…atuf (0,0001 BTC), bc1q…kyhg (0,00029 BTC)"
    )
    assert card.question == (
        "Ist eine dieser Adressen Ihr Wechselgeld? bc1q…atuf (0,0002 BTC), bc1q…gz2k (0,000295 BTC)"
    )
    assert [
        (m.tx_hash, m.index, m.quantity, m.address_id) for m in _movements(dbname, account)
    ] == [
        (txid(1), "0", Decimal("0.00100000"), a),
        (txid(2), "fee", Decimal("-0.00001000"), a),
        (txid(2), "net", Decimal("-0.00030000"), a),
        (txid(3), "0", Decimal("0.00050000"), a),
        (txid(6), "0", Decimal("0.00040000"), a),
    ]
    runs = {r.address_id: r for r in _last_runs(dbname, account)}
    assert {r.cursor_after for r in runs.values()} == {"105"}
    assert all(r.status == "succeeded" for r in runs.values())
    # Σ movements is 159 000 sats, the account holds 69 000 at block 105 (all on SECOND):
    # the 90 000 the two cards hold back wait („wartet auf Antwort“), never a mismatch
    assert runs[b].reconciled is None and runs[b].difference is None
    assert runs[a].document_id is not None  # the run that booked tx 1 to 3 kept the original

    # „Nein, Empfänger“ on the payee: the card narrows to the change address alone, and so
    # does the other card that asked about the same payee
    narrower = answer(ws, card.id, 0)
    with pytest.raises(RuleError) as refused:
        answer(ws, card.id, 1)  # the card is superseded: no open card any more
    assert refused.value.code == WALLET_CHANGE_ANSWERED
    groups = _live_groups(dbname, account)
    assert groups[3].id == narrower and groups[3].kind == "unknown"
    assert groups[3].question == "Ist diese Adresse Ihr Wechselgeld? bc1q…gz2k (0,000295 BTC)"
    assert groups[5].id != second.id and groups[5].kind == "unknown"
    assert groups[5].question == "Ist diese Adresse Ihr Wechselgeld? bc1q…kyhg (0,00029 BTC)"
    other = groups[5]
    with pytest.raises(RuleError) as refused:
        answer(ws, narrower, 0)  # the payee is answered already
    assert refused.value.code == WALLET_CHANGE_ANSWERED
    (event,) = _rows(  # one answer, though it settled the payee on two cards
        dbname,
        "SELECT entity_id, payload FROM event WHERE type = 'wallet.payee_answered'",
    )
    assert event.entity_id == account
    assert event.payload["address_masked"] == "bc1q…atuf"
    assert len(event.payload["address_hmac"]) == 64
    assert PAYEE not in json.dumps(event.payload)

    # „Nein“ on the other output too: the spend is a transfer of both outputs plus its fee
    settled = answer(ws, narrower, 1)
    groups = _live_groups(dbname, account)
    assert (groups[3].id, groups[3].kind, groups[3].question) == (settled, "transfer", None)
    assert groups[5].id == other.id  # an answer that does not concern a card leaves it
    spent = [m for m in _movements(dbname, account) if m.tx_hash == txid(4)]
    assert [(m.index, m.quantity, m.address_id) for m in spent] == [
        ("fee", Decimal("-0.00000500"), a),
        ("net", Decimal("-0.00049500"), a),
    ]

    answer(ws, other.id, 1)  # the second card's change: a transfer too
    assert [g.kind for g in _live_groups(dbname, account)] == ["transfer"] * 6

    # a re-sync: nothing new, nothing asked again, and the account now reconciles
    sync(ws)
    assert len(_live_groups(dbname, account)) == 6
    runs = {r.address_id: r for r in _last_runs(dbname, account)}
    assert runs[b].reconciled is True and runs[b].cursor_after == "105"

    # a later spend to the same payee: only its unknown output is asked about
    esplora.records.append(record(5, 107, [(SECOND, 69_000)], [(PAYEE, 40_000), (CHANGE2, 28_000)]))
    esplora.tip = 115
    sync(ws)
    later = [g for g in _live_groups(dbname, account) if g.tx_hash == txid(5)]
    assert [(g.kind, g.question) for g in later] == [
        ("unknown", f"Ist diese Adresse Ihr Wechselgeld? bc1q…{CHANGE2[-4:]} (0,00028 BTC)")
    ]
    runs = {r.address_id: r for r in _last_runs(dbname, account)}
    assert runs[a].cursor_after == runs[b].cursor_after == "109"
    assert runs[b].reconciled is None  # waits for the new card

    # the known gap (§15.1): that run brought only the card, so it kept no original and the
    # answer cannot read the transaction again
    with pytest.raises(RuleError) as refused:
        answer(ws, later[0].id, 1)
    assert refused.value.code == WALLET_ORIGINAL_MISSING
