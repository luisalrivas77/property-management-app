"""§5.5, D112 (a): the telecom flat (R 9.1 Abs. 5 S. 4 LStR, Q-O27).

`min(cents(bills × 20 %), 20 × months) − employer reimbursement`, floored at 0.
"""

from decimal import Decimal

from hausbuch.schemas.employment import AnlageNRuleSet, TelecomFact

from ._base import ZERO, cents


def telecom_line(fact: TelecomFact, rs: AnlageNRuleSet) -> Decimal:
    flat = min(cents(fact.bills_year * rs.telecom_share), rs.telecom_cap_monthly * fact.months)
    return max(ZERO, flat - fact.employer_reimbursed)
