"""D109: a child's months in a year, and the Kindergeld and Freibeträge they give.

`months` = the calendar months from max(birth month, January) to December (§66 Abs. 2:
Kindergeld from the birth month; §32 Abs. 6 S. 5: a twelfth of the Freibeträge per month), up to
the month of the 18th birthday; a child who turns 18 in or before the year raises
`return.child_over_18` (a child over 18 is not modelled), the birth year raises
`return.kindergeld_apply`. `kindergeld = 259 × months`; `allowances = cents((3,414 + 1,464) × 2
× months ÷ 12)` (doubled for spouses filing jointly). `kindergeld_from` is informational: the
entitlement runs from the birth month whatever month the payments began.
"""

from dataclasses import dataclass
from decimal import Decimal

from hausbuch.rules.tax._common import warning
from hausbuch.schemas.performance import ChildFact, ChildFigures
from hausbuch.schemas.tax import DraftWarning, WarningCode

from ._base import cents

ADULT_AGE = 18


def child_months(child: ChildFact, tax_year: int) -> tuple[int, list[DraftWarning]]:
    born = child.born_on
    first = 1 if born.year < tax_year else born.month if born.year == tax_year else 13
    adult = born.year + ADULT_AGE
    last = 12 if adult > tax_year else born.month if adult == tax_year else 0
    warnings = []
    if adult <= tax_year:
        warnings.append(warning(WarningCode.RETURN_CHILD_OVER_18, born_year=str(born.year)))
    if born.year == tax_year:
        warnings.append(warning(WarningCode.RETURN_KINDERGELD_APPLY))
    return max(0, last - first + 1), warnings


@dataclass
class ChildMonths:
    months: int
    kindergeld: Decimal
    allowances: Decimal
    warnings: list[DraftWarning]


def children_months(children: list[ChildFact], tax_year: int, figures: ChildFigures) -> ChildMonths:
    months, warnings = 0, []
    for child in children:
        n, w = child_months(child, tax_year)
        months += n
        warnings += w
    per_parent = figures.allowance_per_parent + figures.bea_per_parent
    return ChildMonths(
        months=months,
        kindergeld=figures.kindergeld_monthly * months,
        allowances=cents(per_parent * 2 * months / 12),
        warnings=warnings,
    )
