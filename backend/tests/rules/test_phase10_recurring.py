"""rules/recurring against tests/fixtures/phase10/recurring.yaml and one_off_marks.yaml
(docs/phases/phase-10.md §7.9 (b), (c); D296, D297). The candidate search of D297 went with
§7.12 (b), D307; what a mark changes is still tested here."""

from datetime import date
from decimal import Decimal

import fixture_models_phase10 as phase10
import pytest

from hausbuch.rules.budget import budget as B
from hausbuch.rules.budget.differences import compared_by_group, compared_rows
from hausbuch.rules.health.month import cashflow_rows, household_month
from hausbuch.rules.household.cashflow import month_cashflow
from hausbuch.rules.matching import payee, same_payee
from hausbuch.rules.plan import spending as SP
from hausbuch.rules.plan.surplus import mean_cent
from hausbuch.rules.recurring import (
    SeriesMark,
    newest_allocation_marks,
    one_off_ids,
    recurring,
)
from hausbuch.rules.recurring.series import payee_groups
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.budget import BudgetTargets
from hausbuch.schemas.health import MonthLine
from hausbuch.schemas.household import HouseholdGroup
from hausbuch.schemas.recurring import RecurringInterval, RecurringState, SpendingMarkAnswer

RECURRING = phase10.load("recurring").cases
ONE_OFF = phase10.load("one_off_marks").cases


def _row(series) -> dict:
    return {
        "key": series.key,
        "interval": series.interval,
        "state": series.state,
        "last_amount": series.last_amount,
        "yearly_total": series.yearly_total,
        "change": series.change,
        "since": series.since,
        "payments": series.payments,
    }


def _rows(rows) -> list[dict]:
    return sorted((_row(r) for r in rows), key=lambda r: (r["key"], r["interval"].value))


@pytest.mark.parametrize("case", RECURRING, ids=lambda c: c.id)
def test_recurring(case: phase10.RecurringCase):
    view = recurring(
        phase10.spending_lines(case.lines, case.id),
        [SeriesMark(m.key, m.interval, m.answer) for m in case.marks],
        today=case.today,
        history_start=case.history_start,
        accounts_through=phase10.accounts_through(case.accounts_through, case.id),
    )
    assert view.yearly_detectable is case.expect.yearly_detectable
    assert _rows(view.series) == _rows(case.expect.series)
    assert _rows(view.hidden) == _rows(case.expect.hidden)
    for series in [*view.series, *view.hidden]:
        assert series.payments == len(series.sources) == len(series.lines)
        assert sum(li.amount for li in series.lines) > 0


def test_price_changes_come_first_and_totals_count_active_series():
    case = next(c for c in RECURRING if c.id == "monthly_price_changes")
    view = recurring(
        phase10.spending_lines(case.lines, case.id),
        today=case.today,
        history_start=case.history_start,
    )
    assert [s.change is not None for s in view.series] == [True, True, False]
    assert view.count == 3
    assert view.yearly_total == Decimal("191.88") + Decimal("478.80") + Decimal("1008.00")


def test_a_maybe_ended_series_is_not_counted():
    case = next(c for c in RECURRING if c.id == "stopped")
    view = recurring(
        phase10.spending_lines(case.lines, case.id),
        today=case.today,
        history_start=case.history_start,
        accounts_through=phase10.accounts_through(case.accounts_through, case.id),
    )
    assert [s.state for s in view.series] == [RecurringState.MAYBE_ENDED]
    assert view.count == 0 and view.yearly_total == 0


def test_a_rise_beyond_the_band_stays_a_price_change():
    """D296: every amount within 25 % of the median „apart from a price change“."""
    rows = [
        phase10.SpendingLineIn(
            id=f"X_{m:02d}",
            date=date(2026, m, 5),
            payee="Fitness Studio GmbH",
            category="hh_sport",
            amount=Decimal("-20.00") if m < 9 else Decimal("-30.00"),
        )
        for m in range(1, 12)
    ]
    view = recurring(
        phase10.spending_lines(rows, "rise"), today=date(2026, 11, 24), history_start=None
    )
    (series,) = view.series
    assert series.interval is RecurringInterval.MONTHLY
    assert (series.change, series.since, series.payments) == (Decimal("10.00"), "2026-09", 11)
    assert view.yearly_detectable is False


def _reference_groups(lines) -> set[frozenset]:
    """payee_groups as first written: every pair of spellings through same_payee on the lines
    themselves (the speed-up compares their words read once; the groups must not change)."""
    by_words: dict = {}
    first: dict = {}
    for line in lines:
        words = payee(line.payee, line.purpose).words
        if words:
            by_words.setdefault(words, []).append(line)
            first.setdefault(words, line)
    keys = list(by_words)
    parent = list(range(len(keys)))

    def root(i: int) -> int:
        while parent[i] != i:
            i = parent[i]
        return i

    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            la, lb = first[keys[i]], first[keys[j]]
            if root(i) != root(j) and same_payee(la.payee, lb.payee, la.purpose, lb.purpose):
                parent[root(j)] = root(i)
    groups: dict = {}
    for i, words in enumerate(keys):
        groups.setdefault(root(i), set()).update(li.allocation_id for li in by_words[words])
    return {frozenset(g) for g in groups.values()}


def test_payee_groups_equal_same_payee_pairwise():
    names = [
        ("PayPal Europe S.a.r.l. et Cie S.C.A", "PP.1 . Spotify AB, Ihr Einkauf bei Spotify AB"),
        ("PayPal Europe S.a.r.l. et Cie S.C.A", "PP.2 . DisneyPlus, Ihr Einkauf bei DisneyPlus"),
        ("PayPal Europe S.a.r.l. et Cie S.C.A", None),
        ("SPOTIFY P1A2B3", None),
        ("Spotify AB", None),
        ("DisneyPlus - PayPal", None),
        ("KAUFLAND DUESSELDORF 1234", None),
        ("Kaufland", None),
        ("ALDI TALK", None),
        ("ALDI SUED", None),
        ("ALDI", None),
        ("BOOKINGCOMB BO MARKTPLEIN", None),
        ("BOOKING BOOKIN MARKTPLEIN", None),
        ("PENNY MARKT DUESSELDORF", None),
        ("PENNYLANE PUB DUESSELDORF", None),
        ("HOTEL AT BOOKING AMSTERDAM", None),
        ("HOTEL AT BOOKING ROME", None),
        ("VOLKSWAGEN AUTOVERSICHERUNG AG", None),
        ("VOLKSWAGEN AUTOVERS AG", None),
        ("WWW.DAZN.COM Huidekoper", None),
        ("DAZN Limited", None),
        ("12345", None),
        ("", None),
    ]
    rows = [
        phase10.SpendingLineIn(
            id=f"P_{i:02d}_{k}",
            date=date(2026, 1 + k, 3),
            payee=name or " ",
            purpose=purpose,
            category="hh_streaming",
            amount=Decimal("-9.99"),
        )
        for i, (name, purpose) in enumerate(names)
        for k in range(2)
    ]
    for case in RECURRING:
        rows += case.lines
    lines = phase10.spending_lines(rows, "groups")
    got = {frozenset(li.allocation_id for li in g) for g in payee_groups(lines)}
    assert got == _reference_groups(lines)


def _window(today: date) -> list[str]:
    """The fixture's closed window: every ended month of 2026 before today's, at most 12."""
    months = [f"2026-{m:02d}" for m in range(1, today.month)] if today.year == 2026 else []
    return months[-12:]


def test_newest_answer_counts():
    a = phase10.fixture_id("A")
    answers = newest_allocation_marks(
        [(a, SpendingMarkAnswer.ONE_OFF), (a, SpendingMarkAnswer.REGULAR)]
    )
    assert answers == {a: SpendingMarkAnswer.REGULAR}


# --- What a one-off mark changes (§7.9 (c) 2, Q-O183 (a)) -------------------------------------


def _months(case: phase10.OneOffCase):
    """The window's household months with the case's marks, their lines and the marked ids,
    through the production functions (month_cashflow, household_month), as services.health
    reads them."""
    marked = one_off_ids(
        newest_allocation_marks((phase10.fixture_id(m.allocation), m.answer) for m in case.marks)
    )
    months, lines = [], []
    for month in _window(case.today):
        rows = [
            MonthLine(allocation_id=phase10.fixture_id(r.id), category=r.category, amount=r.amount)
            for r in case.lines
            if f"{r.date.year:04d}-{r.date.month:02d}" == month
        ]
        cashflow = month_cashflow(month, cashflow_rows(rows), saved=Decimal("0.00"))
        months.append((cashflow, household_month(cashflow, lines=rows, one_off_ids=marked)))
        lines.append(rows)
    return months, lines, marked


FIGURE_CASES = [c for c in ONE_OFF if c.expect.months]


@pytest.mark.parametrize("case", FIGURE_CASES, ids=lambda c: c.id)
def test_a_one_off_mark_moves_the_payment(case: phase10.OneOffCase):
    months, lines, marked = _months(case)
    by_month = {hm.month: hm for _, hm in months}
    for want in case.expect.months:
        got = by_month[want.month]
        assert (got.private_spending, got.one_off_spending, got.regular_spending) == (
            want.private_spending,
            want.one_off_spending,
            want.regular_spending,
        )
    regular = [hm.regular_spending for _, hm in months]
    assert mean_cent(regular) == case.expect.mean_regular_spending

    per_code = [
        SP.month_spending(cf, rows, marked) for (cf, _), rows in zip(months, lines, strict=True)
    ]
    for (_, hm), codes in zip(months, per_code, strict=True):
        assert sum(codes.values(), Decimal("0.00")) == hm.regular_spending
    means = SP.code_means(per_code)
    for code, want in case.expect.code_means.items():
        assert means.get(code, Decimal("0.00")) == want

    view = B.budget(
        [hm for _, hm in months],
        lines,
        {},
        BudgetTargets(),
        as_of=case.today,
        one_off_ids=marked,
    )
    one_off = {m.month: m.one_off for m in view.months}
    for month, want in case.expect.budget_one_off.items():
        assert one_off[month] == want
    for m, (_, hm) in zip(view.months, months, strict=True):
        assert m.one_off == hm.one_off_spending
        assert m.need + m.want + m.uncategorized == hm.regular_spending


def test_without_marks_nothing_moves():
    """The default argument: every phase 9 and 10 figure as before (§7.9 (c) 2)."""
    case = next(c for c in ONE_OFF if c.id == "no_marks")
    months, lines, marked = _months(case)
    assert marked == frozenset()
    for (cf, hm), rows in zip(months, lines, strict=True):
        assert household_month(cf, lines=rows) == hm
        assert SP.month_spending(cf) == SP.month_spending(cf, rows, marked)


def test_a_marked_payment_leaves_the_differences():
    case = next(c for c in ONE_OFF if c.id == "both_marked")
    _, lines, marked = _months(case)
    october = lines[-1]
    plain = compared_by_group(compared_rows(october))
    moved = compared_by_group(compared_rows(october, marked))
    gone = plain[HouseholdGroup.MOBILITY] - moved.get(HouseholdGroup.MOBILITY, Decimal("0"))
    assert gone == Decimal("870.00")


def test_a_mark_on_a_refund_a_one_off_code_or_a_purchase_cost_moves_nothing():
    car = MonthLine(
        allocation_id=phase10.fixture_id("CAR"),
        category=AllocationCategory.HH_CAR_PURCHASE,
        amount=Decimal("-8500.00"),
    )
    refund = MonthLine(
        allocation_id=phase10.fixture_id("REF"),
        category=AllocationCategory.HH_REFUND,
        amount=Decimal("30.00"),
    )
    notary = MonthLine(  # D234: invested, not spent
        allocation_id=phase10.fixture_id("NOTARY"),
        category=AllocationCategory.HH_PROPERTY_COST,
        amount=Decimal("-4200.00"),
        purchase_cost=True,
    )
    shop = MonthLine(
        allocation_id=phase10.fixture_id("SHOP"),
        category=AllocationCategory.HH_SUPERMARKET,
        amount=Decimal("-100.00"),
    )
    rows = [car, refund, notary, shop]
    cashflow = month_cashflow("2026-03", cashflow_rows(rows), saved=Decimal("0.00"))
    plain = household_month(cashflow, lines=rows)
    marked = {car.allocation_id, refund.allocation_id, notary.allocation_id}
    assert household_month(cashflow, lines=rows, one_off_ids=marked) == plain
    assert SP.month_spending(cashflow, rows, marked) == SP.month_spending(cashflow)


def _line(key: str, code: AllocationCategory, amount: str) -> MonthLine:
    return MonthLine(allocation_id=phase10.fixture_id(key), category=code, amount=Decimal(amount))


@pytest.mark.parametrize(
    "refund_code",
    [AllocationCategory.HH_SUPERMARKET, AllocationCategory.HH_GROCERIES],
    ids=["code_nets_to_zero", "group_nets_to_zero_across_two_codes"],
)
def test_a_marked_payment_whose_code_or_group_nets_to_zero(refund_code):
    """month_cashflow drops a code, and a group, that nets to 0 in a month (a purchase and its
    refund); the marked purchase still leaves regular spending in month_spending as in
    household_month, so the means sum to it and play holds (review of PR #590)."""
    salary = _line("PAY", AllocationCategory.HH_SALARY, "3000.00")
    usual = _line("USUAL", AllocationCategory.HH_SUPERMARKET, "-200.00")
    bought = _line("BUY", AllocationCategory.HH_SUPERMARKET, "-600.00")
    refunded = _line("BACK", refund_code, "600.00")
    marked = frozenset({bought.allocation_id})
    lines_by_month = {
        "2027-01": [salary, usual],
        "2027-02": [salary, usual],
        "2027-03": [salary, bought, refunded],
    }
    window, spending = [], []
    for month, rows in lines_by_month.items():
        cashflow = month_cashflow(month, cashflow_rows(rows), saved=Decimal("0.00"))
        hm = household_month(cashflow, lines=rows, one_off_ids=marked)
        by_code = SP.month_spending(cashflow, rows, marked)
        assert sum(by_code.values(), Decimal("0.00")) == hm.regular_spending
        window.append(hm)
        spending.append(by_code)
    march_cashflow = month_cashflow(
        "2027-03", cashflow_rows(lines_by_month["2027-03"]), saved=Decimal("0.00")
    )
    assert HouseholdGroup.GROCERIES not in march_cashflow.spending_by_group
    march = window[-1]
    assert (march.one_off_spending, march.regular_spending) == (
        Decimal("600.00"),
        Decimal("-600.00"),
    )
    SP.play(window, spending, [], [], 3)  # the invariant holds, nothing raised
