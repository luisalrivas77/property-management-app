"""services.tax_inputs for the seller's renovation inside the purchase price (phase 3 D287,
Q-O180) under hausbuch_app and RLS: the Kaufvertrag required, the work after the handover,
the gross below the building's share (with a kitchen bought with the flat), one claim per
property, the Finanzamt's answer as a superseding row, and the clause kept out of the event.

The world's flat (db.matching_support): price 289,000.00, handed over 1 May 2024. With
11,000.00 of Nebenkosten and a 72 % building share, the building's share is
300,000.00 × 0.72 = 216,000.00. Every figure is invented."""

import itertools
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from db.matching_support import World
from pydantic import ValidationError
from sqlalchemy import select

from hausbuch.db.tables import event as event_table
from hausbuch.rules.errors import (
    TAX_INPUT_MOVABLE_ASSET_EXCEEDS_BUILDING,
    TAX_INPUT_PRICE_RENOVATION_BEFORE_HANDOVER,
    TAX_INPUT_PRICE_RENOVATION_EXCEEDS_BUILDING,
    TAX_INPUT_PRICE_RENOVATION_NO_PURCHASE_DATE,
)
from hausbuch.schemas.enums import AcquisitionCostKind, PriceSplitSource
from hausbuch.schemas.events import EventType
from hausbuch.schemas.property import AcquisitionCostCreate, PriceSplitCreate
from hausbuch.schemas.tax import TAX_INPUT_SCOPE_INVALID, TaxInputCreate, TaxInputKind
from hausbuch.services.errors import RuleViolationError
from hausbuch.services.properties import (
    add_acquisition_cost,
    record_price_split,
    select_price_split,
)
from hausbuch.services.tax_inputs import (
    TAX_INPUT_NOT_CURRENT,
    _check_price_renovation,
    create_tax_input,
    find_current,
    list_tax_inputs,
)

K = TaxInputKind
CLAUSE = "Der Verkäufer verpflichtet sich, die Wohnung nach der Übergabe zu renovieren."


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0])


_TICK = itertools.count(1)


def _create(world: World, data: TaxInputCreate):
    now = world.now + timedelta(seconds=next(_TICK))
    return world.cmd(create_tax_input, world.property_id, data, now=now)


def _refused(world: World, data: TaxInputCreate) -> tuple[str, ...]:
    with pytest.raises(RuleViolationError) as exc:
        _create(world, data)
    return exc.value.codes


def _kaufvertrag(world: World):
    return world.document("manual", {}, type="contract", subtype="purchase_contract")


def _claim(world: World, document_id, amount: str = "23800.00", **extra) -> TaxInputCreate:
    value = {
        "status": "claimed",
        "amount": amount,
        "amount_net": str((Decimal(amount) / Decimal("1.19")).quantize(Decimal("0.01"))),
        "period_start": "2024-05-02",
        "period_end": "2024-07-31",
        "paid_on": "2024-05-01",
        "clause": CLAUSE,
    } | extra.pop("value", {})
    return TaxInputCreate.model_validate(
        {
            "property_id": world.property_id,
            "kind": K.PRICE_RENOVATION,
            "document_id": document_id,
            "value": value,
        }
        | extra
    )


def _split(world: World) -> None:
    world.cmd(
        add_acquisition_cost,
        AcquisitionCostCreate(
            property_id=world.property_id,
            kind=AcquisitionCostKind.REAL_ESTATE_TRANSFER_TAX,
            amount=Decimal("11000.00"),
        ),
    )
    split = world.cmd(
        record_price_split,
        PriceSplitCreate(
            property_id=world.property_id,
            source=PriceSplitSource.PURCHASE_CONTRACT,
            building_share=Decimal("0.720000"),
        ),
    )
    world.cmd(select_price_split, world.property_id, split.id)


def _kitchen(world: World, amount: str) -> TaxInputCreate:
    return TaxInputCreate.model_validate(
        {
            "property_id": world.property_id,
            "kind": K.MOVABLE_ASSET,
            "tax_year": 2024,
            "value": {"label": "einbaukueche", "amount": amount},
        }
    )


def _events(world: World) -> list:
    return world.query(
        lambda conn: conn.execute(
            select(event_table)
            .where(event_table.c.type == EventType.PROPERTY_TAX_INPUT_RECORDED.value)
            .order_by(event_table.c.seq)
        ).all()
    )


def test_a_claim_is_recorded_with_the_kaufvertrag_and_its_clause_stays_in_the_row(world):
    doc = _kaufvertrag(world)
    # The Kaufvertrag is required (DOCUMENT_REQUIRED), refused when the body is parsed.
    with pytest.raises(ValidationError, match=TAX_INPUT_SCOPE_INVALID):
        _claim(world, None)
    row = _create(world, _claim(world, doc))
    assert (row.kind, row.tax_year, row.document_id) == (K.PRICE_RENOVATION, None, doc)
    assert (row.value.status, row.value.amount, row.value.clause) == (
        "claimed",
        Decimal("23800.00"),
        CLAUSE,
    )
    (event,) = _events(world)
    assert event.payload["kind"] == "price_renovation"
    assert event.payload["value"]["clause"] is None
    assert CLAUSE not in str(event.payload)


def test_work_before_the_handover_is_refused(world):
    doc = _kaufvertrag(world)
    early = _claim(world, doc, value={"period_start": "2024-04-30"})
    assert _refused(world, early) == (TAX_INPUT_PRICE_RENOVATION_BEFORE_HANDOVER,)
    # The handover day itself counts (on or after it).
    on_the_day = _create(world, _claim(world, doc, value={"period_start": "2024-05-01"}))
    assert on_the_day.value.period_start == date(2024, 5, 1)


def test_the_gross_stays_below_the_building_share_once_a_split_is_selected(world):
    doc = _kaufvertrag(world)
    # Without a split there is no AfA to reduce: nothing to compare against yet.
    first = _create(world, _claim(world, doc, "216000.00"))
    _split(world)
    # A correction to the building's whole share is refused ...
    whole = _claim(world, doc, "216000.00", supersedes_id=first.id)
    assert _refused(world, whole) == (TAX_INPUT_PRICE_RENOVATION_EXCEEDS_BUILDING,)
    # ... one cent below is not.
    below = _create(world, _claim(world, doc, "215999.99", supersedes_id=first.id))
    assert world.query(find_current, world.property_id, K.PRICE_RENOVATION) == [below]


def test_a_kitchen_and_the_claim_share_the_building(world):
    _split(world)
    doc = _kaufvertrag(world)
    # A kitchen of 8,500.00 in the price has the basis 8,500 × 300,000 ÷ 289,000 = 8,823.53.
    _create(world, _kitchen(world, "8500.00"))
    # 207,176.47 + 8,823.53 = 216,000.00: not below the share.
    assert _refused(world, _claim(world, doc, "207176.47")) == (
        TAX_INPUT_PRICE_RENOVATION_EXCEEDS_BUILDING,
    )
    claim = _create(world, _claim(world, doc, "207176.46"))
    assert claim.value.amount == Decimal("207176.46")


def test_a_kitchen_after_a_claim_counts_the_claim(world):
    _split(world)
    doc = _kaufvertrag(world)
    _create(world, _claim(world, doc, "207176.47"))
    assert _refused(world, _kitchen(world, "8500.00")) == (
        TAX_INPUT_MOVABLE_ASSET_EXCEEDS_BUILDING,
    )
    _create(world, _kitchen(world, "8499.99"))


def test_one_claim_per_property_and_the_finanzamts_no_supersedes_it(world):
    doc = _kaufvertrag(world)
    claim = _create(world, _claim(world, doc))
    assert _refused(world, _claim(world, doc, "11900.00")) == (TAX_INPUT_NOT_CURRENT,)
    declined = _create(
        world, _claim(world, doc, value={"status": "declined"}, supersedes_id=claim.id)
    )
    assert world.query(find_current, world.property_id, K.PRICE_RENOVATION) == [declined]
    history = world.query(list_tax_inputs, world.property_id, include_history=True)
    assert [r.id for r in history] == [claim.id, declined.id]
    # A declined answer can still be withdrawn (the user's own change of mind).
    _create(
        world,
        _claim(world, doc, value={"status": "declined"}, supersedes_id=declined.id, withdrawn=True),
    )
    assert world.query(list_tax_inputs, world.property_id, kind=K.PRICE_RENOVATION) == []


def test_a_declined_answer_is_not_rechecked(world):
    """„Nein“ repeats the claim's value; even a value that would no longer apply is accepted,
    since a declined claim changes nothing in the draft."""
    doc = _kaufvertrag(world)
    claim = _create(world, _claim(world, doc, "216000.00"))
    _split(world)
    declined = _create(
        world,
        _claim(world, doc, "216000.00", value={"status": "declined"}, supersedes_id=claim.id),
    )
    assert declined.value.status == "declined"


def test_the_handover_date_is_needed_first():
    """No database: property.purchase_date is NOT NULL, so the guard gets a stand-in row."""
    prop = SimpleNamespace(id=uuid4(), purchase_date=None)
    data = TaxInputCreate.model_validate(
        {
            "property_id": prop.id,
            "kind": K.PRICE_RENOVATION,
            "document_id": uuid4(),
            "value": {
                "status": "claimed",
                "amount": "11900.00",
                "amount_net": "10000.00",
                "period_start": "2026-07-01",
                "period_end": "2026-08-31",
                "paid_on": "2026-07-01",
            },
        }
    )
    with pytest.raises(RuleViolationError) as exc:
        _check_price_renovation(None, prop, data)  # type: ignore[arg-type]
    assert exc.value.codes == (TAX_INPUT_PRICE_RENOVATION_NO_PURCHASE_DATE,)
