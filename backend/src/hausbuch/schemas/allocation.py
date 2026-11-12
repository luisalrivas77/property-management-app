"""Allocations, learned rules and the matching pipeline's inputs (phase-2.md §4.4, §8).

An allocation splits one transaction into category + scope + service period + recurring. Rows
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
from .common import Confidence, Money, MutableRecord, PositiveMoney, Record, Schema
from .enums import AccountKind, AccountPurpose, AllocationStatus, ExtractionKind, ImportStatus
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
    LOAN_DISBURSEMENT = "loan_disbursement"  # pass_through (the loan's payout, borrowed money)
    HAUSGELD_UMLAGEFAEHIG = "hausgeld_umlagefaehig"  # deductible
    HAUSGELD_VERWALTUNG = "hausgeld_verwaltung"  # deductible
    HAUSGELD_RUECKLAGE = "hausgeld_ruecklage"  # undecided until phase 3
    # deductible: the landlord's own Mietverwaltung (Sondereigentumsverwaltung) fee, not the
    # WEG administrator's share inside the Hausgeld (D51)
    PROPERTY_MANAGEMENT = "property_management"
    GRUNDSTEUER = "grundsteuer"  # deductible
    INSURANCE = "insurance"  # deductible
    # deductible: the flat's own electricity bill or Abschlag, paid by the landlord because the
    # rent's Pauschale covers it; the Hausgeld's Allgemeinstrom stays hausgeld_umlagefaehig
    # (phase 4 D92)
    ELECTRICITY = "electricity"
    REPAIRS = "repairs"  # deductible (Erhaltungsaufwand)
    RENOVATION = "renovation"  # undecided (15 % rule, phase 3)
    PURCHASE_PRICE = "purchase_price"  # capitalized (via the property record, not summed)
    ACQUISITION_COST = "acquisition_cost"  # capitalized
    PROFESSIONAL_FEES = "professional_fees"  # deductible
    BANK_FEES = "bank_fees"  # deductible
    OTHER_INCOME = "other_income"  # income
    OTHER_EXPENSE = "other_expense"  # undecided
    UNCATEGORIZED = "uncategorized"  # undecided
    # --- Phase 6 D159: the household taxonomy (migration 0025). Allowed on a `private`
    # account only, never on the rental one (rules.household.taxonomy.allowed_categories);
    # group, relevance and the input each proposes live in rules/household/taxonomy.py.
    # Income
    HH_SALARY = "hh_salary"  # private
    HH_CHILD_BENEFIT = "hh_child_benefit"  # private
    HH_TAX_REFUND = "hh_tax_refund"  # private
    HH_CAPITAL_INCOME = "hh_capital_income"  # private (Zinsen, Dividenden; read in phase 8)
    HH_OTHER_INCOME = "hh_other_income"  # private
    # Spending, the groups of mockup 10
    HH_HOUSING = "hh_housing"  # private
    HH_GROCERIES = "hh_groceries"  # private
    HH_FAMILY_CHILD = "hh_family_child"  # private
    HH_MOBILITY = "hh_mobility"  # private
    HH_INSURANCE = "hh_insurance"  # private
    HH_SUBSCRIPTIONS = "hh_subscriptions"  # private
    HH_LEISURE_TRAVEL = "hh_leisure_travel"  # private
    HH_HEALTH = "hh_health"  # private
    HH_OTHER = "hh_other"  # private
    # Tax-relevant spending: each a proposal into the input the form already reads (D161),
    # never summed by a form itself
    HH_DONATION = "hh_donation"  # pass_through (-> return_input special_expense)
    HH_HOUSEHOLD_SERVICES = "hh_household_services"  # pass_through (-> household_services)
    HH_CHILDCARE = "hh_childcare"  # pass_through (-> return_input childcare_cost)
    HH_PROPERTY_COST = "hh_property_cost"  # pass_through (-> tax_input private_cost, D161)
    HH_TAX_PAYMENT = "hh_tax_payment"  # private (phase 4b D95: not modelled, nothing proposed)
    # Movements
    INTERNAL_TRANSFER_IN = "internal_transfer_in"  # pass_through (D162: the matcher books it)
    INTERNAL_TRANSFER_OUT = "internal_transfer_out"  # pass_through
    HH_CASH = "hh_cash"  # private (Bargeldabhebung)
    HH_CREDIT_CARD = "hh_credit_card"  # private (a card's monthly settlement)
    # --- Phase 6 D165: the depot's own cash bookings, by booking text in code (rules.depot);
    # not hh_* because phase 8's KAP rules read distribution and capital_income_tax.
    SECURITY_PURCHASE = "security_purchase"  # private
    SECURITY_SALE = "security_sale"  # private
    DISTRIBUTION = "distribution"  # private
    CAPITAL_INCOME_TAX = "capital_income_tax"  # private (Vorabpauschale, KapSt, Soli)
    DEPOT_FEE = "depot_fee"  # private (Verwaltergebühr)


class TaxTreatment(StrEnum):
    INCOME = "income"
    DEDUCTIBLE = "deductible"
    NOT_DEDUCTIBLE = "not_deductible"
    CAPITALIZED = "capitalized"
    PASS_THROUGH = "pass_through"
    UNDECIDED = "undecided"
    # Phase 6 D159 (Q-O67): outside every form. rules/tax never reads such a row; a new value
    # rather than pass_through so that invariant can be tested.
    PRIVATE = "private"


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
    # Phase 6 D162 (migration 0025): the transfer matcher over every own account.
    MATCHER_TRANSFER = "matcher_transfer"


class SplitMethod(StrEnum):
    """matching_rule.split_method (D25): how a rule splits, never how much."""

    SINGLE_CATEGORY = "single_category"
    BY_RENT_SCHEDULE = "by_rent_schedule"
    BY_LOAN_SCHEDULE = "by_loan_schedule"
    BY_WIRTSCHAFTSPLAN = "by_wirtschaftsplan"
    OWNER_TRANSFER = "owner_transfer"
    # D51: the parts of the latest confirmed line of the rule's category set, copied when the
    # amount is the same (read from the ledger; the rule stores the set, never an amount).
    AS_LAST_SPLIT = "as_last_split"


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


_MATCHES_NOTHING = "a rule matches on an IBAN hash, a name pattern or a purpose pattern"


class RuleMatcher(Schema):
    """What a rule matches on. No money field, by design: a rule never stores an amount.
    Patterns are case-insensitive regular expressions searched in the normalized text."""

    # A new or enabled rule must match on something (MatchingRuleCreate, MatchingRule); only
    # a disabled rule may hold an empty matcher, the one a tenant erasure leaves (phase-2.md
    # D42 (3), migration 0008).

    counterparty_iban_hmac: HmacHex | None = Field(default=None, pattern=HMAC_PATTERN)
    counterparty_name_pattern: str | None = None
    purpose_pattern: str | None = None
    amount_sign: Literal["credit", "debit"] | None = None

    @property
    def matches_something(self) -> bool:
        return bool(
            self.counterparty_iban_hmac or self.counterparty_name_pattern or self.purpose_pattern
        )


class MatchingRuleCreate(Schema):
    matcher: RuleMatcher
    split_method: SplitMethod
    category: AllocationCategory | None = None  # single_category only
    property_id: UUID | None = None
    tenancy_id: UUID | None = None
    loan_id: UUID | None = None
    # as_last_split only (D51): the answer's distinct categories, sorted, two or more; the
    # rule's kind, never an amount.
    categories: list[AllocationCategory] | None = None
    # Copied from the confirmed answer; a single_category hit's allocation gets it (the
    # schedule-based split methods set recurring themselves, as_last_split copies each part's).
    recurring: bool = False
    created_from_allocation_id: UUID | None = None
    # D49: "only payments of this amount". A flag, never an amount: a hit stands only when
    # the line's amount equals the latest confirmed line of the rule's category from the
    # same counterparty (read from the ledger at match time), else it is a proposal. Inert
    # on split methods that check the amount themselves (rent, loan, Wirtschaftsplan).
    same_amount_only: bool = False

    @model_validator(mode="after")
    def _matcher(self) -> Self:
        if not self.matcher.matches_something:
            raise ValueError(_MATCHES_NOTHING)
        return self

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
        if (m is SplitMethod.AS_LAST_SPLIT) != (self.categories is not None):
            raise ValueError("categories exactly for split_method as_last_split")
        if self.categories is not None and (
            len(self.categories) < 2 or self.categories != sorted(set(self.categories))
        ):
            raise ValueError("categories: two or more distinct categories, sorted")
        return self


class MatchingRule(MatchingRuleCreate, MutableRecord):
    enabled: bool = True
    hit_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _matcher(self) -> Self:  # replaces MatchingRuleCreate's: erased rules are empty
        if self.enabled and not self.matcher.matches_something:
            raise ValueError(_MATCHES_NOTHING)
        return self


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


class DocumentPart(Schema):
    """One part of a bill sorted by its cost lines (D135): a category and its gross amount."""

    category: AllocationCategory
    amount: PositiveMoney


class DocumentFacts(Schema):
    """An invoice or Bescheid extraction as the document matcher reads it. payments: an
    invoice's total on its document date; a Grundsteuerbescheid's installments on their
    due dates. category: chosen by the service from the kind and links (a Nebenkosten
    invoice -> acquisition_cost, a Grundsteuerbescheid -> grundsteuer, a financing invoice
    (D35) -> loan_fees with the loan it is linked to as loan_id)."""

    id: UUID
    kind: ExtractionKind
    category: AllocationCategory
    property_id: UUID | None = None
    loan_id: UUID | None = None
    payments: list[DuePayment] = Field(min_length=1)
    iban_hmac: HmacHex | None = Field(default=None, pattern=HMAC_PATTERN)
    reference: str | None = None
    # Phase 2 D135: the bill's parts by kind of cost (rules.prefill.sort_bill), gross, summing
    # to the total; empty when the bill is booked whole.
    parts: list[DocumentPart] = Field(default_factory=list)


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


class TransferLegSource(StrEnum):
    """Which table a transfer leg is a row of (phase 6 D162)."""

    TRANSACTION = "transaction"  # a bank or depot cash line
    MOVEMENT = "movement"  # an asset_movement row (an exchange's EUR deposit or withdrawal)


class TransferLeg(Schema):
    """One side of a possible transfer between two own accounts, as rules.matching.
    match_transfer reads it (phase 6 D162): an unlinked line or asset movement of another own
    account. amount is in EUR and signed (an out leg is negative); fee_eur is the fee the
    exchange or depot recorded, which a link within the fee tolerates."""

    source: TransferLegSource
    id: UUID
    account_id: UUID
    account_kind: AccountKind
    booked_on: date
    amount: Money
    fee_eur: Money | None = None
    counterparty_iban_hmac: HmacHex | None = Field(default=None, pattern=HMAC_PATTERN)


class MatchContext(Schema):
    """Everything a matcher or rule may look at. Lists are unordered.

    Phase 6 D162: own_account_hmacs replaces phase 2's owner_account_hmacs, which listed only
    the owner accounts. Step 0 fills it with the same accounts as before (the private ones);
    the line's own account excluded and every other account included is step 1-2's work.
    account_purpose: the purpose of the line's account (D158); rental unless stated, so every
    phase 2-5 caller and fixture keeps its meaning. open_legs: the other own accounts' unlinked
    legs (D162), empty until step 2 fills it."""

    own_account_hmacs: list[HmacHex] = Field(default_factory=list)
    tenancies: list[TenancyFacts] = Field(default_factory=list)
    loans: list[LoanFacts] = Field(default_factory=list)
    documents: list[DocumentFacts] = Field(default_factory=list)
    wirtschaftsplaene: list[WirtschaftsplanFacts] = Field(default_factory=list)
    account_purpose: AccountPurpose = AccountPurpose.RENTAL
    open_legs: list[TransferLeg] = Field(default_factory=list)


class HistoryRow(Schema):
    """One allocation row of a history line. The parts (amount and scope) are what an
    as_last_split rule copies (D51); the D49 check reads only category and status."""

    category: AllocationCategory
    status: AllocationStatus
    amount: Money | None = None  # signed like the line
    property_id: UUID | None = None
    unit_id: UUID | None = None
    tenancy_id: UUID | None = None
    loan_id: UUID | None = None
    recurring: bool = False


class HistoryLine(Schema):
    """Another bank line and its allocation rows, as the learned-rule check reads it (D49):
    what the ledger already says this counterparty's payments were. Only a line from the same
    IBAN hash and of the same sign as the line being matched counts, and only when its
    current rows (superseded ones ignored, D23) are all confirmed; rules.matching filters."""

    transaction_id: UUID
    booking_date: date
    amount: Money
    counterparty_iban_hmac: HmacHex | None = Field(default=None, pattern=HMAC_PATTERN)
    rows: list[HistoryRow] = Field(default_factory=list)


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
