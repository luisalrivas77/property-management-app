"""The yearly questionnaire (Jahresfragen, phase 4b D110, §4.2, §5.11, §9).

One append-only table `questionnaire_answer` (migration 0018: SELECT and INSERT only for the
app role, a forbid_mutation trigger). An answer is never changed: a correction is a new row
whose `supersedes_id` names the answer it replaces; a withdrawal repeats the answer with
`withdrawn = true`. Every answer is written with its `workspace.questionnaire_answered` event
(ids, key, year, coded answer and choice; no free text exists) and, in the same transaction,
the rows a "yes" brings (`return_input` creates through services/return_inputs.create_input,
`tax_input` creates through services/tax_inputs.create_tax_input): either all are written or
none (POST /tax/questionnaire/answers runs in one command).

What an answer must satisfy, beyond QuestionnaireAnswerCreate's own validator (the id set by
the key's scope):

- the key is a question of the year's file (`questionnaire.unknown_key`); the owner or the
  property exists (404); a choice is one of the question's codes
  (`questionnaire.scope_invalid`);
- `supersedes_id` names the current answer of the same key, year and scope
  (`questionnaire.not_current`; another key or scope: `questionnaire.scope_invalid`); a
  withdrawal repeats its answer and choice;
- no other current answer fills the slot (`questionnaire.not_current`: supersede it);
- `carried_from_id` names a current answer of the same key and scope of the year before
  (`questionnaire.scope_invalid`);
- the rows it brings are the slot's: an owner question's rows are that owner's, a household
  question's rows are household rows, a property question's tax inputs are that property's
  (`questionnaire.scope_invalid`).

Reads: `questionnaire_view(conn, tax_year)` (GET /tax/questionnaire: every slot with last
year's answer, the proposed rows and the items to reconfirm, plus the progress), and
`questionnaire_progress` / `current_answers` for the estimate and the Zu prüfen card.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Row, and_, exists, insert, select
from sqlalchemy.exc import IntegrityError

from hausbuch.db.tables import owner as owner_table
from hausbuch.db.tables import property_ as property_table
from hausbuch.db.tables import questionnaire_answer as answer_table
from hausbuch.db.tables import tax_input as tax_input_table
from hausbuch.db.tables import workspace as workspace_table
from hausbuch.rules.errors import (
    QUESTIONNAIRE_NOT_CURRENT,
    QUESTIONNAIRE_SCOPE_INVALID,
    QUESTIONNAIRE_UNKNOWN_KEY,
)
from hausbuch.rules.tax import questionnaire as rules
from hausbuch.rules.tax import registry
from hausbuch.rules.tax.anlage_n._base import rules_for
from hausbuch.schemas.common import Schema, TaxYear
from hausbuch.schemas.employment import (
    HOUSEHOLD_KINDS,
    Question,
    QuestionnaireAnswer,
    QuestionnaireAnswerCreate,
    QuestionnaireProgress,
    QuestionPrefill,
    ReturnInputCreate,
)
from hausbuch.schemas.enums import EntityType
from hausbuch.schemas.events import EventType, QuestionnaireAnswered
from hausbuch.schemas.tax import TaxInput, TaxInputCreate, TaxInputFact, TaxInputKind

from .allocations import user_id
from .context import CommandContext
from .errors import ConflictError, NotFoundError, RuleViolationError, violated_constraint
from .events import append_event
from .lookup import require_row
from .owners import list_owners
from .properties import list_properties
from .return_inputs import create_input, current_rows
from .tax_inputs import create_tax_input

QUESTIONNAIRE_ANSWER = "questionnaire_answer"  # NotFoundError's entity name
_SUPERSEDED_ONCE = "questionnaire_answer_supersedes_id_key"
_SLOT = ("question_key", "tax_year", "owner_id", "property_id")


class QuestionnaireView(Schema):
    """GET /tax/questionnaire: the year's questions and slots in file order (owners in name
    order) and the progress. reused_from is set when the year has no file of its own. questions
    are the file's (texts, answer type, choices, targets, carry-forward), each once; a slot
    names its question by key, so the screen renders the question from the data, never from
    its own copy (phase 4b §10, principle 5)."""

    tax_year: TaxYear
    version: str
    reused_from: TaxYear | None = None
    questions: list[Question]
    slots: list[QuestionPrefill]
    progress: QuestionnaireProgress
    reminders: list[str]


class QuestionnaireAnswerBody(Schema):
    """POST /tax/questionnaire/answers: the answer and the rows it brings, written together."""

    answer: QuestionnaireAnswerCreate
    return_inputs: list[ReturnInputCreate] = []
    tax_inputs: list[TaxInputCreate] = []


# --- Reads -------------------------------------------------------------------------------


def _is_current() -> Any:
    later = answer_table.alias("later")
    return and_(
        answer_table.c.withdrawn.is_(False),
        ~exists().where(later.c.supersedes_id == answer_table.c.id),
    )


def list_answers(
    conn: Connection, *, tax_year: int | None = None, include_history: bool = False
) -> list[QuestionnaireAnswer]:
    """The current answers (every answer with include_history), oldest first."""
    c = answer_table.c
    query = select(answer_table)
    if not include_history:
        query = query.where(_is_current())
    if tax_year is not None:
        query = query.where(c.tax_year == tax_year)
    return [_to_answer(r) for r in conn.execute(query.order_by(c.recorded_at, c.id))]


def current_answers(conn: Connection, tax_year: int) -> list[QuestionnaireAnswer]:
    return list_answers(conn, tax_year=tax_year)


def _scope_ids(conn: Connection) -> tuple[list[UUID], list[UUID]]:
    return [o.id for o in list_owners(conn)], [p.id for p in list_properties(conn)]


def questionnaire_progress(conn: Connection, tax_year: int) -> QuestionnaireProgress:
    """Answered and open slots of the year. RuleError tax.no_rule_set before every file."""
    owners, properties = _scope_ids(conn)
    q = registry.questionnaire(tax_year)
    return rules.progress(tax_year, q, current_answers(conn, tax_year), owners, properties)


def _private_costs(conn: Connection, tax_year: int) -> list[TaxInputFact]:
    """The current private_cost rows of `tax_year`, every property (the copy's source)."""
    c = tax_input_table.c
    later = tax_input_table.alias("later")
    rows = conn.execute(
        select(tax_input_table)
        .where(
            c.kind == TaxInputKind.PRIVATE_COST.value,
            c.tax_year == tax_year,
            c.withdrawn.is_(False),
            ~exists().where(later.c.supersedes_id == c.id),
        )
        .order_by(c.recorded_at, c.id)
    )
    return [
        TaxInputFact.model_validate(
            TaxInput.model_validate(r._asdict()).model_dump(
                exclude={"workspace_id", "recorded_at", "recorded_by"}
            )
        )
        for r in rows
    ]


def questionnaire_view(conn: Connection, tax_year: int) -> QuestionnaireView:
    """GET /tax/questionnaire (§5.11): nothing is written; the proposed rows are what a "yes"
    would record once the user confirms them. RuleError tax.no_rule_set before every file."""
    owners, properties = _scope_ids(conn)
    q = registry.questionnaire(tax_year)
    answers = current_answers(conn, tax_year)
    slots = rules.prefill(
        tax_year,
        q,
        current_answers(conn, tax_year - 1),
        current_rows(conn),
        owners,
        properties,
        prior_tax_rows=_private_costs(conn, tax_year - 1),
        rule_set=rules_for(tax_year),
    )
    return QuestionnaireView(
        tax_year=tax_year,
        version=q.version,
        reused_from=q.reused_from,
        questions=q.questions,
        slots=slots,
        progress=rules.progress(tax_year, q, answers, owners, properties),
        reminders=rules.reminders(answers, q),
    )


def get_answer(conn: Connection, answer_id: UUID) -> QuestionnaireAnswer | None:
    row = conn.execute(select(answer_table).where(answer_table.c.id == answer_id)).one_or_none()
    return None if row is None else _to_answer(row)


# --- Writes ------------------------------------------------------------------------------


def record_answer(ctx: CommandContext, body: QuestionnaireAnswerBody) -> QuestionnaireAnswer:
    """POST /tax/questionnaire/answers: the answer, its event and its rows, in the caller's
    transaction (see the module doc for the checks). Any refusal (of the answer or of one of
    its rows) leaves nothing written."""
    data = body.answer
    _lock_workspace(ctx.conn, ctx.workspace_id)
    question = _question(data)
    if data.owner_id is not None:
        require_row(ctx.conn, owner_table, EntityType.OWNER, data.owner_id)
    if data.property_id is not None:
        require_row(ctx.conn, property_table, EntityType.PROPERTY, data.property_id)
    if data.choice is not None:
        codes = {c.code for c in question.choices or []}
        if data.choice not in codes:
            raise _scope_invalid(f"{data.choice} is not a choice of {data.question_key}")
    if data.supersedes_id is not None:
        _check_supersedes(ctx.conn, data)
    if not data.withdrawn:
        _check_no_other_current(ctx.conn, data)
    if data.carried_from_id is not None:
        _check_carried_from(ctx.conn, data)
    _check_rows(body)

    try:
        row = ctx.conn.execute(
            insert(answer_table)
            .values(
                workspace_id=ctx.workspace_id,
                **data.model_dump(),
                recorded_at=ctx.now,
                recorded_by=user_id(ctx),
            )
            .returning(*answer_table.c)
        ).one()
    except IntegrityError as exc:
        if violated_constraint(exc) == _SUPERSEDED_ONCE:
            raise ConflictError(
                f"answer {data.supersedes_id} was superseded concurrently",
                constraint=_SUPERSEDED_ONCE,
            ) from None
        raise
    created = _to_answer(row)
    append_event(
        ctx,
        EventType.WORKSPACE_QUESTIONNAIRE_ANSWERED,
        ctx.workspace_id,
        QuestionnaireAnswered(
            questionnaire_answer_id=created.id,
            question_key=created.question_key,
            tax_year=created.tax_year,
            answer=created.answer,
            choice=created.choice,
            owner_id=created.owner_id,
            property_id=created.property_id,
            supersedes_id=created.supersedes_id,
            carried_from_id=created.carried_from_id,
            withdrawn=created.withdrawn,
        ),
    )
    for item in body.return_inputs:
        create_input(ctx, item)
    for item in body.tax_inputs:
        create_tax_input(ctx, item.property_id, item)
    return created


def _lock_workspace(conn: Connection, workspace_id: UUID) -> None:
    conn.execute(
        select(workspace_table.c.id)
        .where(workspace_table.c.id == workspace_id)
        .with_for_update(key_share=True)
    ).one()


def _scope_invalid(message: str) -> RuleViolationError:
    return RuleViolationError([QUESTIONNAIRE_SCOPE_INVALID], message)


def _question(data: QuestionnaireAnswerCreate):
    q = registry.questionnaire(data.tax_year)
    question = next((x for x in q.questions if x.key == data.question_key), None)
    if question is None:
        raise RuleViolationError(
            [QUESTIONNAIRE_UNKNOWN_KEY],
            f"{data.question_key} is not a question of {data.tax_year}",
        )
    return question


def _slot_answers(conn: Connection, data: QuestionnaireAnswerCreate, year: int):
    c = answer_table.c

    def same(column, value):
        return column.is_(None) if value is None else column == value

    query = select(answer_table).where(
        c.question_key == data.question_key,
        c.tax_year == year,
        same(c.owner_id, data.owner_id),
        same(c.property_id, data.property_id),
        _is_current(),
    )
    return [_to_answer(r) for r in conn.execute(query.order_by(c.recorded_at, c.id))]


def _check_supersedes(conn: Connection, data: QuestionnaireAnswerCreate) -> None:
    assert data.supersedes_id is not None
    old = get_answer(conn, data.supersedes_id)
    if old is None:
        raise NotFoundError(QUESTIONNAIRE_ANSWER, data.supersedes_id)
    if any(getattr(old, name) != getattr(data, name) for name in _SLOT):
        raise _scope_invalid("a correction supersedes the answer of the same slot")
    if old.id not in {a.id for a in _slot_answers(conn, data, data.tax_year)}:
        raise RuleViolationError(
            [QUESTIONNAIRE_NOT_CURRENT], f"answer {old.id} is not current (withdrawn or replaced)"
        )
    if data.withdrawn and (old.answer, old.choice) != (data.answer, data.choice):
        raise _scope_invalid("a withdrawal repeats the withdrawn answer")


def _check_no_other_current(conn: Connection, data: QuestionnaireAnswerCreate) -> None:
    others = [a for a in _slot_answers(conn, data, data.tax_year) if a.id != data.supersedes_id]
    if others:
        raise RuleViolationError(
            [QUESTIONNAIRE_NOT_CURRENT],
            f"answer {others[0].id} already answers this; supersede it (supersedes_id)",
        )


def _check_carried_from(conn: Connection, data: QuestionnaireAnswerCreate) -> None:
    assert data.carried_from_id is not None
    if data.carried_from_id not in {a.id for a in _slot_answers(conn, data, data.tax_year - 1)}:
        raise _scope_invalid("carried_from_id names last year's current answer of this slot")


def _check_rows(body: QuestionnaireAnswerBody) -> None:
    a = body.answer
    scope = a.question_key.split(".", 1)[0]
    for r in body.return_inputs:
        household = r.kind in HOUSEHOLD_KINDS
        if scope == "property" or (scope == "household") != household:
            raise _scope_invalid(f"a {r.kind} row does not belong to {a.question_key}")
        if scope == "owner" and r.owner_id != a.owner_id:
            raise _scope_invalid("an owner question's rows are that owner's")
    for t in body.tax_inputs:
        if scope != "property" or t.property_id != a.property_id:
            raise _scope_invalid("a property question's tax inputs are that property's")


def _to_answer(row: Row) -> QuestionnaireAnswer:
    return QuestionnaireAnswer.model_validate(row._asdict())
