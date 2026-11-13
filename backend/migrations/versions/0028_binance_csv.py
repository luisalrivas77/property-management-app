"""Phase 6 step 5a: Binance's transaction history as an exchange import.

Spec: docs/phases/phase-6.md D166 as amended and D178 (step 5a). 0025 wrote
movement_import.format for BISON's export only (`bison_csv`). Binance's transaction history
is read into movements the same way (services/exchange/binance_csv.py), so the CHECK gains
`binance_csv`. Binance's withdrawal history is no import of its own (it completes the
withdrawals already read) and needs no value. Nothing else changes.

The downgrade is refused while a `binance_csv` import exists (the 0025 guard): the old CHECK
would refuse it, and a downgrade never drops data. It lifts FORCE RLS for the check, since the
owner has no workspace set.

Revision ID: 0028_binance_csv
Revises: 0027_depot
Create Date: 2026-11-13
"""

from alembic import op

revision = "0028_binance_csv"
down_revision = "0027_depot"
branch_labels = None
depends_on = None

OLD_FORMATS = ("bison_csv",)
NEW_FORMAT = "binance_csv"


def _sql_list(names: tuple[str, ...]) -> str:
    return ", ".join(f"'{n}'" for n in names)


def _set_formats(names: tuple[str, ...]) -> None:
    """Replace movement_import_format_check (0025's inline CHECK, named by Postgres)."""
    op.execute("ALTER TABLE movement_import DROP CONSTRAINT movement_import_format_check")
    op.execute(
        "ALTER TABLE movement_import ADD CONSTRAINT movement_import_format_check"
        f" CHECK (format IN ({_sql_list(names)}))"
    )


REFUSE_IF_DATA = f"""
DO $$
BEGIN
    ALTER TABLE movement_import NO FORCE ROW LEVEL SECURITY;
    IF EXISTS (SELECT 1 FROM movement_import WHERE format = '{NEW_FORMAT}') THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: an import is of format {NEW_FORMAT}'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Export and remove the data first; a downgrade never drops data.';
    END IF;
    ALTER TABLE movement_import FORCE ROW LEVEL SECURITY;
END
$$;
"""


def upgrade() -> None:
    _set_formats((*OLD_FORMATS, NEW_FORMAT))


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    _set_formats(OLD_FORMATS)
