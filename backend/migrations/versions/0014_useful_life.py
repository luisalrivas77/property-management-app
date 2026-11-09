"""Phase 3: tax input kind useful_life (a shorter actual useful life, D79).

Spec: docs/phases/phase-3.md D79. A `useful_life` row records the building's shorter actual
useful life from a Gutachten (§7 Abs. 4 S. 2 EStG): property- and year-scoped, `tax_year` the
first tax year it applies to, `document_id` the Gutachten, value {years}. Mirrors
hausbuch.schemas.tax.TaxInputKind and YEAR_SCOPED.

0013 wrote three CHECKs this changes without names, so Postgres generated theirs: the kind's
value list (a column constraint) and the two year-scope table constraints (tax_year set exactly
for the year-scoped kinds; a document for them). They are found by their definition, the only
CHECKs on tax_input naming 'ruecklage_spent', dropped, and recreated with names and the extended
lists. Everything else of 0013 stays. Runs as the schema owner like every migration; no grant
changes.

The downgrade is refused while any useful_life row exists (tax inputs are append-only, and the
old CHECK would refuse the row); it lifts FORCE RLS for the check, since the owner has no
workspace set (as 0007 to 0013). It then restores 0013's three CHECKs (under the names this
migration gave them).

Revision ID: 0014_useful_life
Revises: 0013_tax_year
Create Date: 2026-11-09
"""

from alembic import op

revision = "0014_useful_life"
down_revision = "0013_tax_year"
branch_labels = None
depends_on = None

ADDED_KIND = "useful_life"

# 0013's lists, in its order.
OLD_KINDS = (
    "'spread_82b', 'renovation_treatment', 'exclude_from_15pct', 'market_rent', "
    "'ruecklage_spent', 'interest_certified', 'pre_handover_income'"
)
OLD_YEAR_SCOPED = "'ruecklage_spent', 'interest_certified'"
NEW_KINDS = f"{OLD_KINDS}, '{ADDED_KIND}'"
NEW_YEAR_SCOPED = f"{OLD_YEAR_SCOPED}, '{ADDED_KIND}'"

KIND_CHECK = "tax_input_kind_check"
YEAR_CHECK = "tax_input_year_scope_check"
DOCUMENT_CHECK = "tax_input_year_document_check"

# 0013's unnamed CHECKs, found by definition: the three that name a year-scoped kind.
DROP_0013_CHECKS = """
DO $$
DECLARE
    c record;
    n integer := 0;
BEGIN
    FOR c IN
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'tax_input'::regclass AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%ruecklage_spent%'
    LOOP
        EXECUTE format('ALTER TABLE tax_input DROP CONSTRAINT %I', c.conname);
        n := n + 1;
    END LOOP;
    IF n <> 3 THEN
        RAISE EXCEPTION 'expected 0013''s three CHECKs naming ruecklage_spent, found %', n;
    END IF;
END
$$;
"""

REFUSE_USEFUL_LIFE = f"""
DO $$
BEGIN
    ALTER TABLE tax_input NO FORCE ROW LEVEL SECURITY;
    IF EXISTS (SELECT 1 FROM tax_input WHERE kind = '{ADDED_KIND}') THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: a tax input is useful_life'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Tax inputs are append-only and are not rewritten; a downgrade never '
                         'drops data.';
    END IF;
    ALTER TABLE tax_input FORCE ROW LEVEL SECURITY;
END
$$;
"""


def _add_checks(kinds: str, year_scoped: str) -> None:
    op.execute(f"ALTER TABLE tax_input ADD CONSTRAINT {KIND_CHECK} CHECK (kind IN ({kinds}))")
    op.execute(
        f"ALTER TABLE tax_input ADD CONSTRAINT {YEAR_CHECK}"
        f" CHECK ((kind IN ({year_scoped})) = (tax_year IS NOT NULL))"
    )
    op.execute(
        f"ALTER TABLE tax_input ADD CONSTRAINT {DOCUMENT_CHECK}"
        f" CHECK (kind NOT IN ({year_scoped}) OR document_id IS NOT NULL)"
    )


def _drop_checks() -> None:
    for name in (DOCUMENT_CHECK, YEAR_CHECK, KIND_CHECK):
        op.execute(f"ALTER TABLE tax_input DROP CONSTRAINT {name}")


def upgrade() -> None:
    op.execute(DROP_0013_CHECKS)
    _add_checks(NEW_KINDS, NEW_YEAR_SCOPED)


def downgrade() -> None:
    op.execute(REFUSE_USEFUL_LIFE)
    _drop_checks()
    _add_checks(OLD_KINDS, OLD_YEAR_SCOPED)
