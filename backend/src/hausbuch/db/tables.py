"""SQLAlchemy Core tables for migrations 0001-0025.

The migration is the source of truth. This module mirrors its columns (name, type, nullability,
whether a server default exists) and primary keys, so services can build typed queries; it
carries no CHECKs, FKs, indexes or policies, and is never used to create a schema.
Procrastinate's procrastinate_* tables (0005) are not mirrored: they belong to the queue (D20).
tests/db/test_tables_match_migration.py reflects the migrated database and fails on any drift.

`property_` has a trailing underscore so it does not shadow the builtin.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
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
    # 0005 (phase 2 settings)
    Column("ai_tenant_documents", Boolean, nullable=False, server_default=text("false")),
    Column("auto_accept_llm", Boolean, nullable=False, server_default=text("false")),
    # 0019 (phase 5, D123)
    Column("reminder_emails", Boolean, nullable=False, server_default=text("false")),
    # 0025 (phase 6, D168)
    Column("ai_private_accounts", Boolean, nullable=False, server_default=text("false")),
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
    _created_at(),
    _updated_at(),
    # 0016 (phase 4 D90): appended; marginal_tax_rate dropped.
    Column("church_tax", Boolean, nullable=False, server_default=text("false")),
    _text("tax_state", nullable=True),
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
    # 0005
    _text("kind", server_default=text("'manual'")),
    _uuid("ai_call_id", nullable=True),
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
    # 0013, appended by ADD COLUMN: the flat's Wohnfläche (D71).
    Column("living_area_m2", Numeric(7, 2), nullable=True),
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
    # 0013, appended by ADD COLUMN: the rent's due day (D67) and the running-cost terms (D72).
    Column("rent_due_day", SmallInteger, nullable=False, server_default=text("3")),
    _text("operating_costs_terms", server_default=text("'prepayment'")),
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
    # 0010, appended by ADD COLUMN: before first_payment_on the lender charges interest only.
    Column(
        "interest_only_before_first_payment", Boolean, nullable=False, server_default=text("false")
    ),
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
    # Phase 3 D143 (migration 0023, appended): LinkTarget.DOCUMENT, a payment proof's bill.
    _uuid("target_document_id", nullable=True),
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


# --- Auth (migration 0003; readable before sign-in only under app.auth_lookup) -----

auth_passkey = Table(
    "auth_passkey",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("user_id"),
    Column("credential_id", LargeBinary, nullable=False),
    Column("public_key", LargeBinary, nullable=False),
    Column("sign_count", BigInteger, nullable=False, server_default=text("0")),
    _uuid("aaguid", nullable=True),
    Column("transports", JSONB, nullable=True),
    Column("backup_eligible", Boolean, nullable=False),
    Column("backed_up", Boolean, nullable=False),
    _text("label"),
    _created_at(),
    _timestamp("last_used_at", nullable=True, default=False),
)

auth_session = Table(
    "auth_session",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("user_id"),
    _uuid("passkey_id", nullable=True),
    Column("token_hash", LargeBinary, nullable=False),
    _created_at(),
    _timestamp("last_seen_at"),
    _timestamp("expires_at", default=False),
    _timestamp("idle_expires_at", default=False),
    _timestamp("revoked_at", nullable=True, default=False),
)

auth_invite = Table(
    "auth_invite",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("user_id"),
    Column("token_hash", LargeBinary, nullable=False),
    _text("purpose", server_default=text("'enrollment'")),
    _uuid("created_by", nullable=True),
    _created_at(),
    _timestamp("expires_at", default=False),
    _timestamp("used_at", nullable=True, default=False),
)


# --- Phase 2: AI calls and proposals (0005) ----------------------------------

ai_call = Table(
    "ai_call",
    metadata,
    _id(),
    _workspace_id(),
    _text("job"),
    _uuid("document_id", nullable=True),
    _uuid("transaction_id", nullable=True),
    _text("model"),
    _text("prompt_version"),
    _text("inference_geo", nullable=True),
    Column("input_sha256", LargeBinary, nullable=False),
    Column("input_tokens", Integer, nullable=True),
    Column("output_tokens", Integer, nullable=True),
    Column("cache_read_tokens", Integer, nullable=True),
    _text("stop_reason", nullable=True),
    Column("output", JSONB, nullable=True),
    _text("raw_text", nullable=True),
    _text("error_code", nullable=True),
    Column("duration_ms", Integer, nullable=True),
    _created_at(),
)

proposal = Table(
    "proposal",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("document_id"),
    _text("kind", nullable=True),
    _text("status", server_default=text("'queued'")),
    _text("error_code", nullable=True),
    _uuid("extraction_id", nullable=True),
    _uuid("ai_call_id", nullable=True),
    _text("target_mode", server_default=text("'create'")),
    Column("contains_tenant_data", Boolean, nullable=False, server_default=text("false")),
    Column("targets", JSONB, nullable=True),
    Column("fields", JSONB, nullable=True),
    Column("decisions", JSONB, nullable=True),
    Column("applied_entities", JSONB, nullable=True),
    _created_at(),
    _updated_at(),
    _timestamp("decided_at", nullable=True, default=False),
    _uuid("decided_by", nullable=True),
    _timestamp("scrubbed_at", nullable=True, default=False),
)


# --- Phase 2: accounts, statements, transactions (0006) ------------------------

account = Table(
    "account",
    metadata,
    _id(),
    _workspace_id(),
    _text("label"),
    _text("bank"),
    _text("iban_masked", nullable=True),  # nullable since 0025 (a depot has no IBAN)
    Column("iban_hmac", LargeBinary, nullable=True),
    _text("source", server_default=text("'upload'")),
    _money("opening_balance"),
    _date("opening_balance_on"),
    Column("is_owner_account", Boolean, nullable=False, server_default=text("false")),
    _created_at(),
    _updated_at(),
    # 0025 (phase 6, D156)
    _text("kind", server_default=text("'bank_current'")),
    _text("purpose", server_default=text("'private'")),
    _text("provider", nullable=True),
    _text("currency", server_default=text("'EUR'")),
    _text("external_ref_masked", nullable=True),
    Column("external_ref_hmac", LargeBinary, nullable=True),
)

statement_import = Table(
    "statement_import",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("account_id"),
    _uuid("document_id"),
    _text("format"),
    Column("lines_total", Integer, nullable=False, server_default=text("0")),
    Column("lines_new", Integer, nullable=False, server_default=text("0")),
    Column("lines_skipped", Integer, nullable=False, server_default=text("0")),
    _date("first_booking_on", nullable=True),
    _date("last_booking_on", nullable=True),
    _money("declared_opening", nullable=True),
    _money("declared_closing", nullable=True),
    _text("balance_source", server_default=text("'none'")),
    _money("computed_opening", nullable=True),
    _money("computed_closing", nullable=True),
    _text("status", server_default=text("'imported'")),
    _text("error_code", nullable=True),
    _uuid("ai_call_id", nullable=True),
    _created_at(),
    _updated_at(),
)

transaction = Table(
    "transaction",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("account_id"),
    _uuid("import_id"),
    Column("line_hash", LargeBinary, nullable=False),
    _date("booking_date"),
    _date("value_date", nullable=True),
    _money("amount"),
    _text("currency", server_default=text("'EUR'")),
    _text("counterparty_name", nullable=True),
    _text("counterparty_iban_masked", nullable=True),
    Column("counterparty_iban_hmac", LargeBinary, nullable=True),
    _text("purpose", server_default=text("''")),
    _text("booking_text", nullable=True),
    _text("bank_reference", nullable=True),
    _text("end_to_end_ref", nullable=True),
    _text("mandate_ref", nullable=True),
    Column("raw", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("is_pending", Boolean, nullable=False, server_default=text("false")),
    _uuid("excluded_as_duplicate_of", nullable=True),
    _created_at(),
)

possible_duplicate = Table(
    "possible_duplicate",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("transaction_id"),
    _uuid("other_transaction_id"),
    _text("status", server_default=text("'open'")),
    _timestamp("decided_at", nullable=True, default=False),
    _created_at(),
)


# --- Phase 2: allocations and learned rules (0007) -----------------------------

allocation = Table(
    "allocation",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("transaction_id"),
    _money("amount"),
    _text("category"),
    _uuid("property_id", nullable=True),
    _uuid("unit_id", nullable=True),
    _uuid("tenancy_id", nullable=True),
    _uuid("loan_id", nullable=True),
    _date("service_period_start", nullable=True),
    _date("service_period_end", nullable=True),
    Column("recurring", Boolean, nullable=False, server_default=text("false")),
    _text("method"),
    Column("confidence", Numeric(4, 3), nullable=False),
    _text("status", server_default=text("'proposed'")),
    _uuid("supersedes_id", nullable=True),
    _uuid("rule_id", nullable=True),
    _uuid("ai_call_id", nullable=True),
    _uuid("document_id", nullable=True),
    _text("reasoning", nullable=True),
    _text("question", nullable=True),
    _timestamp("recorded_at"),
    _timestamp("confirmed_at", nullable=True, default=False),
    _uuid("confirmed_by", nullable=True),
)

matching_rule = Table(
    "matching_rule",
    metadata,
    _id(),
    _workspace_id(),
    Column("matcher", JSONB, nullable=False),
    _text("split_method"),
    _text("category", nullable=True),
    _uuid("property_id", nullable=True),
    _uuid("tenancy_id", nullable=True),
    _uuid("loan_id", nullable=True),
    Column("recurring", Boolean, nullable=False, server_default=text("false")),
    _uuid("created_from_allocation_id", nullable=True),
    Column("enabled", Boolean, nullable=False, server_default=text("true")),
    Column("hit_count", Integer, nullable=False, server_default=text("0")),
    _created_at(),
    _updated_at(),
    # 0011, appended by ADD COLUMN: the rule books only the latest amount of its kind (D49).
    Column("same_amount_only", Boolean, nullable=False, server_default=text("false")),
    # 0012, appended by ADD COLUMN: as_last_split's kind, a category list (D51). None is SQL
    # NULL, never a JSON null: the CHECK reads NULL as "not as_last_split".
    Column("categories", JSONB(none_as_null=True), nullable=True),
)


# --- Phase 3: the tax year (0013) --------------------------------------------
tax_input = Table(
    "tax_input",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("property_id"),
    _text("kind"),
    Column("tax_year", SmallInteger, nullable=True),
    _uuid("unit_id", nullable=True),
    _uuid("loan_id", nullable=True),
    _uuid("allocation_id", nullable=True),
    _uuid("document_id", nullable=True),
    Column("value", JSONB, nullable=False),
    _text("note", nullable=True),
    _uuid("supersedes_id", nullable=True),
    Column("withdrawn", Boolean, nullable=False, server_default=text("false")),
    _timestamp("recorded_at"),
    _uuid("recorded_by", nullable=True),
    _uuid("carried_from_id", nullable=True),  # 0018, phase 4b D110
)
tax_export = Table(
    "tax_export",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("property_id", nullable=True),  # nullable since 0018 (D104)
    Column("tax_year", SmallInteger, nullable=False),
    _text("kind"),
    _text("rule_set_version"),
    Column("form_year", SmallInteger, nullable=False),
    _text("form_status"),
    Column("draft", JSONB, nullable=False),
    Column("file_sha256", LargeBinary, nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    _created_at(),
    _uuid("created_by", nullable=True),
    _uuid("owner_id", nullable=True),  # 0018: an Anlage N sheet's owner (D104)
)

# --- Phase 4: performance (0016) ---------------------------------------------

income_input = Table(
    "income_input",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("owner_id", nullable=True),
    _text("kind"),
    Column("tax_year", SmallInteger, nullable=False),
    _uuid("document_id", nullable=True),
    Column("value", JSONB, nullable=False),
    _text("note", nullable=True),
    _uuid("supersedes_id", nullable=True),
    Column("withdrawn", Boolean, nullable=False, server_default=text("false")),
    _timestamp("recorded_at"),
    _uuid("recorded_by", nullable=True),
)
projection_scenario = Table(
    "projection_scenario",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("property_id"),
    _text("name"),
    Column("is_default", Boolean, nullable=False, server_default=text("false")),
    Column("levers", JSONB, nullable=False),
    _uuid("supersedes_id", nullable=True),
    Column("withdrawn", Boolean, nullable=False, server_default=text("false")),
    _timestamp("recorded_at"),
    _uuid("recorded_by", nullable=True),
)

# --- Phase 4b: income tax return (0018) --------------------------------------

return_input = Table(
    "return_input",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("owner_id", nullable=True),
    _text("kind"),
    Column("tax_year", SmallInteger, nullable=False),
    _uuid("document_id", nullable=True),
    Column("value", JSONB, nullable=False),
    _text("note", nullable=True),
    _uuid("supersedes_id", nullable=True),
    _uuid("carried_from_id", nullable=True),
    Column("withdrawn", Boolean, nullable=False, server_default=text("false")),
    _timestamp("recorded_at"),
    _uuid("recorded_by", nullable=True),
)
questionnaire_answer = Table(
    "questionnaire_answer",
    metadata,
    _id(),
    _workspace_id(),
    _text("question_key"),
    Column("tax_year", SmallInteger, nullable=False),
    _uuid("owner_id", nullable=True),
    _uuid("property_id", nullable=True),
    _text("answer"),
    _text("choice", nullable=True),
    _uuid("supersedes_id", nullable=True),
    _uuid("carried_from_id", nullable=True),
    Column("withdrawn", Boolean, nullable=False, server_default=text("false")),
    _timestamp("recorded_at"),
    _uuid("recorded_by", nullable=True),
)


# --- Phase 5: automation (0019) ----------------------------------------------

bank_connection = Table(
    "bank_connection",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("account_id"),
    _text("bank_code"),
    _text("fints_url"),
    _text("login_name"),
    _text("tan_mechanism", nullable=True),
    Column("client_data", LargeBinary, nullable=True),
    Column("client_data_mac", LargeBinary, nullable=True),
    Column("auto_sync", Boolean, nullable=False, server_default=text("false")),
    _timestamp("sca_confirmed_at", nullable=True, default=False),
    _timestamp("last_sync_at", nullable=True, default=False),
    _date("last_booking_on", nullable=True),
    _created_at(),
    _updated_at(),
    # 0024 (D154 (7))
    Column("fetch_statements", Boolean, nullable=False, server_default=text("false")),
    # 0025 (phase 6, D163)
    _text("credential_slot", server_default=text("'primary'")),
)
bank_sync = Table(
    "bank_sync",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("account_id"),
    _uuid("connection_id"),
    _text("trigger"),
    _text("status", server_default=text("'queued'")),
    _date("requested_from", nullable=True),
    _date("requested_to", nullable=True),
    _uuid("import_id", nullable=True),
    Column("lines_total", Integer, nullable=True),
    Column("lines_new", Integer, nullable=True),
    _text("balance_source", nullable=True),
    Column("balance_checked", Boolean, nullable=False, server_default=text("false")),
    _text("error_code", nullable=True),
    _timestamp("started_at", nullable=True, default=False),
    _timestamp("finished_at", nullable=True, default=False),
    _uuid("created_by", nullable=True),
    _created_at(),
    _updated_at(),
)
inbound_mail = Table(
    "inbound_mail",
    metadata,
    _id(),
    _workspace_id(),
    Column("message_id_hash", LargeBinary, nullable=False),
    Column("attempt", SmallInteger, nullable=False),
    _timestamp("received_at", nullable=True, default=False),
    _text("decision"),
    _text("code", nullable=True),
    Column("attachments_total", SmallInteger, nullable=True),
    Column("attachments_accepted", SmallInteger, nullable=True),
    _timestamp("recorded_at"),
)
bank_statement = Table(
    "bank_statement",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("account_id"),
    Column("year", SmallInteger, nullable=False),
    Column("number", Integer, nullable=False),
    _uuid("document_id"),
    _uuid("sync_id"),
    _timestamp("fetched_at"),
)
inbound_mail_attachment = Table(
    "inbound_mail_attachment",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("mail_id"),
    _uuid("document_id"),
    _uuid("import_id", nullable=True),
    _timestamp("recorded_at"),
)
reminder_ack = Table(
    "reminder_ack",
    metadata,
    _id(),
    _workspace_id(),
    _text("reminder_key"),
    _text("scope_type"),
    _uuid("scope_id", nullable=True),
    _date("due_on", nullable=True),
    _text("action"),
    _date("snoozed_until", nullable=True),
    _timestamp("recorded_at"),
    _uuid("recorded_by", nullable=True),
)
notification = Table(
    "notification",
    metadata,
    _id(),
    _workspace_id(),
    _text("reminder_id"),
    Column("lead_days", SmallInteger, nullable=False),
    _text("channel"),
    _uuid("recipient_user_id"),
    _timestamp("sent_at"),
    _text("status"),
    _text("error_code", nullable=True),
    Column("attempt", SmallInteger, nullable=False),
)


# --- Phase 6 (0025): household accounts, assets, movements, prices --------------------------


def _quantity(name: str, *, nullable: bool = False) -> Column:
    return Column(name, Numeric(28, 10), nullable=nullable)


account_ownership = Table(
    "account_ownership",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("account_id"),
    _uuid("owner_id"),
    Column("share_numerator", Integer, nullable=False),
    Column("share_denominator", Integer, nullable=False),
    _text("reason", nullable=True),
    _date("effective_from"),
    _uuid("superseded_by", nullable=True),
    _created_at(),
)
asset = Table(
    "asset",
    metadata,
    _id(),
    _workspace_id(),
    _text("kind"),
    _text("symbol", nullable=True),
    _text("isin", nullable=True),
    _text("name"),
    _text("currency", server_default=text("'EUR'")),
    _text("coingecko_id", nullable=True),
    _text("kraken_pair", nullable=True),
    Column("decimals", Integer, nullable=False, server_default=text("8")),
    _created_at(),
    _updated_at(),
)
movement_import = Table(
    "movement_import",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("account_id"),
    _uuid("document_id"),
    _text("format"),
    Column("rows_total", Integer, nullable=False, server_default=text("0")),
    Column("rows_new", Integer, nullable=False, server_default=text("0")),
    Column("rows_skipped", Integer, nullable=False, server_default=text("0")),
    _timestamp("first_at", nullable=True, default=False),
    _timestamp("last_at", nullable=True, default=False),
    _text("status", server_default=text("'imported'")),
    _text("error_code", nullable=True),
    _created_at(),
    _updated_at(),
)
asset_movement = Table(
    "asset_movement",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("account_id"),
    _uuid("asset_id"),
    _text("kind"),
    _quantity("quantity"),
    _money("eur_amount", nullable=True),
    _money("fee_eur", nullable=True),
    _quantity("fee_quantity", nullable=True),
    _money("tax_withheld_eur", nullable=True),
    _timestamp("occurred_at", default=False),
    _text("external_ref", nullable=True),
    _text("network", nullable=True),
    _text("tx_hash", nullable=True),
    _text("movement_hash", nullable=True),
    _text("source"),
    _uuid("document_id", nullable=True),
    _uuid("transaction_id", nullable=True),
    _text("status", server_default=text("'proposed'")),
    _uuid("superseded_by", nullable=True),
    _created_at(),
)
holding_statement = Table(
    "holding_statement",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("account_id"),
    _uuid("asset_id"),
    _date("as_of"),
    _quantity("declared_units"),
    _money("declared_value_eur", nullable=True),
    _text("source"),
    _uuid("document_id", nullable=True),
    _uuid("import_id", nullable=True),
    Column("reconciled", Boolean, nullable=True),
    _quantity("difference_units", nullable=True),
    _created_at(),
)
transfer_link = Table(
    "transfer_link",
    metadata,
    _id(),
    _workspace_id(),
    _text("kind"),
    _uuid("out_transaction_id", nullable=True),
    _uuid("out_movement_id", nullable=True),
    _uuid("in_transaction_id", nullable=True),
    _uuid("in_movement_id", nullable=True),
    _text("matched_by"),
    _uuid("superseded_by", nullable=True),
    _created_at(),
)
price = Table(
    "price",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("asset_id"),
    _date("on"),  # a reserved word: quoted in hand-written SQL ("on")
    Column("price_eur", Numeric(20, 8), nullable=False),
    _text("source"),
    _timestamp("fetched_at"),
    _uuid("document_id", nullable=True),
    Column("raw", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    _created_at(),
)
valuation_snapshot = Table(
    "valuation_snapshot",
    metadata,
    _id(),
    _workspace_id(),
    _uuid("account_id"),
    _uuid("asset_id", nullable=True),
    _date("as_of"),
    _quantity("units", nullable=True),
    Column("price_eur", Numeric(20, 8), nullable=True),
    _text("price_source", nullable=True),
    _money("value_eur"),
    _uuid("net_worth_run_id"),
    _created_at(),
)
