"""The yearly questionnaire (Jahresfragen, docs/phases/phase-4b.md D110, §9).

GET lists the year's answer slots with last year's answers, the proposed rows and the items
to reconfirm, plus the progress; POST records one answer together with the rows its "yes"
brings, in one transaction (services/questionnaire.py holds the checks).
"""

from typing import Annotated

from fastapi import Query

from hausbuch.schemas.employment import QuestionnaireAnswer
from hausbuch.services.questionnaire import (
    QuestionnaireAnswerBody,
    QuestionnaireView,
    questionnaire_view,
    record_answer,
)

from ..deps import Cmd, domain_router

router = domain_router()


@router.get("/tax/questionnaire")
def tax_questionnaire(
    cmd: Cmd, tax_year: Annotated[int, Query(ge=2000, le=2100)]
) -> QuestionnaireView:
    """Every slot of the year in file order, pre-filled from the year before (nothing is
    written), and the progress. 422 tax.no_rule_set before the first questionnaire."""
    return cmd.read(questionnaire_view, tax_year)


@router.post("/tax/questionnaire/answers", status_code=201)
def tax_questionnaire_answer(body: QuestionnaireAnswerBody, cmd: Cmd) -> QuestionnaireAnswer:
    """One answer, its event (workspace.questionnaire_answered) and the return_input and
    tax_input rows it brings, all or nothing. 422 questionnaire.unknown_key,
    questionnaire.scope_invalid, questionnaire.not_current, and each row's own codes; 404 for
    an unknown owner, property or referenced row."""
    return cmd.run(record_answer, body)
