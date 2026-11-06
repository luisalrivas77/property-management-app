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
from pydantic import Field, JsonValue, ValidationError, model_validator

from hausbuch.schemas.ai import CitedTextBlock, ExtractedValue, SourceRef
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
    SplitMethod,
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
from hausbuch.schemas.enums import DuplicateStatus, ExtractionKind
from hausbuch.schemas.extraction import EXTRACTION_MODELS, _Typing
from hausbuch.schemas.proposal import PrefillContext, RecordEntity

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
            rule = self.rules[case.rule]
            if case.expect and case.expect.rule_id != rule.id:
                raise ValueError(f"{case.id}: rule_id is not the rule's id")
            single = rule.split_method is SplitMethod.SINGLE_CATEGORY
            if (
                case.expect
                and single
                and any(a.recurring != rule.recurring for a in case.expect.allocations)
            ):
                raise ValueError(f"{case.id}: a single_category hit takes rule.recurring")
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
        r, w, a, f = self.expect.result, self.expect.sums, self.anchor.balance, self.file
        if (r.computed_opening, r.computed_closing) != (a + w.stored_before, a + w.stored_through):
            raise ValueError(f"{self.id}: computed balances do not follow from the sums")
        if r.error_code == "statement.before_anchor":
            if (r.difference, r.opening_difference) != (None, None):
                raise ValueError(f"{self.id}: a file before the anchor has no differences")
            return self
        closing = None if f.declared_closing is None else f.declared_closing - r.computed_closing
        opening = None if f.file_opening is None else f.file_opening - r.computed_opening
        if (r.difference, r.opening_difference) != (closing, opening):
            raise ValueError(f"{self.id}: differences do not follow from the balances")
        if opening:
            code = "statement.opening_mismatch"
        elif closing:
            code = "statement.unreconciled"
        else:
            code = None
        if r.error_code != code:
            raise ValueError(f"{self.id}: error_code should be {code}")
        return self


class ReconcileFixture(Schema):
    anchor: Anchor
    cases: list[ReconcileCase]


# --- extraction_parse.yaml -------------------------------------------------------------


def _extraction_model(kind: ExtractionKind | None) -> type[_Typing]:
    return _Typing if kind is None else EXTRACTION_MODELS[kind]


class ParseCase(Lenient):
    kind: ExtractionKind
    page_offset: int = Field(default=0, ge=0)
    blocks: list[CitedTextBlock] = Field(min_length=1)
    expect: dict[str, ExtractedValue]

    @model_validator(mode="after")
    def _known_fields(self) -> Self:
        unknown = set(self.expect) - set(_extraction_model(self.kind).model_fields)
        if unknown:
            raise ValueError(f"{self.id}: not fields of {self.kind}: {sorted(unknown)}")
        return self


class ParseFixture(Schema):
    cases: list[ParseCase]


# --- prefill.yaml ----------------------------------------------------------------------


class Cited(Schema):
    """Shorthand for an input value that passed every check: {v, p, q}."""

    v: JsonValue
    p: int = Field(ge=1)
    q: str = Field(min_length=1)

    def full(self) -> ExtractedValue:
        return ExtractedValue(
            value=self.v,
            source=SourceRef(page=self.p, quote=self.q),
            checks=["cited", "value_in_quote", "validates"],
            confidence="high",
        )


class RecordExpect(Schema):
    key: str = Field(min_length=1)
    entity_type: RecordEntity
    mode: Literal["create", "patch", "correction", "unchanged"]
    target_id: UUID | None = None
    values: dict[str, JsonValue]
    derived: dict[str, list[str]] = Field(default_factory=dict)
    current: dict[str, JsonValue] | None = None
    notes_contain: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.mode != "create" and self.target_id is None:
            raise ValueError(f"{self.key}: mode {self.mode} needs target_id")
        if self.current is not None and self.target_id is None:
            raise ValueError(f"{self.key}: current is only set for an existing record")
        if not set(self.derived) <= set(self.values):
            raise ValueError(f"{self.key}: a derived field must be in values")
        return self


class PrefillExpect(Schema):
    keys: list[str] = Field(min_length=1)
    records: list[RecordExpect]

    @model_validator(mode="after")
    def _keys(self) -> Self:
        detailed = [r.key for r in self.records]
        if len(set(self.keys)) != len(self.keys) or len(set(detailed)) != len(detailed):
            raise ValueError("record keys must be unique")
        if not set(detailed) <= set(self.keys):
            raise ValueError(f"records not in keys: {sorted(set(detailed) - set(self.keys))}")
        return self


class PrefillCase(Lenient):
    kind: ExtractionKind | None
    context: PrefillContext
    extraction: dict[str, Cited | ExtractedValue]
    expect: PrefillExpect

    def extraction_model(self) -> _Typing:
        values = {k: v.full() if isinstance(v, Cited) else v for k, v in self.extraction.items()}
        return _extraction_model(self.kind).model_validate(values)

    @model_validator(mode="after")
    def _valid_extraction(self) -> Self:
        self.extraction_model()
        if self.kind is not None and self.kind is ExtractionKind.MANUAL:
            raise ValueError(f"{self.id}: manual extractions are never pre-filled")
        return self


class PrefillFixture(Schema):
    ids: dict[str, str]
    shared: dict[str, Any]
    cases: list[PrefillCase]


# --- month_statement.yaml (done criterion, §13) -----------------------------------------


class Balances(Schema):
    anchor: Money
    anchor_on: date
    first_booking_on: date
    last_booking_on: date
    declared_closing: Money


class AllocationBrief(Schema):
    category: AllocationCategory
    amount: Money


class LineRouting(Schema):
    decision: RoutingDecision
    reason: str
    method: AllocationMethod | None

    @model_validator(mode="after")
    def _no_set_no_method(self) -> Self:
        if (self.method is None) != (self.reason == "routing.no_match"):
            raise ValueError("method is null exactly when nothing matched")
        return self


class LineRoutingWithSplit(LineRouting):
    allocations: list[AllocationBrief]

    @model_validator(mode="after")
    def _split_iff_set(self) -> Self:
        if (self.method is None) != (not self.allocations):
            raise ValueError("allocations exactly when a set matched")
        return self


class MonthLine(Lenient):
    transaction: MatchTransaction
    first_month: LineRouting
    second_month: LineRoutingWithSplit

    @model_validator(mode="after")
    def _sums(self) -> Self:
        split = self.second_month.allocations
        if split and sum(a.amount for a in split) != self.transaction.amount:
            raise ValueError(f"{self.id}: the second month's split does not sum to the line")
        return self


class MonthExpect(Schema):
    reconciled: bool
    first_month_auto: int = Field(ge=0)
    second_month_auto: int = Field(ge=0)


class MonthStatementFixture(Schema):
    ids: dict[str, str]
    balances: Balances
    context: MatchContext
    rules: list[MatchingRule]
    lines: list[MonthLine] = Field(min_length=12, max_length=12)
    expect: MonthExpect

    @model_validator(mode="after")
    def _done_criterion(self) -> Self:
        auto = RoutingDecision.AUTO_CONFIRM
        first = sum(line.first_month.decision is auto for line in self.lines)
        second = sum(line.second_month.decision is auto for line in self.lines)
        if (first, second) != (self.expect.first_month_auto, self.expect.second_month_auto):
            raise ValueError(f"auto counts are {first} / {second}, not as expected")
        if second * 5 < len(self.lines) * 4:
            raise ValueError("the second month must auto-match at least 80 % of its lines")
        dates = [line.transaction.booking_date for line in self.lines]
        b = self.balances
        if (min(dates), max(dates)) != (b.first_booking_on, b.last_booking_on):
            raise ValueError("first/last booking dates do not match the lines")
        net = sum(line.transaction.amount for line in self.lines)
        if self.expect.reconciled and b.anchor + net != b.declared_closing:
            raise ValueError("the declared closing is not anchor + the month's lines")
        return self


# --- repeat_answer.yaml (done criterion, §13) ------------------------------------------


class Answered(Schema):
    transaction: MatchTransaction
    confirmed: AllocationSet


class NextLine(Schema):
    transaction: MatchTransaction
    expect: SetExpect


class RepeatCase(Lenient):
    answered: Answered
    expect_rule: MatchingRuleCreate
    rule_id: UUID
    next: NextLine

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        a, n = self.answered, self.next
        if a.confirmed.transaction_id != a.transaction.id:
            raise ValueError(f"{self.id}: the answer is for another transaction")
        if sum(x.amount for x in a.confirmed.allocations) != a.transaction.amount:
            raise ValueError(f"{self.id}: the answer does not sum to its line")
        _sums(n.expect, n.transaction, self.id)
        if self.expect_rule.recurring != all(x.recurring for x in a.confirmed.allocations):
            raise ValueError(f"{self.id}: the rule's recurring is copied from the answer")
        if n.expect.rule_id != self.rule_id:
            raise ValueError(f"{self.id}: the next line must hit the stored rule")
        return self


class RepeatFixture(Schema):
    ids: dict[str, str]
    context: MatchContext
    cases: list[RepeatCase]


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
    "extraction_parse": ParseFixture,
    "prefill": PrefillFixture,
    "month_statement": MonthStatementFixture,
    "repeat_answer": RepeatFixture,
}


@cache
def load(name: str) -> Schema:
    return MODELS[name].model_validate(raw(name))
