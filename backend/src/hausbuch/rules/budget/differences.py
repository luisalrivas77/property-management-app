"""„Was diesen Monat anders ist“ on Haushalt (docs/phases/phase-10.md §7.6 Haushalt (3), build
decision (a)). Pure: no I/O, nothing stored. Outside rules/health/ and rules/plan/, so
services.health.rule_version() does not change with it.

`differences(month, now, before)` compares the newest closed month's spending per group
(`now`, phase 6's `spending_by_group` as the Haushalt table shows it, one-off codes included)
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

from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal

from hausbuch.rules.household.taxonomy import TAXONOMY
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.health import DifferenceWords, GroupDifference
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
