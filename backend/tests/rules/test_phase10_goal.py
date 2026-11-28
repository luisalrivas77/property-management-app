"""rules/goal against tests/fixtures/phase10/goal.yaml (docs/phases/phase-10.md §7.9 (d); D289,
D298), the monthly walk against phase 9's net-worth projection, and rule_version() unchanged."""

from decimal import Decimal
from fractions import Fraction

import fixture_models_phase10 as phase10
import pytest
from test_phase9_plan import PROJECTION, basis, run_case, scenario_of, start_of

from hausbuch.rules.budget import budget as B
from hausbuch.rules.goal import GoalInputs, Projection, household_goal, none_set, walk
from hausbuch.rules.goal import history as H
from hausbuch.rules.plan import levers as L
from hausbuch.rules.plan import projection as N
from hausbuch.schemas.goal import GoalSpending, GoalWord
from hausbuch.schemas.plan import HouseholdLevers, InvestmentSplit
from hausbuch.services.health import rule_version

GOAL = phase10.load("goal").cases

# services.health.rule_version() after phase 10 §7.12 (b), (c) (D307, D308: the one-offs by
# category and left out of the Sparquote changed rules/health and rules/plan once; it was
# 3d24e195... after PR #590; the review's fix, one-off sums from the month's lines, is in this
# value). rules/goal lies outside the hashed folders, so §7.9 (d) leaves it
# as it was; a change of rules/health or rules/plan must update this value on purpose (D242).
# §7.11 (g), (f) 1 (D305, D304: the reserve's need from need-class spending, a depot the
# lever adds) changed rules/health once more; it was 19e42b29... until then.
# §7.11 (a) (D301: one month walk in rules/plan/projection, money above the reserve target
# invested, the walk past each sale) changed rules/plan; it was 2b975321... until then.
# §7.11 (h) (26 Nov 2026, „Euer Plan“'s income gap: `walk(draw_invested=...)`, off by default,
# so every existing answer is unchanged) changed rules/plan; it was 5f75a985... until then.
# §7.11 (j) (27 Nov 2026, D312: `Walk.invested`, read only by the goal's history check) changed
# rules/plan; it was 95bb50be... until then.
RULE_VERSION = "749708febaa684a151b8402de196d5c03078575c799a6f6b778083abd181af1b"


def _projection(case) -> Projection:
    m = case.month
    month_basis = L.MonthBasis(
        newest_closed=case.newest_closed,
        mean_income=m.mean_income,
        mean_regular_spending=m.mean_regular_spending,
        mean_rental_cash=m.mean_rental_cash,
        buffer_percent=m.buffer_percent,
        spending_changes=tuple(case.levers.spending_changes),
        code_means=dict(case.means),
    )
    s = case.start
    start = N.ProjectionStart(
        month_end=s.month_end,
        bank=s.bank,
        depot=s.depot,
        crypto=s.crypto,
        property=s.property,
        loans=s.loans,
        other_liabilities=s.other_liabilities,
    )
    return Projection(
        basis=month_basis,
        start=start,
        reserve_start=case.reserve.amount,
        plans=(),
        flats={},
        levers=case.levers,
    )


def _answer(case):
    levers = case.levers
    if levers.goal is None:
        return none_set()
    classes = {
        code: cls
        for code in case.means
        if (cls := B.class_of(code, levers.budget_class_overrides)) is not None
    }
    value_today = (
        case.value_today.net_worth
        if levers.goal.kind.value == "net_worth"
        else case.value_today.reserve
    )
    return household_goal(
        GoalInputs(
            today=case.today,
            goal=levers.goal,
            choice=levers.goal_choice,
            heute=case.saved_mean,
            value_today=value_today,
            need=case.reserve.need,
            code_means=dict(case.means),
            classes=classes,
            household_changes=tuple(levers.spending_changes),
            projection=_projection(case),
            mean_income=case.month.mean_income,
        )
    )


@pytest.mark.parametrize("case", GOAL, ids=lambda c: c.id)
def test_goal(case) -> None:
    got = _answer(case)
    want = case.expect
    assert got.word.value == want.word
    for field in (
        "target",
        "value_today",
        "percent",
        "months_left",
        "heute",
        "projected",
        "noetig",
        "reached",
        "reached_with_budget",
    ):
        assert getattr(got, field) == getattr(want, field), field
    assert got.issue is None
    scenarios = {s.kind.value: s for s in got.scenarios}
    assert set(scenarios) == set(want.scenarios)
    for kind, expected in want.scenarios.items():
        s = scenarios[kind]
        assert s.closes is expected.closes, kind
        assert (s.cut, s.rest, s.saving) == (expected.cut, expected.rest, expected.saving), kind
        assert {b.code: b.budget for b in s.budgets} == expected.budgets, kind
        assert [(c.code, c.monthly, c.from_month, c.to_month) for c in s.spending_changes] == [
            (c.code, c.monthly, c.from_month, c.to_month) for c in expected.changes
        ], kind
    if want.unreachable is None:
        assert got.unreachable is None
    else:
        assert got.unreachable is not None
        assert got.unreachable.later == want.unreachable.later
        assert got.unreachable.reaches_by_due == want.unreachable.reaches_by_due


@pytest.mark.parametrize("case", [c for c in GOAL if c.expect.scenarios], ids=lambda c: c.id)
def test_a_scenario_is_a_valid_budget(case) -> None:
    """„Als Budget übernehmen“ stores a scenario's changes as the levers' spending changes; the
    levers' own checks (one code per group, a planned amount ≥ 0) accept every one."""
    for s in _answer(case).scenarios:
        levers = HouseholdLevers.model_validate(
            {
                **case.levers.model_dump(mode="json"),
                "spending_changes": [c.model_dump(mode="json") for c in s.spending_changes],
                "goal_choice": {"spending": s.kind.value, "spending_chosen_on": "2026-11-24"},
            }
        )
        assert levers.goal_choice.spending is GoalSpending(s.kind)


def test_without_a_goal_the_word_is_none() -> None:
    assert none_set().word is GoalWord.NONE


@pytest.mark.parametrize(
    "case",
    [c for c in PROJECTION.cases if c.levers is not None and not c.expect.todos],
    ids=lambda c: c.id,
)
def test_the_walk_equals_phase_9_at_every_december(case) -> None:
    """The monthly walk repeats phase 9's projection: at each 31 December its net worth is
    phase 9's point, and its reserve row phase 9's as_is row month by month."""
    run = run_case(case)
    levers = HouseholdLevers(
        value_change=case.levers.value_change,
        net_worth_years=case.levers.net_worth_years,
        income_changes=case.levers.income_changes,
        one_offs=case.levers.one_offs,
    )
    flats = {p: scenario_of(f) for p, f in case.flats.items()}
    p = Projection(
        basis=basis(case.newest_closed, case.month),
        start=start_of(case.start),
        reserve_start=case.reserve,
        plans=case.plans,
        flats={k: f for k, f in flats.items() if f is not None},
        levers=levers,
        target=case.target,
        split=case.split,
    )
    w = walk(p)
    assert run.projection.points, "a case with points"
    for point in run.projection.points:
        month = w.at(f"{point.on.year}-12")
        assert month is not None, point.on
        assert month.net_worth == point.total, point.on
    for ym, reserve in run.as_is.items():
        if w.used_up is not None and ym >= w.used_up:
            break
        assert w.at(ym).reserve == reserve, ym
    if run.projection.used_up is not None:  # phase 9 walks fewer years than the goal's walk
        assert w.used_up == run.projection.used_up


def test_rule_version_is_unchanged() -> None:
    rule_version.cache_clear()
    assert rule_version() == RULE_VERSION


# --- goal_routes.yaml and reserve_above.yaml (§7.11 (a)–(d), D301–D303) -------------------

ROUTES = (
    phase10.load("goal_routes").cases
    + phase10.load("reserve_above").cases
    + phase10.load("goal_property_sale").cases  # §7.13 (d) 4, D316
)


def _route_flat(f) -> N.FlatScenario:
    zero = Decimal("0.00")
    return N.FlatScenario(
        sale_year=f.sale_year,
        years={
            y: L.FlatYear(net_cash=zero, tax_effect=None, value=v, loan_balance=loan)
            for y, (v, loan) in f.years.items()
        },
        net_proceeds=f.net_proceeds,
        kept_value=f.kept_value,
        kept_loans=f.kept_loans,
    )


def _route_inputs(case) -> tuple[GoalInputs, Projection | None, Decimal]:
    m, s, levers = case.month, case.start, case.levers
    basis = L.MonthBasis(
        newest_closed=case.newest_closed,
        mean_income=m.mean_income,
        mean_regular_spending=m.mean_regular_spending,
        mean_rental_cash=m.mean_rental_cash,
        buffer_percent=m.buffer_percent,
        spending_changes=tuple(levers.spending_changes),
        code_means=dict(case.means),
    )
    start = N.ProjectionStart(
        month_end=s.month_end,
        bank=s.bank,
        depot=s.depot,
        crypto=s.crypto,
        property=s.property,
        loans=s.loans,
        other_liabilities=s.other_liabilities,
        reserve_depot=s.reserve_depot,
    )
    split = (
        None
        if case.split is None
        else InvestmentSplit(depot=case.split["depot"], crypto=case.split["crypto"])
    )
    target = (levers.reserve_target_months * case.reserve.need).quantize(Decimal("0.01"))
    todos = N.projection_todos(start, s.month_end, [], levers, split)
    p = None
    if not todos:
        p = Projection(
            basis=basis,
            start=start,
            reserve_start=case.reserve.amount,
            plans=(),
            flats={k: _route_flat(f) for k, f in case.flats.items()},
            levers=levers,
            target=target,
            split=split,
        )
    classes = {
        code: cls
        for code in case.means
        if (cls := B.class_of(code, levers.budget_class_overrides)) is not None
    }
    goal = levers.goal
    inputs = GoalInputs(
        today=case.today,
        goal=goal,
        choice=levers.goal_choice,
        heute=case.saved_mean,
        value_today=(
            case.value_today.net_worth
            if goal.kind.value == "net_worth"
            else case.value_today.reserve
        ),
        need=case.reserve.need,
        code_means=dict(case.means),
        classes=classes,
        household_changes=tuple(levers.spending_changes),
        projection=p,
        todos=todos,
        mean_income=m.mean_income,
        wants_max=levers.budget_targets.wants_max,
        mode=case.mode,
        invested=_invested(case),
        history=_history(case),
    )
    return inputs, p, target


def _invested(case) -> H.Invested:
    """Route 3's weights: the case's own, else the start's depot less its ticked part."""
    if case.invested is not None:
        return H.Invested(funds=case.invested.funds, coins=dict(case.invested.coins))
    assert case.start.crypto == 0, f"{case.id}: crypto held needs `invested`"
    return H.Invested(funds=case.start.depot - case.start.reserve_depot, coins={})


def _series(key: str, returns) -> H.Series:
    return H.Series(
        key=key,
        returns={y: Fraction(str(r)) for y, r in returns.items()},
        source=f"invented {key}",
        url="",
        licence="",
        attribution="",
    )


def _history(case) -> H.History:
    h = case.history
    return H.History(
        funds=None if h.funds is None else _series("funds", h.funds),
        coins={symbol: _series(symbol, r) for symbol, r in h.coins.items()},
    )


@pytest.mark.parametrize("case", ROUTES, ids=lambda c: c.id)
def test_goal_routes(case) -> None:
    inputs, p, target = _route_inputs(case)
    got = household_goal(inputs)
    want = case.expect
    assert (got.word.value if got.word is not None else None) == want.word
    assert (got.issue.value if got.issue is not None else None) == want.issue
    assert [t.code for t in got.todos] == want.todos
    for name in ("projected", "reached", "noetig"):
        if name in want.model_fields_set:
            assert getattr(got, name) == getattr(want, name), name
    if "more_saving" in want.model_fields_set:
        expect = want.more_saving
        if expect is None:
            assert got.more_saving is None
        else:
            assert (got.more_saving.extra, got.more_saving.noetig) == (expect.extra, expect.noetig)
    if want.more_return is not None:
        r, expect = got.more_return, want.more_return
        assert r.available is expect.available
        assert (r.reason.value if r.reason is not None else None) == expect.reason
        for name in (
            "needed_return",
            "window_years",
            "windows",
            "reached_windows",
            "median",
            "best",
            "thin",
            "mix_today_share",
            "mix_future_share",
        ):
            if name in expect.model_fields_set:
                assert getattr(r, name) == getattr(expect, name), name
        if "verdict" in expect.model_fields_set:
            assert (r.verdict.value if r.verdict is not None else None) == expect.verdict
        assert [(x.kind.value, x.symbol, x.share, x.reason.value) for x in r.left_out] == [
            (x.kind, x.symbol, x.share, x.reason) for x in expect.left_out
        ]
        if "sources" in expect.model_fields_set:
            assert [x.key for x in r.sources] == expect.sources
    if "balanced" in want.model_fields_set:
        b, expect = got.balanced, want.balanced
        if expect is None:
            assert b is None
        else:
            assert {x.code: x.mean - x.budget for x in b.budgets} == expect.cuts
            assert (b.cut, b.saving_extra, b.moved_to_saving, b.closes, b.return_rate) == (
                expect.cut,
                expect.saving_extra,
                expect.moved_to_saving,
                expect.closes,
                expect.return_rate,
            )
    if "scenarios" in want.model_fields_set:
        scenarios = {s.kind.value: s for s in got.scenarios}
        assert set(scenarios) == set(want.scenarios)
        for kind, expect in want.scenarios.items():
            assert (scenarios[kind].closes, scenarios[kind].cut) == (expect.closes, expect.cut)
    if "assumption" in want.model_fields_set:
        assert [f"{a.text} {a.action}" for a in got.assumptions] == (
            [want.assumption] if want.assumption else []
        )
        assert all(a.href == "/assets?tab=immobilie" for a in got.assumptions)
    if want.above_target is not None:
        assert N.above_target(case.reserve.amount, target) == want.above_target
    if want.reserve_at or want.net_worth_at:
        assert p is not None
        w = walk(p)
        for ym, value in want.reserve_at.items():
            assert w.at(ym).reserve == value, ym
        for ym, value in want.net_worth_at.items():
            assert w.at(ym).net_worth == value, ym


def test_ambitious_only_through_the_paths_step_2() -> None:
    """D311 (amends D303): „Ehrgeizig“ only when the path's step 2 finds the return needed
    reached in some but under half of the windows, in own use; route 3 decides no word. At
    least one case reads it, so the check is never empty."""
    seen = set()
    for case in ROUTES:
        inputs, _, _ = _route_inputs(case)
        got = household_goal(inputs)
        if got.word is GoalWord.AMBITIOUS:
            path = got.goal_path
            assert path.state.value == "ambitious", case.id
            assert path.reached_windows > 0 and path.reached_windows * 2 < path.windows, case.id
            assert case.mode.value == "own_use", case.id
        seen.add(got.word)
    assert GoalWord.AMBITIOUS in seen


REFERENCE = phase10.load("reference_returns")


def _exact(d) -> dict:
    return {y: Fraction(v) for y, v in d.items()}


def test_reference_returns_in_euros() -> None:
    """D310 amended: the yearly euro returns from the published series, by code."""
    f = REFERENCE
    fx = H.usd_per_eur(f.fx.usd_per_eur, f.fx.dem_per_usd)
    assert fx == _exact(f.expect.usd_per_eur)
    rows = {y: (r.mkt_rf, r.rf) for y, r in f.funds.items()}
    assert H.funds_returns(rows, fx) == _exact(f.expect.funds)
    for symbol, prices in f.coins.items():
        assert H.coin_returns(prices, fx) == _exact(f.expect.coins[symbol]), symbol
    for currency, want in f.expect.pegged.items():
        assert H.pegged_returns(currency, fx) == _exact(want), currency


def test_future_crypto_spreads_over_the_coins_held_by_their_value() -> None:
    """§7.11 (j), D312: the money still to come joins at the split, its crypto share spread over
    the coins held today by their value, a coin without a series left out with its blended
    share. Funds 60,000, BTC 30,000 and XYZ 10,000 (no series) with 80,000 still to come at
    50/50: funds 60,000 + 40,000 = 100,000, BTC 30,000 + 40,000 × 3/4 = 60,000, XYZ 10,000 +
    40,000 × 1/4 = 20,000, of 180,000. XYZ is left out with 20/180 = 11.1 %; the funds weigh
    100/160 = 5/8 and BTC 60/160 = 3/8: 2020 5/8 × 0.1 + 3/8 × 1.0 = 0.4375, 2021 5/8 × 0.2 +
    3/8 × (−0.5) = −0.0625. Held 100,000 of 180,000: 55.6 % and 44.4 %."""
    invested = H.Invested(
        funds=Decimal("60000.00"), coins={"BTC": Decimal("30000.00"), "XYZ": Decimal("10000.00")}
    )
    history = H.History(
        funds=_series("funds", {2020: "0.1", 2021: "0.2"}),
        coins={"BTC": _series("BTC", {2020: "1.0", 2021: "-0.5"})},
    )
    split = InvestmentSplit(depot=Decimal("50"), crypto=Decimal("50"))
    mix = H.mix_of(invested, split, history, 1, future=Decimal("80000.00"))
    assert isinstance(mix, H.Mix)
    assert [(x.symbol, x.share, x.reason.value) for x in mix.left_out] == [
        ("XYZ", Decimal("11.1"), "no_series")
    ]
    assert mix.weights == (Fraction(5, 8), Fraction(3, 8))
    assert mix.returns == {2020: Fraction(7, 16), 2021: Fraction(-1, 16)}
    assert (mix.today_share, mix.future_share) == (Decimal("55.6"), Decimal("44.4"))
    # without the money still to come (the weak-markets risk) the weights are today's alone
    alone = H.mix_of(invested, split, history, 1)
    assert isinstance(alone, H.Mix)
    assert alone.weights == (Fraction(2, 3), Fraction(1, 3))
    assert (alone.today_share, alone.future_share) == (None, None)


def _kept_at_year_turn(loans_from_year: int) -> N.Projection:
    """A kept flat at the year turn: today in January 2027, the newest closed month 2026-11, its
    loan 300,000 € in the stored run, then 295,000 € on 31 Dec 2027 and 5,000 € less a year."""
    from datetime import date

    zero = Decimal("0.00")
    years = range(loans_from_year, loans_from_year + N.WALK_YEARS + 2)
    balance = {2026: Decimal("299500.00")}
    loans = {y: balance.get(y, max(zero, Decimal("295000.00") - 5000 * (y - 2027))) for y in years}
    return N.Projection(
        basis=L.MonthBasis(
            newest_closed="2026-11",
            mean_income=Decimal("4000.00"),
            mean_regular_spending=Decimal("3000.00"),
            mean_rental_cash=zero,
            buffer_percent=Decimal("0"),
        ),
        start=N.ProjectionStart(
            month_end=date(2026, 11, 30),
            bank=Decimal("20000.00"),
            depot=zero,
            crypto=zero,
            property=Decimal("350000.00"),
            loans=Decimal("300000.00"),
            other_liabilities=zero,
        ),
        reserve_start=Decimal("20000.00"),
        plans=(),
        flats={
            "flat": N.FlatScenario(
                sale_year=None,
                kept_value=Decimal("350000.00"),
                kept_loans=loans,
                start_value=Decimal("350000.00"),
                start_loans=Decimal("300000.00"),
            )
        },
        levers=HouseholdLevers(),
    )


def test_kept_flat_missing_december_is_no_anchor_never_a_loan_of_zero():
    """Review finding of 25 Nov 2026: a kept flat's loan balances began at today's year, so the
    December of the newest closed month's year read 0 and the net worth jumped by the loan."""
    without_2026 = N.walk(_kept_at_year_turn(2027), until="2028-01")
    for month in ("2026-12", "2027-06"):
        assert without_2026.at(month).net_worth < Decimal("100000.00"), month
    with_2026 = N.walk(_kept_at_year_turn(2026), until="2028-01")
    assert with_2026.at("2026-12").net_worth == Decimal("71500.00")
    assert with_2026.at("2027-12").net_worth == without_2026.at("2027-12").net_worth


def test_percent_of_a_tiny_target_stays_inside_the_schema():
    from hausbuch.rules.goal.goal import PERCENT_CAP, percent

    assert percent(Decimal("307315.00"), Decimal("1.00")) == PERCENT_CAP
    assert percent(Decimal("51000.00"), Decimal("100000.00")) == Decimal("51.0")


def test_the_screens_read_fields_follow_the_walks() -> None:
    """Mockup 19f's read-only fields: „fehlen“ is target − projected, the balanced route's month
    is the first its own walk reaches the target (never after the due month), the chart's
    points end at the due month with the projected value there, and each sold property is
    named by its year and net proceeds."""
    checked = set()
    for case in ROUTES:
        inputs, p, _ = _route_inputs(case)
        got = household_goal(inputs)
        if got.projected is None:
            assert got.path == [] and got.short_at_due is None, case.id
            continue
        target = got.target
        due = inputs.goal.due_month
        missed = got.projected < target
        assert got.short_at_due == (target - got.projected if missed else None), case.id
        assert got.path[-1].month == due and got.path[-1].as_is == got.projected, case.id
        assert all(x.month <= due for x in got.path), case.id
        assert [s.year for s in got.sales] == sorted(
            f.sale_year for f in p.flats.values() if f.sale_year is not None
        ), case.id
        b = got.balanced
        if b is not None and b.closes:
            assert b.reached is not None and b.reached <= due, case.id
            assert got.path[-1].balanced is not None, case.id
            assert got.path[-1].balanced >= target, case.id
            checked.add("balanced")
        else:
            assert all(x.balanced is None for x in got.path), case.id
        if got.sales:
            checked.add("sales")
        if missed:
            checked.add("short")
    assert checked == {"balanced", "sales", "short"}


def test_the_balanced_cut_never_exceeds_a_wants_mean() -> None:
    """§7.11 (c): the net wants can exceed the playable ones (a positive mean on a code no
    change can play, here the refund code), so the room shared over the playable wants is
    capped at each code's mean: no budget below 0, never a crash on a negative spending
    change. The household of balanced_wants_net_of_a_refund with restaurant 100, refund +800
    and the 15 % wants target (750): room 900 − 750 = 150 against 100 of playable wants."""
    from decimal import Decimal as D

    from hausbuch.schemas.allocation import AllocationCategory as C

    base = next(c for c in ROUTES if c.id == "balanced_wants_net_of_a_refund")
    means = {
        C("hh_rent"): D("1500.00"),
        C("hh_supermarket"): D("600.00"),
        C("hh_restaurant"): D("100.00"),
        C("hh_refund"): D("800.00"),
    }
    case = base.model_copy(update={"means": means})
    inputs, _, _ = _route_inputs(case)
    got = household_goal(inputs)
    assert got.balanced is not None
    for b in got.balanced.budgets:
        assert D("0.00") <= b.budget <= b.mean
