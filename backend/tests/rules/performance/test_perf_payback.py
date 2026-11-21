"""rules.performance.payback by hand (Übersicht's property tile, Luis 21 Nov 2026): the running
sum of phase 4's net cash, the break-even year in a projected year and not by the sale year,
the signed percent, and the totals over several properties. Invented values only."""

import uuid
from datetime import date
from decimal import Decimal

from hausbuch.rules.performance import payback as rules
from hausbuch.rules.performance.sale import SaleFacts, sale
from hausbuch.rules.tax import registry
from hausbuch.schemas.performance import YearFigures, YearFlag, YearKind
from hausbuch.schemas.property import PropertyPaidIn

D = Decimal
ZERO = D("0.00")


def _year(year: int, net_cash: str, kind=YearKind.PROJECTED, flags=()) -> YearFigures:
    return YearFigures(
        year=year,
        kind=kind,
        flags=list(flags),
        rent_in=ZERO,
        other_income=ZERO,
        umlagen_in=ZERO,
        umlagen_out=ZERO,
        umlagen_net=ZERO,
        interest=ZERO,
        principal_repaid=ZERO,
        running_costs=ZERO,
        cash_out=ZERO,
        taxable_result=None,
        afa=None,
        tax_effect=None,
        net_cash=D(net_cash),
    )


def _paid(paid_in: str | None, label: str = "Wohnung A") -> PropertyPaidIn:
    return PropertyPaidIn(
        property_id=uuid.uuid4(),
        label=label,
        purchase_price=D("300000.00"),
        acquisition_costs=ZERO,
        own_loan_costs=ZERO,
        loan_principal=ZERO,
        paid_in=None if paid_in is None else D(paid_in),
    )


def _sold(value: str, balance: str):
    """sale() outside §23 (no contract date): net = value − 3.57 % − balance, no tax."""
    return sale(
        SaleFacts(
            sale_year=2026,
            purchase_date=date(2026, 7, 1),
            tax_free_from=None,
            purchase_price=D("300000.00"),
            cost_rows=ZERO,
            loan_principal=ZERO,
            value=D(value),
            selling_costs_rate=D("0.0357"),
            afa_claimed_building=ZERO,
            afa_claimed_movable=ZERO,
            loan_balance=D(balance),
            zve_base=None,
            result_sale_year=ZERO,
            shares=[],
            profiles=[],
            cash_flows=[],
            freigrenze=D("1000.00"),
        ),
        registry.tariff(2026),
    )


ACTUAL = [
    _year(2025, "-800.00", YearKind.ACTUAL, [YearFlag.TAX_ESTIMATED]),
    _year(2026, "300.00", YearKind.ACTUAL_PARTIAL, [YearFlag.SO_FAR, YearFlag.INCOME_MISSING]),
]
PROJECTED = [_year(2027, "4000.00"), _year(2028, "4000.00"), _year(2029, "4000.00")]


def test_the_break_even_year_is_the_first_whose_running_sum_reaches_paid_in():
    """−800 + 300 = −500 so far; then 3,500, 7,500, 11,500: 10,000.00 is back in 2029.
    −500 ÷ 10,000 = −5.0 %. Sale: 300,000 − 10,710 − 250,000 = 39,290.00 net;
    profit = 39,290.00 − 500.00 − 10,000.00 = 28,790.00."""
    got = rules.payback(
        _paid("10000.00"), ACTUAL, PROJECTED, 2029, _sold("300000.00", "250000.00"), D("0.0357")
    )
    assert [(y.year, y.cumulative) for y in got.years] == [
        (2025, D("-800.00")),
        (2026, D("-500.00")),
        (2027, D("3500.00")),
        (2028, D("7500.00")),
        (2029, D("11500.00")),
    ]
    assert (got.cash_back, got.percent, got.break_even_year, got.sale_year) == (
        D("-500.00"),
        D("-5.0"),
        2029,
        2029,
    )
    assert (got.tax_estimated_years, got.income_missing_years) == ([2025], [2026])
    assert (got.sale_today.net_proceeds, got.sale_today.profit) == (
        D("39290.00"),
        D("28790.00"),
    )


def test_not_back_by_the_sale_year():
    got = rules.payback(
        _paid("20000.00"), ACTUAL, PROJECTED, 2029, _sold("300000.00", "0.00"), D("0.0357")
    )
    assert (got.break_even_year, got.sale_year) == (None, 2029)
    assert got.percent == D("-2.5")  # −500 ÷ 20,000


def test_nothing_put_in_has_no_percent():
    """A loan above price and costs: paid_in −1,000.00. No percent; the break-even year is
    still the first whose running sum is not below paid_in: −800.00 ≥ −1,000.00 in 2025."""
    got = rules.payback(_paid("-1000.00"), ACTUAL, [], 2027, _sold("300000.00", "0.00"), D("0"))
    assert got.percent is None
    assert got.break_even_year == 2025


def test_totals_over_two_properties_and_one_left_out():
    """A: 10,000.00 in, running −800, −500, 3,500, 7,500, 11,500 (2025–2029).
    B: 2,000.00 in, running 1,000 (2026), 2,500 (2027), held at 2,500 after its sale year.
    Together 12,000.00 in: 2025 −800, 2026 500, 2027 6,000, 2028 10,000, 2029 14,000 → 2029.
    Cash back −500 + 1,000 = 500.00; 500 ÷ 12,000 = 4.2 %."""
    a = rules.payback(
        _paid("10000.00"), ACTUAL, PROJECTED, 2029, _sold("300000.00", "250000.00"), D("0.0357")
    )
    b = rules.payback(
        _paid("2000.00", "Wohnung B"),
        [_year(2026, "1000.00", YearKind.ACTUAL_PARTIAL, [YearFlag.TAX_ESTIMATED])],
        [_year(2027, "1500.00")],
        2027,
        _sold("200000.00", "190000.00"),
        D("0.0357"),
    )
    gone = rules.left_out(_paid(None, "Wohnung C"), "ownership_ended")
    got = rules.total([a, b, gone])
    # B: net 200,000 − 7,140 − 190,000 = 2,860.00; profit 2,860 + 1,000 − 2,000 = 1,860.00
    assert b.sale_today.profit == D("1860.00")
    assert (got.paid_in, got.cash_back, got.percent) == (D("12000.00"), D("500.00"), D("4.2"))
    assert got.profit_today == D("30650.00")  # 28,790.00 + 1,860.00
    assert (got.break_even_year, got.sale_year) == (2029, 2029)
    assert (got.tax_estimated_years, got.income_missing_years) == ([2025, 2026], [2026])
    assert got.properties[2].reason == "ownership_ended"


def test_totals_without_any_property():
    got = rules.total([])
    assert (got.paid_in, got.cash_back, got.percent, got.break_even_year) == (
        ZERO,
        ZERO,
        None,
        None,
    )
