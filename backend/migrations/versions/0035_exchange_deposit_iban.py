"""Phase 6: an exchange account may carry its deposit IBAN (D162 amended, 15 Nov 2026).

An exchange credits a SEPA deposit up to 2 € short without a fee row in its export (Luis's
DKB payments of 100, 901 and 1,001 € to the exchange's deposit IBAN arrived 1 € less each).
D162 links such a pair only when the bank line names the exchange's account by IBAN, so the
first link a person makes records the bank line's counterparty IBAN on the exchange account
(services.accounts.learn_exchange_deposit_iban), the confirm_depot_iban pattern of 0027.
account_identity_check is rebuilt so an account of kind `exchange` may carry an IBAN like a
depot; a bank kind still needs one and every other kind still has none. Nothing is backfilled.

Phase 7's spec moved from 0035 to 0036.

The downgrade is refused while an exchange carries an IBAN (the 0027 guard), since the old
CHECK would refuse it and a downgrade never drops data. It lifts FORCE RLS for the check,
since the owner has no workspace set, and restores it (an exception rolls the whole statement
back, FORCE included).

Revision ID: 0035_exchange_deposit_iban
Revises: 0034_bar_category
Create Date: 2026-11-15
"""

from alembic import op

revision = "0035_exchange_deposit_iban"
down_revision = "0034_bar_category"
branch_labels = None
depends_on = None

BANK_KINDS = ("bank_current", "bank_savings")


def _sql_list(names: tuple[str, ...]) -> str:
    return ", ".join(f"'{n}'" for n in names)


def _identity(iban_kinds: tuple[str, ...]) -> str:
    """account_identity_check: a bank kind has an IBAN and no external reference; the kinds in
    `iban_kinds` may carry an IBAN (a depot since 0027, an exchange since 0035); every other
    kind has none."""
    return f"""
ALTER TABLE account DROP CONSTRAINT account_identity_check;
ALTER TABLE account ADD CONSTRAINT account_identity_check CHECK (
    (iban_hmac IS NULL) = (iban_masked IS NULL)
    AND (external_ref_hmac IS NULL) = (external_ref_masked IS NULL)
    AND (kind NOT IN ({_sql_list(BANK_KINDS)}) OR iban_hmac IS NOT NULL)
    AND (kind IN ({_sql_list(iban_kinds)}) OR kind IN ({_sql_list(BANK_KINDS)})
         OR iban_hmac IS NULL)
    AND (kind NOT IN ({_sql_list(BANK_KINDS)}) OR external_ref_hmac IS NULL)
);
"""


REFUSE_IF_DATA = f"""
DO $$
BEGIN
    ALTER TABLE account NO FORCE ROW LEVEL SECURITY;
    IF EXISTS (SELECT 1 FROM account WHERE kind = 'exchange' AND iban_hmac IS NOT NULL) THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: an exchange account carries an IBAN'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Export and remove the data first; a downgrade never drops data.';
    END IF;
    ALTER TABLE account FORCE ROW LEVEL SECURITY;
END
$$;
"""


def upgrade() -> None:
    op.execute(_identity(("depot", "exchange")))


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    op.execute(_identity(("depot",)))
