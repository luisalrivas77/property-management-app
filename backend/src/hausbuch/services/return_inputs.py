"""Return inputs: the employment and household facts of the income tax return (phase 4b D97,
§6, §9).

One append-only table `return_input` (migration 0018: SELECT and INSERT only for the app role,
a forbid_mutation trigger). A row is never changed: a correction is a new row whose
`supersedes_id` names the row it replaces, and a withdrawal is a new row with
`withdrawn = true` that repeats the withdrawn row's value. Every row is written together with
its event in the caller's transaction (principle 4): `owner.return_input_recorded` (entity =
the owner) for the owner kinds, `workspace.return_input_recorded` (entity = the workspace) for
the household kinds. The event carries ids, kind, year, the value's money fields by name and,
for a child, its months in the year; never an employer, a label, a recipient, the note or a
birth date (schemas/events.ReturnInputRecorded).

"Current" = not withdrawn and not superseded by any other row. What a write must satisfy,
beyond ReturnInputCreate's own validator (scope, document by kind, carried_from only on a
yearly kind: a 422 validation error on the body):

- a yearly kind's year has an Anlage N rule set, its own or an earlier one (RuleError
  `tax.no_rule_set` before 2026); a spanning kind (child, work item) may start earlier;
- the owner and the document exist in this workspace (404 otherwise); a certificate's
  document is a Lohnsteuerbescheinigung and a payslip's a Gehaltsabrechnung
  (`return_input.scope_invalid`);
- `supersedes_id` names a row of the same kind, tax year and owner
  (`return_input.scope_invalid`) that is current (`return_input.not_current`); a withdrawal
  repeats its value (`return_input.scope_invalid`);
- `carried_from_id` names a current row of the same kind and owner of the year before
  (`return_input.carried_from_invalid`, D110);
- the one-per-owner-and-year kinds (home_office, work_room, telecom, other_insurance,
  vorsorge_override) and a payslip per (owner, employer, month) have one current row
  (`return_input.not_current`: supersede it); the item kinds any number;
- a home_office row is refused while a 12-month work_room row is current for the owner and
  year, and a 12-month work_room row while a home_office row is (D106,
  `return_input.work_room_conflict`);
- what the rules would refuse for the year is refused at write, so a stored row never breaks a
  computed screen: a childcare cost names a current child (`return_input.scope_invalid`) not
  14 before the year began (`return.childcare_age`); a §35a cost is paid to an account
  (`return.household_services_cash`); an `other` work item states its useful life
  (`anlage_n.useful_life_missing`). A withdrawal is not re-checked.

Writes of one workspace are serialised by locking the workspace row (FOR NO KEY UPDATE), so
the "no other current row" checks cannot race; `supersedes_id` is UNIQUE in the table as well.

Reads for the rules: `current_rows(conn)` (every current row, all years), `owner_facts(conn)`
and `household_rows(conn)` in the shapes rules/tax/anlage_n takes, and `tax_contexts(conn)`,
the D98 contexts phase 4's tax effect reads (spec §5.9: called per year by
services/income_inputs.tax_rate and services/performance).
"""

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Row, and_, exists, insert, select
from sqlalchemy.exc import IntegrityError

from hausbuch.db.tables import document as document_table
from hausbuch.db.tables import owner as owner_table
from hausbuch.db.tables import return_input as return_input_table
from hausbuch.db.tables import workspace as workspace_table
from hausbuch.rules.errors import (
    ANLAGE_N_USEFUL_LIFE_MISSING,
    RETURN_CHILDCARE_AGE,
    RETURN_HOUSEHOLD_SERVICES_CASH,
    RETURN_INPUT_CARRIED_FROM_INVALID,
    RETURN_INPUT_NOT_CURRENT,
    RETURN_INPUT_SCOPE_INVALID,
    RETURN_INPUT_WORK_ROOM_CONFLICT,
    TAX_YEAR_NOT_AVAILABLE,
    RuleError,
)
from hausbuch.rules.tax import registry
from hausbuch.rules.tax.anlage_n._base import SPANNING_KINDS, rules_for
from hausbuch.rules.tax.anlage_n.children import child_months
from hausbuch.rules.tax.anlage_n.draft import draft_n
from hausbuch.rules.tax.anlage_n.summary import TaxContexts
from hausbuch.rules.tax.questionnaire import reminders
from hausbuch.rules.tax.return_estimate import return_estimate
from hausbuch.schemas.employment import (
    HOUSEHOLD_KINDS,
    AnlageNDraft,
    ChildcareFact,
    ChildFact,
    EquipmentFact,
    HouseholdServicesFact,
    OwnerReturnFacts,
    Payslip,
    PropertyIncome,
    ReturnEstimate,
    ReturnInput,
    ReturnInputCreate,
    ReturnInputFact,
    WorkRoomFact,
    employer_key,
    return_input_amounts,
)
from hausbuch.schemas.enums import DocumentType, EntityType, IncomeInputKind, ReturnInputKind
from hausbuch.schemas.events import EventType, ReturnInputRecorded
from hausbuch.schemas.performance import IncomeInputFact

from .allocations import user_id
from .context import CommandContext
from .errors import ConflictError, NotFoundError, RuleViolationError, violated_constraint
from .events import append_event
from .income_inputs import income_facts, owner_profiles
from .lookup import require_row
from .properties import list_properties
from .tax import anlage_v_draft

K = ReturnInputKind
RETURN_INPUT = "return_input"  # NotFoundError's entity name: the row carries no events itself

_SUPERSEDED_ONCE = "return_input_supersedes_id_key"
_SCOPE_COLUMNS = ("kind", "tax_year", "owner_id")
# One current row per owner and year (§6 invariants); a payslip per owner, employer and month.
ONE_PER_YEAR = frozenset(
    {K.HOME_OFFICE, K.WORK_ROOM, K.TELECOM, K.OTHER_INSURANCE, K.VORSORGE_OVERRIDE}
)
_DOCUMENT_TYPES = {
    K.WAGE_CERTIFICATE: DocumentType.LOHNSTEUERBESCHEINIGUNG,
    K.PAYSLIP: DocumentType.GEHALTSABRECHNUNG,
}


# --- Reads -------------------------------------------------------------------------------


def _is_current() -> Any:
    """SQL: the row is neither withdrawn nor superseded by another row."""
    later = return_input_table.alias("later")
    return and_(
        return_input_table.c.withdrawn.is_(False),
        ~exists().where(later.c.supersedes_id == return_input_table.c.id),
    )


def list_return_inputs(
    conn: Connection,
    *,
    tax_year: int | None = None,
    owner_id: UUID | None = None,
    kind: ReturnInputKind | None = None,
    include_history: bool = False,
) -> list[ReturnInput]:
    """GET /return-inputs: the workspace's current rows, oldest first; with include_history
    every row (superseded and withdrawn ones too). tax_year Y keeps the yearly kinds' rows of
    Y and the spanning kinds' (child, work_equipment) rows of Y or earlier (D97); owner_id that
    owner's rows (the household kinds have none); kind that kind's."""
    c = return_input_table.c
    query = select(return_input_table)
    if not include_history:
        query = query.where(_is_current())
    if tax_year is not None:
        spanning = [k.value for k in SPANNING_KINDS]
        query = query.where(
            (c.tax_year == tax_year) | (c.kind.in_(spanning) & (c.tax_year < tax_year))
        )
    if owner_id is not None:
        query = query.where(c.owner_id == owner_id)
    if kind is not None:
        query = query.where(c.kind == kind.value)
    rows = conn.execute(query.order_by(c.recorded_at, c.id))
    return [_to_return_input(r) for r in rows]


def get_return_input(conn: Connection, return_input_id: UUID) -> ReturnInput | None:
    row = conn.execute(
        select(return_input_table).where(return_input_table.c.id == return_input_id)
    ).one_or_none()
    return None if row is None else _to_return_input(row)


def _fact(row: ReturnInput) -> ReturnInputFact:
    return ReturnInputFact.model_validate(
        row.model_dump(exclude={"workspace_id", "recorded_at", "recorded_by"})
    )


def current_rows(conn: Connection) -> list[ReturnInputFact]:
    """Every current return_input row of the workspace, all years, oldest first."""
    return [_fact(r) for r in list_return_inputs(conn)]


def household_rows(conn: Connection) -> list[ReturnInputFact]:
    """The current rows of the household kinds (child, childcare, §35a, donations)."""
    return [r for r in current_rows(conn) if r.kind in HOUSEHOLD_KINDS]


def _expected_pay(inputs: list[IncomeInputFact], owner_id: UUID, tax_year: int):
    """The owner's expected_gross_pay row that applies to `tax_year`: the latest year ≤ it."""
    rows = [
        r
        for r in inputs
        if r.kind is IncomeInputKind.EXPECTED_GROSS_PAY
        and r.owner_id == owner_id
        and r.tax_year <= tax_year
    ]
    return max(rows, key=lambda r: r.tax_year) if rows else None


def owner_facts(conn: Connection, tax_year: int | None = None) -> list[OwnerReturnFacts]:
    """Every owner's current rows (all years; the rules pick the year's), church tax flag and,
    with tax_year, the expected-pay row that applies to it. In the owners' name order."""
    rows = current_rows(conn)
    incomes = income_facts(conn) if tax_year is not None else []
    return [
        OwnerReturnFacts(
            owner_id=p.owner_id,
            church_tax=p.church_tax,
            inputs=[r for r in rows if r.owner_id == p.owner_id],
            expected_gross_pay=(
                _expected_pay(incomes, p.owner_id, tax_year) if tax_year is not None else None
            ),
        )
        for p in owner_profiles(conn)
    ]


def tax_contexts(conn: Connection) -> TaxContexts:
    """The D98 contexts of the household for phase 4's tax effect: call the result with a year
    and the property's result of that year. With no return_input row it gives (None, None)
    for every year, so phase 4's figures stay as they were."""
    return TaxContexts(
        owner_facts=owner_facts(conn),
        household_rows=household_rows(conn),
        rules=rules_for,
        tariffs=registry.tariff,
        expected_pay=income_facts(conn),
    )


# --- The computed reads (GET /owners/{id}/anlage-n, GET /tax/return-estimate) -------------

# What the estimate leaves out (§10's "Nicht berücksichtigt" line); the screen labels them.
NOT_MODELLED = (
    "extraordinary_burden",
    "riester",
    "maintenance",
    "prepayments",
    "capital_income",
)


def anlage_n_draft(conn: Connection, owner_id: UUID, *, tax_year: int) -> AnlageNDraft:
    """One owner's Anlage N for `tax_year`, computed now (D59). NotFoundError for an unknown
    owner; RuleError tax.no_rule_set before 2026 (the year's own rule set: a draft is never
    made under another year's), anlage_n.no_pay without any pay source, and the rules'
    other refusals."""
    require_row(conn, owner_table, EntityType.OWNER, owner_id)
    rs = registry.anlage_n(tax_year)
    facts = next(f for f in owner_facts(conn, tax_year) if f.owner_id == owner_id)
    return draft_n(facts, tax_year, rs, registry.tariff(tax_year))


def property_results(conn: Connection, tax_year: int, today: date) -> list[PropertyIncome]:
    """Every property's Anlage V result of `tax_year`; a property not owned that year (no
    draft for it: tax.year_not_available) has none."""
    out = []
    for prop in list_properties(conn):
        try:
            result = anlage_v_draft(conn, prop.id, tax_year=tax_year, today=today).totals.result
        except RuleError as exc:
            if exc.code == TAX_YEAR_NOT_AVAILABLE:
                continue
            raise
        out.append(PropertyIncome(property_id=prop.id, result=result))
    return out


def household_return_estimate(conn: Connection, *, tax_year: int, today: date) -> ReturnEstimate:
    """GET /tax/return-estimate (D101): the household's whole return for `tax_year`, computed
    now from the owners' rows, the household rows, every property's Anlage V result and the
    year's questionnaire. RuleError tax.no_rule_set before the first Anlage N rule set."""
    from .questionnaire import current_answers, questionnaire_progress

    rs = rules_for(tax_year)
    q = registry.questionnaire(tax_year)
    answers = current_answers(conn, tax_year)
    return return_estimate(
        tax_year,
        owner_profiles(conn),
        owner_facts(conn, tax_year),
        household_rows(conn),
        property_results(conn, tax_year, today),
        questionnaire_progress(conn, tax_year),
        answers,
        registry.tariff(tax_year),
        rs,
        reminders=reminders(answers, q),
        not_modelled=NOT_MODELLED,
    )


# --- Writes ------------------------------------------------------------------------------


def create_input(ctx: CommandContext, data: ReturnInputCreate) -> ReturnInput:
    """POST /return-inputs: one new row and its event (see the module doc for the checks).
    The public entry for every writer (the route, the questionnaire, step 2's pre-fill).
    Raises NotFoundError for an unknown owner, document or referenced row;
    RuleViolationError return_input.scope_invalid, return_input.not_current,
    return_input.carried_from_invalid, return_input.work_room_conflict,
    return.childcare_age, return.household_services_cash or anlage_n.useful_life_missing.
    Nothing is written then."""
    if data.kind not in SPANNING_KINDS:
        # A yearly row before the first Anlage N rule set could never be computed (and would
        # break phase 4's screens that read it): RuleError tax.no_rule_set. A child or a work
        # item may start earlier; only its later years are computed.
        rules_for(data.tax_year)
    _lock_workspace(ctx.conn, ctx.workspace_id)
    if data.owner_id is not None:
        require_row(ctx.conn, owner_table, EntityType.OWNER, data.owner_id)
    _check_document(ctx.conn, data)
    if data.supersedes_id is not None:
        _check_supersedes(ctx.conn, data)
    if data.carried_from_id is not None:
        _check_carried_from(ctx.conn, data)
    if not data.withdrawn:
        _check_no_other_current(ctx.conn, data)
        _check_work_room(ctx.conn, data)
        _check_value(ctx.conn, data)

    try:
        row = ctx.conn.execute(
            insert(return_input_table)
            .values(
                workspace_id=ctx.workspace_id,
                **data.model_dump(exclude={"value"}),
                value=data.value.model_dump(mode="json"),
                recorded_at=ctx.now,
                recorded_by=user_id(ctx),
            )
            .returning(*return_input_table.c)
        ).one()
    except IntegrityError as exc:
        if violated_constraint(exc) == _SUPERSEDED_ONCE:
            # Unreachable while the workspace lock holds; kept so a race is a 409, not a 500.
            raise ConflictError(
                f"return input {data.supersedes_id} was superseded concurrently",
                constraint=_SUPERSEDED_ONCE,
            ) from None
        raise
    created = _to_return_input(row)
    payload = ReturnInputRecorded(
        return_input_id=created.id,
        kind=created.kind,
        tax_year=created.tax_year,
        document_id=created.document_id,
        supersedes_id=created.supersedes_id,
        carried_from_id=created.carried_from_id,
        withdrawn=created.withdrawn,
        amounts=return_input_amounts(created.value),
        months_in_year=_months(created),
    )
    if created.kind in HOUSEHOLD_KINDS:
        append_event(ctx, EventType.WORKSPACE_RETURN_INPUT_RECORDED, ctx.workspace_id, payload)
    else:
        assert created.owner_id is not None
        append_event(ctx, EventType.OWNER_RETURN_INPUT_RECORDED, created.owner_id, payload)
    return created


def _months(row: ReturnInput) -> int | None:
    """A child's months in its first year (D109): the event's only trace of the birth date."""
    if row.kind is not K.CHILD:
        return None
    assert isinstance(row.value, ChildFact)
    months, _ = child_months(row.value, row.tax_year)
    return months


def _lock_workspace(conn: Connection, workspace_id: UUID) -> None:
    """The workspace row, locked FOR NO KEY UPDATE: return input writes of one household
    queue behind each other, while inserts that only reference the workspace don't."""
    conn.execute(
        select(workspace_table.c.id)
        .where(workspace_table.c.id == workspace_id)
        .with_for_update(key_share=True)
    ).one()


def _violation(code: str, message: str) -> RuleViolationError:
    return RuleViolationError([code], message)


def _check_document(conn: Connection, data: ReturnInputCreate) -> None:
    if data.document_id is None:
        return
    document = require_row(conn, document_table, EntityType.DOCUMENT, data.document_id)
    wanted = _DOCUMENT_TYPES.get(data.kind)
    if wanted is not None and document.type != wanted.value:
        raise _violation(
            RETURN_INPUT_SCOPE_INVALID, f"a {data.kind} row's document is a {wanted.value}"
        )


def _find_current(
    conn: Connection, kind: ReturnInputKind, tax_year: int, owner_id: UUID | None
) -> list[ReturnInput]:
    """The current rows of one kind, tax year and owner (oldest first)."""
    c = return_input_table.c
    owner = c.owner_id.is_(None) if owner_id is None else c.owner_id == owner_id
    query = select(return_input_table).where(
        c.kind == kind.value, c.tax_year == tax_year, owner, _is_current()
    )
    return [_to_return_input(r) for r in conn.execute(query.order_by(c.recorded_at, c.id))]


def _check_supersedes(conn: Connection, data: ReturnInputCreate) -> None:
    assert data.supersedes_id is not None
    old = get_return_input(conn, data.supersedes_id)
    if old is None:
        raise NotFoundError(RETURN_INPUT, data.supersedes_id)
    if any(getattr(old, name) != getattr(data, name) for name in _SCOPE_COLUMNS):
        raise _violation(
            RETURN_INPUT_SCOPE_INVALID,
            "a correction supersedes a row of the same kind, year and owner",
        )
    if old.id not in {r.id for r in _find_current(conn, data.kind, data.tax_year, data.owner_id)}:
        raise _violation(
            RETURN_INPUT_NOT_CURRENT,
            f"return input {old.id} is not current (withdrawn or already superseded)",
        )
    if data.withdrawn and old.value != data.value:
        raise _violation(RETURN_INPUT_SCOPE_INVALID, "a withdrawal repeats the withdrawn value")


def _check_carried_from(conn: Connection, data: ReturnInputCreate) -> None:
    """D110: the origin is last year's current row of the same kind and owner."""
    assert data.carried_from_id is not None
    origin = get_return_input(conn, data.carried_from_id)
    if origin is None:
        raise NotFoundError(RETURN_INPUT, data.carried_from_id)
    prior = _find_current(conn, data.kind, data.tax_year - 1, data.owner_id)
    if origin.id not in {r.id for r in prior}:
        raise _violation(
            RETURN_INPUT_CARRIED_FROM_INVALID,
            "carried_from_id names a current row of the same kind and owner of the year before",
        )


def _same_question(row: ReturnInput, data: ReturnInputCreate) -> bool:
    if data.kind in ONE_PER_YEAR:
        return True
    if data.kind is K.PAYSLIP:
        a, b = row.value, data.value
        assert isinstance(a, Payslip) and isinstance(b, Payslip)
        return (employer_key(a.employer), a.period_month) == (
            employer_key(b.employer),
            b.period_month,
        )
    return False  # the item kinds: any number of rows


def _check_no_other_current(conn: Connection, data: ReturnInputCreate) -> None:
    if data.kind not in ONE_PER_YEAR and data.kind is not K.PAYSLIP:
        return
    others = [
        r
        for r in _find_current(conn, data.kind, data.tax_year, data.owner_id)
        if r.id != data.supersedes_id and _same_question(r, data)
    ]
    if others:
        raise _violation(
            RETURN_INPUT_NOT_CURRENT,
            f"return input {others[0].id} already answers this; supersede it (supersedes_id)",
        )


def _check_work_room(conn: Connection, data: ReturnInputCreate) -> None:
    """D106: the Tagespauschale is excluded for a year the work room is the centre all year."""
    if data.kind is K.HOME_OFFICE:
        rooms = _find_current(conn, K.WORK_ROOM, data.tax_year, data.owner_id)
        if any(r.value.months == 12 for r in rooms):  # type: ignore[union-attr]
            raise _violation(
                RETURN_INPUT_WORK_ROOM_CONFLICT,
                "a work room used all year excludes the home-office day rate (D106)",
            )
    elif data.kind is K.WORK_ROOM:
        assert isinstance(data.value, WorkRoomFact)
        if data.value.months == 12 and _find_current(
            conn, K.HOME_OFFICE, data.tax_year, data.owner_id
        ):
            raise _violation(
                RETURN_INPUT_WORK_ROOM_CONFLICT,
                "withdraw the home-office days first: a work room used all year excludes them",
            )


def _check_value(conn: Connection, data: ReturnInputCreate) -> None:
    """What the year's rules would refuse, refused at write (module doc)."""
    v = data.value
    if isinstance(v, HouseholdServicesFact) and not v.paid_to_account:
        raise _violation(
            RETURN_HOUSEHOLD_SERVICES_CASH,
            "a §35a cost paid in cash does not count (§35a Abs. 5 S. 3)",
        )
    if isinstance(v, EquipmentFact) and v.item_class == "other" and v.useful_life_months is None:
        raise _violation(ANLAGE_N_USEFUL_LIFE_MISSING, "an `other` work item needs its useful life")
    if isinstance(v, ChildcareFact):
        children = {
            r.id: r
            for r in list_return_inputs(conn, tax_year=data.tax_year, kind=K.CHILD)
            if r.tax_year <= data.tax_year
        }
        child = children.get(v.child_id)
        if child is None:
            raise _violation(
                RETURN_INPUT_SCOPE_INVALID, "a childcare cost names a current child row"
            )
        care = registry.tariff(data.tax_year).childcare
        assert isinstance(child.value, ChildFact)
        if care is not None and child.value.born_on <= date(data.tax_year - care.max_age, 1, 1):
            raise _violation(
                RETURN_CHILDCARE_AGE,
                f"the child was {care.max_age} before {data.tax_year} began (§10 Abs. 1 Nr. 5)",
            )


def _to_return_input(row: Row) -> ReturnInput:
    return ReturnInput.model_validate(row._asdict())
