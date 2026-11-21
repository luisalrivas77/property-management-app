"""services.payback under hausbuch_app and RLS (Übersicht's property tile, Luis 21 Nov 2026):
the cash back so far is Σ phase 4's actual net cash, the percent and the break-even year
follow from it and the default scenario's projection, the sale today runs through phase 4's
sale() at the net worth's value and loan balance of the same day. Every figure by hand;
invented names and amounts only."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from db.matching_support import World

from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.net_worth import NetWorthLineKind
from hausbuch.schemas.performance import IncomeInputCreate, ScenarioCreate, YearKind
from hausbuch.schemas.property import Address, PropertyCreate
from hausbuch.services import allocations
from hausbuch.services.income_inputs import create_income_input
from hausbuch.services.net_worth import net_worth
from hausbuch.services.payback import properties_payback
from hausbuch.services.performance import performance_actuals
from hausbuch.services.properties import create_property
from hausbuch.services.scenarios import create_scenario

pytestmark = pytest.mark.db

C = AllocationCategory
TODAY = date(2026, 11, 21)


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0])


def _flat(world: World, *, principal: str) -> tuple[uuid.UUID, uuid.UUID]:
    """A flat bought in 2026 for 300,000.00 (contract 10 Mar 2026, handover 1 Jul 2026, no
    Nebenkosten rows, no price split) and its one loan."""
    pid = world.cmd(
        create_property,
        PropertyCreate(
            label="Wohnung Ring",
            kind="etw",
            address=Address(street="Ring", house_number="7", postal_code="10115", city="Berlin"),
            build_year=2001,
            notarized_on=date(2026, 3, 10),
            purchase_date=date(2026, 7, 1),
            purchase_price=Decimal("300000.00"),
        ),
    ).id
    loan = world.loan(
        property_id=pid,
        iban=None,
        principal=Decimal(principal),
        disbursed_on=date(2026, 6, 15),
        first_payment_on=date(2026, 7, 30),
        fixed_until=date(2036, 6, 30),
    )
    return pid, loan


def _book(world: World, on: date, *parts: AllocationDraft) -> None:
    total = sum((p.amount for p in parts), Decimal("0"))
    tx = world.line(on, f"{total:.2f}")
    world.cmd(
        allocations.record_set,
        AllocationSet(
            transaction_id=tx,
            allocations=list(parts),
            method=AllocationMethod.USER,
            confidence=Decimal("1.000"),
        ),
        confirm=True,
        auto=False,
    )


def _year_2026(world: World, pid: uuid.UUID, loan: uuid.UUID, other_income: str) -> None:
    """The year booked so far: an other income, one annuity (720.00 interest + 680.00
    Tilgung) and a repair of 250.00."""
    _book(
        world,
        date(2026, 8, 10),
        AllocationDraft(amount=Decimal(other_income), category=C.OTHER_INCOME, property_id=pid),
    )
    _book(
        world,
        date(2026, 8, 30),
        AllocationDraft(
            amount=Decimal("-720.00"), category=C.LOAN_INTEREST, property_id=pid, loan_id=loan
        ),
        AllocationDraft(
            amount=Decimal("-680.00"), category=C.LOAN_PRINCIPAL, property_id=pid, loan_id=loan
        ),
    )
    _book(
        world,
        date(2026, 9, 15),
        AllocationDraft(amount=Decimal("-250.00"), category=C.REPAIRS, property_id=pid),
    )


def _payback(world: World, pid: uuid.UUID):
    result = world.query(properties_payback, today=TODAY)
    (row,) = [p for p in result.properties if p.property_id == pid]
    return row, result


def test_cash_back_is_the_actual_net_cash_and_break_even_is_not_reached(world):
    """No income input: no tax effect. 1,500.00 − 720.00 − 680.00 (the Tilgung is cash paid
    out) − 250.00 = −150.00. Paid in 300,000.00 − 240,000.00 = 60,000.00; −150 ÷ 60,000 =
    −0.25 % → −0.3 (half-up, away from zero). No rent is projected (no tenancy), so the
    projected years lose money and the 60,000.00 is not back by the sale year 2036."""
    pid, loan = _flat(world, principal="240000.00")
    _year_2026(world, pid, loan, "1500.00")

    row, _ = _payback(world, pid)
    assert row.reason is None
    assert (row.paid_in, row.cash_back, row.percent) == (
        Decimal("60000.00"),
        Decimal("-150.00"),
        Decimal("-0.3"),
    )
    actual = world.query(performance_actuals, pid, today=TODAY)
    assert row.cash_back == sum((y.net_cash for y in actual.years), Decimal("0"))
    (y2026,) = actual.years
    assert y2026.cash_out == Decimal("1650.00")  # 720 + 680 + 250: Tilgung included
    assert row.income_missing_years == [2026]
    assert row.tax_estimated_years == []
    assert row.years[0].year == 2026 and row.years[0].kind is YearKind.ACTUAL_PARTIAL
    assert [y.year for y in row.years] == list(range(2026, 2037))
    assert row.years[-1].cumulative < row.paid_in
    assert (row.break_even_year, row.sale_year) == (None, 2036)


def test_break_even_already_in_an_actual_year(world):
    """Paid in 300,000.00 − 299,000.00 = 1,000.00; 2026 brings 3,500.00 − 1,400.00 − 250.00
    = 1,850.00, so the money is back in 2026; 1,850 ÷ 1,000 = 185.0 %."""
    pid, loan = _flat(world, principal="299000.00")
    _year_2026(world, pid, loan, "3500.00")

    row, _ = _payback(world, pid)
    assert (row.paid_in, row.cash_back, row.percent, row.break_even_year) == (
        Decimal("1000.00"),
        Decimal("1850.00"),
        Decimal("185.0"),
        2026,
    )


def test_a_sale_today_with_a_loss_pays_no_tax(world):
    """No scenario: the value is the price, 300,000.00. Selling costs 3.57 % = 10,710.00. Inside
    the ten years (§23 until 10 Mar 2036, tax-free from the 11th), but without a price split
    no AfA is claimed, so the gain is 300,000 − 10,710 − 300,000 = −10,710.00: a loss, no tax.

    The loan balance on 21 Nov 2026 by the schedule (30/360, 3.6 %, 1,400.00 a month): 30 Jul
    interest for 45 days 1,080.00, Tilgung 320.00 → 239,680.00; 30 Aug 719.04 / 680.96 →
    238,999.04; 30 Sep 717.00 / 683.00 → 238,316.04; 30 Oct 714.95 / 685.05 → 237,630.99.
    net = 300,000.00 − 10,710.00 − 0.00 − 237,630.99 = 51,659.01;
    profit = 51,659.01 + (−150.00) − 60,000.00 = −8,490.99."""
    pid, loan = _flat(world, principal="240000.00")
    _year_2026(world, pid, loan, "1500.00")

    row, _ = _payback(world, pid)
    sold = row.sale_today
    assert (sold.value, sold.selling_costs_rate, sold.selling_costs) == (
        Decimal("300000.00"),
        Decimal("0.0357"),
        Decimal("10710.00"),
    )
    assert sold.taxable and sold.tax_free_from == date(2036, 3, 11)
    assert (sold.gain, sold.sale_tax) == (Decimal("-10710.00"), Decimal("0.00"))
    assert (sold.loan_balance, sold.net_proceeds, sold.profit) == (
        Decimal("237630.99"),
        Decimal("51659.01"),
        Decimal("-8490.99"),
    )


def test_a_sale_today_with_a_taxable_gain(world):
    """A default scenario with „Wert heute“ 360,000.00 and a 42 % rate override for 2026.

    The year: result = 1,500.00 − 720.00 − 250.00 = 530.00 (the Tilgung is not deductible, no
    AfA without a price split); tax effect 530.00 × 0.42 = 222.60; net cash = −150.00 −
    222.60 = −372.60 = the cash back; −372.60 ÷ 60,000 = −0.62 % → −0.6.
    The sale: selling costs 3.57 % = 12,852.00; gain = 360,000 − 12,852 − 300,000 =
    47,148.00; tax 47,148.00 × 0.42 = 19,802.16; net = 360,000.00 − 12,852.00 − 19,802.16
    − 237,630.99 = 89,714.85; profit = 89,714.85 − 372.60 − 60,000.00 = 29,342.25."""
    pid, loan = _flat(world, principal="240000.00")
    _year_2026(world, pid, loan, "1500.00")
    world.cmd(
        create_income_input,
        IncomeInputCreate.model_validate(
            {"kind": "marginal_rate_override", "tax_year": 2026, "value": {"rate": "0.4200"}}
        ),
    )
    world.cmd(
        create_scenario,
        pid,
        ScenarioCreate.model_validate(
            {
                "property_id": pid,
                "name": "Basis",
                "is_default": True,
                "levers": {"sale_year": 2036, "today_value": "360000.00"},
            }
        ),
    )

    row, _ = _payback(world, pid)
    (y2026,) = world.query(performance_actuals, pid, today=TODAY).years
    assert (y2026.taxable_result, y2026.tax_effect.effect) == (
        Decimal("530.00"),
        Decimal("222.60"),
    )
    assert (row.cash_back, row.percent) == (Decimal("-372.60"), Decimal("-0.6"))
    assert row.tax_estimated_years == [2026] and row.income_missing_years == []
    sold = row.sale_today
    assert (sold.value, sold.selling_costs, sold.taxable) == (
        Decimal("360000.00"),
        Decimal("12852.00"),
        True,
    )
    assert (sold.gain, sold.sale_tax, sold.loan_balance) == (
        Decimal("47148.00"),
        Decimal("19802.16"),
        Decimal("237630.99"),
    )
    assert (sold.net_proceeds, sold.profit) == (Decimal("89714.85"), Decimal("29342.25"))


def test_the_value_and_balance_are_the_net_worths_of_the_same_day(world):
    pid, loan = _flat(world, principal="240000.00")
    world.cmd(
        create_scenario,
        pid,
        ScenarioCreate.model_validate(
            {
                "property_id": pid,
                "name": "Basis",
                "is_default": True,
                "levers": {"sale_year": 2036, "today_value": "355000.00"},
            }
        ),
    )
    row, _ = _payback(world, pid)
    worth = world.query(net_worth, TODAY)
    lines = [line for line in worth.lines if line.property_id == pid]
    (value,) = [line.value_eur for line in lines if line.kind is NetWorthLineKind.PROPERTY]
    (owed,) = [line.value_eur for line in lines if line.kind is NetWorthLineKind.LOAN]
    assert row.sale_today.value == value == Decimal("355000.00")
    assert row.sale_today.loan_balance == -owed
    assert Decimal("0") < row.sale_today.loan_balance < Decimal("240000.00")


def test_a_property_it_cannot_compute_carries_its_reason_and_stays_out_of_the_totals(world):
    """World's own flat was bought in 2024, before the first rule set: phase 4 refuses it, so
    the payback does too, and the totals hold the 2026 flat only."""
    pid, loan = _flat(world, principal="240000.00")
    _year_2026(world, pid, loan, "1500.00")
    result = world.query(properties_payback, today=TODAY)
    (old,) = [p for p in result.properties if p.property_id == world.property_id]
    assert (old.reason, old.error_code, old.cash_back) == (
        "not_computable",
        "projection.no_rule_set",
        None,
    )
    (new,) = [p for p in result.properties if p.property_id == pid]
    assert (result.paid_in, result.cash_back, result.percent) == (
        Decimal("60000.00"),
        Decimal("-150.00"),
        Decimal("-0.3"),
    )
    assert result.profit_today == new.sale_today.profit
    assert (result.break_even_year, result.sale_year) == (None, 2036)
