"""rules.proof_match (docs/phases/phase-3.md D143, D149): how a payment proof's amount fits a
bill. Invented amounts."""

from decimal import Decimal as D

import pytest

from hausbuch.rules.proof_match import Fit, bill_total, proof_fit

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
