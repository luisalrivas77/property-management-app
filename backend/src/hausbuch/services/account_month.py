"""GET /accounts/{id}/month?month=YYYY-MM (docs/phases/phase-10.md D269): the card above
Konten › Umsätze, so no figure of it is summed in the browser (principle 1).

The flows are rules.account_month.month_flows over the lines booked in the month, read as
GET /accounts/{id}/transactions reads them (services.statements.list_transactions: each line's
allocation status and current categories). The balances follow the net worth's rule
(services.net_worth._balances): the opening balance plus the lines not merged away as
duplicates, through the day the account's statements are reconciled to, never past the day
asked for; `*_as_of` is the day reached. The opening balance is the one at the end of the month
before. No balance for an account kind without a euro balance of lines (an exchange, a wallet),
for a depot or card without statements and with an opening balance of 0 (the net worth leaves
it out too), or for a day before the account's opening balance. The running month's closing
balance is today's. A month after today's is refused (account.month_in_future). Reads only.
"""

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Connection, func, select

from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.rules import net_worth as rules
from hausbuch.rules.account_month import AccountLine, month_flows
from hausbuch.rules.errors import ACCOUNT_MONTH_IN_FUTURE
from hausbuch.schemas.bank import Account, TransactionAllocationStatus
from hausbuch.schemas.budget import AccountMonth
from hausbuch.schemas.enums import BANK_ACCOUNT_KINDS, EntityType, ImportStatus
from hausbuch.schemas.health import month_of

from .accounts import get_account
from .errors import NotFoundError, RuleViolationError
from .household import month_bounds
from .net_worth import _BALANCE_KINDS
from .statements import list_transactions


def _balance(
    conn: Connection, account: Account, day: date, reached: date | None, has_imports: bool
) -> tuple[Decimal, date] | None:
    """The balance at the end of `day` by the net worth's rule, and the day it reaches."""
    if account.kind not in _BALANCE_KINDS or day < account.opening_balance_on:
        return None
    if account.kind not in BANK_ACCOUNT_KINDS and not has_imports and account.opening_balance == 0:
        return None
    if reached is not None and reached >= day:
        through = day
    else:
        through = max(reached or account.opening_balance_on, account.opening_balance_on)
    t = transaction_table.c
    lines = conn.execute(
        select(t.booking_date, t.amount).where(
            t.account_id == account.id,
            t.excluded_as_duplicate_of.is_(None),
            t.booking_date > account.opening_balance_on,
            t.booking_date <= through,
        )
    ).all()
    total = rules.bank_balance(
        account.opening_balance,
        account.opening_balance_on,
        [(r.booking_date, r.amount) for r in lines],
        through,
    )
    return total, through


def account_month(conn: Connection, account_id: UUID, month: str, today: date) -> AccountMonth:
    """The month `month` of the account on `today` (Berlin). NotFoundError for an account of
    no workspace the session can see (row-level security hides another's)."""
    account = get_account(conn, account_id)
    if account is None:
        raise NotFoundError(EntityType.ACCOUNT, account_id)
    current = month_of(today)
    if month > current:
        raise RuleViolationError(
            [ACCOUNT_MONTH_IN_FUTURE], f"{month} lies after the running month {current}"
        )
    start, end = month_bounds(month)
    running = month == current
    imp = import_table.c
    facts = conn.execute(
        select(
            func.count().label("n"),
            func.max(imp.last_booking_on)
            .filter(imp.status == ImportStatus.RECONCILED.value)
            .label("reconciled_through"),
        ).where(imp.account_id == account_id)
    ).one()
    reached, has_imports = facts.reconciled_through, facts.n > 0
    opening = _balance(conn, account, start - timedelta(days=1), reached, has_imports)
    closing = _balance(conn, account, today if running else end, reached, has_imports)
    lines = list_transactions(conn, account_id, booked_from=start, booked_to=end)
    flows = month_flows(
        AccountLine(
            amount=line.amount,
            status=line.allocation_status,
            categories=tuple(line.allocation_categories),
        )
        for line in lines
        if line.allocation_status is not TransactionAllocationStatus.EXCLUDED
    )
    return AccountMonth(
        account_id=account_id,
        month=month,
        running=running,
        opening_balance=None if opening is None else opening[0],
        opening_as_of=None if opening is None else opening[1],
        closing_balance=None if closing is None else closing[0],
        closing_as_of=None if closing is None else closing[1],
        incoming=flows.incoming,
        outgoing=flows.outgoing,
        transfers_in=flows.transfers_in,
        transfers_out=flows.transfers_out,
        lines=flows.lines,
        open_lines=flows.open_lines,
    )
