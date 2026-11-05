"""Edge cases for hausbuch.rules.loan_schedule beyond loan_schedule.yaml."""

import decimal
from datetime import date
from decimal import Decimal as D

import pytest

from hausbuch.rules.errors import RuleError
from hausbuch.rules.loan_schedule import PAYMENT_DOES_NOT_COVER_INTEREST, build_schedule
from hausbuch.schemas.loan import LoanTerms


def terms(**overrides) -> LoanTerms:
    base = {
        "principal": D("20000.00"),
        "nominal_rate": D("0.036"),
        "monthly_payment": D("1000.00"),
        "disbursed_on": date(2027, 10, 30),
        "first_payment_on": date(2027, 11, 30),
    }
    return LoanTerms(**(base | overrides))


def test_leap_february_clamp_then_back_to_the_30th():
    due = [e.due_on for e in build_schedule(terms()).entries[:6]]
    assert due == [
        date(2027, 11, 30),
        date(2027, 12, 30),
        date(2028, 1, 30),
        date(2028, 2, 29),
        date(2028, 3, 30),
        date(2028, 4, 30),
    ]


def test_base_day_31_follows_each_month_end():
    s = build_schedule(terms(disbursed_on=date(2027, 12, 31), first_payment_on=date(2028, 1, 31)))
    assert [e.due_on for e in s.entries[:4]] == [
        date(2028, 1, 31),
        date(2028, 2, 29),
        date(2028, 3, 31),
        date(2028, 4, 30),
    ]


@pytest.mark.parametrize(
    ("disbursed_on", "first_payment_on", "interest"),
    [
        (date(2025, 1, 31), date(2025, 3, 31), D("300.00")),  # 60 days (actual: 59)
        (date(2025, 1, 31), date(2025, 3, 15), D("225.00")),  # 45 days (uncapped: 44)
        (date(2025, 3, 1), date(2025, 3, 31), D("145.00")),  # 29 days (uncapped: 30)
    ],
)
def test_first_period_30e360_counts_the_31st_as_the_30th(disbursed_on, first_payment_on, interest):
    # 36000 * 5 % * days / 360 = 5.00 per day
    s = build_schedule(
        terms(
            principal=D("36000.00"),
            nominal_rate=D("0.05"),
            disbursed_on=disbursed_on,
            first_payment_on=first_payment_on,
        )
    )
    assert s.entries[0].interest == interest


def test_interest_rounds_half_up_at_exactly_half_a_cent():
    # Period 1: 1000 * 6 % * 30/360 = 5.00, leaving 5.00. Period 2: 5.00 * 0.005 = 0.025,
    # which half-up makes 0.03 (half-even and truncation would give 0.02).
    s = build_schedule(
        terms(
            principal=D("1000.00"),
            nominal_rate=D("0.06"),
            disbursed_on=date(2025, 1, 1),
            first_payment_on=date(2025, 2, 1),
        )
    )
    last = s.entries[1]
    assert (last.interest, last.payment, last.closing_balance) == (D("0.03"), D("5.03"), 0)


def test_payment_due_on_fixed_until_keeps_the_old_rate():
    s = build_schedule(
        terms(
            disbursed_on=date(2025, 1, 1),
            first_payment_on=date(2025, 2, 1),
            fixed_until=date(2025, 3, 1),
            follow_up_rate=D("0.08"),
        )
    )
    assert [e.rate for e in s.entries[:3]] == [D("0.036"), D("0.036"), D("0.08")]


def test_payoff_exactly_equal_to_the_payment():
    # 0 % so the last balance is exactly one payment.
    s = build_schedule(terms(principal=D("3000.00"), nominal_rate=D("0")))
    assert [e.payment for e in s.entries] == [D("1000.00")] * 3
    assert s.entries[-1].closing_balance == 0


def test_interest_equal_to_payment_never_amortizes():
    with pytest.raises(RuleError) as excinfo:
        build_schedule(
            terms(
                principal=D("100000.00"),
                nominal_rate=D("0.06"),
                monthly_payment=D("500.00"),
                disbursed_on=date(2025, 1, 1),
                first_payment_on=date(2025, 2, 1),
            )
        )
    assert (excinfo.value.code, excinfo.value.at_number) == (PAYMENT_DOES_NOT_COVER_INTEREST, 1)


def test_independent_of_the_callers_decimal_context():
    t = terms(principal=D("298998.50"), monthly_payment=D("1400.00"))
    expected = build_schedule(t)
    with decimal.localcontext(decimal.Context(prec=4, rounding=decimal.ROUND_FLOOR)):
        got = build_schedule(t)
    # Compare piecewise: a full-schedule diff of ~340 entries makes pytest's report crawl.
    assert got.years == expected.years
    assert len(got.entries) == len(expected.entries)
    assert (
        next((g for g, e in zip(got.entries, expected.entries, strict=True) if g != e), None)
        is None
    )
