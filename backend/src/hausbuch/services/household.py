"""The Haushalt screen's reads (docs/phases/phase-6.md §12, §13): one month of the private
accounts, computed on read from confirmed allocations only (rules.household.cashflow).

Phase 9 D234 (amends phase 6's screen, not its rule): a private line booked hh_property_cost
whose document is the Beleg of a Nebenkosten row of the property (property_acquisition_cost:
the notary, the land register, the Grunderwerbsteuer; phase 6 D161 amended) bought the flat,
so `cashflow` flags it `purchase_cost` and rules.health.month.cashflow_rows counts it as
hh_investment_property („Gespart, nicht ausgegeben“) instead of spending of the tax group.
`private_purchase_costs` lists those
allocations for phase 9's invested_property, which the category alone cannot tell apart
from a down payment booked hh_investment_property. A line paying a bill with both a purchase
part and a financing part moves whole (one allocation, one document)."""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Connection, and_, exists, func, or_, select

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import property_acquisition_cost as cost_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.rules.health.month import cashflow_rows
from hausbuch.rules.household.cashflow import month_cashflow
from hausbuch.schemas.allocation import AllocationCategory, TransferLegSource
from hausbuch.schemas.enums import AccountPurpose, AllocationStatus
from hausbuch.schemas.health import MonthLine
from hausbuch.schemas.household import HouseholdCashflow

from .transfers import saving_links


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


def months_between(first: str, last: str) -> list[str]:
    """Every calendar month from `first` to `last` ("YYYY-MM", inclusive), oldest first."""
    out, (year, number) = [], (int(first[:4]), int(first[5:7]))
    while f"{year:04d}-{number:02d}" <= last:
        out.append(f"{year:04d}-{number:02d}")
        year, number = (year + 1, 1) if number == 12 else (year, number + 1)
    return out


@dataclass(frozen=True)
class PrivateMonth:
    """One month of the private accounts: the Haushalt screen's cashflow, the lines behind
    it (rules.health.month.household_month reads them for D234 and the sources), and the
    allocation ids of the current-account legs of the transfer links in `cashflow.saved`."""

    cashflow: HouseholdCashflow
    lines: list[MonthLine]
    link_sources: list[UUID]


def private_months(conn: Connection, first: str, last: str) -> dict[str, PrivateMonth]:
    """Every month from `first` to `last`: the confirmed allocations of the private
    accounts' lines booked in it, as MonthLine rows flagged `purchase_cost` by D234, summed
    by rules.health.month.cashflow_rows (the one place D234's recode lives) into phase 6's
    month_cashflow."""
    months = months_between(first, last)
    if not months:
        return {}
    start, end = month_bounds(months[0])[0], month_bounds(months[-1])[1]
    a, t = allocation_table.c, transaction_table.c
    lines = _private_lines(start, end)
    by_month: dict[str, list[MonthLine]] = {m: [] for m in months}
    for r in conn.execute(
        select(a.id, a.category, a.amount, _purchase_cost().label("purchase_cost"), t.booking_date)
        .join(transaction_table, t.id == a.transaction_id)
        .where(
            a.transaction_id.in_(lines.scalar_subquery()),
            a.status == AllocationStatus.CONFIRMED.value,
        )
        .order_by(t.booking_date, a.transaction_id, a.id)
    ):
        by_month[_month(r.booking_date)].append(
            MonthLine(
                allocation_id=r.id,
                category=AllocationCategory(r.category),
                amount=r.amount,
                purchase_cost=bool(r.purchase_cost),
            )
        )
    confirmed = exists().where(
        a.transaction_id == t.id, a.status == AllocationStatus.CONFIRMED.value
    )
    open_lines: dict[str, int] = dict.fromkeys(months, 0)
    for (day,) in conn.execute(
        select(t.booking_date).where(t.id.in_(lines.scalar_subquery()), t.amount != 0, ~confirmed)
    ):
        open_lines[_month(day)] += 1
    saved: dict[str, Decimal] = {m: Decimal("0.00") for m in months}
    out_legs: dict[str, list[UUID]] = {m: [] for m in months}
    for view in saving_links(conn, start=start, end=end):
        month = _month(view.in_leg.booked_on)
        saved[month] += view.in_leg.amount
        if view.out_leg.source is TransferLegSource.TRANSACTION:
            out_legs[month].append(view.out_leg.id)
    sources = _allocation_ids(conn, [i for ids in out_legs.values() for i in ids])
    return {
        month: PrivateMonth(
            cashflow=month_cashflow(
                month,
                cashflow_rows(by_month[month]),
                saved=saved[month],
                open_lines=open_lines[month],
            ),
            lines=by_month[month],
            link_sources=[i for leg in out_legs[month] for i in sources.get(leg, [])],
        )
        for month in months
    }


def _month(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def _allocation_ids(conn: Connection, transaction_ids: list[UUID]) -> dict[UUID, list[UUID]]:
    if not transaction_ids:
        return {}
    a = allocation_table.c
    out: dict[UUID, list[UUID]] = {}
    for transaction_id, allocation_id in conn.execute(
        select(a.transaction_id, a.id)
        .where(
            a.transaction_id.in_(transaction_ids),
            a.status == AllocationStatus.CONFIRMED.value,
        )
        .order_by(a.transaction_id, a.id)
    ):
        out.setdefault(transaction_id, []).append(allocation_id)
    return out


def cashflow(conn: Connection, month: str) -> HouseholdCashflow:
    """GET /household/cashflow?month=: income, spending by group, saved, surplus and the
    savings rate of the private accounts' lines booked in the month, plus how many of them
    no confirmed answer explains yet (private_months, one month)."""
    return private_months(conn, month, month)[month].cashflow
