"""Deterministic matchers, the document match and learned rules (§8.1, D25, D27, D35;
tests/fixtures/phase2/matchers.yaml, rules.yaml, repeat_answer.yaml, month_statement.yaml).

Pure: no model, no database. Counterparties are compared by IBAN HMAC only; a matcher never
sees a plaintext IBAN. Every set has transaction_id = the line's id, allocation amounts signed
like the transaction and summing to it exactly (the caller still runs allocations.check_sum on
every write: the validator enforces the sum, not the matcher), the service period from
periods.service_period, and no tax year (D31). Lists in the context are unordered, so a
result never depends on their order: when more than one candidate fits, nothing is matched
and the line goes on to the model and review.

Matchers (confidence 1.0 when exact):
  owner transfer  counterparty HMAC is an owner account -> owner_transfer_in / _out.
  rent            counterparty HMAC is a tenant's; the rent in force on the period's start,
                  times the period's whole months, split into rent_cold and the prepayment.
                  A credit within 10 % of it -> the same split with the prepayment in full,
                  confidence 0.5 and the difference as the question.
  loan            the loan's account HMAC, or its lender in the counterparty name; the
                  schedule entry due nearest the booking date within 10 days (none, or a tie
                  -> no match, D39) and -amount equal to its payment -> loan_interest /
                  loan_principal, period = the entry's due month.
  document        an invoice or Bescheid payment within 0.01 and 10 days, found by IBAN HMAC or
                  reference -> the document's category, the document as Beleg, its property
                  and loan (a financing invoice is loan_fees on its loan, D35); 0.95.
Learned rules store a matcher and a split method, never an amount (D25): a hit re-splits the
payment from the current schedule or Wirtschaftsplan, and a split that does not fit exactly
returns None rather than splitting silently.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
    DocumentFacts,
    LoanFacts,
    MatchContext,
    MatchingRule,
    MatchingRuleCreate,
    MatchTransaction,
    RentInForce,
    RuleMatcher,
    ServicePeriod,
    SplitMethod,
    TenancyFacts,
    WirtschaftsplanFacts,
)
from hausbuch.schemas.loan import ScheduleEntry

from .loan_schedule import build_schedule
from .periods import months_in, service_period
from .statements import norm

EXACT = Decimal("1.000")
DOCUMENT_CONFIDENCE = Decimal("0.950")
INEXACT_RENT_CONFIDENCE = Decimal("0.500")  # below every threshold: always reviewed
RENT_BAND = Decimal("0.10")  # a credit this close to the rent is a short or over payment
DOCUMENT_AMOUNT_TOLERANCE = Decimal("0.01")
DOCUMENT_WINDOW_DAYS = 10
CENT = Decimal("0.01")
HAUSGELD_PATTERN = "hausgeld"  # an inferred Wirtschaftsplan rule never splits a special levy

C = AllocationCategory
_HAUSGELD = (C.HAUSGELD_UMLAGEFAEHIG, C.HAUSGELD_VERWALTUNG, C.HAUSGELD_RUECKLAGE)
_LOAN = (C.LOAN_INTEREST, C.LOAN_PRINCIPAL)
_RENT = (C.RENT_COLD, C.OPERATING_COSTS_PREPAYMENT)
_OWNER = (C.OWNER_TRANSFER_IN, C.OWNER_TRANSFER_OUT)


@dataclass(frozen=True)
class _Split:
    allocations: list[AllocationDraft]
    reasoning: str
    confidence: Decimal = EXACT
    question: str | None = None


# --- Matchers ------------------------------------------------------------------------


def match_owner_transfer(tx: MatchTransaction, context: MatchContext) -> AllocationSet | None:
    if tx.counterparty_iban_hmac is None:
        return None
    if tx.counterparty_iban_hmac not in context.owner_account_hmacs:
        return None
    return _as_set(tx, _owner_split(tx), AllocationMethod.MATCHER_OWNER)


def match_rent(tx: MatchTransaction, context: MatchContext) -> AllocationSet | None:
    if tx.counterparty_iban_hmac is None:
        return None
    splits = [
        split
        for tenancy in context.tenancies
        if tx.counterparty_iban_hmac in tenancy.tenant_iban_hmacs
        if (split := _rent_split(tx, tenancy, exact_only=False)) is not None
    ]
    exact = [s for s in splits if s.confidence == EXACT]
    found = _only(exact) if exact else _only(splits)
    return _as_set(tx, found, AllocationMethod.MATCHER_RENT)


def match_loan(tx: MatchTransaction, context: MatchContext) -> AllocationSet | None:
    splits = [
        split
        for loan in context.loans
        if _is_lender(tx, loan)
        if (split := _loan_split(tx, loan)) is not None
    ]
    return _as_set(tx, _only(splits), AllocationMethod.MATCHER_LOAN)


def match_document(tx: MatchTransaction, context: MatchContext) -> AllocationSet | None:
    hits = {doc.id: doc for doc in context.documents if _document_hit(tx, doc)}
    if len(hits) != 1:
        return None
    (doc,) = hits.values()
    recurring = len(doc.payments) > 1  # a Bescheid's installments
    draft = _draft(
        tx.amount,
        doc.category,
        service_period(tx.purpose, tx.booking_date, recurring),
        recurring,
        property_id=doc.property_id,
        loan_id=doc.loan_id,
        document_id=doc.id,
    )
    split = _Split(
        [draft],
        "Betrag, Datum und IBAN oder Referenz passen zu einem Beleg.",
        confidence=DOCUMENT_CONFIDENCE,
    )
    return _as_set(tx, split, AllocationMethod.DOCUMENT_MATCH)


# --- Learned rules -------------------------------------------------------------------


def apply_rule(
    rule: MatchingRule, tx: MatchTransaction, context: MatchContext
) -> AllocationSet | None:
    """The rule's set for the line, or None when the rule is disabled, misses, or its split
    method does not fit the line exactly (it is then left to the matchers and review)."""
    if not rule.enabled or not rule_hits(rule.matcher, tx):
        return None
    split = _RULE_SPLITS[rule.split_method](rule, tx, context)
    if split is None or split.confidence != EXACT:
        return None
    return _as_set(tx, split, AllocationMethod.RULE, rule_id=rule.id)


def infer_rule(
    tx: MatchTransaction, confirmed: AllocationSet, context: MatchContext
) -> MatchingRuleCreate | None:
    """The rule a confirmed answer becomes ("Künftig immer so zuordnen"): the line's IBAN HMAC
    and amount sign, and a split method read from the confirmed allocations, never their
    amounts (D25). None when the line has no IBAN (the user then types a pattern), the split
    fits no method, or a single category is answered for a counterparty a matcher owns (an
    owner account, a tenant's or a loan account: D38). Rules run before the matchers, so an
    IBAN-only rule there would pre-empt the exact rent / loan / owner split of every later
    line."""
    if tx.counterparty_iban_hmac is None or tx.amount == 0:
        return None
    allocs = confirmed.allocations
    sign = "credit" if tx.amount > 0 else "debit"
    matcher = RuleMatcher(counterparty_iban_hmac=tx.counterparty_iban_hmac, amount_sign=sign)
    recurring = all(a.recurring for a in allocs)
    categories = sorted(a.category for a in allocs)

    def rule(split_method: SplitMethod, **fields: object) -> MatchingRuleCreate:
        return MatchingRuleCreate.model_validate(
            {"matcher": matcher, "split_method": split_method, "recurring": recurring, **fields}
        )

    if categories == sorted(_HAUSGELD):
        property_id = _one({a.property_id for a in allocs})
        plan = _plan_in_force(tx, property_id, context) if property_id else None
        if plan is None or _parts(allocs) != _parts(_hausgeld_drafts(tx, plan)):
            return None
        matcher = matcher.model_copy(update={"purpose_pattern": HAUSGELD_PATTERN})
        return rule(SplitMethod.BY_WIRTSCHAFTSPLAN, property_id=property_id)
    if categories == sorted(_LOAN):
        loan = _by_id(context.loans, _one({a.loan_id for a in allocs}))
        split = _loan_split(tx, loan) if loan else None
        if split is None or _parts(allocs) != _parts(split.allocations):
            return None
        return rule(SplitMethod.BY_LOAN_SCHEDULE, loan_id=loan.id)  # type: ignore[union-attr]
    if categories == sorted(_RENT):
        tenancy_id = _one({a.tenancy_id for a in allocs})
        return rule(SplitMethod.BY_RENT_SCHEDULE, tenancy_id=tenancy_id) if tenancy_id else None
    if len(allocs) != 1:
        return None
    (only,) = allocs
    if only.category in _OWNER:
        return rule(SplitMethod.OWNER_TRANSFER)
    if matcher_owns(tx.counterparty_iban_hmac, context):
        return None
    return rule(
        SplitMethod.SINGLE_CATEGORY,
        category=only.category,
        property_id=only.property_id,
        tenancy_id=only.tenancy_id,
        loan_id=only.loan_id,
    )


def _single_category(
    rule: MatchingRule, tx: MatchTransaction, context: MatchContext
) -> _Split | None:
    draft = _draft(
        tx.amount,
        rule.category,  # type: ignore[arg-type]  # set for single_category (schema)
        service_period(tx.purpose, tx.booking_date, rule.recurring),
        rule.recurring,
        property_id=rule.property_id,
        tenancy_id=rule.tenancy_id,
        loan_id=rule.loan_id,
    )
    return _Split([draft], "Gelernte Regel.")


def _by_rent_schedule(
    rule: MatchingRule, tx: MatchTransaction, context: MatchContext
) -> _Split | None:
    tenancy = _by_id(context.tenancies, rule.tenancy_id)
    return _rent_split(tx, tenancy, exact_only=True) if tenancy else None


def _by_loan_schedule(
    rule: MatchingRule, tx: MatchTransaction, context: MatchContext
) -> _Split | None:
    loan = _by_id(context.loans, rule.loan_id)
    return _loan_split(tx, loan) if loan else None


def _by_wirtschaftsplan(
    rule: MatchingRule, tx: MatchTransaction, context: MatchContext
) -> _Split | None:
    plan = _plan_in_force(tx, rule.property_id, context) if rule.property_id else None
    if plan is None or tx.amount != -plan.total:
        return None
    return _Split(_hausgeld_drafts(tx, plan), "Hausgeld, aufgeteilt nach dem Wirtschaftsplan.")


def _owner_transfer(
    rule: MatchingRule, tx: MatchTransaction, context: MatchContext
) -> _Split | None:
    return _owner_split(tx) if tx.amount != 0 else None


_RULE_SPLITS: dict[
    SplitMethod, Callable[[MatchingRule, MatchTransaction, MatchContext], _Split | None]
] = {
    SplitMethod.SINGLE_CATEGORY: _single_category,
    SplitMethod.BY_RENT_SCHEDULE: _by_rent_schedule,
    SplitMethod.BY_LOAN_SCHEDULE: _by_loan_schedule,
    SplitMethod.BY_WIRTSCHAFTSPLAN: _by_wirtschaftsplan,
    SplitMethod.OWNER_TRANSFER: _owner_transfer,
}


# --- Splits ---------------------------------------------------------------------------


def _owner_split(tx: MatchTransaction) -> _Split:
    category = C.OWNER_TRANSFER_IN if tx.amount > 0 else C.OWNER_TRANSFER_OUT
    period = service_period(tx.purpose, tx.booking_date, False)
    return _Split(
        [_draft(tx.amount, category, period, False)], "Überweisung mit einem Konto der Eigentümer."
    )


def _rent_split(tx: MatchTransaction, tenancy: TenancyFacts, *, exact_only: bool) -> _Split | None:
    if tx.amount <= 0:
        return None
    period = service_period(tx.purpose, tx.booking_date, True)
    months = months_in(period) if period else None
    if period is None or months is None or not _tenancy_runs(tenancy, period.start):
        return None
    rent = _rent_in_force(tenancy.rent, period.start)
    if rent is None:
        return None
    prepayment = months * rent.operating_costs_prepayment
    expected = months * rent.cold_rent + prepayment
    if expected <= 0:
        return None
    if tx.amount == expected:
        confidence, question = EXACT, None
    elif not exact_only and abs(tx.amount - expected) <= expected * RENT_BAND:
        confidence = INEXACT_RENT_CONFIDENCE
        question = (
            f"Mieter hat {_eur(tx.amount)} statt {_eur(expected)} überwiesen. "
            "Mietminderung oder Teilzahlung?"
        )
    else:
        return None
    scope = {"property_id": tenancy.property_id, "unit_id": tenancy.unit_id}
    drafts = [
        _draft(tx.amount - prepayment, C.RENT_COLD, period, True, tenancy_id=tenancy.id, **scope),
        _draft(
            prepayment, C.OPERATING_COSTS_PREPAYMENT, period, True, tenancy_id=tenancy.id, **scope
        ),
    ]
    return _Split(_nonzero(drafts), "Miete vom Mieterkonto, laut Mietplan.", confidence, question)


def _loan_split(tx: MatchTransaction, loan: LoanFacts) -> _Split | None:
    if tx.amount >= 0:
        return None
    entry = _entry_nearest(build_schedule(loan.terms).entries, tx.booking_date)
    # The entry's payment is terms.monthly_payment for every entry but the final one, whose
    # payment is the remaining balance + interest; matching on it keeps the split exact.
    if entry is None or -tx.amount != entry.payment:
        return None
    # The period is the entry's month, not the booking month: a bank may book on 30 June
    # for the instalment due 1 July (D39).
    due = entry.due_on
    period = ServicePeriod(start=due.replace(day=1), end=_month_end(due))
    scope = {"property_id": loan.property_id, "loan_id": loan.id}
    drafts = [
        _draft(-entry.interest, C.LOAN_INTEREST, period, True, **scope),
        _draft(-entry.principal, C.LOAN_PRINCIPAL, period, True, **scope),
    ]
    return _Split(_nonzero(drafts), f"Darlehensrate {entry.number}, laut Tilgungsplan.")


def _hausgeld_drafts(tx: MatchTransaction, plan: WirtschaftsplanFacts) -> list[AllocationDraft]:
    period = service_period(tx.purpose, tx.booking_date, True)
    scope = {"property_id": plan.property_id, "document_id": plan.document_id}
    parts = (plan.umlagefaehig, plan.verwaltung, plan.ruecklage)
    return _nonzero(
        [
            _draft(-amount, category, period, True, **scope)
            for category, amount in zip(_HAUSGELD, parts, strict=True)
        ]
    )


# --- Helpers -------------------------------------------------------------------------


def _as_set(
    tx: MatchTransaction,
    split: _Split | None,
    method: AllocationMethod,
    *,
    rule_id: UUID | None = None,
) -> AllocationSet | None:
    if split is None:
        return None
    return AllocationSet(
        transaction_id=tx.id,
        allocations=split.allocations,
        method=method,
        confidence=split.confidence,
        reasoning=split.reasoning,
        question=split.question,
        rule_id=rule_id,
    )


def _draft(
    amount: Decimal,
    category: AllocationCategory,
    period: ServicePeriod | None,
    recurring: bool,
    **scope: object,
) -> AllocationDraft:
    return AllocationDraft.model_validate(
        {
            "amount": amount.quantize(CENT),
            "category": category,
            "service_period_start": period.start if period else None,
            "service_period_end": period.end if period else None,
            "recurring": recurring,
            **scope,
        }
    )


def _nonzero(drafts: list[AllocationDraft]) -> list[AllocationDraft]:
    """Drop parts of 0.00 (a plan without Rücklage, an interest-free month), keeping one."""
    kept = [d for d in drafts if d.amount != 0]
    return kept or drafts[:1]


def _parts(drafts: Sequence[AllocationDraft]) -> list[tuple[AllocationCategory, Decimal]]:
    return sorted((d.category, d.amount) for d in drafts if d.amount != 0)


def _only[T](items: Sequence[T]) -> T | None:
    return items[0] if len(items) == 1 else None


def _one[T](values: set[T | None]) -> T | None:
    """The one value all allocations share, or None when they differ or it is unset."""
    return next(iter(values)) if len(values) == 1 else None


def _by_id[T: (TenancyFacts, LoanFacts)](items: Sequence[T], id: object) -> T | None:
    return next((item for item in items if item.id == id), None) if id is not None else None


def rule_hits(matcher: RuleMatcher, tx: MatchTransaction) -> bool:
    """Whether the line is one the matcher names (IBAN hash, sign, patterns); the split is
    apply_rule's. Also used to pick the waiting lines a new rule may book (rematch_on_rule)."""
    if not matcher.matches_something:  # an erased rule's empty matcher hits nothing
        return False
    wanted = matcher.counterparty_iban_hmac
    if wanted is not None and tx.counterparty_iban_hmac != wanted:
        return False
    if matcher.amount_sign == "credit" and not tx.amount > 0:
        return False
    if matcher.amount_sign == "debit" and not tx.amount < 0:
        return False
    for pattern, text in (
        (matcher.counterparty_name_pattern, tx.counterparty_name),
        (matcher.purpose_pattern, tx.purpose),
    ):
        if pattern is not None and not _found(pattern, text):
            return False
    return True


def _found(pattern: str, text: str | None) -> bool:
    if text is None:
        return False
    try:
        return re.search(pattern, " ".join(text.split()), re.IGNORECASE) is not None
    except re.error:
        return False  # a broken pattern never hits; the line goes to review


def _is_lender(tx: MatchTransaction, loan: LoanFacts) -> bool:
    if (
        tx.counterparty_iban_hmac is not None
        and tx.counterparty_iban_hmac == loan.account_iban_hmac
    ):
        return True
    lender = norm(loan.lender)
    return bool(lender) and lender in norm(tx.counterparty_name)


def _tenancy_runs(tenancy: TenancyFacts, on: date) -> bool:
    return tenancy.start_date <= on and (tenancy.end_date is None or on <= tenancy.end_date)


def _rent_in_force(rows: Sequence[RentInForce], on: date) -> RentInForce | None:
    """The latest row with effective_from <= on. rules.rent.rent_at needs stored rows (id,
    recorded_at, supersedes_id); these come with corrections already resolved."""
    rows = [r for r in rows if r.effective_from <= on]
    if not rows:
        return None
    latest = max(r.effective_from for r in rows)
    same_day = [r for r in rows if r.effective_from == latest]
    return same_day[0] if len(same_day) == 1 else None


def _plan_in_force(
    tx: MatchTransaction, property_id: object, context: MatchContext
) -> WirtschaftsplanFacts | None:
    period = service_period(tx.purpose, tx.booking_date, True)
    if period is None:
        return None
    plans = [
        p
        for p in context.wirtschaftsplaene
        if p.property_id == property_id and p.valid_from <= period.start
    ]
    if not plans:
        return None
    latest = max(p.valid_from for p in plans)
    same_day = [p for p in plans if p.valid_from == latest]
    return same_day[0] if len(same_day) == 1 else None


LOAN_WINDOW_DAYS = 10


def _entry_nearest(entries: Sequence[ScheduleEntry], on: date) -> ScheduleEntry | None:
    """The schedule entry whose due date is nearest the booking date, within 10 days either
    side (D39); None when none is that near or two are equally near."""
    near = [e for e in entries if abs((e.due_on - on).days) <= LOAN_WINDOW_DAYS]
    if not near:
        return None
    best = min(abs((e.due_on - on).days) for e in near)
    return _only([e for e in near if abs((e.due_on - on).days) == best])


def _month_end(day: date) -> date:
    following = date(day.year + day.month // 12, day.month % 12 + 1, 1)
    return date.fromordinal(following.toordinal() - 1)


def matcher_owns(hmac: str, context: MatchContext) -> bool:
    """Whether a deterministic matcher owns this counterparty (D38)."""
    return (
        hmac in context.owner_account_hmacs
        or any(hmac in t.tenant_iban_hmacs for t in context.tenancies)
        or any(hmac == loan.account_iban_hmac for loan in context.loans)
    )


def _document_hit(tx: MatchTransaction, doc: DocumentFacts) -> bool:
    paid = abs(tx.amount)
    in_window = any(
        abs(paid - p.amount) <= DOCUMENT_AMOUNT_TOLERANCE
        and abs((tx.booking_date - p.due_on).days) <= DOCUMENT_WINDOW_DAYS
        for p in doc.payments
    )
    if not in_window:
        return False
    if doc.iban_hmac is not None and doc.iban_hmac == tx.counterparty_iban_hmac:
        return True
    return _reference_in(doc.reference, tx.purpose)


def _reference_in(reference: str | None, purpose: str) -> bool:
    """The reference, case-insensitive and whitespace-collapsed, as a whole token of the
    purpose (R 8817 is not found in R 88171)."""
    ref = norm(reference)
    if not ref:
        return False
    return re.search(rf"(?<![0-9a-z]){re.escape(ref)}(?![0-9a-z])", norm(purpose)) is not None


def _eur(amount: Decimal) -> str:
    """German form, cents only when not .00: 1.380 €, 1.380,50 €."""
    value = amount.quantize(CENT)
    whole, cents = f"{abs(value):.2f}".split(".")
    text = f"{int(whole):,}".replace(",", ".")
    if cents != "00":
        text += f",{cents}"
    return f"{'-' if value < 0 else ''}{text} €"
