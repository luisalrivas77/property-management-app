"""Phase 6 D178 amended (3) under hausbuch_app and RLS: the importer writes each exchange
trade as one movement group (migration 0043: no chain, no transaction hash, kind swap), a
EUR fee on the acquisition's fee_eur, and `hausbuch group-exchange-trades` ties the trades
imported before it through the supersede path, changing no balance, check or card.
Invented texts only; the pure half is tests/test_exchange_trade_groups.py."""

from collections import Counter
from datetime import UTC, date, datetime, timedelta
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
from hausbuch.services import net_worth, review
from hausbuch.services.accounts import update_account
from hausbuch.services.exchange import imports
from hausbuch.services.prices.overview import assets_overview

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

    events = _events(read, ws, "movement_group.trade_recorded")
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
    assert len(_events(read, ws, "movement_group.trade_recorded")) == 2
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


def test_group_exchange_trades_skips_an_account_whose_provider_it_does_not_know(
    dbname, run, read, blob_store, fresh_workspaces, monkeypatch, capsys
):
    """PATCH /accounts takes any provider text; an exchange account renamed to one the trade
    rule does not know is skipped and named by its label, and the other accounts are still
    grouped (the rule itself stays strict)."""
    ws, _ = fresh_workspaces
    renamed = _exchange(run, ws, label="Zweites Konto")
    binance = _exchange(run, ws, provider="Binance")
    with monkeypatch.context() as patched:
        patched.setattr(exchange, "trade_groups", lambda movements, provider: [])
        text = next(c for c in BISON.parse_cases if c.id == "real_layout").text
        _upload(run, blob_store, ws, renamed.id, text.encode())
        _upload(run, blob_store, ws, binance.id, _zip(HEADER + ROWS), n=1)
    run(ws, update_account, renamed.id, provider="Bison App")

    result = run(ws, imports.group_stored_trades, now=T0 + timedelta(minutes=5))
    assert (result.groups, result.movements, result.skipped) == (2, 5, ("Zweites Konto",))
    assert {g.account_id for g in _all(read, ws, group_table)} == {binance.id}
    assert all(leg.group_id is None for leg in _legs(read, ws, renamed.id))

    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)}
    argv = ["group-exchange-trades", "--workspace", str(ws)]
    assert cli.main(argv, environ) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "grouped 0 exchange trade(s) of 0 movement(s)" in out
    assert "skipped the exchange account Zweites Konto" in out
    assert str(renamed.id) not in out


# Invented, fuller: a coin-for-coin trade (fee in BNB), a EUR buy whose fee is in the coin
# bought, a sale with its fee in EUR, a EUR buy with a EUR fee, and a BTC withdrawal.
FULL_ROWS = (
    "111111111,2026-03-01 09:00:00,Spot,Deposit,EUR,500,\n"
    "111111111,2026-03-01 09:05:00,Spot,Deposit,USDC,300,\n"
    "111111111,2026-03-01 09:10:00,Spot,Deposit,BNB,0.01,\n"
    "111111111,2026-03-02 10:15:00,Spot,Transaction Fee,BNB,-0.002,\n"
    "111111111,2026-03-02 10:15:00,Spot,Transaction Spend,USDC,-200,\n"
    "111111111,2026-03-02 10:15:00,Spot,Transaction Buy,SOL,1.5,\n"
    "111111111,2026-03-03 08:00:00,Spot,Transaction Fee,ETH,-0.00004,\n"
    "111111111,2026-03-03 08:00:00,Spot,Transaction Spend,EUR,-100,\n"
    "111111111,2026-03-03 08:00:00,Spot,Transaction Buy,ETH,0.04,\n"
    "111111111,2026-03-04 12:30:00,Spot,Transaction Fee,EUR,-0.1,\n"
    "111111111,2026-03-04 12:30:00,Spot,Transaction Sold,ETH,-0.02,\n"
    "111111111,2026-03-04 12:30:00,Spot,Transaction Revenue,EUR,51.2,\n"
    "111111111,2026-03-04 15:00:00,Spot,Transaction Fee,EUR,-0.25,\n"
    "111111111,2026-03-04 15:00:00,Spot,Transaction Spend,EUR,-50,\n"
    "111111111,2026-03-04 15:00:00,Spot,Transaction Buy,SOL,0.3,\n"
    "111111111,2026-03-05 12:20:00,Spot,Withdraw,BTC,-0.0105,Withdraw fee is included\n"
    "111111111,2026-03-05 12:00:00,Spot,Deposit,BTC,0.0105,\n"
)
WITHDRAWAL_HISTORY = (
    "Time,Coin,Network,Amount,Fee,Address,TXID,Status\n"
    "2026-03-05 12:05:00,BTC,BTC,0.0100,0.0005,"
    "bc1qinventedaddressqq7k3m9x2v5c8n4w6e0r1t3y5u7i9o0p,"
    "c8e9e7ce98e98cdbb4b0dd51563e5713145ad9b98617dcebe12676ee885902a0,Completed\n"
)
ON = date(2026, 11, 13)


def _figures(read, ws, account_ids):
    """Every figure the backfill must leave alone: each exchange page, the Anlagen overview,
    the net worth and Zu prüfen."""
    out: dict = {
        ("overview", a): read(ws, imports.exchange_overview, a).model_dump() for a in account_ids
    }
    out["assets"] = read(ws, assets_overview, ON).model_dump()
    out["net_worth"] = read(ws, net_worth.net_worth, ON).model_dump()
    out["cards"] = _cards(read, ws)
    return out


def _current(read, ws):
    m, a = movement_table.c, asset_table.c
    rows = read(
        ws,
        lambda c: c.execute(
            select(
                m.account_id,
                a.symbol,
                m.kind,
                m.quantity,
                m.eur_amount,
                m.fee_quantity,
                m.status,
                m.occurred_at,
                m.document_id,
                m.tx_hash,
                m.network,
                m.external_ref,
            )
            .join(asset_table, a.id == m.asset_id)
            .where(m.superseded_by.is_(None))
        ).all(),
    )
    return sorted(map(repr, rows))


def test_group_exchange_trades_changes_no_figure_on_binance_and_bison(
    run, read, blob_store, fresh_workspaces, monkeypatch
):
    ws, _ = fresh_workspaces
    binance = _exchange(run, ws, provider="Binance")
    bison = _exchange(run, ws)
    with monkeypatch.context() as patched:
        patched.setattr(exchange, "trade_groups", lambda movements, provider: [])
        binance_import = _upload(
            run, blob_store, ws, binance.id, _zip(HEADER + FULL_ROWS)
        ).movement_import
        text = next(c for c in BISON.parse_cases if c.id == "real_layout").text
        bison_import = _upload(run, blob_store, ws, bison.id, text.encode(), n=1).movement_import
    assert _all(read, ws, group_table) == []
    for account, imported in ((binance, binance_import), (bison, bison_import)):
        positions = read(ws, imports.exchange_overview, account.id).positions
        run(
            ws,
            imports.declare_holdings,
            imported.id,
            DeclareHoldingsBody(
                balances=[{"asset_id": p.asset_id, "units": str(p.units)} for p in positions]
            ),
            now=T0 + timedelta(minutes=5),
        )
    before = _figures(read, ws, [binance.id, bison.id])
    movements = _current(read, ws)

    run(ws, imports.group_stored_trades, now=T0 + timedelta(minutes=9))
    after = _figures(read, ws, [binance.id, bison.id])
    for key in before:
        assert after[key] == before[key], key
    assert _current(read, ws) == movements
    assert {g.account_id for g in _all(read, ws, group_table)} == {binance.id, bison.id}
    again = run(ws, imports.group_stored_trades, now=T0 + timedelta(minutes=10))
    assert (again.groups, again.movements, again.fees) == (0, 0, 0)

    # The withdrawal history uploaded after the backfill still completes the withdrawal.
    done = _upload(run, blob_store, ws, binance.id, _zip(WITHDRAWAL_HISTORY), n=2)
    assert (done.withdrawals_completed, done.withdrawals_open) == (1, 0)
