"""Shapes of the phase 9 YAML rule fixtures (docs/phases/phase-9.md §15.1).

Same contract as fixture_models_phase7.py: loading a fixture through these models is itself a
test (tests/test_fixtures_valid.py), so a fixture that does not match the spec schemas fails
before any rule exists. Every rule of phase 9 is a stub in step 0 (NotImplementedError), so
each case a rule will replay carries `pending: "<the step that builds it>"`; the rule tests of
those steps drop the marker. Expectations are recomputed on load where the arithmetic is
small, by the rule written once more here:

- closed_month: §4.4's coverage, waiting lines and open transfers; the window of D231;
- household_month: the identities of §4.2 over the fields each case gives (the reading of
  the lines into a month is step 1's);
- savings_rate: D235's ratios, through hausbuch.schemas.health.SavingsRate;
- reserve: D237, D238, the owners' parts by rules/net_worth.split, through
  hausbuch.schemas.health.Reserve;
- debt: D239, D240;
- net_worth_series: D241's change, saved and other, the gaps, and the estimate of a past
  month-end;
- lights: D250's colours against the confirmed limits and bands.yaml's sentences; the
  refused limits through hausbuch.schemas.health.BandLimits;
- surplus: D244, through hausbuch.schemas.plan.Surplus;
- savings_plans: D245's detection and the overrides, through hausbuch.schemas.plan.SavingsPlan;
- paths and plan_levers: the month function of §7.1 and D246's rows; lever_bounds through
  hausbuch.schemas.plan.HouseholdLevers;
- net_worth_projection: §7.5's points, stretches and change rows, each point through
  hausbuch.schemas.plan.ProjectionPoint;
- spending_play and spending_comparison (step 3b, D263): §7.6's means, Δ, month function
  and rate, the refusals of §7.6 (1) on the changes as typed, §7.7's comparison and the
  scenario in force on a day;
- health_worked: every figure recomputed from the household's own inputs with the functions
  above, and compared with the per-function fixtures that quote §15.2.

Shape only (step 4 replays them): explain_facts, explain_render, explain_validator (whose
must-accept slots are checked here, the counts in tests/rules/test_phase9_fixture_coverage.py)
and instrument_names. Every interpretation step 0 had to make is in the fixture file's header,
and listed in docs/phases/phase-9.md §15.1.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

import yaml
from fixture_models import Lenient
from pydantic import Field, JsonValue, ValidationError, model_validator

from hausbuch.rules.advice import rules as advice_rules
from hausbuch.rules.health.bands import proposals
from hausbuch.rules.household.taxonomy import FALLBACK_CODES, ONE_OFF, TAXONOMY
from hausbuch.rules.net_worth import split
from hausbuch.schemas.advice import (
    STATES,
    AdviceMode,
    ExplainFacts,
    ExplainLabels,
    FigureType,
    Topic,
    ViolationCode,
    known_figure_key,
)
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.common import _STRING_ONLY, Money, NonNegativeMoney, Schema
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.health import (
    DIRECTION,
    BandLimits,
    ClosedReasonCode,
    Direction,
    EstimateSource,
    HealthTodoCode,
    LightColour,
    MarkedFigure,
    Multiple,
    Reserve,
    ReserveAccount,
    ReserveMonths,
    ReserveOrigin,
    ReserveState,
    Share,
    TargetMonths,
    YearMonth,
)
from hausbuch.schemas.household import BudgetClass, HouseholdGroup, NetWorthClass, Relevance
from hausbuch.schemas.household import SavingsRate as Ratio
from hausbuch.schemas.plan import (
    BufferPercent,
    HouseholdLevers,
    IncomeChange,
    InvestmentSplit,
    OneOff,
    OneOffSource,
    PathKey,
    PlanClass,
    PlanKey,
    PlanOverride,
    ProjectionChange,
    ProjectionPoint,
    SavingsPlan,
    Surplus,
    SurplusKey,
    SurplusRow,
    ValueChange,
)

FIXTURES = Path(__file__).parent / "fixtures" / "phase9"

# A decimal written as a string (an owner's share, a rate of a stretch).
Dec = Annotated[Decimal, _STRING_ONLY]
Pending = Literal["1", "2", "3", "4", "5", "6"]
Owners = dict[str, Dec]

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


@cache
def raw(name: str) -> Any:
    return yaml.safe_load((FIXTURES / f"{name}.yaml").read_text(encoding="utf-8"))


def cent(value: Decimal | Fraction) -> Decimal:
    if isinstance(value, Fraction):
        value = Decimal(value.numerator) / Decimal(value.denominator)
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def ratio4(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator <= 0:
        return None
    return (numerator / denominator).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def mean_cent(values: list[Decimal]) -> Decimal:
    return cent(sum(values, ZERO) / len(values))


def add_months(month: str, k: int) -> str:
    year, number = map(int, month.split("-"))
    index = year * 12 + number - 1 + k
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def last_day(month: str) -> date:
    return date.fromisoformat(add_months(month, 1) + "-01") - timedelta(days=1)


def key_uuid(key: str) -> UUID:
    """A stable id for a fixture key, so production schemas keyed by UUID can check a case."""
    return uuid5(NAMESPACE_URL, f"hausbuch:phase9-fixture:{key}")


def check(got: Any, want: Any, what: str) -> None:
    if got != want:
        raise ValueError(f"{what}: recomputed {got!r}, the fixture says {want!r}")


def given(model: Schema) -> dict[str, Any]:
    """The fields a partial expectation names (an explicit null included)."""
    return {name: getattr(model, name) for name in model.model_fields_set}


# --- closed_month.yaml -------------------------------------------------------


class CmAccount(Schema):
    key: str
    kind: AccountKind
    owners: Owners
    first_day: date
    purpose: Literal["rental"] | None = None


class CmReason(Schema):
    code: ClosedReasonCode
    account: str | None = None
    count: int | None = Field(default=None, ge=1)


class CmExpect(Schema):
    closed: bool
    reasons: list[CmReason]
    todos: list[HealthTodoCode]


class CmWindow(Schema):
    months: list[YearMonth] = Field(max_length=12)
    enough: bool


class CmVariant(Schema):
    description: str | None = None
    own_opening: bool
    month: YearMonth
    covered_to: dict[str, date]
    expect: CmExpect


def closed_month(
    month: str,
    accounts: list[CmAccount],
    covered_to: dict[str, date],
    idle: list[str],
    waiting: int,
    open_transfers: int,
    own_opening: bool = False,
) -> CmExpect:
    end = last_day(month)
    reasons: list[CmReason] = []
    for account in accounts:
        if account.first_day > end and own_opening:
            continue  # it did not exist before its first statement (§4.4)
        covered = account.key in idle or (
            account.first_day <= end and covered_to[account.key] >= end
        )
        if not covered:
            reasons.append(CmReason(code=ClosedReasonCode.ACCOUNT_NOT_COVERED, account=account.key))
    if waiting:
        reasons.append(CmReason(code=ClosedReasonCode.LINES_WAITING, count=waiting))
    if open_transfers:
        reasons.append(CmReason(code=ClosedReasonCode.TRANSFER_OPEN, count=open_transfers))
    todos = [HealthTodoCode(r.code.value) for r in reasons]
    return CmExpect(closed=not reasons, reasons=reasons, todos=todos)


def window(closed: dict[str, bool]) -> CmWindow:
    run: list[str] = []
    newest = max((m for m, ok in closed.items() if ok), default=None)
    month = newest
    while month is not None and closed.get(month) and len(run) < 12:
        run.insert(0, month)
        month = add_months(month, -1)
    return CmWindow(months=run, enough=len(run) >= 3)


class CmCase(Lenient):
    pending: Pending | None = None  # None once its step has landed
    month: YearMonth | None = None
    accounts: list[CmAccount] | None = None
    covered_to: dict[str, date] | None = None
    idle_card: list[str] = Field(default_factory=list)
    lines_waiting: int | None = Field(default=None, ge=0)
    transfers_open: int | None = Field(default=None, ge=0)
    expect: CmExpect | None = None
    variants: list[CmVariant] = Field(default_factory=list)
    closed_months: dict[YearMonth, bool] | None = None
    expect_window: CmWindow | None = None
    expect_todos: list[HealthTodoCode] = Field(default_factory=list)

    @model_validator(mode="after")
    def _recompute(self) -> Self:
        if self.closed_months is not None:
            if self.expect_window is None:
                raise ValueError("a window case names its window")
            got = window(self.closed_months)
            check(got, self.expect_window, f"{self.id}: window")
            todos = [] if got.enough else [HealthTodoCode.TOO_FEW_MONTHS]
            check(todos, self.expect_todos, f"{self.id}: window to-dos")
            return self
        if self.accounts is None or self.lines_waiting is None or self.transfers_open is None:
            raise ValueError(f"{self.id}: a month case names its accounts, lines and transfers")
        runs = [
            (self.month, self.covered_to, False, self.expect),
            *((v.month, v.covered_to, v.own_opening, v.expect) for v in self.variants),
        ]
        for month, covered, own, expect in runs:
            if month is None or covered is None or expect is None:
                continue
            got = closed_month(
                month,
                self.accounts,
                covered,
                self.idle_card,
                self.lines_waiting,
                self.transfers_open,
                own_opening=own,
            )
            check(got, expect, f"{self.id}: {month}")
        return self


class ClosedMonthFixture(Schema):
    accounts: list[CmAccount]
    cases: list[CmCase]


# --- household_month.yaml ----------------------------------------------------


class HmLine(Schema):
    account: str
    booked: date
    amount: Money
    category: AllocationCategory
    links: str | None = None
    private_cost_for: str | None = None
    period: YearMonth | None = None
    description: str | None = None


def _flatten(value: Any) -> Any:
    """`lines: [*rental_march, *private_march]` nests the anchored lists."""
    if not isinstance(value, list):
        return value
    out = []
    for item in value:
        out.extend(_flatten(item) if isinstance(item, list) else [item])
    return out


class HmExpect(Schema):
    """Any subset of HouseholdMonth's fields (§4.2); the identities are checked over the
    fields a case gives."""

    private_income: Money | None = None
    private_spending: Money | None = None
    one_off_spending: Money | None = None
    regular_spending: Money | None = None
    rental_result: Money | None = None
    rental_cash: Money | None = None
    income: Money | None = None
    spending: Money | None = None
    saved: Money | None = None
    principal: Money | None = None
    interest: Money | None = None
    invested_property: Money | None = None
    invested: Money | None = None
    free_cash: Money | None = None
    savings_rate: Ratio | None = None
    month: YearMonth | None = None

    @model_validator(mode="after")
    def _identities(self) -> Self:
        f = given(self)

        def has(*names: str) -> bool:
            return all(n in f for n in names)

        rules = [
            (("regular_spending", "private_spending", "one_off_spending"),
             lambda: f["private_spending"] - f["one_off_spending"], "regular_spending"),
            (("rental_cash", "rental_result", "principal"),
             lambda: f["rental_result"] - f["principal"], "rental_cash"),
            (("income", "private_income", "rental_result"),
             lambda: f["private_income"] + f["rental_result"], "income"),
            (("spending", "private_spending"), lambda: f["private_spending"], "spending"),
            (("saved", "income", "spending"), lambda: f["income"] - f["spending"], "saved"),
            (("free_cash", "private_income", "regular_spending", "rental_cash"),
             lambda: f["private_income"] - f["regular_spending"] + f["rental_cash"], "free_cash"),
            (("savings_rate", "saved", "income"),
             lambda: ratio4(f["saved"], f["income"]), "savings_rate"),
        ]  # fmt: skip
        for names, value, target in rules:
            if has(*names):
                check(value(), f[target], f"§4.2 {target}")
        return self


class HmTaxInput(Schema):
    kind: Literal["private_cost"]
    amount: Money
    ignored: Literal[True]


class HmMonth(Schema):
    month: YearMonth
    lines: list[HmLine]
    expect: HmExpect

    @model_validator(mode="before")
    @classmethod
    def _nested(cls, data: Any) -> Any:
        if isinstance(data, dict) and "lines" in data:
            data = {**data, "lines": _flatten(data["lines"])}
        return data


class HmCase(Lenient):
    pending: Pending | None = None  # None once its step has landed
    month: YearMonth | None = None
    lines: list[HmLine] = Field(default_factory=list)
    months: list[HmMonth] = Field(default_factory=list)
    net_invested_property: Money | None = None
    tax_inputs: list[HmTaxInput] = Field(default_factory=list)
    ignored_tax_effect: Money | None = None
    expect: HmExpect | None = None

    @model_validator(mode="before")
    @classmethod
    def _nested(cls, data: Any) -> Any:
        if isinstance(data, dict) and "lines" in data:
            data = {**data, "lines": _flatten(data["lines"])}
        return data

    @model_validator(mode="after")
    def _months(self) -> Self:
        for line in self.lines:
            if self.month is not None and line.booked.strftime("%Y-%m") != self.month:
                raise ValueError(f"{self.id}: a line of another month (D230)")
        if self.months:
            if self.net_invested_property is None:
                raise ValueError(f"{self.id}: the months' net is named")
            parts = [m.expect.invested_property for m in self.months]
            if any(p is None for p in parts):
                raise ValueError(f"{self.id}: each month names its invested_property")
            check(sum(parts, ZERO), self.net_invested_property, f"{self.id}: net")
        elif self.month is None or self.expect is None or not self.lines:
            raise ValueError(f"{self.id}: a month, its lines and its expectation")
        return self


class HouseholdMonthFixture(Schema):
    bases: dict[str, list[HmLine]]
    cases: list[HmCase]


# --- savings_rate.yaml -------------------------------------------------------


class SrMonth(Schema):
    month: YearMonth
    saved: Money
    income: Money
    one_off_spending: Money
    principal: Money


class SrExpect(Schema):
    saved: Money
    income: Money
    one_off_spending: Money
    principal: Money
    rate: Ratio | None
    rate_regular: Ratio | None
    rate_without_principal: Ratio | None
    one_offs_left_out: bool
    months: dict[YearMonth, Ratio | None]


def savings_rate(months: list[SrMonth]) -> SrExpect:
    """Phase 10 D308 (§7.12 (c)): over the window the one-offs are left out of the saving,
    so `saved` is the regular saving Σ (S + one-off) and `rate_regular` is gone (it is `rate`).
    A month's own rate stays S ÷ E."""
    saved = sum((m.saved + m.one_off_spending for m in months), ZERO)
    income = sum((m.income for m in months), ZERO)
    one_off = sum((m.one_off_spending for m in months), ZERO)
    principal = sum((m.principal for m in months), ZERO)
    return SrExpect(
        saved=saved,
        income=income,
        one_off_spending=one_off,
        principal=principal,
        rate=ratio4(saved, income),
        rate_regular=None,
        rate_without_principal=ratio4(saved - principal, income),
        one_offs_left_out=True,
        months={m.month: ratio4(m.saved, m.income) for m in months},
    )


class SrCase(Lenient):
    pending: Pending | None = None  # None once its step has landed
    months: list[SrMonth] = Field(min_length=1, max_length=12)
    expect: SrExpect

    @model_validator(mode="after")
    def _recompute(self) -> Self:
        check(savings_rate(self.months), self.expect, self.id)
        return self


class SavingsRateFixture(Schema):
    cases: list[SrCase]


# --- reserve.yaml ------------------------------------------------------------


class RsBalance(Schema):
    key: str
    kind: AccountKind
    purpose: Literal["rental"] | None = None
    balance: Money
    as_of: date
    owners: Owners


class RsAccounts(Schema):
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)


class RsLevers(Schema):
    reserve_target_months: TargetMonths
    reserve_accounts: RsAccounts = Field(default_factory=RsAccounts)


class RsExpect(Schema):
    counted: list[str]
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    amount: Money
    need: Money
    need_codes: dict[AllocationCategory, Money]
    months: ReserveMonths | None
    target: Money
    gap: Money
    state: ReserveState
    by_owner: dict[str, Money]
    todos: list[HealthTodoCode]


RESERVE_KINDS = (AccountKind.BANK_CURRENT, AccountKind.BANK_SAVINGS, AccountKind.CREDIT_CARD)
# Phase 10 D304: a depot counts only when the lever adds it; no other kind may be added.
ADDABLE_KINDS = (*RESERVE_KINDS, AccountKind.DEPOT)


def need_class(
    need_months: list[dict[AllocationCategory, Decimal]], mean_rental_cash: Decimal
) -> tuple[Decimal, dict[AllocationCategory, Decimal]]:
    """Phase 10 D305: the need = mean need-class spending (to the cent, half-up) + max(0,
    −mean C); each code's exact monthly mean apportioned to the need-class mean by largest
    remainder, ties by the code's value, a code netting to 0 left out."""
    n = len(need_months)
    mean = mean_cent([sum(m.values(), ZERO) for m in need_months])
    totals: dict[AllocationCategory, Decimal] = {}
    for month in need_months:
        for code, amount in month.items():
            totals[code] = totals.get(code, ZERO) + amount
    exact = {c: Fraction(t) * 100 / n for c, t in totals.items() if t}
    floors = {c: v.numerator // v.denominator for c, v in exact.items()}
    left = int(mean * 100) - sum(floors.values())
    order = sorted(exact, key=lambda c: (-(exact[c] - floors[c]), c.value))
    for c in order[:left]:
        floors[c] += 1
    codes = {c: (Decimal(v) / 100).quantize(CENT) for c, v in floors.items()}
    return mean + max(ZERO, -mean_rental_cash), codes


def reserve(
    balances: list[RsBalance],
    today: date,
    need_months: list[dict[AllocationCategory, Decimal]],
    mean_rental_cash: Decimal,
    levers: RsLevers,
) -> RsExpect:
    """D237, D238, phase 10 D304, D305; checked once more by the production schema."""
    accounts, counted, by_owner = [], [], {}
    for b in balances:
        default = b.kind in RESERVE_KINDS and b.purpose != "rental"
        if b.key in levers.reserve_accounts.removed:
            origin = ReserveOrigin.REMOVED
        elif b.key in levers.reserve_accounts.added:
            if b.kind not in ADDABLE_KINDS:
                raise ValueError(f"{b.key}: a {b.kind} account cannot be added to the reserve")
            origin = ReserveOrigin.ADDED
        elif default:
            origin = ReserveOrigin.DEFAULT
        else:
            continue
        accounts.append(
            ReserveAccount(
                account_id=key_uuid(b.key), kind=b.kind, balance=b.balance, as_of=b.as_of,
                origin=origin,
            )
        )  # fmt: skip
        if origin is ReserveOrigin.REMOVED:
            continue
        counted.append(b)
        shares = {owner: Fraction(share) for owner, share in b.owners.items()}
        for owner, part in split(b.balance, shares).items():
            by_owner[owner] = by_owner.get(owner, ZERO) + part
    amount = sum((b.balance for b in counted), ZERO)
    need, need_codes = need_class(need_months, mean_rental_cash)
    target = cent(levers.reserve_target_months * need)
    months = None if need == 0 else (amount / need).quantize(Decimal("0.1"), ROUND_HALF_UP)
    stale = any(b.as_of < today - timedelta(days=7) for b in counted)
    figure = Reserve(
        accounts=accounts,
        amount=amount,
        need=need,
        months=months,
        target_months=levers.reserve_target_months,
        target=target,
        gap=max(ZERO, target - amount),
        state=ReserveState.REACHED if amount >= target else ReserveState.BELOW,
        by_owner={key_uuid(o): v for o, v in by_owner.items()},
    )
    return RsExpect(
        counted=[b.key for b in counted],
        added=[b.key for b in balances if b.key in levers.reserve_accounts.added],
        removed=[b.key for b in balances if b.key in levers.reserve_accounts.removed],
        amount=figure.amount,
        need=figure.need,
        need_codes=need_codes,
        months=figure.months,
        target=figure.target,
        gap=figure.gap,
        state=figure.state,
        by_owner=dict(sorted(by_owner.items())),
        todos=[HealthTodoCode.BALANCE_STALE] if stale else [],
    )


class RsCase(Lenient):
    pending: Pending | None = None  # None once its step has landed
    today: date
    balances: list[RsBalance]
    # Phase 10 D305: per closed month of the window, the need-class spending per code.
    need_months: list[dict[AllocationCategory, Money]] = Field(min_length=1)
    mean_rental_cash: Money
    levers: RsLevers
    expect: RsExpect

    @model_validator(mode="after")
    def _recompute(self) -> Self:
        got = reserve(
            self.balances, self.today, self.need_months, self.mean_rental_cash, self.levers,
        )  # fmt: skip
        check(got, self.expect, self.id)
        return self


class ReserveFixture(Schema):
    bases: dict[str, Any]
    cases: list[RsCase]


# --- debt.yaml ---------------------------------------------------------------


class DbEstimate(Schema):
    value: Money
    source: EstimateSource


class DbLoan(Schema):
    property: str
    balance: Money


class DbWindow(Schema):
    months: int = Field(ge=1, le=12)
    interest: Money
    principal: Money
    income: Money


class DbExpect(Schema):
    gross: Money
    liabilities: Money
    debt_ratio: Ratio | None
    ltv: dict[str, Ratio]
    debt_service_year: Money
    income_year: Money
    debt_service_ratio: Ratio | None
    debt_to_income: Multiple | None
    split: dict[NetWorthClass, Share]
    estimate_source: dict[str, EstimateSource] | None = None
    annualised_from_months: int | None = None


def debt(
    classes: dict[NetWorthClass, Decimal],
    estimates: dict[str, DbEstimate],
    loans: list[DbLoan],
    card_debt: Decimal,
    win: DbWindow,
) -> DbExpect:
    gross = sum(classes.values(), ZERO)
    liabilities = sum((loan.balance for loan in loans), ZERO) + card_debt
    ltv = {}
    for prop in dict.fromkeys(loan.property for loan in loans):
        owed = sum((loan.balance for loan in loans if loan.property == prop), ZERO)
        ltv[prop] = ratio4(owed, estimates[prop].value)
    service = cent((win.interest + win.principal) * 12 / win.months)
    income = cent((win.income + win.interest) * 12 / win.months)
    dti = None
    if income > 0:
        dti = (liabilities / income).quantize(CENT, rounding=ROUND_HALF_UP)
    return DbExpect(
        gross=gross,
        liabilities=liabilities,
        debt_ratio=ratio4(liabilities, gross),
        ltv=ltv,
        debt_service_year=service,
        income_year=income,
        debt_service_ratio=ratio4(service, income),
        debt_to_income=dti,
        split={c: ratio4(v, gross) for c, v in classes.items()},
    )


class DbCase(Lenient):
    pending: Pending | None = None  # None once its step has landed
    classes: dict[NetWorthClass, Money]
    estimates: dict[str, DbEstimate]
    loans: list[DbLoan]
    card_debt: NonNegativeMoney
    window: DbWindow
    expect: DbExpect

    @model_validator(mode="after")
    def _recompute(self) -> Self:
        got = debt(self.classes, self.estimates, self.loans, self.card_debt, self.window)
        extra = {}
        if self.expect.estimate_source is not None:
            extra["estimate_source"] = {p: e.source for p, e in self.estimates.items()}
        if self.expect.annualised_from_months is not None:
            extra["annualised_from_months"] = self.window.months
        check(got.model_copy(update=extra), self.expect, self.id)
        return self


class DebtFixture(Schema):
    bases: dict[str, dict[NetWorthClass, Money]]
    cases: list[DbCase]


# --- net_worth_series.yaml ---------------------------------------------------


class NsRun(Schema):
    id: str
    month_end: date
    created: datetime
    total: Money


class NsPoint(Schema):
    month_end: date
    run: str | None = None
    total: Money
    change: Money | None
    saved: Money | None
    other: Money | None


class NsExpect(Schema):
    points: list[NsPoint] = Field(default_factory=list)
    gaps: list[date] = Field(default_factory=list)
    todos: list[HealthTodoCode] = Field(default_factory=list)


def series(runs: list[NsRun], saved: dict[str, Decimal]) -> NsExpect:
    newest: dict[date, NsRun] = {}
    for run in runs:
        if run.month_end not in newest or run.created > newest[run.month_end].created:
            newest[run.month_end] = run
    ends = sorted(newest)
    points, gaps = [], []
    if ends:
        month = ends[0].strftime("%Y-%m")
        while last_day(month) <= ends[-1]:
            if last_day(month) not in newest:
                gaps.append(last_day(month))
            month = add_months(month, 1)
    for end in ends:
        run = newest[end]
        before = last_day(add_months(end.strftime("%Y-%m"), -1))
        change = saved_month = other = None
        if before in newest:
            change = run.total - newest[before].total
            saved_month = saved[end.strftime("%Y-%m")]
            other = change - saved_month
        points.append(
            NsPoint(
                month_end=end, run=run.id, total=run.total, change=change, saved=saved_month,
                other=other,
            )
        )  # fmt: skip
    return NsExpect(
        points=points, gaps=gaps, todos=[HealthTodoCode.MONTH_END_MISSING] if gaps else []
    )


class NsScenario(Schema):
    id: str
    created: date
    default: bool
    supersedes: str | None = None
    value_today: Money


class NsCapture(Schema):
    month_end: date
    stored_on: date
    purchase_price: Money
    scenarios: list[NsScenario]


class NsEstimate(Schema):
    property_estimate: Money
    scenario: str | None


class NsVariant(Schema):
    description: str | None = None
    month_end: date
    expect: NsEstimate


def estimate_on(capture: NsCapture, month_end: date) -> NsEstimate:
    """D241: the default scenario as it stood on the month-end, else the purchase price."""
    standing = [s for s in capture.scenarios if s.default and s.created <= month_end]
    if not standing:
        return NsEstimate(property_estimate=capture.purchase_price, scenario=None)
    latest = max(standing, key=lambda s: s.created)
    return NsEstimate(property_estimate=latest.value_today, scenario=latest.id)


class NsCase(Lenient):
    pending: Pending | None = None  # None once its step has landed
    runs: list[NsRun] = Field(default_factory=list)
    saved: dict[YearMonth, Money] = Field(default_factory=dict)
    capture: NsCapture | None = None
    variants: list[NsVariant] = Field(default_factory=list)
    expect: dict[str, JsonValue]

    @model_validator(mode="after")
    def _recompute(self) -> Self:
        if self.capture is not None:
            want = NsEstimate.model_validate(self.expect)
            check(estimate_on(self.capture, self.capture.month_end), want, self.id)
            for variant in self.variants:
                got = estimate_on(self.capture, variant.month_end)
                check(got, variant.expect, f"{self.id}: {variant.month_end}")
            return self
        check(series(self.runs, self.saved), NsExpect.model_validate(self.expect), self.id)
        return self


class NetWorthSeriesFixture(Schema):
    cases: list[NsCase]


# --- lights.yaml -------------------------------------------------------------


class LtReserve(Schema):
    amount: Money
    target: Money


class LtFigures(Schema):
    reserve: LtReserve | None = None
    savings_rate: Ratio | None = None
    savings_rate_regular: Ratio | None = None
    ltv: Ratio | None = None
    debt_service_ratio: Ratio | None = None
    debt_ratio: Ratio | None = None
    debt_to_income: Multiple | None = None


class LtLight(Schema):
    colour: LightColour | None
    sentences: list[str]


class LtExpect(Schema):
    lights: dict[MarkedFigure, LtLight]
    todos: list[HealthTodoCode]


def colour(
    direction: Direction, value: Decimal, green: Decimal | None, red: Decimal | None
) -> LightColour | None:
    """On a limit the better colour; a side without a limit gives no colour."""
    if direction is Direction.HIGHER:
        if green is not None and value >= green:
            return LightColour.GREEN
        if red is not None and value < red:
            return LightColour.RED
    else:
        if green is not None and value <= green:
            return LightColour.GREEN
        if red is not None and value > red:
            return LightColour.RED
    if green is not None and red is not None:
        return LightColour.AMBER
    return None


def lights(
    figures: LtFigures, bands: list[BandLimits] | None, wanted: list[MarkedFigure]
) -> LtExpect:
    _, by_figure, _ = proposals()
    limits = {b.figure: b for b in bands or []}
    out = {}
    for figure in wanted:
        band = limits.get(figure)
        found: LightColour | None = None
        if band is not None:
            if figure is MarkedFigure.RESERVE:
                if figures.reserve is not None:
                    target = figures.reserve.target
                    red = None if band.red is None else band.red * target
                    found = colour(Direction.HIGHER, figures.reserve.amount, target, red)
            else:
                value = getattr(figures, figure.value)
                if value is not None:
                    found = colour(DIRECTION[figure], value, band.green, band.red)
        sentences = [] if found is None else list(by_figure[figure].sentences[found])
        regular = figures.savings_rate_regular
        if figure is MarkedFigure.SAVINGS_RATE and (regular is None or regular < 0):
            sentences = [s for s in sentences if s != "savings_rate.red_regular"]
        out[figure] = LtLight(colour=found, sentences=sentences)
    todos = [HealthTodoCode.BANDS_UNCONFIRMED] if bands is None else []
    return LtExpect(lights=out, todos=todos)


class LtRefusal(Schema):
    band: dict[str, JsonValue]
    refuses: str


def refused(band: dict[str, JsonValue], code: str) -> None:
    try:
        BandLimits.model_validate(band)
    except ValidationError as error:
        if code not in str(error):
            raise ValueError(f"{band} is refused, but not with {code}: {error}") from None
        return
    raise ValueError(f"{band} must be refused with {code}")


class LtVariant(Schema):
    description: str | None = None
    figures: LtFigures
    bands: list[BandLimits] | None
    expect: LtExpect


class LtCase(Lenient):
    pending: Pending | None = None  # None once its step has landed
    figures: LtFigures | None = None
    bands: list[BandLimits] | None = None
    expect: LtExpect | None = None
    variants: list[LtVariant] = Field(default_factory=list)
    refusals: list[LtRefusal] = Field(default_factory=list)
    from_bands: bool = False

    @model_validator(mode="after")
    def _recompute(self) -> Self:
        for refusal in self.refusals:
            refused(refusal.band, refusal.refuses)
        runs = [(self.figures, self.bands, self.expect)] if self.expect else []
        runs += [(v.figures, v.bands, v.expect) for v in self.variants]
        for figures, bands, expect in runs:
            if figures is None:
                raise ValueError(f"{self.id}: a case with an expectation names its figures")
            got = lights(figures, bands, list(expect.lights))
            check(got, expect, self.id)
        if not (runs or self.refusals or self.from_bands):
            raise ValueError(f"{self.id}: nothing to check")
        return self


class LightsFixture(Schema):
    bases: dict[str, Any]
    cases: list[LtCase]


# --- surplus.yaml ------------------------------------------------------------


class SpMonth(Schema):
    month: YearMonth
    income: Money
    regular_spending: Money
    rental_cash: Money
    one_off_spending: Money


class SpExpect(Schema):
    mean_income: Money
    mean_regular_spending: Money
    mean_rental_cash: Money
    free_cash: Money
    buffer: Money
    investable: Money
    median_free_cash: Money
    lowest_free_cash: Money
    lowest_month: YearMonth
    one_off_spending_left_out: Money


def surplus(months: list[SpMonth], buffer_percent: Decimal) -> SpExpect:
    k = SurplusKey
    income = mean_cent([m.income for m in months])
    regular = mean_cent([m.regular_spending for m in months])
    flat = mean_cent([m.rental_cash for m in months])
    free = income - regular + flat
    buffer = cent(free * buffer_percent / 100) if free > 0 else ZERO
    per_month = [(m.income - m.regular_spending + m.rental_cash, m.month) for m in months]
    ordered = sorted(f for f, _ in per_month)
    middle = len(ordered) // 2
    median = (
        ordered[middle] if len(ordered) % 2 else cent((ordered[middle - 1] + ordered[middle]) / 2)
    )
    lowest = min(per_month, key=lambda fm: (fm[0], fm[1]))
    rows = Surplus(
        rows=[
            SurplusRow(key=k.MEAN_INCOME, amount=income),
            SurplusRow(key=k.MEAN_REGULAR_SPENDING, amount=regular),
            SurplusRow(key=k.MEAN_RENTAL_CASH, amount=flat),
            SurplusRow(key=k.FREE_CASH, amount=free),
            SurplusRow(key=k.BUFFER, amount=buffer),
            SurplusRow(key=k.INVESTABLE, amount=max(ZERO, free - buffer)),
            SurplusRow(key=k.MEDIAN_FREE_CASH, amount=median),
            SurplusRow(key=k.LOWEST_FREE_CASH, amount=lowest[0], month=lowest[1]),
            SurplusRow(
                key=k.ONE_OFF_SPENDING_LEFT_OUT,
                amount=sum((m.one_off_spending for m in months), ZERO),
            ),
        ]
    )
    values = {r.key.value: r.amount for r in rows.rows}
    return SpExpect(**values, lowest_month=lowest[1])


class SpCase(Lenient):
    pending: Pending | None = None  # None once its step has landed
    months: list[SpMonth] = Field(min_length=1, max_length=12)
    buffer_percent: BufferPercent
    expect: SpExpect

    @model_validator(mode="after")
    def _recompute(self) -> Self:
        check(surplus(self.months, self.buffer_percent), self.expect, self.id)
        return self


class SurplusFixture(Schema):
    bases: dict[str, list[SpMonth]]
    cases: list[SpCase]


# --- savings_plans.yaml ------------------------------------------------------


class PlOwnAccount(Schema):
    kind: AccountKind


class PlLine(Schema):
    booked: date
    amount: Money = Field(lt=0)
    to_account: str | None = None
    payee: str | None = None
    category: AllocationCategory | None = None


class PlPlan(Schema):
    key: PlanKey
    destination_payee: str | None = None
    asset_class: PlanClass
    monthly: NonNegativeMoney
    months: list[Money] = Field(min_length=3, max_length=3)
    overridden: bool = False


class PlExpect(Schema):
    plans: list[PlPlan]
    already_invested: Money
    investable_left: Money


PLAN_CODES = {
    AllocationCategory.HH_INVESTMENT_SECURITIES: PlanClass.DEPOT,
    AllocationCategory.HH_INVESTMENT_CRYPTO: PlanClass.CRYPTO,
}
PLAN_KINDS = {AccountKind.DEPOT: PlanClass.DEPOT, AccountKind.EXCHANGE: PlanClass.CRYPTO}


def slug(payee: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", payee.lower()).strip("-")


def savings_plans(
    own: dict[str, AccountKind],
    last_three: list[str],
    lines: list[PlLine],
    overrides: list[PlanOverride],
    investable: Decimal,
) -> PlExpect:
    """D245: a destination paid in each of the last three closed months; monthly = the
    median; the household's overrides on top."""
    sums: dict[str, dict[str, Decimal]] = {}
    meta: dict[str, tuple[PlanClass, str | None]] = {}
    for line in lines:
        month = line.booked.strftime("%Y-%m")
        if month not in last_three:
            continue
        if line.to_account is not None:
            plan_class = PLAN_KINDS.get(own[line.to_account])
            key, payee = line.to_account, None
        else:
            plan_class = PLAN_CODES.get(line.category)
            key, payee = slug(line.payee or ""), line.payee
        if plan_class is None:
            continue
        meta[key] = (plan_class, payee)
        by_month = sums.setdefault(key, dict.fromkeys(last_three, ZERO))
        by_month[month] -= line.amount
    plans = []
    for key, by_month in sums.items():
        months = [by_month[m] for m in last_three]
        if any(v <= 0 for v in months):
            continue
        plan = SavingsPlan(
            key=key,
            destination_account_id=None if meta[key][1] else key_uuid(key),
            destination_payee=meta[key][1],
            asset_class=meta[key][0],
            monthly=sorted(months)[1],
            months=months,
        )
        override = next((o for o in overrides if o.plan_key == key), None)
        if override is not None and override.dropped:
            continue
        if override is not None:
            plan = plan.model_copy(update={"monthly": override.monthly, "overridden": True})
        plans.append(plan)
    plans.sort(key=lambda p: (-p.monthly, p.key))
    already = sum((p.monthly for p in plans), ZERO)
    return PlExpect(
        plans=[
            PlPlan.model_validate(p.model_dump(exclude={"destination_account_id"})) for p in plans
        ],
        already_invested=already,
        investable_left=investable - already,
    )


class PlCase(Lenient):
    pending: Pending | None = None  # None once its step has landed
    own_accounts: dict[str, PlOwnAccount]
    last_three_closed: list[YearMonth] = Field(min_length=3, max_length=3)
    lines: list[PlLine]
    overrides: list[PlanOverride] = Field(default_factory=list)
    investable: Money
    expect: PlExpect

    @model_validator(mode="after")
    def _recompute(self) -> Self:
        own = {k: a.kind for k, a in self.own_accounts.items()}
        got = savings_plans(
            own, self.last_three_closed, self.lines, self.overrides, self.investable
        )
        check(got, self.expect, self.id)
        return self


class SavingsPlansFixture(Schema):
    bases: dict[str, Any]
    cases: list[PlCase]


# --- paths.yaml and plan_levers.yaml -----------------------------------------


class PaMonth(Schema):
    mean_income: Money
    mean_regular_spending: Money
    mean_rental_cash: Money
    buffer_percent: BufferPercent


class PaPlan(Schema):
    key: PlanKey
    monthly: NonNegativeMoney
    asset_class: PlanClass | None = None


class PaRow(Schema):
    key: PathKey
    paused: list[PlanKey] | None = None
    to_reserve_first_month: Money | None = None
    reached: YearMonth | None
    months_needed: int | None = Field(ge=0)
    used_up: YearMonth | None
    reserve_at_horizon: Money


class PaExpect(Schema):
    rows: list[PaRow]
    others: list[PlanKey] = Field(default_factory=list)


def month_function(
    newest: str,
    k: int,
    month: PaMonth,
    flat_years: dict[int, Decimal] | None,
    changes: list[IncomeChange] = (),
    one_offs: list[OneOff] = (),
) -> tuple[str, Decimal, Decimal, Decimal]:
    """§7.1: (month, flat, free, investable) for the k-th month after the newest closed."""
    ym = add_months(newest, k)
    year = int(ym[:4])
    flat = month.mean_rental_cash
    if flat_years is not None and year != int(newest[:4]) and year in flat_years:
        flat = flat_years[year]
    free = month.mean_income - month.mean_regular_spending + flat
    for change in changes:
        if change.from_month <= ym and (change.to_month is None or ym <= change.to_month):
            free += change.monthly_net
    for one_off in one_offs:
        if one_off.month == ym:
            free += one_off.amount
    investable = free - cent(free * month.buffer_percent / 100) if free > 0 else free
    return ym, flat, free, investable


def flat_by_year(flats: dict[str, dict[int, Any]]) -> dict[int, Decimal]:
    """Σ over the properties of (net_cash + tax_effect) ÷ 12, each to the cent, for the years
    every property's scenario projects."""
    years = set.intersection(*(set(years) for years in flats.values()))
    return {
        y: sum((cent((f[y].net_cash + f[y].tax_effect) / 12) for f in flats.values()), ZERO)
        for y in years
    }


def path_rows(
    newest: str,
    month: PaMonth,
    start: Decimal,
    target: Decimal,
    plans: list[PaPlan],
    horizon: int,
    mode: AdviceMode,
    flat_years: dict[int, Decimal] | None = None,
    changes: list[IncomeChange] = (),
    one_offs: list[OneOff] = (),
) -> PaExpect:
    order = sorted(plans, key=lambda p: -p.monthly)  # stable: the plans' order on a tie
    shown, others = order[:4], [p.key for p in order[4:]]
    defs = [("reserve_first", [p.key for p in order])]
    defs += [(f"pause:{p.key}", [p.key]) for p in shown]
    defs += [("as_is", [])]
    if mode is AdviceMode.GENERIC:
        defs = [defs[0], defs[-1]]
    steps = [
        month_function(newest, k, month, flat_years, changes, one_offs)
        for k in range(1, horizon + 1)
    ]
    rows = []
    for key, paused in defs:
        continuing = sum((p.monthly for p in plans if p.key not in paused), ZERO)
        value = start
        reached = (newest, 0) if start >= target else None
        used = None
        for k, (ym, _, _, investable) in enumerate(steps, start=1):
            value += investable - continuing
            if reached is None and value >= target:
                reached = (ym, k)
            if used is None and value < 0:
                used = ym
        rows.append(
            PaRow(
                key=key,
                paused=paused,
                to_reserve_first_month=steps[0][3] - continuing,
                reached=reached[0] if reached else None,
                months_needed=reached[1] if reached else None,
                used_up=used,
                reserve_at_horizon=value,
            )
        )
    rows.sort(key=lambda r: (r.reached is None, r.reached or ""))
    return PaExpect(rows=rows, others=others)


def same_rows(got: list[PaRow], want: list[PaRow], what: str) -> None:
    """Compare the fields a fixture row names (plan_levers' rows leave out two)."""
    check([r.key for r in got], [r.key for r in want], f"{what}: rows")
    for g, w in zip(got, want, strict=True):
        named = w.model_fields_set
        check(g.model_dump(include=named), w.model_dump(include=named), f"{what}: {w.key}")


class PaCase(Lenient):
    pending: Pending | None = None  # None once its step has landed
    newest_closed: YearMonth
    month: PaMonth
    reserve: Money
    target: Money
    plans: list[PaPlan]
    horizon_months: int = Field(ge=12, le=240)
    mode: AdviceMode
    expect: PaExpect

    @model_validator(mode="after")
    def _recompute(self) -> Self:
        got = path_rows(
            self.newest_closed, self.month, self.reserve, self.target, self.plans,
            self.horizon_months, self.mode,
        )  # fmt: skip
        same_rows(got.rows, self.expect.rows, self.id)
        check(got.others, self.expect.others, f"{self.id}: others")
        return self


class PathsFixture(Schema):
    bases: dict[str, Any]
    cases: list[PaCase]


class YearFigures(Schema):
    value: Money | None = None
    loan_balance: Money | None = None
    net_cash: Money
    tax_effect: Money


class LvMonth(Schema):
    month: YearMonth
    flat: Money
    free: Money
    investable: Money


class LvExpect(Schema):
    months: list[LvMonth] = Field(default_factory=list)
    rows: list[PaRow]
    todos: list[HealthTodoCode]


class LvCase(Lenient):
    pending: Pending | None = None  # None once its step has landed
    newest_closed: YearMonth | None = None
    month: PaMonth | None = None
    reserve: Money | None = None
    target: Money | None = None
    plans: list[PaPlan] = Field(default_factory=list)
    horizon_months: int | None = None
    flat_years: dict[str, dict[int, YearFigures]] | None = None
    income_changes: list[IncomeChange] = Field(default_factory=list)
    one_offs: list[OneOff] = Field(default_factory=list)
    proposed_one_offs: list[OneOff] = Field(default_factory=list)
    expect: LvExpect | None = None
    accepted: list[dict[str, JsonValue]] = Field(default_factory=list)
    refused: list[dict[str, JsonValue]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _recompute(self) -> Self:
        for levers in self.accepted:
            HouseholdLevers.model_validate(levers)
        for levers in self.refused:
            try:
                HouseholdLevers.model_validate(levers)
            except ValidationError:
                continue
            raise ValueError(f"{self.id}: {levers} must be refused")
        if self.expect is None:
            if not (self.accepted and self.refused):
                raise ValueError(f"{self.id}: nothing to check")
            return self
        if any(o.source is not OneOffSource.TYPED for o in self.one_offs):
            raise ValueError(f"{self.id}: a proposed one-off counts only once accepted")
        flats = None if self.flat_years is None else flat_by_year(self.flat_years)
        for want in self.expect.months:
            k = (int(want.month[:4]) - int(self.newest_closed[:4])) * 12
            k += int(want.month[5:]) - int(self.newest_closed[5:])
            _, flat, free, investable = month_function(
                self.newest_closed, k, self.month, flats, self.income_changes, self.one_offs
            )
            got = LvMonth(month=want.month, flat=flat, free=free, investable=investable)
            check(got, want, f"{self.id}: {want.month}")
        got = path_rows(
            self.newest_closed, self.month, self.reserve, self.target, self.plans,
            self.horizon_months, AdviceMode.OWN_USE, flats, self.income_changes, self.one_offs,
        )  # fmt: skip
        same_rows(got.rows, self.expect.rows, self.id)
        todos = [HealthTodoCode.NO_DEFAULT_SCENARIO] if flats is None else []
        check(todos, self.expect.todos, f"{self.id}: to-dos")
        return self


class PlanLeversFixture(Schema):
    bases: dict[str, Any]
    cases: list[LvCase]


# --- net_worth_projection.yaml -----------------------------------------------


class NpStart(Schema):
    month_end: date
    run: str
    bank: Money
    depot: Money
    crypto: Money
    property: Money
    loans: Money
    other_liabilities: Money
    # Phase 10 D304: the part of `depot` on depots ticked „Zählt zur Reserve“. It is in the
    # reserve's start, earns the reserve rate (on the bank row) and stays at its start value in
    # the depot row.
    reserve_depot: Money = Decimal("0.00")


class NpFlat(Schema):
    """A property's default scenario (phase 4): its sale year, its projected years (phase 4
    projects today's year + 1 through the sale year) and the sale's net proceeds
    (`SaleResult.net_proceeds`). With `sale_year` null the property is kept without a
    scenario (phase 10 D301): its value today for ever (`kept_value`) and its loans' balance
    per 31 December along their schedules (`kept_loans`), its cash the measured mean."""

    sale_year: int | None = None
    years: dict[int, YearFigures] = Field(default_factory=dict)
    net_proceeds: Money = Decimal("0.00")
    kept_value: Money | None = None
    kept_loans: dict[int, Money] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _kept(self) -> Self:
        if self.sale_year is None and self.kept_value is None:
            raise ValueError("a kept property names its value")
        return self


class NpLevers(Schema):
    value_change: ValueChange
    net_worth_years: int = Field(ge=1, le=30)
    income_changes: list[IncomeChange] = Field(default_factory=list)
    one_offs: list[OneOff] = Field(default_factory=list)


class NpPoint(Schema):
    on: date
    bank: Money
    depot: Money
    crypto: Money
    property: Money
    loans: Money
    other_liabilities: Money
    total: Money


class NpClasses(Schema):
    bank: Money
    depot: Money
    crypto: Money


class NpExpect(Schema):
    points: list[NpPoint] | None = None
    end_of_2027: NpClasses | None = None
    used_up: YearMonth | None = None
    todos: list[HealthTodoCode] | None = None
    last: NpPoint | None = None
    change: ProjectionChange | None = None
    # The reserve row at the end of a month (phase 10 D301: after its interest, its saving, a
    # sale's proceeds and the move of what lies above the target).
    as_is_reserve: dict[YearMonth, Money] | None = None
    bank: dict[date, Money] | None = None
    difference: Money | None = None
    value: Money | None = None


class NpStretch(Schema):
    value: Money
    rate: Dec
    monthly: Money
    months: int = Field(ge=1, le=12)


def stretch(value: Fraction, rate: Fraction, payments: list[Fraction], months: int) -> Fraction:
    """§7.5: V × (1 + r·m/12) + Σ_j c_j × (1 + r·(m − j)/12), j = 1 … m."""
    out = value * (1 + rate * months / 12)
    for j, paid in enumerate(payments, start=1):
        out += paid * (1 + rate * Fraction(months - j, 12))
    return out


def walk_flat_years(flats: dict[str, NpFlat], first: int, last: int) -> dict[int, Decimal]:
    """Phase 10 D301: per year from `first` to `last`, Σ over the properties of the scenario's
    (net_cash + tax_effect) ÷ 12, each to the cent, and 0 for a property after its sale year.
    A year some property's scenario does not project keeps the measured mean (left out of the
    map). Empty, so the measured mean throughout, when no property has a scenario or one is
    kept without one."""
    if not flats or any(f.sale_year is None for f in flats.values()):
        return {}
    out: dict[int, Decimal] = {}
    for y in range(first, last + 1):
        parts = []
        for f in flats.values():
            if y > f.sale_year:
                parts.append(ZERO)
            elif y in f.years:
                parts.append(cent((f.years[y].net_cash + f.years[y].tax_effect) / 12))
            else:
                break
        else:
            out[y] = sum(parts, ZERO)
    return out


def flats_on(flats: dict[str, NpFlat], year: int) -> tuple[Decimal, Decimal]:
    """The properties' value and loans on 31 December of `year`: a property sold on that day
    or before counts 0 (its proceeds are on the reserve), a kept one its value today and its
    loans' balance of that day."""
    value = loans = ZERO
    for f in flats.values():
        if f.sale_year is None:
            value += f.kept_value
            loans += f.kept_loans[year]
        elif year < f.sale_year:
            value += f.years[year].value
            loans += f.years[year].loan_balance
    return value, loans


def project(
    newest: str,
    month: PaMonth,
    start: NpStart | None,
    reserve_start: Decimal,
    plans: list[PaPlan],
    flats: dict[str, NpFlat | None],
    levers: NpLevers,
    target: Decimal | None = None,
    split: InvestmentSplit | None = None,
) -> dict[str, Any]:
    """§7.5, D247 amended by phase 10 D301 (§7.11 (a)): one month walk. Each month k after the
    newest closed one, in this order:
    1. the reserve row R earns the typed reserve rate for one month, R × r ÷ 12 (simple, only
       while R > 0; no reserve rate typed: nothing);
    2. R += the month's „Anlegbar“ (§7.1, the buffer kept back) − every savings plan;
    3. in December of a property's sale year, R += its net proceeds (counted once);
    4. R < 0: the walk ends there, `used_up` = that month, no point from it on;
    5. what lies above the reserve target moves out of R into the classes in the split's
       shares (no split or no target: nothing moves; nothing ever moves back);
    6. a class's payment this month = its plans + its share of what moved; on 31 December each
       class with a rate grows by the stretch formula over the year's payments.
    The bank row = the start's bank + (R − R at the start); the depot row is the invested funds
    plus the ticked depot's start value. Points: 31 December of each year after the newest
    closed month's year, `net_worth_years` of them, past any sale."""
    vc = levers.value_change
    todos = []
    if start is None:
        todos.append(HealthTodoCode.MONTH_END_MISSING)
    rates = {"depot": vc.depot, "crypto": vc.crypto}
    shares = {c: Fraction(getattr(split, c)) / 100 for c in rates} if split is not None else {}
    invested = {"depot": start.depot - start.reserve_depot, "crypto": start.crypto} if start else {}
    held = {c for c in rates if start is not None and invested[c] != 0}
    held |= {p.asset_class.value for p in plans}
    held |= {c for c, share in shares.items() if share > 0}
    if any(rates[c] is None for c in held):
        todos.append(HealthTodoCode.RATE_MISSING)
    if todos:
        return {"points": [], "todos": todos}
    kept = {k: f for k, f in flats.items() if f is not None}
    first = int(newest[:4]) + 1
    years = list(range(first, first + levers.net_worth_years))
    flat_month = walk_flat_years(kept, first, years[-1])
    by_class = {c: sum((p.monthly for p in plans if p.asset_class.value == c), ZERO) for c in rates}
    continuing = sum((p.monthly for p in plans), ZERO)
    r0 = Fraction(reserve_start)
    reserve = r0
    values = {c: Fraction(invested[c]) for c in rates}
    pays: dict[str, list[Fraction]] = {c: [] for c in rates}
    paid = {c: Fraction(0) for c in rates}
    paid_by_year: dict[int, dict[str, Fraction]] = {}
    decembers, as_is_by_month, bank_by_day, used = {}, {}, {}, None
    k = 0
    while add_months(newest, k) < f"{years[-1]}-12":
        k += 1
        ym, _, _, investable = month_function(
            newest, k, month, flat_month, levers.income_changes, levers.one_offs
        )
        if vc.reserve is not None and reserve > 0:
            reserve += reserve * Fraction(vc.reserve) / 12
        reserve += Fraction(investable - continuing)
        if ym.endswith("-12"):
            for f in kept.values():
                if f.sale_year == int(ym[:4]):
                    reserve += Fraction(f.net_proceeds)
        if reserve < 0:
            as_is_by_month[ym] = cent(reserve)
            used = ym
            break
        moved = Fraction(0)
        if shares and target is not None and reserve > Fraction(target):
            moved = reserve - Fraction(target)
            reserve -= moved
        as_is_by_month[ym] = cent(reserve)
        for c in rates:
            pay = Fraction(by_class[c]) + moved * shares.get(c, Fraction(0))
            pays[c].append(pay)
            paid[c] += pay
        if ym.endswith("-12"):
            for c, rate in rates.items():
                if rate is not None:  # nothing held and nothing paid in (§15.1)
                    values[c] = stretch(values[c], Fraction(rate), pays[c], len(pays[c]))
                pays[c] = []
            year = int(ym[:4])
            bank = cent(Fraction(start.bank) + reserve - r0)
            decembers[year] = {
                "bank": bank,
                "depot": cent(values["depot"]) + start.reserve_depot,
                "crypto": cent(values["crypto"]),
            }
            paid_by_year[year] = dict(paid)
            bank_by_day[last_day(ym)] = bank
    points = []
    start_total = (
        start.bank + start.depot + start.crypto + start.property - start.loans
        - start.other_liabilities
    )  # fmt: skip
    for year in years:
        if year not in decembers:
            break
        classes = decembers[year]
        value, loans = flats_on(kept, year)
        total = sum(classes.values(), ZERO) + value - loans - start.other_liabilities
        paid_in = {c: cent(paid_by_year[year][c]) for c in rates}
        by_assumption = {
            PlanClass(c): classes[c] - getattr(start, c) - paid_in[c]
            for c in rates
            if rates[c] is not None
        }
        change = ProjectionChange(
            on_accounts=classes["bank"] - start.bank,
            paid_in=sum(paid_in.values(), ZERO),
            from_assumptions=sum(by_assumption.values(), ZERO),
            from_assumptions_by_class=by_assumption,
            flat_value=value - start.property,
            loan_repaid=start.loans - loans,
            total=total - start_total,
        )
        point = ProjectionPoint(
            on=date(year, 12, 31),
            **classes,
            property=value,
            loans=loans,
            other_liabilities=start.other_liabilities,
            total=total,
            change=change,
        )
        points.append(point)
    return {
        "points": points,
        "todos": [],
        "decembers": decembers,
        "as_is": as_is_by_month,
        "bank": bank_by_day,
        "used_up": used,
        "start_bank": start.bank,
        "start_reserve": reserve_start,
    }


def as_np(point: ProjectionPoint) -> NpPoint:
    return NpPoint.model_validate(point.model_dump(exclude={"change"}))


class NpCase(Lenient):
    pending: Pending | None = None  # None once its step has landed
    newest_closed: YearMonth | None = None
    closed_months: int | None = None
    month: PaMonth | None = None
    start: NpStart | None = None
    reserve: Money | None = None
    plans: list[PaPlan] = Field(default_factory=list)
    flats: dict[str, NpFlat | None] = Field(default_factory=dict)
    levers: NpLevers | None = None
    # Phase 10 D301: the reserve target (months × need) and the effective investment split;
    # without a split nothing moves out of the reserve.
    target: Money | None = None
    split: InvestmentSplit | None = None
    stretch: NpStretch | None = None
    expect: NpExpect

    @model_validator(mode="after")
    def _recompute(self) -> Self:
        e = self.expect
        if self.stretch is not None:
            s = self.stretch
            got = stretch(
                Fraction(s.value), Fraction(s.rate), [Fraction(s.monthly)] * s.months, s.months
            )
            check(cent(got), e.value, self.id)
            return self
        if self.levers is None or self.month is None or self.reserve is None:
            raise ValueError(f"{self.id}: a projection case names its household")
        got = project(
            self.newest_closed, self.month, self.start, self.reserve, self.plans, self.flats,
            self.levers, self.target, self.split,
        )  # fmt: skip
        check_projection(got, e, self.id)
        return self


def check_projection(got: dict[str, Any], e: NpExpect, what: str) -> None:
    points = got["points"]
    if e.points is not None:
        check([as_np(p) for p in points], e.points, f"{what}: points")
    if e.todos is not None:
        check(got["todos"], e.todos, f"{what}: to-dos")
    if e.last is not None:
        check(as_np(points[-1]), e.last, f"{what}: last point")
    if e.change is not None:
        check(points[-1].change, e.change, f"{what}: change rows")
    if e.end_of_2027 is not None:
        check(NpClasses(**got["decembers"][2027]), e.end_of_2027, f"{what}: end of 2027")
    named = e.model_fields_set
    if "used_up" in named:
        check(got.get("used_up"), e.used_up, f"{what}: used up")
    for ym, want in (e.as_is_reserve or {}).items():
        check(got["as_is"][ym], want, f"{what}: as_is {ym}")
    for day, want in (e.bank or {}).items():
        check(got["bank"][day], want, f"{what}: bank {day}")
    if e.difference is not None:
        for ym, reserve_k in (e.as_is_reserve or {}).items():
            bank_k = got["bank"][last_day(ym)]
            check(bank_k - got["start_bank"], e.difference, f"{what}: bank − start")
            check(reserve_k - got["start_reserve"], e.difference, f"{what}: §7.5 (4)")


class NetWorthProjectionFixture(Schema):
    bases: dict[str, Any]
    cases: list[NpCase]


# --- health_worked.yaml ------------------------------------------------------


class HwAccount(Schema):
    key: str
    kind: AccountKind
    owners: Owners
    purpose: Literal["rental"] | None = None


class HwBalance(Schema):
    account: str
    balance: Money
    as_of: date
    description: str | None = None


class HwRun(Schema):
    id: str
    month_end: date
    created: datetime
    total: Money
    by_class: dict[NetWorthClass, Money] | None = None
    liabilities: dict[Literal["loans", "other"], Money] | None = None


class HwScenario(Schema):
    value_today: Money
    sale_year: int
    years: dict[int, YearFigures]


class HwLoan(Schema):
    balance: Money


class HwProperty(Schema):
    purchase_price: Money
    loans: list[HwLoan]
    default_scenario: HwScenario | None


class HwLevers(Schema):
    reserve_target_months: TargetMonths
    buffer_percent: BufferPercent
    horizon_months: int
    net_worth_years: int
    value_change: ValueChange
    bands: list[BandLimits] | None = None


class HwHousehold(Schema):
    today: date
    newest_closed: YearMonth
    accounts: list[HwAccount]
    lines: list[HmLine]
    balances: list[HwBalance]
    runs: list[HwRun]
    properties: dict[str, HwProperty]
    levers: HwLevers


class HwWindow(Schema):
    months: list[YearMonth]
    enough: bool
    annualised_from: int


class HwReserve(Schema):
    counted: list[str]
    amount: Money
    need: Money
    need_codes: dict[AllocationCategory, Money]
    months: ReserveMonths | None
    target: Money
    gap: Money
    state: ReserveState


class HwNetWorth(Schema):
    gross: Money
    liabilities: Money
    total: Money


class HwDebt(Schema):
    ltv: dict[str, Ratio]
    debt_ratio: Ratio | None
    debt_service_year: Money
    income_year: Money
    debt_service_ratio: Ratio | None
    debt_to_income: Multiple | None
    split: dict[NetWorthClass, Share]


class HwSeriesPoint(Schema):
    month_end: date
    total: Money
    change: Money | None
    saved: Money | None
    other: Money | None


class HwSavingsRate(Schema):
    saved: Money
    income: Money
    one_off_spending: Money
    principal: Money
    rate: Ratio | None
    rate_regular: Ratio | None
    rate_without_principal: Ratio | None
    one_offs_left_out: bool = False


class HwPlan(Schema):
    key: PlanKey
    asset_class: PlanClass
    monthly: NonNegativeMoney


class HwProjection(Schema):
    points: list[NpPoint]
    change: ProjectionChange


class HwExpect(Schema):
    window: HwWindow | None = None
    months: list[HmExpect] | None = None
    savings_rate: HwSavingsRate | None = None
    reserve: HwReserve | None = None
    net_worth: HwNetWorth | None = None
    debt: HwDebt | None = None
    series: list[HwSeriesPoint] | None = None
    lights: dict[MarkedFigure, LtLight] | None = None
    surplus: SpExpect | None = None
    savings_plans: list[HwPlan] | None = None
    already_invested: Money | None = None
    investable_left: Money | None = None
    paths: list[PaRow] | None = None
    projection: HwProjection | None = None
    todos: list[HealthTodoCode] | None = None


class HwCase(Lenient):
    pending: Pending | None = None  # None once its step has landed
    household: HwHousehold
    expect: HwExpect


class HealthWorkedFixture(Schema):
    bases: dict[str, Any]
    cases: list[HwCase]

    @model_validator(mode="after")
    def _recompute(self) -> Self:
        by_id = {c.id: c for c in self.cases}
        figures = by_id["figures_and_marks"].expect
        if figures.months is None or figures.window is None:
            raise ValueError("health_worked: figures_and_marks names the months and window")
        for case in self.cases:
            worked_case(case, figures)
        return self


def _quoted(name: str, case_id: str) -> Any:
    """The expectation of a per-function fixture's case that quotes §15.2."""
    return next(c for c in load(name).cases if c.id == case_id).expect


def worked_case(case: HwCase, figures: HwExpect) -> None:
    """Recompute every figure from the household's inputs and the window's months."""
    hh, e, what = case.household, case.expect, f"health_worked {case.id}"
    months = figures.months
    window_months = figures.window.months
    check([m.month for m in months], window_months, f"{what}: months")
    win_closed = window(dict.fromkeys(window_months, True))
    check(win_closed.months, window_months, f"{what}: the window is one unbroken run")
    check(win_closed.enough, figures.window.enough, f"{what}: window enough")
    check(len(window_months), figures.window.annualised_from, f"{what}: annualised from")
    accounts = {a.key: a for a in hh.accounts}
    # The savings rate (D235).
    rate = savings_rate(
        [SrMonth.model_validate(m.model_dump(include=set(SrMonth.model_fields))) for m in months]
    )
    if e.savings_rate is not None:
        check(
            HwSavingsRate.model_validate(rate.model_dump(exclude={"months"})), e.savings_rate, what
        )
        quoted = _quoted("savings_rate", "three_months")
        check(rate, quoted, f"{what}: savings_rate.yaml three_months")
        for m in months:
            check(m.savings_rate, rate.months[m.month], f"{what}: {m.month} rate")
    # The reserve (D237, D238).
    balances = [
        RsBalance(
            key=b.account,
            kind=accounts[b.account].kind,
            purpose=accounts[b.account].purpose,
            balance=b.balance,
            as_of=b.as_of,
            owners=accounts[b.account].owners,
        )
        for b in hh.balances
    ]
    # Phase 10 D305: the need from the need-class spending of the private lines only (the
    # Budget's Notwendiges: a taxonomy code of class `need`, never a one-off code).
    need_months: list[dict[AllocationCategory, Decimal]] = []
    for month in window_months:
        spent: dict[AllocationCategory, Decimal] = {}
        for line in hh.lines:
            entry = TAXONOMY.get(line.category)
            if (
                line.booked.strftime("%Y-%m") != month
                or accounts[line.account].purpose == "rental"
                or entry is None
                or entry.one_off
                or entry.budget_class is not BudgetClass.NEED
            ):
                continue
            spent[line.category] = spent.get(line.category, ZERO) - line.amount
        need_months.append(spent)
    mean_c = mean_cent([m.rental_cash for m in months])
    res = reserve(
        balances, hh.today, need_months, mean_c,
        RsLevers(reserve_target_months=hh.levers.reserve_target_months),
    )  # fmt: skip
    if e.reserve is not None:
        check(
            HwReserve.model_validate(res.model_dump(include=set(HwReserve.model_fields))),
            e.reserve,
            what,
        )
        quoted = _quoted("reserve", "default_accounts")
        check(res, quoted, f"{what}: reserve.yaml default_accounts")
    # The net worth and the debt figures (D239, D240), from the newest run.
    run = max(hh.runs, key=lambda r: (r.month_end, r.created))
    gross = sum(run.by_class.values(), ZERO)
    owed = run.liabilities["loans"] + run.liabilities["other"]
    check(gross - owed, run.total, f"{what}: the run's total")
    if e.net_worth is not None:
        check(HwNetWorth(gross=gross, liabilities=owed, total=run.total), e.net_worth, what)
    estimates = {
        p: DbEstimate(value=prop.default_scenario.value_today, source=EstimateSource.SCENARIO)
        for p, prop in hh.properties.items()
    }
    loans = [
        DbLoan(property=p, balance=loan.balance)
        for p, prop in hh.properties.items()
        for loan in prop.loans
    ]
    check(sum((loan.balance for loan in loans), ZERO), run.liabilities["loans"], f"{what}: loans")
    win = DbWindow(
        months=len(months),
        interest=sum((m.interest for m in months), ZERO),
        principal=sum((m.principal for m in months), ZERO),
        income=sum((m.income for m in months), ZERO),
    )
    debts = debt(dict(run.by_class), estimates, loans, run.liabilities["other"], win)
    if e.debt is not None:
        check(
            HwDebt.model_validate(debts.model_dump(include=set(HwDebt.model_fields))), e.debt, what
        )
        quoted = _quoted("debt", "one_loan")
        check(debts, quoted, f"{what}: debt.yaml one_loan")
    # The series (D241).
    runs = [
        NsRun(id=r.id, month_end=r.month_end, created=r.created, total=r.total) for r in hh.runs
    ]
    saved = {m.month: m.saved for m in months}
    got_series = series(runs, saved)
    if e.series is not None:
        points = [
            HwSeriesPoint.model_validate(p.model_dump(exclude={"run"})) for p in got_series.points
        ]
        check(points, e.series, what)
        check(
            got_series,
            NsExpect.model_validate(_quoted("net_worth_series", "three_points")),
            f"{what}: net_worth_series.yaml three_points",
        )
    # The marks (D250).
    lt_figures = LtFigures(
        reserve=LtReserve(amount=res.amount, target=res.target),
        savings_rate=rate.rate,
        savings_rate_regular=rate.rate_regular,
        ltv=debts.ltv["p1"],
        debt_service_ratio=debts.debt_service_ratio,
        debt_ratio=debts.debt_ratio,
        debt_to_income=debts.debt_to_income,
    )
    if e.lights is not None:
        got_lights = lights(lt_figures, hh.levers.bands, list(e.lights))
        check(got_lights.lights, e.lights, f"{what}: lights")
        if e.todos is not None:
            check(got_lights.todos, e.todos, f"{what}: lights' to-dos")
        if hh.levers.bands is not None:
            check(got_lights, _quoted("lights", "worked"), f"{what}: lights.yaml worked")
    # The plan (§6, §7).
    sp_months = [
        SpMonth(
            month=m.month,
            income=m.private_income,
            regular_spending=m.regular_spending,
            rental_cash=m.rental_cash,
            one_off_spending=m.one_off_spending,
        )
        for m in months
    ]
    got_surplus = surplus(sp_months, hh.levers.buffer_percent)
    if e.surplus is not None:
        check(got_surplus, e.surplus, f"{what}: surplus")
        check(got_surplus, _quoted("surplus", "baseline"), f"{what}: surplus.yaml baseline")
    legs = {}
    for line in hh.lines:
        if line.links is not None:
            legs.setdefault(line.links, []).append(line)
    plan_lines = []
    for pair in legs.values():
        out = next(leg for leg in pair if leg.amount < 0)
        into = next(leg for leg in pair if leg.amount > 0)
        plan_lines.append(PlLine(booked=out.booked, amount=out.amount, to_account=into.account))
    for m in months:
        if m.invested is not None:
            into = -sum(
                (pl.amount for pl in plan_lines if pl.booked.strftime("%Y-%m") == m.month), ZERO
            )
            check(into, m.invested, f"{what}: {m.month} invested (§4.2, transfers into savings)")
    got_plans = savings_plans(
        {k: a.kind for k, a in accounts.items()}, window_months, plan_lines, [],
        got_surplus.investable,
    )  # fmt: skip
    if e.savings_plans is not None:
        plans = [
            HwPlan.model_validate(p.model_dump(include=set(HwPlan.model_fields)))
            for p in got_plans.plans
        ]
        check(plans, e.savings_plans, f"{what}: savings plans")
        check(got_plans.already_invested, e.already_invested, f"{what}: already invested")
        check(got_plans.investable_left, e.investable_left, f"{what}: investable left")
        check(
            got_plans, _quoted("savings_plans", "depot_and_exchange"), f"{what}: savings_plans.yaml"
        )
    pa_month = PaMonth(
        mean_income=got_surplus.mean_income,
        mean_regular_spending=got_surplus.mean_regular_spending,
        mean_rental_cash=got_surplus.mean_rental_cash,
        buffer_percent=hh.levers.buffer_percent,
    )
    pa_plans = [
        PaPlan(key=p.key, monthly=p.monthly, asset_class=p.asset_class) for p in got_plans.plans
    ]
    scenarios = {p: prop.default_scenario for p, prop in hh.properties.items()}
    flat_years = flat_by_year({p: s.years for p, s in scenarios.items()})
    if e.paths is not None:
        got_paths = path_rows(
            hh.newest_closed, pa_month, res.amount, res.target, pa_plans,
            hh.levers.horizon_months, AdviceMode.OWN_USE, flat_years,
        )  # fmt: skip
        same_rows(got_paths.rows, e.paths, f"{what}: paths")
        quoted = _quoted("plan_levers", "projection_year_replaces_mean")
        same_rows(got_paths.rows, quoted.rows, f"{what}: plan_levers.yaml")
    if e.projection is not None:
        start = NpStart(
            month_end=run.month_end, run=run.id, bank=run.by_class["bank"],
            depot=run.by_class["depot"], crypto=run.by_class["crypto"],
            property=run.by_class["property"], loans=run.liabilities["loans"],
            other_liabilities=run.liabilities["other"],
        )  # fmt: skip
        flats = {p: NpFlat(sale_year=s.sale_year, years=s.years) for p, s in scenarios.items()}
        levers = NpLevers(
            value_change=hh.levers.value_change, net_worth_years=hh.levers.net_worth_years
        )
        # Phase 10 D301: the money above the reserve target moves into the split the savings
        # plans give (no split typed): each class's share of the plans, one place half-up, the
        # crypto share the rest to 100 (400 ÷ 550 = 72.7 %).
        total = sum((p.monthly for p in pa_plans), ZERO)
        depot = sum((p.monthly for p in pa_plans if p.asset_class is PlanClass.DEPOT), ZERO)
        share = (depot * 100 / total).quantize(Decimal("0.1"), ROUND_HALF_UP)
        split = InvestmentSplit(depot=share, crypto=Decimal(100) - share)
        got = project(
            hh.newest_closed, pa_month, start, res.amount, pa_plans, flats, levers, res.target,
            split,
        )  # fmt: skip
        check([as_np(p) for p in got["points"]], e.projection.points, f"{what}: points")
        check(got["points"][-1].change, e.projection.change, f"{what}: change rows")
        quoted = _quoted("net_worth_projection", "worked")
        check(
            [as_np(p) for p in got["points"]], quoted.points, f"{what}: net_worth_projection.yaml"
        )


# --- explain_facts.yaml and explain_render.yaml (shape; step 4 replays them) ---


class ExAccount(Schema):
    id: str
    label: str
    provider: str
    kind: AccountKind
    owners: Owners


class ExPlan(Schema):
    key: PlanKey
    destination_label: str
    asset_class: PlanClass
    monthly: NonNegativeMoney


class ExAsset(Schema):
    id: str
    name: str
    isin: str | None = None
    symbol: str | None = None
    class_: NetWorthClass = Field(alias="class")
    units_at_as_of: Dec
    spam: bool = False


class ExFigure(Schema):
    type: FigureType
    value: str


class ExScenario(Schema):
    name: str
    note: str


class ExProjection(Schema):
    until: YearMonth
    total: Money
    change: Money
    assumption_part: Money
    value_change: ValueChange


class ExInputs(Schema):
    mode: AdviceMode
    as_of: date
    accounts: list[ExAccount]
    plans: list[ExPlan]
    assets: list[ExAsset]
    figures: dict[str, ExFigure | None]
    scenario: ExScenario
    reserve: LtReserve | None = None
    window_months: int | None = None
    bands: list[BandLimits] | None = None
    lights: dict[MarkedFigure, LightColour] | None = None
    projection: ExProjection | None = None

    @model_validator(mode="after")
    def _keys(self) -> Self:
        for key in self.figures:
            if not known_figure_key(key):
                raise ValueError(f"{key}: not a figure key of §8.1")
        return self


class ExFactExpect(Schema):
    key: str
    type: FigureType
    value: str


class ExFactsExpect(Schema):
    accounts: dict[str, AccountKind] | None = None
    plans: dict[str, PlanClass] | None = None
    assets: dict[str, NetWorthClass] | None = None
    labels: ExplainLabels | None = None
    never_in_request: list[str] = Field(default_factory=list)
    figure_keys: list[str] | None = None
    figures_include: list[ExFactExpect] = Field(default_factory=list)
    absent: list[str] = Field(default_factory=list)
    states: dict[str, str] = Field(default_factory=dict)
    states_absent: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _closed(self) -> Self:
        for name, value in self.states.items():
            if name not in STATES or value not in STATES[name]:
                raise ValueError(f"state {name} = {value} is not in the closed list")
        for name in self.states_absent:
            if name not in STATES:
                raise ValueError(f"{name}: not a state")
        for key in [
            *(self.figure_keys or []),
            *self.absent,
            *(f.key for f in self.figures_include),
        ]:
            if not known_figure_key(key):
                raise ValueError(f"{key}: not a figure key of §8.1")
        return self


class ExFactsCase(Lenient):
    pending: Pending | None = None  # None once its step has landed
    inputs: ExInputs
    expect: ExFactsExpect


class ExplainFactsFixture(Schema):
    bases: dict[str, Any]
    cases: list[ExFactsCase]


class ErRender(Schema):
    text: str
    expect: str


class ErCase(Lenient):
    pending: Pending | None = None  # None once its step has landed
    facts: ExplainFacts | None = None
    labels: ExplainLabels | None = None
    renders: list[ErRender] = Field(default_factory=list)
    raises: list[str] = Field(default_factory=list)
    states: dict[str, str] = Field(default_factory=dict)
    sentence: str | None = None
    expect_sentences: list[str] = Field(default_factory=list)
    expect_no_slots: list[Literal["f", "k", "p", "c", "a"]] = Field(default_factory=list)
    expect_rendered_contains: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _closed(self) -> Self:
        for name, value in self.states.items():
            if name not in STATES or value not in STATES[name]:
                raise ValueError(f"{self.id}: state {name} = {value} is not in the closed list")
        accept = {c.id for c in load("explain_validator").accept}
        unknown = [s for s in self.expect_sentences if s not in accept]
        if unknown:
            raise ValueError(f"{self.id}: {unknown} are not must-accept texts")
        return self


class ExplainRenderFixture(Schema):
    facts: ExplainFacts
    labels: ExplainLabels
    cases: list[ErCase]


# --- explain_validator.yaml --------------------------------------------------


class EvFacts(Schema):
    mode: AdviceMode
    as_of: date
    accounts: dict[str, AccountKind]
    plans: dict[str, PlanClass]
    assets: dict[str, NetWorthClass]
    figures: list[str]
    states: dict[str, str]

    @model_validator(mode="after")
    def _closed(self) -> Self:
        unknown = [k for k in self.figures if not known_figure_key(k)]
        if unknown:
            raise ValueError(f"{unknown}: not figure keys of §8.1")
        for name, value in self.states.items():
            if name not in STATES or value not in STATES[name]:
                raise ValueError(f"state {name} = {value} is not in the closed list")
        return self


class EvAccept(Lenient):
    pending: Pending | None = None  # None once its step has landed
    topic: Topic | None = None
    mode: AdviceMode | None = None
    states: dict[str, str] = Field(default_factory=dict)
    text: str
    from_bands: bool = False


class EvParagraph(Schema):
    topic: Topic
    text: str


class EvReject(Lenient):
    pending: Pending | None = None  # None once its step has landed
    topic: Topic | None = None
    mode: AdviceMode | None = None
    states: dict[str, str] = Field(default_factory=dict)
    text: str | None = None
    paragraphs: list[EvParagraph] | None = None
    repeat: int | None = Field(default=None, ge=2)
    rejects: ViolationCode | list[ViolationCode]

    @model_validator(mode="after")
    def _text(self) -> Self:
        if (self.text is None) == (self.paragraphs is None):
            raise ValueError(f"{self.id}: a text or paragraphs")
        return self


def slots_outside(text: str, facts: EvFacts, mode: AdviceMode) -> list[str]:
    """The slots of a text its facts or its mode do not hold (check 2's grammar)."""
    rules = advice_rules()
    allowed = set(rules["slots_by_mode"][mode.value])
    held = {
        "f": set(facts.figures),
        "k": set(facts.accounts),
        "p": set(facts.plans),
        "a": set(facts.assets),
        "c": {c.value for c in NetWorthClass},
    }
    bad = []
    for match in re.finditer(rules["slot_pattern"], text):
        letter, key = match.groups()
        if letter not in allowed or key not in held[letter]:
            bad.append(match.group(0))
    return bad


class ExplainValidatorFixture(Schema):
    facts: EvFacts
    formerly_held: list[str]
    extra_lexicon: list[str]
    accept: list[EvAccept]
    reject: list[EvReject]

    @model_validator(mode="after")
    def _accepted_slots(self) -> Self:
        for case in self.accept:
            for name, value in case.states.items():
                if name not in STATES or value not in STATES[name]:
                    raise ValueError(f"{case.id}: state {name} = {value} is not closed")
            mode = case.mode or self.facts.mode
            bad = slots_outside(case.text, self.facts, mode)
            if bad:
                raise ValueError(f"{case.id}: slots its facts do not hold: {bad}")
        for key in self.formerly_held:
            if key in self.facts.assets:
                raise ValueError(f"{key}: a formerly held key is in no fact")
        return self


# --- instrument_names.yaml ---------------------------------------------------


class InCarrier(Schema):
    id: str
    transform: Literal[
        "plain", "lower", "upper", "spaced", "hyphenated", "compound", "zero_width",
        "lookalike", "dollar", "braces",
    ]  # fmt: skip
    sentence: str = Field(pattern="<NAME>")


class InstrumentNamesFixture(Schema):
    pending: Pending | None = None  # None once its step has landed
    carriers: list[InCarrier] = Field(min_length=11)
    names: dict[str, list[str]]

    @model_validator(mode="after")
    def _names(self) -> Self:
        every = [n for names in self.names.values() for n in names]
        if len(every) != len(set(every)):
            raise ValueError("a name once")
        if "Zephyron" in every:
            raise ValueError("Zephyron (§15.2) is in no list")
        return self


# --- spending_play.yaml and spending_comparison.yaml (D263, §7.6, §7.7) -------
#
# The reference below is written once more from the spec, independent of rules/plan/spending.py:
# the codes a change may name and the refusals of §7.6 (1), the per-code means of (2) with the
# reading that apportions the cents by largest remainder, Δ and the month function of (3), the
# rate of (4) on the window's exact sums, and §7.7's comparison with the scenario in force.

_SP_SPENT = (Relevance.SPENDING, Relevance.TAX)
_SP_UNLISTED = (AllocationCategory.BANK_FEES, AllocationCategory.UNCATEGORIZED)
BERLIN = ZoneInfo("Europe/Berlin")


def sp_group(code: AllocationCategory) -> HouseholdGroup:
    """The group the Haushalt screen shows the code in (bank fees, uncategorised: Sonstiges)."""
    row = TAXONOMY.get(code)
    return HouseholdGroup.OTHER if row is None else row.group


def sp_counted(code: AllocationCategory) -> bool:
    """Counted in a month's regular spending: spending or tax relevance, never one-off."""
    if code in _SP_UNLISTED:
        return True
    row = TAXONOMY.get(code)
    return row is not None and row.relevance in _SP_SPENT and not row.one_off


def sp_playable(code: AllocationCategory) -> bool:
    """§7.6 (1): a household spending code a change may name (not bank fees, uncategorised,
    a one-off code, a refund, an income or a transfer code)."""
    return code in TAXONOMY and sp_counted(code) and code is not AllocationCategory.HH_REFUND


def sp_members(code: AllocationCategory) -> set[AllocationCategory]:
    """A group's fallback code stands for the whole group; any other code for itself."""
    if code not in FALLBACK_CODES:
        return {code}
    group = TAXONOMY[code].group
    every = [*TAXONOMY, *_SP_UNLISTED]
    return {c for c in every if sp_counted(c) and sp_group(c) is group}


class SpChange(Schema):
    code: AllocationCategory
    monthly: NonNegativeMoney | None = None
    percent: Dec | None = None
    from_month: YearMonth
    to_month: YearMonth | None = None
    mean_at_save: Money | None = None

    def in_force(self, month: str) -> bool:
        return self.from_month <= month and (self.to_month is None or month <= self.to_month)


def sp_refusal(changes: list[dict[str, Any]]) -> str | None:
    """§7.6 (1) on the changes as typed; the percent-on-zero refusal needs the means."""
    parsed = []
    for raw_change in changes:
        try:
            change = SpChange.model_validate(raw_change)
        except ValidationError:
            return "shape"
        if (change.monthly is None) == (change.percent is None):
            return "one of monthly and percent"
        if change.percent is not None and not (
            Decimal("-1") <= change.percent <= Decimal("2")
            and change.percent == change.percent.quantize(CENT)
        ):
            return "percent bounds"
        if change.to_month is not None and change.to_month < change.from_month:
            return "order"
        if not sp_playable(change.code):
            return "code"
        parsed.append(change)
    for a in parsed:
        for b in parsed:
            if a is b:
                continue
            if a.code in FALLBACK_CODES and b.code != a.code and b.code in sp_members(a.code):
                return "fallback beside its subcategory"
            if a.code == b.code:
                a_end = a.to_month or "9999-12"
                b_end = b.to_month or "9999-12"
                if a.from_month <= b_end and b.from_month <= a_end:
                    return "overlap"
    return None


def sp_means(
    window_spending: list[dict[AllocationCategory, Decimal]],
) -> dict[AllocationCategory, Decimal]:
    """§7.6 (2): each code's mean over the window, the cents apportioned by largest remainder
    (ties: the code's value, ascending), so the means sum to mean_regular_spending exactly."""
    n = len(window_spending)
    sums: dict[AllocationCategory, Fraction] = {}
    for month in window_spending:
        for code, amount in month.items():
            if sp_counted(code):
                sums[code] = sums.get(code, Fraction(0)) + Fraction(amount)
    exact = {c: v * 100 / n for c, v in sums.items()}
    total = sum(exact.values(), Fraction(0))
    target = int(cent(total / 100) * 100)
    floors = {c: v.numerator // v.denominator for c, v in exact.items()}
    left = target - sum(floors.values())
    order = sorted(exact, key=lambda c: (-(exact[c] - floors[c]), c.value))
    for c in order[:left]:
        floors[c] += 1
    return {c: Decimal(v) / 100 for c, v in floors.items()}


def sp_mean_of(code: AllocationCategory, means: dict[AllocationCategory, Decimal]) -> Decimal:
    return sum((means.get(c, ZERO) for c in sp_members(code)), ZERO)


def sp_planned(change: SpChange, mean: Decimal) -> Decimal:
    if change.monthly is not None:
        return change.monthly
    assert change.percent is not None
    return cent(mean * (1 + change.percent))


class SpWindowMonth(Schema):
    month: YearMonth
    income: Money  # the private accounts' income
    rental_result: Money  # R
    rental_cash: Money  # C
    spending: dict[AllocationCategory, Money]  # by code, positive; one-off codes included


class SpMonthExpect(Schema):
    month: YearMonth
    delta: Money
    free: Money
    buffer: Money
    investable: Money
    rate: Ratio | None


class SpSegment(Schema):
    from_month: YearMonth
    to_month: YearMonth
    rate: Ratio | None


class SpPlanned(Schema):
    code: AllocationCategory
    from_month: YearMonth
    mean: Money
    planned: Money
    delta: Money


class SpExpectPlay(Schema):
    mean_regular_spending: Money
    means: dict[AllocationCategory, Money]
    rate_regular_now: Ratio | None
    months: list[SpMonthExpect]
    rates: list[SpSegment]
    planned: list[SpPlanned] = Field(default_factory=list)


class SpPlayCase(Lenient):
    pending: Pending | None = None  # None once its step has landed
    window: list[SpWindowMonth] = Field(default_factory=list, max_length=12)
    buffer_percent: BufferPercent = Decimal("10")
    horizon_months: int = Field(default=6, ge=1, le=120)
    income_changes: list[IncomeChange] = Field(default_factory=list)
    one_offs: list[OneOff] = Field(default_factory=list)
    changes: list[dict[str, JsonValue]] = Field(default_factory=list)
    expect: SpExpectPlay | None = None
    refused: list[list[dict[str, JsonValue]]] = Field(default_factory=list)
    accepted: list[list[dict[str, JsonValue]]] = Field(default_factory=list)
    # Refused once the means are known: a percent on a code with mean 0 (§7.6 (1)).
    refused_with_means: list[list[dict[str, JsonValue]]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _recompute(self) -> Self:
        for changes in self.accepted:
            if (why := sp_refusal(changes)) is not None:
                raise ValueError(f"{self.id}: {changes} must be accepted, refused for {why}")
        for changes in self.refused:
            if sp_refusal(changes) is None:
                raise ValueError(f"{self.id}: {changes} must be refused")
        spending = [{c: v for c, v in m.spending.items() if c not in ONE_OFF} for m in self.window]
        means = sp_means(spending) if self.window else {}
        for changes in self.refused_with_means:
            if sp_refusal(changes) is not None:
                raise ValueError(f"{self.id}: {changes} must pass the shape checks")
            parsed = [SpChange.model_validate(c) for c in changes]
            if not any(c.percent is not None and sp_mean_of(c.code, means) == 0 for c in parsed):
                raise ValueError(f"{self.id}: {changes} names no percent on a mean of 0")
        if self.expect is None:
            if not (self.refused or self.accepted or self.refused_with_means):
                raise ValueError(f"{self.id}: nothing to check")
            return self
        if (why := sp_refusal(self.changes)) is not None:
            raise ValueError(f"{self.id}: the changes are refused ({why})")
        changes = [SpChange.model_validate(c) for c in self.changes]
        n = len(self.window)
        regular = [sum(m.values(), ZERO) for m in spending]
        mean_regular = mean_cent(regular)
        check(mean_regular, self.expect.mean_regular_spending, f"{self.id}: mean regular")
        check(sum(means.values(), ZERO), mean_regular, f"{self.id}: Σ means")
        check(means, self.expect.means, f"{self.id}: means")
        sum_e = sum((m.income + m.rental_result for m in self.window), ZERO)
        sum_a = sum(regular, ZERO)
        check(ratio4(sum_e - sum_a, sum_e), self.expect.rate_regular_now, f"{self.id}: now")
        mean_income = mean_cent([m.income for m in self.window])
        mean_cash = mean_cent([m.rental_cash for m in self.window])
        newest = self.window[-1].month
        got_months = []
        for k in range(1, self.horizon_months + 1):
            ym = add_months(newest, k)
            delta = sum(
                (
                    sp_planned(c, sp_mean_of(c.code, means)) - sp_mean_of(c.code, means)
                    for c in changes
                    if c.in_force(ym)
                ),
                ZERO,
            )
            inc = sum(
                (
                    i.monthly_net
                    for i in self.income_changes
                    if i.from_month <= ym and (i.to_month is None or ym <= i.to_month)
                ),
                ZERO,
            )
            once = sum((o.amount for o in self.one_offs if o.month == ym), ZERO)
            free = mean_income - mean_regular - delta + mean_cash + inc + once
            buffer = cent(free * self.buffer_percent / 100) if free > 0 else ZERO
            investable = free - buffer if free > 0 else free
            e_k = sum_e + n * inc
            rate = ratio4(e_k - sum_a - n * delta, e_k)
            got_months.append(
                SpMonthExpect(
                    month=ym, delta=delta, free=free, buffer=buffer, investable=investable,
                    rate=rate,
                )
            )  # fmt: skip
        want = {m.month: m for m in self.expect.months}
        if not want.keys() <= {m.month for m in got_months}:
            raise ValueError(f"{self.id}: a month outside the horizon")
        for got in got_months:
            if got.month in want:
                check(got, want[got.month], f"{self.id}: {got.month}")
        segments: list[SpSegment] = []
        for m in got_months:
            if segments and segments[-1].rate == m.rate:
                segments[-1] = segments[-1].model_copy(update={"to_month": m.month})
            else:
                segments.append(SpSegment(from_month=m.month, to_month=m.month, rate=m.rate))
        check(segments, self.expect.rates, f"{self.id}: rates")
        planned = []
        for c in changes:
            mean = sp_mean_of(c.code, means)
            p = sp_planned(c, mean)
            planned.append(
                SpPlanned(
                    code=c.code, from_month=c.from_month, mean=mean, planned=p, delta=p - mean
                )
            )
        check(planned, self.expect.planned, f"{self.id}: planned")
        return self


class SpendingPlayFixture(Schema):
    bases: dict[str, Any]
    cases: list[SpPlayCase]


def sp_words(difference: Decimal) -> tuple[str, str]:
    """§7.7's words: „<Betrag> mehr/weniger als geplant“, „wie geplant“ under 1 €."""
    if abs(difference) < 1:
        return "as_planned", "wie geplant"
    whole = int(abs(difference).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    amount = f"{whole:,}".replace(",", ".")
    if difference > 0:
        return "more", f"{amount} € mehr als geplant"
    return "less", f"{amount} € weniger als geplant"


class CpLine(Schema):
    key: str
    code: AllocationCategory
    amount: Money  # signed, as booked: spending negative


class CpRow(Schema):
    code: AllocationCategory
    planned: Money
    actual: Money
    difference: Money
    words: Literal["more", "less", "as_planned"]
    text: str
    sources: list[str]


class CpExpect(Schema):
    final: bool
    open_lines: int = Field(ge=0)
    rows: list[CpRow]


class CpScenario(Schema):
    key: str
    created_at: datetime
    is_default: bool = True
    supersedes: str | None = None
    withdrawn: bool = False


def cp_in_force(rows: list[CpScenario], day: date) -> str | None:
    """§7.7: the default household scenario in force on `day` (Berlin): created on or before
    it, not withdrawn, not superseded by a row created on or before it; the newest first."""
    known = [r for r in rows if r.created_at.astimezone(BERLIN).date() <= day]
    replaced = {r.supersedes for r in known if r.supersedes is not None}
    live = [r for r in known if r.is_default and not r.withdrawn and r.key not in replaced]
    live.sort(key=lambda r: r.created_at, reverse=True)
    return live[0].key if live else None


class CpCase(Lenient):
    pending: Pending | None = None  # None once its step has landed
    month: YearMonth | None = None
    today: date | None = None
    closed: bool = False
    open_lines: int = Field(default=0, ge=0)
    changes: list[SpChange] = Field(default_factory=list)
    lines: list[CpLine] = Field(default_factory=list)
    expect: CpExpect | None = None
    scenarios: list[CpScenario] = Field(default_factory=list)
    in_force: dict[date, str | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _recompute(self) -> Self:
        for day, key in self.in_force.items():
            check(cp_in_force(self.scenarios, day), key, f"{self.id}: {day}")
        if self.expect is None:
            if not self.in_force:
                raise ValueError(f"{self.id}: nothing to check")
            return self
        if self.month is None or self.today is None:
            raise ValueError(f"{self.id}: a comparison names its month and today")
        if any(c.mean_at_save is None for c in self.changes):
            raise ValueError(f"{self.id}: a stored change carries its mean_at_save")
        running = self.today <= last_day(self.month)
        rows = []
        for c in self.changes:
            if not c.in_force(self.month):
                continue
            assert c.mean_at_save is not None
            planned = sp_planned(c, c.mean_at_save)
            members = sp_members(c.code) - ONE_OFF
            mine = [li for li in self.lines if li.code in members]
            actual = -sum((li.amount for li in mine), ZERO)
            difference = actual - planned
            words, text = sp_words(difference)
            rows.append(
                CpRow(
                    code=c.code, planned=planned, actual=actual, difference=difference,
                    words=words, text=text, sources=[li.key for li in mine],
                )
            )  # fmt: skip
        got = CpExpect(final=self.closed and not running, open_lines=self.open_lines, rows=rows)
        check(got, self.expect, self.id)
        return self


class SpendingComparisonFixture(Schema):
    bases: dict[str, Any] = Field(default_factory=dict)
    cases: list[CpCase]


MODELS: dict[str, type[Schema]] = {
    "closed_month": ClosedMonthFixture,
    "debt": DebtFixture,
    "explain_facts": ExplainFactsFixture,
    "explain_render": ExplainRenderFixture,
    "explain_validator": ExplainValidatorFixture,
    "health_worked": HealthWorkedFixture,
    "household_month": HouseholdMonthFixture,
    "instrument_names": InstrumentNamesFixture,
    "lights": LightsFixture,
    "net_worth_projection": NetWorthProjectionFixture,
    "net_worth_series": NetWorthSeriesFixture,
    "paths": PathsFixture,
    "plan_levers": PlanLeversFixture,
    "reserve": ReserveFixture,
    "savings_plans": SavingsPlansFixture,
    "savings_rate": SavingsRateFixture,
    "spending_comparison": SpendingComparisonFixture,
    "spending_play": SpendingPlayFixture,
    "surplus": SurplusFixture,
}


@cache
def load(name: str) -> Any:
    return MODELS[name].model_validate(raw(name))
