"""Phase 6: a typed balance's difference booked as a correction (D178 amended 13 Nov 2026, Luis).

An exchange's own export can miss a movement the exchange itself does not list (Binance's
transaction history left out one small incoming credit), so the typed balance never agrees
and the import stays „weicht ab“ with no action that resolves it (principle 7). A person may
now book that one coin's difference as a correction, once, with a reason they write; code
computes the quantity, the row is never automatic and never bridged by the app (D178 (6)).

- asset_movement.kind gains `balance_correction` (the 0025 value-list CHECK rebuilt as a
  superset; 0026 to 0029 never changed it).
- asset_movement gains `note`: the person's reason for a correction, free text, never sent to
  a model. Rows stay append-only: 0025's movement_forward() compares the whole row except
  status and superseded_by, so a note can never change after the insert.
- asset_movement_correction_check: a correction carries its note, is typed by a person
  (source user), has no EUR leg and no document; no other kind carries a note.

Phase 7's spec moves from 0030 to 0031.

The downgrade is refused while a correction exists (the 0025 guard): the old CHECK would
refuse it, and a downgrade never drops data (the note lives only on corrections). It lifts
FORCE RLS for the check, since the owner has no workspace set.

Revision ID: 0030_balance_correction
Revises: 0029_household_categories
Create Date: 2026-11-13
"""

from alembic import op

revision = "0030_balance_correction"
down_revision = "0029_household_categories"
branch_labels = None
depends_on = None

# 0025's list, in its order; 0026 to 0029 changed none.
OLD_KINDS = (
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
NEW_KIND = "balance_correction"


def _sql_list(names: tuple[str, ...]) -> str:
    return ", ".join(f"'{n}'" for n in names)


def _set_kinds(names: tuple[str, ...]) -> None:
    """Replace asset_movement's kind CHECK, named asset_movement_kind_check (no IF EXISTS)."""
    op.execute("ALTER TABLE asset_movement DROP CONSTRAINT asset_movement_kind_check")
    op.execute(
        "ALTER TABLE asset_movement ADD CONSTRAINT asset_movement_kind_check"
        f" CHECK (kind IN ({_sql_list(names)}))"
    )


REFUSE_IF_DATA = f"""
DO $$
BEGIN
    ALTER TABLE asset_movement NO FORCE ROW LEVEL SECURITY;
    IF EXISTS (SELECT 1 FROM asset_movement WHERE kind = '{NEW_KIND}') THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: a movement is a balance correction'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Export and remove the data first; a downgrade never drops data.';
    END IF;
    ALTER TABLE asset_movement FORCE ROW LEVEL SECURITY;
END
$$;
"""


def upgrade() -> None:
    _set_kinds((*OLD_KINDS, NEW_KIND))
    op.execute(
        "ALTER TABLE asset_movement ADD COLUMN note text CHECK (length(note) BETWEEN 1 AND 500)"
    )
    op.execute(
        "ALTER TABLE asset_movement ADD CONSTRAINT asset_movement_correction_check CHECK ("
        f" (kind = '{NEW_KIND}') = (note IS NOT NULL)"
        f" AND (kind <> '{NEW_KIND}' OR (source = 'user' AND eur_amount IS NULL"
        " AND document_id IS NULL)))"
    )
    op.execute(
        "COMMENT ON COLUMN asset_movement.note IS"
        " 'D178 amended: the reason a person wrote for a balance_correction; never sent to a"
        " model.'"
    )


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    op.execute("ALTER TABLE asset_movement DROP CONSTRAINT asset_movement_correction_check")
    op.execute("ALTER TABLE asset_movement DROP COLUMN note")
    _set_kinds(OLD_KINDS)
