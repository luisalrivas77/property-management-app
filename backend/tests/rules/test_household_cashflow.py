"""rules.household.cashflow: the Haushalt month (docs/phases/phase-6.md §13). Invented
amounts only."""

from decimal import Decimal

import pytest

from hausbuch.rules.household.cashflow import month_cashflow
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.household import HouseholdGroup as G

D = Decimal
pytestmark = pytest.mark.rule


def test_income_spending_saved_and_the_rate():
    rows = [
        (C.HH_SALARY, D("3000.00")),
        (C.HH_OTHER_INCOME, D("200.00")),
        (C.HH_GROCERIES, D("-450.00")),
        (C.HH_GROCERIES, D("20.00")),  # a refund lowers its group
        (C.HH_HOUSING, D("-900.00")),
        (C.HH_PROPERTY_COST, D("-297.50")),  # tax-relevant spending, under its group
        (C.BANK_FEES, D("-4.90")),  # a shared code: Sonstiges
        (C.INTERNAL_TRANSFER_OUT, D("-500.00")),  # a transfer is never spending
        (C.SECURITY_PURCHASE, D("-100.00")),  # nor a depot trade
    ]
    got = month_cashflow("2026-10", rows, saved=D("500.00"), open_lines=2)
    assert got.income == D("3200.00")
    assert got.income_by_category == {C.HH_SALARY: D("3000.00"), C.HH_OTHER_INCOME: D("200.00")}
    assert got.spending_by_group == {
        G.GROCERIES: D("430.00"),
        G.HOUSING: D("900.00"),
        G.TAX: D("297.50"),
        G.OTHER: D("4.90"),
    }
    assert got.surplus == D("3200.00") - D("1632.40")
    assert got.saved == D("500.00")
    assert got.savings_rate == D("0.1563")
    assert got.open_lines == 2


def test_a_month_without_income_has_no_rate():
    got = month_cashflow("2026-02", [(C.HH_GROCERIES, D("-80.00"))], saved=D("0"))
    assert (got.income, got.surplus, got.savings_rate) == (D("0"), D("-80.00"), None)
    assert got.income_by_category == {}


def test_a_group_that_nets_to_zero_is_left_out():
    rows = [(C.HH_HEALTH, D("-30.00")), (C.HH_HEALTH, D("30.00"))]
    assert month_cashflow("2026-03", rows, saved=D("0")).spending_by_group == {}
