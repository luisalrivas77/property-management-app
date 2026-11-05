"""Closed value sets. Each mirrors a CHECK constraint in the phase 1 migration."""

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
