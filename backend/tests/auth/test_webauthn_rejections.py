"""Every rejection §10.2 names, through the real py_webauthn verify paths: wrong origin, wrong
RP id, replayed challenge, counter going backwards, userHandle mismatch, missing UV flag,
expired ceremony cookie, tampered cookie signature; plus attestation other than "none", the
Origin header check (§6.6), and that a refusal leaves nothing behind and says nothing."""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from authenticator import AT, BE, BS, UP, UV, SoftAuthenticator

# Module-level only: inside a test body, `conftest` may already be tests/db's module.
from conftest import (
    ORIGIN,
    RP_ID,
    enroll,
    login,
    make_settings,
    new_client,
    put_cookie,
    session_cookie,
)

from hausbuch.api.routes import auth as auth_routes
from hausbuch.auth import ceremony

CEREMONY = "__Host-hb_ceremony"


def error_kind(response) -> str:
    """The error string, nothing else in the body. Today the auth routes' HTTPException
    renders as FastAPI's {"detail": kind}; with chunk 1's handler it is the ErrorResponse
    envelope {"error": kind, ...}, whose only other allowed field here is request_id."""
    body = response.json()
    if "detail" in body:
        assert set(body) == {"detail"}, body
        return body["detail"]
    assert set(body) <= {"error", "message", "request_id"}, body  # chunk 1's envelope
    return body["error"]


@pytest.fixture
def enrolled(client, household, authenticator):
    assert enroll(client, household.token, authenticator).status_code == 201
    return authenticator


def _refused(response) -> None:
    assert response.status_code == 401, response.text
    assert error_kind(response) == "unauthenticated"  # no reason, no echo


def _passkey_count(sql, household) -> int:
    [[count]] = sql(
        "SELECT count(*) FROM auth_passkey WHERE user_id = %s",
        (household.user_id,),
        workspace=household.workspace_id,
    )
    return count


def _invite_unused(sql, household) -> bool:
    [[used_at]] = sql(
        "SELECT used_at FROM auth_invite WHERE user_id = %s",
        (household.user_id,),
        workspace=household.workspace_id,
    )
    return used_at is None


# --- Registration (enroll/verify) ------------------------------------------------------

REGISTRATION_CASES = {
    "wrong-origin": {"origin": "https://evil.example"},
    "origin-with-port": {"origin": "https://testserver:8443"},
    "plain-http-origin": {"origin": "http://testserver"},
    "wrong-rp-id": {"rp_id": "evil.example"},
    "parent-of-rp-id": {"rp_id": "server"},
    "other-challenge": {"challenge": "A" * 43},
    "no-user-verification": {"flags": UP | AT | BE | BS},
    "no-user-presence": {"flags": UV | AT | BE | BS},
    "attestation-not-none": {"fmt": "packed"},
    "get-instead-of-create": {"type_": "webauthn.get"},
}


@pytest.mark.parametrize("case", sorted(REGISTRATION_CASES))
def test_enrollment_rejections(client, household, authenticator, sql, case):
    response = enroll(client, household.token, authenticator, **REGISTRATION_CASES[case])
    _refused(response)
    assert session_cookie(client) is None
    assert _passkey_count(sql, household) == 0
    assert _invite_unused(sql, household)  # the invite can still be used
    assert enroll(client, household.token, authenticator).status_code == 201


def test_packed_attestation_would_verify_but_is_not_accepted(monkeypatch, household):
    """Control for "attestation-not-none": py_webauthn itself accepts the packed
    self-attestation the authenticator produces, so the refusal is ours."""
    import webauthn

    from hausbuch.auth import webauthn as flows

    seen = []
    real = webauthn.verify_registration_response
    monkeypatch.setattr(
        webauthn, "verify_registration_response", lambda **kw: seen.append(real(**kw)) or seen[-1]
    )
    device = SoftAuthenticator(rp_id=RP_ID, origin=ORIGIN)
    options = flows.registration_options(
        _settings(),
        b"c" * 32,
        user_id=household.user_id,
        user_name="x",
        display_name="x",
        exclude=[],
    )
    with pytest.raises(flows.AuthenticationFailed, match="attestation_not_none"):
        flows.verify_registration(_settings(), device.register(options, fmt="packed"), b"c" * 32)
    assert seen[0].fmt == "packed"


def _settings():
    return make_settings()


def test_enroll_ceremony_of_another_invite_is_refused(api, signed_in, household, sql):
    """A ceremony cookie is bound to its invite: options for invite A, verify with B."""
    invite = signed_in.post(
        "/api/auth/invites", json={"email": "ana-bound@example.org", "display_name": "Ana"}
    ).json()
    token_b = invite["enrollment_url"].split("#token=")[1]
    browser = new_client(api)
    other = signed_in.post(
        "/api/auth/invites", json={"email": "bob-bound@example.org", "display_name": "Bob"}
    ).json()
    token_c = other["enrollment_url"].split("#token=")[1]
    device = SoftAuthenticator(rp_id=RP_ID, origin=ORIGIN)
    options = browser.post("/api/auth/enroll/options", json={"token": token_c}).json()
    response = browser.post(
        "/api/auth/enroll/verify",
        json={"token": token_b, "credential": device.register(options), "label": "x"},
    )
    _refused(response)


# --- Authentication (login/verify) ------------------------------------------------------

LOGIN_CASES = {
    "wrong-origin": {"origin": "https://evil.example"},
    "wrong-rp-id": {"rp_id": "evil.example"},
    "other-challenge": {"challenge": "A" * 43},
    "no-user-verification": {"flags": UP | BE | BS},
    "no-user-presence": {"flags": UV | BE | BS},
    "no-user-handle": {"user_handle": None},
    "user-handle-of-someone-else": {"user_handle": b"\x00" * 16},
    "create-instead-of-get": {"type_": "webauthn.create"},
}


@pytest.mark.parametrize("case", sorted(LOGIN_CASES))
def test_login_rejections(api, enrolled, household, sql, case):
    browser = new_client(api)
    _refused(login(browser, enrolled, **LOGIN_CASES[case]))
    assert session_cookie(browser) is None
    [[count]] = sql(
        "SELECT sign_count FROM auth_passkey WHERE user_id = %s",
        (household.user_id,),
        workspace=household.workspace_id,
    )
    assert count == 0  # nothing was stored for a refused assertion
    assert login(browser, enrolled).status_code == 200


def test_signature_by_another_key_is_refused(api, enrolled):
    impostor = enrolled.clone()
    impostor.key = SoftAuthenticator(rp_id=RP_ID, origin=ORIGIN).key
    _refused(login(new_client(api), impostor))


def test_unknown_credential_is_refused(api, enrolled):
    _refused(login(new_client(api), SoftAuthenticator(rp_id=RP_ID, origin=ORIGIN)))


def test_malformed_credentials_are_refused_without_a_500(api, enrolled):
    browser = new_client(api)
    for credential in [
        {},
        {"id": "x", "rawId": "y"},
        {"id": "!!", "rawId": "!!"},
        {"id": "AA", "rawId": "AA", "type": "public-key", "response": {}},
        {"id": "AA", "rawId": "AA", "type": "public-key", "response": "x"},
    ]:
        browser.post("/api/auth/login/options")
        _refused(browser.post("/api/auth/login/verify", json={"credential": credential}))
    browser.post("/api/auth/login/options")
    good = enrolled.assertion(browser.post("/api/auth/login/options").json())
    broken = good | {"response": good["response"] | {"authenticatorData": "AAAA"}}
    _refused(browser.post("/api/auth/login/verify", json={"credential": broken}))


def test_replayed_assertion_is_refused(api, enrolled):
    """The same assertion and the same ceremony cookie, sent again (Q4's replay window): the
    counter has moved on, so the second one fails."""
    browser = new_client(api)
    options = browser.post("/api/auth/login/options")
    cookie = options.cookies[CEREMONY]
    credential = enrolled.assertion(options.json())
    assert (
        browser.post("/api/auth/login/verify", json={"credential": credential}).status_code == 200
    )
    replay = new_client(api)
    put_cookie(replay, CEREMONY, cookie)
    _refused(replay.post("/api/auth/login/verify", json={"credential": credential}))


def test_an_old_challenge_does_not_match_a_new_ceremony(api, enrolled):
    browser = new_client(api)
    old = browser.post("/api/auth/login/options").json()
    browser.post("/api/auth/login/options")  # a new ceremony replaces the cookie
    _refused(browser.post("/api/auth/login/verify", json={"credential": enrolled.assertion(old)}))


@pytest.mark.parametrize("pinned", [0, 3], ids=["to-zero", "backwards"])
def test_counter_going_backwards_is_refused(api, enrolled, pinned):
    for _ in range(4):
        assert login(new_client(api), enrolled).status_code == 200  # counter now 4
    _refused(login(new_client(api), enrolled, sign_count=pinned))
    _refused(login(new_client(api), enrolled, sign_count=4))  # equal is not greater
    assert login(new_client(api), enrolled, sign_count=9).status_code == 200  # gaps are fine


def test_a_synced_passkey_without_a_counter_signs_in_repeatedly(api, household, sql):
    synced = SoftAuthenticator(rp_id=RP_ID, origin=ORIGIN, counts=False)
    assert enroll(new_client(api), household.token, synced).status_code == 201
    for _ in range(3):
        assert login(new_client(api), synced).status_code == 200
    [[count, last_used]] = sql(
        "SELECT sign_count, last_used_at FROM auth_passkey WHERE user_id = %s",
        (household.user_id,),
        workspace=household.workspace_id,
    )
    assert count == 0 and last_used is not None
    # Once a credential has counted, 0 is no longer accepted (py_webauthn).
    assert login(new_client(api), synced, sign_count=5).status_code == 200
    _refused(login(new_client(api), synced, sign_count=0))


# --- The ceremony cookie -----------------------------------------------------------------


def test_verify_without_a_ceremony_cookie_is_refused(api, enrolled):
    browser = new_client(api)
    options = browser.post("/api/auth/login/options").json()
    browser.cookies.clear()
    _refused(
        browser.post("/api/auth/login/verify", json={"credential": enrolled.assertion(options)})
    )


def test_expired_ceremony_cookie_is_refused(api, enrolled, monkeypatch):
    browser = new_client(api)
    options = browser.post("/api/auth/login/options").json()
    later = datetime.now(UTC) + timedelta(seconds=ceremony.CEREMONY_MAX_AGE_S + 1)
    monkeypatch.setattr(auth_routes, "_now", lambda: later)
    _refused(
        browser.post("/api/auth/login/verify", json={"credential": enrolled.assertion(options)})
    )


def test_tampered_ceremony_cookie_is_refused(api, enrolled):
    browser = new_client(api)
    options = browser.post("/api/auth/login/options").json()
    body, mac = browser.cookies[CEREMONY].split(".")
    flipped = mac[:-2] + ("A" if mac[-2] != "A" else "B") + mac[-1]
    put_cookie(browser, CEREMONY, f"{body}.{flipped}")
    _refused(
        browser.post("/api/auth/login/verify", json={"credential": enrolled.assertion(options)})
    )


def test_a_cookie_signed_with_another_key_is_refused(api, enrolled):
    browser = new_client(api)
    options = browser.post("/api/auth/login/options").json()
    opened = ceremony.open_sealed(
        api.state.settings.session_key,
        browser.cookies[CEREMONY],
        kind="authentication",
        now=datetime.now(UTC),
    )
    put_cookie(browser, CEREMONY, ceremony.seal(b"k" * 32, opened))
    _refused(
        browser.post("/api/auth/login/verify", json={"credential": enrolled.assertion(options)})
    )


def test_a_registration_ceremony_cannot_be_used_to_sign_in(api, signed_in, authenticator):
    signed_in.post("/api/auth/passkeys/options")  # sets a registration ceremony cookie
    options = new_client(api).post("/api/auth/login/options").json()
    credential = authenticator.assertion(options)
    _refused(signed_in.post("/api/auth/login/verify", json={"credential": credential}))


def test_a_passkey_ceremony_is_bound_to_the_user_who_started_it(api, signed_in, household):
    """User A's add-passkey cookie, presented with user B's session, is refused."""
    invite = signed_in.post(
        "/api/auth/invites", json={"email": "ana-cer@example.org", "display_name": "Ana"}
    ).json()
    spouse = new_client(api)
    token = invite["enrollment_url"].split("#token=")[1]
    assert enroll(spouse, token, SoftAuthenticator(rp_id=RP_ID, origin=ORIGIN)).status_code == 201
    options = signed_in.post("/api/auth/passkeys/options")
    put_cookie(spouse, CEREMONY, options.cookies[CEREMONY])
    device = SoftAuthenticator(rp_id=RP_ID, origin=ORIGIN)
    response = spouse.post(
        "/api/auth/passkeys/verify",
        json={"credential": device.register(options.json()), "label": "x"},
    )
    _refused(response)


def test_the_ceremony_cookie_is_cleared_after_verify(api, enrolled):
    browser = new_client(api)
    assert login(browser, enrolled).status_code == 200
    assert CEREMONY not in browser.cookies
    failing = new_client(api)
    _refused(login(failing, enrolled, origin="https://evil.example"))
    assert CEREMONY not in failing.cookies


# --- Origin header (§6.6) ------------------------------------------------------------------


@pytest.mark.parametrize("origin", [None, "https://evil.example", "http://testserver", "null"])
def test_unsafe_auth_requests_need_our_origin(api, enrolled, origin):
    browser = new_client(api)
    options = browser.post("/api/auth/login/options").json()
    del browser.headers["Origin"]
    headers = {"Origin": origin} if origin else {}
    response = browser.post(
        "/api/auth/login/verify",
        json={"credential": enrolled.assertion(options)},
        headers=headers,
    )
    assert response.status_code == 403
    assert error_kind(response) == "forbidden_origin"
    assert session_cookie(browser) is None
    assert browser.post("/api/auth/login/options", headers=headers).status_code == 403
    assert browser.get("/api/auth/me").status_code == 401  # GET is not origin-checked


def test_logout_needs_our_origin(signed_in):
    assert (
        signed_in.post("/api/auth/logout", headers={"Origin": "https://evil.example"}).status_code
        == 403
    )
    assert signed_in.get("/api/auth/me").status_code == 200


# --- Nothing secret in logs or responses ----------------------------------------------------


def test_refusals_log_a_reason_code_only(api, enrolled, household, caplog):
    caplog.set_level(logging.DEBUG)
    browser = new_client(api)
    options = browser.post("/api/auth/login/options").json()
    credential = enrolled.assertion(options, origin="https://evil.example")
    response = browser.post("/api/auth/login/verify", json={"credential": credential})
    _refused(response)
    text = caplog.text
    assert "login.verify refused: assertion.invalid" in text
    for secret in [credential["rawId"], "evil.example", household.email, options["challenge"]]:
        assert secret not in text
        assert secret not in response.text
