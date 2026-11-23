"""Phase 10 §7.6 Anlagen: a gain's percent outside what GainRatio can hold is not shown
(services/portfolio_performance._figures), never a 500. The amounts are invented."""

from datetime import date
from decimal import Decimal

from hausbuch.rules.performance.portfolio import Flow, performance
from hausbuch.services.portfolio_performance import _figures

D = Decimal
ON = date(2026, 11, 23)


def _shown(paid_in: str, value: str):
    flows = [Flow("depot", date(2025, 1, 1), D(paid_in), "t1")]
    return _figures(performance(flows, D(value), ON))


def test_a_negative_value_loses_more_than_was_paid_in():
    # 100 paid in, a depot whose cash is overdrawn reads −4.17: gain −104.17, −104.17 %.
    figures = _shown("100.00", "-4.17")
    assert figures.gain_eur == D("-104.17")
    assert figures.gain_ratio is None


def test_a_cent_paid_in_makes_the_percent_boundless():
    # 0.01 paid in, worth 10,001: gain 10,000.99, a ratio above 1,000,000.
    figures = _shown("0.01", "10001.00")
    assert figures.gain_eur == D("10000.99")
    assert figures.gain_ratio is None


def test_the_edges_inside_the_range_are_kept():
    assert _shown("100.00", "0.00").gain_ratio == D("-1")
    assert _shown("1.00", "999999.00").gain_ratio == D("999998")
