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

from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Self
from uuid import NAMESPACE_URL, uuid5

import yaml
from fixture_models import Lenient
from pydantic import Field, model_validator

from hausbuch.rules.health.month import cashflow_rows, household_month, rental_month
from hausbuch.rules.household.cashflow import month_cashflow
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
from hausbuch.schemas.health import HealthTodoCode, HouseholdMonth, MonthLine, YearMonth
from hausbuch.schemas.household import BudgetClass

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


def household(month: MonthInput, case_id: str) -> tuple[HouseholdMonth, list[MonthLine]]:
    """Phase 9's household month of one input month (over phase 6's cashflow of its private
    lines, as services.health reads it) and those private lines."""

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
    return household_month(cashflow, blocks, lines=private), private


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
    housing: HousingExpect | None = None
    savings_rate: Dec | None = None  # rules.health.figures.savings_rate(months).rate
    no_shares: bool = False
    todos: list[HealthTodoCode] = Field(default_factory=list)

    @model_validator(mode="after")
    def _partition(self) -> Self:
        if self.income is None:
            if self.lines or self.months:
                raise ValueError("no figure without an income")
            return self
        if sum((li.total for li in self.lines), ZERO) != self.income.total:
            raise ValueError("the lines do not add up to the income (D273)")
        if sum((li.monthly for li in self.lines), ZERO) != self.income.monthly:
            raise ValueError("the lines' means do not add up to the income's (D273)")
        saving = [li for li in self.lines if li.key is BudgetLineKey.SAVING]
        if saving and self.savings_rate != saving[0].share:
            raise ValueError("Sparen ÷ E must be the Sparquote (D273)")
        return self


class BudgetCase(Lenient):
    months: list[MonthInput]
    overrides: dict[AllocationCategory, BudgetClass] = Field(default_factory=dict)
    targets: BudgetTargets = Field(default_factory=BudgetTargets)
    expect: BudgetExpect


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


MODELS: dict[str, type[Schema]] = {
    "budget_partition": BudgetFixture,
    "budget_overrides": BudgetFixture,
    "budget_marks": BudgetFixture,
    "budget_references": ReferencesFixture,
}


def load(name: str) -> Any:
    return MODELS[name].model_validate(raw(name))
