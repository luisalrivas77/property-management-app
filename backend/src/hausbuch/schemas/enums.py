"""Closed value sets. Each mirrors a CHECK constraint in a migration (phase 1: 0001-0004,
phase 2: 0005-0012, phase 3: 0013, phase 4: 0016); tests/db/test_schema_consistency.py keeps
them in sync.

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


class LinkTarget(StrEnum):
    """What a document can be linked to (document_link has one FK column per target)."""

    OWNER = "owner"
    PROPERTY = "property"
    UNIT = "unit"
    TENANT = "tenant"
    TENANCY = "tenancy"
    LOAN = "loan"


class RetentionClass(StrEnum):
    OWNERSHIP_RECORDS = "ownership_records"  # life of ownership + 10 years
    TAX_RECORDS = "tax_records"  # 10 years after the tax year
    TENANCY_RECORDS = "tenancy_records"  # end of tenancy + 4 years
    TENANT_CONTACT = "tenant_contact"  # end of tenancy + 4 years, then deleted


class RetentionHold(StrEnum):
    """Why a retain_until date cannot be computed yet."""

    WHILE_OWNED = "while_owned"
    TENANCY_ACTIVE = "tenancy_active"
    UNTYPED = "untyped"
    UNLINKED = "unlinked"


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
