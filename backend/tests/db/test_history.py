"""History through the services: the phase 1 done criterion.

test_done_criterion rebuilds the scenario of tests/fixtures/phase1/timeline.yaml with the domain
services against Postgres, then checks that property_history returns exactly the fixture's
property_history, flat_history, parking_history and other_property_history entries (ids
mapped, since the database assigns its own).

Where the services cannot record the fixture's events literally, the test says so:
1. The flat's first rent row (2024-07-01) is dated before the wrong move-in (2024-07-15), so
   check_rent_row refuses it until the move-in is corrected. The test corrects the move-in
   first, then schedules the row; History orders by (date, root seq), so it is unchanged.
2. create_property always records the possession transfer, and PropertyCreate requires
   purchase_date, so the second property gets one more milestone than the fixture has.
3. The vault events (document.uploaded / linked) belong to the document service; they carry
   no effective date, so History never shows them.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from conftest import OWNER, TEST_IBAN_KEY, insert_event
from fixture_models import TimelineFixture, load

from hausbuch.schemas.common import Fraction
from hausbuch.schemas.enums import DepositForm, PropertyKind, RentChangeReason, RentModel
from hausbuch.schemas.events import EVENT_CATALOG, Event, EventType
from hausbuch.schemas.loan import LoanCreate
from hausbuch.schemas.property import Address, PropertyCreate, UnitCreate
from hausbuch.schemas.tenancy import TenancyCreate, TenancyParty, TenantCreate
from hausbuch.services.errors import NotFoundError
from hausbuch.services.history import audit_trail, property_history
from hausbuch.services.loans import create_loan
from hausbuch.services.properties import (
    correct_purchase_date,
    create_property,
    update_property,
)
from hausbuch.services.tenancies import (
    correct_move_in,
    create_tenancy,
    record_deposit_received,
    schedule_rent,
)
from hausbuch.services.tenants import create_tenant
from hausbuch.services.units import create_unit

FX = load("timeline")
assert isinstance(FX, TimelineFixture)
FIXTURE_EVENTS = {e.id: e for e in FX.events}
VAULT_EVENTS = {EventType.DOCUMENT_UPLOADED, EventType.DOCUMENT_LINKED}
FIXTURE_TENANTS = {
    "tenant_flat": uuid.UUID("0000000d-0000-4000-8000-000000000001"),
    "tenant_parking": uuid.UUID("0000000d-0000-4000-8000-000000000002"),
}
ADDRESS = Address(street="Lindenstraße", house_number="12", postal_code="10115", city="Berlin")


class Clock:
    """A recorded_at per command, one hour apart, like the fixture."""

    def __init__(self) -> None:
        self.at = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.at += timedelta(hours=1)
        return self.at


def build_scenario(run, ws: uuid.UUID) -> dict[str, uuid.UUID]:
    """The fixture's story, command by command. Returns fixture name -> stored id."""
    now = Clock()
    ids: dict[str, uuid.UUID] = {}

    # Purchase: notarized 2024-03-15, possession typed as 2024-05-02 (should be 2024-05-01).
    flat_property = run(
        ws,
        create_property,
        PropertyCreate(
            label="Wohnung Lindenstraße",
            kind=PropertyKind.ETW,
            address=ADDRESS,
            build_year=1994,
            notarized_on=date(2024, 3, 15),
            purchase_date=date(2024, 5, 2),
            purchase_price=Decimal("289000.00"),
        ),
        now=now(),
    )
    ids["property_flat"] = flat_property.id
    ids["unit_flat"] = run(
        ws,
        create_unit,
        UnitCreate(
            property_id=flat_property.id,
            label="WE 3",
            floor_area_m2=Decimal("68.40"),
            rooms=Decimal("2.5"),
            mea=Fraction(numerator=85, denominator=1000),
        ),
        now=now(),
    ).id
    ids["unit_parking"] = run(
        ws,
        create_unit,
        UnitCreate(
            property_id=flat_property.id, label="TG-Stellplatz 12", floor_area_m2=Decimal("12.50")
        ),
        now=now(),
    ).id

    # Financing: signed 2024-03-01, paid out 2024-04-30.
    ids["loan"] = run(
        ws,
        create_loan,
        LoanCreate(
            property_id=flat_property.id,
            lender="Sparkasse Musterstadt",
            principal=Decimal("240000.00"),
            nominal_rate=Decimal("0.0365"),
            monthly_payment=Decimal("1130.00"),
            disbursed_on=date(2024, 4, 30),
            first_payment_on=date(2024, 5, 30),
            fixed_until=date(2034, 4, 30),
        ),
        iban_key=TEST_IBAN_KEY,
        signed_on=date(2024, 3, 1),
        now=now(),
    ).id

    # Letting the flat: signed 2024-06-10, move-in typed as 2024-07-15 (should be 2024-07-01).
    ids["tenant_flat"] = run(
        ws,
        create_tenant,
        TenantCreate(full_name="Anna Beispiel"),
        iban_key=TEST_IBAN_KEY,
        now=now(),
    ).id
    ids["tenancy_flat"] = run(
        ws,
        create_tenancy,
        TenancyCreate(
            unit_id=ids["unit_flat"],
            parties=[TenancyParty(tenant_id=ids["tenant_flat"], role="primary")],
            signed_on=date(2024, 6, 10),
            start_date=date(2024, 7, 15),
            rent_model=RentModel.STAFFEL,
            deposit_amount=Decimal("2850.00"),
            deposit_form=DepositForm.CASH_ACCOUNT,
        ),
        initial_rent=None,  # deviation 1: the 2024-07-01 row waits for the move-in correction
        iban_key=TEST_IBAN_KEY,
        now=now(),
    ).id
    run(ws, correct_move_in, ids["tenancy_flat"], date(2024, 7, 1), now=now())
    for day, cold, reason in (
        (date(2024, 7, 1), "950.00", RentChangeReason.INITIAL),
        (date(2025, 7, 1), "1000.00", RentChangeReason.STAFFEL_STEP),
    ):
        run(
            ws,
            schedule_rent,
            ids["tenancy_flat"],
            effective_from=day,
            cold_rent=Decimal(cold),
            operating_costs_prepayment=Decimal("220.00"),
            reason=reason,
            now=now(),
        )
        if reason is RentChangeReason.INITIAL:
            run(
                ws,
                record_deposit_received,
                ids["tenancy_flat"],
                amount=Decimal("2850.00"),
                received_on=date(2024, 7, 1),
                now=now(),
            )

    # Possession corrected twice: first to another wrong date, then to the true one.
    run(ws, correct_purchase_date, flat_property.id, date(2024, 5, 11), now=now())
    run(ws, correct_purchase_date, flat_property.id, date(2024, 5, 1), now=now())

    # Letting the parking space: signed 2025-08-20, from 2025-09-01, no deposit, no rent row.
    ids["tenant_parking"] = run(
        ws,
        create_tenant,
        TenantCreate(full_name="Bert Beispiel"),
        iban_key=TEST_IBAN_KEY,
        now=now(),
    ).id
    ids["tenancy_parking"] = run(
        ws,
        create_tenancy,
        TenancyCreate(
            unit_id=ids["unit_parking"],
            parties=[TenancyParty(tenant_id=ids["tenant_parking"], role="primary")],
            signed_on=date(2025, 8, 20),
            start_date=date(2025, 9, 1),
            rent_model=RentModel.FIXED,
        ),
        initial_rent=None,
        iban_key=TEST_IBAN_KEY,
        now=now(),
    ).id

    # Another property; it must never appear in the flat's History.
    ids["property_other"] = run(
        ws,
        create_property,
        PropertyCreate(
            label="Haus Am Bach",
            kind=PropertyKind.EFH,
            address=ADDRESS.model_copy(update={"street": "Am Bach", "house_number": "3"}),
            build_year=2023,
            notarized_on=date(2025, 1, 10),
            purchase_date=date(2025, 3, 1),  # deviation 2: required, so recorded as a milestone
            purchase_price=Decimal("520000.00"),
        ),
        now=now(),
    ).id

    # Audit trail only: the label update.
    run(ws, update_property, flat_property.id, label="Lindenstraße 12, WE 3", now=now())
    return ids


@pytest.fixture
def scenario(run, fresh_workspaces):
    return build_scenario(run, fresh_workspaces[0])


def _match_events(stored: list[Event], fixture_to_stored: dict[uuid.UUID, uuid.UUID]):
    """Pair each fixture event (vault events aside) with the stored event of the same type,
    entity and effective date; each pair must be unique on both sides."""
    by_key: dict[tuple, Event] = {}
    for e in stored:
        key = (e.type, e.entity_id, e.effective_date)
        assert key not in by_key, f"two stored events share {key}"
        by_key[key] = e
    pairs: dict[uuid.UUID, Event] = {}
    for f in FX.events:
        if f.type in VAULT_EVENTS:
            continue
        key = (f.type, fixture_to_stored[f.entity_id], f.effective_date)
        assert key in by_key, f"fixture event seq {f.seq} ({f.type}) has no stored twin"
        pairs[f.id] = by_key[key]
    assert len({e.id for e in pairs.values()}) == len(pairs)
    return pairs


def _substitute(value, ids: dict[str, str]):
    if isinstance(value, dict):
        return {k: _substitute(v, ids) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, ids) for v in value]
    return ids.get(value, value) if isinstance(value, str) else value


def test_done_criterion_history_matches_the_fixture(scenario, read, fresh_workspaces):
    a, _ = fresh_workspaces
    named = {FX.ids[name]: stored for name, stored in scenario.items() if name in FX.ids}
    named |= {FIXTURE_TENANTS[name]: scenario[name] for name in FIXTURE_TENANTS}

    stored = [
        e
        for pid in (scenario["property_flat"], scenario["property_other"])
        for e in read(a, audit_trail, property_id=pid)
    ] + [e for name in FIXTURE_TENANTS for e in read(a, audit_trail, entity_id=scenario[name])]
    pairs = _match_events(stored, named)

    # Correction chains match the fixture's, event for event.
    for fixture_id, twin in pairs.items():
        corrected = FIXTURE_EVENTS[fixture_id].corrects_event_id
        assert twin.corrects_event_id == (None if corrected is None else pairs[corrected].id)
        assert (twin.property_id, twin.unit_id) == tuple(
            None if v is None else named[v]
            for v in (FIXTURE_EVENTS[fixture_id].property_id, FIXTURE_EVENTS[fixture_id].unit_id)
        )

    # Payloads match too, once ids (entities, tenants, rent rows) are mapped.
    id_map = {str(k): str(v) for k, v in named.items()}
    for fixture_id, twin in pairs.items():
        if "rent_schedule_id" in twin.payload:
            fixture_row = FIXTURE_EVENTS[fixture_id].payload["rent_schedule_id"]
            id_map[fixture_row] = twin.payload["rent_schedule_id"]
    for fixture_id, twin in pairs.items():
        f = FIXTURE_EVENTS[fixture_id]
        model = EVENT_CATALOG[f.type].payload
        # As models: Decimal("0.0365") == Decimal("0.036500"), as numeric(7,6) stores it.
        expected = model.model_validate(_substitute(f.payload, id_map))
        assert model.model_validate(twin.payload) == expected, f"fixture seq {f.seq} {f.type}"

    # History: every case of the fixture, through property_history.
    extra_possession = None
    for case in FX.cases:
        property_id = named[case.scope.property_id]
        unit_id = None if case.scope.unit_id is None else named[case.scope.unit_id]
        entries = read(a, property_history, property_id, unit_id)

        if case.id == "other_property_history":
            # Deviation 2: one more milestone than the fixture, and only that one.
            [extra_possession] = [
                e for e in entries if e.type is EventType.PROPERTY_POSSESSION_TRANSFERRED
            ]
            entries = [e for e in entries if e is not extra_possession]

        expected = [
            x.model_dump()
            | {
                "event_id": pairs[x.event_id].id,
                "root_event_id": pairs[x.root_event_id].id,
                "entity_id": named[x.entity_id],
            }
            for x in case.expect
        ]
        assert [e.model_dump(exclude={"payload"}) for e in entries] == expected, case.id
        for e in entries:
            assert e.payload == next(t.payload for t in pairs.values() if t.id == e.event_id)

    assert extra_possession is not None
    assert (extra_possession.effective_date, extra_possession.corrected) == (
        date(2025, 3, 1),
        False,
    )
    assert extra_possession.entity_id == scenario["property_other"]


def test_the_audit_trail_keeps_every_correction_in_seq_order(scenario, read, fresh_workspaces):
    a, _ = fresh_workspaces
    trail = read(a, audit_trail, property_id=scenario["property_flat"])
    assert [e.seq for e in trail] == sorted(e.seq for e in trail)
    possession = [e for e in trail if e.type is EventType.PROPERTY_POSSESSION_TRANSFERRED]
    assert [e.effective_date for e in possession] == [
        date(2024, 5, 2),
        date(2024, 5, 11),
        date(2024, 5, 1),
    ]
    # The label update is audit trail only, recorded as the fixture's seq 24 is.
    label = trail[-1]
    assert label.type is EventType.PROPERTY_UPDATED
    assert label.payload == {
        "changes": {"label": {"old": "Wohnung Lindenstraße", "new": "Lindenstraße 12, WE 3"}},
        "reason": None,
    }
    history = read(a, property_history, scenario["property_flat"])
    assert EventType.PROPERTY_UPDATED not in {e.type for e in history}
    assert len(trail) > len(history)


def test_history_is_scoped_to_the_workspace_and_the_property(scenario, read, fresh_workspaces):
    a, b = fresh_workspaces
    with pytest.raises(NotFoundError, match="property"):
        read(b, property_history, scenario["property_flat"])
    assert read(b, audit_trail, property_id=scenario["property_flat"]) == []
    with pytest.raises(NotFoundError, match="unit"):
        read(a, property_history, scenario["property_other"], scenario["unit_flat"])
    with pytest.raises(NotFoundError, match="property"):
        read(a, property_history, uuid.uuid4())


def test_history_follows_a_chain_that_crosses_scopes(run, read, connect, fresh_workspaces):
    """A chain whose root lies outside the property's scope still collapses correctly. The
    services never write one; the database allows it, so History must not fail on it."""
    a, _ = fresh_workspaces
    prop = run(
        a,
        create_property,
        PropertyCreate(
            label="Legacy",
            kind=PropertyKind.EFH,
            address=ADDRESS,
            build_year=1960,
            notarized_on=date(2020, 1, 15),
            purchase_date=date(2020, 2, 1),
            purchase_price=Decimal("300000.00"),
        ),
    )
    entity = uuid.uuid4()
    with connect(OWNER, a) as conn:
        root = insert_event(
            conn,
            a,
            type="tenancy.started",
            entity_type="tenancy",
            entity_id=entity,
            effective_date=date(2021, 3, 15),
        )
        insert_event(
            conn,
            a,
            type="tenancy.started",
            entity_type="tenancy",
            entity_id=entity,
            property_id=prop.id,
            effective_date=date(2021, 3, 1),
            corrects_event_id=root,
        )
        conn.commit()

    [moved_in] = [e for e in read(a, property_history, prop.id) if e.entity_id == entity]
    assert (moved_in.root_event_id, moved_in.corrected) == (root, True)
    assert moved_in.original_effective_date == date(2021, 3, 15)
