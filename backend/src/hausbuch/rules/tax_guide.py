"""Steuern as one guided path (phase 10 §7.6, D290): which step each open point belongs to,
who sends each Beleg and when it usually comes, and the step states. Pure: the service
(services/tax_guide.py) reads the drafts, the checklists and the questionnaire and hands their
results here; nothing in this module computes a tax figure.

The five steps, in order: Eure Angaben, Immobilie (one per property owned in the year),
Jahresfragen, Belege, Prüfen und abgeben.

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

from hausbuch.schemas.checklist import ChecklistStatus, DocumentChecklistItem, RequirementCode
from hausbuch.schemas.tax import DraftWarning
from hausbuch.schemas.tax_guide import (
    GuideDocument,
    GuidePoint,
    GuideSender,
    GuideStep,
    GuideStepKind,
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
    # Jahresfragen: counted by the questionnaire's own progress, never as a separate point.
    "return.questionnaire_open": "questions",
    "tax_export.questions_open": "questions",
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
    no_pay), its warnings, and whether the draft refused for want of any pay source."""

    owner_id: object
    status: Literal["certified", "estimate", "none"] | None
    warnings: Sequence[DraftWarning]
    no_pay: bool = False


@dataclass(frozen=True)
class GuideInput:
    tax_year: int
    today: date
    properties: Sequence[PropertyFacts]
    owners: Sequence[OwnerFacts]
    estimate_warnings: Sequence[DraftWarning]
    questions_answered: int | None  # None: no questionnaire for the year
    questions_total: int | None


@dataclass(frozen=True)
class GuideResult:
    steps: list[GuideStep]
    points: list[GuidePoint]
    documents: list[GuideDocument]
    next_step: int | None
    waiting_from: date | None


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
        step = STEP_OF_WARNING.get(warning.code.value)
        if step is None or step == "questions":
            return
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
            )
        )

    for owner in facts.owners:
        if owner.no_pay:
            out.append(
                GuidePoint(
                    step="angaben", form="anlage_n", code=NO_PAY_CODE, owner_id=owner.owner_id
                )
            )
        for warning in owner.warnings:
            add(warning, "anlage_n", owner_id=owner.owner_id)
    for warning in facts.estimate_warnings:
        add(warning, "return")
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
    `waiting_from` is the first month one of them usually comes (never before this month)."""
    docs = documents(facts)
    pts = points(facts, docs)

    def open_points(step: GuideStepKind, property_id=None) -> int:
        return sum(
            1
            for p in pts
            if p.step == step and (property_id is None or p.property_id == property_id)
        )

    steps: list[GuideStep] = []
    n = open_points("angaben")
    steps.append(GuideStep(kind="angaben", state="open" if n else "done", open=n))
    for prop in facts.properties:
        n = open_points("property", prop.property_id)
        steps.append(
            GuideStep(
                kind="property",
                property_id=prop.property_id,
                state="open" if n else "done",
                open=n,
            )
        )
    if facts.questions_total is None:
        steps.append(GuideStep(kind="questions", state="done", open=0))
    else:
        answered = facts.questions_answered or 0
        n = facts.questions_total - answered
        steps.append(
            GuideStep(
                kind="questions",
                state="open" if n else "done",
                open=n,
                done=answered,
                total=facts.questions_total,
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
    n = open_points("review")
    steps.append(
        GuideStep(
            kind="review",
            state="open" if n else ("waiting" if expected else "done"),
            open=n,
            waiting_from=None if n else waiting_from,
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
    )
