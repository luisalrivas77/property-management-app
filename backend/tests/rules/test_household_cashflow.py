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
    assert got.savings_rate == D("0.4899")  # surplus 1567.60 / income 3200.00
    assert got.open_lines == 2


def test_a_month_without_income_has_no_rate():
    got = month_cashflow("2026-02", [(C.HH_GROCERIES, D("-80.00"))], saved=D("0"))
    assert (got.income, got.surplus, got.savings_rate) == (D("0"), D("-80.00"), None)
    assert got.income_by_category == {}


def test_a_group_that_nets_to_zero_is_left_out():
    rows = [(C.HH_HEALTH, D("-30.00")), (C.HH_HEALTH, D("30.00"))]
    assert month_cashflow("2026-03", rows, saved=D("0")).spending_by_group == {}


def test_a_refund_is_a_negative_spending_group_never_income():
    """D159 amended 13 Nov 2026: hh_refund lowers the month's spending, shown as a negative
    group among the others; the income total does not move."""
    rows = [
        (C.HH_SALARY, D("2000.00")),
        (C.HH_CLOTHING, D("-120.00")),
        (C.HH_REFUND, D("45.00")),  # a returned order
    ]
    got = month_cashflow("2026-11", rows, saved=D("0"))
    assert got.income == D("2000.00")
    assert got.income_by_category == {C.HH_SALARY: D("2000.00")}
    assert got.spending_by_group == {G.CLOTHING: D("120.00"), G.REFUND: D("-45.00")}
    assert got.surplus == D("2000.00") - D("75.00")


def test_an_investment_is_saved_not_spent():
    """D159 amended 13 Nov 2026: a payment to an investment platform the app holds no account
    of is neither spending nor income; it is added to what the month saved, and money back
    from it lowers that."""
    rows = [
        (C.HH_SALARY, D("2000.00")),
        (C.HH_INVESTMENT, D("-300.00")),
        (C.HH_INVESTMENT, D("50.00")),
        (C.HH_DINING, D("-60.00")),
    ]
    got = month_cashflow("2026-11", rows, saved=D("100.00"))
    assert got.spending_by_group == {G.DINING: D("60.00")}
    assert got.saved == D("350.00")
    assert got.surplus == D("1940.00")


def test_each_group_is_split_by_its_codes():
    """D159 amended 14 Nov 2026 (migration 0032): spending_by_category gives each group's
    figure by its subcategories and its fallback, keyed by the group and signed as the
    group's; the codes of a group sum to the group, bank fees and uncategorized sit under their
    own codes inside „Sonstiges“, and a code that nets to zero is left out."""
    rows = [
        (C.HH_SALARY, D("3000.00")),
        (C.HH_SUPERMARKET, D("-80.00")),
        (C.HH_SUPERMARKET, D("-20.00")),
        (C.HH_DRUGSTORE, D("-15.50")),
        (C.HH_GROCERIES, D("-4.50")),  # the group's fallback
        (C.HH_STREAMING, D("-12.99")),
        (C.HH_MUSIC_AUDIO, D("-9.99")),
        (C.HH_MUSIC_AUDIO, D("9.99")),  # refunded: nets to zero
        (C.HH_POSTAGE, D("-3.70")),
        (C.BANK_FEES, D("-4.90")),
        (C.INTERNAL_TRANSFER_OUT, D("-500.00")),
    ]
    got = month_cashflow("2026-10", rows, saved=D("0"))
    assert got.spending_by_group == {
        G.GROCERIES: D("120.00"),
        G.SUBSCRIPTIONS: D("12.99"),
        G.OTHER: D("8.60"),
    }
    assert got.spending_by_category == {
        G.GROCERIES: {
            C.HH_SUPERMARKET: D("100.00"),
            C.HH_DRUGSTORE: D("15.50"),
            C.HH_GROCERIES: D("4.50"),
        },
        G.SUBSCRIPTIONS: {C.HH_STREAMING: D("12.99")},
        G.OTHER: {C.HH_POSTAGE: D("3.70"), C.BANK_FEES: D("4.90")},
    }
    for group, codes in got.spending_by_category.items():
        assert sum(codes.values()) == got.spending_by_group[group]


def test_a_group_that_nets_to_zero_has_no_codes_either():
    rows = [(C.HH_SUPERMARKET, D("-30.00")), (C.HH_GROCERIES, D("30.00"))]
    got = month_cashflow("2026-03", rows, saved=D("0"))
    assert (got.spending_by_group, got.spending_by_category) == ({}, {})
