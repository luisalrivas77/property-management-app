"""Fixed and variable payments inside Notwendiges and Wünsche (docs/phases/phase-10.md
§7.10 (c), D300). Fixture: tests/fixtures/phase10/budget_fixed.yaml. Pure: no I/O.

`fixed_split(class_mean, line, series, window, ...)` splits one class's monthly figure (the
Budget line's `monthly`) into its fixed payments and the variable rest:

- **Fixed payments** are the recurring series of D296 the caller passes as listed (active or
  maybe ended; a series hidden as „kein Abo“ and one left out after three missed intervals
  are not passed) whose newest payment's code the Budget places in this class: the household's
  override for a movable code, else the taxonomy's class; a code one-off in force and
  `uncategorized` belong to neither class.
- **Expected monthly** (`monthly`): the series' yearly total ÷ 12, to the cent half-up (a
  quarterly or yearly payment as its monthly share).
- **Mean** (`mean`): the series' payments booked inside the window's months that the Budget
  counts in this class (their own code placed in it; a payment marked one-off, D297, left
  out, and with `window_ids` given only the window's own lines), summed and divided by the
  window's months, to the cent half-up. A series that started inside the window or changed
  its price has a mean apart from its monthly amount.
- **Variable** = the class mean − Σ the fixed means, so fixed + variable equals the class mean
  to the cent whatever each part's rounding; it may be negative (a refund in the class).
- Rows ordered by `monthly`, largest first, then label, then key.

Outside rules/health/ and rules/plan/, so services.health.rule_version() does not change with
it. Imports no rules.recurring (which imports rules.budget): it reads the series' schema only.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from hausbuch.rules.household.taxonomy import ONE_OFF
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.budget import BudgetFixedPayment, BudgetLineKey, BudgetView
from hausbuch.schemas.household import BudgetClass
from hausbuch.schemas.recurring import RecurringSeries

from .budget import class_of, is_spending, line_of

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


@dataclass(frozen=True, slots=True)
class FixedSplit:
    fixed: list[BudgetFixedPayment]
    variable: Decimal


def _half_up(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _placed(
    code: C, overrides: Mapping[C, BudgetClass], one_off_codes: Collection[C]
) -> BudgetLineKey | None:
    """The Budget line a code goes to, None for a code the Budget does not place."""
    if not is_spending(code):
        return None
    if (
        code not in one_off_codes
        and code is not C.UNCATEGORIZED
        and class_of(code, overrides, one_off_codes) is None
    ):
        return None
    return line_of(code, overrides, one_off_codes)


def series_line(
    series: RecurringSeries,
    overrides: Mapping[C, BudgetClass],
    one_off_codes: Collection[C] = ONE_OFF,
) -> BudgetLineKey | None:
    """The line of a series' newest payment's code, None without payments."""
    if not series.lines:
        return None
    newest = max(series.lines, key=lambda p: (p.booked_on, str(p.allocation_id)))
    return _placed(newest.category, overrides, one_off_codes)


def fixed_split(
    class_mean: Decimal,
    line: BudgetLineKey,
    series: Sequence[RecurringSeries],
    window: Sequence[str],
    overrides: Mapping[C, BudgetClass],
    *,
    one_off_codes: Collection[C] = ONE_OFF,
    one_off_ids: Collection[UUID] = frozenset(),
    window_ids: Collection[UUID] | None = None,
) -> FixedSplit:
    """One class's fixed payments and variable rest (see the module). `line` is NEED or
    WANT, `window` the Budget's closed months (YYYY-MM, at least one), `series` the listed
    ones with their payments (`lines`)."""
    if line not in (BudgetLineKey.NEED, BudgetLineKey.WANT):
        raise ValueError("only Notwendiges and Wünsche split into fixed and variable")
    if not window:
        raise ValueError("a window of at least one month")
    months = set(window)
    n = Decimal(len(window))
    rows: list[BudgetFixedPayment] = []
    for s in series:
        if not s.lines or series_line(s, overrides, one_off_codes) is not line:
            continue
        newest = max(s.lines, key=lambda p: (p.booked_on, str(p.allocation_id)))
        counted = sum(
            (
                p.amount
                for p in s.lines
                if p.booked_on.strftime("%Y-%m") in months
                and p.allocation_id not in one_off_ids
                and (window_ids is None or p.allocation_id in window_ids)
                and _placed(p.category, overrides, one_off_codes) is line
            ),
            ZERO,
        )
        rows.append(
            BudgetFixedPayment(
                key=s.sources[0] if s.sources else s.lines[0].allocation_id,
                label=s.label,
                category=newest.category,
                interval=s.interval,
                state=s.state,
                monthly=_half_up(s.yearly_total / 12),
                mean=_half_up(counted / n),
                change=s.change,
                since=s.since,
            )
        )
    rows.sort(key=lambda r: (-r.monthly, r.label, str(r.key)))
    return FixedSplit(fixed=rows, variable=class_mean - sum((r.mean for r in rows), ZERO))


def with_fixed(
    view: BudgetView,
    series: Sequence[RecurringSeries],
    overrides: Mapping[C, BudgetClass],
    *,
    one_off_codes: Collection[C] = ONE_OFF,
    one_off_ids: Collection[UUID] = frozenset(),
    window_ids: Collection[UUID] | None = None,
) -> BudgetView:
    """The Budget with Notwendiges and Wünsche split (`fixed`, `variable`), each against its
    line's `monthly`; unchanged below three closed months (no lines)."""
    if not view.lines or not view.window.months:
        return view
    lines = []
    for budget_line in view.lines:
        if budget_line.key in (BudgetLineKey.NEED, BudgetLineKey.WANT):
            split = fixed_split(
                budget_line.monthly,
                budget_line.key,
                series,
                view.window.months,
                overrides,
                one_off_codes=one_off_codes,
                one_off_ids=one_off_ids,
                window_ids=window_ids,
            )
            budget_line = budget_line.model_copy(
                update={
                    "fixed": split.fixed,
                    "variable": split.variable,
                    "fixed_monthly": budget_line.monthly - split.variable,
                }
            )
        lines.append(budget_line)
    return view.model_copy(update={"lines": lines})


__all__ = ["FixedSplit", "fixed_split", "series_line", "with_fixed"]
