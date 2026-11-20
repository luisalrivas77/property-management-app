"""Household scenarios: the levers of the household plan (docs/phases/phase-9.md D248, §7.3,
§11.2, §13).

One append-only table `household_scenario` (migration 0048: SELECT and INSERT only for the
app role). A row is never changed: a correction is a new row whose `supersedes_id` names the
row it replaces, and a withdrawal is a new row with `withdrawn = true` (phase 4's
`projection_scenario` convention). The withdraw command repeats the withdrawn row's name,
flag and levers; a withdrawal posted to the create command is checked against the levers
only, so its name and default flag may differ from the row it withdraws. Every row
is written with its event in the caller's transaction (principle 4):
`household_scenario.created` or `household_scenario.withdrawn`, ids and flags only (the
levers hold amounts and the name is free text, §11.6).

"In force" = not withdrawn and not superseded by any other row. What a write must satisfy,
beyond HouseholdScenarioCreate's own validator (HouseholdLevers checks the ranges):

- `supersedes_id` names a row of this workspace (404 otherwise) that is in force
  (`household_scenario.not_current`); a withdrawal repeats its levers
  (`household_scenario.withdrawal_invalid`);
- every account in `reserve_accounts` (added or removed) exists (404) and is of kind
  bank_current, bank_savings or credit_card (`household_scenario.account_invalid`);
- no one-off lies after the horizon's end, the month `horizon_months` after the newest
  closed month of today's window, or after the last ended month when no month is closed
  (`household_scenario.one_off_outside`). §7.3 amended (PR #434 review D): a one-off at or
  before the newest closed month may stay, so a scenario holding a refund already received
  can still be superseded; the plan ignores it, since its month is measured;
- at most one default in force: a new default while another default is in force and not the
  row being superseded is refused (`household_scenario.not_current`). The table has no
  partial unique index (it cannot see supersession), so this is the service's rule.

- spending changes (D263, §7.6 (1) amended, PR #436 review): a percent change, new or altered
  against the row being superseded, of a code with no measured spending in today's window
  (a mean at or below 0; `household_scenario.spending_invalid`); a change carried unaltered
  is never refused, so a save of another lever (the limits on Übersicht) is never blocked by
  a category that lost its spending since; GET /household-plan names such a change in a
  „Szenario prüfen“ to-do instead. The other refusals are HouseholdLevers' own. A change
  carried unaltered keeps its stored `mean_at_save`; a new or altered one gets the code's
  mean over today's window (0 without three closed months), whatever the request carried.
- budget class overrides (phase 10 D272 (2)): a code that cannot move between Notwendiges and
  Wünsche (a one-off code, `uncategorized`, a code without a class;
  `household_scenario.budget_class_invalid`). rules/budget ignores such a stored override.

A withdrawal is not re-checked against the accounts, the horizon or the spending: it only
ends a row, and it repeats the withdrawn row's levers with their `mean_at_save` as stored.
`check_levers` is the same set of checks for POST /household-plan/preview, which writes
nothing.

Writes of one workspace are serialised by a transaction-scoped advisory lock, so the "one
default" check cannot race; `supersedes_id` is UNIQUE in the table as well.
"""

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Row, and_, exists, func, insert, select
from sqlalchemy.exc import IntegrityError

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import household_scenario as scenario_table
from hausbuch.rules.budget.budget import refused_overrides
from hausbuch.rules.categories import CATEGORY_LABEL
from hausbuch.rules.errors import (
    HOUSEHOLD_SCENARIO_ACCOUNT_INVALID,
    HOUSEHOLD_SCENARIO_BUDGET_CLASS_INVALID,
    HOUSEHOLD_SCENARIO_NOT_CURRENT,
    HOUSEHOLD_SCENARIO_ONE_OFF_OUTSIDE,
    HOUSEHOLD_SCENARIO_SPENDING_INVALID,
    HOUSEHOLD_SCENARIO_WITHDRAWAL_INVALID,
)
from hausbuch.rules.plan import spending as SP
from hausbuch.rules.plan.levers import add_months, one_offs_outside
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.enums import AccountKind, EntityType
from hausbuch.schemas.events import EventType, HouseholdScenarioRecorded
from hausbuch.schemas.health import month_of
from hausbuch.schemas.household import BudgetClass
from hausbuch.schemas.plan import (
    HouseholdLevers,
    HouseholdScenario,
    HouseholdScenarioCreate,
    SpendingChange,
)

from .allocations import user_id
from .context import CommandContext
from .documents import LOCAL_TIME_ZONE
from .errors import ConflictError, NotFoundError, RuleViolationError, violated_constraint
from .events import append_event
from .health import MonthRead, _window, ended_months, read_months

RESERVE_KINDS = frozenset(
    {AccountKind.BANK_CURRENT, AccountKind.BANK_SAVINGS, AccountKind.CREDIT_CARD}
)
_SUPERSEDED_ONCE = "household_scenario_supersedes_id_key"


# --- Reads -------------------------------------------------------------------------------


def _in_force() -> Any:
    """SQL: the row is neither withdrawn nor superseded by another row."""
    later = scenario_table.alias("later")
    return and_(
        scenario_table.c.withdrawn.is_(False),
        ~exists().where(later.c.supersedes_id == scenario_table.c.id),
    )


def list_scenarios(conn: Connection, *, include_history: bool = False) -> list[HouseholdScenario]:
    """GET /household-scenarios: the scenarios in force, oldest first; with include_history
    every row, the withdrawn and superseded ones too."""
    query = select(scenario_table)
    if not include_history:
        query = query.where(_in_force())
    rows = conn.execute(query.order_by(scenario_table.c.created_at, scenario_table.c.id))
    return [_to_scenario(r) for r in rows]


def get_scenario(conn: Connection, scenario_id: UUID) -> HouseholdScenario | None:
    row = conn.execute(
        select(scenario_table).where(scenario_table.c.id == scenario_id)
    ).one_or_none()
    return None if row is None else _to_scenario(row)


def is_in_force(conn: Connection, scenario_id: UUID) -> bool:
    row = conn.execute(
        select(scenario_table.c.id).where(scenario_table.c.id == scenario_id, _in_force())
    ).one_or_none()
    return row is not None


def default_scenario(conn: Connection) -> HouseholdScenario | None:
    """The default household scenario in force (the newest when two are), None when none is.
    The same row health.household_levers reads."""
    row = conn.execute(
        select(scenario_table)
        .where(scenario_table.c.is_default.is_(True), _in_force())
        .order_by(scenario_table.c.created_at.desc(), scenario_table.c.id)
        .limit(1)
    ).one_or_none()
    return None if row is None else _to_scenario(row)


# --- Writes ------------------------------------------------------------------------------


def create_scenario(ctx: CommandContext, data: HouseholdScenarioCreate) -> HouseholdScenario:
    """POST /household-scenarios: one new row and its event (see the module doc for the
    checks). NotFoundError for an unknown superseded row or reserve account;
    RuleViolationError household_scenario.not_current, .account_invalid, .one_off_outside or
    .withdrawal_invalid. Nothing is written then."""
    _lock(ctx)
    old = None if data.supersedes_id is None else _check_supersedes(ctx.conn, data)
    if not data.withdrawn:
        today = ctx.now.astimezone(LOCAL_TIME_ZONE).date()
        previous = [] if old is None else old.levers.spending_changes
        means = check_levers(
            ctx.conn,
            data.levers,
            today,
            previous=previous,
            previous_overrides={} if old is None else old.levers.budget_class_overrides,
        )
        if data.is_default:
            _check_one_default(ctx.conn, data)
        if data.levers.spending_changes:
            changes = SP.with_means_at_save(data.levers.spending_changes, means, previous)
            levers = data.levers.model_copy(update={"spending_changes": changes})
            data = data.model_copy(update={"levers": levers})
    return _insert(ctx, data)


def check_levers(
    conn: Connection,
    levers: HouseholdLevers,
    today: date,
    reads: dict[str, MonthRead] | None = None,
    *,
    previous: Sequence[SpendingChange] = (),
    previous_overrides: Mapping[AllocationCategory, BudgetClass] | None = None,
) -> dict[AllocationCategory, Decimal]:
    """The checks of a scenario's levers beyond HouseholdLevers' own (the module doc), for a
    new row and for the preview; returns the codes' means over today's window (§7.6 (2)).
    `reads`: today's months as services.health.read_months gives them (the preview passes the
    reads its plan uses); read here once, and only for one-offs or spending changes.
    `previous`: the spending changes of the row a save supersedes (for the preview the default
    in force); only a change new or altered against them is refused as a percent of
    nothing (§7.6 (1) amended). `previous_overrides`: that row's budget class overrides; one
    carried unaltered is not refused (phase 10 D272 (2), as a carried spending change)."""
    _check_accounts(conn, levers)
    _check_budget_classes(levers, previous_overrides or {})
    if not levers.one_offs and not levers.spending_changes:
        return {}
    if reads is None:
        reads = read_months(conn, ended_months(today), today)
    _check_one_offs(levers, today, reads)
    if not levers.spending_changes:
        return {}
    means = window_means(reads)
    code = SP.percent_on_nothing(SP.typed_now(levers.spending_changes, previous), means)
    if code is not None:
        raise RuleViolationError(
            [HOUSEHOLD_SCENARIO_SPENDING_INVALID],
            f"a percent change of {code.value} ({CATEGORY_LABEL[code]}), which has no "
            "measured spending in the window",
        )
    return means


def _check_budget_classes(
    levers: HouseholdLevers, previous: Mapping[AllocationCategory, BudgetClass]
) -> None:
    """Phase 10 D272 (2): a code moved to the other class must be movable: it has a class and
    is not one-off (rules.budget.budget.movable). rules/budget ignores such an override on
    read, so a stored row still reads; a save carrying one new or altered against `previous`
    (the row it supersedes) is refused, one carried unaltered is not, so a code that stops
    being movable never blocks saving the other levers."""
    added = {
        code: cls
        for code, cls in levers.budget_class_overrides.items()
        if previous.get(code) is not cls
    }
    refused = refused_overrides(added)
    if refused:
        raise RuleViolationError(
            [HOUSEHOLD_SCENARIO_BUDGET_CLASS_INVALID],
            "a budget class for "
            + ", ".join(f"{c.value} ({CATEGORY_LABEL[c]})" for c in refused)
            + ", which cannot be moved between Notwendiges and Wünsche",
        )


def window_means(reads: dict[str, MonthRead]) -> dict[AllocationCategory, Decimal]:
    """§7.6 (2): each code's mean over the window of closed months; none below three."""
    win = _window(reads)
    if not win.enough:
        return {}
    return SP.code_means([SP.month_spending(reads[m].private.cashflow) for m in win.months])


def withdraw_scenario(ctx: CommandContext, scenario_id: UUID) -> HouseholdScenario:
    """POST /household-scenarios/{id}/withdraw: a new row that repeats the scenario's name,
    flag and levers with `withdrawn = true`. NotFoundError for an unknown row;
    household_scenario.not_current when it is no longer in force."""
    _lock(ctx)
    old = get_scenario(ctx.conn, scenario_id)
    if old is None:
        raise NotFoundError(EntityType.HOUSEHOLD_SCENARIO, scenario_id)
    data = HouseholdScenarioCreate(
        name=old.name,
        is_default=old.is_default,
        levers=old.levers,
        supersedes_id=old.id,
        withdrawn=True,
    )
    _check_supersedes(ctx.conn, data)
    return _insert(ctx, data)


def _insert(ctx: CommandContext, data: HouseholdScenarioCreate) -> HouseholdScenario:
    try:
        row = ctx.conn.execute(
            insert(scenario_table)
            .values(
                workspace_id=ctx.workspace_id,
                name=data.name,
                is_default=data.is_default,
                levers=data.levers.model_dump(mode="json"),
                supersedes_id=data.supersedes_id,
                withdrawn=data.withdrawn,
                created_by=user_id(ctx),
                created_at=ctx.now,
            )
            .returning(*scenario_table.c)
        ).one()
    except IntegrityError as exc:
        if violated_constraint(exc) == _SUPERSEDED_ONCE:
            # Unreachable while the workspace lock holds; kept so a race is a 409, not a 500.
            raise ConflictError(
                f"household scenario {data.supersedes_id} was superseded concurrently",
                constraint=_SUPERSEDED_ONCE,
            ) from None
        raise
    created = _to_scenario(row)
    append_event(
        ctx,
        EventType.HOUSEHOLD_SCENARIO_WITHDRAWN
        if created.withdrawn
        else EventType.HOUSEHOLD_SCENARIO_CREATED,
        created.id,
        HouseholdScenarioRecorded(
            is_default=created.is_default, supersedes_id=created.supersedes_id
        ),
    )
    return created


# --- Checks ------------------------------------------------------------------------------


def _lock(ctx: CommandContext) -> None:
    """One household scenario write at a time per workspace."""
    key = func.hashtextextended(f"household_scenario:{ctx.workspace_id}", 0)
    ctx.conn.execute(select(func.pg_advisory_xact_lock(key)))


def _not_current(message: str) -> RuleViolationError:
    return RuleViolationError([HOUSEHOLD_SCENARIO_NOT_CURRENT], message)


def _check_supersedes(conn: Connection, data: HouseholdScenarioCreate) -> HouseholdScenario:
    """The row `data` supersedes, in force; refused otherwise (the module doc)."""
    assert data.supersedes_id is not None
    old = get_scenario(conn, data.supersedes_id)
    if old is None:
        raise NotFoundError(EntityType.HOUSEHOLD_SCENARIO, data.supersedes_id)
    if not is_in_force(conn, old.id):
        raise _not_current(f"household scenario {old.id} is withdrawn or already superseded")
    if data.withdrawn and old.levers != data.levers:
        raise RuleViolationError(
            [HOUSEHOLD_SCENARIO_WITHDRAWAL_INVALID],
            "a withdrawal repeats the withdrawn scenario's levers",
        )
    return old


def _check_accounts(conn: Connection, levers: HouseholdLevers) -> None:
    named = levers.reserve_accounts.added + levers.reserve_accounts.removed
    if not named:
        return
    acc = account_table.c
    kinds = {
        r.id: AccountKind(r.kind)
        for r in conn.execute(select(acc.id, acc.kind).where(acc.id.in_(named)))
    }
    for account_id in named:
        if account_id not in kinds:
            raise NotFoundError(EntityType.ACCOUNT, account_id)
        if kinds[account_id] not in RESERVE_KINDS:
            raise RuleViolationError(
                [HOUSEHOLD_SCENARIO_ACCOUNT_INVALID],
                f"account {account_id} is a {kinds[account_id].value}: the reserve counts"
                " current and savings accounts and credit cards only",
            )


def newest_closed_month(conn: Connection, today: date) -> str:
    """The month the plan's months count from: the newest closed month of today's window,
    else the last month that has ended."""
    return _newest(read_months(conn, ended_months(today), today), today)


def _newest(reads: dict[str, MonthRead], today: date) -> str:
    win = _window(reads)
    if win.months:
        return win.months[-1]
    ended = ended_months(today)
    return ended[-1] if ended else month_of(today)


def _check_one_offs(levers: HouseholdLevers, today: date, reads: dict[str, MonthRead]) -> None:
    if not levers.one_offs:
        return
    newest = _newest(reads, today)
    outside = one_offs_outside(levers.one_offs, newest, levers.horizon_months)
    if outside:
        last = add_months(newest, levers.horizon_months)
        raise RuleViolationError(
            [HOUSEHOLD_SCENARIO_ONE_OFF_OUTSIDE],
            f"a one-off in {outside[0].month} lies outside the horizon,"
            f" {add_months(newest, 1)} to {last}",
        )


def _check_one_default(conn: Connection, data: HouseholdScenarioCreate) -> None:
    rows = conn.execute(
        select(scenario_table.c.id).where(scenario_table.c.is_default.is_(True), _in_force())
    ).scalars()
    others = [r for r in rows if r != data.supersedes_id]
    if others:
        raise _not_current(
            f"household scenario {others[0]} is the default; supersede it (supersedes_id) or"
            " record this one without is_default"
        )


def _to_scenario(row: Row) -> HouseholdScenario:
    return HouseholdScenario.model_validate(row._asdict())
