"""Matching a transaction (docs/phases/phase-2.md §8.1, §5.3, D25, D26, D35, D37-D39).

The pure rules (rules.matching, rules.review) decide; this module reads their inputs from the
database and writes their outcome through services.allocations.

Order (§8.1, cheapest first): learned rules (`apply_rule`), the deterministic matchers (owner
transfer, rent, loan), the document match, and only then the model (jobs.matching, which calls
back into `finish_line`). `rules.review.route` decides auto-confirm or review; the workspace's
`auto_accept_llm` (default false, D26) is passed through.

A line is matched only when it is *open*:
- its import is reconciled (§3.4: lines of an unreconciled import never get allocations);
- it is not excluded as a duplicate, nor the later line of an open possible duplicate (the
  pair is decided first; kept_both enqueues it again);
- it has no current allocation rows. A re-match on new evidence (`rematch=True`, D23) also
  takes a line whose rows are proposed, or confirmed by a learned rule; it replaces them only
  with a different set, and replaces confirmed rule rows only with a set that auto-confirms.
  A person's confirmation is never re-matched.

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
- a Grundsteuerbescheid pays its installment on each stated due date, every year from its
  tax_year until the next Bescheid of the property (or next year); no due dates -> no facts;
- a one-payment document that already backs a confirmed allocation of another line is left
  out, so the next equal payment does not match the same Beleg;
- the document's IBAN is hashed here (the extraction holds it in plain text, the matchers
  compare hashes only).
"""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import Connection, Row, and_, func, or_, select

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
from hausbuch.rules import matching as matchers
from hausbuch.rules.errors import RuleError
from hausbuch.rules.iban import iban_hmac, normalize_iban
from hausbuch.rules.periods import service_period
from hausbuch.rules.prefill import names_financing
from hausbuch.rules.review import route
from hausbuch.schemas.allocation import (
    Allocation,
    AllocationCategory,
    AllocationMethod,
    AllocationSet,
    DocumentFacts,
    DuePayment,
    LoanFacts,
    MatchContext,
    MatchingRule,
    MatchTransaction,
    RentInForce,
    RoutingDecision,
    RoutingInput,
    TenancyFacts,
    WirtschaftsplanFacts,
)
from hausbuch.schemas.document import DocumentExtraction
from hausbuch.schemas.enums import (
    AllocationStatus,
    DocumentSubtype,
    DuplicateStatus,
    ExtractionKind,
    ImportStatus,
)
from hausbuch.schemas.loan import LoanTerms

from . import allocations, rules_learned
from .context import CommandContext
from .documents import DOCUMENT_EVIDENCE_KINDS, EVIDENCE_KINDS, current_extraction
from .tenancies import _rows_in_force, rent_on  # rent rows no correction supersedes

C = AllocationCategory

DOCUMENT_KINDS = DOCUMENT_EVIDENCE_KINDS

GUARDED_CONFIDENCE = Decimal("0.500")  # below every threshold: the set goes to review
RENT_BOOKED_QUESTION = (
    "Für diesen Zeitraum ist schon eine Miete gebucht. Ist das eine Nachzahlung, eine "
    "Vorauszahlung oder die Miete eines anderen Monats?"
)
LOAN_BOOKED_QUESTION = (
    "Für diese Darlehensrate ist schon eine Zahlung gebucht. Ist das eine Sondertilgung "
    "oder eine verschobene Rate?"
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


def load_line(conn: Connection, transaction_id: UUID, *, lock: bool = False) -> Line | None:
    """The line as matching sees it. With `lock`, the transaction row is locked (FOR UPDATE)
    for the rest of the caller's transaction, so a job and a person answering the same line
    decide one after the other, the second on the first one's rows."""
    query = (
        select(
            transaction_table,
            import_table.c.status.label("import_status"),
        )
        .join(import_table, import_table.c.id == transaction_table.c.import_id)
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
    return Line(
        tx=match_transaction(row),
        import_status=ImportStatus(row.import_status),
        excluded=row.excluded_as_duplicate_of is not None,
        awaiting_duplicate=awaiting > 0,
        current=allocations.current_allocations(conn, transaction_id),
        auto_accept_llm=auto_accept_llm(conn),
        counterparty_iban_hmac=row.counterparty_iban_hmac,
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


# --- The context -------------------------------------------------------------------------


def build_context(
    conn: Connection, *, iban_key: bytes, today: date, for_transaction: UUID | None = None
) -> MatchContext:
    """Everything the matchers may look at, read from the workspace. `for_transaction`: the
    line being matched (a Beleg its own rows reference stays a candidate)."""
    owners = conn.execute(
        select(account_table.c.iban_hmac).where(account_table.c.is_owner_account.is_(True))
    ).scalars()
    plans, documents = _evidence(conn, iban_key=iban_key, today=today, exclude_for=for_transaction)
    return MatchContext(
        owner_account_hmacs=sorted({bytes(h).hex() for h in owners}),
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
    property_id = e.property_id
    if loan_id is not None and property_id is None:
        property_id = next(p for p, ids in loans_by_property.items() if loan_id in ids)
    return DocumentFacts(
        id=e.row.id,
        kind=x.kind,
        category=category,
        property_id=property_id,
        loan_id=loan_id,
        payments=[DuePayment(due_on=paid_on, amount=amount)],
        iban_hmac=_hmac(_value(x, "iban"), iban_key),
        reference=_value(x, "reference") or None,
    )


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
    """Learned rules, then the matchers, then the document match (§8.1), with the
    service-side guards (F3, D39) applied."""
    found = _rule_set(rules_learned.enabled_rules(conn), tx, context)
    if found is None:
        for matcher in (
            matchers.match_owner_transfer,
            matchers.match_rent,
            matchers.match_loan,
            matchers.match_document,
        ):
            found = matcher(tx, context)
            if found is not None:
                break
    return None if found is None else _guarded(conn, found)


def _rule_set(
    rules: list[MatchingRule], tx: MatchTransaction, context: MatchContext
) -> AllocationSet | None:
    """The rules' answer: the oldest hit when every hit gives the same split; none when two
    rules disagree (the line goes on to the matchers and review)."""
    hits = [s for rule in rules if (s := matchers.apply_rule(rule, tx, context)) is not None]
    if not hits:
        return None
    first = hits[0]
    if any(not _same(first, other) for other in hits[1:]):
        return None
    return first


def _same(a: AllocationSet, b: AllocationSet) -> bool:
    return sorted(d.model_dump_json() for d in a.allocations) == sorted(
        d.model_dump_json() for d in b.allocations
    )


def _guarded(conn: Connection, found: AllocationSet) -> AllocationSet:
    if found.method is AllocationMethod.USER:
        return found
    tx_id = found.transaction_id
    for d in found.allocations:
        if d.service_period_start is None:
            continue
        rent = d.category is C.RENT_COLD and d.tenancy_id is not None
        if rent and _booked(conn, tx_id, d, [C.RENT_COLD], tenancy_id=d.tenancy_id):
            return _to_review(found, RENT_BOOKED_QUESTION)
        loan = d.category in _LOAN and d.loan_id is not None
        if loan and _booked(conn, tx_id, d, list(_LOAN), loan_id=d.loan_id):
            return _to_review(found, LOAN_BOOKED_QUESTION)
    return found


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
        ctx.conn, iban_key=iban_key, today=ctx.now.date(), for_transaction=transaction_id
    )
    found = deterministic_set(ctx.conn, line.tx, context)
    if found is None:
        if rematch or not ai_available:
            return MatchPlan("skipped", "routing.no_match")
        return MatchPlan("categorize", "routing.no_match")
    decision = _route(line, found)
    if decision is not RoutingDecision.AUTO_CONFIRM:
        if any(a.status is AllocationStatus.CONFIRMED for a in line.current):
            return MatchPlan("skipped", "matching.keeps_confirmed")
        if found.method in _MATCHERS and ai_available and not rematch:
            return MatchPlan("explain", "routing.below_threshold", allocation_set=found)
    return write(ctx, line, found)


def write(ctx: CommandContext, line: Line, found: AllocationSet) -> MatchPlan:
    """Route the set and write it, unless the line already holds the same answer."""
    if line.current and allocations.same_answer(found.allocations, line.current):
        return MatchPlan("skipped", "matching.unchanged")
    decision = _route(line, found)
    auto = decision is RoutingDecision.AUTO_CONFIRM
    written = allocations.record_set(ctx, found, confirm=auto, auto=auto)
    if found.rule_id is not None:
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
    return write(ctx, line, found)


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
    """The newest confirmed answers for the same counterparty (by IBAN hash, else by name)."""
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
        .where(
            same,
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


def llm_period(text: str | None, tx: MatchTransaction, recurring: bool):  # noqa: ANN201
    """The model's service period as text, read by the same rule as a purpose (D27); the
    line's own purpose when the model's text says nothing parsable."""
    return service_period(text or "", tx.booking_date, False) or service_period(
        tx.purpose, tx.booking_date, recurring
    )
