"""Provisioning without a superuser (§7, §10.2): provision_workspace succeeds as hausbuch_owner,
the owner cannot insert a workspace without the set_config, a superuser engine is refused
with UnsafeDatabaseRoleError, the app role cannot provision; and the recovery path
(reissue_enrollment, `hausbuch invite`)."""

from datetime import UTC, datetime

import pytest
from auth_support import (
    ORIGIN,
    RP_ID,
    add_passkey,
    enroll,
    login,
    new_client,
    role_url,
    unique_email,
)
from authenticator import SoftAuthenticator
from db import db_support
from db.db_support import APP
from pydantic import ValidationError
from sqlalchemy import create_engine, insert, pool, text
from sqlalchemy.exc import DBAPIError

from hausbuch.auth.errors import UserNotFoundError
from hausbuch.auth.provisioning import provision_workspace, reissue_enrollment
from hausbuch.db.session import UnsafeDatabaseRoleError, workspace_transaction
from hausbuch.db.tables import workspace

# The real clock, not a fixed date: the enrollment tests redeem these invites through the API,
# which checks their 24 h expiry against the real clock. A fixed date made every one of them
# fail from the day after it (CI on 6 Nov 2026).
NOW = datetime.now(UTC)


def _provision(engine, **overrides):
    values = {"name": "Haushalt", "email": unique_email(), "display_name": "Luis", "now": NOW}
    return provision_workspace(engine, **(values | overrides))


def test_provision_as_the_owner_creates_workspace_user_and_invite(owner_engine, sql):
    email = unique_email().upper()
    provisioned = _provision(owner_engine, email=email)
    ws = provisioned.workspace_id
    assert sql("SELECT id, name, created_at FROM workspace", workspace=ws) == [
        (ws, "Haushalt", NOW)
    ]
    assert sql("SELECT id, email, display_name FROM app_user", workspace=ws) == [
        (provisioned.user_id, email.lower(), "Luis")
    ]
    [(user_id, created_by, expires_at, used_at)] = sql(
        "SELECT user_id, created_by, expires_at, used_at FROM auth_invite", workspace=ws
    )
    assert (user_id, created_by, used_at) == (provisioned.user_id, None, None)
    assert expires_at - NOW == provisioned.invite.expires_at - NOW
    assert (expires_at - NOW).total_seconds() == 24 * 3600
    assert len(provisioned.invite.token) == 43


def test_the_owner_needs_the_set_config(owner_engine):
    """What workspace_transaction's set_config buys: without it, RLS rejects even the owner."""
    with pytest.raises(DBAPIError, match="row-level security"), owner_engine.begin() as conn:
        conn.execute(insert(workspace).values(name="X"))


def test_the_app_role_cannot_provision(app_engine):
    with pytest.raises(DBAPIError, match="permission denied"):
        _provision(app_engine)


def test_a_superuser_engine_is_refused_by_create_app_engine(dbname):
    superuser = db_support.make_engine(dbname, "postgres")
    try:
        with pytest.raises(UnsafeDatabaseRoleError, match="'postgres'"):
            _provision(superuser)
    finally:
        superuser.dispose()


def test_a_superuser_engine_is_refused_even_without_the_engine_guard(dbname, sql):
    """provision_workspace checks the role itself, so a plain create_engine on a superuser URL
    (no create_app_engine guard) fails too, before anything is written."""
    plain = create_engine(role_url(dbname, None), poolclass=pool.NullPool)
    try:
        with pytest.raises(UnsafeDatabaseRoleError, match="superuser"):
            _provision(plain, name="Never")
        with plain.connect() as conn:  # nothing was written, as seen by the superuser
            names = conn.execute(text("SELECT name FROM workspace")).scalars().all()
        assert "Never" not in names
    finally:
        plain.dispose()


@pytest.mark.parametrize(
    "overrides",
    [{"email": "not-an-email"}, {"display_name": ""}, {"name": ""}, {"name": "x" * 201}],
    ids=["email", "display-name", "empty-name", "long-name"],
)
def test_invalid_input_is_refused_before_connecting(overrides):
    class NoEngine:
        def begin(self):
            raise AssertionError("must not connect")

    with pytest.raises(ValidationError):
        _provision(NoEngine(), **overrides)


def test_a_taken_email_is_refused_and_nothing_is_written(owner_engine, sql):
    first = _provision(owner_engine)
    email = sql("SELECT email FROM app_user", workspace=first.workspace_id)[0][0]
    from hausbuch.auth.errors import DuplicateUserError

    with pytest.raises(DuplicateUserError) as excinfo:
        _provision(owner_engine, email=email, name="Zweiter")
    assert email not in str(excinfo.value)


def test_provisioned_household_enrolls_through_the_api(owner_engine, api):
    provisioned = _provision(owner_engine)
    client = new_client(api)
    response = enroll(client, provisioned.invite.token, SoftAuthenticator(RP_ID, ORIGIN))
    assert response.status_code == 201
    assert response.json()["workspace"]["id"] == str(provisioned.workspace_id)


# --- Recovery: reissue_enrollment ----------------------------------------------------------


@pytest.fixture
def enrolled_household(owner_engine, api):
    email = unique_email()
    provisioned = _provision(owner_engine, email=email)
    phone, laptop = SoftAuthenticator(RP_ID, ORIGIN), SoftAuthenticator(RP_ID, ORIGIN)
    client = new_client(api)
    assert enroll(client, provisioned.invite.token, phone).status_code == 201
    assert add_passkey(client, laptop).status_code == 201
    return provisioned, email, client, phone, laptop


def test_reissue_gives_a_fresh_link_and_kills_older_unused_ones(owner_engine, api, sql):
    provisioned = _provision(owner_engine, email=(email := unique_email()))
    again = reissue_enrollment(owner_engine, email=email, now=NOW)
    assert again.workspace_id == provisioned.workspace_id and again.user_id == provisioned.user_id
    client = new_client(api)
    device = SoftAuthenticator(RP_ID, ORIGIN)
    old = client.post("/api/auth/enroll/options", json={"token": provisioned.invite.token})
    assert old.status_code == 401
    assert enroll(client, again.invite.token, device).status_code == 201


def test_reissue_with_revoke_passkeys_ends_every_session(
    owner_engine, api, enrolled_household, sql
):
    provisioned, email, client, phone, laptop = enrolled_household
    laptop_browser = new_client(api)
    assert login(laptop_browser, laptop).status_code == 200

    result = reissue_enrollment(owner_engine, email=email, revoke_passkeys=True, now=NOW)

    assert result.passkeys_deleted == 2
    assert client.get("/api/auth/me").status_code == 401
    assert laptop_browser.get("/api/auth/me").status_code == 401
    ws = provisioned.workspace_id
    assert sql("SELECT count(*) FROM auth_passkey", workspace=ws) == [(0,)]
    assert sql(
        "SELECT count(*) FROM auth_session WHERE revoked_at IS NULL OR passkey_id IS NOT NULL",
        workspace=ws,
    ) == [(0,)]
    fresh = new_client(api)
    assert enroll(fresh, result.invite.token, SoftAuthenticator(RP_ID, ORIGIN)).status_code == 201


def test_reissue_with_revoke_sessions_keeps_the_passkeys(owner_engine, api, enrolled_household):
    _, email, client, phone, _ = enrolled_household
    result = reissue_enrollment(owner_engine, email=email, revoke_sessions=True, now=NOW)
    assert (result.passkeys_deleted, result.sessions_revoked) == (0, 1)
    assert client.get("/api/auth/me").status_code == 401
    assert login(new_client(api), phone).status_code == 200


def test_reissue_for_an_unknown_email(owner_engine):
    _provision(owner_engine)
    with pytest.raises(UserNotFoundError):
        reissue_enrollment(owner_engine, email=unique_email("nobody"), now=NOW)


def test_reissue_in_a_named_workspace(owner_engine):
    a = _provision(owner_engine, email=(email := unique_email()))
    b = _provision(owner_engine)
    assert reissue_enrollment(owner_engine, email=email, workspace_id=a.workspace_id, now=NOW)
    with pytest.raises(UserNotFoundError):
        reissue_enrollment(owner_engine, email=email, workspace_id=b.workspace_id, now=NOW)


def test_reissue_refuses_a_superuser(dbname):
    plain = create_engine(role_url(dbname, None), poolclass=pool.NullPool)
    try:
        with pytest.raises(UnsafeDatabaseRoleError):
            reissue_enrollment(plain, email=unique_email(), now=NOW)
    finally:
        plain.dispose()


def test_the_app_role_engine_has_no_owner_powers(app_engine, owner_engine):
    """Sanity for the role names the tests rely on: what each engine is."""
    with workspace_transaction(app_engine, _provision(owner_engine).workspace_id) as conn:
        assert conn.execute(text("SELECT current_user")).scalar_one() == APP
