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
        # The flat's own electricity, paid by the landlord because the rent's Pauschale covers
        # it: Werbungskosten when paid (phase 4 D92).
        C.ELECTRICITY: T.DEDUCTIBLE,
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
        # Phase 6 D159: the household's codes. PRIVATE is ignored by every rental tax rule.
        C.HH_SALARY: T.PRIVATE,
        C.HH_CHILD_BENEFIT: T.PRIVATE,
        C.HH_TAX_REFUND: T.PRIVATE,
        C.HH_CAPITAL_INCOME: T.PRIVATE,  # phase 8's KAP rules read the category
        C.HH_OTHER_INCOME: T.PRIVATE,
        C.HH_HOUSING: T.PRIVATE,
        C.HH_GROCERIES: T.PRIVATE,
        C.HH_FAMILY_CHILD: T.PRIVATE,
        C.HH_MOBILITY: T.PRIVATE,
        C.HH_INSURANCE: T.PRIVATE,
        C.HH_SUBSCRIPTIONS: T.PRIVATE,
        C.HH_LEISURE_TRAVEL: T.PRIVATE,
        C.HH_HEALTH: T.PRIVATE,
        C.HH_OTHER: T.PRIVATE,
        # The tax-relevant spending (D159: "all hh_* codes except the tax-relevant ones get
        # PRIVATE"): each reaches its form only through the input it proposes, so it is
        # pass_through, never summed as a cost; hh_property_cost so by D161.
        C.HH_DONATION: T.PASS_THROUGH,
        C.HH_HOUSEHOLD_SERVICES: T.PASS_THROUGH,
        C.HH_CHILDCARE: T.PASS_THROUGH,
        C.HH_PROPERTY_COST: T.PASS_THROUGH,
        C.HH_TAX_PAYMENT: T.PRIVATE,  # not modelled (phase 4b D95): a label, nothing proposed
        # Movements between the household's own accounts and the cash it takes out (D162).
        C.INTERNAL_TRANSFER_IN: T.PASS_THROUGH,
        C.INTERNAL_TRANSFER_OUT: T.PASS_THROUGH,
        C.HH_CASH: T.PRIVATE,
        C.HH_CREDIT_CARD: T.PRIVATE,
        C.HH_INTEREST_FEES: T.PRIVATE,  # D176 (3)
        # D159 amended 13 Nov 2026 (migration 0029): the finer spending codes.
        C.HH_PERSONAL_CARE: T.PRIVATE,
        C.HH_CLOTHING: T.PRIVATE,
        C.HH_DINING: T.PRIVATE,
        C.HH_HOME: T.PRIVATE,
        C.HH_ELECTRONICS: T.PRIVATE,
        C.HH_ONLINE_SHOPPING: T.PRIVATE,
        C.HH_FEES: T.PRIVATE,
        C.HH_GIFTS: T.PRIVATE,
        C.HH_FAMILY_TRANSFERS: T.PRIVATE,
        C.HH_TELECOM: T.PRIVATE,
        C.HH_WORK_EDUCATION: T.PRIVATE,
        C.HH_SPORT: T.PRIVATE,
        C.HH_TRAVEL: T.PRIVATE,
        C.HH_INVESTMENT: T.PRIVATE,
        C.HH_REFUND: T.PRIVATE,
        C.HH_FUEL: T.PRIVATE,  # D159 amended 14 Nov 2026 (migration 0031)
        # D159 amended 14 Nov 2026 (migration 0032): the subcategories, private like their groups
        C.HH_STREAMING: T.PRIVATE,
        C.HH_MUSIC_AUDIO: T.PRIVATE,
        C.HH_SOFTWARE: T.PRIVATE,
        C.HH_CLOUD: T.PRIVATE,
        C.HH_GAMING: T.PRIVATE,
        C.HH_NEWS: T.PRIVATE,
        C.HH_MEMBERSHIPS: T.PRIVATE,
        C.HH_PUBLIC_TRANSPORT: T.PRIVATE,
        C.HH_TAXI: T.PRIVATE,
        C.HH_PARKING: T.PRIVATE,
        C.HH_CAR_SERVICE: T.PRIVATE,
        C.HH_CAR_TAX: T.PRIVATE,
        C.HH_SUPERMARKET: T.PRIVATE,
        C.HH_DRUGSTORE: T.PRIVATE,
        C.HH_BAKERY: T.PRIVATE,
        C.HH_RESTAURANT: T.PRIVATE,
        C.HH_CAFE: T.PRIVATE,
        C.HH_SWEETS: T.PRIVATE,
        C.HH_FAST_FOOD: T.PRIVATE,
        C.HH_DELIVERY: T.PRIVATE,
        C.HH_RENT: T.PRIVATE,
        C.HH_ENERGY: T.PRIVATE,
        C.HH_BROADCAST_FEE: T.PRIVATE,
        C.HH_FLAT_SEARCH: T.PRIVATE,
        C.HH_INS_CAR: T.PRIVATE,
        C.HH_INS_LIABILITY_CONTENTS: T.PRIVATE,
        C.HH_INS_HEALTH: T.PRIVATE,
        C.HH_INS_LIFE: T.PRIVATE,
        C.HH_PHARMACY: T.PRIVATE,
        C.HH_DOCTOR: T.PRIVATE,
        C.HH_DENTAL_OPTICAL: T.PRIVATE,
        C.HH_OUTINGS: T.PRIVATE,
        C.HH_BOOKS_MEDIA: T.PRIVATE,
        C.HH_HOBBIES: T.PRIVATE,
        C.HH_LODGING: T.PRIVATE,
        C.HH_FLIGHTS: T.PRIVATE,
        C.HH_KIDS_GEAR: T.PRIVATE,
        C.HH_TOYS: T.PRIVATE,
        C.HH_PREGNANCY_BIRTH: T.PRIVATE,
        C.HH_MOBILE_PHONE: T.PRIVATE,
        C.HH_INTERNET: T.PRIVATE,
        C.HH_POSTAGE: T.PRIVATE,
        # The depot's own bookings (D165 (1)); phase 8 reads distribution and
        # capital_income_tax.
        C.SECURITY_PURCHASE: T.PRIVATE,
        C.SECURITY_SALE: T.PRIVATE,
        C.DISTRIBUTION: T.PRIVATE,
        C.CAPITAL_INCOME_TAX: T.PRIVATE,
        C.DEPOT_FEE: T.PRIVATE,
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
        C.ELECTRICITY: "Strom",
        C.REPAIRS: "Erhaltungsaufwand (Reparatur)",
        C.RENOVATION: "Renovierung oder Modernisierung",
        C.PURCHASE_PRICE: "Kaufpreiszahlung",
        C.ACQUISITION_COST: "Anschaffungsnebenkosten",
        C.PROFESSIONAL_FEES: "Beratungs- und Rechtskosten",
        C.BANK_FEES: "Kontoführung und Bankgebühren",
        C.OTHER_INCOME: "Sonstige Einnahme",
        C.OTHER_EXPENSE: "Sonstige Ausgabe",
        C.UNCATEGORIZED: "Noch offen (nicht zugeordnet)",
        # Phase 6 (D159, D162, D165)
        C.HH_SALARY: "Gehalt",
        C.HH_CHILD_BENEFIT: "Kindergeld",
        C.HH_TAX_REFUND: "Steuererstattung",
        C.HH_CAPITAL_INCOME: "Zinsen und Dividenden",
        C.HH_OTHER_INCOME: "Sonstige Einnahme (privat)",
        C.HH_HOUSING: "Sonstiges Wohnen",  # the group's fallback since 0032
        C.HH_GROCERIES: "Sonstige Lebensmittel",  # the group's fallback since 0032
        C.HH_FAMILY_CHILD: "Sonstiges Familie und Kind",  # the group's fallback since 0032
        C.HH_MOBILITY: "Sonstige Mobilität",  # the group's fallback since 0032
        C.HH_INSURANCE: "Sonstige Versicherungen",  # the group's fallback since 0032
        C.HH_SUBSCRIPTIONS: "Sonstige Abos & Verträge",  # the group's fallback since 0032
        C.HH_LEISURE_TRAVEL: "Sonstige Freizeit & Hobbys",  # the group's fallback since 0032
        C.HH_HEALTH: "Sonstige Gesundheit",  # the group's fallback since 0032
        C.HH_OTHER: "Sonstiges",
        C.HH_DONATION: "Spende",
        C.HH_HOUSEHOLD_SERVICES: "Haushaltsnahe Dienstleistung oder Handwerker",
        C.HH_CHILDCARE: "Kinderbetreuung",
        C.HH_PROPERTY_COST: "Kosten der Immobilie, privat bezahlt",
        C.HH_TAX_PAYMENT: "Einkommensteuer (Vorauszahlung oder Nachzahlung)",
        C.INTERNAL_TRANSFER_IN: "Übertrag von eigenem Konto",
        C.INTERNAL_TRANSFER_OUT: "Übertrag auf eigenes Konto",
        C.HH_CASH: "Bargeldabhebung",
        C.HH_CREDIT_CARD: "Kreditkartenabrechnung",
        C.HH_INTEREST_FEES: "Zinsen und Gebühren",  # D176 (3)
        # D159 amended 13 Nov 2026 (migration 0029); Luis's labels.
        C.HH_PERSONAL_CARE: "Friseur & Körperpflege",
        C.HH_CLOTHING: "Kleidung & Schuhe",
        C.HH_DINING: "Sonstiges Essen gehen",  # the group's fallback since 0032
        C.HH_HOME: "Haushalt & Einrichtung",
        C.HH_ELECTRONICS: "Elektronik & Technik",
        C.HH_ONLINE_SHOPPING: "Online-Einkäufe",
        C.HH_FEES: "Behörden & Gebühren",
        C.HH_GIFTS: "Geschenke & Feiern",
        C.HH_FAMILY_TRANSFERS: "Geld an Familie / ins Ausland",
        C.HH_TELECOM: "Sonstiges Telefon & Internet",  # the group's fallback since 0032
        C.HH_WORK_EDUCATION: "Arbeitsmittel & Fortbildung",
        C.HH_SPORT: "Sport & Fitness",
        C.HH_TRAVEL: "Sonstiges Reisen",  # the group's fallback since 0032
        C.HH_INVESTMENT: "Geldanlage",
        C.HH_REFUND: "Erstattung",
        C.HH_FUEL: "Tanken (Auto)",  # D159 amended 14 Nov 2026 (migration 0031)
        # D159 amended 14 Nov 2026 (migration 0032); Luis's labels
        C.HH_STREAMING: "Streaming (Video)",
        C.HH_MUSIC_AUDIO: "Musik & Hörbücher",
        C.HH_SOFTWARE: "Software & KI",
        C.HH_CLOUD: "Cloud & Server",
        C.HH_GAMING: "Gaming",
        C.HH_NEWS: "Zeitungen & Zeitschriften",
        C.HH_MEMBERSHIPS: "Mitgliedschaften & Vereine",
        C.HH_PUBLIC_TRANSPORT: "Bahn, Bus & ÖPNV",
        C.HH_TAXI: "Taxi & Fahrdienste",
        C.HH_PARKING: "Parken & Maut",
        C.HH_CAR_SERVICE: "Auto: Werkstatt & Wartung",
        C.HH_CAR_TAX: "Auto: Kfz-Steuer & Zulassung",
        C.HH_SUPERMARKET: "Supermarkt",
        C.HH_DRUGSTORE: "Drogerie & Kosmetik",
        C.HH_BAKERY: "Bäckerei, Kiosk & Markt",
        C.HH_RESTAURANT: "Restaurant",
        C.HH_CAFE: "Café",
        C.HH_SWEETS: "Eis & Süßes",
        C.HH_FAST_FOOD: "Imbiss & Fast Food",
        C.HH_DELIVERY: "Lieferdienst",
        C.HH_RENT: "Miete",
        C.HH_ENERGY: "Strom, Gas & Heizung",
        C.HH_BROADCAST_FEE: "Rundfunkbeitrag",
        C.HH_FLAT_SEARCH: "Wohnungssuche & Umzug",
        C.HH_INS_CAR: "Kfz-Versicherung",
        C.HH_INS_LIABILITY_CONTENTS: "Haftpflicht & Hausrat",
        C.HH_INS_HEALTH: "Kranken & Pflege",
        C.HH_INS_LIFE: "Leben, Berufsunfähigkeit & Rente",
        C.HH_PHARMACY: "Apotheke",
        C.HH_DOCTOR: "Arzt & Labor",
        C.HH_DENTAL_OPTICAL: "Zahnarzt & Brille",
        C.HH_OUTINGS: "Ausflüge & Kultur",
        C.HH_BOOKS_MEDIA: "Bücher & Medien",
        C.HH_HOBBIES: "Hobbys",
        C.HH_LODGING: "Unterkunft",
        C.HH_FLIGHTS: "Flüge",
        C.HH_KIDS_GEAR: "Baby- & Kinderausstattung",
        C.HH_TOYS: "Spielzeug",
        C.HH_PREGNANCY_BIRTH: "Schwangerschaft & Geburt",
        C.HH_MOBILE_PHONE: "Mobilfunk",
        C.HH_INTERNET: "Internet & Festnetz",
        C.HH_POSTAGE: "Porto & Versand",
        C.SECURITY_PURCHASE: "Wertpapierkauf",
        C.SECURITY_SALE: "Wertpapierverkauf",
        C.DISTRIBUTION: "Ausschüttung oder Dividende",
        C.CAPITAL_INCOME_TAX: "Kapitalertragsteuer und Vorabpauschale",
        C.DEPOT_FEE: "Depot- und Verwaltergebühr",
    }
)

if set(CATEGORY_LABEL) != set(C):  # pragma: no cover - a new category without a label
    raise RuntimeError("every category needs a German label")
