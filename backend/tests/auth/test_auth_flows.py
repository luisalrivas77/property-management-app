"""Passkey flows end to end over HTTP, with the software authenticator (§6.3-§6.7, §10.2):
bootstrap -> enroll -> me; login -> me -> logout -> 401; logout-all; a second passkey; the
spouse's invite; last-passkey delete refused; invite reuse refused; deleting a passkey ends
its sessions (§10.5)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from authenticator import SoftAuthenticator
from conftest import (
    ORIGIN,
    RP_ID,
    add_passkey,
    enroll,
    login,
    new_client,
    session_cookie,
    unique_email,
)

from hausbuch.api.commands import Commands
from hausbuch.schemas.enums import PropertyKind
from hausbuch.schemas.property import Address, PropertyCreate
from hausbuch.services.properties import create_property, list_properties

FLAT = PropertyCreate(
    label="Wohnung Lindenstraße",
    kind=PropertyKind.ETW,
    address=Address(street="Lindenstraße", house_number="12", postal_code="10115", city="Berlin"),
    build_year=1994,
    notarized_on=date(2024, 3, 15),
    purchase_date=date(2024, 5, 2),
    purchase_price=Decimal("289000.00"),
)


def test_bootstrap_enroll_me(client, household, authenticator, sql):
    response = enroll(client, household.token, authenticator, label="iPhone von Luis")

    assert response.status_code == 201, response.text
    me = response.json()
    assert me["user"]["id"] == str(household.user_id)
    assert me["user"]["email"] == household.email
    assert me["workspace"]["id"] == str(household.workspace_id)
    assert me["workspace"]["name"] == "Haushalt Rivas"
    assert me["session"]["passkey_id"] is not None
    assert session_cookie(client)
    assert client.get("/api/auth/me").json() == me

    # The registration options named the user by app_user.id (§6.1), and what was stored.
    assert authenticator.user_handle == household.user_id.bytes
    [row] = sql(
        "SELECT credential_id, sign_count, aaguid, transports, backup_eligible, backed_up, label"
        " FROM auth_passkey WHERE user_id = %s",
        (household.user_id,),
        workspace=household.workspace_id,
    )
    assert row == (
        authenticator.credential_id,
        0,
        authenticator.aaguid,
        ["hybrid", "internal"],
        True,
        True,
        "iPhone von Luis",
    )
    [[used_at]] = sql(
        "SELECT used_at FROM auth_invite WHERE user_id = %s",
        (household.user_id,),
        workspace=household.workspace_id,
    )
    assert used_at is not None


def test_registration_options_follow_the_spec(client, household):
    options = client.post("/api/auth/enroll/options", json={"token": household.token}).json()
    assert options["rp"] == {"id": RP_ID, "name": "Hausbuch"}
    assert options["attestation"] == "none"
    assert options["authenticatorSelection"]["residentKey"] == "required"
    assert options["authenticatorSelection"]["userVerification"] == "required"
    assert options["user"]["name"] == household.email
    assert options["user"]["displayName"] == "Luis"
    assert options["excludeCredentials"] == []
    assert len(options["challenge"]) == 43  # 32 random bytes, base64url
    assert {p["alg"] for p in options["pubKeyCredParams"]} >= {-7, -8, -257}


def test_login_me_logout(client, household, authenticator):
    assert enroll(client, household.token, authenticator).status_code == 201
    browser = new_client(client.app)  # a fresh browser: no cookies

    options = browser.post("/api/auth/login/options").json()
    assert options["allowCredentials"] == []  # discoverable credentials
    assert options["userVerification"] == "required"
    assert options["rpId"] == RP_ID
    response = browser.post(
        "/api/auth/login/verify", json={"credential": authenticator.assertion(options)}
    )
    assert response.status_code == 200, response.text
    assert response.json()["user"]["id"] == str(household.user_id)
    assert browser.get("/api/auth/me").status_code == 200

    assert browser.post("/api/auth/logout").status_code == 204
    assert session_cookie(browser) is None  # the cookie is cleared
    assert browser.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me").status_code == 200  # the enrollment session is untouched


def test_a_revoked_session_stays_revoked_even_with_the_old_cookie(client, household, authenticator):
    assert enroll(client, household.token, authenticator).status_code == 201
    token = session_cookie(client)
    assert client.post("/api/auth/logout").status_code == 204
    replay = new_client(client.app)
    replay.cookies.set("__Host-hb_session", token)
    assert replay.get("/api/auth/me").status_code == 401


def test_every_sign_in_opens_a_new_session(client, household, authenticator, sql):
    assert enroll(client, household.token, authenticator).status_code == 201
    first = session_cookie(client)
    assert login(client, authenticator).status_code == 200
    assert session_cookie(client) != first
    rows = sql(
        "SELECT count(*) FROM auth_session WHERE user_id = %s",
        (household.user_id,),
        workspace=household.workspace_id,
    )
    assert rows == [(2,)]


def test_logout_all_revokes_every_session_of_the_user(client, household, authenticator):
    assert enroll(client, household.token, authenticator).status_code == 201
    other = new_client(client.app)
    assert login(other, authenticator).status_code == 200

    assert other.post("/api/auth/logout-all").status_code == 204
    assert other.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_second_passkey_list_rename(signed_in, household):
    laptop = SoftAuthenticator(rp_id=RP_ID, origin=ORIGIN, backed_up=False, backup_eligible=False)
    options = signed_in.post("/api/auth/passkeys/options").json()
    assert len(options["excludeCredentials"]) == 1  # the phone's credential
    credential = laptop.register(options)
    response = signed_in.post(
        "/api/auth/passkeys/verify", json={"credential": credential, "label": "Laptop"}
    )
    assert response.status_code == 201, response.text
    passkey = response.json()
    assert passkey["label"] == "Laptop" and passkey["backed_up"] is False
    assert set(passkey) == {"id", "label", "created_at", "last_used_at", "backed_up", "aaguid"}

    listed = signed_in.get("/api/auth/passkeys").json()
    assert [p["label"] for p in listed] == ["iPhone", "Laptop"]

    renamed = signed_in.patch(f"/api/auth/passkeys/{passkey['id']}", json={"label": "MacBook"})
    assert renamed.status_code == 200 and renamed.json()["label"] == "MacBook"
    assert signed_in.patch(f"/api/auth/passkeys/{uuid4()}", json={"label": "x"}).status_code == 404

    # The laptop signs in on its own.
    browser = new_client(signed_in.app)
    assert login(browser, laptop).json()["session"]["passkey_id"] == passkey["id"]


def test_the_same_authenticator_cannot_be_registered_twice(signed_in, authenticator):
    assert add_passkey(signed_in, authenticator).status_code == 409


def test_last_passkey_delete_is_refused(signed_in):
    [passkey] = signed_in.get("/api/auth/passkeys").json()
    assert signed_in.delete(f"/api/auth/passkeys/{passkey['id']}").status_code == 409
    assert signed_in.get("/api/auth/passkeys").json() == [passkey]
    assert signed_in.get("/api/auth/me").status_code == 200


def test_deleting_a_passkey_ends_the_sessions_it_opened(signed_in, household, sql):
    """§10.5: revoke and detach its sessions, then delete it, in one transaction."""
    laptop = SoftAuthenticator(rp_id=RP_ID, origin=ORIGIN)
    laptop_passkey = add_passkey(signed_in, laptop).json()
    laptop_browser = new_client(signed_in.app)
    assert login(laptop_browser, laptop).status_code == 200
    laptop_session = laptop_browser.get("/api/auth/me").json()["session"]["id"]

    assert signed_in.delete(f"/api/auth/passkeys/{laptop_passkey['id']}").status_code == 204

    assert laptop_browser.get("/api/auth/me").status_code == 401
    assert signed_in.get("/api/auth/me").status_code == 200  # the phone's session lives on
    [(revoked_at, passkey_id)] = sql(
        "SELECT revoked_at, passkey_id FROM auth_session WHERE id = %s",
        (laptop_session,),
        workspace=household.workspace_id,
    )
    assert revoked_at is not None and passkey_id is None
    assert login(new_client(signed_in.app), laptop).status_code == 401  # the key is gone


def test_deleting_the_passkey_of_the_current_session_signs_out(signed_in, household):
    laptop = SoftAuthenticator(rp_id=RP_ID, origin=ORIGIN)
    assert add_passkey(signed_in, laptop).status_code == 201
    phone_passkey = signed_in.get("/api/auth/me").json()["session"]["passkey_id"]
    response = signed_in.delete(f"/api/auth/passkeys/{phone_passkey}")
    assert response.status_code == 204
    assert session_cookie(signed_in) is None
    assert signed_in.get("/api/auth/me").status_code == 401


def test_one_member_cannot_touch_the_others_passkeys(signed_in, household):
    response = signed_in.post(
        "/api/auth/invites", json={"email": unique_email("ana"), "display_name": "Ana"}
    )
    token = response.json()["enrollment_url"].split("#token=")[1]
    spouse = new_client(signed_in.app)
    spouse_device = SoftAuthenticator(rp_id=RP_ID, origin=ORIGIN)
    assert enroll(spouse, token, spouse_device).status_code == 201
    assert add_passkey(spouse, SoftAuthenticator(rp_id=RP_ID, origin=ORIGIN)).status_code == 201

    [mine] = signed_in.get("/api/auth/passkeys").json()
    theirs = spouse.get("/api/auth/passkeys").json()
    assert mine["id"] not in {p["id"] for p in theirs}
    assert spouse.delete(f"/api/auth/passkeys/{mine['id']}").status_code == 404
    assert spouse.patch(f"/api/auth/passkeys/{mine['id']}", json={"label": "x"}).status_code == 404
    assert signed_in.get("/api/auth/passkeys").json() == [mine]


def test_invite_spouse_enroll_both_see_the_same_property(signed_in, household, api):
    email = unique_email("ana")
    response = signed_in.post(
        "/api/auth/invites", json={"email": email.upper(), "display_name": "Ana"}
    )
    assert response.status_code == 201, response.text
    invite = response.json()
    assert invite["user"]["email"] == email  # stored lower-case
    assert invite["enrollment_url"].startswith(f"{ORIGIN}/enroll#token=")
    pending = signed_in.get("/api/auth/invites").json()
    assert [i["id"] for i in pending] == [invite["id"]]
    assert pending[0]["enrollment_url"] is None  # the URL is shown once

    token = invite["enrollment_url"].split("#token=")[1]
    spouse = new_client(api)
    me = enroll(spouse, token, SoftAuthenticator(rp_id=RP_ID, origin=ORIGIN), label="Pixel")
    assert me.status_code == 201, me.text
    assert me.json()["workspace"]["id"] == str(household.workspace_id)
    assert me.json()["user"]["id"] == invite["user"]["id"]
    assert signed_in.get("/api/auth/invites").json() == []  # redeemed

    # Both members' sessions resolve to the same workspace, as different actors.
    resolver = api.state.session_resolver
    now = datetime.now(UTC)
    luis = resolver.resolve(session_cookie(signed_in), now=now)
    ana = resolver.resolve(session_cookie(spouse), now=now)
    assert luis.workspace_id == ana.workspace_id == household.workspace_id
    assert luis.actor != ana.actor
    created = Commands(api.state.engine, luis, uuid4()).run(create_property, FLAT)
    assert [p.id for p in Commands(api.state.engine, ana, uuid4()).read(list_properties)] == [
        created.id
    ]


def test_invite_reuse_is_refused(client, household, authenticator, sql):
    assert enroll(client, household.token, authenticator).status_code == 201
    other = new_client(client.app)
    assert (
        other.post("/api/auth/enroll/options", json={"token": household.token}).status_code == 401
    )
    rows = sql(
        "SELECT count(*) FROM auth_passkey WHERE user_id = %s",
        (household.user_id,),
        workspace=household.workspace_id,
    )
    assert rows == [(1,)]


def test_an_invite_cannot_be_redeemed_twice_with_ceremonies_started_before_use(api, household, sql):
    """Two browsers fetch options for the same invite, then both verify: one wins."""
    first, second = new_client(api), new_client(api)
    device_a = SoftAuthenticator(rp_id=RP_ID, origin=ORIGIN)
    device_b = SoftAuthenticator(rp_id=RP_ID, origin=ORIGIN)
    options_a = first.post("/api/auth/enroll/options", json={"token": household.token}).json()
    options_b = second.post("/api/auth/enroll/options", json={"token": household.token}).json()
    body = {"token": household.token, "label": "x"}
    a = first.post(
        "/api/auth/enroll/verify", json=body | {"credential": device_a.register(options_a)}
    )
    b = second.post(
        "/api/auth/enroll/verify", json=body | {"credential": device_b.register(options_b)}
    )
    assert (a.status_code, b.status_code) == (201, 401)
    rows = sql(
        "SELECT credential_id FROM auth_passkey WHERE user_id = %s",
        (household.user_id,),
        workspace=household.workspace_id,
    )
    assert rows == [(device_a.credential_id,)]


def test_an_expired_invite_is_refused(client, household, authenticator, sql):
    sql(
        "UPDATE auth_invite SET expires_at = now() - interval '1 second' WHERE user_id = %s",
        (household.user_id,),
        workspace=household.workspace_id,
    )
    assert (
        client.post("/api/auth/enroll/options", json={"token": household.token}).status_code == 401
    )


def test_unknown_and_malformed_invite_tokens_are_401_and_never_echoed(client, household):
    unknown = "A" * 43
    response = client.post("/api/auth/enroll/options", json={"token": unknown})
    assert response.status_code == 401
    assert unknown not in response.text
    malformed = client.post("/api/auth/enroll/options", json={"token": "short"})
    assert malformed.status_code == 422


def test_delete_a_pending_invite(signed_in):
    invite = signed_in.post(
        "/api/auth/invites", json={"email": unique_email("ana"), "display_name": "Ana"}
    ).json()
    token = invite["enrollment_url"].split("#token=")[1]
    assert signed_in.delete(f"/api/auth/invites/{invite['id']}").status_code == 204
    assert signed_in.get("/api/auth/invites").json() == []
    assert signed_in.delete(f"/api/auth/invites/{invite['id']}").status_code == 404
    browser = new_client(signed_in.app)
    assert browser.post("/api/auth/enroll/options", json={"token": token}).status_code == 401


def test_a_used_invite_cannot_be_deleted(signed_in, household, sql):
    [[invite_id]] = sql(
        "SELECT id FROM auth_invite WHERE user_id = %s",
        (household.user_id,),
        workspace=household.workspace_id,
    )
    assert signed_in.delete(f"/api/auth/invites/{invite_id}").status_code == 404


def test_inviting_a_taken_email_is_a_conflict_without_the_email(signed_in, household):
    response = signed_in.post(
        "/api/auth/invites", json={"email": household.email, "display_name": "Again"}
    )
    assert response.status_code == 409
    assert household.email not in response.text


def test_session_endpoints_need_a_session(client):
    for method, path in [
        ("get", "/api/auth/me"),
        ("post", "/api/auth/logout"),
        ("post", "/api/auth/logout-all"),
        ("get", "/api/auth/passkeys"),
        ("post", "/api/auth/passkeys/options"),
        ("patch", f"/api/auth/passkeys/{uuid4()}"),
        ("delete", f"/api/auth/passkeys/{uuid4()}"),
        ("get", "/api/auth/invites"),
        ("post", "/api/auth/invites"),
        ("delete", f"/api/auth/invites/{uuid4()}"),
    ]:
        kwargs = {"json": {"label": "x"}} if method == "patch" else {}
        if path.endswith("/invites") and method == "post":
            kwargs = {"json": {"email": "a@b.c", "display_name": "A"}}
        assert getattr(client, method)(path, **kwargs).status_code == 401, (method, path)


def test_me_names_the_session_not_its_token(signed_in):
    me = signed_in.get("/api/auth/me").json()
    assert set(me["session"]) == {
        "id",
        "passkey_id",
        "created_at",
        "last_seen_at",
        "expires_at",
        "idle_expires_at",
    }
    assert session_cookie(signed_in) not in signed_in.get("/api/auth/me").text
    UUID(me["session"]["id"])
