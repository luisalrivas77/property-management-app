"""Spec amendment 1: document subtype cost_basis_evidence.

Evidence of acquisition or production cost in the AfA basis (Nebenkosten invoices, renovation
invoices capitalised as anschaffungsnahe Herstellungskosten) must be kept for the life of
ownership + 10 years, like the purchase contract, because the §23 gain is computed from it.
Mirrors hausbuch.schemas.enums.DocumentSubtype.

Revision ID: 0002_cost_basis_evidence
Revises: 0001_phase1_vault_ledger
Create Date: 2026-11-05
"""

from alembic import op

revision = "0002_cost_basis_evidence"
down_revision = "0001_phase1_vault_ledger"
branch_labels = None
depends_on = None

_OLD = (
    "'purchase_contract', 'price_split_evidence', 'appraisal', "
    "'loan_contract', 'lease', 'handover_protocol', 'deposit_record'"
)
_NEW = _OLD + ", 'cost_basis_evidence'"


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
