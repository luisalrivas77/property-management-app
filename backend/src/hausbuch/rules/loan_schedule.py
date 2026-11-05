"""Deterministic annuity repayment schedule (D10; tests/fixtures/phase1/loan_schedule.yaml).

Conventions:
- Monthly, in arrears. Payment n is due on first_payment_on + (n-1) months, on the base day of
  month, clamped to the month's last day (the 30th is Feb 28, then the 30th again in March).
- Interest is 30E/360: period 1 accrues days360(disbursed_on, first_payment_on) / 360, every
  later period 1/12. Rounded half-up to the cent each period.
- The rate is nominal_rate while due_on <= fixed_until, then follow_up_rate (null = keep
  nominal_rate). The monthly payment does not change at the switch.
- principal = payment - interest; the final payment is the remaining balance + interest.
- A period whose interest uses up the whole payment raises loan.payment_does_not_cover_interest.
- Year totals are per calendar year of due_on.
"""

import calendar
from collections.abc import Iterator
from datetime import date
from decimal import ROUND_HALF_UP, Context, Decimal, localcontext

from hausbuch.schemas.loan import LoanSchedule, LoanTerms, ScheduleEntry, YearTotals

from .errors import RuleError

PAYMENT_DOES_NOT_COVER_INTEREST = "loan.payment_does_not_cover_interest"

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
# Exact for any numeric(14,2) balance times a numeric(7,6) rate; independent of the caller's
# decimal context, so the schedule is reproducible everywhere.
_CONTEXT = Context(prec=50)


def build_schedule(terms: LoanTerms) -> LoanSchedule:
    with localcontext(_CONTEXT):
        entries = list(_entries(terms))
        return LoanSchedule(entries=entries, years=_year_totals(entries))


def _entries(terms: LoanTerms) -> Iterator[ScheduleEntry]:
    balance = terms.principal
    payment = terms.monthly_payment
    number = 0
    while balance > 0:
        number += 1
        due_on = _add_months(terms.first_payment_on, number - 1)
        rate = _rate_on(terms, due_on)
        days = _days360(terms.disbursed_on, terms.first_payment_on) if number == 1 else 30
        # Divide last: days/360 is a repeating decimal, and rounding it first can push an
        # exact half cent (e.g. 50.205) just below the tie, so half-up would round it down.
        interest = (balance * rate * days / 360).quantize(CENT, rounding=ROUND_HALF_UP)
        if balance + interest <= payment:
            this_payment, principal = balance + interest, balance
        elif interest >= payment:
            raise RuleError(
                PAYMENT_DOES_NOT_COVER_INTEREST,
                f"payment {number} on {due_on}: interest {interest} >= payment {payment}",
                at_number=number,
            )
        else:
            this_payment, principal = payment, payment - interest
        closing = balance - principal
        yield ScheduleEntry(
            number=number,
            due_on=due_on,
            rate=rate,
            opening_balance=balance,
            interest=interest,
            principal=principal,
            payment=this_payment,
            closing_balance=closing,
        )
        balance = closing


def _year_totals(entries: list[ScheduleEntry]) -> list[YearTotals]:
    years: dict[int, list[ScheduleEntry]] = {}
    for e in entries:
        years.setdefault(e.due_on.year, []).append(e)
    return [
        YearTotals(
            year=year,
            interest=sum((e.interest for e in rows), ZERO),
            principal=sum((e.principal for e in rows), ZERO),
            payment=sum((e.payment for e in rows), ZERO),
            closing_balance=rows[-1].closing_balance,
        )
        for year, rows in years.items()
    ]


def _rate_on(terms: LoanTerms, due_on: date) -> Decimal:
    if terms.fixed_until is None or due_on <= terms.fixed_until:
        return terms.nominal_rate
    return terms.nominal_rate if terms.follow_up_rate is None else terms.follow_up_rate


def _add_months(base: date, months: int) -> date:
    """Same day of month as `base`, clamped to the target month's last day."""
    month_index = base.month - 1 + months
    year, month = base.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(base.day, calendar.monthrange(year, month)[1]))


def _days360(start: date, end: date) -> int:
    """30E/360 (Eurobond basis): day 31 counts as day 30 at either end."""
    d1, d2 = min(start.day, 30), min(end.day, 30)
    return (end.year - start.year) * 360 + (end.month - start.month) * 30 + (d2 - d1)
