"""Steuern as one guided path (phase 10 §7.6, D290): which step each open point belongs to,
who sends each Beleg and when it usually comes, and the step states. Pure: the service
(services/tax_guide.py) reads the drafts, the checklists and the questionnaire and hands their
results here; nothing in this module computes a tax figure.

The four steps, in order: Eure Angaben, Immobilie (one per property owned in the year), Belege,
Prüfen und abgeben. The Jahresfragen are no step of their own since phase 10 §7.8 (b), D294: an
open question about an owner or the household is open in Eure Angaben, one about a property in
that property's Immobilie step (rules/angaben_topics.py sorts Eure Angaben into its topics).

- STEP_OF_WARNING names the step of every warning code the frontend lists as a to-do
  (components/todos.tsx, `todo: true`); tests/rules/test_tax_guide.py keeps the two equal. A
  code that is only a note (`todo: false`) is no point of any step.
- EXPECTED is the rule data beside the checklist catalogue (rules/document_checklist.py): for
  each requirement, whether it is a Beleg of the Belege step (price_split and market_rent are
  values the household types, so they are points of the Immobilie step) and who sends it, with
  the months it usually comes in, as an offset from the tax year. WAGE_CERTIFICATE is the same
  for each owner's Lohnsteuerbescheinigung, which no checklist holds. These months are a hint
  for the screen („meist im Januar“), never a due date: the due dates stay the checklist's and
  the reminders' own.

Kept out of rules/health and rules/plan on purpose: services.health.rule_version() hashes those
two folders into every stored key-figure row, and this module changes no stored figure.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from hausbuch.schemas.booked_inputs import BookedGroup, BookedProposal
from hausbuch.schemas.checklist import ChecklistStatus, DocumentChecklistItem, RequirementCode
from hausbuch.schemas.employment import OpenQuestion
from hausbuch.schemas.tax import DraftWarning
from hausbuch.schemas.tax_guide import (
    GuideDocument,
    GuidePoint,
    GuideSender,
    GuideStep,
    GuideStepKind,
    GuideStepState,
)

R = RequirementCode

# --- The step of each to-do ----------------------------------------------------------------

# "documents": the point is a Beleg of the Belege step while that Beleg is not there
# (COVERED_BY_DOCUMENT); once it is, the point is the review step's.
STEP_OF_WARNING: dict[str, GuideStepKind] = {
    # Eure Angaben: the owners' data, pay and the household (Steuern › „Eure Angaben“).
    "anlage_n.church_mismatch": "angaben",
    "anlage_n.days_implausible": "angaben",
    "anlage_n.no_pay_overruled": "angaben",
    "return.progression_provisional": "angaben",
    "return.benefit_possible_double": "angaben",
    "return.benefit_booked": "angaben",
    "return.kindergeld_apply": "angaben",
    # Immobilie: the property's own values and decisions.
    "afa.no_split": "property",
    "tax.price_renovation_ask": "property",
    "tax.price_renovation_not_applied": "property",
    "afa.acquisition_cost_without_row": "property",
    "afa.movable_asset_exceeds_building": "property",
    "income.pre_handover": "property",
    "income.living_area_missing": "property",
    "reduced_rent.no_market_rent": "property",
    "reduced_rent.partial": "property",
    # The Jahresfragen: the open questions themselves are counted (Eure Angaben, or the
    # property's step), so these two warnings, which only say that some are open, are never
    # a point of their own (QUESTIONNAIRE_CODES).
    "return.questionnaire_open": "angaben",
    "tax_export.questions_open": "angaben",
    # Belege: the document itself, while it is not there.
    "anlage_n.certificate_missing": "documents",
    "hausgeld.abrechnung_pending": "documents",
    # Prüfen und abgeben: a payment or a line of a form to settle.
    "payment_proof.missing": "review",
    "limit.prior_year_amendment": "review",
    "limit.private_cost_undated": "review",
    "limit.private_cost_after_window": "review",
    "anschaffungsnah.performance_date_check": "review",
    "renovation.undecided": "review",
    "income.other_review": "review",
    "draft.open_lines_excluded": "review",
    "draft.allocation_without_property": "review",
    "tax.private_cost_paid_from_account": "review",
    "anlage_n.periods_overlap": "review",
    "anlage_n.payslip_gap": "review",
}

# The notes (`todo: false` in todos.tsx) that carry something to check, listed on the form
# views only until phase 10 §7.6 wave 2's open point: each is a point of its step with the
# note's action, never blocking (it says „stimmt es, ist nichts zu tun“), so it never holds a
# step, a form card or the year open. tests/rules/test_tax_guide.py keeps this map to the
# notes with an action in todos.tsx (plus return.child_over_18, whose step is ELSTER's Anlage
# Kind and carries no button).
NOTE_STEP: dict[str, Literal["angaben", "property", "review"]] = {
    "afa.cost_row_unpaid": "property",
    "afa.purchase_price_mismatch": "property",
    "afa.movable_asset_no_evidence": "property",
    "umlagen.mixed_terms": "property",
    "reduced_rent.prognosis_needed": "property",
    "limit.estimated_net": "review",
    "interest.certificate_mismatch": "review",
    "tax.travel_frequency": "review",
    "anlage_n.receipt_missing": "review",
    "anlage_n.work_room_centre": "angaben",
    "anlage_n.work_room_partial": "angaben",
    "anlage_n.first_workplace_check": "angaben",
    "return.children_mismatch": "angaben",
    "return.child_over_18": "angaben",
    "return.childcare_until_14": "angaben",
}

QUESTIONNAIRE_CODES = frozenset({"return.questionnaire_open", "tax_export.questions_open"})

# The Beleg a "documents" point stands for: the property's checklist requirement, or the
# owner's wage certificate.
WAGE_CERTIFICATE_KIND = "wage_certificate"
COVERED_BY_DOCUMENT: dict[str, str] = {
    "anlage_n.certificate_missing": WAGE_CERTIFICATE_KIND,
    "hausgeld.abrechnung_pending": R.HAUSGELDABRECHNUNG.value,
}

# The checklist gaps that are values the household types: the Immobilie step's points, left
# out while a draft warning already stands for the same gap (todos.tsx `coveredByWarning`).
PROPERTY_REQUIREMENTS: dict[RequirementCode, str] = {
    R.PRICE_SPLIT: "afa.no_split",
    R.MARKET_RENT: "reduced_rent.no_market_rent",
}

# The owner without any pay source for the year (RuleError anlage_n.no_pay from the Anlage N
# draft): a point of Eure Angaben.
NO_PAY_CODE = "anlage_n.no_pay"
# The owner who answered in the Jahresfragen that a wage-replacement benefit came in the year,
# but whose amount is not typed yet (the whole-return estimate's MissingItem `benefit_letter`,
# which raises no warning): a point of Eure Angaben, one per owner.
BENEFIT_LETTER_CODE = "anlage_n.benefit_letter"

# An input the rules refuse in a year that has rules (a draft's RuleError naming a typed row,
# not a missing rule set): a point of Eure Angaben naming the refusal, so „bereit“ is held by
# something the household can fix instead of a general sentence (phase 10 §7.6, wave 2's open
# point). The year stays incomplete (the service keeps the source in `unavailable`).
REFUSED_CODE = "return_input.refused"
REFUSAL_CODES = frozenset(
    {
        "anlage_n.useful_life_missing",
        "anlage_n.work_room_excludes_day_rate",
        "return_input.scope_invalid",
        "return.childcare_age",
        "return.household_services_cash",
    }
)

# The bookings that propose a tax input (phase 10 §7.8 (a), D293, rules/tax/booked_inputs.py):
# one point of Eure Angaben per proposal, with its POST /return-inputs payload. The benefit
# proposal keeps the code of the estimate's to-do it replaces; when the service hands the
# proposals in, that warning is no separate point.
PROPOSAL_CODE: dict[BookedGroup, str] = {
    "benefit": "return.benefit_booked",
    "donation": "return.donation_booked",
    "childcare": "return.childcare_booked",
    "household_services": "return.services_booked",
}


# --- Who sends a Beleg, and when ----------------------------------------------------------


@dataclass(frozen=True)
class Expectation:
    """`sender` sends it; `months` = (years after the tax year, first month, last month) it
    usually comes in, None for a document of no particular month (a contract, a receipt)."""

    sender: GuideSender | None
    months: tuple[int, int, int] | None = None
    beleg: bool = True  # False: a value the household types, not a document of the Belege

    def usual(self, tax_year: int) -> tuple[date | None, date | None]:
        if self.months is None:
            return None, None
        offset, first, last = self.months
        return date(tax_year + offset, first, 1), date(tax_year + offset, last, 1)


EXPECTED: dict[RequirementCode, Expectation] = {
    R.PURCHASE_CONTRACT: Expectation("notary"),
    R.PRICE_SPLIT: Expectation(None, beleg=False),
    R.ACQUISITION_COST_EVIDENCE: Expectation("issuer"),
    # Usually sent in January of the year it is for (phase 5 D126's due day, 15 January).
    R.GRUNDSTEUERBESCHEID: Expectation("municipality", (0, 1, 1)),
    R.LOAN_CONTRACT: Expectation("bank"),
    # Banks send the year's interest certificate in January of the next year.
    R.ZINSBESCHEINIGUNG: Expectation("bank", (1, 1, 1)),
    R.LEASE: Expectation("lease_parties"),
    R.HANDOVER_PROTOCOL: Expectation("lease_parties"),
    R.DEPOSIT_RECORD: Expectation("tenant"),
    R.WIRTSCHAFTSPLAN: Expectation("management"),
    # After the owners' meeting, usually May to June of the next year (due 30 June).
    R.HAUSGELDABRECHNUNG: Expectation("management", (1, 5, 6)),
    R.COST_RECEIPT: Expectation("issuer"),
    R.MARKET_RENT: Expectation(None, beleg=False),
}

# §41b Abs. 1 S. 3 EStG: the employer hands the certificate out by the end of February; the
# reminder `tax.wage_certificate_missing` is due on 1 March (rules/reminders.py).
WAGE_CERTIFICATE = Expectation("employer", (1, 2, 2))


def wage_certificate_due(tax_year: int) -> date:
    return date(tax_year + 1, 3, 1)


# --- Inputs and the guide -------------------------------------------------------------------


@dataclass(frozen=True)
class PropertyFacts:
    """One property owned in the tax year: its checklist items (None: not readable) and its
    Anlage V warnings (None: no draft for the year, e.g. no rule set)."""

    property_id: object
    checklist: Sequence[DocumentChecklistItem] | None
    warnings: Sequence[DraftWarning] | None


@dataclass(frozen=True)
class OwnerFacts:
    """One owner: the Anlage N status ("certified", "estimate", "none"; None: no draft, see
    no_pay), its warnings, whether the draft refused for want of any pay source, whether a
    wage-replacement benefit announced in the Jahresfragen is still to be typed, and the code
    of an input the draft refused (REFUSAL_CODES)."""

    owner_id: object
    status: Literal["certified", "estimate", "none"] | None
    warnings: Sequence[DraftWarning]
    no_pay: bool = False
    benefit_missing: bool = False
    refused: str | None = None

    @property
    def failed(self) -> bool:
        """No Anlage N for the year, for another reason than no pay at all."""
        return self.status is None and not self.no_pay


@dataclass(frozen=True)
class GuideInput:
    tax_year: int
    today: date
    properties: Sequence[PropertyFacts]
    owners: Sequence[OwnerFacts]
    estimate_warnings: Sequence[DraftWarning]
    questions_answered: int | None  # None: no questionnaire for the year
    questions_total: int | None
    # The open Jahresfragen with their owner and property: each counts on that owner's Anlage N
    # or that property's Anlage V card (a household question on no card), and in its step: an
    # owner's or the household's in Eure Angaben, a property's in that property's Immobilie step
    # (in Eure Angaben when the property has no step in the year).
    open_questions: Sequence[OpenQuestion] = ()
    # A source could not be read for the year (the service's `unavailable`: no rule set, a
    # draft refused): the year cannot be finished, so Prüfen und abgeben is never done.
    incomplete: bool = False
    # The bookings that propose a tax input (rules/tax/booked_inputs.booked_proposals). None:
    # not read (the estimate's `return.benefit_booked` warnings then stay points as before);
    # a list, even empty: those warnings are left to the proposals.
    proposals: Sequence[BookedProposal] | None = None
    # The whole-return estimate could not be computed (its warnings, which are points of Eure
    # Angaben, are unknown), and the code when it refused an input (REFUSAL_CODES).
    estimate_failed: bool = False
    estimate_refused: str | None = None


@dataclass(frozen=True)
class GuideResult:
    steps: list[GuideStep]
    points: list[GuidePoint]
    documents: list[GuideDocument]
    next_step: int | None
    waiting_from: date | None
    open_questions: Sequence[OpenQuestion] = ()

    def form_open(
        self, form: Literal["anlage_v", "anlage_n"], *, property_id=None, owner_id=None
    ) -> int:
        """What is still open for one form card, whichever step clears it: the points listed
        for the form, its Belege not there yet (the property's checklist documents, the owner's
        wage certificate) and the open Jahresfragen about that owner or property. A point a
        missing Beleg stands for is not listed while the Beleg is missing (`_covered`), so
        nothing counts twice."""

        def mine(p_id, o_id) -> bool:
            if form == "anlage_v":
                return property_id is not None and p_id == property_id
            return owner_id is not None and o_id == owner_id

        points = sum(
            1
            for p in self.points
            if p.blocking and p.form == form and mine(p.property_id, p.owner_id)
        )
        belege = sum(
            1 for d in self.documents if d.status != "present" and mine(d.property_id, d.owner_id)
        )
        questions = sum(1 for q in self.open_questions if mine(q.property_id, q.owner_id))
        return points + belege + questions

    @property
    def steps_done(self) -> int:
        return sum(1 for s in self.steps if s.state == "done")

    @property
    def ready(self) -> bool:
        """Every step done: the ELSTER-Ausfüllhilfe can be made without open points."""
        return all(s.state == "done" for s in self.steps)


def _document_status(status: ChecklistStatus) -> Literal["present", "missing", "expected"]:
    if status is ChecklistStatus.PRESENT:
        return "present"
    if status is ChecklistStatus.EXPECTED:
        return "expected"
    return "missing"  # missing, or linked without its tax year (incomplete)


def documents(facts: GuideInput) -> list[GuideDocument]:
    """The Belege: every property's checklist documents, then every owner's wage certificate
    while an Anlage N is drafted from something else than certificates."""
    out: list[GuideDocument] = []
    for prop in facts.properties:
        for item in prop.checklist or ():
            expectation = EXPECTED[item.code]
            if not expectation.beleg:
                continue
            usual_from, usual_to = expectation.usual(facts.tax_year)
            out.append(
                GuideDocument(
                    kind=item.code.value,
                    status=_document_status(item.status),
                    property_id=prop.property_id,
                    item=item,
                    due_on=item.due_on,
                    sender=expectation.sender,
                    usual_from=usual_from,
                    usual_to=usual_to,
                )
            )
    for owner in facts.owners:
        if owner.status is None or owner.status == "none":
            continue
        usual_from, usual_to = WAGE_CERTIFICATE.usual(facts.tax_year)
        due = wage_certificate_due(facts.tax_year)
        status: Literal["present", "missing", "expected"]
        if owner.status == "certified":
            status = "present"
        else:
            status = "expected" if due > facts.today else "missing"
        out.append(
            GuideDocument(
                kind=WAGE_CERTIFICATE_KIND,
                status=status,
                owner_id=owner.owner_id,
                due_on=due,
                sender=WAGE_CERTIFICATE.sender,
                usual_from=usual_from,
                usual_to=usual_to,
            )
        )
    return out


def _covered(code: str, docs: list[GuideDocument], property_id, owner_id) -> bool:
    """A "documents" point is the Beleg's while that Beleg is not there."""
    kind = COVERED_BY_DOCUMENT.get(code)
    return kind is not None and any(
        d.kind == kind
        and d.status != "present"
        and d.property_id == property_id
        and d.owner_id == owner_id
        for d in docs
    )


def points(facts: GuideInput, docs: list[GuideDocument]) -> list[GuidePoint]:
    """Every open point of the steps Eure Angaben, Immobilie and Prüfen, in step order and,
    inside a step, in the order the forms list them. The same warning twice (one rule read by
    two forms) is one point."""
    out: list[GuidePoint] = []
    seen: set[tuple] = set()

    def add(warning: DraftWarning, form, property_id=None, owner_id=None) -> None:
        if facts.proposals is not None and warning.code.value == PROPOSAL_CODE["benefit"]:
            return  # the benefit proposal below stands for it, with its payload
        note = warning.code.value in NOTE_STEP
        step = NOTE_STEP.get(warning.code.value) or STEP_OF_WARNING.get(warning.code.value)
        if step is None or warning.code.value in QUESTIONNAIRE_CODES:
            return
        if note and step == "property" and property_id is None:
            step = "review"  # a property's note read without its property: Prüfen
        if step == "documents":
            if _covered(warning.code.value, docs, property_id, owner_id):
                return
            step = "review"
        # The refs belong to the key: two payments of the same amount from the same IBAN raise
        # two warnings with equal params, and each is its own point.
        refs = tuple(sorted((r.kind, str(r.id)) for r in warning.refs))
        key = (
            warning.code.value,
            tuple(sorted(warning.params.items())),
            refs,
            property_id,
            owner_id,
        )
        if key in seen:
            return
        seen.add(key)
        out.append(
            GuidePoint(
                step=step,
                form=form,
                code=warning.code.value,
                property_id=property_id,
                owner_id=owner_id,
                warning=warning,
                note=note,
                blocking=not note,
            )
        )

    for owner in facts.owners:
        if owner.no_pay:
            out.append(
                GuidePoint(
                    step="angaben", form="anlage_n", code=NO_PAY_CODE, owner_id=owner.owner_id
                )
            )
        if owner.benefit_missing:
            out.append(
                GuidePoint(
                    step="angaben",
                    form="anlage_n",
                    code=BENEFIT_LETTER_CODE,
                    owner_id=owner.owner_id,
                )
            )
        if owner.refused:
            out.append(
                GuidePoint(
                    step="angaben",
                    form="anlage_n",
                    code=REFUSED_CODE,
                    owner_id=owner.owner_id,
                    refused=owner.refused,
                )
            )
        for warning in owner.warnings:
            add(warning, "anlage_n", owner_id=owner.owner_id)
    # The estimate drafts every Anlage N too: a refusal an owner's point already names is not
    # a second point.
    owners_refused = {o.refused for o in facts.owners if o.refused}
    if facts.estimate_refused and facts.estimate_refused not in owners_refused:
        out.append(
            GuidePoint(
                step="angaben", form="return", code=REFUSED_CODE, refused=facts.estimate_refused
            )
        )
    for warning in facts.estimate_warnings:
        add(warning, "return")
    for proposal in facts.proposals or ():
        out.append(
            GuidePoint(
                step="angaben",
                form="return",
                code=PROPOSAL_CODE[proposal.group],
                owner_id=proposal.owner_id,
                proposal=proposal,
                blocking=proposal.blocking,
            )
        )
    for prop in facts.properties:
        warnings = list(prop.warnings or ())
        codes = {w.code.value for w in warnings}
        for warning in warnings:
            add(warning, "anlage_v", property_id=prop.property_id)
        for item in prop.checklist or ():
            covering = PROPERTY_REQUIREMENTS.get(item.code)
            if covering is None or covering in codes:
                continue
            if item.status in (ChecklistStatus.MISSING, ChecklistStatus.INCOMPLETE):
                out.append(
                    GuidePoint(
                        step="property",
                        form="anlage_v",
                        code=item.code.value,
                        property_id=prop.property_id,
                        item=item,
                    )
                )
    order = {"angaben": 0, "property": 1, "review": 2}
    return sorted(out, key=lambda p: order[p.step])  # stable: keeps the forms' order


def _month(day: date) -> date:
    return day.replace(day=1)


def guide(facts: GuideInput) -> GuideResult:
    """The steps with their states, the points, the Belege and „Jetzt dran“: the first step
    with something to do today. When nothing is open but a Beleg is still to come,
    `waiting_from` is the first month one of them usually comes (never before this month).
    Prüfen und abgeben is "done" only when every other step is and every source could be read
    (not `incomplete`); until then it is "open" with its own points, else "waiting", so it
    never reads „bereit“ while another step is open or a part of the year cannot be computed."""
    docs = documents(facts)
    pts = points(facts, docs)

    def open_points(step: GuideStepKind, property_id=None) -> int:
        return sum(
            1
            for p in pts
            if p.blocking
            and p.step == step
            and (property_id is None or p.property_id == property_id)
        )

    # The open Jahresfragen count in their step (D294): a property's in its Immobilie step, any
    # other in Eure Angaben, so a question about a property without a step still holds the year.
    with_step = {prop.property_id for prop in facts.properties}

    def open_questions(property_id=None) -> int:
        if property_id is not None:
            return sum(1 for q in facts.open_questions if q.property_id == property_id)
        return sum(
            1
            for q in facts.open_questions
            if q.property_id is None or q.property_id not in with_step
        )

    # A step whose own draft could not be computed (an owner's Anlage N or the estimate for
    # Eure Angaben, the property's Anlage V for Immobilie) never reads „erledigt“: with nothing
    # open it waits, without a month (phase 10 §7.6, wave 2's open point). Nor does one whose
    # questions could not be read (no questionnaire for the year): it held the Jahresfragen step
    # waiting until D294.
    unread = facts.questions_total is None
    steps: list[GuideStep] = []
    n = open_points("angaben") + open_questions()
    angaben_failed = facts.estimate_failed or any(o.failed for o in facts.owners) or unread
    steps.append(
        GuideStep(
            kind="angaben",
            state="open" if n else ("waiting" if angaben_failed else "done"),
            open=n,
        )
    )
    for prop in facts.properties:
        n = open_points("property", prop.property_id) + open_questions(prop.property_id)
        failed = prop.warnings is None or unread
        steps.append(
            GuideStep(
                kind="property",
                property_id=prop.property_id,
                state="open" if n else ("waiting" if failed else "done"),
                open=n,
            )
        )
    missing = sum(1 for d in docs if d.status == "missing")
    expected = [d for d in docs if d.status == "expected"]
    waiting_from = None
    if expected:
        this_month = _month(facts.today)
        waiting_from = max(
            this_month, min(d.usual_from or _month(d.due_on or facts.today) for d in expected)
        )
    steps.append(
        GuideStep(
            kind="documents",
            state="open" if missing else ("waiting" if expected else "done"),
            open=missing,
            waiting=len(expected),
            done=sum(1 for d in docs if d.status == "present"),
            total=len(docs),
            waiting_from=None if missing else waiting_from,
        )
    )
    # Prüfen und abgeben is done (ready to file) only when every other step is done; while one
    # is not, it waits, with a month only when Belege still to come are all that is left.
    n = open_points("review")
    if n:
        review_state: GuideStepState = "open"
    elif not facts.incomplete and all(s.state == "done" for s in steps):
        review_state = "done"
    else:
        review_state = "waiting"
    only_belege_to_come = not facts.incomplete and all(
        s.state == "done" or (s.kind == "documents" and s.state == "waiting") for s in steps
    )
    steps.append(
        GuideStep(
            kind="review",
            state=review_state,
            open=n,
            waiting_from=waiting_from
            if review_state == "waiting" and only_belege_to_come
            else None,
        )
    )
    next_step = next((i for i, s in enumerate(steps) if s.state == "open"), None)
    nothing_left = all(s.state == "done" for s in steps)
    if next_step is None and nothing_left:
        next_step = len(steps) - 1  # ready to file
    return GuideResult(
        steps=steps,
        points=pts,
        documents=docs,
        next_step=next_step,
        waiting_from=waiting_from if next_step is None else None,
        open_questions=facts.open_questions,
    )
