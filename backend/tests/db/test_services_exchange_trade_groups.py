"""Phase 6 D178 amended (3) under hausbuch_app and RLS: the importer writes each exchange
trade as one movement group (migration 0043: no chain, no transaction hash, kind swap), a
EUR fee on the acquisition's fee_eur, and `hausbuch group-exchange-trades` ties the trades
imported before it through the supersede path, changing no balance, check or card.
Invented texts only; the pure half is tests/test_exchange_trade_groups.py."""

from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import fixture_models_phase6 as fixtures
import pytest
from db.test_cli_evals_report import _owner_url
from db.test_services_exchange_import import T0, _exchange, _upload, _zip
from sqlalchemy import select

from hausbuch import cli
from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import movement_group as group_table
from hausbuch.rules import exchange
from hausbuch.schemas.enums import ImportStatus
from hausbuch.schemas.household import DeclareHoldingsBody, MovementKind
from hausbuch.services import review
from hausbuch.services.exchange import imports

pytestmark = pytest.mark.db

BISON = fixtures.load("bison_csv")
HEADER = "User ID,Time,Account,Operation,Coin,Change,Remark\n"
# Invented: deposits, a SOL bought with USDC (fee in BNB), an ETH bought for EUR (fee in EUR).
ROWS = (
    "111111111,2026-03-01 09:00:00,Spot,Deposit,EUR,500,\n"
    "111111111,2026-03-01 09:05:00,Spot,Deposit,USDC,300,\n"
    "111111111,2026-03-01 09:10:00,Spot,Deposit,BNB,0.01,\n"
    "111111111,2026-03-02 10:15:00,Spot,Transaction Fee,BNB,-0.002,\n"
    "111111111,2026-03-02 10:15:00,Spot,Transaction Spend,USDC,-200,\n"
    "111111111,2026-03-02 10:15:00,Spot,Transaction Buy,SOL,1.5,\n"
    "111111111,2026-03-03 08:00:00,Spot,Transaction Fee,EUR,-0.25,\n"
    "111111111,2026-03-03 08:00:00,Spot,Transaction Spend,EUR,-100,\n"
    "111111111,2026-03-03 08:00:00,Spot,Transaction Buy,ETH,0.04,\n"
)
LATER_TRADE = (
    "111111111,2026-03-10 14:00:00,Spot,Transaction Spend,EUR,-50,\n"
    "111111111,2026-03-10 14:00:00,Spot,Transaction Buy,SOL,0.3,\n"
)
# The two trades' legs, each group sorted by symbol.
TRADES = [
    [("BNB", MovementKind.FEE), ("SOL", MovementKind.ACQUISITION), ("USDC", MovementKind.DISPOSAL)],
    [("ETH", MovementKind.ACQUISITION), ("EUR", MovementKind.FEE)],
]
COIN_FOR_COIN_AT = datetime(2026, 3, 2, 10, 15, tzinfo=UTC)
EUR_BUY_AT = datetime(2026, 3, 3, 8, 0, tzinfo=UTC)


def _all(read, ws, table):
    return read(ws, lambda c: c.execute(select(table)).all())


def _legs(read, ws, account_id):
    """The current movements of the account with their symbol."""
    m, a = movement_table.c, asset_table.c
    return read(
        ws,
        lambda c: c.execute(
            select(movement_table, a.symbol)
            .join(asset_table, a.id == m.asset_id)
            .where(m.account_id == account_id, m.superseded_by.is_(None))
            .order_by(m.occurred_at, a.symbol)
        ).all(),
    )


def _events(read, ws, kind):
    ev = event_table.c
    return read(
        ws,
        lambda c: c.execute(select(event_table).where(ev.type == kind).order_by(ev.seq)).all(),
    )


def _by_group(legs):
    out: dict = {}
    for leg in legs:
        if leg.group_id is not None:
            out.setdefault(leg.group_id, []).append((leg.symbol, MovementKind(leg.kind)))
    return {gid: sorted(v) for gid, v in out.items()}


def _amounts(legs):
    return Counter(
        (leg.symbol, leg.kind, leg.quantity, leg.eur_amount, leg.status, leg.occurred_at)
        for leg in legs
    )


def _cards(read, ws):
    return sorted(repr(i.model_dump()) for i in read(ws, review.inbox))


def test_a_binance_export_writes_each_trade_as_one_group(run, read, blob_store, fresh_workspaces):
    ws, _ = fresh_workspaces
    account = _exchange(run, ws, provider="Binance")
    upload = _upload(run, blob_store, ws, account.id, _zip(HEADER + ROWS))
    legs = _legs(read, ws, account.id)

    groups = _all(read, ws, group_table)
    assert sorted(
        (g.account_id, g.chain, g.tx_hash, g.kind, g.status, g.occurred_at) for g in groups
    ) == [
        (account.id, None, None, "swap", "confirmed", COIN_FOR_COIN_AT),
        (account.id, None, None, "swap", "confirmed", EUR_BUY_AT),
    ]
    assert sorted(_by_group(legs).values()) == TRADES
    # The deposits are in no group.
    assert {leg.kind for leg in legs if leg.group_id is None} == {
        "internal_transfer_in",
        "cashback_reward",
    }
    by_symbol = {(leg.symbol, leg.kind): leg for leg in legs}
    eth = by_symbol[("ETH", "acquisition")]
    assert (eth.eur_amount, eth.fee_eur) == (Decimal("-100.00"), Decimal("0.25"))
    assert by_symbol[("EUR", "fee")].quantity == Decimal("-0.25")  # the EUR fee stays
    sol = by_symbol[("SOL", "acquisition")]
    assert (sol.eur_amount, sol.fee_eur) == (None, None)

    events = _events(read, ws, "movement_group.confirmed")
    assert sorted(e.entity_id for e in events) == sorted(g.id for g in groups)
    assert {e.entity_type for e in events} == {"movement_group"}
    ids_of = {gid: {leg.id for leg in legs if leg.group_id == gid} for gid in _by_group(legs)}
    for e in events:
        assert {str(i) for i in ids_of[e.entity_id]} == set(e.payload["movement_ids"])
        assert e.payload["kind"] == "swap"
        assert e.payload["document_id"] == str(upload.movement_import.document_id)


def test_a_bison_buy_is_a_group_of_its_one_row(run, read, blob_store, fresh_workspaces):
    ws, _ = fresh_workspaces
    account = _exchange(run, ws)
    text = next(c for c in BISON.parse_cases if c.id == "real_layout").text
    _upload(run, blob_store, ws, account.id, text.encode())
    legs = _legs(read, ws, account.id)
    [group] = _all(read, ws, group_table)
    assert (group.chain, group.tx_hash, group.kind) == (None, None, "swap")
    assert list(_by_group(legs).values()) == [[("BTC", MovementKind.ACQUISITION)]]
    [buy] = [leg for leg in legs if leg.group_id is not None]
    assert buy.eur_amount is not None and buy.fee_eur is None


def test_group_exchange_trades_ties_earlier_imports_once_and_changes_no_check(
    dbname, run, read, blob_store, fresh_workspaces, monkeypatch, capsys
):
    ws, _ = fresh_workspaces
    account = _exchange(run, ws, provider="Binance")
    # The import as it was before migration 0043: no group and no fee_eur.
    with monkeypatch.context() as patched:
        patched.setattr(exchange, "trade_groups", lambda movements, provider: [])
        imported = _upload(run, blob_store, ws, account.id, _zip(HEADER + ROWS)).movement_import
    before = _legs(read, ws, account.id)
    assert all(leg.group_id is None and leg.fee_eur is None for leg in before)
    assert _all(read, ws, group_table) == []
    positions = read(ws, imports.exchange_overview, account.id).positions
    checked = run(
        ws,
        imports.declare_holdings,
        imported.id,
        DeclareHoldingsBody(
            balances=[{"asset_id": p.asset_id, "units": str(p.units)} for p in positions]
        ),
        now=T0 + timedelta(minutes=5),
    )
    assert checked.status is ImportStatus.RECONCILED
    positions = read(ws, imports.exchange_overview, account.id).positions
    assert all(p.reconciled for p in positions)
    cards = _cards(read, ws)

    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)}
    argv = ["group-exchange-trades", "--workspace", str(ws)]
    assert cli.main([*argv, "--dry-run"], environ) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "would group 2 exchange trade(s) of 5 movement(s)" in out
    assert "would give 1 acquisition(s) the fee their trade paid in EUR" in out
    assert _all(read, ws, group_table) == []
    assert _legs(read, ws, account.id) == before

    assert cli.main(argv, environ) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "grouped 2 exchange trade(s) of 5 movement(s)" in out
    assert "gave 1 acquisition(s)" in out and "postgresql" not in out
    after = _legs(read, ws, account.id)
    assert sorted(_by_group(after).values()) == TRADES
    assert _amounts(after) == _amounts(before)  # quantities, EUR legs and statuses unchanged
    [eth] = [leg for leg in after if (leg.symbol, leg.kind) == ("ETH", "acquisition")]
    assert eth.fee_eur == Decimal("0.25")
    assert eth.document_id == imported.document_id
    assert len(_events(read, ws, "movement.superseded")) == 5
    assert len(_events(read, ws, "movement_group.confirmed")) == 2
    # The typed balances still agree, and Zu prüfen shows what it showed.
    [view] = read(ws, imports.exchange_overview, account.id).imports
    assert view.status is ImportStatus.RECONCILED
    assert read(ws, imports.exchange_overview, account.id).positions == positions
    assert _cards(read, ws) == cards

    assert cli.main(argv, environ) == cli.EXIT_OK  # a second run finds nothing
    assert "grouped 0 exchange trade(s) of 0 movement(s)" in capsys.readouterr().out
    assert len(_all(read, ws, group_table)) == 2

    # A longer export later: the stored rows are still recognised, only the new trade is
    # written, in its own group.
    longer = _upload(run, blob_store, ws, account.id, _zip(HEADER + ROWS + LATER_TRADE), n=1)
    assert (longer.movement_import.rows_new, longer.movement_import.rows_skipped) == (1, 8)
    groups = _all(read, ws, group_table)
    assert len(groups) == 3
    [new] = [leg for leg in _legs(read, ws, account.id) if leg.occurred_at.day == 10]
    assert new.group_id is not None and new.group_id not in {g for g in _by_group(after)}
