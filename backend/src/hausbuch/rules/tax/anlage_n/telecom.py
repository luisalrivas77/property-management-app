"""§5.5, D112 (a): the telecom flat (R 9.1 Abs. 5 S. 4 LStR, Q-O27).

`min(cents(bills × 20 %), 20 × months) − employer reimbursement`, floored at 0.
"""

from dataclasses import dataclass
from decimal import Decimal

from hausbuch.schemas.employment import AnlageNRuleSet, TelecomFact

from ._base import ZERO, cents


@dataclass
class TelecomResult:
    share_amount: Decimal  # cents(bills × share)
    cap: Decimal  # cap per month × months
    amount: Decimal


def telecom_result(fact: TelecomFact, rs: AnlageNRuleSet) -> TelecomResult:
    share_amount = cents(fact.bills_year * rs.telecom_share)
    cap = rs.telecom_cap_monthly * fact.months
    flat = min(share_amount, cap)
    return TelecomResult(
        share_amount=share_amount, cap=cap, amount=max(ZERO, flat - fact.employer_reimbursed)
    )


def telecom_line(fact: TelecomFact, rs: AnlageNRuleSet) -> Decimal:
    return telecom_result(fact, rs).amount
