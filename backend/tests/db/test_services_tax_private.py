"""Phase 6 D159, D161 under hausbuch_app and RLS: lines of a private account leave the Anlage V
draft unchanged to the cent, even those booked with the property (a tenant paying into the
private account, a bill of the flat paid from it), until step 2 reads such a row as a bill's
proof of payment. The pure half is phase 3 anlage_v_2026.yaml's
household_rows_leave_the_draft_unchanged; this is the service's half: no private line
reaches the facts' allocations, coverage or open lines. Invented names and IBANs only."""

from datetime import date
from decimal import Decimal

import pytest
from db.matching_support import World
from db.test_services_private_matching import Private

from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.services import allocations
from hausbuch.services import tax as tax_service

pytestmark = pytest.mark.db

C = AllocationCategory
TODAY = date(2026, 11, 9)


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0])


def _book(world: World, tx, *parts: dict, confirm: bool = True):
    return world.cmd(
        allocations.record_set,
        AllocationSet(
            transaction_id=tx,
            allocations=[AllocationDraft(**p) for p in parts],
            method=AllocationMethod.USER,
            confidence=Decimal("1.000"),
        ),
        confirm=confirm,
        auto=False,
    )


def _snapshot(world: World):
    facts = world.query(tax_service.draft_facts, world.property_id, tax_year=2026, today=TODAY)
    draft = world.query(tax_service.anlage_v_draft, world.property_id, tax_year=2026, today=TODAY)
    sources = world.query(
        tax_service.anlage_v_sources, world.property_id, tax_year=2026, today=TODAY
    )
    return (
        [a.model_dump() for a in facts.allocations],
        [c.model_dump() for c in facts.coverage],
        facts.pending.model_dump(),
        draft.model_dump(),
        [s.model_dump() for s in sources],
    )


def test_private_lines_leave_the_draft_unchanged_to_the_cent(world):
    tenancy = world.tenancy(start=date(2024, 6, 1), cold="850.00", prepayment="180.00")
    prop = {"property_id": world.property_id}
    # The rental account: rent, an insurance bill, the bank's fee, and one line not yet
    # matched (an open line of the year).
    _book(
        world,
        world.line(date(2026, 3, 1), "850.00"),
        prop
        | {
            "amount": Decimal("850.00"),
            "category": C.RENT_COLD,
            "unit_id": world.unit_id,
            "tenancy_id": tenancy,
            "recurring": True,
            "service_period_start": date(2026, 3, 1),
            "service_period_end": date(2026, 3, 31),
        },
    )
    _book(
        world,
        world.line(date(2026, 3, 2), "-50.00"),
        prop | {"amount": Decimal("-50.00"), "category": C.INSURANCE},
    )
    _book(
        world,
        world.line(date(2026, 3, 31), "-4.90"),
        {"amount": Decimal("-4.90"), "category": C.BANK_FEES},
    )
    world.line(date(2026, 4, 2), "-19.99")
    before = _snapshot(world)
    assert before[2]["count"] == 1  # the rental account's open line

    private = Private(world)
    # Household lines without the property, confirmed.
    _book(
        world,
        private.line(date(2026, 8, 3), "-54.20", name="Supermarkt Beispiel"),
        {"amount": Decimal("-54.20"), "category": C.HH_GROCERIES},
    )
    _book(
        world,
        private.line(date(2026, 8, 28), "3200.00", name="Arbeitgeber Beispiel GmbH"),
        {"amount": Decimal("3200.00"), "category": C.HH_SALARY},
    )
    # Lines booked with the property: a tenant's payment into the private account and a bill
    # of the flat paid from it. Neither is rent or a cost of the draft (D159, D161).
    _book(
        world,
        private.line(date(2026, 9, 3), "120.00", name="E. Mustermieterin"),
        prop | {"amount": Decimal("120.00"), "category": C.HH_OTHER_INCOME},
    )
    _book(
        world,
        private.line(date(2026, 9, 10), "-595.00", name="Sanitär Beispiel GmbH"),
        prop | {"amount": Decimal("-595.00"), "category": C.HH_PROPERTY_COST},
    )
    # A proposed one with the property and an unmatched one: no open line of the draft.
    _book(
        world,
        private.line(date(2026, 10, 1), "-80.00", name="Handwerk Beispiel"),
        prop | {"amount": Decimal("-80.00"), "category": C.HH_PROPERTY_COST},
        confirm=False,
    )
    private.line(date(2026, 10, 2), "-12.00", name="Kiosk Beispiel")

    assert _snapshot(world) == before
