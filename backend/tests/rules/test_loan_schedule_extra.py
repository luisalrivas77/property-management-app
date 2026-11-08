"""Edge cases for hausbuch.rules.loan_schedule beyond loan_schedule.yaml."""

import decimal
from datetime import date
from decimal import Decimal as D

import pytest

from hausbuch.rules.errors import RuleError
from hausbuch.rules.loan_schedule import (
    PAYMENT_DOES_NOT_COVER_INTEREST,
    TERM_TOO_LONG,
    build_schedule,
)
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


def test_first_period_half_cent_tie_rounds_up():
    """4 days of 30E/360 on 125,512.50 at 3.6 % is exactly 50.205: half-up gives 50.21.

    Rounding days/360 before multiplying once pushed this just below the tie (50.20).
    """
    terms = LoanTerms(
        principal=D("125512.50"),
        nominal_rate=D("0.036"),
        monthly_payment=D("1400.00"),
        disbursed_on=date(2025, 1, 8),
        first_payment_on=date(2025, 1, 12),
    )
    assert build_schedule(terms).entries[0].interest == D("50.21")


@pytest.mark.parametrize("base_day", [1, 15, 28, 29, 30, 31])
@pytest.mark.parametrize("fixed_until_day", [(2, 27), (2, 28), (3, 28), (3, 30), (6, 15), (6, 30)])
def test_a_row_at_the_old_rate_is_a_full_month_at_the_old_rate(base_day, fixed_until_day):
    """If a regular period shows the old rate, none of its days accrued at the new one.

    This invariant failed for payments on the 29th-31st when the fixed rate ended just
    before a clamped February due date (phantom follow-up days).
    """
    first = date(2025, 1, min(base_day, 31))
    terms = LoanTerms(
        principal=D("200000.00"),
        nominal_rate=D("0.036"),
        monthly_payment=D("1400.00"),
        disbursed_on=date(2024, 12, min(base_day, 31)),
        first_payment_on=first,
        fixed_until=date(2025, *fixed_until_day),
        follow_up_rate=D("0.05"),
    )
    for e in build_schedule(terms).entries[1:24]:
        if e.rate == D("0.036"):
            expected = (e.opening_balance * D("0.036") * 30 / 360).quantize(D("0.01"))
            assert e.interest == expected, (e.number, e.due_on)


@pytest.mark.parametrize("base_day", [1, 15, 28, 29, 30, 31])
@pytest.mark.parametrize("fixed_until_day", [(2, 27), (2, 28), (3, 28), (3, 30), (6, 15), (6, 30)])
def test_every_row_recomputes_its_interest_from_its_own_fields(base_day, fixed_until_day):
    """Spec amendment 1: interest_days and follow_up_days make each row traceable."""
    nominal, follow_up = D("0.036"), D("0.05")
    terms = LoanTerms(
        principal=D("200000.00"),
        nominal_rate=nominal,
        monthly_payment=D("1400.00"),
        disbursed_on=date(2024, 12, base_day),
        first_payment_on=date(2025, 1, base_day),
        fixed_until=date(2025, *fixed_until_day),
        follow_up_rate=follow_up,
    )
    entries = build_schedule(terms).entries
    for e in entries:
        old_days = e.interest_days - e.follow_up_days
        accrued = e.opening_balance * (nominal * old_days + follow_up * e.follow_up_days)
        expected = (accrued / 360).quantize(D("0.01"), rounding=decimal.ROUND_HALF_UP)
        assert e.interest == expected, (e.number, e.due_on)
    # The switch is crossed exactly once: no follow-up days before it, only after it.
    split = [e.follow_up_days for e in entries[1:]]
    assert split == sorted(split)
    assert split[0] == 0 and split[-1] == 30
    assert all(e.interest_days == 30 for e in entries[1:])


def test_first_period_days_are_recorded():
    s = build_schedule(terms(disbursed_on=date(2027, 10, 16), first_payment_on=date(2027, 11, 30)))
    assert (s.entries[0].interest_days, s.entries[0].follow_up_days) == (44, 0)


def test_term_cap_allows_exactly_1200_payments():
    # 0 %, so 120,000.00 at 100.00 a month is exactly 1,200 payments.
    s = build_schedule(
        terms(principal=D("120000.00"), nominal_rate=D("0"), monthly_payment=D("100.00"))
    )
    assert len(s.entries) == 1200
    assert s.entries[-1].number == 1200
    assert (s.entries[-1].payment, s.entries[-1].closing_balance) == (D("100.00"), 0)


def test_term_cap_fires_at_payment_1201():
    # One cent more than the case above needs a 1,201st payment.
    with pytest.raises(RuleError) as excinfo:
        build_schedule(
            terms(principal=D("120000.01"), nominal_rate=D("0"), monthly_payment=D("100.00"))
        )
    assert (excinfo.value.code, excinfo.value.at_number) == (TERM_TOO_LONG, 1201)


@pytest.mark.parametrize(
    ("disbursed_on", "flag", "days"),
    [
        # First payment 30 Mar; one month before is 28 Feb (clamped), which days360 counts as
        # 32 days. Paid out that day with the flag: the period is the usual 30.
        (date(2027, 2, 28), True, 30),
        (date(2027, 2, 28), False, 32),
        # Paid out earlier with the flag: 30, from 28 Feb; without it, from the payout.
        (date(2026, 12, 30), True, 30),
        (date(2026, 12, 30), False, 90),
        # Paid out after 28 Feb: no interest-only time, the flag changes nothing.
        (date(2027, 3, 10), True, 20),
    ],
)
def test_interest_only_first_period(disbursed_on, flag, days):
    first = build_schedule(
        terms(
            principal=D("1000.00"),
            disbursed_on=disbursed_on,
            first_payment_on=date(2027, 3, 30),
            interest_only_before_first_payment=flag,
        )
    ).entries[0]
    assert first.interest_days == days
    assert first.interest == (D("1000.00") * D("0.036") * days / 360).quantize(D("0.01"))
