"""D109: a child's months in a year, and the Kindergeld and Freibeträge they give.

`months` = the calendar months from max(birth month, January) to December (§66 Abs. 2:
Kindergeld from the birth month; §32 Abs. 6 S. 5: a twelfth of the Freibeträge per month), up to
the month of the 18th birthday; a child who turns 18 in or before the year raises
`return.child_over_18` (a child over 18 is not modelled), the birth year raises
`return.kindergeld_apply` until `kindergeld_from` is typed. `kindergeld = 259 × months`;
`allowances = cents((3,414 + 1,464) × 2 × months ÷ 12)` (doubled for spouses filing jointly).
`kindergeld_from` does not move the months (the entitlement runs from the birth month whatever
month the payments began); typed, it ends the birth year's to-do (phase 10 §7.6, wave 2's open
points).
"""

from dataclasses import dataclass
from decimal import Decimal

from hausbuch.rules.tax._common import warning
from hausbuch.schemas.performance import ChildFact, ChildFigures
from hausbuch.schemas.tax import DraftWarning, WarningCode

from ._base import cents

ADULT_AGE = 18


def child_months(
    child: ChildFact, tax_year: int, *, index: int | None = None
) -> tuple[int, list[DraftWarning]]:
    """The child's months in `tax_year` and its warnings. `return.kindergeld_apply` stands in
    the birth year until `kindergeld_from` is typed (Kindergeld applied for); it names the
    child by its birth month and, with `index` (1-based, the household's list), its place, so
    twins are two to-dos (phase 10 §7.6, wave 2's open points)."""
    born = child.born_on
    first = 1 if born.year < tax_year else born.month if born.year == tax_year else 13
    adult = born.year + ADULT_AGE
    last = 12 if adult > tax_year else born.month if adult == tax_year else 0
    warnings = []
    if adult <= tax_year:
        warnings.append(warning(WarningCode.RETURN_CHILD_OVER_18, born_year=str(born.year)))
    if born.year == tax_year and child.kindergeld_from is None:
        params = {"born_month": born.strftime("%Y-%m")}
        if index is not None:
            params["child"] = str(index)
        warnings.append(warning(WarningCode.RETURN_KINDERGELD_APPLY, **params))
    return max(0, last - first + 1), warnings


@dataclass
class ChildMonths:
    months: int
    kindergeld: Decimal
    allowances: Decimal
    warnings: list[DraftWarning]


def children_months(children: list[ChildFact], tax_year: int, figures: ChildFigures) -> ChildMonths:
    months, warnings = 0, []
    for i, child in enumerate(children, start=1):
        n, w = child_months(child, tax_year, index=i)
        months += n
        warnings += w
    per_parent = figures.allowance_per_parent + figures.bea_per_parent
    return ChildMonths(
        months=months,
        kindergeld=figures.kindergeld_monthly * months,
        allowances=cents(per_parent * 2 * months / 12),
        warnings=warnings,
    )
