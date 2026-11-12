"""A rental cost paid from a private account in Zu prüfen (docs/phases/phase-6.md D161).

Path 1 needs no card: a private line document-matched to a bill with a private_cost row is
booked hh_property_cost (services.matching) and, once confirmed, is the row's proof in the
draft (services.tax private_proofs). Path 2: the bill has no private_cost row yet, so the card
carries the rows rules.household.property_cost proposes, pre-filled for the existing
tax-input command, which the user confirms. Path 3: a line booked hh_property_cost without a
bill gets „Beleg hochladen“. Computed on read, so a card ends as soon as its row or bill
exists."""

from datetime import date
from uuid import UUID

from sqlalchemy import Connection, select

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.rules.household.property_cost import proposed_private_costs
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.api import InboxItem, InboxItemKind
from hausbuch.schemas.common import Fraction
from hausbuch.schemas.enums import AccountPurpose, AllocationStatus
from hausbuch.schemas.tax import PrivateCostValue, TaxInputCreate, TaxInputKind

from . import matching
from .tax_inputs import current_of_document

_LIVE = (AllocationStatus.CONFIRMED.value, AllocationStatus.PROPOSED.value)
_NO_KEY = b"\x00" * 32  # the bill's IBAN HMAC is not read here, only its parts
_ROW_REASON = (
    "Diese Zahlung von einem privaten Konto bezahlt eine Rechnung des Mietobjekts, die noch "
    "nicht als privat bezahlte Werbungskosten erfasst ist."
)
_ROW_QUESTION = "Als Werbungskosten des Mietobjekts übernehmen?"
_BILL_REASON = (
    "Diese Zahlung gehört zum Mietobjekt, aber es ist keine Rechnung dazu hinterlegt. Ohne "
    "Rechnung zählt sie nicht in der Anlage V."
)
_BILL_QUESTION = "Beleg hochladen"


def property_cost_items(conn: Connection) -> list[InboxItem]:
    """The path 2 and path 3 cards of every live private hh_property_cost line."""
    a, t = allocation_table.c, transaction_table.c
    rows = conn.execute(
        select(
            a.transaction_id,
            a.property_id,
            a.document_id,
            a.status,
            a.recorded_at,
            t.booking_date,
            t.amount,
        )
        .select_from(allocation_table.join(transaction_table, t.id == a.transaction_id))
        .join(account_table, account_table.c.id == t.account_id)
        .where(
            a.category == AllocationCategory.HH_PROPERTY_COST.value,
            a.status.in_(_LIVE),
            a.property_id.is_not(None),
            t.excluded_as_duplicate_of.is_(None),
            account_table.c.purpose == AccountPurpose.PRIVATE.value,
        )
        .order_by(t.booking_date, a.transaction_id)
    ).all()
    items = []
    for r in rows:
        if r.document_id is None:
            if r.status == AllocationStatus.CONFIRMED.value:
                items.append(_bill_item(r.transaction_id, r.property_id, r.recorded_at))
            continue
        rows_of_bill = current_of_document(conn, r.document_id, TaxInputKind.PRIVATE_COST)
        if rows_of_bill:
            continue  # path 1: the line proves the row
        proposed = _proposed(conn, r.document_id, r.booking_date, r.amount)
        if proposed:
            items.append(
                InboxItem(
                    kind=InboxItemKind.PROPERTY_COST_ROW,
                    created_at=r.recorded_at,
                    reasoning=_ROW_REASON,
                    question=_ROW_QUESTION,
                    transaction_id=r.transaction_id,
                    document_id=r.document_id,
                    property_id=r.property_id,
                    private_costs=proposed,
                )
            )
    return items


def _bill_item(transaction_id: UUID, property_id: UUID, at) -> InboxItem:  # noqa: ANN001
    return InboxItem(
        kind=InboxItemKind.PROPERTY_COST_BILL,
        created_at=at,
        reasoning=_BILL_REASON,
        question=_BILL_QUESTION,
        transaction_id=transaction_id,
        property_id=property_id,
    )


def _proposed(conn: Connection, document_id: UUID, booked_on: date, amount) -> list[TaxInputCreate]:  # noqa: ANN001
    """D161 (2): the bill's parts as private_cost rows for the year paid, with the bill as
    evidence; class financing for loan_fees, other for the rest (D135's convention)."""
    evidence = matching.evidence_of(conn, document_id)
    if evidence is None:
        return []
    bill = matching.document_facts(evidence, iban_key=_NO_KEY, loans_by_property={})
    if bill is None:
        return []
    label = (evidence.row.title or "Rechnung")[:120]
    return [
        TaxInputCreate(
            property_id=row.property_id,
            kind=TaxInputKind.PRIVATE_COST,
            tax_year=row.tax_year,
            document_id=row.document_id,
            value=PrivateCostValue(
                cost_class="financing" if row.category is AllocationCategory.LOAN_FEES else "other",
                label=label,
                amount=row.amount,
                share=Fraction(numerator=1, denominator=1),
                category=row.category,
            ),
        )
        for row in proposed_private_costs(booked_on, amount, bill)
    ]
