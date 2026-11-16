"""Auto-confirm thresholds and review routing (§8.1, §8.3, D21, D26, D52, principle 3;
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
  6. llm for a line of 0.00: one allocation (its amount is then 0.00, check_sum having made
     the set sum to the line), no tenancy and no loan, a category outside ZERO_EXCLUDED ->
     auto_confirm, routing.zero_amount, whatever the confidence or auto_accept_llm (D52);
  7. llm without auto_accept_llm -> review, routing.llm_not_accepted (D26);
  8. document_match and llm: any category without a threshold -> review, routing.never_auto;
     confidence >= the strictest threshold of the set -> auto_confirm, routing.above_threshold;
     else review, routing.below_threshold.
A user's set is confirmed by the user and never routed: route refuses it.
"""

from decimal import Decimal
from types import MappingProxyType

from hausbuch.rules.errors import ROUTING_AI_DISABLED
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.allocation import (
    AllocationMethod,
    AllocationSet,
    Routing,
    RoutingDecision,
    RoutingInput,
)
from hausbuch.schemas.chain import GroupKind, GroupRoutingInput
from hausbuch.schemas.enums import AccountPurpose, ImportStatus

RULE_HIT = "routing.rule_hit"
DETERMINISTIC = "routing.deterministic"
ABOVE_THRESHOLD = "routing.above_threshold"
BELOW_THRESHOLD = "routing.below_threshold"
NEVER_AUTO = "routing.never_auto"
LLM_NOT_ACCEPTED = "routing.llm_not_accepted"
ZERO_AMOUNT = "routing.zero_amount"
NO_MATCH = "routing.no_match"
IMPORT_UNRECONCILED = "routing.import_unreconciled"
IMPORT_PENDING_BALANCE = "routing.import_pending_balance"
IMPORT_FAILED = "routing.import_failed"
# Phase 7 (D186 (1)): why a chain movement group waits for a person (route_group).
CHAIN_FIRST_SYNC = "routing.chain_first_sync"
CHAIN_UNPRICED = "routing.chain_unpriced"
CHAIN_NO_DECODER = "routing.chain_no_decoder"
CHAIN_CHANGE_CANDIDATE = "routing.chain_change_candidate"
# Written by the sync itself, never by route_group (phase 7 step 2 core, §15.1): a transaction
# Hausbuch cannot store as movements (an amount beyond asset_movement.quantity's
# numeric(38,18), more decimals than an asset holds, two legs a classifier gave the same
# place) becomes a group without movements and without a question, so the address's history
# goes on. Card: „Hausbuch konnte diese Transaktion nicht speichern: ein Betrag ist größer
# oder feiner, als Hausbuch ihn speichern kann, oder sie ist nicht lesbar aufgeteilt. Die
# übrigen Transaktionen der Adresse sind eingelesen; der Kontostand weicht um diese
# Transaktion ab, bis Hausbuch sie lesen kann.“
CHAIN_UNSTORABLE = "routing.chain_unstorable"

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
        C.PROPERTY_MANAGEMENT: _RECURRING,  # a fixed monthly fee (D51)
        C.GRUNDSTEUER: _RECURRING,
        C.INSURANCE: _RECURRING,
        C.ELECTRICITY: _RECURRING,  # a monthly Abschlag from one supplier (phase 4 D92)
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
        # Phase 6 D159: recurring employers and merchants at the recurring threshold; the
        # tax-relevant codes, the catch-alls and the movements need a person.
        C.HH_SALARY: _RECURRING,
        C.HH_CHILD_BENEFIT: _RECURRING,
        C.HH_HOUSING: _RECURRING,
        C.HH_GROCERIES: _RECURRING,
        C.HH_FAMILY_CHILD: _RECURRING,
        C.HH_MOBILITY: _RECURRING,
        C.HH_INSURANCE: _RECURRING,
        C.HH_SUBSCRIPTIONS: _RECURRING,
        C.HH_LEISURE_TRAVEL: _RECURRING,
        C.HH_HEALTH: _RECURRING,
        C.HH_OTHER: None,  # named in D159's None list, though a spending group
        C.HH_CAPITAL_INCOME: None,
        C.HH_TAX_REFUND: None,
        C.HH_OTHER_INCOME: None,
        C.HH_WAGE_REPLACEMENT: None,  # D159 amended (8) (0037): a return input, a person confirms
        C.HH_DONATION: None,
        C.HH_HOUSEHOLD_SERVICES: None,
        C.HH_CHILDCARE: None,
        C.HH_PROPERTY_COST: None,
        C.HH_TAX_PAYMENT: None,
        C.HH_CREDIT_CARD: None,
        # D159 amended 14 Nov 2026 (Luis could not tick „Künftig immer so zuordnen“ on an ATM
        # withdrawal): 0.9 like the spending groups, so a cash withdrawal can learn a rule.
        C.HH_CASH: _RECURRING,
        # D176 (3): a card's interest and fee lines are booked by code, never by a threshold.
        C.HH_INTEREST_FEES: None,
        # D159 amended 13 Nov 2026: the finer spending codes at 0.9 like their groups; None
        # for money to family (Unterhalt may matter later), work costs (maybe Werbungskosten),
        # an investment and a refund, which a person confirms.
        C.HH_PERSONAL_CARE: _RECURRING,
        C.HH_CLOTHING: _RECURRING,
        C.HH_DINING: _RECURRING,
        C.HH_HOME: _RECURRING,
        C.HH_ELECTRONICS: _RECURRING,
        C.HH_ONLINE_SHOPPING: _RECURRING,
        C.HH_FEES: _RECURRING,
        C.HH_GIFTS: _RECURRING,
        C.HH_FAMILY_TRANSFERS: None,
        C.HH_TELECOM: _RECURRING,
        C.HH_WORK_EDUCATION: None,
        C.HH_SPORT: _RECURRING,
        C.HH_TRAVEL: _RECURRING,
        C.HH_INVESTMENT: None,
        # D159 amended (6) 15 Nov 2026 (migration 0036): none like hh_investment, so the
        # model never books a payment into an investment alone; since D160 amended (5) a
        # learned rule may repeat a person's own answer for a payee (a savings plan).
        C.HH_INVESTMENT_PROPERTY: None,
        C.HH_INVESTMENT_SECURITIES: None,
        C.HH_INVESTMENT_CRYPTO: None,
        C.HH_INVESTMENT_SAVINGS: None,
        C.HH_REFUND: None,
        # D159 amended 14 Nov 2026 (migration 0031): fuel at 0.9 like hh_mobility.
        C.HH_FUEL: _RECURRING,
        # D159 amended 14 Nov 2026 (migration 0032): the subcategories at 0.9 like their
        # groups' codes (hh_postage too, though hh_other, its group's fallback, has none).
        C.HH_STREAMING: _RECURRING,
        C.HH_PAY_TV: _RECURRING,  # D159 amended (4) 14 Nov 2026 (migration 0033), as hh_streaming
        C.HH_MUSIC_AUDIO: _RECURRING,
        C.HH_SOFTWARE: _RECURRING,
        C.HH_CLOUD: _RECURRING,
        C.HH_GAMING: _RECURRING,
        C.HH_NEWS: _RECURRING,
        C.HH_MEMBERSHIPS: _RECURRING,
        C.HH_PUBLIC_TRANSPORT: _RECURRING,
        C.HH_TAXI: _RECURRING,
        C.HH_PARKING: _RECURRING,
        C.HH_CAR_SERVICE: _RECURRING,
        C.HH_CAR_TAX: _RECURRING,
        C.HH_CAR_PURCHASE: _RECURRING,  # D159 amended (7) (migration 0037), as hh_car_tax
        C.HH_SUPERMARKET: _RECURRING,
        C.HH_DRUGSTORE: _RECURRING,
        C.HH_BAKERY: _RECURRING,
        C.HH_RESTAURANT: _RECURRING,
        C.HH_CAFE: _RECURRING,
        C.HH_BAR: _RECURRING,  # D159 amended (5) 15 Nov 2026 (migration 0034), as hh_cafe
        C.HH_SWEETS: _RECURRING,
        C.HH_FAST_FOOD: _RECURRING,
        C.HH_DELIVERY: _RECURRING,
        C.HH_RENT: _RECURRING,
        C.HH_ENERGY: _RECURRING,
        C.HH_BROADCAST_FEE: _RECURRING,
        C.HH_FLAT_SEARCH: _RECURRING,
        C.HH_INS_CAR: _RECURRING,
        C.HH_INS_LIABILITY_CONTENTS: _RECURRING,
        C.HH_INS_HEALTH: _RECURRING,
        C.HH_INS_LIFE: _RECURRING,
        C.HH_PHARMACY: _RECURRING,
        C.HH_DOCTOR: _RECURRING,
        C.HH_DENTAL_OPTICAL: _RECURRING,
        C.HH_OUTINGS: _RECURRING,
        C.HH_BOOKS_MEDIA: _RECURRING,
        C.HH_HOBBIES: _RECURRING,
        C.HH_LODGING: _RECURRING,
        C.HH_FLIGHTS: _RECURRING,
        C.HH_KIDS_GEAR: _RECURRING,
        C.HH_TOYS: _RECURRING,
        C.HH_PREGNANCY_BIRTH: _RECURRING,
        C.HH_MOBILE_PHONE: _RECURRING,
        C.HH_INTERNET: _RECURRING,
        C.HH_POSTAGE: _RECURRING,
        # D162: only the transfer matcher books a transfer, never a model or a threshold.
        C.INTERNAL_TRANSFER_IN: None,
        C.INTERNAL_TRANSFER_OUT: None,
        # D165 (1): the depot's bookings are categorised by code from the booking text.
        C.SECURITY_PURCHASE: None,
        C.SECURITY_SALE: None,
        C.DISTRIBUTION: None,
        C.CAPITAL_INCOME_TAX: None,
        C.DEPOT_FEE: None,
    }
)

if set(THRESHOLDS) != set(C):  # pragma: no cover - a new category without a threshold
    raise RuntimeError("every category needs a threshold (None = never auto-confirmed)")

# D52: a model's 0.00 answer in one of these categories keeps the usual routing: other checks
# read them (the rent or instalment already booked for a period, the matchers' categories,
# D40), or they need a person (deposits, the purchase, the payout, an uncategorized line).
ZERO_EXCLUDED = frozenset(
    {
        C.RENT_COLD,
        C.OPERATING_COSTS_PREPAYMENT,
        C.LOAN_INTEREST,
        C.LOAN_PRINCIPAL,
        C.LOAN_DISBURSEMENT,
        C.HAUSGELD_UMLAGEFAEHIG,
        C.HAUSGELD_VERWALTUNG,
        C.HAUSGELD_RUECKLAGE,
        C.OWNER_TRANSFER_IN,
        C.OWNER_TRANSFER_OUT,
        C.DEPOSIT_IN,
        C.DEPOSIT_OUT,
        C.PURCHASE_PRICE,
        C.UNCATEGORIZED,
        # Phase 6 D159: the transfer codes (the matcher alone books them, D162) and the
        # tax-relevant codes (each proposes an input a person confirms).
        C.INTERNAL_TRANSFER_IN,
        C.INTERNAL_TRANSFER_OUT,
        C.HH_DONATION,
        C.HH_HOUSEHOLD_SERVICES,
        C.HH_CHILDCARE,
        C.HH_PROPERTY_COST,
        C.HH_TAX_PAYMENT,
        # D159 amended (8) (migration 0037): proposes the year's replacement_income input
        C.HH_WAGE_REPLACEMENT,
    }
)

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
        AllocationMethod.MATCHER_TRANSFER,  # phase 6 D162
        AllocationMethod.MATCHER_CARD,  # phase 6 D176 (3)
        AllocationMethod.MATCHER_DEPOT,  # phase 6 step 4, D165 (1)
    }
)


def threshold(category: C) -> Decimal | None:
    return THRESHOLDS[category]


def no_match_reason(purpose: AccountPurpose, ai_private_accounts: bool) -> str:
    """Why a line nothing deterministic explains waits in review without a model answer: on a
    private account with the switch ai_private_accounts off it never reaches the model, so the
    card offers every household code (routing.ai_disabled, phase 6 D158, D168)."""
    if purpose is AccountPurpose.PRIVATE and not ai_private_accounts:
        return ROUTING_AI_DISABLED
    return NO_MATCH


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
    if method is AllocationMethod.LLM and _zero_line(found):
        return Routing(decision=RoutingDecision.AUTO_CONFIRM, reason=ZERO_AMOUNT)
    if method is AllocationMethod.LLM and not item.auto_accept_llm:
        return _review(LLM_NOT_ACCEPTED)
    # document_match, and llm with auto_accept_llm: as strict as the strictest category.
    limits = [THRESHOLDS[a.category] for a in found.allocations]
    if any(limit is None for limit in limits):
        return _review(NEVER_AUTO)
    if found.confidence >= max(limits):  # type: ignore[type-var]
        return Routing(decision=RoutingDecision.AUTO_CONFIRM, reason=ABOVE_THRESHOLD)
    return _review(BELOW_THRESHOLD)


def route_group(item: GroupRoutingInput) -> Routing:
    """D186 (1): a chain movement group confirmed by code or sent to a person; never a model
    answer (there is none on a chain). Fixture: phase 2 review_routing.yaml `chain_cases`;
    the order is stated in the comment above that key (and replayed by
    tests/fixture_models_phase2.route_group_reference). Built in step 2: a failed or spam
    group and a fee alone are booked; a wallet's first sync waits for a person, then a Bitcoin
    output to an unregistered address; a transfer is booked once linked, a wrap once its
    decoder is listed, a swap or reward once listed and priced (a swap only between registry
    assets), a bridge once its pair is learned and linked; everything else waits. A group of a
    fee alone that asks a question (a Solana record it cannot read in full) is never booked as
    „a fee“: it waits for the person's answer like any other card."""
    if item.kind in (GroupKind.FAILED, GroupKind.SPAM) or (item.fee_only and not item.question):
        return Routing(decision=RoutingDecision.AUTO_CONFIRM, reason=DETERMINISTIC)
    auto = Routing(decision=RoutingDecision.AUTO_CONFIRM, reason=DETERMINISTIC)
    if item.first_sync:
        return _review(CHAIN_FIRST_SYNC)
    if item.unregistered_output:
        return _review(CHAIN_CHANGE_CANDIDATE)
    if item.kind in (GroupKind.TRANSFER, GroupKind.EXCHANGE_TRANSFER):
        return auto if item.linked else _review(NO_MATCH)
    if item.kind is GroupKind.WRAP:
        return auto if item.decoder_listed else _review(CHAIN_NO_DECODER)
    if item.kind in (GroupKind.SWAP, GroupKind.REWARD):
        if not item.decoder_listed:
            return _review(CHAIN_NO_DECODER)
        if not item.priced:
            return _review(CHAIN_UNPRICED)
        if item.kind is GroupKind.SWAP and not item.registry_assets:
            return _review(NEVER_AUTO)
        return auto
    if item.kind is GroupKind.BRIDGE and item.pair_learned and item.linked:
        return auto
    return _review(NEVER_AUTO)  # bridge, stake, airdrop, lp, contract_call, unknown


def _zero_line(found: AllocationSet) -> bool:
    """A set of one 0.00 part, unscoped to a tenancy or loan, outside ZERO_EXCLUDED (D52)."""
    if len(found.allocations) != 1:
        return False
    (part,) = found.allocations
    return (
        part.amount == 0
        and part.tenancy_id is None
        and part.loan_id is None
        and part.category not in ZERO_EXCLUDED
    )


def _review(reason: str) -> Routing:
    return Routing(decision=RoutingDecision.REVIEW, reason=reason)
