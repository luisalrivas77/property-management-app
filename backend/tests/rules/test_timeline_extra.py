"""Edge cases for hausbuch.rules.timeline beyond timeline.yaml."""

import itertools
from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from hausbuch.rules.timeline import build_history
from hausbuch.schemas.events import Event, TimelineScope

WS = UUID("0000000a-0000-4000-8000-000000000001")
P1 = UUID("0000000b-0000-4000-8000-000000000001")
P2 = UUID("0000000b-0000-4000-8000-000000000002")
U_FLAT = UUID("0000000c-0000-4000-8000-000000000001")
U_PARK = UUID("0000000c-0000-4000-8000-000000000002")
LOAN = UUID("0000000f-0000-4000-8000-000000000001")
TENANCY = UUID("0000000e-0000-4000-8000-000000000001")


def eid(n: int) -> UUID:
    return UUID(f"00000e00-0000-4000-8000-{n:012d}")


def event(seq: int, type_: str, entity_id: UUID, id_: UUID | None = None, **kw) -> Event:
    payloads = {
        "loan.disbursed": {"amount": "240000.00"},
        "tenancy.started": {},
        "property.purchase_notarized": {"purchase_price": "289000.00"},
        "tenancy.created": {
            "tenant_ids": [str(UUID(int=7))],
            "rent_model": "fixed",
            "deposit_amount": "0.00",
        },
    }
    return Event(
        id=id_ or eid(seq),
        workspace_id=WS,
        seq=seq,
        type=type_,
        entity_type=type_.split(".")[0],
        entity_id=entity_id,
        recorded_at=datetime(2026, 11, 5, 9, seq, tzinfo=UTC),
        actor_type="user",
        actor_id="user:1",
        correlation_id=UUID(int=seq),
        payload=payloads[type_],
        **kw,
    )


def ids(entries):
    return [(e.event_id, e.root_event_id) for e in entries]


def test_correction_moving_an_event_to_another_property():
    # The disbursement was recorded against P2 by mistake; the correction puts it on P1.
    wrong = event(1, "loan.disbursed", LOAN, property_id=P2, effective_date=date(2024, 4, 30))
    fixed = event(
        2,
        "loan.disbursed",
        LOAN,
        property_id=P1,
        effective_date=date(2024, 4, 30),
        corrects_event_id=wrong.id,
    )
    p1 = build_history([wrong, fixed], TimelineScope(property_id=P1))
    assert ids(p1) == [(fixed.id, wrong.id)]
    assert p1[0].corrected and p1[0].original_effective_date == date(2024, 4, 30)
    assert build_history([wrong, fixed], TimelineScope(property_id=P2)) == []


def test_correction_moving_an_event_to_another_unit():
    wrong = event(
        1,
        "tenancy.started",
        TENANCY,
        property_id=P1,
        unit_id=U_PARK,
        effective_date=date(2024, 7, 1),
    )
    fixed = event(
        2,
        "tenancy.started",
        TENANCY,
        property_id=P1,
        unit_id=U_FLAT,
        effective_date=date(2024, 7, 1),
        corrects_event_id=wrong.id,
    )
    log = [wrong, fixed]
    assert ids(build_history(log, TimelineScope(property_id=P1, unit_id=U_FLAT))) == [
        (fixed.id, wrong.id)
    ]
    assert build_history(log, TimelineScope(property_id=P1, unit_id=U_PARK)) == []
    assert ids(build_history(log, TimelineScope(property_id=P1))) == [(fixed.id, wrong.id)]


def test_correction_adding_a_date_makes_a_milestone_with_no_original_date():
    undated = event(1, "tenancy.created", TENANCY, property_id=P1, unit_id=U_FLAT)
    dated = event(
        2,
        "tenancy.created",
        TENANCY,
        property_id=P1,
        unit_id=U_FLAT,
        effective_date=date(2024, 6, 10),
        corrects_event_id=undated.id,
    )
    [entry] = build_history([undated, dated], TimelineScope(property_id=P1))
    assert entry.corrected and entry.original_effective_date is None
    assert entry.effective_date == date(2024, 6, 10)


def test_correction_removing_the_date_removes_the_milestone():
    dated = event(
        1,
        "tenancy.created",
        TENANCY,
        property_id=P1,
        unit_id=U_FLAT,
        effective_date=date(2024, 6, 10),
    )
    undated = event(
        2, "tenancy.created", TENANCY, property_id=P1, unit_id=U_FLAT, corrects_event_id=dated.id
    )
    assert build_history([dated, undated], TimelineScope(property_id=P1)) == []


def test_same_day_orders_by_root_seq_in_every_input_order():
    # Root 1 is corrected by 4 (recorded last); 2 and 3 share the date. Order: 1's chain, 2, 3.
    # Ids run against seq, so sorting by id instead of seq would reverse them.
    day = date(2024, 7, 1)
    a = event(
        1,
        "tenancy.started",
        TENANCY,
        id_=eid(93),
        property_id=P1,
        effective_date=date(2024, 7, 15),
    )
    b = event(2, "loan.disbursed", LOAN, id_=eid(92), property_id=P1, effective_date=day)
    c = event(3, "property.purchase_notarized", P1, id_=eid(91), property_id=P1, effective_date=day)
    a2 = event(
        4,
        "tenancy.started",
        TENANCY,
        id_=eid(90),
        property_id=P1,
        effective_date=day,
        corrects_event_id=a.id,
    )
    expected = [(a2.id, a.id), (b.id, b.id), (c.id, c.id)]
    for perm in itertools.permutations([a, b, c, a2]):
        assert ids(build_history(perm, TimelineScope(property_id=P1))) == expected


def test_broken_logs_are_refused():
    a = event(1, "loan.disbursed", LOAN, property_id=P1, effective_date=date(2024, 4, 30))

    def fix(seq, of):
        return event(
            seq,
            "loan.disbursed",
            LOAN,
            property_id=P1,
            effective_date=date(2024, 5, 1),
            corrects_event_id=of,
        )

    scope = TimelineScope(property_id=P1)
    with pytest.raises(ValueError, match="not in the input"):
        build_history([fix(2, eid(99))], scope)
    with pytest.raises(ValueError, match="more than once"):
        build_history([a, fix(2, a.id), fix(3, a.id)], scope)
    with pytest.raises(ValueError, match="cycle"):
        build_history([fix(2, eid(3)), fix(3, eid(2))], scope)
    with pytest.raises(ValueError, match="twice"):
        build_history([a, a], scope)
