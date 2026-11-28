"""„Was diesen Monat anders ist“ on Haushalt (docs/phases/phase-10.md §7.6 Haushalt (3), build
decision (a)). Pure: no I/O, nothing stored. Outside rules/health/ and rules/plan/, so
services.health.rule_version() does not change with it.

`differences(month, now, before)` compares the newest closed month's spending per group
(`now`, the month's regular spending per group by `compared_by_group`: one-off codes and
refunds left out)
with the mean of the same groups over the closed months before it (`before`, oldest first):

- **Fewer than three earlier months** (MIN_BASELINE): None, the screen shows nothing.
- **Mean** per group: Σ over the earlier months ÷ their number, a month without the group
  counting 0, to the cent half-up; the difference is the month less that shown mean.
- **Named** only when |difference| ≥ 50 € (MIN_AMOUNT) *and* |difference| ≥ 25 % of |mean|
  (MIN_SHARE), both inclusive; a group with a mean of 0 (new this month) needs the 50 € only.
- **At most three** (MAX_NAMED), the largest |difference| first, a tie in the groups' order.

`spending_group(category)` places one private line's code (after D234's recode) in the group
month_cashflow sums it under, None for income, the Geldanlage group and a transfer, so the
service can list the payments behind a named group.
"""

from collections.abc import Collection, Iterable, Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from hausbuch.rules.health.month import cashflow_rows
from hausbuch.rules.household.taxonomy import ONE_OFF, TAXONOMY
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.health import (
    CategoryDifference,
    DifferenceWords,
    GroupDifference,
    MonthLine,
)
from hausbuch.schemas.household import HouseholdGroup, Relevance

MIN_BASELINE = 3
MAX_BASELINE = 12
MIN_AMOUNT = Decimal("50.00")
MIN_SHARE = Decimal("0.25")
MAX_NAMED = 3

_CENT = Decimal("0.01")
_ZERO = Decimal("0.00")
_ORDER = {group: index for index, group in enumerate(HouseholdGroup)}


def spending_group(category: AllocationCategory) -> HouseholdGroup | None:
    """The group a line of `category` counts under in month_cashflow's spending, else None."""
    code = TAXONOMY.get(category)
    if code is None:  # bank_fees, uncategorized
        return HouseholdGroup.OTHER
    if code.group is HouseholdGroup.INVESTMENT:
        return None
    if code.relevance in (Relevance.SPENDING, Relevance.TAX):
        return code.group
    return None


def compared_group(
    category: AllocationCategory, one_off_codes: Collection[AllocationCategory] = ONE_OFF
) -> HouseholdGroup | None:
    """The group a line of `category` is compared under, else None: `spending_group` less the
    one-off codes in force (D236, phase 10 D307; a large one-off purchase would make every
    later month read as „weniger als üblich“) and refunds (they lower spending, nothing the
    household spends on), so the comparison is of the regular month."""
    if category in one_off_codes:
        return None
    group = spending_group(category)
    return None if group is HouseholdGroup.REFUND else group


def compared_by_group(
    rows: Iterable[tuple[AllocationCategory, Decimal]],
    one_off_codes: Collection[AllocationCategory] = ONE_OFF,
) -> dict[HouseholdGroup, Decimal]:
    """One month's regular spending per compared group from month_cashflow's rows (an
    outflow is negative there, so spending is its negation)."""
    out: dict[HouseholdGroup, Decimal] = {}
    for category, amount in rows:
        group = compared_group(category, one_off_codes)
        if group is not None:
            out[group] = out.get(group, _ZERO) - amount
    return out


def compared_rows(
    lines: Iterable[MonthLine], one_off_ids: Collection[UUID] = frozenset()
) -> list[tuple[AllocationCategory, Decimal]]:
    """month_cashflow's rows of one month's private lines (D234 applied), less the payments a
    person marked one-off (phase 10 §7.9 (c) 2, D297): like a one-off code's, they are no
    regular spending to compare."""
    return [
        row
        for line in lines
        if not (line.allocation_id in one_off_ids and line.amount < 0)
        for row in cashflow_rows([line])
    ]


def differences(
    now: Mapping[HouseholdGroup, Decimal],
    before: Sequence[Mapping[HouseholdGroup, Decimal]],
) -> list[GroupDifference] | None:
    """The groups of `now` that differ from their mean over `before` (see the module)."""
    if len(before) < MIN_BASELINE:
        return None
    count = Decimal(len(before))
    groups = set(now).union(*before)
    named: list[GroupDifference] = []
    for group in groups:
        total = sum((month.get(group, _ZERO) for month in before), _ZERO)
        mean = (total / count).quantize(_CENT, ROUND_HALF_UP)
        amount = now.get(group, _ZERO)
        difference = amount - mean
        size = abs(difference)
        if size < MIN_AMOUNT or size < MIN_SHARE * abs(mean):
            continue
        named.append(
            GroupDifference(
                group=group,
                amount=amount,
                mean=mean,
                difference=difference,
                words=DifferenceWords.MORE if difference > 0 else DifferenceWords.LESS,
            )
        )
    named.sort(key=lambda d: (-abs(d.difference), _ORDER[d.group]))
    return named[:MAX_NAMED]


# --- Per category (phase 10 §7.13 (b) 2, D314) ---------------------------------------------

_CATEGORY_ORDER = {category: index for index, category in enumerate(AllocationCategory)}


def compared_by_category(
    rows: Iterable[tuple[AllocationCategory, Decimal]],
    one_off_codes: Collection[AllocationCategory] = ONE_OFF,
) -> dict[AllocationCategory, Decimal]:
    """One month's regular spending per compared code (compared_group's codes: one-off codes
    and refunds left out), from month_cashflow's rows (an outflow is negative there)."""
    out: dict[AllocationCategory, Decimal] = {}
    for category, amount in rows:
        if compared_group(category, one_off_codes) is not None:
            out[category] = out.get(category, _ZERO) - amount
    return out


def category_differences(
    now: Mapping[AllocationCategory, Decimal],
    before: Sequence[Mapping[AllocationCategory, Decimal]],
    *,
    final: bool,
) -> list[CategoryDifference] | None:
    """„Was diesen Monat anders ist“ per category for any chosen month (D314): each code of
    `now` (one month's regular spending by compared_by_category) whose month differs from its
    mean over `before` (the closed months before it, oldest first), with the thresholds of
    `differences` (the mean to the cent, |difference| ≥ 50 € and ≥ 25 % of |mean|, a mean of 0
    needing the 50 € only). None below three earlier months. Every code that crosses them is
    named, the largest |difference| first, a tie in the groups' then the codes' order; the
    screen folds the list. While the month is not `final` (not closed: it runs, or lines still
    wait) a code is named only when it is already above its mean: a month half read is lower
    than usual by being half read, so „weniger als üblich“ would say nothing."""
    if len(before) < MIN_BASELINE:
        return None
    count = Decimal(len(before))
    named: list[CategoryDifference] = []
    for category in set(now).union(*before):
        group = spending_group(category)
        if group is None:  # compared_by_category's rows hold spending codes only
            continue
        total = sum((month.get(category, _ZERO) for month in before), _ZERO)
        mean = (total / count).quantize(_CENT, ROUND_HALF_UP)
        amount = now.get(category, _ZERO)
        difference = amount - mean
        size = abs(difference)
        if size < MIN_AMOUNT or size < MIN_SHARE * abs(mean):
            continue
        if not final and difference < 0:
            continue
        named.append(
            CategoryDifference(
                category=category,
                group=group,
                amount=amount,
                mean=mean,
                difference=difference,
                words=DifferenceWords.MORE if difference > 0 else DifferenceWords.LESS,
            )
        )
    named.sort(key=lambda d: (-abs(d.difference), _ORDER[d.group], _CATEGORY_ORDER[d.category]))
    return named
