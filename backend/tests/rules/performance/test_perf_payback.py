"""rules.performance.payback by hand (Übersicht's property tile, Luis 21 Nov 2026): the running
sum of phase 4's net cash, the break-even year in a projected year and not by the sale year,
the signed percent, and the totals over several properties. Invented values only."""

import uuid
from datetime import date
from decimal import Decimal

from hausbuch.rules.performance import payback as rules
from hausbuch.rules.performance.sale import SaleFacts, sale
from hausbuch.rules.tax import registry
from hausbuch.schemas.performance import Assumption, TaxEffect, YearFigures, YearFlag, YearKind
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


def test_nothing_put_in_has_no_percent_and_no_break_even():
    """A loan above price and costs: paid_in −1,000.00. Nothing was put in to earn back, so
    there is no percent and no break-even year (not 2025, whose running sum −800.00 is merely
    not below −1,000.00), and the totals over it have none either. The screen reads the missing
    percent as „no break-even line“, so its sale year never shows as „nicht zurück“."""
    got = rules.payback(_paid("-1000.00"), ACTUAL, [], 2027, _sold("300000.00", "0.00"), D("0"))
    assert got.percent is None
    assert got.break_even_year is None
    assert rules.total([got]).break_even_year is None


def test_a_failed_projection_keeps_the_cash_back_and_the_sale():
    """The projection raised (a stale default scenario): no projected years, no sale year and
    no break-even year; the cash back (−500.00 of 1,000.00, −50.0 %) and the sale today stay.
    The totals state no break-even year and no sale year although the other property is back
    in 2026: without the first one's projected years the sum cannot say when both are back,
    nor that they are not back by a year."""
    got = rules.payback(
        _paid("1000.00"),
        ACTUAL,
        [],
        None,
        _sold("300000.00", "250000.00"),
        D("0.0357"),
        projection_error_code="scenario.sale_year_before_today",
    )
    assert (got.cash_back, got.percent) == (D("-500.00"), D("-50.0"))
    assert [y.year for y in got.years] == [2025, 2026]
    assert (got.break_even_year, got.sale_year) == (None, None)
    assert got.sale_today is not None and got.reason is None
    assert got.projection_error_code == "scenario.sale_year_before_today"
    other = rules.payback(
        _paid("2000.00", "Wohnung B"),
        [_year(2026, "3000.00", YearKind.ACTUAL_PARTIAL)],
        [],
        2030,
        _sold("200000.00", "190000.00"),
        D("0.0357"),
    )
    assert other.break_even_year == 2026
    total = rules.total([got, other])
    assert (total.cash_back, total.break_even_year, total.sale_year) == (D("2500.00"), None, None)


def test_the_sale_tax_is_flagged_only_when_a_taxable_gain_had_no_income():
    without = [Assumption(code="sale.tax_without_income")]
    sold = _sold("300000.00", "0.00")
    gain = sold.model_copy(update={"taxable": True, "gain": D("1000.00")})
    loss = sold.model_copy(update={"taxable": True, "gain": D("-1000.00")})
    assert rules.tax_not_computed(gain, without)
    assert not rules.tax_not_computed(gain, [])
    assert not rules.tax_not_computed(loss, without)
    assert not rules.tax_not_computed(sold, without)  # outside §23: no tax whatever the income
    got = rules.payback(
        _paid("1000.00"), ACTUAL, [], 2027, gain, D("0.0357"), sale_assumptions=without
    )
    assert got.sale_today.tax_not_computed


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


def test_a_loan_without_a_value_names_its_loan():
    loan = uuid.uuid4()
    got = rules.left_out(_paid("1000.00"), "loan_not_valued", loan_id=loan)
    assert (got.reason, got.loan_id, got.cash_back) == ("loan_not_valued", loan, None)
    assert rules.total([got]).paid_in == ZERO  # left out of the totals


def _taxed(
    year: int, net_cash: str, effect: str, source: str, carried: bool = False
) -> YearFigures:
    """An actual year with a tax effect: cash before tax = net_cash + effect."""
    return _year(year, net_cash, YearKind.ACTUAL).model_copy(
        update={
            "tax_effect": TaxEffect(effect=D(effect), source=source, carried_forward=carried),
            "flags": [] if source == "bescheid" else [YearFlag.TAX_ESTIMATED],
        }
    )


def test_the_cash_back_splits_into_arrived_and_the_expected_refund():
    """Phase 10 §7.6 (d). 2024: −1,500 before tax, its own Bescheid saves 2,000 → net 500,
    all arrived. 2025: −1,000 before tax, the estimate saves 1,800 → net 800; −1,000 arrived,
    1,800 expected. 2026: 200 before tax, a Bescheid of 2025 carried forward costs 100 →
    net 100; 200 arrived, −100 expected (a back-payment). 2023: income missing, net −50 all
    arrived. arrived = 500 − 1,000 + 200 − 50 = −350.00; expected = 1,800 − 100 = 1,700.00;
    together 1,350.00 = cash_back. percent and break-even stay on cash_back."""
    actual = [
        _year(2023, "-50.00", YearKind.ACTUAL, [YearFlag.INCOME_MISSING]),
        _taxed(2024, "500.00", "-2000.00", "bescheid"),
        _taxed(2025, "800.00", "-1800.00", "estimate"),
        _taxed(2026, "100.00", "100.00", "bescheid", carried=True),
    ]
    got = rules.payback(
        _paid("10000.00"), actual, [], 2030, _sold("300000.00", "250000.00"), D("0.0357")
    )
    assert (got.cash_back, got.arrived, got.expected_tax_refund) == (
        D("1350.00"),
        D("-350.00"),
        D("1700.00"),
    )
    assert got.arrived + got.expected_tax_refund == got.cash_back
    assert got.percent == D("13.5")  # 1,350 ÷ 10,000: both parts together
    assert [(y.cash_before_tax, y.tax_refund, y.tax_assessed) for y in got.years] == [
        (D("-50.00"), None, False),
        (D("-1500.00"), D("2000.00"), True),
        (D("-1000.00"), D("1800.00"), False),
        (D("200.00"), D("-100.00"), False),
    ]
    assert all(y.cash_before_tax + (y.tax_refund or ZERO) == y.net_cash for y in got.years)
    other = rules.payback(
        _paid("2000.00", "Wohnung B"),
        [_taxed(2026, "300.00", "-400.00", "override")],
        [],
        2030,
        _sold("200000.00", "190000.00"),
        D("0.0357"),
    )
    total = rules.total([got, other])
    assert (total.cash_back, total.arrived, total.expected_tax_refund) == (
        D("1650.00"),
        D("-450.00"),  # −350 + (300 − 400)
        D("2100.00"),  # 1,700 + 400
    )
    assert total.arrived + total.expected_tax_refund == total.cash_back


def test_without_a_tax_effect_everything_has_arrived():
    got = rules.payback(
        _paid("10000.00"), ACTUAL, PROJECTED, 2029, _sold("300000.00", "250000.00"), D("0.0357")
    )
    assert (got.arrived, got.expected_tax_refund) == (D("-500.00"), ZERO)
    assert rules.total([]).arrived == rules.total([]).expected_tax_refund == ZERO
