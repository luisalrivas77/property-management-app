"""GET /tax/{tax_year}/guide (phase 10 §7.6, D290): Steuern as one guided path.

Reads what the Steuern screens already read, each through its own service, and hands the
results to rules/tax_guide.py: every property's document checklist and Anlage V draft, every
owner's Anlage N draft, the questionnaire's progress and the whole-return estimate. A source
that cannot be read for the year (a year without a rule set: `tax.no_rule_set`) is named in
`unavailable` and the rest is still served, so the guide never answers 422 as a whole.

Cost: the estimate drafts every property's Anlage V once more (services/return_inputs.py
property_results), so a property is drafted twice per request; the answer is kept while
nothing changes (api/kept_answers.py KEPT_ROUTES).
"""

from datetime import date
from uuid import UUID

from sqlalchemy import Connection

from hausbuch.rules import document_checklist as checklist_rule
from hausbuch.rules import tax_guide as rule
from hausbuch.rules.errors import ANLAGE_N_NO_PAY, RuleError
from hausbuch.rules.tax.return_estimate import filing_deadline
from hausbuch.schemas.employment import AnlageNDraft
from hausbuch.schemas.tax import AnlageVDraft
from hausbuch.schemas.tax_guide import GuideEstimate, GuideFormResult, TaxGuide

from .checklist import checklist_facts, document_checklist
from .owners import list_owners
from .properties import list_properties
from .questionnaire import questionnaire_progress
from .return_inputs import anlage_n_draft, household_return_estimate
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

    owners: list[rule.OwnerFacts] = []
    n_drafts: dict[UUID, AnlageNDraft | str] = {}
    for owner in list_owners(conn):
        try:
            n = anlage_n_draft(conn, owner.id, tax_year=tax_year)
        except RuleError as exc:
            n_drafts[owner.id] = exc.code
            no_pay = exc.code == ANLAGE_N_NO_PAY
            if not no_pay:
                unavailable.append(f"anlage_n:{exc.code}")
            owners.append(rule.OwnerFacts(owner.id, None, (), no_pay=no_pay))
        else:
            n_drafts[owner.id] = n
            owners.append(rule.OwnerFacts(owner.id, n.status, n.warnings))

    estimate = None
    estimate_warnings = []
    try:
        full = household_return_estimate(conn, tax_year=tax_year, today=today)
    except RuleError as exc:
        unavailable.append(f"estimate:{exc.code}")
    else:
        estimate = GuideEstimate(result=full.result, status=full.status)
        estimate_warnings = full.warnings

    answered = total = None
    try:
        progress = questionnaire_progress(conn, tax_year)
    except RuleError as exc:
        unavailable.append(f"questionnaire:{exc.code}")
    else:
        answered, total = progress.answered, progress.total

    result = rule.guide(
        rule.GuideInput(
            tax_year=tax_year,
            today=today,
            properties=properties,
            owners=owners,
            estimate_warnings=estimate_warnings,
            questions_answered=answered,
            questions_total=total,
        )
    )
    # A form's open count is every point the guide lists for it, whichever step clears it, so
    # „fertig“ on Prüfen und abgeben never stands under a point that names the form.
    forms: list[GuideFormResult] = []
    for prop_id, v in v_drafts.items():
        forms.append(
            GuideFormResult(
                form="anlage_v",
                property_id=prop_id,
                result=None if isinstance(v, str) else v.totals.result,
                reason=v if isinstance(v, str) else None,
                open=sum(
                    1 for p in result.points if p.form == "anlage_v" and p.property_id == prop_id
                ),
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
                open=sum(
                    1 for p in result.points if p.form == "anlage_n" and p.owner_id == owner_id
                ),
            )
        )
    return TaxGuide(
        tax_year=tax_year,
        filing_deadline=filing_deadline(tax_year),
        steps=result.steps,
        steps_done=sum(1 for s in result.steps if s.state == "done"),
        next_step=result.next_step,
        waiting_from=result.waiting_from,
        ready=all(s.state == "done" for s in result.steps),
        estimate=estimate,
        forms=forms,
        points=result.points,
        documents=result.documents,
        unavailable=sorted(set(unavailable)),
    )
