"""Phase 10 §7.5 (b): an inbox section that reads the clock, with the moment its answer
next changes, so GET /inbox can be kept until then (api/kept_answers.py).

A section lists a row while `recorded_at >= now - window`, so the row is still listed at
exactly `recorded_at + window` and gone one microsecond later (the resolution of Python's
datetime and of Postgres' timestamptz): that later moment is when the answer may change.
"""

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from hausbuch.schemas.api import InboxItem

RESOLUTION = timedelta(microseconds=1)


class TimedItems(NamedTuple):
    items: list[InboxItem]
    # The first moment the section's answer may differ with no write in between; None when
    # time alone never changes it.
    changes_at: datetime | None


def leaves_window(recorded_at: datetime, window: timedelta) -> datetime:
    """The first moment a row listed while `recorded_at >= now - window` is no longer listed.
    In UTC: added to a Berlin-zoned time, a window of days would be wall-clock arithmetic,
    an hour off across a clock change."""
    return recorded_at.astimezone(UTC) + window + RESOLUTION


def earliest(moments: Iterable[datetime | None]) -> datetime | None:
    found = [moment for moment in moments if moment is not None]
    return min(found) if found else None
