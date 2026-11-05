"""The rent in force on a date (tests/fixtures/phase1/rent_at.yaml).

Rent rows are append-only; a wrong row is superseded by a correction row, never edited (D12).
"""

from collections.abc import Sequence
from datetime import date

from hausbuch.schemas.tenancy import RentScheduleEntry


def rent_at(rows: Sequence[RentScheduleEntry], at: date) -> RentScheduleEntry | None:
    """The row of one tenancy's schedule that applies on `at`, or None before the first row.

    Superseded rows are dropped. Of the rest, the latest effective_from <= at wins; on a tie,
    the latest recorded_at (then the id, so the result never depends on input order).
    Tenancy bounds are not checked here (validation.check_rent_row).
    """
    superseded = {r.supersedes_id for r in rows if r.supersedes_id is not None}
    candidates = [r for r in rows if r.id not in superseded and r.effective_from <= at]
    if not candidates:
        return None
    return max(candidates, key=lambda r: (r.effective_from, r.recorded_at, r.id))
