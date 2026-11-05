"""Two binding rules from the chunk 0 review (§10.5), tested against the real database:

1. The sign counter is checked and stored in one atomic UPDATE: of two concurrent assertions
   carrying the same counter (a cloned authenticator), exactly one signs in.
2. The pre-auth lookup flag (app.auth_lookup) is never on while anything is written: every
   INSERT/UPDATE/DELETE the auth code issues, over every flow, is recorded together with the
   flag's value in that same transaction.
"""

import re
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from authenticator import SoftAuthenticator
from conftest import ORIGIN, RP_ID, add_passkey, enroll, login, new_client, unique_email
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError

from hausbuch.auth import lookup
from hausbuch.auth import webauthn as flows
from hausbuch.auth.ceremony import CEREMONY_COOKIE
from hausbuch.auth.errors import AuthenticationFailed
from hausbuch.db.session import workspace_transaction

# --- 1. Atomic sign counter ----------------------------------------------------------------


def _ceremony(api, client):
    """Start a sign-in ceremony in `client`; return (options, opened ceremony)."""
    from hausbuch.auth.ceremony import open_sealed

    response = client.post("/api/auth/login/options")
    opened = open_sealed(
        api.state.settings.session_key,
        response.cookies[CEREMONY_COOKIE],
        kind="authentication",
        now=datetime.now(UTC),
    )
    return response.json(), opened


def test_two_concurrent_assertions_with_the_same_counter_one_wins(
    api, client, household, authenticator, monkeypatch, sql
):
    """Both assertions pass the lookup and py_webauthn against the same stored counter (the
    barrier holds both threads after verification, before either writes). The UPDATE ...
    WHERE sign_count < :new then lets exactly one through."""
    assert enroll(client, household.token, authenticator).status_code == 201
    clone = authenticator.clone()
    options_a, ceremony_a = _ceremony(api, new_client(api))
    options_b, ceremony_b = _ceremony(api, new_client(api))
    credential_a = authenticator.assertion(options_a, sign_count=5)
    credential_b = clone.assertion(options_b, sign_count=5)

    barrier = threading.Barrier(2, timeout=10)
    real_verify = flows.verify_assertion

    def verify_then_wait(*args, **kwargs):
        count = real_verify(*args, **kwargs)
        barrier.wait()
        return count

    monkeypatch.setattr(flows, "verify_assertion", verify_then_wait)
    results: dict[str, object] = {}

    def attempt(name, credential, ceremony):
        try:
            results[name] = flows.sign_in(
                api.state.engine,
                api.state.settings,
                credential=credential,
                ceremony=ceremony,
                now=datetime.now(UTC),
            )
        except Exception as exc:  # noqa: BLE001 - the test inspects it
            results[name] = exc

    threads = [
        threading.Thread(target=attempt, args=("a", credential_a, ceremony_a)),
        threading.Thread(target=attempt, args=("b", credential_b, ceremony_b)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    winners = [r for r in results.values() if isinstance(r, flows.SignedIn)]
    losers = [r for r in results.values() if isinstance(r, AuthenticationFailed)]
    assert len(winners) == 1 and len(losers) == 1, results
    assert losers[0].reason == "assertion.sign_count_not_increased"
    rows = sql(
        "SELECT sign_count FROM auth_passkey WHERE user_id = %s",
        (household.user_id,),
        workspace=household.workspace_id,
    )
    assert rows == [(5,)]
    [[sessions]] = sql(
        "SELECT count(*) FROM auth_session WHERE user_id = %s",
        (household.user_id,),
        workspace=household.workspace_id,
    )
    assert sessions == 2  # the enrollment's and the winner's; the loser opened none


def test_a_stale_counter_read_cannot_win(api, client, household, authenticator):
    """Sequential version of the race: verification against a stale lookup (counter 0)
    passes py_webauthn, but the atomic UPDATE sees the stored 5 and refuses 5."""
    assert enroll(client, household.token, authenticator).status_code == 201
    assert login(new_client(api), authenticator, sign_count=5).status_code == 200
    stale = lookup.PasskeyRecord(
        **{
            **vars(lookup.find_passkey(api.state.engine, authenticator.credential_id)),
            "sign_count": 0,
        }
    )
    options, opened = _ceremony(api, new_client(api))
    credential = authenticator.clone().assertion(options, sign_count=5)
    count = flows.verify_assertion(api.state.settings, credential, opened.challenge, stale)
    assert count == 5
    with workspace_transaction(api.state.engine, household.workspace_id) as conn:
        from hausbuch.auth.passkeys import record_assertion

        assert record_assertion(conn, stale.id, count, datetime.now(UTC)) is False


# --- 2. The lookup flag is never on during a write ------------------------------------------

_WRITE = re.compile(r"^\s*(INSERT|UPDATE|DELETE)\b", re.IGNORECASE)
_FLAG = "SELECT coalesce(current_setting('app.auth_lookup', true), '')"


@pytest.fixture
def statements(app_engine):
    """Every statement on the app engine, with the flag's value at that moment (asked on the
    same connection, so inside the same transaction)."""
    seen: list[tuple[str, str]] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith(_FLAG):
            return
        with cursor.connection.cursor() as probe:
            probe.execute(_FLAG)
            (flag,) = probe.fetchone()
        verb = statement.split(None, 1)[0].upper() if statement.strip() else ""
        seen.append((verb, flag))

    event.listen(app_engine, "before_cursor_execute", record)
    yield seen
    event.remove(app_engine, "before_cursor_execute", record)


def test_no_auth_flow_writes_under_the_lookup_flag(api, client, household, statements):
    """Every auth flow, end to end: enroll, sign in, session use and slide, second passkey,
    rename, invite, member enrollment, delete passkey, delete invite, logout, logout-all."""
    phone = SoftAuthenticator(rp_id=RP_ID, origin=ORIGIN)
    assert enroll(client, household.token, phone).status_code == 201
    assert login(client, phone).status_code == 200
    resolver = api.state.session_resolver
    token = client.cookies.get("__Host-hb_session")
    later = datetime.now(UTC) + timedelta(minutes=10)
    assert resolver.resolve(token, now=later) is not None  # slides idle_expires_at
    laptop = SoftAuthenticator(rp_id=RP_ID, origin=ORIGIN)
    passkey = add_passkey(client, laptop).json()
    assert client.patch(f"/api/auth/passkeys/{passkey['id']}", json={"label": "Mac"}).is_success
    invite = client.post(
        "/api/auth/invites", json={"email": unique_email("ana"), "display_name": "Ana"}
    ).json()
    spouse = new_client(api)
    token_b = invite["enrollment_url"].split("#token=")[1]
    assert enroll(spouse, token_b, SoftAuthenticator(rp_id=RP_ID, origin=ORIGIN)).is_success
    assert client.delete(f"/api/auth/passkeys/{passkey['id']}").status_code == 204
    pending = client.post(
        "/api/auth/invites", json={"email": unique_email("bob"), "display_name": "Bob"}
    ).json()
    assert client.delete(f"/api/auth/invites/{pending['id']}").status_code == 204
    assert login(new_client(api), phone, origin="https://evil.example").status_code == 401
    assert spouse.post("/api/auth/logout").status_code == 204
    assert client.post("/api/auth/logout-all").status_code == 204

    writes = [(verb, flag) for verb, flag in statements if verb in {"INSERT", "UPDATE", "DELETE"}]
    reads_under_flag = [s for s in statements if s == ("SELECT", "on")]
    verbs = {verb for verb, _ in writes}
    assert verbs == {"INSERT", "UPDATE", "DELETE"}, "the flows should have written all three ways"
    assert len(writes) >= 20
    assert reads_under_flag, "the lookups should have run under the flag"
    assert [w for w in writes if w[1] == "on"] == []


def test_the_lookup_transaction_is_read_only(app_engine, household):
    with lookup.auth_lookup_transaction(app_engine) as conn:
        assert conn.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
        assert conn.execute(text("SELECT current_setting('app.auth_lookup')")).scalar_one() == "on"
        with pytest.raises(DBAPIError, match="read-only transaction"):
            conn.execute(text("UPDATE auth_invite SET used_at = now()"))


def test_the_lookup_flag_ends_with_its_transaction(app_engine, household):
    engine = app_engine
    assert lookup.find_invite(engine, b"\x00" * 32) is None
    with engine.begin() as conn:
        flag = conn.execute(text("SELECT current_setting('app.auth_lookup', true)")).scalar()
        assert flag in (None, "")
        assert conn.execute(text("SELECT count(*) FROM auth_invite")).scalar_one() == 0


def test_the_lookup_refuses_to_run_inside_a_workspace(app_engine, household):
    """A session-level workspace (which the app never sets) would make the lookup a write
    path's sibling: refused."""
    with app_engine.connect() as conn:
        conn.execute(
            text("SELECT set_config('app.workspace_id', :ws, false)"),
            {"ws": str(household.workspace_id)},
        )
        conn.commit()
        try:

            class OneConnection:
                @contextmanager
                def begin(self):
                    with conn.begin():
                        yield conn

            with (
                pytest.raises(lookup.LookupScopeError),
                lookup.auth_lookup_transaction(OneConnection()),
            ):
                pass
        finally:
            conn.rollback()
            conn.execute(text("RESET app.workspace_id"))
            conn.commit()
