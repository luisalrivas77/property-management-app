"""Sign-in and passkey events in the event log (phase 2 spec §10, D28): entity type `user`,
actor the user, no effective date, payloads with ids, a label and a count only."""

from uuid import UUID

from auth_support import ORIGIN, RP_ID, add_passkey, enroll, login, new_client, unique_email
from authenticator import SoftAuthenticator


def _events(sql, household):
    rows = sql(
        "SELECT type, entity_type, entity_id, actor_type, actor_id, effective_date, payload,"
        " correlation_id FROM event WHERE entity_type = 'user' ORDER BY seq",
        workspace=household.workspace_id,
    )
    return rows


def test_the_auth_flows_append_user_events(client, household, authenticator, sql, api):
    assert enroll(client, household.token, authenticator).status_code == 201
    laptop = SoftAuthenticator(rp_id=RP_ID, origin=ORIGIN)
    second = add_passkey(client, laptop, label="Laptop").json()
    phone = [p for p in client.get("/api/auth/passkeys").json() if p["label"] == "iPhone"][0]
    assert client.patch(f"/api/auth/passkeys/{second['id']}", json={"label": "Mac"}).is_success
    invite = client.post(
        "/api/auth/invites", json={"email": unique_email("ana"), "display_name": "Ana"}
    ).json()
    other = new_client(api)
    assert login(other, laptop).is_success
    assert client.delete(f"/api/auth/passkeys/{second['id']}").status_code == 204
    browser = new_client(api)
    assert login(browser, authenticator).is_success
    assert browser.post("/api/auth/logout").status_code == 204
    assert client.post("/api/auth/logout-all").status_code == 204

    events = _events(sql, household)
    assert [e[0] for e in events] == [
        "user.enrolled",
        "user.passkey_enrolled",
        "user.signed_in",
        "user.passkey_enrolled",
        "user.passkey_renamed",
        "user.invited",
        "user.signed_in",
        "user.passkey_removed",
        "user.signed_in",
        "user.signed_out",
        "user.sessions_revoked",
    ]
    user = str(household.user_id)
    for _type, entity_type, entity_id, actor_type, actor_id, effective, _p, _c in events:
        assert (entity_type, str(entity_id), actor_type, actor_id, effective) == (
            "user",
            user,
            "user",
            f"user:{user}",
            None,
        )
    payloads = [e[6] for e in events]
    assert payloads[0] == {"invite_id": payloads[0]["invite_id"]}
    assert payloads[1] == {"passkey_id": phone["id"], "label": "iPhone"}
    assert payloads[2] == {"passkey_id": phone["id"]}
    assert payloads[3] == {"passkey_id": second["id"], "label": "Laptop"}
    assert payloads[4] == payloads[7] == {"passkey_id": second["id"]}
    assert payloads[5] == {"invite_id": invite["id"], "invited_user_id": invite["user"]["id"]}
    assert payloads[6] == {"passkey_id": second["id"]}
    assert payloads[9] == {}
    # The enrolling browser's session was still open (the other two were already ended).
    assert payloads[10] == {"count": 1}
    # Enrollment's three events are one command.
    assert len({e[7] for e in events[:3]}) == 1
    assert UUID(payloads[0]["invite_id"])


def test_a_failed_sign_in_appends_nothing(client, household, authenticator, sql):
    stranger = SoftAuthenticator(rp_id=RP_ID, origin=ORIGIN)
    assert login(client, stranger).status_code == 401
    assert _events(sql, household) == []
