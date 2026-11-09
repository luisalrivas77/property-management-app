"""Projection scenarios: the levers of a property's projection (docs/phases/phase-4.md D87, §6).

One append-only table `projection_scenario` (migration 0016: SELECT and INSERT only for the
app role, a forbid_mutation trigger). A row is never changed: a correction is a new row whose
`supersedes_id` names the row it replaces, and a withdrawal is a new row with
`withdrawn = true` that repeats the withdrawn row's levers. Every row is written together with
its `property.scenario_recorded` event, in the caller's transaction (principle 4). The event
carries the levers and flags, never the name (free text).

"In force" = not withdrawn and not superseded by any other row. What a write must satisfy,
beyond ScenarioCreate's own validator:

- the path's property is the body's, and every loan lever names a loan of that property
  (`scenario.scope_invalid`);
- `supersedes_id` names a scenario in force of the same property (`scenario.not_current`; one
  of another property is `scenario.scope_invalid`, the table's FK says the same); a
  withdrawal repeats its levers;
- the sale year is after today's year: the projection starts on 1 January after today and
  the sale is on 31 December of the sale year (D86, D88), so a sale this year or earlier has
  no projected year (`scenario.sale_year_before_today`); a withdrawal is not re-checked;
- at most one default in force per property: a new default while another default is in force
  and not the row being superseded is refused (`scenario.not_current`: supersede it, or
  record this one without the flag). A partial unique index cannot see supersession, so this
  is the service's rule (migration 0016).

Writes for one property are serialised by locking the property row (FOR NO KEY UPDATE), as
tax inputs do, so the "one default" check cannot race; `supersedes_id` is UNIQUE in the table
as well.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Row, and_, exists, insert, select
from sqlalchemy.exc import IntegrityError

from hausbuch.db.tables import loan as loan_table
from hausbuch.db.tables import projection_scenario as scenario_table
from hausbuch.db.tables import property_ as property_table
from hausbuch.rules.errors import (
    SCENARIO_NOT_CURRENT,
    SCENARIO_SALE_YEAR_BEFORE_TODAY,
    SCENARIO_SCOPE_INVALID,
)
from hausbuch.schemas.enums import EntityType
from hausbuch.schemas.events import EventType, ScenarioRecorded
from hausbuch.schemas.performance import Scenario, ScenarioCreate

from .allocations import user_id
from .context import CommandContext
from .documents import LOCAL_TIME_ZONE
from .errors import ConflictError, NotFoundError, RuleViolationError, violated_constraint
from .events import append_event
from .lookup import require_row

SCENARIO = "scenario"  # NotFoundError's entity name: the row carries no events of its own
_SUPERSEDED_ONCE = "projection_scenario_supersedes_id_key"


# --- Reads -------------------------------------------------------------------------------


def _in_force() -> Any:
    """SQL: the row is neither withdrawn nor superseded by another row."""
    later = scenario_table.alias("later")
    return and_(
        scenario_table.c.withdrawn.is_(False),
        ~exists().where(later.c.supersedes_id == scenario_table.c.id),
    )


def list_scenarios(
    conn: Connection, property_id: UUID, *, include_history: bool = False
) -> list[Scenario]:
    """GET /properties/{id}/scenarios: the property's scenarios in force, oldest first; with
    include_history every row. NotFoundError for an unknown property."""
    require_row(conn, property_table, EntityType.PROPERTY, property_id)
    query = select(scenario_table).where(scenario_table.c.property_id == property_id)
    if not include_history:
        query = query.where(_in_force())
    rows = conn.execute(query.order_by(scenario_table.c.recorded_at, scenario_table.c.id))
    return [_to_scenario(r) for r in rows]


def get_scenario(conn: Connection, scenario_id: UUID) -> Scenario | None:
    row = conn.execute(
        select(scenario_table).where(scenario_table.c.id == scenario_id)
    ).one_or_none()
    return None if row is None else _to_scenario(row)


def is_in_force(conn: Connection, scenario_id: UUID) -> bool:
    row = conn.execute(
        select(scenario_table.c.id).where(scenario_table.c.id == scenario_id, _in_force())
    ).one_or_none()
    return row is not None


def default_scenario(conn: Connection, property_id: UUID) -> Scenario | None:
    """The property's default scenario in force, None when it has none."""
    row = conn.execute(
        select(scenario_table)
        .where(
            scenario_table.c.property_id == property_id,
            scenario_table.c.is_default.is_(True),
            _in_force(),
        )
        .order_by(scenario_table.c.recorded_at.desc(), scenario_table.c.id)
        .limit(1)
    ).one_or_none()
    return None if row is None else _to_scenario(row)


# --- Writes ------------------------------------------------------------------------------


def create_scenario(ctx: CommandContext, property_id: UUID, data: ScenarioCreate) -> Scenario:
    """POST /properties/{id}/scenarios: one new row and its event (see the module doc for the
    checks). NotFoundError for an unknown property, loan or superseded row; RuleViolationError
    scenario.scope_invalid, scenario.not_current or scenario.sale_year_before_today. Nothing
    is written then."""
    if data.property_id != property_id:
        raise _scope_invalid("the body names another property than the path")
    _lock_property(ctx.conn, property_id)
    _check_loans(ctx.conn, data)
    if data.supersedes_id is not None:
        _check_supersedes(ctx.conn, data)
    if not data.withdrawn:
        today = ctx.now.astimezone(LOCAL_TIME_ZONE).date()
        if data.levers.sale_year <= today.year:
            raise RuleViolationError(
                [SCENARIO_SALE_YEAR_BEFORE_TODAY],
                f"the sale year {data.levers.sale_year} is not after {today.year}: the"
                " projection starts next January",
            )
        if data.is_default:
            _check_one_default(ctx.conn, data)

    try:
        row = ctx.conn.execute(
            insert(scenario_table)
            .values(
                workspace_id=ctx.workspace_id,
                **data.model_dump(exclude={"levers"}),
                levers=data.levers.model_dump(mode="json"),
                recorded_at=ctx.now,
                recorded_by=user_id(ctx),
            )
            .returning(*scenario_table.c)
        ).one()
    except IntegrityError as exc:
        if violated_constraint(exc) == _SUPERSEDED_ONCE:
            # Unreachable while the property lock holds; kept so a race is a 409, not a 500.
            raise ConflictError(
                f"scenario {data.supersedes_id} was superseded concurrently",
                constraint=_SUPERSEDED_ONCE,
            ) from None
        raise
    created = _to_scenario(row)
    append_event(
        ctx,
        EventType.PROPERTY_SCENARIO_RECORDED,
        property_id,
        ScenarioRecorded(
            scenario_id=created.id,
            is_default=created.is_default,
            levers=created.levers,
            supersedes_id=created.supersedes_id,
            withdrawn=created.withdrawn,
        ),
        property_id=property_id,
    )
    return created


# --- Checks ------------------------------------------------------------------------------


def _lock_property(conn: Connection, property_id: UUID) -> Row:
    """The property row, locked FOR NO KEY UPDATE: scenario writes of one property queue
    behind each other."""
    row = conn.execute(
        select(property_table)
        .where(property_table.c.id == property_id)
        .with_for_update(key_share=True)
    ).one_or_none()
    if row is None:
        raise NotFoundError(EntityType.PROPERTY, property_id)
    return row


def _scope_invalid(message: str) -> RuleViolationError:
    return RuleViolationError([SCENARIO_SCOPE_INVALID], message)


def _check_loans(conn: Connection, data: ScenarioCreate) -> None:
    for lever in data.levers.loans:
        loan = require_row(conn, loan_table, EntityType.LOAN, lever.loan_id)
        if loan.property_id != data.property_id:
            raise _scope_invalid("a loan lever names a loan of another property")


def _check_supersedes(conn: Connection, data: ScenarioCreate) -> None:
    assert data.supersedes_id is not None
    old = get_scenario(conn, data.supersedes_id)
    if old is None:
        raise NotFoundError(SCENARIO, data.supersedes_id)
    if old.property_id != data.property_id:
        raise _scope_invalid("a correction supersedes a scenario of the same property")
    if not is_in_force(conn, old.id):
        raise RuleViolationError(
            [SCENARIO_NOT_CURRENT],
            f"scenario {old.id} is not in force (withdrawn or already superseded)",
        )
    if data.withdrawn and old.levers != data.levers:
        raise _scope_invalid("a withdrawal repeats the withdrawn scenario's levers")


def _check_one_default(conn: Connection, data: ScenarioCreate) -> None:
    rows = conn.execute(
        select(scenario_table.c.id).where(
            scenario_table.c.property_id == data.property_id,
            scenario_table.c.is_default.is_(True),
            _in_force(),
        )
    ).scalars()
    others = [r for r in rows if r != data.supersedes_id]
    if others:
        raise RuleViolationError(
            [SCENARIO_NOT_CURRENT],
            f"scenario {others[0]} is the default; supersede it (supersedes_id) or record this"
            " one without is_default",
        )


def _to_scenario(row: Row) -> Scenario:
    return Scenario.model_validate(row._asdict())
