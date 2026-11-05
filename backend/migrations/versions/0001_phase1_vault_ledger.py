"""Phase 1: vault and ledger.

Owners, properties, units, tenants, tenancies, rent schedule, loans, documents and the event
log, with row-level security on every table. Spec: docs/phases/phase-1.md.

Conventions
- Every table has workspace_id; RLS filters on current_workspace_id() (the app.workspace_id
  setting). RLS is FORCEd so the table owner is filtered too; superusers still bypass it.
- Every parent has UNIQUE (workspace_id, id) and children reference it with a composite FK,
  so a row can never point at another workspace's row, even by id.
- Closed value sets are text + CHECK (not Postgres enums) so adding a value is one ALTER.
  They mirror hausbuch.schemas.enums; tests/db/test_schema_consistency.py keeps them in sync.
- Append-only tables are guarded by a trigger: event, rent_schedule and property_price_split
  reject UPDATE and DELETE; document_file and document_extraction reject UPDATE (DELETE is
  reserved for retention).
- Money numeric(14,2) EUR. Rates numeric(7,6) as fractions.

Revision ID: 0001_phase1_vault_ledger
Revises:
Create Date: 2026-11-04
"""

from alembic import op

revision = "0001_phase1_vault_ledger"
down_revision = None
branch_labels = None
depends_on = None

# Order matters: parents before children. Reversed for downgrade.
TABLES = [
    "workspace",
    "app_user",
    "owner",
    "document_file",
    "document",
    "document_extraction",
    "property",
    "property_ownership",
    "property_acquisition_cost",
    "property_price_split",
    "unit",
    "tenant",
    "tenancy",
    "tenancy_tenant",
    "rent_schedule",
    "loan",
    "document_link",
    "event",
]

# Privileges for the application role (created by ops, not by this migration).
APP_ROLE = "hausbuch_app"
APP_GRANTS = {
    "workspace": "SELECT, UPDATE",
    "app_user": "SELECT",
    "owner": "SELECT, INSERT, UPDATE",
    "document_file": "SELECT, INSERT, DELETE",
    "document": "SELECT, INSERT, UPDATE, DELETE",
    "document_extraction": "SELECT, INSERT",
    "property": "SELECT, INSERT, UPDATE",
    "property_ownership": "SELECT, INSERT, UPDATE, DELETE",
    "property_acquisition_cost": "SELECT, INSERT, UPDATE, DELETE",
    "property_price_split": "SELECT, INSERT",
    "unit": "SELECT, INSERT, UPDATE",
    "tenant": "SELECT, INSERT, UPDATE",
    "tenancy": "SELECT, INSERT, UPDATE",
    "tenancy_tenant": "SELECT, INSERT, UPDATE, DELETE",
    "rent_schedule": "SELECT, INSERT",
    "loan": "SELECT, INSERT, UPDATE",
    "document_link": "SELECT, INSERT, DELETE",
    "event": "SELECT, INSERT",
}

FUNCTIONS = """
CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE FUNCTION current_workspace_id() RETURNS uuid
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT nullif(current_setting('app.workspace_id', true), '')::uuid
$$;
COMMENT ON FUNCTION current_workspace_id() IS
    'Workspace of the current transaction. Set with SET LOCAL app.workspace_id = ''<uuid>''. '
    'Unset means no rows are visible and no rows can be written.';

CREATE FUNCTION forbid_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% on % is not allowed: rows are append-only', TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation',
              HINT = 'Record a correction as a new row or event instead.';
END
$$;
"""

DDL = r"""
-- Tenancy root ---------------------------------------------------------------

CREATE TABLE workspace (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    created_at  timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE workspace IS
    'Row-level-security tenant: one household. Not the Owner entity.';

CREATE TABLE app_user (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid NOT NULL REFERENCES workspace (id),
    email         text NOT NULL UNIQUE CHECK (email = lower(email) AND email LIKE '%_@_%'),
    display_name  text NOT NULL CHECK (length(display_name) > 0),
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id)
);

CREATE TABLE owner (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id       uuid NOT NULL REFERENCES workspace (id),
    full_name          text NOT NULL CHECK (length(full_name) > 0),
    tax_id_ref         text,
    marginal_tax_rate  numeric(5,4) CHECK (marginal_tax_rate >= 0 AND marginal_tax_rate < 1),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id)
);
COMMENT ON COLUMN owner.tax_id_ref IS
    'Reference into the secrets manager. The raw Steuer-ID is never stored here.';

-- Documents (before property: properties and costs reference evidence) ------

CREATE TABLE document_file (
    workspace_id  uuid NOT NULL REFERENCES workspace (id),
    sha256        bytea NOT NULL CHECK (octet_length(sha256) = 32),
    size_bytes    bigint NOT NULL CHECK (size_bytes > 0),
    media_type    text NOT NULL CHECK (media_type ~ '^[a-z]+/[a-z0-9.+-]+$'),
    storage_key   text NOT NULL UNIQUE,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, sha256)
);
COMMENT ON TABLE document_file IS
    'Immutable originals, content-addressed by SHA-256. Dedup is per workspace.';

CREATE TABLE document (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id       uuid NOT NULL REFERENCES workspace (id),
    file_sha256        bytea NOT NULL,
    source             text NOT NULL
                       CHECK (source IN ('upload', 'photo', 'email', 'bank_sync')),
    original_filename  text,
    type               text CHECK (type IN (
                           'statement', 'invoice', 'letter', 'contract', 'bescheid',
                           'zinsbescheinigung', 'wirtschaftsplan', 'hausgeldabrechnung', 'other')),
    subtype            text CHECK (subtype IN (
                           'purchase_contract', 'price_split_evidence', 'appraisal',
                           'loan_contract', 'lease', 'handover_protocol', 'deposit_record')),
    title              text,
    document_date      date,
    tax_year           smallint CHECK (tax_year BETWEEN 2000 AND 2100),
    received_at        timestamptz NOT NULL DEFAULT now(),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, file_sha256),
    FOREIGN KEY (workspace_id, file_sha256) REFERENCES document_file (workspace_id, sha256),
    CHECK (subtype IS NULL OR type IS NOT NULL)
);
COMMENT ON COLUMN document.type IS 'Null until typed (by hand in phase 1, classifier in phase 2).';

CREATE TABLE document_extraction (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    uuid NOT NULL REFERENCES workspace (id),
    document_id     uuid NOT NULL,
    version         integer NOT NULL CHECK (version >= 1),
    source          text NOT NULL CHECK (source IN ('manual', 'ai')),
    fields          jsonb NOT NULL CHECK (jsonb_typeof(fields) = 'object'),
    schema_version  smallint NOT NULL DEFAULT 1,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, version),
    FOREIGN KEY (workspace_id, document_id) REFERENCES document (workspace_id, id)
        ON DELETE CASCADE
);
COMMENT ON TABLE document_extraction IS
    'Versioned field layers on top of an original. Highest version is current.';

-- Property -------------------------------------------------------------------

CREATE TABLE property (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id             uuid NOT NULL REFERENCES workspace (id),
    label                    text NOT NULL CHECK (length(label) BETWEEN 1 AND 100),
    kind                     text NOT NULL CHECK (kind IN ('etw', 'efh', 'mfh')),
    street                   text NOT NULL,
    house_number             text NOT NULL,
    postal_code              text NOT NULL CHECK (postal_code ~ '^[0-9]{5}$'),
    city                     text NOT NULL,
    country_code             text NOT NULL DEFAULT 'DE' CHECK (country_code = 'DE'),
    build_year               smallint NOT NULL CHECK (build_year BETWEEN 1800 AND 2100),
    notarized_on             date NOT NULL,
    purchase_date            date NOT NULL,
    purchase_price           numeric(14,2) NOT NULL CHECK (purchase_price > 0),
    selected_price_split_id  uuid,
    ownership_ended_on       date,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    CHECK (purchase_date >= notarized_on),
    CHECK (ownership_ended_on IS NULL OR ownership_ended_on >= purchase_date)
);
COMMENT ON COLUMN property.build_year IS 'Fertigstellungsjahr; selects the AfA rate.';
COMMENT ON COLUMN property.notarized_on IS
    'Kaufvertrag notarized; starts the §23 Spekulationsfrist.';
COMMENT ON COLUMN property.purchase_date IS
    'Übergang von Besitz, Nutzen und Lasten; AfA starts in this month.';

CREATE TABLE property_ownership (
    workspace_id       uuid NOT NULL REFERENCES workspace (id),
    property_id        uuid NOT NULL,
    owner_id           uuid NOT NULL,
    share_numerator    integer NOT NULL CHECK (share_numerator > 0),
    share_denominator  integer NOT NULL CHECK (share_denominator > 0),
    PRIMARY KEY (property_id, owner_id),
    FOREIGN KEY (workspace_id, property_id) REFERENCES property (workspace_id, id),
    FOREIGN KEY (workspace_id, owner_id) REFERENCES owner (workspace_id, id),
    CHECK (share_numerator <= share_denominator)
);
COMMENT ON TABLE property_ownership IS
    'Shares per property must sum to exactly 1; enforced by the service layer.';

CREATE TABLE property_acquisition_cost (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid NOT NULL REFERENCES workspace (id),
    property_id   uuid NOT NULL,
    kind          text NOT NULL CHECK (kind IN (
                      'real_estate_transfer_tax', 'notary', 'land_registry', 'broker', 'other')),
    amount        numeric(14,2) NOT NULL CHECK (amount > 0),
    paid_on       date,
    document_id   uuid,
    note          text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, property_id) REFERENCES property (workspace_id, id),
    FOREIGN KEY (workspace_id, document_id) REFERENCES document (workspace_id, id)
);

CREATE TABLE property_price_split (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id          uuid NOT NULL REFERENCES workspace (id),
    property_id           uuid NOT NULL,
    source                text NOT NULL
                          CHECK (source IN ('purchase_contract', 'bmf_tool', 'appraisal')),
    building_share        numeric(7,6) NOT NULL CHECK (building_share > 0 AND building_share < 1),
    evidence_document_id  uuid,
    inputs                jsonb CHECK (inputs IS NULL OR jsonb_typeof(inputs) = 'object'),
    note                  text,
    recorded_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, property_id, id),
    FOREIGN KEY (workspace_id, property_id) REFERENCES property (workspace_id, id),
    FOREIGN KEY (workspace_id, evidence_document_id) REFERENCES document (workspace_id, id),
    CHECK (source <> 'appraisal' OR evidence_document_id IS NOT NULL),
    CHECK ((source = 'bmf_tool') = (inputs IS NOT NULL))
);
COMMENT ON TABLE property_price_split IS
    'Append-only candidate splits. property.selected_price_split_id picks the AfA basis.';

-- The selected split must belong to the same property.
ALTER TABLE property ADD CONSTRAINT property_selected_split_fk
    FOREIGN KEY (workspace_id, id, selected_price_split_id)
    REFERENCES property_price_split (workspace_id, property_id, id);

-- Unit -----------------------------------------------------------------------

CREATE TABLE unit (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id     uuid NOT NULL REFERENCES workspace (id),
    property_id      uuid NOT NULL,
    label            text NOT NULL CHECK (length(label) BETWEEN 1 AND 50),
    floor_area_m2    numeric(8,2) NOT NULL CHECK (floor_area_m2 > 0),
    rooms            numeric(4,1) CHECK (rooms > 0),
    mea_numerator    integer,
    mea_denominator  integer,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    UNIQUE (property_id, label),
    FOREIGN KEY (workspace_id, property_id) REFERENCES property (workspace_id, id),
    CHECK ((mea_numerator IS NULL) = (mea_denominator IS NULL)),
    CHECK (mea_numerator IS NULL
           OR (mea_numerator > 0 AND mea_denominator > 0 AND mea_numerator <= mea_denominator))
);
COMMENT ON COLUMN unit.mea_numerator IS 'Miteigentumsanteil in the WEG, as a fraction.';

-- Tenant (personal data) -----------------------------------------------------

CREATE TABLE tenant (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid NOT NULL REFERENCES workspace (id),
    full_name     text,
    email         text,
    phone         text,
    iban_masked   text CHECK (iban_masked ~ '^[A-Z]{2}[0-9]{2} \*{4} [A-Z0-9]{4}$'),
    iban_hmac     bytea CHECK (octet_length(iban_hmac) = 32),
    erased_at     timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    CHECK ((iban_masked IS NULL) = (iban_hmac IS NULL)),
    CHECK (erased_at IS NOT NULL OR full_name IS NOT NULL),
    CHECK (erased_at IS NULL OR num_nonnulls(full_name, email, phone, iban_masked, iban_hmac) = 0)
);
CREATE INDEX tenant_iban_hmac_idx ON tenant (workspace_id, iban_hmac) WHERE iban_hmac IS NOT NULL;
COMMENT ON TABLE tenant IS
    'Personal data. Erasure nulls every personal column and sets erased_at; the row and its '
    'PII-free events stay.';
COMMENT ON COLUMN tenant.iban_hmac IS
    'HMAC-SHA256 of the normalized IBAN with a key from the secrets manager; used for matching.';

-- Tenancy --------------------------------------------------------------------

CREATE TABLE tenancy (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id         uuid NOT NULL REFERENCES workspace (id),
    unit_id              uuid NOT NULL,
    signed_on            date,
    start_date           date NOT NULL,
    end_date             date,
    fixed_term_until     date,
    rent_model           text NOT NULL CHECK (rent_model IN ('fixed', 'staffel', 'index')),
    deposit_amount       numeric(14,2) NOT NULL DEFAULT 0 CHECK (deposit_amount >= 0),
    deposit_form         text NOT NULL DEFAULT 'none' CHECK (deposit_form IN (
                             'none', 'cash_account', 'bank_guarantee', 'surety')),
    deposit_bank         text,
    deposit_iban_masked  text CHECK (deposit_iban_masked ~ '^[A-Z]{2}[0-9]{2} \*{4} [A-Z0-9]{4}$'),
    deposit_iban_hmac    bytea CHECK (octet_length(deposit_iban_hmac) = 32),
    notice_given_on      date,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, unit_id) REFERENCES unit (workspace_id, id),
    CHECK (end_date IS NULL OR end_date >= start_date),
    CHECK (signed_on IS NULL OR signed_on <= start_date),
    CHECK (fixed_term_until IS NULL OR fixed_term_until >= start_date),
    CHECK ((deposit_form = 'none') = (deposit_amount = 0)),
    CHECK ((deposit_iban_masked IS NULL) = (deposit_iban_hmac IS NULL)),
    -- One tenancy per unit at a time. Dates are inclusive; null end_date = open-ended.
    CONSTRAINT tenancy_no_overlap EXCLUDE USING gist (
        workspace_id WITH =,
        unit_id WITH =,
        daterange(start_date, end_date, '[]') WITH &&
    )
);

CREATE TABLE tenancy_tenant (
    workspace_id  uuid NOT NULL REFERENCES workspace (id),
    tenancy_id    uuid NOT NULL,
    tenant_id     uuid NOT NULL,
    role          text NOT NULL CHECK (role IN ('primary', 'co_tenant')),
    PRIMARY KEY (tenancy_id, tenant_id),
    FOREIGN KEY (workspace_id, tenancy_id) REFERENCES tenancy (workspace_id, id),
    FOREIGN KEY (workspace_id, tenant_id) REFERENCES tenant (workspace_id, id)
);
-- At most one primary; "at least one" is a service-layer rule.
CREATE UNIQUE INDEX tenancy_one_primary_idx ON tenancy_tenant (tenancy_id) WHERE role = 'primary';

CREATE TABLE rent_schedule (
    id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id                uuid NOT NULL REFERENCES workspace (id),
    tenancy_id                  uuid NOT NULL,
    effective_from              date NOT NULL,
    cold_rent                   numeric(14,2) NOT NULL CHECK (cold_rent >= 0),
    operating_costs_prepayment  numeric(14,2) NOT NULL DEFAULT 0
                                CHECK (operating_costs_prepayment >= 0),
    reason                      text NOT NULL CHECK (reason IN (
                                    'initial', 'staffel_step', 'index_adjustment',
                                    'rent_increase', 'modernisation', 'agreement', 'correction')),
    supersedes_id               uuid UNIQUE,
    document_id                 uuid,
    recorded_at                 timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, tenancy_id, id),
    FOREIGN KEY (workspace_id, tenancy_id) REFERENCES tenancy (workspace_id, id),
    -- A correction supersedes a row of the same tenancy.
    FOREIGN KEY (workspace_id, tenancy_id, supersedes_id)
        REFERENCES rent_schedule (workspace_id, tenancy_id, id),
    FOREIGN KEY (workspace_id, document_id) REFERENCES document (workspace_id, id),
    CHECK ((reason = 'correction') = (supersedes_id IS NOT NULL)),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id)
);
COMMENT ON TABLE rent_schedule IS
    'Append-only dated rent rows (Kaltmiete + NK-Vorauszahlung per month). A wrong row is '
    'superseded by a correction row, never edited.';

-- Loan -----------------------------------------------------------------------

CREATE TABLE loan (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id         uuid NOT NULL REFERENCES workspace (id),
    property_id          uuid NOT NULL,
    lender               text NOT NULL CHECK (length(lender) > 0),
    contract_number      text,
    principal            numeric(14,2) NOT NULL CHECK (principal > 0),
    nominal_rate         numeric(7,6) NOT NULL CHECK (nominal_rate >= 0 AND nominal_rate < 1),
    monthly_payment      numeric(14,2) NOT NULL CHECK (monthly_payment > 0),
    disbursed_on         date NOT NULL,
    first_payment_on     date NOT NULL,
    fixed_until          date,
    follow_up_rate       numeric(7,6) CHECK (follow_up_rate >= 0 AND follow_up_rate < 1),
    account_iban_masked  text CHECK (account_iban_masked ~ '^[A-Z]{2}[0-9]{2} \*{4} [A-Z0-9]{4}$'),
    account_iban_hmac    bytea CHECK (octet_length(account_iban_hmac) = 32),
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, property_id) REFERENCES property (workspace_id, id),
    CHECK (first_payment_on > disbursed_on),
    CHECK (fixed_until IS NULL OR fixed_until >= first_payment_on),
    CHECK (follow_up_rate IS NULL OR fixed_until IS NOT NULL),
    CHECK ((account_iban_masked IS NULL) = (account_iban_hmac IS NULL))
);
COMMENT ON COLUMN loan.follow_up_rate IS
    'Rate after fixed_until (user-set). Null = keep nominal_rate.';

-- Document links (after every link target exists) ---------------------------

CREATE TABLE document_link (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid NOT NULL REFERENCES workspace (id),
    document_id   uuid NOT NULL,
    owner_id      uuid,
    property_id   uuid,
    unit_id       uuid,
    tenant_id     uuid,
    tenancy_id    uuid,
    loan_id       uuid,
    created_at    timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (workspace_id, document_id) REFERENCES document (workspace_id, id)
        ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, owner_id) REFERENCES owner (workspace_id, id),
    FOREIGN KEY (workspace_id, property_id) REFERENCES property (workspace_id, id),
    FOREIGN KEY (workspace_id, unit_id) REFERENCES unit (workspace_id, id),
    FOREIGN KEY (workspace_id, tenant_id) REFERENCES tenant (workspace_id, id),
    FOREIGN KEY (workspace_id, tenancy_id) REFERENCES tenancy (workspace_id, id),
    FOREIGN KEY (workspace_id, loan_id) REFERENCES loan (workspace_id, id),
    CHECK (num_nonnulls(owner_id, property_id, unit_id, tenant_id, tenancy_id, loan_id) = 1),
    UNIQUE NULLS NOT DISTINCT
        (document_id, owner_id, property_id, unit_id, tenant_id, tenancy_id, loan_id)
);
COMMENT ON TABLE document_link IS 'Links a document to exactly one entity (what it proves).';

-- Event log ------------------------------------------------------------------

CREATE TABLE event (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id       uuid NOT NULL REFERENCES workspace (id),
    seq                bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    type               text NOT NULL CHECK (type ~ '^[a-z_]+\.[a-z_]+$'),
    entity_type        text NOT NULL CHECK (entity_type IN (
                           'owner', 'property', 'unit', 'tenant', 'tenancy', 'loan', 'document')),
    entity_id          uuid NOT NULL,
    property_id        uuid,
    unit_id            uuid,
    effective_date     date,
    recorded_at        timestamptz NOT NULL DEFAULT now(),
    actor_type         text NOT NULL CHECK (actor_type IN ('user', 'system', 'ai')),
    actor_id           text NOT NULL CHECK (length(actor_id) > 0),
    correlation_id     uuid NOT NULL,
    corrects_event_id  uuid UNIQUE,
    payload            jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(payload) = 'object'),
    payload_version    smallint NOT NULL DEFAULT 1 CHECK (payload_version >= 1),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, corrects_event_id) REFERENCES event (workspace_id, id),
    CHECK (split_part(type, '.', 1) = entity_type),
    CHECK (corrects_event_id IS NULL OR corrects_event_id <> id),
    CHECK (unit_id IS NULL OR property_id IS NOT NULL)
);
-- No FKs from entity_id / property_id / unit_id: the log must outlive retention deletes.
CREATE INDEX event_entity_idx ON event (workspace_id, entity_type, entity_id, seq);
CREATE INDEX event_property_history_idx ON event (workspace_id, property_id, effective_date, seq)
    WHERE effective_date IS NOT NULL;
CREATE INDEX event_unit_history_idx ON event (workspace_id, unit_id, effective_date, seq)
    WHERE effective_date IS NOT NULL;
COMMENT ON TABLE event IS
    'Append-only domain log: the History timeline and the audit trail. No tenant PII in payload.';

-- Append-only guards ---------------------------------------------------------

CREATE TRIGGER event_append_only BEFORE UPDATE OR DELETE ON event
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER event_no_truncate BEFORE TRUNCATE ON event
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER rent_schedule_append_only BEFORE UPDATE OR DELETE ON rent_schedule
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER property_price_split_append_only BEFORE UPDATE OR DELETE ON property_price_split
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER document_file_immutable BEFORE UPDATE ON document_file
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER document_extraction_immutable BEFORE UPDATE ON document_extraction
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
"""


def _rls(table: str) -> str:
    key = "id" if table == "workspace" else "workspace_id"
    return f"""
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON {table}
    USING ({key} = current_workspace_id())
    WITH CHECK ({key} = current_workspace_id());
"""


def _grants() -> str:
    lines = [f"GRANT {privs} ON {table} TO {APP_ROLE};" for table, privs in APP_GRANTS.items()]
    body = "\n        ".join(lines)
    return f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        {body}
    ELSE
        RAISE NOTICE 'role {APP_ROLE} does not exist; skipping grants';
    END IF;
END
$$;
"""


def upgrade() -> None:
    assert set(APP_GRANTS) == set(TABLES)
    op.execute(FUNCTIONS)
    op.execute(DDL)
    for table in TABLES:
        op.execute(_rls(table))
    op.execute(_grants())


def downgrade() -> None:
    op.execute("ALTER TABLE property DROP CONSTRAINT property_selected_split_fk")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP FUNCTION forbid_mutation()")
    op.execute("DROP FUNCTION current_workspace_id()")
    # btree_gist is left installed: other schemas may depend on it.
