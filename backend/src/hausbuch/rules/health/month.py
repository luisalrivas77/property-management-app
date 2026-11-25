"""One household month (docs/phases/phase-9.md §4, D230–D236). Fixture:
tests/fixtures/phase9/household_month.yaml.

Three pure functions, used in this order by the service:

- `cashflow_rows(lines)`: the private lines as phase 6's `month_cashflow` rows, with D234's
  recode: a line flagged `purchase_cost` (a private payment whose document is the Beleg of a
  property's Nebenkosten row) is counted as `hh_investment_property`, whatever it was booked
  as. Idempotent: a line already booked so stays so. `services/household.cashflow` builds its
  rows with this function, so the Haushalt screen and this phase show the same month (D234).
- `rental_month(month, property_id, lines)`: one property's rental-account lines summed by
  phase 4's own category sets (D233), imported from rules/performance/year_figures.
- `household_month(private, rental, *, lines, link_sources)`: the private block (phase 6's
  `HouseholdCashflow`, unchanged) plus the rental blocks, counted once (D232).

A line counts in the calendar month of its booking date (D230); the caller selects the lines.
Amounts are exact sums; only the savings rate is rounded (four places, half-up).
"""

from collections.abc import Collection, Iterable, Sequence
from decimal import Decimal
from uuid import UUID

from hausbuch.rules.household.taxonomy import ONE_OFF, TAXONOMY
from hausbuch.rules.performance.year_figures import (
    INTEREST,
    INVESTED,
    OTHER_INCOME,
    PRINCIPAL,
    RENT,
    RUNNING,
    UMLAGEN_IN,
    UMLAGEN_OUT,
)
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.health import HouseholdMonth, MonthLine, RentalMonth, ratio
from hausbuch.schemas.household import HouseholdCashflow, HouseholdGroup, Relevance

ZERO = Decimal("0.00")

# RentalMonth's blocks: the categories and whether the block is an income (Σ amounts, a
# refund to a tenant lowers it) or a cost (−Σ amounts, as phase 4's _signed_in/_signed_out).
_BLOCKS: dict[str, tuple[frozenset[AllocationCategory], int]] = {
    "rent": (RENT, 1),
    "other_income": (OTHER_INCOME, 1),
    "umlagen_in": (UMLAGEN_IN, 1),
    "umlagen_out": (UMLAGEN_OUT, -1),
    "running": (RUNNING, -1),
    "interest": (INTEREST, -1),
    "principal": (PRINCIPAL, -1),
    "invested": (INVESTED, -1),
    "loan_disbursement": (frozenset({AllocationCategory.LOAN_DISBURSEMENT}), 1),
}
_RESULT_BLOCKS = ("rent", "other_income", "umlagen_in", "umlagen_out", "running", "interest")
_PROPERTY_BLOCKS = ("invested", "loan_disbursement")


def _counted_as(line: MonthLine) -> AllocationCategory:
    """D234: a privately paid purchase cost counts as hh_investment_property."""
    return AllocationCategory.HH_INVESTMENT_PROPERTY if line.purchase_cost else line.category


def cashflow_rows(lines: Iterable[MonthLine]) -> list[tuple[AllocationCategory, Decimal]]:
    """The private lines as `month_cashflow`'s (category, signed amount) rows, D234 applied."""
    return [(_counted_as(line), line.amount) for line in lines]


def rental_month(month: str, property_id: UUID, lines: Iterable[MonthLine]) -> RentalMonth:
    """One property's rental-account lines booked in `month`, summed by phase 4's sets
    (§4.1). A line in none of them (an owner transfer, a deposit, a transfer between own
    accounts) is in neither block (§4.3); a 0 € line is left out."""
    sums = dict.fromkeys(_BLOCKS, ZERO)
    by_block: dict[str, list[UUID]] = {}
    sources: list[UUID] = []
    for line in lines:
        if not line.amount:
            continue
        for block, (categories, sign) in _BLOCKS.items():
            if line.category in categories:
                sums[block] += sign * line.amount
                by_block.setdefault(block, []).append(line.allocation_id)
                sources.append(line.allocation_id)
                break
    return RentalMonth(
        month=month, property_id=property_id, **sums, sources=sources, sources_by_block=by_block
    )


def _role(category: AllocationCategory) -> str | None:
    """Where month_cashflow puts a private row: income, spending, invested or nowhere."""
    code = TAXONOMY.get(category)
    if code is None:  # bank_fees, uncategorized: „Sonstiges“
        return "spending"
    if code.group is HouseholdGroup.INVESTMENT:
        return "invested"
    if code.relevance is Relevance.INCOME:
        return "income"
    if code.relevance in (Relevance.SPENDING, Relevance.TAX):
        return "spending"
    return None


def _marked(
    line: MonthLine,
    one_off_ids: Collection[UUID],
    one_off_codes: Collection[AllocationCategory] = ONE_OFF,
) -> bool:
    """A spending line a person marked one-off (D297) that a one-off code does not already
    count: a payment (money out) in a code that is not one-off."""
    return (
        line.allocation_id in one_off_ids
        and line.amount < 0
        and _counted_as(line) not in one_off_codes
    )


def _ids(*groups: Iterable[UUID]) -> list[UUID]:
    return list(dict.fromkeys(i for group in groups for i in group))


def household_month(
    private: HouseholdCashflow,
    rental: Sequence[RentalMonth] = (),
    *,
    lines: Sequence[MonthLine] = (),
    link_sources: Sequence[UUID] = (),
    one_off_ids: Collection[UUID] = frozenset(),
    one_off_codes: Collection[AllocationCategory] = ONE_OFF,
) -> HouseholdMonth:
    """§4.2: the private accounts' cashflow plus the rental accounts' cash, counted once, in
    the calendar month of each line's booking date (D230, D232, D233).

    `private` is `month_cashflow` over `cashflow_rows(lines)` with the transfer links' saved
    amount, as `services/household.cashflow` returns it. `rental`: one RentalMonth per
    property (blocks summed; empty for a household without one). `lines`: the private lines
    behind `private`; they give D234's purchase costs and every field's `sources`, and must
    reproduce `private`'s income and spending (ValueError otherwise). `link_sources`: the
    allocation ids of the transfer links inside `private.saved`. `one_off_ids`: the
    allocations a person marked one-off (phase 10 §7.9 (c) 2, D297; empty by default): such a
    line moves from regular to one-off spending as a one-off code's does; a marked line that
    is no payment of spending (money in, a purchase cost, a one-off code already) moves
    nothing. `one_off_codes`: the codes counted as one-off, the taxonomy's by default, else
    rules.household.taxonomy.one_off_codes with the household's switches (phase 10 §7.12 (b),
    D307)."""
    for block in rental:
        if block.month != private.month:
            raise ValueError(f"a rental block of {block.month} in {private.month}")
    roles: dict[str, list[MonthLine]] = {}
    for line in lines:
        role = _role(_counted_as(line))
        if role is not None:
            roles.setdefault(role, []).append(line)
    if lines:
        income = sum((li.amount for li in roles.get("income", [])), ZERO)
        spent = -sum((li.amount for li in roles.get("spending", [])), ZERO)
        if income != private.income or spent != sum(private.spending_by_group.values(), ZERO):
            raise ValueError("the lines are not the ones behind the private cashflow")

    def total(name: str) -> Decimal:
        return sum((getattr(block, name) for block in rental), ZERO)

    def block_ids(*names: str) -> list[UUID]:
        return _ids(*(block.sources_by_block.get(n, []) for block in rental for n in names))

    private_spending = sum(private.spending_by_group.values(), ZERO)
    one_off = sum(
        (
            v
            for codes in private.spending_by_category.values()
            for c, v in codes.items()
            if c in one_off_codes
        ),
        ZERO,
    )
    marked = [li for li in roles.get("spending", []) if _marked(li, one_off_ids, one_off_codes)]
    one_off += -sum((li.amount for li in marked), ZERO)
    result = sum((block.result for block in rental), ZERO)
    principal = total("principal")
    rental_property = total("invested") - total("loan_disbursement")
    purchase = [li for li in lines if li.purchase_cost]
    purchase_costs = -sum((li.amount for li in purchase), ZERO)
    income = private.income + result
    regular = private_spending - one_off
    cash = result - principal

    spending_ids = [li.allocation_id for li in roles.get("spending", [])]
    one_off_sources = [
        li.allocation_id
        for li in roles.get("spending", [])
        if _counted_as(li) in one_off_codes or _marked(li, one_off_ids, one_off_codes)
    ]
    regular_ids = [i for i in spending_ids if i not in set(one_off_sources)]
    income_ids = [li.allocation_id for li in roles.get("income", [])]
    result_ids = block_ids(*_RESULT_BLOCKS)
    principal_ids = block_ids("principal")
    property_ids = block_ids(*_PROPERTY_BLOCKS)
    sources = {
        "private_income": income_ids,
        "private_spending": spending_ids,
        "one_off_spending": one_off_sources,
        "regular_spending": regular_ids,
        "rental_result": result_ids,
        "rental_cash": _ids(result_ids, principal_ids),
        "income": _ids(income_ids, result_ids),
        "spending": spending_ids,
        "saved": _ids(income_ids, result_ids, spending_ids),
        "principal": principal_ids,
        "invested_property": _ids(property_ids, (li.allocation_id for li in purchase)),
        "invested": _ids(
            link_sources, (li.allocation_id for li in roles.get("invested", [])), property_ids
        ),
        "free_cash": _ids(income_ids, regular_ids, result_ids, principal_ids),
    }
    return HouseholdMonth(
        month=private.month,
        private_income=private.income,
        private_spending=private_spending,
        one_off_spending=one_off,
        regular_spending=regular,
        rental_result=result,
        rental_cash=cash,
        income=income,
        spending=private_spending,
        saved=income - private_spending,
        principal=principal,
        invested_property=rental_property + purchase_costs,
        invested=private.saved + rental_property,
        free_cash=private.income - regular + cash,
        savings_rate=ratio(income - private_spending, income),
        sources={k: v for k, v in sources.items() if v},
    )
