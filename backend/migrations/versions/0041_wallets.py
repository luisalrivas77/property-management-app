"""Phase 7 step 0: wallets, chain syncs, movement groups, decoders, pairs, holdings exports
(docs/phases/phase-7.md §11, D179-D191).

- `wallet_address` (D179): one row per chain an own wallet is read on. The plaintext address
  lives here and nowhere else, because the app must ask the chain for it; everything that
  leaves the table carries `address_masked` (first four and last four characters) or the
  HMAC. The account must be a `wallet` (trigger, as the service checks too), the address
  matches its chain's format (the per-chain CHECK beside the length CHECK), `change` is a
  Bitcoin kind and `stake` a Solana one. Mutable only in `active` and `label`.
- `wallet_sync` (D182 (1)): one row per address and run, written `running` and finished
  once; only the run's outcome columns change, and only while it is running (trigger).
- `movement_group` (D183 (1)): one row per transaction that touches an own address. Status
  moves proposed -> confirmed and superseded_by is set once (as asset_movement), nothing
  else changes.
- `chain_decoder`, `chain_pair` (D191): a workspace's own decoder rows and confirmed bridge
  or wrap pairs, read after the public registry files. Append-only, superseded_by once.
- `holdings_export` (D190): one year-end holdings statement snapshot per account and date,
  its PDF and CSV as document_files. Immutable.
- `asset` gains `chain`, `contract_address`, `priced_as_asset_id` and `spam` (D180, D187).
  UNIQUE (workspace_id, symbol) becomes a partial unique index on
  (workspace_id, symbol, COALESCE(chain, '')) WHERE symbol IS NOT NULL, so ETH on Ethereum,
  ETH on Arbitrum and the exchanges' chainless ETH are three assets; a contract is unique
  per chain.
- `asset_movement` gains `group_id`, `address_id` and `index` (D182 (2), D183 (1)); its kind
  CHECK gains airdrop, spam, lp_deposit and lp_withdrawal, its source CHECK chain.
- The value-list CHECKs rebuilt as supersets: movement_import.format (chain_sync),
  transfer_link.kind (chain, bridge, wrap; a chain kind links two movements),
  document.subtype (chain_sync), event.entity_type (wallet, wallet_address, movement_group).
- §11 also names `reminder_ack.kind`: that column does not exist. 0019's `reminder_key` is
  a pattern CHECK ('^[a-z_]+\\.[a-z_]+$'), which `crypto.holdings_statement` already meets,
  so nothing changes there (§15.1).

Row-level security on every new table keyed on workspace_id (0025's policy); the app role
gets SELECT, INSERT, UPDATE on the five mutable tables and SELECT, INSERT on holdings_export.

The downgrade is refused while any new table has a row, an asset is a chain asset or spam,
a movement carries a group, an address or an index, or a new CHECK value is in use: the old
CHECKs would refuse those rows, and a downgrade never drops data. It lifts FORCE RLS for the
checks, since the owner has no workspace set, and restores it.

0039 is phase 6's bank_credential (D196) and 0040 its valuation_snapshot_scope; this revision
follows them.

Revision ID: 0041_wallets
Revises: 0040_valuation_snapshot_scope
Create Date: 2026-11-15
"""

from alembic import op

revision = "0041_wallets"
down_revision = "0040_valuation_snapshot_scope"
branch_labels = None
depends_on = None

APP_ROLE = "hausbuch_app"

TABLES = (
    "wallet_address",
    "movement_group",
    "wallet_sync",
    "chain_decoder",
    "chain_pair",
    "holdings_export",
)
APP_GRANTS = {
    "wallet_address": "SELECT, INSERT, UPDATE",  # active and label (events)
    "movement_group": "SELECT, INSERT, UPDATE",  # status forward, superseded_by once
    "wallet_sync": "SELECT, INSERT, UPDATE",  # finished once
    "chain_decoder": "SELECT, INSERT, UPDATE",  # superseded_by once
    "chain_pair": "SELECT, INSERT, UPDATE",  # superseded_by once
    "holdings_export": "SELECT, INSERT",
}
SUPERSEDED_ONCE = ("chain_decoder", "chain_pair")

# hausbuch.schemas.chain: Chain, ChainFamily's members by chain, AddressKind, GroupKind,
# GroupStatus, WalletSyncStatus, DecoderKind, ChainPairKind.
CHAINS = ("bitcoin", "ethereum", "arbitrum", "bsc", "solana")
EVM_CHAINS = ("ethereum", "arbitrum", "bsc")
ADDRESS_KINDS = ("receive", "change", "stake")
GROUP_KINDS = (
    "transfer",
    "exchange_transfer",
    "swap",
    "wrap",
    "bridge",
    "stake",
    "reward",
    "airdrop",
    "lp",
    "contract_call",
    "failed",
    "spam",
    "unknown",
)
GROUP_STATUSES = ("proposed", "confirmed")
SYNC_STATUSES = ("running", "succeeded", "failed", "throttled", "refused")
DECODER_KINDS = ("swap", "wrap", "bridge", "stake", "reward", "lp")
PAIR_KINDS = ("bridge", "wrap")

# The lists as 0025 to 0038 left them, in their order (0039 and 0040 change none of them, as
# far as step 0 knew).
OLD_MOVEMENT_KINDS = (
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
    "balance_correction",
)
NEW_MOVEMENT_KINDS = ("airdrop", "spam", "lp_deposit", "lp_withdrawal")
OLD_MOVEMENT_SOURCES = (
    "csv",
    "trade_confirmation",
    "distribution_note",
    "report",
    "user",
    "securities_transfer",
)
NEW_MOVEMENT_SOURCE = "chain"
OLD_IMPORT_FORMATS = ("bison_csv", "binance_csv")
NEW_IMPORT_FORMAT = "chain_sync"
OLD_TRANSFER_KINDS = ("bank", "exchange_eur", "depot_cash")
NEW_TRANSFER_KINDS = ("chain", "bridge", "wrap")
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
    "financing_invoice",
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
    "payment_proof",
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
    "exchange_export",
    "distribution_note",
    "securities_transfer",
)
NEW_SUBTYPE = "chain_sync"
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
    "asset",
    "movement",
    "transfer",
)
NEW_ENTITY_TYPES = ("wallet", "wallet_address", "movement_group")

# The address formats in the database, the backstop of rules/chain/address.validate (which
# also checks the bech32 / base58check checksums and EIP-55 casing): Bitcoin bech32 (lower
# case, as validate normalises it) or a base58 legacy/P2SH address; EVM 0x + 40 hex; Solana
# base58 of 32 bytes (32 to 44 characters).
_B58 = "[1-9A-HJ-NP-Za-km-z]"
BITCOIN_ADDRESS = rf"^(bc1[02-9ac-hj-np-z]{{8,87}}|[13]{_B58}{{25,34}})$"
EVM_ADDRESS = r"^0x[0-9a-fA-F]{40}$"
SOLANA_ADDRESS = rf"^{_B58}{{32,44}}$"


def _sql_list(names: tuple[str, ...]) -> str:
    return ", ".join(f"'{n}'" for n in names)


def _format_check(column: str) -> str:
    """The per-chain format of an address column (a wallet address or a token contract)."""
    return (
        f"(chain = 'bitcoin' AND {column} ~ '{BITCOIN_ADDRESS}')"
        f" OR (chain IN ({_sql_list(EVM_CHAINS)}) AND {column} ~ '{EVM_ADDRESS}')"
        f" OR (chain = 'solana' AND {column} ~ '{SOLANA_ADDRESS}')"
    )


# --- Functions -----------------------------------------------------------------------------

FUNCTIONS = """
CREATE FUNCTION wallet_address_of_a_wallet() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- D179 (2): an address belongs to an account of kind wallet (the service checks it too).
    IF NOT EXISTS (SELECT 1 FROM account a
                   WHERE a.workspace_id = NEW.workspace_id AND a.id = NEW.account_id
                     AND a.kind = 'wallet') THEN
        RAISE EXCEPTION 'wallet_address on account % refused: the account is no wallet',
                NEW.account_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION wallet_address_label_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- D179: an address is retired (active false) or relabelled, never edited.
    IF (to_jsonb(NEW) - 'active' - 'label') IS DISTINCT FROM (to_jsonb(OLD) - 'active' - 'label')
    THEN
        RAISE EXCEPTION 'UPDATE on wallet_address is not allowed: only active and label change'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Register a new address instead; the old one keeps its movements.';
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION wallet_sync_finish_once() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- D182 (1): a run is written running and finished once; what identifies it never moves.
    IF OLD.status <> 'running'
       OR (to_jsonb(NEW) - 'finished_at' - 'status' - 'cursor_after' - 'block' - 'txs_seen'
                         - 'movements_new' - 'oldest_slot_reached' - 'balances' - 'reconciled'
                         - 'difference' - 'error_code' - 'import_id' - 'document_id')
          IS DISTINCT FROM
          (to_jsonb(OLD) - 'finished_at' - 'status' - 'cursor_after' - 'block' - 'txs_seen'
                         - 'movements_new' - 'oldest_slot_reached' - 'balances' - 'reconciled'
                         - 'difference' - 'error_code' - 'import_id' - 'document_id') THEN
        RAISE EXCEPTION 'UPDATE on wallet_sync is not allowed: a run is finished once'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Only a running sync records its outcome; a new run is a new row.';
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION group_forward() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- D183 (1), as asset_movement (0025's movement_forward): status moves proposed ->
    -- confirmed and superseded_by is set once; nothing else changes.
    IF (to_jsonb(NEW) - 'status' - 'superseded_by')
           IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'superseded_by')
       OR NOT (NEW.status = OLD.status
               OR (OLD.status = 'proposed' AND NEW.status = 'confirmed'))
       OR (OLD.superseded_by IS NOT NULL
           AND NEW.superseded_by IS DISTINCT FROM OLD.superseded_by)
       OR (OLD.superseded_by IS NULL AND NEW.superseded_by IS NULL
           AND NEW.status = OLD.status) THEN
        RAISE EXCEPTION 'UPDATE on movement_group is not allowed: rows are append-only'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Only status moves forward (proposed -> confirmed) and superseded_by '
                         'is set once; record an answer as a new group.';
    END IF;
    RETURN NEW;
END
$$;
"""

DDL = rf"""
CREATE TABLE wallet_address (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id     uuid NOT NULL REFERENCES workspace (id),
    account_id       uuid NOT NULL,
    chain            text NOT NULL CHECK (chain IN ({_sql_list(CHAINS)})),
    address          text NOT NULL CHECK (length(address) BETWEEN 20 AND 128),
    address_masked   text NOT NULL CHECK (address_masked ~ '^.{{3,4}}….{{4}}$'),
    address_hmac     bytea NOT NULL CHECK (octet_length(address_hmac) = 32),
    label            text CHECK (length(label) BETWEEN 1 AND 100),
    kind             text NOT NULL DEFAULT 'receive'
                     CHECK (kind IN ({_sql_list(ADDRESS_KINDS)})),
    discovered_from  uuid,
    active           boolean NOT NULL DEFAULT true,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, account_id, id),
    UNIQUE (workspace_id, chain, address_hmac),
    FOREIGN KEY (workspace_id, account_id) REFERENCES account (workspace_id, id),
    FOREIGN KEY (workspace_id, discovered_from) REFERENCES asset_movement (workspace_id, id),
    CONSTRAINT wallet_address_format_check CHECK ({_format_check("address")}),
    -- D185: change is Bitcoin's; D183 (4): a stake account is Solana's.
    CONSTRAINT wallet_address_kind_chain_check CHECK (
        (kind <> 'change' OR chain = 'bitcoin') AND (kind <> 'stake' OR chain = 'solana')
    )
);
CREATE INDEX wallet_address_account_idx ON wallet_address (workspace_id, account_id);
COMMENT ON TABLE wallet_address IS
    'D179: one own wallet address per chain. address is the one plaintext identifier of the '
    'schema (the app must ask the chain for it); everything that leaves the table carries '
    'address_masked or address_hmac. Only active and label change (events).';

CREATE TABLE movement_group (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id   uuid NOT NULL REFERENCES workspace (id),
    account_id     uuid NOT NULL,
    chain          text NOT NULL CHECK (chain IN ({_sql_list(CHAINS)})),
    tx_hash        text NOT NULL CHECK (length(tx_hash) BETWEEN 1 AND 200),
    occurred_at    timestamptz NOT NULL,
    kind           text NOT NULL CHECK (kind IN ({_sql_list(GROUP_KINDS)})),
    decoder        text CHECK (length(decoder) BETWEEN 1 AND 200),
    status         text NOT NULL DEFAULT 'proposed'
                   CHECK (status IN ({_sql_list(GROUP_STATUSES)})),
    question       text CHECK (length(question) BETWEEN 1 AND 500),
    superseded_by  uuid,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, account_id, id),
    UNIQUE (workspace_id, account_id, chain, tx_hash, id),
    FOREIGN KEY (workspace_id, account_id) REFERENCES account (workspace_id, id),
    FOREIGN KEY (workspace_id, account_id, superseded_by)
        REFERENCES movement_group (workspace_id, account_id, id),
    CHECK (superseded_by <> id)
);
CREATE INDEX movement_group_tx_idx ON movement_group (workspace_id, account_id, chain, tx_hash);
COMMENT ON TABLE movement_group IS
    'D183: one transaction that touches an own address, with one asset_movement per leg and '
    'one for the fee. Status proposed -> confirmed, superseded_by set once.';

CREATE TABLE wallet_sync (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id         uuid NOT NULL REFERENCES workspace (id),
    account_id           uuid NOT NULL,
    address_id           uuid NOT NULL,
    started_at           timestamptz NOT NULL DEFAULT now(),
    finished_at          timestamptz,
    status               text NOT NULL DEFAULT 'running'
                         CHECK (status IN ({_sql_list(SYNC_STATUSES)})),
    source_host          text NOT NULL CHECK (source_host ~ '^[a-z0-9.-]{{1,253}}$'),
    cursor_before        text CHECK (length(cursor_before) BETWEEN 1 AND 200),
    cursor_after         text CHECK (length(cursor_after) BETWEEN 1 AND 200),
    block                bigint CHECK (block >= 0),
    oldest_slot_reached  bigint CHECK (oldest_slot_reached >= 0),
    txs_seen             integer NOT NULL DEFAULT 0 CHECK (txs_seen >= 0),
    movements_new        integer NOT NULL DEFAULT 0 CHECK (movements_new >= 0),
    balances             jsonb CHECK (jsonb_typeof(balances) = 'object'),
    reconciled           boolean,
    difference           jsonb CHECK (jsonb_typeof(difference) = 'object'),
    error_code           text CHECK (char_length(error_code) BETWEEN 1 AND 64),
    import_id            uuid,
    document_id          uuid,
    created_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, account_id) REFERENCES account (workspace_id, id),
    FOREIGN KEY (workspace_id, account_id, address_id)
        REFERENCES wallet_address (workspace_id, account_id, id),
    FOREIGN KEY (workspace_id, account_id, import_id)
        REFERENCES movement_import (workspace_id, account_id, id),
    FOREIGN KEY (workspace_id, document_id) REFERENCES document (workspace_id, id),
    CHECK ((status = 'running') = (finished_at IS NULL)),
    CHECK (finished_at >= started_at),
    -- D182 (3): an original is stored, with its import, only by a run that brought movements.
    CHECK ((import_id IS NULL) = (document_id IS NULL)),
    CHECK (import_id IS NULL OR movements_new > 0),
    CHECK ((error_code IS NOT NULL) = (status IN ('failed', 'throttled', 'refused'))),
    CHECK (reconciled IS NULL OR balances IS NOT NULL),
    CHECK (difference IS NULL OR reconciled IS FALSE)
);
CREATE INDEX wallet_sync_address_started
    ON wallet_sync (workspace_id, address_id, started_at DESC);
COMMENT ON TABLE wallet_sync IS
    'D182 (1): one run of one wallet address. source_host is the provider''s host name, never '
    'its key or path. Written running and finished once (trigger).';

CREATE TABLE chain_decoder (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id           uuid NOT NULL REFERENCES workspace (id),
    chain                  text NOT NULL CHECK (chain IN ({_sql_list(CHAINS)})),
    program                text NOT NULL CHECK (length(program) BETWEEN 20 AND 128),
    method                 text CHECK (length(method) BETWEEN 1 AND 100),
    kind                   text NOT NULL CHECK (kind IN ({_sql_list(DECODER_KINDS)})),
    created_from_group_id  uuid,
    superseded_by          uuid,
    created_at             timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, created_from_group_id) REFERENCES movement_group (workspace_id, id),
    FOREIGN KEY (workspace_id, superseded_by) REFERENCES chain_decoder (workspace_id, id),
    CONSTRAINT chain_decoder_program_format_check
        CHECK (chain <> 'bitcoin' AND ({_format_check("program")})),
    CHECK (superseded_by <> id)
);
CREATE INDEX chain_decoder_program_idx ON chain_decoder (workspace_id, chain, program);
COMMENT ON TABLE chain_decoder IS
    'D191: a decoder row a person taught („Künftig immer so“), read after the public '
    'rules/chain/decoders.yaml and winning over it for the same key. Never an amount. '
    'Append-only; superseded_by set once.';

CREATE TABLE chain_pair (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id           uuid NOT NULL REFERENCES workspace (id),
    kind                   text NOT NULL CHECK (kind IN ({_sql_list(PAIR_KINDS)})),
    from_chain             text NOT NULL CHECK (from_chain IN ({_sql_list(CHAINS)})),
    from_asset_id          uuid NOT NULL,
    to_chain               text NOT NULL CHECK (to_chain IN ({_sql_list(CHAINS)})),
    to_asset_id            uuid NOT NULL,
    max_fee_share          numeric(5,4) CHECK (max_fee_share >= 0 AND max_fee_share < 1),
    created_from_group_id  uuid,
    superseded_by          uuid,
    created_at             timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, from_asset_id) REFERENCES asset (workspace_id, id),
    FOREIGN KEY (workspace_id, to_asset_id) REFERENCES asset (workspace_id, id),
    FOREIGN KEY (workspace_id, created_from_group_id) REFERENCES movement_group (workspace_id, id),
    FOREIGN KEY (workspace_id, superseded_by) REFERENCES chain_pair (workspace_id, id),
    -- D184 (2), (3): a bridge joins two chains and may keep a fee share; a wrap stays on one.
    CHECK ((kind = 'bridge') = (from_chain <> to_chain)),
    CHECK (kind = 'bridge' OR max_fee_share IS NULL),
    CHECK (from_asset_id <> to_asset_id),
    CHECK (superseded_by <> id)
);
COMMENT ON TABLE chain_pair IS
    'D191: a bridge or wrap pair a person confirmed, read after the registry''s bridge_pairs '
    'and wraps. Append-only; superseded_by set once.';

CREATE TABLE holdings_export (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid NOT NULL REFERENCES workspace (id),
    account_id    uuid NOT NULL,
    as_of         date NOT NULL,
    pdf_sha256    bytea NOT NULL CHECK (octet_length(pdf_sha256) = 32),
    csv_sha256    bytea NOT NULL CHECK (octet_length(csv_sha256) = 32),
    statement     jsonb NOT NULL CHECK (jsonb_typeof(statement) = 'object'),
    created_at    timestamptz NOT NULL DEFAULT now(),
    created_by    uuid,
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, account_id) REFERENCES account (workspace_id, id),
    FOREIGN KEY (workspace_id, pdf_sha256) REFERENCES document_file (workspace_id, sha256),
    FOREIGN KEY (workspace_id, csv_sha256) REFERENCES document_file (workspace_id, sha256),
    FOREIGN KEY (workspace_id, created_by) REFERENCES app_user (workspace_id, id)
);
CREATE INDEX holdings_export_account_as_of ON holdings_export (workspace_id, account_id, as_of);
COMMENT ON TABLE holdings_export IS
    'D190: a year-end holdings statement per account and day (PDF and CSV as document_files, '
    'the statement and its source movement ids as jsonb). Immutable; a later export is a new row.';
"""

ASSET_UP = rf"""
ALTER TABLE asset
    ADD COLUMN chain text CHECK (chain IN ({_sql_list(CHAINS)})),
    ADD COLUMN contract_address text,
    ADD COLUMN priced_as_asset_id uuid,
    ADD COLUMN spam boolean NOT NULL DEFAULT false,
    ADD CONSTRAINT asset_contract_address_check CHECK (
        contract_address IS NULL OR (chain <> 'bitcoin' AND ({_format_check("contract_address")}))
    ),
    ADD CONSTRAINT asset_chain_fields_check CHECK (
        (contract_address IS NULL OR chain IS NOT NULL)
        AND (priced_as_asset_id IS NULL OR chain IS NOT NULL)
        AND (NOT spam OR chain IS NOT NULL)
    ),
    ADD CONSTRAINT asset_priced_as_check CHECK (priced_as_asset_id <> id),
    ADD CONSTRAINT asset_priced_as_fkey FOREIGN KEY (workspace_id, priced_as_asset_id)
        REFERENCES asset (workspace_id, id),
    ADD CONSTRAINT asset_workspace_id_chain_contract_address_key
        UNIQUE (workspace_id, chain, contract_address),
    DROP CONSTRAINT asset_workspace_id_symbol_key;
-- D180 (2): a symbol is unique per chain; NULL symbols (funds, known by their ISIN) repeat.
CREATE UNIQUE INDEX asset_symbol_chain_key
    ON asset (workspace_id, symbol, COALESCE(chain, '')) WHERE symbol IS NOT NULL;
COMMENT ON TABLE asset IS
    'D167, D180: what a position is held in (EUR is an asset of kind cash). A chain asset is '
    'identified by its chain and contract (null for the native coin), priced through '
    'priced_as_asset_id, spam hidden (D187). Mutable: the name, the price-source ids, '
    'priced_as and spam, with asset events.';
"""

ASSET_DOWN = """
DROP INDEX asset_symbol_chain_key;
ALTER TABLE asset
    ADD CONSTRAINT asset_workspace_id_symbol_key UNIQUE (workspace_id, symbol),
    DROP CONSTRAINT asset_workspace_id_chain_contract_address_key,
    DROP CONSTRAINT asset_priced_as_fkey,
    DROP COLUMN spam,
    DROP COLUMN priced_as_asset_id,
    DROP COLUMN contract_address,
    DROP COLUMN chain;
COMMENT ON TABLE asset IS
    'D167: what a position is held in (EUR is an asset of kind cash). Mutable: the name and '
    'the price-source ids, with asset.updated events.';
"""

MOVEMENT_UP = f"""
ALTER TABLE asset_movement
    ADD COLUMN group_id uuid,
    ADD COLUMN address_id uuid,
    ADD COLUMN "index" text CHECK (length("index") BETWEEN 1 AND 64),
    ADD CONSTRAINT asset_movement_group_fkey FOREIGN KEY (workspace_id, account_id, group_id)
        REFERENCES movement_group (workspace_id, account_id, id),
    ADD CONSTRAINT asset_movement_address_fkey FOREIGN KEY (workspace_id, account_id, address_id)
        REFERENCES wallet_address (workspace_id, account_id, id),
    -- D182 (2): a movement read from a chain names its own address, its index in the
    -- transaction and its hash.
    ADD CONSTRAINT asset_movement_chain_check CHECK (
        source <> 'chain'
        OR (address_id IS NOT NULL AND "index" IS NOT NULL AND movement_hash IS NOT NULL)
    ),
    -- The chain kinds come from a chain or from a person's answer, never from a file.
    ADD CONSTRAINT asset_movement_chain_kind_check CHECK (
        kind NOT IN ({_sql_list(NEW_MOVEMENT_KINDS)}) OR source IN ('chain', 'user')
    );
CREATE INDEX asset_movement_group_idx ON asset_movement (workspace_id, group_id)
    WHERE group_id IS NOT NULL;
"""

MOVEMENT_DOWN = """
DROP INDEX asset_movement_group_idx;
ALTER TABLE asset_movement
    DROP CONSTRAINT asset_movement_chain_kind_check,
    DROP CONSTRAINT asset_movement_chain_check,
    DROP CONSTRAINT asset_movement_address_fkey,
    DROP CONSTRAINT asset_movement_group_fkey,
    DROP COLUMN "index",
    DROP COLUMN address_id,
    DROP COLUMN group_id;
"""

LINK_UP = f"""
ALTER TABLE transfer_link ADD CONSTRAINT transfer_link_chain_legs_check CHECK (
    kind NOT IN ({_sql_list(NEW_TRANSFER_KINDS)})
    OR (out_movement_id IS NOT NULL AND in_movement_id IS NOT NULL)
);
"""
LINK_DOWN = "ALTER TABLE transfer_link DROP CONSTRAINT transfer_link_chain_legs_check;"


def _triggers() -> str:
    lines = [
        """
CREATE TRIGGER wallet_address_of_a_wallet BEFORE INSERT OR UPDATE ON wallet_address
    FOR EACH ROW EXECUTE FUNCTION wallet_address_of_a_wallet();
CREATE TRIGGER wallet_address_label_only BEFORE UPDATE ON wallet_address
    FOR EACH ROW EXECUTE FUNCTION wallet_address_label_only();
CREATE TRIGGER wallet_sync_finish_once BEFORE UPDATE ON wallet_sync
    FOR EACH ROW EXECUTE FUNCTION wallet_sync_finish_once();
CREATE TRIGGER movement_group_forward BEFORE UPDATE ON movement_group
    FOR EACH ROW EXECUTE FUNCTION group_forward();
CREATE TRIGGER holdings_export_append_only BEFORE UPDATE OR DELETE ON holdings_export
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();"""
    ]
    for table in SUPERSEDED_ONCE:
        lines.append(f"""
CREATE TRIGGER {table}_superseded_once BEFORE UPDATE ON {table}
    FOR EACH ROW EXECUTE FUNCTION superseded_once();""")
    for table in TABLES:
        lines.append(f"""
CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table}
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();""")
        if table != "holdings_export":
            lines.append(f"""
CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table}
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();""")
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


def _set_value_list(table: str, column: str, names: tuple[str, ...]) -> None:
    """Replace a column's value-list CHECK, named <table>_<column>_check (no IF EXISTS)."""
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_{column}_check")
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT {table}_{column}_check"
        f" CHECK ({column} IN ({_sql_list(names)}))"
    )


def _set_checks(new: bool) -> None:
    def pick(old: tuple[str, ...], added: tuple[str, ...]) -> tuple[str, ...]:
        return (*old, *added) if new else old

    _set_value_list("asset_movement", "kind", pick(OLD_MOVEMENT_KINDS, NEW_MOVEMENT_KINDS))
    _set_value_list("asset_movement", "source", pick(OLD_MOVEMENT_SOURCES, (NEW_MOVEMENT_SOURCE,)))
    _set_value_list("movement_import", "format", pick(OLD_IMPORT_FORMATS, (NEW_IMPORT_FORMAT,)))
    _set_value_list("transfer_link", "kind", pick(OLD_TRANSFER_KINDS, NEW_TRANSFER_KINDS))
    _set_value_list("document", "subtype", pick(OLD_SUBTYPES, (NEW_SUBTYPE,)))
    _set_value_list("event", "entity_type", pick(OLD_ENTITY_TYPES, NEW_ENTITY_TYPES))


# --- The downgrade -------------------------------------------------------------------------

_DATA_CHECKS = (
    *((table, f"SELECT 1 FROM {table}", f"table {table} holds rows") for table in TABLES),
    (
        "asset",
        "SELECT 1 FROM asset WHERE chain IS NOT NULL OR spam",
        "an asset is a chain asset",
    ),
    (
        "asset",
        "SELECT 1 FROM asset GROUP BY workspace_id, symbol HAVING count(*) > 1"
        " AND symbol IS NOT NULL",
        "two assets share a symbol",
    ),
    (
        "asset_movement",
        "SELECT 1 FROM asset_movement WHERE group_id IS NOT NULL OR address_id IS NOT NULL"
        f" OR \"index\" IS NOT NULL OR source = '{NEW_MOVEMENT_SOURCE}'"
        f" OR kind IN ({_sql_list(NEW_MOVEMENT_KINDS)})",
        "a movement was read from a chain or has a chain kind",
    ),
    (
        "movement_import",
        f"SELECT 1 FROM movement_import WHERE format = '{NEW_IMPORT_FORMAT}'",
        "an import is a chain sync",
    ),
    (
        "transfer_link",
        f"SELECT 1 FROM transfer_link WHERE kind IN ({_sql_list(NEW_TRANSFER_KINDS)})",
        "a transfer link is a chain, bridge or wrap link",
    ),
    (
        "document",
        f"SELECT 1 FROM document WHERE subtype = '{NEW_SUBTYPE}'",
        "a document is the original of a chain sync",
    ),
    (
        "event",
        f"SELECT 1 FROM event WHERE entity_type IN ({_sql_list(NEW_ENTITY_TYPES)})",
        "an event is about a wallet, an address or a movement group",
    ),
)
_CHECKED = tuple(dict.fromkeys(t for t, _, _ in _DATA_CHECKS))
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

DROP_FUNCTIONS = """
DROP FUNCTION group_forward();
DROP FUNCTION wallet_sync_finish_once();
DROP FUNCTION wallet_address_label_only();
DROP FUNCTION wallet_address_of_a_wallet();
"""


def upgrade() -> None:
    op.execute(FUNCTIONS)
    op.execute(DDL)
    op.execute(_triggers())
    for table in TABLES:
        op.execute(_rls(table))
    op.execute(ASSET_UP)
    op.execute(MOVEMENT_UP)
    _set_checks(new=True)
    op.execute(LINK_UP)
    lines = [f"GRANT {privs} ON {table} TO {APP_ROLE};" for table, privs in APP_GRANTS.items()]
    op.execute(_if_app_role("\n        ".join(lines)))


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    op.execute(LINK_DOWN)
    _set_checks(new=False)
    op.execute(MOVEMENT_DOWN)
    op.execute(ASSET_DOWN)
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
    op.execute(DROP_FUNCTIONS)
