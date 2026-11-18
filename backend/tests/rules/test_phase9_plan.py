"""Phase 9 step 2: the real rules of rules/plan/ against the fixtures of tests/fixtures/phase9/
(docs/phases/phase-9.md §6, §7, §15.1), pure, no database.

fixture_models_phase9.py recomputes each `expect` on load with its own reference functions;
this file runs the production functions on the same cases and compares. Plus what the fixtures
cannot hold: the to-do texts and links, a payee without a key, an override of a plan not
detected, a one-off outside the horizon and a projection without any property."""

from datetime import date
from decimal import Decimal
from fractions import Fraction
from uuid import UUID

import fixture_models_phase9 as phase9
import pytest

from hausbuch.rules.plan import levers as L
from hausbuch.rules.plan import paths as P
from hausbuch.rules.plan import projection as N
from hausbuch.rules.plan import savings_plans as S
from hausbuch.rules.plan import surplus as U
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.health import HealthTodoCode, HouseholdMonth, ratio
from hausbuch.schemas.plan import (
    HouseholdLevers,
    IncomeChange,
    OneOff,
    PlanClass,
    PlanOverride,
    SavingsPlan,
    SurplusKey,
    ValueChange,
)

SURPLUS = phase9.load("surplus")
PLANS = phase9.load("savings_plans")
PATHS = phase9.load("paths")
LEVERS = phase9.load("plan_levers")
PROJECTION = phase9.load("net_worth_projection")
ZERO = Decimal("0.00")


def _live(case) -> None:
    if case.pending:
        pytest.skip(f"pending step {case.pending}")


def uid(key: str) -> UUID:
    return phase9.key_uuid(key)


# --- surplus.yaml ----------------------------------------------------------------------


def as_month(m) -> HouseholdMonth:
    """A household month carrying only what §6.1 reads (the rest follows its identities)."""
    private_spending = m.regular_spending + m.one_off_spending
    income = m.income + m.rental_cash
    return HouseholdMonth(
        month=m.month,
        private_income=m.income,
        private_spending=private_spending,
        one_off_spending=m.one_off_spending,
        regular_spending=m.regular_spending,
        rental_result=m.rental_cash,
        rental_cash=m.rental_cash,
        income=income,
        spending=private_spending,
        saved=income - private_spending,
        principal=ZERO,
        invested_property=ZERO,
        invested=ZERO,
        free_cash=m.income - m.regular_spending + m.rental_cash,
        savings_rate=ratio(income - private_spending, income),
    )


@pytest.mark.parametrize("case", SURPLUS.cases, ids=lambda c: c.id)
def test_surplus(case):
    _live(case)
    got = U.surplus([as_month(m) for m in case.months], case.buffer_percent)
    want = case.expect
    for key in SurplusKey:
        assert got.amount(key) == getattr(want, key.value), key
    lowest = next(r for r in got.rows if r.key is SurplusKey.LOWEST_FREE_CASH)
    assert lowest.month == want.lowest_month


def test_surplus_sources_follow_the_months():
    """Each row names the allocations of its field (principle 2); the buffer has none."""
    a, b = uid("a"), uid("b")
    m1 = as_month(SURPLUS.cases[0].months[0]).model_copy(
        update={"sources": {"private_income": [a], "free_cash": [a, b]}}
    )
    m2 = as_month(SURPLUS.cases[0].months[1]).model_copy(
        update={"sources": {"private_income": [a], "free_cash": [b]}}
    )
    got = U.surplus([m1, m2], Decimal("10"))
    rows = {r.key: r for r in got.rows}
    assert rows[SurplusKey.MEAN_INCOME].sources == [a]
    assert rows[SurplusKey.FREE_CASH].sources == [a, b]
    assert rows[SurplusKey.BUFFER].sources == []


def test_surplus_needs_a_month():
    with pytest.raises(ValueError):
        U.surplus([], Decimal("10"))


# --- savings_plans.yaml ----------------------------------------------------------------


def plan_lines(case) -> list[S.PlanLine]:
    return [
        S.PlanLine(
            booked=li.booked,
            amount=li.amount,
            account_id=uid(li.to_account) if li.to_account else None,
            account_kind=case.own_accounts[li.to_account].kind if li.to_account else None,
            payee=li.payee,
            category=li.category,
        )
        for li in case.lines
    ]


@pytest.mark.parametrize("case", PLANS.cases, ids=lambda c: c.id)
def test_savings_plans(case):
    _live(case)
    detected = S.detect(plan_lines(case), case.last_three_closed)
    got = S.apply_overrides(
        detected,
        [
            o.model_copy(update={"plan_key": str(uid(o.plan_key))})
            if o.plan_key in case.own_accounts
            else o
            for o in case.overrides
        ],
    )
    want = case.expect
    keys = {str(uid(k)): k for k in case.own_accounts}
    assert [
        (keys.get(p.key, p.key), p.destination_payee, p.asset_class, p.monthly, p.months)
        for p in got
    ] == [(p.key, p.destination_payee, p.asset_class, p.monthly, p.months) for p in want.plans]
    assert [p.overridden for p in got] == [p.overridden for p in want.plans]
    for plan in got:
        if plan.key in keys:
            assert plan.destination_account_id == uid(keys[plan.key])
    already = S.already_invested(got)
    assert (already, case.investable - already) == (want.already_invested, want.investable_left)


def test_payee_key_folds_and_refuses_empty():
    assert S.payee_key("Fondsplan Nord") == "fondsplan-nord"
    assert S.payee_key("  ÄÖ Sparen--Plan! ") == "sparen-plan"
    assert S.payee_key("***") is None
    assert len(S.payee_key("x" * 80 + " y")) == 64


def test_payee_without_a_key_is_no_plan():
    lines = [
        S.PlanLine(
            booked=date(2027, m, 1),
            amount=Decimal("-50.00"),
            payee="***",
            category=C.HH_INVESTMENT_SECURITIES,
        )
        for m in (3, 4, 5)
    ]
    assert S.detect(lines, ["2027-03", "2027-04", "2027-05"]) == []


def test_refund_into_plan_and_other_months_ignored():
    """An incoming line and a line outside the three months never count."""
    acc = uid("depot")
    lines = [
        S.PlanLine(date(2027, m, 2), Decimal("-100.00"), acc, AccountKind.DEPOT) for m in (3, 4, 5)
    ]
    lines += [
        S.PlanLine(date(2027, 2, 2), Decimal("-900.00"), acc, AccountKind.DEPOT),
        S.PlanLine(date(2027, 4, 9), Decimal("40.00"), acc, AccountKind.DEPOT),
    ]
    (plan,) = S.detect(lines, ["2027-03", "2027-04", "2027-05"])
    assert (plan.monthly, plan.months) == (Decimal("100.00"), [Decimal("100.00")] * 3)


def test_override_of_an_undetected_plan_changes_nothing():
    plan = SavingsPlan(
        key="depot",
        destination_payee="Depot",
        asset_class=PlanClass.DEPOT,
        monthly=Decimal("10.00"),
        months=[Decimal("10.00")] * 3,
    )
    assert S.apply_overrides([plan], [PlanOverride(plan_key="other", dropped=True)]) == [plan]


# --- paths.yaml ------------------------------------------------------------------------


def basis(newest: str, m) -> L.MonthBasis:
    return L.MonthBasis(
        newest_closed=newest,
        mean_income=m.mean_income,
        mean_regular_spending=m.mean_regular_spending,
        mean_rental_cash=m.mean_rental_cash,
        buffer_percent=m.buffer_percent,
    )


def compare_rows(got, want, what):
    phase9.same_rows([phase9.PaRow.model_validate(r.model_dump()) for r in got], want, what)


@pytest.mark.parametrize("case", PATHS.cases, ids=lambda c: c.id)
def test_paths(case):
    _live(case)
    months = L.months(basis(case.newest_closed, case.month), case.horizon_months, None)
    got = P.paths(case.reserve, case.target, case.plans, months, case.newest_closed, case.mode)
    compare_rows(got.rows, case.expect.rows, case.id)
    assert got.others == case.expect.others


class _Plan:
    def __init__(self, key: str, monthly: str) -> None:
        self.key, self.monthly = key, Decimal(monthly)


def _review_basis() -> L.MonthBasis:
    return L.MonthBasis(
        newest_closed="2027-05",
        mean_income=Decimal("6000.00"),
        mean_regular_spending=Decimal("4000.00"),
        mean_rental_cash=ZERO,
        buffer_percent=Decimal("10"),
    )


def test_a_one_off_in_the_first_month_is_not_the_monthly_rate():
    """„im Monat“ is the first month without one-offs; the reserve still counts the refund
    (PR #434 review B)."""
    b = _review_basis()
    plans = [_Plan("depot", "300.00")]
    refund = [OneOff(month="2027-06", amount=Decimal("2500.00"))]
    with_refund = L.months(b, 12, None, (), refund)
    steady = L.months(b, 12, None, ())
    got = P.paths(ZERO, Decimal("9000.00"), plans, with_refund, "2027-05", steady=steady)
    plain = P.paths(ZERO, Decimal("9000.00"), plans, steady, "2027-05")
    first = {r.key: r.to_reserve_first_month for r in got.rows}
    assert first == {r.key: r.to_reserve_first_month for r in plain.rows}
    assert first["as_is"] == Decimal("1800.00") - Decimal("300.00")
    # the months with the refund still reach the target earlier and end higher
    by_key = {r.key: r for r in plain.rows}
    for row in got.rows:
        assert row.reserve_at_horizon == by_key[row.key].reserve_at_horizon + Decimal("2250.00")
        assert row.reached is not None and row.reached < by_key[row.key].reached
    # without `steady` the first month is taken from `months`, as before
    old = P.paths(ZERO, Decimal("9000.00"), plans, with_refund, "2027-05")
    assert {r.key: r.to_reserve_first_month for r in old.rows}["as_is"] == Decimal("3750.00")


def test_the_later_rate_holds_only_without_a_later_change():
    """„ab <Monat>“ is one month's figure: shown only when no income change or one-off moves
    the rate after it (PR #434 review B)."""
    b = _review_basis()
    flat_years = {2028: Decimal("-200.00"), 2029: Decimal("-150.00")}
    steady = L.months(b, 36, flat_years, ())
    change = P.flat_change([], steady, ZERO)
    assert change is not None and change.month == "2028-01"
    # the flat's own result may move from year to year
    assert P.holds_from(change, steady, [])
    # a one-off after the change month, or an income change that starts or ends after it
    assert not P.holds_from(change, steady, [OneOff(month="2028-03", amount=Decimal("1.00"))])
    assert P.holds_from(change, steady, [OneOff(month="2027-08", amount=Decimal("1.00"))])
    starts = IncomeChange(monthly_net=Decimal("-600.00"), from_month="2028-06")
    assert not P.holds_from(change, L.months(b, 36, flat_years, [starts]), [])
    ends = IncomeChange(monthly_net=Decimal("-600.00"), from_month="2027-09", to_month="2028-04")
    assert not P.holds_from(change, L.months(b, 36, flat_years, [ends]), [])
    before = IncomeChange(monthly_net=Decimal("-600.00"), from_month="2027-09")
    assert P.holds_from(change, L.months(b, 36, flat_years, [before]), [])


def test_paths_need_a_month():
    with pytest.raises(ValueError):
        P.paths(ZERO, ZERO, [], [], "2027-05")


# --- plan_levers.yaml ------------------------------------------------------------------


def flats_of(flat_years):
    return {
        p: {y: L.FlatYear(net_cash=f.net_cash, tax_effect=f.tax_effect) for y, f in years.items()}
        for p, years in flat_years.items()
    }


@pytest.mark.parametrize("case", LEVERS.cases, ids=lambda c: c.id)
def test_plan_levers(case):
    _live(case)
    for levers in case.accepted:
        HouseholdLevers.model_validate(levers)
    for levers in case.refused:
        with pytest.raises(ValueError):
            HouseholdLevers.model_validate(levers)
    if case.expect is None:
        return
    flat_years = None if case.flat_years is None else L.flat_by_year(flats_of(case.flat_years))
    b = basis(case.newest_closed, case.month)
    months = L.months(b, case.horizon_months, flat_years, case.income_changes, case.one_offs)
    by_month = {m.month: m for m in months}
    for want in case.expect.months:
        got = by_month[want.month]
        assert (got.flat, got.free, got.investable) == (want.flat, want.free, want.investable)
    got = P.paths(case.reserve, case.target, case.plans, months, case.newest_closed)
    compare_rows(got.rows, case.expect.rows, case.id)
    # What stands in for the projection's flat when a property has no default scenario.
    flats = {} if flat_years is not None else {"p1": None}
    todos = N.projection_todos(None, date(2027, 5, 31), [], flats, HouseholdLevers())
    codes = [t.code for t in todos if t.code is not HealthTodoCode.MONTH_END_MISSING]
    assert codes == case.expect.todos


def test_one_offs_outside_the_horizon():
    inside = OneOff(month="2027-06", amount=Decimal("100.00"))
    last = OneOff(month="2032-05", amount=Decimal("1.00"))
    past = OneOff(month="2027-05", amount=Decimal("5.00"))
    beyond = OneOff(month="2032-06", amount=Decimal("5.00"))
    # §7.3 amended (PR #434 review D): a one-off in a closed month may stay, only one after
    # the horizon's end is outside; the months ignore the past one
    assert L.one_offs_outside([inside, last, past, beyond], "2027-05", 60) == [beyond]
    b = _review_basis()  # newest closed month 2027-05
    with_past = L.months(b, 12, None, (), [past])
    assert with_past == L.months(b, 12, None, (), ())


def test_add_months_and_distance():
    assert L.add_months("2027-11", 3) == "2028-02"
    assert L.add_months("2027-01", -1) == "2026-12"
    assert L.months_after("2027-05", "2028-02") == 9


# --- net_worth_projection.yaml ---------------------------------------------------------


def start_of(s) -> N.ProjectionStart | None:
    if s is None:
        return None
    return N.ProjectionStart(
        month_end=s.month_end,
        bank=s.bank,
        depot=s.depot,
        crypto=s.crypto,
        property=s.property,
        loans=s.loans,
        other_liabilities=s.other_liabilities,
    )


def scenario_of(flat) -> N.FlatScenario | None:
    if flat is None:
        return None
    return N.FlatScenario(
        sale_year=flat.sale_year,
        years={
            y: L.FlatYear(
                net_cash=f.net_cash,
                tax_effect=f.tax_effect,
                value=f.value,
                loan_balance=f.loan_balance,
            )
            for y, f in flat.years.items()
        },
    )


def run_case(case) -> N.ProjectionRun:
    levers = HouseholdLevers(
        value_change=case.levers.value_change,
        net_worth_years=case.levers.net_worth_years,
        income_changes=case.levers.income_changes,
        one_offs=case.levers.one_offs,
    )
    return N.project_net_worth(
        basis(case.newest_closed, case.month),
        start_of(case.start),
        phase9.last_day(case.newest_closed),
        case.reserve,
        case.plans,
        {p: scenario_of(f) for p, f in case.flats.items()},
        levers,
    )


def as_got(run: N.ProjectionRun) -> dict:
    """The run in the shape fixture_models_phase9.check_projection compares."""
    p = run.projection
    return {
        "points": p.points,
        "todos": [t.code for t in p.todos],
        "decembers": run.decembers,
        "as_is": run.as_is,
        "bank": run.bank,
        "ends_before_sale_year": p.ends_before_sale_year,
        "used_up": p.used_up,
        "start_bank": p.start.bank if p.start else None,
        "start_reserve": None,
    }


@pytest.mark.parametrize("case", PROJECTION.cases, ids=lambda c: c.id)
def test_net_worth_projection(case):
    _live(case)
    if case.stretch is not None:
        s = case.stretch
        got = N.stretch(
            Fraction(s.value), Fraction(s.rate), [Fraction(s.monthly)] * s.months, s.months
        )
        assert U.cent(Decimal(got.numerator) / Decimal(got.denominator)) == case.expect.value
        return
    run = run_case(case)
    got = as_got(run)
    got["start_reserve"] = case.reserve
    phase9.check_projection(got, case.expect, case.id)
    if run.projection.points:
        assert run.projection.start is not None
        assert run.projection.start.on == case.start.month_end


def test_projection_todo_texts_and_links():
    todos = N.projection_todos(
        None,
        date(2027, 5, 31),
        [
            SavingsPlan(
                key="x",
                destination_payee="X",
                asset_class=PlanClass.CRYPTO,
                monthly=Decimal("1.00"),
                months=[Decimal("1.00")] * 3,
            )
        ],  # fmt: skip
        {"p1": None},
        HouseholdLevers(),
    )
    assert [(t.code, t.href) for t in todos] == [
        (HealthTodoCode.MONTH_END_MISSING, "/?month_end=2027-05-31"),
        (HealthTodoCode.NO_DEFAULT_SCENARIO, "/planning"),
        (HealthTodoCode.RATE_MISSING, "/planning?tab=haushalt"),
    ]
    assert all(t.action and t.text for t in todos)
    assert "31.05.2027" in todos[0].text
    assert "Krypto" in todos[2].text and "Fonds" not in todos[2].text


def test_projection_without_property_runs_on_calendar_years():
    """No property at all: the years after the newest closed month's year, no flat rows."""
    start = N.ProjectionStart(
        month_end=date(2027, 5, 31),
        bank=Decimal("1000.00"),
        depot=ZERO,
        crypto=ZERO,
        property=ZERO,
        loans=ZERO,
        other_liabilities=ZERO,
    )
    b = L.MonthBasis("2027-05", Decimal("100.00"), Decimal("0.00"), Decimal("0.00"), Decimal("0"))
    run = N.project_net_worth(
        b, start, date(2027, 5, 31), Decimal("1000.00"), [], {},
        HouseholdLevers(net_worth_years=2, value_change=ValueChange()),
    )  # fmt: skip
    p = run.projection
    assert [x.on for x in p.points] == [date(2028, 12, 31), date(2029, 12, 31)]
    # 7 months of 2027 + 12 of 2028 at 100 a month.
    assert p.points[0].bank == Decimal("2900.00")
    assert p.points[0].property == ZERO and p.ends_before_sale_year is None
