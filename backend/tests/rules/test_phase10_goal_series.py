"""The committed reference data of route 3 (phase 10 §7.11 (c) 3, „Route 3 as decided for the
build“ points 7, 8): rules/goal/history.yaml loads, its rates are contiguous, each coin has a
history, and it sits outside the folders rule_version() hashes. No real figure is asserted
beyond the shape: the values are the published files' and change only by regenerating."""

from decimal import Decimal

from hausbuch.rules.goal import series
from hausbuch.rules.goal.history import EUR_PEGGED, FUNDS, USD_PEGGED
from hausbuch.services.health import rule_version

WALK_RULE_VERSION = "5f75a9856b0a2a7e0d14cc596036e57a561920b9e08355d5e01eb4cb3c407d0a"


def test_rates_are_contiguous_from_1990():
    fx = series.fx()
    assert sorted(fx) == list(range(1990, max(fx) + 1))
    assert all(rate > 0 for rate in fx.values())
    rates = series.committed()["fx"]
    assert min(int(y) for y in rates["ecb_usd_per_eur"]["values"]) == 1999
    assert sorted(int(y) for y in rates["bbk_dem_per_usd"]["values"]) == list(range(1990, 1999))


def test_every_coin_has_a_history_and_bitcoin_starts_2014():
    coins = series.coin_series()
    for sym, s in coins.items():
        assert len(s.returns) >= 1, sym
        assert s.key == sym
        assert s.attribution and s.licence and s.url
    assert min(coins["BTC"].returns) == 2014
    for absent in ("SOL", "AVAX", "POL", "MATIC"):
        assert absent not in coins


def test_stablecoins_follow_their_currency():
    coins = series.coin_series()
    for sym in EUR_PEGGED:
        assert set(coins[sym].returns.values()) == {0}
    for sym in USD_PEGGED:
        assert coins[sym].returns == coins["USDT"].returns
        assert min(coins[sym].returns) == 1991


def test_the_funds_series_is_built_from_the_download_only():
    assert series.history(None).funds is None
    rows = {1991: (Decimal("10.00"), Decimal("5.00")), 1992: (Decimal("-3.50"), Decimal("4.00"))}
    funds = series.history(rows).funds
    assert funds is not None and funds.key == FUNDS
    assert sorted(funds.returns) == [1991, 1992]
    assert "eigene Berechnung" in funds.attribution


def test_the_committed_data_does_not_move_the_rule_version():
    assert rule_version() == WALK_RULE_VERSION
