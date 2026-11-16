"""Phase 6 D178 amended (4), 16 Nov 2026, under hausbuch_app and RLS: a Binance export's times
moved to UTC by the zone its file names name, and the imports read as UTC before moved by
`repair_times` (`hausbuch repair-exchange-times`) or by the export uploaded again, append-only,
with trade groups, balance corrections, written deposits and transfer links following.

The lot book (§ the first test): EUR in, a coin bought at 14:57 and withdrawn at 15:04 as a
UTC+1 file writes them, the chain's receipt at 14:03 UTC linked by its hash. Read as UTC the
withdrawal stood after the receipt and before nothing (the purchase at „14:57 UTC“), so the
wallet's lot had no cost and the withdrawal was unfilled; moved to UTC, the wallet's lot
carries the purchase's cost and day.

Invented data only: the names follow Binance's pattern with invented stamps and ids, the
address is EIP-55's published example, the hash a SHA-256 of invented text."""

import hashlib
import io
import uuid
import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY, _admin, _insert, insert_event
from db.matching_support import World
from db.test_phase6_migration import _asset
from db.test_phase7_migration import EVM, _address, _wallet
from db.test_services_private_matching import Private
from db.test_services_transfers import _book
from db.test_wallet_sync_bsc_deposits import node  # noqa: F401 - the fake BSC node fixture
from psycopg.types.json import Jsonb
from sqlalchemy import func, select

from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import movement_group as group_table
from hausbuch.db.tables import movement_import as import_table
from hausbuch.db.tables import transfer_link as link_table
from hausbuch.rules import errors as E
from hausbuch.rules.review import NO_MATCH
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.bank import AccountCreate
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.household import (
    BalanceCorrectionBody,
    DeclareHoldingsBody,
    MovementKind,
    TransferLinkKind,
    TransferMatchedBy,
)
from hausbuch.services import holdings
from hausbuch.services.accounts import create_account
from hausbuch.services.chain import transfers as chain_transfers
from hausbuch.services.exchange import binance_csv, imports, times

pytestmark = pytest.mark.db

T0 = datetime(2026, 11, 16, 8, 0, tzinfo=UTC)
ON = date(2026, 3, 31)
HEADER = "User ID,Time,Account,Operation,Coin,Change,Remark\n"
# Written in UTC+1, as Binance writes the household's exports.
TX = HEADER + (
    "100000001,2026-03-02 10:00:00,Spot,Deposit,EUR,500,\n"
    "100000001,2026-03-02 14:57:10,Spot,Transaction Spend,EUR,-400,\n"
    "100000001,2026-03-02 14:57:10,Spot,Transaction Buy,ETH,0.2,\n"
    "100000001,2026-03-02 15:04:32,Spot,Withdraw,ETH,-0.2,Withdraw fee is included\n"
)
LATER_ROW = "100000001,2026-03-09 11:00:00,Spot,Deposit,EUR,50,\n"
TX_HASH = "0x" + hashlib.sha256(b"invented withdrawal to the own wallet").hexdigest()
WD = (
    "Time,Coin,Network,Amount,Fee,Address,TXID,Status\n"
    f"2026-03-02 14:59:00,ETH,ETH,0.1995,0.0005,{EVM},{TX_HASH},Completed\n"
)
TX_NAME = "Binance-Transaction-History-202611161200UTC1_100001.zip"
WD_NAME = "Binance-Withdraw-History-202611161200UTC1_100002.zip"
TX_MEMBER = "Transaction-History-202611161200(UTC+1)-part1-of1.csv"
BOUGHT = datetime(2026, 3, 2, 13, 57, 10, tzinfo=UTC)  # 14:57:10 in UTC+1
WITHDRAWN = datetime(2026, 3, 2, 14, 4, 32, tzinfo=UTC)  # 15:04:32 in UTC+1
RECEIVED = datetime(2026, 3, 2, 14, 3, 50, tzinfo=UTC)  # the chain's own time
HOUR = timedelta(hours=1)


def _zip(text: str, member: str = "export.csv") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        # A fixed member date, so the same text zips to the same bytes (the same document).
        info = zipfile.ZipInfo(member, date_time=(2026, 11, 16, 12, 0, 0))
        archive.writestr(info, text.replace("\n", "\r\n").encode())
    return buffer.getvalue()


def _binance(run, ws):
    return run(
        ws,
        create_account,
        AccountCreate(
            label="Binance-Konto",
            bank="Binance",
            kind=AccountKind.EXCHANGE,
            provider="Binance",
            opening_balance=Decimal("0.00"),
            opening_balance_on=date(2026, 1, 1),
        ),
        iban_key=TEST_IBAN_KEY,
        now=T0,
    )


def _upload(run, blob_store, ws, account_id, data, name, n=0):
    return run(
        ws,
        imports.import_export,
        blob_store,
        account_id,
        data,
        media_type="application/zip",
        original_filename=name,
        now=T0 + timedelta(minutes=n),
    )


def _legacy(monkeypatch):
    """An upload as before the amendment: no zone read from any name."""
    return monkeypatch.context()


def _no_zone(m):
    m.setattr(binance_csv, "utc_offset", lambda *_: None)


def _live(read, ws, account_id):
    m = movement_table.c
    return read(
        ws,
        lambda c: c.execute(
            select(movement_table)
            .where(m.account_id == account_id, m.superseded_by.is_(None))
            .order_by(m.occurred_at, m.kind, m.id)
        ).all(),
    )


def _times(read, ws, account_id):
    return sorted((r.kind, r.occurred_at) for r in _live(read, ws, account_id))


def _current_links(read, ws):
    from hausbuch.services.transfers import _current_links

    return read(ws, _current_links)


def _event_count(read, ws):
    return read(ws, lambda c: c.execute(select(func.count()).select_from(event_table)).scalar())


def _leaks(read, ws, *needles: str) -> list[str]:
    """The event types whose payload holds a needle (a file name's part or the hash): none may
    (the document's own document.uploaded keeps its file name, as before)."""
    e = event_table.c
    rows = read(
        ws,
        lambda c: c.execute(select(e.type, e.payload).where(e.type != "document.uploaded")).all(),
    )
    return sorted({t for t, p in rows for n in needles if n in str(p)})


def _repair(run, ws, n=30):
    return run(ws, times.repair_times, now=T0 + timedelta(minutes=n))


class Chain:
    """The own wallet's side, written as the superuser: a wallet with an Ethereum address, the
    chain's ETH priced as the exchange's, and a receipt group as a sync writes it."""

    def __init__(self, dbname: str, ws: uuid.UUID, plain_eth: uuid.UUID) -> None:
        self.dbname, self.ws = dbname, ws
        with _admin(dbname) as conn:
            self.wallet = _wallet(conn, ws, label="Hardware-Wallet")
            self.address = _address(conn, ws, self.wallet, chain="ethereum", address=EVM)
            self.eth = _asset(
                conn,
                ws,
                symbol="ETH",
                name="Ether",
                chain="ethereum",
                decimals=18,
                priced_as_asset_id=plain_eth,
            )

    def receipt(self, units: str, at: datetime, tx_hash: str | None = TX_HASH):
        tx_hash = tx_hash or "0x" + hashlib.sha256(uuid.uuid4().bytes).hexdigest()
        with _admin(self.dbname) as conn:
            group = _insert(
                conn,
                "movement_group",
                {
                    "workspace_id": self.ws,
                    "account_id": self.wallet,
                    "chain": "ethereum",
                    "tx_hash": tx_hash,
                    "occurred_at": at,
                    "kind": "exchange_transfer",
                    "status": "proposed",
                },
            )
            insert_event(
                conn,
                self.ws,
                type="movement_group.proposed",
                entity_type="movement_group",
                entity_id=group,
                actor_type="system",
                actor_id="sync_wallets",
                payload=Jsonb(
                    {
                        "account_id": str(self.wallet),
                        "chain": "ethereum",
                        "kind": "exchange_transfer",
                        "reason": NO_MATCH,
                    }
                ),
            )
            movement = _insert(
                conn,
                "asset_movement",
                {
                    "workspace_id": self.ws,
                    "account_id": self.wallet,
                    "asset_id": self.eth,
                    "kind": "internal_transfer_in",
                    "quantity": units,
                    "occurred_at": at,
                    "tx_hash": tx_hash,
                    "source": "chain",
                    "status": "proposed",
                    "group_id": group,
                    "address_id": self.address,
                    "index": "0",
                    "movement_hash": hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                },
            )
        return group, movement

    def pair(self, exchange_id: uuid.UUID, learned_from: uuid.UUID) -> None:
        """D261: the exchange and the address learned as a withdrawal pair."""
        with _admin(self.dbname) as conn:
            _insert(
                conn,
                "exchange_withdrawal_pair",
                {
                    "workspace_id": self.ws,
                    "account_id": exchange_id,
                    "address_id": self.address,
                    "learned_from": learned_from,
                },
            )


def _plain_eth(read, ws, account_id) -> uuid.UUID:
    overview = read(ws, imports.exchange_overview, account_id)
    return next(p.asset_id for p in overview.positions if p.symbol == "ETH")


# --- The lot book (the task's scenario) ----------------------------------------------------


def test_the_repair_gives_the_wallet_lot_its_purchase(
    run, read, blob_store, fresh_workspaces, dbname, monkeypatch
):
    ws, other = fresh_workspaces
    account = _binance(run, ws)
    with _legacy(monkeypatch) as m:
        _no_zone(m)
        imported = _upload(
            run, blob_store, ws, account.id, _zip(TX, TX_MEMBER), TX_NAME
        ).movement_import
        chain = Chain(dbname, ws, _plain_eth(read, ws, account.id))
        _, receipt = chain.receipt("0.1995", RECEIVED)
        done = _upload(run, blob_store, ws, account.id, _zip(WD), WD_NAME, n=1)
        assert done.withdrawals_completed == 1
    # Read as UTC: every time an hour late, the hash link made all the same.
    assert ("acquisition", BOUGHT + HOUR) in _times(read, ws, account.id)
    [link] = _current_links(read, ws)
    assert (link.in_movement_id, link.matched_by) == (receipt, TransferMatchedBy.MATCHER.value)
    ids = {r.symbol: r.asset_id for r in read(ws, imports.exchange_overview, account.id).positions}
    run(
        ws,
        imports.declare_holdings,
        imported.id,
        DeclareHoldingsBody(
            balances=[
                {"asset_id": ids["EUR"], "units": "100"},
                {"asset_id": ids["ETH"], "units": "0.0001"},
            ]
        ),
        now=T0 + timedelta(minutes=2),
    )
    run(
        ws,
        imports.book_correction,
        imported.id,
        BalanceCorrectionBody(asset_id=ids["ETH"], reason="Gutschrift fehlt im Export"),
        now=T0 + timedelta(minutes=3),
    )
    before = read(ws, holdings.crypto_holdings, ON)
    assert before.unfilled_movements == 1  # the withdrawal stood before the purchase
    assert read(ws, imports.exchange_overview, account.id).todos == [E.EXCHANGE_TIME_ZONE_UNKNOWN]

    result = _repair(run, ws)

    assert (result.accounts, result.corrected.imports, result.corrected.unknown) == (1, 1, 0)
    # The deposit, the purchase (its EUR leg on the row), the withdrawal and the correction:
    # each moved by one new row.
    assert result.corrected.movements == 4
    assert (result.linked, result.reopened, result.ended) == (0, 0, 0)
    moved = _times(read, ws, account.id)
    assert ("acquisition", BOUGHT) in moved
    assert ("internal_transfer_out", WITHDRAWN) in moved
    assert ("internal_transfer_in", datetime(2026, 3, 2, 9, 0, tzinfo=UTC)) in moved
    assert ("balance_correction", WITHDRAWN) in moved  # dated at the import's last row
    row = read(ws, lambda c: c.execute(select(import_table)).one())
    assert (row.first_at, row.last_at) == (
        datetime(2026, 3, 2, 9, 0, tzinfo=UTC),
        WITHDRAWN,
    )
    # The trade is still one group, superseded by one at the new time holding both legs.
    live = _live(read, ws, account.id)
    trade = {r.group_id for r in live if r.kind in ("acquisition", "disposal")}
    [group_id] = trade
    group = read(
        ws, lambda c: c.execute(select(group_table).where(group_table.c.id == group_id)).one()
    )
    assert (group.occurred_at, group.superseded_by) == (BOUGHT, None)
    # The hash link holds the live withdrawal and the same receipt, as the matcher wrote it.
    [new_link] = _current_links(read, ws)
    withdrawal = next(r for r in live if r.kind == MovementKind.INTERNAL_TRANSFER_OUT.value)
    assert (new_link.out_movement_id, new_link.in_movement_id) == (withdrawal.id, receipt)
    assert (new_link.kind, new_link.matched_by) == (
        TransferLinkKind.CHAIN.value,
        TransferMatchedBy.MATCHER.value,
    )
    assert withdrawal.tx_hash == TX_HASH and withdrawal.status == "confirmed"
    old = read(ws, lambda c: c.execute(select(link_table).where(link_table.c.id == link.id)).one())
    assert old.superseded_by == new_link.id
    # The lot book: the wallet's lot carries the purchase's day and cost, nothing unfilled.
    after = read(ws, holdings.crypto_holdings, ON)
    assert after.unfilled_movements == 0
    [coin] = [c for c in after.coins if c.account_id == chain.wallet]
    [lot] = coin.lots
    assert lot.acquired_at == BOUGHT and lot.acquired_on == date(2026, 3, 2)
    assert lot.cost_eur is not None and lot.cost_eur > 0
    assert lot.units_open == Decimal("0.1995")
    # The zone is known now: no to-do; one event names the import's offset, no name.
    assert read(ws, imports.exchange_overview, account.id).todos == []
    assert _leaks(read, ws, "UTC1", "UTC+1", "History", TX_HASH[2:], EVM) == []
    corrected = read(
        ws,
        lambda c: (
            c.execute(
                select(event_table.c.payload).where(
                    event_table.c.type == "account.movement_times_corrected"
                )
            )
            .scalars()
            .all()
        ),
    )
    assert [
        (p["utc_offset_minutes"], p["movements_corrected"], p["links_moved"]) for p in corrected
    ] == [(60, 4, 1)]

    # Idempotent: a second run moves nothing and writes nothing.
    count = _event_count(read, ws)
    again = _repair(run, ws, n=40)
    assert (again.corrected.imports, again.corrected.movements) == (0, 0)
    assert (again.linked, again.reopened, again.ended) == (0, 0, 0)
    assert _event_count(read, ws) == count
    assert _times(read, ws, account.id) == moved

    # The same export again is still its stored import; a longer one adds only its new row,
    # at its UTC time, and moves nothing it already holds.
    same = _upload(run, blob_store, ws, account.id, _zip(TX, TX_MEMBER), TX_NAME, n=50)
    assert same.movement_import.id == imported.id
    longer = _upload(
        run, blob_store, ws, account.id, _zip(TX + LATER_ROW, TX_MEMBER), "upload.zip", n=51
    ).movement_import
    assert (longer.rows_new, longer.rows_skipped) == (1, 3)
    assert ("internal_transfer_in", datetime(2026, 3, 9, 10, 0, tzinfo=UTC)) in _times(
        read, ws, account.id
    )
    assert len(_times(read, ws, account.id)) == len(moved) + 1
    assert read(ws, holdings.crypto_holdings, ON).unfilled_movements == 0
    # Workspace isolation.
    assert read(other, lambda c: c.execute(select(movement_table)).all()) == []


# --- An import whose names named no zone: the to-do and its answers ------------------------


def test_no_zone_named_is_read_as_utc_with_a_to_do(run, read, blob_store, fresh_workspaces):
    ws, _ = fresh_workspaces
    account = _binance(run, ws)
    upload = _upload(run, blob_store, ws, account.id, _zip(TX), "export.zip").movement_import
    assert ("acquisition", BOUGHT + HOUR) in _times(read, ws, account.id)
    assert read(ws, imports.exchange_overview, account.id).todos[0] == E.EXCHANGE_TIME_ZONE_UNKNOWN
    [payload] = read(
        ws,
        lambda c: (
            c.execute(
                select(event_table.c.payload).where(
                    event_table.c.type == "account.movements_imported"
                )
            )
            .scalars()
            .all()
        ),
    )
    assert (payload["utc_offset_minutes"], payload["time_zone_assumed"]) == (0, True)
    # The command cannot help: the stored name names nothing.
    result = _repair(run, ws)
    assert (result.corrected.imports, result.corrected.unknown) == (0, 1)
    assert ("acquisition", BOUGHT + HOUR) in _times(read, ws, account.id)

    # The same file uploaded again under Binance's own name: the import's times move.
    again = _upload(run, blob_store, ws, account.id, _zip(TX), TX_NAME, n=5).movement_import
    assert again.id == upload.id
    assert again.first_at == upload.first_at - HOUR and again.last_at == upload.last_at - HOUR
    assert ("acquisition", BOUGHT) in _times(read, ws, account.id)
    assert E.EXCHANGE_TIME_ZONE_UNKNOWN not in read(ws, imports.exchange_overview, account.id).todos


def test_a_later_export_naming_its_zone_moves_the_rows_it_shares(
    run, read, blob_store, fresh_workspaces
):
    ws, _ = fresh_workspaces
    account = _binance(run, ws)
    first = _upload(run, blob_store, ws, account.id, _zip(TX), "export.zip").movement_import
    longer = _upload(
        run, blob_store, ws, account.id, _zip(TX + LATER_ROW, TX_MEMBER), "upload.zip", n=1
    ).movement_import
    assert (longer.rows_new, longer.rows_skipped) == (1, 3)
    got = _times(read, ws, account.id)
    assert ("acquisition", BOUGHT) in got and ("internal_transfer_out", WITHDRAWN) in got
    assert ("internal_transfer_in", datetime(2026, 3, 9, 10, 0, tzinfo=UTC)) in got
    assert len(got) == 4
    row = read(
        ws, lambda c: c.execute(select(import_table).where(import_table.c.id == first.id)).one()
    )
    assert row.last_at == WITHDRAWN
    assert read(ws, imports.exchange_overview, account.id).todos[:1] != [
        E.EXCHANGE_TIME_ZONE_UNKNOWN
    ]
    [payload] = read(
        ws,
        lambda c: (
            c.execute(
                select(event_table.c.payload).where(
                    event_table.c.type == "account.movement_times_corrected"
                )
            )
            .scalars()
            .all()
        ),
    )
    assert payload["import_id"] == str(first.id) and payload["document_id"] == str(
        longer.document_id
    )


# --- Links: an EUR link and a learned withdrawal pair --------------------------------------


def test_an_eur_link_follows_its_moved_deposit(
    app_engine, fresh_workspaces, blob_store, monkeypatch
):
    world = World(app_engine, fresh_workspaces[0], now=T0)
    private = Private(world)
    out = private.line(date(2026, 3, 1), "-500.00", purpose="Binance Einzahlung")
    _book(world, out, "-500.00", C.INTERNAL_TRANSFER_OUT)
    account = world.cmd(
        create_account,
        AccountCreate(
            label="Binance",
            bank="Binance",
            kind=AccountKind.EXCHANGE,
            provider="Binance",
            opening_balance=Decimal("0.00"),
            opening_balance_on=date(2026, 1, 1),
        ),
        iban_key=TEST_IBAN_KEY,
    )
    with monkeypatch.context() as m:
        _no_zone(m)
        world.cmd(
            imports.import_export,
            blob_store,
            account.id,
            _zip(TX, TX_MEMBER),
            media_type="application/zip",
            original_filename=TX_NAME,
        )
    [link] = world.query(lambda c: c.execute(select(link_table)).all())
    assert link.kind == TransferLinkKind.EXCHANGE_EUR.value and link.out_transaction_id == out

    result = world.cmd(times.repair_times)

    assert result.corrected.imports == 1 and (result.linked, result.ended) == (0, 0)
    from hausbuch.services.transfers import _current_links

    [current] = world.query(_current_links)
    deposit = world.query(
        lambda c: c.execute(
            select(movement_table).where(
                movement_table.c.kind == MovementKind.INTERNAL_TRANSFER_IN.value,
                movement_table.c.superseded_by.is_(None),
            )
        ).one()
    )
    assert deposit.occurred_at == datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
    assert (current.out_transaction_id, current.in_movement_id) == (out, deposit.id)
    assert (current.kind, current.matched_by) == (link.kind, link.matched_by)
    assert current.id != link.id


@pytest.mark.parametrize(
    ("received", "linked"),
    [
        # Five minutes after Binance's stamp: read as UTC the stamp stood 55 minutes after the
        # receipt and the learned pair refused it; moved, it links.
        (WITHDRAWN + timedelta(minutes=5), 1),
        # 42 seconds before the stamp, as the live data's chain receipts stand: D261 (3) asks
        # the receipt at or after the withdrawal, so it still refuses (unchanged here).
        (RECEIVED, 0),
    ],
    ids=["receipt_after_the_stamp", "receipt_before_the_stamp"],
)
def test_a_learned_withdrawal_pair_after_the_repair(
    run, read, blob_store, fresh_workspaces, dbname, monkeypatch, received, linked
):
    ws, _ = fresh_workspaces
    account = _binance(run, ws)
    no_fee = TX.replace("Withdraw fee is included", "")
    with _legacy(monkeypatch) as m:
        _no_zone(m)
        _upload(run, blob_store, ws, account.id, _zip(no_fee, TX_MEMBER), TX_NAME)
    chain = Chain(dbname, ws, _plain_eth(read, ws, account.id))
    withdrawal = next(
        r for r in _live(read, ws, account.id) if r.kind == MovementKind.INTERNAL_TRANSFER_OUT.value
    )
    chain.pair(account.id, withdrawal.id)
    chain.receipt("0.2", received, tx_hash=None)
    assert run(ws, chain_transfers.relink, now=T0).linked == 0  # an hour apart

    result = _repair(run, ws)

    assert result.linked == linked
    links = _current_links(read, ws)
    assert len(links) == linked
    if linked:
        [code] = links
        live = next(
            r
            for r in _live(read, ws, account.id)
            if r.kind == MovementKind.INTERNAL_TRANSFER_OUT.value
        )
        assert code.out_movement_id == live.id and live.occurred_at == WITHDRAWN
        reason = read(
            ws,
            lambda c: c.execute(
                select(event_table.c.payload["reason"].astext).where(
                    event_table.c.entity_id == code.id, event_table.c.type == "transfer.linked"
                )
            ).scalar_one(),
        )
        assert reason == "withdrawal_pair"
        assert read(ws, chain_transfers.learned_links, [live.id]) == {
            live.id: code.id,
            code.in_movement_id: code.id,
        }


# --- A deposit the app wrote for a withdrawal on a balance-only chain ----------------------


def test_a_written_deposit_moves_with_its_withdrawal(
    dbname,
    app_engine,
    blob_store,
    fresh_workspaces,
    node,  # noqa: F811 - the fixture imported above
    run,
    read,
    monkeypatch,
):
    """Phase 7 D181 (c) amended, two Binance withdrawals to the own BSC address, both completed
    by the history. The node knows the first hash: its deposit is written at the block's own
    time, which is UTC already, so the repair leaves it alone. The node never knew the second
    hash, so a person picked the address and the deposit was written at the withdrawal's time.
    The repair moves that deposit with the withdrawal (a new group, still routed as written)
    and keeps both links. Neither the relink nor the next sync ends a link or writes a second
    deposit."""
    from db.test_wallet_sync_bsc_deposits import (
        BNB,
        OWN,
        Seed,
        _deposits,
        _hash,
        _links,
        _own_wallet,
        _run,
    )

    from hausbuch.services.exchange.imports import confirm_movement

    ws, _ = fresh_workspaces
    seed = Seed(dbname, ws)
    binance = _binance(run, ws).id
    tx_hash = _hash("binance bsc withdrawal in UTC+1")
    unknown_hash = _hash("a hash the node never saw")
    files = [
        (
            HEADER + "100000001,2026-05-04 11:00:20,Spot,Withdraw,BNB,-2.001,"
            "Withdraw fee is included\n"
            "100000001,2026-05-04 13:00:00,Spot,Withdraw,BNB,-1.001,"
            "Withdraw fee is included\n",
            "Transaction-History-202611161200(UTC+1)-part1-of1.csv",
        ),
        (
            "Time,Coin,Network,Amount,Fee,Address,TXID,Status\n"
            f"2026-05-04 10:55:00,BNB,BSC,2.000,0.001,{OWN},{tx_hash},Completed\n"
            f"2026-05-04 12:55:00,BNB,BSC,1.000,0.001,{OWN},{unknown_hash},Completed\n",
            "Withdraw-History-202611161200(UTC+1)-part1-of1.csv",
        ),
    ]
    with _legacy(monkeypatch) as m:
        _no_zone(m)
        for n, (text, name) in enumerate(files):
            run(
                ws,
                imports.import_export,
                blob_store,
                binance,
                text.replace("\n", "\r\n").encode(),
                media_type="text/csv",
                original_filename=name,
                now=T0 + timedelta(minutes=n),
            )
    rows = read(ws, imports.exchange_movements, binance)
    for row in rows:
        run(ws, lambda ctx, row=row: confirm_movement(ctx, row.id))
    hashed, picked = sorted(rows, key=lambda r: r.occurred_at)
    node.send(tx_hash, OWN, 2 * BNB)
    node.native = 3 * BNB
    _run(app_engine, blob_store, ws, seed.wallet)  # months later: the unknown hash remembered
    run(ws, _own_wallet(picked.id, seed.address, remember=False))
    assert len(_deposits(dbname, seed.address)) == 2 and len(_links(dbname, ws)) == 2
    block = datetime(2026, 5, 4, 10, 0, 30, tzinfo=UTC)  # the fake node's block time
    stamp = datetime(2026, 5, 4, 13, 0, tzinfo=UTC)  # the second withdrawal read as UTC

    def deposits():
        m = movement_table.c
        return {
            r.tx_hash: r
            for r in read(
                ws,
                lambda c: c.execute(
                    select(movement_table).where(
                        m.address_id == seed.address, m.superseded_by.is_(None)
                    )
                ).all(),
            )
        }

    before = deposits()
    assert before[tx_hash].occurred_at == block and before[unknown_hash].occurred_at == stamp

    result = _repair(run, ws)

    # The two withdrawals, and the deposit written at the second one's time.
    assert result.corrected.movements == 3
    assert (result.linked, result.reopened, result.ended) == (0, 0, 0)
    after = deposits()
    assert after[tx_hash].id == before[tx_hash].id  # the block's time is true already
    moved = after[unknown_hash]
    assert moved.id != before[unknown_hash].id and moved.occurred_at == stamp - HOUR
    assert moved.group_id != before[unknown_hash].group_id and moved.status == "confirmed"
    group = read(
        ws,
        lambda c: c.execute(select(group_table).where(group_table.c.id == moved.group_id)).one(),
    )
    assert (group.occurred_at, group.tx_hash, group.status) == (
        stamp - HOUR,
        unknown_hash,
        "confirmed",
    )
    live = {r.tx_hash: r for r in read(ws, imports.exchange_movements, binance)}
    assert live[unknown_hash].occurred_at == stamp - HOUR
    links = {in_id: (out_id, matched_by) for out_id, in_id, matched_by, _ in _links(dbname, ws)}
    assert links[moved.id][0] == live[unknown_hash].id
    assert links[after[tx_hash].id][0] == live[tx_hash].id
    assert len(links) == 2
    # The chain's relink and the next sync find both pairs linked: nothing ended, no third.
    assert run(ws, chain_transfers.relink).ended == 0
    _run(app_engine, blob_store, ws, seed.wallet, day=1)
    assert {r.id for r in deposits().values()} == {after[tx_hash].id, moved.id}
    assert len(_links(dbname, ws)) == 2


# --- The command ---------------------------------------------------------------------------


def test_the_command_counts_on_a_dry_run_and_changes_nothing(
    run, read, blob_store, fresh_workspaces, dbname, monkeypatch, capsys
):
    """`hausbuch repair-exchange-times --dry-run` reports what the real run then does, and
    rolls everything back; neither run prints a file name, an address or a hash."""
    from db.test_cli_evals_report import _owner_url

    from hausbuch import cli

    ws, _ = fresh_workspaces
    account = _binance(run, ws)
    with _legacy(monkeypatch) as m:
        _no_zone(m)
        _upload(run, blob_store, ws, account.id, _zip(TX, TX_MEMBER), TX_NAME)
        chain = Chain(dbname, ws, _plain_eth(read, ws, account.id))
        chain.receipt("0.1995", RECEIVED)
        _upload(run, blob_store, ws, account.id, _zip(WD), WD_NAME, n=1)
    times_before = _times(read, ws, account.id)
    links_before = [r.id for r in _current_links(read, ws)]
    events_before = _event_count(read, ws)
    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)}
    argv = ["repair-exchange-times", "--workspace", str(ws)]

    assert cli.main([*argv, "--dry-run"], environ) == cli.EXIT_OK
    dry = capsys.readouterr().out
    assert "would move 3 movement(s) of 1 Binance import(s) to UTC" in dry
    assert "would then link 0 transfer(s); reopened 0, ended 0" in dry
    assert _times(read, ws, account.id) == times_before
    assert [r.id for r in _current_links(read, ws)] == links_before
    assert _event_count(read, ws) == events_before
    assert read(ws, imports.exchange_overview, account.id).todos == [E.EXCHANGE_TIME_ZONE_UNKNOWN]

    assert cli.main(argv, environ) == cli.EXIT_OK
    real = capsys.readouterr().out
    assert "moved 3 movement(s) of 1 Binance import(s) to UTC" in real
    assert ("acquisition", BOUGHT) in _times(read, ws, account.id)
    assert read(ws, imports.exchange_overview, account.id).todos == []
    assert cli.main(argv, environ) == cli.EXIT_OK
    assert "moved 0 movement(s) of 0 Binance import(s)" in capsys.readouterr().out
    for text in (dry, real):
        for needle in ("UTC1", "UTC+1", "History", TX_HASH[2:], EVM):
            assert needle not in text


# --- The normal path after the amendment ---------------------------------------------------


def test_zone_named_files_complete_their_withdrawal_at_utc(
    run, read, blob_store, fresh_workspaces, dbname
):
    """No repair needed: both of Binance's own ZIPs name their zone, the withdrawal stands at
    its UTC time, the history completes it and the hash links the chain's receipt; the wallet's
    lot carries the purchase."""
    ws, _ = fresh_workspaces
    account = _binance(run, ws)
    _upload(run, blob_store, ws, account.id, _zip(TX, TX_MEMBER), TX_NAME)
    chain = Chain(dbname, ws, _plain_eth(read, ws, account.id))
    _, receipt = chain.receipt("0.1995", RECEIVED)
    done = _upload(run, blob_store, ws, account.id, _zip(WD), WD_NAME, n=1)
    assert done.withdrawals_completed == 1
    withdrawal = next(
        r for r in _live(read, ws, account.id) if r.kind == MovementKind.INTERNAL_TRANSFER_OUT.value
    )
    assert (withdrawal.occurred_at, withdrawal.tx_hash) == (WITHDRAWN, TX_HASH)
    [link] = _current_links(read, ws)
    assert (link.out_movement_id, link.in_movement_id) == (withdrawal.id, receipt)
    assert read(ws, imports.exchange_overview, account.id).todos == []
    after = read(ws, holdings.crypto_holdings, ON)
    assert after.unfilled_movements == 0
    [coin] = [c for c in after.coins if c.account_id == chain.wallet]
    assert [lot.acquired_at for lot in coin.lots] == [BOUGHT]
    assert _repair(run, ws).corrected.movements == 0


def test_a_renamed_history_after_the_repair_is_read_in_the_import_s_zone(
    run, read, blob_store, fresh_workspaces, monkeypatch
):
    """The withdrawal history uploaded under a name that names no zone, onto an import the
    repair has moved: it is read in that import's zone (the account's known offset), so its row
    still stands within the hour before the moved withdrawal and completes it."""
    ws, _ = fresh_workspaces
    account = _binance(run, ws)
    with _legacy(monkeypatch) as m:
        _no_zone(m)
        _upload(run, blob_store, ws, account.id, _zip(TX, TX_MEMBER), TX_NAME)
    assert _repair(run, ws).corrected.imports == 1
    done = _upload(run, blob_store, ws, account.id, _zip(WD), "history.zip", n=40)
    assert done.withdrawals_completed == 1
    withdrawal = next(
        r for r in _live(read, ws, account.id) if r.kind == MovementKind.INTERNAL_TRANSFER_OUT.value
    )
    assert (withdrawal.occurred_at, withdrawal.tx_hash) == (WITHDRAWN, TX_HASH)
    assert withdrawal.fee_quantity == Decimal("0.0005")


# --- Review fixes (16 Nov 2026) --------------------------------------------------------------


def _unreconciled(run, read, ws, account_id, import_id, n=2):
    """The exchange's balances typed for the import with one ETH short, so a correction may
    be booked on it."""
    ids = {r.symbol: r.asset_id for r in read(ws, imports.exchange_overview, account_id).positions}
    run(
        ws,
        imports.declare_holdings,
        import_id,
        DeclareHoldingsBody(
            balances=[
                {"asset_id": ids["EUR"], "units": "100"},
                {"asset_id": ids["ETH"], "units": "0.0001"},
            ]
        ),
        now=T0 + timedelta(minutes=n),
    )
    return ids


def _correct(run, ws, import_id, ids, n=3):
    return run(
        ws,
        imports.book_correction,
        import_id,
        BalanceCorrectionBody(asset_id=ids["ETH"], reason="Gutschrift fehlt im Export"),
        now=T0 + timedelta(minutes=n),
    )


def test_an_export_whose_rows_are_all_stored_never_asks_for_its_zone(
    run, read, blob_store, fresh_workspaces
):
    """Review A: the zone-named ZIP, then the same rows as a CSV whose name names no zone. The
    CSV writes no row, so it owns nothing to move: no to-do, nothing unknown for the command,
    and its bounds are the stored rows' (UTC), so it is not later than the ZIP's import."""
    ws, _ = fresh_workspaces
    account = _binance(run, ws)
    first = _upload(run, blob_store, ws, account.id, _zip(TX, TX_MEMBER), TX_NAME).movement_import
    plain = run(
        ws,
        imports.import_export,
        blob_store,
        account.id,
        TX.replace("\n", "\r\n").encode(),
        media_type="text/csv",
        original_filename="export.csv",
        now=T0 + timedelta(minutes=1),
    ).movement_import
    assert (plain.rows_new, plain.rows_skipped) == (0, 3)
    assert (plain.first_at, plain.last_at) == (first.first_at, first.last_at)
    assert E.EXCHANGE_TIME_ZONE_UNKNOWN not in read(ws, imports.exchange_overview, account.id).todos
    assert _repair(run, ws).corrected.unknown == 0
    ids = _unreconciled(run, read, ws, account.id, first.id)
    assert _correct(run, ws, first.id, ids).status.value == "reconciled"


def test_an_export_whose_rows_are_all_stored_follows_their_repair(
    run, read, blob_store, fresh_workspaces, monkeypatch
):
    """Review A, the other order: an import read as UTC, then the same rows under a name that
    names no zone, both before the amendment. The second's bounds are the stored rows' file
    time; the repair moves those rows and the second's bounds with them, so a correction on the
    first is still accepted."""
    ws, _ = fresh_workspaces
    account = _binance(run, ws)
    with _legacy(monkeypatch) as m:
        _no_zone(m)
        first = _upload(
            run, blob_store, ws, account.id, _zip(TX, TX_MEMBER), TX_NAME
        ).movement_import
        plain = _upload(run, blob_store, ws, account.id, _zip(TX), "export.zip", n=1)
    assert plain.movement_import.rows_new == 0
    assert plain.movement_import.last_at == first.last_at  # file time, both

    result = _repair(run, ws)

    assert (result.corrected.imports, result.corrected.unknown) == (1, 0)
    rows = {r.id: r for r in read(ws, lambda c: c.execute(select(import_table)).all())}
    assert rows[plain.movement_import.id].last_at == rows[first.id].last_at == WITHDRAWN
    assert rows[plain.movement_import.id].first_at == rows[first.id].first_at
    assert E.EXCHANGE_TIME_ZONE_UNKNOWN not in read(ws, imports.exchange_overview, account.id).todos
    ids = _unreconciled(run, read, ws, account.id, first.id, n=40)
    assert _correct(run, ws, first.id, ids, n=41).status.value == "reconciled"


def test_the_same_file_again_links_what_its_upload_moved(
    run, read, blob_store, fresh_workspaces, dbname, monkeypatch
):
    """Review B: an import read as UTC under Binance's own name, a learned withdrawal pair
    (D261) and the chain's receipt five minutes after the true time. The same bytes uploaded
    again are the stored import, but the upload first moved its rows by the stored name; it
    links them as an import does, so the pair links the moved withdrawal."""
    ws, _ = fresh_workspaces
    account = _binance(run, ws)
    no_fee = TX.replace("Withdraw fee is included", "")
    with _legacy(monkeypatch) as m:
        _no_zone(m)
        first = _upload(
            run, blob_store, ws, account.id, _zip(no_fee, TX_MEMBER), TX_NAME
        ).movement_import
    chain = Chain(dbname, ws, _plain_eth(read, ws, account.id))
    withdrawal = next(
        r for r in _live(read, ws, account.id) if r.kind == MovementKind.INTERNAL_TRANSFER_OUT.value
    )
    chain.pair(account.id, withdrawal.id)
    chain.receipt("0.2", WITHDRAWN + timedelta(minutes=5), tx_hash=None)
    assert run(ws, chain_transfers.relink, now=T0).linked == 0  # an hour apart

    again = _upload(run, blob_store, ws, account.id, _zip(no_fee, TX_MEMBER), TX_NAME, n=5)

    assert again.movement_import.id == first.id
    assert again.movement_import.last_at == WITHDRAWN
    [link] = _current_links(read, ws)
    live = next(
        r for r in _live(read, ws, account.id) if r.kind == MovementKind.INTERNAL_TRANSFER_OUT.value
    )
    assert link.out_movement_id == live.id and live.occurred_at == WITHDRAWN
    assert run(ws, chain_transfers.relink, now=T0 + HOUR).linked == 0  # nothing left to link
