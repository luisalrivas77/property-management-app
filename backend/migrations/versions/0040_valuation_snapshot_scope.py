"""Phase 6 step 6: a stored month-end holds the property and its loans too (D167 (5) amended,
15 Nov 2026).

0025's valuation_snapshot had one row per account (account_id NOT NULL), so a month-end run
could store the accounts and positions only, and the property's estimate and the loans'
balances were recomputed on read: a later change of the scenario's „Wert heute“ or of a loan
moved a past month-end's total. Now a row values exactly one of an account (asset_id null: its
balance; else a position on it), a property (its estimate) or a loan (its balance by the
schedule, negative), so a stored run is read from its rows alone.

- `account_id` becomes nullable; `property_id` and `loan_id` are added, nullable, each with a
  workspace-scoped foreign key like the table's others.
- CHECK `valuation_snapshot_one_scope_check`: exactly one of account_id, property_id, loan_id.
  CHECK `valuation_snapshot_asset_on_account_check`: an asset only on an account's row.
- 0025's unique key (one row per account and asset per run, NULLS NOT DISTINCT) would let a
  run hold only one property row (account and asset both null); it is replaced by one over
  the run, account, asset, property and loan.

The append-only trigger, the no-truncate trigger, row-level security and the grants (SELECT,
INSERT for the app role) are untouched: the new columns fall under the table's grants and
policies.

The downgrade is refused while any row has no account (a property's or a loan's row; the 0025
guard: a downgrade never drops data and the old NOT NULL would refuse them). It lifts FORCE RLS
for the check, since the owner has no workspace set, and restores it. It then drops the CHECKs,
the new key and the columns, and restores 0025's key and NOT NULL under their names.

Revision ID: 0040_valuation_snapshot_scope
Revises: 0039_bank_credential
Create Date: 2026-11-15
"""

from alembic import op

revision = "0040_valuation_snapshot_scope"
down_revision = "0039_bank_credential"
branch_labels = None
depends_on = None

OLD_KEY = "valuation_snapshot_workspace_id_net_worth_run_id_account_id_key"
NEW_KEY = "valuation_snapshot_run_scope_key"
ONE_SCOPE = "valuation_snapshot_one_scope_check"
ASSET_ON_ACCOUNT = "valuation_snapshot_asset_on_account_check"
PROPERTY_FK = "valuation_snapshot_workspace_id_property_id_fkey"
LOAN_FK = "valuation_snapshot_workspace_id_loan_id_fkey"

REFUSE_IF_DATA = f"""DO $$
BEGIN
    ALTER TABLE valuation_snapshot NO FORCE ROW LEVEL SECURITY;
    IF EXISTS (SELECT 1 FROM valuation_snapshot WHERE account_id IS NULL) THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: a property or loan month-end row exists'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Export and remove the data first; a downgrade never drops data.';
    END IF;
    ALTER TABLE valuation_snapshot FORCE ROW LEVEL SECURITY;
END
$$;"""


def upgrade() -> None:
    op.execute(f"""
ALTER TABLE valuation_snapshot ALTER COLUMN account_id DROP NOT NULL;
ALTER TABLE valuation_snapshot ADD COLUMN property_id uuid;
ALTER TABLE valuation_snapshot ADD COLUMN loan_id uuid;
ALTER TABLE valuation_snapshot ADD CONSTRAINT {PROPERTY_FK}
    FOREIGN KEY (workspace_id, property_id) REFERENCES property (workspace_id, id);
ALTER TABLE valuation_snapshot ADD CONSTRAINT {LOAN_FK}
    FOREIGN KEY (workspace_id, loan_id) REFERENCES loan (workspace_id, id);
ALTER TABLE valuation_snapshot ADD CONSTRAINT {ONE_SCOPE}
    CHECK (num_nonnulls(account_id, property_id, loan_id) = 1);
ALTER TABLE valuation_snapshot ADD CONSTRAINT {ASSET_ON_ACCOUNT}
    CHECK (asset_id IS NULL OR account_id IS NOT NULL);
ALTER TABLE valuation_snapshot DROP CONSTRAINT {OLD_KEY};
ALTER TABLE valuation_snapshot ADD CONSTRAINT {NEW_KEY}
    UNIQUE NULLS NOT DISTINCT
    (workspace_id, net_worth_run_id, account_id, asset_id, property_id, loan_id);
COMMENT ON TABLE valuation_snapshot IS
    'D167: a month-end valuation written by one net-worth run, of exactly one of an account '
    '(asset_id null: its balance; else a position), a property (its estimate) or a loan (its '
    'balance by the schedule, negative). Immutable.';
""")


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    op.execute(f"""
ALTER TABLE valuation_snapshot DROP CONSTRAINT {NEW_KEY};
ALTER TABLE valuation_snapshot ADD CONSTRAINT {OLD_KEY}
    UNIQUE NULLS NOT DISTINCT (workspace_id, net_worth_run_id, account_id, asset_id);
ALTER TABLE valuation_snapshot DROP CONSTRAINT {ASSET_ON_ACCOUNT};
ALTER TABLE valuation_snapshot DROP CONSTRAINT {ONE_SCOPE};
ALTER TABLE valuation_snapshot DROP CONSTRAINT {LOAN_FK};
ALTER TABLE valuation_snapshot DROP CONSTRAINT {PROPERTY_FK};
ALTER TABLE valuation_snapshot DROP COLUMN loan_id;
ALTER TABLE valuation_snapshot DROP COLUMN property_id;
ALTER TABLE valuation_snapshot ALTER COLUMN account_id SET NOT NULL;
COMMENT ON TABLE valuation_snapshot IS
    'D167: a month-end valuation of one position (asset_id null: a bank balance), written by '
    'one net-worth run. Immutable.';
""")
