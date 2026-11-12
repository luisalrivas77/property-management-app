"""A rental cost paid from a private account (phase 6 D161; fixture private_cost_link.yaml).

The Anlage V reads such a cost only through the phase 3 private_cost tax input; the private
line booked hh_property_cost is the input's proof, never summed. This module holds the pure
parts of the three paths: which rows a line proves (path 1, and the late import), which rows
a line proposes when its bill has none (path 2), and whether a booked line still needs its
bill (path 3, „Beleg hochladen“)."""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from uuid import UUID

from hausbuch.schemas.allocation import AllocationCategory, DocumentFacts
from hausbuch.schemas.common import Money, Schema
from hausbuch.schemas.tax import PRIVATE_COST_CATEGORIES

BILL_TODO = "Beleg hochladen"  # path 3's to-do label (no warning code, the fixture's reading)


class ProposedPrivateCost(Schema):
    """D161 (2): one private_cost row proposed from a bill's part and the line paying it, for
    the existing tax-input command (cost_class other, or financing for loan_fees); the tax
    year is the year paid, the line's booking year (D143's year-paid rule)."""

    property_id: UUID
    document_id: UUID
    category: AllocationCategory
    amount: Money
    tax_year: int


def proposed_private_costs(
    booked_on: date, amount: Decimal, bill: DocumentFacts
) -> list[ProposedPrivateCost]:
    """The rows a private line paying `bill` proposes when the bill has no private_cost row
    yet: one per deductible part (D135's parts), or the bill's own category for the line's
    amount when it was booked whole. Nothing for a bill of no property or with no deductible
    part (a purchase cost is not a private_cost row)."""
    if bill.property_id is None:
        return []
    parts = [(p.category, p.amount) for p in bill.parts] or [(bill.category, abs(amount))]
    return [
        ProposedPrivateCost(
            property_id=bill.property_id,
            document_id=bill.id,
            category=category,
            amount=part,
            tax_year=booked_on.year,
        )
        for category, part in parts
        if category in PRIVATE_COST_CATEGORIES and part > 0
    ]


def rows_proven(document_id: UUID | None, rows: Sequence[tuple[UUID, UUID | None]]) -> list[UUID]:
    """D161 (1): the private_cost rows (id, document_id) a line booked with `document_id`
    proves: those of the same bill, sorted by id."""
    if document_id is None:
        return []
    return sorted(row_id for row_id, doc in rows if doc == document_id)


def needs_bill(category: AllocationCategory, document_id: UUID | None) -> bool:
    """D161 (3): a line booked hh_property_cost without a bill gets „Beleg hochladen“."""
    return category is AllocationCategory.HH_PROPERTY_COST and document_id is None
