"""The way back to the own money put into a property (Übersicht's property tile, Luis 21 Nov
2026: „Both“). Pure, Decimal; it re-derives nothing: every year's figure is phase 4's
YearFigures.net_cash and the sale is phase 4's sale().

Definitions:

    cash_back       = Σ net_cash of the actual years (actual_years: the purchase year through
                      today's, today's year as far as booked). net_cash = rent in + other
                      income + Umlagen in − cash paid out − the tax effect
                      (rules.performance.year_figures). Cash paid out = interest + principal
                      (the loan's Tilgung, LOAN_PRINCIPAL) + running costs + Umlagen paid
                      out, so the Tilgung counts as money out: the own money put in is
                      measured against the loan's full principal, and every euro repaid since
                      is money out of pocket that the cash back must first earn back. The
                      investment flows (purchase price, acquisition costs, the loan's payout)
                      are not yearly cash. A rental bill paid privately is not in cash paid
                      out (phase 4 reads the rental account), only its tax saving is.
    percent         = cash_back ÷ paid_in × 100, one decimal half-up; None when paid_in ≤ 0
                      (nothing was put in to measure against).
    break_even_year = the first year y, from the purchase year to the projection's sale year,
                      with paid_in ≤ Σ net_cash of the years up to y: the actual years first,
                      then the default scenario's projected years (next January on). It may
                      be an actual year already. None when not by the sale year, when
                      paid_in ≤ 0 (nothing to earn back) and when the projection failed
                      (projection_error_code: the actual years alone cannot say it is not
                      reached).
    arrived         = the part of cash_back that has arrived (phase 10 §7.6 (d), Luis 23 Nov
                      2026): Σ cash before tax of the actual years (net_cash + the tax
                      effect) + the tax refund of the years whose effect is that year's own
                      Einkommensteuerbescheid (source bescheid, not carried forward from an
                      earlier year's).
    expected_tax_refund = the tax refund of the other actual years (an estimate, an override,
                      or a Bescheid carried from an earlier year): signed, positive for a
                      refund, negative for an expected back-payment. A year's tax refund is
                      −tax effect (a saving is a negative effect), 0 without an effect
                      (income missing). arrived + expected_tax_refund = cash_back exactly,
                      every part being whole cents. percent and break_even_year stay on
                      cash_back, both parts together.
    profit_today    = sale().net_proceeds (value − selling costs − §23 tax − loan balance,
                      for a sale in today's year at today's value and balance) + cash_back
                      − paid_in. sale().profit is not used: its E₀ lacks the loan costs paid
                      privately, which paid_in holds. tax_not_computed when the sale is
                      taxable with a gain and phase 4 stated sale.tax_without_income (no
                      income input): its sale_tax 0.00 is not a computed tax.
"""

from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID

from hausbuch.rules.tax.rounding import cents, one_decimal
from hausbuch.schemas.performance import (
    Assumption,
    PaybackReason,
    PaybackYear,
    PropertiesPayback,
    PropertyPayback,
    SaleResult,
    SaleToday,
    YearFigures,
    YearFlag,
)
from hausbuch.schemas.property import PropertyPaidIn

ZERO = Decimal("0.00")
HUNDRED = Decimal("100")


def percent(cash_back: Decimal, paid_in: Decimal) -> Decimal | None:
    """cash_back as a share of paid_in, one decimal; None when paid_in ≤ 0."""
    if paid_in <= 0:
        return None
    return one_decimal(cash_back * HUNDRED / paid_in)


def tax_refund(y: YearFigures) -> Decimal:
    """The money a year's tax effect brings: −effect (a saving is a negative effect), so
    positive for a refund and negative for a back-payment; 0 without an effect."""
    return ZERO if y.tax_effect is None else -y.tax_effect.effect


def tax_assessed(y: YearFigures) -> bool:
    """The year's tax effect is its own Einkommensteuerbescheid's: the money has arrived. An
    estimate, an override or an earlier year's Bescheid carried forward is still expected."""
    effect = y.tax_effect
    return effect is not None and effect.source == "bescheid" and not effect.carried_forward


def series(actual: Sequence[YearFigures], projected: Sequence[YearFigures]) -> list[PaybackYear]:
    """Every year's net cash, actual then projected, with its running sum, its cash before tax
    and its tax refund (net_cash = cash_before_tax + tax_refund)."""
    out = []
    running = ZERO
    for y in [*actual, *projected]:
        running += y.net_cash
        refund = tax_refund(y)
        out.append(
            PaybackYear(
                year=y.year,
                kind=y.kind,
                net_cash=y.net_cash,
                cumulative=running,
                cash_before_tax=y.net_cash - refund,
                tax_refund=refund if y.tax_effect is not None else None,
                tax_assessed=tax_assessed(y),
            )
        )
    return out


def arrived_and_expected(actual: Sequence[YearFigures]) -> tuple[Decimal, Decimal]:
    """(arrived, expected_tax_refund) of the actual years (the module doc)."""
    arrived = sum(
        (y.net_cash - tax_refund(y) + (tax_refund(y) if tax_assessed(y) else ZERO) for y in actual),
        ZERO,
    )
    expected = sum((tax_refund(y) for y in actual if not tax_assessed(y)), ZERO)
    return cents(arrived), cents(expected)


def break_even(years: Sequence[PaybackYear], paid_in: Decimal) -> int | None:
    """The first year whose running sum reaches paid_in, None when none does or when paid_in
    ≤ 0 (no own money put in: there is nothing to earn back)."""
    if paid_in <= 0:
        return None
    return next((y.year for y in years if y.cumulative >= paid_in), None)


def tax_not_computed(sold: SaleResult, assumptions: Sequence[Assumption]) -> bool:
    """The sale is taxable with a gain, but phase 4 had no income to tax it with
    (sale.tax_without_income), so its sale_tax 0.00 is not a computed tax. A loss or a sale
    outside §23 owes no tax whatever the income, so it is never flagged."""
    return (
        sold.taxable
        and sold.gain is not None
        and sold.gain > 0
        and any(a.code == "sale.tax_without_income" for a in assumptions)
    )


def payback(
    paid: PropertyPaidIn,
    actual: Sequence[YearFigures],
    projected: Sequence[YearFigures],
    sale_year: int | None,
    sold_today: SaleResult,
    selling_costs_rate: Decimal,
    sale_assumptions: Sequence[Assumption] = (),
    projection_error_code: str | None = None,
) -> PropertyPayback:
    """One property's payback from phase 4's actual and projected years and the sale today.
    `paid` must have a paid_in (the service gives a property without one its reason). With a
    projection_error_code there are no projected years and no sale year, and no break-even
    year is stated."""
    if paid.paid_in is None:
        raise ValueError("payback needs the property's paid_in")
    paid_in = paid.paid_in
    cash_back = cents(sum((y.net_cash for y in actual), ZERO))
    arrived, expected = arrived_and_expected(actual)
    years = series(actual, projected)
    return PropertyPayback(
        property_id=paid.property_id,
        label=paid.label,
        paid_in=paid_in,
        cash_back=cash_back,
        arrived=arrived,
        expected_tax_refund=expected,
        percent=percent(cash_back, paid_in),
        years=years,
        break_even_year=None if projection_error_code else break_even(years, paid_in),
        sale_year=None if projection_error_code else sale_year,
        tax_estimated_years=[y.year for y in actual if YearFlag.TAX_ESTIMATED in y.flags],
        income_missing_years=[y.year for y in actual if YearFlag.INCOME_MISSING in y.flags],
        sale_today=SaleToday(
            value=sold_today.value,
            selling_costs_rate=selling_costs_rate,
            selling_costs=sold_today.selling_costs,
            taxable=sold_today.taxable,
            tax_free_from=sold_today.tax_free_from,
            gain=sold_today.gain,
            sale_tax=sold_today.sale_tax,
            tax_not_computed=tax_not_computed(sold_today, sale_assumptions),
            loan_balance=sold_today.loan_balance,
            net_proceeds=sold_today.net_proceeds,
            profit=cents(sold_today.net_proceeds + cash_back - paid_in),
        ),
        projection_error_code=projection_error_code,
    )


def left_out(
    paid: PropertyPaidIn,
    reason: PaybackReason,
    error_code: str | None = None,
    loan_id: UUID | None = None,
) -> PropertyPayback:
    """A property without a payback: its paid_in as computed, and why (with loan_not_valued,
    the loan whose schedule fails, so the screen can link to it)."""
    return PropertyPayback(
        property_id=paid.property_id,
        label=paid.label,
        paid_in=paid.paid_in,
        reason=reason,
        error_code=error_code,
        loan_id=loan_id,
    )


def total(properties: Sequence[PropertyPayback]) -> PropertiesPayback:
    """The totals over the properties with a payback (reason None). The break-even year runs
    over the union of their years: each property's running sum at its newest year up to y (0
    before its first, held after its sale year), summed, against Σ paid_in; None when Σ
    paid_in ≤ 0 or when one of them has no projection, and then no sale year either."""
    counted = [p for p in properties if p.reason is None and p.paid_in is not None]
    paid_in = cents(sum((p.paid_in for p in counted if p.paid_in is not None), ZERO))
    cash_back = cents(sum((p.cash_back or ZERO for p in counted), ZERO))
    arrived = cents(sum((p.arrived or ZERO for p in counted), ZERO))
    expected = cents(sum((p.expected_tax_refund or ZERO for p in counted), ZERO))
    profit = cents(sum((p.sale_today.profit for p in counted if p.sale_today is not None), ZERO))
    year_set = sorted({y.year for p in counted for y in p.years})
    reached = None
    if paid_in <= 0 or any(p.projection_error_code for p in counted):
        year_set = []
    for year in year_set:
        running = ZERO
        for p in counted:
            upto = [y.cumulative for y in p.years if y.year <= year]
            running += upto[-1] if upto else ZERO
        if running >= paid_in:
            reached = year
            break
    # Without one property's projection the sum cannot say „not back by“ any year either.
    complete = not any(p.projection_error_code for p in counted)
    sale_years = [p.sale_year for p in counted if p.sale_year is not None and complete]
    return PropertiesPayback(
        properties=list(properties),
        paid_in=paid_in,
        cash_back=cash_back,
        arrived=arrived,
        expected_tax_refund=expected,
        percent=percent(cash_back, paid_in) if counted else None,
        profit_today=profit,
        break_even_year=reached,
        sale_year=max(sale_years) if sale_years else None,
        tax_estimated_years=sorted({y for p in counted for y in p.tax_estimated_years}),
        income_missing_years=sorted({y for p in counted for y in p.income_missing_years}),
    )
