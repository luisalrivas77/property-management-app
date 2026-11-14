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
A shared IBAN (phase 6 D160 amended: a private account's clearing IBAN that carries many
payees, shared_iban) names no payee: a rule on it alone never applies there, and a rule learned
there is the IBAN plus the payee's name.
"""

import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
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
    RecordedBill,
    RentInForce,
    RuleMatcher,
    ServicePeriod,
    SplitMethod,
    TenancyFacts,
    TransferLeg,
    WirtschaftsplanFacts,
)
from hausbuch.schemas.enums import AccountKind, AccountPurpose, AllocationStatus
from hausbuch.schemas.household import TransferCandidate, TransferLinkKind, TransferMatch
from hausbuch.schemas.loan import ScheduleEntry

from . import proof_match
from .categories import CATEGORY_LABEL
from .errors import (
    RULE_CONFLICTS,
    RULE_NO_MERCHANT,
    RULE_TOO_BROAD,
    TRANSFER_AMBIGUOUS,
    TRANSFER_UNMATCHED_LEG,
    RuleError,
)
from .household.taxonomy import HOUSEHOLD_CODES, TAX_RELEVANT, allowed_categories
from .loan_schedule import build_schedule
from .periods import months_in, service_period
from .review import THRESHOLDS
from .statements import norm

# The shared code (D159) a private account may learn a rule for, beside its household codes:
# its own bank fees. uncategorized stays unlearnable (no threshold).
LEARNABLE_SHARED = frozenset({AllocationCategory.BANK_FEES})

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


def match_private_document(tx: MatchTransaction, context: MatchContext) -> AllocationSet | None:
    """D161 on a private line: a line that pays one bill of a property (the document match's
    test: amount within 0.01, date within 10 days, the bill's IBAN or number) is booked
    hh_property_cost with the property and the bill, never the bill's own category, so the
    Anlage V counts it only through the bill's private_cost row (pass_through, D159). At the
    document match's confidence; hh_property_cost has no threshold, so it always goes to
    review (path 1 proves the row once confirmed; path 2 then proposes the row). A bill of
    no property is no answer here."""
    hits = {doc.id: doc for doc in context.documents if document_hit(tx, doc)}
    if len(hits) != 1:
        return None
    (doc,) = hits.values()
    if doc.property_id is None:
        return None
    draft = _draft(
        tx.amount,
        C.HH_PROPERTY_COST,
        service_period(tx.purpose, tx.booking_date, False),
        False,
        property_id=doc.property_id,
        document_id=doc.id,
    )
    split = _Split(
        [draft],
        "Betrag, Datum und IBAN oder Referenz passen zu einer Rechnung des Mietobjekts, "
        "bezahlt von einem privaten Konto.",
        confidence=DOCUMENT_CONFIDENCE,
        question="Ist das die Zahlung dieser Rechnung des Mietobjekts?",
    )
    return _as_set(tx, split, AllocationMethod.DOCUMENT_MATCH)


# --- A private line paying a bill already recorded (D161 widened, 14 Nov 2026) --------------

# The window around a recorded bill: from RECORDED_BEFORE_DAYS before its issue date to
# RECORDED_AFTER_DAYS after the later of its issue and due dates. Wider than the document
# match's ± 10 days: a Grunderwerbsteuerbescheid is due a month after its notice
# (§ 15 GrEStG) and a notary's or the Grundbuchamt's bill is often paid weeks after its date;
# the evidence here is the amount equal to a recorded row or cost line plus the payee, so the
# window only bounds it.
RECORDED_BEFORE_DAYS = 10
RECORDED_AFTER_DAYS = 60
# Words that may name a cost of buying or financing the flat (rules.prefill's purchase and
# financing terms, and the payees of those bills). Also what the one-off household re-ask
# leaves alone (services.matching.reask_household, D159 amended).
PURCHASE_COST_WORDS = (
    "grunderwerb",
    "notar",
    "grundbuch",
    "makler",
    "justizkasse",
    "amtsgericht",
    "kaufpreis",
    "kaufvertrag",
    "auflassung",
    "eigentumsumschreibung",
    "grundschuld",
    "grundpfand",
    "hypothek",
    "darlehen",
)
# The payees of such a bill whose name says nothing of the purchase (the Finanzamt collects
# the Grunderwerbsteuer, a Landes- or Gerichtskasse the Grundbuch fees): corroboration for a
# line whose amount already equals a recorded row, never a reason on their own.
_RECORDED_PAYEE_WORDS = PURCHASE_COST_WORDS + ("finanzamt", "landeshauptkasse", "gerichtskasse")


def recorded_amounts(bill: RecordedBill) -> list[Decimal]:
    """The magnitudes a payment of a recorded bill may have (rules.proof_match: the bill's
    combined total, its private part alone when it also has Nebenkosten rows, one Nebenkosten
    row, or one of its cost lines when they sum to the total), each once."""
    whole = [proof_match.bill_total(bill.acquisition, bill.private)]
    if bill.acquisition and bill.private:
        whole.append(sum(bill.private, Decimal(0)))
    parts = proof_match.bill_parts(bill.lines, bill.acquisition, bill.private)
    return list(dict.fromkeys([*whole, *bill.acquisition, *parts]))


def recorded_window(bill: RecordedBill) -> tuple[date, date] | None:
    """The booking dates a payment of the bill may have; None for a bill with no date read."""
    dates = [d for d in (bill.issued_on, bill.due_on) if d is not None]
    if not dates:
        return None
    first = bill.issued_on or bill.due_on
    assert first is not None
    return (
        date.fromordinal(first.toordinal() - RECORDED_BEFORE_DAYS),
        date.fromordinal(max(dates).toordinal() + RECORDED_AFTER_DAYS),
    )


def recorded_hit(tx: MatchTransaction, bill: RecordedBill) -> proof_match.Fit | None:
    """How a private debit pays a recorded bill, or None: its amount fits the bill
    (rules.proof_match.proof_fit, within 0.01), it pays no more than what other lines left
    open, it was booked in the bill's window (recorded_window), and its payee is the bill's:
    the bill's IBAN, its reference in the purpose, or a purchase-cost word in the line's name
    or purpose (the Finanzamt, a Justizkasse, a notary)."""
    if tx.amount >= 0:
        return None
    paid = abs(tx.amount)
    fit = proof_match.proof_fit(paid, bill.acquisition, bill.private, parts=bill.lines)
    if fit is None:
        return None
    total = proof_match.bill_total(bill.acquisition, bill.private)
    if bill.paid + paid > total + DOCUMENT_AMOUNT_TOLERANCE:
        return None
    window = recorded_window(bill)
    if window is None or not window[0] <= tx.booking_date <= window[1]:
        return None
    if bill.iban_hmac is not None and bill.iban_hmac == tx.counterparty_iban_hmac:
        return fit
    if _reference_in(bill.reference, tx.purpose):
        return fit
    text = " ".join(filter(None, (tx.counterparty_name, tx.purpose, tx.booking_text))).casefold()
    return fit if any(word in text for word in _RECORDED_PAYEE_WORDS) else None


RECORDED_WHOLE_QUESTION = "Ist das die Zahlung dieser Rechnung des Mietobjekts?"
RECORDED_PART_QUESTION = (
    "Ist das die Zahlung eines Teils dieser Rechnung des Mietobjekts (etwa der Anteil eines "
    "Käufers)?"
)


def match_recorded_bill(tx: MatchTransaction, context: MatchContext) -> AllocationSet | None:
    """D161 widened (14 Nov 2026): a private line paying a bill already recorded as a
    Nebenkosten row or a private_cost row (context.recorded_bills, recorded_hit) is booked
    hh_property_cost with the bill's property and the bill, so it becomes that record's proof
    of payment (phase 3 D143) and never a second record: the Anlage V reads the bill through
    its rows only (hh_property_cost is pass_through, never summed). One bill must fit; two
    that fit equally are no answer. Always to review: hh_property_cost has no threshold."""
    hits = {b.document_id: (b, fit) for b in context.recorded_bills if (fit := recorded_hit(tx, b))}
    if len(hits) != 1:
        return None
    ((bill, fit),) = hits.values()
    draft = _draft(
        tx.amount,
        C.HH_PROPERTY_COST,
        service_period(tx.purpose, tx.booking_date, False),
        False,
        property_id=bill.property_id,
        document_id=bill.document_id,
    )
    whole = fit is proof_match.Fit.WHOLE
    split = _Split(
        [draft],
        "Der Betrag passt zu einer schon erfassten Rechnung des Mietobjekts "
        f"({'ganz' if whole else 'zu einem Teil'}), bezahlt von einem privaten Konto; die "
        "Zahlung wird ihr Zahlungsnachweis, die Kosten zählen nur einmal.",
        confidence=DOCUMENT_CONFIDENCE,
        question=RECORDED_WHOLE_QUESTION if whole else RECORDED_PART_QUESTION,
    )
    return _as_set(tx, split, AllocationMethod.DOCUMENT_MATCH)


# --- A private line paying the seller (14 Nov 2026) ----------------------------------------

DOWN_PAYMENT_QUESTION = "Anzahlung auf den Kaufpreis der Wohnung an den Verkäufer?"


def match_down_payment(tx: MatchTransaction, context: MatchContext) -> AllocationSet | None:
    """A private debit to the seller's account (context.purchase_payees: the IBAN of a rental
    line confirmed as purchase_price) is a payment on the purchase price, so it is proposed as
    hh_investment („Geldanlage“: neither income nor spending, the money went into the flat)
    with the property when one is known. Never a Nebenkosten or private_cost row and never
    anything the Anlage V reads: the AfA basis comes from the property record only (phase 3
    D55). Always to review (hh_investment has no threshold)."""
    hmac = tx.counterparty_iban_hmac
    if hmac is None or tx.amount >= 0:
        return None
    properties = {p.property_id for p in context.purchase_payees if p.iban_hmac == hmac}
    if not properties:
        return None
    property_id = next(iter(properties)) if len(properties) == 1 else None
    draft = _draft(
        tx.amount,
        C.HH_INVESTMENT,
        service_period(tx.purpose, tx.booking_date, False),
        False,
        property_id=property_id,
    )
    return AllocationSet(
        transaction_id=tx.id,
        method=AllocationMethod.DOCUMENT_MATCH,
        confidence=GUARDED_CONFIDENCE,
        allocations=[draft],
        reasoning=(
            "Anzahlung auf den Kaufpreis der Wohnung an den Verkäufer: das Gegenkonto hat auf "
            "dem Mietkonto den Kaufpreis erhalten. Die Zahlung ist weder Einnahme noch Ausgabe "
            "und zählt nicht in der Anlage V (die AfA-Basis kommt aus dem Kaufpreis der "
            "Immobilie)."
        ),
        question=DOWN_PAYMENT_QUESTION,
    )


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
    `history` (as for rule_answer) is read by as_last_split only, which copies from it.

    D160 amended: a rule on the IBAN alone never applies to a line whose IBAN is shared
    (context.shared_iban_hmacs), however it was learned (before the IBAN became shared, or on
    another account): the IBAN does not name the payee there.

    D160 amended (4), 14 Nov 2026: a rule that names no merchant never applies to a payment
    service's line (service_blind: an IBAN-only rule on PayPal's IBAN, or one learned on PayPal's
    name alone before that day), on any account: it is disabled on read, not deleted, since it
    would book every merchant's payment through the service as the one it was learned on."""
    if not rule.enabled or not rule_hits(rule.matcher, tx):
        return None
    if iban_only(rule.matcher) and rule.matcher.counterparty_iban_hmac in context.shared_iban_hmacs:
        return None
    if service_blind(rule.matcher, tx):
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
    and that rule is D158's "the counterparty is also a private one". D160 amended: on a
    shared IBAN (context.shared_iban_hmacs) it is the name rule of _name_rule, under its
    checks; since D160 amended (4) (14 Nov 2026) without the IBAN, and so is a payment
    service's line (PayPal, Klarna), whose IBAN names the service, not the merchant."""
    if tx.amount == 0:
        return None
    if context.account_purpose is AccountPurpose.PRIVATE:
        if (
            tx.counterparty_iban_hmac is None
            or tx.counterparty_iban_hmac in context.shared_iban_hmacs
            or payee(tx.counterparty_name, tx.purpose).service is not None
        ):
            # A shared IBAN or a service's names no payee: the rule is the payee's name (and
            # the merchant's purpose pattern), checked like a name rule; None without a name.
            return _name_rule(tx, confirmed, names)
        if len(confirmed.allocations) != 1:
            return None
        (only,) = confirmed.allocations
        if not learnable_household(only.category):
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
    purpose_pattern = matcher.purpose_pattern
    if name_pattern is None:
        return purpose_pattern is None or _found(purpose_pattern, tx.purpose)
    # D160 amended (4): a learned name pattern also hits the other spellings of its payee.
    return _names_hit(name_pattern, purpose_pattern, tx.counterparty_name, tx.purpose)


def _found(pattern: str, text: str | None) -> bool:
    if text is None:
        return False
    try:
        return re.search(pattern, " ".join(text.split()), re.IGNORECASE) is not None
    except re.error:
        return False  # a broken pattern never hits; the line goes to review


# --- A suggested purpose pattern (14 Nov 2026, principle 7) --------------------------------
#
# A line without an IBAN (a bank's own fee booking, „Entgeltabrechnung siehe Anlage …“) learns
# a rule only on a typed purpose pattern (D38), and a person does not know which word to type.
# The panel proposes the purpose's first word, anchored, when it is long enough to name the
# kind of booking and is safe on the workspace's lines (services.review).

PURPOSE_WORD_MIN = 6
_LEADING_WORD = re.compile(r"[^\W\d_]+")  # letters only, umlauts and ß included


def purpose_word(purpose: str | None) -> str | None:
    """The word of letters the purpose starts with, as rule_hits reads the purpose
    (whitespace collapsed), when it has at least PURPOSE_WORD_MIN letters; None for an empty
    purpose, one starting with a digit or a sign, or a shorter word."""
    found = _LEADING_WORD.match(" ".join((purpose or "").split()))
    if found is None or len(found.group()) < PURPOSE_WORD_MIN:
        return None
    return found.group()


def suggested_purpose_pattern(purpose: str | None) -> str | None:
    """`^` + the purpose's first word (purpose_word), regex-escaped; None without one."""
    word = purpose_word(purpose)
    return None if word is None else "^" + re.escape(word)


@dataclass(frozen=True)
class PatternLine:
    """Another line of the workspace a suggested purpose-pattern rule may hit: the line,
    the categories of its confirmed current rows (empty when none is confirmed), and whether
    it waits for an answer on an account the rule would book (a reconciled import, not
    excluded, no confirmed row, an account of the line's purpose)."""

    tx: MatchTransaction
    confirmed: tuple[AllocationCategory, ...]
    waiting: bool


def pattern_waiting(
    matcher: RuleMatcher, category: AllocationCategory, lines: Iterable[PatternLine]
) -> int | None:
    """How many waiting lines a rule with `matcher` booking `category` would book, or None
    when the suggestion is not safe: a line it hits (rule_hits) has a counterparty IBAN, or
    is confirmed under another category or as a split."""
    waiting = 0
    for line in lines:
        if not rule_hits(matcher, line.tx):
            continue
        if line.tx.counterparty_iban_hmac is not None:
            return None
        if line.confirmed and line.confirmed != (category,):
            return None
        if line.waiting:
            waiting += 1
    return waiting


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


_LOCATION = re.compile(r"//[^/]*/[A-Za-z]{2}\s*$")  # a Sparkasse card line's //<Ort>/<Land>
_NAME_DROPPED = re.compile(r"[0-9/.]")


def merchant_pattern(name: str) -> str:
    """D160: a card line's counterparty name to an anchored rule pattern: the trailing
    location token (`//<Ort>/<Land>`) removed, the name cut before its first per-purchase
    token (_merchant_cut, D160 amended (4)), upper-cased, digits, slashes and dots replaced by
    a space, whitespace collapsed, regex-escaped with a space left plain, anchored at the start
    (fixture merchant_pattern.yaml: "S.C.A." keeps "S C A", so a dropped character is a space)."""
    return _as_pattern(_payee_text(name))


def _as_pattern(text: str) -> str:
    return "^" + re.escape(text).replace("\\ ", " ")


def _merchant_text(name: str) -> str:
    """The whole name normalised as merchant_pattern normalises it, unescaped: what a name
    pattern is matched on besides the collapsed name, since "S.C.A." becomes "S C A" in the
    pattern."""
    return _normalised(_LOCATION.sub("", name))


def _normalised(text: str) -> str:
    return " ".join(_NAME_DROPPED.sub(" ", text.upper()).split())


def _payee_text(name: str) -> str:
    """The name as a learned name rule reads it: _merchant_cut, normalised."""
    return _normalised(_merchant_cut(name))


_LETTER = re.compile(r"[^\W\d_]")
_WORD_MIN = re.compile(r"[^\W\d_]{4,}")  # a word of at least 4 letters
_BEFORE_DIGIT = re.compile(r"^\D*")


def _has_digit(token: str) -> bool:
    return any(c.isdigit() for c in token)


def _mixed(token: str) -> bool:
    """A token mixing letters and digits: an order code ("ZD2HG0G54", "B9WNACJA") or a date
    glued to a name ("21MAY", "GLOVO24MAY")."""
    return _has_digit(token) and _LETTER.search(token) is not None


_REFERENCE = re.compile(r"[A-Za-z0-9]{6,}")


def _reference(token: str) -> bool:
    """A leading reference code (D160 amended (4), from Luis's real card lines): one token of
    6 or more letters and digits with at least two digits ("G0A3LMSE", "375be52e", "R0131347"),
    printed before the merchant's name and different on every purchase. Not a name with a date
    glued to it ("GLOVO24MAY": a word of 4 letters before the first digit), which _merchant_cut
    reads as its name."""
    return (
        _REFERENCE.fullmatch(token) is not None
        and sum(c.isdigit() for c in token) >= 2
        and not _WORD_MIN.search(_BEFORE_DIGIT.match(token).group())  # type: ignore[union-attr]
    )


def _merchant_cut(name: str) -> str:
    """D160 amended (4), 14 Nov 2026: the raw name (location token removed) up to its first
    per-purchase token, so a card name that carries an order code or a date repeats:

    - a `*` followed by a token with a digit ("AMZN Mktp DE*ZD2HG0G54") ends the name at the
      `*`; a `*` followed by a word is the merchant a payment facilitator names ("PAYPAL
      *DISNEYPLUS", "SUMUP *CAFE …"), kept, and then the first token with a digit (the
      facilitator's phone number or the merchant's address) ends the name;
    - a token mixing letters and digits ("21MAY", "B9WNACJA", "GLOVO24MAY") ends the name;
      its letters before the first digit stay when they hold a word of 4 letters or more
      ("GLOVO" of "GLOVO24MAY").

    Digits-only tokens ("4421", "0815/2026", "22-24") are no per-purchase code: they are
    dropped by the normalisation as before. A reference code before the merchant's name
    (_reference: "G0A3LMSE ANTHROPIC …", "375be52e SQ *ATARIGUA …") is dropped and the name
    read on after it. When the cut keeps no word of 4 letters or more,
    the whole name is read as before (an "O2 GmbH" stays "O GMBH")."""
    text = _LOCATION.sub("", name)
    tokens = text.split()
    kept: list[str] = []
    starred = False
    for i, token in enumerate(tokens):
        if starred and _has_digit(token):
            break
        if i + 1 < len(tokens) and _reference(token) and not any(map(_LETTER.search, kept)):
            continue  # a reference code before the name
        head, star, tail = token.partition("*")
        if _mixed(head):
            lead = _BEFORE_DIGIT.match(head).group()  # type: ignore[union-attr]
            if _WORD_MIN.search(lead):
                kept.append(lead)
            break
        if not star:
            kept.append(token)
            continue
        after = tail or (tokens[i + 1] if i + 1 < len(tokens) else "")
        if not after or _has_digit(after.partition("*")[0]):
            if head:
                kept.append(head)
            break
        kept.append(token)
        starred = True
    cut = " ".join(kept)
    return cut if _WORD_MIN.search(cut) else text


def _name_found(pattern: str, name: str | None) -> bool:
    """A counterparty name pattern holds on the collapsed name (a pattern a person typed, as
    before) or on the name normalised like merchant_pattern (a learned name rule, D160, whose
    dots and digits became spaces: "^NETFLIX INTERNATIONAL B V" on "Netflix International
    B.V.")."""
    if name is None:
        return False
    return (
        _found(pattern, name)
        or _found(pattern, _merchant_text(name))
        or _found(pattern, _payee_text(name))  # a reference code before the name dropped
    )


# --- A shared counterparty IBAN (D160 amended 13 Nov 2026) ---------------------------------

# DKB's CSV export puts one clearing IBAN on every card payment, so on a private account one
# IBAN hash can carry dozens of payees. An IBAN whose private lines carry this many payees that
# are not one payee (same_payee) names no payee: no rule on it alone, and everything else keyed
# by the IBAN (the D49 history and question, the similar answers) is keyed by IBAN and payee.
SHARED_IBAN_MIN_NAMES = 2


# --- The payee of a line (D160 amended (4), 14 Nov 2026) -----------------------------------
#
# Luis: „There are some repeated disney plus from PayPal …“; „Please check for all these
# repetitions“. One payee is spelled several ways ("EPRIMO GMBH" and "EPRIMO GMBH
# FLUGHAFENSTRASE 20", "HAUS DES DÖNERS" and "HAUS DES DOENERS MUENSTERSTR"), a card processor
# names the merchant after its own name ("SPC*CAMPUS HAIR"), and a payment service carries
# many merchants under its own name. Who a line pays is decided by code (payee, same_payee),
# never by a cleverer pattern; the patterns a rule stores are unchanged (merchant_pattern), and
# a learned one is read back as a payee to hit the other spellings (rule_hits).


@dataclass(frozen=True)
class PaymentService:
    label: str  # as the panel names it („PayPal: DisneyPlus“)
    word: str  # the name's first word, upper-case
    purpose_phrases: tuple[str, ...]  # the words before the merchant in a direct debit's purpose


# Payment services: their own name on many merchants' payments. Recognised by the name's first
# word (PayPal's two legal names, its card form "<digits> PAYPAL *<MERCHANT> …") or a debit
# card's "<Merchant> - PayPal"; a direct debit names the merchant in the purpose after one of
# the phrases ("…, Ihr Einkauf bei <Merchant>, …", "Purchase at <Merchant> Gläubiger-ID: …").
PAYMENT_SERVICES = (
    PaymentService("PayPal", "PAYPAL", ("Ihr Einkauf bei",)),
    PaymentService("Klarna", "KLARNA", ("Purchase at", "Ihr Einkauf bei")),
)
# Card processors whose own short name comes first and the merchant after it ("SPC*CAMPUS
# HAIR", "UZR SPANISCHER GARTEN", "ZETTLE CAFE NANE"): the payee is what follows.
CARD_PROCESSORS = frozenset({"SPC", "UZR", "SQ", "SUMUP", "ZETTLE"})
# First words many unrelated payees share: a shared first word alone never makes one payee.
GENERIC_FIRST_WORDS = frozenset(
    {
        "APOTHEKE",
        "AUTOHAUS",
        "BAECKEREI",
        "BAHNHOF",
        "BISTRO",
        "CAFE",
        "CITY",
        "DEUTSCHE",
        "GASTSTAETTE",
        "GRUPO",
        "HAUS",
        "HOTEL",
        "KIOSK",
        "KLINIK",
        "PARKHAUS",
        "PARKPLATZ",
        "PIZZERIA",
        "PRAXIS",
        "RESTAURANT",
        "SHOP",
        "SPARKASSE",
        "STADT",
        "STADTWERKE",
        "TANKSTELLE",
    }
)
# Words that never make the second of the first two words a payee is known by ("HOTEL AT
# BOOKING COM", "HAUS DES …").
_LINKING_WORDS = frozenset(
    {"AM", "AN", "AT", "C", "DAS", "DE", "DEL", "DER", "DES", "DI", "DIE", "EL", "IM", "IN"}
    | {"LA", "LE", "OF", "THE", "UND", "VON", "ZUM", "ZUR"}
)
FIRST_WORD_MIN = 5  # a shared non-generic first word of this many letters names one payee
# Where a merchant named in the purpose ends: a comma, the creditor id, a SEPA field tag.
_MERCHANT_END = r"(?:,|;|\s+Gl(?:ä|ae)ubiger-?ID\b|\s+(?:EREF|MREF|CRED|SVWZ)\b|$)"
_TRANSLITERATED = str.maketrans({"Ä": "AE", "Ö": "OE", "Ü": "UE"})


@dataclass(frozen=True)
class Payee:
    """Who a line pays or is paid by. `words`: what two lines are compared on (same_payee),
    upper-case letters only, umlauts written out, a card processor's name dropped; for a payment
    service its merchant's words; () for no payee (no name, digits only, or a payment service
    whose merchant is not read). `name_pattern` and `purpose_pattern`: the matcher a name rule
    learned from the line gets. `known`: False when a service's direct debit was read without
    its purpose (a caller that has only the name), so the merchant is not known."""

    words: tuple[str, ...]
    name_pattern: str | None
    purpose_pattern: str | None = None
    service: PaymentService | None = None
    merchant: str | None = None  # the merchant as the line names it, for a service
    known: bool = True

    @property
    def label(self) -> str | None:
        """„PayPal: DisneyPlus“ for a service's merchant; None for any other payee."""
        if self.service is None or self.merchant is None:
            return None
        return f"{self.service.label}: {self.merchant}"

    @property
    def unreadable(self) -> bool:
        """A payment service whose merchant the line does not name: no payee, no rule."""
        return self.service is not None and self.merchant is None and self.known


_NO_PAYEE = Payee((), None)


def payee_words(text: str) -> tuple[str, ...]:
    """A name's words as payees are compared on: upper-case, Ä/Ö/Ü/ß written out (OE, SS),
    accents dropped, every character that is not a letter (digits, '-', '*', '.', '/') a word
    break."""
    upper = text.upper().translate(_TRANSLITERATED)
    ascii_text = unicodedata.normalize("NFKD", upper).encode("ascii", "ignore").decode()
    return tuple(re.findall(r"[A-Z]+", ascii_text))


def payee(name: str | None, purpose: str | None = None) -> Payee:
    """The line's payee (D160 amended (4)). A payment service's line (PAYMENT_SERVICES):

    - card form, "<digits> PAYPAL *<MERCHANT> <phone> <country>": the merchant after the `*`;
      the name pattern is merchant_pattern's, up to and including the merchant;
    - debit card form, "<Merchant> - PayPal": the merchant before it; the whole name's pattern;
    - direct debit ("PayPal Europe S.a.r.l. et Cie S.C.A", "Klarna Bank AB"): the merchant read
      from the purpose after the service's phrase; the name pattern is the service's word
      (^PAYPAL\\b) and the purpose pattern the phrase and merchant ("Ihr Einkauf bei
      DisneyPlus(?!\\w)"), so both of the service's IBANs and every spelling of its name hit;
      no merchant read -> no payee (`unreadable`), purpose None -> not `known`.

    Any other name: the name cut before a per-purchase token (_merchant_cut) and a card
    processor's name dropped, with merchant_pattern as its pattern."""
    if not name or not name.strip():
        return _NO_PAYEE
    full = _merchant_text(name)
    for service in PAYMENT_SERVICES:
        debit = re.fullmatch(rf"(.*\S)\s+-\s+{service.word}", full)
        if debit is not None and _WORD_MIN.search(debit.group(1)):
            merchant = re.sub(rf"\s+-\s+{service.word}\s*$", "", name.strip(), flags=re.I)
            return _service_payee(service, merchant, merchant_pattern(name))
        if re.match(rf"{service.word}\b", full) is None:
            continue
        cut = _merchant_cut(name)
        if "*" in cut:
            return _service_payee(service, cut.partition("*")[2], merchant_pattern(name))
        return _purpose_payee(service, purpose)
    words = _web_words(payee_words(_DOMAIN.sub("", _merchant_cut(name))))
    if len(words) > 1 and words[0] in CARD_PROCESSORS:
        words = words[1:]
    pattern = merchant_pattern(name)
    return Payee(words, pattern) if words and pattern != "^" else _NO_PAYEE


# A web address's parts that name no payee (D160 amended (4)): "WWW.DAZN.COM Huidekoper…" is
# DAZN. On the raw name a suffix glued to a word by its dot is dropped (".de" too); on a learned
# pattern's text, whose dots are spaces, a leading WWW and a COM/NET/ORG right after the first
# word ("DE" there is too often a word of the name).
_DOMAIN = re.compile(r"(?<=[A-Za-z0-9])\.(?:com|de|net|org|eu|io)\b|^\s*www\.", re.IGNORECASE)
_WEB_SUFFIXES = frozenset({"COM", "NET", "ORG"})


def _web_words(words: tuple[str, ...]) -> tuple[str, ...]:
    if len(words) > 1 and words[0] == "WWW":
        words = words[1:]
    if len(words) > 2 and words[1] in _WEB_SUFFIXES:
        words = (words[0], *words[2:])
    return words


def _service_payee(service: PaymentService, merchant: str, name_pattern: str) -> Payee:
    merchant = merchant.strip(" .:-*")
    words = payee_words(merchant)
    if not words:
        return Payee((), None, service=service)
    return Payee(words, name_pattern, service=service, merchant=merchant)


def _purpose_payee(service: PaymentService, purpose: str | None) -> Payee:
    if purpose is None:
        return Payee((), None, service=service, known=False)
    collapsed = " ".join(purpose.split())
    for phrase in service.purpose_phrases:
        found = re.search(
            rf"\b{re.escape(phrase)}\s+(.+?)\s*{_MERCHANT_END}", collapsed, re.IGNORECASE
        )
        if found is None:
            continue
        merchant = found.group(1).strip(" .:-")
        words = payee_words(merchant)
        if not words:
            continue
        return Payee(
            words,
            rf"^{service.word}\b",
            _as_pattern(phrase).removeprefix("^")
            + " "
            + _as_pattern(merchant).removeprefix("^")
            + r"(?!\w)",
            service=service,
            merchant=merchant,
        )
    return Payee((), None, service=service)


def _same_words(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """One payee (D160 amended (4)), both ways: either name's words start the other's ("EPRIMO
    GMBH" and "EPRIMO GMBH FLUGHAFENSTRASE"; one generic word alone, "HOTEL", is no payee), or
    the first two words are the same ("NETTO MARKEN DISCOUNT …", "BURGER KING …"; not with a
    linking second word, "HOTEL AT …", nor fewer than FIRST_WORD_MIN letters, "G A …"), or the
    first word is, when it is not generic and has FIRST_WORD_MIN letters or more
    ("VOLKSWAGEN AUTOVERSICHERUNG AG" and "VOLKSWAGEN AUTOVERS AG"). Never for no payee."""
    if not a or not b:
        return False
    short, long = sorted((a, b), key=len)
    if long[: len(short)] == short and not (len(short) == 1 and short[0] in GENERIC_FIRST_WORDS):
        return True
    if (
        len(short) >= 2
        and a[:2] == b[:2]
        and a[1] not in _LINKING_WORDS
        and len(a[0]) + len(a[1]) >= FIRST_WORD_MIN
    ):
        return True
    return a[0] == b[0] and len(a[0]) >= FIRST_WORD_MIN and a[0] not in GENERIC_FIRST_WORDS


def payee_key(name: str | None, purpose: str | None = None) -> str:
    """The payee's words as one text ("" for no payee), as shared_iban counts them."""
    return " ".join(payee(name, purpose).words)


def shared_iban(lines: Iterable[str | None | tuple[str | None, str | None]]) -> bool:
    """Whether an IBAN hash is shared: its private lines, by counterparty name or by (name,
    purpose), hold two payees that are not one payee (same_payee: a longer spelling of a name
    is no second payee, a payment service's merchants are; no name is no payee)."""
    seen: list[tuple[str, ...]] = []
    for line in lines:
        name, purpose = line if isinstance(line, tuple) else (line, None)
        words = payee(name, purpose).words
        if not words or words in seen:
            continue
        if any(not _same_words(words, other) for other in seen):
            return True
        seen.append(words)
    return False


def same_payee(
    name: str | None,
    other: str | None,
    purpose: str | None = None,
    other_purpose: str | None = None,
) -> bool:
    """Whether two lines have one payee (D160 amended (4), _same_words), both ways, so the
    lines of a rule learned on the shorter spelling count the longer one's as its kind and the
    reverse ("PayPal Europe … S.C.A" and "PayPal Europe … S.C.A 22-24 Boulevard …" had not
    been). A payment service's line is its merchant's. False when either has no payee or its
    merchant is not known."""
    return _same_words(payee(name, purpose).words, payee(other, other_purpose).words)


def _learned_text(pattern: str) -> str | None:
    """The text a learned name pattern was built from (merchant_pattern: "^" and the escaped
    normalised name), or None for any other pattern (a person's own regular expression, which
    keeps its plain regular-expression meaning)."""
    if not pattern.startswith("^"):
        return None
    text = re.sub(r"\\(.)", r"\1", pattern[1:])
    return text if _as_pattern(_normalised(text)) == pattern and text else None


def _payee_hit(pattern: str, name: str | None, purpose: str | None) -> bool:
    """Whether a learned name pattern (_learned_text) names the line's payee (same_payee): a
    rule learned on one spelling hits the others. A pattern naming a payment service without
    its merchant hits nothing this way."""
    text = _learned_text(pattern)
    if text is None or name is None:
        return False
    learned = payee(text)
    if learned.service is not None and learned.merchant is None:
        return False
    return _same_words(learned.words, payee(name, purpose).words)


def _names_hit(
    name_pattern: str, purpose_pattern: str | None, name: str | None, purpose: str | None
) -> bool:
    """A rule's name pattern (and purpose pattern) on a line: the patterns as regular
    expressions (_name_found, _found), or, for a learned name pattern without a purpose
    pattern, the line's payee (_payee_hit). A purpose of None is not known (a caller that has
    only the name): the purpose pattern then counts as a hit, so a check never passes on
    missing evidence."""
    if purpose_pattern is not None:
        if purpose is not None and not _found(purpose_pattern, purpose):
            return False
        return _name_found(name_pattern, name)
    return _name_found(name_pattern, name) or _payee_hit(name_pattern, name, purpose)


def rule_name_hits(tx: MatchTransaction, name: str | None, purpose: str | None) -> bool:
    """Whether the name rule learned from `tx` (payee) would hit a line with this name and
    purpose, as rule_hits reads it: the lines its rule books, counted and re-booked by
    „Künftig immer so zuordnen“. False when `tx` gives no payee."""
    found = payee(tx.counterparty_name, tx.purpose)
    if found.name_pattern is None:
        return False
    return _names_hit(found.name_pattern, found.purpose_pattern, name, purpose)


def service_only(matcher: RuleMatcher) -> bool:
    """D160 amended (4): a rule on a payment service without its merchant: no purpose
    pattern, and its name pattern names the service but no merchant, like the rule learned on
    "^PAYPAL EUROPE S A R L ET CIE S C A" before 14 Nov 2026 (it booked every PayPal payment
    of its amount as one merchant's)."""
    pattern = matcher.counterparty_name_pattern
    if matcher.purpose_pattern is not None or pattern is None:
        return False
    text = _learned_text(pattern)
    if text is None:
        return False
    learned = payee(text)  # no purpose: a service's name form has no merchant here
    return learned.service is not None and learned.merchant is None


def service_blind(matcher: RuleMatcher, tx: MatchTransaction) -> bool:
    """Whether the rule cannot tell which merchant a payment service's line pays: the line is
    a service's (payee) and the rule names no merchant (no purpose pattern, and no name
    pattern or a service_only one), e.g. an IBAN-only rule on PayPal's IBAN. Such a rule is
    disabled on read for that line (apply_rule, services.matching.waiting_lines): it would book
    every merchant's payment through the service as the one it was learned on."""
    if payee(tx.counterparty_name, tx.purpose).service is None:
        return False
    if matcher.purpose_pattern is not None:
        return False
    return matcher.counterparty_name_pattern is None or service_only(matcher)


def iban_only(matcher: RuleMatcher) -> bool:
    """A rule on the IBAN hash alone (and the sign): no name or purpose pattern narrows it."""
    return (
        matcher.counterparty_iban_hmac is not None
        and matcher.counterparty_name_pattern is None
        and matcher.purpose_pattern is None
    )


def learnable_household(category: AllocationCategory) -> bool:
    """D160: a household code with a threshold that is not tax-relevant, or bank_fees (the
    shared code a private account's own fee lines take; they carry no counterparty, so their
    rule is a typed purpose pattern). A tax-relevant code proposes a return input a person
    confirms each time; a transfer or depot code is booked by its matcher only; a code
    without a threshold (hh_other, hh_credit_card, uncategorized) is never booked unseen."""
    return (
        (category in HOUSEHOLD_CODES or category in LEARNABLE_SHARED)
        and category not in TAX_RELEVANT
        and THRESHOLDS[category] is not None
    )


def name_hits(tx: MatchTransaction, names: Sequence[NameHistory]) -> list[NameHistory]:
    """The confirmed lines a name rule learned from `tx` would hit (rule_name_hits), as
    _name_rule checks them: the amount has tx's sign (a refund of the same merchant never
    counts against a debit rule, nor a purchase against a refund rule), not tx itself. Empty
    when the line gives no payee or tx is 0."""
    if tx.amount == 0:
        return []
    credit = tx.amount > 0
    return [
        h
        for h in names
        if h.transaction_id != tx.id
        and (h.amount is None or (h.amount != 0 and (h.amount > 0) == credit))
        and rule_name_hits(tx, h.counterparty_name, h.purpose)
    ]


def _name_rule(
    tx: MatchTransaction,
    confirmed: AllocationSet,
    names: Sequence[NameHistory],
) -> MatchingRuleCreate | None:
    """D160: on a private account, a single-category answer of a learnable household code for
    a line without an IBAN becomes a rule on the payee's name. Not for a split. Refused when
    the account's confirmed lines the rule hits carry two or more categories (rule.too_broad:
    the name does not tell the kind, so the card asks for a purpose pattern) or one category
    other than the answer's (rule.conflicts); too_broad is checked first. Only lines of the
    rule's amount sign count (name_hits).

    D160 amended (4), 14 Nov 2026: the rule is the payee's (payee): a payment service's
    direct debit learns the service's name plus the merchant's purpose pattern, a card form the
    name up to the merchant, and a line whose merchant cannot be read learns none
    (rule.no_merchant). It never carries the IBAN, also for a line on a shared IBAN (which
    names no payee) or a service's IBAN, so it books the same payee's lines on every private
    account, a card statement's without an IBAN included. A rule learned earlier with the IBAN
    stays as it was."""
    if len(confirmed.allocations) != 1:
        return None
    (only,) = confirmed.allocations
    if not learnable_household(only.category):
        return None
    found = payee(tx.counterparty_name, tx.purpose)
    if found.unreadable:
        raise RuleError(RULE_NO_MERCHANT, "the payment service names no merchant here")
    if found.name_pattern is None:
        return None  # no name (or digits and dots only): nothing to match on
    hits = {h.category for h in name_hits(tx, names)}
    if len(hits) >= 2:
        raise RuleError(RULE_TOO_BROAD, "the name was confirmed under two categories")
    if hits and hits != {only.category}:
        raise RuleError(RULE_CONFLICTS, "the pattern hits a line confirmed otherwise")
    return MatchingRuleCreate(
        matcher=RuleMatcher(
            counterparty_name_pattern=found.name_pattern,
            purpose_pattern=found.purpose_pattern,
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
    property bill matched on a private line is match_private_document's hh_property_cost,
    D161)."""
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
# D158 on a private account: the transfer by an own IBAN (D162), a payment to the seller
# (14 Nov 2026), a bill already recorded as a Nebenkosten or private_cost row (D161 widened),
# the document match (a bill of the property becomes hh_property_cost, D161), then the
# tenant / lender question.
_PRIVATE_ORDER: tuple[Callable[[MatchTransaction, MatchContext], AllocationSet | None], ...] = (
    lambda tx, context: match_transfer_line(tx, context),  # defined below
    match_down_payment,
    match_recorded_bill,
    match_private_document,
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
    a by_wirtschaftsplan rule), as in phase 2. Private: learned rules -> transfer by an own
    IBAN (D162) -> a payment to the seller -> a recorded bill -> a property bill (D161) ->
    D158's question; the rent, loan, Hausgeld and
    owner-transfer matchers never run there. A standing rule hit (confidence 1) wins at once;
    a rule's near-miss proposal (D49) only when no matcher answers. Every answer is one the
    account's purpose takes (D159)."""
    found = rule_answer(rules, tx, context, history)
    if found is not None and found.confidence == EXACT:
        return found
    order = _PRIVATE_ORDER if context.account_purpose is AccountPurpose.PRIVATE else _RENTAL_ORDER
    for matcher in order:
        answer = _allowed_set(matcher(tx, context), context)
        if answer is not None:
            return answer
    return found


# --- Internal transfers (D162; fixture transfers.yaml) -------------------------------------

TRANSFER_WINDOW_BANKING_DAYS = 3
TRANSFER_CODES = {
    (AccountPurpose.PRIVATE, False): C.INTERNAL_TRANSFER_OUT,
    (AccountPurpose.PRIVATE, True): C.INTERNAL_TRANSFER_IN,
    (AccountPurpose.RENTAL, False): C.OWNER_TRANSFER_OUT,
    (AccountPurpose.RENTAL, True): C.OWNER_TRANSFER_IN,
}
TRANSFER_CATEGORIES = frozenset(TRANSFER_CODES.values())
_CASH_KINDS = (AccountKind.EXCHANGE, AccountKind.DEPOT)


def add_banking_days(day: date, n: int) -> date:
    """Monday to Friday, no holiday calendar (the fixture's step 0 reading)."""
    while n:
        day = date.fromordinal(day.toordinal() + 1)
        if day.weekday() < 5:
            n -= 1
    return day


def transfer_window_end(booked_on: date) -> date:
    """The last day a counterpart of a leg booked on `booked_on` may be booked: three banking
    days later. A leg without one gets its card only after this day."""
    return add_banking_days(booked_on, TRANSFER_WINDOW_BANKING_DAYS)


def transfer_link_kind(a: AccountKind, b: AccountKind) -> TransferLinkKind:
    kinds = {a, b}
    if AccountKind.EXCHANGE in kinds:
        return TransferLinkKind.EXCHANGE_EUR
    if AccountKind.DEPOT in kinds:
        return TransferLinkKind.DEPOT_CASH
    return TransferLinkKind.BANK


def _ordered(a: TransferLeg, b: TransferLeg) -> tuple[TransferLeg, TransferLeg]:
    return (a, b) if a.amount < 0 else (b, a)


def legs_fit(out: TransferLeg, in_: TransferLeg, *, exact: bool = False) -> bool:
    """D162: the in-leg booked on or after the out-leg and within three banking days of it;
    the amounts equal to the cent, or, when one side is an exchange (and not `exact`), the
    in-leg less than the out-leg by at most the fee either side recorded."""
    if not out.booked_on <= in_.booked_on <= transfer_window_end(out.booked_on):
        return False
    gap = -out.amount - in_.amount  # what the exchange kept
    exchange = AccountKind.EXCHANGE in {out.account_kind, in_.account_kind}
    if exact or not exchange:
        return gap == 0
    fee = (out.fee_eur or Decimal("0")) + (in_.fee_eur or Decimal("0"))
    return Decimal("0") <= gap <= fee


def ibans_agree(a: TransferLeg, b: TransferLeg) -> bool:
    """D162: two legs may be one transfer only when neither names another account: a leg's
    counterparty IBAN, when known, is the other leg's account IBAN, when that is known. So a
    third party's line of the same amount is never the counterpart (an exchange or depot
    account without an IBAN still pairs with the bank line to its bank)."""

    def names_other(x: TransferLeg, y: TransferLeg) -> bool:
        return (
            x.counterparty_iban_hmac is not None
            and y.account_iban_hmac is not None
            and x.counterparty_iban_hmac != y.account_iban_hmac
        )

    return not names_other(a, b) and not names_other(b, a)


def _candidate(a: TransferLeg, b: TransferLeg) -> TransferCandidate:
    out, in_ = _ordered(a, b)
    return TransferCandidate(
        out_leg=out, in_leg=in_, kind=transfer_link_kind(out.account_kind, in_.account_kind)
    )


def transfer_category(leg: TransferLeg, context: MatchContext) -> AllocationCategory | None:
    """The transfer code a leg is booked as: by an own account's IBAN (internal_transfer_* on
    a private account, owner_transfer_* on the rental one, phase 2 unchanged), else the
    depot or exchange leg's own transfer type; None for anything else."""
    if leg.amount == 0:
        return None
    if leg.counterparty_iban_hmac is not None and (
        leg.counterparty_iban_hmac in context.own_account_hmacs
    ):
        return TRANSFER_CODES[(context.account_purpose, leg.amount > 0)]
    if leg.category in TRANSFER_CATEGORIES:
        return leg.category
    return None


def match_transfer(leg: TransferLeg, context: MatchContext, *, today: date) -> TransferMatch:
    """D162: one leg of a possible transfer against the other own accounts' open legs
    (context.open_legs; the line's own account is context.account_purpose's). A leg booked by
    an own IBAN looks for its counterpart on the account that IBAN names, a depot or exchange
    leg booked by its type on every other account: one counterpart -> link (written by the
    matcher), several -> transfer.ambiguous with the candidates, none -> no card until the
    window has passed (`today` after its third banking day), then transfer.unmatched_leg. A
    leg that is neither, whose amount and date fit exactly one exchange or depot EUR movement,
    is proposed as a link for review, never written, and keeps no category. A candidate
    whose IBANs name another account than the leg's is never one (ibans_agree)."""
    credit = leg.amount > 0
    other = [
        o
        for o in context.open_legs
        if o.account_id != leg.account_id
        and o.amount != 0
        and (o.amount > 0) != credit
        and ibans_agree(leg, o)
    ]
    category = transfer_category(leg, context)
    if category is None:
        fitting = [
            o
            for o in other
            if o.account_kind in _CASH_KINDS and legs_fit(*_ordered(leg, o), exact=True)
        ]
        proposed = _candidate(leg, fitting[0]) if len(fitting) == 1 else None
        return TransferMatch(proposed_link=proposed)
    if leg.counterparty_iban_hmac in context.own_account_hmacs:
        other = [o for o in other if o.account_iban_hmac == leg.counterparty_iban_hmac]
    fitting = [o for o in other if legs_fit(*_ordered(leg, o))]
    result = TransferMatch(category=category, confidence=EXACT)
    if len(fitting) == 1:
        return result.model_copy(update={"link": _candidate(leg, fitting[0])})
    if len(fitting) > 1:
        return result.model_copy(
            update={"card": TRANSFER_AMBIGUOUS, "candidates": sorted(o.id for o in fitting)}
        )
    if today > transfer_window_end(leg.booked_on):
        return result.model_copy(update={"card": TRANSFER_UNMATCHED_LEG})
    return result


def match_transfer_line(tx: MatchTransaction, context: MatchContext) -> AllocationSet | None:
    """D162 in the private order: a line whose counterparty IBAN is another own account's is a
    transfer by the IBAN alone, internal_transfer_out|in at confidence 1.0, booked before its
    counterpart is linked (services.transfers links it), so a transfer is never mis-booked
    while the other account's import lags. The rental account keeps match_owner_transfer."""
    if tx.amount == 0 or tx.counterparty_iban_hmac is None:
        return None
    if tx.counterparty_iban_hmac not in context.own_account_hmacs:
        return None
    category = TRANSFER_CODES[(context.account_purpose, tx.amount > 0)]
    period = service_period(tx.purpose, tx.booking_date, False)
    return AllocationSet(
        transaction_id=tx.id,
        method=AllocationMethod.MATCHER_TRANSFER,
        confidence=EXACT,
        allocations=[_draft(tx.amount, category, period, False)],
        reasoning="Überweisung zwischen zwei eigenen Konten.",
    )
