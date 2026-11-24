"""The household goal (docs/phases/phase-10.md §7.9 (d), D289, D298): `GET /household-goal`.

The goal and what the household chose for it are levers of the default household scenario
(schemas.plan.Goal, GoalChoice); this is the answer computed from them by rules/goal on read.
Every figure is computed by code; the app proposes no goal, no date and no amount (D289 (1)).
"""

from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import Field

from .allocation import AllocationCategory
from .common import _STRING_ONLY, Money, NonNegativeMoney, Schema, _string_schema
from .health import HealthTodo, YearMonth
from .household import BudgetClass
from .plan import Goal, GoalChoice, GoalSpending, SpendingChange

# Today's value against the target, one place (it may pass 100 once the goal is reached).
GoalPercent = Annotated[
    Decimal, Field(ge=0, max_digits=8, decimal_places=1), _STRING_ONLY, _string_schema(1)
]


class GoalWord(StrEnum):
    """The one word Übersicht shows beside the financial-health verdict (§7.9 (d) 5)."""

    NONE = "none"  # no goal set: „Ziel festlegen“
    ON_TRACK = "on_track"  # „Auf Kurs“
    BEHIND = "behind"  # „Dahinter“
    UNREACHABLE = "unreachable"  # „So nicht erreichbar“


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


class GoalUnreachable(Schema):
    """§7.9 (d) 4: what the household reaches instead. `later` is the month the goal is
    reached without change (None beyond the projection); `reaches_by_due` the value at the due
    month without change."""

    later: YearMonth | None
    reaches_by_due: Money


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
    scenarios: list[GoalScenario] = Field(default_factory=list)
    unreachable: GoalUnreachable | None = None
    todos: list[HealthTodo] = Field(default_factory=list)
