"""Phase 6 step 0: every account of the household, its owners, assets, movements and prices.

Spec: docs/phases/phase-6.md §10, D156, D157, D162, D163, D165-D169, D171, D173-D175.
- `account` gains `kind` (bank_current, bank_savings, depot, exchange, wallet), `purpose`
  (rental, private), `provider`, `currency` (EUR only in this phase), `external_ref_masked` and
  `external_ref_hmac` (a depot's or exchange's own number, masked and HMAC'd like an IBAN:
  bytea of 32 bytes as iban_hmac, not the text §10 names, so both HMACs are one type). The IBAN
  columns become nullable; account_identity_check says a bank kind has an IBAN and no external
  reference, any other kind no IBAN.
- The data step of D156/D171: every existing account is `kind = bank_current` (the column
  default) and `is_owner_account = true` (the flag now means "one of the household's own
  accounts"); `purpose = rental` for the account that has a statement_import, `private`
  otherwise. A workspace with two accounts that have imports is refused: the migration cannot
  pick. The services read `purpose` from this migration on (phase 6 step 0).
- Eight tables, each with workspace_id, UNIQUE (workspace_id, id), composite FKs and forced
  row-level security keyed on the workspace:
  - `account_ownership` (D157): append-only; superseded_by is set once, to a row of the same
    account (superseded_once), the shares' Σ = 1 is the service's (as property_ownership).
  - `asset` (D167): mutable (name and price-source ids, with events); D171's "append-only" list
    names it, §10 and §12 (PATCH /assets/{id}) say mutable, read as mutable.
  - `asset_movement` (D165, D166, D175): append-only like allocation (D23): status moves
    proposed -> confirmed only, superseded_by is set once to a movement of the same account
    (movement_forward); movement_hash unique per account where present (the dedup key).
  - `movement_import` (D166): mutable with status, like statement_import.
  - `holding_statement` (D165 (3), D166), `price` (D167, D174), `valuation_snapshot` (D167):
    append-only, forbid_mutation. A daily feed's price is unique per asset, day and source;
    trade, statement and user prices are not (two trades on one day).
  - `transfer_link` (D162): append-only, superseded_by set once (superseded_once); exactly one
    leg of each side. "One current link per leg" is the service's: a replacement is inserted
    before the old row can point at it, so a partial unique index would refuse it.
- `bank_connection.credential_slot` (D163) under a pattern CHECK, default primary;
  `workspace.ai_private_accounts` (D168), default off.
- CHECKs rebuilt (named <table>_<column>_check, dropped without IF EXISTS so a wrong name fails
  loudly): allocation.category and matching_rule.category (+ the codes of D159 and D165) with
  matching_rule_categories_check (0012's element CHECK); allocation.method (+ matcher_transfer,
  D162); document.subtype (+ exchange_export, distribution_note); statement_import.format
  (+ dkb_csv, D173); document_extraction.kind and proposal.kind (+ trade_confirmation,
  distribution_note, depot_report: the readings of D165, built in step 4); event.entity_type
  (+ asset, movement, transfer, §11). D171 names four rebuilt CHECKs; the last four are needed
  by the enums the spec adds and are rebuilt here too. Mirrors hausbuch.schemas.

The downgrade is refused while it would drop data or leave a row the old CHECKs refuse (D171):
any row of the eight tables, an account of another kind than bank_current or a private account
with imports, a new category, method, subtype, kind, format or entity type in use, a connection
with another slot than primary, or a workspace with the switch on. It lifts FORCE RLS for each
check, since the owner has no workspace set. It then restores is_owner_account from purpose
(the inverse of the data step), drops the tables and columns and restores 0024's CHECKs.

Revision ID: 0025_household_accounts
Revises: 0024_bank_statements
Create Date: 2026-11-12
"""

import json

from alembic import op

revision = "0025_household_accounts"
down_revision = "0024_bank_statements"
branch_labels = None
depends_on = None

APP_ROLE = "hausbuch_app"

# In creation order (dropped in reverse).
TABLES = (
    "account_ownership",
    "asset",
    "movement_import",
    "asset_movement",
    "holding_statement",
    "transfer_link",
    "price",
    "valuation_snapshot",
)
APP_GRANTS = {
    "account_ownership": "SELECT, INSERT, UPDATE",  # append-only; superseded_by set once
    "asset": "SELECT, INSERT, UPDATE",  # mutable + events
    "movement_import": "SELECT, INSERT, UPDATE",  # mutable + events
    "asset_movement": "SELECT, INSERT, UPDATE",  # append-only; status and superseded_by
    "holding_statement": "SELECT, INSERT",  # append-only
    "transfer_link": "SELECT, INSERT, UPDATE",  # append-only; superseded_by set once
    "price": "SELECT, INSERT",  # append-only
    "valuation_snapshot": "SELECT, INSERT",  # append-only
}
FORBID_MUTATION = ("holding_statement", "price", "valuation_snapshot")
SUPERSEDED_ONCE = ("account_ownership", "transfer_link")

# --- Value lists ---------------------------------------------------------------------------

ACCOUNT_KINDS = ("bank_current", "bank_savings", "depot", "exchange", "wallet")
BANK_KINDS = ("bank_current", "bank_savings")
ACCOUNT_PURPOSES = ("rental", "private")
ASSET_KINDS = ("cash", "etf", "stock", "crypto")
MOVEMENT_KINDS = (
    "acquisition",
    "disposal",
    "internal_transfer_in",
    "internal_transfer_out",
    "fee",
    "staking_reward",
    "cashback_reward",
    "distribution",
    "capital_income_tax",
    "uncategorized",
)
MOVEMENT_SOURCES = ("csv", "trade_confirmation", "distribution_note", "report", "user")
DOCUMENTED_SOURCES = ("trade_confirmation", "distribution_note", "report")
MOVEMENT_STATUSES = ("proposed", "confirmed")
MOVEMENT_IMPORT_FORMATS = ("bison_csv",)
IMPORT_STATUSES = ("imported", "reconciled", "unreconciled", "failed")
HOLDING_SOURCES = ("report", "user")
TRANSFER_KINDS = ("bank", "exchange_eur", "depot_cash")
TRANSFER_MATCHED_BY = ("matcher", "user")
PRICE_SOURCES = (
    "trade",
    "statement",
    "issuer_nav",
    "coingecko_daily",
    "kraken_daily",
    "ecb_fx",
    "user",
)
DAILY_PRICE_SOURCES = ("issuer_nav", "coingecko_daily", "kraken_daily", "ecb_fx")

# 0017's allocation.category / matching_rule.category list, in its order.
OLD_CATEGORIES = (
    "rent_cold",
    "operating_costs_prepayment",
    "operating_costs_settlement",
    "deposit_in",
    "deposit_out",
    "owner_transfer_in",
    "owner_transfer_out",
    "loan_interest",
    "loan_principal",
    "loan_fees",
    "hausgeld_umlagefaehig",
    "hausgeld_verwaltung",
    "hausgeld_ruecklage",
    "grundsteuer",
    "insurance",
    "repairs",
    "renovation",
    "acquisition_cost",
    "professional_fees",
    "bank_fees",
    "other_income",
    "other_expense",
    "uncategorized",
    "loan_disbursement",
    "purchase_price",
    "property_management",
    "electricity",
)
# D159 (household and transfer codes) and D165 (depot codes).
NEW_CATEGORIES = (
    "hh_salary",
    "hh_child_benefit",
    "hh_tax_refund",
    "hh_capital_income",
    "hh_other_income",
    "hh_housing",
    "hh_groceries",
    "hh_family_child",
    "hh_mobility",
    "hh_insurance",
    "hh_subscriptions",
    "hh_leisure_travel",
    "hh_health",
    "hh_other",
    "hh_donation",
    "hh_household_services",
    "hh_childcare",
    "hh_property_cost",
    "hh_tax_payment",
    "internal_transfer_in",
    "internal_transfer_out",
    "hh_cash",
    "hh_credit_card",
    "security_purchase",
    "security_sale",
    "distribution",
    "capital_income_tax",
    "depot_fee",
)
# 0007's allocation.method list.
OLD_METHODS = (
    "rule",
    "matcher_rent",
    "matcher_loan",
    "matcher_owner",
    "matcher_hausgeld",
    "document_match",
    "llm",
    "user",
)
NEW_METHODS = ("matcher_transfer",)
# 0023's document.subtype list, in its order.
OLD_SUBTYPES = (
    "purchase_contract",
    "price_split_evidence",
    "appraisal",
    "loan_contract",
    "lease",
    "handover_protocol",
    "deposit_record",
    "cost_basis_evidence",
    "grundsteuerbescheid",
    "einkommensteuerbescheid",
    "land_register_extract",
    "declaration_of_division",
    "land_charge_deed",
    "energy_certificate",
    "building_records",
    "insurance_policy",
    "management_contract",
    "service_contract",
    "weg_minutes",
    "weg_invitation",
    "special_levy",
    "repayment_schedule",
    "loan_terms_change",
    "loan_statement",
    "lease_amendment",
    "rent_increase",
    "termination_notice",
    "tenant_self_disclosure",
    "landlord_confirmation",
    "operating_costs_statement",
    "tenant_correspondence",
    "utility_bill",
    "quote",
    "chimney_sweep_notice",
    "prepayment_notice",
    "tax_return_copy",
    "donation_receipt",
    "childcare_invoice",
    "household_services_invoice",
    "insurance_contribution_certificate",
    "pension_contribution_certificate",
    "benefit_notice",
    "child_benefit_notice",
    "employment_contract",
    "employer_certificate",
    "work_expense_invoice",
    "depot_statement",
    "depot_report",
    "trade_confirmation",
    "capital_income_tax_certificate",
    "financing_invoice",
    "payment_proof",
)
NEW_SUBTYPES = ("exchange_export", "distribution_note")
# 0023's document_extraction.kind list (proposal.kind: the same without manual).
OLD_EXTRACTION_KINDS = (
    "manual",
    "purchase_contract",
    "loan_contract",
    "lease",
    "handover_protocol",
    "deposit_record",
    "invoice",
    "bescheid",
    "grundsteuerbescheid",
    "wirtschaftsplan",
    "hausgeldabrechnung",
    "zinsbescheinigung",
    "statement",
    "lohnsteuerbescheinigung",
    "gehaltsabrechnung",
    "einkommensteuerbescheid",
    "general",
    "management_contract",
    "payment_proof",
)
NEW_EXTRACTION_KINDS = ("trade_confirmation", "distribution_note", "depot_report")
# 0006's statement_import.format list.
OLD_FORMATS = ("csv_camt", "camt", "pdf")
NEW_FORMATS = ("dkb_csv",)
# 0005's event.entity_type list.
OLD_ENTITY_TYPES = (
    "owner",
    "property",
    "unit",
    "tenant",
    "tenancy",
    "loan",
    "document",
    "user",
    "proposal",
    "account",
    "statement",
    "transaction",
    "rule",
    "workspace",
)
NEW_ENTITY_TYPES = ("asset", "movement", "transfer")


def _sql_list(names: tuple[str, ...]) -> str:
    return ", ".join(f"'{n}'" for n in names)


def _proposal_kinds(kinds: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(k for k in kinds if k != "manual")


# --- Functions -----------------------------------------------------------------------------

FUNCTIONS = """
CREATE FUNCTION superseded_once() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- Append-only rows whose one change is naming the row that replaced them, once.
    IF (to_jsonb(NEW) - 'superseded_by') IS DISTINCT FROM (to_jsonb(OLD) - 'superseded_by')
       OR OLD.superseded_by IS NOT NULL
       OR NEW.superseded_by IS NULL THEN
        RAISE EXCEPTION 'UPDATE on % is not allowed: rows are append-only', TG_TABLE_NAME
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Only superseded_by is set, once; record a change as new rows.';
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION movement_forward() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- Append-only rows (D23 pattern): status moves proposed -> confirmed, and superseded_by
    -- is set once; nothing else changes.
    IF (to_jsonb(NEW) - 'status' - 'superseded_by')
           IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'superseded_by')
       OR NOT (NEW.status = OLD.status
               OR (OLD.status = 'proposed' AND NEW.status = 'confirmed'))
       OR (OLD.superseded_by IS NOT NULL
           AND NEW.superseded_by IS DISTINCT FROM OLD.superseded_by)
       OR (OLD.superseded_by IS NULL AND NEW.superseded_by IS NULL
           AND NEW.status = OLD.status) THEN
        RAISE EXCEPTION 'UPDATE on asset_movement is not allowed: rows are append-only'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Only status moves forward (proposed -> confirmed) and superseded_by '
                         'is set once; record a correction as a new movement.';
    END IF;
    RETURN NEW;
END
$$;
"""

# --- account, bank_connection, workspace ---------------------------------------------------

ACCOUNT_COLUMNS = rf"""
ALTER TABLE account
    ADD COLUMN kind text NOT NULL DEFAULT 'bank_current'
        CHECK (kind IN ({_sql_list(ACCOUNT_KINDS)})),
    ADD COLUMN purpose text NOT NULL DEFAULT 'private'
        CHECK (purpose IN ({_sql_list(ACCOUNT_PURPOSES)})),
    ADD COLUMN provider text CHECK (length(provider) BETWEEN 1 AND 100),
    ADD COLUMN currency text NOT NULL DEFAULT 'EUR' CHECK (currency IN ('EUR')),
    ADD COLUMN external_ref_masked text CHECK (external_ref_masked ~ '^\*{{4}} [A-Z0-9]{{4}}$'),
    ADD COLUMN external_ref_hmac bytea CHECK (octet_length(external_ref_hmac) = 32),
    ALTER COLUMN iban_masked DROP NOT NULL,
    ALTER COLUMN iban_hmac DROP NOT NULL,
    ADD CONSTRAINT account_identity_check CHECK (
        (iban_hmac IS NULL) = (iban_masked IS NULL)
        AND (external_ref_hmac IS NULL) = (external_ref_masked IS NULL)
        AND (kind IN ({_sql_list(BANK_KINDS)})) = (iban_hmac IS NOT NULL)
        AND (kind NOT IN ({_sql_list(BANK_KINDS)}) OR external_ref_hmac IS NULL)
    ),
    ADD CONSTRAINT account_workspace_id_external_ref_hmac_key
        UNIQUE (workspace_id, external_ref_hmac);
COMMENT ON TABLE account IS
    'Every account of the household (phase 6 D156): kind, purpose (the rental account is '
    'purpose = rental), provider. is_owner_account = one of the household''s own accounts.';
COMMENT ON COLUMN account.purpose IS
    'D156: rental for the rental account, private for every other; set by 0025''s data step.';
COMMENT ON COLUMN account.external_ref_hmac IS
    'D156, D164: a depot''s or exchange''s own number, keyed HMAC like iban_hmac.';

ALTER TABLE bank_connection ADD COLUMN credential_slot text NOT NULL DEFAULT 'primary'
    CHECK (credential_slot ~ '^[a-z][a-z0-9_]{{0,31}}$');
COMMENT ON COLUMN bank_connection.credential_slot IS
    'D163: the login whose PIN is the container secret HAUSBUCH_FINTS_PIN_<SLOT>; primary is '
    'HAUSBUCH_FINTS_PIN. A label, never a secret.';

ALTER TABLE workspace ADD COLUMN ai_private_accounts boolean NOT NULL DEFAULT false;
COMMENT ON COLUMN workspace.ai_private_accounts IS
    'D168: private accounts'' lines may be sent to the model. Changed with '
    'workspace.settings_changed, after both owners'' consent.';
"""

# D156/D171: kind = bank_current by the default; is_owner_account on every account; rental =
# the account with imports. Lifts FORCE RLS: the owner has no workspace set.
DATA_STEP = """
DO $$
BEGIN
    ALTER TABLE account NO FORCE ROW LEVEL SECURITY;
    ALTER TABLE statement_import NO FORCE ROW LEVEL SECURITY;
    IF EXISTS (
        SELECT 1 FROM account a
        WHERE EXISTS (SELECT 1 FROM statement_import s WHERE s.account_id = a.id)
        GROUP BY a.workspace_id HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'migration 0025_household_accounts refused: a workspace has two '
                        'accounts with imported statements'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'The migration cannot pick the rental account (D171).';
    END IF;
    UPDATE account a SET
        is_owner_account = true,
        purpose = CASE
            WHEN EXISTS (SELECT 1 FROM statement_import s WHERE s.account_id = a.id)
            THEN 'rental' ELSE 'private' END;
    ALTER TABLE account FORCE ROW LEVEL SECURITY;
    ALTER TABLE statement_import FORCE ROW LEVEL SECURITY;
END
$$;
"""

# --- The eight tables ----------------------------------------------------------------------

DDL = rf"""
CREATE TABLE account_ownership (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id       uuid NOT NULL REFERENCES workspace (id),
    account_id         uuid NOT NULL,
    owner_id           uuid NOT NULL,
    share_numerator    integer NOT NULL CHECK (share_numerator > 0),
    share_denominator  integer NOT NULL CHECK (share_denominator > 0),
    reason             text CHECK (length(reason) BETWEEN 1 AND 500),
    effective_from     date NOT NULL,
    superseded_by      uuid,
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, account_id, id),
    FOREIGN KEY (workspace_id, account_id) REFERENCES account (workspace_id, id),
    FOREIGN KEY (workspace_id, owner_id) REFERENCES owner (workspace_id, id),
    -- Superseded by a row of the same account.
    FOREIGN KEY (workspace_id, account_id, superseded_by)
        REFERENCES account_ownership (workspace_id, account_id, id),
    CHECK (share_numerator <= share_denominator),
    CHECK (superseded_by <> id)
);
CREATE INDEX account_ownership_account_idx ON account_ownership (workspace_id, account_id);
COMMENT ON TABLE account_ownership IS
    'D157: owner shares per account, as property_ownership; the current set (superseded_by '
    'null) sums to exactly 1, enforced by the service. reason: why this split.';

CREATE TABLE asset (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid NOT NULL REFERENCES workspace (id),
    kind          text NOT NULL CHECK (kind IN ({_sql_list(ASSET_KINDS)})),
    symbol        text CHECK (symbol ~ '^[A-Z0-9]{{1,12}}$'),
    isin          text CHECK (isin ~ '^[A-Z]{{2}}[A-Z0-9]{{9}}[0-9]$'),
    name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    currency      text NOT NULL DEFAULT 'EUR' CHECK (currency ~ '^[A-Z]{{3}}$'),
    coingecko_id  text CHECK (length(coingecko_id) BETWEEN 1 AND 100),
    kraken_pair   text CHECK (length(kraken_pair) BETWEEN 1 AND 20),
    decimals      integer NOT NULL DEFAULT 8 CHECK (decimals BETWEEN 0 AND 10),
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, isin),
    UNIQUE (workspace_id, symbol),
    CHECK (symbol IS NOT NULL OR isin IS NOT NULL),
    CHECK (kind NOT IN ('etf', 'stock') OR isin IS NOT NULL)
);
COMMENT ON TABLE asset IS
    'D167: what a position is held in (EUR is an asset of kind cash). Mutable: the name and '
    'the price-source ids, with asset.updated events.';

CREATE TABLE movement_import (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid NOT NULL REFERENCES workspace (id),
    account_id    uuid NOT NULL,
    document_id   uuid NOT NULL,
    format        text NOT NULL CHECK (format IN ({_sql_list(MOVEMENT_IMPORT_FORMATS)})),
    rows_total    integer NOT NULL DEFAULT 0 CHECK (rows_total >= 0),
    rows_new      integer NOT NULL DEFAULT 0 CHECK (rows_new >= 0),
    rows_skipped  integer NOT NULL DEFAULT 0 CHECK (rows_skipped >= 0),
    first_at      timestamptz,
    last_at       timestamptz,
    status        text NOT NULL DEFAULT 'imported'
                  CHECK (status IN ({_sql_list(IMPORT_STATUSES)})),
    error_code    text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, account_id, id),
    FOREIGN KEY (workspace_id, account_id) REFERENCES account (workspace_id, id),
    FOREIGN KEY (workspace_id, document_id) REFERENCES document (workspace_id, id),
    CHECK (rows_new + rows_skipped <= rows_total),
    CHECK ((first_at IS NULL) = (last_at IS NULL)),
    CHECK (last_at >= first_at),
    CHECK ((error_code IS NOT NULL) = (status IN ('unreconciled', 'failed')))
);
CREATE INDEX movement_import_account_idx ON movement_import (workspace_id, account_id);
COMMENT ON TABLE movement_import IS
    'D166: one exchange export (the statement_import of a CSV of movements). imported = '
    'waiting for the typed balances.';

CREATE TABLE asset_movement (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id      uuid NOT NULL REFERENCES workspace (id),
    account_id        uuid NOT NULL,
    asset_id          uuid NOT NULL,
    kind              text NOT NULL CHECK (kind IN ({_sql_list(MOVEMENT_KINDS)})),
    quantity          numeric(28,10) NOT NULL,
    eur_amount        numeric(14,2),
    fee_eur           numeric(14,2) CHECK (fee_eur >= 0),
    fee_quantity      numeric(28,10) CHECK (fee_quantity >= 0),
    tax_withheld_eur  numeric(14,2) CHECK (tax_withheld_eur >= 0),
    occurred_at       timestamptz NOT NULL,
    external_ref      text CHECK (length(external_ref) BETWEEN 1 AND 200),
    network           text CHECK (length(network) BETWEEN 1 AND 50),
    tx_hash           text CHECK (length(tx_hash) BETWEEN 1 AND 200),
    movement_hash     text CHECK (movement_hash ~ '^[0-9a-f]{{64}}$'),
    source            text NOT NULL CHECK (source IN ({_sql_list(MOVEMENT_SOURCES)})),
    document_id       uuid,
    transaction_id    uuid,
    status            text NOT NULL DEFAULT 'proposed'
                      CHECK (status IN ({_sql_list(MOVEMENT_STATUSES)})),
    superseded_by     uuid,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, account_id, id),
    FOREIGN KEY (workspace_id, account_id) REFERENCES account (workspace_id, id),
    FOREIGN KEY (workspace_id, asset_id) REFERENCES asset (workspace_id, id),
    FOREIGN KEY (workspace_id, document_id) REFERENCES document (workspace_id, id),
    FOREIGN KEY (workspace_id, transaction_id) REFERENCES transaction (workspace_id, id),
    -- A correction supersedes a movement of the same account.
    FOREIGN KEY (workspace_id, account_id, superseded_by)
        REFERENCES asset_movement (workspace_id, account_id, id),
    CHECK (source NOT IN ({_sql_list(DOCUMENTED_SOURCES)}) OR document_id IS NOT NULL),
    CHECK (superseded_by <> id)
);
-- The dedup key of an exported row (D166): a re-import adds nothing. Superseded rows keep
-- theirs, so a correction never carries the hash and a re-import still finds the original.
CREATE UNIQUE INDEX asset_movement_hash_key
    ON asset_movement (workspace_id, account_id, movement_hash)
    WHERE movement_hash IS NOT NULL;
CREATE INDEX asset_movement_account_idx ON asset_movement (workspace_id, account_id, asset_id);
COMMENT ON TABLE asset_movement IS
    'D165, D166, D175: one movement of an asset on an account, quantity signed (in positive). '
    'Append-only: status proposed -> confirmed, superseded_by set once.';

CREATE TABLE holding_statement (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id        uuid NOT NULL REFERENCES workspace (id),
    account_id          uuid NOT NULL,
    asset_id            uuid NOT NULL,
    as_of               date NOT NULL,
    declared_units      numeric(28,10) NOT NULL CHECK (declared_units >= 0),
    declared_value_eur  numeric(14,2) CHECK (declared_value_eur >= 0),
    source              text NOT NULL CHECK (source IN ({_sql_list(HOLDING_SOURCES)})),
    document_id         uuid,
    import_id           uuid,
    reconciled          boolean,
    difference_units    numeric(28,10),
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, account_id) REFERENCES account (workspace_id, id),
    FOREIGN KEY (workspace_id, asset_id) REFERENCES asset (workspace_id, id),
    FOREIGN KEY (workspace_id, document_id) REFERENCES document (workspace_id, id),
    -- The import it was typed for is the same account's.
    FOREIGN KEY (workspace_id, account_id, import_id)
        REFERENCES movement_import (workspace_id, account_id, id)
);
CREATE INDEX holding_statement_account_idx ON holding_statement (workspace_id, account_id, as_of);
COMMENT ON TABLE holding_statement IS
    'D165 (3), D166: what a report or the user declares an account held of an asset on as_of, '
    'and whether the confirmed movements agreed. Append-only.';

CREATE TABLE transfer_link (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id        uuid NOT NULL REFERENCES workspace (id),
    kind                text NOT NULL CHECK (kind IN ({_sql_list(TRANSFER_KINDS)})),
    out_transaction_id  uuid,
    out_movement_id     uuid,
    in_transaction_id   uuid,
    in_movement_id      uuid,
    matched_by          text NOT NULL CHECK (matched_by IN ({_sql_list(TRANSFER_MATCHED_BY)})),
    superseded_by       uuid,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, out_transaction_id) REFERENCES transaction (workspace_id, id),
    FOREIGN KEY (workspace_id, out_movement_id) REFERENCES asset_movement (workspace_id, id),
    FOREIGN KEY (workspace_id, in_transaction_id) REFERENCES transaction (workspace_id, id),
    FOREIGN KEY (workspace_id, in_movement_id) REFERENCES asset_movement (workspace_id, id),
    FOREIGN KEY (workspace_id, superseded_by) REFERENCES transfer_link (workspace_id, id),
    CHECK (num_nonnulls(out_transaction_id, out_movement_id) = 1),
    CHECK (num_nonnulls(in_transaction_id, in_movement_id) = 1),
    CHECK (superseded_by <> id)
);
CREATE INDEX transfer_link_out_tx_idx ON transfer_link (workspace_id, out_transaction_id);
CREATE INDEX transfer_link_in_tx_idx ON transfer_link (workspace_id, in_transaction_id);
COMMENT ON TABLE transfer_link IS
    'D162: the two legs of one transfer between own accounts (a bank line or an asset '
    'movement each). Append-only; superseded_by set once. One current link per leg is the '
    'service''s.';

CREATE TABLE price (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid NOT NULL REFERENCES workspace (id),
    asset_id      uuid NOT NULL,
    "on"          date NOT NULL,
    price_eur     numeric(20,8) NOT NULL CHECK (price_eur > 0),
    source        text NOT NULL CHECK (source IN ({_sql_list(PRICE_SOURCES)})),
    fetched_at    timestamptz NOT NULL DEFAULT now(),
    document_id   uuid,
    raw           jsonb NOT NULL DEFAULT '{{}}'::jsonb CHECK (jsonb_typeof(raw) = 'object'),
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, asset_id) REFERENCES asset (workspace_id, id),
    FOREIGN KEY (workspace_id, document_id) REFERENCES document (workspace_id, id)
);
-- A daily feed prices an asset once a day; a trade, a statement or the user may price it
-- several times on one day (two trades).
CREATE UNIQUE INDEX price_daily_key ON price (workspace_id, asset_id, "on", source)
    WHERE source IN ({_sql_list(DAILY_PRICE_SOURCES)});
CREATE INDEX price_asset_idx ON price (workspace_id, asset_id, "on");
COMMENT ON TABLE price IS
    'D167, D174: a price in EUR per unit of an asset on a day, with its source; raw keeps the '
    'native figure and currency. Append-only.';

CREATE TABLE valuation_snapshot (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id      uuid NOT NULL REFERENCES workspace (id),
    account_id        uuid NOT NULL,
    asset_id          uuid,
    as_of             date NOT NULL,
    units             numeric(28,10),
    price_eur         numeric(20,8) CHECK (price_eur > 0),
    price_source      text CHECK (price_source IN ({_sql_list(PRICE_SOURCES)})),
    value_eur         numeric(14,2) NOT NULL,
    net_worth_run_id  uuid NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    -- One row per account and asset (null: the bank balance) per run.
    UNIQUE NULLS NOT DISTINCT (workspace_id, net_worth_run_id, account_id, asset_id),
    FOREIGN KEY (workspace_id, account_id) REFERENCES account (workspace_id, id),
    FOREIGN KEY (workspace_id, asset_id) REFERENCES asset (workspace_id, id),
    CHECK ((price_eur IS NULL) = (price_source IS NULL))
);
CREATE INDEX valuation_snapshot_as_of_idx ON valuation_snapshot (workspace_id, as_of);
COMMENT ON TABLE valuation_snapshot IS
    'D167: a month-end valuation of one position (asset_id null: a bank balance), written by '
    'one net-worth run. Immutable.';
"""


def _triggers() -> str:
    lines = []
    for table in FORBID_MUTATION:
        lines.append(f"""
CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table}
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();""")
    for table in SUPERSEDED_ONCE:
        lines.append(f"""
CREATE TRIGGER {table}_superseded_once BEFORE UPDATE ON {table}
    FOR EACH ROW EXECUTE FUNCTION superseded_once();
CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table}
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();""")
    lines.append("""
CREATE TRIGGER asset_movement_forward BEFORE UPDATE ON asset_movement
    FOR EACH ROW EXECUTE FUNCTION movement_forward();
CREATE TRIGGER asset_movement_no_delete BEFORE DELETE ON asset_movement
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();""")
    for table in (*FORBID_MUTATION, *SUPERSEDED_ONCE, "asset_movement"):
        lines.append(f"""
CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table}
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();""")
    return "\n".join(lines)


def _rls(table: str) -> str:
    return f"""
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON {table}
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());
"""


def _if_app_role(body: str) -> str:
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


# --- The rebuilt CHECKs --------------------------------------------------------------------


def _set_value_list(table: str, column: str, names: tuple[str, ...]) -> None:
    """Replace a column's value-list CHECK, named <table>_<column>_check (no IF EXISTS)."""
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_{column}_check")
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT {table}_{column}_check"
        f" CHECK ({column} IN ({_sql_list(names)}))"
    )


def _set_categories(names: tuple[str, ...]) -> None:
    _set_value_list("allocation", "category", names)
    _set_value_list("matching_rule", "category", names)
    # 0012's element CHECK, with the same shape and the new list (as 0017).
    known = json.dumps(list(names))
    op.execute("ALTER TABLE matching_rule DROP CONSTRAINT matching_rule_categories_check")
    op.execute(
        "ALTER TABLE matching_rule ADD CONSTRAINT matching_rule_categories_check CHECK ("
        " categories IS NULL OR (jsonb_typeof(categories) = 'array'"
        " AND jsonb_array_length(categories) >= 2"
        f" AND categories <@ '{known}'::jsonb))"
    )


def _set_checks(new: bool) -> None:
    def pick(old: tuple[str, ...], added: tuple[str, ...]) -> tuple[str, ...]:
        return (*old, *added) if new else old

    _set_categories(pick(OLD_CATEGORIES, NEW_CATEGORIES))
    _set_value_list("allocation", "method", pick(OLD_METHODS, NEW_METHODS))
    _set_value_list("document", "subtype", pick(OLD_SUBTYPES, NEW_SUBTYPES))
    kinds = pick(OLD_EXTRACTION_KINDS, NEW_EXTRACTION_KINDS)
    _set_value_list("document_extraction", "kind", kinds)
    _set_value_list("proposal", "kind", _proposal_kinds(kinds))
    _set_value_list("statement_import", "format", pick(OLD_FORMATS, NEW_FORMATS))
    _set_value_list("event", "entity_type", pick(OLD_ENTITY_TYPES, NEW_ENTITY_TYPES))


# --- The downgrade -------------------------------------------------------------------------

_NEW_CATEGORY_IN_RULE = " OR ".join(f"categories @> '[\"{c}\"]'::jsonb" for c in NEW_CATEGORIES)
_DATA_CHECKS = (
    *((table, f"SELECT 1 FROM {table}", f"table {table} holds rows") for table in TABLES),
    (
        "account",
        "SELECT 1 FROM account WHERE kind <> 'bank_current'",
        "an account is of another kind than bank_current",
    ),
    (
        "account",
        "SELECT 1 FROM account a WHERE a.purpose = 'private'"
        " AND EXISTS (SELECT 1 FROM statement_import s WHERE s.account_id = a.id)",
        "a private account holds imported statements",
    ),
    (
        "allocation",
        f"SELECT 1 FROM allocation WHERE category IN ({_sql_list(NEW_CATEGORIES)})"
        f" OR method IN ({_sql_list(NEW_METHODS)})",
        "an allocation uses a new category or method",
    ),
    (
        "matching_rule",
        f"SELECT 1 FROM matching_rule WHERE category IN ({_sql_list(NEW_CATEGORIES)})"
        f" OR {_NEW_CATEGORY_IN_RULE}",
        "a learned rule uses a new category",
    ),
    (
        "document",
        f"SELECT 1 FROM document WHERE subtype IN ({_sql_list(NEW_SUBTYPES)})",
        "a document has a new subtype",
    ),
    (
        "document_extraction",
        f"SELECT 1 FROM document_extraction WHERE kind IN ({_sql_list(NEW_EXTRACTION_KINDS)})",
        "an extraction has a new kind",
    ),
    (
        "proposal",
        f"SELECT 1 FROM proposal WHERE kind IN ({_sql_list(NEW_EXTRACTION_KINDS)})",
        "a proposal has a new kind",
    ),
    (
        "statement_import",
        f"SELECT 1 FROM statement_import WHERE format IN ({_sql_list(NEW_FORMATS)})",
        "an import is of format dkb_csv",
    ),
    (
        "event",
        f"SELECT 1 FROM event WHERE entity_type IN ({_sql_list(NEW_ENTITY_TYPES)})",
        "an event is about an asset, a movement or a transfer",
    ),
    (
        "bank_connection",
        "SELECT 1 FROM bank_connection WHERE credential_slot <> 'primary'",
        "a connection uses another slot than primary",
    ),
    (
        "workspace",
        "SELECT 1 FROM workspace WHERE ai_private_accounts",
        "a workspace sends private accounts to the model",
    ),
)

# Every table a check reads (the private-account check reads statement_import too), lifted
# once before the checks and forced again after them; a refusal rolls the lifts back.
_CHECKED = tuple(dict.fromkeys([*(t for t, _, _ in _DATA_CHECKS), "statement_import"]))
REFUSE_IF_DATA = (
    "DO $$\nBEGIN"
    + "".join(f"\n    ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;" for table in _CHECKED)
    + "".join(
        f"""
    IF EXISTS ({query}) THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: {what}'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Export and remove the data first; a downgrade never drops data.';
    END IF;"""
        for _, query, what in _DATA_CHECKS
    )
    + "".join(f"\n    ALTER TABLE {table} FORCE ROW LEVEL SECURITY;" for table in _CHECKED)
    + "\nEND\n$$;"
)

# The inverse of the data step: before 0025 only the owners' accounts carried the flag.
RESTORE_OWNER_FLAG = """
DO $$
BEGIN
    ALTER TABLE account NO FORCE ROW LEVEL SECURITY;
    UPDATE account SET is_owner_account = (purpose = 'private');
    ALTER TABLE account FORCE ROW LEVEL SECURITY;
END
$$;
"""


def upgrade() -> None:
    op.execute(FUNCTIONS)
    op.execute(ACCOUNT_COLUMNS)
    op.execute(DATA_STEP)
    op.execute(DDL)
    op.execute(_triggers())
    for table in TABLES:
        op.execute(_rls(table))
    _set_checks(new=True)
    lines = [f"GRANT {privs} ON {table} TO {APP_ROLE};" for table, privs in APP_GRANTS.items()]
    op.execute(_if_app_role("\n        ".join(lines)))


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    op.execute(RESTORE_OWNER_FLAG)
    _set_checks(new=False)
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
    op.execute("ALTER TABLE workspace DROP COLUMN ai_private_accounts")
    op.execute("ALTER TABLE bank_connection DROP COLUMN credential_slot")
    op.execute("""
ALTER TABLE account
    DROP CONSTRAINT account_workspace_id_external_ref_hmac_key,
    DROP CONSTRAINT account_identity_check,
    DROP COLUMN external_ref_hmac,
    DROP COLUMN external_ref_masked,
    DROP COLUMN currency,
    DROP COLUMN provider,
    DROP COLUMN purpose,
    DROP COLUMN kind,
    ALTER COLUMN iban_masked SET NOT NULL,
    ALTER COLUMN iban_hmac SET NOT NULL;
COMMENT ON TABLE account IS
    'Bank accounts. The rental account holds the transactions; owner accounts '
    '(is_owner_account) are only recognized by IBAN for owner transfers and hold none.';
""")
    op.execute("DROP FUNCTION movement_forward()")
    op.execute("DROP FUNCTION superseded_once()")
