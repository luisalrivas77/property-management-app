"""One account's month for Konten › Umsätze (docs/phases/phase-10.md D269). Pure: the service
(services.account_month) reads the month's lines and the balances.

A line counts as a transfer between own accounts when it is booked (every current allocation
row confirmed) and every one of its categories is a transfer code: the internal transfers, the
depot's purchase and sale, the Geldanlage codes (the household taxonomy's TRANSFER relevance),
and on the rental account the owner transfers to and from the owners' own accounts. Every other
line counts by its sign in Eingänge or Ausgänge, whether booked or not; a line not booked is
counted in `open_lines`. A line merged away as a duplicate counts nowhere.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from hausbuch.rules.household.taxonomy import TAXONOMY
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.bank import TransactionAllocationStatus as S
from hausbuch.schemas.household import Relevance

ZERO = Decimal("0.00")
TRANSFER_CODES = frozenset(
    {code for code, row in TAXONOMY.items() if row.relevance is Relevance.TRANSFER}
    | {C.OWNER_TRANSFER_IN, C.OWNER_TRANSFER_OUT}
)


@dataclass(frozen=True)
class AccountLine:
    amount: Decimal  # signed as on the account
    status: S
    categories: tuple[C, ...]  # the current allocation rows' categories


@dataclass(frozen=True)
class MonthFlows:
    incoming: Decimal = ZERO
    outgoing: Decimal = ZERO
    transfers_in: Decimal = ZERO
    transfers_out: Decimal = ZERO
    lines: int = 0
    open_lines: int = 0


def is_transfer(line: AccountLine) -> bool:
    return (
        line.status is S.CONFIRMED
        and bool(line.categories)
        and all(c in TRANSFER_CODES for c in line.categories)
    )


def month_flows(lines: Iterable[AccountLine]) -> MonthFlows:
    """The month's figures from its lines (booked in the month)."""
    incoming = outgoing = transfers_in = transfers_out = ZERO
    count = open_count = 0
    for line in lines:
        if line.status is S.EXCLUDED:
            continue
        count += 1
        if line.status is not S.CONFIRMED:
            open_count += 1
        if is_transfer(line):
            if line.amount > 0:
                transfers_in += line.amount
            else:
                transfers_out -= line.amount
        elif line.amount > 0:
            incoming += line.amount
        else:
            outgoing -= line.amount
    return MonthFlows(incoming, outgoing, transfers_in, transfers_out, count, open_count)
