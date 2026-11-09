"""The §23 date (phase 3 §5.9, D68): a sale contract signed on or after this day is tax-free.

§23 Abs. 1 S. 1 Nr. 1 EStG counts the ten years between the two obligatory contracts, so the
first tax-free day is the purchase contract's date + `speculation_years` + 1 day; a 29 Feb
contract date falls on 28 Feb ten years on, so the day after is 1 Mar. The gain is phase 4.
"""

from datetime import date, timedelta

from hausbuch.schemas.tax import RuleSet

from ._common import add_years


def tax_free_from(notarized_on: date, rule_set: RuleSet) -> date:
    return add_years(notarized_on, rule_set.speculation_years) + timedelta(days=1)
