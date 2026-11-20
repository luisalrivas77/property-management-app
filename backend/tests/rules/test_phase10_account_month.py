"""rules.account_month (docs/phases/phase-10.md D269): the card above Konten › Umsätze."""

from decimal import Decimal

from hausbuch.rules.account_month import TRANSFER_CODES, AccountLine, month_flows
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.bank import TransactionAllocationStatus as S


def line(amount: str, status: S = S.CONFIRMED, *categories: C) -> AccountLine:
    return AccountLine(Decimal(amount), status, categories)


def test_the_transfer_codes_are_d269s_list() -> None:
    assert {
        C.INTERNAL_TRANSFER_IN,
        C.INTERNAL_TRANSFER_OUT,
        C.SECURITY_PURCHASE,
        C.SECURITY_SALE,
        C.HH_INVESTMENT,
        C.HH_INVESTMENT_PROPERTY,
        C.HH_INVESTMENT_SECURITIES,
        C.HH_INVESTMENT_CRYPTO,
        C.HH_INVESTMENT_SAVINGS,
        C.OWNER_TRANSFER_IN,  # the rental account's transfers from and to the owners
        C.OWNER_TRANSFER_OUT,
    } == TRANSFER_CODES


def test_month_flows() -> None:
    flows = month_flows(
        [
            line("4200.00", S.CONFIRMED, C.HH_SALARY),
            line("-612.37", S.CONFIRMED, C.HH_GROCERIES),
            line("-300.00", S.CONFIRMED, C.INTERNAL_TRANSFER_OUT),
            line("150.00", S.CONFIRMED, C.INTERNAL_TRANSFER_IN),
            line("-250.00", S.CONFIRMED, C.HH_INVESTMENT_SECURITIES),
            # a split with one transfer part and one spending part is not a transfer
            line("-80.00", S.CONFIRMED, C.INTERNAL_TRANSFER_OUT, C.HH_GROCERIES),
            # not yet booked: by its sign, and open, even when proposed as a transfer
            line("-45.00", S.PROPOSED, C.INTERNAL_TRANSFER_OUT),
            line("19.99", S.OPEN),
            line("-12.00", S.NOT_RECONCILED),
            # merged away as a duplicate: nowhere
            line("-612.37", S.EXCLUDED, C.HH_GROCERIES),
        ]
    )
    assert flows.incoming == Decimal("4219.99")
    assert flows.outgoing == Decimal("749.37")
    assert flows.transfers_in == Decimal("150.00")
    assert flows.transfers_out == Decimal("550.00")
    assert (flows.lines, flows.open_lines) == (9, 3)


def test_an_empty_month() -> None:
    flows = month_flows([])
    assert (flows.incoming, flows.outgoing, flows.lines, flows.open_lines) == (0, 0, 0, 0)
