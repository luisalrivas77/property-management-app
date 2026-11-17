"""The Haushalt screen's reads (docs/phases/phase-6.md §12, §13): one month of the private
accounts, computed on read from confirmed allocations only (rules.household.cashflow).

Phase 9 D234 (amends phase 6's screen, not its rule): a private line booked hh_property_cost
whose document is the Beleg of a Nebenkosten row of the property (property_acquisition_cost:
the notary, the land register, the Grunderwerbsteuer; phase 6 D161 amended) bought the flat,
so `cashflow` hands it to month_cashflow as hh_investment_property („Gespart, nicht
ausgegeben“) instead of spending of the tax group. `private_purchase_costs` lists those
allocations for phase 9's invested_property, which the category alone cannot tell apart
from a down payment booked hh_investment_property. A line paying a bill with both a purchase
part and a financing part moves whole (one allocation, one document)."""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Connection, and_, case, exists, func, or_, select

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import property_acquisition_cost as cost_table
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


def _purchase_cost():  # noqa: ANN202
    """SQL: the allocation is a private hh_property_cost row whose document is the Beleg of a
    Nebenkosten row (of the allocation's property when it names one), phase 9 D234."""
    a, c = allocation_table.c, cost_table.c
    return and_(
        a.category == AllocationCategory.HH_PROPERTY_COST.value,
        a.document_id.is_not(None),
        exists().where(
            c.document_id == a.document_id,
            or_(a.property_id.is_(None), c.property_id == a.property_id),
        ),
    )


@dataclass(frozen=True)
class PrivatePurchaseCost:
    """One confirmed private allocation that paid a Nebenkosten row (D234): its amount signed
    as booked (a payment is negative), the property of the row it pays."""

    allocation_id: UUID
    transaction_id: UUID
    booked_on: date
    property_id: UUID
    amount: Decimal


def _private_lines(start: date, end: date):  # noqa: ANN202
    t, acc = transaction_table.c, account_table.c
    return (
        select(t.id)
        .join(account_table, acc.id == t.account_id)
        .where(
            acc.purpose == AccountPurpose.PRIVATE.value,
            t.excluded_as_duplicate_of.is_(None),
            t.booking_date >= start,
            t.booking_date <= end,
        )
    )


def private_purchase_costs(conn: Connection, start: date, end: date) -> list[PrivatePurchaseCost]:
    """The private allocations booked between the days (inclusive) that `cashflow` counts as
    hh_investment_property under D234, oldest first. The property is the allocation's, else
    the Nebenkosten row's (the first by id when one document is the Beleg of several)."""
    a, t, c = allocation_table.c, transaction_table.c, cost_table.c
    row_property = (
        select(c.property_id)
        .where(c.document_id == a.document_id)
        .order_by(c.property_id)
        .limit(1)
        .scalar_subquery()
    )
    rows = conn.execute(
        select(
            a.id,
            a.transaction_id,
            t.booking_date,
            func.coalesce(a.property_id, row_property),
            a.amount,
        )
        .join(transaction_table, t.id == a.transaction_id)
        .where(
            a.transaction_id.in_(_private_lines(start, end).scalar_subquery()),
            a.status == AllocationStatus.CONFIRMED.value,
            _purchase_cost(),
        )
        .order_by(t.booking_date, a.transaction_id, a.id)
    ).all()
    return [PrivatePurchaseCost(*r) for r in rows]


def cashflow(conn: Connection, month: str) -> HouseholdCashflow:
    """GET /household/cashflow?month=: income, spending by group, saved, surplus and the
    savings rate of the private accounts' lines booked in the month, plus how many of them
    no confirmed answer explains yet."""
    start, end = month_bounds(month)
    a, t = allocation_table.c, transaction_table.c
    lines = _private_lines(start, end)
    # D234: a privately paid purchase cost is invested, not spent.
    category = case(
        (_purchase_cost(), AllocationCategory.HH_INVESTMENT_PROPERTY.value), else_=a.category
    ).label("category")
    rows = conn.execute(
        select(category, func.sum(a.amount))
        .where(
            a.transaction_id.in_(lines.scalar_subquery()),
            a.status == AllocationStatus.CONFIRMED.value,
        )
        .group_by(category)
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
