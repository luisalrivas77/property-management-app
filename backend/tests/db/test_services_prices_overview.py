"""The Anlagen Übersicht, the two price commands and the price cards as hausbuch_app under RLS
(docs/phases/phase-6.md D167, D174 (4), §13; phase 6 step 5b). Invented coins, funds and
prices only; the fetch itself is tests/db/test_services_prices_fetch.py."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.test_services_prices_fetch import (
    ACTOR,
    EUR_FUND,
    FIRST,
    NOW,
    Fake,
    _asset,
    _events,
    _held,
    _setup,
)
from db.test_services_prices_fetch import funds as funds  # noqa: F401 (a fixture)
from sqlalchemy import insert, select

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import holding_statement as holding_table
from hausbuch.db.tables import price as price_table
from hausbuch.rules import errors as E
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.bank import AccountCreate
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.events import EventType
from hausbuch.schemas.household import AssetPatch
from hausbuch.schemas.prices import PriceSourceKind, TypedPriceBody
from hausbuch.services import depot, review
from hausbuch.services.accounts import create_account
from hausbuch.services.prices import overview
from hausbuch.services.prices.fetch import Sources, run_workspace

pytestmark = pytest.mark.db
S = PriceSourceKind


def _account_id(read, ws):
    return read(ws, lambda conn: conn.execute(select(account_table.c.id)).scalar_one())


def _price(ctx, asset_id, on, price, source, fetched_at=None):
    ctx.conn.execute(
        insert(price_table).values(
            workspace_id=ctx.workspace_id,
            asset_id=asset_id,
            on=on,
            price_eur=Decimal(price),
            source=source.value,
            fetched_at=fetched_at or ctx.now,
            raw={},
            created_at=ctx.now,
        )
    )


def _cards(read, ws, now, kind=None):
    items = read(ws, review.inbox, today=now.date(), now=now)
    return [i for i in items if kind is None or i.kind is kind]


def test_the_overview_values_each_position_by_its_days_price(
    run, read, app_engine, fresh_workspaces, funds
):
    ws, other = fresh_workspaces
    ids = _setup(run, ws)
    run_workspace(app_engine, ws, ACTOR, Sources(fetch=Fake(), coingecko=None), NOW)
    account = _account_id(read, ws)

    def more(ctx):
        euro = _asset(ctx, kind="cash", symbol="EUR", name="Euro", currency="EUR")
        _held(ctx, account, euro, "150.505")
        unknown = _asset(ctx, kind="crypto", symbol="ZZZ", name="Unbekannte Münze")
        _held(ctx, account, unknown, "3")
        stock = _asset(ctx, kind="etf", isin="IE000INVENT9", name="Fonds ohne Adapter")
        _held(ctx, account, stock, "10")
        _price(ctx, stock, date(2026, 6, 1), "50.00", S.STATEMENT)
        # A trade on the 25th ranks above that day's NAV, but the 26th's NAV is newer.
        _price(ctx, ids["eur"], date(2026, 6, 25), "99.00", S.TRADE)
        return {"euro": euro, "unknown": unknown, "stock": stock}

    more_ids = run(ws, more, now=NOW)
    seen = read(ws, overview.assets_overview, date(2026, 6, 27))
    [acc] = seen.accounts
    by_id = {p.asset_id: p for p in acc.positions}
    assert acc.positions[0].asset_id == more_ids["euro"]  # cash first
    assert by_id[more_ids["euro"]].value_eur == Decimal("150.51")
    assert by_id[more_ids["euro"]].price is None and not by_id[more_ids["euro"]].stale

    eur = by_id[ids["eur"]]
    assert (eur.price.source, eur.price.on) == (S.ISSUER_NAV, date(2026, 6, 26))
    assert eur.price.issuer == "State Street SPDR"
    assert eur.value_eur == Decimal("163.40")  # 2 x 81.70
    coin = by_id[ids["coin"]]
    assert (coin.price.source, coin.price.price_eur) == (S.KRAKEN_DAILY, Decimal("92.40000001"))
    assert coin.value_eur == Decimal("184.80")

    stock = by_id[more_ids["stock"]]
    assert stock.from_report and stock.stale and stock.value_eur == Decimal("500.00")
    unknown = by_id[more_ids["unknown"]]
    assert unknown.value_eur is None and unknown.not_valued_reason == E.PRICE_NO_SOURCE
    assert ids["sold"] not in by_id

    assert (seen.stale_positions, seen.unvalued_positions) == (1, 1)
    assert acc.value_eur == seen.total_eur == sum(seen.by_kind.values())
    assert seen.by_kind["cash"] == Decimal("150.51")

    # On the 25th the trade's own price wins its day (date first, then source).
    earlier = read(ws, overview.assets_overview, date(2026, 6, 25))
    eur_25 = next(p for p in earlier.accounts[0].positions if p.asset_id == ids["eur"])
    assert (eur_25.price.source, eur_25.price.price_eur) == (S.TRADE, Decimal("99.00000000"))
    # Before the first movement nothing is held.
    assert read(ws, overview.assets_overview, date(2026, 6, 23)).accounts == []
    assert read(other, overview.assets_overview, date(2026, 6, 27)).accounts == []


def test_choosing_a_source_and_typing_a_price(run, read, fresh_workspaces):
    ws, other = fresh_workspaces

    def seed(ctx):
        return {
            "coin": _asset(ctx, kind="crypto", symbol="ZZZ", name="Unbekannte Münze"),
            "fund": _asset(ctx, kind="etf", isin="IE000INVENT9", name="Fonds"),
            "euro": _asset(ctx, kind="cash", symbol="EUR", name="Euro", currency="EUR"),
        }

    ids = run(ws, seed, now=FIRST)
    [coin] = [a for a in read(ws, overview.list_assets, date(2026, 6, 27)) if a.symbol == "ZZZ"]
    assert not coin.has_source
    row = run(ws, overview.update_asset, ids["coin"], AssetPatch(coingecko_id="zzz-coin"), now=NOW)
    assert row.has_source and row.coingecko_id == "zzz-coin"
    [updated] = _events(read, ws, EventType.ASSET_UPDATED)
    assert updated.payload["changes"]["coingecko_id"] == {"old": None, "new": "zzz-coin"}
    run(ws, overview.update_asset, ids["coin"], AssetPatch(coingecko_id="zzz-coin"), now=NOW)
    assert len(_events(read, ws, EventType.ASSET_UPDATED)) == 1  # no change, no event

    with pytest.raises(RuleError) as refused:
        run(ws, overview.update_asset, ids["fund"], AssetPatch(kraken_pair="XEUR"), now=NOW)
    assert refused.value.code == E.ASSET_NOT_CRYPTO

    body = TypedPriceBody(on=date(2026, 6, 26), price_eur=Decimal("12.5"))
    typed = run(ws, overview.record_price, ids["fund"], body, now=NOW)
    assert (typed.source, typed.price_eur) == (S.USER, Decimal("12.5"))
    [recorded] = _events(read, ws, EventType.ASSET_PRICE_RECORDED)
    assert recorded.payload["price_id"] == str(typed.id)
    with pytest.raises(RuleError) as refused:
        run(ws, overview.record_price, ids["euro"], body, now=NOW)
    assert refused.value.code == E.ASSET_IS_EURO
    with pytest.raises(Exception):  # noqa: B017 - RLS: another workspace's asset is not found
        run(other, overview.record_price, ids["fund"], body, now=NOW)


def test_no_source_cards_until_a_source_or_a_report(run, read, fresh_workspaces):
    ws, _ = fresh_workspaces

    def seed(ctx):
        created = create_account(
            ctx,
            AccountCreate(
                label="Depot",
                bank="Beispiel",
                kind=AccountKind.DEPOT,
                opening_balance=Decimal("0.00"),
                opening_balance_on=date(2026, 6, 1),
            ),
            iban_key=TEST_IBAN_KEY,
        )
        coin = _asset(ctx, kind="crypto", symbol="ZZZ", name="Unbekannte Münze")
        fund = _asset(ctx, kind="etf", isin="IE000INVENT9", name="Fonds ohne Adapter")
        known = _asset(ctx, kind="crypto", symbol="BTC", name="Bitcoin")  # the registry's
        for asset_id in (coin, fund, known):
            _held(ctx, created.id, asset_id)
        return {"coin": coin, "fund": fund, "known": known}

    ids = run(ws, seed, now=FIRST)
    cards = _cards(read, ws, NOW, InboxItemKind.PRICE_NO_SOURCE)
    assert {c.asset_id for c in cards} == {ids["coin"], ids["fund"]}
    coin_card = next(c for c in cards if c.asset_id == ids["coin"])
    assert "Kursquelle wählen" in coin_card.question
    run(ws, overview.update_asset, ids["coin"], AssetPatch(kraken_pair="ZZZEUR"), now=NOW)
    run(ws, _price, ids["fund"], date(2026, 6, 26), "50.00", S.STATEMENT, now=NOW)
    assert _cards(read, ws, NOW, InboxItemKind.PRICE_NO_SOURCE) == []


def test_the_failed_card_until_a_run_succeeds_or_a_price_is_typed(
    run, read, app_engine, fresh_workspaces, funds
):
    ws, _ = fresh_workspaces
    ids = _setup(run, ws)
    down = Fake(down=("navhist-emea-en-inv-gy",))
    for day in range(7):
        now = NOW + timedelta(days=day)
        assert _cards(read, ws, now, InboxItemKind.PRICE_SOURCE_FAILED) == []
        run_workspace(app_engine, ws, ACTOR, Sources(fetch=down, coingecko=None), now)
    now = NOW + timedelta(days=6, hours=1)
    [card] = _cards(read, ws, now, InboxItemKind.PRICE_SOURCE_FAILED)
    assert card.asset_id == ids["eur"]
    assert EUR_FUND.name in card.reasoning and "State Street SPDR" in card.reasoning
    assert "7 Tagen" in card.reasoning and "Kurs eintragen" in card.question

    # A typed price ends it; a new failure the next day does not bring it back.
    typed = TypedPriceBody(on=now.date(), price_eur=Decimal("82"))
    run(ws, overview.record_price, ids["eur"], typed, now=now)
    assert _cards(read, ws, now, InboxItemKind.PRICE_SOURCE_FAILED) == []
    nxt = NOW + timedelta(days=7)
    run_workspace(app_engine, ws, ACTOR, Sources(fetch=down, coingecko=None), nxt)
    assert _cards(read, ws, nxt, InboxItemKind.PRICE_SOURCE_FAILED) == []


def test_a_successful_run_ends_the_failed_card(run, read, app_engine, fresh_workspaces, funds):
    ws, _ = fresh_workspaces
    _setup(run, ws)
    down = Fake(down=("navhist-emea-en-inv-gy",))
    for day in range(7):
        run_workspace(
            app_engine, ws, ACTOR, Sources(fetch=down, coingecko=None), NOW + timedelta(days=day)
        )
    later = NOW + timedelta(days=6, hours=2)
    assert len(_cards(read, ws, later, InboxItemKind.PRICE_SOURCE_FAILED)) == 1
    ok_day = NOW + timedelta(days=7)
    run_workspace(app_engine, ws, ACTOR, Sources(fetch=Fake(), coingecko=None), ok_day)
    assert _cards(read, ws, ok_day, InboxItemKind.PRICE_SOURCE_FAILED) == []


def _report(ctx, account_id, asset_id, as_of, units):
    """A current depot report's position (source report), as finish_report stores it."""
    ctx.conn.execute(
        insert(holding_table).values(
            workspace_id=ctx.workspace_id,
            account_id=account_id,
            asset_id=asset_id,
            as_of=as_of,
            declared_units=Decimal(units),
            source="report",
            created_at=ctx.now,
        )
    )


def test_a_report_that_disagrees_leaves_the_fund_unvalued_until_it_agrees(
    run, read, app_engine, fresh_workspaces, funds
):
    ws, _ = fresh_workspaces
    ids = _setup(run, ws)  # 2 units each of the coin and both funds, bought 24 Jun
    run_workspace(app_engine, ws, ACTOR, Sources(fetch=Fake(), coingecko=None), NOW)
    account = _account_id(read, ws)
    on = date(2026, 6, 27)

    def eur_of(seen):
        return next(p for acc in seen.accounts for p in acc.positions if p.asset_id == ids["eur"])

    # The report of the 25th says 3 units of the EUR fund; 2 are booked.
    run(
        ws,
        lambda ctx: [
            _report(ctx, account, ids[name], date(2026, 6, 25), units)
            for name, units in (("eur", "3"), ("usd", "2"), ("coin", "2"))
        ],
        now=NOW,
    )
    seen = read(ws, overview.assets_overview, on)
    eur = eur_of(seen)
    assert eur.value_eur is None and eur.not_valued_reason == E.VALUATION_NOT_VALUED
    assert eur.reconciled_through == overview.NEVER_RECONCILED
    assert eur.price is not None  # the NAV is still shown, its value is not counted
    assert seen.unvalued_positions == 1
    assert ids["eur"] not in {
        p.asset_id for acc in seen.accounts for p in acc.positions if p.value_eur is not None
    }
    assert seen.total_eur == sum(
        (p.value_eur for p in seen.accounts[0].positions if p.value_eur is not None), Decimal(0)
    )
    # The ETF tab says the same about that fund.
    [etf] = [
        p for p in read(ws, depot.etf_overview).accounts[0].positions if p.asset_id == ids["eur"]
    ]
    assert etf.reconciled is False and etf.value_eur is None

    # A newer report that disagrees too: bounded by the 25th only once the 25th agrees.
    run(
        ws,
        lambda ctx: [
            _report(ctx, account, ids[name], date(2026, 6, 26), units)
            for name, units in (("eur", "5"), ("usd", "2"), ("coin", "2"))
        ],
        now=NOW,
    )
    assert eur_of(read(ws, overview.assets_overview, on)).reconciled_through == (
        overview.NEVER_RECONCILED
    )

    # The missing trade confirmation, confirmed later: the 25th now agrees, the 26th not.
    run(ws, _held, account, ids["eur"], "1", datetime(2026, 6, 25, 9, 0, tzinfo=UTC), now=NOW)
    eur = eur_of(read(ws, overview.assets_overview, on))
    assert eur.reconciled_through == date(2026, 6, 25) and eur.value_eur is None

    # And the one the 26th lists: the latest report agrees, so the fund is valued again,
    # units x the day's NAV, without a new report and past the report's own day.
    run(ws, _held, account, ids["eur"], "2", datetime(2026, 6, 26, 9, 0, tzinfo=UTC), now=NOW)
    seen = read(ws, overview.assets_overview, on)
    eur = eur_of(seen)
    assert eur.reconciled_through is None and eur.not_valued_reason is None
    assert (eur.price.source, eur.value_eur) == (S.ISSUER_NAV, Decimal("408.50"))  # 5 x 81.70
    assert seen.unvalued_positions == 0
    [etf] = [
        p for p in read(ws, depot.etf_overview).accounts[0].positions if p.asset_id == ids["eur"]
    ]
    assert etf.reconciled is True


def test_a_fund_the_report_omits_is_unvalued(run, read, app_engine, fresh_workspaces, funds):
    ws, _ = fresh_workspaces
    ids = _setup(run, ws)
    run_workspace(app_engine, ws, ACTOR, Sources(fetch=Fake(), coingecko=None), NOW)
    account = _account_id(read, ws)
    # The report lists the coin and the EUR fund only: the USD fund is 0 by omission.
    run(
        ws,
        lambda ctx: [
            _report(ctx, account, ids[name], date(2026, 6, 25), "2") for name in ("eur", "coin")
        ],
        now=NOW,
    )
    seen = read(ws, overview.assets_overview, date(2026, 6, 27))
    by_id = {p.asset_id: p for p in seen.accounts[0].positions}
    assert by_id[ids["usd"]].not_valued_reason == E.VALUATION_NOT_VALUED
    assert by_id[ids["eur"]].value_eur == Decimal("163.40")  # 2 x 81.70: the report agrees
    assert by_id[ids["eur"]].reconciled_through is None
    # Before the report's day the report does not apply.
    earlier = read(ws, overview.assets_overview, date(2026, 6, 24))
    assert all(p.reconciled_through is None for p in earlier.accounts[0].positions)
