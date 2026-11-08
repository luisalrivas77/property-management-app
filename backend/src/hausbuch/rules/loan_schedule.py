"""Deterministic annuity repayment schedule (D10; tests/fixtures/phase1/loan_schedule.yaml).

Conventions:
- Monthly, in arrears. Payment n is due on first_payment_on + (n-1) months, on the base day of
  month, clamped to the month's last day (the 30th is Feb 28, then the 30th again in March).
- Interest is 30E/360 and accrues over the period it pays for, in arrears: from the previous
  due date (disbursed_on for payment 1) up to but not including due_on. Period 1 has
  days360(disbursed_on, first_payment_on) days, every later period 30.
- interest_only_before_first_payment (a loan paid out in parts): before first_payment_on the
  lender charges interest only, outside this schedule, so period 1 runs from one month before
  first_payment_on and has 30 days, on the full principal, like every later period. A loan
  paid out after that day has no interest-only time: period 1 stays days360(disbursed_on,
  first_payment_on). Flag off: unchanged.
- Each day accrues at the rate in force that day: nominal_rate up to and including
  fixed_until, follow_up_rate after it (null = keep nominal_rate). A period whose due date is
  on or before the day after fixed_until is entirely at nominal_rate; one starting on or after
  it is entirely at follow_up_rate. A period straddling it gets days360(switch day, due_on)
  days at follow_up_rate, capped at the period's days, and the rest at nominal_rate. Interest
  is rounded half-up to the cent once per period. The monthly payment does not change.
- ScheduleEntry.rate is the rate in force on the period's last day.
- principal = payment - interest; the final payment is the remaining balance + interest.
- A period whose interest uses up the whole payment raises loan.payment_does_not_cover_interest.
- More than 1,200 payments (100 years) raises loan.term_too_long at payment 1201, checked
  before that payment is computed (spec amendment 1).
- Each entry carries interest_days (the period's 30E/360 days) and follow_up_days (those after
  fixed_until, at the follow-up rate), so its interest is traceable from the entry alone:
  interest = opening_balance * (nominal * (interest_days - follow_up_days)
             + follow_up * follow_up_days) / 360, rounded half-up (principle 2).
- Year totals are per calendar year of due_on.
"""

import calendar
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Context, Decimal, localcontext

from hausbuch.schemas.loan import LoanSchedule, LoanTerms, ScheduleEntry, YearTotals

from .errors import RuleError

PAYMENT_DOES_NOT_COVER_INTEREST = "loan.payment_does_not_cover_interest"
TERM_TOO_LONG = "loan.term_too_long"

MAX_PAYMENTS = 1200  # 100 years of monthly payments

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
    # First day at the follow-up rate; None when the rate never changes.
    switch_on = None if terms.fixed_until is None else terms.fixed_until + timedelta(days=1)
    follow_up = terms.nominal_rate if terms.follow_up_rate is None else terms.follow_up_rate
    start, first_days = _first_period(terms)
    number = 0
    while balance > 0:
        number += 1
        if number > MAX_PAYMENTS:
            raise RuleError(
                TERM_TOO_LONG,
                f"balance {balance} is not repaid after {MAX_PAYMENTS} payments",
                at_number=number,
            )
        due_on = _add_months(terms.first_payment_on, number - 1)
        days = first_days if number == 1 else 30
        old_days = _days_at_old_rate(start, due_on, days, switch_on)
        # Divide last: days/360 is a repeating decimal, and rounding it first can push an
        # exact half cent (e.g. 50.205) just below the tie, so half-up would round it down.
        accrued = balance * (terms.nominal_rate * old_days + follow_up * (days - old_days))
        interest = (accrued / 360).quantize(CENT, rounding=ROUND_HALF_UP)
        rate = terms.nominal_rate if switch_on is None or due_on <= switch_on else follow_up
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
            interest_days=days,
            follow_up_days=days - old_days,
            opening_balance=balance,
            interest=interest,
            principal=principal,
            payment=this_payment,
            closing_balance=closing,
        )
        balance = closing
        start = due_on


def _first_period(terms: LoanTerms) -> tuple[date, int]:
    """Start and 30E/360 days of period 1."""
    if terms.interest_only_before_first_payment:
        month_before = _add_months(terms.first_payment_on, -1)
        if terms.disbursed_on <= month_before:
            return month_before, 30
    return terms.disbursed_on, _days360(terms.disbursed_on, terms.first_payment_on)


def _days_at_old_rate(start: date, due_on: date, days: int, switch_on: date | None) -> int:
    """Days of the period [start, due_on) at nominal_rate; switch_on is the first follow-up day.

    Decided by date first: days360 between clamped month-end dates is not the period's fixed
    30 days (Jan 30 -> Feb 28 measures 28, Feb 28 -> Mar 30 measures 32), so measuring the
    old part from `start` would leak phantom days to the wrong rate. The follow-up part is
    measured from the switch to the due date, where every day is a real day after fixed_until;
    the old rate takes the rest of the period.
    """
    if switch_on is None or switch_on >= due_on:
        return days
    if switch_on <= start:
        return 0
    return days - min(days, _days360(switch_on, due_on))


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


def _add_months(base: date, months: int) -> date:
    """Same day of month as `base`, clamped to the target month's last day."""
    month_index = base.month - 1 + months
    year, month = base.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(base.day, calendar.monthrange(year, month)[1]))


def _days360(start: date, end: date) -> int:
    """30E/360 (Eurobond basis): day 31 counts as day 30 at either end."""
    d1, d2 = min(start.day, 30), min(end.day, 30)
    return (end.year - start.year) * 360 + (end.month - start.month) * 30 + (d2 - d1)
