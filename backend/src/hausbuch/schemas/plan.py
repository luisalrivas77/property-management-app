"""The household plan: levers, the surplus, savings plans, the paths to the reserve target and
the net worth in some years (docs/phases/phase-9.md §6, §7, §11, §12; migration 0048).

Phase 9 step 0 writes the shapes; step 2 fills them (rules/plan/, services/plan.py). A
scenario is the household's assumption, never a fact, and an append-only row like phase 4's
`projection_scenario` (D248). A lever without a default is None and no screen pre-fills it:
the two yearly rates of the projection have no default value and no example (D247 amended).
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import Field, model_validator

from .common import _STRING_ONLY, Money, NonNegativeMoney, Schema, _string_schema
from .health import BandLimits, HealthTodo, Reserve, TargetMonths, YearMonth, is_month_end

# The buffer kept back from the free cash (D244, §7.3): 0 to 50 %, one place.
BufferPercent = Annotated[
    Decimal,
    Field(ge=0, le=50, max_digits=3, decimal_places=1),
    _STRING_ONLY,
    _string_schema(1),
]
# An assumed yearly change of a class's value (D247 amended, §7.3): −0.50 to 0.50 in steps of
# 0.001. Not schemas.common.Rate, which cannot be negative (§12 names `Rate`; §7.3 and the
# worked example's −0.015 settle the bound).
ValueChangeRate = Annotated[
    Decimal,
    Field(ge=Decimal("-0.5"), le=Decimal("0.5"), max_digits=4, decimal_places=3),
    _STRING_ONLY,
    _string_schema(3, signed=True),
]
# An assumption's value (§7.5 (9)): a typed depot or crypto rate (ValueChangeRate, three
# places, signed) or phase 4's growth lever (Rate, six places); bounded by both.
AssumptionValue = Annotated[
    Decimal,
    Field(ge=Decimal("-0.5"), lt=Decimal("1"), max_digits=7, decimal_places=6),
    _STRING_ONLY,
    _string_schema(6, signed=True),
]
# A savings plan's key: stable per destination (an account or a payee pattern).
PlanKey = Annotated[str, Field(pattern=r"^[a-z0-9_-]{1,64}$")]
# A path: reserve_first, as_is, or pause:<plan key> (D246).
PathKey = Annotated[str, Field(pattern=r"^(reserve_first|as_is|pause:[a-z0-9_-]{1,64})$")]

LEVER_INVALID = "plan.lever_invalid"


# --- Levers (D248, §7.3) ----------------------------------------------------------------


class IncomeChangeNote(StrEnum):
    """A code, never a sentence (§12), as phase 4b stores questionnaire answers."""

    PARENTAL_LEAVE = "parental_leave"
    JOB_CHANGE = "job_change"
    PART_TIME = "part_time"
    OTHER = "other"


class IncomeChange(Schema):
    monthly_net: Money  # signed: −600.00 lowers the monthly income
    from_month: YearMonth
    to_month: YearMonth | None = None  # None: no end
    note_code: IncomeChangeNote = IncomeChangeNote.OTHER

    @model_validator(mode="after")
    def _order(self) -> Self:
        if self.to_month is not None and self.to_month < self.from_month:
            raise ValueError(f"{LEVER_INVALID}: an income change ends on or after its start")
        return self


class OneOffSource(StrEnum):
    TYPED = "typed"
    RETURN_ESTIMATE = "return_estimate"  # phase 4b's refund or back-payment, once accepted


class OneOff(Schema):
    month: YearMonth
    amount: Money  # signed: a refund positive, a back-payment negative
    source: OneOffSource = OneOffSource.TYPED


class ProposedOneOff(Schema):
    """Phase 4b's expected refund (positive) or back-payment (negative) of one tax year, from
    the whole-return estimate of Steuern › Gesamt, proposed without a month (Q-O168, answered
    by mockup 12): a member picks the month the money is expected, and „Übernehmen“ adds it to
    the levers as `OneOff(source=return_estimate)`. Until then the plan counts no refund."""

    tax_year: int = Field(ge=2000, le=2100)
    amount: Money  # signed like OneOff: −ReturnEstimate.result
    source: OneOffSource = OneOffSource.RETURN_ESTIMATE


class PlanOverride(Schema):
    """Sets a detected plan's monthly amount, or drops it (D245)."""

    plan_key: PlanKey
    monthly: NonNegativeMoney | None = None
    dropped: bool = False

    @model_validator(mode="after")
    def _one(self) -> Self:
        if self.dropped == (self.monthly is not None):
            raise ValueError(f"{LEVER_INVALID}: an override sets an amount or drops the plan")
        return self


class ReserveAccounts(Schema):
    """Accounts added to or removed from the reserve's default set (D237). Their kind
    (bank_current, bank_savings, credit_card) is checked by the service, which sees them."""

    added: list[UUID] = Field(default_factory=list)
    removed: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def _disjoint(self) -> Self:
        if set(self.added) & set(self.removed):
            raise ValueError(f"{LEVER_INVALID}: an account is added or removed, not both")
        return self


class ValueChange(Schema):
    """The yearly change of a class's value without its distributions (§7.5 (10)), typed by
    a member. Neither field has a default value or an example: an empty field means no
    projection, a typed 0 is a rate (D247 amended)."""

    depot: ValueChangeRate | None = None
    crypto: ValueChangeRate | None = None


class HouseholdLevers(Schema):
    """`household_scenario.levers` (D248). The defaults are the spec's (§18 Q1)."""

    reserve_target_months: TargetMonths = Decimal("6.0")
    buffer_percent: BufferPercent = Decimal("10")
    horizon_months: int = Field(default=60, ge=12, le=240)
    reserve_accounts: ReserveAccounts = Field(default_factory=ReserveAccounts)
    plan_overrides: list[PlanOverride] = Field(default_factory=list)
    income_changes: list[IncomeChange] = Field(default_factory=list)
    one_offs: list[OneOff] = Field(default_factory=list)
    value_change: ValueChange = Field(default_factory=ValueChange)
    net_worth_years: int = Field(default=10, ge=1, le=30)
    bands: list[BandLimits] = Field(default_factory=list)  # confirmed limits (D250 (3))

    @model_validator(mode="after")
    def _unique(self) -> Self:
        keys = [o.plan_key for o in self.plan_overrides]
        if len(keys) != len(set(keys)):
            raise ValueError(f"{LEVER_INVALID}: one override per plan")
        figures = [b.figure for b in self.bands]
        if len(figures) != len(set(figures)):
            raise ValueError(f"{LEVER_INVALID}: one band per figure")
        return self


class HouseholdScenarioCreate(Schema):
    name: str = Field(min_length=1, max_length=100)
    is_default: bool = False
    levers: HouseholdLevers = Field(default_factory=HouseholdLevers)
    supersedes_id: UUID | None = None
    withdrawn: bool = False

    @model_validator(mode="after")
    def _withdrawal(self) -> Self:
        if self.withdrawn and self.supersedes_id is None:
            raise ValueError("a withdrawal supersedes the scenario it withdraws")
        return self


class HouseholdScenario(HouseholdScenarioCreate):
    """household_scenario row (append-only; §11 names its columns created_*)."""

    id: UUID
    workspace_id: UUID
    created_at: datetime
    created_by: UUID | None = None


# --- The plan today (§6) ----------------------------------------------------------------


class SurplusKey(StrEnum):
    """The rows of „So entsteht die Zahl“, in this order (D244, §6.1)."""

    MEAN_INCOME = "mean_income"
    MEAN_REGULAR_SPENDING = "mean_regular_spending"
    MEAN_RENTAL_CASH = "mean_rental_cash"
    FREE_CASH = "free_cash"
    BUFFER = "buffer"
    INVESTABLE = "investable"
    MEDIAN_FREE_CASH = "median_free_cash"
    LOWEST_FREE_CASH = "lowest_free_cash"
    ONE_OFF_SPENDING_LEFT_OUT = "one_off_spending_left_out"


class SurplusRow(Schema):
    key: SurplusKey
    amount: Money
    month: YearMonth | None = None  # the lowest month's
    sources: list[UUID] = Field(default_factory=list)


class Surplus(Schema):
    """§6.1. Free cash is the sum of the three rounded means (D244); the buffer is 0 when the
    free cash is not positive; investable = max(0, free cash − buffer)."""

    rows: list[SurplusRow]

    def amount(self, key: SurplusKey) -> Decimal:
        return next(r.amount for r in self.rows if r.key is key)

    @model_validator(mode="after")
    def _rows(self) -> Self:
        if [r.key for r in self.rows] != list(SurplusKey):
            raise ValueError("every row once, in the spec's order")
        k = SurplusKey
        free = self.amount(k.MEAN_INCOME) - self.amount(k.MEAN_REGULAR_SPENDING)
        free += self.amount(k.MEAN_RENTAL_CASH)
        if self.amount(k.FREE_CASH) != free:
            raise ValueError("free cash = mean income − mean regular spending + mean flat")
        if free <= 0 and self.amount(k.BUFFER) != 0:
            raise ValueError("no buffer without free cash")
        if self.amount(k.INVESTABLE) != max(Decimal("0.00"), free - self.amount(k.BUFFER)):
            raise ValueError("investable = max(0, free cash − buffer)")
        return self


class PlanClass(StrEnum):
    DEPOT = "depot"
    CRYPTO = "crypto"


class SavingsPlan(Schema):
    """A savings plan detected from the last three closed months (D245): the same destination
    in each of the three, monthly = the median of the three sums."""

    key: PlanKey
    destination_account_id: UUID | None = None
    destination_payee: str | None = None
    asset_class: PlanClass
    monthly: NonNegativeMoney
    months: list[Money] = Field(min_length=3, max_length=3)
    overridden: bool = False

    @model_validator(mode="after")
    def _destination(self) -> Self:
        if (self.destination_account_id is None) == (self.destination_payee is None):
            raise ValueError("a plan goes to an account or to a payee")
        return self


class PlanMonth(Schema):
    """One future month of §7.1."""

    month: YearMonth
    flat: Money
    free: Money
    investable: Money


class Path(Schema):
    """D246, §7.2. None: not within the horizon."""

    key: PathKey
    paused: list[PlanKey] = Field(default_factory=list)
    to_reserve_first_month: Money
    reached: YearMonth | None = None
    months_needed: int | None = Field(default=None, ge=0)
    used_up: YearMonth | None = None
    reserve_at_horizon: Money


# --- The net worth in some years (§7.5, D247 amended) -----------------------------------


class PathLater(Schema):
    """One path's amount to the reserve per month from `FlatChange.month` on."""

    key: PathKey
    to_reserve: Money


class FlatChange(Schema):
    """The month the flat's default scenario first gives another monthly result than the
    first plan month (phase 4's projected year after the newest closed month's year, §7.1),
    computed without one-offs: that month's flat result, the flat result of the months before
    it (`before_is_mean`: that is the window's measured mean, „statt im Schnitt“), and each
    path's amount to the reserve from then on (the month's investable less the plans that
    continue on that path), in the order of `HouseholdPlan.paths`."""

    month: YearMonth
    flat: Money
    before: Money
    before_is_mean: bool
    paths: list[PathLater]


class ProjectionChange(Schema):
    """The change since the start in five rows that sum to it (§7.5 (7))."""

    on_accounts: Money
    paid_in: Money  # into the savings plans
    from_assumptions: Money  # per class: value − start − paid in
    from_assumptions_by_class: dict[PlanClass, Money] = Field(default_factory=dict)
    flat_value: Money  # the flat's value by its scenario
    loan_repaid: Money
    total: Money

    @model_validator(mode="after")
    def _sums(self) -> Self:
        rows = self.on_accounts + self.paid_in + self.from_assumptions
        if rows + self.flat_value + self.loan_repaid != self.total:
            raise ValueError("the five rows sum to the change")
        by_class = sum(self.from_assumptions_by_class.values(), Decimal("0.00"))
        if self.from_assumptions_by_class and by_class != self.from_assumptions:
            raise ValueError("the classes sum to the assumptions' part")
        return self


class ProjectionPoint(Schema):
    """One 31 December (or the start): the classes, the liabilities, the total."""

    on: date
    bank: Money
    depot: Money
    crypto: Money
    property: Money
    loans: Money  # positive: owed on the properties' loans
    other_liabilities: Money  # a card's debt, kept at its start value
    total: Money
    change: ProjectionChange | None = None  # None at the start

    @model_validator(mode="after")
    def _total(self) -> Self:
        assets = self.bank + self.depot + self.crypto + self.property
        if assets - self.loans - self.other_liabilities != self.total:
            raise ValueError("total = the classes − the liabilities")
        if self.change is not None and not (self.on.month == 12 and self.on.day == 31):
            raise ValueError("a projected point is a 31 December")
        if self.change is None and not is_month_end(self.on):
            raise ValueError("the start is a month-end with a stored run")
        return self


class AssumptionKind(StrEnum):
    DEPOT_RATE = "depot_rate"
    CRYPTO_RATE = "crypto_rate"
    FLAT_GROWTH = "flat_growth"  # the property scenario's value growth (phase 4)


class ProjectionAssumption(Schema):
    """What was assumed, by which scenario row (§7.5 (9))."""

    kind: AssumptionKind
    value: AssumptionValue
    scenario_id: UUID  # a household_scenario or, for the flat, a projection_scenario
    property_id: UUID | None = None
    created_at: datetime
    created_by: UUID | None = None


class NetWorthProjection(Schema):
    """§7.5. Instead of the points, the to-dos when an input is missing (§7.5 (1))."""

    start: ProjectionPoint | None = None
    points: list[ProjectionPoint] = Field(default_factory=list)
    assumptions: list[ProjectionAssumption] = Field(default_factory=list)
    ends_before_sale_year: int | None = None  # the earliest sale year, when it cuts the points
    used_up: YearMonth | None = None  # the as_is path's reserve falls below 0 (§7.5 (8))
    todos: list[HealthTodo] = Field(default_factory=list)

    @model_validator(mode="after")
    def _either(self) -> Self:
        if self.todos and self.points:
            raise ValueError("a missing input gives to-dos and no figure")
        if self.points and self.start is None:
            raise ValueError("the points start from a stored run")
        return self


class HouseholdPlan(Schema):
    """GET /household-plan (§13)."""

    as_of: date
    scenario_id: UUID | None = None  # None: the defaults
    levers: HouseholdLevers
    newest_closed: YearMonth | None = None
    surplus: Surplus | None = None
    plans: list[SavingsPlan] = Field(default_factory=list)
    already_invested: Money | None = None  # Σ monthly of the plans
    investable_left: Money | None = None  # investable − already invested, signed
    reserve: Reserve | None = None
    paths: list[Path] = Field(default_factory=list)
    # the plans with no pause row of their own (more than four plans): the screen's „weitere“
    paths_others: list[PlanKey] = Field(default_factory=list)
    # the flat's mean C when positive: that part lands on the rental account first, which is
    # not a reserve account by default (D246's „davon X € auf dem Mietkonto“); None otherwise,
    # and None while the lever `reserve_accounts.added` counts a rental account (PR #434 E)
    paths_on_rental_account: Money | None = None
    # the first month the flat's own scenario replaces the measured mean, with each path's
    # monthly amount from then on (mockup 12: „ab Januar 2028: 1.139 € im Monat“); None when
    # that one month's figure would not hold from then on (paths.holds_from, PR #434 B)
    flat_change: FlatChange | None = None
    months: list[PlanMonth] = Field(default_factory=list)
    proposed_one_offs: list[ProposedOneOff] = Field(default_factory=list)
    projection: NetWorthProjection | None = None
    todos: list[HealthTodo] = Field(default_factory=list)
