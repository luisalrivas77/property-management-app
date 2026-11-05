"""SQLAlchemy Core tables for migration 0001.

The migration is the source of truth. This module mirrors its columns (name, type, nullability,
whether a server default exists) and primary keys, so services can build typed queries; it
carries no CHECKs, FKs, indexes or policies, and is never used to create a schema.
tests/db/test_tables_match_migration.py reflects the migrated database and fails on any drift.

`property_` has a trailing underscore so it does not shadow the builtin.
"""

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Identity,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    SmallInteger,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = MetaData()

NOW = text("now()")


def _id() -> Column:
    return Column(
        "id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _uuid(name: str, *, nullable: bool = False) -> Column:
    return Column(name, UUID(as_uuid=True), nullable=nullable)


def _workspace_id() -> Column:
    return _uuid("workspace_id")


def _text(name: str, *, nullable: bool = False, **kw) -> Column:
    return Column(name, Text, nullable=nullable, **kw)


def _money(name: str, *, nullable: bool = False, **kw) -> Column:
    return Column(name, Numeric(14, 2), nullable=nullable, **kw)


def _rate(name: str, *, nullable: bool = False) -> Column:
    return Column(name, Numeric(7, 6), nullable=nullable)


def _date(name: str, *, nullable: bool = False) -> Column:
    return Column(name, Date, nullable=nullable)


def _timestamp(name: str, *, nullable: bool = False, default: bool = True) -> Column:
    return Column(
        name, DateTime(timezone=True), nullable=nullable, server_default=NOW if default else None
    )


def _created_at() -> Column:
    return _timestamp("created_at")


def _updated_at() -> Column:
    return _timestamp("updated_at")


# --- Tenancy root ------------------------------------------------------------

workspace = Table(
    "workspace",
    metadata,
    _id(),
    _text("name"),
    _created_at(),
)

app_user = Table(
    "app_user",
    metadata,
    _id(),
    _workspace_id(),
    _text("email"),
    _text("display_name"),
    _created_at(),
)

owner = Table(
    "owner",
    metadata,
    _id(),
    _workspace_id(),
    _text("full_name"),
    _text("tax_id_ref", nullable=True),
    Column("marginal_tax_rate", Numeric(5, 4), nullable=True),
    _created_at(),
    _updated_at(),
)

# --- Documents ---------------------------------------------------------------

document_file = Table(
    "document_file",
    metadata,
    _workspace_id(),
    Column("sha256", LargeBinary, nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    _text("media_type"),
    _text("storage_key"),
    _created_at(),
    PrimaryKeyConstraint("workspace_id", "sha256"),
)

document = Table(
    "document",
    metadata,
    _id(),
    _workspace_id(),
    Column("file_sha256", LargeBinary, nullable=False),
    _text("source"),
    _text("original_filename", nullable=True),
    _text("type", nullable=True),
    _text("subtype", nullable=True),
    _text("title", nullable=True),
    _date("document_date", nullable=True),
    Column("tax_year", SmallInteger, nullable=True),
    _timestamp("received_at"),
    _created_at(),
    _updated_at(),
)

document_extraction = Table(
    "document_extraction",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("document_id"),
    Column("version", Integer, nullable=False),
    _text("source"),
    Column("fields", JSONB, nullable=False),
    Column("schema_version", SmallInteger, nullable=False, server_default=text("1")),
    _created_at(),
)

# --- Property ----------------------------------------------------------------

property_ = Table(
    "property",
    metadata,
    _id(),
    _workspace_id(),
    _text("label"),
    _text("kind"),
    _text("street"),
    _text("house_number"),
    _text("postal_code"),
    _text("city"),
    _text("country_code", server_default=text("'DE'")),
    Column("build_year", SmallInteger, nullable=False),
    _date("notarized_on"),
    _date("purchase_date"),
    _money("purchase_price"),
    _uuid("selected_price_split_id", nullable=True),
    _date("ownership_ended_on", nullable=True),
    _created_at(),
    _updated_at(),
)

property_ownership = Table(
    "property_ownership",
    metadata,
    _workspace_id(),
    _uuid("property_id"),
    _uuid("owner_id"),
    Column("share_numerator", Integer, nullable=False),
    Column("share_denominator", Integer, nullable=False),
    PrimaryKeyConstraint("property_id", "owner_id"),
)

property_acquisition_cost = Table(
    "property_acquisition_cost",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("property_id"),
    _text("kind"),
    _money("amount"),
    _date("paid_on", nullable=True),
    _uuid("document_id", nullable=True),
    _text("note", nullable=True),
    _created_at(),
    _updated_at(),
)

property_price_split = Table(
    "property_price_split",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("property_id"),
    _text("source"),
    _rate("building_share"),
    _uuid("evidence_document_id", nullable=True),
    Column("inputs", JSONB, nullable=True),
    _text("note", nullable=True),
    _timestamp("recorded_at"),
)

# --- Unit --------------------------------------------------------------------

unit = Table(
    "unit",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("property_id"),
    _text("label"),
    Column("floor_area_m2", Numeric(8, 2), nullable=False),
    Column("rooms", Numeric(4, 1), nullable=True),
    Column("mea_numerator", Integer, nullable=True),
    Column("mea_denominator", Integer, nullable=True),
    _created_at(),
    _updated_at(),
)

# --- Tenant (personal data) --------------------------------------------------

tenant = Table(
    "tenant",
    metadata,
    _id(),
    _workspace_id(),
    _text("full_name", nullable=True),
    _text("email", nullable=True),
    _text("phone", nullable=True),
    _text("iban_masked", nullable=True),
    Column("iban_hmac", LargeBinary, nullable=True),
    _timestamp("erased_at", nullable=True, default=False),
    _created_at(),
    _updated_at(),
)

# --- Tenancy -----------------------------------------------------------------

tenancy = Table(
    "tenancy",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("unit_id"),
    _date("signed_on", nullable=True),
    _date("start_date"),
    _date("end_date", nullable=True),
    _date("fixed_term_until", nullable=True),
    _text("rent_model"),
    _money("deposit_amount", server_default=text("0")),
    _text("deposit_form", server_default=text("'none'")),
    _text("deposit_bank", nullable=True),
    _text("deposit_iban_masked", nullable=True),
    Column("deposit_iban_hmac", LargeBinary, nullable=True),
    _date("notice_given_on", nullable=True),
    _created_at(),
    _updated_at(),
)

tenancy_tenant = Table(
    "tenancy_tenant",
    metadata,
    _workspace_id(),
    _uuid("tenancy_id"),
    _uuid("tenant_id"),
    _text("role"),
    PrimaryKeyConstraint("tenancy_id", "tenant_id"),
)

rent_schedule = Table(
    "rent_schedule",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("tenancy_id"),
    _date("effective_from"),
    _money("cold_rent"),
    _money("operating_costs_prepayment", server_default=text("0")),
    _text("reason"),
    _uuid("supersedes_id", nullable=True),
    _uuid("document_id", nullable=True),
    _timestamp("recorded_at"),
)

# --- Loan --------------------------------------------------------------------

loan = Table(
    "loan",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("property_id"),
    _text("lender"),
    _text("contract_number", nullable=True),
    _money("principal"),
    _rate("nominal_rate"),
    _money("monthly_payment"),
    _date("disbursed_on"),
    _date("first_payment_on"),
    _date("fixed_until", nullable=True),
    _rate("follow_up_rate", nullable=True),
    _text("account_iban_masked", nullable=True),
    Column("account_iban_hmac", LargeBinary, nullable=True),
    _created_at(),
    _updated_at(),
)

# --- Document links ----------------------------------------------------------

document_link = Table(
    "document_link",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("document_id"),
    _uuid("owner_id", nullable=True),
    _uuid("property_id", nullable=True),
    _uuid("unit_id", nullable=True),
    _uuid("tenant_id", nullable=True),
    _uuid("tenancy_id", nullable=True),
    _uuid("loan_id", nullable=True),
    _created_at(),
)

# --- Event log ---------------------------------------------------------------

event = Table(
    "event",
    metadata,
    _id(),
    _workspace_id(),
    Column("seq", BigInteger, Identity(always=True), nullable=False),
    _text("type"),
    _text("entity_type"),
    _uuid("entity_id"),
    _uuid("property_id", nullable=True),
    _uuid("unit_id", nullable=True),
    _date("effective_date", nullable=True),
    _timestamp("recorded_at"),
    _text("actor_type"),
    _text("actor_id"),
    _uuid("correlation_id"),
    _uuid("corrects_event_id", nullable=True),
    Column("payload", JSONB, nullable=False, server_default=text("'{}'")),
    Column("payload_version", SmallInteger, nullable=False, server_default=text("1")),
)
