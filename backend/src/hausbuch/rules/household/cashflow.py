"""The Haushalt screen's month (docs/phases/phase-6.md §13, mockup 10), pure over the
month's confirmed allocations of the private accounts.

Income is the sum of the income codes (a refund booked to one lowers it); spending is each
spending or tax code's amount, turned positive, under its taxonomy group (a refund lowers its
group); a transfer between own accounts or a depot trade is neither (D162). bank_fees and a
confirmed uncategorized line, the two codes both purposes share, count under „Sonstiges“.
saved comes from the transfer links (services.transfers.saved_in) plus what hh_investment
lines paid to an investment the app holds no account of (D159 amended 13 Nov 2026: saved, not
spent; a payout back lowers it), surplus is income less
spending, and the savings rate is surplus / income, (income - spending) / income as the
household design defines it (docs/design/household-finances.md, Sparquote; mockup 10), None for
a month without income."""

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal

from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.household import HouseholdCashflow, HouseholdGroup, Relevance

from .taxonomy import TAXONOMY

_SPENT = (Relevance.SPENDING, Relevance.TAX)
_RATE = Decimal("0.0001")


def month_cashflow(
    month: str,
    rows: Iterable[tuple[AllocationCategory, Decimal]],
    *,
    saved: Decimal,
    open_lines: int = 0,
) -> HouseholdCashflow:
    """The month's figures from its (category, signed amount) rows."""
    income = Decimal("0.00")
    by_income: dict[AllocationCategory, Decimal] = {}
    spending: dict[HouseholdGroup, Decimal] = {}
    invested = Decimal("0.00")
    for category, amount in rows:
        if category is AllocationCategory.HH_INVESTMENT:
            invested -= amount
            continue
        code = TAXONOMY.get(category)
        if code is None:  # bank_fees, uncategorized
            other = spending.get(HouseholdGroup.OTHER, Decimal("0.00"))
            spending[HouseholdGroup.OTHER] = other - amount
        elif code.relevance is Relevance.INCOME:
            income += amount
            by_income[category] = by_income.get(category, Decimal("0.00")) + amount
        elif code.relevance in _SPENT:
            spending[code.group] = spending.get(code.group, Decimal("0.00")) - amount
    spent = sum(spending.values(), Decimal("0.00"))
    rate = ((income - spent) / income).quantize(_RATE, ROUND_HALF_UP) if income > 0 else None
    return HouseholdCashflow(
        month=month,
        income=income,
        spending_by_group={g: v for g, v in spending.items() if v},
        saved=saved + invested,
        surplus=income - spent,
        savings_rate=rate,
        income_by_category={c: v for c, v in by_income.items() if v},
        open_lines=open_lines,
    )
