"""rules.proof_match (docs/phases/phase-3.md D143, D149, D150): how a payment proof's amount
fits a bill. Invented amounts."""

from decimal import Decimal as D

import pytest

from hausbuch.rules.proof_match import Fit, bill_parts, bill_total, proof_fit

pytestmark = pytest.mark.rule


@pytest.mark.parametrize(
    ("amount", "acquisition", "private", "want"),
    [
        # a private bill, paid in full; the bank's minus sign does not matter
        ("-476.00", [], ["357.00", "119.00"], Fit.WHOLE),
        ("476.01", [], ["357.00", "119.00"], Fit.WHOLE),
        ("476.02", [], ["357.00", "119.00"], None),
        # a split bill: the combined total, or its private part alone
        ("2630.00", ["2380.00"], ["250.00"], Fit.WHOLE),
        ("250.00", ["2380.00"], ["250.00"], Fit.WHOLE),
        # its purchase part alone, or one buyer's Grunderwerbsteuer row: a part
        ("2380.00", ["2380.00"], ["250.00"], Fit.PART),
        ("5500.00", ["5500.00", "5500.00"], [], Fit.PART),
        ("11000.00", ["5500.00", "5500.00"], [], Fit.WHOLE),
        # one Nebenkosten row alone is the whole bill
        ("1785.00", ["1785.00"], [], Fit.WHOLE),
        # a bill without any row has nothing to fit
        ("0.00", [], [], None),
    ],
)
def test_proof_fit(amount, acquisition, private, want):
    assert proof_fit(D(amount), [D(a) for a in acquisition], [D(p) for p in private]) is want


def test_bill_total_sums_every_row():
    assert bill_total([D("2380.00")], [D("250.00"), D("0.50")]) == D("2630.50")


@pytest.mark.parametrize(
    ("amount", "acquisition", "private", "parts", "want"),
    [
        # D150: one document holds both buyers' notices, read as two lines, one row of the
        # total: one buyer's proof pays one line, a part; both together the whole
        ("5687.00", ["11374.00"], [], ["5687.00", "5687.00"], Fit.PART),
        ("-5687.01", ["11374.00"], [], ["5687.00", "5687.00"], Fit.PART),
        ("11374.00", ["11374.00"], [], ["5687.00", "5687.00"], Fit.WHOLE),
        ("5687.02", ["11374.00"], [], ["5687.00", "5687.00"], None),
        # without the lines, as before D150: no fit
        ("5687.00", ["11374.00"], [], [], None),
        # lines that do not sum to the bill's total are no parts
        ("5687.00", ["11374.00"], [], ["5687.00", "5600.00"], None),
        ("5687.00", ["11374.00"], [], ["5687.00"], None),
        # the total within a cent of the lines' sum still counts
        ("5687.00", ["11374.01"], [], ["5687.00", "5687.00"], Fit.PART),
        # a single line equal to the total is the whole bill
        ("1785.00", ["1785.00"], [], ["1785.00"], Fit.WHOLE),
        # one buyer's Grunderwerbsteuer row and its line: still a part
        ("5500.00", ["5500.00", "5500.00"], [], ["5500.00", "5500.00"], Fit.PART),
        # a private bill's line (a net line and its VAT line sum to the total)
        ("100.00", [], ["119.00"], ["100.00", "19.00"], Fit.PART),
        # a credit line is no part: its magnitude never fits
        ("10.00", ["90.00"], [], ["100.00", "-10.00"], None),
        # a bill without any row has nothing to fit, lines or not
        ("5.00", [], [], ["5.00"], None),
    ],
)
def test_proof_fit_with_parts(amount, acquisition, private, parts, want):
    got = proof_fit(
        D(amount),
        [D(a) for a in acquisition],
        [D(p) for p in private],
        parts=[D(p) for p in parts],
    )
    assert got is want


def test_bill_parts_only_when_they_sum_to_the_total():
    halves = [D("5687.00"), D("5687.00")]
    assert bill_parts(halves, [D("11374.00")], []) == halves
    assert bill_parts([D("5687.00"), D("5000.00")], [D("11374.00")], []) == []
    assert bill_parts([], [D("11374.00")], []) == []
    # a credit line counts in the sum but is no part
    assert bill_parts([D("100.00"), D("-10.00")], [D("90.00")], []) == [D("100.00")]
