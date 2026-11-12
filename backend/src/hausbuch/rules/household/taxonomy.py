"""The household taxonomy (docs/phases/phase-6.md D159, D162, D165 (1), §5.3; fixture
tests/fixtures/phase6/household_categories.yaml).

One table: per household code its group (the Haushalt screen's groups, mockup 10), its
relevance (income, spending, transfer, tax) and the input a tax-relevant code proposes. The
label and the tax treatment are rules/categories.py's, the threshold rules/review.py's, so a
code has all six only when all three tables carry it; the completeness checks below and
theirs make a code without one fail at import.

Readings of the spec (named in the fixture's header too):
- D159's headings decide group and relevance: its "Income" codes are group income; its
  "Tax-relevant spending" codes are group tax and relevance tax; its "Movements" are group
  movements, with relevance transfer for the two transfer codes and spending for cash and
  the card settlement (the money leaves the household's tracked accounts to be spent).
- The depot's codes (D165 (1)) are group depot: a purchase or sale swaps cash for units
  (transfer), a distribution is income, the capital income tax is tax and the fee is
  spending.
- `allowed_categories(purpose)` (D159): a private account takes the household codes, the
  transfer codes, the depot codes, bank_fees and uncategorized; the rental account takes
  every other code (phases 2-4), bank_fees and uncategorized included.
"""

from types import MappingProxyType

from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.enums import AccountPurpose
from hausbuch.schemas.household import HouseholdCode, HouseholdGroup, ProposedInput, Relevance

G = HouseholdGroup
R = Relevance
P = ProposedInput


def _code(category: C, group: G, relevance: R, proposes: P | None = None) -> HouseholdCode:
    return HouseholdCode(category=category, group=group, relevance=relevance, proposes=proposes)


TAXONOMY = MappingProxyType(
    {
        row.category: row
        for row in (
            # Income
            _code(C.HH_SALARY, G.INCOME, R.INCOME),
            _code(C.HH_CHILD_BENEFIT, G.INCOME, R.INCOME),
            _code(C.HH_TAX_REFUND, G.INCOME, R.INCOME),
            _code(C.HH_CAPITAL_INCOME, G.INCOME, R.INCOME),  # read by phase 8's KAP
            _code(C.HH_OTHER_INCOME, G.INCOME, R.INCOME),
            # Spending, the groups of mockup 10
            _code(C.HH_HOUSING, G.HOUSING, R.SPENDING),
            _code(C.HH_GROCERIES, G.GROCERIES, R.SPENDING),
            _code(C.HH_FAMILY_CHILD, G.FAMILY_CHILD, R.SPENDING),
            _code(C.HH_MOBILITY, G.MOBILITY, R.SPENDING),
            _code(C.HH_INSURANCE, G.INSURANCE, R.SPENDING),
            _code(C.HH_SUBSCRIPTIONS, G.SUBSCRIPTIONS, R.SPENDING),
            _code(C.HH_LEISURE_TRAVEL, G.LEISURE_TRAVEL, R.SPENDING),
            _code(C.HH_HEALTH, G.HEALTH, R.SPENDING),
            _code(C.HH_OTHER, G.OTHER, R.SPENDING),
            _code(C.HH_INTEREST_FEES, G.OTHER, R.SPENDING),  # a card's interest, D176 (3)
            # Tax-relevant spending: each proposes the input its form already reads
            _code(C.HH_DONATION, G.TAX, R.TAX, P.RETURN_SPECIAL_EXPENSE),
            _code(C.HH_HOUSEHOLD_SERVICES, G.TAX, R.TAX, P.RETURN_HOUSEHOLD_SERVICES),
            _code(C.HH_CHILDCARE, G.TAX, R.TAX, P.RETURN_CHILDCARE_COST),
            _code(C.HH_PROPERTY_COST, G.TAX, R.TAX, P.TAX_PRIVATE_COST),  # D161
            _code(C.HH_TAX_PAYMENT, G.TAX, R.TAX),  # not modelled (phase 4b D95)
            # Movements
            _code(C.INTERNAL_TRANSFER_IN, G.MOVEMENTS, R.TRANSFER),
            _code(C.INTERNAL_TRANSFER_OUT, G.MOVEMENTS, R.TRANSFER),
            _code(C.HH_CASH, G.MOVEMENTS, R.SPENDING),
            _code(C.HH_CREDIT_CARD, G.MOVEMENTS, R.SPENDING),
            # The depot's own bookings (D165 (1))
            _code(C.SECURITY_PURCHASE, G.DEPOT, R.TRANSFER),
            _code(C.SECURITY_SALE, G.DEPOT, R.TRANSFER),
            _code(C.DISTRIBUTION, G.DEPOT, R.INCOME),
            _code(C.CAPITAL_INCOME_TAX, G.DEPOT, R.TAX),
            _code(C.DEPOT_FEE, G.DEPOT, R.SPENDING),
        )
    }
)

DEPOT_CODES = frozenset(
    {C.SECURITY_PURCHASE, C.SECURITY_SALE, C.DISTRIBUTION, C.CAPITAL_INCOME_TAX, C.DEPOT_FEE}
)
TRANSFER_CODES = frozenset({C.INTERNAL_TRANSFER_IN, C.INTERNAL_TRANSFER_OUT})
# D176 (3): a card's interest and fee lines, booked by code from the statement (they have no
# payee a rule could learn), never offered to the model, like the depot's codes.
CARD_CODES = frozenset({C.HH_INTEREST_FEES})
HOUSEHOLD_CODES = frozenset(c for c in C if c.value.startswith("hh_"))
# The codes only a private account may carry; they never reach the rental account.
PRIVATE_ONLY = HOUSEHOLD_CODES | TRANSFER_CODES | DEPOT_CODES
# The codes both purposes share (D159: bank_fees is reused for every bank kind).
SHARED = frozenset({C.BANK_FEES, C.UNCATEGORIZED})
# Tax-relevant codes: never auto-confirmed, never a learned name rule (D159, D160).
TAX_RELEVANT = frozenset(c for c, row in TAXONOMY.items() if row.relevance is R.TAX)

if set(TAXONOMY) != PRIVATE_ONLY:  # pragma: no cover - a code without its row
    raise RuntimeError("every household, transfer and depot code needs a taxonomy row")

_ALLOWED = MappingProxyType(
    {
        AccountPurpose.PRIVATE: PRIVATE_ONLY | SHARED,
        AccountPurpose.RENTAL: frozenset(set(C) - PRIVATE_ONLY),
    }
)


def allowed_categories(purpose: AccountPurpose) -> frozenset[C]:
    """The categories an allocation on an account of this purpose may carry (D159); the
    allocation command refuses any other with allocation.category_not_for_account (step 1)."""
    return _ALLOWED[purpose]


def household_code(category: C) -> HouseholdCode:
    """The taxonomy row of a household, transfer or depot code (KeyError for a rental one)."""
    return TAXONOMY[category]
