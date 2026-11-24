"""A single payment that may be one-off (docs/phases/phase-10.md §7.9 (c) 1, D297). Fixture:
tests/fixtures/phase10/one_off_marks.yaml.

Pure: no I/O. A candidate is a confirmed private spending line booked in the window's closed
months (at least three, D231) that

- is in a code not already one-off (D236),
- is not a payment of a recurring series in any state (rules.recurring.series.find),
- has no answer yet (any stored mark on its allocation, `one_off` or `regular`),
- is at least 250 € and at least 3 × its code's median month: the code's spending in each of
  the window's months (a month without the code counting 0; a marked payment still counts
  in its month), the median of those months.

The app never marks a payment itself: a person answers „Ja, einmalig“ or „Nein, normal“, and
the newest answer per allocation counts (`newest_allocation_marks`); a re-booked payment gets
a new allocation and is asked again. What a `one_off` answer changes is in rules/health/month,
rules/plan/spending and rules/budget (§7.9 (c) 2).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from statistics import median
from uuid import UUID

from hausbuch.rules.budget.budget import is_spending
from hausbuch.rules.household.taxonomy import ONE_OFF
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.recurring import SpendingMarkAnswer

from .series import SpendingLine, counted, find

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
MIN_AMOUNT = Decimal("250.00")
MIN_MULTIPLE = 3
MIN_MONTHS = 3


@dataclass(frozen=True)
class Candidate:
    line: SpendingLine
    code_median: Decimal


def newest_allocation_marks(
    marks: Iterable[tuple[UUID, SpendingMarkAnswer]],
) -> dict[UUID, SpendingMarkAnswer]:
    """The newest answer per allocation, from the answers in the order stored."""
    out: dict[UUID, SpendingMarkAnswer] = {}
    for allocation_id, answer in marks:
        out[allocation_id] = answer
    return out


def one_off_ids(answers: Mapping[UUID, SpendingMarkAnswer]) -> frozenset[UUID]:
    """The allocations whose newest answer is `one_off`."""
    return frozenset(i for i, a in answers.items() if a is SpendingMarkAnswer.ONE_OFF)


def _month(line: SpendingLine) -> str:
    return f"{line.booked_on.year:04d}-{line.booked_on.month:02d}"


def code_medians(
    lines: Iterable[SpendingLine], months: Sequence[str]
) -> dict[AllocationCategory, Decimal]:
    """Each spending code's median month over `months` (what was spent, a refund of the code
    lowering it), a month without the code counting 0."""
    window = set(months)
    by_code: dict[AllocationCategory, dict[str, Decimal]] = {}
    for line in lines:
        month = _month(line)
        if month in window and is_spending(line.category):
            per = by_code.setdefault(line.category, {})
            per[month] = per.get(month, ZERO) - line.amount
    return {
        code: Decimal(median([per.get(m, ZERO) for m in months])).quantize(CENT)
        for code, per in by_code.items()
    }


def candidates(
    lines: Sequence[SpendingLine],
    months: Sequence[str],
    answered: Iterable[UUID],
    *,
    today: date,
) -> list[Candidate]:
    """The candidates among `lines` (see the module), oldest booking first. `lines`: every
    confirmed private line up to `today` (a series is found over all of them); `months`: the
    window's closed months; `answered`: every allocation with a stored answer."""
    if len(months) < MIN_MONTHS:
        return []
    in_series = {li.allocation_id for found in find(lines, today) for li in found.lines}
    asked = set(answered)
    medians = code_medians(lines, months)
    window = set(months)
    out = []
    for line in sorted(lines, key=lambda li: (li.booked_on, str(li.allocation_id))):
        if (
            _month(line) not in window
            or not counted(line)
            or line.category in ONE_OFF
            or line.allocation_id in in_series
            or line.allocation_id in asked
        ):
            continue
        amount = -line.amount
        middle = medians.get(line.category, ZERO)
        if amount >= MIN_AMOUNT and amount >= MIN_MULTIPLE * middle:
            out.append(Candidate(line=line, code_median=middle))
    return out
