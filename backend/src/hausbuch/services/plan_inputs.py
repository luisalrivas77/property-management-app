"""The household plan's reads, kept for a data version (docs/phases/phase-10.md §7.10 (b)).

`POST /household-plan/preview` (the what-if) and `GET /household-plan` read the same rows on
every call, whatever levers they are given: the closed months (services.health's month
inputs), the savings-plan payments, the stored month-end and the net worth, the reserve's
account balances, the property projections, the accounts, the scenario rows and the
refund's return estimates. Only the pure rules (rules/plan/, rules/health/, rules/goal/)
depend on the levers. `PlanInputs` reads each of those once and keeps it while the
workspace's data version (migration 0050, phase 10 §7.4) and the day stay the same, so a
preview with new levers runs the rules only.

What is kept: rows and facts read from the database, never a figure computed with levers
(no mean, no household month, no reserve, no plan). The household month, the window, the
codes' means and everything after them are recomputed on each call from the kept inputs
(services.health.months_from_inputs), so a lever that reaches the household month (phase 10
§7.12's one-off switches, D307: `one_off_codes` below) never meets a stale figure.

When a part is kept, and for how long:
- the key is the workspace, its data version, the day the caller passes (`today`, Berlin)
  and the UTC day now, as §7.4's kept answers are keyed (api/kept_answers.py), so every
  committed write and a new day each start afresh;
- a part read in a call is kept only when the data version read again after it equals the
  one read before it (a commit meanwhile may have shown the read either side's rows);
- a transaction that has written to the workspace (its `app.data_version_queued` is set by
  migration 0050's marks) neither reads nor keeps a part: its own uncommitted rows are not
  the version's; nor does one without a version row or without `app.workspace_id`;
- one entry per workspace, the least recently used of MAX_WORKSPACES dropped first; an
  entry under an older version or day is replaced, never served;
- the kept answers' checks (api/kept_answers.py `check`, `compare_all`) recompute with
  `bypass()` set, so a check compares with a computation from the database.

Kept objects are shared between requests and threads: no reader may change one. The pure
rules build new values; the accessors here hand out new containers where a caller appends.
"""

import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import Connection, text

from hausbuch.schemas.allocation import AllocationCategory

from .health import MonthInputs, MonthRead, months_from_inputs, read_month_inputs

T = TypeVar("T")

MAX_WORKSPACES = 4

_STAMP = text(
    "SELECT current_setting('app.workspace_id', true) AS workspace,"
    " coalesce(current_setting('app.data_version_queued', true), '') AS queued,"
    " (SELECT v.version FROM workspace_data_version v"
    "  WHERE v.workspace_id::text = current_setting('app.workspace_id', true)) AS version"
)

_bypass: ContextVar[bool] = ContextVar("hausbuch_plan_inputs_bypass", default=False)


@contextmanager
def bypass() -> Iterator[None]:
    """Inside it, PlanInputs reads every part from the database and keeps nothing."""
    token = _bypass.set(True)
    try:
        yield
    finally:
        _bypass.reset(token)


@dataclass(frozen=True)
class Stamp:
    workspace_id: UUID
    version: int
    today: date
    utc_day: date


@dataclass
class _Entry:
    stamp: Stamp
    parts: dict[Hashable, Any] = field(default_factory=dict)


class _Store:
    def __init__(self) -> None:
        self._entries: OrderedDict[UUID, _Entry] = OrderedDict()
        self._lock = threading.Lock()
        self.enabled = True

    def get(self, stamp: Stamp, key: Hashable) -> tuple[bool, Any]:
        with self._lock:
            entry = self._entries.get(stamp.workspace_id)
            if entry is None or entry.stamp != stamp or key not in entry.parts:
                return False, None
            self._entries.move_to_end(stamp.workspace_id)
            return True, entry.parts[key]

    def put(self, stamp: Stamp, key: Hashable, value: Any) -> None:
        with self._lock:
            entry = self._entries.get(stamp.workspace_id)
            if entry is None or entry.stamp != stamp:
                entry = _Entry(stamp)
                self._entries[stamp.workspace_id] = entry
            entry.parts[key] = value
            self._entries.move_to_end(stamp.workspace_id)
            while len(self._entries) > MAX_WORKSPACES:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


STORE = _Store()


def clear() -> None:
    """Forget every kept part (tests)."""
    STORE.clear()


def _stamp(conn: Connection, today: date) -> Stamp | None:
    """The key this transaction may keep under, or None: no workspace set, no version row,
    or the transaction has written to a workspace (its rows are not the version's)."""
    row = conn.execute(_STAMP).one()
    if not row.workspace or row.version is None or row.queued:
        return None
    return Stamp(UUID(row.workspace), row.version, today, datetime.now(UTC).date())


class PlanInputs:
    """The lever-independent reads of one call on `today`, each read once per call and,
    while the data version holds, once per version (the module doc)."""

    def __init__(self, conn: Connection, today: date) -> None:
        self.conn = conn
        self.today = today
        self._stamp = None if _bypass.get() or not STORE.enabled else _stamp(conn, today)
        self._local: dict[Hashable, Any] = {}

    @property
    def keeps(self) -> bool:
        return self._stamp is not None

    def part(self, key: Hashable, read: Callable[[Connection], T]) -> T:
        """`read(conn)`'s value under `key`: from this call, else kept, else read now (and
        kept when the version did not move meanwhile). An exception of `read` propagates and
        nothing is kept."""
        if key in self._local:
            return self._local[key]
        found, value = (False, None) if self._stamp is None else STORE.get(self._stamp, key)
        if not found:
            value = read(self.conn)
            if self._stamp is not None and _stamp(self.conn, self.today) == self._stamp:
                STORE.put(self._stamp, key, value)
        self._local[key] = value
        return value

    # --- The parts the plan reads ---------------------------------------------------------

    def month_inputs(self, months: Sequence[str]) -> MonthInputs | None:
        if not months:
            return None
        return self.part(
            ("months", tuple(months)), lambda c: read_month_inputs(c, months, self.today)
        )

    def reads(
        self, months: Sequence[str], one_off_codes: frozenset[AllocationCategory]
    ) -> dict[str, MonthRead]:
        """services.health.read_months(conn, months, today, one_off_codes), its rules run on
        every call: the codes counted as one-off (phase 10 D307, the levers' switches) are
        applied by the rules here, never kept."""
        inputs = self.month_inputs(months)
        return {} if inputs is None else months_from_inputs(months, inputs, one_off_codes)

    def ended_reads(self, one_off_codes: frozenset[AllocationCategory]) -> dict[str, MonthRead]:
        from .health import ended_months

        return self.reads(ended_months(self.today), one_off_codes)
