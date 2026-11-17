"""Phase 9 step 1: the real rules of rules/health/ against the fixtures of
tests/fixtures/phase9/ (docs/phases/phase-9.md §4, §5, §15.1), pure, no database.

fixture_models_phase9.py recomputes each `expect` on load with its own reference functions;
this file runs the production functions on the same cases and compares, so a rule and its
fixture can only agree by computing the same figure. A case still marked `pending` is skipped.
Plus what the fixtures cannot hold: the to-do texts, several properties, a month not closed in
the series, the D234 recode of a flagged line, and two stale accounts."""

from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from uuid import UUID, uuid4

import fixture_models_phase9 as phase9
import pytest

from hausbuch.rules.health import bands as B
from hausbuch.rules.health import figures as F
from hausbuch.rules.health import month as M
from hausbuch.rules.health import window as W
from hausbuch.rules.household.cashflow import month_cashflow
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.common import Fraction as ExactShare
from hausbuch.schemas.health import (
    AccountCoverage,
    BandLimits,
    ClosedReasonCode,
    Debt,
    EstimateSource,
    Health,
    HealthTodoCode,
    HouseholdMonth,
    LightColour,
    LimitOrigin,
    MarkedFigure,
    MonthFacts,
    MonthLine,
    PropertyLtv,
    Reserve,
    ReserveBalance,
    ReserveOrigin,
    ReserveState,
    RunTotals,
    SavingsRate,
    ScenarioValue,
    Window,
    ratio,
)
from hausbuch.schemas.household import NetWorthClass
from hausbuch.schemas.net_worth import NetWorth, NetWorthLine, NetWorthLineKind
from hausbuch.schemas.plan import ReserveAccounts

CLOSED = phase9.load("closed_month")
MONTH = phase9.load("household_month")
RATE = phase9.load("savings_rate")
RESERVE = phase9.load("reserve")
DEBT = phase9.load("debt")
SERIES = phase9.load("net_worth_series")
ZERO = Decimal("0.00")
SAVING_KINDS = {"savings", "depot", "exchange"}  # services/transfers.saved_in's in-legs


def _live(case) -> None:
    if case.pending:
        pytest.skip(f"pending step {case.pending}")


def uid(key: str) -> UUID:
    return phase9.key_uuid(key)


# --- closed_month.yaml ---------------------------------------------------------------


def facts(case, month, covered_to, own_opening) -> MonthFacts:
    return MonthFacts(
        month=month,
        accounts=[
            AccountCoverage(
                account_id=uid(a.key),
                label=a.key,
                credit_card=a.kind == "credit_card",
                first_day=a.first_day,
                covered_to=covered_to.get(a.key),
                idle=a.key in case.idle_card,
                own_opening=own_opening,
            )
            for a in case.accounts
        ],
        lines_waiting=case.lines_waiting,
        transfers_open=case.transfers_open,
    )


@pytest.mark.parametrize("case", CLOSED.cases, ids=lambda c: c.id)
def test_closed_month(case):
    _live(case)
    if case.closed_months is not None:
        got = W.window(case.closed_months)
        assert got.months == case.expect_window.months
        assert got.enough == case.expect_window.enough
        assert [t.code for t in W.window_todos(got)] == case.expect_todos
        return
    runs = [(case.month, case.covered_to, False, case.expect)]
    runs += [(v.month, v.covered_to, v.own_opening, v.expect) for v in case.variants]
    for month, covered_to, own, expect in runs:
        if month is None or expect is None:
            continue
        got = W.closed(facts(case, month, covered_to, own))
        assert got.closed == expect.closed, (case.id, month)
        assert [(r.code, r.account_id, r.count) for r in got.reasons] == [
            (r.code, None if r.account is None else uid(r.account), r.count) for r in expect.reasons
        ], (case.id, month)
        assert [t.code for t in got.todos] == expect.todos, (case.id, month)


def test_closed_month_todos_name_the_button():
    month = MonthFacts(
        month="2027-03",
        accounts=[
            AccountCoverage(
                account_id=uid("card"),
                label="Karte",
                credit_card=True,
                first_day=date(2026, 1, 1),
                covered_to=date(2027, 2, 28),
            ),
            AccountCoverage(account_id=uid("new"), label="Neu"),  # nothing imported yet
        ],
        lines_waiting=1,
        transfers_open=3,
    )
    got = W.closed(month)
    assert [r.code for r in got.reasons] == [
        ClosedReasonCode.ACCOUNT_NOT_COVERED,
        ClosedReasonCode.ACCOUNT_NOT_COVERED,
        ClosedReasonCode.LINES_WAITING,
        ClosedReasonCode.TRANSFER_OPEN,
    ]
    card, new, waiting, transfers = got.todos
    assert card.action == "Kreditkartenabrechnung hochladen" and "„Karte“" in card.text
    assert card.href == f"/accounts/{uid('card')}?tab=kontoauszuege"
    assert new.action == "Kontoauszug hochladen"
    assert waiting.action == "Zu prüfen öffnen" and "Ein Umsatz" in waiting.text
    assert transfers.action == "Gegenbuchung zuordnen" and "3 Umbuchungen" in transfers.text


def test_a_month_missing_from_the_mapping_is_not_closed():
    got = W.window({"2027-01": True, "2027-03": True, "2027-04": True})
    assert got.months == ["2027-03", "2027-04"] and not got.enough
    assert W.window({}).months == [] and W.window_todos(W.window({}))[0].code is (
        HealthTodoCode.TOO_FEW_MONTHS
    )


# --- household_month.yaml ------------------------------------------------------------


def household(month, lines):
    """What the service hands the rule: the private lines (D234's flag from the Beleg link),
    the rental lines per property (one here), and the saved amount of the transfer links
    whose in-leg is a savings, depot or exchange account (phase 6 saved_in)."""
    ids = {id(line): uuid4() for line in lines}

    def as_line(line):
        return MonthLine(
            allocation_id=ids[id(line)],
            category=line.category,
            amount=line.amount,
            purchase_cost=(line.private_cost_for or "").startswith("nebenkosten_"),
        )

    private = [as_line(li) for li in lines if li.account != "rental"]
    rental = [as_line(li) for li in lines if li.account == "rental"]
    legs = defaultdict(list)
    for line in lines:
        if line.links:
            legs[line.links].append(line)
    saved, link_ids = ZERO, []
    for pair in legs.values():
        ins = [li for li in pair if li.amount > 0 and li.account in SAVING_KINDS]
        outs = [li for li in pair if li.amount < 0 and li.account not in SAVING_KINDS]
        if len(pair) == 2 and ins and outs:
            saved += ins[0].amount
            link_ids += [ids[id(li)] for li in pair]
    flow = month_cashflow(month, M.cashflow_rows(private), saved=saved)
    blocks = [M.rental_month(month, uid("flat"), rental)] if rental else []
    got = M.household_month(flow, blocks, lines=private, link_sources=link_ids)
    return got, blocks, ids


def compare(got, blocks, expect, where):
    want = expect.model_dump(exclude_none=True)
    have = got.model_dump()
    have["interest"] = sum((b.interest for b in blocks), ZERO)
    for name, value in want.items():
        assert have[name] == value, (where, name, have[name], value)


@pytest.mark.parametrize("case", MONTH.cases, ids=lambda c: c.id)
def test_household_month(case):
    _live(case)
    if case.months:
        net = ZERO
        for m in case.months:
            got, blocks, _ = household(m.month, m.lines)
            compare(got, blocks, m.expect, (case.id, m.month))
            net += got.invested_property
        assert net == case.net_invested_property
        return
    got, blocks, ids = household(case.month, case.lines)
    compare(got, blocks, case.expect, case.id)
    # principle 2: every id a field names is one of the month's lines
    for name, named in got.sources.items():
        assert set(named) <= set(ids.values()), (case.id, name)


def test_a_flagged_line_counts_as_invested_whatever_it_was_booked_as():
    """D234's recode in the rule: the notary's bill booked as Sonstiges still leaves spending
    once its document is the Beleg of a Nebenkosten row; flagging is idempotent."""
    salary = MonthLine(allocation_id=uuid4(), category=C.HH_SALARY, amount=Decimal("3000.00"))
    notary = MonthLine(
        allocation_id=uuid4(),
        category=C.HH_OTHER,
        amount=Decimal("-1800.00"),
        purchase_cost=True,
    )
    lines = [salary, notary]
    flow = month_cashflow("2027-03", M.cashflow_rows(lines), saved=ZERO)
    got = M.household_month(flow, lines=lines)
    assert got.private_spending == ZERO and got.saved == Decimal("3000.00")
    assert got.invested_property == Decimal("1800.00") and got.invested == Decimal("1800.00")
    assert got.sources["invested_property"] == [notary.allocation_id]
    already = notary.model_copy(update={"category": C.HH_INVESTMENT_PROPERTY})
    assert M.cashflow_rows([already]) == M.cashflow_rows([notary])


def test_two_properties_add_up():
    def rent(prop, amount, principal):
        return M.rental_month(
            "2027-03",
            uid(prop),
            [
                MonthLine(allocation_id=uuid4(), category=C.RENT_COLD, amount=Decimal(amount)),
                MonthLine(
                    allocation_id=uuid4(), category=C.LOAN_PRINCIPAL, amount=Decimal(principal)
                ),
            ],
        )

    flow = month_cashflow("2027-03", [], saved=ZERO)
    got = M.household_month(
        flow, [rent("one", "900.00", "-300.00"), rent("two", "700.00", "-200.00")]
    )
    assert got.rental_result == Decimal("1600.00") and got.principal == Decimal("500.00")
    assert got.rental_cash == Decimal("1100.00") and len(got.sources["rental_result"]) == 2


def test_lines_that_do_not_match_the_cashflow_are_refused():
    line = MonthLine(allocation_id=uuid4(), category=C.HH_SALARY, amount=Decimal("10.00"))
    flow = month_cashflow("2027-03", [], saved=ZERO)
    with pytest.raises(ValueError):
        M.household_month(flow, lines=[line])
    other = M.rental_month("2027-04", uid("flat"), [])
    with pytest.raises(ValueError):
        M.household_month(flow, [other])


def hm(
    month,
    *,
    private_income=ZERO,
    private_spending=ZERO,
    one_off=ZERO,
    rental_result=ZERO,
    principal=ZERO,
) -> HouseholdMonth:
    """A valid HouseholdMonth from its five free fields; the rest follow §4.2."""
    income = private_income + rental_result
    regular = private_spending - one_off
    cash = rental_result - principal
    return HouseholdMonth(
        month=month,
        private_income=private_income,
        private_spending=private_spending,
        one_off_spending=one_off,
        regular_spending=regular,
        rental_result=rental_result,
        rental_cash=cash,
        income=income,
        spending=private_spending,
        saved=income - private_spending,
        principal=principal,
        invested_property=ZERO,
        invested=ZERO,
        free_cash=private_income - regular + cash,
        savings_rate=ratio(income - private_spending, income),
    )


# --- savings_rate.yaml ---------------------------------------------------------------


@pytest.mark.parametrize("case", RATE.cases, ids=lambda c: c.id)
def test_savings_rate(case):
    _live(case)
    window = [
        hm(
            m.month,
            private_income=m.income,
            private_spending=m.income - m.saved,
            one_off=m.one_off_spending,
            principal=m.principal,
        )
        for m in case.months
    ]
    got = F.savings_rate(window)
    want = case.expect
    assert (got.saved, got.income, got.one_off_spending, got.principal) == (
        want.saved,
        want.income,
        want.one_off_spending,
        want.principal,
    )
    assert (got.rate, got.rate_regular, got.rate_without_principal) == (
        want.rate,
        want.rate_regular,
        want.rate_without_principal,
    )
    assert {m.month: m.rate for m in got.months} == want.months


# --- reserve.yaml --------------------------------------------------------------------


def owner_ids(cases) -> dict[str, UUID]:
    """Owner keys to ids that sort as the keys do (A before B), as phase 6's cent rule breaks
    a tie by the owner's sort key; key_uuid's order would be arbitrary."""
    keys = sorted({o for c in cases for b in c.balances for o in b.owners})
    return {k: UUID(int=i + 1) for i, k in enumerate(keys)}


OWNERS = owner_ids(RESERVE.cases)


def exact(share: Decimal) -> ExactShare:
    f = Fraction(share)
    return ExactShare(numerator=f.numerator, denominator=f.denominator)


def balance_of(b) -> ReserveBalance:
    return ReserveBalance(
        account_id=uid(b.key),
        label=b.key,
        kind=b.kind,
        rental=b.purpose == "rental",
        balance=b.balance,
        as_of=b.as_of,
        owners={OWNERS[o]: exact(share) for o, share in b.owners.items()},
    )


@pytest.mark.parametrize("case", RESERVE.cases, ids=lambda c: c.id)
def test_reserve(case):
    _live(case)
    window = [
        hm(
            "2027-05",
            private_spending=case.mean_regular_spending,
            rental_result=case.mean_rental_cash,
        )
    ]
    lever = ReserveAccounts(
        added=[uid(k) for k in case.levers.reserve_accounts.added],
        removed=[uid(k) for k in case.levers.reserve_accounts.removed],
    )
    got = F.reserve(
        [balance_of(b) for b in case.balances],
        window,
        target_months=case.levers.reserve_target_months,
        accounts=lever,
        today=case.today,
    )
    want = case.expect
    by_key = {uid(b.key): b.key for b in case.balances}
    origin = {by_key[a.account_id]: a.origin for a in got.accounts}
    assert [k for k, o in origin.items() if o is not ReserveOrigin.REMOVED] == want.counted
    assert [k for k, o in origin.items() if o is ReserveOrigin.ADDED] == want.added
    assert [k for k, o in origin.items() if o is ReserveOrigin.REMOVED] == want.removed
    assert (got.amount, got.need, got.months, got.target, got.gap, got.state) == (
        want.amount,
        want.need,
        want.months,
        want.target,
        want.gap,
        want.state,
    )
    assert got.by_owner == {OWNERS[o]: v for o, v in want.by_owner.items()}
    assert [t.code for t in got.todos] == want.todos


def test_two_stale_accounts_each_get_their_todo():
    owners = {UUID(int=1): exact(Decimal("1"))}
    balances = [
        ReserveBalance(
            account_id=uid(key),
            label=key,
            kind=kind,
            balance=Decimal("100.00"),
            as_of=date(2027, 5, 20),
            owners=owners,
        )
        for key, kind in (("giro", "bank_current"), ("karte", "credit_card"))
    ]
    got = F.reserve(
        balances,
        [hm("2027-05", private_spending=Decimal("100.00"))],
        target_months=Decimal("6"),
        today=date(2027, 6, 3),
    )
    assert [t.code for t in got.todos] == [HealthTodoCode.BALANCE_STALE] * 2
    assert got.todos[0].action == "Kontoauszug hochladen"
    assert got.todos[1].action == "Kreditkartenabrechnung hochladen"
    assert got.todos[1].href == f"/accounts/{uid('karte')}?tab=kontoauszuege"
    # seven days old is not stale yet (rules/valuation.STALE_AFTER_DAYS)
    fresh = F.reserve(
        balances, [hm("2027-05")], target_months=Decimal("6"), today=date(2027, 5, 27)
    )
    assert fresh.todos == [] and fresh.months is None


def test_an_account_without_shares_leaves_by_owner_empty():
    plain = ReserveBalance(
        account_id=uid("giro"),
        label="Giro",
        kind="bank_current",
        balance=Decimal("10.00"),
        as_of=date(2027, 6, 2),
    )
    got = F.reserve([plain], [hm("2027-05")], target_months=Decimal("6"), today=date(2027, 6, 3))
    assert got.amount == Decimal("10.00") and got.by_owner == {}


def test_the_need_is_taken_from_the_window_means():
    window = [
        hm("2027-03", private_spending=Decimal("100.00"), rental_result=Decimal("-10.00")),
        hm("2027-04", private_spending=Decimal("100.00"), rental_result=Decimal("-10.00")),
        hm("2027-05", private_spending=Decimal("101.00"), rental_result=Decimal("20.00")),
    ]
    # mean regular 100.33, mean C 0.00: no shortfall
    assert F.need(window) == Decimal("100.33")
    with pytest.raises(ValueError):
        F.need([])


# --- debt.yaml -----------------------------------------------------------------------


def net_worth_of(case) -> NetWorth:
    loans = sum((loan.balance for loan in case.loans), ZERO)
    by_class = {**case.classes, NetWorthClass.LIABILITIES: -(loans + case.card_debt)}
    lines = [
        NetWorthLine(
            kind=NetWorthLineKind.PROPERTY,
            net_worth_class=NetWorthClass.PROPERTY,
            label=key,
            property_id=uid(key),
            value_eur=e.value,
            estimate=True,
        )
        for key, e in case.estimates.items()
    ] + [
        NetWorthLine(
            kind=NetWorthLineKind.LOAN,
            net_worth_class=NetWorthClass.LIABILITIES,
            label=f"Darlehen {i}",
            property_id=uid(loan.property),
            loan_id=uid(f"loan{i}"),
            value_eur=-loan.balance,
            estimate=True,
        )
        for i, loan in enumerate(case.loans)
    ]
    estimate = sum((e.value for e in case.estimates.values()), ZERO)
    return NetWorth(
        on=date(2027, 5, 31),
        total=sum(by_class.values(), ZERO),
        by_class=by_class,
        by_owner={},
        property_estimate=estimate,
        loan_balances=loans,
        property_net=estimate - loans,
        lines=lines,
    )


def debt_window(w):
    first = hm("2027-01", private_income=w.income, principal=w.principal)
    return [first, *(hm(f"2027-{k + 2:02d}") for k in range(w.months - 1))]


@pytest.mark.parametrize("case", DEBT.cases, ids=lambda c: c.id)
def test_debt(case):
    _live(case)
    nw = net_worth_of(case)
    got = F.debt(
        nw,
        debt_window(case.window),
        interest=case.window.interest,
        estimate_sources={uid(k): e.source for k, e in case.estimates.items()},
    )
    want = case.expect
    assert (got.gross_assets, got.liabilities, got.debt_ratio) == (
        want.gross,
        want.liabilities,
        want.debt_ratio,
    )
    assert {p.property_id: p.ltv for p in got.properties if p.ltv is not None} == {
        uid(k): v for k, v in want.ltv.items()
    }
    assert (got.debt_service_year, got.income_year) == (want.debt_service_year, want.income_year)
    assert (got.debt_service_ratio, got.debt_to_income) == (
        want.debt_service_ratio,
        want.debt_to_income,
    )
    if want.estimate_source is not None:
        assert {p.property_id: p.estimate_source for p in got.properties} == {
            uid(k): v for k, v in want.estimate_source.items()
        }
    if want.annualised_from_months is not None:
        assert got.months == want.annualised_from_months and got.annualised
    assert F.split(nw) == want.split
    gapped = {g.figure.value for g in got.gaps}
    assert ("debt_to_income" in gapped) == (want.debt_to_income is None)


def test_two_properties_each_get_their_ltv():
    base = DEBT.cases[0]  # one_loan
    case = base.model_copy(
        update={
            "estimates": {
                "p1": base.estimates["p1"],
                "p2": base.estimates["p1"].model_copy(update={"value": Decimal("100000.00")}),
            },
            "loans": [
                *base.loans,
                base.loans[0].model_copy(update={"property": "p2", "balance": Decimal("50000.00")}),
            ],
        }
    )
    sources = {uid("p1"): EstimateSource.SCENARIO, uid("p2"): EstimateSource.PURCHASE_PRICE}
    got = F.debt(
        net_worth_of(case),
        debt_window(case.window),
        interest=case.window.interest,
        estimate_sources=sources,
    )
    assert {p.property_id: p.ltv for p in got.properties} == {
        uid("p1"): Decimal("0.7388"),
        uid("p2"): Decimal("0.5000"),
    }
    with pytest.raises(ValueError):
        F.debt(net_worth_of(case), debt_window(case.window), interest=ZERO, estimate_sources={})


def test_a_property_without_a_loan_has_no_ltv():
    case = next(c for c in DEBT.cases if c.id == "no_loan")
    got = F.debt(
        net_worth_of(case),
        debt_window(case.window),
        interest=ZERO,
        estimate_sources={uid("p1"): EstimateSource.SCENARIO},
    )
    assert [(p.ltv, p.loan_balance) for p in got.properties] == [(None, ZERO)]


# --- net_worth_series.yaml -----------------------------------------------------------


def check_estimate(scenarios, month_end, purchase_price, want):
    got = F.estimate_on(scenarios, month_end, purchase_price)
    assert got.value == Decimal(want["property_estimate"])
    key = want["scenario"]
    assert got.scenario_id == (None if key is None else uid(key))
    assert got.source is (EstimateSource.PURCHASE_PRICE if key is None else EstimateSource.SCENARIO)


@pytest.mark.parametrize("case", SERIES.cases, ids=lambda c: c.id)
def test_net_worth_series(case):
    _live(case)
    if case.capture is not None:
        cap = case.capture
        rows = [
            ScenarioValue(
                scenario_id=uid(s.id),
                created_on=s.created,
                default=s.default,
                value_today=s.value_today,
            )
            for s in cap.scenarios
        ]
        check_estimate(rows, cap.month_end, cap.purchase_price, case.expect)
        for v in case.variants:
            check_estimate(rows, v.month_end, cap.purchase_price, v.expect.model_dump())
        return
    want = phase9.NsExpect.model_validate(case.expect)
    runs = [
        RunTotals(
            net_worth_run_id=uid(r.id), month_end=r.month_end, created_at=r.created, total=r.total
        )
        for r in case.runs
    ]
    closed = [hm(m, private_income=s) for m, s in case.saved.items()]
    got = F.series(runs, closed)
    points = [p for p in got if p.net_worth_run_id is not None]
    assert [
        (p.month_end, p.net_worth_run_id, p.total, p.change, p.saved, p.other) for p in points
    ] == [
        (p.month_end, None if p.run is None else uid(p.run), p.total, p.change, p.saved, p.other)
        for p in want.points
    ]
    gaps = [p for p in got if p.net_worth_run_id is None]
    assert [p.month_end for p in gaps] == want.gaps
    assert sorted({p.todo.code for p in gaps}) == want.todos
    assert all(p.todo.action == "Monatsabschluss festhalten" for p in gaps)


def test_a_month_not_closed_has_a_change_but_no_saved():
    runs = [
        RunTotals(
            net_worth_run_id=uid(key),
            month_end=end,
            created_at=f"{end.isoformat()}T08:00:00Z",
            total=Decimal(total),
        )
        for key, end, total in (
            ("r1", date(2027, 3, 31), "100.00"),
            ("r2", date(2027, 4, 30), "150.00"),
        )
    ]
    _, second = F.series(runs, [])
    assert second.change == Decimal("50.00") and second.saved is None and second.other is None
    assert F.series([], []) == []


def test_the_estimate_skips_a_withdrawn_scenario():
    rows = [
        ScenarioValue(
            scenario_id=uid("s1"),
            created_on=date(2026, 12, 1),
            default=True,
            value_today=Decimal("310000.00"),
        ),
        ScenarioValue(
            scenario_id=uid("s2"),
            created_on=date(2027, 1, 5),
            default=True,
            withdrawn=True,
            value_today=Decimal("1.00"),
        ),
    ]
    got = F.estimate_on(rows, date(2027, 1, 31), Decimal("300000.00"))
    assert got.scenario_id == uid("s1") and got.value == Decimal("310000.00")


# --- lights.yaml ---------------------------------------------------------------------

LIGHTS = phase9.load("lights")
WINDOW = Window(months=["2027-03", "2027-04", "2027-05"], enough=True)
INCOME = Decimal("10000.00")


def health_of(figures) -> Health:
    """A Health whose figures are the case's values (rates exact over an income of 10,000)."""
    rate = None
    if figures.savings_rate is not None:
        assert figures.savings_rate_regular is not None, "no case gives a rate without it"
        rate = SavingsRate(
            saved=figures.savings_rate * INCOME,
            income=INCOME,
            one_off_spending=(figures.savings_rate_regular - figures.savings_rate) * INCOME,
            principal=ZERO,
            rate=figures.savings_rate,
            rate_regular=figures.savings_rate_regular,
            rate_without_principal=figures.savings_rate,
        )
    reserve = None
    if figures.reserve is not None:
        amount, target = figures.reserve.amount, figures.reserve.target
        reserve = Reserve(
            amount=amount,
            need=target,
            months=(amount / target).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP),
            target_months=Decimal("1"),
            target=target,
            gap=max(ZERO, target - amount),
            state=ReserveState.REACHED if amount >= target else ReserveState.BELOW,
        )
    properties = []
    if figures.ltv is not None:
        properties.append(
            PropertyLtv(
                property_id=uid("p1"),
                loan_balance=ZERO,
                estimate=ZERO,
                estimate_source=EstimateSource.SCENARIO,
                ltv=figures.ltv,
            )
        )
    debt = Debt(
        properties=properties,
        liabilities=ZERO,
        gross_assets=ZERO,
        debt_ratio=figures.debt_ratio,
        interest=ZERO,
        principal=ZERO,
        debt_service_year=ZERO,
        income_year=ZERO,
        debt_service_ratio=figures.debt_service_ratio,
        debt_to_income=figures.debt_to_income,
        months=3,
    )
    return Health(
        as_of=date(2027, 6, 3), window=WINDOW, savings_rate=rate, reserve=reserve, debt=debt
    )


def lights_runs(case):
    runs = [(case.figures, case.bands, case.expect)] if case.expect else []
    return runs + [(v.figures, v.bands, v.expect) for v in case.variants]


@pytest.mark.parametrize("case", LIGHTS.cases, ids=lambda c: c.id)
def test_lights(case):
    _live(case)
    for figures, bands, expect in lights_runs(case):
        got = B.lights(health_of(figures), bands)
        assert set(got) == set(MarkedFigure)
        for figure, want in expect.lights.items():
            mark = got[figure]
            assert (mark.colour, mark.sentences) == (want.colour, want.sentences), figure
            if mark.colour is not None:
                assert mark.origin is LimitOrigin.OWN_LEVER
        assert [t.code for t in B.bands_todos(bands)] == expect.todos


def test_an_empty_list_of_limits_is_unconfirmed():
    case = next(c for c in LIGHTS.cases if c.id == "worked")
    got = B.lights(health_of(case.figures), [])
    assert all(mark.colour is None and mark.origin is None for mark in got.values())
    todo = B.bands_todos([])
    assert [t.code for t in todo] == [HealthTodoCode.BANDS_UNCONFIRMED]
    assert todo[0].action == "Ampel-Grenzen festlegen"
    assert B.bands_todos(case.bands) == []


def test_the_highest_ltv_is_marked():
    case = next(c for c in LIGHTS.cases if c.id == "worked")
    health = health_of(case.figures)
    second = health.debt.properties[0].model_copy(
        update={"property_id": uid("p2"), "ltv": Decimal("1.0500")}
    )
    health.debt.properties.append(second)
    assert B.lights(health, case.bands)[MarkedFigure.LTV].colour is LightColour.RED


def test_the_reserve_mark_keeps_its_limits():
    band = BandLimits(figure=MarkedFigure.RESERVE, red=Decimal("0.5"))
    mark = B.light(Decimal("13410.00"), band, MarkedFigure.RESERVE, target=Decimal("26820.00"))
    assert (mark.colour, mark.green, mark.red) == (LightColour.AMBER, None, Decimal("0.5"))
    # without a target there is nothing to compare
    assert B.light(Decimal("1.00"), band, MarkedFigure.RESERVE).colour is None


# --- health_worked.yaml: §15.2 end to end (step 1's figures and marks) ----------------

WORKED = phase9.load("health_worked")


def run_net_worth(run, properties) -> NetWorth:
    """The newest run as phase 6's NetWorth: its classes, its liabilities negative, and one
    property and one loan line per property (the run's lines)."""
    loans = run.liabilities["loans"]
    by_class = {**run.by_class, NetWorthClass.LIABILITIES: -(loans + run.liabilities["other"])}
    lines = []
    for key, prop in properties.items():
        lines.append(
            NetWorthLine(
                kind=NetWorthLineKind.PROPERTY,
                net_worth_class=NetWorthClass.PROPERTY,
                label=key,
                property_id=uid(key),
                value_eur=prop.default_scenario.value_today,
                estimate=True,
            )
        )
        lines += [
            NetWorthLine(
                kind=NetWorthLineKind.LOAN,
                net_worth_class=NetWorthClass.LIABILITIES,
                label=f"Darlehen {i}",
                property_id=uid(key),
                loan_id=uid(f"{key}-loan{i}"),
                value_eur=-loan.balance,
                estimate=True,
            )
            for i, loan in enumerate(prop.loans)
        ]
    estimate = sum((p.default_scenario.value_today for p in properties.values()), ZERO)
    return NetWorth(
        on=run.month_end,
        total=run.total,
        by_class=by_class,
        by_owner={},
        property_estimate=estimate,
        loan_balances=loans,
        property_net=estimate - loans,
        lines=lines,
    )


@pytest.mark.parametrize("case", WORKED.cases, ids=lambda c: c.id)
def test_health_worked(case):
    """The whole of step 1 on §15.2's household: lines → months → window → figures → marks."""
    _live(case)
    hh, want = case.household, case.expect
    closed = [phase9.add_months(hh.newest_closed, -k) for k in (2, 1, 0)]
    win = W.window(dict.fromkeys(closed, True))
    months, interest = [], ZERO
    for month in win.months:
        got, blocks, _ = household(month, [li for li in hh.lines if str(li.booked)[:7] == month])
        months.append(got)
        interest += sum((b.interest for b in blocks), ZERO)
        if want.months is not None:
            expect = next(m for m in want.months if m.month == month)
            compare(got, blocks, expect, (case.id, month))
    if want.window is not None:
        assert (win.months, win.enough) == (want.window.months, want.window.enough)
    rate = F.savings_rate(months)
    if want.savings_rate is not None:
        assert (
            phase9.HwSavingsRate.model_validate(rate.model_dump(exclude={"months"}))
            == want.savings_rate
        )
    kinds = {a.key: a for a in hh.accounts}
    balances = [
        ReserveBalance(
            account_id=uid(b.account),
            label=b.account,
            kind=kinds[b.account].kind,
            rental=kinds[b.account].purpose == "rental",
            balance=b.balance,
            as_of=b.as_of,
            owners={OWNERS[o]: exact(s) for o, s in kinds[b.account].owners.items()},
        )
        for b in hh.balances
    ]
    reserve = F.reserve(
        balances, months, target_months=hh.levers.reserve_target_months, today=hh.today
    )
    if want.reserve is not None:
        r = want.reserve
        names = {uid(b.account): b.account for b in hh.balances}
        assert [names[a.account_id] for a in reserve.accounts] == r.counted
        assert (reserve.amount, reserve.need, reserve.months) == (r.amount, r.need, r.months)
        assert (reserve.target, reserve.gap, reserve.state) == (r.target, r.gap, r.state)
        assert reserve.todos == []
    run = max(hh.runs, key=lambda r: (r.month_end, r.created))
    nw = run_net_worth(run, hh.properties)
    debt = F.debt(
        nw,
        months,
        interest=interest,
        estimate_sources={uid(k): EstimateSource.SCENARIO for k in hh.properties},
    )
    if want.net_worth is not None:
        assert (debt.gross_assets, debt.liabilities, nw.total) == (
            want.net_worth.gross,
            want.net_worth.liabilities,
            want.net_worth.total,
        )
    if want.debt is not None:
        d = want.debt
        assert {p.property_id: p.ltv for p in debt.properties} == {
            uid(k): v for k, v in d.ltv.items()
        }
        assert (debt.debt_ratio, debt.debt_service_year, debt.income_year) == (
            d.debt_ratio,
            d.debt_service_year,
            d.income_year,
        )
        assert (debt.debt_service_ratio, debt.debt_to_income) == (
            d.debt_service_ratio,
            d.debt_to_income,
        )
        assert F.split(nw) == d.split
    runs = [
        RunTotals(
            net_worth_run_id=uid(r.id), month_end=r.month_end, created_at=r.created, total=r.total
        )
        for r in hh.runs
    ]
    series = F.series(runs, months)
    if want.series is not None:
        assert [(p.month_end, p.total, p.change, p.saved, p.other) for p in series] == [
            (p.month_end, p.total, p.change, p.saved, p.other) for p in want.series
        ]
    health = Health(
        as_of=hh.today,
        window=win,
        newest=months[-1],
        savings_rate=rate,
        reserve=reserve,
        debt=debt,
        split=F.split(nw),
        series=series,
    )
    marks = B.lights(health, hh.levers.bands)
    for figure, expect in want.lights.items():
        assert (marks[figure].colour, marks[figure].sentences) == (
            expect.colour,
            expect.sentences,
        ), figure
    assert [t.code for t in B.bands_todos(hh.levers.bands)] == (want.todos or [])
