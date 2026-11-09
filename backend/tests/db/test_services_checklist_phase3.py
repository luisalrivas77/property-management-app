"""services.checklist feeds the phase 3 entries (phase-3.md §8, D64, D70, D71): the confirmed
allocations that need a Beleg, and the market_rent tax inputs with their history."""

import itertools
from datetime import date, timedelta
from decimal import Decimal

import pytest
from db.matching_support import World

from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.checklist import ChecklistScopeType, ChecklistStatus, RequirementCode
from hausbuch.schemas.property import Address, PropertyCreate, UnitCreate
from hausbuch.schemas.tax import TaxInputCreate, TaxInputKind
from hausbuch.services.allocations import record_set
from hausbuch.services.checklist import checklist_facts, document_checklist
from hausbuch.services.properties import create_property
from hausbuch.services.tax_inputs import create_tax_input
from hausbuch.services.units import create_unit

pytestmark = pytest.mark.db

TODAY = date(2026, 11, 9)
MIETSPIEGEL = {"valid_from": "2025-08-01", "cold_rent_per_m2": "12.80", "source": "mietspiegel"}
_TICK = itertools.count(1)


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0])


def _allocation(
    world: World,
    category: str,
    amount: str = "-1190.00",
    *,
    booked: date = date(2026, 9, 21),
    confirm: bool = True,
    document_id=None,
):
    tx = world.line(booked, amount, name="Maler Muster")
    rows = world.cmd(
        record_set,
        AllocationSet(
            transaction_id=tx,
            allocations=[
                AllocationDraft(
                    amount=Decimal(amount),
                    category=AllocationCategory(category),
                    property_id=world.property_id,
                    document_id=document_id,
                )
            ],
            method=AllocationMethod.USER,
            confidence=Decimal("1"),
        ),
        confirm=confirm,
        auto=False,
    )
    return rows[0].id


def _market_rent(
    world: World, *, property_id=None, unit_id=None, supersedes_id=None, withdrawn=False
):
    property_id = property_id or world.property_id
    data = TaxInputCreate.model_validate(
        {
            "property_id": property_id,
            "kind": TaxInputKind.MARKET_RENT,
            "unit_id": unit_id,
            "value": MIETSPIEGEL,
            "supersedes_id": supersedes_id,
            "withdrawn": withdrawn,
        }
    )
    now = world.now + timedelta(seconds=next(_TICK))
    return world.cmd(create_tax_input, property_id, data, now=now).id


def _items(world: World, code: RequirementCode, year: int = 2026, *, property_id=None):
    got = world.query(
        document_checklist, property_id or world.property_id, tax_year=year, today=TODAY
    )
    return [i for i in got.items if i.code is code]


def test_a_confirmed_repair_needs_its_beleg(world):
    invoice = world.document("invoice", {}, link_property=False)
    with_beleg = _allocation(world, "repairs", document_id=invoice)
    without = _allocation(world, "renovation", "-4760.00", booked=date(2026, 3, 2))
    _allocation(world, "repairs", "-99.00", confirm=False)  # proposed: not yet a cost
    _allocation(world, "repairs", "0.00")  # D70: not evidence
    _allocation(world, "insurance", "-310.00")  # needs no Beleg
    _allocation(world, "repairs", "-50.00", booked=date(2025, 6, 1))  # another year

    items = _items(world, RequirementCode.COST_RECEIPT)
    # The Beleg is listed even though it is linked to nothing.
    assert [(i.scope.id, i.status, [d.id for d in i.documents]) for i in items] == [
        (without, ChecklistStatus.MISSING, []),
        (with_beleg, ChecklistStatus.PRESENT, [invoice]),
    ]
    assert items[0].scope.type is ChecklistScopeType.ALLOCATION
    assert (items[0].scope.category, items[0].scope.booking_date) == (
        AllocationCategory.RENOVATION,
        date(2026, 3, 2),
    )


def test_the_allocation_year_resolver_is_the_callers(world):
    _allocation(world, "repairs", booked=date(2026, 12, 28))
    facts, _ = world.query(checklist_facts, world.property_id)
    assert [a.tax_year for a in facts.allocations] == [2026]
    facts, _ = world.query(checklist_facts, world.property_id, allocation_year=lambda row: 2027)
    assert [a.tax_year for a in facts.allocations] == [2027]


def test_the_market_rent_follows_its_input_history(world):
    assert _items(world, RequirementCode.MARKET_RENT) == []  # not let: no item
    world.tenancy()
    (item,) = _items(world, RequirementCode.MARKET_RENT)
    assert (item.status, item.scope.type, item.scope.id, item.documents) == (
        ChecklistStatus.MISSING,
        ChecklistScopeType.PROPERTY,
        world.property_id,
        [],
    )
    first = _market_rent(world)
    (item,) = _items(world, RequirementCode.MARKET_RENT)
    assert item.status is ChecklistStatus.PRESENT
    _market_rent(world, supersedes_id=first, withdrawn=True)
    (item,) = _items(world, RequirementCode.MARKET_RENT)
    assert item.status is ChecklistStatus.MISSING


def test_an_mfh_asks_per_let_unit(world):
    mfh = world.cmd(
        create_property,
        PropertyCreate(
            label="Haus",
            kind="mfh",
            address=Address(street="Allee", house_number="2", postal_code="10115", city="Berlin"),
            build_year=1960,
            notarized_on=date(2025, 1, 1),
            purchase_date=date(2025, 2, 1),
            purchase_price=Decimal("900000.00"),
        ),
    ).id
    units = [
        world.cmd(
            create_unit,
            UnitCreate(property_id=mfh, label=label, floor_area_m2=Decimal("50.00")),
        ).id
        for label in ("WE 2", "WE 1", "WE 3")
    ]
    world.tenancy(unit_id=units[0])
    world.tenancy(name="Max Muster", iban="DE02120300000000202051", unit_id=units[1])
    _market_rent(world, property_id=mfh, unit_id=units[0])
    items = _items(world, RequirementCode.MARKET_RENT, property_id=mfh)
    assert [(i.scope.type, i.scope.unit_label, i.status) for i in items] == [
        (ChecklistScopeType.UNIT, "WE 1", ChecklistStatus.MISSING),
        (ChecklistScopeType.UNIT, "WE 2", ChecklistStatus.PRESENT),
    ]
