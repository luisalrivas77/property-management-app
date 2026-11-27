"""Shapes of the phase 10 YAML rule fixtures (docs/phases/phase-10.md §4).

Loading a fixture through these models is itself a test (tests/test_fixtures_valid.py). The
expectations were computed once by tests/fixture_gen_phase10.py for invented owners A and B
(never from the mockups' numbers), and are checked here on load only for what holds whatever
the rule does: each month's partition adds up to its income (D273, through
hausbuch.schemas.budget.BudgetMonth), the lines add up to the income over the window and per
month mean, and each line's codes add up to the line. tests/rules/test_phase10_budget.py replays
rules/budget on the same inputs.

The input of a budget case is what phase 9 reads, one month at a time: the private accounts'
confirmed allocations signed as on the account (`private`), the transfer links' saved amount
(`saved_links`), and the rental account's confirmed allocations (`rental`). `household(case)`
turns them into phase 9's HouseholdMonth (over phase 6's HouseholdCashflow) and the private
lines the Budget reads, through the production functions
(rules.household.cashflow.month_cashflow, rules.health.month), so the fixtures exercise
exactly the month phase 9's key figures are computed from.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from uuid import NAMESPACE_URL, UUID, uuid5

import yaml
from fixture_models import Lenient
from fixture_models_angaben_topics import AngabenTopicsFixture
from fixture_models_booked_inputs import BookedInputsFixture
from pydantic import AfterValidator, Field, model_validator

from hausbuch.rules.health.month import cashflow_rows, household_month, rental_month
from hausbuch.rules.household.cashflow import month_cashflow
from hausbuch.rules.household.taxonomy import ONE_OFF, one_off_codes
from hausbuch.rules.recurring import SpendingLine
from hausbuch.schemas.advice import AdviceMode
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.budget import (
    BudgetLineKey,
    BudgetMarkColour,
    BudgetMonth,
    BudgetPercent,
    BudgetTargets,
    Points,
)
from hausbuch.schemas.common import _STRING_ONLY, Money, Schema
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.health import HealthTodoCode, HouseholdMonth, MonthLine, YearMonth
from hausbuch.schemas.household import BudgetClass
from hausbuch.schemas.plan import HouseholdLevers, PlanClass, SpendingChange
from hausbuch.schemas.recurring import RecurringInterval, RecurringState, SpendingMarkAnswer

FIXTURES = Path(__file__).parent / "fixtures" / "phase10"
ZERO = Decimal("0.00")
PROPERTY = uuid5(NAMESPACE_URL, "hausbuch:fixture:phase10:property")
Dec = Annotated[Decimal, _STRING_ONLY]


def raw(name: str) -> Any:
    return yaml.safe_load((FIXTURES / f"{name}.yaml").read_text(encoding="utf-8"))


# --- Inputs ------------------------------------------------------------------------------


class Booking(Schema):
    category: AllocationCategory
    amount: Money  # signed as on the account, money in positive


class MonthInput(Schema):
    month: YearMonth
    private: list[Booking] = Field(default_factory=list)
    saved_links: Money = ZERO
    rental: list[Booking] = Field(default_factory=list)
    # the private lines, by position, a person marked one-off (D297; one_off_switches.yaml)
    marked: list[int] = Field(default_factory=list)


def household(
    month: MonthInput,
    case_id: str,
    one_off_codes: frozenset[AllocationCategory] = ONE_OFF,
) -> tuple[HouseholdMonth, list[MonthLine]]:
    """Phase 9's household month of one input month (over phase 6's cashflow of its private
    lines, as services.health reads it) and those private lines; `one_off_codes` the codes in
    force (D307), the month's `marked` lines its one-off marks (D297)."""

    def lines(rows: list[Booking], side: str) -> list[MonthLine]:
        return [
            MonthLine(
                allocation_id=uuid5(NAMESPACE_URL, f"{case_id}:{month.month}:{side}:{i}"),
                category=row.category,
                amount=row.amount,
            )
            for i, row in enumerate(rows)
        ]

    private = lines(month.private, "private")
    cashflow = month_cashflow(month.month, cashflow_rows(private), saved=month.saved_links)
    blocks = (
        [rental_month(month.month, PROPERTY, lines(month.rental, "rental"))] if month.rental else []
    )
    marked = frozenset(private[i].allocation_id for i in month.marked)
    hm = household_month(
        cashflow, blocks, lines=private, one_off_ids=marked, one_off_codes=one_off_codes
    )
    return hm, private


# --- Expectations ------------------------------------------------------------------------


class MarkExpect(Schema):
    colour: BudgetMarkColour
    gap_points: Points
    gap_monthly: Money
    text: str


class CodeExpect(Schema):
    monthly: Money
    total: Money
    budget_class: BudgetClass | None = None
    overridden: bool = False


class LineExpect(Schema):
    key: BudgetLineKey
    monthly: Money
    total: Money
    share: Dec | None = None
    codes: dict[AllocationCategory, CodeExpect] = Field(default_factory=dict)
    target: BudgetPercent | None = None
    mark: MarkExpect | None = None

    @model_validator(mode="after")
    def _codes(self) -> Self:
        if self.key is not BudgetLineKey.SAVING:
            if sum((c.monthly for c in self.codes.values()), ZERO) != self.monthly:
                raise ValueError(f"{self.key}: the codes' means do not add up")
            if sum((c.total for c in self.codes.values()), ZERO) != self.total:
                raise ValueError(f"{self.key}: the codes' sums do not add up")
        return self


class HousingExpect(Schema):
    monthly: Money
    total: Money
    share: Dec | None = None
    codes: list[AllocationCategory] = Field(default_factory=list)
    target: BudgetPercent | None = None
    mark: MarkExpect | None = None


class IncomeExpect(Schema):
    monthly: Money
    total: Money
    rental_monthly: Money
    private_monthly: Money


class BudgetExpect(Schema):
    window: list[YearMonth]
    enough: bool
    months: list[BudgetMonth] = Field(default_factory=list)
    income: IncomeExpect | None = None
    lines: list[LineExpect] = Field(default_factory=list)
    # D308 (amended 25 Nov 2026): „Einmalig im Zeitraum“ beside the partition, not a line of it
    one_off: LineExpect | None = None
    housing: HousingExpect | None = None
    savings_rate: Dec | None = None  # the regular saving ÷ ΣE (D308)
    no_shares: bool = False
    todos: list[HealthTodoCode] = Field(default_factory=list)

    @model_validator(mode="after")
    def _partition(self) -> Self:
        if self.income is None:
            if self.lines or self.months or self.one_off is not None:
                raise ValueError("no figure without an income")
            return self
        if any(li.key is BudgetLineKey.ONE_OFF for li in self.lines):
            raise ValueError("the one-offs stand beside the partition (D308)")
        if self.one_off is not None and self.one_off.key is not BudgetLineKey.ONE_OFF:
            raise ValueError("the one-off figure is the one-off line")
        one_off = sum((m.one_off for m in self.months), ZERO)
        if one_off != (self.one_off.total if self.one_off else ZERO):
            raise ValueError("the one-off figure is the months' one-offs")
        if sum((li.total for li in self.lines), ZERO) != self.income.total:
            raise ValueError("the lines do not add up to the income (D273)")
        if sum((li.monthly for li in self.lines), ZERO) != self.income.monthly:
            raise ValueError("the lines' means do not add up to the income's (D273)")
        saving = [li for li in self.lines if li.key is BudgetLineKey.SAVING]
        if saving and self.savings_rate != saving[0].share:
            raise ValueError("Sparen ÷ E must be the Sparquote (D273)")
        return self


class ScenarioRow(Schema):
    """A default-scenario row's one-off switches and when it was stored (D308: a stored month
    reads the switches of the row in force when it was stored)."""

    created_at: str  # ISO instant, UTC
    one_off_overrides: dict[AllocationCategory, bool] = Field(default_factory=dict)


class BudgetCase(Lenient):
    months: list[MonthInput]
    overrides: dict[AllocationCategory, BudgetClass] = Field(default_factory=dict)
    targets: BudgetTargets = Field(default_factory=BudgetTargets)
    # D307: the household's switches (HouseholdLevers.one_off_overrides), or the scenario rows
    # and the instant the month was stored (one_off_switches.yaml's stored_month_reads_its_day)
    one_off_overrides: dict[AllocationCategory, bool] = Field(default_factory=dict)
    scenarios: list[ScenarioRow] = Field(default_factory=list)
    stored_at: str | None = None
    expect: BudgetExpect

    def switches(self) -> dict[AllocationCategory, bool]:
        """The switches in force: the case's own, or those of the newest scenario row stored
        on or before `stored_at`."""
        if not self.scenarios:
            return self.one_off_overrides
        rows = sorted(
            (r for r in self.scenarios if self.stored_at and r.created_at <= self.stored_at),
            key=lambda r: r.created_at,
        )
        return rows[-1].one_off_overrides if rows else {}

    def codes(self) -> frozenset[AllocationCategory]:
        return one_off_codes(self.switches())


class BudgetFixture(Schema):
    cases: list[BudgetCase]


class FillExpect(Schema):
    targets: BudgetTargets | None = None
    refused: bool = False


class FillCase(Lenient):
    reference: str
    form: BudgetTargets = Field(default_factory=BudgetTargets)
    expect: FillExpect


class ReferenceRow(Schema):
    key: str
    kind: str


class ReferencesFixture(Schema):
    references: list[ReferenceRow]
    cases: list[FillCase]


# --- recurring.yaml and one_off_marks.yaml (§7.9 (b), (c)) ---------------------------------


class SpendingLineIn(Schema):
    id: str
    date: date
    payee: str
    purpose: str | None = None
    category: AllocationCategory
    amount: Money  # signed as on the account, money out negative
    account: str = "giro"  # the account's name in the case (D296 amended: its lines' reach)


def account_id(case_id: str, name: str) -> UUID:
    """A case's account by its name; `giro` keeps the id every case had before accounts were
    named."""
    if name == "giro":
        return uuid5(NAMESPACE_URL, f"{case_id}:account")
    return uuid5(NAMESPACE_URL, f"{case_id}:account:{name}")


def accounts_through(through: dict[str, date], case_id: str) -> dict[UUID, date]:
    """A case's `accounts_through` keyed by the accounts' ids, as rules.recurring reads it."""
    return {account_id(case_id, name): day for name, day in through.items()}


def spending_lines(rows: list[SpendingLineIn], case_id: str) -> list[SpendingLine]:
    """The fixture's lines as rules.recurring's input, each id an allocation id by uuid5."""
    return [
        SpendingLine(
            allocation_id=fixture_id(row.id),
            transaction_id=uuid5(NAMESPACE_URL, f"{case_id}:transaction:{row.id}"),
            account_id=account_id(case_id, row.account),
            account_label="Girokonto" if row.account == "giro" else row.account,
            booked_on=row.date,
            payee=row.payee,
            purpose=row.purpose,
            category=row.category,
            amount=row.amount,
        )
        for row in rows
    ]


def fixture_id(name: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"hausbuch:fixture:phase10:allocation:{name}")


class SeriesMarkIn(Schema):
    key: str
    interval: RecurringInterval
    answer: SpendingMarkAnswer


class SeriesExpect(Schema):
    key: str
    interval: RecurringInterval
    state: RecurringState
    last_amount: Money
    yearly_total: Money
    change: Money | None
    since: YearMonth | None
    payments: int


class RecurringExpect(Schema):
    yearly_detectable: bool
    series: list[SeriesExpect]
    hidden: list[SeriesExpect]


class RecurringCase(Lenient):
    today: date
    history_start: date
    lines: list[SpendingLineIn]
    # the newest line on an account that `lines` leave out (income, transfers), by name
    accounts_through: dict[str, date] = Field(default_factory=dict)
    marks: list[SeriesMarkIn] = Field(default_factory=list)
    expect: RecurringExpect


class RecurringFixture(Schema):
    cases: list[RecurringCase]


class AllocationMarkIn(Schema):
    allocation: str
    answer: SpendingMarkAnswer


class OneOffMonthExpect(Schema):
    month: YearMonth
    private_spending: Money
    one_off_spending: Money
    regular_spending: Money

    @model_validator(mode="after")
    def _split(self) -> Self:
        if self.one_off_spending + self.regular_spending != self.private_spending:
            raise ValueError(f"{self.month}: one-off + regular must be the private spending")
        return self


class OneOffExpect(Schema):
    months: list[OneOffMonthExpect] = Field(default_factory=list)
    mean_regular_spending: Money | None = None
    code_means: dict[AllocationCategory, Money] = Field(default_factory=dict)
    budget_one_off: dict[YearMonth, Money] = Field(default_factory=dict)


class OneOffCase(Lenient):
    today: date
    lines: list[SpendingLineIn]
    marks: list[AllocationMarkIn] = Field(default_factory=list)
    expect: OneOffExpect


class OneOffFixture(Schema):
    cases: list[OneOffCase]


# --- goal.yaml (§7.9 (d), D289, D298) -----------------------------------------------------


class GoalMonthIn(Schema):
    mean_income: Money
    mean_regular_spending: Money
    mean_rental_cash: Money
    buffer_percent: Dec


class GoalStartIn(Schema):
    month_end: date
    bank: Money
    depot: Money
    crypto: Money
    property: Money
    loans: Money
    other_liabilities: Money


class GoalReserveIn(Schema):
    amount: Money
    need: Money


class GoalTodayIn(Schema):
    net_worth: Money
    reserve: Money


class GoalChangeExpect(Schema):
    code: AllocationCategory
    monthly: Money
    from_month: YearMonth
    to_month: YearMonth


class GoalScenarioExpect(Schema):
    closes: bool
    cut: Money
    rest: Money
    saving: Money
    budgets: dict[AllocationCategory, Money]
    changes: list[GoalChangeExpect]


class GoalUnreachableExpect(Schema):
    later: YearMonth | None
    reaches_by_due: Money


class GoalExpect(Schema):
    # D303 (phase 10 §7.11 (c)): „Auf Kurs“, „Machbar“, „Ehrgeizig“ (route 3, not built),
    # „Kaum erreichbar“; „Dahinter“ and „So nicht erreichbar“ until then.
    word: Literal["none", "on_track", "feasible", "ambitious", "hardly"]
    target: Money | None = None
    value_today: Money | None = None
    percent: Money | None = None
    months_left: int | None = None
    heute: Money | None = None
    projected: Money | None = None
    noetig: Money | None = None
    reached: YearMonth | None = None
    reached_with_budget: YearMonth | None = None
    scenarios: dict[Literal["wants_only", "all_equal"], GoalScenarioExpect] = Field(
        default_factory=dict
    )
    unreachable: GoalUnreachableExpect | None = None


class GoalCase(Lenient):
    today: date
    newest_closed: YearMonth
    month: GoalMonthIn
    start: GoalStartIn
    reserve: GoalReserveIn
    saved_mean: Money
    value_today: GoalTodayIn
    means: dict[AllocationCategory, Money]
    levers: HouseholdLevers
    expect: GoalExpect

    @model_validator(mode="after")
    def _regular(self) -> Self:
        if sum(self.means.values(), Decimal(0)) != self.month.mean_regular_spending:
            raise ValueError(f"{self.id}: the codes' means must add up to the regular spending")
        return self


class GoalFixture(Schema):
    bases: dict[str, Any] = Field(default_factory=dict)  # YAML anchors only
    cases: list[GoalCase]


# --- goal_routes.yaml and reserve_above.yaml (§7.11 (a)–(d), D301–D303) --------------------


class RouteStartIn(GoalStartIn):
    reserve_depot: Money = Decimal("0.00")  # D304: the ticked depots' part of `depot`


class RouteFlatIn(Schema):
    """phase 9's NpFlat: a property's default scenario (sale year, its projected years'
    value and loan balance, the sale's net proceeds; its cash 0 in these cases), or with
    `sale_year` null one kept without a scenario (its value today, its loans per 31 Dec)."""

    sale_year: int | None = None
    years: dict[int, tuple[Money, Money]] = Field(default_factory=dict)  # (value, loan)
    net_proceeds: Money = Decimal("0.00")
    kept_value: Money | None = None
    kept_loans: dict[int, Money] = Field(default_factory=dict)


class InvestedIn(Schema):
    """Route 3's weights (§7.11 „Route 3 as decided“ 1): the funds' value today (a ticked depot
    left out) and each coin's, by its symbol."""

    funds: Money
    coins: dict[str, Money] = Field(default_factory=dict)


class HistoryIn(Schema):
    """The reference series as yearly returns in euros (0.10 = +10 %), INVENTED numbers, never
    French's or Coin Metrics' data: `funds` None is the funds series not downloaded."""

    funds: dict[int, Decimal] | None = None
    coins: dict[str, dict[int, Decimal]] = Field(default_factory=dict)


class RouteScenarioExpect(Schema):
    closes: bool
    cut: Money


class BalancedExpect(Schema):
    cuts: dict[AllocationCategory, Money]  # per want code, a month
    cut: Money
    saving_extra: Money | None  # whole euros; None when no saving up to the income closes it
    moved_to_saving: Money
    closes: bool
    # the one rate the route is walked at for funds and crypto: route 3's median when it
    # decides (available, not thin, own use), else None (the household's typed rates)
    return_rate: Decimal | None = None


class MoreSavingExpect(Schema):
    extra: Money
    noetig: Money | None


class LeftOutExpect(Schema):
    """A part of the invested value route 3's mix leaves out (§7.11 „Route 3 as decided“ 1, 2):
    `share` its percent of the invested value today, to 0.1 %."""

    kind: Literal["funds", "coin"]
    symbol: str | None = None  # a coin's symbol; None for the funds or a split's crypto part
    share: Decimal
    reason: Literal["too_short", "no_series", "no_coin"]


RouteReason = Literal[
    "generic_mode", "nothing_invested", "series_missing", "history_short", "out_of_range"
]


class MoreReturnExpect(Schema):
    """Route 3 „Mehr Rendite“ (§7.11 (c) 3, D310 amended). Rates as fractions to 0.001
    (0.128 = 12.8 %): `needed_return` on the 0.1 % grid between −0.5 and 1.0, `median` and
    `best` the windows' annualised returns rounded down to it. `sources` the series keys in
    the mix (the funds series `funds`, a coin by its symbol)."""

    available: bool
    reason: RouteReason | None = None
    needed_return: Decimal | None = None
    window_years: int | None = None
    windows: int | None = None
    reached_windows: int | None = None
    median: Decimal | None = None
    best: Decimal | None = None
    verdict: Literal["within_history", "good_times", "never"] | None = None
    thin: bool | None = None
    left_out: list[LeftOutExpect] = Field(default_factory=list)
    # §7.11 (j), D312: the mix's part held today and the part still to come, percent to 0.1
    mix_today_share: Dec | None = None
    mix_future_share: Dec | None = None
    sources: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _reason(self) -> Self:
        if self.available == (self.reason is not None):
            raise ValueError("route 3 has a reason exactly when it is not available")
        return self


class RouteExpect(Schema):
    word: Literal["none", "on_track", "feasible", "ambitious", "hardly"] | None
    issue: Literal["no_projection"] | None = None
    todos: list[HealthTodoCode] = Field(default_factory=list)
    projected: Money | None = None
    reached: YearMonth | None = None
    noetig: Money | None = None
    more_saving: MoreSavingExpect | None = None
    more_return: MoreReturnExpect | None = None
    balanced: BalancedExpect | None = None
    scenarios: dict[Literal["wants_only", "all_equal"], RouteScenarioExpect] = Field(
        default_factory=dict
    )
    assumption: str | None = None
    # reserve_above.yaml: the reserve above its target today (§7.11 (d)), the reserve row and
    # the net worth at the end of named months of the walk
    above_target: Money | None = None
    reserve_at: dict[YearMonth, Money] = Field(default_factory=dict)
    net_worth_at: dict[YearMonth, Money] = Field(default_factory=dict)


class RouteCase(Lenient):
    today: date
    newest_closed: YearMonth
    month: GoalMonthIn
    start: RouteStartIn
    reserve: GoalReserveIn
    saved_mean: Money
    value_today: GoalTodayIn
    means: dict[AllocationCategory, Money]
    levers: HouseholdLevers
    split: dict[PlanClass, Money] | None = None  # the effective split; None: nothing moves
    flats: dict[str, RouteFlatIn] = Field(default_factory=dict)
    mode: AdviceMode = AdviceMode.OWN_USE
    invested: InvestedIn | None = None  # None: the funds are the start's depot less its ticked part
    history: HistoryIn = Field(default_factory=lambda: HistoryIn())
    expect: RouteExpect

    @model_validator(mode="after")
    def _regular(self) -> Self:
        if sum(self.means.values(), Decimal(0)) != self.month.mean_regular_spending:
            raise ValueError(f"{self.id}: the codes' means must add up to the regular spending")
        return self


class RouteFixture(Schema):
    bases: dict[str, Any] = Field(default_factory=dict)  # YAML anchors only
    cases: list[RouteCase]


# --- plan_page.yaml (§7.11 (h), D306) -------------------------------------------------------


class PlanFlatIn(RouteFlatIn):
    """A property of the walk (RouteFlatIn) with what „Euer Plan“ reads besides: its living
    area in m² (None: not stored) and its scenario's yearly value change (None: none typed)."""

    living_area: Decimal | None = None
    value_change: Decimal | None = None


class PlanSwitchesIn(Schema):
    """„Was geteilt wird“ (§7.11 (h)): the five switches, their defaults as the spec sets them."""

    round_amounts: bool = True
    accounts: bool = False
    wants: bool = False
    risks: bool = True
    assumptions: bool = True


class PlanExpect(Schema):
    """`cards`: a subset of the page's answer „Für euch“, compared key by key (a null expects
    the key present and null); `share`: a subset of the answer „Zum Teilen“ under the case's
    switches; `share_absent`: dotted key paths that must not be in that answer at all."""

    cards: dict[str, Any] = Field(default_factory=dict)
    share: dict[str, Any] | None = None
    share_absent: list[str] = Field(default_factory=list)


class PlanCase(RouteCase):
    net_worth_today: Money
    reserve_today: Money
    reserve_accounts: list[str] = Field(default_factory=list)  # labels with their provider
    flats: dict[str, PlanFlatIn] = Field(default_factory=dict)  # type: ignore[assignment]
    invested_this_month: Money = Decimal("0.00")  # the running month's booked investments
    budget_adopted: bool = False  # the balanced route's wants budget is the default scenario's
    switches: PlanSwitchesIn = Field(default_factory=lambda: PlanSwitchesIn())
    expect: PlanExpect  # type: ignore[assignment]


class RoundingIn(Lenient):
    kind: Literal["net_worth", "reserve", "monthly", "once"]
    value: Money
    rounded: Money


class MaintenanceExpect(Schema):
    year: int
    per_m2: Money | None  # None: no figure for that year or before it


class PlanFixture(Schema):
    bases: dict[str, Any] = Field(default_factory=dict)  # YAML anchors only
    rules: dict[str, Any] = Field(default_factory=dict)  # risks.yaml's values, as expected
    maintenance: list[MaintenanceExpect] = Field(default_factory=list)
    rounding: list[RoundingIn] = Field(default_factory=list)
    cases: list[PlanCase]


# --- budget_fixed.yaml (§7.10 (c), D300) ----------------------------------------------------


class FixedRowExpect(Schema):
    key: str  # the fixture id of the series' oldest payment
    label: str
    interval: RecurringInterval
    state: RecurringState
    monthly: Money
    mean: Money
    change: Money | None
    since: YearMonth | None


class FixedClassExpect(Schema):
    fixed: list[FixedRowExpect]
    variable: Money


class FixedExpect(Schema):
    need: FixedClassExpect
    want: FixedClassExpect


class FixedCase(Lenient):
    today: date
    history_start: date
    window: list[YearMonth]
    lines: list[SpendingLineIn]
    accounts_through: dict[str, date] = Field(default_factory=dict)
    marks: list[SeriesMarkIn] = Field(default_factory=list)
    overrides: dict[AllocationCategory, BudgetClass] = Field(default_factory=dict)
    class_means: dict[BudgetClass, Money]
    expect: FixedExpect

    @model_validator(mode="after")
    def _adds_up(self) -> Self:
        """What holds whatever the rule does: each class's fixed means and variable part add up
        to its class mean (D300)."""
        for budget_class in (BudgetClass.NEED, BudgetClass.WANT):
            got: FixedClassExpect = getattr(self.expect, budget_class.value)
            total = sum((row.mean for row in got.fixed), got.variable)
            if total != self.class_means[budget_class]:
                raise ValueError(f"{self.id}: {budget_class.value} must add up to its class mean")
        return self


class FixedFixture(Schema):
    cases: list[FixedCase]


# --- investment_split.yaml (§7.11 (a) 2, (a) 6, (f) 1, D301, D304) -------------------------

SplitPercent = Annotated[Decimal, _STRING_ONLY, Field(ge=0, le=100, max_digits=4, decimal_places=1)]


class SplitIn(Schema):
    depot: SplitPercent
    crypto: SplitPercent

    @model_validator(mode="after")
    def _hundred(self) -> Self:
        if self.depot + self.crypto != Decimal(100):
            raise ValueError("a split's shares must add up to 100")
        return self


class SplitPlanIn(Schema):
    key: str
    account: str | None = None
    payee: str | None = None
    asset_class: PlanClass
    monthly: Money


class SplitHeldIn(Schema):
    account: str
    asset_class: PlanClass
    value: Money


class SplitRatesIn(Schema):
    depot: Dec | None
    crypto: Dec | None


class SplitTodoExpect(Schema):
    action: str
    names: str


class MixExpect(Schema):
    source: Literal["saved", "savings_plans", "holdings", "none"]
    shares: SplitIn | None
    left_out: list[PlanClass]
    todo: SplitTodoExpect | None = None

    @model_validator(mode="after")
    def _todo(self) -> Self:
        if (self.todo is not None) != (self.shares is None and bool(self.left_out)):
            raise ValueError("the to-do comes exactly when nothing moves for a missing rate")
        return self


class MixCase(Lenient):
    saved: SplitIn | None = None
    plans: list[SplitPlanIn] = Field(default_factory=list)
    held: list[SplitHeldIn] = Field(default_factory=list)
    reserve_added: list[str] = Field(default_factory=list)
    rates: SplitRatesIn
    expect: MixExpect


class PurchaseIn(Schema):
    booked: date
    amount: Money  # signed as on the private account, money out negative
    account: str | None = None  # an own account the money went into, by its name
    account_kind: AccountKind | None = None
    payee: str | None = None  # a third party, with the household code it was booked to
    category: AllocationCategory | None = None

    @model_validator(mode="after")
    def _one_destination(self) -> Self:
        own = self.account is not None and self.account_kind is not None
        payee = self.payee is not None and self.category is not None
        if own == payee:
            raise ValueError("a purchase goes into an own account or to a payee, not both")
        return self


class PrefillExpect(Schema):
    depot: Money
    crypto: Money
    split: SplitIn


class PrefillCase(Lenient):
    window: list[YearMonth]
    purchases: list[PurchaseIn]
    reserve_added: list[str] = Field(default_factory=list)
    expect: PrefillExpect | None


class NotInvestedLineExpect(Schema):
    asset_class: PlanClass
    target_percent: SplitPercent
    actual_percent: SplitPercent
    amount: Money


class NotInvestedExpect(Schema):
    invested: Money
    amount: Money
    lines: list[NotInvestedLineExpect]

    @model_validator(mode="after")
    def _sum(self) -> Self:
        if self.lines and sum((x.amount for x in self.lines), ZERO) != self.amount:
            raise ValueError("the lines must add up to the amount")
        return self


class NotInvestedCase(Lenient):
    window: list[YearMonth]
    purchases: list[PurchaseIn]
    reserve_added: list[str] = Field(default_factory=list)
    saved: SplitIn | None = None
    mode: AdviceMode
    expect: NotInvestedExpect | None


class InvestmentSplitFixture(Schema):
    purchases: dict[str, Any] = Field(default_factory=dict)  # YAML anchors only
    windows: dict[str, Any] = Field(default_factory=dict)  # YAML anchors only
    mix: list[MixCase]
    prefill: list[PrefillCase]
    not_invested: list[NotInvestedCase]


# --- goal_invest.yaml (§7.9 (e), „(e) as decided for the build“ 3, 4; D298 (5)–(7)) -----------

TrackerRoute = Literal["more_return", "balanced"]
TrackerReason = Literal[
    "generic_mode",
    "no_goal",
    "not_following",
    "route_gone",
    "goal_issue",
    "no_plan",
    "unpriced",
    "unvalued",
]


class TrackerFollowedIn(Schema):
    """Today's answer of the route followed: its return and, for the balanced route, what it
    changes from next month (the saving extra and the spending changes)."""

    route: TrackerRoute
    rate: Annotated[Decimal, _STRING_ONLY]
    saving_extra: Money = ZERO
    spending_changes: list[SpendingChange] = Field(default_factory=list)


class TrackerFlowIn(Schema):
    day: date  # `on` reads as a YAML boolean
    amount: Money | None  # + in, − out; None: no price that day


class TrackerActualIn(Schema):
    """The funds and crypto accounts (no reserve depot): their value at the end of the day
    chosen and today, the external flows after the day chosen, and the positions without a
    value."""

    value_then: Money
    flows: list[TrackerFlowIn] = Field(default_factory=list)
    value_now: Money
    unvalued: int = 0


class TrackerExpect(Schema):
    shown: bool
    reason: TrackerReason | None = None
    route: TrackerRoute | None = None
    chosen_on: date | None = None
    days: int | None = None
    rate: Annotated[Decimal, _STRING_ONLY] | None = None
    planned_per_year: Money | None = None
    planned_by_now: Money | None = None
    paid_in: Money | None = None
    value_then: Money | None = None
    value_now: Money | None = None
    value_change: Money | None = None
    implied_change: Money | None = None
    yearly_return: Annotated[Decimal, _STRING_ONLY] | None = None
    no_rate: Literal["short_history", "unpriced", "unvalued", "no_flows", "no_rate"] | None = None
    word: Literal["on_path", "behind"] | None = None
    short: list[Literal["paid", "value"]] = Field(default_factory=list)


class TrackerCase(RouteCase):
    """The walk's household (RouteCase's inputs), the route followed today (or why none
    counts) and the accounts' actual figures."""

    followed: TrackerFollowedIn | Literal["route_gone", "goal_issue"] | None = None
    actual: TrackerActualIn | None = None
    expect: TrackerExpect  # type: ignore[assignment]

    @model_validator(mode="after")
    def _followed(self) -> Self:
        choice = self.levers.goal_choice
        if isinstance(self.followed, TrackerFollowedIn) and (
            choice is None or choice.investment != self.followed.route
        ):
            raise ValueError(f"{self.id}: the route followed is the one chosen")
        return self


class TrackerFixture(Schema):
    bases: dict[str, Any] = Field(default_factory=dict)  # YAML anchors only
    cases: list[TrackerCase]


# --- goal_property.yaml (§7.9 (e), „(e) as decided for the build“ 1, 2; D289, D298) ---------


def _cents(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), ROUND_HALF_UP)


class GrowthPropertyIn(Schema):
    """A property's default scenario as phase 4 projects its value: `base` the value today,
    `typed_growth` the scenario's yearly value growth; with `selling_rate` its net proceeds in
    the sale year are V − V × rate (to the cent) − the loan, no tax in these cases."""

    base: Money
    typed_growth: Annotated[Decimal, _STRING_ONLY]
    selling_rate: Annotated[Decimal, _STRING_ONLY] | None = None

    def value(self, growth: Decimal, years: int) -> Decimal:
        return _cents(self.base * (Decimal(1) + growth) ** years)

    def proceeds(self, growth: Decimal, years: int, loan: Decimal) -> Decimal:
        if self.selling_rate is None:
            raise ValueError("no selling rate")
        v = self.value(growth, years)
        return v - _cents(v * self.selling_rate) - loan


GrowthReason = Literal["out_of_range", "history_short"]


class GrowthExpect(Schema):
    """One property's card: rates as fractions to 0.001 (0.030 = 3.0 %)."""

    available: bool
    reason: GrowthReason | None = None
    typed_growth: Annotated[Decimal, _STRING_ONLY]
    needed_growth: Annotated[Decimal, _STRING_ONLY] | None
    sale_year: int
    window_years: int
    windows: int
    reached_windows: int | None = None
    median: Annotated[Decimal, _STRING_ONLY] | None = None
    best: Annotated[Decimal, _STRING_ONLY] | None = None
    verdict: Literal["within_history", "good_times", "never"] | None = None
    thin: bool

    @model_validator(mode="after")
    def _reason(self) -> Self:
        if self.available == (self.reason is not None):
            raise ValueError("a card has a reason exactly when its count is not available")
        return self


class GrowthGoalExpect(Schema):
    shown: bool
    reason: Literal["generic_mode", "not_net_worth", "goal_issue"] | None = None
    properties: dict[str, GrowthExpect] = Field(default_factory=dict)  # by flat key


class GrowthCase(RouteCase):
    """The walk's household (RouteCase's inputs) with each flat's scenario as phase 4 values
    it and an INVENTED house price index (Q4 of each year), never Eurostat's."""

    properties: dict[str, GrowthPropertyIn] = Field(default_factory=dict)
    index: dict[int, Annotated[Decimal, _STRING_ONLY]] = Field(default_factory=dict)
    expect: GrowthGoalExpect  # type: ignore[assignment]

    @model_validator(mode="after")
    def _phase4(self) -> Self:
        """Each projected year's value is phase 4's at the typed growth, and the net proceeds
        the closed form at it, so the cases hold whatever the rule does."""
        for key, prop in self.properties.items():
            flat = self.flats.get(key)
            if flat is None or flat.sale_year is None:
                continue
            for year, (value, _loan) in flat.years.items():
                if value != prop.value(prop.typed_growth, year - self.today.year):
                    raise ValueError(f"{self.id}: {key} {year} is not phase 4's value")
            if prop.selling_rate is not None:
                n = flat.sale_year - self.today.year
                loan = flat.years[flat.sale_year][1]
                if flat.net_proceeds != prop.proceeds(prop.typed_growth, n, loan):
                    raise ValueError(f"{self.id}: {key}'s net proceeds are not the closed form")
        return self


class GrowthFixture(Schema):
    bases: dict[str, Any] = Field(default_factory=dict)  # YAML anchors only
    cases: list[GrowthCase]


# --- reference_returns.yaml (§7.11 (c) 3, D310 amended) -------------------------------------


def _fraction(value: str) -> str:
    Fraction(value)  # an exact fraction such as '-1/25'
    return value


ExactFraction = Annotated[str, AfterValidator(_fraction)]


class ReferenceFxIn(Schema):
    usd_per_eur: dict[int, Decimal]  # the ECB's year-end rate, USD per EUR
    dem_per_usd: dict[int, Decimal] = Field(default_factory=dict)  # the Bundesbank's, before 1999


class FundsRowIn(Schema):
    mkt_rf: Decimal  # percent, as French publishes it
    rf: Decimal


class ReferenceExpect(Schema):
    usd_per_eur: dict[int, ExactFraction]
    funds: dict[int, ExactFraction]
    coins: dict[str, dict[int, ExactFraction]]
    pegged: dict[Literal["USD", "EUR"], dict[int, ExactFraction]]


class ReferenceFixture(Schema):
    fx: ReferenceFxIn
    funds: dict[int, FundsRowIn]
    coins: dict[str, dict[int, Decimal]]  # 31 Dec prices in USD
    expect: ReferenceExpect


# --- goal_path.yaml (§7.11 (i), D311) ------------------------------------------------------


class PathCeilingExpect(Schema):
    """Item 1: what the household can save at most a month and its parts."""

    income: Money
    need: Money
    wants_kept: Money
    # The regular spending outside the need and the wants' positive means, signed (a refund
    # lowers it, an uncategorised line raises it); 0 where the codes are needs and wants only.
    other_spending: Money = ZERO
    suggested: bool  # no wants target saved: the wants kept are the suggestion (half of each)
    buffer: Money
    ceiling: Money


class PathWhyExpect(Schema):
    """Item 4: with the whole room used at the typed rates, the value at the due month and
    the month the goal is then reached (None within 40 years)."""

    due_value: Money
    reached: YearMonth | None


class PathExpect(Schema):
    state: Literal["on_track", "doable_rates", "doable_return", "ambitious", "hardly"]
    ceiling: PathCeilingExpect
    anlegbar: Money
    room: Money
    extra: Money | None = None  # the path's extra a month; None without a path
    monthly: Money | None = None  # Anlegbar + extra
    rate: Annotated[Decimal, _STRING_ONLY] | None = None  # the rate the path counts on
    return_needed: Annotated[Decimal, _STRING_ONLY] | None = None
    window_years: int | None = None
    windows: int | None = None
    reached_windows: int | None = None
    short_history: bool | None = None
    years: tuple[int, int] | None = None  # the mix's first and last year
    # D312: step 2's mix, the part held today and the part still to come, percent to 0.1
    mix_today_share: Dec | None = None
    mix_future_share: Dec | None = None
    wants_budget: dict[AllocationCategory, Money] = Field(default_factory=dict)
    wants_budget_total: Money | None = None
    adoptable: bool
    why: PathWhyExpect | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.room != max(ZERO, self.ceiling.ceiling - self.anlegbar):
            raise ValueError("the room is the ceiling less Anlegbar, at least 0")
        if self.adoptable != (self.state in ("on_track", "doable_rates", "doable_return")):
            raise ValueError("a path is adoptable exactly in the states that have one")
        if self.adoptable:
            if self.extra is None or self.monthly != self.anlegbar + self.extra:
                raise ValueError("a path's monthly amount is Anlegbar + its extra")
            if self.extra > self.room or self.why is not None:
                raise ValueError("a path stays within the room and needs no why")
        elif self.why is None:
            raise ValueError("no path: the why is shown")
        return self


class PathCaseExpect(Schema):
    word: Literal["on_track", "feasible", "ambitious", "hardly"]
    path: PathExpect


class PathCase(RouteCase):
    expect: PathCaseExpect  # type: ignore[assignment]


class PathAdoptedIn(Schema):
    day: date
    monthly: Money
    wants_budget: Money
    rate: Annotated[Decimal, _STRING_ONLY]
    crypto_percent: Dec | None  # the saved split's crypto percent; None: no saved split
    now: Money | None = None  # §7.11 (k): „Jetzt anlegen“ as adopted
    now_crypto: Money | None = None  # its crypto part


class PathMonthIn(Schema):
    month_end: date
    saved: Money
    principal: Money = Decimal("0.00")  # Q-O194: the month's loan repayment
    invested_property: Money = Decimal("0.00")  # Q-O194: paid into the property (D234)
    invested: Money
    crypto_bought: Money
    wants: Money
    net_worth: Money
    path_net_worth: Money
    value_now: Money
    flows: list[tuple[date, Money]]
    invested_since: Money | None = None  # §7.11 (k): bought after the day, in the month adopted
    crypto_since: Money | None = None


class PathRowExpect(Schema):
    month_end: date
    word: Literal["on_path", "behind"]
    invest_now: Money | None = None  # §7.11 (k): still open at the month's start
    waiting: Money | None
    invested_gap: Money | None
    wants_gap: Money | None
    net_worth_gap: Money | None
    value_change: Money
    implied_change: Money
    actual_percent: Dec
    needed_percent: Dec
    value_behind: bool


class PathTrackingCase(Lenient):
    adopted: PathAdoptedIn
    value_then: Money
    months: list[PathMonthIn]
    expect: list[PathRowExpect]

    @model_validator(mode="after")
    def _rows(self) -> Self:
        if [m.month_end for m in self.months] != [r.month_end for r in self.expect]:
            raise ValueError(f"{self.id}: one row per month-end")
        return self


class PathFixture(Schema):
    bases: dict[str, Any] = Field(default_factory=dict)  # YAML anchors only
    cases: list[PathCase]
    tracking: list[PathTrackingCase]


MODELS: dict[str, type[Schema]] = {
    "reference_returns": ReferenceFixture,
    "budget_fixed": FixedFixture,
    "budget_partition": BudgetFixture,
    "budget_overrides": BudgetFixture,
    "budget_marks": BudgetFixture,
    "one_off_switches": BudgetFixture,
    "budget_references": ReferencesFixture,
    "recurring": RecurringFixture,
    "one_off_marks": OneOffFixture,
    "goal": GoalFixture,
    "goal_routes": RouteFixture,
    "goal_path": PathFixture,
    "reserve_above": RouteFixture,
    "plan_page": PlanFixture,
    "investment_split": InvestmentSplitFixture,
    "booked_inputs": BookedInputsFixture,
    "angaben_topics": AngabenTopicsFixture,
    "goal_invest": TrackerFixture,
    "goal_property": GrowthFixture,
}


def load(name: str) -> Any:
    return MODELS[name].model_validate(raw(name))
