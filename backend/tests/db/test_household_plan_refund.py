"""Phase 9 step 3, Q-O168 (answered by mockup 12): the household plan proposes phase 4b's
expected refund or back-payment of the most recent past tax year without a confirmed
Einkommensteuerbescheid, as a one-off without a month."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from db.matching_support import World
from db.test_services_return_inputs import _certificate, _create, _document, _no_pay, _owner

from hausbuch.schemas.performance import IncomeInputCreate
from hausbuch.schemas.plan import HouseholdLevers, OneOff, OneOffSource, ProposedOneOff
from hausbuch.services.income_inputs import create_income_input
from hausbuch.services.plan import proposed_refund
from hausbuch.services.return_inputs import household_return_estimate

TODAY = date(2027, 6, 3)


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0])


def _proposed(world: World, levers: HouseholdLevers | None = None) -> list[ProposedOneOff]:
    return world.query(proposed_refund, TODAY, levers or HouseholdLevers())


@pytest.mark.parametrize(
    ("gross", "refund"),
    [("20000.00", True), ("150000.00", False)],
    ids=["refund", "back_payment"],
)
def test_the_estimate_of_the_last_year_is_proposed_with_a_one_offs_sign(world, gross, refund):
    owner = _owner(world)
    _create(world, **_certificate(owner.id, _document(world), gross=gross))
    estimate = world.query(household_return_estimate, tax_year=2026, today=TODAY)
    assert estimate.result != 0
    got = _proposed(world)
    assert got == [ProposedOneOff(tax_year=2026, amount=-estimate.result)]
    assert got[0].source is OneOffSource.RETURN_ESTIMATE
    assert (got[0].amount > 0) is refund


def test_nothing_is_proposed_without_an_estimate(world):
    assert _proposed(world) == []  # no pay recorded: the estimate refuses


def test_nothing_is_proposed_once_the_bescheid_is_confirmed(world):
    owner = _owner(world)
    _create(world, **_certificate(owner.id, _document(world)))
    assert _proposed(world) != []
    body = {
        "kind": "bescheid_zve",
        "tax_year": 2026,
        "document_id": _document(world, "bescheid"),
        "value": {"amount": "48000.00"},
    }
    world.cmd(
        create_income_input,
        IncomeInputCreate.model_validate(body),
        now=world.now + timedelta(hours=1),
    )
    assert _proposed(world) == []


def test_nothing_is_proposed_once_a_refund_is_taken_over(world):
    owner = _owner(world)
    _create(world, **_certificate(owner.id, _document(world)))
    taken = OneOff(month="2027-08", amount=Decimal("850.00"), source=OneOffSource.RETURN_ESTIMATE)
    assert _proposed(world, HouseholdLevers(one_offs=[taken])) == []
    typed = OneOff(month="2027-08", amount=Decimal("850.00"))
    assert _proposed(world, HouseholdLevers(one_offs=[typed])) != []


def test_a_year_a_one_off_names_is_not_proposed_again_even_once_received(world):
    """PR #434 review D: „Übernehmen“ writes the tax year into the source; the one-off stays
    in the scenario after its month has closed (§7.3 amended), so the year is not proposed
    again. Another year still is."""
    owner = _owner(world)
    _create(world, **_certificate(owner.id, _document(world)))
    received = OneOff(month="2027-03", amount=Decimal("850.00"), source="return_estimate:2026")
    assert _proposed(world, HouseholdLevers(one_offs=[received])) == []
    other = OneOff(month="2027-03", amount=Decimal("850.00"), source="return_estimate:2025")
    assert [p.tax_year for p in _proposed(world, HouseholdLevers(one_offs=[other]))] == [2026]


def test_nothing_is_proposed_before_the_wage_certificate(world):
    """PR #434 review G: on an expected pay alone the withheld wage tax is a projection, so
    the refund would be one too; the estimate names the missing Lohnsteuerbescheinigung and
    the plan proposes nothing until it is confirmed."""
    owner = _owner(world)
    world.cmd(
        create_income_input,
        IncomeInputCreate.model_validate(
            {
                "owner_id": owner.id,
                "kind": "expected_gross_pay",
                "tax_year": 2026,
                "value": {"amount": "20000.00"},
            }
        ),
        now=world.now + timedelta(hours=1),
    )
    estimate = world.query(household_return_estimate, tax_year=2026, today=TODAY)
    assert any(m.key == "wage_certificate" for m in estimate.missing)
    assert _proposed(world) == []
    _create(world, **_certificate(owner.id, _document(world), gross="20000.00"))
    assert [p.tax_year for p in _proposed(world)] == [2026]


def test_an_owner_without_pay_does_not_hold_the_proposal_back(world):
    """G's gate asks for a certificate only where there is pay: „Kein Arbeitslohn“ (D195)
    for the other owner leaves the proposal."""
    owner = _owner(world)
    other = _owner(world, "Person B")
    _create(world, **_certificate(owner.id, _document(world), gross="20000.00"))
    _create(world, **_no_pay(other.id))
    estimate = world.query(household_return_estimate, tax_year=2026, today=TODAY)
    assert not any(m.key == "wage_certificate" for m in estimate.missing)
    assert [p.tax_year for p in _proposed(world)] == [2026]
