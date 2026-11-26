"""GET /tax/{tax_year}/guide (phase 10 §7.6, D290): Steuern as one guided path.

Reads what the Steuern screens already read, each through its own service, and hands the
results to rules/tax_guide.py: every property's document checklist and Anlage V draft, every
owner's Anlage N draft, the questionnaire's progress and the whole-return estimate, and sorts
Eure Angaben into its topics (rules/angaben_topics.py, phase 10 §7.8 (b), D294). A source
that cannot be read for the year (a year without a rule set: `tax.no_rule_set`) is named in
`unavailable` and the rest is still served, so the guide never answers 422 as a whole.

Cost: the estimate drafts every property's Anlage V once more (services/return_inputs.py
property_results), so a property is drafted twice per request; the answer is kept while
nothing changes (api/kept_answers.py KEPT_ROUTES).
"""

from datetime import date
from uuid import UUID

from sqlalchemy import Connection

from hausbuch.rules import angaben_topics as topics_rule
from hausbuch.rules import document_checklist as checklist_rule
from hausbuch.rules import tax_guide as rule
from hausbuch.rules.errors import ANLAGE_N_NO_PAY, RuleError
from hausbuch.rules.tax import registry
from hausbuch.rules.tax.anlage_n._base import in_year
from hausbuch.rules.tax.return_estimate import filing_deadline
from hausbuch.schemas.employment import AnlageNDraft, OpenQuestion
from hausbuch.schemas.tax import AnlageVDraft
from hausbuch.schemas.tax_guide import GuideEstimate, GuideFormResult, TaxGuide

from .checklist import checklist_facts, document_checklist
from .owners import list_owners
from .properties import list_properties
from .questionnaire import current_answers, questionnaire_progress
from .return_inputs import (
    anlage_n_draft,
    booked_input_proposals,
    current_rows,
    household_return_estimate,
)
from .tax import anlage_v_draft


def tax_guide(conn: Connection, *, tax_year: int, today: date) -> TaxGuide:
    unavailable: list[str] = []
    properties: list[rule.PropertyFacts] = []
    v_drafts: dict[UUID, AnlageVDraft | str] = {}
    for prop in list_properties(conn):
        facts, _ = checklist_facts(conn, prop.id)
        if tax_year not in checklist_rule.available_years(facts, today):
            continue  # not owned in the year: no Immobilie step
        checklist = document_checklist(conn, prop.id, tax_year=tax_year, today=today)
        try:
            draft = anlage_v_draft(conn, prop.id, tax_year=tax_year, today=today)
        except RuleError as exc:
            v_drafts[prop.id] = exc.code
            unavailable.append(f"anlage_v:{exc.code}")
            warnings = None
        else:
            v_drafts[prop.id] = draft
            warnings = draft.warnings
        properties.append(rule.PropertyFacts(prop.id, checklist.items, warnings))

    estimate = None
    estimate_warnings = []
    estimate_failed = False
    estimate_refused = None
    benefit_owners: set[UUID] = set()
    try:
        full = household_return_estimate(conn, tax_year=tax_year, today=today)
    except RuleError as exc:
        unavailable.append(f"estimate:{exc.code}")
        estimate_failed = True
        estimate_refused = exc.code if exc.code in rule.REFUSAL_CODES else None
    else:
        estimate = GuideEstimate(result=full.result, status=full.status)
        estimate_warnings = full.warnings
        # A benefit announced in the Jahresfragen but not typed is only in the estimate's
        # `missing` (no warning): the guide makes it a point of Eure Angaben.
        benefit_owners = {
            m.owner_id for m in full.missing if m.kind == "benefit_letter" and m.owner_id
        }

    owners: list[rule.OwnerFacts] = []
    n_drafts: dict[UUID, AnlageNDraft | str] = {}
    owner_records = list_owners(conn)
    for owner in owner_records:
        benefit_missing = owner.id in benefit_owners
        try:
            n = anlage_n_draft(conn, owner.id, tax_year=tax_year)
        except RuleError as exc:
            n_drafts[owner.id] = exc.code
            no_pay = exc.code == ANLAGE_N_NO_PAY
            if not no_pay:
                unavailable.append(f"anlage_n:{exc.code}")
            owners.append(
                rule.OwnerFacts(
                    owner.id,
                    None,
                    (),
                    no_pay=no_pay,
                    benefit_missing=benefit_missing,
                    refused=exc.code if exc.code in rule.REFUSAL_CODES else None,
                )
            )
        else:
            n_drafts[owner.id] = n
            owners.append(
                rule.OwnerFacts(owner.id, n.status, n.warnings, benefit_missing=benefit_missing)
            )

    answered = total = None
    open_questions: list[OpenQuestion] = []
    try:
        progress = questionnaire_progress(conn, tax_year)
    except RuleError as exc:
        unavailable.append(f"questionnaire:{exc.code}")
    else:
        answered, total = progress.answered, progress.total
        open_questions = progress.open

    # The bookings that propose a tax input (phase 10 §7.8 (a), D293): a point of Eure Angaben
    # each, with the POST /return-inputs payload; nothing is written here.
    try:
        proposals = booked_input_proposals(conn, tax_year)
    except RuleError as exc:
        unavailable.append(f"proposals:{exc.code}")
        proposals = None

    result = rule.guide(
        rule.GuideInput(
            proposals=proposals,
            estimate_failed=estimate_failed,
            estimate_refused=estimate_refused,
            tax_year=tax_year,
            today=today,
            properties=properties,
            owners=owners,
            estimate_warnings=estimate_warnings,
            questions_answered=answered,
            questions_total=total,
            open_questions=open_questions,
            incomplete=bool(unavailable),
        )
    )
    # A form's open count is everything still open for it, whichever step clears it (its
    # points, its Belege not there yet, the Jahresfragen about its owner or property), so
    # „fertig“ on Prüfen und abgeben never stands over something left to do for the form.
    forms: list[GuideFormResult] = []
    for prop_id, v in v_drafts.items():
        forms.append(
            GuideFormResult(
                form="anlage_v",
                property_id=prop_id,
                result=None if isinstance(v, str) else v.totals.result,
                reason=v if isinstance(v, str) else None,
                open=result.form_open("anlage_v", property_id=prop_id),
            )
        )
    for owner_id, n in n_drafts.items():
        forms.append(
            GuideFormResult(
                form="anlage_n",
                owner_id=owner_id,
                result=None if isinstance(n, str) else n.result,
                status=None if isinstance(n, str) else n.status,
                reason=n if isinstance(n, str) else None,
                open=result.form_open("anlage_n", owner_id=owner_id),
            )
        )
    # Eure Angaben by topic (D294): None while the year's questionnaire cannot be read.
    topics = None
    if total is not None:
        topic_owners = []
        for o in owner_records:
            n = n_drafts[o.id]
            draft = None if isinstance(n, str) else n
            topic_owners.append(
                topics_rule.TopicOwner(
                    owner_id=o.id,
                    church_tax=o.church_tax,
                    tax_state=o.tax_state,
                    status=draft.status if draft else None,
                    gross_source=draft.gross_source if draft else None,
                    gross=draft.gross if draft else None,
                )
            )
        topics = topics_rule.topics(
            registry.questionnaire(tax_year),
            topic_owners,
            current_answers(conn, tax_year),
            in_year(current_rows(conn), tax_year),
            result.points,
            proposals,
        )
    # Each point names its topic, so Eure Angaben shows it in its topic (a read, D294).
    points = [
        p.model_copy(update={"topic": topics_rule.topic_of_point(p)}) if p.step == "angaben" else p
        for p in result.points
    ]
    return TaxGuide(
        tax_year=tax_year,
        filing_deadline=filing_deadline(tax_year),
        steps=result.steps,
        steps_done=result.steps_done,
        next_step=result.next_step,
        waiting_from=result.waiting_from,
        ready=result.ready and not unavailable,
        estimate=estimate,
        forms=forms,
        points=points,
        documents=result.documents,
        unavailable=sorted(set(unavailable)),
        topics=topics,
    )
