"""A person's answers about the household's spending (docs/phases/phase-10.md §7.9 (b), (c),
(f); D295 (2), D296, D297): the insert-only `spending_mark` rows (migration 0056).

- **Reads**: the answers in the order stored (`created_at`, then id); the newest per target
  counts (rules/recurring). `one_off_marked` is what services.health.read_months passes to the
  household month, so every reader of the household month (the key figures, the plan, the
  Budget, Haushalt's differences, the daily step, `health-check`) sees the same marks.
- **Writes**: POST /household/one-off-marks answers `one_off` or `regular` on exactly a
  payment GET /household/one-off-candidates lists as of the same day: a candidate or an
  answered payment, over the same window and the lines up to today. Anything else is a 404,
  as an id of another workspace is: a payment under the limits, in a code the candidates
  leave out (`hh_cash`, a one-off code), in a recurring series, dated after today or outside
  the window, a proposed, rejected or superseded allocation, a rental line, money in, a
  privately paid purchase cost (D234), and every payment while the window has fewer than
  three closed months (review of PR #590).
  POST /household/recurring-marks answers a series by its payee key and interval `hidden`
  or `shown`; it is not checked against today's series: an answer naming no series is
  stored and ignored by the rule, as one whose payee's recognition changed later is (D296
  (4)). Each answer writes one row and the event `household.mark_recorded` in the same
  transaction; the event carries the allocation id or the interval and the answer, never the
  series key, which is the payee's words.
- `created_at` is the database's clock at the insert (`clock_timestamp()`), so two answers
  are ordered as given even inside one transaction.

Nothing here changes a stored figure: no month is rewritten (§7.9 (c) 3).
"""

from datetime import date
from uuid import UUID

from sqlalchemy import Connection, func, insert, select

from hausbuch.db.tables import spending_mark as mark_table
from hausbuch.rules.recurring import SeriesMark, newest_allocation_marks, one_off_ids
from hausbuch.schemas.events import EventType, SpendingMarkRecordedEvent
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

HISTORY_START = date(2026, 1, 1)  # the household's bank history (§7.9 (b) 1)

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


def _markable(conn: Connection, today: date) -> set[UUID]:
    """The payments GET /household/one-off-candidates lists as of `today`: its candidates and
    its answered payments, both over the closed months' window and the lines up to `today`
    (empty while the window has fewer than three closed months)."""
    from .recurring import one_off_candidates  # it imports this module's reads

    listed = one_off_candidates(conn, today)
    return {c.payment.allocation_id for c in (*listed.candidates, *listed.answered)}


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


def record_one_off(
    ctx: CommandContext, data: OneOffMarkRequest, today: date
) -> SpendingMarkRecorded:
    """POST /household/one-off-marks (see the module); `today` the Berlin day the GET reads."""
    if data.allocation_id not in _markable(ctx.conn, today):
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
