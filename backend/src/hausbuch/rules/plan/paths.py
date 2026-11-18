"""The paths to the reserve target (docs/phases/phase-9.md §7.2, D246, D251). Fixture:
tests/fixtures/phase9/paths.yaml.

Rows: `reserve_first` (every plan paused), `pause:<plan>` for each of the four largest plans
(largest first, a tie in the plans' order), `as_is` (every plan continued); under the advice
mode `generic` only the first and the last. reserve(k) = reserve(k−1) + investable(k) − the
plans that continue, from the reserve at the end of the newest closed month. `reached`: the
first month with reserve ≥ target; `used_up`: the first month below 0. A reserve already at its
target gives every row reached = the newest closed month and months_needed 0. Rows sorted by
`reached`, the rows never reached last, ties in the order above; no row is recommended. The
plans beyond the four largest have no row: `others` names them (the screen's „weitere“).

`flat_change` (phase 9 step 3): the first month whose flat result differs from the first plan
month's, with each path's amount from then on, for the screen's „ab Januar 2028: … im Monat“.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from hausbuch.schemas.advice import AdviceMode
from hausbuch.schemas.plan import FlatChange, Path, PathLater, PlanMonth

from .surplus import ZERO

SHOWN_PAUSES = 4


class Plan(Protocol):
    @property
    def key(self) -> str: ...

    @property
    def monthly(self) -> Decimal: ...


@dataclass(frozen=True)
class PathRows:
    rows: list[Path]
    others: list[str] = field(default_factory=list)  # plan keys without a pause row


def path_defs(
    plans: Sequence[Plan], mode: AdviceMode
) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """The rows' keys with the plans each pauses, and the plans left without a row."""
    order = sorted(plans, key=lambda p: -p.monthly)  # stable: the plans' order on a tie
    shown = order[:SHOWN_PAUSES]
    defs = [("reserve_first", [p.key for p in order])]
    defs += [(f"pause:{p.key}", [p.key]) for p in shown]
    defs += [("as_is", [])]
    if mode is AdviceMode.GENERIC:
        defs = [defs[0], defs[-1]]
    return defs, [p.key for p in order[SHOWN_PAUSES:]]


def paths(
    start: Decimal,
    target: Decimal,
    plans: Sequence[Plan],
    months: Sequence[PlanMonth],
    newest_closed: str,
    mode: AdviceMode = AdviceMode.OWN_USE,
) -> PathRows:
    """Every path month by month over `months` (§7.1's, k = 1 … horizon); none recommended."""
    if not months:
        raise ValueError("a path runs over at least one month")
    defs, others = path_defs(plans, mode)
    rows = []
    for key, paused in defs:
        continuing = sum((p.monthly for p in plans if p.key not in paused), ZERO)
        value = start
        reached = (newest_closed, 0) if start >= target else None
        used = None
        for k, month in enumerate(months, start=1):
            value += month.investable - continuing
            if reached is None and value >= target:
                reached = (month.month, k)
            if used is None and value < 0:
                used = month.month
        rows.append(
            Path(
                key=key,
                paused=paused,
                to_reserve_first_month=months[0].investable - continuing,
                reached=reached[0] if reached else None,
                months_needed=reached[1] if reached else None,
                used_up=used,
                reserve_at_horizon=value,
            )
        )
    rows.sort(key=lambda r: (r.reached is None, r.reached or ""))
    return PathRows(rows=rows, others=others)


def flat_change(
    plans: Sequence[Plan],
    months: Sequence[PlanMonth],
    mean: Decimal,
    mode: AdviceMode = AdviceMode.OWN_USE,
) -> FlatChange | None:
    """The first month k > 1 whose flat result differs from month 1's, over `months` as §7.1
    gives them without one-offs (so a one-off month is never taken for the change), with each
    path's investable(k) less the plans that continue on it; None when the flat's result
    never changes over the horizon."""
    if not months:
        return None
    before = months[0].flat
    change = next((m for m in months[1:] if m.flat != before), None)
    if change is None:
        return None
    defs, _ = path_defs(plans, mode)
    return FlatChange(
        month=change.month,
        flat=change.flat,
        before=before,
        before_is_mean=before == mean,
        paths=[
            PathLater(
                key=key,
                to_reserve=change.investable
                - sum((p.monthly for p in plans if p.key not in paused), ZERO),
            )
            for key, paused in defs
        ],
    )
