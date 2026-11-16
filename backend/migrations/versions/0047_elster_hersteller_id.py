"""Phase 6 D260 amended (10): the ELSTER Hersteller-ID typed in the app.

Luis, 16 Nov 2026: „I got the elster id. Can you also add a settings where I can input it?“
Hausbuch is registered as an ELSTER software maker, and ERiC (not built) needs the maker's own
Hersteller-ID even to validate a return without sending it (docs/ARCHITECTURE.md, „Input for a
later phase: filing through ERiC“). The ID joins the allowlist of `app_secret.name`, stored
encrypted like every other name there; nothing reads it yet.

The name CHECK is 0045's inline column CHECK, which Postgres named `app_secret_name_check`
(checked on a database migrated from 0001; the name is deterministic for the same CREATE
TABLE). It keeps its name, so a downgrade restores 0045's exactly.

The downgrade deletes the stored Hersteller-IDs (the brief for this change: it is a number
ELSTER printed, typed again in a minute, and nothing reads it yet) and restores the old CHECK,
which would refuse such a row. It lifts FORCE RLS for the delete, since the owner has no
workspace set and would otherwise see no row, and restores it.

Phase 8's and phase 9's specs had reserved 0047 and 0048; they move to 0048 and 0049.

Revision ID: 0047_elster_hersteller_id
Revises: 0046_learned_exchange_transfers
Create Date: 2026-11-16
"""

from alembic import op

revision = "0047_elster_hersteller_id"
down_revision = "0046_learned_exchange_transfers"
branch_labels = None
depends_on = None

# 0045's list (hausbuch.schemas.app_secrets.AppSecretName), in its order.
OLD_NAMES = (
    "anthropic_api_key",
    "etherscan_key",
    "solana_rpc_url",
    "bsc_rpc_url",
    "helius_key",
    "coingecko_key",
    "fints_product_id",
    "tem_secret_key",
    "tem_project_id",
    "mail_from",
    "inbox_imap_user",
    "inbox_imap_password",
    "inbox_address",
    "inbox_label",
)
NEW_NAME = "elster_hersteller_id"


def _set_check(names: tuple[str, ...]) -> None:
    listed = ", ".join(f"'{n}'" for n in names)
    op.execute("ALTER TABLE app_secret DROP CONSTRAINT app_secret_name_check")
    op.execute(
        f"ALTER TABLE app_secret ADD CONSTRAINT app_secret_name_check CHECK (name IN ({listed}))"
    )


DELETE_NEW_NAME = f"""DO $$
BEGIN
    ALTER TABLE app_secret NO FORCE ROW LEVEL SECURITY;
    DELETE FROM app_secret WHERE name = '{NEW_NAME}';
    ALTER TABLE app_secret FORCE ROW LEVEL SECURITY;
END
$$;"""


def upgrade() -> None:
    _set_check((*OLD_NAMES, NEW_NAME))


def downgrade() -> None:
    op.execute(DELETE_NEW_NAME)
    _set_check(OLD_NAMES)
