"""Matching a transaction (docs/phases/phase-2.md §8.1, §5.3, D25, D26, D35, D37-D39).

The pure rules (rules.matching, rules.review) decide; this module reads their inputs from the
database and writes their outcome through services.allocations.

Order (§8.1, cheapest first): learned rules (`rules.matching.rule_answer`, which reads the
counterparty's confirmed lines from `rule_history`, D49), the deterministic matchers (owner
transfer, rent, loan), the document match, and only then the model (jobs.matching, which calls
back into `finish_line`). A rules' proposal (a near miss on the amount, D49) is used only when
no matcher answers. `rules.review.route` decides auto-confirm or review; the workspace's
`auto_accept_llm` (default false, D26) is passed through.

A line is matched only when it is *open*:
- its import is reconciled (§3.4: lines of an unreconciled import never get allocations);
- it is not excluded as a duplicate, nor the later line of an open possible duplicate (the
  pair is decided first; kept_both enqueues it again);
- it has no current allocation rows. A re-match on new evidence (`rematch=True`, D23) also
  takes a line whose rows are proposed, or confirmed by a learned rule; it replaces them only
  with a different set, and replaces confirmed rule rows only with a set that auto-confirms,
  never with an as_last_split copy (D51, review F1: the copy reads the ledger, it is no new
  evidence; a later line's corrected split would otherwise re-book the lines before it).
  A person's confirmation is never re-matched. A proposed set the deterministic order now
  auto-confirms with the same split is confirmed as a new set (the new rule or matcher's).
  A change to what the order reads (a learned rule, a loan, a tenant's IBAN, a tenancy's
  parties or rent, an owner account) re-matches the *waiting* lines it may book
  (`waiting_lines`, rematch_waiting): no confirmed rows, so never a line a person, matcher
  or rule booked.

Service-side guards on a machine set before it is routed (review F3, D39): a rent set whose
tenancy already has confirmed rent_cold for an overlapping period, and a loan set whose
instalment (loan and the entry's due month) already has a confirmed allocation, are sent to
review with a question instead of auto-confirming.

Documents as the matcher sees them (DocumentFacts), from each document's current extraction
(an AI kind; a manual-source version of that kind counts the same, D37):
- category: a financing invoice (rules.prefill.names_financing over subject and title, a
  Bescheid over its title, D35) -> loan_fees on its loan (the one linked loan, else the
  property's only loan, else none); subtype cost_basis_evidence -> acquisition_cost; a
  Grundsteuerbescheid -> grundsteuer; any other invoice or Bescheid -> uncategorized (the
  spec names no category for it, and uncategorized never auto-confirms);
- an invoice or Bescheid pays amount_total on its document_date (else due_on);
- its parts by kind of cost (D135, rules.prefill.sort_reading over the stored reading): a
  Grundschuld part takes the loan as a financing bill does;
- a Grundsteuerbescheid pays its installment on each stated due date, every year from its
  tax_year until the next Bescheid of the property (or next year); no due dates -> no facts;
- a one-payment document that already backs a confirmed allocation of another line is left
  out, so the next equal payment does not match the same Beleg;
- the document's IBAN is hashed here (the extraction holds it in plain text, the matchers
  compare hashes only).
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from functools import partial
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import Connection, Row, and_, exists, func, or_, select

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import document as document_table
from hausbuch.db.tables import document_extraction as extraction_table
from hausbuch.db.tables import document_link as link_table
from hausbuch.db.tables import loan as loan_table
from hausbuch.db.tables import matching_rule as rule_table
from hausbuch.db.tables import possible_duplicate as duplicate_table
from hausbuch.db.tables import property_ as property_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import tenancy as tenancy_table
from hausbuch.db.tables import tenancy_tenant as party_table
from hausbuch.db.tables import tenant as tenant_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.db.tables import unit as unit_table
from hausbuch.db.tables import workspace as workspace_table
from hausbuch.jobs import enqueue
from hausbuch.rules import matching as matchers
from hausbuch.rules.errors import ALLOCATION_CATEGORY_NOT_FOR_ACCOUNT, RuleError
from hausbuch.rules.iban import iban_hmac, normalize_iban
from hausbuch.rules.periods import service_period
from hausbuch.rules.prefill import names_financing, sort_reading
from hausbuch.rules.review import no_match_reason, route
from hausbuch.schemas.ai import ExtractedValue
from hausbuch.schemas.allocation import (
    Allocation,
    AllocationCategory,
    AllocationMethod,
    AllocationSet,
    DocumentFacts,
    DocumentPart,
    DuePayment,
    HistoryLine,
    HistoryRow,
    LoanFacts,
    MatchContext,
    MatchingRule,
    MatchTransaction,
    NameHistory,
    RentInForce,
    RoutingDecision,
    RoutingInput,
    SplitMethod,
    TenancyFacts,
    WirtschaftsplanFacts,
)
from hausbuch.schemas.api import WaitingAmount
from hausbuch.schemas.document import DocumentExtraction
from hausbuch.schemas.enums import (
    AccountPurpose,
    AllocationStatus,
    DocumentSubtype,
    DuplicateStatus,
    ExtractionKind,
    ImportStatus,
)
from hausbuch.schemas.loan import LoanTerms

from . import allocations, rules_learned, workspace_settings
from .context import CommandContext
from .documents import DOCUMENT_EVIDENCE_KINDS, EVIDENCE_KINDS, current_extraction
from .tenancies import _rows_in_force, rent_on  # rent rows no correction supersedes

C = AllocationCategory

DOCUMENT_KINDS = DOCUMENT_EVIDENCE_KINDS

GUARDED_CONFIDENCE = matchers.GUARDED_CONFIDENCE  # below every threshold: review
RENT_BOOKED_QUESTION = (
    "Für diesen Zeitraum ist schon eine Miete gebucht. Ist das eine Nachzahlung, eine "
    "Vorauszahlung oder die Miete eines anderen Monats?"
)
LOAN_BOOKED_QUESTION = (
    "Für diese Darlehensrate ist schon eine Zahlung gebucht. Ist das eine Sondertilgung "
    "oder eine verschobene Rate?"
)

LLM_GUARDED_QUESTION = (
    "Die KI hält diese Buchung für Miete, Darlehensrate, Hausgeld oder eine "
    "Eigentümer-Umbuchung, aber keine feste Regel passt. Stimmt die Aufteilung?"
)
LLM_SPLIT_QUESTION = "Die KI hat den Betrag auf mehrere Kategorien aufgeteilt. Stimmen die Beträge?"
# Categories a deterministic matcher owns (rent, annuity, Hausgeld, owner transfers): the
# model naming one means no matcher or rule recognised the line, so a person checks it.
_MATCHER_CATEGORIES = frozenset(
    {
        C.RENT_COLD,
        C.OPERATING_COSTS_PREPAYMENT,
        C.LOAN_INTEREST,
        C.LOAN_PRINCIPAL,
        C.HAUSGELD_UMLAGEFAEHIG,
        C.HAUSGELD_VERWALTUNG,
        C.HAUSGELD_RUECKLAGE,
        C.OWNER_TRANSFER_IN,
        C.OWNER_TRANSFER_OUT,
    }
)

_GRUNDSTEUER_DUE = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.?\s*(\d{4})?\s*$")
_CENT = Decimal("0.01")


# --- Reading the line --------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    """A transaction and what decides whether it may be matched."""

    tx: MatchTransaction
    import_status: ImportStatus
    excluded: bool
    awaiting_duplicate: bool  # the later line of an open possible duplicate
    current: list[Allocation]
    auto_accept_llm: bool
    counterparty_iban_hmac: bytes | None = field(repr=False, default=None)
    # Phase 6 (D158, D168): the line's account, its purpose (which order runs, which codes it
    # takes) and its kind; `private_ai`: the model may see this private line (the switch on
    # and both members' consent), always False on a rental line, which the gate never holds.
    account_id: UUID | None = None
    account_purpose: AccountPurpose = AccountPurpose.RENTAL
    account_kind: str = "bank_current"
    private_ai: bool = False

    @property
    def private(self) -> bool:
        return self.account_purpose is AccountPurpose.PRIVATE

    def open_for(self, *, rematch: bool) -> bool:
        if self.excluded or self.awaiting_duplicate:
            return False
        if self.import_status is not ImportStatus.RECONCILED:
            return False
        if not self.current:
            return True
        if not rematch:
            return False
        statuses = {a.status for a in self.current}
        if statuses == {AllocationStatus.PROPOSED}:
            return True
        return all(a.method is AllocationMethod.RULE for a in self.current)

    @property
    def waiting(self) -> bool:
        """The line waits for an answer: no current rows, or only proposed ones. A line
        with confirmed rows (by a person, a matcher or a rule) is not waiting
        (rematch_waiting takes waiting lines only)."""
        return all(a.status is AllocationStatus.PROPOSED for a in self.current)


def load_line(conn: Connection, transaction_id: UUID, *, lock: bool = False) -> Line | None:
    """The line as matching sees it. With `lock`, the transaction row is locked (FOR UPDATE)
    for the rest of the caller's transaction, so a job and a person answering the same line
    decide one after the other, the second on the first one's rows."""
    query = (
        select(
            transaction_table,
            import_table.c.status.label("import_status"),
            account_table.c.purpose.label("account_purpose"),
            account_table.c.kind.label("account_kind"),
        )
        .join(import_table, import_table.c.id == transaction_table.c.import_id)
        .join(account_table, account_table.c.id == transaction_table.c.account_id)
        .where(transaction_table.c.id == transaction_id)
    )
    if lock:
        query = query.with_for_update(of=transaction_table)
    row = conn.execute(query).one_or_none()
    if row is None:
        return None
    awaiting = conn.execute(
        select(func.count())
        .select_from(duplicate_table)
        .where(
            duplicate_table.c.other_transaction_id == transaction_id,
            duplicate_table.c.status == DuplicateStatus.OPEN.value,
        )
    ).scalar_one()
    purpose = AccountPurpose(row.account_purpose)
    private = purpose is AccountPurpose.PRIVATE
    return Line(
        tx=match_transaction(row),
        import_status=ImportStatus(row.import_status),
        excluded=row.excluded_as_duplicate_of is not None,
        awaiting_duplicate=awaiting > 0,
        current=allocations.current_allocations(conn, transaction_id),
        auto_accept_llm=auto_accept_llm(conn)
        and not (private and in_first_import(conn, row.account_id, row.import_id)),
        counterparty_iban_hmac=row.counterparty_iban_hmac,
        account_id=row.account_id,
        account_purpose=purpose,
        account_kind=row.account_kind,
        private_ai=private and workspace_settings.private_ai_allowed(conn),
    )


def match_transaction(row: Row) -> MatchTransaction:
    hmac = row.counterparty_iban_hmac
    return MatchTransaction(
        id=row.id,
        booking_date=row.booking_date,
        value_date=row.value_date,
        amount=row.amount,
        counterparty_name=row.counterparty_name,
        counterparty_iban_hmac=None if hmac is None else bytes(hmac).hex(),
        purpose=row.purpose or "",
        booking_text=row.booking_text,
    )


def auto_accept_llm(conn: Connection) -> bool:
    value = conn.execute(select(workspace_table.c.auto_accept_llm)).scalar_one_or_none()
    return bool(value)


def in_first_import(conn: Connection, account_id: UUID, import_id: UUID) -> bool:
    """D26 per private account (phase 6 D168, D172): the model's answers on an account's
    first import (the 2026 history) are never auto-confirmed, whatever the workspace's
    auto_accept_llm says, since that one switch was turned on after the rental account's
    first month. The first import is the account's earliest by creation."""
    first = conn.execute(
        select(import_table.c.id)
        .where(import_table.c.account_id == account_id)
        .order_by(import_table.c.created_at, import_table.c.id)
        .limit(1)
    ).scalar_one_or_none()
    return first == import_id


# --- The context -------------------------------------------------------------------------


def build_context(
    conn: Connection,
    *,
    iban_key: bytes,
    today: date,
    for_transaction: UUID | None = None,
    account_purpose: AccountPurpose = AccountPurpose.RENTAL,
) -> MatchContext:
    """Everything the matchers may look at, read from the workspace. `for_transaction`: the
    line being matched (a Beleg its own rows reference stays a candidate); `account_purpose`:
    its account's purpose, which picks the order (phase 6 D158)."""
    # The owners' accounts by purpose since migration 0025, which sets is_owner_account on
    # every account (phase 6 D156); step 2's transfer matcher widens this to every account.
    owners = conn.execute(
        select(account_table.c.iban_hmac).where(
            account_table.c.purpose == AccountPurpose.PRIVATE.value,
            account_table.c.iban_hmac.is_not(None),
        )
    ).scalars()
    plans, documents = _evidence(conn, iban_key=iban_key, today=today, exclude_for=for_transaction)
    return MatchContext(
        account_purpose=account_purpose,
        own_account_hmacs=sorted({bytes(h).hex() for h in owners}),
        tenancies=_tenancies(conn),
        loans=_loans(conn),
        documents=documents,
        wirtschaftsplaene=plans,
    )


def _tenancies(conn: Connection) -> list[TenancyFacts]:
    hmacs: dict[UUID, set[str]] = {}
    for tenancy_id, hmac in conn.execute(
        select(party_table.c.tenancy_id, tenant_table.c.iban_hmac)
        .join(tenant_table, tenant_table.c.id == party_table.c.tenant_id)
        .where(tenant_table.c.iban_hmac.is_not(None))
    ):
        hmacs.setdefault(tenancy_id, set()).add(bytes(hmac).hex())
    facts = []
    for row in conn.execute(
        select(tenancy_table, unit_table.c.property_id).join(
            unit_table, unit_table.c.id == tenancy_table.c.unit_id
        )
    ):
        rent = [
            RentInForce(
                effective_from=r.effective_from,
                cold_rent=r.cold_rent,
                operating_costs_prepayment=r.operating_costs_prepayment,
            )
            for r in _rows_in_force(conn, row.id)
        ]
        if not rent:
            continue
        facts.append(
            TenancyFacts(
                id=row.id,
                property_id=row.property_id,
                unit_id=row.unit_id,
                start_date=row.start_date,
                end_date=row.end_date,
                tenant_iban_hmacs=sorted(hmacs.get(row.id, set())),
                rent=rent,
            )
        )
    return facts


def _loans(conn: Connection) -> list[LoanFacts]:
    return [
        LoanFacts(
            id=row.id,
            property_id=row.property_id,
            lender=row.lender,
            account_iban_hmac=None
            if row.account_iban_hmac is None
            else bytes(row.account_iban_hmac).hex(),
            terms=LoanTerms(
                principal=row.principal,
                nominal_rate=row.nominal_rate,
                monthly_payment=row.monthly_payment,
                disbursed_on=row.disbursed_on,
                first_payment_on=row.first_payment_on,
                fixed_until=row.fixed_until,
                follow_up_rate=row.follow_up_rate,
                interest_only_before_first_payment=row.interest_only_before_first_payment,
            ),
        )
        for row in conn.execute(select(loan_table))
    ]


@dataclass(frozen=True)
class _Evidence:
    row: Row  # the document
    extraction: DocumentExtraction
    property_id: UUID | None
    loan_ids: list[UUID]


def _evidence(
    conn: Connection, *, iban_key: bytes, today: date, exclude_for: UUID | None
) -> tuple[list[WirtschaftsplanFacts], list[DocumentFacts]]:
    newest = (
        select(
            extraction_table.c.document_id,
            func.max(extraction_table.c.version).label("version"),
        )
        .group_by(extraction_table.c.document_id)
        .subquery()
    )
    ids = conn.execute(
        select(extraction_table.c.document_id)
        .join(
            newest,
            and_(
                newest.c.document_id == extraction_table.c.document_id,
                newest.c.version == extraction_table.c.version,
            ),
        )
        .where(extraction_table.c.kind.in_([k.value for k in EVIDENCE_KINDS]))
    ).scalars()
    found = [e for doc_id in ids if (e := evidence_of(conn, doc_id)) is not None]
    used = _backing_confirmed(conn, exclude_for)
    loans_by_property: dict[UUID, list[UUID]] = {}
    for loan_id, property_id in conn.execute(select(loan_table.c.id, loan_table.c.property_id)):
        loans_by_property.setdefault(property_id, []).append(loan_id)

    plans: list[WirtschaftsplanFacts] = []
    documents: list[DocumentFacts] = []
    grundsteuer: list[_Evidence] = []
    for e in found:
        kind = e.extraction.kind
        if kind is ExtractionKind.WIRTSCHAFTSPLAN:
            if (plan := wirtschaftsplan_facts(e)) is not None:
                plans.append(plan)
        elif kind is ExtractionKind.GRUNDSTEUERBESCHEID:
            grundsteuer.append(e)
        elif e.row.id not in used:
            facts = document_facts(e, iban_key=iban_key, loans_by_property=loans_by_property)
            if facts is not None:
                documents.append(facts)
    documents.extend(grundsteuer_facts(grundsteuer, iban_key=iban_key, today=today))
    return plans, documents


def evidence_of(conn: Connection, document_id: UUID) -> _Evidence | None:
    extraction = current_extraction(conn, document_id)
    if extraction is None or extraction.kind not in EVIDENCE_KINDS:
        return None
    row = conn.execute(select(document_table).where(document_table.c.id == document_id)).one()
    links = conn.execute(select(link_table).where(link_table.c.document_id == document_id)).all()
    properties = {link.property_id for link in links if link.property_id is not None}
    loan_ids = sorted({link.loan_id for link in links if link.loan_id is not None})
    if not properties:
        unit_ids = [link.unit_id for link in links if link.unit_id is not None]
        if unit_ids:
            properties |= set(
                conn.execute(
                    select(unit_table.c.property_id).where(unit_table.c.id.in_(unit_ids))
                ).scalars()
            )
        if loan_ids:
            properties |= set(
                conn.execute(
                    select(loan_table.c.property_id).where(loan_table.c.id.in_(loan_ids))
                ).scalars()
            )
    property_id = next(iter(properties)) if len(properties) == 1 else None
    return _Evidence(row, extraction, property_id, loan_ids)


def _backing_confirmed(conn: Connection, exclude_for: UUID | None) -> set[UUID]:
    """Documents that already back a confirmed allocation of another line."""
    query = select(allocation_table.c.document_id).where(
        allocation_table.c.status == AllocationStatus.CONFIRMED.value,
        allocation_table.c.document_id.is_not(None),
    )
    if exclude_for is not None:
        query = query.where(allocation_table.c.transaction_id != exclude_for)
    return set(conn.execute(query).scalars())


def _value(extraction: DocumentExtraction, name: str) -> Any:
    item = getattr(extraction.fields, name, None)
    return getattr(item, "value", None)


def _money(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        amount = Decimal(str(value)).quantize(_CENT)
    except InvalidOperation:
        return None
    return amount if amount > 0 else None


def _date(value: Any) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _hmac(value: Any, iban_key: bytes) -> str | None:
    if not value:
        return None
    try:
        return iban_hmac(normalize_iban(str(value)), iban_key).hex()
    except RuleError:
        return None


def is_financing(extraction: DocumentExtraction) -> bool:
    """D35, decided as rules.prefill decides it: an invoice by its subject or title, a
    Bescheid by its title."""
    title = _value(extraction, "title")
    if extraction.kind is ExtractionKind.INVOICE:
        return names_financing(_value(extraction, "subject"), title)
    if extraction.kind is ExtractionKind.BESCHEID:
        return names_financing(title)
    return False


def document_facts(
    e: _Evidence, *, iban_key: bytes, loans_by_property: dict[UUID, list[UUID]]
) -> DocumentFacts | None:
    """An invoice or (non-Grundsteuer) Bescheid as the document matcher reads it."""
    x = e.extraction
    amount = _money(_value(x, "amount_total"))
    paid_on = _date(_value(x, "document_date")) or _date(_value(x, "due_on"))
    if amount is None or paid_on is None:
        return None
    loan_id = None
    if is_financing(x):
        category = C.LOAN_FEES
        if len(e.loan_ids) == 1:
            loan_id = e.loan_ids[0]
        elif not e.loan_ids and e.property_id is not None:
            candidates = loans_by_property.get(e.property_id, [])
            loan_id = candidates[0] if len(candidates) == 1 else None
    elif e.row.subtype == DocumentSubtype.COST_BASIS_EVIDENCE.value:
        category = C.ACQUISITION_COST
    else:
        category = C.UNCATEGORIZED
    bill = sort_reading(x.kind, e.row.subtype, partial(_extracted, x))
    if bill.parts and loan_id is None and C.LOAN_FEES in bill.parts:
        # A Grundschuld part of a mixed bill (D135): on the linked loan, else the property's
        # only loan, as a financing bill's.
        if len(e.loan_ids) == 1:
            loan_id = e.loan_ids[0]
        elif not e.loan_ids and e.property_id is not None:
            candidates = loans_by_property.get(e.property_id, [])
            loan_id = candidates[0] if len(candidates) == 1 else None
    property_id = e.property_id
    if loan_id is not None and property_id is None:
        # The loan's property; None when the caller's map does not hold it (a bill linked to
        # two properties and one loan, read with an empty map; review F5).
        property_id = next((p for p, ids in loans_by_property.items() if loan_id in ids), None)
    return DocumentFacts(
        id=e.row.id,
        kind=x.kind,
        category=category,
        property_id=property_id,
        loan_id=loan_id,
        payments=[DuePayment(due_on=paid_on, amount=amount)],
        iban_hmac=_hmac(_value(x, "iban"), iban_key),
        reference=_value(x, "reference") or None,
        parts=[DocumentPart(category=c, amount=a) for c, a in bill.parts.items()],
    )


def _extracted(extraction: DocumentExtraction, name: str) -> ExtractedValue:
    """A field of the stored reading as rules.prefill reads it; missing when absent."""
    value = getattr(extraction.fields, name, None)
    return value if isinstance(value, ExtractedValue) else ExtractedValue.missing()


def paid_from_rental_account(
    conn: Connection, document_id: UUID, *, iban_key: bytes | None = None
) -> bool:
    """Phase 2 D135: a bank line of the read accounts pays this bill, so none of it is a cost
    paid from another account. True when an allocation (proposed or confirmed) already
    carries the document as its Beleg, or when a stored transaction is a document hit of it
    (rules.matching.document_hit: the amount within 0.01, the booking within 10 days of the
    document date, and the bill's IBAN or its reference in the purpose; the IBAN is compared
    only when the key is given). A document the matcher cannot read (no amount or date)
    finds no line."""
    carried = conn.execute(
        select(
            exists().where(
                allocation_table.c.document_id == document_id,
                allocation_table.c.status != AllocationStatus.SUPERSEDED.value,
            )
        )
    ).scalar_one()
    if carried:
        return True
    e = evidence_of(conn, document_id)
    if e is None or e.extraction.kind is ExtractionKind.GRUNDSTEUERBESCHEID:
        return False
    facts = document_facts(e, iban_key=iban_key or b"", loans_by_property={})
    if facts is None:
        return False
    if iban_key is None:
        facts = facts.model_copy(update={"iban_hmac": None})
    t = transaction_table.c
    window = timedelta(days=matchers.DOCUMENT_WINDOW_DAYS)
    for payment in facts.payments:
        low = payment.amount - matchers.DOCUMENT_AMOUNT_TOLERANCE
        high = payment.amount + matchers.DOCUMENT_AMOUNT_TOLERANCE
        rows = conn.execute(
            select(transaction_table).where(
                func.abs(t.amount).between(low, high),
                t.booking_date.between(payment.due_on - window, payment.due_on + window),
            )
        )
        if any(matchers.document_hit(match_transaction(row), facts) for row in rows):
            return True
    return False


def grundsteuer_facts(
    found: list[_Evidence], *, iban_key: bytes, today: date
) -> list[DocumentFacts]:
    """Each Grundsteuerbescheid's installments, one property's Bescheide in tax-year order:
    a Bescheid's years end where the next one's begin."""
    dated = []
    for e in found:
        year = _value(e.extraction, "tax_year")
        if year is None and (d := _date(_value(e.extraction, "document_date"))) is not None:
            year = d.year
        if isinstance(year, int):
            dated.append((e, year))
    out = []
    for e, year in dated:
        later = [y for other, y in dated if other.property_id == e.property_id and y > year]
        last_year = min(later) - 1 if later else max(year, today.year + 1)
        payments = _installments(e.extraction, range(year, last_year + 1))
        if not payments:
            continue
        x = e.extraction
        out.append(
            DocumentFacts(
                id=e.row.id,
                kind=x.kind,
                category=C.GRUNDSTEUER,
                property_id=e.property_id,
                payments=payments,
                iban_hmac=_hmac(_value(x, "iban"), iban_key),
                reference=_value(x, "reference") or None,
            )
        )
    return out


def _installments(x: DocumentExtraction, years: range) -> list[DuePayment]:
    raw = _value(x, "due_dates")
    if not isinstance(raw, list) or not raw:
        return []
    days: list[tuple[int, int, int | None]] = []
    for text in raw:
        m = _GRUNDSTEUER_DUE.match(str(text))
        if m is None:
            return []
        days.append((int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None))
    amount = _money(_value(x, "installment_amount"))
    if amount is None:
        yearly = _money(_value(x, "yearly_amount"))
        per_year = len({(d, mo) for d, mo, _ in days})
        if yearly is None or (yearly / per_year).quantize(_CENT) * per_year != yearly:
            return []
        amount = (yearly / per_year).quantize(_CENT)
    payments = set()
    for day, month, year in days:
        for y in [year] if year is not None else years:
            try:
                payments.add(date(y, month, day))
            except ValueError:
                return []
    return [DuePayment(due_on=d, amount=amount) for d in sorted(payments)]


def wirtschaftsplan_facts(e: _Evidence) -> WirtschaftsplanFacts | None:
    x = e.extraction
    if e.property_id is None:
        return None
    valid_from = _date(_value(x, "valid_from"))
    year = _value(x, "tax_year")
    if valid_from is None and isinstance(year, int):
        valid_from = date(year, 1, 1)
    total = _money(_value(x, "hausgeld_total_monthly"))
    parts = [
        _part(_value(x, f"hausgeld_{name}_monthly"))
        for name in ("umlagefaehig", "verwaltung", "ruecklage")
    ]
    if valid_from is None or total is None or any(p is None for p in parts):
        return None
    if sum(parts, Decimal("0.00")) != total:  # type: ignore[arg-type]
        return None
    umlagefaehig, verwaltung, ruecklage = parts
    return WirtschaftsplanFacts(
        document_id=e.row.id,
        property_id=e.property_id,
        valid_from=valid_from,
        total=total,
        umlagefaehig=umlagefaehig,  # type: ignore[arg-type]
        verwaltung=verwaltung,  # type: ignore[arg-type]
        ruecklage=ruecklage,  # type: ignore[arg-type]
    )


def _part(value: Any) -> Decimal | None:
    if value is None:
        return Decimal("0.00")  # a plan without Rücklage states none
    try:
        amount = Decimal(str(value)).quantize(_CENT)
    except InvalidOperation:
        return None
    return amount if amount >= 0 else None


# --- The deterministic order ---------------------------------------------------------------


def deterministic_set(
    conn: Connection, tx: MatchTransaction, context: MatchContext
) -> AllocationSet | None:
    """The deterministic order of the line's account purpose (rules.matching.
    deterministic_answer, phase 6 D158), with the service-side guards (F3, D39) applied.
    Rental: learned rules, then the matchers, then the document match (§8.1); the rules'
    answer is either a standing hit (confidence 1, used at once) or a near-miss proposal
    (D49), which the matchers come before: an exact rent, loan, owner or document match wins
    over it. Private: learned rules, the document match, then the tenant / lender question."""
    rules = rules_learned.enabled_rules(conn)
    history = rule_history(conn, tx, context.account_purpose) if _any_hit(rules, tx) else []
    found = matchers.deterministic_answer(rules, tx, context, history)
    return None if found is None else _guarded(conn, found)


def _any_hit(rules: list[MatchingRule], tx: MatchTransaction) -> bool:
    return tx.counterparty_iban_hmac is not None and any(
        r.enabled and matchers.rule_hits(r.matcher, tx) for r in rules
    )


def rule_history(
    conn: Connection, tx: MatchTransaction, purpose: AccountPurpose = AccountPurpose.RENTAL
) -> list[HistoryLine]:
    """The other lines from the line's IBAN hash with the same sign on accounts of the line's
    purpose (phase 6 D158: a private line of the same payee is not a kind of the rental
    rule's, nor the reverse), not excluded as a
    duplicate, with their current (not superseded) allocation rows, for rules.matching.
    rule_answer (D49), which keeps those whose rows are all confirmed. Each row carries its
    amount, scope and recurring flag, which an as_last_split rule copies (D51). Empty for a line
    without an IBAN hash or of amount 0."""
    if tx.counterparty_iban_hmac is None or tx.amount == 0:
        return []
    t = transaction_table.c
    a = allocation_table.c
    rows = conn.execute(
        select(
            t.id,
            t.booking_date,
            t.amount,
            a.category,
            a.status,
            a.amount.label("part_amount"),
            a.property_id,
            a.unit_id,
            a.tenancy_id,
            a.loan_id,
            a.recurring,
        )
        .join(allocation_table, a.transaction_id == t.id)
        .join(account_table, account_table.c.id == t.account_id)
        .where(
            account_table.c.purpose == purpose.value,
            t.counterparty_iban_hmac == bytes.fromhex(tx.counterparty_iban_hmac),
            t.amount > 0 if tx.amount > 0 else t.amount < 0,
            t.id != tx.id,
            t.excluded_as_duplicate_of.is_(None),
            a.status != AllocationStatus.SUPERSEDED.value,
        )
        .order_by(t.booking_date, t.id, a.recorded_at, a.id)
    ).all()
    lines: dict[UUID, tuple[Row, list[HistoryRow]]] = {}
    for row in rows:
        _, parts = lines.setdefault(row.id, (row, []))
        parts.append(
            HistoryRow(
                category=row.category,
                status=row.status,
                amount=row.part_amount,
                property_id=row.property_id,
                unit_id=row.unit_id,
                tenancy_id=row.tenancy_id,
                loan_id=row.loan_id,
                recurring=row.recurring,
            )
        )
    return [
        HistoryLine(
            transaction_id=row.id,
            booking_date=row.booking_date,
            amount=row.amount,
            counterparty_iban_hmac=tx.counterparty_iban_hmac,
            rows=parts,
        )
        for row, parts in lines.values()
    ]


def name_history(conn: Connection, tx: MatchTransaction) -> list[NameHistory]:
    """D160: the other lines of every private account with the line's sign and a
    counterparty name whose current rows are confirmed under one category, for the checks a
    name rule learned from this line must pass (rules.matching._name_rule: rule.too_broad,
    rule.conflicts). Every private account, since a rule hits the lines of all of them."""
    if not tx.counterparty_name or tx.amount == 0:
        return []
    t = transaction_table.c
    a = allocation_table.c
    rows = conn.execute(
        select(t.id, t.counterparty_name, a.category, a.status)
        .join(allocation_table, a.transaction_id == t.id)
        .join(account_table, account_table.c.id == t.account_id)
        .where(
            account_table.c.purpose == AccountPurpose.PRIVATE.value,
            t.counterparty_name.is_not(None),
            t.amount > 0 if tx.amount > 0 else t.amount < 0,
            t.id != tx.id,
            t.excluded_as_duplicate_of.is_(None),
            a.status != AllocationStatus.SUPERSEDED.value,
        )
        .order_by(t.booking_date, t.id)
    ).all()
    lines: dict[UUID, list[Row]] = {}
    for row in rows:
        lines.setdefault(row.id, []).append(row)
    return [
        NameHistory(
            transaction_id=line_id,
            counterparty_name=parts[0].counterparty_name,
            category=parts[0].category,
        )
        for line_id, parts in lines.items()
        if len(parts) == 1 and parts[0].status == AllocationStatus.CONFIRMED.value
    ]


def _guarded(conn: Connection, found: AllocationSet) -> AllocationSet:
    """Review F3 / D39: a rent set for a tenancy and period, or an instalment of a loan,
    already confirmed on another line goes to review.

    Two lines are matched by two jobs at once (worker concurrency), each holding only its
    own transaction row, so both `_booked` reads could see nothing and both auto-confirm.
    Each check therefore first takes a transaction-scoped advisory lock keyed on the scope
    (the tenancy or loan id): the second job waits until the first commits, and its
    `_booked` read (READ COMMITTED: a new snapshot per statement) then sees the first one's
    rows. INVARIANT: this only works because `_guarded` -> route -> write ->
    allocations.record_set all run in the one transaction match_line opened, so the lock
    is held until the set is committed. Do not split that transaction."""
    if found.method is AllocationMethod.USER:
        return found
    tx_id = found.transaction_id
    for d in found.allocations:
        if d.service_period_start is None:
            continue
        if d.category is C.RENT_COLD and d.tenancy_id is not None:
            _scope_lock(conn, d.tenancy_id)
            if _booked(conn, tx_id, d, [C.RENT_COLD], tenancy_id=d.tenancy_id):
                return _to_review(found, RENT_BOOKED_QUESTION)
        if d.category in _LOAN and d.loan_id is not None:
            _scope_lock(conn, d.loan_id)
            if _booked(conn, tx_id, d, list(_LOAN), loan_id=d.loan_id):
                return _to_review(found, LOAN_BOOKED_QUESTION)
    return found


def _scope_lock(conn: Connection, scope_id: UUID) -> None:
    """pg_advisory_xact_lock on the tenancy or loan id, released at commit or rollback."""
    conn.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(str(scope_id), 0))))


_LOAN = (C.LOAN_INTEREST, C.LOAN_PRINCIPAL)


def _booked(
    conn: Connection,
    transaction_id: UUID,
    draft: Any,
    categories: list[AllocationCategory],
    **scope: UUID,
) -> bool:
    """A confirmed allocation of another line in these categories and scope whose service
    period overlaps the draft's."""
    a = allocation_table.c
    query = select(func.count()).where(
        a.status == AllocationStatus.CONFIRMED.value,
        a.transaction_id != transaction_id,
        a.category.in_([c.value for c in categories]),
        a.service_period_start <= draft.service_period_end,
        a.service_period_end >= draft.service_period_start,
        *(getattr(a, name) == value for name, value in scope.items()),
    )
    return conn.execute(query).scalar_one() > 0


def _llm_guarded(line: Line, found: AllocationSet) -> AllocationSet:
    """Review finding 3, principle 1 (AI explains, code calculates): a model's set never
    auto-confirms when it names a matcher-owned category (rent, annuity, Hausgeld, owner
    transfer: the matchers did not recognise the line, so the model's reading of it is a
    guess), or when it splits the amount into more than one part (a model-chosen euro split).
    Such a set goes to review with GUARDED_CONFIDENCE and a question.

    Inert while the workspace's auto_accept_llm is off (the default, D26): rules.review.route
    then sends every LLM set to review anyway, so the set keeps the model's own confidence
    and question. The one exception is a line of 0.00 (D52): route auto-confirms a set of
    one part, with no tenancy or loan, outside rules.review.ZERO_EXCLUDED, which holds every
    _MATCHER_CATEGORIES category; so route itself enforces this guard's exclusions there,
    with the setting on or off (tests/rules/test_review.py checks the subset)."""
    if found.method is not AllocationMethod.LLM or not line.auto_accept_llm:
        return found
    if any(a.category in _MATCHER_CATEGORIES for a in found.allocations):
        return _to_review(found, LLM_GUARDED_QUESTION)
    if len(found.allocations) > 1:
        return _to_review(found, LLM_SPLIT_QUESTION)
    return found


def _to_review(found: AllocationSet, question: str) -> AllocationSet:
    return found.model_copy(update={"confidence": GUARDED_CONFIDENCE, "question": question})


# --- One line ----------------------------------------------------------------------------


Outcome = Literal["skipped", "written", "categorize", "explain"]


@dataclass(frozen=True)
class MatchPlan:
    """What match_line did, or what it needs the model for (jobs.matching).

    written: rows were written (auto-confirmed or proposed). categorize: nothing matched,
    the model may try. explain: a matcher's set lands in review and the model may word its
    reasoning and question (the set is not written yet). skipped: the line is not open, or
    nothing changes."""

    outcome: Outcome
    reason: str
    allocation_set: AllocationSet | None = None
    written: list[Allocation] = field(default_factory=list)


_MATCHERS = frozenset(
    {
        AllocationMethod.MATCHER_RENT,
        AllocationMethod.MATCHER_LOAN,
        AllocationMethod.MATCHER_OWNER,
        AllocationMethod.MATCHER_HAUSGELD,
    }
)


def match_line(
    ctx: CommandContext,
    transaction_id: UUID,
    *,
    iban_key: bytes,
    ai_available: bool,
    rematch: bool = False,
) -> MatchPlan:
    """Run the deterministic order on one line and write what it can. With `ai_available`,
    a line nothing matched comes back as `categorize`, and a matcher's set bound for review
    as `explain`; without, the matcher's set is written with its template text and an
    unmatched line stays without rows (inbox "Nicht zugeordnet"). A re-match never calls
    the model."""
    line = load_line(ctx.conn, transaction_id, lock=True)
    if line is None:
        return MatchPlan("skipped", "matching.not_found")
    if not line.open_for(rematch=rematch):
        return MatchPlan("skipped", _closed_reason(line))
    context = build_context(
        ctx.conn,
        iban_key=iban_key,
        today=ctx.now.date(),
        for_transaction=transaction_id,
        account_purpose=line.account_purpose,
    )
    found = deterministic_set(ctx.conn, line.tx, context)
    if rematch and not line.waiting and found is not None and _copies_a_split(ctx.conn, found):
        # D51, review F1: an as_last_split copy reads the ledger, it is no new evidence, so a
        # re-match never replaces confirmed rows with it (a waiting line is still booked).
        return MatchPlan("skipped", "matching.keeps_confirmed")
    if found is None:
        if rematch or not ai_available:
            return MatchPlan("skipped", "routing.no_match")
        if line.private and not line.private_ai:
            # D168: a private line reaches the model only with the switch on and both
            # members' consent; until then it waits in Zu prüfen for a person.
            return MatchPlan("skipped", no_match_reason(line.account_purpose, False))
        return MatchPlan("categorize", "routing.no_match")
    decision = _route(line, found)
    if decision is not RoutingDecision.AUTO_CONFIRM:
        if any(a.status is AllocationStatus.CONFIRMED for a in line.current):
            return MatchPlan("skipped", "matching.keeps_confirmed")
        # The model words a rental matcher's question only; D158's tenant / lender question
        # on a private line keeps its template text (the model never sees such a line).
        if found.method in _MATCHERS and ai_available and not rematch and not line.private:
            return MatchPlan("explain", "routing.below_threshold", allocation_set=found)
    return write(ctx, line, found)


def _copies_a_split(conn: Connection, found: AllocationSet) -> bool:
    """Whether the set is an as_last_split rule's copy (D51)."""
    if found.rule_id is None:
        return False
    method = conn.execute(
        select(rule_table.c.split_method).where(rule_table.c.id == found.rule_id)
    ).scalar_one_or_none()
    return method == SplitMethod.AS_LAST_SPLIT.value


def write(ctx: CommandContext, line: Line, found: AllocationSet) -> MatchPlan:
    """Route the set and write it, unless the line already holds the same answer."""
    decision = _route(line, found)
    auto = decision is RoutingDecision.AUTO_CONFIRM
    # The same split as the current rows changes nothing, except a proposed set the order
    # now auto-confirms (a new rule or evidence books it): that is written as a confirmed set
    # superseding the proposal, so it leaves the inbox with the rule's or matcher's
    # provenance.
    same = bool(line.current) and allocations.same_answer(found.allocations, line.current)
    if same and not (auto and line.waiting):
        return MatchPlan("skipped", "matching.unchanged")
    written = allocations.record_set(ctx, found, confirm=auto, auto=auto)
    if found.rule_id is not None and auto:  # a rule's proposal (D49) is not a booking
        rules_learned.record_hit(ctx, found.rule_id)
    return MatchPlan(
        "written", "routing.auto_confirm" if auto else "routing.review", found, written
    )


def finish_line(ctx: CommandContext, transaction_id: UUID, found: AllocationSet) -> MatchPlan:
    """After a model call: write the set if the line is still open and still has no rows
    (a person may have answered meanwhile)."""
    line = load_line(ctx.conn, transaction_id, lock=True)
    if line is None or not line.open_for(rematch=False):
        return MatchPlan("skipped", "matching.answered_meanwhile")
    if not allocations.categories_allowed(line.account_purpose, found.allocations):
        # D159: never booked; the line stays unmatched in Zu prüfen (the request's schema
        # makes this unreachable, so a model answer here is a bug, not a retry).
        return MatchPlan("skipped", ALLOCATION_CATEGORY_NOT_FOR_ACCOUNT)
    return write(ctx, line, _llm_guarded(line, found))


def _route(line: Line, found: AllocationSet) -> RoutingDecision:
    return route(
        RoutingInput(
            import_status=line.import_status,
            auto_accept_llm=line.auto_accept_llm,
            allocation_set=found,
        )
    ).decision


def _closed_reason(line: Line) -> str:
    if line.excluded:
        return "matching.excluded"
    if line.awaiting_duplicate:
        return "matching.possible_duplicate"
    if line.import_status is not ImportStatus.RECONCILED:
        return "routing.import_unreconciled"
    return "matching.already_allocated"


# --- Which lines a new document affects ---------------------------------------------------


def lines_for_evidence(
    conn: Connection, document_id: UUID, *, iban_key: bytes, today: date
) -> list[UUID]:
    """Lines a new reading of this document may change (rematch_on_evidence): for an invoice
    or Bescheid, lines within its payments' amount and date window; for a Wirtschaftsplan,
    Hausgeld-sized debits from its first month on, and lines a by_wirtschaftsplan rule of its
    property split. Whether each is open is decided by match_line(rematch=True)."""
    e = evidence_of(conn, document_id)
    if e is None:
        return []
    t = transaction_table.c
    if e.extraction.kind is ExtractionKind.WIRTSCHAFTSPLAN:
        plan = wirtschaftsplan_facts(e)
        if plan is None:
            return []
        ruled = (
            select(allocation_table.c.transaction_id)
            .join(rule_table, rule_table.c.id == allocation_table.c.rule_id)
            .where(
                allocation_table.c.status == AllocationStatus.CONFIRMED.value,
                rule_table.c.property_id == plan.property_id,
            )
        )
        query = select(t.id).where(
            t.booking_date >= plan.valid_from,
            or_(t.amount == -plan.total, t.id.in_(ruled)),
        )
        return list(conn.execute(query.order_by(t.booking_date, t.id)).scalars())
    loans: dict[UUID, list[UUID]] = {}
    if e.extraction.kind is ExtractionKind.GRUNDSTEUERBESCHEID:
        facts = grundsteuer_facts([e], iban_key=iban_key, today=today)
        payments = facts[0].payments if facts else []
    else:
        found = document_facts(e, iban_key=iban_key, loans_by_property=loans)
        payments = found.payments if found else []
    window = matchers.DOCUMENT_WINDOW_DAYS
    tolerance = matchers.DOCUMENT_AMOUNT_TOLERANCE
    ids: list[UUID] = []
    for p in payments:
        start = date.fromordinal(p.due_on.toordinal() - window)
        end = date.fromordinal(p.due_on.toordinal() + window)
        query = select(t.id).where(
            t.booking_date.between(start, end),
            func.abs(t.amount).between(p.amount - tolerance, p.amount + tolerance),
        )
        ids.extend(conn.execute(query.order_by(t.booking_date, t.id)).scalars())
    return list(dict.fromkeys(ids))


# --- Which waiting lines a change may book -------------------------------------------------


def waiting_lines(conn: Connection, kind: str, entity_id: UUID) -> list[UUID]:
    """Lines waiting in the inbox that a change to this entity may now let the deterministic
    order book (rematch_waiting), oldest first. Waiting: a line of a reconciled import, not
    excluded as a duplicate, with no confirmed allocation row (none, or only a proposed set);
    a line a person, matcher or rule booked is never picked. Narrowed by what the change can
    reach:
    - rule: the rule's IBAN hash and sign, then its patterns (rules.matching.rule_hits);
      none for a rule that is gone or disabled;
    - loan: every waiting debit (the loan matcher reads the account hash or the lender's
      name; a debit is cheap to re-match);
    - tenant: credits from the tenant's IBAN hash (rent); none without one;
    - tenancy: credits from any of its tenants' IBAN hashes (new parties or rent rows);
    - account: lines from an owner account's IBAN hash; none for an account that is not one.
    Whether a rule or matcher then fits, and the open-duplicate check, are match_line's."""
    t = transaction_table.c
    a = allocation_table.c
    confirmed = exists().where(
        a.transaction_id == t.id, a.status == AllocationStatus.CONFIRMED.value
    )
    query = (
        select(transaction_table)
        .join(import_table, import_table.c.id == t.import_id)
        .where(
            import_table.c.status == ImportStatus.RECONCILED.value,
            t.excluded_as_duplicate_of.is_(None),
            ~confirmed,
        )
    )
    rule = None
    if kind == enqueue.TRIGGER_RULE:
        row = conn.execute(select(rule_table).where(rule_table.c.id == entity_id)).one_or_none()
        if row is None:
            return []
        rule = rules_learned.to_rule(row)
        if not rule.enabled or not rule.matcher.matches_something:
            return []
        if rule.matcher.counterparty_iban_hmac is not None:
            hmac = bytes.fromhex(rule.matcher.counterparty_iban_hmac)
            query = query.where(t.counterparty_iban_hmac == hmac)
        if rule.matcher.amount_sign == "credit":
            query = query.where(t.amount > 0)
        elif rule.matcher.amount_sign == "debit":
            query = query.where(t.amount < 0)
    elif kind == enqueue.TRIGGER_LOAN:
        found = conn.execute(select(loan_table.c.id).where(loan_table.c.id == entity_id))
        if found.one_or_none() is None:
            return []
        query = query.where(t.amount < 0)
    elif kind in (enqueue.TRIGGER_TENANT, enqueue.TRIGGER_TENANCY):
        hmacs = select(tenant_table.c.iban_hmac).where(tenant_table.c.iban_hmac.is_not(None))
        if kind == enqueue.TRIGGER_TENANT:
            hmacs = hmacs.where(tenant_table.c.id == entity_id)
        else:
            hmacs = hmacs.join(party_table, party_table.c.tenant_id == tenant_table.c.id).where(
                party_table.c.tenancy_id == entity_id
            )
        found_hmacs = list(conn.execute(hmacs).scalars())
        if not found_hmacs:
            return []
        query = query.where(t.counterparty_iban_hmac.in_(found_hmacs), t.amount > 0)
    elif kind == enqueue.TRIGGER_ACCOUNT:
        owner = conn.execute(
            select(account_table.c.iban_hmac).where(
                account_table.c.id == entity_id,
                account_table.c.purpose == AccountPurpose.PRIVATE.value,
                account_table.c.iban_hmac.is_not(None),
            )
        ).scalar_one_or_none()
        if owner is None:
            return []
        query = query.where(t.counterparty_iban_hmac == owner)
    else:
        return []
    rows = conn.execute(query.order_by(t.booking_date, t.id)).all()
    if rule is None:
        return [r.id for r in rows]
    return [r.id for r in rows if matchers.rule_hits(rule.matcher, match_transaction(r))]


WAITING_AMOUNTS_LIMIT = 5


def waiting_other_amounts(conn: Connection, tx: MatchTransaction) -> list[WaitingAmount]:
    """D49: the amounts other than this line's that lines still waiting from its IBAN hash
    and sign carry (waiting as in waiting_lines: a reconciled import, not excluded, no
    confirmed row), each with its count, most frequent first (then the larger amount), at
    most five. Empty for a line without an IBAN hash."""
    if tx.counterparty_iban_hmac is None or tx.amount == 0:
        return []
    t = transaction_table.c
    a = allocation_table.c
    confirmed = exists().where(
        a.transaction_id == t.id, a.status == AllocationStatus.CONFIRMED.value
    )
    count = func.count().label("count")
    rows = conn.execute(
        select(t.amount, count)
        .join(import_table, import_table.c.id == t.import_id)
        .where(
            import_table.c.status == ImportStatus.RECONCILED.value,
            t.excluded_as_duplicate_of.is_(None),
            ~confirmed,
            t.counterparty_iban_hmac == bytes.fromhex(tx.counterparty_iban_hmac),
            t.amount > 0 if tx.amount > 0 else t.amount < 0,
            t.id != tx.id,
            t.amount != tx.amount,
        )
        .group_by(t.amount)
        .order_by(count.desc(), func.abs(t.amount).desc())
        .limit(WAITING_AMOUNTS_LIMIT)
    ).all()
    return [WaitingAmount(amount=r.amount, count=r.count) for r in rows]


# --- What the model sees (data minimization, §5.3) -----------------------------------------


_IBAN_LIKE = re.compile(r"[A-Za-z]{2}[0-9]{2}(?: ?[A-Za-z0-9]){11,32}")


def scrub(text: str | None) -> str:
    """The text with every IBAN-like run replaced, so no IBAN reaches a prompt."""
    return _IBAN_LIKE.sub("[IBAN]", text or "")


@dataclass(frozen=True)
class SimilarAnswer:
    booking_date: date
    amount: Decimal
    categories: list[str]


@dataclass(frozen=True)
class CategorizeInput:
    """The categorize request's data, minimized: no tenant name, no IBAN beyond the last four
    characters of the line's own counterparty, other lines only as date, amount and
    categories."""

    transaction_id: UUID
    booking_date: date
    value_date: date | None
    amount: Decimal
    counterparty_name: str
    purpose: str
    booking_text: str
    iban_last4: str | None
    properties: list[str]
    tenancies: list[str]
    loans: list[str]
    similar: list[SimilarAnswer]


def categorize_input(conn: Connection, line: Line) -> CategorizeInput:
    tx = line.tx
    masked = conn.execute(
        select(transaction_table.c.counterparty_iban_masked).where(transaction_table.c.id == tx.id)
    ).scalar_one_or_none()
    labels = {
        row.id: row.label
        for row in conn.execute(select(property_table.c.id, property_table.c.label))
    }
    tenancies = []
    for row in conn.execute(
        select(tenancy_table.c.id, tenancy_table.c.end_date, unit_table.c.label)
        .join(unit_table, unit_table.c.id == tenancy_table.c.unit_id)
        .order_by(unit_table.c.label, tenancy_table.c.start_date)
    ):
        if row.end_date is not None and row.end_date < tx.booking_date:
            continue
        rent = rent_on(conn, row.id, tx.booking_date)
        if rent is not None:
            tenancies.append(
                f"Mietverhältnis {row.label}: Kaltmiete {rent.cold_rent} €, "
                f"Nebenkostenvorauszahlung {rent.operating_costs_prepayment} €"
            )
    loans = [
        f"Darlehen {scrub(row.lender)}: Rate {row.monthly_payment} €"
        for row in conn.execute(select(loan_table.c.lender, loan_table.c.monthly_payment))
    ]
    names, own_unit = _tenant_names(conn, line)
    counterparty = _without(scrub(tx.counterparty_name), names)
    if own_unit is not None:
        counterparty = f"Mieter (Mietverhältnis {own_unit})"
    return CategorizeInput(
        transaction_id=tx.id,
        booking_date=tx.booking_date,
        value_date=tx.value_date,
        amount=tx.amount,
        counterparty_name=counterparty,
        purpose=_without(scrub(tx.purpose), names),
        booking_text=_without(scrub(tx.booking_text), names),
        iban_last4=masked[-4:] if masked else None,
        properties=sorted(labels.values()),
        tenancies=tenancies,
        loans=loans,
        similar=_similar(conn, line),
    )


@dataclass(frozen=True)
class HouseholdSimilar:
    """D168: an earlier confirmed answer of a private account for the same counterparty, as
    its name and categories only, never its amount or date."""

    counterparty_name: str
    categories: list[str]


@dataclass(frozen=True)
class HouseholdCategorizeInput:
    """The household request's data (D168): the line's own fields, scrubbed of IBANs and of
    every tenant's name, the last four characters of its counterparty's IBAN and the account's
    kind (never its label or its owner); never a property, tenancy or loan, never another
    account's lines but the similar answers of private accounts."""

    transaction_id: UUID
    booking_date: date
    value_date: date | None
    amount: Decimal
    counterparty_name: str
    purpose: str
    booking_text: str
    iban_last4: str | None
    account_kind: str
    similar: list[HouseholdSimilar]


def household_categorize_input(conn: Connection, line: Line) -> HouseholdCategorizeInput:
    tx = line.tx
    masked = conn.execute(
        select(transaction_table.c.counterparty_iban_masked).where(transaction_table.c.id == tx.id)
    ).scalar_one_or_none()
    names, _ = _tenant_names(conn, line)
    return HouseholdCategorizeInput(
        transaction_id=tx.id,
        booking_date=tx.booking_date,
        value_date=tx.value_date,
        amount=tx.amount,
        counterparty_name=_without(scrub(tx.counterparty_name), names),
        purpose=_without(scrub(tx.purpose), names),
        booking_text=_without(scrub(tx.booking_text), names),
        iban_last4=masked[-4:] if masked else None,
        account_kind=line.account_kind,
        similar=[
            HouseholdSimilar(_without(scrub(name), names), categories)
            for name, categories in _similar_private(conn, line)
        ],
    )


def _similar_private(conn: Connection, line: Line, limit: int = 5) -> list[tuple[str, list[str]]]:
    """The newest confirmed answers of private accounts for the same counterparty (by IBAN
    hash, else by name), as (name, categories)."""
    t = transaction_table.c
    if line.counterparty_iban_hmac is not None:
        same = t.counterparty_iban_hmac == line.counterparty_iban_hmac
    elif line.tx.counterparty_name:
        same = func.lower(t.counterparty_name) == line.tx.counterparty_name.lower()
    else:
        return []
    rows = conn.execute(
        select(t.id, t.counterparty_name, allocation_table.c.category)
        .join(allocation_table, allocation_table.c.transaction_id == t.id)
        .join(account_table, account_table.c.id == t.account_id)
        .where(
            same,
            t.id != line.tx.id,
            account_table.c.purpose == AccountPurpose.PRIVATE.value,
            allocation_table.c.status == AllocationStatus.CONFIRMED.value,
        )
        .order_by(t.booking_date.desc(), t.id, allocation_table.c.category)
    ).all()
    by_tx: dict[UUID, tuple[str, list[str]]] = {}
    for row in rows:
        if row.id not in by_tx:
            if len(by_tx) == limit:
                break
            by_tx[row.id] = (row.counterparty_name or "", [])
        by_tx[row.id][1].append(row.category)
    return list(by_tx.values())


TENANT_PLACEHOLDER = "[Mieter]"
_MIN_NAME_PART = 3


def _tenant_names(conn: Connection, line: Line) -> tuple[list[str], str | None]:
    """Every tenant's name and its parts (longest first), and the unit label when the line
    comes from a tenant's own account. The line's counterparty is then named by the unit, so
    no tenant name of any tenancy reaches the model (§5.3, stricter than "never another
    tenant's name")."""
    names: set[str] = set()
    own_unit = None
    rows = conn.execute(
        select(tenant_table.c.full_name, tenant_table.c.iban_hmac, unit_table.c.label)
        .select_from(tenant_table)
        .outerjoin(party_table, party_table.c.tenant_id == tenant_table.c.id)
        .outerjoin(tenancy_table, tenancy_table.c.id == party_table.c.tenancy_id)
        .outerjoin(unit_table, unit_table.c.id == tenancy_table.c.unit_id)
    )
    for row in rows:
        if row.full_name:
            full = " ".join(row.full_name.split())
            names.add(full)
            names.update(p for p in re.split(r"[\s,]+", full) if len(p) >= _MIN_NAME_PART)
        hmac = line.counterparty_iban_hmac
        if hmac is not None and row.iban_hmac is not None and bytes(row.iban_hmac) == hmac:
            own_unit = row.label
    return sorted(names, key=len, reverse=True), own_unit


def _without(text: str, names: list[str]) -> str:
    for name in names:
        # Whole words only: a name part "Jan" must not turn "Januar" into "[Mieter]uar".
        text = re.sub(rf"\b{re.escape(name)}\b", TENANT_PLACEHOLDER, text, flags=re.IGNORECASE)
    return text


def _similar(conn: Connection, line: Line, limit: int = 5) -> list[SimilarAnswer]:
    """The newest confirmed answers for the same counterparty (by IBAN hash, else by name) on
    accounts of the line's purpose: a private account's answers never reach a rental prompt
    (phase 6 D168), as _similar_private keeps rental ones out of the household prompt."""
    t = transaction_table.c
    if line.counterparty_iban_hmac is not None:
        same = t.counterparty_iban_hmac == line.counterparty_iban_hmac
    elif line.tx.counterparty_name:
        same = func.lower(t.counterparty_name) == line.tx.counterparty_name.lower()
    else:
        return []
    rows = conn.execute(
        select(t.id, t.booking_date, t.amount, allocation_table.c.category)
        .join(allocation_table, allocation_table.c.transaction_id == t.id)
        .join(account_table, account_table.c.id == t.account_id)
        .where(
            same,
            account_table.c.purpose == line.account_purpose.value,
            t.id != line.tx.id,
            allocation_table.c.status == AllocationStatus.CONFIRMED.value,
        )
        .order_by(t.booking_date.desc(), t.id, allocation_table.c.category)
    ).all()
    by_tx: dict[UUID, SimilarAnswer] = {}
    for row in rows:
        if row.id not in by_tx:
            if len(by_tx) == limit:
                break
            by_tx[row.id] = SimilarAnswer(row.booking_date, row.amount, [])
        by_tx[row.id].categories.append(row.category)
    return list(by_tx.values())


def only_property(conn: Connection) -> UUID | None:
    ids = conn.execute(select(property_table.c.id).limit(2)).scalars().all()
    return ids[0] if len(ids) == 1 else None


def only_loan(conn: Connection, property_id: UUID | None) -> tuple[UUID | None, UUID | None]:
    """D51: the workspace's one loan, when it is of `property_id`, and its Darlehensvertrag
    when exactly one document of that subtype is linked to it, for a model suggestion of a
    loan category (jobs.matching). (None, None) with no loan, two or more, or no property;
    (loan, None) without exactly one contract. Never a choice between records: the model
    names a category only, code fills the one record it can be."""
    loans = conn.execute(select(loan_table.c.id, loan_table.c.property_id).limit(2)).all()
    if property_id is None or len(loans) != 1 or loans[0].property_id != property_id:
        return None, None
    loan_id = loans[0].id
    contracts = (
        conn.execute(
            select(document_table.c.id)
            .join(link_table, link_table.c.document_id == document_table.c.id)
            .where(
                link_table.c.loan_id == loan_id,
                document_table.c.subtype == DocumentSubtype.LOAN_CONTRACT.value,
            )
            .distinct()
            .limit(2)
        )
        .scalars()
        .all()
    )
    return loan_id, contracts[0] if len(contracts) == 1 else None


def llm_period(text: str | None, tx: MatchTransaction, recurring: bool):  # noqa: ANN201
    """The model's service period as text, read by the same rule as a purpose (D27); the
    line's own purpose when the model's text says nothing parsable."""
    return service_period(text or "", tx.booking_date, False) or service_period(
        tx.purpose, tx.booking_date, recurring
    )
