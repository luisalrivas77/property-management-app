"""A rental cost paid from a private account in Zu prüfen (docs/phases/phase-6.md D161).

Path 1: a private line document-matched to a bill with a private_cost row is booked
hh_property_cost (services.matching) and, once confirmed, is the row's proof in the draft
(services.tax private_proofs). When a row is in another year than the line was booked, a card
proposes moving it to the year paid (D143's rule, review F2: property_cost_year); the move is
count_in_year_paid, applied only when the user confirms. Path 2: the bill has no private_cost
row yet, so the card carries the rows rules.household.property_cost proposes; „Übernehmen“
is record_proposed, which records them unless the bill's rows exist already (review F6); a
part the bill's Nebenkosten rows already record is never proposed again (D161 widened, 14 Nov
2026), and a private line paying a bill that is recorded by its rows is path 1
(rules.matching.match_recorded_bill).
Path 3: a line booked hh_property_cost without a bill gets „Beleg hochladen“; once a bill is
attached that yields no rows, a card says why and links the document (review F4:
property_cost_document). Computed on read, so a card ends as soon as its row or bill
exists."""

from datetime import date
from decimal import Decimal
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import Connection, select

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import property_acquisition_cost as acquisition_cost_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.rules.household.property_cost import proposed_private_costs
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.api import InboxItem, InboxItemKind
from hausbuch.schemas.common import Fraction
from hausbuch.schemas.enums import AccountPurpose, AllocationStatus
from hausbuch.schemas.tax import PrivateCostValue, TaxInput, TaxInputCreate, TaxInputKind

from . import matching
from .context import CommandContext
from .documents import EVIDENCE_KINDS, current_extraction
from .errors import NotFoundError
from .tax_inputs import (
    create_tax_input,
    current_of_document,
    lock_property,
    move_private_cost_year,
)

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
_YEAR_REASON = (
    "Diese Rechnung wurde am {paid} von einem privaten Konto bezahlt, also {year}. Privat "
    "bezahlte Kosten zählen im Jahr der Zahlung, die Kosten stehen aber noch im Steuerjahr "
    "{rows}."
)
_YEAR_QUESTION = "In {year} zählen?"
# Review F4: why an attached bill yields no row (the card links the document's page).
_DOC_NOT_READ = (
    "Zu dieser Zahlung ist ein Beleg hinterlegt, aber er ist noch nicht gelesen oder konnte "
    "nicht gelesen werden. Ohne gelesene Rechnung zählt die Zahlung nicht in der Anlage V."
)
_DOC_NOT_A_BILL = (
    "Der hinterlegte Beleg wurde nicht als Rechnung oder Bescheid gelesen. Ohne Rechnung "
    "zählt die Zahlung nicht in der Anlage V."
)
_DOC_NO_PROPERTY = (
    "Der hinterlegte Beleg ist noch keinem Mietobjekt zugeordnet: sein Vorschlag ist noch "
    "nicht übernommen. Erst dann zählt die Zahlung in der Anlage V."
)
_DOC_NO_AMOUNT = (
    "Im hinterlegten Beleg fehlt der Betrag oder das Datum der Rechnung. Ohne beides zählt "
    "die Zahlung nicht in der Anlage V."
)
_DOC_QUESTION = "Beleg öffnen und prüfen"


class _Line(NamedTuple):
    transaction_id: UUID
    property_id: UUID
    document_id: UUID | None
    status: str
    recorded_at: object
    booking_date: date
    amount: Decimal


def _lines(conn: Connection, *where) -> list[_Line]:  # noqa: ANN002
    """Every live private hh_property_cost row of a property, with its line."""
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
            *where,
        )
        .order_by(t.booking_date, a.transaction_id)
    ).all()
    return [_Line(*r) for r in rows]


def property_cost_items(conn: Connection) -> list[InboxItem]:
    """The cards of every live private hh_property_cost line: path 1's year move, path 2's
    rows, path 3's bill and the attached bill that yields no row."""
    items = []
    for r in _lines(conn):
        confirmed = r.status == AllocationStatus.CONFIRMED.value
        if r.document_id is None:
            if confirmed:
                items.append(_bill_item(r.transaction_id, r.property_id, r.recorded_at))
            continue
        rows_of_bill = current_of_document(conn, r.document_id, TaxInputKind.PRIVATE_COST)
        if rows_of_bill:  # path 1: the line proves the rows
            other_years = sorted({row.tax_year for row in rows_of_bill} - {r.booking_date.year})
            if confirmed and other_years:
                items.append(_year_item(r, other_years))
            continue
        proposed, why = _proposed(conn, r.document_id, r.booking_date, r.amount)
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
        elif why is not None and confirmed:
            items.append(
                InboxItem(
                    kind=InboxItemKind.PROPERTY_COST_DOCUMENT,
                    created_at=r.recorded_at,
                    reasoning=why,
                    question=_DOC_QUESTION,
                    transaction_id=r.transaction_id,
                    document_id=r.document_id,
                    property_id=r.property_id,
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


def _year_item(line: _Line, other_years: list[int]) -> InboxItem:
    """Review F2: the bill's rows count in another year than the line paid them; tax_year is
    the year paid, which count_in_year_paid moves them to."""
    year = line.booking_date.year
    return InboxItem(
        kind=InboxItemKind.PROPERTY_COST_YEAR,
        created_at=line.recorded_at,
        reasoning=_YEAR_REASON.format(
            paid=line.booking_date.strftime("%d.%m.%Y"),
            year=year,
            rows=", ".join(str(y) for y in other_years),
        ),
        question=_YEAR_QUESTION.format(year=year),
        transaction_id=line.transaction_id,
        document_id=line.document_id,
        property_id=line.property_id,
        tax_year=year,
    )


def _proposed(
    conn: Connection, document_id: UUID, booked_on: date, amount: Decimal
) -> tuple[list[TaxInputCreate], str | None]:
    """D161 (2): the bill's parts as private_cost rows for the year paid, with the bill as
    evidence; class financing for loan_fees, other for the rest (D135's convention). Without
    rows, why (review F4), or None when the bill was read and has no deductible part (a
    purchase cost is not a private_cost row: nothing to do here) or its Nebenkosten rows
    record every part (D161 widened: never a second row for a recorded cost)."""
    extraction = current_extraction(conn, document_id)
    if extraction is None:
        return [], _DOC_NOT_READ
    if extraction.kind not in EVIDENCE_KINDS:
        return [], _DOC_NOT_A_BILL
    evidence = matching.evidence_of(conn, document_id)
    if evidence is None:
        return [], _DOC_NOT_A_BILL
    if evidence.property_id is None:
        return [], _DOC_NO_PROPERTY
    bill = matching.document_facts(evidence, iban_key=_NO_KEY, loans_by_property={})
    if bill is None:
        return [], _DOC_NO_AMOUNT
    label = (evidence.row.title or "Rechnung")[:120]
    recorded = list(
        conn.execute(
            select(acquisition_cost_table.c.amount).where(
                acquisition_cost_table.c.document_id == document_id
            )
        ).scalars()
    )
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
        for row in proposed_private_costs(booked_on, amount, bill, recorded)
    ], None


def _the_line(conn: Connection, transaction_id: UUID, *, confirmed: bool) -> _Line:
    """The line's private hh_property_cost row with its bill; NotFoundError otherwise (an
    unknown line, another workspace's, or one that is no such payment)."""
    a = allocation_table.c
    where = [a.transaction_id == transaction_id, a.document_id.is_not(None)]
    if confirmed:
        where.append(a.status == AllocationStatus.CONFIRMED.value)
    found = _lines(conn, *where)
    if not found:
        raise NotFoundError("transaction", transaction_id)
    return found[0]


def record_proposed(ctx: CommandContext, transaction_id: UUID) -> list[TaxInput]:
    """POST /transactions/{id}/private-costs, path 2's „Übernehmen“: the rows the card
    proposes, each through the tax-input command (its event), all in one transaction. When
    the bill has current private_cost rows already (path 1, or a D135 reading applied
    meanwhile) nothing is written and those rows come back (review F6: no bill counted
    twice). NotFoundError for a line that is no private property-cost payment with a bill."""
    line = _the_line(ctx.conn, transaction_id, confirmed=False)
    assert line.document_id is not None
    lock_property(ctx.conn, line.property_id)  # one writer per property, as every tax input
    existing = current_of_document(ctx.conn, line.document_id, TaxInputKind.PRIVATE_COST)
    if existing:
        return existing
    proposed, _ = _proposed(ctx.conn, line.document_id, line.booking_date, line.amount)
    return [create_tax_input(ctx, row.property_id, row) for row in proposed]


def count_in_year_paid(ctx: CommandContext, transaction_id: UUID) -> list[TaxInput]:
    """POST /transactions/{id}/private-costs/year, the year card's „In <Jahr> zählen“ (review
    F2, D161 (1) with D143's rule): every current private_cost row of the line's bill moves
    to the year the confirmed line was booked (tax_inputs.move_private_cost_year, events
    written). The rows as they stand after; nothing is written when they are in that year
    already. NotFoundError for a line that is no confirmed private property-cost payment with
    a bill."""
    line = _the_line(ctx.conn, transaction_id, confirmed=True)
    assert line.document_id is not None
    year = line.booking_date.year
    rows = current_of_document(ctx.conn, line.document_id, TaxInputKind.PRIVATE_COST)
    return [move_private_cost_year(ctx, row.id, year) for row in rows]
