"""services.property_equity under hausbuch_app and RLS: a loan cost counts once. A financing
bill recorded as a private_cost row and also booked as loan_fees on the rental account (the
bill paid from there after all, phase 2 D135) counts through its row only; a loan_fees line
with no row adds nothing. Invented names, IBANs and amounts only."""

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
from hausbuch.schemas.common import Fraction
from hausbuch.schemas.tax import PrivateCostValue, TaxInputCreate, TaxInputKind
from hausbuch.services import allocations, tax_inputs
from hausbuch.services.property_equity import properties_paid_in

pytestmark = pytest.mark.db

C = AllocationCategory
TODAY = date(2026, 11, 21)


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0])


def _financing(world: World, amount: str, doc) -> None:
    world.cmd(
        tax_inputs.create_tax_input,
        world.property_id,
        TaxInputCreate(
            property_id=world.property_id,
            kind=TaxInputKind.PRIVATE_COST,
            tax_year=2026,
            document_id=doc,
            value=PrivateCostValue(
                cost_class="financing",
                label="grundschuld",
                amount=Decimal(amount),
                share=Fraction(numerator=1, denominator=1),
                category=C.LOAN_FEES,
            ),
        ),
    )


def _loan_fees(world: World, amount: str, doc) -> None:
    tx = world.line(date(2026, 3, 20), f"-{amount}", purpose="Notarkosten Grundschuld")
    world.cmd(
        allocations.record_set,
        AllocationSet(
            transaction_id=tx,
            allocations=[
                AllocationDraft(
                    amount=Decimal(f"-{amount}"),
                    category=C.LOAN_FEES,
                    property_id=world.property_id,
                    document_id=doc,
                )
            ],
            method=AllocationMethod.USER,
            confidence=Decimal("1.000"),
        ),
        confirm=True,
        auto=False,
    )


def _row(world: World):
    result = world.query(properties_paid_in, today=TODAY)
    (row,) = result.properties
    return row, result.total_paid_in


def test_a_bill_both_recorded_privately_and_paid_from_the_rental_account_counts_once(world):
    world.loan(principal=Decimal("250000.00"))
    doc = world.document(
        "invoice",
        {"sender": "Notar Beispiel", "document_date": "2026-03-10", "amount_total": "635.00"},
    )
    _financing(world, "635.00", doc)
    _loan_fees(world, "635.00", doc)
    _loan_fees(world, "120.00", None)  # a fee booked on the account only: not added

    row, total = _row(world)
    # 289,000.00 + 0.00 + 635.00 − 250,000.00
    assert (row.own_loan_costs, row.loan_principal, row.paid_in) == (
        Decimal("635.00"),
        Decimal("250000.00"),
        Decimal("39635.00"),
    )
    assert total == Decimal("39635.00")
