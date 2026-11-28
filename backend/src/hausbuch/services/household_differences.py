"""Haushalt › Monat per category (docs/phases/phase-10.md §7.13 (b) 2, 3, D314).

- `month_category_differences(conn, month, today)`: GET /household/differences?month=, the
  chosen month's spending codes against their mean over the closed months before it, by
  rules.budget.differences.category_differences. The month may be any month from January
  2026 to the one running today; the month in progress names only what is already above its
  mean. GET /health's `differences` (the newest closed month by group, phase 10 §7.6) is
  unchanged.
- `category_lines(conn, month, category, today)`: GET /household/lines?month=&category=,
  the payments behind one code's month, so a row of „Was diesen Monat anders ist“ and a
  subcategory in the spending card's panel open their bank lines; the server sums them.

Both read the private accounts' month the Haushalt screen reads (services.household's
private_months, D234's recode in rules.health.month.cashflow_rows) and the payments marked
one-off (services.spending_marks); nothing is stored.
"""

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Connection, select

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.rules.budget.differences import (
    category_differences,
    compared_by_category,
    compared_rows,
    spending_group,
)
from hausbuch.rules.errors import HEALTH_RANGE_INVALID
from hausbuch.rules.health.month import cashflow_rows
from hausbuch.rules.health.window import window
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.health import (
    CategoryDifferences,
    CategoryLines,
    CategoryPayment,
    month_of,
)

from .errors import RuleViolationError
from .health import FIRST_MONTH, household_levers, levers_one_off_codes, read_months
from .household import month_bounds, months_between, private_months
from .spending_marks import one_off_marked

_ZERO = Decimal("0.00")


def _check_month(month: str, today: date) -> None:
    """A month from January 2026 to the one running `today` (Berlin)."""
    if month < FIRST_MONTH or month > month_of(today):
        raise RuleViolationError(
            [HEALTH_RANGE_INVALID], f"{month}: a month from {FIRST_MONTH} to {month_of(today)}"
        )


def _previous(month: str) -> str:
    return month_of(month_bounds(month)[0] - timedelta(days=1))


def month_category_differences(conn: Connection, month: str, today: date) -> CategoryDifferences:
    """GET /household/differences?month=: `month` against the unbroken run of closed months
    ending before it (cut to 12, as `window` takes it), the codes counted as one-off by the
    default household scenario in force (phase 10 D307) left out as GET /health leaves them."""
    _check_month(month, today)
    codes = levers_one_off_codes(household_levers(conn))
    reads = read_months(conn, months_between(FIRST_MONTH, month), today, codes)
    before = _previous(month)
    baseline = window({m: r.closed.closed for m, r in reads.items() if m <= before}).months
    spending = {
        m: compared_by_category(
            compared_rows(reads[m].private.lines, reads[m].one_off_ids), reads[m].one_off_codes
        )
        for m in [*baseline, month]
    }
    final = reads[month].closed.closed
    named = category_differences(spending[month], [spending[m] for m in baseline], final=final)
    return CategoryDifferences(
        month=month,
        final=final,
        enough=named is not None,
        baseline=baseline,
        categories=named or [],
    )


def category_lines(
    conn: Connection, month: str, category: AllocationCategory, today: date
) -> CategoryLines:
    """GET /household/lines?month=&category=: the month's confirmed private allocations
    counted under `category` as spending, each signed as spending, with their sum and the sum
    less the payments marked one-off. A code that is no spending has no lines here."""
    _check_month(month, today)
    group = spending_group(category)
    lines = private_months(conn, month, month)[month].lines
    marked = one_off_marked(conn)
    wanted: dict[UUID, tuple[Decimal, bool]] = {}
    if group is not None:
        for line, (counted, amount) in zip(lines, cashflow_rows(lines), strict=True):
            if counted is category:
                wanted[line.allocation_id] = (-amount, line.allocation_id in marked and amount < 0)
    payments: list[CategoryPayment] = []
    if wanted:
        a, t, acc = allocation_table.c, transaction_table.c, account_table.c
        for r in conn.execute(
            select(
                a.id,
                t.id.label("transaction_id"),
                acc.id.label("account_id"),
                acc.label,
                t.booking_date,
                t.counterparty_name,
                t.purpose,
            )
            .join(transaction_table, t.id == a.transaction_id)
            .join(account_table, acc.id == t.account_id)
            .where(a.id.in_(list(wanted)))
            .order_by(t.booking_date, t.id, a.id)
        ):
            amount, one_off = wanted[r.id]
            payments.append(
                CategoryPayment(
                    allocation_id=r.id,
                    transaction_id=r.transaction_id,
                    account_id=r.account_id,
                    account_label=r.label,
                    booked_on=r.booking_date,
                    payee=r.counterparty_name,
                    purpose=r.purpose or "",
                    category=category,
                    amount=amount,
                    one_off=one_off,
                )
            )
    return CategoryLines(
        month=month,
        category=category,
        group=group,
        total=sum((p.amount for p in payments), _ZERO),
        regular=sum((p.amount for p in payments if not p.one_off), _ZERO),
        payments=payments,
    )
