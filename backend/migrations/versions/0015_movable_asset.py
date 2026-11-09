"""Phase 3: tax input kind movable_asset (the Einbauküche, D80).

Spec: docs/phases/phase-3.md D80 (Q15). A `movable_asset` row records a movable asset with its
own AfA, such as a fitted kitchen bought with the flat (BFH IX R 14/15): property-scoped,
`tax_year` its first year, `document_id` optional (the value may be an estimate), value
{label, amount, useful_life_years, in_purchase_price}. Mirrors hausbuch.schemas.tax
TaxInputKind, YEAR_SCOPED and DOCUMENT_REQUIRED.

0014 named the three CHECKs this concerns, so they are dropped by name and recreated:
- tax_input_kind_check: the kind's value list gains movable_asset;
- tax_input_year_scope_check: movable_asset is year-scoped (tax_year set exactly for it);
- tax_input_year_document_check: unchanged in content, recreated with its own list that does
  NOT gain movable_asset, since its document is optional. 0014 built the year and document
  CHECKs from one list; from here they have one list each.
Everything else of 0013 and 0014 stays. Runs as the schema owner like every migration; no grant
changes.

The downgrade is refused while any movable_asset row exists (tax inputs are append-only, and
the old CHECK would refuse the row); it lifts FORCE RLS for the check, since the owner has no
workspace set (as 0007 to 0014). It then restores 0014's three CHECKs under the same names.

Revision ID: 0015_movable_asset
Revises: 0014_useful_life
Create Date: 2026-11-09
"""

from alembic import op

revision = "0015_movable_asset"
down_revision = "0014_useful_life"
branch_labels = None
depends_on = None

ADDED_KIND = "movable_asset"

# 0014's lists, in its order.
OLD_KINDS = (
    "'spread_82b', 'renovation_treatment', 'exclude_from_15pct', 'market_rent', "
    "'ruecklage_spent', 'interest_certified', 'pre_handover_income', 'useful_life'"
)
OLD_YEAR_SCOPED = "'ruecklage_spent', 'interest_certified', 'useful_life'"
NEW_KINDS = f"{OLD_KINDS}, '{ADDED_KIND}'"
NEW_YEAR_SCOPED = f"{OLD_YEAR_SCOPED}, '{ADDED_KIND}'"
DOCUMENT_REQUIRED = OLD_YEAR_SCOPED  # a movable asset's document is optional

KIND_CHECK = "tax_input_kind_check"
YEAR_CHECK = "tax_input_year_scope_check"
DOCUMENT_CHECK = "tax_input_year_document_check"

REFUSE_MOVABLE_ASSET = f"""
DO $$
BEGIN
    ALTER TABLE tax_input NO FORCE ROW LEVEL SECURITY;
    IF EXISTS (SELECT 1 FROM tax_input WHERE kind = '{ADDED_KIND}') THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: a tax input is movable_asset'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Tax inputs are append-only and are not rewritten; a downgrade never '
                         'drops data.';
    END IF;
    ALTER TABLE tax_input FORCE ROW LEVEL SECURITY;
END
$$;
"""


def _add_checks(kinds: str, year_scoped: str, document_required: str) -> None:
    op.execute(f"ALTER TABLE tax_input ADD CONSTRAINT {KIND_CHECK} CHECK (kind IN ({kinds}))")
    op.execute(
        f"ALTER TABLE tax_input ADD CONSTRAINT {YEAR_CHECK}"
        f" CHECK ((kind IN ({year_scoped})) = (tax_year IS NOT NULL))"
    )
    op.execute(
        f"ALTER TABLE tax_input ADD CONSTRAINT {DOCUMENT_CHECK}"
        f" CHECK (kind NOT IN ({document_required}) OR document_id IS NOT NULL)"
    )


def _drop_checks() -> None:
    for name in (DOCUMENT_CHECK, YEAR_CHECK, KIND_CHECK):
        op.execute(f"ALTER TABLE tax_input DROP CONSTRAINT {name}")


def upgrade() -> None:
    _drop_checks()
    _add_checks(NEW_KINDS, NEW_YEAR_SCOPED, DOCUMENT_REQUIRED)


def downgrade() -> None:
    op.execute(REFUSE_MOVABLE_ASSET)
    _drop_checks()
    _add_checks(OLD_KINDS, OLD_YEAR_SCOPED, OLD_YEAR_SCOPED)
