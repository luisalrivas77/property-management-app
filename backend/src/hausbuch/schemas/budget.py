"""The Budget: needs, wants and saving against the household's own targets, and the month card
of Konten › Umsätze (docs/phases/phase-10.md D269, D272–D276, §4, §6; no migration).

Phase 10 step 0 writes the shapes; step 1 fills them (rules/budget/, services/budget.py,
rules/account_month.py). The Budget is a partition of phase 9's household month over the same
window (D273): Notwendiges + Wünsche + Ohne Einordnung + Sparen = E, the window's income, so
Sparen ÷ E is phase 9's Sparquote; the one-offs („Einmalig im Zeitraum“) stand beside the
partition, not in it, and Sparen is the income less the regular spending (§7.12 (c), D308,
amended 25 Nov 2026; until then the one-offs were a line of the partition). Every figure is
computed on read and never stored; only the targets and the class overrides are stored, in
the default household scenario's levers (D274). Amounts are exact sums to the cent or monthly
means apportioned by largest remainder; shares are of E, four places half-up, None when E ≤ 0.
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import Field, model_validator

from .allocation import AllocationCategory
from .common import _STRING_ONLY, Money, NonNegativeMoney, Schema, _string_schema
from .health import HealthTodo, Window, YearMonth
from .household import BudgetClass, HouseholdGroup
from .household import SavingsRate as Ratio  # signed, four places

# A target in percent of the income (D274): 0 to 100, one place.
BudgetPercent = Annotated[
    Decimal,
    Field(ge=0, le=100, max_digits=4, decimal_places=1),
    _STRING_ONLY,
    _string_schema(1),
]
# A gap in percentage points (D274's sentence): signed, one place.
Points = Annotated[
    Decimal,
    Field(max_digits=7, decimal_places=1),
    _STRING_ONLY,
    _string_schema(1, signed=True),
]


# --- The levers (D274): stored in household_scenario.levers --------------------------------


class BudgetTargets(Schema):
    """The household's own targets, each a percent of the income with one place, None while
    not set (no mark then). Needs, wants and the housing share are maxima, saving a minimum.
    No default and no proposal: a reference's one tap only fills the form (D275)."""

    needs_max: BudgetPercent | None = None
    wants_max: BudgetPercent | None = None
    saving_min: BudgetPercent | None = None
    housing_max: BudgetPercent | None = None


# --- The Budget (D273–D276, §6) -----------------------------------------------------------


class BudgetLineKey(StrEnum):
    """The lines of the partition, in this order on screen (D272, D273)."""

    NEED = "need"  # Notwendiges
    WANT = "want"  # Wünsche
    # „Einmalig im Zeitraum“: served beside the partition (BudgetView.one_off), never in
    # `lines` (D308, 25 Nov 2026)
    ONE_OFF = "one_off"
    UNCATEGORIZED = "uncategorized"  # Ohne Einordnung, shown only when not zero
    SAVING = "saving"  # Sparen = E − A


class BudgetMarkColour(StrEnum):
    """D274: green when the target is met, amber when it is missed; never red."""

    GREEN = "green"
    AMBER = "amber"


class BudgetMark(Schema):
    """A line's or the housing share's mark against its target, with the sentence that says by
    how many points and euros a month it meets or misses it (D274). Computed on read."""

    colour: BudgetMarkColour
    target: BudgetPercent
    gap_points: Points  # share − target, in points; signed
    gap_monthly: Money  # monthly amount − the target's amount of the monthly income; signed
    # the target's amount of the monthly income, to the cent (Haushalt's Budget line, §7.6):
    # „Notwendiges 1.900 von 2.000 €“; gap_monthly = monthly − at_target
    at_target: Money
    text: str


class BudgetCode(Schema):
    """One code inside a line or the housing share: its window mean (largest remainder within
    its line, D273), the class shown (the override, else the taxonomy's) and the default."""

    category: AllocationCategory
    group: HouseholdGroup
    budget_class: BudgetClass | None = None  # None for the one-off line and uncategorized
    default_class: BudgetClass | None = None
    overridden: bool = False
    monthly: Money
    total: Money  # the window's sum


class BudgetLine(Schema):
    key: BudgetLineKey
    monthly: Money  # the window's monthly mean, apportioned so the lines add up to E's mean
    total: Money  # the window's exact sum
    share: Ratio | None = None  # total ÷ ΣE, None when ΣE ≤ 0
    codes: list[BudgetCode] = Field(default_factory=list)  # empty for saving
    target: BudgetPercent | None = None  # need, want, saving only
    mark: BudgetMark | None = None

    @model_validator(mode="after")
    def _codes(self) -> Self:
        if self.key is BudgetLineKey.SAVING:
            if self.codes:
                raise ValueError("saving has no codes")
        else:
            if sum((c.monthly for c in self.codes), Decimal("0.00")) != self.monthly:
                raise ValueError(f"{self.key}: the codes' means must add up to the line's")
            if sum((c.total for c in self.codes), Decimal("0.00")) != self.total:
                raise ValueError(f"{self.key}: the codes' sums must add up to the line's")
        if self.mark is not None and self.target != self.mark.target:
            raise ValueError("a mark is against the line's target")
        return self


class BudgetHousing(Schema):
    """D276: the window's spending in the Wohnen group ÷ E; an override does not move it."""

    monthly: Money
    total: Money
    share: Ratio | None = None
    codes: list[BudgetCode] = Field(default_factory=list)
    target: BudgetPercent | None = None
    mark: BudgetMark | None = None


class BudgetIncome(Schema):
    """E over the window (D232, D273): its monthly mean, of which the flat's result."""

    monthly: Money  # mean of E to the cent, half-up: what the lines add up to
    total: Money  # ΣE
    rental_monthly: Money  # the flat's result's mean, half-up („inkl. Wohnung −120 €“)
    private_monthly: Money  # monthly − rental_monthly

    @model_validator(mode="after")
    def _parts(self) -> Self:
        if self.private_monthly + self.rental_monthly != self.monthly:
            raise ValueError("private + rental must be the monthly income")
        return self


class BudgetMonth(Schema):
    """One window month's partition, exact sums (D273 per month): the lines add up to E, the
    one-offs beside them (D308)."""

    month: YearMonth
    income: Money  # E
    need: Money
    want: Money
    one_off: Money  # beside the partition: the month's one-off spending
    uncategorized: Money
    saving: Money  # E − the regular spending (D308)

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.need + self.want + self.uncategorized + self.saving != self.income:
            raise ValueError(f"{self.month}: the lines must add up to the income")
        return self


class BudgetReferenceKind(StrEnum):
    SPLIT = "split"  # a split of the income into needs, wants and saving
    HOUSING = "housing"  # a threshold for the housing share


class BudgetThreshold(Schema):
    """One band of a housing reference: from `above` (exclusive) up to `up_to` (inclusive),
    in percent of that source's own denominator; None is open."""

    above: BudgetPercent | None = None
    up_to: BudgetPercent | None = None
    label: str


class BudgetReference(Schema):
    """One published reference point of rules/budget/references.yaml (D275, D276, §3): never a
    default, never a target until the household saves one."""

    key: str = Field(pattern=r"^[a-z0-9_]+$")
    kind: BudgetReferenceKind
    label: str
    source: str
    read_on: date
    measures: str  # what the source's figure is a share of
    does_not_measure: str  # why it is not the app's figure
    needs: BudgetPercent | None = None  # a split's three numbers
    wants: BudgetPercent | None = None
    saving: BudgetPercent | None = None
    thresholds: list[BudgetThreshold] = Field(default_factory=list)  # a housing reference's

    @model_validator(mode="after")
    def _shape(self) -> Self:
        parts = (self.needs, self.wants, self.saving)
        if self.kind is BudgetReferenceKind.SPLIT:
            if any(p is None for p in parts) or self.thresholds:
                raise ValueError(f"{self.key}: a split has its three numbers and no bands")
            if sum(parts, Decimal("0")) != 100:  # type: ignore[arg-type]
                raise ValueError(f"{self.key}: a split adds up to 100")
        elif any(p is not None for p in parts) or not self.thresholds:
            raise ValueError(f"{self.key}: a housing reference has bands only")
        return self


class BudgetView(Schema):
    """GET /household-budget?scenario_id= (§6). Below three closed months, `income`, `months`
    and `lines` are empty and a to-do says why (D231); with ΣE ≤ 0 the lines stand without
    shares or marks and `no_shares` says why (D273)."""

    as_of: date
    scenario_id: UUID | None = None  # the scenario whose targets and overrides were read
    window: Window
    months: list[BudgetMonth] = Field(default_factory=list)
    income: BudgetIncome | None = None
    lines: list[BudgetLine] = Field(default_factory=list)
    # „Einmalig im Zeitraum“ (D308): the window's one-off spending beside the partition, its
    # codes inside; None when the window holds none
    one_off: BudgetLine | None = None
    housing: BudgetHousing | None = None
    targets: BudgetTargets = Field(default_factory=BudgetTargets)
    no_shares: str | None = None
    references: list[BudgetReference] = Field(default_factory=list)
    todos: list[HealthTodo] = Field(default_factory=list)

    @model_validator(mode="after")
    def _partition(self) -> Self:
        if [m.month for m in self.months] != (self.window.months if self.months else []):
            raise ValueError("the months are the window's")
        keys = [line.key for line in self.lines]
        if keys != sorted(keys, key=list(BudgetLineKey).index) or len(keys) != len(set(keys)):
            raise ValueError("each line once, in BudgetLineKey's order")
        if BudgetLineKey.ONE_OFF in keys:
            raise ValueError("the one-offs stand beside the partition (D308)")
        if self.one_off is not None and self.one_off.key is not BudgetLineKey.ONE_OFF:
            raise ValueError("the one-off figure is the one-off line")
        if self.income is None:
            if self.lines or self.months or self.housing is not None or self.one_off is not None:
                raise ValueError("no figure without an income")
            return self
        if sum((line.monthly for line in self.lines), Decimal("0.00")) != self.income.monthly:
            raise ValueError("the lines' means must add up to the income's (D273)")
        if sum((line.total for line in self.lines), Decimal("0.00")) != self.income.total:
            raise ValueError("the lines must add up to the income (D273)")
        return self


# --- Konten › Umsätze: the month card (D269) ----------------------------------------------


class AccountMonth(Schema):
    """GET /accounts/{id}/month?month=YYYY-MM (D269). The balances are the net worth's
    (services.net_worth): opening balance plus the lines through the day its statements are
    reconciled to, `as_of` that day; None for a month ending before the account's opening
    balance, and for an account kind without a euro balance of lines (an exchange, a wallet).
    Eingänge and Ausgänge leave out the transfers between own accounts, which have their own
    figures; a line not yet confirmed counts by its sign and is counted in `open_lines`.
    Amounts are positive sums of each direction. A duplicate merged away counts nowhere."""

    account_id: UUID
    month: YearMonth
    running: bool  # the month of today: the closing balance is today's
    opening_balance: Money | None = None  # at the end of the month before
    opening_as_of: date | None = None
    closing_balance: Money | None = None
    closing_as_of: date | None = None
    incoming: NonNegativeMoney = Decimal("0.00")
    outgoing: NonNegativeMoney = Decimal("0.00")
    transfers_in: NonNegativeMoney = Decimal("0.00")
    transfers_out: NonNegativeMoney = Decimal("0.00")
    lines: int = Field(default=0, ge=0)
    open_lines: int = Field(default=0, ge=0)  # lines whose status is not „Gebucht“

    @model_validator(mode="after")
    def _balances(self) -> Self:
        if (self.closing_balance is None) != (self.closing_as_of is None):
            raise ValueError("a closing balance has its day")
        if (self.opening_balance is None) != (self.opening_as_of is None):
            raise ValueError("an opening balance has its day")
        if self.open_lines > self.lines:
            raise ValueError("more open lines than lines")
        return self
