"""The phase 1 done criterion through the API (§10.1): the timeline.yaml scenario replayed
over HTTP, then GET /api/properties/{id}/history compared with every case of the fixture,
the same comparison tests/db/test_history.py makes through the services.

The scenario is entered request by request, as the browser will: property, units, loan,
tenant, tenancy, corrections, rent rows, deposit, the purchase contract uploaded and linked,
the parking space let, a second property, a label change. The services' two deviations from
the literal fixture (tests/db/test_history.py) apply here too:
1. the flat's first rent row is dated before the wrong move-in, so it is scheduled after the
   move-in correction (History orders by (date, root seq), so it is unchanged);
2. the second property also gets its possession milestone (purchase_date is required).
"""

import hashlib
import uuid

import pytest
from api_support import create, ok, upload
from fixture_models import TimelineFixture, load

from hausbuch.schemas.events import EVENT_CATALOG

pytestmark = pytest.mark.db

FX = load("timeline")
assert isinstance(FX, TimelineFixture)
FIXTURE_EVENTS = {e.id: e for e in FX.events}
FIXTURE_TENANTS = {
    "tenant_flat": uuid.UUID("0000000d-0000-4000-8000-000000000001"),
    "tenant_parking": uuid.UUID("0000000d-0000-4000-8000-000000000002"),
}
FIXTURE_DOCUMENT = uuid.UUID("00000d00-0000-4000-8000-000000000001")
VAULT_TYPES = {"document.uploaded", "document.linked"}
ADDRESS = {"street": "Lindenstraße", "house_number": "12", "postal_code": "10115", "city": "Berlin"}
CONTRACT = b"%PDF-1.7 Kaufvertrag Wohnung Lindenstrasse 12, WE 3"


def build_scenario_over_http(client) -> dict[str, str]:
    """The fixture's story, one request per command. Returns fixture name -> stored id."""
    ids: dict[str, str] = {}
    flat = create(
        client,
        "/api/properties",
        {
            "label": "Wohnung Lindenstraße",
            "kind": "etw",
            "address": ADDRESS,
            "build_year": 1994,
            "notarized_on": "2024-03-15",
            "purchase_date": "2024-05-02",  # typo, corrected twice below
            "purchase_price": "289000.00",
        },
    )
    ids["property_flat"] = flat["id"]
    ids["unit_flat"] = create(
        client,
        "/api/units",
        {
            "property_id": flat["id"],
            "label": "WE 3",
            "floor_area_m2": "68.40",
            "rooms": "2.5",
            "mea": {"numerator": 85, "denominator": 1000},
        },
    )["id"]
    ids["unit_parking"] = create(
        client,
        "/api/units",
        {"property_id": flat["id"], "label": "TG-Stellplatz 12", "floor_area_m2": "12.50"},
    )["id"]
    ids["loan"] = create(
        client,
        "/api/loans",
        {
            "property_id": flat["id"],
            "lender": "Sparkasse Musterstadt",
            "principal": "240000.00",
            "nominal_rate": "0.0365",
            "monthly_payment": "1130.00",
            "disbursed_on": "2024-04-30",
            "first_payment_on": "2024-05-30",
            "fixed_until": "2034-04-30",
            "signed_on": "2024-03-01",
        },
    )["id"]

    ids["tenant_flat"] = create(client, "/api/tenants", {"full_name": "Anna Beispiel"})["id"]
    ids["tenancy_flat"] = create(
        client,
        "/api/tenancies",
        {
            "unit_id": ids["unit_flat"],
            "parties": [{"tenant_id": ids["tenant_flat"], "role": "primary"}],
            "signed_on": "2024-06-10",
            "start_date": "2024-07-15",  # typo, corrected below
            "rent_model": "staffel",
            "deposit_amount": "2850.00",
            "deposit_form": "cash_account",
        },
    )["id"]
    tenancy = f"/api/tenancies/{ids['tenancy_flat']}"
    ok(client.post(f"{tenancy}/corrections/move-in", json={"new_date": "2024-07-01"}))
    for day, cold, reason in (
        ("2024-07-01", "950.00", "initial"),
        ("2025-07-01", "1000.00", "staffel_step"),
    ):
        create(
            client,
            "/api/rent-schedule",
            {
                "tenancy_id": ids["tenancy_flat"],
                "effective_from": day,
                "cold_rent": cold,
                "operating_costs_prepayment": "220.00",
                "reason": reason,
            },
        )
        if reason == "initial":
            create(
                client,
                f"{tenancy}/deposit-received",
                {"amount": "2850.00", "received_on": "2024-07-01"},
            )

    # The purchase contract, into the vault and linked to the property.
    ids["document"] = ok(upload(client, CONTRACT), 201)["id"]
    create(
        client,
        f"/api/documents/{ids['document']}/links",
        {"target_type": "property", "target_id": flat["id"]},
    )

    possession = f"/api/properties/{flat['id']}/corrections/purchase-date"
    ok(client.post(possession, json={"new_date": "2024-05-11"}))  # still wrong
    ok(client.post(possession, json={"new_date": "2024-05-01"}))

    ids["tenant_parking"] = create(client, "/api/tenants", {"full_name": "Bert Beispiel"})["id"]
    ids["tenancy_parking"] = create(
        client,
        "/api/tenancies",
        {
            "unit_id": ids["unit_parking"],
            "parties": [{"tenant_id": ids["tenant_parking"], "role": "primary"}],
            "signed_on": "2025-08-20",
            "start_date": "2025-09-01",
            "rent_model": "fixed",
        },
    )["id"]

    ids["property_other"] = create(
        client,
        "/api/properties",
        {
            "label": "Haus Am Bach",
            "kind": "efh",
            "address": ADDRESS | {"street": "Am Bach", "house_number": "3"},
            "build_year": 2023,
            "notarized_on": "2025-01-10",
            "purchase_date": "2025-03-01",  # deviation 2
            "purchase_price": "520000.00",
        },
    )["id"]
    ok(client.patch(f"/api/properties/{flat['id']}", json={"label": "Lindenstraße 12, WE 3"}))
    return ids


def _substitute(value, ids: dict[str, str]):
    if isinstance(value, dict):
        return {k: _substitute(v, ids) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, ids) for v in value]
    return ids.get(value, value) if isinstance(value, str) else value


def test_done_criterion_history_over_http(client_a):
    scenario = build_scenario_over_http(client_a)
    named = {str(FX.ids[n]): s for n, s in scenario.items() if n in FX.ids}
    named |= {str(FIXTURE_TENANTS[n]): scenario[n] for n in FIXTURE_TENANTS}
    named[str(FIXTURE_DOCUMENT)] = scenario["document"]

    # Every stored event the fixture names, found through the audit-trail endpoints.
    stored = [
        e
        for query in (
            f"property_id={scenario['property_flat']}",
            f"property_id={scenario['property_other']}",
            f"entity_id={scenario['tenant_flat']}",
            f"entity_id={scenario['tenant_parking']}",
            f"entity_id={scenario['document']}",
        )
        for e in ok(client_a.get(f"/api/events?{query}"))
    ]
    by_key = {}
    for e in stored:
        key = (e["type"], e["entity_id"], e["effective_date"])
        assert key not in by_key, f"two stored events share {key}"
        by_key[key] = e
    pairs = {}
    for f in FX.events:
        date = None if f.effective_date is None else f.effective_date.isoformat()
        key = (f.type.value, named[str(f.entity_id)], date)
        assert key in by_key, f"fixture event seq {f.seq} ({f.type}) has no stored twin"
        pairs[f.id] = by_key[key]

    # Chains and History scopes match the fixture's, event for event.
    for fixture_id, twin in pairs.items():
        f = FIXTURE_EVENTS[fixture_id]
        expected = None if f.corrects_event_id is None else pairs[f.corrects_event_id]["id"]
        assert twin["corrects_event_id"] == expected, f.seq
        assert (twin["property_id"], twin["unit_id"]) == tuple(
            None if v is None else named[str(v)] for v in (f.property_id, f.unit_id)
        ), f.seq

    # Payloads, once ids are mapped (vault payloads describe the fixture's made-up file).
    id_map = dict(named)
    for fixture_id, twin in pairs.items():
        if "rent_schedule_id" in twin["payload"]:
            id_map[FIXTURE_EVENTS[fixture_id].payload["rent_schedule_id"]] = twin["payload"][
                "rent_schedule_id"
            ]
    for fixture_id, twin in pairs.items():
        f = FIXTURE_EVENTS[fixture_id]
        if f.type.value in VAULT_TYPES:
            continue
        model = EVENT_CATALOG[f.type].payload
        expected = model.model_validate(_substitute(f.payload, id_map))
        assert model.model_validate(twin["payload"]) == expected, f"seq {f.seq} {f.type}"
    uploaded = pairs[next(e.id for e in FX.events if e.type.value == "document.uploaded")]
    assert uploaded["payload"]["sha256"] == hashlib.sha256(CONTRACT).hexdigest()

    # History: every case of the fixture.
    extra_possession = None
    for case in FX.cases:
        property_id = named[str(case.scope.property_id)]
        query = "" if case.scope.unit_id is None else f"?unit_id={named[str(case.scope.unit_id)]}"
        entries = ok(client_a.get(f"/api/properties/{property_id}/history{query}"))

        if case.id == "other_property_history":  # deviation 2: one more, and only that one
            [extra_possession] = [
                e for e in entries if e["type"] == "property.possession_transferred"
            ]
            entries = [e for e in entries if e is not extra_possession]

        expected = [
            x.model_dump(mode="json")
            | {
                "event_id": pairs[x.event_id]["id"],
                "root_event_id": pairs[x.root_event_id]["id"],
                "entity_id": named[str(x.entity_id)],
            }
            for x in case.expect
        ]
        assert [{k: v for k, v in e.items() if k != "payload"} for e in entries] == expected, (
            case.id
        )
        twins = {t["id"]: t for t in pairs.values()}
        for e in entries:
            assert e["payload"] == twins[e["event_id"]]["payload"]

    assert extra_possession is not None
    assert (extra_possession["effective_date"], extra_possession["corrected"]) == (
        "2025-03-01",
        False,
    )


def test_history_shows_purchase_then_move_in_in_order(client_a):
    """The done criterion in one line: purchase -> financing -> possession -> move-in, with
    the dates as corrected and the corrections visible."""
    scenario = build_scenario_over_http(client_a)
    entries = ok(
        client_a.get(
            f"/api/properties/{scenario['property_flat']}/history?unit_id={scenario['unit_flat']}"
        )
    )
    milestones = [
        (e["type"], e["effective_date"], e["original_effective_date"]) for e in entries[:6]
    ]
    assert milestones == [
        ("loan.created", "2024-03-01", None),
        ("property.purchase_notarized", "2024-03-15", None),
        ("loan.disbursed", "2024-04-30", None),
        ("property.possession_transferred", "2024-05-01", "2024-05-02"),
        ("tenancy.created", "2024-06-10", None),
        ("tenancy.started", "2024-07-01", "2024-07-15"),
    ]
