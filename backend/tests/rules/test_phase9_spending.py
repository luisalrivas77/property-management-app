"""Phase 9 step 3b: rules/plan/spending.py against spending_play.yaml and
spending_comparison.yaml (docs/phases/phase-9.md §7.6, §7.7, D263), pure, no database.

fixture_models_phase9.py recomputes each `expect` on load with its own reference functions;
this file runs the production functions on the same cases and compares. Plus what the fixtures
cannot hold: the month function through rules.plan.levers, the codes per month read from
phase 6's cashflow, D234's purchase cost, and the group rows of the play."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import fixture_models_phase9 as phase9
import pytest

from hausbuch.rules.health.month import cashflow_rows, household_month
from hausbuch.rules.household.cashflow import month_cashflow
from hausbuch.rules.plan import levers as L
from hausbuch.rules.plan import spending as S
from hausbuch.rules.plan.surplus import mean_cent
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.health import HouseholdMonth, MonthLine, ratio
from hausbuch.schemas.household import HouseholdGroup as G
from hausbuch.schemas.plan import HouseholdLevers, SpendingChange

PLAY = phase9.load("spending_play")
COMPARISON = phase9.load("spending_comparison")
ZERO = Decimal("0.00")


def _live(case) -> None:
    if case.pending:
        pytest.skip(f"pending step {case.pending}")


def uid(key: str) -> UUID:
    return phase9.key_uuid(key)


def regular(m) -> dict[C, Decimal]:
    return {c: v for c, v in m.spending.items() if c not in S.ONE_OFF}


def as_month(m) -> HouseholdMonth:
    """The household month §7.6 reads: E = income + R, the regular spending without one-offs."""
    reg = sum(regular(m).values(), ZERO)
    one_off = sum(m.spending.values(), ZERO) - reg
    private_spending = reg + one_off
    income = m.income + m.rental_result
    return HouseholdMonth(
        month=m.month,
        private_income=m.income,
        private_spending=private_spending,
        one_off_spending=one_off,
        regular_spending=reg,
        rental_result=m.rental_result,
        rental_cash=m.rental_cash,
        income=income,
        spending=private_spending,
        saved=income - private_spending,
        principal=m.rental_result - m.rental_cash,
        invested_property=ZERO,
        invested=ZERO,
        free_cash=m.income - reg + m.rental_cash,
        savings_rate=ratio(income - private_spending, income),
    )


def changes_of(raw) -> list[SpendingChange]:
    return [SpendingChange.model_validate(c) for c in raw]


# --- spending_play.yaml ------------------------------------------------------------------


@pytest.mark.parametrize("case", PLAY.cases, ids=lambda c: c.id)
def test_spending_play(case):
    _live(case)
    for changes in case.accepted:
        HouseholdLevers.model_validate({"spending_changes": changes})
    for changes in case.refused:
        with pytest.raises(ValueError):
            HouseholdLevers.model_validate({"spending_changes": changes})
    means = S.code_means([regular(m) for m in case.window])
    for changes in case.refused_with_means:
        levers = HouseholdLevers.model_validate({"spending_changes": changes})
        assert S.percent_on_nothing(levers.spending_changes, means) is not None
    if case.expect is None:
        return
    want = case.expect
    changes = HouseholdLevers.model_validate({"spending_changes": case.changes}).spending_changes
    window = [as_month(m) for m in case.window]
    got = S.play(
        window,
        [regular(m) for m in case.window],
        changes,
        case.income_changes,
        case.horizon_months,
    )
    assert means == want.means
    assert got.mean_regular_spending == want.mean_regular_spending
    assert sum((r.mean for g in got.groups for r in g.rows), ZERO) == want.mean_regular_spending
    assert got.rate_regular_now == want.rate_regular_now
    assert [(s.from_month, s.to_month, s.rate) for s in got.rates] == [
        (s.from_month, s.to_month, s.rate) for s in want.rates
    ]
    in_order = sorted(_played(got), key=lambda p: changes.index(p.change))
    played = [(p.change.code, p.change.from_month, p.planned, p.delta) for p in in_order]
    assert played == [(p.code, p.from_month, p.planned, p.delta) for p in want.planned]
    for p in want.planned:
        assert S.mean_of(p.code, means) == p.mean

    basis = L.MonthBasis(
        newest_closed=case.window[-1].month,
        mean_income=mean_cent([m.income for m in case.window]),
        mean_regular_spending=got.mean_regular_spending,
        mean_rental_cash=mean_cent([m.rental_cash for m in case.window]),
        buffer_percent=case.buffer_percent,
        spending_changes=tuple(changes),
        code_means=means,
    )
    months = L.months(basis, case.horizon_months, None, case.income_changes, case.one_offs)
    by_month = {m.month: m for m in months}
    rate_of = {
        L.add_months(s.from_month, k): s.rate
        for s in got.rates
        for k in range(L.months_after(s.from_month, s.to_month) + 1)
    }
    for m in want.months:
        month = by_month[m.month]
        assert (month.spending_delta, month.free, month.investable) == (
            m.delta,
            m.free,
            m.investable,
        ), m.month
        assert month.free - month.investable == m.buffer, m.month
        assert rate_of[m.month] == m.rate, m.month


def _played(play):
    """Every change the play's groups and rows hold."""
    out = [p for g in play.groups for p in g.changes]
    return out + [p for g in play.groups for r in g.rows for p in r.changes]


def test_no_change_equals_rate_regular():
    case = next(c for c in PLAY.cases if c.id == "no_change")
    window = [as_month(m) for m in case.window]
    got = S.play(window, [regular(m) for m in case.window], [], [], 12)
    assert [s.rate for s in got.rates] == [got.rate_regular_now]
    sum_e = sum((m.income for m in window), ZERO)
    sum_a = sum((m.regular_spending for m in window), ZERO)
    assert got.rate_regular_now == ratio(sum_e - sum_a, sum_e)


def test_play_groups_and_rows_of_the_worked_case():
    case = next(c for c in PLAY.cases if c.id == "worked")
    changes = changes_of(case.changes)
    got = S.play(
        [as_month(m) for m in case.window], [regular(m) for m in case.window], changes, [], 6
    )
    groups = {g.group: g for g in got.groups}
    travel = groups[G.TRAVEL]
    assert travel.code is C.HH_TRAVEL and travel.mean == Decimal("200.00")
    assert [p.change.code for p in travel.changes] == [C.HH_TRAVEL]
    # July is not the first month: June plans the mean.
    assert travel.change_first == ZERO and travel.planned_first == Decimal("200.00")
    assert {r.code for r in travel.rows} == {C.HH_LODGING, C.HH_FLIGHTS}
    groceries = groups[G.GROCERIES]
    assert groceries.code is C.HH_GROCERIES and groceries.changes == []
    (super_row,) = groceries.rows
    assert super_row.code is C.HH_SUPERMARKET and super_row.playable
    assert (super_row.planned_first, super_row.change_first) == (
        Decimal("682.50"),
        Decimal("32.50"),
    )
    assert groceries.change_first == Decimal("32.50")
    other = groups[G.OTHER]
    rows = {r.code: r for r in other.rows}
    assert not rows[C.BANK_FEES].playable
    assert not rows[C.HH_OTHER].playable  # the group's own field plans it
    assert other.code is C.HH_OTHER
    refund = groups[G.REFUND]
    assert refund.code is None and not refund.rows[0].playable
    assert refund.mean == Decimal("-6.67")


def test_play_refuses_codes_that_do_not_sum():
    case = next(c for c in PLAY.cases if c.id == "no_change")
    window = [as_month(m) for m in case.window]
    spending = [regular(m) for m in case.window]
    spending[0] = {**spending[0], C.HH_DINING: spending[0][C.HH_DINING] + 1}
    with pytest.raises(ValueError, match="sum to the regular spending"):
        S.play(window, spending, [], [], 3)


def test_codes_and_groups():
    assert S.group_code(G.TAX) is None
    assert S.group_code(G.MOVEMENTS) is None
    assert S.group_code(G.REFUND) is None
    assert S.group_code(G.PERSONAL_CARE) is C.HH_PERSONAL_CARE
    assert S.group_code(G.GROCERIES) is C.HH_GROCERIES
    assert {C.BANK_FEES, C.UNCATEGORIZED, C.HH_POSTAGE, C.HH_OTHER} <= S.members(C.HH_OTHER)
    assert C.HH_CAR_PURCHASE not in S.members(C.HH_MOBILITY)
    assert S.members(C.HH_SUPERMARKET) == {C.HH_SUPERMARKET}
    assert S.playable(C.HH_CHILDCARE) and S.playable(C.HH_CASH)
    assert not S.playable(C.HH_REFUND) and not S.playable(C.BANK_FEES)
    assert S.refused_code(C.HH_CAR_PURCHASE) == "is a one-off code"
    assert S.refused_code(C.RENT_COLD) is not None


def test_month_spending_sums_to_the_household_months_regular_spending():
    """The codes per month come from phase 6's cashflow, D234 and the one-off codes applied,
    and sum to rules.health.month's regular spending of the same month."""
    lines = [
        MonthLine(allocation_id=uid("pay"), category=C.HH_SALARY, amount=Decimal("4000.00")),
        MonthLine(allocation_id=uid("s"), category=C.HH_SUPERMARKET, amount=Decimal("-300.00")),
        MonthLine(allocation_id=uid("r"), category=C.HH_REFUND, amount=Decimal("20.00")),
        MonthLine(allocation_id=uid("f"), category=C.BANK_FEES, amount=Decimal("-5.00")),
        MonthLine(allocation_id=uid("k"), category=C.HH_CHILDCARE, amount=Decimal("-250.00")),
        MonthLine(allocation_id=uid("car"), category=C.HH_CAR_PURCHASE, amount=Decimal("-9000")),
        MonthLine(
            allocation_id=uid("notary"),
            category=C.HH_OTHER,
            amount=Decimal("-800.00"),
            purchase_cost=True,
        ),
    ]
    cashflow = month_cashflow("2027-03", cashflow_rows(lines), saved=ZERO)
    month = household_month(cashflow, lines=lines)
    by_code = S.month_spending(cashflow)
    assert sum(by_code.values(), ZERO) == month.regular_spending == Decimal("535.00")
    assert C.HH_CAR_PURCHASE not in by_code and C.HH_OTHER not in by_code
    assert by_code[C.HH_REFUND] == Decimal("-20.00")


def test_with_means_at_save_overwrites_a_typed_mean():
    means = {C.HH_SUPERMARKET: Decimal("650.00"), C.HH_LODGING: Decimal("100.00")}
    changes = changes_of(
        [
            {"code": "hh_supermarket", "percent": "0.05", "from_month": "2027-06",
             "mean_at_save": "1.00"},
            {"code": "hh_travel", "monthly": "500.00", "from_month": "2027-07"},
            {"code": "hh_childcare", "monthly": "300.00", "from_month": "2027-07"},
        ]
    )  # fmt: skip
    stored = S.with_means_at_save(changes, means)
    assert [c.mean_at_save for c in stored] == [
        Decimal("650.00"),
        Decimal("100.00"),
        ZERO,
    ]


# --- spending_comparison.yaml ------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    id: UUID
    created_at: datetime
    is_default: bool
    withdrawn: bool
    supersedes_id: UUID | None


def rows_of(scenarios) -> list[Row]:
    return [
        Row(
            id=uid(s.key),
            created_at=s.created_at,
            is_default=s.is_default,
            withdrawn=s.withdrawn,
            supersedes_id=None if s.supersedes is None else uid(s.supersedes),
        )
        for s in scenarios
    ]


@pytest.mark.parametrize("case", COMPARISON.cases, ids=lambda c: c.id)
def test_spending_comparison(case):
    _live(case)
    rows = rows_of(case.scenarios)
    for day, key in case.in_force.items():
        got = S.in_force_on(rows, day)
        assert (None if got is None else got.id) == (None if key is None else uid(key)), day
    if case.expect is None:
        return
    scenario = Row(uid("scenario"), datetime(2027, 6, 1), True, False, None)
    lines = [
        MonthLine(allocation_id=uid(li.key), category=li.code, amount=li.amount)
        for li in case.lines
    ]
    got = S.comparison(
        case.month,
        case.today,
        closed=case.closed,
        open_lines=case.open_lines,
        scenario=scenario,
        changes=[SpendingChange.model_validate(c.model_dump()) for c in case.changes],
        lines=lines,
    )
    assert got.final == case.expect.final
    assert got.open_lines == case.expect.open_lines
    assert got.scenario_id == scenario.id
    assert [
        (r.code, r.planned, r.actual, r.difference, r.words.value, r.text, r.sources)
        for r in got.rows
    ] == [
        (r.code, r.planned, r.actual, r.difference, r.words, r.text, [uid(k) for k in r.sources])
        for r in case.expect.rows
    ]
    assert all(str(r.actual) != "-0.00" for r in got.rows)


def test_comparison_leaves_a_purchase_cost_out():
    """D234: a private line that pays a property's Nebenkosten row is invested, never spending
    on the code it was booked to."""
    change = SpendingChange(
        code=C.HH_OTHER, monthly=Decimal("100.00"), from_month="2027-07",
        mean_at_save=Decimal("100.00"),
    )  # fmt: skip
    lines = [
        MonthLine(allocation_id=uid("a"), category=C.HH_OTHER, amount=Decimal("-40.00")),
        MonthLine(
            allocation_id=uid("b"),
            category=C.HH_OTHER,
            amount=Decimal("-900.00"),
            purchase_cost=True,
        ),
    ]
    (row,) = S.compare_rows("2027-07", [change], lines)
    assert row.actual == Decimal("40.00") and row.sources == [uid("a")]


def test_comparison_without_scenario_is_empty():
    got = S.comparison(
        "2027-07", date(2027, 8, 10), closed=True, open_lines=0, scenario=None, changes=[],
        lines=[],
    )  # fmt: skip
    assert got.rows == [] and got.final and got.scenario_id is None


def test_as_of_and_final():
    assert S.as_of("2027-08", date(2027, 8, 15)) == S.AsOf(date(2027, 8, 15), True)
    assert S.as_of("2027-07", date(2027, 8, 15)) == S.AsOf(date(2027, 7, 31), False)
    assert S.as_of("2027-12", date(2028, 1, 2)).day == date(2027, 12, 31)
    future = S.comparison(
        "2027-09", date(2027, 8, 15), closed=True, open_lines=0, scenario=None, changes=[],
        lines=[],
    )  # fmt: skip
    assert not future.final


def test_words():
    assert S.words(Decimal("0.99"))[1] == "wie geplant"
    assert S.words(Decimal("-0.99"))[1] == "wie geplant"
    assert S.words(Decimal("1.00"))[1] == "1 € mehr als geplant"
    assert S.words(Decimal("-1234.50"))[1] == "1.235 € weniger als geplant"
