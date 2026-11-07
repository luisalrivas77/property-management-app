"""Default tax treatment per category (§8.2, D24; tests/fixtures/phase2/categories.yaml).

Categories are a fixed taxonomy and their treatment is rule data, never a column the user
edits, so renaming a category never changes a return. Phase 3 adds the Anlage V line per tax
year next to this table; it does not change the treatment.
"""

from types import MappingProxyType

from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.allocation import TaxTreatment as T

TAX_TREATMENT = MappingProxyType(
    {
        C.RENT_COLD: T.INCOME,
        C.OPERATING_COSTS_PREPAYMENT: T.INCOME,
        C.OPERATING_COSTS_SETTLEMENT: T.INCOME,  # negative = refund to the tenant
        C.DEPOSIT_IN: T.PASS_THROUGH,
        C.DEPOSIT_OUT: T.PASS_THROUGH,
        C.OWNER_TRANSFER_IN: T.PASS_THROUGH,
        C.OWNER_TRANSFER_OUT: T.PASS_THROUGH,
        C.LOAN_INTEREST: T.DEDUCTIBLE,
        C.LOAN_PRINCIPAL: T.NOT_DEDUCTIBLE,
        C.LOAN_FEES: T.DEDUCTIBLE,  # Geldbeschaffungskosten (D35)
        # The loan's payout into the account: borrowed money, neither income nor a cost; the
        # principal is on the loan record.
        C.LOAN_DISBURSEMENT: T.PASS_THROUGH,
        C.HAUSGELD_UMLAGEFAEHIG: T.DEDUCTIBLE,
        C.HAUSGELD_VERWALTUNG: T.DEDUCTIBLE,
        C.HAUSGELD_RUECKLAGE: T.UNDECIDED,  # until phase 3 reads the Hausgeldabrechnung
        C.GRUNDSTEUER: T.DEDUCTIBLE,
        C.INSURANCE: T.DEDUCTIBLE,
        C.REPAIRS: T.DEDUCTIBLE,  # Erhaltungsaufwand
        C.RENOVATION: T.UNDECIDED,  # the 15 % rule, phase 3
        # The price paid to the seller (or a notary escrow account). It enters the AfA basis
        # through the property record (purchase price and price split), never as a cost of
        # the year paid: the allocation records that and when it was paid, and phase 3 must
        # not sum it into the basis a second time.
        C.PURCHASE_PRICE: T.CAPITALIZED,
        C.ACQUISITION_COST: T.CAPITALIZED,
        C.PROFESSIONAL_FEES: T.DEDUCTIBLE,
        C.BANK_FEES: T.DEDUCTIBLE,
        C.OTHER_INCOME: T.INCOME,
        C.OTHER_EXPENSE: T.UNDECIDED,
        C.UNCATEGORIZED: T.UNDECIDED,
    }
)

if set(TAX_TREATMENT) != set(C):  # pragma: no cover - a new category without a treatment
    raise RuntimeError("every category needs a tax treatment")


def tax_treatment(category: C) -> T:
    return TAX_TREATMENT[category]
