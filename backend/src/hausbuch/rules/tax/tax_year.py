"""The tax year of a payment: §11 EStG with the Fälligkeit requirement (phase 3 §5.1, D54).

1. The payment date is the bank's booking date; the year paid is its year.
2. Only a recurring payment whose service period lies within one calendar year E has an
   economic year; no period, or one across a year end, means no shift.
3. The short period is `short_period_days` around the year end (10: 22-31 Dec and 1-10 Jan).
   A payment booked in the short period before E (22-31 Dec of E-1) or after it (1-10 Jan of
   E+1) counts in E only when it was also *due* in that same short period (BFH X R 2/21 of
   16 Feb 2022): a December rent due on the 3rd and paid on 5 January stays in January's year.
4. due_on is resolved by the service (loan schedule, tenancy.rent_due_day, the period's first
   day); the rule never guesses one. A candidate without it keeps the year paid, noted.
5. A value date in another year than the booking date is noted; the year does not change.

The answer depends on seven plain values only (booking date, value date, recurring, the service
period's ends, due date, short_period_days), so it is memoised on them (_resolve): one draft
asks for the same payment's year once per rule and per year it sums, and a projection runs a
draft per projected year (a payback request ran this ~193,000 times, 21 Nov 2026). The cache
holds immutable tuples only; tax_year_of builds a new result, with its own notes list, for each
caller.
"""

from datetime import date, timedelta
from functools import lru_cache

from hausbuch.schemas.tax import PaymentFacts, RuleSet, TaxYearNote, TaxYearResult


def short_period(year_end: int, days: int) -> tuple[date, date]:
    """The short period around the end of `year_end`: its last `days` days and the next
    year's first `days` days, both ends inclusive."""
    return (
        date(year_end, 12, 31) - timedelta(days=days - 1),
        date(year_end + 1, 1, 1) + timedelta(days=days - 1),
    )


def _economic_year(recurring: bool, start: date | None, end: date | None) -> int | None:
    if not recurring or start is None or end is None or start.year != end.year:
        return None
    return start.year


def economic_year(payment: PaymentFacts) -> int | None:
    """E: the one calendar year a recurring payment's service period lies in, else None."""
    period = payment.service_period
    if period is None:
        return None
    return _economic_year(payment.recurring, period.start, period.end)


@lru_cache(maxsize=1 << 16)
def _resolve(
    booked: date,
    value_date: date | None,
    recurring: bool,
    period_start: date | None,
    period_end: date | None,
    due_on: date | None,
    short_period_days: int,
) -> tuple[int, str, tuple[TaxYearNote, ...]]:
    """Steps 1-5 on the values they read: (year, rule, notes)."""
    paid_year = booked.year
    notes: list[TaxYearNote] = []
    if value_date is not None and value_date.year != paid_year:
        notes.append(TaxYearNote.YEAR_END_STRADDLE)

    e = _economic_year(recurring, period_start, period_end)
    if e is not None and abs(e - paid_year) == 1:
        # The year end between the year paid and E.
        year_end = min(e, paid_year)
        start, end = short_period(year_end, short_period_days)
        if start <= booked <= end:
            if due_on is None:
                notes.append(TaxYearNote.DUE_DATE_MISSING)
            elif start <= due_on <= end:
                return e, "short_period_shift", tuple(notes)
    return paid_year, "paid", tuple(notes)


def _values(payment: PaymentFacts, rule_set: RuleSet) -> tuple:
    period = payment.service_period
    return (
        payment.booking_date,
        payment.value_date,
        payment.recurring,
        None if period is None else period.start,
        None if period is None else period.end,
        payment.due_on,
        rule_set.short_period_days,
    )


def tax_year(payment: PaymentFacts, rule_set: RuleSet) -> int:
    """tax_year_of(payment, rule_set).year, without building the result."""
    return _resolve(*_values(payment, rule_set))[0]


def tax_year_of(payment: PaymentFacts, rule_set: RuleSet) -> TaxYearResult:
    year, rule, notes = _resolve(*_values(payment, rule_set))
    return TaxYearResult(year=year, rule=rule, notes=list(notes))
