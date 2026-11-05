"""Property commands end to end, as hausbuch_app under RLS."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from hausbuch.db.session import workspace_transaction
from hausbuch.rules.validation import SHARES_MUST_SUM_TO_ONE
from hausbuch.schemas.common import Fraction
from hausbuch.schemas.enums import (
    AcquisitionCostKind,
    ActorType,
    EntityType,
    PriceSplitSource,
    PropertyKind,
)
from hausbuch.schemas.events import EventType
from hausbuch.schemas.property import (
    AcquisitionCostCreate,
    Address,
    OwnerCreate,
    OwnerShare,
    PriceSplitCreate,
    PropertyCreate,
)
from hausbuch.services.context import Actor, new_command
from hausbuch.services.errors import NotFoundError, RuleViolationError
from hausbuch.services.events import list_events
from hausbuch.services.owners import create_owner
from hausbuch.services.properties import (
    add_acquisition_cost,
    correct_notarized_on,
    correct_purchase_date,
    create_property,
    get_ownership,
    get_property,
    list_acquisition_costs,
    list_price_splits,
    record_price_split,
    select_price_split,
    set_ownership,
    update_property,
)

CREATED_AT = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
ACTOR = Actor(ActorType.USER, "user:test")
ADDRESS = Address(street="Lindenstraße", house_number="12", postal_code="10115", city="Berlin")
FLAT = PropertyCreate(
    label="Wohnung Lindenstraße",
    kind=PropertyKind.ETW,
    address=ADDRESS,
    build_year=1994,
    notarized_on=date(2024, 3, 15),
    purchase_date=date(2024, 5, 2),
    purchase_price=Decimal("289000.00"),
)
HALF = Fraction(numerator=1, denominator=2)


def _events(read, ws, property_id):
    return read(ws, list_events, property_id=property_id)


def _types(events):
    return [e.type for e in events]


# --- create ------------------------------------------------------------------


def test_create_writes_the_row_and_the_purchase_milestones(app_engine, workspaces, read):
    a, _ = workspaces
    with workspace_transaction(app_engine, a) as conn:
        ctx = new_command(conn, a, ACTOR, now=CREATED_AT)
        flat = create_property(ctx, FLAT)

    assert flat.model_dump(include=set(PropertyCreate.model_fields)) == FLAT.model_dump()
    assert flat.workspace_id == a and flat.selected_price_split_id is None
    assert flat.created_at == flat.updated_at == CREATED_AT
    assert read(a, get_property, flat.id) == flat

    created, notarized, possession = _events(read, a, flat.id)
    assert _types([created, notarized, possession]) == [
        EventType.PROPERTY_CREATED,
        EventType.PROPERTY_PURCHASE_NOTARIZED,
        EventType.PROPERTY_POSSESSION_TRANSFERRED,
    ]
    assert created.payload == {
        "label": "Wohnung Lindenstraße",
        "kind": "etw",
        "build_year": 1994,
        "purchase_price": "289000.00",
    }
    assert created.effective_date is None
    assert (notarized.effective_date, notarized.payload) == (
        date(2024, 3, 15),
        {"purchase_price": "289000.00"},
    )
    assert (possession.effective_date, possession.payload) == (date(2024, 5, 2), {})
    for e in (created, notarized, possession):
        assert (e.entity_id, e.property_id, e.unit_id) == (flat.id, flat.id, None)
        assert e.correlation_id == ctx.correlation_id


def test_property_is_invisible_from_another_workspace(run, read, workspaces):
    a, b = workspaces
    flat = run(a, create_property, FLAT, now=CREATED_AT)
    assert read(b, get_property, flat.id) is None
    assert _events(read, b, flat.id) == []
    with pytest.raises(NotFoundError, match="property"):
        run(b, update_property, flat.id, label="Mallory")
    with pytest.raises(NotFoundError, match="property"):
        run(b, correct_purchase_date, flat.id, date(2024, 5, 1))
    assert read(a, get_property, flat.id) == flat


def test_failure_after_create_leaves_neither_row_nor_events(app_engine, workspaces, read):
    a, _ = workspaces

    class Boom(Exception):
        pass

    with pytest.raises(Boom), workspace_transaction(app_engine, a) as conn:
        flat = create_property(new_command(conn, a, ACTOR, now=CREATED_AT), FLAT)
        raise Boom

    assert read(a, get_property, flat.id) is None
    assert _events(read, a, flat.id) == []


# --- update ------------------------------------------------------------------


def test_update_records_the_changed_fields(run, read, workspaces):
    a, _ = workspaces
    flat = run(a, create_property, FLAT, now=CREATED_AT)
    moved = ADDRESS.model_copy(update={"house_number": "12a"})

    updated = run(
        a,
        update_property,
        flat.id,
        label="Lindenstraße 12, WE 3",
        address=moved,
        build_year=1994,  # unchanged: not in the event
        now=LATER,
    )

    assert (updated.label, updated.address, updated.updated_at) == (
        "Lindenstraße 12, WE 3",
        moved,
        LATER,
    )
    assert read(a, get_property, flat.id) == updated
    changed = _events(read, a, flat.id)[-1]
    assert changed.type is EventType.PROPERTY_UPDATED
    assert changed.effective_date is None and changed.property_id == flat.id
    assert changed.payload["changes"] == {
        "label": {"old": "Wohnung Lindenstraße", "new": "Lindenstraße 12, WE 3"},
        "address": {"old": ADDRESS.model_dump(), "new": moved.model_dump()},
    }


def test_noop_and_invalid_updates_write_nothing(run, read, workspaces):
    a, _ = workspaces
    flat = run(a, create_property, FLAT, now=CREATED_AT)
    assert run(a, update_property, flat.id, label=FLAT.label, now=LATER) == flat
    with pytest.raises(ValidationError):
        run(a, update_property, flat.id, label="", now=LATER)
    assert read(a, get_property, flat.id) == flat
    assert len(_events(read, a, flat.id)) == 3


# --- milestone corrections -----------------------------------------------------


def test_purchase_date_corrections_form_a_chain(run, read, workspaces):
    a, _ = workspaces
    flat = run(a, create_property, FLAT, now=CREATED_AT)

    run(a, correct_purchase_date, flat.id, date(2024, 5, 11), now=LATER)
    fixed = run(a, correct_purchase_date, flat.id, date(2024, 5, 1), now=LATER)

    assert fixed.purchase_date == date(2024, 5, 1) and fixed.updated_at == LATER
    assert read(a, get_property, flat.id) == fixed
    *_, original, first, second = _events(read, a, flat.id)
    assert _types([original, first, second]) == [EventType.PROPERTY_POSSESSION_TRANSFERRED] * 3
    assert [e.effective_date for e in (original, first, second)] == [
        date(2024, 5, 2),
        date(2024, 5, 11),
        date(2024, 5, 1),
    ]
    assert (first.corrects_event_id, second.corrects_event_id) == (original.id, first.id)
    assert second.property_id == flat.id and second.payload == {}


def test_purchase_date_correction_is_validated_and_a_noop_writes_nothing(run, read, workspaces):
    a, _ = workspaces
    flat = run(a, create_property, FLAT, now=CREATED_AT)
    with pytest.raises(ValidationError, match="purchase_date must not be before notarized_on"):
        run(a, correct_purchase_date, flat.id, date(2024, 3, 14), now=LATER)
    assert run(a, correct_purchase_date, flat.id, FLAT.purchase_date, now=LATER) == flat
    assert read(a, get_property, flat.id) == flat
    assert len(_events(read, a, flat.id)) == 3


def test_notarized_on_correction_carries_the_full_payload(run, read, workspaces):
    a, _ = workspaces
    flat = run(a, create_property, FLAT, now=CREATED_AT)

    fixed = run(
        a,
        correct_notarized_on,
        flat.id,
        date(2024, 3, 14),
        purchase_price=Decimal("288500"),
        now=LATER,
    )

    assert (fixed.notarized_on, fixed.purchase_price) == (date(2024, 3, 14), Decimal("288500.00"))
    original = _events(read, a, flat.id)[1]
    correction = _events(read, a, flat.id)[-1]
    assert correction.type is EventType.PROPERTY_PURCHASE_NOTARIZED
    assert correction.corrects_event_id == original.id
    assert correction.effective_date == date(2024, 3, 14)
    assert correction.payload == {"purchase_price": "288500.00"}

    # Date only: the price stays as corrected.
    again = run(a, correct_notarized_on, flat.id, date(2024, 3, 15), now=LATER)
    assert again.purchase_price == Decimal("288500.00")
    assert _events(read, a, flat.id)[-1].corrects_event_id == correction.id
    with pytest.raises(ValidationError):
        run(a, correct_notarized_on, flat.id, date(2024, 5, 3), now=LATER)


# --- ownership -----------------------------------------------------------------


def _owners(run, ws):
    return [
        run(ws, create_owner, OwnerCreate(full_name=name), now=CREATED_AT).id
        for name in ("Erika Muster", "Max Muster")
    ]


def test_set_ownership_replaces_the_shares(run, read, workspaces):
    a, _ = workspaces
    flat = run(a, create_property, FLAT, now=CREATED_AT)
    erika, max_ = _owners(run, a)
    halves = [OwnerShare(owner_id=erika, share=HALF), OwnerShare(owner_id=max_, share=HALF)]

    run(a, set_ownership, flat.id, halves, effective_date=date(2024, 5, 2), now=LATER)
    assert set(read(a, get_ownership, flat.id).shares) == set(halves)
    event = _events(read, a, flat.id)[-1]
    assert event.type is EventType.PROPERTY_OWNERSHIP_SET
    assert event.effective_date == date(2024, 5, 2)
    assert {s["owner_id"] for s in event.payload["shares"]} == {str(erika), str(max_)}

    # Unchanged shares are a no-op; new shares replace the old ones entirely.
    run(a, set_ownership, flat.id, list(reversed(halves)), now=LATER)
    assert len(_events(read, a, flat.id)) == 4
    whole = [OwnerShare(owner_id=erika, share=Fraction(numerator=1, denominator=1))]
    run(a, set_ownership, flat.id, whole, now=LATER)
    assert read(a, get_ownership, flat.id).shares == whole
    assert len(_events(read, a, flat.id)) == 5


def test_invalid_ownership_is_rejected_and_writes_nothing(run, read, workspaces):
    a, b = workspaces
    flat = run(a, create_property, FLAT, now=CREATED_AT)
    erika, max_ = _owners(run, a)
    foreign = _owners(run, b)[0]
    third = Fraction(numerator=1, denominator=3)

    with pytest.raises(RuleViolationError) as raised:
        run(a, set_ownership, flat.id, [OwnerShare(owner_id=erika, share=HALF)])
    assert raised.value.code == SHARES_MUST_SUM_TO_ONE
    with pytest.raises(RuleViolationError):
        run(
            a,
            set_ownership,
            flat.id,
            [OwnerShare(owner_id=erika, share=HALF), OwnerShare(owner_id=max_, share=third)],
        )
    with pytest.raises(ValueError, match="only one share"):
        run(a, set_ownership, flat.id, [OwnerShare(owner_id=erika, share=HALF)] * 2)
    with pytest.raises(NotFoundError) as missing:
        run(
            a,
            set_ownership,
            flat.id,
            [OwnerShare(owner_id=erika, share=HALF), OwnerShare(owner_id=foreign, share=HALF)],
        )
    assert (missing.value.entity_type, missing.value.entity_id) == (EntityType.OWNER, foreign)

    assert read(a, get_ownership, flat.id) is None
    assert len(_events(read, a, flat.id)) == 3


# --- acquisition costs and price splits ----------------------------------------


def test_acquisition_cost_is_itemized_with_its_paid_date(run, read, workspaces):
    a, _ = workspaces
    flat = run(a, create_property, FLAT, now=CREATED_AT)
    tax = AcquisitionCostCreate(
        property_id=flat.id,
        kind=AcquisitionCostKind.REAL_ESTATE_TRANSFER_TAX,
        amount=Decimal("17340.00"),
        paid_on=date(2024, 4, 20),
    )

    cost = run(a, add_acquisition_cost, tax, now=LATER)

    assert read(a, list_acquisition_costs, flat.id) == [cost]
    event = _events(read, a, flat.id)[-1]
    assert event.type is EventType.PROPERTY_ACQUISITION_COST_RECORDED
    assert event.effective_date == date(2024, 4, 20)
    assert event.payload == {
        "cost_id": str(cost.id),
        "kind": "real_estate_transfer_tax",
        "amount": "17340.00",
        "document_id": None,
    }

    missing_doc = tax.model_copy(update={"document_id": uuid.uuid4()})
    with pytest.raises(NotFoundError, match="document"):
        run(a, add_acquisition_cost, missing_doc)
    with pytest.raises(NotFoundError, match="property"):
        run(a, add_acquisition_cost, tax.model_copy(update={"property_id": uuid.uuid4()}))
    assert read(a, list_acquisition_costs, flat.id) == [cost]


def test_price_splits_are_candidates_and_one_is_selected(run, read, workspaces):
    a, _ = workspaces
    flat = run(a, create_property, FLAT, now=CREATED_AT)
    other = run(a, create_property, FLAT.model_copy(update={"label": "Other"}), now=CREATED_AT)
    contract = run(
        a,
        record_price_split,
        PriceSplitCreate(
            property_id=flat.id,
            source=PriceSplitSource.PURCHASE_CONTRACT,
            building_share=Decimal("0.80"),
        ),
        now=LATER,
    )
    bmf = run(
        a,
        record_price_split,
        PriceSplitCreate(
            property_id=flat.id,
            source=PriceSplitSource.BMF_TOOL,
            building_share=Decimal("0.712345"),
            inputs={"bodenrichtwert": "1450.00"},
        ),
        now=datetime(2026, 11, 6, 13, 0, tzinfo=UTC),
    )
    assert read(a, list_price_splits, flat.id) == [contract, bmf]

    selected = run(a, select_price_split, flat.id, bmf.id, now=LATER)
    assert selected.selected_price_split_id == bmf.id
    assert run(a, select_price_split, flat.id, bmf.id, now=LATER) == selected  # no-op
    with pytest.raises(NotFoundError, match="price_split"):
        run(a, select_price_split, other.id, contract.id)

    tail = _events(read, a, flat.id)[-3:]
    assert _types(tail) == [
        EventType.PROPERTY_PRICE_SPLIT_RECORDED,
        EventType.PROPERTY_PRICE_SPLIT_RECORDED,
        EventType.PROPERTY_PRICE_SPLIT_SELECTED,
    ]
    assert tail[1].payload == {
        "split_id": str(bmf.id),
        "source": "bmf_tool",
        "building_share": "0.712345",
        "evidence_document_id": None,
    }
    assert tail[2].payload == {"split_id": str(bmf.id)}
    assert read(a, get_property, other.id).selected_price_split_id is None
