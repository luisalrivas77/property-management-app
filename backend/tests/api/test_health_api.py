"""The key-figure routes (docs/phases/phase-9.md §13; phase 9 step 1): status codes, the read
shapes, the refusals and workspace isolation. The service behaviour is
tests/db/test_services_health_snapshots.py; the arithmetic is the phase 9 rule fixtures.
The routes read today's date, so these tests use months of 2026 that have ended whatever day
they run on."""

import uuid

import pytest
from api_support import ok

pytestmark = pytest.mark.db


def test_the_health_reads_answer_for_an_empty_workspace(client_a):
    got = ok(client_a.get("/api/health"))
    assert {"window", "newest", "marks", "todos", "proposals"} <= got.keys()
    # No account leaves a month open, so the months from January 2026 count as closed.
    assert got["window"]["months"][0] == "2026-01" or len(got["window"]["months"]) == 12
    unknown = client_a.get("/api/health", params={"scenario": str(uuid.uuid4())})
    assert unknown.status_code == 404

    months = ok(client_a.get("/api/health/months", params={"from": "2026-01", "to": "2026-03"}))
    assert [m["month"] for m in months] == ["2026-01", "2026-02", "2026-03"]
    assert all(m["snapshot_id"] is None and m["stored"] is None for m in months)
    backwards = client_a.get("/api/health/months", params={"from": "2026-03", "to": "2026-01"})
    assert (backwards.status_code, backwards.json()["codes"]) == (422, ["health.range_invalid"])
    for params in ({"from": "2026-1", "to": "2026-03"}, {"from": "2026-01"}):
        assert client_a.get("/api/health/months", params=params).status_code == 422

    assert ok(client_a.get("/api/health/net-worth-series")) == []


def test_a_snapshot_is_refused_with_its_code(client_a, client_b):
    """Before January 2026 no month is closed; a malformed month is a 422 of the body."""
    refused = client_a.post("/api/health/snapshots", json={"month": "2025-12"})
    assert (refused.status_code, refused.json()["codes"]) == (422, ["health.month_not_closed"])
    assert client_a.post("/api/health/snapshots", json={"month": "Januar"}).status_code == 422
    assert client_a.post("/api/health/snapshots", json={}).status_code == 422
    # Nothing was stored, in either workspace.
    for client in (client_a, client_b):
        months = ok(client.get("/api/health/months", params={"from": "2026-01", "to": "2026-01"}))
        assert months[0]["snapshot_id"] is None
