"""Shapes of the phase 2 YAML rule fixtures (docs/phases/phase-2.md §12, §13).

Same contract as fixture_models.py: loading a fixture through these models is itself a test
(tests/test_fixtures_valid.py), so a fixture that does not match the spec schemas fails before
any rule is implemented.
"""

from datetime import date
from functools import cache
from pathlib import Path
from typing import Any, Literal, Self
from uuid import UUID

import yaml
from fixture_models import Lenient
from pydantic import Field, ValidationError, model_validator

from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
    MatchContext,
    MatchingRule,
    MatchingRuleCreate,
    MatchTransaction,
    Routing,
    RoutingDecision,
    RoutingInput,
    ServicePeriod,
    TaxTreatment,
)
from hausbuch.schemas.bank import (
    LineForHash,
    ReconcileResult,
    StatementFormat,
    StoredLine,
    WindowSums,
)
from hausbuch.schemas.common import Confidence, Money, Schema, Sha256Hex
from hausbuch.schemas.enums import DuplicateStatus

FIXTURES = Path(__file__).parent / "fixtures" / "phase2"


@cache
def raw(name: str) -> dict:
    with (FIXTURES / f"{name}.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _every_category_once(categories: list[AllocationCategory], what: str) -> None:
    missing = set(AllocationCategory) - set(categories)
    if missing or len(categories) != len(set(categories)):
        raise ValueError(f"{what}: every category exactly once (missing {sorted(missing)})")


# --- categories.yaml -------------------------------------------------------------------


class CategoryRow(Schema):
    category: AllocationCategory
    treatment: TaxTreatment


class CategoriesFixture(Schema):
    categories: list[CategoryRow]

    @model_validator(mode="after")
    def _complete(self) -> Self:
        _every_category_once([r.category for r in self.categories], "categories.yaml")
        return self


# --- thresholds.yaml -------------------------------------------------------------------


class ThresholdRow(Schema):
    category: AllocationCategory
    threshold: Confidence | None


class ThresholdsFixture(Schema):
    thresholds: list[ThresholdRow]

    @model_validator(mode="after")
    def _complete(self) -> Self:
        _every_category_once([r.category for r in self.thresholds], "thresholds.yaml")
        return self

    def table(self) -> dict[AllocationCategory, Confidence | None]:
        return {r.category: r.threshold for r in self.thresholds}


# --- allocation_sum.yaml ---------------------------------------------------------------


class SumExpect(Schema):
    error: Literal["allocation.sum_mismatch", "allocation.empty"] | None


class SumCase(Lenient):
    transaction_amount: Money
    allocations: list[AllocationDraft]
    expect: SumExpect


class SumFixture(Schema):
    cases: list[SumCase]


# --- service_period.yaml ---------------------------------------------------------------


class ServicePeriodCase(Lenient):
    purpose: str
    booking_date: date
    recurring: bool
    expect: ServicePeriod | None


class ServicePeriodFixture(Schema):
    cases: list[ServicePeriodCase] = Field(min_length=1)


# --- matchers.yaml, rules.yaml ---------------------------------------------------------


class SetExpect(Schema):
    """An expected AllocationSet without transaction_id (the case's transaction) and without
    reasoning (free text, not compared). Allocations are compared as a set."""

    method: AllocationMethod
    confidence: Confidence
    question: str | None = None
    rule_id: UUID | None = None
    allocations: list[AllocationDraft] = Field(min_length=1)

    def check(self, got: AllocationSet | None, transaction: MatchTransaction) -> None:
        assert got is not None, "expected an allocation set, got none"
        assert got.transaction_id == transaction.id
        assert (got.method, got.confidence, got.question, got.rule_id) == (
            self.method,
            self.confidence,
            self.question,
            self.rule_id,
        )
        key = lambda a: a.model_dump_json()  # noqa: E731
        assert sorted(got.allocations, key=key) == sorted(self.allocations, key=key)
        assert sum(a.amount for a in self.allocations) == transaction.amount


def _sums(expect: SetExpect | None, transaction: MatchTransaction, case_id: str) -> None:
    if expect is not None and sum(a.amount for a in expect.allocations) != transaction.amount:
        raise ValueError(f"{case_id}: expected allocations do not sum to the transaction")


class MatcherCase(Lenient):
    matcher: Literal["owner_transfer", "rent", "loan", "document"]
    transaction: MatchTransaction
    expect: SetExpect | None

    @model_validator(mode="after")
    def _sum(self) -> Self:
        _sums(self.expect, self.transaction, self.id)
        return self


class MatchersFixture(Schema):
    ids: dict[str, str]
    context: MatchContext
    cases: list[MatcherCase]


class RuleCase(Lenient):
    rule: str
    transaction: MatchTransaction
    expect: SetExpect | None

    @model_validator(mode="after")
    def _sum(self) -> Self:
        _sums(self.expect, self.transaction, self.id)
        return self


class RejectedRule(Lenient):
    rule: dict[str, Any]

    @model_validator(mode="after")
    def _rejected(self) -> Self:
        try:
            MatchingRuleCreate.model_validate(self.rule)
        except ValidationError:
            return self
        raise ValueError(f"{self.id}: a rule that stores an amount must not validate")


class RulesFixture(Schema):
    ids: dict[str, str]
    context: MatchContext
    rules: dict[str, MatchingRule]
    cases: list[RuleCase]
    rejected: list[RejectedRule] = Field(min_length=1)

    @model_validator(mode="after")
    def _known_rules(self) -> Self:
        for case in self.cases:
            if case.rule not in self.rules:
                raise ValueError(f"{case.id}: unknown rule {case.rule}")
            if case.expect and case.expect.rule_id != self.rules[case.rule].id:
                raise ValueError(f"{case.id}: rule_id is not the rule's id")
        return self


# --- review_routing.yaml ---------------------------------------------------------------


class CategoryRouting(Schema):
    category: AllocationCategory
    decision: Literal[RoutingDecision.AUTO_CONFIRM, RoutingDecision.REVIEW]


class RoutingCase(Lenient):
    item: RoutingInput
    expect: Routing


class RoutingFixture(Schema):
    tx: UUID
    rule_id: UUID
    per_category: list[CategoryRouting]
    cases: list[RoutingCase]

    @model_validator(mode="after")
    def _complete(self) -> Self:
        _every_category_once([r.category for r in self.per_category], "review_routing.yaml")
        return self


# --- line_hash.yaml --------------------------------------------------------------------


class LineHashCase(Lenient):
    files: list[list[LineForHash]] = Field(min_length=1)
    expect: list[list[Sha256Hex]]

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if [len(f) for f in self.files] != [len(h) for h in self.expect]:
            raise ValueError(f"{self.id}: one pinned hash per line of every file")
        return self


class LineHashFixture(Schema):
    account_id: UUID
    cases: list[LineHashCase]


# --- duplicates.yaml -------------------------------------------------------------------


class LikelyCase(Lenient):
    new_line: StoredLine
    existing: list[StoredLine]
    expect: list[UUID]


class Exclusion(Schema):
    excluded_id: UUID
    excluded_as_duplicate_of: UUID


class ResolveCase(Lenient):
    transaction_id: UUID
    other_transaction_id: UUID
    status: Literal[DuplicateStatus.KEPT_BOTH, DuplicateStatus.MERGED]
    expect: Exclusion | None


class DuplicatesFixture(Schema):
    ids: dict[str, str]
    likely: list[LikelyCase]
    resolve: list[ResolveCase]


# --- reconcile.yaml --------------------------------------------------------------------


class Anchor(Schema):
    balance: Money
    on: date


class BalanceLine(Schema):
    id: UUID
    booking_date: date
    amount: Money
    excluded_as_duplicate_of: UUID | None = None

    def stored(self) -> StoredLine:
        """The StoredLine the rule reads; account, format and hash do not affect the sums."""
        return StoredLine(
            id=self.id,
            account_id=UUID(int=0),
            format=StatementFormat.CSV_CAMT,
            line_hash=self.id.hex * 2,
            booking_date=self.booking_date,
            amount=self.amount,
            excluded_as_duplicate_of=self.excluded_as_duplicate_of,
        )


class FileWindow(Schema):
    first_booking_on: date
    last_booking_on: date
    declared_closing: Money | None
    file_opening: Money | None


class ReconcileExpect(Schema):
    sums: WindowSums
    result: ReconcileResult


class ReconcileCase(Lenient):
    anchor: Anchor
    lines: list[BalanceLine]
    file: FileWindow
    expect: ReconcileExpect

    @model_validator(mode="after")
    def _arithmetic(self) -> Self:
        """The expected computed balances follow from the expected sums."""
        r, w, a = self.expect.result, self.expect.sums, self.anchor.balance
        if (r.computed_opening, r.computed_closing) != (a + w.stored_before, a + w.stored_through):
            raise ValueError(f"{self.id}: computed balances do not follow from the sums")
        return self


class ReconcileFixture(Schema):
    anchor: Anchor
    cases: list[ReconcileCase]


MODELS: dict[str, type[Schema]] = {
    "categories": CategoriesFixture,
    "thresholds": ThresholdsFixture,
    "allocation_sum": SumFixture,
    "service_period": ServicePeriodFixture,
    "matchers": MatchersFixture,
    "rules": RulesFixture,
    "review_routing": RoutingFixture,
    "line_hash": LineHashFixture,
    "duplicates": DuplicatesFixture,
    "reconcile": ReconcileFixture,
}


@cache
def load(name: str) -> Schema:
    return MODELS[name].model_validate(raw(name))
