"""Tenants and tenancies over HTTP (§4.4, §4.5): erasure and its retention hold, the rent
schedule and its corrections, deposit, notice, move-out, overlaps, the transaction
boundary."""

from uuid import uuid4

import pytest
from api_support import (
    GOOD_IBAN,
    create,
    flat,
    new_property,
    new_tenancy,
    new_tenant,
    new_unit,
    ok,
    tenancy_body,
)

pytestmark = pytest.mark.db


# --- Tenants ------------------------------------------------------------------------


def test_tenant_round_trip(client_a):
    tenant = new_tenant(client_a, email="anna@example.org", iban=GOOD_IBAN)
    assert tenant["iban_masked"] == "DE89 **** 3000"
    assert ok(client_a.get(f"/api/tenants/{tenant['id']}")) == tenant
    assert ok(client_a.get("/api/tenants")) == [tenant]
    patched = ok(client_a.patch(f"/api/tenants/{tenant['id']}", json={"iban": None, "phone": "1"}))
    assert (patched["iban_masked"], patched["phone"], patched["email"]) == (
        None,
        "1",
        "anna@example.org",
    )
    [_, updated] = ok(client_a.get(f"/api/events?entity_id={tenant['id']}"))
    assert updated["payload"] == {"changed_fields": ["phone", "iban"]}  # names, never values


def test_an_invalid_contact_field_is_named_not_echoed(client_a):
    tenant = new_tenant(client_a)
    response = client_a.patch(f"/api/tenants/{tenant['id']}", json={"full_name": ""})
    assert response.status_code == 422  # TenantPatch: min_length


def test_erasure_respects_the_retention_hold(client_a):
    setup = flat(client_a)
    tenant = setup["tenant"]
    response = client_a.post(f"/api/tenants/{tenant['id']}/erase", json={})
    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "retention_hold"
    assert body["result"] == {"retention_class": "tenant_contact", "hold": "tenancy_active"}
    assert ok(client_a.get(f"/api/tenants/{tenant['id']}"))["full_name"] == "Anna Beispiel"

    applicant = new_tenant(client_a, full_name="Bert Bewerber", email="bert@example.org")
    erased = ok(client_a.post(f"/api/tenants/{applicant['id']}/erase", json={}))
    assert erased["erased_at"] is not None
    assert (erased["full_name"], erased["email"]) == (None, None)
    response = client_a.patch(f"/api/tenants/{applicant['id']}", json={"phone": "1"})
    assert (response.status_code, response.json()["error"]) == (409, "conflict")
    names = [t["full_name"] for t in ok(client_a.get("/api/tenants"))]
    assert names == ["Anna Beispiel", None]  # erased ones are listed, last


# --- Tenancies -------------------------------------------------------------------------


def test_create_with_initial_rent_and_read_the_schedule(client_a):
    prop = new_property(client_a)
    unit = new_unit(client_a, prop["id"])
    tenant = new_tenant(client_a)
    body = tenancy_body(
        unit["id"],
        [tenant["id"]],
        signed_on="2024-06-10",
        deposit_amount="2850.00",
        deposit_form="cash_account",
        deposit_iban=GOOD_IBAN,
        initial_rent={"cold_rent": "950.00", "operating_costs_prepayment": "220.00"},
    )
    tenancy = create(client_a, "/api/tenancies", body)
    assert tenancy["deposit_amount"] == "2850.00"
    assert tenancy["deposit_iban_masked"] == "DE89 **** 3000"
    assert tenancy["parties"] == [{"tenant_id": tenant["id"], "role": "primary"}]
    assert ok(client_a.get(f"/api/tenancies/{tenancy['id']}")) == tenancy
    assert ok(client_a.get(f"/api/units/{unit['id']}/tenancies")) == [tenancy]

    [row] = ok(client_a.get(f"/api/tenancies/{tenancy['id']}/rent-schedule"))
    assert (row["effective_from"], row["cold_rent"], row["reason"]) == (
        "2024-07-01",
        "950.00",
        "initial",
    )
    assert ok(client_a.get(f"/api/tenancies/{tenancy['id']}/rent?at=2025-01-01")) == row
    assert ok(client_a.get(f"/api/tenancies/{tenancy['id']}/rent?at=2024-06-30")) is None
    response = client_a.get(f"/api/tenancies/{tenancy['id']}/rent")
    assert response.status_code == 422  # `at` is required


def test_an_invalid_party_set_writes_nothing(client_a):
    """§10.1 transaction boundary: two primaries -> 422, no tenancy row, no event."""
    prop = new_property(client_a)
    unit = new_unit(client_a, prop["id"])
    a, b = new_tenant(client_a), new_tenant(client_a, full_name="Bert")
    body = tenancy_body(unit["id"], [a["id"]])
    body["parties"].append({"tenant_id": b["id"], "role": "primary"})
    response = client_a.post("/api/tenancies", json=body)
    assert response.status_code == 422
    assert response.json()["codes"] == ["tenancy.exactly_one_primary"]
    assert ok(client_a.get(f"/api/units/{unit['id']}/tenancies")) == []
    assert ok(client_a.get("/api/events?entity_type=tenancy")) == []


def test_overlapping_tenancies_conflict(client_a):
    setup = flat(client_a)
    other = new_tenant(client_a, full_name="Bert")
    response = client_a.post(
        "/api/tenancies",
        json=tenancy_body(setup["unit"]["id"], [other["id"]], start_date="2025-01-01"),
    )
    assert response.status_code == 409
    body = response.json()
    assert (body["error"], body["code"], body["constraint"]) == (
        "conflict",
        "tenancy.overlap",
        "tenancy_no_overlap",
    )


def test_rent_rows_and_their_corrections(client_a):
    tenancy = flat(client_a)["tenancy"]
    step = create(
        client_a,
        "/api/rent-schedule",
        {
            "tenancy_id": tenancy["id"],
            "effective_from": "2025-07-01",
            "cold_rent": "1000.00",
            "operating_costs_prepayment": "220.00",
            "reason": "staffel_step",
        },
    )
    path = f"/api/rent-schedule/{step['id']}/corrections"
    correction = create(client_a, path, {"cold_rent": "1010.00"})
    assert (correction["supersedes_id"], correction["reason"]) == (step["id"], "correction")
    assert (correction["effective_from"], correction["cold_rent"]) == ("2025-07-01", "1010.00")

    response = client_a.post(path, json={"cold_rent": "1020.00"})
    assert (response.status_code, response.json()["error"]) == (409, "conflict")
    noop = client_a.post(f"/api/rent-schedule/{correction['id']}/corrections", json={})
    assert (noop.status_code, noop.json()) == (200, correction)  # nothing to correct

    rows = ok(client_a.get(f"/api/tenancies/{tenancy['id']}/rent-schedule"))
    assert [r["id"] for r in rows] == [step["id"], correction["id"]]  # superseded row kept

    response = client_a.post(
        "/api/rent-schedule",
        json={
            "tenancy_id": tenancy["id"],
            "effective_from": "2024-06-01",
            "cold_rent": "900.00",
            "operating_costs_prepayment": "0",
            "reason": "agreement",
        },
    )
    assert response.json()["codes"] == ["rent_schedule.outside_tenancy"]

    response = client_a.post(
        "/api/rent-schedule",
        json={
            "tenancy_id": tenancy["id"],
            "effective_from": "2025-01-01",
            "cold_rent": "900.00",
            "operating_costs_prepayment": "0",
            "reason": "correction",
            "supersedes_id": step["id"],
        },
    )
    assert (response.status_code, response.json()["error"]) == (422, "invalid")


def test_deposit_notice_move_out_and_move_in_correction(client_a):
    tenancy = flat(client_a)["tenancy"]
    base = f"/api/tenancies/{tenancy['id']}"
    event = create(
        client_a, f"{base}/deposit-received", {"amount": "950.00", "received_on": "2024-07-01"}
    )
    assert (event["type"], event["payload"], event["effective_date"]) == (
        "tenancy.deposit_received",
        {"amount": "950.00"},
        "2024-07-01",
    )

    moved = ok(client_a.post(f"{base}/corrections/move-in", json={"new_date": "2024-06-15"}))
    assert moved["start_date"] == "2024-06-15"

    response = client_a.post(
        f"{base}/notice",
        json={"given_by": "tenant", "given_on": "2026-03-01", "ends_on": "2026-02-01"},
    )
    assert (response.status_code, response.json()["error"]) == (422, "invalid")
    noticed = ok(
        client_a.post(
            f"{base}/notice",
            json={"given_by": "tenant", "given_on": "2026-03-01", "ends_on": "2026-05-31"},
        )
    )
    assert noticed["notice_given_on"] == "2026-03-01"
    response = client_a.post(
        f"{base}/notice",
        json={"given_by": "tenant", "given_on": "2026-03-02", "ends_on": "2026-05-31"},
    )
    assert (response.status_code, response.json()["error"]) == (409, "conflict")

    ended = ok(client_a.post(f"{base}/end", json={"end_date": "2026-05-31"}))
    assert ended["end_date"] == "2026-05-31"
    response = client_a.post(f"{base}/end", json={"end_date": "2026-06-30"})
    assert (response.status_code, response.json()["error"]) == (409, "conflict")


def test_a_move_in_correction_moves_the_first_rent_row(client_a):
    """§10.8 item 6 over HTTP: 15.07 -> 01.07; History shows the rent at 01.07, corrected."""
    prop = new_property(client_a)
    unit = new_unit(client_a, prop["id"])
    tenant = new_tenant(client_a)
    body = tenancy_body(
        unit["id"],
        [tenant["id"]],
        start_date="2024-07-15",
        initial_rent={"cold_rent": "950.00", "operating_costs_prepayment": "220.00"},
    )
    tenancy = create(client_a, "/api/tenancies", body)
    base = f"/api/tenancies/{tenancy['id']}"
    ok(client_a.post(f"{base}/corrections/move-in", json={"new_date": "2024-07-01"}))

    moved, first = ok(client_a.get(f"{base}/rent-schedule"))  # effective order
    assert (moved["effective_from"], moved["reason"], moved["supersedes_id"]) == (
        "2024-07-01",
        "correction",
        first["id"],
    )
    assert ok(client_a.get(f"{base}/rent?at=2024-07-01")) == moved
    history = ok(client_a.get(f"/api/properties/{prop['id']}/history"))
    [rent] = [e for e in history if e["type"] == "tenancy.rent_scheduled"]
    assert (rent["effective_date"], rent["corrected"], rent["original_effective_date"]) == (
        "2024-07-01",
        True,
        "2024-07-15",
    )


def test_unknown_tenancy_reads_are_404(client_a):
    missing = uuid4()
    for sub in ("", "/rent-schedule", "/rent?at=2025-01-01", "/documents"):
        response = client_a.get(f"/api/tenancies/{missing}{sub}")
        assert (response.status_code, response.json()["entity_type"]) == (404, "tenancy"), sub
    response = client_a.post(f"/api/tenancies/{missing}/end", json={"end_date": "2026-01-01"})
    assert response.status_code == 404


def test_a_second_tenancy_after_move_out(client_a):
    setup = flat(client_a)
    ok(
        client_a.post(
            f"/api/tenancies/{setup['tenancy']['id']}/end", json={"end_date": "2025-06-30"}
        )
    )
    other = new_tenant(client_a, full_name="Bert")
    second = new_tenancy(client_a, setup["unit"]["id"], other["id"], start_date="2025-07-01")
    listed = ok(client_a.get(f"/api/units/{setup['unit']['id']}/tenancies"))
    assert [t["id"] for t in listed] == [setup["tenancy"]["id"], second["id"]]
