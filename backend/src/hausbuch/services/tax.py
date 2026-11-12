"""The Anlage V draft of one property and tax year (docs/phases/phase-3.md §5.11, §10).

Loads the DraftFacts the pure rule `hausbuch.rules.tax.draft.draft` reads, inside the
caller's workspace transaction, and calls it with the year's rule set. Read only: no events,
no writes; the draft is computed on every read and never stored (D59).

What the facts hold, and what they leave out:
- Tenancies are loaded from the tenancy table without their parties, and transactions
  without their counterparty name or purpose text: a counterparty is at most its masked IBAN
  (D5). No tenant name reaches the facts or the response.
- Allocations: every confirmed allocation with a non-zero amount (D70) of the property, all
  years (the 15 % window, §82b and the AfA additions reach back), plus the confirmed lines
  without a property on the accounts that carry the property's allocations (the dedicated
  rental account's bank fees and owner transfers; property_id None, which the draft reports).
  A line merged away as a duplicate is never read. Only lines of a rental-purpose account are
  read (phase 6 D159, D161): a private account's line, even one booked with the property (a
  tenant paying into it, a bill of the flat paid from it), adds no line, no coverage and no
  open line to the draft until step 2 reads such a row as a bill's proof of payment.
- due_on is resolved here, never guessed by the rule (§5.1 step 4): a loan line takes the
  schedule entry due nearest its booking date within 10 days (D39, as the matcher does); rent
  and its prepayment the tenancy's rent_due_day in the service period's first month (D67);
  any other recurring line the period's first day. A loan line without a schedule entry
  keeps due_on None, so the rule counts it in the year paid with a note
  (`tax.due_date_unresolved` is that case, reported as a note, not raised).
- Tax inputs: every row of the property's history, superseded and withdrawn ones included;
  the rules tell the row in force from the rest (TaxInputFact).
- Coverage: per account that carries any of the property's allocations, the last booking
  day of its reconciled imports.
- Pending: the lines of the tax year on those accounts, from reconciled imports and not
  merged away, that have no confirmed allocation yet (proposed, or not matched at all); a
  proposed line counts only when one of its proposed parts is of this property or of none.
- Payment proofs (phase 3 D143): every document of subtype payment_proof linked to a bill
  of an acquisition cost row or a private_cost row of the property (document_link target
  document), with the amount of its current reading as a magnitude (None: not read).
"""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, and_, exists, func, or_, select

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import document as document_table
from hausbuch.db.tables import document_link as link_table
from hausbuch.db.tables import property_price_split as split_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import tax_input as tax_input_table
from hausbuch.db.tables import tenancy as tenancy_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.rules import document_checklist as checklist_rule
from hausbuch.rules.errors import TAX_YEAR_NOT_AVAILABLE, RuleError
from hausbuch.rules.extraction_parse import cost_lines as parse_cost_lines
from hausbuch.rules.hand_fields import HAND_FIELD_MAP, hand_values
from hausbuch.rules.loan_schedule import build_schedule
from hausbuch.rules.matching import LOAN_WINDOW_DAYS
from hausbuch.rules.tax import registry
from hausbuch.rules.tax.draft import draft
from hausbuch.rules.tax.rounding import cents
from hausbuch.schemas.allocation import AllocationCategory, ServicePeriod
from hausbuch.schemas.checklist import ChecklistFacts
from hausbuch.schemas.document import Document
from hausbuch.schemas.enums import (
    AccountPurpose,
    AllocationStatus,
    DocumentSubtype,
    ExtractionKind,
    ImportStatus,
    LinkTarget,
)
from hausbuch.schemas.loan import Loan, ScheduleEntry
from hausbuch.schemas.tax import (
    AccountCoverage,
    AfaCostRow,
    AfaSplit,
    AllocationFact,
    AnlageVDraft,
    DraftFacts,
    OpenLines,
    PaymentProofFact,
    RuleSet,
    TaxInputFact,
    TaxLoan,
    TaxOwner,
    TaxProperty,
    TaxRent,
    TaxTenancy,
    TaxUnit,
    UnlinkedProofFact,
)

from .checklist import checklist_facts
from .documents import current_extraction
from .loans import list_loans
from .properties import get_ownership, get_property, list_acquisition_costs
from .tenancies import list_rent_schedule
from .units import list_units

_CONFIRMED = AllocationStatus.CONFIRMED.value
_PROPOSED = AllocationStatus.PROPOSED.value
_SUPERSEDED = AllocationStatus.SUPERSEDED.value
_CENT = Decimal("0.01")
C = AllocationCategory
_LOAN_CATEGORIES = frozenset({C.LOAN_INTEREST, C.LOAN_PRINCIPAL})
_RENT_CATEGORIES = frozenset({C.RENT_COLD, C.OPERATING_COSTS_PREPAYMENT})


def anlage_v_draft(
    conn: Connection, property_id: UUID, *, tax_year: int, today: date
) -> AnlageVDraft:
    """The property's Anlage V draft for `tax_year`, computed now. NotFoundError for an
    unknown property (or one of another workspace); RuleError `tax.year_not_available` for a
    year outside the checklist's available_years; RuleError `tax.no_rule_set` for a year
    without a rule set."""
    checklist, documents, rule_set = _year_checked(conn, property_id, tax_year, today)
    facts = draft_facts(
        conn,
        property_id,
        tax_year=tax_year,
        today=today,
        checklist=checklist,
        documents=documents,
        private_proofs=True,
    )
    return draft(facts, tax_year, rule_set)


def anlage_v_sources(
    conn: Connection, property_id: UUID, *, tax_year: int, today: date
) -> list[AllocationFact]:
    """The payments a draft line can cite, for the sources drawer (§11): the same confirmed
    allocations the draft reads (every year of the property, since the 15 % window, §82b and
    the ten-day rule reach across years), as AllocationFact: date, amount, category, period,
    transaction and Beleg ids and the masked counterparty IBAN only, never a name or purpose
    text (D5). Also the private-account lines the draft takes as a bill's proof (phase 6
    D161), so a proof the draft cites resolves here too. The same checks and errors as
    anlage_v_draft."""
    _year_checked(conn, property_id, tax_year, today)
    units = list_units(conn, property_id)
    tenancies = _tenancies(conn, [u.id for u in units])
    loans = list_loans(conn, property_id)
    return _allocations(
        conn,
        property_id,
        _accounts(conn, property_id),
        due_day={t.id: t.rent_due_day for t in tenancies},
        loans={lo.id: lo for lo in loans},
    ) + _private_proofs(conn, property_id)


def _year_checked(
    conn: Connection, property_id: UUID, tax_year: int, today: date
) -> tuple[ChecklistFacts, dict[UUID, Document], RuleSet]:
    """NotFoundError for an unknown property (or one of another workspace); RuleError
    `tax.year_not_available` outside the checklist's available_years; `tax.no_rule_set`
    without a rule set. Returns the checklist facts and the year's rule set."""
    checklist, documents = checklist_facts(conn, property_id)
    years = checklist_rule.available_years(checklist, today)
    if tax_year not in years:
        raise RuleError(
            TAX_YEAR_NOT_AVAILABLE,
            f"tax_year {tax_year} is outside this property's years {years[0]}-{years[-1]}",
        )
    return checklist, documents, registry.rule_set(tax_year)


def draft_facts(
    conn: Connection,
    property_id: UUID,
    *,
    tax_year: int,
    today: date,
    checklist: ChecklistFacts | None = None,
    documents: dict[UUID, Document] | None = None,
    private_proofs: bool = False,
) -> DraftFacts:
    """Everything draft() reads for one property (§5.11). `checklist` and `documents` are
    checklist_facts' result when the caller has it already; loaded otherwise.
    `private_proofs` (phase 6 D161): also the confirmed private-account lines booked
    hh_property_cost with this property and a bill, which draft() takes as the bill's proof
    and never sums; off for phase 4's figures, which read the allocations directly."""
    if checklist is None or documents is None:
        checklist, documents = checklist_facts(conn, property_id)  # 404 for an unknown one
    prop = get_property(conn, property_id)
    assert prop is not None  # checklist_facts raised NotFoundError otherwise

    units = list_units(conn, property_id)
    tenancies = _tenancies(conn, [u.id for u in units])
    loans = list_loans(conn, property_id)
    accounts = _accounts(conn, property_id)
    allocations = _allocations(
        conn,
        property_id,
        accounts,
        due_day={t.id: t.rent_due_day for t in tenancies},
        loans={lo.id: lo for lo in loans},
    )
    ownership = get_ownership(conn, property_id)
    cost_rows = _cost_rows(conn, property_id, allocations)
    if private_proofs:
        allocations += _private_proofs(conn, property_id)
    tax_inputs = _tax_inputs(conn, property_id)
    bills = {r.document_id for r in cost_rows} | {r.document_id for r in tax_inputs}
    bills.discard(None)

    return DraftFacts(
        property=TaxProperty(
            id=prop.id,
            label=prop.label,
            kind=prop.kind,
            address=prop.address,
            build_year=prop.build_year,
            notarized_on=prop.notarized_on,
            purchase_date=prop.purchase_date,
            ownership_ended_on=prop.ownership_ended_on,
            purchase_price=prop.purchase_price,
            living_area_m2=prop.living_area_m2,
            grundsteuer_reference=_grundsteuer_reference(conn, checklist, tax_year),
        ),
        owners=[]
        if ownership is None
        else [TaxOwner(owner_id=s.owner_id, share=s.share) for s in ownership.shares],
        units=[TaxUnit(id=u.id, label=u.label, floor_area_m2=u.floor_area_m2) for u in units],
        tenancies=tenancies,
        split=_split(conn, prop.selected_price_split_id),
        cost_rows=cost_rows,
        loans=[TaxLoan(id=lo.id, lender=lo.lender) for lo in loans],
        allocations=allocations,
        tax_inputs=tax_inputs,
        payment_proofs=_payment_proofs(conn, bills),
        unlinked_proofs=_unlinked_proofs(conn, property_id),
        bill_parts=bill_parts(conn, bills),
        checklist=checklist,
        today=today,
        coverage=_coverage(conn, accounts),
        pending=_pending(conn, property_id, accounts, tax_year),
    )


# --- Records -------------------------------------------------------------------------


def _tenancies(conn: Connection, unit_ids: Sequence[UUID]) -> list[TaxTenancy]:
    """The units' tenancies with their rent rows in force (a row another row supersedes is
    not, as rules.rent.rent_at reads them). No parties are read (D5)."""
    if not unit_ids:
        return []
    t = tenancy_table.c
    rows = conn.execute(
        select(t.id, t.unit_id, t.start_date, t.end_date, t.rent_due_day, t.operating_costs_terms)
        .where(t.unit_id.in_(unit_ids))
        .order_by(t.start_date, t.id)
    ).all()
    tenancies = []
    for row in rows:
        schedule = list_rent_schedule(conn, row.id)
        superseded = {r.supersedes_id for r in schedule if r.supersedes_id is not None}
        rents = [
            TaxRent(
                effective_from=r.effective_from,
                cold_rent=r.cold_rent,
                operating_costs_prepayment=r.operating_costs_prepayment,
            )
            for r in schedule
            if r.id not in superseded
        ]
        tenancies.append(TaxTenancy.model_validate(dict(row._mapping) | {"rents": rents}))
    return tenancies


def _split(conn: Connection, split_id: UUID | None) -> AfaSplit | None:
    if split_id is None:
        return None
    s = split_table.c
    row = conn.execute(
        select(s.id, s.source, s.building_share, s.evidence_document_id).where(s.id == split_id)
    ).one()
    return AfaSplit.model_validate(dict(row._mapping))


def _cost_rows(
    conn: Connection, property_id: UUID, allocations: Sequence[AllocationFact]
) -> list[AfaCostRow]:
    """The property's acquisition cost rows, each with the confirmed acquisition_cost
    allocations that paid it (AfaCostRow.paid_by): first by the same Beleg, then, for a row
    still unpaid, one allocation of exactly its amount (earliest first). Each allocation pays
    one row at most."""
    costs = list_acquisition_costs(conn, property_id)
    payments = sorted(
        (
            a
            for a in allocations
            if a.category is C.ACQUISITION_COST and a.property_id == property_id
        ),
        key=lambda a: (a.booking_date, str(a.allocation_id)),
    )
    used: set[UUID] = set()
    paid_by: dict[UUID, list[UUID]] = {c.id: [] for c in costs}
    for cost in costs:
        if cost.document_id is None:
            continue
        for a in payments:
            if a.allocation_id not in used and a.document_id == cost.document_id:
                paid_by[cost.id].append(a.allocation_id)
                used.add(a.allocation_id)
    for cost in costs:
        if paid_by[cost.id]:
            continue
        for a in payments:
            if a.allocation_id not in used and -a.amount == cost.amount:
                paid_by[cost.id].append(a.allocation_id)
                used.add(a.allocation_id)
                break
    return [
        AfaCostRow(
            id=c.id,
            kind=c.kind,
            amount=c.amount,
            paid_on=c.paid_on,
            document_id=c.document_id,
            paid_by=paid_by[c.id],
        )
        for c in costs
    ]


def _payment_proofs(conn: Connection, bills: set[UUID]) -> list[PaymentProofFact]:
    """D143: the payment_proof documents linked to one of the bills, each with its reading's
    amount as a magnitude (a transfer is often read as negative); None when the proof has no
    reading yet or its amount was not read, which the draft counts as a proof."""
    if not bills:
        return []
    k, d = link_table.c, document_table.c
    rows = conn.execute(
        select(k.document_id, k.target_document_id)
        .join(document_table, d.id == k.document_id)
        .where(
            k.target_document_id.in_(bills),
            d.subtype == DocumentSubtype.PAYMENT_PROOF.value,
        )
        .order_by(k.target_document_id, k.document_id)
    ).all()
    return [
        PaymentProofFact(
            proof_document_id=row.document_id,
            bill_document_id=row.target_document_id,
            amount=_proof_amount(conn, row.document_id),
        )
        for row in rows
    ]


def _unlinked_proofs(conn: Connection, property_id: UUID) -> list[UnlinkedProofFact]:
    """D149: the payment_proof documents linked to no bill (no link of target `document`),
    linked to this property or to no property at all, each with its read amount, for the
    to-do's „Vorhandenen Nachweis zuordnen“."""
    k, d = link_table.c, document_table.c
    linked_to = select(link_table.c.document_id).where(k.document_id == d.id)
    rows = conn.execute(
        select(d.id)
        .where(
            d.subtype == DocumentSubtype.PAYMENT_PROOF.value,
            ~exists(linked_to.where(k.target_document_id.is_not(None))),
            or_(
                exists(linked_to.where(k.property_id == property_id)),
                ~exists(linked_to.where(k.property_id.is_not(None))),
            ),
        )
        .order_by(d.id)
    ).all()
    return [
        UnlinkedProofFact(proof_document_id=row.id, amount=_proof_amount(conn, row.id))
        for row in rows
    ]


def bill_parts(conn: Connection, bills: set[UUID]) -> dict[UUID, list[Decimal]]:
    """Phase 3 D150: per bill, the amounts of the cost lines its current reading states
    (bill_lines); a bill without lines is left out. The draft (DraftFacts.bill_parts) and the
    pre-fill (ExistingProofBill.parts) load them here."""
    out: dict[UUID, list[Decimal]] = {}
    for bill in sorted(bills, key=str):
        lines = bill_lines(conn, bill)
        if lines:
            out[bill] = lines
    return out


def bill_lines(conn: Connection, document_id: UUID) -> list[Decimal]:
    """Phase 3 D150: the amounts of the cost lines a bill's current reading states (an
    invoice's or Bescheid's cost_lines, phase 2 D135, parsed as the pre-fill parses them),
    each signed as read; empty without such a reading, for a reading typed by hand, or when
    a line does not parse. Whether they are the bill's parts (they sum to its total) is
    rules.proof_match.bill_parts's test."""
    extraction = current_extraction(conn, document_id)
    if extraction is None or extraction.kind not in (
        ExtractionKind.INVOICE,
        ExtractionKind.BESCHEID,
    ):
        return []
    read = getattr(extraction.fields, "cost_lines", None)
    lines = None if read is None else parse_cost_lines(read.value)
    return [] if lines is None else [line.amount.quantize(_CENT) for line in lines]


def _proof_amount(conn: Connection, document_id: UUID) -> Decimal | None:
    """A payment proof's amount: the reading's `amount`, or what a person typed as Betrag
    (the hand field over either reading, rules.hand_fields)."""
    extraction = current_extraction(conn, document_id)
    if extraction is None:
        return None
    if extraction.kind is ExtractionKind.MANUAL:
        amount = getattr(extraction.fields, "amount_total", None)
    else:
        mapping = HAND_FIELD_MAP.get(extraction.kind, {})
        if "amount_total" not in mapping:
            return None
        amount = hand_values(extraction.fields, mapping).amount_total
    return None if amount is None else abs(amount).quantize(_CENT)


def _tax_inputs(conn: Connection, property_id: UUID) -> list[TaxInputFact]:
    """Every tax_input row of the property, in the order they were recorded. Rows written in
    one transaction share recorded_at, so ties are broken by the supersedes chain (a row always
    after the row it supersedes), then by id."""
    ti = tax_input_table.c
    rows = conn.execute(
        select(
            ti.recorded_at,
            ti.id,
            ti.property_id,
            ti.kind,
            ti.tax_year,
            ti.unit_id,
            ti.loan_id,
            ti.allocation_id,
            ti.document_id,
            ti.value,
            ti.note,
            ti.supersedes_id,
            ti.withdrawn,
        ).where(ti.property_id == property_id)
    ).all()
    supersedes = {r.id: r.supersedes_id for r in rows}

    def depth(row_id: UUID) -> int:
        n = 0
        while (row_id := supersedes.get(row_id)) is not None:
            n += 1
        return n

    ordered = sorted(rows, key=lambda r: (r.recorded_at, depth(r.id), r.id))
    return [
        TaxInputFact.model_validate({k: v for k, v in r._mapping.items() if k != "recorded_at"})
        for r in ordered
    ]


def _grundsteuer_reference(
    conn: Connection, checklist: ChecklistFacts, tax_year: int
) -> str | None:
    """The Aktenzeichen of the property's Grundsteuerbescheid (§4.2, header.aktenzeichen):
    the extraction's `reference` of the newest Bescheid in force in the tax year (first year
    ≤ tax_year), else of the newest one; None when no Bescheid states it."""
    candidates = [
        d
        for d in checklist.documents
        if d.subtype is DocumentSubtype.GRUNDSTEUERBESCHEID
        and any(link.target_type is LinkTarget.PROPERTY for link in d.links)
    ]
    candidates.sort(
        key=lambda d: (
            d.tax_year is None or d.tax_year <= tax_year,
            d.tax_year or 0,
            d.received_at,
        ),
        reverse=True,
    )
    for document in candidates:
        extraction = current_extraction(conn, document.id)
        if extraction is None:
            continue
        item = getattr(extraction.fields, "reference", None)
        value = getattr(item, "value", item)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


# --- Accounts, allocations, coverage, pending -------------------------------------------


def _rental_accounts():
    """Phase 6 D159, D161: the accounts whose lines the draft reads, the rental-purpose ones."""
    return select(account_table.c.id).where(account_table.c.purpose == AccountPurpose.RENTAL.value)


def _accounts(conn: Connection, property_id: UUID) -> list[UUID]:
    """The rental-purpose accounts that carry any current (not superseded) allocation of the
    property."""
    a, t = allocation_table.c, transaction_table.c
    return list(
        conn.execute(
            select(t.account_id)
            .select_from(transaction_table.join(allocation_table, a.transaction_id == t.id))
            .where(
                a.property_id == property_id,
                a.status != _SUPERSEDED,
                t.account_id.in_(_rental_accounts()),
            )
            .distinct()
            .order_by(t.account_id)
        ).scalars()
    )


def _allocations(
    conn: Connection,
    property_id: UUID,
    accounts: Sequence[UUID],
    *,
    due_day: dict[UUID, int],
    loans: dict[UUID, Loan],
) -> list[AllocationFact]:
    a, t = allocation_table.c, transaction_table.c
    in_scope = a.property_id == property_id
    if accounts:
        in_scope = or_(in_scope, and_(a.property_id.is_(None), t.account_id.in_(accounts)))
    rows = conn.execute(
        select(
            a.id,
            a.transaction_id,
            a.amount,
            a.category,
            a.property_id,
            a.unit_id,
            a.tenancy_id,
            a.loan_id,
            a.service_period_start,
            a.service_period_end,
            a.recurring,
            a.document_id,
            t.booking_date,
            t.value_date,
            t.counterparty_iban_masked,
        )
        .select_from(allocation_table.join(transaction_table, t.id == a.transaction_id))
        .where(
            a.status == _CONFIRMED,
            a.amount != 0,
            t.excluded_as_duplicate_of.is_(None),
            t.account_id.in_(_rental_accounts()),
            in_scope,
        )
        .order_by(t.booking_date, t.id, a.recorded_at, a.id)
    ).all()

    schedules: dict[UUID, list[ScheduleEntry]] = {}
    bills: dict[UUID, tuple[Decimal | None, Decimal | None]] = {}
    paid: dict[UUID, Decimal] = {}  # per document, the confirmed allocations' magnitudes
    for r in rows:
        if r.document_id is not None:
            paid[r.document_id] = paid.get(r.document_id, Decimal(0)) + abs(r.amount)
    facts = []
    for r in rows:
        category = AllocationCategory(r.category)
        period = (
            None
            if r.service_period_start is None
            else ServicePeriod(start=r.service_period_start, end=r.service_period_end)
        )
        recurring = r.recurring
        due_on = None
        loan = loans.get(r.loan_id) if r.loan_id is not None else None
        if category in _LOAN_CATEGORIES and loan is not None:
            entry = _loan_entry(loan, r.booking_date, schedules)
            if entry is not None:
                due_on = entry.due_on
                if period is None:  # the instalment's month, as the loan matcher books it
                    period = ServicePeriod(
                        start=entry.due_on.replace(day=1), end=_month_end(entry.due_on)
                    )
                    recurring = True
        elif recurring and period is not None:
            if category in _RENT_CATEGORIES and r.tenancy_id in due_day:
                due_on = period.start.replace(day=due_day[r.tenancy_id])
            else:
                due_on = period.start
        invoice_net = None
        if r.document_id is not None:
            if r.document_id not in bills:
                bills[r.document_id] = (
                    _invoice_net(conn, r.document_id),
                    _invoice_gross(conn, r.document_id),
                )
            net, gross = bills[r.document_id]
            invoice_net = _net_share(net, gross or paid[r.document_id], abs(r.amount))
        facts.append(
            AllocationFact(
                allocation_id=r.id,
                transaction_id=r.transaction_id,
                booking_date=r.booking_date,
                value_date=r.value_date,
                amount=r.amount,
                category=category,
                recurring=recurring,
                service_period=period,
                due_on=due_on,
                property_id=r.property_id,
                unit_id=r.unit_id,
                tenancy_id=r.tenancy_id,
                loan_id=r.loan_id,
                document_id=r.document_id,
                invoice_net=invoice_net,
                counterparty_iban_masked=r.counterparty_iban_masked,
            )
        )
    return facts


def _private_proofs(conn: Connection, property_id: UUID) -> list[AllocationFact]:
    """D161: the confirmed private-purpose lines booked hh_property_cost with the property and
    a bill, as the draft reads them (account_purpose private: a proof, never a cost)."""
    a, t = allocation_table.c, transaction_table.c
    rows = conn.execute(
        select(
            a.id,
            a.transaction_id,
            a.amount,
            a.document_id,
            t.booking_date,
            t.value_date,
            t.counterparty_iban_masked,
        )
        .select_from(allocation_table.join(transaction_table, t.id == a.transaction_id))
        .join(account_table, account_table.c.id == t.account_id)
        .where(
            a.status == _CONFIRMED,
            a.amount != 0,
            a.category == C.HH_PROPERTY_COST.value,
            a.property_id == property_id,
            a.document_id.is_not(None),
            t.excluded_as_duplicate_of.is_(None),
            account_table.c.purpose == AccountPurpose.PRIVATE.value,
        )
        .order_by(t.booking_date, t.id, a.id)
    )
    return [
        AllocationFact(
            allocation_id=r.id,
            transaction_id=r.transaction_id,
            booking_date=r.booking_date,
            value_date=r.value_date,
            amount=r.amount,
            category=C.HH_PROPERTY_COST,
            property_id=property_id,
            document_id=r.document_id,
            account_purpose=AccountPurpose.PRIVATE,
            counterparty_iban_masked=r.counterparty_iban_masked,
        )
        for r in rows
    ]


def _loan_entry(
    loan: Loan, booking_date: date, schedules: dict[UUID, list[ScheduleEntry]]
) -> ScheduleEntry | None:
    """The schedule entry the loan matcher would have used (D39): due nearest the booking
    date within LOAN_WINDOW_DAYS either side, None when none is that near or two tie. A
    debit in a flagged loan's interest-only time has no entry: it is not an instalment."""
    terms = loan.terms
    if terms.interest_only_before_first_payment and booking_date < terms.first_payment_on:
        return None
    if loan.id not in schedules:
        schedules[loan.id] = build_schedule(terms).entries
    near = [
        e for e in schedules[loan.id] if abs((e.due_on - booking_date).days) <= LOAN_WINDOW_DAYS
    ]
    if not near:
        return None
    best = min(abs((e.due_on - booking_date).days) for e in near)
    nearest = [e for e in near if abs((e.due_on - booking_date).days) == best]
    return nearest[0] if len(nearest) == 1 else None


def _month_end(day: date) -> date:
    following = date(day.year + day.month // 12, day.month % 12 + 1, 1)
    return date.fromordinal(following.toordinal() - 1)


def _net_share(net: Decimal | None, gross: Decimal, part: Decimal) -> Decimal | None:
    """This allocation's share of its bill's net (phase 2 D135): a bank line split by the
    bill's lines carries several allocations citing one invoice, and each counts only its
    part, net × part ÷ gross, half-up to the cent. gross is the bill's amount_total, else the
    sum of its confirmed allocations. A part as large as the whole bill (or within the
    matcher's cent above it) keeps the whole net; a share of 0.00 is None."""
    if net is None or gross <= 0:
        return None
    share = min(cents(net * part / gross), net)
    return share if share > 0 else None


def _invoice_gross(conn: Connection, document_id: UUID) -> Decimal | None:
    """The invoice extraction's amount_total (a magnitude), to pro-rate its net (D135)."""
    extraction = current_extraction(conn, document_id)
    if extraction is None or extraction.kind is not ExtractionKind.INVOICE:
        return None
    item: Any = getattr(extraction.fields, "amount_total", None)
    value = getattr(item, "value", None)
    if value is None:
        return None
    try:
        amount = abs(Decimal(str(value))).quantize(_CENT)
    except InvalidOperation:
        return None
    return amount if amount > 0 else None


def _invoice_net(conn: Connection, document_id: UUID) -> Decimal | None:
    """The invoice extraction's amount_net (a magnitude), for the 15 % tracker (D56)."""
    extraction = current_extraction(conn, document_id)
    if extraction is None or extraction.kind is not ExtractionKind.INVOICE:
        return None
    item: Any = getattr(extraction.fields, "amount_net", None)
    value = getattr(item, "value", None)
    if value is None:
        return None
    try:
        amount = Decimal(str(value)).quantize(_CENT)
    except InvalidOperation:
        return None
    return amount if amount > 0 else None


def _coverage(conn: Connection, accounts: Sequence[UUID]) -> list[AccountCoverage]:
    """Per account, the last booking day of its reconciled imports (None: none yet)."""
    if not accounts:
        return []
    s = import_table.c
    last = dict(
        conn.execute(
            select(s.account_id, func.max(s.last_booking_on))
            .where(s.account_id.in_(accounts), s.status == ImportStatus.RECONCILED.value)
            .group_by(s.account_id)
        ).all()
    )
    return [AccountCoverage(account_id=i, covered_through=last.get(i)) for i in accounts]


def _pending(
    conn: Connection, property_id: UUID, accounts: Sequence[UUID], tax_year: int
) -> OpenLines:
    """The lines of the year that are not in the draft because nobody confirmed them yet:
    count and signed sum (D59). Lines of an import that is not reconciled are left out (the
    coverage shows those months), and so are lines merged away as duplicates."""
    if not accounts:
        return OpenLines(count=0)
    a, t, s = allocation_table.c, transaction_table.c, import_table.c
    current = and_(a.transaction_id == t.id, a.status != _SUPERSEDED)
    confirmed = exists().where(current, a.status == _CONFIRMED).correlate(transaction_table)
    any_current = exists().where(current).correlate(transaction_table)
    proposed_here = (
        exists()
        .where(
            current,
            a.status == _PROPOSED,
            or_(a.property_id == property_id, a.property_id.is_(None)),
        )
        .correlate(transaction_table)
    )
    row = conn.execute(
        select(func.count(), func.coalesce(func.sum(t.amount), 0))
        .select_from(transaction_table.join(import_table, s.id == t.import_id))
        .where(
            t.account_id.in_(accounts),
            t.booking_date >= date(tax_year, 1, 1),
            t.booking_date <= date(tax_year, 12, 31),
            t.excluded_as_duplicate_of.is_(None),
            s.status == ImportStatus.RECONCILED.value,
            ~confirmed,
            or_(~any_current, proposed_here),
        )
    ).one()
    return OpenLines(count=row[0], sum=Decimal(row[1]).quantize(_CENT))
