"""The Konten tabs' parts (phase 10 §7.13 (a), D313): GET /net-worth/overview splits today's
account and position lines per account and per tab, so the depot tab shows the depot's whole
value (the Anlagen portfolio card's total, PR #553) and the card tab what is owed beside the
money on its way to the card. Invented IBANs and amounts only."""

from decimal import Decimal

import pytest
from db.test_services_net_worth import NOW, ON
from db.test_services_net_worth_transit import _card_world, _world

from hausbuch.schemas.net_worth import AccountTab
from hausbuch.services import net_worth

pytestmark = pytest.mark.db


def test_the_depot_tab_shows_funds_cash_and_money_on_its_way(run, read, fresh_workspaces):
    from db.test_services_prices_fetch import _asset, _held
    from db.test_services_prices_overview import S, _price

    from hausbuch.services.prices import overview

    ws, _ = fresh_workspaces
    _, depot, _ = _world(run, ws)

    def fund(ctx):
        asset = _asset(ctx, kind="etf", isin="IE000PART001", name="Ein Fonds")
        _held(ctx, depot, asset, "4")
        _price(ctx, asset, ON, "25.00", S.USER)

    run(ws, fund, now=NOW)
    seen = read(ws, net_worth.overview, ON)
    parts = seen.today_accounts[depot]
    assert (parts.balance_eur, parts.positions_eur, parts.in_transit_eur) == (
        Decimal("200.00"),
        Decimal("100.00"),
        Decimal("500.00"),
    )
    assert parts.value_eur == Decimal("800.00")
    [portfolio] = read(ws, overview.etf_today, ON).accounts
    assert parts.value_eur == portfolio.total_eur
    tab = seen.today_tabs[AccountTab.DEPOT]
    assert tab.value_eur == seen.today.by_account_tab[AccountTab.DEPOT]
    assert tab == parts  # one depot


def test_the_card_tab_shows_the_debt_and_the_payment_on_its_way(run, read, fresh_workspaces):
    ws, _ = fresh_workspaces
    current, card = _card_world(run, ws)
    seen = read(ws, net_worth.overview, ON)
    parts = seen.today_accounts[card]
    assert (parts.balance_eur, parts.in_transit_eur, parts.value_eur) == (
        Decimal("-300.00"),
        Decimal("300.00"),
        Decimal("0.00"),
    )
    assert seen.today_tabs[AccountTab.CARD] == parts
    assert seen.today_accounts[current].in_transit_eur == Decimal("0.00")
    # Every tab's value is the figure by_account_tab already served.
    assert {t: p.value_eur for t, p in seen.today_tabs.items()} == seen.today.by_account_tab
