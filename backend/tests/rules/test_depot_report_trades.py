"""A quarterly report's Umsätze list as movements (phase 6 D165 amended, 15 Nov 2026):
rules.depot.bookable_trades and rules.depot.report_trade_movement. Invented ISINs and
references only."""

from datetime import date
from decimal import Decimal

import pytest

from hausbuch.rules import depot
from hausbuch.schemas.household import MovementKind

pytestmark = pytest.mark.rule

ISIN = "IE00BEISPL02"


def _trade(reference, on=date(2026, 3, 13), units="4.8", amount="-500.00", isin=ISIN):
    return depot.ProposedTrade(
        isin=isin,
        trade_date=on,
        units=Decimal(units),
        amount=Decimal(amount),
        reference=reference,
    )


def test_only_trades_code_can_tell_apart_are_bookable():
    trades = [
        _trade("5500000001"),
        _trade(" 5500000001 "),  # the list repeats an earlier trade
        _trade("5500000002", on=date(2026, 7, 1)),  # after the report's date
        _trade("unbekannt"),
        _trade(""),
        _trade("5500000003", units="0"),
        _trade("5500000004", isin="kein ISIN"),
        _trade("5500000005"),
        _trade("5500000006"),
    ]
    found = depot.bookable_trades(trades, date(2026, 6, 30), booked={"5500000006"})
    assert [t.reference for t in found] == ["5500000001", "5500000005"]


def test_a_purchase_and_a_sale_take_their_cash_sign_from_the_side():
    buy = depot.report_trade_movement(_trade(" 5500000001 ", amount="500.00"))
    assert (buy.kind, buy.quantity, buy.eur_amount, buy.external_ref) == (
        MovementKind.ACQUISITION,
        Decimal("4.8"),
        Decimal("-500.00"),
        "5500000001",
    )
    sale = depot.report_trade_movement(_trade("5500000002", units="-2.4", amount="-250.00"))
    assert (sale.kind, sale.quantity, sale.eur_amount, sale.occurred_on) == (
        MovementKind.DISPOSAL,
        Decimal("-2.4"),
        Decimal("250.00"),
        date(2026, 3, 13),
    )
