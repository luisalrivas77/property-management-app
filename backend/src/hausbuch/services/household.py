"""The Haushalt screen's reads (docs/phases/phase-6.md §12, §13): one month of the private
accounts, computed on read from confirmed allocations only (rules.household.cashflow)."""

from calendar import monthrange
from datetime import date
from decimal import Decimal

from sqlalchemy import Connection, exists, func, select

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.rules.household.cashflow import month_cashflow
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.enums import AccountPurpose, AllocationStatus
from hausbuch.schemas.household import HouseholdCashflow

from .transfers import saved_in


def month_bounds(month: str) -> tuple[date, date]:
    """'2026-10' -> (1 Oct, 31 Oct); the schema's pattern has checked the shape."""
    year, number = int(month[:4]), int(month[5:7])
    return date(year, number, 1), date(year, number, monthrange(year, number)[1])


def cashflow(conn: Connection, month: str) -> HouseholdCashflow:
    """GET /household/cashflow?month=: income, spending by group, saved, surplus and the
    savings rate of the private accounts' lines booked in the month, plus how many of them
    no confirmed answer explains yet."""
    start, end = month_bounds(month)
    a, t, acc = allocation_table.c, transaction_table.c, account_table.c
    lines = (
        select(t.id)
        .join(account_table, acc.id == t.account_id)
        .where(
            acc.purpose == AccountPurpose.PRIVATE.value,
            t.excluded_as_duplicate_of.is_(None),
            t.booking_date >= start,
            t.booking_date <= end,
        )
    )
    rows = conn.execute(
        select(a.category, func.sum(a.amount))
        .where(
            a.transaction_id.in_(lines.scalar_subquery()),
            a.status == AllocationStatus.CONFIRMED.value,
        )
        .group_by(a.category)
    ).all()
    confirmed = exists().where(
        a.transaction_id == t.id, a.status == AllocationStatus.CONFIRMED.value
    )
    open_lines = conn.execute(
        select(func.count()).select_from(lines.where(t.amount != 0, ~confirmed).subquery())
    ).scalar_one()
    return month_cashflow(
        month,
        [(AllocationCategory(c), Decimal(v)) for c, v in rows],
        saved=saved_in(conn, start=start, end=end),
        open_lines=open_lines,
    )
