"""The period a payment pays for (D27; tests/fixtures/phase2/service_period.yaml).

service_period(purpose, booking_date, recurring), first rule that applies:
  1. a range of months in the purpose: MM/YY-MM/YY or MM/YYYY-MM/YYYY -> the first day of its
     first month to the last day of its last month;
  2. one month in the purpose, leftmost of: MM/YYYY, MM.YYYY, YYYY-MM, German month name +
     YYYY (case-insensitive) -> that month;
  3. a bare four-digit year (2000-2100) -> that calendar year;
  4. a recurring line -> its booking month;
  5. else None.
The period stands on its own when the purpose states one: January's rent paid on 30 December
is January's (the §11 ten-day rule in phase 3 reads both dates). Digits that belong to a longer
number or a full date (01.03.2027) are not read as a month or a year. A debit card's expiry
in a Sparkasse card line ("Debitk.1 2028-12", phase 6 D160) is not a period the payment is
for, so it is removed before reading.
"""

import calendar
import re
from datetime import date

from hausbuch.schemas.allocation import ServicePeriod

YEAR_MIN = 2000
YEAR_MAX = 2100

_MONTHS = {
    "januar": 1,
    "februar": 2,
    "märz": 3,
    "maerz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}

# Not preceded by a digit or by "digit." / "digit/" (the day or month of a longer date), not
# followed by a digit or by ".digit" / "/digit" / "-digit".
_START = r"(?<!\d)(?<!\d[./])"
_END = r"(?![./-]?\d)"

_RANGE = re.compile(
    _START + r"(\d{1,2})/(\d{4}|\d{2})\s*-\s*(\d{1,2})/(\d{4}|\d{2})" + _END,
)
_MONTH = re.compile(
    _START
    + r"(?:(?P<mm>\d{1,2})[/.](?P<yyyy>\d{4})"
    + r"|(?P<iso_y>\d{4})-(?P<iso_m>\d{1,2})"
    + r"|(?P<name>"
    + "|".join(_MONTHS)
    + r")\s+(?P<name_y>\d{4}))"
    + _END,
    re.IGNORECASE,
)
_YEAR = re.compile(_START + r"(\d{4})" + _END)
# A card and its expiry month, as a Sparkasse card purpose ends: "Debitk.1 2028-12".
_CARD_EXPIRY = re.compile(r"\b(?:Debitk|Kreditk)\.\s*\d+\s+\d{4}-\d{1,2}" + _END, re.IGNORECASE)


def service_period(purpose: str, booking_date: date, recurring: bool) -> ServicePeriod | None:
    text = _CARD_EXPIRY.sub(" ", purpose or "")
    for m in _RANGE.finditer(text):
        first = _month(m.group(1), m.group(2))
        last = _month(m.group(3), m.group(4))
        if first and last and last >= first:
            return _months(first, last)
    for m in _MONTH.finditer(text):
        if m.group("mm"):
            found = _month(m.group("mm"), m.group("yyyy"))
        elif m.group("iso_y"):
            found = _month(m.group("iso_m"), m.group("iso_y"))
        else:
            found = _month(str(_MONTHS[m.group("name").casefold()]), m.group("name_y"))
        if found:
            return _months(found, found)
    for m in _YEAR.finditer(text):
        if (year := _year(m.group(1))) is not None:
            return ServicePeriod(start=date(year, 1, 1), end=date(year, 12, 31))
    if recurring:
        month = (booking_date.year, booking_date.month)
        return _months(month, month)
    return None


def months_in(period: ServicePeriod) -> int | None:
    """The number of whole calendar months the period covers, or None when it does not start
    on a month's first day and end on a month's last day."""
    start, end = period.start, period.end
    if start.day != 1 or end.day != calendar.monthrange(end.year, end.month)[1]:
        return None
    return (end.year - start.year) * 12 + end.month - start.month + 1


def _year(text: str) -> int | None:
    year = int(text) + 2000 if len(text) == 2 else int(text)
    return year if YEAR_MIN <= year <= YEAR_MAX else None


def _month(mm: str, yy: str) -> tuple[int, int] | None:
    year, month = _year(yy), int(mm)
    if year is None or not 1 <= month <= 12:
        return None
    return year, month


def _months(first: tuple[int, int], last: tuple[int, int]) -> ServicePeriod:
    (y1, m1), (y2, m2) = first, last
    return ServicePeriod(start=date(y1, m1, 1), end=date(y2, m2, calendar.monthrange(y2, m2)[1]))
