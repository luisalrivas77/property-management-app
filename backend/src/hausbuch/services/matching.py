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

from sqlalchemy import Connection, Row, Select, and_, exists, func, or_, select

from hausbuch.ai.requests import load_prompt
from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import ai_call as ai_call_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import document as document_table
from hausbuch.db.tables import document_extraction as extraction_table
from hausbuch.db.tables import document_link as link_table
from hausbuch.db.tables import loan as loan_table
from hausbuch.db.tables import matching_rule as rule_table
from hausbuch.db.tables import possible_duplicate as duplicate_table
from hausbuch.db.tables import property_ as property_table
from hausbuch.db.tables import property_acquisition_cost as acquisition_cost_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import tax_input as tax_input_table
from hausbuch.db.tables import tenancy as tenancy_table
from hausbuch.db.tables import tenancy_tenant as party_table
from hausbuch.db.tables import tenant as tenant_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.db.tables import unit as unit_table
from hausbuch.db.tables import workspace as workspace_table
from hausbuch.jobs import enqueue
from hausbuch.rules import depot as depot_rules
from hausbuch.rules import matching as matchers
from hausbuch.rules.errors import ALLOCATION_CATEGORY_NOT_FOR_ACCOUNT, RuleError
from hausbuch.rules.extraction_parse import cost_lines as parse_cost_lines
from hausbuch.rules.household import card as card_rules
from hausbuch.rules.household.taxonomy import BULK_CONFIRMABLE
from hausbuch.rules.iban import iban_hmac, normalize_iban
from hausbuch.rules.periods import service_period
from hausbuch.rules.prefill import names_financing, sort_reading
from hausbuch.rules.review import no_match_reason, route
from hausbuch.schemas.ai import AiJob, ExtractedValue
from hausbuch.schemas.allocation import (
    Allocation,
    AllocationCategory,
    AllocationDraft,
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
    PurchasePayee,
    RecordedBill,
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
    AccountKind,
    AccountPurpose,
    AllocationStatus,
    DocumentSubtype,
    DuplicateStatus,
    ExtractionKind,
    ImportStatus,
)
from hausbuch.schemas.loan import LoanTerms
from hausbuch.schemas.tax import TaxInputKind

from . import allocations, rules_learned, workspace_settings
from .context import CommandContext
from .documents import DOCUMENT_EVIDENCE_KINDS, EVIDENCE_KINDS
from .tenancies import _rows_in_force, rent_on  # rent rows no correction supersedes
from .transfers import own_account_hmacs

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

    def open_for_reask(self) -> bool:
        """A one-off re-ask of the model (reask_household): a line that is open but for its
        current rows, and those are all proposed. Never a line with a confirmed row."""
        return bool(self.current) and self.waiting and self.open_for(rematch=True)

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
    account_id: UUID | None = None,
) -> MatchContext:
    """Everything the matchers may look at, read from the workspace. `for_transaction`: the
    line being matched (a Beleg its own rows reference stays a candidate); `account_purpose`:
    its account's purpose, which picks the order (phase 6 D158); `account_id`: its account,
    whose own IBAN is no other own account's (D162: own_account_hmacs is every account with
    an IBAN except the line's own, and every card account's confirmed payment IBAN, D176 (4),
    so the debit that settles a card is a transfer by that IBAN alone; two cards printing the
    same payment IBAN give it once, and the link decides by amount). For a private line,
    shared_iban_hmacs holds its counterparty IBAN hash when that IBAN is shared (D160 amended);
    a rental line never gets one, so the rental account's IBAN logic is unchanged."""
    owners = own_account_hmacs(conn, except_account=account_id)
    plans, documents = _evidence(conn, iban_key=iban_key, today=today, exclude_for=for_transaction)
    shared: list[str] = []
    if account_purpose is AccountPurpose.PRIVATE and for_transaction is not None:
        hmac = conn.execute(
            select(transaction_table.c.counterparty_iban_hmac).where(
                transaction_table.c.id == for_transaction
            )
        ).scalar_one_or_none()
        if hmac is not None and is_shared_iban(conn, bytes(hmac)):
            shared = [bytes(hmac).hex()]
    private = account_purpose is AccountPurpose.PRIVATE
    return MatchContext(
        recorded_bills=recorded_bills(conn, iban_key=iban_key, for_transaction=for_transaction)
        if private
        else [],
        purchase_payees=purchase_payees(conn) if private else [],
        account_purpose=account_purpose,
        own_account_hmacs=owners,
        shared_iban_hmacs=shared,
        tenancies=_tenancies(conn),
        loans=_loans(conn),
        documents=documents,
        wirtschaftsplaene=plans,
    )


def recorded_bills(
    conn: Connection,
    *,
    iban_key: bytes | None,
    for_transaction: UUID | None = None,
    document_ids: list[UUID] | None = None,
) -> list[RecordedBill]:
    """Phase 6 D161 widened (14 Nov 2026): every bill a property already counts through its
    rows, as rules.matching.match_recorded_bill reads it for a private line. A bill is a
    document that a Nebenkosten row (property_acquisition_cost) or a private_cost row in force
    carries as Beleg, all of one property, with an evidence reading. Left out: a bill a live
    rental-account line already carries (it was paid from the rental account, D135). `paid`:
    the live private hh_property_cost rows of other lines that carry it. In a fixed number of
    queries; `document_ids` narrows it to those bills; without `iban_key` no bill has its
    IBAN hash (a count from the operator's computer, where the key is not)."""
    c, ti = acquisition_cost_table.c, tax_input_table.c
    acquisition: dict[UUID, list[tuple[UUID, Decimal]]] = {}
    query = select(c.document_id, c.property_id, c.amount).where(c.document_id.is_not(None))
    if document_ids is not None:
        query = query.where(c.document_id.in_(document_ids))
    for doc, prop, amount in conn.execute(query.order_by(c.created_at, c.id)):
        acquisition.setdefault(doc, []).append((prop, amount))
    later = tax_input_table.alias("later")
    query = select(ti.document_id, ti.property_id, ti.value).where(
        ti.kind == TaxInputKind.PRIVATE_COST.value,
        ti.document_id.is_not(None),
        ti.withdrawn.is_(False),
        ~exists().where(later.c.supersedes_id == ti.id),
    )
    if document_ids is not None:
        query = query.where(ti.document_id.in_(document_ids))
    private: dict[UUID, list[tuple[UUID, Decimal | None]]] = {}
    for doc, prop, value in conn.execute(query.order_by(ti.recorded_at, ti.id)):
        amount = _money((value or {}).get("amount"))
        private.setdefault(doc, []).append((prop, amount))
    ids = sorted(set(acquisition) | set(private), key=str)
    if not ids:
        return []
    a, t = allocation_table.c, transaction_table.c
    live = conn.execute(
        select(a.document_id, a.amount, a.category, account_table.c.purpose)
        .select_from(allocation_table.join(transaction_table, t.id == a.transaction_id))
        .join(account_table, account_table.c.id == t.account_id)
        .where(
            a.document_id.in_(ids),
            a.status != AllocationStatus.SUPERSEDED.value,
            t.excluded_as_duplicate_of.is_(None),
            *([] if for_transaction is None else [a.transaction_id != for_transaction]),
        )
    ).all()
    rental_paid = {r.document_id for r in live if r.purpose == AccountPurpose.RENTAL.value}
    paid: dict[UUID, Decimal] = {}
    for r in live:
        if r.purpose == AccountPurpose.PRIVATE.value and r.category == C.HH_PROPERTY_COST.value:
            paid[r.document_id] = paid.get(r.document_id, Decimal("0.00")) + abs(r.amount)
    evidence = evidence_of_many(conn, ids)
    out: list[RecordedBill] = []
    for doc in ids:
        e = evidence.get(doc)
        if e is None or doc in rental_paid:
            continue
        rows_a, rows_p = acquisition.get(doc, []), private.get(doc, [])
        properties = {p for p, _ in rows_a} | {p for p, _ in rows_p}
        if len(properties) != 1 or any(amount is None for _, amount in rows_p):
            continue  # two properties, or a private row without an amount (a trip)
        x = e.extraction
        out.append(
            RecordedBill(
                document_id=doc,
                property_id=next(iter(properties)),
                acquisition=[amount for _, amount in rows_a],
                private=[amount for _, amount in rows_p if amount is not None],
                lines=_cost_line_amounts(x),
                issued_on=_date(_value(x, "document_date")),
                due_on=_date(_value(x, "due_on")),
                iban_hmac=None if iban_key is None else _hmac(_value(x, "iban"), iban_key),
                reference=_value(x, "reference") or None,
                paid=paid.get(doc, Decimal("0.00")),
            )
        )
    return out


def _cost_line_amounts(extraction: DocumentExtraction) -> list[Decimal]:
    """The cost lines an invoice's or Bescheid's reading states (phase 3 D150, as
    services.tax.bill_lines reads them), from the extraction in hand."""
    if extraction.kind not in (ExtractionKind.INVOICE, ExtractionKind.BESCHEID):
        return []
    read = getattr(extraction.fields, "cost_lines", None)
    lines = None if read is None else parse_cost_lines(read.value)
    return [] if lines is None else [line.amount.quantize(_CENT) for line in lines]


def purchase_payees(conn: Connection) -> list[PurchasePayee]:
    """The seller's accounts: the counterparty IBAN hash of every rental line with a confirmed
    purchase_price row, with the property its rows name (None when they name none or two)."""
    a, t = allocation_table.c, transaction_table.c
    rows = conn.execute(
        select(t.counterparty_iban_hmac, a.property_id)
        .select_from(allocation_table.join(transaction_table, t.id == a.transaction_id))
        .join(account_table, account_table.c.id == t.account_id)
        .where(
            a.category == C.PURCHASE_PRICE.value,
            a.status == AllocationStatus.CONFIRMED.value,
            t.counterparty_iban_hmac.is_not(None),
            t.excluded_as_duplicate_of.is_(None),
            account_table.c.purpose == AccountPurpose.RENTAL.value,
        )
    ).all()
    by_iban: dict[str, set[UUID | None]] = {}
    for hmac, property_id in rows:
        by_iban.setdefault(bytes(hmac).hex(), set()).add(property_id)
    return [
        PurchasePayee(iban_hmac=hmac, property_id=next(iter(props)) if len(props) == 1 else None)
        for hmac, props in sorted(by_iban.items())
    ]


def is_shared_iban(conn: Connection, hmac: bytes | None) -> bool:
    """D160 amended: whether the lines of the workspace's private accounts with this IBAN hash
    carry two payees that are not one payee (rules.matching.shared_iban; computed on read,
    nothing stored). False for no IBAN. D160 amended (4): a payment service's direct debit is
    read with its purpose, which names its merchant (PayPal's IBAN carries many merchants);
    every other line by its name alone."""
    if hmac is None:
        return False
    t = transaction_table.c
    private_lines = (
        select(t.counterparty_name)
        .distinct()
        .join(account_table, account_table.c.id == t.account_id)
        .where(
            account_table.c.purpose == AccountPurpose.PRIVATE.value,
            t.counterparty_iban_hmac == hmac,
            t.counterparty_name.is_not(None),
        )
    )
    lines: list[str | None | tuple[str | None, str | None]] = []
    services: list[str] = []
    for name in conn.execute(private_lines).scalars():
        found = matchers.payee(name)
        if found.service is not None and not found.known:
            services.append(name)
        else:
            lines.append(name)
    if services:
        with_purpose = private_lines.add_columns(t.purpose).where(t.counterparty_name.in_(services))
        lines += [(row.counterparty_name, row.purpose) for row in conn.execute(with_purpose)]
    return matchers.shared_iban(lines)


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
    ids = list(ids)
    # One read for every document (14 Nov 2026: a query per document per line was most of a
    # rule's re-check and of the allocations GET); `found` keeps the order of `ids`.
    by_id = evidence_of_many(conn, ids)
    found = [e for doc_id in ids if (e := by_id.get(doc_id)) is not None]
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
    return evidence_of_many(conn, [document_id]).get(document_id)


def evidence_of_many(conn: Connection, document_ids: list[UUID]) -> dict[UUID, _Evidence]:
    """evidence_of for many documents in a fixed number of queries: each document's current
    (highest-version) extraction when it is an evidence kind, its row, its links, and the
    property its unit or loan links name when no link names one. Documents that are no
    evidence are left out."""
    if not document_ids:
        return {}
    x = extraction_table.c
    newest = (
        select(x.document_id, func.max(x.version).label("version"))
        .where(x.document_id.in_(document_ids))
        .group_by(x.document_id)
        .subquery()
    )
    extractions: dict[UUID, DocumentExtraction] = {}
    for row in conn.execute(
        select(extraction_table).join(
            newest, and_(newest.c.document_id == x.document_id, newest.c.version == x.version)
        )
    ):
        extraction = DocumentExtraction.model_validate(dict(row._mapping))
        if extraction.kind in EVIDENCE_KINDS:
            extractions[row.document_id] = extraction
    if not extractions:
        return {}
    ids = list(extractions)
    rows = {
        r.id: r for r in conn.execute(select(document_table).where(document_table.c.id.in_(ids)))
    }
    links: dict[UUID, list[Row]] = {doc_id: [] for doc_id in ids}
    for link in conn.execute(select(link_table).where(link_table.c.document_id.in_(ids))):
        links[link.document_id].append(link)
    unit_ids = {lk.unit_id for found in links.values() for lk in found if lk.unit_id is not None}
    loan_ids = {lk.loan_id for found in links.values() for lk in found if lk.loan_id is not None}
    unit_property = (
        dict(
            conn.execute(
                select(unit_table.c.id, unit_table.c.property_id).where(
                    unit_table.c.id.in_(unit_ids)
                )
            ).all()
        )
        if unit_ids
        else {}
    )
    loan_property = (
        dict(
            conn.execute(
                select(loan_table.c.id, loan_table.c.property_id).where(
                    loan_table.c.id.in_(loan_ids)
                )
            ).all()
        )
        if loan_ids
        else {}
    )
    found: dict[UUID, _Evidence] = {}
    for doc_id, extraction in extractions.items():
        doc_links = links[doc_id]
        properties = {lk.property_id for lk in doc_links if lk.property_id is not None}
        doc_loans = sorted({lk.loan_id for lk in doc_links if lk.loan_id is not None})
        if not properties:
            properties |= {
                unit_property[lk.unit_id]
                for lk in doc_links
                if lk.unit_id is not None and lk.unit_id in unit_property
            }
            properties |= {loan_property[i] for i in doc_loans if i in loan_property}
        property_id = next(iter(properties)) if len(properties) == 1 else None
        found[doc_id] = _Evidence(rows[doc_id], extraction, property_id, doc_loans)
    return found


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
    """Phase 2 D135: a bank line of a rental account pays this bill, so none of it is a cost
    paid from another account. True when an allocation (proposed or confirmed) of a rental
    account's line already carries the document as its Beleg, or when a stored rental line
    is a document hit of it (rules.matching.document_hit: the amount within 0.01, the booking
    within 10 days of the document date, and the bill's IBAN or its reference in the purpose;
    the IBAN is compared only when the key is given). A document the matcher cannot read (no
    amount or date) finds no line. A private account's line is paid_from_private_account's
    (phase 6 review F4)."""
    return _paid_from(conn, document_id, AccountPurpose.RENTAL, iban_key)


def paid_from_private_account(
    conn: Connection, document_id: UUID, *, iban_key: bytes | None = None
) -> bool:
    """Phase 6 D161 (review F4): a live private line is booked hh_property_cost with this
    bill as its Beleg. Such a bill is no D135 private cost of the reading: D161's path 2 card
    proposes its rows from the line (services.property_costs), so they are never proposed
    twice. A bare document hit on a private line is not enough (second review F-A): that line
    may be booked as something else, or still waiting, and then no card would ever offer the
    rows, so the reading proposes them itself."""
    return _paid_from(conn, document_id, AccountPurpose.PRIVATE, iban_key)


def _paid_from(
    conn: Connection, document_id: UUID, purpose: AccountPurpose, iban_key: bytes | None
) -> bool:
    t, acc = transaction_table.c, account_table.c
    of_purpose = (
        select(t.id).join(account_table, acc.id == t.account_id).where(acc.purpose == purpose.value)
    )
    booked = [
        allocation_table.c.document_id == document_id,
        allocation_table.c.status != AllocationStatus.SUPERSEDED.value,
        allocation_table.c.transaction_id.in_(of_purpose),
    ]
    if purpose is AccountPurpose.PRIVATE:
        booked.append(allocation_table.c.category == AllocationCategory.HH_PROPERTY_COST.value)
    carried = conn.execute(select(exists().where(*booked))).scalar_one()
    if carried or purpose is AccountPurpose.PRIVATE:
        return carried
    e = evidence_of(conn, document_id)
    if e is None or e.extraction.kind is ExtractionKind.GRUNDSTEUERBESCHEID:
        return False
    facts = document_facts(e, iban_key=iban_key or b"", loans_by_property={})
    if facts is None:
        return False
    if iban_key is None:
        facts = facts.model_copy(update={"iban_hmac": None})
    window = timedelta(days=matchers.DOCUMENT_WINDOW_DAYS)
    for payment in facts.payments:
        low = payment.amount - matchers.DOCUMENT_AMOUNT_TOLERANCE
        high = payment.amount + matchers.DOCUMENT_AMOUNT_TOLERANCE
        rows = conn.execute(
            select(transaction_table)
            .join(account_table, acc.id == t.account_id)
            .where(
                acc.purpose == purpose.value,
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
    conn: Connection,
    tx: MatchTransaction,
    context: MatchContext,
    *,
    skip_rule_categories: frozenset[AllocationCategory] = frozenset(),
) -> AllocationSet | None:
    """The deterministic order of the line's account purpose (rules.matching.
    deterministic_answer, phase 6 D158), with the service-side guards (F3, D39) applied.
    Rental: learned rules, then the matchers, then the document match (§8.1); the rules'
    answer is either a standing hit (confidence 1, used at once) or a near-miss proposal
    (D49), which the matchers come before: an exact rent, loan, owner or document match wins
    over it. Private: learned rules, the document match, then the tenant / lender question.
    `skip_rule_categories`: a rule booking one of these codes is left out (the re-sort into
    the subcategories, services.household_resort: a rule learned on a group's fallback would
    only book the line back where it is)."""
    rules = [
        rule
        for rule in rules_learned.rules_for_purpose(conn, context.account_purpose)
        if rule.category not in skip_rule_categories
        and not set(rule.categories or ()) & skip_rule_categories
    ]
    shared = tx.counterparty_iban_hmac in context.shared_iban_hmacs
    history = (
        rule_history(conn, tx, context.account_purpose, shared=shared or _service_line(tx))
        if _any_hit(rules, tx)
        else []
    )
    found = matchers.deterministic_answer(rules, tx, context, history)
    return None if found is None else _guarded(conn, found)


def _service_line(tx: MatchTransaction) -> bool:
    """A payment service's line (PayPal, Klarna; D160 amended (4)): its IBAN names the
    service, so what is keyed by the IBAN is keyed by IBAN and payee, as on a shared IBAN."""
    return matchers.payee(tx.counterparty_name, tx.purpose).service is not None


def _any_hit(rules: list[MatchingRule], tx: MatchTransaction) -> bool:
    return tx.counterparty_iban_hmac is not None and any(
        r.enabled and matchers.rule_hits(r.matcher, tx) for r in rules
    )


def rule_history(
    conn: Connection,
    tx: MatchTransaction,
    purpose: AccountPurpose = AccountPurpose.RENTAL,
    *,
    shared: bool = False,
) -> list[HistoryLine]:
    """The other lines from the line's IBAN hash with the same sign on accounts of the line's
    purpose (phase 6 D158: a private line of the same payee is not a kind of the rental
    rule's, nor the reverse), not excluded as a
    duplicate, with their current (not superseded) allocation rows, for rules.matching.
    rule_answer (D49), which keeps those whose rows are all confirmed. Each row carries its
    amount, scope and recurring flag, which an as_last_split rule copies (D51). Empty for a line
    without an IBAN hash or of amount 0. `shared` (D160 amended: the IBAN is shared): only the
    lines of the line's own payee (rules.matching.same_payee) count, so the other payees of a
    clearing IBAN are not "another kind" of this payee's payments; so for a payment service's
    line (D160 amended (4), the caller passes shared), whose merchants share the service's IBAN."""
    if tx.counterparty_iban_hmac is None or tx.amount == 0:
        return []
    t = transaction_table.c
    a = allocation_table.c
    rows = conn.execute(
        select(
            t.id,
            t.booking_date,
            t.amount,
            t.counterparty_name,
            t.purpose,
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
        if shared and not matchers.same_payee(
            tx.counterparty_name, row.counterparty_name, tx.purpose, row.purpose
        ):
            continue
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


@dataclass(frozen=True)
class NameLine:
    """A private line of the same sign as the line a name rule is learned from, with its
    current (not superseded) rows: what name_history reads and what the re-booking of
    „Künftig immer so zuordnen“ (review.allocate_transaction, rebook_earlier) checks."""

    transaction_id: UUID
    counterparty_name: str
    amount: Decimal
    rows: tuple[Row, ...]
    purpose: str = ""  # names a payment service's merchant (D160 amended (4))

    @property
    def single_confirmed(self) -> Row | None:
        """The one current row when the line has exactly one and it is confirmed."""
        if len(self.rows) == 1 and self.rows[0].status == AllocationStatus.CONFIRMED.value:
            return self.rows[0]
        return None

    @property
    def confirmed(self) -> bool:
        """Every current row confirmed (a line with none is not)."""
        return bool(self.rows) and all(
            r.status == AllocationStatus.CONFIRMED.value for r in self.rows
        )


def name_lines(conn: Connection, tx: MatchTransaction) -> list[NameLine]:
    """D160: the other lines of every private account with the line's sign and a counterparty
    name, each with its current rows (none superseded), oldest first. Every private account,
    since a name rule hits the lines of all of them. Lines merged away as a duplicate are left
    out; whether the line's name rule hits one is the caller's (rules.matching.name_hits,
    rule_name_hits)."""
    if not tx.counterparty_name or tx.amount == 0:
        return []
    t = transaction_table.c
    a = allocation_table.c
    rows = conn.execute(
        select(
            t.id,
            t.counterparty_name,
            t.purpose,
            t.amount,
            a.id.label("allocation_id"),
            a.category,
            a.status,
            a.rule_id,
            a.property_id,
            a.unit_id,
            a.tenancy_id,
            a.loan_id,
            a.document_id,
            a.recurring,
            a.service_period_start,
            a.service_period_end,
        )
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
        .order_by(t.booking_date, t.id, a.recorded_at, a.id)
    ).all()
    lines: dict[UUID, list[Row]] = {}
    for row in rows:
        lines.setdefault(row.id, []).append(row)
    return [
        NameLine(
            transaction_id=line_id,
            counterparty_name=parts[0].counterparty_name,
            amount=parts[0].amount,
            rows=tuple(parts),
            purpose=parts[0].purpose or "",
        )
        for line_id, parts in lines.items()
    ]


def name_history(conn: Connection, tx: MatchTransaction) -> list[NameHistory]:
    """D160: the other lines of every private account with the line's sign and a
    counterparty name whose current rows are confirmed under one category, for the checks a
    name rule learned from this line must pass (rules.matching._name_rule: rule.too_broad,
    rule.conflicts). Every private account, since a rule hits the lines of all of them."""
    return [
        NameHistory(
            transaction_id=line.transaction_id,
            counterparty_name=line.counterparty_name,
            category=only.category,
            amount=line.amount,
            purpose=line.purpose,
        )
        for line in name_lines(conn, tx)
        if (only := line.single_confirmed) is not None
    ]


def pattern_lines(
    conn: Connection, tx: MatchTransaction, word: str, purpose: AccountPurpose
) -> list[matchers.PatternLine]:
    """The workspace's other lines of the line's sign whose purpose contains `word` (the
    first word a suggested purpose pattern anchors, rules.matching.purpose_word), every
    account, with the categories of their confirmed current rows; lines merged away as a
    duplicate left out. One query; it reads a superset of what `^word` hits (any position,
    any non-ASCII letter matched by one character whatever the database's locale folds), so
    the caller narrows it with rules.matching.rule_hits. `waiting` as in waiting_lines (a
    reconciled import, no confirmed row), on an account of `purpose`: a rule learned on the
    line books no other purpose's line (rules_learned.rules_for_purpose, the categories each
    purpose takes)."""
    if tx.amount == 0:
        return []
    t = transaction_table.c
    a = allocation_table.c
    # The word is letters only: no LIKE wildcard to escape. ILIKE folds ASCII in any locale.
    like = "%" + "".join(ch if ch.isascii() else "_" for ch in word) + "%"
    confirmed = func.array_agg(a.category).filter(a.status == AllocationStatus.CONFIRMED.value)
    rows = conn.execute(
        select(
            t.id,
            t.booking_date,
            t.value_date,
            t.amount,
            t.counterparty_name,
            t.counterparty_iban_hmac,
            t.purpose,
            t.booking_text,
            import_table.c.status.label("import_status"),
            account_table.c.purpose.label("account_purpose"),
            confirmed.label("confirmed"),
        )
        .select_from(
            transaction_table.join(import_table, import_table.c.id == t.import_id)
            .join(account_table, account_table.c.id == t.account_id)
            .outerjoin(
                allocation_table,
                and_(
                    a.transaction_id == t.id,
                    a.status != AllocationStatus.SUPERSEDED.value,
                ),
            )
        )
        .where(
            t.purpose.ilike(like),
            t.amount > 0 if tx.amount > 0 else t.amount < 0,
            t.id != tx.id,
            t.excluded_as_duplicate_of.is_(None),
        )
        .group_by(t.id, import_table.c.status, account_table.c.purpose)
    ).all()
    lines = []
    for row in rows:
        categories = tuple(AllocationCategory(c) for c in row.confirmed or ())
        lines.append(
            matchers.PatternLine(
                tx=match_transaction(row),
                confirmed=categories,
                waiting=not categories
                and row.import_status == ImportStatus.RECONCILED.value
                and row.account_purpose == purpose.value,
            )
        )
    return lines


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
    reask: bool = False,
    skip_if_asked: bool = True,
) -> MatchPlan:
    """Run the deterministic order on one line and write what it can. With `ai_available`,
    a line nothing matched comes back as `categorize`, and a matcher's set bound for review
    as `explain`; without, the matcher's set is written with its template text and an
    unmatched line stays without rows (inbox "Nicht zugeordnet"). A re-match never calls
    the model. A re-ask (`reask`, reask_household) takes a line whose rows are all proposed
    and runs the order as for a new line: the model is asked again when nothing else
    answers, and its set supersedes the proposal (finish_line). `skip_if_asked` makes a
    second queued re-ask a no-op; a retry of the same job passes False, since its own
    model call succeeded but writing the answer failed, and the answer must not be lost."""
    line = load_line(ctx.conn, transaction_id, lock=True)
    if line is None:
        return MatchPlan("skipped", "matching.not_found")
    if not (line.open_for_reask() if reask else line.open_for(rematch=rematch)):
        return MatchPlan("skipped", _closed_reason(line))
    if reask and skip_if_asked and asked_with_current_prompt(ctx.conn, transaction_id):
        return MatchPlan("skipped", "matching.asked_already")  # a re-ask queued twice
    context = build_context(
        ctx.conn,
        iban_key=iban_key,
        today=ctx.now.date(),
        for_transaction=transaction_id,
        account_purpose=line.account_purpose,
        account_id=line.account_id,
    )
    found = (
        _card_charge(line) or _depot_booking(line) or deterministic_set(ctx.conn, line.tx, context)
    )
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


def _card_charge(line: Line) -> AllocationSet | None:
    """Phase 6 D176 (3): a card statement's interest or fee line, booked by code to
    hh_interest_fees before any rule or model (it has no payee to learn); D176 (4): its payment
    line, booked by code as internal_transfer_in (the card's leg of the settlement, linked by
    services.transfers). Card accounts only."""
    if line.account_kind != AccountKind.CREDIT_CARD.value:
        return None
    return card_rules.interest_fee_set(line.tx) or card_rules.payment_set(line.tx)


def _depot_booking(line: Line) -> AllocationSet | None:
    """Phase 6 step 4 (D165 (1)): a depot cash line booked by code from its headline
    (rules.depot.categorise) before any rule or model, at confidence 1 (method
    matcher_depot). A headline no pattern knows is left to the usual order and waits in Zu
    prüfen. Depot accounts only. An Einlage or Auszahlung is booked as a transfer, which
    services.transfers links to the bank account's leg (D162), as the card's payment is."""
    if line.account_kind != AccountKind.DEPOT.value:
        return None
    category = depot_rules.categorise(line.tx.booking_text or "")
    if category is AllocationCategory.UNCATEGORIZED:
        return None
    return AllocationSet(
        transaction_id=line.tx.id,
        allocations=[AllocationDraft(amount=line.tx.amount, category=category)],
        method=AllocationMethod.MATCHER_DEPOT,
        confidence=Decimal(1),
        reasoning=depot_rules.REASONING,
    )


def _copies_a_split(conn: Connection, found: AllocationSet) -> bool:
    """Whether the set is an as_last_split rule's copy (D51)."""
    if found.rule_id is None:
        return False
    method = conn.execute(
        select(rule_table.c.split_method).where(rule_table.c.id == found.rule_id)
    ).scalar_one_or_none()
    return method == SplitMethod.AS_LAST_SPLIT.value


def write(
    ctx: CommandContext, line: Line, found: AllocationSet, *, propose_only: bool = False
) -> MatchPlan:
    """Route the set and write it, unless the line already holds the same answer.
    `propose_only` (a re-ask's model answer, reask_household): never auto-confirmed, so the
    one-off re-ask only replaces proposals and the person confirms every new answer."""
    decision = _route(line, found)
    auto = decision is RoutingDecision.AUTO_CONFIRM and not propose_only
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


def finish_line(
    ctx: CommandContext, transaction_id: UUID, found: AllocationSet, *, reask: bool = False
) -> MatchPlan:
    """After a model call: write the set if the line is still open and still has no rows
    (a person may have answered meanwhile). After a re-ask, also while its rows are still all
    proposed: a person's confirmation in between is never replaced."""
    line = load_line(ctx.conn, transaction_id, lock=True)
    still_open = line is not None and (
        line.open_for(rematch=False) or (reask and line.open_for_reask())
    )
    if line is None or not still_open:
        return MatchPlan("skipped", "matching.answered_meanwhile")
    if not allocations.categories_allowed(line.account_purpose, found.allocations):
        # D159: never booked; the line stays unmatched in Zu prüfen (the request's schema
        # makes this unreachable, so a model answer here is a bug, not a retry).
        return MatchPlan("skipped", ALLOCATION_CATEGORY_NOT_FOR_ACCOUNT)
    return write(ctx, line, _llm_guarded(line, found), propose_only=reask)


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


# --- The one-off re-ask of household lines (D159 amended 13 Nov 2026) -------------------------

# The codes a household line was parked under before the finer codes existed (migration 0029).
REASK_CATEGORIES = frozenset(
    {C.HH_OTHER, C.UNCATEGORIZED, C.HH_LEISURE_TRAVEL, C.HH_SUBSCRIPTIONS, C.HH_HOUSING}
)
# The codes a re-ask may be pointed at (`household-reask --category`, D159 amended 14 Nov 2026:
# hh_mobility once hh_fuel split fuel off, migration 0031): the parked codes above and the
# plain household income and spending codes „Alle bestätigen“ may confirm as a group. Never a
# tax-relevant, movement or depot code, nor one a person confirms line by line.
REASKABLE_CATEGORIES = REASK_CATEGORIES | BULK_CONFIRMABLE
# Words that may name a cost of buying or financing the flat, paid from a private account
# (phase 2 D135, phase 3 D143, phase 4b D134): such a line is left as it is, since re-sorting
# it is a tax question for a person, never a one-off batch (rules.matching's list, which the
# recorded-bill match reads too).
_PURCHASE_COST_WORDS = matchers.PURCHASE_COST_WORDS


@dataclass(frozen=True)
class ReaskResult:
    queued: int  # a match_transaction job each, with reask
    held_back: int  # `ai_private_accounts` is off: nothing sent, nothing queued
    purchase_costs: int  # left alone: may be a privately paid cost of buying the flat
    already_asked: int  # asked with the current household prompt already


def household_prompt_version() -> str:
    return load_prompt("categorize_household").version


def _asked_with(version: str) -> Select:
    """The lines a categorize call with this prompt answered (an ai_call without an error)."""
    c = ai_call_table.c
    return select(c.transaction_id).where(
        c.job == AiJob.MATCH.value,
        c.prompt_version == version,
        c.error_code.is_(None),
        c.transaction_id.is_not(None),
    )


def lines_asked_with_current_prompt(conn: Connection) -> set[UUID]:
    """Every line answered with the current household prompt (asked_with_current_prompt for
    a whole run at once)."""
    return set(conn.execute(_asked_with(household_prompt_version())).scalars())


def acquisition_cost_amounts(conn: Connection) -> set[Decimal]:
    """The Nebenkosten rows' amounts (property_acquisition_cost), for may_be_purchase_cost."""
    return {
        abs(amount)
        for amount in conn.execute(select(acquisition_cost_table.c.amount)).scalars()
        if amount is not None
    }


def may_be_purchase_cost(line: Row, costs: set[Decimal]) -> bool:
    """Whether a private line may be a cost of buying or financing the flat: its payee,
    purpose or booking text names one (_PURCHASE_COST_WORDS), or its amount equals a
    Nebenkosten row's to the cent. A one-off batch (the re-ask, the re-sort) leaves such a
    line to a person. `line` has amount, counterparty_name, purpose and booking_text."""
    text = " ".join(
        filter(None, (line.counterparty_name, line.purpose, line.booking_text))
    ).casefold()
    return any(word in text for word in _PURCHASE_COST_WORDS) or any(
        abs(abs(line.amount) - cost) <= _CENT for cost in costs
    )


def asked_with_current_prompt(conn: Connection, transaction_id: UUID) -> bool:
    """Whether the line was answered with the current household prompt (a re-ask job queued
    twice asks once)."""
    asked = _asked_with(household_prompt_version())
    return conn.execute(
        select(exists(asked.where(ai_call_table.c.transaction_id == transaction_id)))
    ).scalar_one()


def reask_household(
    ctx: CommandContext, categories: frozenset[AllocationCategory] = REASK_CATEGORIES
) -> ReaskResult:
    """Ask the model again for every private-account line whose current set is all proposed
    and all in `categories` (by default the codes the finer ones of migration 0029 split up),
    so a line parked in „Sonstiges“ gets one of the new codes; a later code that splits one
    up is re-asked by naming it (hh_mobility after hh_fuel, migration 0031), only codes of
    REASKABLE_CATEGORIES. Run by the operator after the deploy; no endpoint.

    Each line gets a match_transaction job with `reask`: the job runs the whole order as for a
    new line (a learned rule or matcher answers first) and asks the model last; its set
    supersedes the proposal (append-only, allocations.record_set), routed as any model answer
    (a private account's first import never auto-confirms). A line with a confirmed row is
    never taken, here or in the job (Line.open_for_reask, under the line's lock).

    Left alone: a line whose text names a cost of buying or financing the flat, or whose
    amount equals a Nebenkosten row's (property_acquisition_cost) to the cent; and a line
    already answered with the current household prompt, so a second run asks nothing again.
    Nothing is queued while `ai_private_accounts` is off (D168); the lines are counted.
    Writes no event: the jobs write the sets, and each model call its ai_call row."""
    if not categories or not categories <= REASKABLE_CATEGORIES:
        raise ValueError("a re-ask takes parked or plain household codes only")
    codes = sorted(c.value for c in categories)
    a = allocation_table.c
    t = transaction_table.c

    def has_row(*where: Any) -> Any:
        return exists().where(
            a.transaction_id == t.id, a.status != AllocationStatus.SUPERSEDED.value, *where
        )

    awaiting = exists().where(
        duplicate_table.c.other_transaction_id == t.id,
        duplicate_table.c.status == DuplicateStatus.OPEN.value,
    )
    lines = ctx.conn.execute(
        select(t.id, t.amount, t.counterparty_name, t.purpose, t.booking_text)
        .join(import_table, import_table.c.id == t.import_id)
        .join(account_table, account_table.c.id == t.account_id)
        .where(
            account_table.c.purpose == AccountPurpose.PRIVATE.value,
            import_table.c.status == ImportStatus.RECONCILED.value,
            t.excluded_as_duplicate_of.is_(None),
            ~awaiting,
            has_row(a.category.in_(codes)),
            ~has_row(or_(a.status != AllocationStatus.PROPOSED.value, a.category.not_in(codes))),
        )
        .order_by(t.booking_date, t.id)
    ).all()
    costs = acquisition_cost_amounts(ctx.conn)
    asked = lines_asked_with_current_prompt(ctx.conn)
    purchase = already = 0
    candidates: list[UUID] = []
    for line in lines:
        if may_be_purchase_cost(line, costs):
            purchase += 1
        elif line.id in asked:
            already += 1
        else:
            candidates.append(line.id)
    if not workspace_settings.private_ai_allowed(ctx.conn):
        return ReaskResult(0, len(candidates), purchase, already)
    for transaction_id in candidates:
        enqueue.enqueue_match_transaction(
            ctx.conn, workspace_id=ctx.workspace_id, transaction_id=transaction_id, reask=True
        )
    return ReaskResult(len(candidates), 0, purchase, already)


# --- Which lines a new document affects ---------------------------------------------------


def lines_for_evidence(
    conn: Connection, document_id: UUID, *, iban_key: bytes, today: date
) -> list[UUID]:
    """Lines a new reading of this document may change (rematch_on_evidence): for an invoice
    or Bescheid, lines within its payments' amount and date window, and, for a bill already
    recorded as a Nebenkosten or private_cost row, the private debits that may pay it
    (_recorded_bill_lines, D161 widened); for a Wirtschaftsplan,
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
    ids.extend(_recorded_bill_lines(conn, document_id, iban_key=iban_key))
    return list(dict.fromkeys(ids))


def _recorded_bill_lines(conn: Connection, document_id: UUID, *, iban_key: bytes) -> list[UUID]:
    """D161 widened: the private debits a recorded bill may be paid by (rules.matching.
    recorded_amounts within 0.01, booked in recorded_window), the candidates of
    match_recorded_bill; none for a document that is no recorded bill."""
    bills = recorded_bills(conn, iban_key=iban_key, document_ids=[document_id])
    return [tx for bill in bills for tx in _paying_candidates(conn, bill)]


def _paying_candidates(conn: Connection, bill: RecordedBill) -> list[UUID]:
    window = matchers.recorded_window(bill)
    if window is None:
        return []
    t = transaction_table.c
    tolerance = matchers.DOCUMENT_AMOUNT_TOLERANCE
    near = [
        func.abs(t.amount).between(amount - tolerance, amount + tolerance)
        for amount in matchers.recorded_amounts(bill)
    ]
    query = (
        select(t.id)
        .join(account_table, account_table.c.id == t.account_id)
        .where(
            account_table.c.purpose == AccountPurpose.PRIVATE.value,
            t.amount < 0,
            t.booking_date.between(*window),
            or_(*near),
        )
    )
    return list(conn.execute(query.order_by(t.booking_date, t.id)).scalars())


@dataclass(frozen=True)
class RecordedBillsRematch:
    bills: int  # recorded bills, a rematch_on_evidence job each
    waiting: int  # private debits of their amount and window still waiting for an answer


def rematch_recorded_bills(ctx: CommandContext) -> RecordedBillsRematch:
    """One-off after the deploy of D161 widened (14 Nov 2026), run by the operator (`hausbuch
    rematch-bills`): queue rematch_on_evidence for every bill recorded as a Nebenkosten or
    private_cost row, so the private lines already waiting in Zu prüfen that pay one get the
    hh_property_cost proposal. The job runs the deterministic order only, never the model,
    and never replaces a confirmed row (Line.open_for). New rows queue the same job
    themselves (services.properties, services.tax_inputs). The IBAN key stays in the API
    container: the bills are read here without it, which changes only the count."""
    bills = recorded_bills(ctx.conn, iban_key=None)
    waiting = set()
    for bill in bills:
        for tx in _paying_candidates(ctx.conn, bill):
            line = load_line(ctx.conn, tx)
            if line is not None and line.waiting and line.open_for(rematch=True):
                waiting.add(tx)
        enqueue.enqueue_rematch_on_evidence(
            ctx.conn, workspace_id=ctx.workspace_id, document_id=bill.document_id
        )
    return RecordedBillsRematch(bills=len(bills), waiting=len(waiting))


@dataclass(frozen=True)
class PrivateLinesRematch:
    accounts: int  # private accounts, a rematch_waiting job each
    waiting: int  # their lines still waiting for an answer


def rematch_private_lines(ctx: CommandContext) -> PrivateLinesRematch:
    """One-off after the deploy of D160 amended (4) (14 Nov 2026), run by the operator
    (`hausbuch rematch-waiting`): queue rematch_waiting for every private account, so its
    lines already waiting in Zu prüfen are checked once more against the learned rules and
    the matchers, which now read a payee's other spellings and a payment service's merchant.
    The job runs the deterministic order only, never the model, and never replaces a
    confirmed row (Line.open_for); a line no rule or matcher answers keeps what it has."""
    accounts = list(
        ctx.conn.execute(
            select(account_table.c.id)
            .where(account_table.c.purpose == AccountPurpose.PRIVATE.value)
            .order_by(account_table.c.id)
        ).scalars()
    )
    waiting = 0
    for account_id in accounts:
        waiting += len(waiting_lines(ctx.conn, enqueue.TRIGGER_PRIVATE_LINES, account_id))
        enqueue.enqueue_rematch_waiting(
            ctx.conn,
            workspace_id=ctx.workspace_id,
            kind=enqueue.TRIGGER_PRIVATE_LINES,
            entity_id=account_id,
        )
    return PrivateLinesRematch(accounts=len(accounts), waiting=waiting)


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
    - account: lines from an owner account's IBAN hash; none for an account that is not one;
    - private_lines: every waiting line of one private account (`hausbuch rematch-waiting`,
      D160 amended (4)); none for a rental account.
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
            if matchers.iban_only(rule.matcher) and is_shared_iban(conn, hmac):
                # D160 amended: such a rule never applies to a private line of a shared IBAN
                # (rules.matching.apply_rule), so none is re-checked; a rental line still is.
                query = query.join(account_table, account_table.c.id == t.account_id).where(
                    account_table.c.purpose != AccountPurpose.PRIVATE.value
                )
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
        # A card account's confirmed payment IBAN counts as its own (D176 (4)): confirming
        # it re-checks the debits to it that wait.
        own_iban = func.coalesce(account_table.c.iban_hmac, account_table.c.payment_iban_hmac)
        owner = conn.execute(
            select(own_iban).where(
                account_table.c.id == entity_id,
                account_table.c.purpose == AccountPurpose.PRIVATE.value,
                own_iban.is_not(None),
            )
        ).scalar_one_or_none()
        if owner is None:
            return []
        query = query.where(t.counterparty_iban_hmac == owner)
    elif kind == enqueue.TRIGGER_PRIVATE_LINES:
        private = conn.execute(
            select(account_table.c.id).where(
                account_table.c.id == entity_id,
                account_table.c.purpose == AccountPurpose.PRIVATE.value,
            )
        ).one_or_none()
        if private is None:
            return []
        query = query.where(t.account_id == entity_id)
    else:
        return []
    rows = conn.execute(query.order_by(t.booking_date, t.id)).all()
    if rule is None:
        return [r.id for r in rows]
    return [
        r.id
        for r in rows
        if matchers.rule_hits(rule.matcher, tx := match_transaction(r))
        and not matchers.service_blind(rule.matcher, tx)  # never applied (apply_rule)
    ]


WAITING_AMOUNTS_LIMIT = 5


def waiting_amount_counts(
    conn: Connection, tx: MatchTransaction, *, shared: bool = False
) -> dict[Decimal, int]:
    """D49: how many lines still wait from the line's IBAN hash and sign, by amount, this
    line's own amount included and the line itself excluded (waiting as in waiting_lines: a
    reconciled import, not excluded, no confirmed row). Empty for a line without an IBAN
    hash. `shared` (D160 amended: the line's IBAN is shared): only the lines of the line's
    own payee count (rules.matching.rule_name_hits, the lines the line's name rule would
    book), and so for a payment service's line (D160 amended (4)); empty for such a line
    without a payee. Uncapped: the panel says how many lines a rule
    would book (14 Nov 2026: 111 waiting grocery lines of one payee, nearly every one a
    different amount, showed as five amounts of 1x or 2x)."""
    if tx.counterparty_iban_hmac is None or tx.amount == 0:
        return {}
    shared = shared or _service_line(tx)
    if shared and matchers.payee(tx.counterparty_name, tx.purpose).name_pattern is None:
        return {}
    t = transaction_table.c
    a = allocation_table.c
    confirmed = exists().where(
        a.transaction_id == t.id, a.status == AllocationStatus.CONFIRMED.value
    )
    waiting = (
        select(t.amount, t.counterparty_name, t.purpose)
        .join(import_table, import_table.c.id == t.import_id)
        .where(
            import_table.c.status == ImportStatus.RECONCILED.value,
            t.excluded_as_duplicate_of.is_(None),
            ~confirmed,
            t.counterparty_iban_hmac == bytes.fromhex(tx.counterparty_iban_hmac),
            t.amount > 0 if tx.amount > 0 else t.amount < 0,
            t.id != tx.id,
        )
    )
    if shared:
        counts: dict[Decimal, int] = {}
        for row in conn.execute(waiting).all():
            if matchers.rule_name_hits(tx, row.counterparty_name, row.purpose):
                counts[row.amount] = counts.get(row.amount, 0) + 1
        return counts
    sub = waiting.subquery()
    rows = conn.execute(
        select(sub.c.amount, func.count().label("count")).group_by(sub.c.amount)
    ).all()
    return {r.amount: r.count for r in rows}


def other_amounts(counts: dict[Decimal, int], amount: Decimal) -> list[WaitingAmount]:
    """The waiting amounts other than `amount`, each with its count, most frequent first
    (then the larger amount), at most five."""
    ranked = sorted(
        ((value, n) for value, n in counts.items() if value != amount),
        key=lambda item: (-item[1], -abs(item[0])),
    )
    return [WaitingAmount(amount=value, count=n) for value, n in ranked][:WAITING_AMOUNTS_LIMIT]


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
    hash, else by name), as (name, categories). On a shared IBAN (D160 amended) by IBAN hash
    and payee (rules.matching.same_payee), so another payee's answer is never shown as this
    one's; none for such a line without a name."""
    t = transaction_table.c
    shared = is_shared_iban(conn, line.counterparty_iban_hmac) or _service_line(line.tx)
    if shared and matchers.payee(line.tx.counterparty_name, line.tx.purpose).name_pattern is None:
        return []
    if line.counterparty_iban_hmac is not None:
        same = t.counterparty_iban_hmac == line.counterparty_iban_hmac
    elif line.tx.counterparty_name:
        same = func.lower(t.counterparty_name) == line.tx.counterparty_name.lower()
    else:
        return []
    rows = conn.execute(
        select(t.id, t.counterparty_name, t.purpose, allocation_table.c.category)
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
        if shared and not matchers.same_payee(
            line.tx.counterparty_name, row.counterparty_name, line.tx.purpose, row.purpose
        ):
            continue
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
