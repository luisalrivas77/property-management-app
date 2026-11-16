"""Phase 7 D261: an exchange's deposit addresses and withdrawal pairs, learned from a person's
link, so code links the rest (docs/phases/phase-7.md D261, amends D184 and D186 (1)).

Luis, 16 Nov 2026: „Do I need to link every transaction between the exchange and my wallet? I
would prefer if the app would learn the address and book the rest by itself.“

- `asset_movement` gains `counterparty_hmac` and `counterparty_masked`: the one external
  address a wallet's plain send paid (EVM's recipient, Solana's credited owner, Bitcoin's one
  output that is no own address), as its HMAC under the IBAN key and its masked form, as
  `wallet_address` stores an own address (D179). Never the plaintext. Both or neither; null on
  every other movement and on every send paying more than one external output.
- `exchange_deposit_address`: an exchange account's deposit address on one chain, learned from
  the wallet's send a person linked to the exchange's deposit: the HMAC and masked form only
  (no plaintext column), the sending movement it was learned from, who set it and when, and an
  append-only end (`ended_at`, `ended_by`, set once by „Vergessen“). One live row per
  (workspace, chain, HMAC): an address is one exchange's.
- `exchange_withdrawal_pair`: an exchange account and an own `wallet_address` its withdrawals
  were linked to by a person, so code links the next ones by quantity (an exchange without a
  hash in its export, BISON). Same end. One live row per (workspace, exchange, address).

Both tables belong to an account of kind `exchange` (trigger, as the service checks too), are
never deleted or truncated, and change only in their end, once. Row-level security keyed on
workspace_id; the app role gets SELECT, INSERT, UPDATE.

The downgrade is refused while a row exists or a movement carries a counterparty; it lifts
FORCE RLS for the checks, since the owner has no workspace set, and restores it.

Phase 8's and phase 9's specs had reserved 0046 and 0047; they move to 0047 and 0048.

Revision ID: 0046_learned_exchange_transfers
Revises: 0045_app_secret
Create Date: 2026-11-16
"""

from alembic import op

revision = "0046_learned_exchange_transfers"
down_revision = "0045_app_secret"
branch_labels = None
depends_on = None

APP_ROLE = "hausbuch_app"
TABLES = ("exchange_deposit_address", "exchange_withdrawal_pair")
CHAINS = ("bitcoin", "ethereum", "arbitrum", "bsc", "solana")
_MASKED = "'^.{3,4}….{4}$'"
_CHAIN_LIST = ", ".join(f"'{c}'" for c in CHAINS)

FUNCTIONS = """
CREATE FUNCTION exchange_learned_of_an_exchange() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- D261: a learned address or pair belongs to an account of kind exchange.
    IF NOT EXISTS (SELECT 1 FROM account a
                   WHERE a.workspace_id = NEW.workspace_id AND a.id = NEW.account_id
                     AND a.kind = 'exchange') THEN
        RAISE EXCEPTION '% on account % refused: the account is no exchange',
                TG_TABLE_NAME, NEW.account_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION exchange_learned_end_once() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- D261: a learned row is ended once („Vergessen“); nothing else ever changes.
    IF OLD.ended_at IS NOT NULL OR NEW.ended_at IS NULL
       OR (to_jsonb(NEW) - 'ended_at' - 'ended_by')
          IS DISTINCT FROM (to_jsonb(OLD) - 'ended_at' - 'ended_by') THEN
        RAISE EXCEPTION 'UPDATE on % is not allowed: a learned row is only ended, once',
                TG_TABLE_NAME
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Learn it again as a new row; the old one keeps its history.';
    END IF;
    RETURN NEW;
END
$$;
"""

DDL = f"""
ALTER TABLE asset_movement
    ADD COLUMN counterparty_hmac bytea
        CHECK (octet_length(counterparty_hmac) = 32),
    ADD COLUMN counterparty_masked text
        CHECK (counterparty_masked ~ {_MASKED}),
    -- D261: the HMAC and its masked form together, and only on a movement read from a chain.
    ADD CONSTRAINT asset_movement_counterparty_check CHECK (
        (counterparty_hmac IS NULL) = (counterparty_masked IS NULL)
        AND (counterparty_hmac IS NULL OR source = 'chain')
    );

CREATE TABLE exchange_deposit_address (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    uuid NOT NULL REFERENCES workspace (id),
    account_id      uuid NOT NULL,  -- the exchange
    chain           text NOT NULL CHECK (chain IN ({_CHAIN_LIST})),
    address_hmac    bytea NOT NULL CHECK (octet_length(address_hmac) = 32),
    address_masked  text NOT NULL CHECK (address_masked ~ {_MASKED}),
    learned_from    uuid NOT NULL,  -- the wallet's sending movement a person linked
    set_by          uuid,  -- the person (no FK, as bank_credential.set_by)
    created_at      timestamptz NOT NULL DEFAULT now(),
    ended_at        timestamptz,
    ended_by        uuid,
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, account_id) REFERENCES account (workspace_id, id),
    FOREIGN KEY (workspace_id, learned_from) REFERENCES asset_movement (workspace_id, id),
    CHECK (ended_at IS NOT NULL OR ended_by IS NULL)
);
CREATE UNIQUE INDEX exchange_deposit_address_live_idx
    ON exchange_deposit_address (workspace_id, chain, address_hmac) WHERE ended_at IS NULL;
CREATE INDEX exchange_deposit_address_account_idx
    ON exchange_deposit_address (workspace_id, account_id);
COMMENT ON TABLE exchange_deposit_address IS
    'D261: an exchange''s deposit address on one chain, learned from a person''s link: HMAC '
    'and masked form only, never the plaintext (D179). Ended once by „Vergessen“.';

CREATE TABLE exchange_withdrawal_pair (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    uuid NOT NULL REFERENCES workspace (id),
    account_id      uuid NOT NULL,  -- the exchange
    address_id      uuid NOT NULL,  -- the own wallet address its withdrawals arrive on
    learned_from    uuid NOT NULL,  -- the exchange's withdrawal a person linked
    set_by          uuid,
    created_at      timestamptz NOT NULL DEFAULT now(),
    ended_at        timestamptz,
    ended_by        uuid,
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, account_id) REFERENCES account (workspace_id, id),
    FOREIGN KEY (workspace_id, address_id) REFERENCES wallet_address (workspace_id, id),
    FOREIGN KEY (workspace_id, learned_from) REFERENCES asset_movement (workspace_id, id),
    CHECK (ended_at IS NOT NULL OR ended_by IS NULL)
);
CREATE UNIQUE INDEX exchange_withdrawal_pair_live_idx
    ON exchange_withdrawal_pair (workspace_id, account_id, address_id) WHERE ended_at IS NULL;
COMMENT ON TABLE exchange_withdrawal_pair IS
    'D261: an exchange and an own wallet address its withdrawals were linked to by a person, '
    'so code links the next ones by quantity. Ended once by „Vergessen“.';
"""


def _triggers(table: str) -> str:
    return f"""
CREATE TRIGGER {table}_of_an_exchange BEFORE INSERT OR UPDATE ON {table}
    FOR EACH ROW EXECUTE FUNCTION exchange_learned_of_an_exchange();
CREATE TRIGGER {table}_end_once BEFORE UPDATE ON {table}
    FOR EACH ROW EXECUTE FUNCTION exchange_learned_end_once();
CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table}
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table}
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON {table}
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());
"""


GRANTS = f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        GRANT SELECT, INSERT, UPDATE ON exchange_deposit_address TO {APP_ROLE};
        GRANT SELECT, INSERT, UPDATE ON exchange_withdrawal_pair TO {APP_ROLE};
    ELSE
        RAISE NOTICE 'role {APP_ROLE} does not exist; skipping grants';
    END IF;
END
$$;
"""

REFUSE_IF_DATA = f"""
DO $$
BEGIN
    ALTER TABLE exchange_deposit_address NO FORCE ROW LEVEL SECURITY;
    ALTER TABLE exchange_withdrawal_pair NO FORCE ROW LEVEL SECURITY;
    ALTER TABLE asset_movement NO FORCE ROW LEVEL SECURITY;
    IF EXISTS (SELECT 1 FROM exchange_deposit_address)
       OR EXISTS (SELECT 1 FROM exchange_withdrawal_pair)
       OR EXISTS (SELECT 1 FROM asset_movement WHERE counterparty_hmac IS NOT NULL) THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: a learned address or a counterparty '
                        'is stored'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'A downgrade never drops data.';
    END IF;
    ALTER TABLE exchange_deposit_address FORCE ROW LEVEL SECURITY;
    ALTER TABLE exchange_withdrawal_pair FORCE ROW LEVEL SECURITY;
    ALTER TABLE asset_movement FORCE ROW LEVEL SECURITY;
END
$$;
"""


def upgrade() -> None:
    op.execute(FUNCTIONS)
    op.execute(DDL)
    for table in TABLES:
        op.execute(_triggers(table))
    op.execute(GRANTS)


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
    op.execute(
        """
ALTER TABLE asset_movement
    DROP CONSTRAINT asset_movement_counterparty_check,
    DROP COLUMN counterparty_masked,
    DROP COLUMN counterparty_hmac;
DROP FUNCTION exchange_learned_end_once();
DROP FUNCTION exchange_learned_of_an_exchange();
"""
    )
