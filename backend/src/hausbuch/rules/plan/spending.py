"""Spending in play and the plan beside the real month (docs/phases/phase-9.md §7.6, §7.7,
D263). Fixtures: tests/fixtures/phase9/spending_play.yaml, spending_comparison.yaml.

- **Codes.** The codes counted in a month's regular spending are those the Haushalt screen
  counts as spending (phase 6's `month_cashflow`: spending and tax relevance, and a code
  outside the taxonomy such as bank fees or an uncategorised line under „Sonstiges“), less
  the one-off codes. A change may name a counted code of the taxonomy except a refund (its
  mean is negative, a member does not plan refunds) and a one-off code (§7.6 (1)). A group's
  fallback code stands for the whole group, bank fees and uncategorised lines included for
  „Sonstiges“; any other code for itself.
- **Means** (§7.6 (2)). Each code's window sum ÷ n to the cent, the cents apportioned by
  largest remainder (ties: the code's value, ascending), so the means sum to
  `mean_regular_spending` exactly; a group code's mean is Σ over its codes.
- **Δ and the month function** (§7.6 (3)). Δ(k) = Σ (planned − mean) over the changes in
  force in month k; rules.plan.levers.month_at subtracts it from free(k).
- **The rate with the changes** (§7.6 (4)) on the window's exact sums:
  (ΣE + n·inc(k) − ΣA − n·Δ(k)) ÷ (ΣE + n·inc(k)), four places half-up, so with no change in
  force it is §5.1's `rate_regular` to the last digit.
- **The comparison** (§7.7). The default scenario in force on a Berlin day, the planned
  amount from the change's `mean_at_save`, the month's confirmed lines on the code.

Pure: no I/O.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from hausbuch.rules.household.taxonomy import FALLBACK, FALLBACK_CODES, ONE_OFF, TAXONOMY
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.health import HouseholdMonth, MonthLine, ratio
from hausbuch.schemas.household import HouseholdCashflow, HouseholdGroup, Relevance
from hausbuch.schemas.plan import (
    ComparisonWords,
    IncomeChange,
    PlayedChange,
    RateSegment,
    SpendingChange,
    SpendingComparison,
    SpendingComparisonRow,
    SpendingPlay,
    SpendingPlayGroup,
    SpendingPlayRow,
)

from .surplus import ZERO, cent, mean_cent

BERLIN = ZoneInfo("Europe/Berlin")
_SPENT = (Relevance.SPENDING, Relevance.TAX)
_NEVER_PLAYED = frozenset({AllocationCategory.HH_REFUND})
_ALL = tuple(AllocationCategory)


# --- Codes (§7.6 (1)) ------------------------------------------------------------------------


def group_of(code: AllocationCategory) -> HouseholdGroup:
    """The group the Haushalt screen shows a code in; a code outside the taxonomy (bank fees,
    an uncategorised line) under „Sonstiges“, as month_cashflow books it."""
    row = TAXONOMY.get(code)
    return HouseholdGroup.OTHER if row is None else row.group


def counted(code: AllocationCategory) -> bool:
    """Counted in a month's regular spending (rules.health.month._role's spending, less the
    one-off codes)."""
    row = TAXONOMY.get(code)
    if row is None:
        return True
    return row.relevance in _SPENT and not row.one_off


def playable(code: AllocationCategory) -> bool:
    """A change may name the code (§7.6 (1))."""
    return code in TAXONOMY and counted(code) and code not in _NEVER_PLAYED


def members(code: AllocationCategory) -> frozenset[AllocationCategory]:
    """The codes a change of `code` covers: a group's fallback code the whole group (every
    counted code shown in it), any other code itself."""
    return _members(code)


_MEMBERS: dict[AllocationCategory, frozenset[AllocationCategory]] = {}


def _members(code: AllocationCategory) -> frozenset[AllocationCategory]:
    if code not in _MEMBERS:
        if code in FALLBACK_CODES:
            group = TAXONOMY[code].group
            _MEMBERS[code] = frozenset(c for c in _ALL if counted(c) and group_of(c) is group)
        else:
            _MEMBERS[code] = frozenset({code})
    return _MEMBERS[code]


def group_code(group: HouseholdGroup) -> AllocationCategory | None:
    """The code that stands for a whole group on its row: its fallback, else its only code
    when that is playable; None otherwise (Steuerlich relevant, Bewegungen, Depot, Erstattung)."""
    if group in FALLBACK:
        return FALLBACK[group]
    codes = [c for c, row in TAXONOMY.items() if row.group is group]
    if len(codes) == 1 and playable(codes[0]):
        return codes[0]
    return None


def refused_code(code: AllocationCategory) -> str | None:
    """Why a change may not name `code` (§7.6 (1)); None when it may."""
    if code in ONE_OFF:
        return "is a one-off code"
    if code not in TAXONOMY:
        return "is not a household spending code"
    if not playable(code):
        return "is not a household spending code that can be planned"
    return None


def refused_together(changes: Sequence[SpendingChange]) -> str | None:
    """§7.6 (1) across the changes of one scenario: a group's fallback code beside one of its
    subcategories; two changes of one code whose months overlap."""
    for i, a in enumerate(changes):
        for b in changes[i + 1 :]:
            if a.code == b.code:
                a_end, b_end = a.to_month or "9999-12", b.to_month or "9999-12"
                if a.from_month <= b_end and b.from_month <= a_end:
                    return f"two changes of {a.code.value} overlap"
                continue
            for x, y in ((a, b), (b, a)):
                if x.code in FALLBACK_CODES and y.code in members(x.code):
                    return f"{x.code.value} stands for its group beside {y.code.value}"
    return None


# --- Means and Δ (§7.6 (2), (3)) -------------------------------------------------------------


def month_spending(cashflow: HouseholdCashflow) -> dict[AllocationCategory, Decimal]:
    """A month's regular spending per code: the Haushalt screen's spending by category, less
    the one-off codes. Σ = HouseholdMonth.regular_spending of the same month."""
    out: dict[AllocationCategory, Decimal] = {}
    for codes in cashflow.spending_by_category.values():
        for code, value in codes.items():
            if code not in ONE_OFF:
                out[code] = out.get(code, ZERO) + value
    return out


def code_means(
    window: Sequence[Mapping[AllocationCategory, Decimal]],
) -> dict[AllocationCategory, Decimal]:
    """Each code's mean over the window's months, to the cent, apportioned by largest
    remainder so that Σ of the means = the mean of the months' totals to the cent."""
    if not window:
        return {}
    n = len(window)
    sums: dict[AllocationCategory, Fraction] = {}
    for month in window:
        for code, amount in month.items():
            sums[code] = sums.get(code, Fraction(0)) + Fraction(amount)
    exact = {c: v * 100 / n for c, v in sums.items()}
    target = int(mean_cent([sum(m.values(), ZERO) for m in window]) * 100)
    floors = {c: v.numerator // v.denominator for c, v in exact.items()}
    left = target - sum(floors.values())
    order = sorted(exact, key=lambda c: (-(exact[c] - floors[c]), c.value))
    for c in order[:left]:
        floors[c] += 1
    return {
        c: (Decimal(v) / 100).quantize(ZERO)
        for c, v in sorted(floors.items(), key=lambda kv: kv[0].value)
    }


def mean_of(code: AllocationCategory, means: Mapping[AllocationCategory, Decimal]) -> Decimal:
    """A code's mean; a group code's the Σ over its codes."""
    return sum((means.get(c, ZERO) for c in members(code)), ZERO)


def planned(change: SpendingChange, mean: Decimal) -> Decimal:
    """§7.6 (3): `monthly`, or the mean × (1 + percent) to the cent half-up."""
    if change.monthly is not None:
        return change.monthly
    assert change.percent is not None
    return cent(mean * (1 + change.percent))


def delta(
    changes: Iterable[SpendingChange], means: Mapping[AllocationCategory, Decimal], month: str
) -> Decimal:
    """Δ(k): Σ (planned − mean) over the changes in force in `month`, signed."""
    total = ZERO
    for change in changes:
        if change.in_force(month):
            mean = mean_of(change.code, means)
            total += planned(change, mean) - mean
    return total


def percent_on_nothing(
    changes: Iterable[SpendingChange], means: Mapping[AllocationCategory, Decimal]
) -> AllocationCategory | None:
    """§7.6 (1): the first change with a percent on a code with no measured spending."""
    for change in changes:
        if change.percent is not None and mean_of(change.code, means) == 0:
            return change.code
    return None


def with_means_at_save(
    changes: Iterable[SpendingChange], means: Mapping[AllocationCategory, Decimal]
) -> list[SpendingChange]:
    """The changes as stored (§7.6 (1)): each with its code's mean now as `mean_at_save`,
    whatever was typed."""
    return [c.model_copy(update={"mean_at_save": mean_of(c.code, means)}) for c in changes]


# --- The rate and the play (§7.6 (4), (5)) ---------------------------------------------------


def _add_months(month: str, k: int) -> str:
    index = int(month[:4]) * 12 + int(month[5:]) - 1 + k
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _income_change(income_changes: Iterable[IncomeChange], month: str) -> Decimal:
    return sum(
        (
            c.monthly_net
            for c in income_changes
            if c.from_month <= month and (c.to_month is None or month <= c.to_month)
        ),
        ZERO,
    )


def rate_segments(
    window: Sequence[HouseholdMonth],
    changes: Sequence[SpendingChange],
    means: Mapping[AllocationCategory, Decimal],
    income_changes: Sequence[IncomeChange],
    horizon_months: int,
) -> list[RateSegment]:
    """The rate with the changes for each future month 1 … horizon, in stretches that share
    it (§7.6 (4))."""
    n = len(window)
    sum_e = sum((m.income for m in window), ZERO)
    sum_a = sum((m.regular_spending for m in window), ZERO)
    newest = window[-1].month
    out: list[RateSegment] = []
    for k in range(1, horizon_months + 1):
        month = _add_months(newest, k)
        e_k = sum_e + n * _income_change(income_changes, month)
        rate = ratio(e_k - sum_a - n * delta(changes, means, month), e_k)
        if out and out[-1].rate == rate:
            out[-1] = out[-1].model_copy(update={"to_month": month})
        else:
            out.append(RateSegment(from_month=month, to_month=month, rate=rate))
    return out


def _played(change: SpendingChange, means: Mapping[AllocationCategory, Decimal]) -> PlayedChange:
    mean = mean_of(change.code, means)
    amount = planned(change, mean)
    return PlayedChange(change=change, planned=amount, delta=amount - mean)


def play(
    window: Sequence[HouseholdMonth],
    spending: Sequence[Mapping[AllocationCategory, Decimal]],
    changes: Sequence[SpendingChange],
    income_changes: Sequence[IncomeChange],
    horizon_months: int,
) -> SpendingPlay:
    """§7.6 (5) over the window's household months and their spending per code
    (`month_spending`, one mapping per month, in the same order)."""
    if not window or len(window) != len(spending):
        raise ValueError("the play is taken over the window's months, one spending per month")
    for m, by_code in zip(window, spending, strict=True):
        if sum(by_code.values(), ZERO) != m.regular_spending:
            raise ValueError(f"{m.month}: the codes do not sum to the regular spending")
    means = code_means(spending)
    mean_regular = mean_cent([m.regular_spending for m in window])
    if sum(means.values(), ZERO) != mean_regular:  # §7.6 (2), asserted
        raise ValueError("the codes' means do not sum to the mean regular spending")
    first = _add_months(window[-1].month, 1)

    # A row per code with a mean or a change of its own; a change of the code that stands for
    # its group sits on the group's row and opens the group without a row of its own.
    by_group: dict[HouseholdGroup, set[AllocationCategory]] = {}
    for code in set(means) | {c.code for c in changes}:
        rows_of = by_group.setdefault(group_of(code), set())
        if code in means or code != group_code(group_of(code)):
            rows_of.add(code)
    groups: list[SpendingPlayGroup] = []
    for group, group_codes in by_group.items():
        stands = group_code(group)
        rows = []
        for code in sorted(group_codes, key=lambda c: (-means.get(c, ZERO), c.value)):
            own = code != stands
            played = [_played(c, means) for c in changes if c.code == code] if own else []
            change_first = sum((p.delta for p in played if p.change.in_force(first)), ZERO)
            mean = means.get(code, ZERO)
            rows.append(
                SpendingPlayRow(
                    code=code,
                    mean=mean,
                    planned_first=mean + change_first,
                    change_first=change_first,
                    playable=own and playable(code),
                    changes=played,
                )
            )
        group_played = (
            [_played(c, means) for c in changes if c.code == stands] if stands is not None else []
        )
        mean = sum((r.mean for r in rows), ZERO)
        change_first = sum((r.change_first for r in rows), ZERO) + sum(
            (p.delta for p in group_played if p.change.in_force(first)), ZERO
        )
        groups.append(
            SpendingPlayGroup(
                group=group,
                code=stands,
                mean=mean,
                planned_first=mean + change_first,
                change_first=change_first,
                changes=group_played,
                rows=rows,
            )
        )
    groups.sort(key=lambda g: (-g.mean, g.group.value))
    sum_e = sum((m.income for m in window), ZERO)
    sum_a = sum((m.regular_spending for m in window), ZERO)
    return SpendingPlay(
        groups=groups,
        mean_regular_spending=mean_regular,
        rate_regular_now=ratio(sum_e - sum_a, sum_e),
        rates=rate_segments(window, changes, means, income_changes, horizon_months),
    )


# --- The comparison (§7.7) -------------------------------------------------------------------


def words(difference: Decimal) -> tuple[ComparisonWords, str]:
    """„wie geplant“ under 1 € either way, else the difference in whole euros half-up with a
    thousands point, „mehr als geplant“ or „weniger als geplant“."""
    if abs(difference) < 1:
        return ComparisonWords.AS_PLANNED, "wie geplant"
    whole = int(abs(difference).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    amount = f"{whole:,}".replace(",", ".")
    if difference > 0:
        return ComparisonWords.MORE, f"{amount} € mehr als geplant"
    return ComparisonWords.LESS, f"{amount} € weniger als geplant"


def counted_as(line: MonthLine) -> AllocationCategory:
    """D234: a privately paid purchase cost counts as hh_investment_property, never spending."""
    return AllocationCategory.HH_INVESTMENT_PROPERTY if line.purchase_cost else line.category


def compare_rows(
    month: str, changes: Sequence[SpendingChange], lines: Sequence[MonthLine]
) -> list[SpendingComparisonRow]:
    """One row per change in force in `month`, in the scenario's order; `lines` are the
    month's confirmed private lines (signed as booked, spending negative)."""
    rows = []
    for change in changes:
        if not change.in_force(month):
            continue
        if change.mean_at_save is None:
            raise ValueError(f"{change.code.value}: a stored change carries its mean_at_save")
        amount = planned(change, change.mean_at_save)
        covered = members(change.code) - ONE_OFF
        mine = [li for li in lines if counted_as(li) in covered]
        actual = -sum((li.amount for li in mine), ZERO)
        actual = actual + ZERO  # never „-0.00“
        difference = actual - amount
        said, text = words(difference)
        rows.append(
            SpendingComparisonRow(
                code=change.code,
                group=group_of(change.code),
                planned=amount,
                actual=actual,
                difference=difference,
                words=said,
                text=text,
                sources=[li.allocation_id for li in mine],
            )
        )
    return rows


class ScenarioRow(Protocol):
    """What the rule reads of a household_scenario row."""

    @property
    def id(self) -> UUID: ...
    @property
    def created_at(self) -> datetime: ...  # aware
    @property
    def is_default(self) -> bool: ...
    @property
    def withdrawn(self) -> bool: ...
    @property
    def supersedes_id(self) -> UUID | None: ...


@dataclass(frozen=True)
class AsOf:
    """The day a comparison reads its scenario on: the month's last day, or today."""

    day: date
    running: bool


def as_of(month: str, today: date) -> AsOf:
    """§7.7: the month's last day; today for the running month (today inside it)."""
    year, mon = int(month[:4]), int(month[5:])
    nxt = date(year + mon // 12, mon % 12 + 1, 1)
    last = date.fromordinal(nxt.toordinal() - 1)
    running = date(year, mon, 1) <= today <= last
    return AsOf(day=today if running else last, running=running)


def in_force_on[R: ScenarioRow](rows: Iterable[R], day: date) -> R | None:
    """The default household scenario in force on `day` (Berlin calendar): created on or
    before it, not withdrawn, not superseded by a row created on or before it; the newest
    first (then the id, as services.household_scenarios orders the default)."""
    known = [r for r in rows if r.created_at.astimezone(BERLIN).date() <= day]
    replaced = {r.supersedes_id for r in known if r.supersedes_id is not None}
    live = [r for r in known if r.is_default and not r.withdrawn and r.id not in replaced]
    live.sort(key=lambda r: (r.created_at, str(r.id)), reverse=True)
    return live[0] if live else None


def comparison(
    month: str,
    today: date,
    *,
    closed: bool,
    open_lines: int,
    scenario: ScenarioRow | None,
    changes: Sequence[SpendingChange],
    lines: Sequence[MonthLine],
) -> SpendingComparison:
    """§7.7 for `month`: final only for a closed month that has ended."""
    when = as_of(month, today)
    return SpendingComparison(
        month=month,
        final=closed and not when.running and when.day < today,
        open_lines=open_lines,
        scenario_id=None if scenario is None else scenario.id,
        scenario_created_at=None if scenario is None else scenario.created_at,
        rows=compare_rows(month, changes, lines) if scenario is not None else [],
    )
