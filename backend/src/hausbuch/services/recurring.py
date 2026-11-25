"""The recurring payments on read (docs/phases/phase-10.md §7.9 (b) 5; D296). Every figure
comes from rules/recurring over the confirmed private lines; the only stored input is a
person's answers (services.spending_marks). The search for payments that may be one-off
(§7.9 (c) 1, D297, GET /household/one-off-candidates) went with phase 10 §7.12 (b), D307:
the one-offs are categories now, and a single payment is marked from its detail panel.

- **The lines**: every confirmed, non-zero allocation of a private account's line that is no
  duplicate, booked from 1 Jan 2026 (the household's bank history, §7.9 (b) 1) to `today`,
  with its account, payee and purpose; its code as the household month counts it (D234's
  recode applied by rules.health.month.cashflow_rows, the one place it lives).
- **GET /household/recurring**: rules.recurring.recurring over those lines and the series
  answers, as of `today` (Berlin). A series' state and the yearly detection read the day, so
  the kept answer's key (it holds the Berlin day, §7.4) covers it. The state also reads how
  far each private account's lines reach (D296 amended 24 Nov 2026): the newest booking date
  of any line on it that is no duplicate, up to `today`, whatever its status or sign.
"""

from datetime import date
from uuid import UUID

from sqlalchemy import Connection, func, select

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.rules.health.month import cashflow_rows
from hausbuch.rules.recurring import SpendingLine, recurring
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.enums import AllocationStatus
from hausbuch.schemas.health import MonthLine
from hausbuch.schemas.recurring import RecurringPayments

from .household import _private_lines, _purchase_cost
from .spending_marks import HISTORY_START, series_marks


def spending_lines(conn: Connection, today: date) -> list[SpendingLine]:
    """The confirmed private lines from HISTORY_START to `today` (see the module), oldest
    booking first."""
    a, t, acc = allocation_table.c, transaction_table.c, account_table.c
    out = []
    for r in conn.execute(
        select(
            a.id,
            a.category,
            a.amount,
            _purchase_cost().label("purchase_cost"),
            t.id.label("transaction_id"),
            t.booking_date,
            t.counterparty_name,
            t.purpose,
            acc.id.label("account_id"),
            acc.label.label("account_label"),
        )
        .join(transaction_table, t.id == a.transaction_id)
        .join(account_table, acc.id == t.account_id)
        .where(
            a.transaction_id.in_(_private_lines(HISTORY_START, today).scalar_subquery()),
            a.status == AllocationStatus.CONFIRMED.value,
            a.amount != 0,
        )
        .order_by(t.booking_date, t.id, a.id)
    ):
        line = MonthLine(
            allocation_id=r.id,
            category=AllocationCategory(r.category),
            amount=r.amount,
            purchase_cost=bool(r.purchase_cost),
        )
        ((category, _),) = cashflow_rows([line])
        out.append(
            SpendingLine(
                allocation_id=r.id,
                transaction_id=r.transaction_id,
                account_id=r.account_id,
                account_label=r.account_label,
                booked_on=r.booking_date,
                payee=r.counterparty_name,
                purpose=r.purpose,
                category=category,
                amount=r.amount,
            )
        )
    return out


def accounts_through(conn: Connection, today: date) -> dict[UUID, date]:
    """Per private account the newest booking date of a line on it from HISTORY_START to
    `today`, any line that is no duplicate (see the module)."""
    t = transaction_table.c
    rows = conn.execute(
        select(t.account_id, func.max(t.booking_date))
        .where(t.id.in_(_private_lines(HISTORY_START, today).scalar_subquery()))
        .group_by(t.account_id)
    )
    return {account_id: day for account_id, day in rows}


def recurring_payments(conn: Connection, today: date) -> RecurringPayments:
    """GET /household/recurring (see the module)."""
    return recurring(
        spending_lines(conn, today),
        series_marks(conn),
        today=today,
        history_start=HISTORY_START,
        accounts_through=accounts_through(conn, today),
    )
