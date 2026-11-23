"""Phase 3 D287: the seller's renovation inside the purchase price as a tax input.

Luis, 22 Nov 2026: „I just want to apply the renovation as the 15% and if they decline then
it's deducted as now“. The Kaufvertrag obliges the seller to renovate the flat after the
handover, paid inside the price. A person may claim that part of the price as the household's
own renovation (docs/phases/phase-3.md D287): a property-scoped `tax_input` row of kind
`price_renovation` (gross, net, the period the work was done, the day it was paid with the
price, the Kaufvertrag's clause quoted, a status claimed / accepted / declined), append-only and
superseded like every input.

`tax_input` gains the kind: not year-scoped (the row names the year paid in its value; a
correction or a „Nein“ supersedes it whatever the year) and document-required (the
Kaufvertrag). The three kind CHECKs (named by 0014/0015, last rebuilt by 0019) are dropped and
recreated as 0019 does, with 0019's lists plus the kind.

The table is append-only (SELECT and INSERT only for the app role, a forbid_mutation trigger),
so the downgrade refuses while any `price_renovation` row exists instead of dropping data,
lifting FORCE RLS for the check since the owner has no workspace set, and then restores 0019's
CHECKs exactly under the same names.

Revision ID: 0054_price_renovation
Revises: 0053_spot_price
Create Date: 2026-11-23
"""

from alembic import op

revision = "0054_price_renovation"
down_revision = "0053_spot_price"
branch_labels = None
depends_on = None

# 0019's tax_input lists, in its order (0018's + grundsteuer_schedule).
OLD_TAX_INPUT_KINDS = (
    "spread_82b",
    "renovation_treatment",
    "exclude_from_15pct",
    "market_rent",
    "ruecklage_spent",
    "interest_certified",
    "pre_handover_income",
    "useful_life",
    "movable_asset",
    "private_cost",
    "grundsteuer_schedule",
)
OLD_YEAR_SCOPED = (
    "ruecklage_spent",
    "interest_certified",
    "useful_life",
    "movable_asset",
    "private_cost",
    "grundsteuer_schedule",
)
OLD_DOCUMENT_REQUIRED = (
    "ruecklage_spent",
    "interest_certified",
    "useful_life",
    "grundsteuer_schedule",
)
PRICE_RENOVATION = "price_renovation"

KIND_CHECK = "tax_input_kind_check"
YEAR_CHECK = "tax_input_year_scope_check"
DOCUMENT_CHECK = "tax_input_year_document_check"


def _sql_list(names: tuple[str, ...]) -> str:
    return ", ".join(f"'{name}'" for name in names)


def _drop_tax_input_checks() -> None:
    for name in (DOCUMENT_CHECK, YEAR_CHECK, KIND_CHECK):
        op.execute(f"ALTER TABLE tax_input DROP CONSTRAINT {name}")


def _add_tax_input_checks(
    kinds: tuple[str, ...], year_scoped: tuple[str, ...], document_required: tuple[str, ...]
) -> None:
    op.execute(
        f"ALTER TABLE tax_input ADD CONSTRAINT {KIND_CHECK} CHECK (kind IN ({_sql_list(kinds)}))"
    )
    op.execute(
        f"ALTER TABLE tax_input ADD CONSTRAINT {YEAR_CHECK}"
        f" CHECK ((kind IN ({_sql_list(year_scoped)})) = (tax_year IS NOT NULL))"
    )
    op.execute(
        f"ALTER TABLE tax_input ADD CONSTRAINT {DOCUMENT_CHECK}"
        f" CHECK (kind NOT IN ({_sql_list(document_required)}) OR document_id IS NOT NULL)"
    )


REFUSE_IF_DATA = f"""
DO $$
BEGIN
    ALTER TABLE tax_input NO FORCE ROW LEVEL SECURITY;
    IF EXISTS (SELECT 1 FROM tax_input WHERE kind = '{PRICE_RENOVATION}') THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: a tax input is {PRICE_RENOVATION}'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Export and remove the data first; a downgrade never drops data.';
    END IF;
    ALTER TABLE tax_input FORCE ROW LEVEL SECURITY;
END
$$;
"""


def upgrade() -> None:
    _drop_tax_input_checks()
    _add_tax_input_checks(
        (*OLD_TAX_INPUT_KINDS, PRICE_RENOVATION),
        OLD_YEAR_SCOPED,
        (*OLD_DOCUMENT_REQUIRED, PRICE_RENOVATION),
    )


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    _drop_tax_input_checks()
    _add_tax_input_checks(OLD_TAX_INPUT_KINDS, OLD_YEAR_SCOPED, OLD_DOCUMENT_REQUIRED)
