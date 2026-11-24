"""A person's answers about the household's spending (docs/phases/phase-10.md §7.9 (b), (c),
(f); D295 (2), D296, D297): the insert-only `spending_mark` rows (migration 0056).

- **Reads**: the answers in the order stored (`created_at`, then id); the newest per target
  counts (rules/recurring). `one_off_marked` is what services.health.read_months passes to the
  household month, so every reader of the household month (the key figures, the plan, the
  Budget, Haushalt's differences, the daily step, `health-check`) sees the same marks.
- **Writes**: POST /household/one-off-marks answers one confirmed private spending payment
  `one_off` or `regular`: 404 for an id that is no such allocation of the workspace (a
  proposed, rejected or superseded allocation, a rental line, money in). POST
  /household/recurring-marks answers a series by its payee key and interval `hidden` or
  `shown`; it is not checked against today's series: an answer naming no series is stored
  and ignored by the rule, as one whose payee's recognition changed later is (D296 (4)).
  Each answer writes one row and the event `household.mark_recorded` in the same
  transaction; the event carries the allocation id or the interval and the answer, never the
  series key, which is the payee's words.
- `created_at` is the database's clock at the insert (`clock_timestamp()`), so two answers
  are ordered as given even inside one transaction.

Nothing here changes a stored figure: no month is rewritten (§7.9 (c) 3).
"""

from uuid import UUID

from sqlalchemy import Connection, func, insert, select

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import spending_mark as mark_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.rules.budget.budget import is_spending
from hausbuch.rules.health.month import cashflow_rows
from hausbuch.rules.recurring import SeriesMark, newest_allocation_marks, one_off_ids
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.enums import AccountPurpose, AllocationStatus
from hausbuch.schemas.events import EventType, SpendingMarkRecordedEvent
from hausbuch.schemas.health import MonthLine
from hausbuch.schemas.recurring import (
    OneOffMarkRequest,
    RecurringInterval,
    SeriesMarkRequest,
    SpendingMarkAnswer,
    SpendingMarkRecorded,
)

from .allocations import user_id
from .context import CommandContext
from .errors import NotFoundError
from .events import append_event

# --- Reads -------------------------------------------------------------------------------


def _ordered():  # noqa: ANN202
    m = mark_table.c
    return select(m.allocation_id, m.series_key, m.series_interval, m.answer).order_by(
        m.created_at, m.id
    )


def allocation_answers(conn: Connection) -> dict[UUID, SpendingMarkAnswer]:
    """The newest answer per answered allocation."""
    m = mark_table.c
    return newest_allocation_marks(
        (r.allocation_id, SpendingMarkAnswer(r.answer))
        for r in conn.execute(_ordered().where(m.allocation_id.is_not(None)))
    )


def one_off_marked(conn: Connection) -> frozenset[UUID]:
    """The allocations whose newest answer is `one_off` (§7.9 (c) 2)."""
    return one_off_ids(allocation_answers(conn))


def series_marks(conn: Connection) -> list[SeriesMark]:
    """The answers on series, in the order stored."""
    m = mark_table.c
    return [
        SeriesMark(r.series_key, RecurringInterval(r.series_interval), SpendingMarkAnswer(r.answer))
        for r in conn.execute(_ordered().where(m.series_key.is_not(None)))
    ]


# --- Writes ------------------------------------------------------------------------------


def _is_private_spending(conn: Connection, allocation_id: UUID) -> bool:
    """Whether the allocation is a confirmed spending payment of a private account's line
    that is no duplicate: money out in a code the household month counts as spending."""
    a, t, acc = allocation_table.c, transaction_table.c, account_table.c
    row = conn.execute(
        select(a.category, a.amount)
        .join(transaction_table, t.id == a.transaction_id)
        .join(account_table, acc.id == t.account_id)
        .where(
            a.id == allocation_id,
            a.status == AllocationStatus.CONFIRMED.value,
            acc.purpose == AccountPurpose.PRIVATE.value,
            t.excluded_as_duplicate_of.is_(None),
        )
    ).one_or_none()
    if row is None or row.amount >= 0:
        return False
    line = MonthLine(
        allocation_id=allocation_id, category=AllocationCategory(row.category), amount=row.amount
    )
    ((category, _),) = cashflow_rows([line])
    return is_spending(category)


def _insert(ctx: CommandContext, **values: object) -> SpendingMarkRecorded:
    row = ctx.conn.execute(
        insert(mark_table)
        .values(
            workspace_id=ctx.workspace_id,
            created_by=user_id(ctx),
            created_at=func.clock_timestamp(),
            **values,
        )
        .returning(mark_table.c.id, mark_table.c.answer, mark_table.c.created_at)
    ).one()
    return SpendingMarkRecorded(
        id=row.id, answer=SpendingMarkAnswer(row.answer), created_at=row.created_at
    )


def record_one_off(ctx: CommandContext, data: OneOffMarkRequest) -> SpendingMarkRecorded:
    """POST /household/one-off-marks (see the module)."""
    if not _is_private_spending(ctx.conn, data.allocation_id):
        raise NotFoundError("allocation", data.allocation_id)
    recorded = _insert(ctx, allocation_id=data.allocation_id, answer=data.answer.value)
    append_event(
        ctx,
        EventType.HOUSEHOLD_MARK_RECORDED,
        recorded.id,
        SpendingMarkRecordedEvent(allocation_id=data.allocation_id, answer=data.answer),
    )
    return recorded


def record_series(ctx: CommandContext, data: SeriesMarkRequest) -> SpendingMarkRecorded:
    """POST /household/recurring-marks (see the module)."""
    recorded = _insert(
        ctx, series_key=data.key, series_interval=data.interval.value, answer=data.answer.value
    )
    append_event(
        ctx,
        EventType.HOUSEHOLD_MARK_RECORDED,
        recorded.id,
        SpendingMarkRecordedEvent(interval=data.interval, answer=data.answer),
    )
    return recorded
