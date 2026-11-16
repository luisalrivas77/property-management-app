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
from hausbuch.services import holdings, holdings_export

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


# --- the year-end statement as a snapshot (D190) -------------------------------------------

KEY = b"k" * 32


def test_the_year_end_statement_is_a_snapshot_per_account(run, read, fresh_workspaces, blob_store):
    from sqlalchemy import select, text
    from sqlalchemy.exc import ProgrammingError

    from hausbuch.db.tables import event as event_table
    from hausbuch.db.tables import holdings_export as export_table
    from hausbuch.schemas.holdings import HoldingsExportCreate
    from hausbuch.services.errors import NotFoundError
    from hausbuch.services.reminders import facts

    ws, other = fresh_workspaces
    ids = run(ws, _moved_lot, now=NOW)
    body = HoldingsExportCreate(year=2026)

    def missing():
        return {y.year: y.accounts_missing for y in read(ws, facts, date(2027, 1, 5)).crypto_years}

    # 2025: the exchange held the coin bought that year; 2026: both accounts had movements.
    assert missing() == {2025: 1, 2026: 2}

    written = run(ws, holdings_export.write_exports, blob_store, body, key=KEY, now=NOW)
    assert {e.account_id for e in written} == {ids["exchange"], ids["wallet"]}
    assert all(e.as_of == date(2026, 12, 31) for e in written)
    [wallet_export] = [e for e in written if e.account_id == ids["wallet"]]

    # The files are stored and read back verified; the statement names its movements.
    pdf, media, name = read(ws, holdings_export.read_file, blob_store, wallet_export.id, "pdf")
    assert pdf.startswith(b"%PDF") and media == "application/pdf"
    assert name == "bestandsnachweis-2026-12-31-Wallet.pdf"
    assert hashlib.sha256(pdf).hexdigest() == wallet_export.pdf_sha256
    csv_bytes, _, _ = read(ws, holdings_export.read_file, blob_store, wallet_export.id, "csv")
    lines = csv_bytes.decode("utf-8-sig").splitlines()
    assert lines[0].startswith("Zeile;Coin;Adresse;Einheiten")
    # The moved lot keeps its purchase day and cost; the reward's cost stays open, never 0 €.
    assert "Posten;SOL;;2;;;;2025-03-01;201,00;2026-03-02;" in lines
    assert "Posten;SOL;;0,5;;;;2026-06-10;;2027-06-11;Kurs fehlt" in lines
    assert any(line.startswith("Adresse;SOL;So1x…xxxx;2,5;") for line in lines)
    assert b"So1xxxx" not in csv_bytes  # the stored address never leaves the database

    def stored(conn):
        x = export_table.c
        row = conn.execute(select(x.statement, x.created_by).where(x.id == wallet_export.id)).one()
        events = conn.execute(
            select(event_table.c.entity_id, event_table.c.payload).where(
                event_table.c.type == "account.holdings_exported"
            )
        ).all()
        return row, events

    row, events = read(ws, stored)
    assert row.statement["on"] == "2026-12-31"
    assert str(ids["buy"]) not in row.statement["account"]["movement_ids"]  # the exchange's
    assert str(ids["reward"]) in row.statement["account"]["movement_ids"]
    assert {e.entity_id for e in events} == {ids["exchange"], ids["wallet"]}
    assert {e.payload["holdings_export_id"] for e in events} == {str(e.id) for e in written}

    # Append-only: the app role can neither change nor delete a row.
    for statement in (
        "UPDATE holdings_export SET as_of = '2026-12-30'",
        "DELETE FROM holdings_export",
    ):
        with pytest.raises(ProgrammingError):
            run(ws, lambda ctx, sql=statement: ctx.conn.execute(text(sql)), now=NOW)

    # The reminder closes; a second export is new rows, the first ones unchanged.
    assert missing() == {2025: 1, 2026: 0}
    again = run(ws, holdings_export.write_exports, blob_store, body, key=KEY, now=NOW)
    listed = read(ws, holdings_export.list_exports, key=KEY, now=NOW, year=2026)
    assert {e.id for e in listed} == {e.id for e in written + again}
    assert {e.pdf_sha256 for e in again} != {e.pdf_sha256 for e in written}  # its own id

    # Workspace isolation.
    assert read(other, holdings_export.list_exports, key=KEY, now=NOW) == []
    with pytest.raises(NotFoundError):
        read(other, holdings_export.read_file, blob_store, wallet_export.id, "pdf")
    assert read(other, facts, date(2027, 1, 5)).crypto_years == []


def test_the_statement_waits_for_the_year_end_and_for_something_held(
    run, fresh_workspaces, blob_store
):
    from hausbuch.rules.errors import (
        HOLDINGS_EXPORT_NOTHING_HELD,
        HOLDINGS_EXPORT_YEAR_OPEN,
        RuleError,
    )
    from hausbuch.schemas.holdings import HoldingsExportCreate

    ws, other = fresh_workspaces
    run(ws, _moved_lot, now=NOW)
    body = HoldingsExportCreate(year=2026)
    # 31 Dec 2026, 23:30 in Berlin: the day is not over yet.
    with pytest.raises(RuleError) as refused:
        late = datetime(2026, 12, 31, 22, 30, tzinfo=UTC)
        run(ws, holdings_export.write_exports, blob_store, body, key=KEY, now=late)
    assert refused.value.code == HOLDINGS_EXPORT_YEAR_OPEN
    # 1 Jan 2027, 00:30 in Berlin: it is.
    early = datetime(2026, 12, 31, 23, 30, tzinfo=UTC)
    assert len(run(ws, holdings_export.write_exports, blob_store, body, key=KEY, now=early)) == 2
    with pytest.raises(RuleError) as refused:
        run(other, holdings_export.write_exports, blob_store, body, key=KEY, now=NOW)
    assert refused.value.code == HOLDINGS_EXPORT_NOTHING_HELD
    # Nothing was held on 31 Dec 2024 either: the purchase is of 2025.
    with pytest.raises(RuleError) as refused:
        run(
            ws,
            holdings_export.write_exports,
            blob_store,
            HoldingsExportCreate(year=2024),
            key=KEY,
            now=NOW,
        )
    assert refused.value.code == HOLDINGS_EXPORT_NOTHING_HELD
