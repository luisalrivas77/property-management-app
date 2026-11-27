"""The household goal (docs/phases/phase-10.md §7.9 (d), D289, D298): `GET /household-goal`.

The goal and what the household chose for it are levers of the default household scenario
(schemas.plan.Goal, GoalChoice); this is the answer computed from them by rules/goal on read.
Every figure is computed by code; the app proposes no goal, no date and no amount (D289 (1)).
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import Field

from .allocation import AllocationCategory
from .common import _STRING_ONLY, Money, NonNegativeMoney, ReturnRate, Schema, _string_schema
from .health import HealthTodo, YearMonth
from .household import BudgetClass
from .plan import (
    Goal,
    GoalChoice,
    GoalPathState,
    GoalSpending,
    SavingsPlan,
    SpendingChange,
)
from .portfolio import NoRateReason

# Today's value against the target, one place (it may pass 100 once the goal is reached).
GoalPercent = Annotated[
    Decimal, Field(ge=0, max_digits=8, decimal_places=1), _STRING_ONLY, _string_schema(1)
]


class GoalWord(StrEnum):
    """The one word Übersicht shows beside the financial-health verdict (§7.9 (d) 5; D303
    since 25 Nov 2026, „Dahinter“ and „So nicht erreichbar“ until then). Computed on read,
    never stored."""

    NONE = "none"  # no goal set: „Ziel festlegen“
    ON_TRACK = "on_track"  # „Auf Kurs“: the walk without change reaches it
    # „Machbar“: a spending scenario (route 4) closes the gap, or route 3 is within the median
    FEASIBLE = "feasible"
    # „Ehrgeizig“: only through route 3, its return reached only in good times while no
    # spending scenario closes; never in `generic` mode or on a thin history
    AMBITIOUS = "ambitious"
    HARDLY = "hardly"  # „Kaum erreichbar“


class GoalIssue(StrEnum):
    """Why a set goal has no word yet; the card shows the plan's to-dos or the sentence."""

    NO_PROJECTION = "no_projection"  # phase 9's projection has to-dos (`todos`)
    DUE_PASSED = "due_passed"  # the due month is the current month or before it
    BEYOND_PROJECTION = "beyond_projection"  # the due month lies after the last month projected
    USED_UP = "used_up"  # the reserve runs out before the due month (`used_up`)
    NO_NEED = "no_need"  # a reserve goal while the monthly need is 0


class GoalBudget(Schema):
    """One category of a spending scenario: its measured mean and its budget (D272's class)."""

    code: AllocationCategory
    budget_class: BudgetClass
    mean: NonNegativeMoney
    budget: NonNegativeMoney


class GoalScenario(Schema):
    """A spending scenario (§7.9 (d) 3). `cut` is what it takes off the spending each month
    (the Σ of its codes' cuts), `rest` what it still misses a month when its codes cannot give
    the cut, `saving` „Heute im Monat“ + `cut`. `spending_changes` are what „Als Budget
    übernehmen“ stores (D263 planned amounts from next month to the due month); `closes` is
    true when the projection with them reaches the target by the due month."""

    kind: GoalSpending
    closes: bool
    cut: NonNegativeMoney
    rest: NonNegativeMoney
    saving: Money
    budgets: list[GoalBudget] = Field(default_factory=list)
    spending_changes: list[SpendingChange] = Field(default_factory=list)


class GoalMoreSaving(Schema):
    """Route 2 „Mehr anlegen“ (D302): the smallest whole-euro `extra` a month from next month
    to the due month that reaches the target on the walk; `noetig` = Heute + extra, None when
    the extra is above the mean income. It never decides the word."""

    extra: NonNegativeMoney
    noetig: Money | None


class GoalRouteReason(StrEnum):
    """Why route 3 has no count (§7.11 „Route 3 as decided for the build“ 5, 6; D310
    amended). `not_built` went with route 3's build."""

    GENERIC_MODE = "generic_mode"  # never offered in `generic` mode (D289)
    NOTHING_INVESTED = "nothing_invested"  # no fund or coin held and no split
    SERIES_MISSING = "series_missing"  # the funds series not downloaded, or its download failed
    HISTORY_SHORT = "history_short"  # the series left cover under half, or give no window
    OUT_OF_RANGE = "out_of_range"  # no yearly rate in −50 % … +100 % reaches the target


class GoalVerdict(StrEnum):
    """The needed return against the windows (§7.9 (e) 2)."""

    WITHIN_HISTORY = "within_history"  # „im historischen Rahmen“: at or below the median
    GOOD_TIMES = "good_times"  # „nur in guten Zeiten“: at or below the best
    NEVER = "never"  # „nie über so lange Zeit erreicht“


class GoalLeftOutKind(StrEnum):
    FUNDS = "funds"
    COIN = "coin"


class GoalLeftOutReason(StrEnum):
    TOO_SHORT = "too_short"  # a history shorter than the goal's window („zu kurze Geschichte“)
    NO_SERIES = "no_series"  # a coin with no reference series
    NO_COIN = "no_coin"  # a split with crypto while no coin is held: no coin is assumed


# A yearly return to 0.1 %, as a fraction (0.147 = 14.7 %); signed, and above 100 % for a
# coin's best window.
GoalReturn = Annotated[
    Decimal,
    Field(max_digits=9, decimal_places=3),
    _STRING_ONLY,
    _string_schema(3, signed=True),
]
# A share of the invested value, percent with one place.
GoalShare = Annotated[
    Decimal, Field(ge=0, le=100, max_digits=4, decimal_places=1), _STRING_ONLY, _string_schema(1)
]


class GoalLeftOut(Schema):
    """What the mix left out and named, with its share of the invested value (or of the
    saved split while nothing is held); its weight went to the rest pro rata."""

    kind: GoalLeftOutKind
    symbol: str | None = None  # the coin; None for crypto in a split with no coin held
    share: GoalShare
    reason: GoalLeftOutReason


class GoalReferenceSource(Schema):
    """A series the mix rests on, named in the answer (§7.11 „Route 3 …“ 7)."""

    key: str  # "funds", or the coin's symbol
    source: str
    url: str
    licence: str
    attribution: str
    read_on: date | None = None  # the download's day, or the day the archive was read
    own_calculation: str = "eigene Berechnung"  # converted to euros by the app
    first_year: int
    last_year: int


class GoalMoreReturn(Schema):
    """Route 3 „Mehr Rendite“ (§7.11 (c) 3, D303, D310 amended): the one yearly return for
    funds and crypto together that reaches the target by the due month (`needed_return`,
    smallest to 0.1 % between −50 % and +100 %), against every window of `window_years` in the
    mix's history: `reached_windows` of `windows` reached it, their `median` and `best`
    annualised (each floored to 0.1 %), and the `verdict`. `thin` (under 5 windows) is shown
    but never decides the word. Without a count `available` is false and `reason` says why;
    `out_of_range` still shows the windows."""

    available: bool = False
    reason: GoalRouteReason | None = None
    needed_return: GoalReturn | None = None
    window_years: int | None = None
    windows: int | None = None
    reached_windows: int | None = None
    median: GoalReturn | None = None
    best: GoalReturn | None = None
    verdict: GoalVerdict | None = None
    thin: bool = False
    left_out: list[GoalLeftOut] = Field(default_factory=list)
    # §7.11 (j), D312: what the mix weighs, the part held today („Heute angelegt“) and the
    # money still to come at the split („Kommt noch dazu, nach eurer Aufteilung“), in percent
    # to 0.1; None without a count
    mix_today_share: GoalShare | None = None
    mix_future_share: GoalShare | None = None
    sources: list[GoalReferenceSource] = Field(default_factory=list)


class GoalBalanced(Schema):
    """„Ausgewogener Weg“ (D302), shown first whenever the walk without change misses: half
    of the extra cut from the wants by their means, each want at most to half its mean or, with
    the household's wants target, to that share of the income; `saving_extra` the smallest
    whole euro a month that with these cuts reaches the target (None above the mean income);
    `moved_to_saving` what the limit left open and the saving takes on."""

    budgets: list[GoalBudget] = Field(default_factory=list)  # the want codes cut
    cut: NonNegativeMoney
    saving_extra: NonNegativeMoney | None
    moved_to_saving: NonNegativeMoney
    saving: Money | None  # Heute + saving_extra
    closes: bool
    spending_changes: list[SpendingChange] = Field(default_factory=list)
    # The yearly return walked for funds and crypto: route 3's median when it decides the word
    # (D302: „the invested mix at its median-window return“), None for the typed rates.
    return_rate: GoalReturn | None = None
    # The first month the walk with these cuts and `saving_extra` reaches the target, up to the
    # due month (mockup 19f „am Ziel“); None while it does not close.
    reached: YearMonth | None = None


class GoalAssumption(Schema):
    """An assumption the walk makes that the household can replace (D301 (a) 5): a property
    without a default scenario is kept at today's value."""

    text: str
    action: str
    href: str


class GoalSale(Schema):
    """A property walked to its own sale year (D301 (a) 4): the year and phase 4's net
    proceeds, which the walk puts on the reserve row in its December (mockup 19f „Verkauf“)."""

    year: int
    net_proceeds: Money


class GoalPoint(Schema):
    """One point of the goal's chart (mockup 19f „So wie jetzt“): the goal's value at a month's
    end on the walk without change, and on the balanced route's walk while it closes. The
    points are the first walked month, each December before the due month, and the due month."""

    month: YearMonth
    as_is: Money
    balanced: Money | None = None


class GoalUnreachable(Schema):
    """§7.9 (d) 4: what the household reaches instead. `later` is the month the goal is
    reached without change (None beyond the projection); `reaches_by_due` the value at the due
    month without change."""

    later: YearMonth | None
    reaches_by_due: Money


# --- The path (§7.11 (i), D311): a realistic path, adopted and tracked ----------------------


class GoalPathCeiling(Schema):
    """Item 1: what the household can save at most a month: Anlegbar's free cash with the
    wants cut to the wants kept, less the buffer kept from it as phase 9 keeps it from a free
    month. Its rows add up: ceiling = income + rental cash (when positive) − need − wants kept
    − other spending − buffer."""

    income: Money
    rental_cash: NonNegativeMoney  # the rental cash when positive, else 0
    need: Money  # the reserve's monthly need, needs only (§7.11 (g)), with the flat's shortfall
    wants_kept: NonNegativeMoney
    # The regular spending outside the need and the wants net (the want class's net mean, a
    # refund netted as the Budget's Wünsche nets it), signed: an uncategorised line raises it.
    other_spending: Money
    suggested: bool  # no wants target saved: the wants kept are the app's suggestion
    buffer: NonNegativeMoney
    ceiling: Money


class GoalPathWhy(Schema):
    """Item 4, when no path holds: with the whole room used at the typed rates, the value at
    the due month and the month the goal is then reached (None within the walk's 40 years)."""

    due_value: Money
    reached: YearMonth | None = None


class GoalPathToday(Schema):
    """„Heute angelegt“ (item 3): the mean invested a month over the closed window (the
    purchases rules/investment_split.py counts), the savings plans detected, the mean saved and
    what is saved but not invested. In `generic` mode no plan is listed (no kind, no payee)."""

    invested: NonNegativeMoney
    saved: Money
    saved_not_invested: NonNegativeMoney
    plans: list[SavingsPlan] = Field(default_factory=list)


class GoalPathAdopted(Schema):
    """The path the household adopted (the default scenario's `goal_path` lever), as stored,
    and whether what it was computed from has moved since („Plan prüfen“)."""

    adopted_on: date
    state: GoalPathState
    monthly: NonNegativeMoney
    extra: NonNegativeMoney
    rate: GoalReturn | None = None
    return_needed: GoalReturn | None = None
    windows: int | None = None
    reached_windows: int | None = None
    wants_budget_total: NonNegativeMoney
    moved: bool


class GoalPath(Schema):
    """The goal's path (D311): step 1 at the typed rates within the room, else step 2 the return
    needed with the whole room against the invested mix's history, else why. Every figure by
    code; the app proposes no goal, no rate and no product (D247, D289)."""

    state: GoalPathState
    ceiling: GoalPathCeiling
    anlegbar: Money  # the walk's own „Anlegbar“ a month
    room: NonNegativeMoney  # the ceiling less Anlegbar, at least 0
    extra: NonNegativeMoney | None = None  # the path's extra a month; None without a path
    monthly: Money | None = None  # Anlegbar + extra
    typed_rate: GoalReturn | None = None  # the typed rates weighted by the split
    rate: GoalReturn | None = None  # the rate the path counts on; None without a path
    return_needed: GoalReturn | None = None  # step 2
    window_years: int | None = None
    windows: int | None = None
    reached_windows: int | None = None
    short_history: bool | None = None  # under 5 windows: judged, and labelled
    years: list[int] | None = None  # the mix's first and last year
    # D312: step 2's mix, the part held today and the money still to come, in percent to 0.1
    mix_today_share: GoalShare | None = None
    mix_future_share: GoalShare | None = None
    wants_budget: list[GoalBudget] = Field(default_factory=list)  # each want cut
    wants_budget_total: NonNegativeMoney | None = None
    spending_changes: list[SpendingChange] = Field(default_factory=list)  # the cut, adopted
    adoptable: bool
    why: GoalPathWhy | None = None
    today: GoalPathToday | None = None  # „Heute angelegt“, filled by the service
    adopted: GoalPathAdopted | None = None  # the stored path, when one is adopted


class HouseholdGoal(Schema):
    """`GET /household-goal`: the card on Übersicht and the Ziel tab read the same answer."""

    word: GoalWord | None  # None while `issue` says why
    goal: Goal | None = None
    choice: GoalChoice | None = None
    issue: GoalIssue | None = None
    target: Money | None = None  # euros; a reserve goal's months × the monthly need
    value_today: Money | None = None
    percent: GoalPercent | None = None
    months_left: int | None = None
    heute: Money | None = None  # „Heute im Monat“, the mean saved over the closed window
    noetig: Money | None = None  # „Nötig im Monat“
    projected: Money | None = None  # the value at the due month without change
    reached: YearMonth | None = None  # without change; None when not within the projection
    reached_with_budget: YearMonth | None = None  # with the adopted budget
    used_up: YearMonth | None = None
    # target − projected when the walk without change misses (mockup 19f „fehlen“), else None
    short_at_due: NonNegativeMoney | None = None
    sales: list[GoalSale] = Field(default_factory=list)  # each property sold in the walk
    path: list[GoalPoint] = Field(default_factory=list)  # up to the due month
    scenarios: list[GoalScenario] = Field(default_factory=list)  # route 4
    more_saving: GoalMoreSaving | None = None  # route 2
    more_return: GoalMoreReturn | None = None  # route 3
    balanced: GoalBalanced | None = None  # „Ausgewogener Weg“, while route 1 misses
    unreachable: GoalUnreachable | None = None
    goal_path: GoalPath | None = None  # §7.11 (i), D311
    assumptions: list[GoalAssumption] = Field(default_factory=list)
    todos: list[HealthTodo] = Field(default_factory=list)


class ReferenceRefreshQueued(Schema):
    """POST /household-goal/reference-returns/refresh, 202 (phase 10 §7.11 (c) 3, point 8):
    the job that downloads route 3's funds series again for the household; `queued` false
    when one already waits or runs, its id then that job's."""

    job_id: int
    queued: bool


# --- „Für euer Ziel“ (§7.9 (e), „(e) as decided for the build“ 3; D298 (5)–(7)) -------------


class GoalInvestment(StrEnum):
    """The return the household follows (`goal_choice.investment`, „Diesem Weg folgen“)."""

    MORE_RETURN = "more_return"  # route 3 „Mehr Rendite“, its needed return
    BALANCED = "balanced"  # „Ausgewogener Weg“, at the typed rates since D311
    PATH = "path"  # the path adopted (§7.11 (i), D311), its stored rate


class GoalTrackingReason(StrEnum):
    """Why the card is not shown (the first three) or shows no word."""

    GENERIC_MODE = "generic_mode"  # `advice_mode = generic`: nothing shown or computed
    NO_GOAL = "no_goal"
    NOT_FOLLOWING = "not_following"  # no return followed
    ROUTE_GONE = "route_gone"  # the route followed has no return today
    GOAL_ISSUE = "goal_issue"  # the goal has an issue (no projection, due passed, …)
    NO_PLAN = "no_plan"  # the walk of the route followed ends before a year's pace
    UNPRICED = "unpriced"  # a payment's euros are unknown
    UNVALUED = "unvalued"  # a position then or today has no value


class GoalTrackingWord(StrEnum):
    ON_PATH = "on_path"  # „auf dem Pfad“
    BEHIND = "behind"  # „dahinter“


class GoalTrackingShort(StrEnum):
    PAID = "paid"  # the money put in is below the plan's by now
    VALUE = "value"  # the value change is below the change the return implies by now


# A cumulative change in percent of the value at the day plus the money in, two places, signed;
# never annualised (§7.11 (i) item 6).
GoalChangePercent = Annotated[
    Decimal, Field(max_digits=9, decimal_places=2), _STRING_ONLY, _string_schema(2, signed=True)
]


class GoalPathRow(Schema):
    """One closed month-end since the path was adopted (§7.11 (i) item 6, 12): what was invested
    (with the money waiting to be bought) against the path's monthly amount, the wants against
    its budget, the net worth against the path's walk, and the value change against the change
    its rate implies; each gap what is short, None when nothing is."""

    month_end: date
    word: GoalTrackingWord
    planned: NonNegativeMoney
    invested: Money
    waiting: NonNegativeMoney | None = None  # None without a saved split
    invested_gap: NonNegativeMoney | None = None
    wants: Money
    wants_budget: NonNegativeMoney
    wants_gap: NonNegativeMoney | None = None
    net_worth: Money
    path_net_worth: Money | None = None
    net_worth_gap: NonNegativeMoney | None = None
    value_change: Money
    implied_change: Money
    actual_percent: GoalChangePercent | None = None
    needed_percent: GoalChangePercent | None = None
    value_behind: bool


class GoalPathPoint(Schema):
    """The chart on Anlagen: the change needed and the change made, cumulative, per row."""

    month_end: date
    needed: GoalChangePercent | None = None
    actual: GoalChangePercent | None = None
    behind: bool


class GoalTracking(Schema):
    """`GET /household-goal/tracking`: „Für euer Ziel“ on Anlagen › Übersicht. Since the day
    the household chose to follow a return: the money put into its funds and crypto accounts
    (a depot counted in the reserve left out, D304) against what the walk of that route plans
    to invest by now, and the value change of those accounts against the change the return
    implies by now; one word by code. Every figure computed on read, never stored."""

    shown: bool
    reason: GoalTrackingReason | None = None
    route: GoalInvestment | None = None
    chosen_on: date | None = None
    days: int | None = None  # since the day chosen
    rate: GoalReturn | None = None  # the return followed, today's answer of its route
    planned_per_year: NonNegativeMoney | None = None  # the walk's pace, the next 12 months
    planned_by_now: NonNegativeMoney | None = None  # planned per year × days ÷ 365
    paid_in: Money | None = None  # net: in − out
    value_then: Money | None = None  # at the end of the day chosen
    value_now: Money | None = None
    value_change: Money | None = None  # value now − value then − paid in
    implied_change: Money | None = None
    yearly_return: ReturnRate | None = None  # money-weighted, only after 365 days
    no_rate: NoRateReason | None = None
    word: GoalTrackingWord | None = None
    short: list[GoalTrackingShort] = Field(default_factory=list)
    reserve_depots: int = 0  # depots counted in the reserve, left out
    # §7.11 (i) item 6: for an adopted path, one row and one point per closed month-end
    rows: list[GoalPathRow] = Field(default_factory=list)
    points: list[GoalPathPoint] = Field(default_factory=list)


# --- The property scenario (§7.9 (e), „(e) as decided for the build“ 1, 2; D289, D298) -------


class GoalPropertiesReason(StrEnum):
    """Why no property card is shown."""

    GENERIC_MODE = "generic_mode"  # `advice_mode = generic`: nothing shown or computed (D289)
    NO_GOAL = "no_goal"
    NOT_NET_WORTH = "not_net_worth"  # a sum saved or the reserve counts no property
    GOAL_ISSUE = "goal_issue"  # no projection, due passed, beyond it, the reserve used up


class GoalPropertyGrowth(Schema):
    """One property's card on Anlagen › Immobilie: the yearly value growth this property alone
    would need for the goal to be reached at the due month at today's saving (the typed fund
    and crypto rates kept; smallest to 0.1 % between −50 % and +100 %), beside the German house
    price index over every window of `window_years` (Q4 to Q4 from 2005; prices only, no rent
    and no costs): `reached_windows` of `windows` reached it, their `median` and `best`
    annualised (each floored to 0.1 %), and the `verdict` as route 3 gives it. Under 5 windows
    `thin`. `available` false with `reason` out_of_range (no growth in range reaches it, the
    windows still shown) or history_short (no window: the growth still shown)."""

    property_id: UUID
    available: bool = False
    reason: GoalRouteReason | None = None
    typed_growth: ReturnRate  # the default scenario's value growth
    needed_growth: GoalReturn | None = None
    sale_year: int
    window_years: int
    windows: int
    reached_windows: int | None = None
    median: GoalReturn | None = None
    best: GoalReturn | None = None
    verdict: GoalVerdict | None = None
    thin: bool = False
    source: GoalReferenceSource | None = None  # None without a window


class GoalProperties(Schema):
    """`GET /household-goal/properties`: one card per property the goal's walk values at the
    due month with its default scenario (none for a property kept without one, none for one
    sold before the due month). Computed on read, never stored; own use only."""

    shown: bool
    reason: GoalPropertiesReason | None = None
    properties: list[GoalPropertyGrowth] = Field(default_factory=list)
