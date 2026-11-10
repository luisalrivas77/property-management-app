"""§5.5, D108: work equipment, year Y's amount of one item.

`net = net or cents(gross ÷ (1 + VAT))`; `base = cents(gross × professional share)` (the gross
counts, an employee deducts VAT). A subscription, an item of at most 800 net (GWG, §6 Abs. 2 via
R 9.12 LStR) or a class written off in full in its year (computer, software: BMF 22 Feb 2022)
counts `base` in the purchase year only. Every other item is depreciated by months from the
purchase month (R 7.4 Abs. 2): each year `cents(base × months in the year ÷ life)`, the last year
the remainder, so the slices sum to the base. `disposed_on` in year d (D108 (a)): d takes the
months up to the disposal month (the planned slice when d is the plan's last year), nothing
after. An `other` item without a typed life: RuleError `anlage_n.useful_life_missing`.
"""

from datetime import date
from decimal import Decimal

from hausbuch.rules.errors import ANLAGE_N_USEFUL_LIFE_MISSING, RuleError
from hausbuch.schemas.employment import AnlageNRuleSet, EquipmentFact

from ._base import NOT_ZERO, ZERO, cents


def months_by_year(start: date, months: int) -> list[tuple[int, int]]:
    """The depreciation months per calendar year from the purchase month, inclusive."""
    out, year, month, left = [], start.year, start.month, months
    while left > 0:
        n = min(13 - month, left)
        out.append((year, n))
        left -= n
        year, month = year + 1, 1
    return out


def base_amount(item: EquipmentFact) -> Decimal:
    share = Decimal(item.professional_share.numerator) / item.professional_share.denominator
    return cents(item.gross * share)


def equipment_amount(item: EquipmentFact, tax_year: int, rs: AnlageNRuleSet) -> Decimal:
    """Year `tax_year`'s amount of one item (0.00 outside its years)."""
    net = item.net if item.net is not None else cents(item.gross / (1 + rs.default_vat_rate))
    base = base_amount(item)
    bought = item.bought_on.year
    life = rs.useful_life(item.item_class)
    if item.item_class == "subscription" or net <= rs.gwg_limit_net or life.full_in_year:
        return base if tax_year == bought else NOT_ZERO
    months = item.useful_life_months or life.months
    if months is None:
        raise RuleError(
            ANLAGE_N_USEFUL_LIFE_MISSING, f"{item.item_class}: type the useful life in months"
        )
    plan = months_by_year(item.bought_on, months)
    slices: dict[int, Decimal] = {}
    done = ZERO
    for i, (year, n) in enumerate(plan):
        slices[year] = base - done if i == len(plan) - 1 else cents(base * n / months)
        done += slices[year]
    if tax_year not in slices:
        # Outside the plan nothing counts, whatever the disposal date: a disposal after the
        # life ended must not add a slice the plan never had.
        return NOT_ZERO
    disposed = item.disposed_on
    if disposed is not None:
        if tax_year > disposed.year:
            return NOT_ZERO
        if tax_year == disposed.year:
            if tax_year == plan[-1][0]:
                return slices[tax_year]
            start = item.bought_on.month if tax_year == bought else 1
            return cents(base * (disposed.month - start + 1) / months)
    return slices[tax_year]


def spans_years(item: EquipmentFact, rs: AnlageNRuleSet) -> bool:
    """Whether the item is depreciated over more than its purchase year (a spanning row, D110)."""
    if item.item_class == "subscription":
        return False
    net = item.net if item.net is not None else cents(item.gross / (1 + rs.default_vat_rate))
    life = rs.useful_life(item.item_class)
    return not (net <= rs.gwg_limit_net or life.full_in_year)
