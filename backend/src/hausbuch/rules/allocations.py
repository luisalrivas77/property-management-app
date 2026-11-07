"""The allocation sum invariant (§3.4, D23; tests/fixtures/phase2/allocation_sum.yaml).

A transaction's allocations split it exactly: Σ amount equals the transaction amount, with no
rounding tolerance. Amounts are signed like the transaction, so a set with flipped signs is a
sum mismatch too. Every write path (matcher, rule, model, user) calls check_sum: the validator
enforces the sum, never the proposer.
"""

from collections.abc import Sequence
from decimal import Decimal

from hausbuch.schemas.allocation import AllocationDraft

from .errors import RuleError

SUM_MISMATCH = "allocation.sum_mismatch"
EMPTY = "allocation.empty"


def check_sum(transaction_amount: Decimal, drafts: Sequence[AllocationDraft]) -> None:
    """Raise RuleError(allocation.empty | allocation.sum_mismatch) unless the drafts split
    the transaction exactly."""
    if not drafts:
        raise RuleError(EMPTY, "a transaction needs at least one allocation")
    total = sum((d.amount for d in drafts), Decimal("0.00"))
    if total != transaction_amount:
        raise RuleError(
            SUM_MISMATCH, f"allocations sum to {total}, the transaction is {transaction_amount}"
        )
