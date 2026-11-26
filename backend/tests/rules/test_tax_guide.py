"""Steuern as one guided path (phase 10 §7.6, D290): rules/tax_guide.py's step states, „Jetzt
dran“, the Belege and the routing of each to-do warning to its step. Invented owners and
properties only."""

import re
from dataclasses import replace
from datetime import date
from pathlib import Path
from uuid import uuid4

from hausbuch.rules import tax_guide as rule
from hausbuch.rules.tax._common import warning
from hausbuch.schemas.booked_inputs import BookedProposal
from hausbuch.schemas.checklist import (
    ChecklistScope,
    ChecklistScopeType,
    ChecklistStatus,
    DocumentChecklistItem,
    RequirementCode,
)
from hausbuch.schemas.employment import OpenQuestion
from hausbuch.schemas.tax import SourceRef, WarningCode
from hausbuch.services.health import rule_version

TODOS_TSX = Path(__file__).resolve().parents[3] / "frontend" / "src" / "components" / "todos.tsx"
YEAR = 2026
BEFORE_FEB = date(2026, 11, 23)  # the year's Belege are still to come
AFTER_JUNE = date(2027, 7, 15)  # every usual month has passed

P1, P2 = uuid4(), uuid4()
A, B = uuid4(), uuid4()


def item(code: RequirementCode, status: ChecklistStatus, due: date | None = None, scope_id=None):
    return DocumentChecklistItem(
        code=code,
        status=status,
        scope=ChecklistScope(type=ChecklistScopeType.PROPERTY, id=scope_id or uuid4()),
        due_on=due,
        documents=[],
    )


def facts(
    *,
    today=BEFORE_FEB,
    properties=None,
    owners=None,
    estimate_warnings=(),
    answered=8,
    total=8,
    open_questions=(),
    incomplete=False,
) -> rule.GuideInput:
    return rule.GuideInput(
        tax_year=YEAR,
        today=today,
        properties=properties if properties is not None else [rule.PropertyFacts(P1, [], [])],
        owners=owners if owners is not None else [rule.OwnerFacts(A, "certified", [])],
        estimate_warnings=list(estimate_warnings),
        questions_answered=answered,
        questions_total=total,
        open_questions=list(open_questions),
        incomplete=incomplete,
    )


# Three of A's owner questions, unanswered.
OWNER_QUESTIONS_A = [
    OpenQuestion(key=f"owner.{key}", scope="owner", owner_id=A)
    for key in ("tax_class", "commute", "telecom")
]


def kinds(result: rule.GuideResult) -> list[str]:
    return [s.kind for s in result.steps]


# --- the to-do table ----------------------------------------------------------------------


def _todo_codes() -> set[str]:
    source = TODOS_TSX.read_text(encoding="utf-8")
    block = re.search(r"export const WARNING_GUIDE\b[^=]*=\s*\{(.*?)\n\};", source, re.S)
    assert block is not None
    entries = re.findall(r'^  "?([a-z0-9_.]+)"?: \{\s*todo: (true|false)', block.group(1), re.M)
    return {code for code, todo in entries if todo == "true"}


def _notes_with_an_action() -> set[str]:
    source = TODOS_TSX.read_text(encoding="utf-8")
    block = re.search(r"export const WARNING_GUIDE\b[^=]*=\s*\{(.*?)\n\};", source, re.S)
    assert block is not None
    parts = re.split(r'^  "?([a-z0-9_.]+)"?: \{', block.group(1), flags=re.M)
    entries = dict(zip(parts[1::2], parts[2::2], strict=True))
    return {
        code
        for code, body in entries.items()
        if re.match(r"\s*todo: false", body) and re.search(r"^    action:", body, re.M)
    }


def test_every_to_do_warning_has_a_step_and_notes_have_none():
    assert set(rule.STEP_OF_WARNING) == _todo_codes()
    assert set(rule.STEP_OF_WARNING) <= {c.value for c in WarningCode}


def test_every_note_with_an_action_is_a_point_of_its_step():
    """Wave 2's open point: the notes that carry something to check are guide points (never
    blocking); return.child_over_18 joins them without a button (its place is ELSTER)."""
    assert set(rule.NOTE_STEP) == _notes_with_an_action() | {"return.child_over_18"}
    assert set(rule.NOTE_STEP) <= {c.value for c in WarningCode}
    assert not set(rule.NOTE_STEP) & set(rule.STEP_OF_WARNING)


def test_every_requirement_has_its_sender_and_month_data():
    assert set(rule.EXPECTED) == set(RequirementCode)
    for code, expectation in rule.EXPECTED.items():
        assert (expectation.sender is None) == (not expectation.beleg), code
    assert {c for c, e in rule.EXPECTED.items() if not e.beleg} == set(rule.PROPERTY_REQUIREMENTS)


def test_the_stored_rule_version_does_not_read_this_module():
    # rule_version() hashes rules/health and rules/plan only (tests/rules/test_phase10_budget.py
    # pins its value): the guide's data sits beside them, never inside.
    module = Path(rule.__file__).resolve()
    rules_dir = module.parent
    assert rules_dir.name == "rules"
    for folder in ("health", "plan"):
        assert rules_dir / folder not in module.parents
    assert len(rule_version()) == 64


# --- the steps ------------------------------------------------------------------------------


def test_four_steps_in_order_and_a_second_property_adds_a_second_immobilie_step():
    """Phase 10 §7.8 (b), D294: the Jahresfragen are no step of their own any more."""
    one = rule.guide(facts())
    assert kinds(one) == ["angaben", "property", "documents", "review"]
    two = rule.guide(
        facts(properties=[rule.PropertyFacts(P1, [], []), rule.PropertyFacts(P2, [], [])])
    )
    assert kinds(two) == ["angaben", "property", "property", "documents", "review"]
    assert [s.property_id for s in two.steps if s.kind == "property"] == [P1, P2]


def test_everything_done_is_ready_and_jetzt_dran_is_the_last_step():
    result = rule.guide(facts())
    assert all(s.state == "done" for s in result.steps)
    assert result.next_step == len(result.steps) - 1
    assert result.waiting_from is None


def test_the_review_step_is_done_only_when_every_other_step_is():
    """No pay, three questions open and a Beleg past its day: Prüfen has no point of its own,
    yet it is not „bereit“ and not counted as a done step."""
    zins = item(RequirementCode.ZINSBESCHEINIGUNG, ChecklistStatus.MISSING, date(2027, 2, 28))
    result = rule.guide(
        facts(
            today=AFTER_JUNE,
            properties=[rule.PropertyFacts(P1, [zins], [])],
            owners=[rule.OwnerFacts(A, None, (), no_pay=True)],
            answered=5,
            total=8,
            open_questions=OWNER_QUESTIONS_A,
        )
    )
    review = result.steps[-1]
    assert (review.kind, review.state, review.open, review.waiting_from) == (
        "review",
        "waiting",
        0,
        None,
    )
    assert [s.state for s in result.steps] == ["open", "done", "open", "waiting"]
    assert result.steps[0].open == 4  # the no-pay point and the three questions
    assert result.steps_done == 1  # the Immobilie step only
    assert result.ready is False
    assert result.next_step == 0


def test_the_review_step_waits_with_a_month_only_for_belege_still_to_come():
    zins = item(RequirementCode.ZINSBESCHEINIGUNG, ChecklistStatus.EXPECTED, date(2027, 2, 28))
    waiting = rule.guide(facts(properties=[rule.PropertyFacts(P1, [zins], [])]))
    assert (waiting.steps[-1].state, waiting.steps[-1].waiting_from) == (
        "waiting",
        date(2027, 1, 1),
    )
    with_questions = rule.guide(
        facts(
            properties=[rule.PropertyFacts(P1, [zins], [])],
            answered=7,
            total=8,
            open_questions=[OpenQuestion(key="household.donations", scope="household")],
        )
    )
    assert (with_questions.steps[-1].state, with_questions.steps[-1].waiting_from) == (
        "waiting",
        None,
    )


def test_open_questions_count_in_eure_angaben_and_in_their_propertys_step():
    """Phase 10 §7.8 (b), D294: an owner's or the household's open question is open in Eure
    Angaben, a property's in that property's step; never a point of its own."""
    result = rule.guide(
        facts(
            properties=[rule.PropertyFacts(P1, [], []), rule.PropertyFacts(P2, [], [])],
            answered=5,
            total=8,
            open_questions=[
                OpenQuestion(key="owner.commute", scope="owner", owner_id=A),
                OpenQuestion(key="household.children", scope="household"),
                OpenQuestion(key="property.trips", scope="property", property_id=P2),
            ],
        )
    )
    assert [(s.kind, s.state, s.open) for s in result.steps] == [
        ("angaben", "open", 2),
        ("property", "done", 0),
        ("property", "open", 1),
        ("documents", "done", 0),
        ("review", "waiting", 0),
    ]
    assert result.points == []
    assert result.next_step == 0
    assert result.ready is False
    only_property = rule.guide(
        facts(
            answered=7,
            total=8,
            open_questions=[OpenQuestion(key="property.trips", scope="property", property_id=P1)],
        )
    )
    assert [s.state for s in only_property.steps] == ["done", "open", "done", "waiting"]
    assert only_property.next_step == 1


def test_a_property_question_without_a_property_step_counts_in_eure_angaben():
    """A property not owned in the year has no Immobilie step, yet the questionnaire asks about
    it: its open question holds Eure Angaben, so it still holds „bereit“ as the questions step
    did."""
    result = rule.guide(
        facts(
            answered=7,
            total=8,
            open_questions=[OpenQuestion(key="property.trips", scope="property", property_id=P2)],
        )
    )
    assert [(s.kind, s.state, s.open) for s in result.steps][:2] == [
        ("angaben", "open", 1),
        ("property", "done", 0),
    ]
    assert result.ready is False


def test_an_unreadable_questionnaire_never_lets_angaben_or_a_property_read_done():
    result = rule.guide(facts(answered=None, total=None, incomplete=True))
    assert [(s.kind, s.state, s.open) for s in result.steps] == [
        ("angaben", "waiting", 0),
        ("property", "waiting", 0),
        ("documents", "done", 0),
        ("review", "waiting", 0),
    ]
    assert result.next_step is None and result.ready is False


def test_the_questionnaires_own_warnings_are_never_points():
    """return.questionnaire_open and tax_export.questions_open stand for the open questions,
    which the steps count themselves."""
    result = rule.guide(
        facts(
            estimate_warnings=[warning(WarningCode.RETURN_QUESTIONNAIRE_OPEN, open="1")],
            answered=7,
            total=8,
            open_questions=[OpenQuestion(key="household.children", scope="household")],
        )
    )
    assert result.points == []
    assert result.steps[0].open == 1
    assert rule.STEP_OF_WARNING["return.questionnaire_open"] == "angaben"
    assert rule.STEP_OF_WARNING["tax_export.questions_open"] == "angaben"


def test_jetzt_dran_is_the_first_open_step_in_order():
    no_split = warning(WarningCode.AFA_NO_SPLIT)
    proof = warning(WarningCode.PAYMENT_PROOF_MISSING, document=str(uuid4()))
    result = rule.guide(
        facts(
            properties=[rule.PropertyFacts(P1, [], [proof, no_split])],
            answered=7,
            total=8,
            open_questions=[OpenQuestion(key="property.trips", scope="property", property_id=P1)],
        )
    )
    assert [s.state for s in result.steps] == ["done", "open", "done", "open"]
    assert result.steps[1].open == 2  # the price split and the property's open question
    assert result.next_step == 1  # the Immobilie step comes before Prüfen
    assert [p.step for p in result.points] == ["property", "review"]
    review = result.steps[-1]
    assert review.open == 1


def test_an_owner_without_pay_is_a_point_of_eure_angaben_first():
    result = rule.guide(facts(owners=[rule.OwnerFacts(A, None, (), no_pay=True)]))
    assert result.steps[0].state == "open"
    assert result.next_step == 0
    assert result.points[0].code == "anlage_n.no_pay"
    assert result.points[0].owner_id == A


def test_an_announced_benefit_not_yet_typed_is_one_point_of_eure_angaben_per_owner():
    """Answered „ja“ in the Jahresfragen, never typed: only in the estimate's `missing`, no
    warning; the guide keeps Eure Angaben open and is not ready."""
    result = rule.guide(
        facts(
            owners=[
                rule.OwnerFacts(A, "certified", [], benefit_missing=True),
                rule.OwnerFacts(B, None, (), no_pay=True, benefit_missing=True),
            ]
        )
    )
    assert [(p.step, p.form, p.code, p.owner_id) for p in result.points] == [
        ("angaben", "anlage_n", "anlage_n.benefit_letter", A),
        ("angaben", "anlage_n", "anlage_n.no_pay", B),
        ("angaben", "anlage_n", "anlage_n.benefit_letter", B),
    ]
    assert (result.steps[0].state, result.steps[0].open) == ("open", 3)
    assert result.next_step == 0
    assert result.ready is False
    assert rule.BENEFIT_LETTER_CODE not in rule.STEP_OF_WARNING  # no warning stands for it
    typed = rule.guide(facts(owners=[rule.OwnerFacts(A, "certified", [])]))
    assert typed.points == [] and typed.ready is True


def test_angaben_warnings_from_anlage_n_and_the_estimate_route_to_eure_angaben():
    result = rule.guide(
        facts(
            owners=[
                rule.OwnerFacts(A, "certified", [warning(WarningCode.ANLAGE_N_CHURCH_MISMATCH)])
            ],
            estimate_warnings=[
                warning(WarningCode.RETURN_KINDERGELD_APPLY),
                warning(WarningCode.RETURN_QUESTIONNAIRE_OPEN, open="3"),  # counted as questions
                warning(WarningCode.RETURN_FILING_REQUIRED),  # a note, no point
            ],
        )
    )
    assert [(p.step, p.code) for p in result.points] == [
        ("angaben", "anlage_n.church_mismatch"),
        ("angaben", "return.kindergeld_apply"),
    ]
    assert result.steps[0].open == 2


def test_a_waiting_beleg_says_from_when_and_nothing_is_jetzt_dran():
    zins = item(RequirementCode.ZINSBESCHEINIGUNG, ChecklistStatus.EXPECTED, date(2027, 2, 28))
    there = item(RequirementCode.PURCHASE_CONTRACT, ChecklistStatus.PRESENT)
    result = rule.guide(
        facts(
            properties=[rule.PropertyFacts(P1, [zins, there], [])],
            owners=[
                rule.OwnerFacts(A, "estimate", [warning(WarningCode.ANLAGE_N_CERTIFICATE_MISSING)])
            ],
        )
    )
    documents = result.steps[2]
    assert (documents.state, documents.open, documents.waiting) == ("waiting", 0, 2)
    assert (documents.done, documents.total) == (1, 3)
    assert documents.waiting_from == date(2027, 1, 1)  # the bank's January before the employer
    assert result.steps[-1].state == "waiting"
    assert result.next_step is None
    assert result.waiting_from == date(2027, 1, 1)
    # the certificate's warning is the Beleg, not a second point
    assert result.points == []
    wage = next(d for d in result.documents if d.kind == "wage_certificate")
    assert (wage.status, wage.sender, wage.usual_from, wage.due_on) == (
        "expected",
        "employer",
        date(2027, 2, 1),
        date(2027, 3, 1),
    )
    zins_doc = next(d for d in result.documents if d.kind == "zinsbescheinigung")
    assert (zins_doc.sender, zins_doc.usual_from) == ("bank", date(2027, 1, 1))


def test_a_beleg_past_its_due_day_is_missing_and_open():
    zins = item(RequirementCode.ZINSBESCHEINIGUNG, ChecklistStatus.MISSING, date(2027, 2, 28))
    result = rule.guide(
        facts(
            today=AFTER_JUNE,
            properties=[rule.PropertyFacts(P1, [zins], [])],
            owners=[rule.OwnerFacts(A, "estimate", [])],
        )
    )
    documents = result.steps[2]
    assert (documents.state, documents.open, documents.waiting_from) == ("open", 2, None)
    assert result.next_step == 2


def test_waiting_from_is_never_before_this_month():
    zins = item(RequirementCode.ZINSBESCHEINIGUNG, ChecklistStatus.EXPECTED, date(2027, 2, 28))
    result = rule.guide(
        facts(today=date(2027, 2, 10), properties=[rule.PropertyFacts(P1, [zins], [])])
    )
    assert result.waiting_from == date(2027, 2, 1)


def test_the_hausgeld_point_is_the_belegs_until_the_abrechnung_is_there():
    pending = warning(WarningCode.HAUSGELD_ABRECHNUNG_PENDING)
    missing = item(RequirementCode.HAUSGELDABRECHNUNG, ChecklistStatus.EXPECTED, date(2027, 6, 30))
    result = rule.guide(facts(properties=[rule.PropertyFacts(P1, [missing], [pending])]))
    assert result.points == []
    there = item(RequirementCode.HAUSGELDABRECHNUNG, ChecklistStatus.PRESENT, date(2027, 6, 30))
    result = rule.guide(facts(properties=[rule.PropertyFacts(P1, [there], [pending])]))
    assert [(p.step, p.code) for p in result.points] == [("review", "hausgeld.abrechnung_pending")]


def test_price_split_and_market_rent_are_immobilie_points_unless_a_warning_stands_for_them():
    split = item(RequirementCode.PRICE_SPLIT, ChecklistStatus.MISSING)
    rent = item(RequirementCode.MARKET_RENT, ChecklistStatus.MISSING)
    result = rule.guide(facts(properties=[rule.PropertyFacts(P1, [split, rent], [])]))
    assert [(p.step, p.code) for p in result.points] == [
        ("property", "price_split"),
        ("property", "market_rent"),
    ]
    assert all(d.kind not in ("price_split", "market_rent") for d in result.documents)
    covered = rule.guide(
        facts(properties=[rule.PropertyFacts(P1, [split], [warning(WarningCode.AFA_NO_SPLIT)])])
    )
    assert [p.code for p in covered.points] == ["afa.no_split"]


def test_a_property_without_a_draft_still_has_its_step_and_belege():
    grundsteuer = item(
        RequirementCode.GRUNDSTEUERBESCHEID, ChecklistStatus.MISSING, date(2026, 1, 15)
    )
    result = rule.guide(
        facts(
            properties=[rule.PropertyFacts(P1, [grundsteuer], None)],
            owners=[rule.OwnerFacts(A, None, ())],
            answered=None,
            total=None,
            incomplete=True,
        )
    )
    assert kinds(result) == ["angaben", "property", "documents", "review"]
    # The questionnaire could not be read: neither Eure Angaben nor the Immobilie step is done.
    assert [(s.state, s.open) for s in result.steps[:2]] == [("waiting", 0), ("waiting", 0)]
    assert (result.steps[-1].state, result.steps[-1].waiting_from) == ("waiting", None)
    assert result.ready is False
    assert [d.kind for d in result.documents] == ["grundsteuerbescheid"]
    assert result.documents[0].sender == "municipality"
    assert result.next_step == 2


def test_the_same_warning_twice_is_one_point():
    same = warning(WarningCode.RETURN_BENEFIT_BOOKED, amount="100.00")
    result = rule.guide(
        facts(owners=[rule.OwnerFacts(A, "certified", [same])], estimate_warnings=[same])
    )
    assert len([p for p in result.points if p.code == "return.benefit_booked"]) == 2  # two forms
    result = rule.guide(facts(estimate_warnings=[same, same]))
    assert len(result.points) == 1


def test_two_payments_with_equal_params_are_two_points():
    def other_income():
        ref = SourceRef(kind="allocation", id=uuid4())
        return warning(WarningCode.INCOME_OTHER_REVIEW, [ref], amount="500.00")

    result = rule.guide(
        facts(properties=[rule.PropertyFacts(P1, [], [other_income(), other_income()])])
    )
    assert [p.code for p in result.points] == ["income.other_review", "income.other_review"]


def test_two_owners_each_get_their_wage_certificate():
    result = rule.guide(
        facts(owners=[rule.OwnerFacts(A, "certified", []), rule.OwnerFacts(B, "estimate", [])])
    )
    wages = [(d.owner_id, d.status) for d in result.documents if d.kind == "wage_certificate"]
    assert wages == [(A, "present"), (B, "expected")]


def test_a_forms_open_count_holds_its_belege_and_its_jahresfragen():
    """An Anlage N card counts the wage certificate still to come and the owner's open
    question; an Anlage V card its missing Beleg, its point and the property's open question; a
    household question counts on no card. The certificate's warning is not counted twice."""
    zins = item(RequirementCode.ZINSBESCHEINIGUNG, ChecklistStatus.MISSING, date(2027, 2, 28))
    split = item(RequirementCode.PRICE_SPLIT, ChecklistStatus.MISSING)
    questions = [
        OpenQuestion(key="owner.tax_class", scope="owner", owner_id=A),
        OpenQuestion(key="property.self_use", scope="property", property_id=P1),
        OpenQuestion(key="household.children", scope="household"),
    ]
    result = rule.guide(
        facts(
            properties=[rule.PropertyFacts(P1, [zins, split], []), rule.PropertyFacts(P2, [], [])],
            owners=[
                rule.OwnerFacts(A, "estimate", [warning(WarningCode.ANLAGE_N_CERTIFICATE_MISSING)]),
                rule.OwnerFacts(B, "certified", []),
            ],
            answered=5,
            total=8,
            open_questions=questions,
        )
    )
    assert not any(p.code == "anlage_n.certificate_missing" for p in result.points)
    # Eure Angaben: A's and the household's question; P1's step: its price split and question
    assert [s.open for s in result.steps if s.kind in ("angaben", "property")] == [2, 2, 0]
    assert result.form_open("anlage_n", owner_id=A) == 2  # the certificate, the question
    assert result.form_open("anlage_n", owner_id=B) == 0
    assert result.form_open("anlage_v", property_id=P1) == 3  # Beleg, price split, question
    assert result.form_open("anlage_v", property_id=P2) == 0


def test_a_year_that_cannot_be_computed_is_never_ready():
    """Nothing open, every Beleg there, but a source could not be read (no rule set): Prüfen
    waits without a month and the year is not ready."""
    result = rule.guide(facts(incomplete=True))
    assert [s.state for s in result.steps] == ["done", "done", "done", "waiting"]
    assert result.steps[-1].waiting_from is None
    assert result.ready is False
    assert result.steps_done == 3
    assert result.next_step is None
    assert rule.guide(facts()).ready is True


# --- phase 10 §7.8 (a), D293: bookings propose tax inputs -------------------------------------

_KIND_OF_GROUP = {
    "benefit": "replacement_income",
    "donation": "special_expense",
    "childcare": "childcare_cost",
    "household_services": "household_services",
}


def _proposal(group: str, *, blocking: bool, owner=None) -> BookedProposal:
    kind = _KIND_OF_GROUP[group]
    return BookedProposal.model_validate(
        {
            "group": group,
            "kind": kind,
            "tax_year": YEAR,
            "owner_id": owner,
            "booked": "50.00",
            "typed": "0.00",
            "difference": "50.00",
            "payments": 1,
            "allocation_ids": [uuid4()],
            "payload": {"kind": kind, "tax_year": YEAR, "value": {}},
            "blocking": blocking,
        }
    )


def test_proposals_are_points_and_only_the_blocking_ones_hold_the_step():
    donation = _proposal("donation", blocking=False)
    result = rule.guide(replace(facts(), proposals=[donation]))
    (point,) = result.points
    assert (point.step, point.form, point.code, point.blocking) == (
        "angaben",
        "return",
        "return.donation_booked",
        False,
    )
    assert point.proposal == donation
    assert (result.steps[0].state, result.steps[0].open) == ("done", 0)
    assert result.ready is True

    benefit = _proposal("benefit", blocking=True, owner=A)
    result = rule.guide(replace(facts(), proposals=[benefit, donation]))
    assert [p.code for p in result.points] == ["return.benefit_booked", "return.donation_booked"]
    assert (result.steps[0].state, result.steps[0].open) == ("open", 1)
    assert result.ready is False


def test_the_benefit_warning_gives_way_to_its_proposal():
    """With the proposals read, the estimate's `return.benefit_booked` warning is no second
    point; without them (None) it stays a point as before."""
    todo = warning(WarningCode.RETURN_BENEFIT_BOOKED, difference="50.00")
    base = facts(estimate_warnings=[todo])
    assert [p.code for p in rule.guide(base).points] == ["return.benefit_booked"]
    assert rule.guide(replace(base, proposals=[])).points == []
    result = rule.guide(replace(base, proposals=[_proposal("benefit", blocking=True)]))
    assert [(p.code, p.proposal is not None) for p in result.points] == [
        ("return.benefit_booked", True)
    ]


# --- wave 2's open points (phase 10 §7.6) ----------------------------------------------------


def test_a_note_is_a_point_of_its_step_that_never_blocks():
    result = rule.guide(
        facts(
            properties=[
                rule.PropertyFacts(P1, [], [warning(WarningCode.AFA_PURCHASE_PRICE_MISMATCH)])
            ],
            owners=[
                rule.OwnerFacts(A, "certified", [warning(WarningCode.ANLAGE_N_RECEIPT_MISSING)])
            ],
            estimate_warnings=[warning(WarningCode.RETURN_CHILDREN_MISMATCH)],
        )
    )
    assert [(p.step, p.code, p.note, p.blocking) for p in result.points] == [
        ("angaben", "return.children_mismatch", True, False),
        ("property", "afa.purchase_price_mismatch", True, False),
        ("review", "anlage_n.receipt_missing", True, False),
    ]
    assert all(s.state == "done" for s in result.steps)
    assert result.ready is True
    assert result.form_open("anlage_v", property_id=P1) == 0


def test_a_refused_input_is_a_point_naming_it_and_the_step_holds():
    refused = rule.OwnerFacts(A, None, (), refused="anlage_n.useful_life_missing")
    result = rule.guide(
        replace(
            facts(owners=[refused], incomplete=True),
            estimate_failed=True,
            estimate_refused="anlage_n.useful_life_missing",  # the same refusal, read twice
        )
    )
    assert [(p.step, p.form, p.code, p.owner_id, p.refused) for p in result.points] == [
        ("angaben", "anlage_n", "return_input.refused", A, "anlage_n.useful_life_missing")
    ]
    assert (result.steps[0].state, result.steps[0].open) == ("open", 1)
    assert result.ready is False

    household = rule.guide(
        replace(
            facts(incomplete=True),
            estimate_failed=True,
            estimate_refused="return.household_services_cash",
        )
    )
    assert [(p.form, p.owner_id, p.refused) for p in household.points] == [
        ("return", None, "return.household_services_cash")
    ]


def test_a_step_whose_draft_failed_never_reads_done():
    failed = rule.guide(
        facts(
            properties=[rule.PropertyFacts(P1, [], None)],
            owners=[rule.OwnerFacts(A, None, ())],
            incomplete=True,
        )
    )
    assert [s.state for s in failed.steps][:2] == ["waiting", "waiting"]
    no_pay = rule.guide(facts(owners=[rule.OwnerFacts(A, None, (), no_pay=True)]))
    assert no_pay.steps[0].state == "open"  # the no-pay point, as before
    estimate = rule.guide(replace(facts(incomplete=True), estimate_failed=True))
    assert estimate.steps[0].state == "waiting"


def test_two_overlapping_pairs_and_twins_are_one_point_each():
    def overlap(first: str, second: str):
        return warning(WarningCode.ANLAGE_N_PERIODS_OVERLAP, first=first, second=second)

    def kindergeld(child: str):
        return warning(WarningCode.RETURN_KINDERGELD_APPLY, born_month="2026-05", child=child)

    result = rule.guide(
        facts(
            owners=[
                rule.OwnerFacts(
                    A,
                    "certified",
                    [
                        overlap("2026-01-01/2026-06-30", "2026-03-01/2026-12-31"),
                        overlap("2026-01-01/2026-06-30", "2026-05-01/2026-08-31"),
                    ],
                )
            ],
            estimate_warnings=[kindergeld("1"), kindergeld("2")],
        )
    )
    codes = [p.code for p in result.points]
    assert codes.count("anlage_n.periods_overlap") == 2
    assert codes.count("return.kindergeld_apply") == 2
