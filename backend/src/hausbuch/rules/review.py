"""Auto-confirm thresholds and review routing (§8.1, §8.3, D21, D26, principle 3;
tests/fixtures/phase2/thresholds.yaml and review_routing.yaml).

THRESHOLDS is rule data: the auto-confirm threshold per category, None = never auto-confirmed
whatever the confidence. It applies to document matches (0.95) and to LLM categorizations
(0.9 / 0.6 / 0.3, only with auto_accept_llm); learned rules and exact deterministic matches
(1.0) auto-confirm without it. The values are the spec's starting guesses (open question 6).

route(item), first rule that applies:
  1. import not reconciled -> import_review (no line of it is matched);
  2. nothing matched -> review, routing.no_match;
  3. a learned rule at 1.0 -> auto_confirm, routing.rule_hit;
  4. a deterministic matcher at 1.0 -> auto_confirm, routing.deterministic;
  5. a rule or matcher below 1.0 (an inexact match) -> review, routing.below_threshold;
  6. llm without auto_accept_llm -> review, routing.llm_not_accepted (D26);
  7. document_match and llm: any category without a threshold -> review, routing.never_auto;
     confidence >= the strictest threshold of the set -> auto_confirm, routing.above_threshold;
     else review, routing.below_threshold.
A user's set is confirmed by the user and never routed: route refuses it.
"""

from decimal import Decimal
from types import MappingProxyType

from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.allocation import (
    AllocationMethod,
    Routing,
    RoutingDecision,
    RoutingInput,
)
from hausbuch.schemas.enums import ImportStatus

RULE_HIT = "routing.rule_hit"
DETERMINISTIC = "routing.deterministic"
ABOVE_THRESHOLD = "routing.above_threshold"
BELOW_THRESHOLD = "routing.below_threshold"
NEVER_AUTO = "routing.never_auto"
LLM_NOT_ACCEPTED = "routing.llm_not_accepted"
NO_MATCH = "routing.no_match"
IMPORT_UNRECONCILED = "routing.import_unreconciled"
IMPORT_PENDING_BALANCE = "routing.import_pending_balance"
IMPORT_FAILED = "routing.import_failed"

EXACT = Decimal("1.000")
_RECURRING = Decimal("0.900")

THRESHOLDS = MappingProxyType(
    {
        # The payments that recur month by month or quarter by quarter with a stable
        # counterparty.
        C.RENT_COLD: _RECURRING,
        C.OPERATING_COSTS_PREPAYMENT: _RECURRING,
        C.OWNER_TRANSFER_IN: _RECURRING,
        C.OWNER_TRANSFER_OUT: _RECURRING,
        C.LOAN_INTEREST: _RECURRING,
        C.LOAN_PRINCIPAL: _RECURRING,
        C.HAUSGELD_UMLAGEFAEHIG: _RECURRING,
        C.HAUSGELD_VERWALTUNG: _RECURRING,
        C.HAUSGELD_RUECKLAGE: _RECURRING,
        C.GRUNDSTEUER: _RECURRING,
        C.INSURANCE: _RECURRING,
        C.BANK_FEES: _RECURRING,
        # One-offs whose treatment needs a person, repairs, renovation, anything capitalized
        # and the catch-alls: never auto-confirmed.
        C.OPERATING_COSTS_SETTLEMENT: None,
        C.DEPOSIT_IN: None,
        C.DEPOSIT_OUT: None,
        C.LOAN_FEES: None,
        C.LOAN_DISBURSEMENT: None,  # the loan's payout, once
        C.REPAIRS: None,
        C.RENOVATION: None,
        C.PURCHASE_PRICE: None,  # once, at the purchase
        C.ACQUISITION_COST: None,
        C.PROFESSIONAL_FEES: None,
        C.OTHER_INCOME: None,
        C.OTHER_EXPENSE: None,
        C.UNCATEGORIZED: None,
    }
)

if set(THRESHOLDS) != set(C):  # pragma: no cover - a new category without a threshold
    raise RuntimeError("every category needs a threshold (None = never auto-confirmed)")

_IMPORT_REASONS = {
    ImportStatus.UNRECONCILED: IMPORT_UNRECONCILED,
    ImportStatus.IMPORTED: IMPORT_PENDING_BALANCE,
    ImportStatus.FAILED: IMPORT_FAILED,
}

_MATCHERS = frozenset(
    {
        AllocationMethod.MATCHER_RENT,
        AllocationMethod.MATCHER_LOAN,
        AllocationMethod.MATCHER_OWNER,
        AllocationMethod.MATCHER_HAUSGELD,
    }
)


def threshold(category: C) -> Decimal | None:
    return THRESHOLDS[category]


def route(item: RoutingInput) -> Routing:
    if item.import_status is not ImportStatus.RECONCILED:
        return Routing(
            decision=RoutingDecision.IMPORT_REVIEW, reason=_IMPORT_REASONS[item.import_status]
        )
    found = item.allocation_set
    if found is None:
        return _review(NO_MATCH)
    method = found.method
    if method is AllocationMethod.USER:
        raise ValueError("a user's allocation set is confirmed by the user, never routed")
    if method is AllocationMethod.RULE or method in _MATCHERS:
        if found.confidence == EXACT:
            reason = RULE_HIT if method is AllocationMethod.RULE else DETERMINISTIC
            return Routing(decision=RoutingDecision.AUTO_CONFIRM, reason=reason)
        return _review(BELOW_THRESHOLD)
    if method is AllocationMethod.LLM and not item.auto_accept_llm:
        return _review(LLM_NOT_ACCEPTED)
    # document_match, and llm with auto_accept_llm: as strict as the strictest category.
    limits = [THRESHOLDS[a.category] for a in found.allocations]
    if any(limit is None for limit in limits):
        return _review(NEVER_AUTO)
    if found.confidence >= max(limits):  # type: ignore[type-var]
        return Routing(decision=RoutingDecision.AUTO_CONFIRM, reason=ABOVE_THRESHOLD)
    return _review(BELOW_THRESHOLD)


def _review(reason: str) -> Routing:
    return Routing(decision=RoutingDecision.REVIEW, reason=reason)
