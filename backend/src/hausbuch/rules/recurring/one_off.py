"""The answers on single payments (docs/phases/phase-10.md §7.9 (c), D297; §7.12 (b), D307).

Pure: no I/O. A person marks a payment one-off („Als einmalig markieren“) or regular again
(„Doch normal zählen“) from its detail panel; the newest answer per allocation counts
(`newest_allocation_marks`); a re-booked payment gets a new allocation. What a `one_off`
answer changes is in rules/health/month, rules/plan/spending and rules/budget (§7.9 (c) 2).
The search for payments that may be one-off (D297's candidates, the card „Vielleicht
einmalig?“) went with D307: the one-offs are categories now (rules.household.taxonomy).
"""

from collections.abc import Iterable, Mapping
from uuid import UUID

from hausbuch.schemas.recurring import SpendingMarkAnswer


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
