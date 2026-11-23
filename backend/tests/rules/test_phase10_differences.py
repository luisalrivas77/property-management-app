"""„Was diesen Monat anders ist“ (docs/phases/phase-10.md §7.6 Haushalt (3), build decision
(a)): rules/budget/differences.py. Table cases in the fixtures' style, synthetic figures only."""

from decimal import Decimal

import pytest

from hausbuch.rules.budget.differences import differences, spending_group
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
