"""The household's key figures and their marks (docs/phases/phase-9.md §4, §5, §11, §12;
migration 0048).

Phase 9 step 0 writes the shapes; step 1 fills them (rules/health/, services/health.py).
Every figure is computed on read from confirmed allocations and stored runs (D230); only a
closed month's figures are stored once, in `health_snapshot` (D242). Amounts are exact sums
to the cent; ratios are rounded to four places half-up, multiples to two, reserve months to
one (§4.2, D235, D237, D239). A figure that cannot be computed is None, never 0, and a
`HealthTodo` says what to do (D250 (8)).
"""

import calendar
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import Field, model_validator

from .common import _STRING_ONLY, Money, Schema, _string_schema
from .enums import AccountKind
from .household import NetWorthClass
from .household import SavingsRate as Ratio  # signed, four places (numeric(9,4))

# A calendar month, "2027-05".
YearMonth = Annotated[str, Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]

# A share of the gross assets (D240): 0 to 1, four places.
Share = Annotated[
    Decimal, Field(ge=0, le=1, max_digits=5, decimal_places=4), _STRING_ONLY, _string_schema(4)
]
# Schulden zu Jahreseinkommen (D239 (4)): a multiple with two places.
Multiple = Annotated[
    Decimal, Field(ge=0, max_digits=9, decimal_places=2), _STRING_ONLY, _string_schema(2)
]
# Reserve months (D237): one place.
ReserveMonths = Annotated[
    Decimal, Field(ge=0, max_digits=7, decimal_places=1), _STRING_ONLY, _string_schema(1)
]
# The reserve target in months of the need (D238, §7.3): 0 to 24 in steps of 0.5.
TargetMonths = Annotated[
    Decimal,
    Field(ge=0, le=24, max_digits=3, decimal_places=1, multiple_of=Decimal("0.5")),
    _STRING_ONLY,
    _string_schema(1),
]
# A limit of a mark (D250): a ratio or, for the reserve, a share of the target; signed, since
# a member may type a negative limit for the savings rate.
LimitValue = Annotated[
    Decimal,
    Field(ge=-100, le=100, max_digits=8, decimal_places=4),
    _STRING_ONLY,
    _string_schema(4, signed=True),
]

BAND_ORDER_INVALID = "band.limits_out_of_order"
BAND_ON_SHARE = "band.share_refused"


def month_of(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def is_month_end(day: date) -> bool:
    return day.day == calendar.monthrange(day.year, day.month)[1]


def _consecutive(months: list[str]) -> bool:
    def index(month: str) -> int:
        year, number = month.split("-")
        return int(year) * 12 + int(number) - 1

    return all(index(b) - index(a) == 1 for a, b in zip(months, months[1:], strict=False))


# --- To-dos (D250 (8): the shape of NetWorthTodo) ---------------------------------------


class HealthTodoCode(StrEnum):
    MONTH_END_MISSING = "month_end_missing"  # „Monatsabschluss festhalten“
    ACCOUNT_NOT_COVERED = "account_not_covered"  # a statement or fetch missing for the month
    LINES_WAITING = "lines_waiting"  # lines of the month wait in Zu prüfen
    TRANSFER_OPEN = "transfer_open"  # a transfer leg without its counterpart
    TOO_FEW_MONTHS = "too_few_months"  # fewer than three closed months (D231)
    BALANCE_STALE = "balance_stale"  # a counted reserve account older than 7 days (§5.2)
    BANDS_UNCONFIRMED = "bands_unconfirmed"  # „Ampel-Grenzen festlegen“ (D250 (3))
    NO_DEFAULT_SCENARIO = "no_default_scenario"  # „Szenario für die Wohnung anlegen“
    SCENARIO_CHECK = "scenario_check"  # „Szenario prüfen“ (§7.3)
    RATE_MISSING = "rate_missing"  # „Annahme eintragen“ (§7.5 (1))
    INCOME_CHANGE_LINK = "income_change_link"  # an income change beside Eigentümer (Q-O141)


class HealthTodo(Schema):
    """A gap as a plain to-do with the button or page that closes it (principle 7)."""

    code: HealthTodoCode
    text: str
    action: str  # the button's or link's words
    href: str


# --- The household month (§4, D230–D236) ------------------------------------------------


class RentalMonth(Schema):
    """The confirmed, non-zero allocations of one property's rental accounts booked in the
    month, summed by phase 4's sets (§4.1). Signed: a refund to a tenant lowers its block."""

    month: YearMonth
    property_id: UUID
    rent: Money = Decimal("0.00")
    other_income: Money = Decimal("0.00")
    umlagen_in: Money = Decimal("0.00")
    umlagen_out: Money = Decimal("0.00")
    running: Money = Decimal("0.00")
    interest: Money = Decimal("0.00")
    principal: Money = Decimal("0.00")
    invested: Money = Decimal("0.00")  # the INVESTED set (purchase price, acquisition costs)
    loan_disbursement: Money = Decimal("0.00")  # the loan's payout (D234)
    sources: list[UUID] = Field(default_factory=list)

    @property
    def result(self) -> Decimal:
        """R = rent + other income + Umlagen in − Umlagen out − running − interest."""
        return (
            self.rent
            + self.other_income
            + self.umlagen_in
            - self.umlagen_out
            - self.running
            - self.interest
        )


class HouseholdMonth(Schema):
    """§4.2. The identities between the fields are checked here; how each block is summed is
    rules/health/month (step 1). `sources`: the allocation ids per field (principle 2), empty
    in a stored snapshot (§5.5)."""

    month: YearMonth
    private_income: Money
    private_spending: Money
    one_off_spending: Money
    regular_spending: Money
    rental_result: Money  # R
    rental_cash: Money  # C = R − principal
    income: Money  # E = private income + R
    spending: Money  # A = private spending
    saved: Money  # S = E − A
    principal: Money
    invested_property: Money  # D234, signed
    invested: Money
    free_cash: Money  # F = private income − regular spending + C
    savings_rate: Ratio | None = None  # S ÷ E, None when E ≤ 0
    sources: dict[str, list[UUID]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _identities(self) -> Self:
        checks = {
            "regular_spending": self.private_spending - self.one_off_spending,
            "rental_cash": self.rental_result - self.principal,
            "income": self.private_income + self.rental_result,
            "spending": self.private_spending,
            "saved": self.income - self.spending,
            "free_cash": self.private_income - self.regular_spending + self.rental_cash,
        }
        for name, want in checks.items():
            if getattr(self, name) != want:
                raise ValueError(f"{name} must be {want}")
        rate = ratio(self.saved, self.income)
        if self.savings_rate != rate:
            raise ValueError(f"savings_rate must be {rate}")
        return self


def ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    """A ratio to four places, half-up; None when the denominator is 0 or below (D235)."""
    if denominator <= 0:
        return None
    return (numerator / denominator).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


# --- Closed months and the window (D231) ------------------------------------------------


class ClosedReasonCode(StrEnum):
    ACCOUNT_NOT_COVERED = "account_not_covered"
    LINES_WAITING = "lines_waiting"
    TRANSFER_OPEN = "transfer_open"


class ClosedReason(Schema):
    code: ClosedReasonCode
    account_id: UUID | None = None  # account_not_covered
    count: int | None = Field(default=None, ge=1)  # lines_waiting, transfer_open

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if (self.code is ClosedReasonCode.ACCOUNT_NOT_COVERED) != (self.account_id is not None):
            raise ValueError("an account is named by account_not_covered only")
        if (self.code is ClosedReasonCode.ACCOUNT_NOT_COVERED) == (self.count is not None):
            raise ValueError("a count belongs to lines_waiting and transfer_open")
        return self


class ClosedMonth(Schema):
    month: YearMonth
    closed: bool
    reasons: list[ClosedReason] = Field(default_factory=list)
    todos: list[HealthTodo] = Field(default_factory=list)

    @model_validator(mode="after")
    def _closed_without_reasons(self) -> Self:
        if self.closed == bool(self.reasons):
            raise ValueError("a month is closed exactly when it has no reason")
        if len(self.todos) != len(self.reasons):
            raise ValueError("one to-do per reason")
        return self


class AccountCoverage(Schema):
    """Input of `rules/health/window.closed` (step 1): one active bank account or credit card
    of purpose private or rental, as the service read its coverage (Q-O133)."""

    account_id: UUID
    label: str
    credit_card: bool = False  # names the card's upload in the to-do
    first_day: date | None = None  # its first imported day; None: nothing imported yet
    covered_to: date | None = None  # the last day its imports or fetches reach
    idle: bool = False  # a card idle by phase 5 D122 amended (6): covered
    own_opening: bool = False  # its opening on first_day is its first statement's own


class MonthFacts(Schema):
    """Input of `rules/health/window.closed` (step 1): what decides whether a month is
    closed (D231)."""

    month: YearMonth
    accounts: list[AccountCoverage] = Field(default_factory=list)
    lines_waiting: int = Field(default=0, ge=0)  # lines booked in the month in Zu prüfen
    transfers_open: int = Field(default=0, ge=0)  # transfer legs of the month still open


class Window(Schema):
    """The unbroken run of closed months ending at the newest, cut to 12 (D231)."""

    months: list[YearMonth] = Field(default_factory=list, max_length=12)
    enough: bool

    @model_validator(mode="after")
    def _run(self) -> Self:
        if not _consecutive(self.months):
            raise ValueError("the window's months are consecutive, oldest first")
        if self.enough != (len(self.months) >= 3):
            raise ValueError("enough = at least three months")
        return self

    @property
    def annualised(self) -> bool:
        return len(self.months) < 12


# --- Key figures (§5) ---------------------------------------------------------------------


class MonthRate(Schema):
    month: YearMonth
    rate: Ratio | None = None


class SavingsRate(Schema):
    """D235, §5.1. Every ratio None when income ≤ 0."""

    saved: Money
    income: Money
    one_off_spending: Money
    principal: Money  # „davon Tilgung“
    rate: Ratio | None = None
    rate_regular: Ratio | None = None  # „ohne Einmalausgaben“
    rate_without_principal: Ratio | None = None  # „ohne Tilgung“
    months: list[MonthRate] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ratios(self) -> Self:
        want = (
            ratio(self.saved, self.income),
            ratio(self.saved + self.one_off_spending, self.income),
            ratio(self.saved - self.principal, self.income),
        )
        got = (self.rate, self.rate_regular, self.rate_without_principal)
        if got != want:
            raise ValueError(f"the rates must be {want}")
        return self


class ReserveOrigin(StrEnum):
    DEFAULT = "default"  # a private current or savings account, or a card (D237)
    ADDED = "added"  # by the lever
    REMOVED = "removed"  # by the lever: shown, not counted


class ReserveState(StrEnum):
    BELOW = "below"
    REACHED = "reached"


class ReserveAccount(Schema):
    account_id: UUID
    kind: AccountKind
    balance: Money  # a card's debt negative
    as_of: date
    origin: ReserveOrigin

    @property
    def counted(self) -> bool:
        return self.origin is not ReserveOrigin.REMOVED


class Reserve(Schema):
    """D237, D238, §5.2. Compared in euros, never in rounded months."""

    accounts: list[ReserveAccount] = Field(default_factory=list)
    amount: Money
    need: Money  # mean regular spending + max(0, −mean C), each mean to the cent
    months: ReserveMonths | None = None  # None when the need is 0
    target_months: TargetMonths
    target: Money  # target months × need
    gap: Money  # max(0, target − amount)
    state: ReserveState
    by_owner: dict[UUID, Money] = Field(default_factory=dict)
    todos: list[HealthTodo] = Field(default_factory=list)

    @model_validator(mode="after")
    def _figures(self) -> Self:
        counted = sum((a.balance for a in self.accounts if a.counted), Decimal("0.00"))
        if self.accounts and counted != self.amount:
            raise ValueError("the amount is the counted accounts' sum")
        target = (self.target_months * self.need).quantize(Decimal("0.01"), ROUND_HALF_UP)
        if self.target != target:
            raise ValueError(f"target must be {target}")
        if self.gap != max(Decimal("0.00"), self.target - self.amount):
            raise ValueError("gap = max(0, target − amount)")
        reached = self.amount >= self.target
        if (self.state is ReserveState.REACHED) != reached:
            raise ValueError("state compares the amount with the target in euros")
        if self.need == 0:
            if self.months is not None:
                raise ValueError("no months without a need")
        elif self.months != (self.amount / self.need).quantize(Decimal("0.1"), ROUND_HALF_UP):
            raise ValueError("months = amount ÷ need, one place")
        if self.by_owner and sum(self.by_owner.values()) != self.amount:
            raise ValueError("the owners' parts sum to the amount")
        return self


class EstimateSource(StrEnum):
    SCENARIO = "scenario"  # the default scenario's „Wert heute“
    PURCHASE_PRICE = "purchase_price"  # labelled „geschätzt“ all the same (D239 (1))


class PropertyLtv(Schema):
    property_id: UUID
    loan_balance: Money
    estimate: Money
    estimate_source: EstimateSource
    ltv: Ratio | None = None  # absent without a loan


class DebtFigure(StrEnum):
    LTV = "ltv"
    DEBT_RATIO = "debt_ratio"
    DEBT_SERVICE_RATIO = "debt_service_ratio"
    DEBT_TO_INCOME = "debt_to_income"


class DebtGap(Schema):
    """A debt figure whose denominator is 0 or below (§5.3)."""

    figure: DebtFigure
    reason: str


class Debt(Schema):
    """D239, §5.3. Annualised from n months when n < 12."""

    properties: list[PropertyLtv] = Field(default_factory=list)
    liabilities: Money  # positive: what is owed
    gross_assets: Money
    debt_ratio: Ratio | None = None  # Verschuldungsgrad
    interest: Money  # of the window
    principal: Money  # of the window
    debt_service_year: Money
    income_year: Money  # (Σ E + Σ interest) × 12 ÷ n
    debt_service_ratio: Ratio | None = None
    debt_to_income: Multiple | None = None
    months: int = Field(ge=1, le=12)
    gaps: list[DebtGap] = Field(default_factory=list)

    @property
    def annualised(self) -> bool:
        return self.months < 12


class NetWorthPoint(Schema):
    """One month-end of the series (D241, §5.4). A month-end without a stored run is a gap:
    no run, no figures, and the to-do „Monatsabschluss festhalten“."""

    month_end: date
    net_worth_run_id: UUID | None = None
    total: Money | None = None
    by_class: dict[NetWorthClass, Money] = Field(default_factory=dict)
    by_owner: dict[UUID, Money] = Field(default_factory=dict)
    change: Money | None = None  # from the second point on
    saved: Money | None = None  # the month's S when the month is closed
    other: Money | None = None  # change − saved
    todo: HealthTodo | None = None

    @model_validator(mode="after")
    def _point(self) -> Self:
        if not is_month_end(self.month_end):
            raise ValueError("a point is a month's last day")
        if (self.net_worth_run_id is None) != (self.total is None):
            raise ValueError("a point has a total exactly when it has a run")
        if self.net_worth_run_id is None and self.todo is None:
            raise ValueError("a gap carries its to-do")
        if self.saved is not None and self.change is not None:
            if self.other != self.change - self.saved:
                raise ValueError("other = change − saved")
        elif self.other is not None:
            raise ValueError("other needs change and saved")
        return self


# --- Marks (D250, §5.6; data in rules/health/bands.yaml) --------------------------------


class MarkedFigure(StrEnum):
    """The figures a mark may be put on. The split's shares are not among them (D240)."""

    RESERVE = "reserve"
    SAVINGS_RATE = "savings_rate"
    LTV = "ltv"
    DEBT_SERVICE_RATIO = "debt_service_ratio"
    DEBT_RATIO = "debt_ratio"
    DEBT_TO_INCOME = "debt_to_income"


SHARE_FIGURES = frozenset({"share_bank", "share_depot", "share_crypto", "share_property"})


class Direction(StrEnum):
    LOWER = "lower"  # lower is better
    HIGHER = "higher"  # higher is better


DIRECTION = {
    MarkedFigure.RESERVE: Direction.HIGHER,
    MarkedFigure.SAVINGS_RATE: Direction.HIGHER,
    MarkedFigure.LTV: Direction.LOWER,
    MarkedFigure.DEBT_SERVICE_RATIO: Direction.LOWER,
    MarkedFigure.DEBT_RATIO: Direction.LOWER,
    MarkedFigure.DEBT_TO_INCOME: Direction.LOWER,
}


class LightColour(StrEnum):
    GREEN = "green"
    AMBER = "amber"
    RED = "red"


class LimitOrigin(StrEnum):
    SOURCE = "source"  # an original whose definition matches the figure was read
    OWN_LEVER = "own_lever"  # „eigene Grenze“: every limit as read on 16 Nov 2026


def _refuse_share(value: object) -> object:
    if isinstance(value, dict) and value.get("figure") in SHARE_FIGURES:
        raise ValueError(f"{BAND_ON_SHARE}: no limit on a share of the split (D240)")
    return value


class BandLimits(Schema):
    """A figure's limits as the household confirmed them (stored in the default scenario's
    `levers.bands`, D248). The reserve's green is its own target (D238), so it takes no green
    limit; its red is a share of the target. A side without a limit gives no colour."""

    figure: MarkedFigure
    green: LimitValue | None = None
    red: LimitValue | None = None

    @model_validator(mode="before")
    @classmethod
    def _no_share(cls, data: object) -> object:
        return _refuse_share(data)

    @model_validator(mode="after")
    def _order(self) -> Self:
        if self.figure is MarkedFigure.RESERVE:
            if self.green is not None:
                raise ValueError(f"{BAND_ORDER_INVALID}: the reserve's green is its target")
            if self.red is not None and not (0 < self.red <= 1):
                raise ValueError(f"{BAND_ORDER_INVALID}: the reserve's red is a share (0, 1]")
            return self
        if self.green is None or self.red is None:
            return self
        lower = DIRECTION[self.figure] is Direction.LOWER
        if (lower and self.green > self.red) or (not lower and self.green < self.red):
            raise ValueError(f"{BAND_ORDER_INVALID}: {self.figure} needs its limits in order")
        return self


class ProposedLimit(Schema):
    value: LimitValue
    origin: LimitOrigin
    citation: str | None = None  # only with origin source

    @model_validator(mode="after")
    def _citation(self) -> Self:
        if (self.origin is LimitOrigin.SOURCE) != (self.citation is not None):
            raise ValueError("a source limit names its citation, an own lever none")
        return self


class ReferencePoint(Schema):
    source: str
    says: str
    does_not_say: str


class BandProposal(Schema):
    """One figure of bands.yaml: direction, the proposed limits with their origins, the
    reference points and the sentence codes per colour (D250 (1))."""

    figure: MarkedFigure
    direction: Direction
    green: ProposedLimit | None = None
    red: ProposedLimit | None = None
    reference_points: list[ReferencePoint] = Field(default_factory=list)
    sentences: dict[LightColour, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if DIRECTION[self.figure] is not self.direction:
            raise ValueError(f"{self.figure} is {DIRECTION[self.figure]} is better")
        BandLimits(
            figure=self.figure,
            green=self.green.value if self.green else None,
            red=self.red.value if self.red else None,
        )
        return self


class Light(Schema):
    """One figure's mark: the colour (None: unmarked), the limits used with their origins, and
    the codes of its sentences (§5.6). Computed on read, never stored."""

    figure: MarkedFigure
    colour: LightColour | None = None
    green: LimitValue | None = None
    red: LimitValue | None = None
    origin: LimitOrigin | None = None  # of the limits used; None when no limit was confirmed
    sentences: list[str] = Field(default_factory=list)


# --- GET /health, the stored month, the closing check -----------------------------------


class Health(Schema):
    """GET /health (§13): the window, the figures, the marks, the proposals not yet
    confirmed, and the to-dos."""

    as_of: date
    window: Window
    newest: HouseholdMonth | None = None
    savings_rate: SavingsRate | None = None
    reserve: Reserve | None = None
    debt: Debt | None = None
    split: dict[NetWorthClass, Share] = Field(default_factory=dict)
    free_cash: Money | None = None
    series: list[NetWorthPoint] = Field(default_factory=list)
    marks: dict[MarkedFigure, Light] = Field(default_factory=dict)
    proposals: list[BandProposal] = Field(default_factory=list)  # not yet confirmed
    todos: list[HealthTodo] = Field(default_factory=list)


class HealthSnapshotFigures(Schema):
    """`health_snapshot.figures` (§5.5): the month without sources, the window's savings rate
    and basis as of that month, the reserve, the debt figures and the split from the run, and
    the free cash. No mark (§5.6)."""

    month: HouseholdMonth
    window: Window
    savings_rate: SavingsRate | None = None
    reserve: Reserve | None = None
    debt: Debt | None = None
    split: dict[NetWorthClass, Share] = Field(default_factory=dict)
    free_cash: Money | None = None

    @model_validator(mode="after")
    def _no_sources(self) -> Self:
        if self.month.sources:
            raise ValueError("a stored month carries no sources (§5.5)")
        return self


class HealthSnapshot(Schema):
    """health_snapshot row (immutable, D242). `rule_version` is the hash of rules/health/ and
    rules/plan/; created_by None for the daily task."""

    id: UUID
    workspace_id: UUID
    month_end: date
    net_worth_run_id: UUID
    figures: HealthSnapshotFigures
    rule_version: str = Field(min_length=1)
    created_by: UUID | None = None
    created_at: datetime

    @model_validator(mode="after")
    def _month_end(self) -> Self:
        if not is_month_end(self.month_end):
            raise ValueError("month_end is a month's last day")
        if self.figures.month.month != month_of(self.month_end):
            raise ValueError("the figures are of the snapshot's month")
        return self


class HealthCheckRow(Schema):
    """One figure of one month: stored in the snapshot against recomputed from the stored
    rows today (D259 amended 17 Nov 2026). Values as decimal strings; None: not computed."""

    month: YearMonth
    figure: str
    stored: str | None = None
    recomputed: str | None = None
    equal: bool


class HealthCheckInput(Schema):
    """One input of a month against its own source: a month-end run's account and position
    lines as phase 6 already checks them (a statement balance, a depot report, an exchange's
    typed balance, a wallet's chain balance)."""

    month: YearMonth
    net_worth_run_id: UUID | None = None
    account_id: UUID | None = None
    agrees: bool
    reason: str | None = None  # why not, in plain words


class HealthCheckResult(Schema):
    """`hausbuch health-check` (D259 amended 17 Nov 2026: no spreadsheet): for consecutive
    closed months, each stored figure equals the one recomputed from the stored rows, and each
    input agrees with its own source. The repo records only the date, the months and whether
    it matched, never a household figure."""

    months: list[YearMonth] = Field(min_length=1)
    rows: list[HealthCheckRow] = Field(default_factory=list)
    inputs: list[HealthCheckInput] = Field(default_factory=list)
    match: bool

    @model_validator(mode="after")
    def _match(self) -> Self:
        if not _consecutive(self.months):
            raise ValueError("the months are consecutive")
        everything = all(r.equal for r in self.rows) and all(i.agrees for i in self.inputs)
        if self.match != everything:
            raise ValueError("match = every row equal and every input agreeing")
        return self
