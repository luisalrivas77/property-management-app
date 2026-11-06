"""Spec amendment 3: document subtype grundsteuerbescheid.

The document checklist (spec amendment 3, 6 Nov 2026) asks for the property's
Grundsteuerbescheid (type bescheid). It stays a tax record (10 years after the tax year), so
retention is unchanged. Mirrors hausbuch.schemas.enums.DocumentSubtype.

Revision ID: 0004_grundsteuerbescheid
Revises: 0003_auth_passkeys
Create Date: 2026-11-06
"""

from alembic import op

revision = "0004_grundsteuerbescheid"
down_revision = "0003_auth_passkeys"
branch_labels = None
depends_on = None

_OLD = (
    "'purchase_contract', 'price_split_evidence', 'appraisal', "
    "'loan_contract', 'lease', 'handover_protocol', 'deposit_record', 'cost_basis_evidence'"
)
_NEW = _OLD + ", 'grundsteuerbescheid'"


def _set_subtypes(values: str) -> None:
    op.execute("ALTER TABLE document DROP CONSTRAINT document_subtype_check")
    op.execute(
        f"ALTER TABLE document ADD CONSTRAINT document_subtype_check CHECK (subtype IN ({values}))"
    )


def upgrade() -> None:
    _set_subtypes(_NEW)


def downgrade() -> None:
    # Fails while any document uses the new subtype: retype those documents first.
    _set_subtypes(_OLD)
