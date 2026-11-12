"""Closed value sets. Each mirrors a CHECK constraint in a migration (phase 1: 0001-0004,
phase 2: 0005-0012, phase 3: 0013, phase 4: 0016, phase 4b: 0018, phase 6: 0025);
tests/db/test_schema_consistency.py keeps them in sync.

Phase 2 sets that belong to one module live there instead (AiJob in ai.py, StatementFormat in
bank.py, the allocation sets in allocation.py), as docs/phases/phase-2.md §4 places them."""

from enum import StrEnum


class PropertyKind(StrEnum):
    ETW = "etw"  # Eigentumswohnung (one unit in a WEG)
    EFH = "efh"  # Einfamilienhaus
    MFH = "mfh"  # Mehrfamilienhaus


class AcquisitionCostKind(StrEnum):
    """Anschaffungsnebenkosten. Itemized so the AfA basis is traceable (principle 2)."""

    REAL_ESTATE_TRANSFER_TAX = "real_estate_transfer_tax"  # Grunderwerbsteuer
    NOTARY = "notary"  # Notarkosten
    LAND_REGISTRY = "land_registry"  # Grundbuchkosten
    BROKER = "broker"  # Maklerprovision
    OTHER = "other"


class PriceSplitSource(StrEnum):
    """Where the building/land split of the purchase price comes from."""

    PURCHASE_CONTRACT = "purchase_contract"  # Kaufpreisaufteilung im Kaufvertrag
    BMF_TOOL = "bmf_tool"  # BMF Arbeitshilfe
    APPRAISAL = "appraisal"  # Gutachten (evidence document required)


class RentModel(StrEnum):
    """How rent changes over time. Term (befristet / unbefristet) is a separate field."""

    FIXED = "fixed"  # changes only by §558 increase or agreement
    STAFFEL = "staffel"  # Staffelmiete (§557a BGB)
    INDEX = "index"  # Indexmiete (§557b BGB)


class DepositForm(StrEnum):
    NONE = "none"
    CASH_ACCOUNT = "cash_account"  # Barkaution auf Mietkautionskonto
    BANK_GUARANTEE = "bank_guarantee"  # Bankbürgschaft
    SURETY = "surety"  # Bürgschaft


class OperatingCostsTerms(StrEnum):
    """How a lease agrees the running costs (tenancy.operating_costs_terms, 0013; phase 3 D72)."""

    PREPAYMENT = "prepayment"  # Vorauszahlung with a yearly settlement (default)
    FLAT_RATE = "flat_rate"  # Nebenkostenpauschale, no settlement
    INCLUDED = "included"  # one all-in rent; Nebenkosten not agreed separately (Z24)


class TenancyRole(StrEnum):
    PRIMARY = "primary"
    CO_TENANT = "co_tenant"


class RentChangeReason(StrEnum):
    INITIAL = "initial"
    STAFFEL_STEP = "staffel_step"
    INDEX_ADJUSTMENT = "index_adjustment"
    RENT_INCREASE = "rent_increase"  # §558 BGB Mieterhöhung
    MODERNISATION = "modernisation"  # §559 BGB
    AGREEMENT = "agreement"
    CORRECTION = "correction"  # supersedes an earlier row; never an edit


class DocumentSource(StrEnum):
    UPLOAD = "upload"
    PHOTO = "photo"
    EMAIL = "email"
    BANK_SYNC = "bank_sync"


class DocumentType(StrEnum):
    """The classifier's output set (AI layer). Phase 1 sets it by hand."""

    STATEMENT = "statement"
    INVOICE = "invoice"
    LETTER = "letter"
    CONTRACT = "contract"
    BESCHEID = "bescheid"
    ZINSBESCHEINIGUNG = "zinsbescheinigung"
    WIRTSCHAFTSPLAN = "wirtschaftsplan"
    HAUSGELDABRECHNUNG = "hausgeldabrechnung"
    OTHER = "other"
    # Phase 4b (D96, migration 0018): the employer's Lohnsteuerbescheinigung and a monthly
    # payslip, the owners' own pay documents (retention class tax_records).
    # Not yet in the classify request schema (ai/requests.py NOT_YET_CLASSIFIED): they join
    # the classifier in phase 4b step 2, which re-records the classify evals.
    LOHNSTEUERBESCHEINIGUNG = "lohnsteuerbescheinigung"
    GEHALTSABRECHNUNG = "gehaltsabrechnung"


class DocumentSubtype(StrEnum):
    """Finer typing where retention or the AfA basis depends on it."""

    PURCHASE_CONTRACT = "purchase_contract"  # Kaufvertrag
    PRICE_SPLIT_EVIDENCE = "price_split_evidence"  # Kaufpreisaufteilung / BMF Arbeitshilfe result
    APPRAISAL = "appraisal"  # Gutachten
    LOAN_CONTRACT = "loan_contract"  # Darlehensvertrag
    LEASE = "lease"  # Mietvertrag
    HANDOVER_PROTOCOL = "handover_protocol"  # Übergabeprotokoll
    DEPOSIT_RECORD = "deposit_record"  # Kautionsnachweis
    # Evidence of acquisition or production cost in the AfA basis: Nebenkosten invoices,
    # renovation invoices capitalised as anschaffungsnahe Herstellungskosten (spec amendment 1).
    COST_BASIS_EVIDENCE = "cost_basis_evidence"
    # The property's Grundsteuerbescheid (type bescheid); a tax record (spec amendment 3).
    GRUNDSTEUERBESCHEID = "grundsteuerbescheid"
    # Phase 4b D129 (migration 0020): the household's Einkommensteuerbescheid (type bescheid);
    # a tax record like every Bescheid. Its zvE is proposed as the year's bescheid_zve.
    EINKOMMENSTEUERBESCHEID = "einkommensteuerbescheid"
    # The document catalogue (phase-2.md D130, D131, §6.2.1; migration 0021). Each is read by
    # the general reading (title, date, plus its group's link field), except the three invoices
    # (invoice reader) and the Verwaltervertrag (its own kind); none creates a record.
    # Property and purchase (retention ownership_records; link:property)
    LAND_REGISTER_EXTRACT = "land_register_extract"  # Grundbuchauszug (letter)
    DECLARATION_OF_DIVISION = "declaration_of_division"  # Teilungserklärung (contract)
    LAND_CHARGE_DEED = "land_charge_deed"  # Grundschuldbestellungsurkunde (contract)
    ENERGY_CERTIFICATE = "energy_certificate"  # Energieausweis (other)
    BUILDING_RECORDS = "building_records"  # Grundriss, Wohnflächenberechnung ... (other)
    INSURANCE_POLICY = "insurance_policy"  # Versicherungsschein (contract)
    MANAGEMENT_CONTRACT = "management_contract"  # Verwaltervertrag (contract, D130)
    SERVICE_CONTRACT = "service_contract"  # Versorger- oder Wartungsvertrag (contract)
    # WEG (link:property)
    WEG_MINUTES = "weg_minutes"  # Protokoll der Eigentümerversammlung (letter; ownership)
    WEG_INVITATION = "weg_invitation"  # Einladung zur Eigentümerversammlung (letter)
    SPECIAL_LEVY = "special_levy"  # Sonderumlage (letter)
    # Loan (link:loan)
    REPAYMENT_SCHEDULE = "repayment_schedule"  # Tilgungsplan (other; ownership)
    LOAN_TERMS_CHANGE = "loan_terms_change"  # Prolongation, Konditionsanpassung (letter; own.)
    LOAN_STATEMENT = "loan_statement"  # Darlehenskontoauszug (statement)
    # Phase 2 D138 (migration 0022): a whole financing bill (Grundschuld, Darlehen; invoice or
    # bescheid, read by its type's reader; tax_records). Set by code (rules.prefill), never by
    # the classifier: left out of the classify schema (ai.requests.classify_schema).
    FINANCING_INVOICE = "financing_invoice"
    # Tenancy (tenant gate; retention tenancy_records; link:tenancy)
    LEASE_AMENDMENT = "lease_amendment"  # Nachtrag zum Mietvertrag (contract)
    RENT_INCREASE = "rent_increase"  # Mieterhöhung (letter)
    TERMINATION_NOTICE = "termination_notice"  # Kündigung, Mietaufhebungsvertrag (letter)
    TENANT_SELF_DISCLOSURE = "tenant_self_disclosure"  # Mieterselbstauskunft (other)
    LANDLORD_CONFIRMATION = "landlord_confirmation"  # Wohnungsgeberbestätigung (letter)
    OPERATING_COSTS_STATEMENT = "operating_costs_statement"  # Betriebskostenabrechnung (letter)
    TENANT_CORRESPONDENCE = "tenant_correspondence"  # Schreiben von oder an Mieter (letter)
    # Running costs (link:property)
    UTILITY_BILL = "utility_bill"  # Jahresabrechnung Strom, Gas, Wasser (invoice; invoice reader)
    QUOTE = "quote"  # Kostenvoranschlag, Angebot (letter)
    CHIMNEY_SWEEP_NOTICE = "chimney_sweep_notice"  # Feuerstättenbescheid (bescheid)
    # Phase 3 D143 (migration 0023): the proof that a bill was paid (a transfer confirmation or
    # a statement page; type other, tax_records). Set by code when it is uploaded from a bill's
    # „Zahlungsnachweis fehlt“ to-do and linked to that bill, never by the classifier: left out
    # of the classify schema (ai.requests.classify_schema).
    PAYMENT_PROOF = "payment_proof"
    # Income tax (no link)
    PREPAYMENT_NOTICE = "prepayment_notice"  # Vorauszahlungsbescheid (bescheid)
    TAX_RETURN_COPY = "tax_return_copy"  # Steuererklärung, ELSTER-Protokoll (other)
    DONATION_RECEIPT = "donation_receipt"  # Zuwendungsbestätigung (letter)
    CHILDCARE_INVOICE = "childcare_invoice"  # Kita-Beitrag, Betreuungsrechnung (invoice)
    HOUSEHOLD_SERVICES_INVOICE = "household_services_invoice"  # §35a (invoice; invoice reader)
    INSURANCE_CONTRIBUTION_CERTIFICATE = "insurance_contribution_certificate"  # (letter)
    PENSION_CONTRIBUTION_CERTIFICATE = "pension_contribution_certificate"  # Riester (letter)
    BENEFIT_NOTICE = "benefit_notice"  # Elterngeld-, Krankengeldbescheid (bescheid)
    CHILD_BENEFIT_NOTICE = "child_benefit_notice"  # Kindergeldbescheid (bescheid)
    EMPLOYMENT_CONTRACT = "employment_contract"  # Arbeitsvertrag (contract)
    EMPLOYER_CERTIFICATE = "employer_certificate"  # Arbeitgeberbescheinigung (letter)
    WORK_EXPENSE_INVOICE = "work_expense_invoice"  # Arbeitsmittel (invoice; invoice reader)
    # Household finances (no link; their own readers come with phases 6 and 8)
    DEPOT_STATEMENT = "depot_statement"  # Depotauszug (statement)
    DEPOT_REPORT = "depot_report"  # Quartals- oder Vermögensbericht (other)
    TRADE_CONFIRMATION = "trade_confirmation"  # Wertpapierabrechnung (letter)
    CAPITAL_INCOME_TAX_CERTIFICATE = "capital_income_tax_certificate"  # (letter)
    # Phase 6 (migration 0025). An exchange's transaction history CSV (D166): typed by code
    # from the upload page's „Börse / Exchange-Export“ button, never classified and never sent
    # to the model (it holds every trade and the EUR legs); retention holding_records (D169).
    EXCHANGE_EXPORT = "exchange_export"
    # A depot's distribution note (Ertragsabrechnung, D165 (2)); read in phase 6 step 4 by the
    # distribution_note reader, until then left out of the classify schema like the other
    # code-only subtypes; retention tax_records (D169).
    DISTRIBUTION_NOTE = "distribution_note"


class ExtractionSource(StrEnum):
    MANUAL = "manual"
    AI = "ai"  # phase 2; provenance columns arrive with it


class ActorType(StrEnum):
    USER = "user"
    SYSTEM = "system"
    AI = "ai"


class EntityType(StrEnum):
    """Entities that carry events. Also the prefix of every event type."""

    OWNER = "owner"
    PROPERTY = "property"
    UNIT = "unit"
    TENANT = "tenant"
    TENANCY = "tenancy"
    LOAN = "loan"
    DOCUMENT = "document"
    # Phase 2 (migration 0005 adds them all at once, so 0006/0007 never touch the CHECK).
    USER = "user"  # sign-in and passkey events (D28); entity_id = app_user.id
    PROPOSAL = "proposal"
    ACCOUNT = "account"
    STATEMENT = "statement"  # a statement_import row
    TRANSACTION = "transaction"
    RULE = "rule"  # a matching_rule row
    WORKSPACE = "workspace"
    # Phase 6 (migration 0025, §11).
    ASSET = "asset"  # an asset row; its prices' events too (price.* -> asset.*)
    MOVEMENT = "movement"  # an asset_movement row
    TRANSFER = "transfer"  # a transfer_link row


class LinkTarget(StrEnum):
    """What a document can be linked to (document_link has one FK column per target)."""

    OWNER = "owner"
    PROPERTY = "property"
    UNIT = "unit"
    TENANT = "tenant"
    TENANCY = "tenancy"
    LOAN = "loan"
    # Phase 3 D143 (migration 0023, column target_document_id): a payment proof linked to the
    # bill it proves was paid. Never the document itself (a CHECK), never rolled up.
    DOCUMENT = "document"


class RetentionClass(StrEnum):
    OWNERSHIP_RECORDS = "ownership_records"  # life of ownership + 10 years
    TAX_RECORDS = "tax_records"  # 10 years after the tax year
    TENANCY_RECORDS = "tenancy_records"  # end of tenancy + 4 years
    TENANT_CONTACT = "tenant_contact"  # end of tenancy + 4 years, then deleted
    # Phase 6 D169: a trade confirmation or an exchange export, kept while the position is
    # held and 10 years after the year it is sold.
    HOLDING_RECORDS = "holding_records"


class RetentionHold(StrEnum):
    """Why a retain_until date cannot be computed yet."""

    WHILE_OWNED = "while_owned"
    TENANCY_ACTIVE = "tenancy_active"
    UNTYPED = "untyped"
    UNLINKED = "unlinked"
    WHILE_HELD = "while_held"  # phase 6 D169: an asset it carries a movement for is still held


class InvitePurpose(StrEnum):
    """auth_invite.purpose (migration 0003). One value today; kept as a CHECK'd set (D3)."""

    ENROLLMENT = "enrollment"  # first user, household member, or recovery: enrol a passkey


# --- Phase 2 -------------------------------------------------------------------


class ExtractionKind(StrEnum):
    """document_extraction.kind and proposal.kind (migration 0005): which field set an
    extraction holds. `manual` is phase 1's ManualDocumentFields; the others are the per-type
    models in schemas/extraction.py."""

    MANUAL = "manual"
    PURCHASE_CONTRACT = "purchase_contract"  # Kaufvertrag
    LOAN_CONTRACT = "loan_contract"  # Darlehensvertrag
    LEASE = "lease"  # Mietvertrag
    HANDOVER_PROTOCOL = "handover_protocol"  # Übergabeprotokoll
    DEPOSIT_RECORD = "deposit_record"  # Kautionsnachweis
    INVOICE = "invoice"  # Nebenkosten and later invoices
    BESCHEID = "bescheid"  # e.g. Grunderwerbsteuerbescheid
    GRUNDSTEUERBESCHEID = "grundsteuerbescheid"
    WIRTSCHAFTSPLAN = "wirtschaftsplan"
    HAUSGELDABRECHNUNG = "hausgeldabrechnung"
    ZINSBESCHEINIGUNG = "zinsbescheinigung"
    STATEMENT = "statement"  # PDF Kontoauszug: balances + lines
    # Phase 4b (D96, migration 0018)
    LOHNSTEUERBESCHEINIGUNG = "lohnsteuerbescheinigung"
    GEHALTSABRECHNUNG = "gehaltsabrechnung"
    # Phase 4b D129 (migration 0020)
    EINKOMMENSTEUERBESCHEID = "einkommensteuerbescheid"
    # Phase 2 D130, D131 (migration 0021): title and date (plus a group's link field) for
    # every document without a reader of its own, and the Verwaltervertrag.
    GENERAL = "general"
    MANAGEMENT_CONTRACT = "management_contract"
    # Phase 3 D143 (migration 0023): a payment proof's date, amount, payee and purpose.
    PAYMENT_PROOF = "payment_proof"
    # Phase 6 D165 (migration 0025): the depot's three readers, built in step 4.
    TRADE_CONFIRMATION = "trade_confirmation"  # Wertpapierabrechnung
    DISTRIBUTION_NOTE = "distribution_note"  # Ertragsabrechnung
    DEPOT_REPORT = "depot_report"  # the quarterly PM-Reporting


class ProposalStatus(StrEnum):
    """proposal.status (§3.1). applied and rejected are decisions; the rest are job states."""

    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    APPLIED = "applied"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"  # a re-run replaced it before a decision
    FAILED = "failed"  # dead letter: "Konnte nicht gelesen werden"
    BLOCKED_TENANT_DATA = "blocked_tenant_data"  # the privacy gate stopped it (D16)


class ProposalTargetMode(StrEnum):
    """proposal.target_mode: whether the proposal creates new records or patches `targets`."""

    CREATE = "create"
    PATCH = "patch"


class AccountSource(StrEnum):
    """account.source (migration 0006). Only `upload` in phase 2."""

    UPLOAD = "upload"
    FINTS = "fints"  # phase 5
    AGGREGATOR = "aggregator"  # only if it goes SaaS


class AccountKind(StrEnum):
    """account.kind (migration 0025, phase 6 D156). `wallet` is in the CHECK so phase 7 adds
    no migration for it; create_account refuses it (account.kind_not_available) until then,
    as AccountSource.AGGREGATOR is refused."""

    BANK_CURRENT = "bank_current"
    BANK_SAVINGS = "bank_savings"
    DEPOT = "depot"
    EXCHANGE = "exchange"
    WALLET = "wallet"
    # Phase 6 D176 (migration 0026): a credit card, fed by its PDF statements and routed by
    # the issuer's customer number (external_ref), with no IBAN of its own.
    CREDIT_CARD = "credit_card"


BANK_ACCOUNT_KINDS = frozenset({AccountKind.BANK_CURRENT, AccountKind.BANK_SAVINGS})


class AccountPurpose(StrEnum):
    """account.purpose (migration 0025, phase 6 D156): `rental` is the account the property's
    rent and costs run on; every other account of the household is `private`."""

    RENTAL = "rental"
    PRIVATE = "private"


class ImportStatus(StrEnum):
    """statement_import.status. `imported` with no declared closing balance is what the
    inbox shows as "pending balance" (reconcile's pending_balance)."""

    IMPORTED = "imported"
    RECONCILED = "reconciled"
    UNRECONCILED = "unreconciled"
    FAILED = "failed"


class BalanceSource(StrEnum):
    """statement_import.balance_source: where the declared balances came from (D21)."""

    FILE = "file"  # camt.053 Bal or the PDF Kontoauszug
    USER = "user"  # typed by Luis from online banking
    NONE = "none"  # not declared yet
    BANK = "bank"  # phase 5 D117: a FinTS sync's HKSAL booked balance, when the camt has no Bal


class DuplicateStatus(StrEnum):
    """possible_duplicate.status (D22)."""

    OPEN = "open"
    KEPT_BOTH = "kept_both"
    MERGED = "merged"  # the later-imported line is excluded from balances and allocations


class Currency(StrEnum):
    """transaction.currency. v1 is EUR only (one value, kept as a CHECK'd set like
    InvitePurpose)."""

    EUR = "EUR"


class AllocationStatus(StrEnum):
    """allocation.status (migration 0007, D23). The only column a trigger lets change, and
    only forward: proposed -> confirmed, proposed -> superseded, confirmed -> superseded."""

    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    SUPERSEDED = "superseded"


# --- Phase 4 -------------------------------------------------------------------


class StateCode(StrEnum):
    """owner.tax_state (migration 0016, phase 4 D90): the Bundesland whose church tax rate
    applies (rules/tax/tariffs/<year>.yaml, church_tax_rates)."""

    BW = "BW"  # Baden-Württemberg
    BY = "BY"  # Bayern
    BE = "BE"  # Berlin
    BB = "BB"  # Brandenburg
    HB = "HB"  # Bremen
    HH = "HH"  # Hamburg
    HE = "HE"  # Hessen
    MV = "MV"  # Mecklenburg-Vorpommern
    NI = "NI"  # Niedersachsen
    NW = "NW"  # Nordrhein-Westfalen
    RP = "RP"  # Rheinland-Pfalz
    SL = "SL"  # Saarland
    SN = "SN"  # Sachsen
    ST = "ST"  # Sachsen-Anhalt
    SH = "SH"  # Schleswig-Holstein
    TH = "TH"  # Thüringen


class IncomeInputKind(StrEnum):
    """income_input.kind (migration 0016, phase 4 D83)."""

    EXPECTED_GROSS_PAY = "expected_gross_pay"  # per owner and year
    DEDUCTIONS_OVERRIDE = "deductions_override"  # per owner and year; replaces the default
    BESCHEID_ZVE = "bescheid_zve"  # per year, household; the Bescheid as its document
    MARGINAL_RATE_OVERRIDE = "marginal_rate_override"  # per year, household; incl. Soli, church


# --- Phase 4b ------------------------------------------------------------------


class ReturnInputKind(StrEnum):
    """return_input.kind (migration 0018, phase 4b D97). Owner-scoped unless listed in
    HOUSEHOLD_KINDS; read per year unless listed in SPANNING_KINDS (D110)."""

    WAGE_CERTIFICATE = "wage_certificate"  # Lohnsteuerbescheinigung; document required
    PAYSLIP = "payslip"  # one month of one employer; document required (D105)
    COMMUTE = "commute"  # one route for a period
    HOME_OFFICE = "home_office"  # Tagespauschale days; one per owner and year
    WORK_ROOM = "work_room"  # häusliches Arbeitszimmer; one per owner and year (D106)
    WORK_EQUIPMENT = "work_equipment"  # one item or subscription (D108)
    TELECOM = "telecom"  # one per owner and year (D112)
    PROFESSIONAL_ASSOCIATION = "professional_association"  # incl. unions (is_union)
    TRAINING = "training"
    OTHER_EXPENSE = "other_expense"  # Kontoführung, Bewerbung, Fähr- und Flugkosten, other
    OTHER_INSURANCE = "other_insurance"  # §10 Abs. 1 Nr. 3a premiums; one per owner and year
    REPLACEMENT_INCOME = "replacement_income"  # a benefit paid by an authority (D102)
    VORSORGE_OVERRIDE = "vorsorge_override"  # one per owner and year
    CHILD = "child"  # household; first year it counts (D109)
    CHILDCARE_COST = "childcare_cost"  # household (D112)
    HOUSEHOLD_SERVICES = "household_services"  # household, §35a (D112)
    SPECIAL_EXPENSE = "special_expense"  # household, donations (D112)


# The household-scoped kinds (owner_id NULL); every other kind names its owner. Migration
# 0018's scope CHECK says the same.
HOUSEHOLD_KINDS = frozenset(
    {
        ReturnInputKind.CHILD,
        ReturnInputKind.CHILDCARE_COST,
        ReturnInputKind.HOUSEHOLD_SERVICES,
        ReturnInputKind.SPECIAL_EXPENSE,
    }
)
# Read with tax_year <= Y and not ended, never copied into a later year (D97, D110). A
# work_equipment row is spanning only while its useful life runs past the purchase year; a
# subscription row is a yearly item of the same kind (D108 (c)).
SPANNING_KINDS = frozenset({ReturnInputKind.CHILD, ReturnInputKind.WORK_EQUIPMENT})
# Never carried into the next year: the year's documents and one-off items (D110).
NOT_CARRIED_KINDS = frozenset(
    {
        ReturnInputKind.WAGE_CERTIFICATE,
        ReturnInputKind.PAYSLIP,
        ReturnInputKind.REPLACEMENT_INCOME,
        ReturnInputKind.TRAINING,
    }
)
# One current row per owner and year (the "one current answer" rule; payslip: per owner,
# employer and month). The other kinds are items, any number.
ONE_PER_OWNER_AND_YEAR = frozenset(
    {
        ReturnInputKind.HOME_OFFICE,
        ReturnInputKind.WORK_ROOM,
        ReturnInputKind.TELECOM,
        ReturnInputKind.OTHER_INSURANCE,
        ReturnInputKind.VORSORGE_OVERRIDE,
    }
)


class QuestionnaireAnswerValue(StrEnum):
    """questionnaire_answer.answer (migration 0018, D110). not_applicable is recorded too, so
    "answered no" differs from "not asked yet"."""

    YES = "yes"
    NO = "no"
    NOT_APPLICABLE = "not_applicable"
