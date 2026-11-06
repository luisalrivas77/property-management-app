"""Allocations, learned rules and the matching pipeline's inputs (phase-2.md §4.4, §8).

An allocation splits one transaction into category + scope + tax year + service period. Rows
are append-only (D23): a correction is a new row set, and status is the one thing that moves,
forward only. Every tax and performance figure sums over `confirmed` rows. Σ amounts of a
transaction's non-superseded rows equals the transaction amount; rules.allocations.check_sum
enforces it on every write (the database cannot check a sum across rows).

Categories are a fixed taxonomy (D24); their tax treatment is rule data
(rules.categories.tax_treatment, fixture categories.yaml), never a column the user edits.
Learned rules store a matcher and a split method, never an amount (D25).
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from .ai import ConfidenceLevel
from .common import Confidence, Money, MutableRecord, PositiveMoney, Record, Schema, TaxYear
from .enums import AllocationStatus, ExtractionKind, ImportStatus
from .loan import LoanTerms

HmacHex = str  # 64 lower-case hex characters: an IBAN's keyed HMAC, as the rules see it
HMAC_PATTERN = r"^[0-9a-f]{64}$"


class AllocationCategory(StrEnum):
    """§8.2. Default tax treatment in the comment (rules/categories.py is the source)."""

    RENT_COLD = "rent_cold"  # income
    OPERATING_COSTS_PREPAYMENT = "operating_costs_prepayment"  # income
    OPERATING_COSTS_SETTLEMENT = "operating_costs_settlement"  # income; negative = refund
    DEPOSIT_IN = "deposit_in"  # pass_through
    DEPOSIT_OUT = "deposit_out"  # pass_through
    OWNER_TRANSFER_IN = "owner_transfer_in"  # pass_through
    OWNER_TRANSFER_OUT = "owner_transfer_out"  # pass_through
    LOAN_INTEREST = "loan_interest"  # deductible
    LOAN_PRINCIPAL = "loan_principal"  # not_deductible
    LOAN_FEES = "loan_fees"  # deductible (Geldbeschaffungskosten)
    HAUSGELD_UMLAGEFAEHIG = "hausgeld_umlagefaehig"  # deductible
    HAUSGELD_VERWALTUNG = "hausgeld_verwaltung"  # deductible
    HAUSGELD_RUECKLAGE = "hausgeld_ruecklage"  # undecided until phase 3
    GRUNDSTEUER = "grundsteuer"  # deductible
    INSURANCE = "insurance"  # deductible
    REPAIRS = "repairs"  # deductible (Erhaltungsaufwand)
    RENOVATION = "renovation"  # undecided (15 % rule, phase 3)
    ACQUISITION_COST = "acquisition_cost"  # capitalized
    PROFESSIONAL_FEES = "professional_fees"  # deductible
    BANK_FEES = "bank_fees"  # deductible
    OTHER_INCOME = "other_income"  # income
    OTHER_EXPENSE = "other_expense"  # undecided
    UNCATEGORIZED = "uncategorized"  # undecided


class TaxTreatment(StrEnum):
    INCOME = "income"
    DEDUCTIBLE = "deductible"
    NOT_DEDUCTIBLE = "not_deductible"
    CAPITALIZED = "capitalized"
    PASS_THROUGH = "pass_through"
    UNDECIDED = "undecided"


class AllocationMethod(StrEnum):
    """allocation.method (migration 0007): who proposed the allocation set."""

    RULE = "rule"
    MATCHER_RENT = "matcher_rent"
    MATCHER_LOAN = "matcher_loan"
    MATCHER_OWNER = "matcher_owner"
    MATCHER_HAUSGELD = "matcher_hausgeld"
    DOCUMENT_MATCH = "document_match"
    LLM = "llm"
    USER = "user"


class SplitMethod(StrEnum):
    """matching_rule.split_method (D25): how a rule splits, never how much."""

    SINGLE_CATEGORY = "single_category"
    BY_RENT_SCHEDULE = "by_rent_schedule"
    BY_LOAN_SCHEDULE = "by_loan_schedule"
    BY_WIRTSCHAFTSPLAN = "by_wirtschaftsplan"
    OWNER_TRANSFER = "owner_transfer"


# --- Allocations ---------------------------------------------------------------------


class AllocationDraft(Schema):
    """What a matcher, a rule, the model or the user proposes for part of a transaction.
    amount is signed like the transaction (a debit's parts are negative)."""

    amount: Money
    category: AllocationCategory
    property_id: UUID | None = None
    unit_id: UUID | None = None
    tenancy_id: UUID | None = None
    loan_id: UUID | None = None
    tax_year: TaxYear
    service_period_start: date | None = None
    service_period_end: date | None = None
    recurring: bool = False
    document_id: UUID | None = None  # the Beleg

    @model_validator(mode="after")
    def _period(self) -> Self:
        if (self.service_period_start is None) != (self.service_period_end is None):
            raise ValueError("a service period has both a start and an end, or neither")
        if self.service_period_start and self.service_period_end < self.service_period_start:  # type: ignore[operator]
            raise ValueError("service_period_end must not be before service_period_start")
        return self


class AllocationSet(Schema):
    """One transaction's proposal. Σ amount must equal the transaction amount: checked by
    rules.allocations.check_sum, which knows the transaction (allocation_sum.yaml)."""

    transaction_id: UUID
    allocations: list[AllocationDraft] = Field(min_length=1)
    method: AllocationMethod
    confidence: Confidence
    reasoning: str | None = None
    question: str | None = None  # the one concrete question for the review queue
    rule_id: UUID | None = None
    ai_call_id: UUID | None = None

    @model_validator(mode="after")
    def _provenance(self) -> Self:
        if (self.method is AllocationMethod.RULE) != (self.rule_id is not None):
            raise ValueError("rule_id exactly when method is rule")
        if self.ai_call_id is not None and self.method is not AllocationMethod.LLM:
            raise ValueError("ai_call_id only on an llm allocation set")
        return self


class Allocation(Record):
    """Read shape of one allocation row (migration 0007)."""

    transaction_id: UUID
    amount: Money
    category: AllocationCategory
    property_id: UUID | None
    unit_id: UUID | None
    tenancy_id: UUID | None
    loan_id: UUID | None
    tax_year: TaxYear
    service_period_start: date | None
    service_period_end: date | None
    recurring: bool
    method: AllocationMethod
    confidence: Confidence
    status: AllocationStatus
    supersedes_id: UUID | None
    rule_id: UUID | None
    ai_call_id: UUID | None
    document_id: UUID | None
    reasoning: str | None
    question: str | None
    recorded_at: datetime
    confirmed_at: datetime | None
    confirmed_by: UUID | None  # null for an auto-confirmation (rule or matcher)


# --- Learned rules (D25) -------------------------------------------------------------


class RuleMatcher(Schema):
    """What a rule matches on. No money field, by design: a rule never stores an amount.
    Patterns are case-insensitive regular expressions searched in the normalized text."""

    counterparty_iban_hmac: HmacHex | None = Field(default=None, pattern=HMAC_PATTERN)
    counterparty_name_pattern: str | None = None
    purpose_pattern: str | None = None
    amount_sign: Literal["credit", "debit"] | None = None

    @model_validator(mode="after")
    def _matches_something(self) -> Self:
        if not (
            self.counterparty_iban_hmac or self.counterparty_name_pattern or self.purpose_pattern
        ):
            raise ValueError("a rule matches on an IBAN hash, a name pattern or a purpose pattern")
        return self


class MatchingRuleCreate(Schema):
    matcher: RuleMatcher
    split_method: SplitMethod
    category: AllocationCategory | None = None  # single_category only
    property_id: UUID | None = None
    tenancy_id: UUID | None = None
    loan_id: UUID | None = None
    # Copied from the confirmed answer; a single_category hit's allocation gets it (the
    # schedule-based split methods set recurring themselves).
    recurring: bool = False
    created_from_allocation_id: UUID | None = None

    @model_validator(mode="after")
    def _scope(self) -> Self:
        m = self.split_method
        if (m is SplitMethod.SINGLE_CATEGORY) != (self.category is not None):
            raise ValueError("category exactly for split_method single_category")
        if m is SplitMethod.BY_RENT_SCHEDULE and self.tenancy_id is None:
            raise ValueError("by_rent_schedule needs tenancy_id")
        if m is SplitMethod.BY_LOAN_SCHEDULE and self.loan_id is None:
            raise ValueError("by_loan_schedule needs loan_id")
        if m is SplitMethod.BY_WIRTSCHAFTSPLAN and self.property_id is None:
            raise ValueError("by_wirtschaftsplan needs property_id")
        return self


class MatchingRule(MatchingRuleCreate, MutableRecord):
    enabled: bool = True
    hit_count: int = Field(default=0, ge=0)


# --- The categorize job's structured output (§5.3) ------------------------------------


class LlmAllocation(Schema):
    category: AllocationCategory
    amount: Money  # a decimal string, signed like the transaction
    service_period: str | None = None  # as text; rules.periods parses it
    recurring: bool


class LlmMatchOutput(Schema):
    allocations: list[LlmAllocation] = Field(min_length=1)
    confidence: ConfidenceLevel  # mapped by code to 0.9 / 0.6 / 0.3
    reasoning: str
    question: str | None
    ambiguous: bool  # true -> one retry on the hard model (D17)


# --- Inputs of rules.matching and rules.periods -----------------------------------------


class ServicePeriod(Schema):
    start: date
    end: date

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.end < self.start:
            raise ValueError("end must not be before start")
        return self


class MatchTransaction(Schema):
    """A stored transaction as the matchers see it (no plaintext IBAN)."""

    id: UUID
    booking_date: date
    value_date: date | None = None
    amount: Money
    counterparty_name: str | None = None
    counterparty_iban_hmac: HmacHex | None = Field(default=None, pattern=HMAC_PATTERN)
    purpose: str = ""
    booking_text: str | None = None


class RentInForce(Schema):
    """A rent_schedule row as the rent matcher reads it: corrections already resolved."""

    effective_from: date
    cold_rent: Money
    operating_costs_prepayment: Money


class TenancyFacts(Schema):
    id: UUID
    property_id: UUID
    unit_id: UUID
    start_date: date
    end_date: date | None = None
    tenant_iban_hmacs: list[HmacHex] = Field(default_factory=list)
    rent: list[RentInForce] = Field(min_length=1)


class LoanFacts(Schema):
    id: UUID
    property_id: UUID
    lender: str
    account_iban_hmac: HmacHex | None = Field(default=None, pattern=HMAC_PATTERN)
    terms: LoanTerms


class DuePayment(Schema):
    due_on: date
    amount: PositiveMoney


class DocumentFacts(Schema):
    """An invoice or Bescheid extraction as the document matcher reads it. payments: an
    invoice's total on its document date; a Grundsteuerbescheid's installments on their
    due dates. category: chosen by the service from the kind and links (a Nebenkosten
    invoice -> acquisition_cost, a Grundsteuerbescheid -> grundsteuer)."""

    id: UUID
    kind: ExtractionKind
    category: AllocationCategory
    property_id: UUID | None = None
    payments: list[DuePayment] = Field(min_length=1)
    iban_hmac: HmacHex | None = Field(default=None, pattern=HMAC_PATTERN)
    reference: str | None = None


class WirtschaftsplanFacts(Schema):
    """A Wirtschaftsplan extraction's monthly Hausgeld split (§8.1, by_wirtschaftsplan)."""

    document_id: UUID
    property_id: UUID
    valid_from: date
    total: PositiveMoney
    umlagefaehig: Money
    verwaltung: Money
    ruecklage: Money

    @model_validator(mode="after")
    def _sums(self) -> Self:
        if self.umlagefaehig + self.verwaltung + self.ruecklage != self.total:
            raise ValueError("the Hausgeld parts must sum to the total")
        return self


class MatchContext(Schema):
    """Everything a matcher or rule may look at. Lists are unordered."""

    owner_account_hmacs: list[HmacHex] = Field(default_factory=list)
    tenancies: list[TenancyFacts] = Field(default_factory=list)
    loans: list[LoanFacts] = Field(default_factory=list)
    documents: list[DocumentFacts] = Field(default_factory=list)
    wirtschaftsplaene: list[WirtschaftsplanFacts] = Field(default_factory=list)


# --- Review routing (rules.review.route) ----------------------------------------------


class RoutingDecision(StrEnum):
    AUTO_CONFIRM = "auto_confirm"
    REVIEW = "review"  # the line goes to the inbox with its question
    IMPORT_REVIEW = "import_review"  # the whole import goes to review; the line is not matched


class RoutingInput(Schema):
    """One line's situation. allocation_set null = nothing matched it."""

    import_status: ImportStatus
    auto_accept_llm: bool = False  # workspace setting (D26)
    allocation_set: AllocationSet | None = None


class Routing(Schema):
    decision: RoutingDecision
    reason: str  # a routing.* code (review_routing.yaml)
