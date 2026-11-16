"""rules/holdings.py: the Krypto tab's figures over open lots (docs/phases/phase-7.md D188 (4),
(5); step 6). Invented coins and prices."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from hausbuch.rules import holdings as H
from hausbuch.rules.lots import tax_free_from
from hausbuch.schemas.chain import Lot, LotSource, PriceNeed
from hausbuch.schemas.holdings import CryptoAccount
from hausbuch.schemas.prices import PricePoint, PriceSourceKind

ACCOUNT = uuid.uuid4()
SOL, BNB = uuid.uuid4(), uuid.uuid4()
ON = date(2026, 7, 1)


def _lot(units, cost, acquired: date, source=LotSource.ACQUISITION) -> Lot:
    return Lot(
        id=uuid.uuid4(),
        movement_id=uuid.uuid4(),
        acquired_at=datetime(acquired.year, acquired.month, acquired.day, 10, tzinfo=UTC),
        account_id=ACCOUNT,
        asset_id=SOL,
        acquired_on=acquired,
        units=Decimal(units),
        units_open=Decimal(units),
        cost_eur=None if cost is None else Decimal(cost),
        source=source,
        tax_free_from=tax_free_from(acquired),
    )


def _price(price, asset=SOL) -> PricePoint:
    return PricePoint(
        asset_id=asset,
        on=ON,
        price_eur=Decimal(price),
        source=PriceSourceKind.COINGECKO_DAILY,
        fetched_at=datetime(2026, 7, 1, 6, tzinfo=UTC),
    )


def _coin(lots, price, needs=None):
    return H.coin(
        account_id=ACCOUNT,
        asset_id=SOL,
        symbol="SOL",
        name="Solana",
        units=sum((lot.units_open for lot in lots), Decimal(0)),
        lots=lots,
        price=price,
        needs=needs or {},
        symbols={SOL: "SOL", BNB: "BNB"},
        on=ON,
    )


def test_a_coin_splits_free_and_waiting_lots():
    old = _lot("1", "100.00", date(2025, 1, 10))
    new = _lot("0.333", "40.00", date(2026, 2, 1))
    coin = _coin([old, new], _price("150.00"))
    assert coin.value_eur == Decimal("199.95")
    assert coin.cost_eur == Decimal("140.00") and coin.unrealized_eur == Decimal("59.95")
    assert coin.tax_free_units == Decimal("1") and coin.tax_free_value_eur == Decimal("150.00")
    assert coin.next_tax_free_from == tax_free_from(date(2026, 2, 1))
    assert coin.next_tax_free_units == Decimal("0.333")
    assert [lot.tax_free for lot in coin.lots] == [True, False]
    assert coin.lots[1].value_eur == Decimal("49.95")


def test_an_unpriced_lot_is_never_a_cost_of_zero():
    priced = _lot("1", "100.00", date(2026, 1, 10))
    unpriced = _lot("0.5", None, date(2026, 3, 1), source=LotSource.ACQUISITION)
    unknown = _lot("0.2", None, date(2026, 4, 1), source=LotSource.UNKNOWN)
    needs = {
        unpriced.movement_id: [PriceNeed(asset_id=BNB, on=date(2026, 3, 1))],
        unknown.movement_id: [PriceNeed(asset_id=SOL, on=date(2026, 4, 1))],
    }
    coin = _coin([priced, unpriced, unknown], _price("150.00"), needs)
    assert coin.cost_eur is None and coin.unrealized_eur is None
    assert (coin.cost_priced_eur, coin.unpriced_lots) == (Decimal("100.00"), 2)
    assert [(n.symbol, n.on) for n in coin.lots[1].price_needs] == [("BNB", date(2026, 3, 1))]
    assert coin.lots[2].price_needs == []  # unknown: no price can give it a cost


def test_the_cards_leave_out_what_has_no_number():
    priced = _coin([_lot("1", "100.00", date(2025, 1, 10))], _price("150.00"))
    no_price = _coin([_lot("2", "10.00", date(2025, 1, 10)), _lot("1", "5.00", ON)], None)
    account = CryptoAccount(
        account_id=ACCOUNT, label="Wallet", kind="wallet", value_eur=Decimal("0.00")
    )
    seen = H.summary(ON, [priced, no_price], [account])
    assert (seen.total_eur, seen.unvalued_coins) == (Decimal("150.00"), 1)
    assert (seen.unrealized_eur, seen.unrealized_excluded) == (Decimal("50.00"), 1)
    assert (seen.tax_free_today_eur, seen.tax_free_today_excluded) == (Decimal("150.00"), 1)
    assert seen.next_tranche.on == tax_free_from(ON)
    assert seen.next_tranche.value_eur is None  # its coin has no price
    assert seen.accounts[0].value_eur == Decimal("150.00")
    assert seen.accounts[0].unvalued_coins == 1


def test_nothing_held_has_no_tranche():
    seen = H.summary(ON, [], [])
    assert seen.next_tranche is None and seen.total_eur == Decimal("0.00")
