"""Sessions (§6.4): lifetimes from settings (12 h absolute, 2 h idle), the idle slide, revocation,
only the token's SHA-256 stored, expired rows deleted on the next sign-in, and the cookie
flags (__Host-, Secure, HttpOnly, SameSite=Lax, Path=/, no Domain)."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from auth_support import enroll, login, make_settings, new_client, session_cookie

from hausbuch.api.main import build_app
from hausbuch.auth import sessions, tokens
from hausbuch.auth.sessions import SLIDE_INTERVAL, SessionResolverImpl
from hausbuch.storage import LocalBlobStore

HOUR = timedelta(hours=1)


def _row(sql, household, session_id):
    [row] = sql(
        "SELECT created_at, last_seen_at, expires_at, idle_expires_at, revoked_at"
        " FROM auth_session WHERE id = %s",
        (session_id,),
        workspace=household.workspace_id,
    )
    return row


def test_lifetimes_come_from_settings(signed_in, household, sql):
    me = signed_in.get("/api/auth/me").json()
    created, _, expires, idle, revoked = _row(sql, household, me["session"]["id"])
    assert expires - created == timedelta(hours=12)
    assert idle - created == timedelta(hours=2)
    assert revoked is None


def test_custom_lifetimes(app_engine, household, authenticator, tmp_path, sql):
    settings = make_settings(session_max_age_s=3600, session_idle_s=600)
    api = build_app(settings, engine=app_engine, blob_store=LocalBlobStore(tmp_path))
    client = new_client(api)
    response = enroll(client, household.token, authenticator)
    created, _, expires, idle, _ = _row(sql, household, response.json()["session"]["id"])
    assert (expires - created, idle - created) == (HOUR, timedelta(minutes=10))
    assert _cookies(response)["__Host-hb_session"]["max-age"] == "3600"


@pytest.fixture
def resolver(app_engine, settings):
    return SessionResolverImpl(app_engine, settings)


@pytest.fixture
def session(signed_in, household, sql):
    """(token, session id, created_at) of the signed-in browser's session."""
    me = signed_in.get("/api/auth/me").json()
    created = _row(sql, household, me["session"]["id"])[0]
    return session_cookie(signed_in), me["session"]["id"], created


def test_resolve_returns_the_auth_context(resolver, session, household):
    token, session_id, created = session
    auth = resolver.resolve(token, now=created + timedelta(seconds=1))
    assert (auth.workspace_id, auth.user_id, str(auth.session_id)) == (
        household.workspace_id,
        household.user_id,
        session_id,
    )
    assert auth.passkey_id is not None


def test_idle_expiry(resolver, session):
    token, _, created = session
    assert resolver.resolve(token, now=created + 2 * HOUR - timedelta(seconds=1)) is not None
    # That use was more than 5 minutes after the last one, so it slid the idle deadline...
    assert resolver.resolve(token, now=created + 3 * HOUR) is not None
    # ...but four idle hours later the session is gone.
    assert resolver.resolve(token, now=created + 7 * HOUR) is None


def test_idle_expiry_without_use(resolver, session):
    token, _, created = session
    assert resolver.resolve(token, now=created + 2 * HOUR) is None


def test_absolute_expiry_even_when_used_all_day(resolver, session, household, sql):
    token, session_id, created = session
    for minutes in range(60, 12 * 60, 60):
        assert resolver.resolve(token, now=created + timedelta(minutes=minutes)) is not None
    _, _, expires, idle, _ = _row(sql, household, session_id)
    assert idle <= expires  # the slide never passes the absolute deadline
    assert resolver.resolve(token, now=created + 12 * HOUR) is None


def test_the_slide_writes_at_most_every_five_minutes(resolver, session, household, sql):
    token, session_id, created = session
    resolver.resolve(token, now=created + SLIDE_INTERVAL - timedelta(seconds=1))
    assert _row(sql, household, session_id)[1] == created  # no write yet
    later = created + SLIDE_INTERVAL
    resolver.resolve(token, now=later)
    _, last_seen, _, idle, _ = _row(sql, household, session_id)
    assert last_seen == later and idle == later + 2 * HOUR


def test_revoked_session_does_not_resolve(resolver, session, signed_in):
    token, _, created = session
    assert signed_in.post("/api/auth/logout").status_code == 204
    assert resolver.resolve(token, now=created + timedelta(seconds=1)) is None
    assert resolver.resolve(token, now=created + SLIDE_INTERVAL) is None


@pytest.mark.parametrize(
    "token", ["", "x", "A" * 42, "A" * 44, "A" * 42 + "=", "Ä" * 43, "A" * 43, "A" * 128]
)
def test_malformed_or_unknown_tokens_do_not_resolve(resolver, signed_in, token):
    assert resolver.resolve(token, now=datetime.now(UTC)) is None


def test_expired_session_over_http_is_401(signed_in, household, sql):
    me = signed_in.get("/api/auth/me").json()
    sql(
        "UPDATE auth_session SET idle_expires_at = now() - interval '1 second' WHERE id = %s",
        (me["session"]["id"],),
        workspace=household.workspace_id,
    )
    assert signed_in.get("/api/auth/me").status_code == 401


def test_only_the_token_hash_is_stored(signed_in, household, sql):
    token = session_cookie(signed_in)
    assert tokens.is_well_formed(token)
    rows = sql(
        "SELECT token_hash FROM auth_session WHERE user_id = %s",
        (household.user_id,),
        workspace=household.workspace_id,
    )
    assert rows == [(hashlib.sha256(token.encode()).digest(),)]
    [[invite_hash]] = sql(
        "SELECT token_hash FROM auth_invite WHERE user_id = %s",
        (household.user_id,),
        workspace=household.workspace_id,
    )
    assert invite_hash == hashlib.sha256(household.token.encode()).digest()
    # The raw tokens appear in no column of any auth table.
    for table in ("auth_session", "auth_invite", "auth_passkey"):
        dump = str(sql(f"SELECT * FROM {table}", workspace=household.workspace_id))
        assert token not in dump and household.token not in dump


def test_expired_sessions_are_deleted_on_the_next_sign_in(signed_in, household, authenticator, sql):
    old = signed_in.get("/api/auth/me").json()["session"]["id"]
    sql(
        "UPDATE auth_session SET expires_at = now() - interval '1 second',"
        " idle_expires_at = now() - interval '1 second' WHERE id = %s",
        (old,),
        workspace=household.workspace_id,
    )
    revoked = new_client(signed_in.app)
    assert login(revoked, authenticator).status_code == 200
    assert revoked.post("/api/auth/logout").status_code == 204  # revoked, not expired: kept
    assert login(signed_in, authenticator).status_code == 200
    ids = {
        str(r[0])
        for r in sql(
            "SELECT id FROM auth_session WHERE user_id = %s",
            (household.user_id,),
            workspace=household.workspace_id,
        )
    }
    assert old not in ids and len(ids) == 2


def test_open_session_never_lets_idle_pass_absolute(app_engine, household):
    """With idle == absolute lifetime the two deadlines coincide."""
    from hausbuch.db.session import workspace_transaction

    settings = make_settings(session_max_age_s=600, session_idle_s=600)
    now = datetime.now(UTC)
    with workspace_transaction(app_engine, household.workspace_id) as conn:
        opened = sessions.open_session(
            conn,
            settings,
            workspace_id=household.workspace_id,
            user_id=household.user_id,
            passkey_id=None,
            now=now,
        )
    assert opened.session.idle_expires_at == opened.session.expires_at


# --- Cookies ---------------------------------------------------------------------------


def _cookies(response) -> dict[str, dict[str, str]]:
    """Each Set-Cookie header, parsed: name -> attributes (lower-case keys)."""
    parsed = {}
    for header in response.headers.get_list("set-cookie"):
        name_value, *attributes = [part.strip() for part in header.split(";")]
        name, value = name_value.split("=", 1)
        attrs = {"value": value}
        for attribute in attributes:
            key, _, val = attribute.partition("=")
            attrs[key.lower()] = val
        parsed[name] = attrs
    return parsed


def _assert_flags(attrs: dict, *, secure: bool, max_age: str) -> None:
    assert attrs["path"] == "/"
    assert "httponly" in attrs
    assert attrs["samesite"].lower() == "lax"
    assert ("secure" in attrs) is secure
    assert "domain" not in attrs
    assert attrs["max-age"] == max_age


def test_cookie_flags(client, household, authenticator):
    options = client.post("/api/auth/enroll/options", json={"token": household.token})
    ceremony = _cookies(options)["__Host-hb_ceremony"]
    _assert_flags(ceremony, secure=True, max_age="300")

    credential = authenticator.register(options.json())
    verify = client.post(
        "/api/auth/enroll/verify",
        json={"token": household.token, "credential": credential, "label": "x"},
    )
    cookies = _cookies(verify)
    _assert_flags(cookies["__Host-hb_session"], secure=True, max_age=str(12 * 3600))
    assert cookies["__Host-hb_ceremony"]["max-age"] == "0"  # cleared
    assert len(cookies["__Host-hb_session"]["value"]) == 43

    logout = client.post("/api/auth/logout")
    assert _cookies(logout)["__Host-hb_session"]["max-age"] == "0"


def test_dev_cookies_drop_the_host_prefix(app_engine, household, tmp_path):
    """HAUSBUCH_COOKIE_SECURE=false (http://localhost only): no Secure, so no __Host-."""
    from authenticator import SoftAuthenticator
    from fastapi.testclient import TestClient

    settings = make_settings(rp_id="localhost", origin="http://localhost:3000", cookie_secure=False)
    api = build_app(settings, engine=app_engine, blob_store=LocalBlobStore(tmp_path))
    client = TestClient(api, base_url="http://localhost", headers={"Origin": settings.origin})
    device = SoftAuthenticator(rp_id="localhost", origin="http://localhost:3000")
    options = client.post("/api/auth/enroll/options", json={"token": household.token})
    _assert_flags(_cookies(options)["hb_ceremony"], secure=False, max_age="300")
    verify = client.post(
        "/api/auth/enroll/verify",
        json={
            "token": household.token,
            "credential": device.register(options.json()),
            "label": "x",
        },
    )
    assert verify.status_code == 201, verify.text
    _assert_flags(_cookies(verify)["hb_session"], secure=False, max_age=str(12 * 3600))
    assert client.get("/api/auth/me").status_code == 200
