"""Phase 10 §7.6 Anlagen, items 2 and 3: a portfolio's gain since the start, its percent and its
money-weighted yearly return (rules/performance/portfolio.py). Every case is computed by hand;
the amounts are invented."""

from datetime import date
from decimal import Decimal

from hausbuch.rules.performance.irr import xirr
from hausbuch.rules.performance.portfolio import (
    Flow,
    NoRate,
    combined,
    external,
    performance,
)

D = Decimal


def test_one_payment_in_a_year_ago():
    # 1,000 paid in on 1 Jan 2025, worth 1,100 on 1 Jan 2026 (365 days): +100, 10 %, 10 % a year.
    flows = [Flow("depot", date(2025, 1, 1), D("1000.00"), "t1")]
    p = performance(flows, D("1100.00"), date(2026, 1, 1))
    assert p.start == date(2025, 1, 1)
    assert (p.paid_in, p.taken_out, p.gain) == (D("1000.00"), D("0.00"), D("100.00"))
    assert p.gain_ratio == D("0.1")
    assert p.yearly_return == D("0.1")
    assert p.no_rate is None


def test_less_than_a_year_shows_no_rate():
    # 1,000 paid in on 1 Jun 2026, worth 1,050 on 23 Nov 2026: +50, 5 %, no yearly rate.
    flows = [Flow("depot", date(2026, 6, 1), D("1000.00"), "t1")]
    p = performance(flows, D("1050.00"), date(2026, 11, 23))
    assert (p.gain, p.gain_ratio) == (D("50.00"), D("0.05"))
    assert p.yearly_return is None
    assert p.no_rate is NoRate.SHORT


def test_exactly_one_year_has_a_rate_one_day_less_has_none():
    flows = [Flow("x", date(2025, 3, 1), D("100.00"), "t1")]
    assert performance(flows, D("110.00"), date(2026, 3, 1)).yearly_return == D("0.1")
    assert performance(flows, D("110.00"), date(2026, 2, 28)).no_rate is NoRate.SHORT


def test_taken_out_counts_back():
    # 1,000 in on 1 Jan 2025, 1,100 taken out on 1 Jan 2026, nothing left: +100, 10 % a year.
    flows = [
        Flow("x", date(2025, 1, 1), D("1000.00"), "in"),
        Flow("x", date(2026, 1, 1), D("-1100.00"), "out"),
    ]
    p = performance(flows, D("0.00"), date(2026, 6, 1))
    assert (p.paid_in, p.taken_out, p.gain) == (D("1000.00"), D("1100.00"), D("100.00"))
    assert p.gain_ratio == D("0.1")
    assert p.yearly_return == D("0.1")


def test_rate_is_xirr_from_the_investors_side():
    # Two payments in and one out, plus today's value: the rate is xirr of −in, +out, +value.
    flows = [
        Flow("x", date(2024, 2, 1), D("500.00"), "a"),
        Flow("x", date(2024, 9, 1), D("300.00"), "b"),
        Flow("x", date(2025, 5, 1), D("-200.00"), "c"),
    ]
    today = date(2026, 2, 1)
    p = performance(flows, D("750.00"), today)
    assert p.gain == D("750.00") + D("200.00") - D("800.00")
    assert p.gain_ratio == D("0.1875")  # 150 ÷ 800
    assert p.yearly_return == xirr(
        [
            (date(2024, 2, 1), D("-500.00")),
            (date(2024, 9, 1), D("-300.00")),
            (date(2025, 5, 1), D("200.00")),
            (today, D("750.00")),
        ]
    )


def test_an_unpriced_flow_leaves_the_figures_unknown_never_zero():
    flows = [
        Flow("w", date(2025, 1, 1), D("1000.00"), "a"),
        Flow("w", date(2025, 2, 1), None, "b"),
    ]
    p = performance(flows, D("1500.00"), date(2026, 6, 1))
    assert (p.gain, p.gain_ratio, p.yearly_return) == (None, None, None)
    assert p.no_rate is NoRate.UNPRICED
    assert p.unpriced == 1


def test_no_payments_in_no_percent():
    p = performance([], D("50.00"), date(2026, 6, 1))
    assert p.gain == D("50.00")
    assert p.gain_ratio is None
    assert p.start is None
    assert p.no_rate is NoRate.NO_FLOWS


def test_a_move_between_two_own_portfolios_nets_out_in_the_total():
    # Depot A: 1,000 in from the bank on 1 Jan 2025; on 1 Jun 2025 units worth 400 move to depot
    # B. On 1 Jan 2026 A is worth 700 and B 450.
    #   A: paid in 1,000, taken out 400, value 700  → +100, 10 %
    #   B: paid in 400, value 450                    → +50, 12.5 %
    #   A and B: only the 1,000 from outside, value 1,150 → +150, 15 %, 15 % a year.
    flows = [
        Flow("A", date(2025, 1, 1), D("1000.00"), "bank-line", pair="bank-debit"),
        Flow("A", date(2025, 6, 1), D("-400.00"), "move-out", pair="move-in"),
        Flow("B", date(2025, 6, 1), D("400.00"), "move-in", pair="move-out"),
    ]
    values = {"A": D("700.00"), "B": D("450.00")}
    today = date(2026, 1, 1)
    a = combined(flows, values, {"A"}, today)
    b = combined(flows, values, {"B"}, today)
    both = combined(flows, values, {"A", "B"}, today)
    assert (a.paid_in, a.taken_out, a.gain, a.gain_ratio) == (
        D("1000.00"),
        D("400.00"),
        D("100.00"),
        D("0.1"),
    )
    assert (b.paid_in, b.gain, b.gain_ratio) == (D("400.00"), D("50.00"), D("0.125"))
    assert (both.paid_in, both.taken_out, both.gain) == (D("1000.00"), D("0.00"), D("150.00"))
    assert both.gain_ratio == D("0.15")
    assert both.yearly_return == D("0.15")
    assert a.gain + b.gain == both.gain


def test_an_unpriced_move_inside_the_set_does_not_block_the_total():
    # Coins moved from an exchange to a wallet on a day with no price: each portfolio's
    # figures are unknown, the set's are not (the move nets out).
    flows = [
        Flow("ex", date(2025, 1, 1), D("1000.00"), "dep"),
        Flow("ex", date(2025, 3, 1), None, "w-out", pair="w-in"),
        Flow("wal", date(2025, 3, 1), None, "w-in", pair="w-out"),
    ]
    values = {"ex": D("100.00"), "wal": D("1200.00")}
    today = date(2026, 1, 1)
    assert combined(flows, values, {"ex"}, today).gain is None
    both = combined(flows, values, {"ex", "wal"}, today)
    assert both.gain == D("300.00")
    assert both.unpriced == 0


def test_a_pair_whose_other_leg_is_no_flow_stays_external():
    flows = [Flow("A", date(2025, 1, 1), D("1000.00"), "dep", pair="bank-line")]
    assert external(flows, {"A"}) == flows


def test_a_pair_inside_one_portfolio_nets_out():
    # Coins between two addresses of one wallet: out and in of the same account.
    flows = [
        Flow("w", date(2025, 1, 1), D("1000.00"), "in1"),
        Flow("w", date(2025, 2, 1), D("-300.00"), "a", pair="b"),
        Flow("w", date(2025, 2, 1), D("299.00"), "b", pair="a"),
    ]
    p = combined(flows, {"w": D("1100.00")}, {"w"}, date(2026, 2, 1))
    assert (p.paid_in, p.taken_out, p.gain) == (D("1000.00"), D("0.00"), D("100.00"))
