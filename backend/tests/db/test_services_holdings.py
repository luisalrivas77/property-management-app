"""Phase 7 step 6: lots, the Krypto tab and the holdings statement as hausbuch_app under RLS
(docs/phases/phase-7.md D188, D190, §13). Invented accounts, coins, addresses and prices only;
the masked addresses are holdings_statement.yaml's own."""

import hashlib
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from fixture_models_phase7 import load
from sqlalchemy import insert

from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import price as price_table
from hausbuch.db.tables import transfer_link as link_table
from hausbuch.db.tables import wallet_address as address_table
from hausbuch.db.tables import wallet_sync as sync_table
from hausbuch.rules.lots import tax_free_from
from hausbuch.schemas.bank import AccountCreate
from hausbuch.schemas.enums import AccountKind
from hausbuch.services import holdings

pytestmark = pytest.mark.db

NOW = datetime(2027, 1, 5, 9, 0, tzinfo=UTC)


def _account(ctx, kind: AccountKind, label: str) -> uuid.UUID:
    from hausbuch.services.accounts import create_account

    return create_account(
        ctx,
        AccountCreate(
            label=label,
            bank="Testanbieter",
            kind=kind,
            provider="Binance" if kind is AccountKind.EXCHANGE else "Testwallet",
            opening_balance=Decimal("0.00"),
            opening_balance_on=date(2025, 1, 1),
        ),
        iban_key=TEST_IBAN_KEY,
    ).id


def _asset(ctx, symbol: str | None, name: str, *, chain=None, spam=False, priced_as=None):
    return ctx.conn.execute(
        insert(asset_table)
        .values(
            workspace_id=ctx.workspace_id,
            kind="crypto",
            symbol=symbol,
            name=name,
            chain=chain,
            contract_address=None
            if chain != "ethereum" or symbol == "ETH"
            else "0x" + hashlib.sha256(name.encode()).hexdigest()[:40],
            spam=spam,
            priced_as_asset_id=priced_as,
        )
        .returning(asset_table.c.id)
    ).scalar_one()


def _address(ctx, account_id, chain: str, masked: str) -> uuid.UUID:
    return ctx.conn.execute(
        insert(address_table)
        .values(
            workspace_id=ctx.workspace_id,
            account_id=account_id,
            chain=chain,
            address=("0x" + "ab" * 20) if chain == "ethereum" else "So1" + "x" * 40,
            address_masked=masked,
            address_hmac=hashlib.sha256(masked.encode()).digest(),
        )
        .returning(address_table.c.id)
    ).scalar_one()


def _movement(ctx, account_id, asset_id, units, at, **extra) -> uuid.UUID:
    chain = extra.pop("address_id", None)
    values = {
        "workspace_id": ctx.workspace_id,
        "account_id": account_id,
        "asset_id": asset_id,
        "kind": "acquisition" if Decimal(units) > 0 else "disposal",
        "quantity": Decimal(units),
        "occurred_at": at,
        "source": "user",
        "status": "confirmed",
    }
    if chain is not None:
        values |= {
            "kind": "internal_transfer_in" if Decimal(units) > 0 else "internal_transfer_out",
            "source": "chain",
            "address_id": chain,
            "index": "0",
            "movement_hash": hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        }
    values |= extra
    return ctx.conn.execute(
        insert(movement_table).values(**values).returning(movement_table.c.id)
    ).scalar_one()


def _price(ctx, asset_id, on, price, source="coingecko_daily"):
    ctx.conn.execute(
        insert(price_table).values(
            workspace_id=ctx.workspace_id,
            asset_id=asset_id,
            on=on,
            price_eur=Decimal(price),
            source=source,
            fetched_at=ctx.now,
            raw={},
        )
    )


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


# --- the statement (holdings_statement.yaml) -----------------------------------------------


def test_the_statement_fixture(run, read, fresh_workspaces):
    ws, other = fresh_workspaces
    [case] = load("holdings_statement").cases

    def setup(ctx):
        accounts = {
            "wallet_a": _account(ctx, AccountKind.WALLET, "Wallet A"),
            "binance": _account(ctx, AccountKind.EXCHANGE, "Börse"),
        }
        addresses = {
            "BU8o…jsJP": _address(ctx, accounts["wallet_a"], "solana", "BU8o…jsJP"),
            "0x2b…9c1e": _address(ctx, accounts["wallet_a"], "ethereum", "0x2b…9c1e"),
        }
        assets: dict[str, uuid.UUID] = {}
        ids: dict[str, uuid.UUID] = {}
        for mv in case.movements:
            if mv.asset not in assets:
                assets[mv.asset] = _asset(
                    ctx,
                    None if mv.spam else mv.asset,
                    mv.asset,
                    chain=None if mv.address is None else _chain_of(mv.address),
                    spam=mv.spam,
                )
            extra = {} if mv.address is None else {"address_id": addresses[mv.address]}
            if mv.spam:
                extra["kind"] = "spam"
            ids[mv.name] = _movement(
                ctx, accounts[mv.account], assets[mv.asset], mv.units, mv.at, **extra
            )
        for masked in case.unreconciled:
            ctx.conn.execute(
                insert(sync_table).values(
                    workspace_id=ctx.workspace_id,
                    account_id=accounts["wallet_a"],
                    address_id=addresses[masked],
                    source_host="explorer.invalid",
                    cursor_before="1",
                    status="succeeded",
                    started_at=_at("2026-12-31T06:45:00Z"),
                    finished_at=_at("2026-12-31T06:46:00Z"),
                    balances={"ethereum:native": "1"},
                    reconciled=False,
                )
            )
        return accounts, assets, ids

    accounts, assets, ids = run(ws, setup, now=NOW)
    names = {v: k for k, v in accounts.items()}
    symbol_of = {v: k for k, v in assets.items()}
    seen = read(ws, holdings.statement, case.on)

    positions = set()
    unvalued, unreconciled, summed = set(), [], set()
    for acc in seen.accounts:
        summed |= set(acc.movement_ids)
        unreconciled += acc.unreconciled_addresses
        unvalued |= {symbol_of[t.asset_id] for t in acc.unvalued_tokens}
        if acc.addresses:
            for address in acc.addresses:
                for asset_id, units in address.units_by_asset.items():
                    positions.add(
                        (
                            names[acc.account_id],
                            address.address_masked,
                            symbol_of[uuid.UUID(asset_id)],
                            units,
                        )
                    )
        else:
            for p in acc.positions:
                positions.add((names[acc.account_id], None, symbol_of[p.asset_id], p.units))
    assert positions == {(p.account, p.address, p.asset, p.units) for p in case.expect.positions}
    assert {n for n, i in ids.items() if i not in summed} == set(case.expect.excluded)
    assert sorted(unvalued) == case.expect.unvalued
    assert unreconciled == case.expect.unreconciled

    # A position on the unreconciled address is marked; the spam token is never a position.
    wallet = next(a for a in seen.accounts if names[a.account_id] == "wallet_a")
    by_symbol = {symbol_of[p.asset_id]: p for p in wallet.positions}
    assert not by_symbol["ETH"].reconciled and by_symbol["SOL"].reconciled
    assert "Token 0x9f…21ab" not in by_symbol
    # Nothing priced: every position stays without a value, never 0 €.
    assert all(p.value_eur is None for p in wallet.positions)
    # Workspace isolation: the other workspace sees nothing.
    assert read(other, holdings.statement, case.on).accounts == []


def _chain_of(masked: str) -> str:
    return "ethereum" if masked.startswith("0x") else "solana"


# --- lots across a chain link, the holding period, „Kurs fehlt“ ----------------------------

BOUGHT = _at("2025-03-01T10:00:00Z")  # 11:00 in Berlin, 1 Mar 2025
MOVED = _at("2026-05-01T10:00:00Z")


def _moved_lot(ctx):
    """2 SOL bought on an exchange for 200 € (+1 € fee), moved to a wallet a year later and
    linked as a chain transfer (as the chain matcher writes it); a staking reward in the
    wallet with no price yet."""
    exchange = _account(ctx, AccountKind.EXCHANGE, "Börse")
    wallet = _account(ctx, AccountKind.WALLET, "Wallet")
    sol = _asset(ctx, "SOL", "Solana", chain="solana")
    address = _address(ctx, wallet, "solana", "So1x…xxxx")
    buy = _movement(
        ctx, exchange, sol, "2", BOUGHT, eur_amount=Decimal("-200.00"), fee_eur=Decimal("1.00")
    )
    out = _movement(
        ctx,
        exchange,
        sol,
        "-2",
        MOVED,
        kind="internal_transfer_out",
    )
    into = _movement(ctx, wallet, sol, "2", MOVED + timedelta(minutes=3), address_id=address)
    ctx.conn.execute(
        insert(link_table).values(
            workspace_id=ctx.workspace_id,
            kind="chain",
            out_movement_id=out,
            in_movement_id=into,
            matched_by="matcher",
        )
    )
    reward = _movement(
        ctx,
        wallet,
        sol,
        "0.5",
        _at("2026-06-10T08:00:00Z"),
        address_id=address,
        kind="staking_reward",
    )
    _price(ctx, sol, date(2026, 7, 1), "150.00")
    return {"exchange": exchange, "wallet": wallet, "sol": sol, "buy": buy, "reward": reward}


def test_a_lot_moved_by_a_chain_link_keeps_its_day_and_cost(run, read, fresh_workspaces):
    ws, other = fresh_workspaces
    ids = run(ws, _moved_lot, now=NOW)
    seen = read(ws, holdings.crypto_holdings, date(2026, 7, 1))

    [coin] = seen.coins  # the exchange holds nothing any more
    assert coin.account_id == ids["wallet"] and coin.units == Decimal("2.5")
    moved, reward = coin.lots
    assert moved.movement_id == ids["buy"]  # principle 2: the lot names its purchase
    assert moved.acquired_at == BOUGHT and moved.account_id == ids["wallet"]
    assert moved.cost_eur == Decimal("201.00")  # the EUR paid plus the EUR fee
    assert moved.tax_free_from == tax_free_from(date(2025, 3, 1)) and moved.tax_free
    assert moved.value_eur == Decimal("300.00")
    # The reward has no price on its day: „Kurs fehlt“ names the coin and the day.
    assert reward.cost_eur is None and not reward.tax_free
    assert [(n.asset_id, n.symbol, n.on) for n in reward.price_needs] == [
        (ids["sol"], "SOL", date(2026, 6, 10))
    ]
    assert coin.cost_eur is None and coin.unrealized_eur is None and coin.unpriced_lots == 1
    assert coin.cost_priced_eur == Decimal("201.00")
    assert seen.total_eur == Decimal("375.00")
    assert (seen.unrealized_eur, seen.unrealized_excluded) == (Decimal("0.00"), 1)
    assert seen.tax_free_today_eur == Decimal("300.00")
    assert seen.next_tranche.on == tax_free_from(date(2026, 6, 10))
    assert seen.next_tranche.value_eur == Decimal("75.00")
    [wallet_card] = [a for a in seen.accounts if a.account_id == ids["wallet"]]
    assert wallet_card.value_eur == Decimal("375.00")

    # „Kurs eintragen“ (a typed price of the reward's day) prices the lot on the next read.
    run(ws, lambda ctx: _price(ctx, ids["sol"], date(2026, 6, 10), "120.00", "user"), now=NOW)
    seen = read(ws, holdings.crypto_holdings, date(2026, 7, 1))
    [coin] = seen.coins
    assert coin.lots[1].cost_eur == Decimal("60.00") and coin.lots[1].price_needs == []
    assert coin.cost_eur == Decimal("261.00")
    assert coin.unrealized_eur == Decimal("114.00")

    lots = read(ws, holdings.account_lots, ids["wallet"], date(2026, 7, 1), ids["sol"])
    assert [lot.movement_id for lot in lots] == [ids["buy"], ids["reward"]]
    assert read(ws, holdings.account_lots, ids["exchange"], date(2026, 7, 1)) == []
    # Workspace isolation.
    assert read(other, holdings.crypto_holdings, date(2026, 7, 1)).coins == []


def test_the_holding_period_turns_on_its_day(run, read, fresh_workspaces):
    ws, _ = fresh_workspaces
    ids = run(ws, _moved_lot, now=NOW)
    free_on = tax_free_from(date(2025, 3, 1))

    def lot_on(on):
        [coin] = [
            c for c in read(ws, holdings.crypto_holdings, on).coins if c.asset_id == ids["sol"]
        ]
        return coin, coin.lots[0]

    coin, lot = lot_on(free_on - timedelta(days=1))
    assert not lot.tax_free and coin.next_tax_free_from == free_on
    assert coin.tax_free_units == 0
    # Before the move the lot is still on the exchange, with its purchase day.
    assert coin.account_id == ids["exchange"] and lot.acquired_at == BOUGHT
    coin, lot = lot_on(free_on)
    assert lot.tax_free and coin.tax_free_units == Decimal("2")
    assert coin.next_tax_free_from is None


def test_an_unlinked_in_leg_is_a_lot_of_unknown_cost_without_a_price_need(
    run, read, fresh_workspaces
):
    ws, _ = fresh_workspaces

    def setup(ctx):
        wallet = _account(ctx, AccountKind.WALLET, "Wallet")
        eth = _asset(ctx, "ETH", "Ether", chain="ethereum")
        address = _address(ctx, wallet, "ethereum", "0xab…abab")
        _movement(ctx, wallet, eth, "1", _at("2026-03-01T10:00:00Z"), address_id=address)
        return eth

    eth = run(ws, setup, now=NOW)
    [coin] = read(ws, holdings.crypto_holdings, date(2026, 7, 1)).coins
    [lot] = coin.lots
    assert coin.asset_id == eth and lot.cost_eur is None and lot.price_needs == []
    assert lot.source.value == "unknown"
