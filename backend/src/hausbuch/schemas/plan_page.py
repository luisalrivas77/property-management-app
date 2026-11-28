"""„Euer Plan“ (docs/phases/phase-10.md §7.11 (h), D306; mockup 19i): `GET /household-goal/plan`.

Seven cards from the goal's one walk (rules/goal/risks.plan_cards), every figure the server's.
Two views: `own` („Für euch“) with everything, and `share` („Zum Teilen“) built from
rules/goal/share.share: amounts rounded, and the parts a switch leaves out absent from the
answer. Absent means the key is not in the JSON, never null or an empty list, so a label the
household did not choose to show cannot reach a screen by mistake; each model below that can
lose a key drops it from its dump when it was not set (`_ABSENT`). Names, the property's
address and label, tenants, IBANs and wallet addresses are never in the shared view.
"""

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, ClassVar

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer

from .allocation import AllocationCategory
from .common import _STRING_ONLY, Money, NonNegativeMoney, Schema, _string_schema
from .enums import PropertyKind
from .goal import GoalIssue, GoalLeftOut, GoalPercent, GoalReturn, GoalRouteReason, GoalVerdict
from .goal import GoalWord as GoalWord
from .health import HealthTodo, ReserveMonths, YearMonth
from .plan import AssumptionValue, GoalKind, PlanClass, ValueChangeRate

# The maintenance rate per m² of living area a year (risks.yaml, ImmoWertV Anlage 3).
PerSquareMetre = Annotated[
    Decimal,
    Field(ge=0, max_digits=7, decimal_places=2),
    _STRING_ONLY,
    _string_schema(2),
]


class _Absent(Schema):
    """A model whose `_ABSENT` fields are left out of its dump while unset (the shared view)."""

    _ABSENT: ClassVar[tuple[str, ...]] = ()

    @model_serializer(mode="wrap")
    def _without_absent(self, handler: SerializerFunctionWrapHandler):
        # No return annotation: pydantic then keeps the fields' JSON schema for the answer.
        data = handler(self)
        if isinstance(data, dict):
            for name in self._ABSENT:
                if name not in self.model_fields_set:
                    data.pop(name, None)
        return data


class PlanView(StrEnum):
    OWN = "own"  # „Für euch“
    SHARE = "share"  # „Zum Teilen“


class PlanSwitches(Schema):
    """„Was geteilt wird“ (D306): the five switches the shared view was built with."""

    round_amounts: bool = True
    accounts: bool = False
    wants: bool = False
    risks: bool = True
    assumptions: bool = True


class PlanGoalCard(Schema):
    """Card 1, the green goal card of 19f: target, month, word and the share reached."""

    kind: GoalKind
    target: Money | None
    due_month: YearMonth
    word: GoalWord | None
    value_today: Money | None
    percent: GoalPercent | None


class PlanHeute(_Absent):
    """Card 2 „Heute“: the net worth, the reserve in months of its need (D305) and the mean saved
    per month of the window; the reserve's accounts (D304) with their provider, „Für euch“ or
    with the switch `accounts` only."""

    _ABSENT: ClassVar[tuple[str, ...]] = ("reserve_accounts",)

    net_worth: Money
    reserve: Money
    reserve_months: ReserveMonths | None
    reserve_full: bool | None
    saved_mean: Money
    # „Anlegbar jetzt“ (§7.11 (d)): the reserve above its target today, GET /household-plan's
    # `above_target`; read by the on-track plan card („Jetzt anlegen“). Rounded like the reserve
    # in „Zum Teilen“.
    above_target: NonNegativeMoney | None = None
    # §7.13 (d) 2: the reserve „X € von Y €“, the target and what is still missing to it (0 once
    # reached); None without a target. The gap is the server's, the browser only formats it.
    reserve_target: Money | None = None
    reserve_gap: NonNegativeMoney | None = None
    window_from: YearMonth | None = None  # the closed months the mean is taken over
    window_to: YearMonth | None = None
    reserve_accounts: list[str] = Field(default_factory=list)


class PlanWant(Schema):
    """A want the route lowers: its measured mean (`from`) and its budget on the route (`to`)."""

    code: AllocationCategory
    from_: NonNegativeMoney = Field(alias="from")
    to: NonNegativeMoney


class PlanRoute(_Absent):
    """Card 3 „Der Plan“, the main route (D303, „Ausgewogener Weg“): the extra invested per
    month and its split by class, the wants' cut and the month reached; „auf Kurs“ without
    moves. `without_route` the value at the due month without change, `gap` the target less it."""

    _ABSENT: ClassVar[tuple[str, ...]] = ("wants",)

    on_track: bool
    reached: YearMonth | None
    without_route: Money | None
    gap: NonNegativeMoney | None
    saving_extra: NonNegativeMoney | None
    by_class: dict[PlanClass, NonNegativeMoney] = Field(default_factory=dict)
    cut: NonNegativeMoney | None
    wants: list[PlanWant] = Field(default_factory=list)


class PlanRates(Schema):
    """The yearly rates the route walks (typed by the household, D247; the reserve's D304)."""

    depot: ValueChangeRate | None = None
    crypto: ValueChangeRate | None = None
    reserve: ValueChangeRate | None = None


class PlanPropertyAssumption(_Absent):
    """A property in the walk: kept (D301 (a) 5) or sold in its scenario's year, and its
    scenario's yearly value change. `kind` names it generically („Wohnung“); `label` only „Für
    euch“, since a property's label can be its address."""

    _ABSENT: ClassVar[tuple[str, ...]] = ("label",)

    key: str  # the property's id
    kind: PropertyKind | None = None
    kept: bool
    sale_year: int | None
    value_change: AssumptionValue | None
    label: str | None = None


class PlanAssumptions(Schema):
    """Card 4 „Eure Annahmen“: nothing the app chose."""

    rates: PlanRates
    properties: list[PlanPropertyAssumption] = Field(default_factory=list)


class PlanWeakMarkets(Schema):
    """„Schwache Märkte“: per kind the worst window of the goal's years in its own history
    (`worst`), walked never above the typed rate (`rates`, `capped`)."""

    available: bool
    reason: GoalRouteReason | None = None
    window_years: int | None = None
    rates: dict[PlanClass, GoalReturn] = Field(default_factory=dict)
    worst: dict[PlanClass, GoalReturn] = Field(default_factory=dict)
    capped: list[PlanClass] = Field(default_factory=list)
    left_out: list[GoalLeftOut] = Field(default_factory=list)
    reached: YearMonth | None = None
    delay_months: int | None = None
    not_until: YearMonth | None = None


class PlanIncomeGap(Schema):
    """„Einkommenslücke“: months with no income, the reserve paying first, then the invested
    money (`drawn`); `used_up` when even that is short."""

    available: bool
    months: int
    reached: YearMonth | None = None
    delay_months: int | None = None
    not_until: YearMonth | None = None
    drawn: NonNegativeMoney = Decimal("0.00")
    used_up: YearMonth | None = None


class PlanRepairReason(StrEnum):
    NO_PROPERTY = "no_property"
    NO_LIVING_AREA = "no_living_area"
    NO_RATE = "no_rate"


class PlanRepair(Schema):
    """„Große Reparatur“: living area × the year's maintenance rate × `years`, paid once."""

    available: bool
    reason: PlanRepairReason | None = None
    per_m2: PerSquareMetre | None = None
    years: int | None = None
    by_property: dict[str, NonNegativeMoney] = Field(default_factory=dict)  # by property id
    amount: NonNegativeMoney | None = None
    reached: YearMonth | None = None
    delay_months: int | None = None
    not_until: YearMonth | None = None
    used_up: YearMonth | None = None


class PlanSource(Schema):
    """A source of a size in risks.yaml (the maintenance rate)."""

    name: str
    url: str
    read_on: str


class PlanRisks(_Absent):
    """Card 5 „Wenn es schlechter läuft“: the route run again under three changes, each the
    months it moves the goal. The to-dos (a property's living area) never in the shared view."""

    _ABSENT: ClassVar[tuple[str, ...]] = ("todos",)

    base_reached: YearMonth | None
    weak_markets: PlanWeakMarkets
    income_gap: PlanIncomeGap
    repair: PlanRepair
    sources: list[PlanSource] = Field(default_factory=list)
    todos: list[HealthTodo] = Field(default_factory=list)


class PlanOtherRoutes(Schema):
    """Card 6 „Andere Wege“: routes 2, 3 and 4 of §7.11 (c), one figure each; route 3's history
    (windows reached, verdict) for its panel."""

    more_saving: NonNegativeMoney | None
    needed_return: GoalReturn | None
    less_spending: NonNegativeMoney | None
    return_windows: int | None = None
    return_reached_windows: int | None = None
    return_verdict: GoalVerdict | None = None


class PlanStepKind(StrEnum):
    INVEST = "invest"  # „<Betrag> mehr anlegen“
    WANTS_BUDGET = "wants_budget"  # „Wünsche-Budget <Betrag>“


class PlanStep(Schema):
    kind: PlanStepKind
    amount: NonNegativeMoney
    done: bool  # computed from the data, never typed (Q-O190)


class PlanThisMonth(Schema):
    """Card 7 „Diesen Monat“: the route's steps for the running month, at most two."""

    steps: list[PlanStep] = Field(default_factory=list)
    done: int
    of: int


class PlanPage(_Absent):
    """`GET /household-goal/plan`. `goal` None while no goal is set (word `none`); the other
    cards None while the goal has no word (`issue` says why, `todos` what is missing). In the
    shared view `risks` or `assumptions` are absent when their switch is off, and the to-dos
    always."""

    _ABSENT: ClassVar[tuple[str, ...]] = ("switches", "risks", "assumptions", "todos")

    view: PlanView
    switches: PlanSwitches | None = None  # the shared view's only
    word: GoalWord | None
    issue: GoalIssue | None = None
    goal: PlanGoalCard | None = None
    heute: PlanHeute | None = None
    plan: PlanRoute | None = None
    assumptions: PlanAssumptions | None = None
    risks: PlanRisks | None = None
    other_routes: PlanOtherRoutes | None = None
    this_month: PlanThisMonth | None = None
    todos: list[HealthTodo] = Field(default_factory=list)
