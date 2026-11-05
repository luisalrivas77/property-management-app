"""Owners over HTTP (§4.1), and the cross-cutting basics shown on the simplest resource:
sessions, isolation, money as strings, the patch semantics, correlation ids."""

from uuid import uuid4

import pytest
from api_support import create, ok

pytestmark = pytest.mark.db


def test_create_read_list_patch_round_trip(client_a):
    owner = create(
        client_a,
        "/api/owners",
        {
            "full_name": "Luis Rivas",
            "tax_id_ref": "secret:owner/luis",
            "marginal_tax_rate": "0.4200",
        },
    )
    assert owner["full_name"] == "Luis Rivas"
    assert owner["marginal_tax_rate"] == "0.4200"  # a JSON string, never a number
    assert ok(client_a.get(f"/api/owners/{owner['id']}")) == owner
    assert ok(client_a.get("/api/owners")) == [owner]

    patched = ok(client_a.patch(f"/api/owners/{owner['id']}", json={"marginal_tax_rate": None}))
    assert patched["marginal_tax_rate"] is None  # null clears
    assert patched["tax_id_ref"] == "secret:owner/luis"  # absent = unchanged
    assert patched["full_name"] == "Luis Rivas"


def test_patch_cannot_null_a_required_field(client_a):
    owner = create(client_a, "/api/owners", {"full_name": "Luis Rivas"})
    response = client_a.patch(f"/api/owners/{owner['id']}", json={"full_name": None})
    assert response.status_code == 422
    assert response.json()["error"] == "validation"


def test_money_must_be_a_string(client_a):
    response = client_a.post("/api/owners", json={"full_name": "x", "marginal_tax_rate": 0.42})
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "validation"
    assert body["detail"][0]["loc"] == ["body", "marginal_tax_rate"]
    assert set(body["detail"][0]) == {"loc", "msg", "type"}


def test_unknown_and_foreign_ids_are_404(client_a, client_b):
    owner = create(client_a, "/api/owners", {"full_name": "Luis Rivas"})
    for client, owner_id in ((client_a, uuid4()), (client_b, owner["id"])):
        response = client.get(f"/api/owners/{owner_id}")
        assert response.status_code == 404
        body = response.json()
        assert (body["error"], body["entity_type"], body["entity_id"]) == (
            "not_found",
            "owner",
            str(owner_id),
        )
        assert client.patch(f"/api/owners/{owner_id}", json={"full_name": "x"}).status_code == 404
    assert ok(client_b.get("/api/owners")) == []


def test_every_request_needs_a_session(anonymous):
    response = anonymous.get("/api/owners")
    assert response.status_code == 401
    assert response.json()["error"] == "unauthenticated"
    assert anonymous.post("/api/owners", json={"full_name": "x"}).status_code == 401


def test_events_carry_the_request_id_and_the_user(client_a, auth_a):
    request_id = str(uuid4())
    response = client_a.post(
        "/api/owners", json={"full_name": "Luis Rivas"}, headers={"X-Request-Id": request_id}
    )
    owner = ok(response, 201)
    assert response.headers["X-Request-Id"] == request_id
    [event] = ok(client_a.get(f"/api/events?entity_id={owner['id']}"))
    assert event["correlation_id"] == request_id
    assert (event["actor_type"], event["actor_id"]) == ("user", f"user:{auth_a.user_id}")


def test_a_request_id_that_is_not_a_uuid_is_replaced(client_a):
    response = client_a.post(
        "/api/owners", json={"full_name": "Luis Rivas"}, headers={"X-Request-Id": "not-a-uuid"}
    )
    owner = ok(response, 201)
    fresh = response.headers["X-Request-Id"]
    assert fresh != "not-a-uuid"
    [event] = ok(client_a.get(f"/api/events?entity_id={owner['id']}"))
    assert event["correlation_id"] == fresh
