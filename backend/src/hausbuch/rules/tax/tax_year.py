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
"""

from datetime import date, timedelta

from hausbuch.schemas.tax import PaymentFacts, RuleSet, TaxYearNote, TaxYearResult


def short_period(year_end: int, days: int) -> tuple[date, date]:
    """The short period around the end of `year_end`: its last `days` days and the next
    year's first `days` days, both ends inclusive."""
    return (
        date(year_end, 12, 31) - timedelta(days=days - 1),
        date(year_end + 1, 1, 1) + timedelta(days=days - 1),
    )


def economic_year(payment: PaymentFacts) -> int | None:
    """E: the one calendar year a recurring payment's service period lies in, else None."""
    period = payment.service_period
    if not payment.recurring or period is None or period.start.year != period.end.year:
        return None
    return period.start.year


def tax_year_of(payment: PaymentFacts, rule_set: RuleSet) -> TaxYearResult:
    booked = payment.booking_date
    paid_year = booked.year
    notes: list[TaxYearNote] = []
    if payment.value_date is not None and payment.value_date.year != paid_year:
        notes.append(TaxYearNote.YEAR_END_STRADDLE)

    e = economic_year(payment)
    if e is not None and abs(e - paid_year) == 1:
        # The year end between the year paid and E.
        year_end = min(e, paid_year)
        start, end = short_period(year_end, rule_set.short_period_days)
        if start <= booked <= end:
            if payment.due_on is None:
                notes.append(TaxYearNote.DUE_DATE_MISSING)
            elif start <= payment.due_on <= end:
                return TaxYearResult(year=e, rule="short_period_shift", notes=notes)
    return TaxYearResult(year=paid_year, rule="paid", notes=notes)
