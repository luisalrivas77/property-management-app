"""Phase 6 D178 amended (3), migration 0043: an exchange trade's legs are one movement group,
and a fee the trade paid in EUR is also its acquisition's fee_eur, so phase 7's lots
(rules/lots.py, D188 (1), (4)) cost the lot from the given coin's value plus the fee.

The pure half: `rules.exchange.trade_groups` and `trade_fee_eur` over Binance's and BISON's
invented texts, and the grouped movements fed into `lots.book`. The importer and the command
`hausbuch group-exchange-trades` are tests/db/test_services_exchange_trade_groups.py.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import fixture_models_phase6 as fixtures
import pytest

from hausbuch.rules import exchange
from hausbuch.schemas.household import (
    AssetMovement,
    ExchangeMovement,
    MovementKind,
    MovementSource,
    PricePoint,
    PriceSourceKind,
)
from hausbuch.services.exchange import binance_csv, bison_csv

BINANCE = fixtures.load("binance_csv")
BISON = fixtures.load("bison_csv")
HEADER = "User ID,Time,Account,Operation,Coin,Change,Remark\n"

# Invented: a SOL bought with USDC, the fee in BNB (no EUR anywhere in the trade).
COIN_FOR_COIN = HEADER + (
    "111111111,2026-03-02 10:15:00,Spot,Transaction Fee,BNB,-0.002,\n"
    "111111111,2026-03-02 10:15:00,Spot,Transaction Spend,USDC,-200,\n"
    "111111111,2026-03-02 10:15:00,Spot,Transaction Buy,SOL,1.5,\n"
)
# Invented: an ETH bought for EUR, the fee in EUR.
EUR_BUY = HEADER + (
    "111111111,2026-03-03 08:00:00,Spot,Transaction Fee,EUR,-0.25,\n"
    "111111111,2026-03-03 08:00:00,Spot,Transaction Spend,EUR,-100,\n"
    "111111111,2026-03-03 08:00:00,Spot,Transaction Buy,ETH,0.04,\n"
)
# Invented: an ETH sold for EUR, the fee in EUR; then a EUR deposit and a SOL withdrawal.
EUR_SALE_AND_TRANSFERS = HEADER + (
    "111111111,2026-03-04 09:00:00,Spot,Deposit,EUR,500,\n"
    "111111111,2026-03-04 12:30:00,Spot,Transaction Fee,EUR,-0.1,\n"
    "111111111,2026-03-04 12:30:00,Spot,Transaction Sold,ETH,-0.02,\n"
    "111111111,2026-03-04 12:30:00,Spot,Transaction Revenue,EUR,51.2,\n"
    "111111111,2026-03-05 07:45:00,Spot,Withdraw,SOL,-1,\n"
)


def _movements(text: str) -> list[ExchangeMovement]:
    return exchange.binance_movements(binance_csv.parse_transactions_text(text))


def _kinds(movements, legs):
    return sorted((movements[i].asset, movements[i].kind) for i in legs)


# --- Which legs are one trade ---------------------------------------------------------------


def test_a_coin_for_coin_trade_with_a_bnb_fee_is_one_group_of_three_without_a_eur_fee():
    movements = _movements(COIN_FOR_COIN)
    [legs] = exchange.trade_groups(movements, exchange.BINANCE)
    assert _kinds(movements, legs) == [
        ("BNB", MovementKind.FEE),
        ("SOL", MovementKind.ACQUISITION),
        ("USDC", MovementKind.DISPOSAL),
    ]
    assert exchange.trade_fee_eur([movements[i] for i in legs]) is None
    assert all(m.fee_eur is None for m in movements)
    assert all(m.eur_amount is None for m in movements)  # no EUR leg: valued by the lots


def test_a_eur_buy_with_a_eur_fee_carries_the_fee_on_its_acquisition():
    movements = _movements(EUR_BUY)
    [legs] = exchange.trade_groups(movements, exchange.BINANCE)
    assert _kinds(movements, legs) == [("ETH", MovementKind.ACQUISITION), ("EUR", MovementKind.FEE)]
    [buy] = [m for m in movements if m.kind is MovementKind.ACQUISITION]
    assert (buy.eur_amount, buy.fee_eur) == (Decimal("-100.00"), Decimal("0.25"))
    # The EUR fee movement stays: the EUR position counts it (D178 (6)).
    [fee] = [m for m in movements if m.kind is MovementKind.FEE]
    assert (fee.quantity, fee.eur_amount) == (Decimal("-0.25"), Decimal("-0.25"))
    assert exchange.trade_fee_eur([movements[i] for i in legs]) == Decimal("0.25")


def test_a_sale_is_grouped_with_its_fee_but_its_eur_fee_is_on_no_acquisition():
    movements = _movements(EUR_SALE_AND_TRANSFERS)
    [legs] = exchange.trade_groups(movements, exchange.BINANCE)
    assert _kinds(movements, legs) == [("ETH", MovementKind.DISPOSAL), ("EUR", MovementKind.FEE)]
    assert exchange.trade_fee_eur([movements[i] for i in legs]) is None
    assert all(m.fee_eur is None for m in movements)
    # The deposit and the withdrawal are in no group.
    grouped = set(legs)
    assert sorted(m.kind for i, m in enumerate(movements) if i not in grouped) == sorted(
        [MovementKind.INTERNAL_TRANSFER_IN, MovementKind.INTERNAL_TRANSFER_OUT]
    )


@pytest.mark.parametrize("case", BINANCE.cases, ids=lambda c: c.id)
def test_every_binance_fixture_trade_is_one_group_of_one_second(case):
    if case.error is not None:
        pytest.skip("a refused export writes no movement")
    movements = _movements(case.transactions)
    groups = exchange.trade_groups(movements, exchange.BINANCE)
    grouped = [i for legs in groups for i in legs]
    assert len(grouped) == len(set(grouped))  # no movement in two groups
    for legs in groups:
        assert len({movements[i].occurred_at for i in legs}) == 1
        assert any(movements[i].kind in exchange.TRADE_KINDS for i in legs)
    for i, m in enumerate(movements):
        assert (i in grouped) == (m.kind in exchange.TRADE_LEG_KINDS)


def test_a_bison_buy_or_sell_is_a_group_of_its_own_row():
    text = next(c for c in BISON.parse_cases if c.id == "real_layout").text
    movements = exchange.bison_movements(bison_csv.parse_text(text))
    groups = exchange.trade_groups(movements, exchange.BISON)
    assert [[movements[i].kind for i in legs] for legs in groups] == [[MovementKind.ACQUISITION]]
    [[i]] = groups
    assert movements[i].eur_amount is not None  # its own EUR leg: the lot's cost
    assert exchange.trade_fee_eur([movements[i]]) is None


def test_trade_groups_names_no_rule_for_another_exchange():
    with pytest.raises(ValueError):
        exchange.trade_groups([], "Kraken")


# --- What the lots make of a grouped trade (phase 7 D188 (1), (4)) ----------------------------

WS, ACCOUNT = uuid.uuid4(), uuid.uuid4()
CREATED = datetime(2026, 11, 16, 8, 0, tzinfo=UTC)


def _stored(movements, assets, *, grouped=True):
    """The movements as the importer stores them: each trade's legs with its group's id."""
    group_of = {}
    if grouped:
        for legs in exchange.trade_groups(movements, exchange.BINANCE):
            gid = uuid.uuid4()
            group_of |= dict.fromkeys(legs, gid)
    return [
        AssetMovement(
            id=uuid.uuid4(),
            workspace_id=WS,
            created_at=CREATED,
            account_id=ACCOUNT,
            asset_id=assets[m.asset],
            kind=m.kind,
            quantity=m.quantity,
            eur_amount=m.eur_amount,
            fee_eur=m.fee_eur,
            fee_quantity=m.fee_quantity,
            occurred_at=m.occurred_at,
            source=MovementSource.CSV,
            status=m.status,
            movement_hash=m.movement_hash,
            group_id=group_of.get(i),
        )
        for i, m in enumerate(movements)
    ]


def _lots():
    """rules/lots.py's book() (phase 7 step 6, merged into main after this branch's base);
    skipped where it is not there yet."""
    lots = pytest.importorskip("hausbuch.rules.lots")
    if not hasattr(lots, "book"):
        pytest.skip("rules/lots.py has no book() on this base (phase 7 step 6)")
    return lots


def _price(asset_id, on, eur):
    return PricePoint(
        asset_id=asset_id,
        on=on,
        price_eur=Decimal(eur),
        source=PriceSourceKind.COINGECKO_DAILY,
        fetched_at=CREATED,
    )


def test_a_grouped_coin_for_coin_trade_costs_the_given_coin_plus_the_bnb_fee():
    """SOL has no price on the day: the lot costs the USDC given (200 × 0.92 = 184.00) plus
    the BNB fee (0.002 × 500 = 1.00), 185.00 € for 1.5 SOL. Ungrouped (before 0043) the same
    movements left the lot unpriced."""
    lots = _lots()
    assets = {s: uuid.uuid4() for s in ("SOL", "USDC", "BNB")}
    day = date(2026, 3, 2)
    prices = [_price(assets["USDC"], day, "0.92"), _price(assets["BNB"], day, "500")]
    movements = _movements(COIN_FOR_COIN)

    book = lots.book(_stored(movements, assets), [], prices, day)
    [sol] = [lot for lot in book.lots if lot.asset_id == assets["SOL"]]
    assert (sol.units, sol.cost_eur) == (Decimal("1.5"), Decimal("185.00"))

    before = lots.book(_stored(movements, assets, grouped=False), [], prices, day)
    [sol] = [lot for lot in before.lots if lot.asset_id == assets["SOL"]]
    assert sol.cost_eur is None


def test_a_grouped_eur_buy_costs_its_eur_leg_plus_its_eur_fee():
    """The caller passes no EUR movement to the lots; the EUR fee reaches the cost through the
    acquisition's fee_eur: 100.00 + 0.25 = 100.25 € for 0.04 ETH, with no price needed."""
    lots = _lots()
    assets = {s: uuid.uuid4() for s in ("ETH", "EUR")}
    movements = [m for m in _movements(EUR_BUY) if m.asset != exchange.EUR]
    book = lots.book(_stored(movements, assets), [], [], date(2026, 3, 3))
    [eth] = book.lots
    assert (eth.units, eth.cost_eur) == (Decimal("0.04"), Decimal("100.25"))
