"""„Was diesen Monat anders ist“ (docs/phases/phase-10.md §7.6 Haushalt (3), build decision
(a)): rules/budget/differences.py. Table cases in the fixtures' style, synthetic figures only."""

from decimal import Decimal

import pytest

from hausbuch.rules.budget.differences import (
    category_differences,
    compared_by_category,
    compared_by_group,
    compared_group,
    differences,
    spending_group,
)
from hausbuch.rules.household.cashflow import month_cashflow
from hausbuch.rules.household.taxonomy import ONE_OFF
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.health import DifferenceWords
from hausbuch.schemas.household import HouseholdGroup as G

D = Decimal


def months(*amounts: str, group: G = G.DINING) -> list[dict[G, Decimal]]:
    """One earlier month per amount, the group's spending in it ("0" leaves it out)."""
    return [{group: D(a)} if D(a) else {} for a in amounts]


@pytest.mark.parametrize(
    ("case", "now", "before", "expected"),
    [
        # (mean, difference) of the one group named, or None when nothing is named.
        ("both thresholds met", "180.00", ["100.00"] * 3, ("100.00", "80.00")),
        ("exactly 50 € and 25 %: inclusive", "250.00", ["200.00"] * 3, ("200.00", "50.00")),
        ("49.99 € is not enough", "249.99", ["200.00"] * 3, None),
        ("50 € but below 25 % of the mean", "450.00", ["400.00"] * 3, None),
        ("25 % but below 50 €", "130.00", ["100.00"] * 3, None),
        ("exactly 25 % of a larger mean", "500.00", ["400.00"] * 3, ("400.00", "100.00")),
        ("less than usual", "20.00", ["100.00"] * 3, ("100.00", "-80.00")),
        ("absent this month counts 0", "0", ["90.00", "60.00", "90.00"], ("80.00", "-80.00")),
        ("new this month (mean 0): 50 € alone", "50.00", ["0"] * 3, ("0.00", "50.00")),
        ("new this month below 50 €", "49.00", ["0"] * 3, None),
        ("a month without the group counts 0", "200.00", ["150.00", "0", "0"], ("50.00", "150.00")),
        (
            "mean to the cent half-up",
            "200.00",
            ["100.00", "100.00", "100.01"],
            ("100.00", "100.00"),
        ),
        ("a refund month: negative mean", "40.00", ["-30.00", "0", "-30.00"], ("-20.00", "60.00")),
    ],
)
def test_the_thresholds(case, now, before, expected):
    del case
    named = differences({G.DINING: D(now)} if D(now) else {}, months(*before))
    if expected is None:
        assert named == []
        return
    assert len(named) == 1
    (one,) = named
    assert one.group is G.DINING
    assert (one.mean, one.difference) == (D(expected[0]), D(expected[1]))
    assert one.amount == D(now)
    assert one.words is (DifferenceWords.MORE if one.difference > 0 else DifferenceWords.LESS)


@pytest.mark.parametrize(("count", "shown"), [(0, False), (2, False), (3, True), (12, True)])
def test_three_earlier_closed_months_are_needed(count, shown):
    named = differences({G.DINING: D("500.00")}, months(*["100.00"] * count))
    assert (named is not None) is shown
    if shown:
        assert named and named[0].group is G.DINING


def test_at_most_three_the_largest_first_a_tie_in_the_groups_order():
    before = [
        {
            G.DINING: D("100.00"),
            G.GROCERIES: D("400.00"),
            G.MOBILITY: D("100.00"),
            G.CLOTHING: D("100.00"),
            G.HEALTH: D("100.00"),
        }
    ] * 3
    now = {
        G.DINING: D("300.00"),  # +200
        G.GROCERIES: D("100.00"),  # −300
        G.MOBILITY: D("200.00"),  # +100
        G.CLOTHING: D("200.00"),  # +100, a tie with MOBILITY
        G.HEALTH: D("110.00"),  # +10, not named
    }
    named = differences(now, before)
    assert [(d.group, d.difference) for d in named] == [
        (G.GROCERIES, D("-300.00")),
        (G.DINING, D("200.00")),
        (G.MOBILITY, D("100.00")),  # MOBILITY comes before CLOTHING in HouseholdGroup
    ]


def test_nothing_differs_enough():
    assert differences({G.DINING: D("100.00")}, months("100.00", "90.00", "110.00")) == []


@pytest.mark.parametrize(
    ("category", "group"),
    [
        (C.HH_GROCERIES, G.GROCERIES),
        (C.BANK_FEES, G.OTHER),
        (C.UNCATEGORIZED, G.OTHER),
        (C.HH_SALARY, None),  # income
        (C.HH_INVESTMENT_PROPERTY, None),  # Geldanlage: saved, not spent
        (C.INTERNAL_TRANSFER_OUT, None),
    ],
)
def test_a_line_counts_under_the_group_month_cashflow_sums_it_under(category, group):
    assert spending_group(category) is group


@pytest.mark.parametrize("category", list(C), ids=lambda c: c.value)
def test_spending_group_is_the_group_month_cashflow_counts_a_line_under(category):
    """For every code: a payment listed under a named group is exactly a line month_cashflow
    sums into that group's spending, so a group's payments add up to its month, and a code
    it does not count as spending (income, Geldanlage, transfers) is listed under none."""
    counted = month_cashflow("2027-03", [(category, Decimal("-10.00"))], saved=Decimal("0.00"))
    assert set(counted.spending_by_group) == ({spending_group(category)} - {None})


@pytest.mark.parametrize("category", list(C))
def test_compared_by_group_is_month_cashflow_less_one_offs_and_refunds(category):
    """The comparison's month equals month_cashflow's spending per group with one-off codes and
    refunds left out, so a named group's payments add up to the figure shown."""
    counted = month_cashflow("2027-03", [(category, Decimal("-10.00"))], saved=Decimal("0.00"))
    expected = {
        g: v
        for g, v in counted.spending_by_group.items()
        if category not in ONE_OFF and g is not G.REFUND
    }
    assert compared_by_group([(category, Decimal("-10.00"))]) == expected
    assert compared_group(category) == (next(iter(expected)) if expected else None)


# --- Per category for any chosen month (phase 10 §7.13 (b) 2, D314) ----------------------


def code_months(*amounts: str, code: C = C.HH_SUPERMARKET) -> list[dict[C, Decimal]]:
    return [{code: D(a)} if D(a) else {} for a in amounts]


@pytest.mark.parametrize(
    ("case", "now", "final", "expected"),
    [
        ("more, a closed month", "384.00", True, "84.00"),
        ("more, the month in progress", "384.00", False, "84.00"),
        ("less, a closed month", "200.00", True, "-100.00"),
        ("less while the month runs: not named", "200.00", False, None),
        ("below the thresholds", "320.00", True, None),
    ],
)
def test_a_code_against_its_mean(case, now, final, expected):
    del case
    named = category_differences(
        {C.HH_SUPERMARKET: D(now)}, code_months("300.00", "300.00", "300.00"), final=final
    )
    assert named is not None
    if expected is None:
        assert named == []
        return
    (one,) = named
    assert (one.category, one.group, one.mean) == (C.HH_SUPERMARKET, G.GROCERIES, D("300.00"))
    assert one.difference == D(expected)
    assert one.words is (DifferenceWords.MORE if one.difference > 0 else DifferenceWords.LESS)


@pytest.mark.parametrize(("count", "shown"), [(0, False), (2, False), (3, True)])
def test_per_code_three_earlier_closed_months_are_needed(count, shown):
    named = category_differences(
        {C.HH_SUPERMARKET: D("500.00")}, code_months(*["100.00"] * count), final=True
    )
    assert (named is not None) is shown


def test_every_code_is_named_the_largest_first_then_groups_then_codes():
    before = [{C.HH_SUPERMARKET: D("100.00"), C.HH_BAKERY: D("100.00"), C.HH_FUEL: D("100.00")}]
    now = {
        C.HH_SUPERMARKET: D("200.00"),  # +100, groceries
        C.HH_BAKERY: D("200.00"),  # +100, groceries, a tie in the same group
        C.HH_FUEL: D("200.00"),  # +100, mobility
        C.HH_RESTAURANT: D("400.00"),  # new: +400
    }
    named = category_differences(now, before * 3, final=True)
    order = {c: i for i, c in enumerate(C)}
    ties = sorted([C.HH_SUPERMARKET, C.HH_BAKERY], key=order.__getitem__)
    assert [d.category for d in named] == [C.HH_RESTAURANT, *ties, C.HH_FUEL]
    assert len(named) == 4  # no cap of three: the screen folds the list


def test_compared_by_category_leaves_out_one_offs_refunds_and_what_is_not_spending():
    rows = [
        (C.HH_SUPERMARKET, D("-80.00")),
        (C.HH_SUPERMARKET, D("-20.00")),
        (C.HH_SALARY, D("3000.00")),
        (C.HH_INVESTMENT_PROPERTY, D("-500.00")),
        (C.HH_REFUND, D("15.00")),
        (C.HH_CAR_PURCHASE, D("-9000.00")),  # one-off by the taxonomy
    ]
    assert compared_by_category(rows) == {C.HH_SUPERMARKET: D("100.00")}
    assert compared_by_category(rows, one_off_codes=frozenset()) == {
        C.HH_SUPERMARKET: D("100.00"),
        C.HH_CAR_PURCHASE: D("9000.00"),
    }
