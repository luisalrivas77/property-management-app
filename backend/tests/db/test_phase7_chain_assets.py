"""Phase 6's asset and price code next to phase 7's chain assets, as hausbuch_app under RLS
(docs/phases/phase-7.md D180 amended, D187, §10, §15.1 item 10, "fix E"): a symbol lookup never
meets a chain asset, so an exchange import never books onto one; a chain asset priced as a
symbol-level asset is fetched through that asset once and valued from its rows, a price typed
on the chain asset still winning its day; a spam token is never fetched or carded. Invented
coins, contracts and prices only."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.test_services_exchange_import import REAL_LAYOUT, _exchange, _upload
from db.test_services_prices_fetch import ACTOR, FIRST, NOW, Fake, _asset, _held
from db.test_services_prices_overview import _cards, _price
from sqlalchemy import select

from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import price as price_table
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.bank import AccountCreate
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.household import DeclareHoldingsBody
from hausbuch.schemas.prices import PriceSourceKind
from hausbuch.services.accounts import create_account
from hausbuch.services.exchange import imports
from hausbuch.services.prices import assets as price_assets
from hausbuch.services.prices import overview
from hausbuch.services.prices.fetch import Sources, held_assets, run_workspace

pytestmark = pytest.mark.db
S = PriceSourceKind

# Invented contracts (lower-case hex, 0041's EVM format).
EUR_LOOKALIKE = "0x" + "e1" * 20
INV_ON_ETHEREUM = "0x" + "a1" * 20
INV_ON_ARBITRUM = "0x" + "a2" * 20
SPAM = "0x" + "5a" * 20
BTC_LOOKALIKE = "0x" + "b7" * 20


def _chain_assets(ctx) -> dict:
    """The chain assets a lookup by symbol must never meet: the native BTC of the bitcoin
    chain and SOL of solana, and an ERC-20 whose label reads „EUR“."""
    return {
        "btc": _asset(ctx, kind="crypto", symbol="BTC", name="Bitcoin", chain="bitcoin"),
        "sol": _asset(ctx, kind="crypto", symbol="SOL", name="Solana", chain="solana"),
        "eur": _asset(
            ctx,
            kind="crypto",
            symbol="EUR",
            name="Euro Lookalike",
            chain="ethereum",
            contract_address=EUR_LOOKALIKE,
            spam=True,
        ),
    }


def _symbols(read, ws, asset_ids) -> dict:
    def query(conn):
        a = asset_table.c
        rows = conn.execute(select(a.id, a.symbol, a.chain).where(a.id.in_(asset_ids))).all()
        return {r.id: (r.symbol, r.chain) for r in rows}

    return read(ws, query)


# --- Fix E: a symbol names an off-chain asset only -----------------------------------------


def test_an_exchange_import_never_books_onto_a_chain_asset_of_its_symbol(
    run, read, blob_store, fresh_workspaces
):
    ws, _ = fresh_workspaces
    chain = run(ws, _chain_assets, now=FIRST)
    account = _exchange(run, ws)
    imported = _upload(run, blob_store, ws, account.id, REAL_LAYOUT.encode()).movement_import

    movements = read(ws, imports.exchange_movements, account.id)
    used = {m.asset_id for m in movements}
    assert used.isdisjoint(chain.values())
    assert set(_symbols(read, ws, used).values()) == {("EUR", None), ("BTC", None)}
    # The phase 6 figures are unchanged: EUR 500 less the buy's leg, BTC bought, bonus, sent.
    seen = read(ws, imports.exchange_overview, account.id)
    assert {p.symbol: p.units for p in seen.positions} == {
        "EUR": Decimal("250.00"),
        "BTC": Decimal("0.00009965"),
    }
    by_symbol = {b.symbol: b for b in next(i for i in seen.imports if i.id == imported.id).balances}
    assert {b.asset_id for b in by_symbol.values()}.isdisjoint(chain.values())
    checked = run(
        ws,
        imports.declare_holdings,
        imported.id,
        DeclareHoldingsBody(
            balances=[
                {"asset_id": by_symbol["EUR"].asset_id, "units": "250.00"},
                {"asset_id": by_symbol["BTC"].asset_id, "units": "0.00009965"},
            ]
        ),
        now=NOW,
    )
    assert checked.status.value == "reconciled"


def test_the_eur_leg_of_a_buy_counts_on_the_off_chain_euro(run, read, blob_store, fresh_workspaces):
    """An export with a buy and no EUR row: the EUR position comes from the trade's leg alone
    (the second lookup, `_held_by_asset`), on the chainless EUR, never the „EUR“ token."""
    ws, _ = fresh_workspaces
    chain = run(ws, _chain_assets, now=FIRST)
    account = _exchange(run, ws)
    header = REAL_LAYOUT.splitlines()[0]
    buy = REAL_LAYOUT.splitlines()[2]
    imported = _upload(
        run, blob_store, ws, account.id, f"{header}\n{buy}\n".encode()
    ).movement_import
    [view] = [i for i in read(ws, imports.exchange_overview, account.id).imports]
    assert view.id == imported.id
    by_symbol = {b.symbol: b for b in view.balances}
    assert by_symbol["EUR"].held == Decimal("-250.00")
    assert by_symbol["EUR"].asset_id != chain["eur"]
    assert _symbols(read, ws, [by_symbol["EUR"].asset_id]) == {
        by_symbol["EUR"].asset_id: ("EUR", None)
    }


def test_the_price_lookups_by_symbol_skip_chain_assets(run, read, fresh_workspaces):
    ws, _ = fresh_workspaces
    chain = run(ws, _chain_assets, now=FIRST)

    def lookup(ctx):
        return {
            "sol": price_assets.asset_for_symbol(ctx, "SOL"),
            "btc": price_assets.asset_for_symbol(ctx, "BTC"),
            "eur": price_assets.cash_asset(ctx, "EUR"),
            "exchange_sol": imports.asset_for_symbol(ctx, "SOL"),
        }

    found = run(ws, lookup, now=NOW)
    assert set(found.values()).isdisjoint(chain.values())
    assert found["sol"] == found["exchange_sol"]  # one chainless SOL, created once
    assert set(_symbols(read, ws, found.values()).values()) == {
        ("SOL", None),
        ("BTC", None),
        ("EUR", None),
    }


def test_a_chain_asset_reads_through_the_api_schema(run, read, fresh_workspaces):
    """The read schema takes a chain token's label and its chain fields (wstETH: lower case)."""
    ws, _ = fresh_workspaces

    def seed(ctx):
        return _asset(
            ctx,
            kind="crypto",
            symbol="wstETH",
            name="Wrapped Staked Ether",
            chain="ethereum",
            contract_address=INV_ON_ETHEREUM,
            decimals=18,
        )

    token = run(ws, seed, now=FIRST)
    [row] = [a for a in read(ws, overview.list_assets, date(2026, 6, 27)) if a.id == token]
    assert (row.symbol, row.chain, row.contract_address, row.spam) == (
        "wstETH",
        "ethereum",
        INV_ON_ETHEREUM,
        False,
    )
    assert row.priced_as_asset_id is None and not row.has_source  # no registry ids by symbol


# --- priced_as: fetched once through the symbol-level asset, valued from its rows ----------


def _depot(ctx):
    return create_account(
        ctx,
        AccountCreate(
            label="Depot",
            bank="Beispiel",
            kind=AccountKind.DEPOT,
            opening_balance=Decimal("0.00"),
            opening_balance_on=date(2026, 6, 1),
        ),
        iban_key=TEST_IBAN_KEY,
    ).id


def _priced_as_world(ctx) -> dict:
    """INV held on two chains, both priced as the chainless INV (which is never held itself);
    the arbitrum leg only proposed and moved a day earlier; a spam token and a lookalike
    reading „BTC“ with no ids of its own."""
    account = _depot(ctx)
    twin = _asset(
        ctx,
        kind="crypto",
        symbol="INV",
        name="Invented Coin",
        coingecko_id="invented-coin",
        kraken_pair="INVEUR",
    )
    on_ethereum = _asset(
        ctx,
        kind="crypto",
        symbol="INV",
        name="Invented Coin (Ethereum)",
        chain="ethereum",
        contract_address=INV_ON_ETHEREUM,
        priced_as_asset_id=twin,
    )
    on_arbitrum = _asset(
        ctx,
        kind="crypto",
        symbol="INV",
        name="Invented Coin (Arbitrum)",
        chain="arbitrum",
        contract_address=INV_ON_ARBITRUM,
        priced_as_asset_id=twin,
    )
    spam = _asset(
        ctx,
        kind="crypto",
        symbol="FREE",
        name="Claim reward",
        chain="ethereum",
        contract_address=SPAM,
        spam=True,
        kraken_pair="FREEEUR",
    )
    lookalike = _asset(
        ctx,
        kind="crypto",
        symbol="BTC",
        name="Bitcoin Lookalike",
        chain="ethereum",
        contract_address=BTC_LOOKALIKE,
    )
    _held(ctx, account, on_ethereum, "2", at=datetime(2026, 6, 25, 9, 0, tzinfo=UTC))
    _held(ctx, account, on_arbitrum, "1", at=FIRST, status="proposed")
    for asset_id in (spam, lookalike):
        _held(ctx, account, asset_id, "100")
    return {
        "account": account,
        "twin": twin,
        "ethereum": on_ethereum,
        "arbitrum": on_arbitrum,
        "spam": spam,
        "lookalike": lookalike,
    }


def _price_rows(read, ws) -> list:
    def query(conn):
        p = price_table.c
        return conn.execute(select(p.asset_id, p.on, p.source).order_by(p.on)).all()

    return read(ws, query)


def test_a_priced_as_asset_is_fetched_once_from_the_first_chain_movement(
    run, read, app_engine, fresh_workspaces
):
    ws, _ = fresh_workspaces
    ids = run(ws, _priced_as_world, now=FIRST)

    planned = read(ws, held_assets)
    by_id = {h.id: h for h in planned}
    # The chainless INV once, never the chain INVs nor the spam token; the lookalike is
    # planned without ids (none of its own, none from the registry by its label), so skipped.
    assert set(by_id) == {ids["twin"], ids["lookalike"]}
    assert [h.id for h in planned].count(ids["twin"]) == 1
    assert by_id[ids["twin"]].first_on == FIRST.date()  # the proposed arbitrum leg's day
    assert not by_id[ids["lookalike"]].ids.any

    fake = Fake()
    run_workspace(app_engine, ws, ACTOR, Sources(fetch=fake, coingecko=None), NOW)
    assert fake.asked("OHLC?pair=INVEUR") == 1
    assert fake.asked("FREEEUR") == 0  # spam is never fetched
    coin_rows = [r for r in _price_rows(read, ws) if r.source == S.KRAKEN_DAILY.value]
    assert coin_rows and {r.asset_id for r in coin_rows} == {ids["twin"]}
    assert min(r.on for r in coin_rows) == FIRST.date()  # nothing before the first movement


def test_a_chain_asset_is_valued_through_its_priced_as_asset_and_a_typed_price_wins_its_day(
    run, read, app_engine, fresh_workspaces
):
    ws, _ = fresh_workspaces
    ids = run(ws, _priced_as_world, now=FIRST)
    run_workspace(app_engine, ws, ACTOR, Sources(fetch=Fake(), coingecko=None), NOW)

    def positions(on):
        [acc] = read(ws, overview.assets_overview, on).accounts
        return {p.asset_id: p for p in acc.positions}

    seen = positions(date(2026, 6, 27))
    eth = seen[ids["ethereum"]]
    # The same Kraken row the phase 6 coin test reads, stored under the chainless INV.
    assert (eth.price.source, eth.price.price_eur) == (S.KRAKEN_DAILY, Decimal("92.40000001"))
    assert eth.value_eur == Decimal("184.80")
    assert ids["spam"] not in seen  # hidden from Anlagen and the net worth (D187 (1))
    assert seen[ids["lookalike"]].value_eur is None  # no registry price by its label

    # A price typed on the chain asset wins its own day, and only that day.
    run(ws, _price, ids["ethereum"], date(2026, 6, 25), "90.00", S.USER, now=NOW)
    on_25 = positions(date(2026, 6, 25))[ids["ethereum"]]
    assert (on_25.price.source, on_25.price.price_eur) == (S.USER, Decimal("90.00000000"))
    assert on_25.value_eur == Decimal("180.00")
    on_26 = positions(date(2026, 6, 26))[ids["ethereum"]]
    assert (on_26.price.source, on_26.price.on) == (S.KRAKEN_DAILY, date(2026, 6, 26))

    rows = {r.id: r for r in read(ws, overview.list_assets, date(2026, 6, 27))}
    assert rows[ids["arbitrum"]].has_source  # through its priced_as asset
    assert rows[ids["arbitrum"]].priced_as_asset_id == ids["twin"]
    assert not rows[ids["lookalike"]].has_source


def test_price_cards_skip_priced_as_and_spam_but_ask_for_a_lookalike(run, read, fresh_workspaces):
    ws, _ = fresh_workspaces
    ids = run(ws, _priced_as_world, now=FIRST)
    cards = _cards(read, ws, NOW, InboxItemKind.PRICE_NO_SOURCE)
    # The lookalike reads „BTC“ but is not the registry's Bitcoin (D187): „Kursquelle wählen“.
    assert {c.asset_id for c in cards} == {ids["lookalike"]}
