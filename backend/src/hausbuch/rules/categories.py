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
        # The landlord's own Mietverwaltung (Sondereigentumsverwaltung) fee: Werbungskosten
        # when paid, like the WEG administrator's share inside the Hausgeld (D51).
        C.PROPERTY_MANAGEMENT: T.DEDUCTIBLE,
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


# The German label of each category, as the UI shows it (frontend components/labels.ts,
# ALLOCATION_CATEGORY; keep the two equal). Used where the server writes a question that
# names a category (rules.matching.rule_answer, D49); a label never decides a figure.
CATEGORY_LABEL = MappingProxyType(
    {
        C.RENT_COLD: "Kaltmiete",
        C.OPERATING_COSTS_PREPAYMENT: "Nebenkostenvorauszahlung",
        C.OPERATING_COSTS_SETTLEMENT: "Nebenkostenabrechnung (Nachzahlung oder Erstattung)",
        C.DEPOSIT_IN: "Kaution erhalten",
        C.DEPOSIT_OUT: "Kaution zurückgezahlt",
        C.OWNER_TRANSFER_IN: "Einlage von einem Eigentümer",
        C.OWNER_TRANSFER_OUT: "Entnahme an einen Eigentümer",
        C.LOAN_INTEREST: "Darlehenszinsen",
        C.LOAN_PRINCIPAL: "Tilgung",
        C.LOAN_FEES: "Geldbeschaffungskosten",
        C.LOAN_DISBURSEMENT: "Darlehensauszahlung",
        C.HAUSGELD_UMLAGEFAEHIG: "Hausgeld, umlagefähig",
        C.HAUSGELD_VERWALTUNG: "Hausgeld, nicht umlagefähig (Verwaltung)",
        C.HAUSGELD_RUECKLAGE: "Hausgeld, Erhaltungsrücklage",
        C.PROPERTY_MANAGEMENT: "Mietverwaltung (Sondereigentumsverwaltung)",
        C.GRUNDSTEUER: "Grundsteuer",
        C.INSURANCE: "Versicherung",
        C.REPAIRS: "Erhaltungsaufwand (Reparatur)",
        C.RENOVATION: "Renovierung oder Modernisierung",
        C.PURCHASE_PRICE: "Kaufpreiszahlung",
        C.ACQUISITION_COST: "Anschaffungsnebenkosten",
        C.PROFESSIONAL_FEES: "Beratungs- und Rechtskosten",
        C.BANK_FEES: "Kontoführung und Bankgebühren",
        C.OTHER_INCOME: "Sonstige Einnahme",
        C.OTHER_EXPENSE: "Sonstige Ausgabe",
        C.UNCATEGORIZED: "Noch offen (nicht zugeordnet)",
    }
)

if set(CATEGORY_LABEL) != set(C):  # pragma: no cover - a new category without a label
    raise RuntimeError("every category needs a German label")
