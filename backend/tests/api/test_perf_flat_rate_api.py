"""/api/properties/{id}/flat-rate-check (phase-4.md D155) over HTTP: a flat_rate tenancy with
its Pauschale, decimal strings, a property bought before the first rule set still answers,
and workspace isolation. The route reads the real clock."""

from uuid import uuid4

import pytest
from api_support import create, new_property, new_tenancy, new_tenant, new_unit, ok

pytestmark = pytest.mark.db


def _flat_rate(client) -> tuple[str, str]:
    pid = new_property(client)["id"]  # bought in 2024: before the first rule set
    unit = new_unit(client, pid, label="Zimmer 1")
    tenant = new_tenant(client)
    tenancy = new_tenancy(client, unit["id"], tenant["id"], operating_costs_terms="flat_rate")
    create(
        client,
        "/api/rent-schedule",
        {
            "tenancy_id": tenancy["id"],
            "effective_from": "2024-07-01",
            "cold_rent": "400.00",
            "operating_costs_prepayment": "100.00",
            "reason": "initial",
        },
    )
    return pid, tenancy["id"]


def test_flat_rate_check(client_a):
    pid, tenancy_id = _flat_rate(client_a)
    got = ok(client_a.get(f"/api/properties/{pid}/flat-rate-check"))
    assert got["property_id"] == pid
    assert got["pauschalen_total"] == "100.00"  # money stays a decimal string
    assert got["raise_above"] == "10.00"
    [row] = got["tenancies"]
    assert (row["tenancy_id"], row["unit_label"], row["pauschale"]) == (
        tenancy_id,
        "Zimmer 1",
        "100.00",
    )
    assert (row["rise"], row["verdict"], row["set_by"]) == ("0.00", "none", "start")
    assert [r["basis"] for r in got["levels"][-1]["rates"]] == ["none"] * 4


def test_a_property_without_flat_rate_tenancies(client_a):
    pid = new_property(client_a)["id"]
    got = ok(client_a.get(f"/api/properties/{pid}/flat-rate-check"))
    assert (got["tenancies"], got["levels"], got["pauschalen_total"]) == ([], [], "0.00")


def test_another_workspace_sees_nothing(client_a, client_b):
    pid, _ = _flat_rate(client_a)
    response = client_b.get(f"/api/properties/{pid}/flat-rate-check")
    assert (response.status_code, response.json()["entity_type"]) == (404, "property")
    response = client_a.get(f"/api/properties/{uuid4()}/flat-rate-check")
    assert response.status_code == 404
