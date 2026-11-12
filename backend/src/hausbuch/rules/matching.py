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
  loan, interest  for a loan with interest_only_before_first_payment, when no entry fits:
  only            a debit booked after disbursed_on and before first_payment_on whose
                  purpose states "Zinsen <amount>" equal to it (one distinct amount) and no
                  Tilgung above zero -> one loan_interest of the whole amount, period = the
                  stated "Für dd.mm.yyyy - dd.mm.yyyy" range, else the booking month.
  document        an invoice or Bescheid payment within 0.01 and 10 days, found by IBAN HMAC or
                  reference -> the document's category, the document as Beleg, its property
                  and loan (a financing invoice is loan_fees on its loan, D35); 0.95.
Learned rules store a matcher and a split method, never an amount (D25): a hit re-splits the
payment from the current schedule or Wirtschaftsplan, and a split that does not fit exactly
returns None rather than splitting silently. Which hits stand is rule_answer's (D49,
rule_history.yaml): on a counterparty whose confirmed lines show more than one kind of
payment, or for a rule learned same_amount_only, a hit whose split method does not check the
amount itself stands only at the latest amount of its kind, read from the ledger's history.
An as_last_split rule (D51) copies the parts of the latest confirmed line of its category set
from that history when the amount is the same; it never computes a part, so any other amount
is no hit (and never a near-miss proposal).
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
    HistoryLine,
    HistoryRow,
    LoanFacts,
    MatchContext,
    MatchingRule,
    MatchingRuleCreate,
    MatchTransaction,
    NameHistory,
    RentInForce,
    RuleMatcher,
    ServicePeriod,
    SplitMethod,
    TenancyFacts,
    WirtschaftsplanFacts,
)
from hausbuch.schemas.enums import AccountPurpose, AllocationStatus
from hausbuch.schemas.loan import ScheduleEntry

from .categories import CATEGORY_LABEL
from .errors import RULE_CONFLICTS, RULE_TOO_BROAD, RuleError
from .household.taxonomy import HOUSEHOLD_CODES, TAX_RELEVANT, allowed_categories
from .loan_schedule import build_schedule
from .periods import months_in, service_period
from .review import THRESHOLDS
from .statements import norm

EXACT = Decimal("1.000")
DOCUMENT_CONFIDENCE = Decimal("0.950")
INEXACT_RENT_CONFIDENCE = Decimal("0.500")  # below every threshold: always reviewed
GUARDED_CONFIDENCE = Decimal("0.500")  # below every threshold: the set goes to review
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
    if tx.counterparty_iban_hmac not in context.own_account_hmacs:
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
    # A line from a loan's own account belongs to that loan: the lender-name fallback only
    # applies when the account is no loan's, so two loans at one bank never claim each
    # other's debits (review F1, PR #64).
    own = [
        loan
        for loan in context.loans
        if tx.counterparty_iban_hmac is not None
        and tx.counterparty_iban_hmac == loan.account_iban_hmac
    ]
    splits = [
        split
        for loan in own or context.loans
        if _is_lender(tx, loan)
        if (split := _loan_split(tx, loan) or _interest_only_split(tx, loan)) is not None
    ]
    return _as_set(tx, _only(splits), AllocationMethod.MATCHER_LOAN)


def match_document(tx: MatchTransaction, context: MatchContext) -> AllocationSet | None:
    hits = {doc.id: doc for doc in context.documents if document_hit(tx, doc)}
    if len(hits) != 1:
        return None
    (doc,) = hits.values()
    recurring = len(doc.payments) > 1  # a Bescheid's installments
    if doc.parts and (len(doc.parts) > 1 or doc.parts[0].category != doc.category):
        return _as_set(tx, _bill_split(tx, doc), AllocationMethod.DOCUMENT_MATCH)
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


def _bill_split(tx: MatchTransaction, doc: DocumentFacts) -> _Split:
    """A bill sorted by its cost lines (phase 2 D135): one part per category, in the
    bill's order, signed as the line; the last part takes the line's amount less the others,
    so the parts sum to it exactly. Always to review (GUARDED_CONFIDENCE and a question):
    a line's category may be the model's choice, and the model never books a split."""
    sign = -1 if tx.amount < 0 else 1
    period = service_period(tx.purpose, tx.booking_date, False)
    drafts = []
    for i, part in enumerate(doc.parts):
        last = i == len(doc.parts) - 1
        others = sum((d.amount for d in drafts), Decimal(0))
        amount = tx.amount - others if last else sign * part.amount
        drafts.append(
            _draft(
                amount,
                part.category,
                period,
                False,
                property_id=doc.property_id,
                loan_id=doc.loan_id if part.category is AllocationCategory.LOAN_FEES else None,
                document_id=doc.id,
            )
        )
    names = ", ".join(f"{CATEGORY_LABEL[p.category]} {_eur(p.amount)}" for p in doc.parts)
    return _Split(
        _nonzero(drafts),
        f"Betrag, Datum und IBAN oder Referenz passen zu einem Beleg; nach seinen Positionen "
        f"aufgeteilt: {names}.",
        confidence=GUARDED_CONFIDENCE,
        question="Stimmt die Aufteilung nach den Positionen der Rechnung?",
    )


# --- Learned rules -------------------------------------------------------------------


def apply_rule(
    rule: MatchingRule,
    tx: MatchTransaction,
    context: MatchContext,
    history: Sequence[HistoryLine] = (),
) -> AllocationSet | None:
    """The rule's set for the line, or None when the rule is disabled, misses, or its split
    method does not fit the line exactly (it is then left to the matchers and review).
    `history` (as for rule_answer) is read by as_last_split only, which copies from it."""
    if not rule.enabled or not rule_hits(rule.matcher, tx):
        return None
    if rule.split_method is SplitMethod.AS_LAST_SPLIT:
        split = _as_last_split(rule, tx, history)
    else:
        split = _RULE_SPLITS[rule.split_method](rule, tx, context)
    if split is None or split.confidence != EXACT:
        return None
    return _as_set(tx, split, AllocationMethod.RULE, rule_id=rule.id)


# Split methods that check the amount against a record (apply_rule returns None for any
# other amount), so their hits stand whatever the history says (D49). as_last_split checks it
# against the latest confirmed line of its kind (D51).
AMOUNT_CHECKED = frozenset(
    {
        SplitMethod.BY_WIRTSCHAFTSPLAN,
        SplitMethod.BY_LOAN_SCHEDULE,
        SplitMethod.BY_RENT_SCHEDULE,
        SplitMethod.AS_LAST_SPLIT,
    }
)
NEAR_MISS_QUESTION = (
    "Der Betrag weicht von der letzten Buchung ab (zuletzt {amount} als „{label}“ gebucht). "
    "Stimmt die Zuordnung?"
)


@dataclass(frozen=True)
class _Booked:
    """A history line that counts: when, how much, and the categories it was booked as."""

    booking_date: date
    transaction_id: UUID
    amount: Decimal
    kind: frozenset[AllocationCategory]
    rows: tuple[HistoryRow, ...] = ()  # its current (confirmed) rows: what as_last_split copies


@dataclass(frozen=True)
class _NearMiss:
    found: AllocationSet
    latest: _Booked | None  # the latest history line of the hit's category set, if any


def rule_answer(
    rules: Sequence[MatchingRule],
    tx: MatchTransaction,
    context: MatchContext,
    history: Sequence[HistoryLine],
) -> AllocationSet | None:
    """The learned rules' answer for the line (D49, rule_history.yaml): the hits that stand,
    when they agree (confidence 1, auto-confirm); else, in a mixed history, the near miss
    whose category set has the strictly closest latest amount of all the history's sets, as a
    proposal (GUARDED_CONFIDENCE and a question; the service uses it only when no matcher
    answers); else None. `rules` oldest first; `history` may hold any lines, only those of the same
    IBAN hash and sign whose current rows are all confirmed count.

    Rules are the workspace's, not an account's (phase 6 D159): a hit whose categories the
    line's account does not take (a household rule on a rental card line, a rental rule on a
    private line) is no hit."""
    allowed = allowed_categories(context.account_purpose)
    hits = [
        (rule, s)
        for rule in rules
        if (s := apply_rule(rule, tx, context, history)) is not None
        and all(a.category in allowed for a in s.allocations)
    ]
    if not hits:
        return None
    booked = _booked(tx, history)
    standing: list[AllocationSet] = []
    near: list[_NearMiss] = []
    for rule, found in hits:
        kind = frozenset(a.category for a in found.allocations)
        if rule.split_method in AMOUNT_CHECKED:
            standing.append(found)
            continue
        if not rule.same_amount_only and all(b.kind == kind for b in booked):
            standing.append(found)  # single-kind: any amount, as before D49
            continue
        latest = max((b for b in booked if b.kind == kind), key=_order, default=None)
        if latest is not None and latest.amount == tx.amount:
            standing.append(found)
        else:
            near.append(_NearMiss(found, latest))
    if standing:
        first = standing[0]
        return first if all(same_split(first, other) for other in standing[1:]) else None
    closest = _closest_kind(tx, booked)
    if closest is None:
        return None
    of_kind = [n for n in near if n.latest is not None and n.latest.kind == closest]
    if not of_kind or any(not same_split(of_kind[0].found, n.found) for n in of_kind[1:]):
        return None
    return _proposal(of_kind[0])


def _closest_kind(
    tx: MatchTransaction, booked: Sequence[_Booked]
) -> frozenset[AllocationCategory] | None:
    """The category set whose latest line's amount is strictly the closest to the line's,
    among every set in a mixed history (sets no rule books included); None for a
    single-kind or empty history, or a tie (D49 (5))."""
    latest: dict[frozenset[AllocationCategory], _Booked] = {}
    for b in booked:
        known = latest.get(b.kind)
        if known is None or _order(b) > _order(known):
            latest[b.kind] = b
    if len(latest) < 2:
        return None
    ranked = sorted(latest.items(), key=lambda item: abs(tx.amount - item[1].amount))
    best, runner_up = ranked[0][1], ranked[1][1]
    if abs(tx.amount - best.amount) == abs(tx.amount - runner_up.amount):
        return None
    return ranked[0][0]


def _order(b: _Booked) -> tuple[date, str]:
    """Which history line is the latest: booking date, then transaction id."""
    return (b.booking_date, str(b.transaction_id))


def same_split(a: AllocationSet, b: AllocationSet) -> bool:
    return sorted(d.model_dump_json() for d in a.allocations) == sorted(
        d.model_dump_json() for d in b.allocations
    )


def _booked(tx: MatchTransaction, history: Sequence[HistoryLine]) -> list[_Booked]:
    """The history lines that count for this line: same IBAN hash (never none) and sign, not
    the line itself, current rows (not superseded) present and all confirmed."""
    if tx.counterparty_iban_hmac is None or tx.amount == 0:
        return []
    out = []
    for line in history:
        if line.transaction_id == tx.id or line.counterparty_iban_hmac != tx.counterparty_iban_hmac:
            continue
        if (line.amount > 0) != (tx.amount > 0) or line.amount == 0:
            continue
        current = [r for r in line.rows if r.status is not AllocationStatus.SUPERSEDED]
        if not current or any(r.status is not AllocationStatus.CONFIRMED for r in current):
            continue
        kind = frozenset(r.category for r in current)
        out.append(
            _Booked(line.booking_date, line.transaction_id, line.amount, kind, tuple(current))
        )
    return out


def _proposal(miss: _NearMiss) -> AllocationSet:
    latest = miss.latest
    assert latest is not None  # only measured near misses are proposed
    label = " und ".join(sorted(CATEGORY_LABEL[c] for c in latest.kind))
    question = NEAR_MISS_QUESTION.format(amount=_eur(abs(latest.amount)), label=label)
    return miss.found.model_copy(update={"confidence": GUARDED_CONFIDENCE, "question": question})


def infer_rule(
    tx: MatchTransaction,
    confirmed: AllocationSet,
    context: MatchContext,
    names: Sequence[NameHistory] = (),
) -> MatchingRuleCreate | None:
    """The rule a confirmed answer becomes ("Künftig immer so zuordnen"): the line's IBAN HMAC
    and amount sign, and a split method read from the confirmed allocations, never their
    amounts (D25). A split that no schedule, plan or tenancy fits becomes as_last_split, its
    kind the answer's category set (D51). None when the line has no IBAN (the user then types a
    pattern), the split fits no method (one category over several parts; a part of
    loan_interest or loan_principal, whose shares the schedule moves every month at the same
    amount; a rent part without one shared tenancy, review F2), or a single category or such
    a split is answered for a counterparty a matcher owns (an owner account, a tenant's or a
    loan account: D38). Rules run before the matchers, so an IBAN-only rule there would
    pre-empt the exact rent / loan / owner split of every later line.

    Phase 6 D160: on a private account a line without an IBAN may become a name-pattern rule
    (_name_rule; `names` are the account's confirmed lines it is checked against; it raises
    rule.too_broad or rule.conflicts). A private line with an IBAN learns the same one-category
    rule on its IBAN, under the same limits; a tenant's or a lender's IBAN included, since a
    household rule never hits a rental line (rule_answer drops a hit its account does not take)
    and that rule is D158's "the counterparty is also a private one"."""
    if tx.amount == 0:
        return None
    if context.account_purpose is AccountPurpose.PRIVATE:
        if tx.counterparty_iban_hmac is None:
            return _name_rule(tx, confirmed, names)
        if len(confirmed.allocations) != 1:
            return None
        (only,) = confirmed.allocations
        if not _learnable_household(only.category):
            return None
        return MatchingRuleCreate(
            matcher=RuleMatcher(
                counterparty_iban_hmac=tx.counterparty_iban_hmac,
                amount_sign="credit" if tx.amount > 0 else "debit",
            ),
            split_method=SplitMethod.SINGLE_CATEGORY,
            category=only.category,
            recurring=only.recurring,
        )
    if tx.counterparty_iban_hmac is None:
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
        if plan is not None and _parts(allocs) == _parts(_hausgeld_drafts(tx, plan)):
            plan_matcher = matcher.model_copy(update={"purpose_pattern": HAUSGELD_PATTERN})
            return MatchingRuleCreate(
                matcher=plan_matcher,
                split_method=SplitMethod.BY_WIRTSCHAFTSPLAN,
                property_id=property_id,
                recurring=recurring,
            )
    if categories == sorted(_LOAN):
        loan = _by_id(context.loans, _one({a.loan_id for a in allocs}))
        split = _loan_split(tx, loan) if loan else None
        if split is None or _parts(allocs) != _parts(split.allocations):
            return None  # never as_last_split: the schedule moves the shares every month
        return rule(SplitMethod.BY_LOAN_SCHEDULE, loan_id=loan.id)  # type: ignore[union-attr]
    if categories == sorted(_RENT):
        tenancy_id = _one({a.tenancy_id for a in allocs})
        if tenancy_id:
            return rule(SplitMethod.BY_RENT_SCHEDULE, tenancy_id=tenancy_id)
    if len(allocs) != 1:
        kind = sorted(set(categories))
        if len(kind) < 2 or any(c in _LOAN or c in _RENT for c in kind):
            return None  # rent: by_rent_schedule of one tenancy or no rule, as before (F2)
        if matcher_owns(tx.counterparty_iban_hmac, context):
            return None
        return rule(SplitMethod.AS_LAST_SPLIT, categories=kind)
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


def _as_last_split(
    rule: MatchingRule, tx: MatchTransaction, history: Sequence[HistoryLine]
) -> _Split | None:
    """D51: the parts of the latest confirmed history line whose category set is the rule's and
    whose booking date is on or before the line's own (a later split is never copied back,
    review F1), copied when the line's amount equals that line's (category, amount, scope and
    recurring; the service period read for the new line, no Beleg). Nothing is computed: any
    other amount, no such line, or a row without its amount -> None."""
    kind = frozenset(rule.categories or ())
    earlier = (
        b for b in _booked(tx, history) if b.kind == kind and b.booking_date <= tx.booking_date
    )
    latest = max(earlier, key=_order, default=None)
    if latest is None or latest.amount != tx.amount:
        return None
    if any(r.amount is None for r in latest.rows):
        return None
    drafts = [
        _draft(
            r.amount,  # type: ignore[arg-type]  # checked above
            r.category,
            service_period(tx.purpose, tx.booking_date, r.recurring),
            r.recurring,
            property_id=r.property_id,
            unit_id=r.unit_id,
            tenancy_id=r.tenancy_id,
            loan_id=r.loan_id,
        )
        for r in latest.rows
    ]
    if sum(d.amount for d in drafts) != tx.amount:
        return None  # a confirmed line always sums; never book a set that does not
    return _Split(drafts, "Gelernte Regel: aufgeteilt wie die letzte bestätigte Zahlung.")


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


# A German amount: 1.061,82 / 1061,82 / 944,63, not part of a longer number.
_AMOUNT = r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+,\d{2}|\d+,\d{2})(?![\d,])"
_LABEL = r"\s*:?\s*(?:(?:EUR|€)\s*)?"
_STATED_INTEREST = re.compile(r"\bZinsen\b" + _LABEL + _AMOUNT, re.IGNORECASE)
_STATED_REPAYMENT = re.compile(r"\bTilgung\b" + _LABEL + _AMOUNT, re.IGNORECASE)
_STATED_RANGE = re.compile(
    r"\bF(?:ü|ue)r\s+(\d{1,2})\.(\d{1,2})\.(\d{4})\s*-\s*(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)",
    re.IGNORECASE,
)


def _interest_only_split(tx: MatchTransaction, loan: LoanFacts) -> _Split | None:
    """A debit in a flagged loan's interest-only time (after the payout, before the first
    annuity) whose purpose states the interest, equal to the debit, and no repayment: the
    bank's own figure, so the whole amount is loan_interest. Anything else -> None (review)."""
    terms = loan.terms
    if not terms.interest_only_before_first_payment or tx.amount >= 0:
        return None
    if not terms.disbursed_on < tx.booking_date < terms.first_payment_on:
        return None
    purpose = " ".join((tx.purpose or "").split())
    stated = {_german_amount(m.group(1)) for m in _STATED_INTEREST.finditer(purpose)}
    if stated != {-tx.amount}:
        return None
    if any(_german_amount(m.group(1)) > 0 for m in _STATED_REPAYMENT.finditer(purpose)):
        return None
    period = _stated_range(purpose) or ServicePeriod(
        start=tx.booking_date.replace(day=1), end=_month_end(tx.booking_date)
    )
    scope = {"property_id": loan.property_id, "loan_id": loan.id}
    draft = _draft(tx.amount, C.LOAN_INTEREST, period, True, **scope)
    return _Split([draft], "Nur Zinsen vor der ersten Rate, Betrag laut Buchungstext der Bank.")


def _german_amount(text: str) -> Decimal:
    return Decimal(text.replace(".", "").replace(",", "."))


def _stated_range(purpose: str) -> ServicePeriod | None:
    """The first valid "Für dd.mm.yyyy - dd.mm.yyyy" range (start <= end), else None."""
    for m in _STATED_RANGE.finditer(purpose):
        d1, m1, y1, d2, m2, y2 = (int(g) for g in m.groups())
        try:
            start, end = date(y1, m1, d1), date(y2, m2, d2)
        except ValueError:
            continue
        if start <= end:
            return ServicePeriod(start=start, end=end)
    return None


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
    apply_rule's. Also used to pick the waiting lines a new rule may book (rematch_waiting)."""
    if not matcher.matches_something:  # an erased rule's empty matcher hits nothing
        return False
    wanted = matcher.counterparty_iban_hmac
    if wanted is not None and tx.counterparty_iban_hmac != wanted:
        return False
    if matcher.amount_sign == "credit" and not tx.amount > 0:
        return False
    if matcher.amount_sign == "debit" and not tx.amount < 0:
        return False
    name_pattern = matcher.counterparty_name_pattern
    if name_pattern is not None and not _name_found(name_pattern, tx.counterparty_name):
        return False
    purpose_pattern = matcher.purpose_pattern
    return purpose_pattern is None or _found(purpose_pattern, tx.purpose)


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
        hmac in context.own_account_hmacs
        or any(hmac in t.tenant_iban_hmacs for t in context.tenancies)
        or any(hmac == loan.account_iban_hmac for loan in context.loans)
    )


def document_hit(tx: MatchTransaction, doc: DocumentFacts) -> bool:
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


# --- Phase 6 (docs/phases/phase-6.md D160, D162; built in steps 1 and 2) ------------------

PHASE6_RULES_STEP = "phase 6 step 1 (learned rules on a private account)"
PHASE6_TRANSFERS_STEP = "phase 6 step 2 (internal transfers)"


_LOCATION = re.compile(r"//[^/]*/[A-Za-z]{2}\s*$")  # a Sparkasse card line's //<Ort>/<Land>
_NAME_DROPPED = re.compile(r"[0-9/.]")


def merchant_pattern(name: str) -> str:
    """D160: a card line's counterparty name to an anchored rule pattern: the trailing
    location token (`//<Ort>/<Land>`) removed, upper-cased, digits, slashes and dots replaced by
    a space, whitespace collapsed, regex-escaped with a space left plain, anchored at the start
    (fixture merchant_pattern.yaml: "S.C.A." keeps "S C A", so a dropped character is a space)."""
    return "^" + re.escape(_merchant_text(name)).replace("\\ ", " ")


def _merchant_text(name: str) -> str:
    """The name as merchant_pattern reads it, unescaped: what a name pattern is matched on
    besides the collapsed name, since "S.C.A." becomes "S C A" in the pattern."""
    text = _LOCATION.sub("", name).upper()
    return " ".join(_NAME_DROPPED.sub(" ", text).split())


def _name_found(pattern: str, name: str | None) -> bool:
    """A counterparty name pattern holds on the collapsed name (a pattern a person typed, as
    before) or on the name normalised like merchant_pattern (a learned name rule, D160, whose
    dots and digits became spaces: "^NETFLIX INTERNATIONAL B V" on "Netflix International
    B.V.")."""
    if name is None:
        return False
    return _found(pattern, name) or _found(pattern, _merchant_text(name))


def _learnable_household(category: AllocationCategory) -> bool:
    """D160: a household code with a threshold that is not tax-relevant. A tax-relevant code
    proposes a return input a person confirms each time; a transfer or depot code is booked
    by its matcher only; a code without a threshold (hh_other) is never booked unseen."""
    return (
        category in HOUSEHOLD_CODES
        and category not in TAX_RELEVANT
        and THRESHOLDS[category] is not None
    )


def _name_rule(
    tx: MatchTransaction,
    confirmed: AllocationSet,
    names: Sequence[NameHistory],
) -> MatchingRuleCreate | None:
    """D160: on a private account, a single-category answer of a learnable household code for
    a line without an IBAN becomes a rule on the merchant's name. Not for a split. Refused when
    the account's confirmed lines the pattern hits carry two or more categories (rule.too_broad:
    the name does not tell the kind, so the card asks for a purpose pattern) or one category
    other than the answer's (rule.conflicts); too_broad is checked first."""
    if len(confirmed.allocations) != 1:
        return None
    (only,) = confirmed.allocations
    if not _learnable_household(only.category):
        return None
    pattern = merchant_pattern(tx.counterparty_name or "")
    if pattern == "^":
        return None  # no name (or digits and dots only): nothing to match on
    hits = {
        h.category
        for h in names
        if h.transaction_id != tx.id and _name_found(pattern, h.counterparty_name)
    }
    if len(hits) >= 2:
        raise RuleError(RULE_TOO_BROAD, "the name was confirmed under two categories")
    if hits and hits != {only.category}:
        raise RuleError(RULE_CONFLICTS, "the pattern hits a line confirmed otherwise")
    return MatchingRuleCreate(
        matcher=RuleMatcher(
            counterparty_name_pattern=pattern,
            amount_sign="credit" if tx.amount > 0 else "debit",
        ),
        split_method=SplitMethod.SINGLE_CATEGORY,
        category=only.category,
        recurring=only.recurring,
    )


# D158's question for a private line whose counterparty the rental account's matchers own.
TENANT_QUESTION = "Diese Zahlung kommt von einem Mieter: gehört sie zum Mietobjekt?"
LENDER_QUESTION = "Diese Zahlung geht an die Bank des Darlehens: gehört sie zum Mietobjekt?"


def rental_counterparty_question(
    tx: MatchTransaction, context: MatchContext
) -> AllocationSet | None:
    """D158: a private line from or to a tenant's IBAN or a loan's account is not booked as rent
    or annuity (the rental matchers never run on a private account); it goes to review with the
    question whether it belongs to the property, proposing hh_other_income (a credit) or
    hh_property_cost (a debit) with the property, at GUARDED_CONFIDENCE so it never
    auto-confirms. The model never sees such a line. None for any other counterparty, or one
    that two properties share (no property to name)."""
    hmac = tx.counterparty_iban_hmac
    if hmac is None or tx.amount == 0:
        return None
    tenant = {t.property_id for t in context.tenancies if hmac in t.tenant_iban_hmacs}
    lender = {loan.property_id for loan in context.loans if hmac == loan.account_iban_hmac}
    if tenant:
        properties, question, method = tenant, TENANT_QUESTION, AllocationMethod.MATCHER_RENT
    elif lender:
        properties, question, method = lender, LENDER_QUESTION, AllocationMethod.MATCHER_LOAN
    else:
        return None
    if len(properties) != 1:
        return None
    (property_id,) = properties
    category = (
        AllocationCategory.HH_OTHER_INCOME if tx.amount > 0 else AllocationCategory.HH_PROPERTY_COST
    )
    draft = _draft(
        tx.amount,
        category,
        service_period(tx.purpose, tx.booking_date, False),
        False,
        property_id=property_id,
    )
    return AllocationSet(
        transaction_id=tx.id,
        method=method,
        confidence=GUARDED_CONFIDENCE,
        allocations=[draft],
        reasoning=(
            "Das Gegenkonto gehört zum Mietobjekt; auf einem privaten Konto wird daraus "
            "keine Miete oder Rate gebucht."
        ),
        question=question,
    )


def _allowed_set(found: AllocationSet | None, context: MatchContext) -> AllocationSet | None:
    """D159: an answer whose categories the line's account does not take is no answer (a
    property bill matched on a private line is D161's path, phase 6 step 2)."""
    if found is None:
        return None
    allowed = allowed_categories(context.account_purpose)
    return found if all(a.category in allowed for a in found.allocations) else None


_RENTAL_ORDER: tuple[Callable[[MatchTransaction, MatchContext], AllocationSet | None], ...] = (
    match_owner_transfer,
    match_rent,
    match_loan,
    match_document,
)
# D158 on a private account: the transfer matcher (D162, phase 6 step 2) comes first once it is
# built; then the document match; then the tenant / lender question before the model.
_PRIVATE_ORDER: tuple[Callable[[MatchTransaction, MatchContext], AllocationSet | None], ...] = (
    match_document,
    rental_counterparty_question,
)


def deterministic_answer(
    rules: Sequence[MatchingRule],
    tx: MatchTransaction,
    context: MatchContext,
    history: Sequence[HistoryLine] = (),
) -> AllocationSet | None:
    """The deterministic order per account purpose (D158; fixture matching_order.yaml), before
    the model. Rental: learned rules -> owner transfer -> rent -> loan -> document (Hausgeld is
    a by_wirtschaftsplan rule), as in phase 2. Private: learned rules -> document -> D158's
    question; the rent, loan, Hausgeld and owner-transfer matchers never run there. A standing
    rule hit (confidence 1) wins at once; a rule's near-miss proposal (D49) only when no
    matcher answers. Every answer is one the account's purpose takes (D159)."""
    found = rule_answer(rules, tx, context, history)
    if found is not None and found.confidence == EXACT:
        return found
    order = _PRIVATE_ORDER if context.account_purpose is AccountPurpose.PRIVATE else _RENTAL_ORDER
    for matcher in order:
        answer = _allowed_set(matcher(tx, context), context)
        if answer is not None:
            return answer
    return found


def match_transfer(tx: MatchTransaction, context: MatchContext) -> AllocationSet | None:
    """D162: a line whose counterparty is one of the household's own accounts, linked to the
    open leg of the other side (fixture transfers.yaml)."""
    raise NotImplementedError(PHASE6_TRANSFERS_STEP)
