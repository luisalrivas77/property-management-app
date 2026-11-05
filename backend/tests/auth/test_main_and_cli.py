"""The composition root (hausbuch.api.main) and the CLI (hausbuch.cli).

main.py: the API refuses to start as the schema owner, as a role with INSERT on workspace,
or as a superuser (§10.5); `hausbuch.api.main:app` builds from the environment.
cli.py: `hausbuch bootstrap` against a fresh, migrated database as hausbuch_owner (the dev
setup of the "Running it" section), refusals, `hausbuch invite`, `hausbuch openapi`.

Real URLs for the NOLOGIN test roles use libpq's `-c role=` startup option (role_url).
"""

import json
import re
import uuid

import psycopg
import pytest
from authenticator import SoftAuthenticator

# Module-level only: inside a test body, `conftest` may already be tests/db's module.
from conftest import (
    ADMIN_URL,
    APP,
    ORIGIN,
    OWNER,
    RP_ID,
    db,
    enroll,
    make_settings,
    new_client,
    role_url,
)
from psycopg import sql as pgsql

from hausbuch import cli
from hausbuch.api import main
from hausbuch.db.session import UnsafeDatabaseRoleError
from hausbuch.storage import LocalBlobStore

KEYS = {"HAUSBUCH_IBAN_HMAC_KEY": "11" * 32, "HAUSBUCH_SESSION_KEY": "22" * 32}

# --- main.py: role refusals ----------------------------------------------------------------


def _build(settings, engine, tmp_path):
    return main.build_app(settings, engine=engine, blob_store=LocalBlobStore(tmp_path))


def test_the_app_role_starts(app_engine, settings, tmp_path):
    app = _build(settings, app_engine, tmp_path)
    assert app.state.engine is app_engine
    assert isinstance(app.state.session_resolver, main.SessionResolverImpl)


def test_the_schema_owner_is_refused(owner_engine, settings, tmp_path):
    with pytest.raises(main.SchemaOwnerRoleError, match="'hausbuch_owner'"):
        _build(settings, owner_engine, tmp_path)


def test_a_superuser_is_refused(dbname, settings, tmp_path):
    engine = db.make_engine(dbname, "postgres")
    try:
        with pytest.raises(UnsafeDatabaseRoleError, match="superuser"):
            _build(settings, engine, tmp_path)
    finally:
        engine.dispose()


@pytest.fixture
def extra_role(dbname):
    """A throwaway NOLOGIN role, dropped after the test (privileges first)."""
    name = f"hausbuch_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(pgsql.SQL("CREATE ROLE {} NOLOGIN").format(pgsql.Identifier(name)))
    yield name
    with psycopg.connect(ADMIN_URL, dbname=dbname, autocommit=True) as conn:
        conn.execute(pgsql.SQL("DROP OWNED BY {}").format(pgsql.Identifier(name)))
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(pgsql.SQL("DROP ROLE {}").format(pgsql.Identifier(name)))


def _grant(dbname, statement, role):
    with psycopg.connect(ADMIN_URL, dbname=dbname, autocommit=True) as conn:
        conn.execute(pgsql.SQL(statement).format(pgsql.Identifier(role)))


def test_a_role_that_may_insert_workspaces_is_refused(dbname, extra_role, settings, tmp_path):
    _grant(dbname, "GRANT SELECT, INSERT ON workspace TO {}", extra_role)
    engine = db.make_engine(dbname, extra_role)
    try:
        with pytest.raises(main.SchemaOwnerRoleError, match="insert workspaces"):
            _build(settings, engine, tmp_path)
    finally:
        engine.dispose()


def test_a_member_of_the_owner_role_is_refused(dbname, extra_role, settings, tmp_path):
    _grant(dbname, f"GRANT {OWNER} TO {{}}", extra_role)
    engine = db.make_engine(dbname, extra_role)
    try:
        with pytest.raises(main.SchemaOwnerRoleError):
            _build(settings, engine, tmp_path)
    finally:
        engine.dispose()


def test_every_new_connection_is_checked(owner_engine, app_engine):
    """The per-connection listener, independent of the startup check."""
    main.refuse_schema_owner(owner_engine)
    main.refuse_schema_owner(owner_engine)  # idempotent
    with pytest.raises(main.SchemaOwnerRoleError), owner_engine.connect():
        pass
    main.refuse_schema_owner(app_engine)
    with app_engine.connect():
        pass


def _api_environ(dbname, role, tmp_path) -> dict[str, str]:
    return KEYS | {
        "HAUSBUCH_DATABASE_URL": role_url(dbname, role),
        "HAUSBUCH_RP_ID": RP_ID,
        "HAUSBUCH_ORIGIN": ORIGIN,
        "HAUSBUCH_BLOB_STORE": "local",
        "HAUSBUCH_BLOB_ROOT": str(tmp_path),
    }


def test_main_app_builds_from_the_environment(dbname, household, tmp_path, monkeypatch):
    """`uvicorn hausbuch.api.main:app`: lazily built, once, from HAUSBUCH_* variables."""
    for name, value in _api_environ(dbname, APP, tmp_path).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(main, "_app", None)
    try:
        app = main.app
        assert main.app is app
        assert app.state.settings.origin == ORIGIN
        client = new_client(app)
        response = enroll(client, household.token, SoftAuthenticator(RP_ID, ORIGIN))
        assert response.status_code == 201
        assert client.get("/api/auth/me").status_code == 200
    finally:
        if main._app is not None:
            main._app.state.engine.dispose()
        monkeypatch.setattr(main, "_app", None)


def test_main_app_refuses_the_owner_url(dbname, tmp_path, monkeypatch):
    for name, value in _api_environ(dbname, OWNER, tmp_path).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(main, "_app", None)
    with pytest.raises(main.SchemaOwnerRoleError):
        main.app  # noqa: B018 - the attribute access builds the app
    assert main._app is None
    with pytest.raises(AttributeError):
        main.not_there  # noqa: B018


# --- cli.py ---------------------------------------------------------------------------------


@pytest.fixture
def dev_db():
    """A fresh database like the dev setup: owned by hausbuch_owner, migrated as the owner."""
    name = db.create_database()
    try:
        db.alembic(name, "upgrade", "head")
        yield name
    finally:
        db.drop_database(name)


def _cli(capsys, argv, environ) -> tuple[int, str, str]:
    code = cli.main(argv, environ)
    out, err = capsys.readouterr()
    return code, out, err


BOOTSTRAP = ["bootstrap", "--workspace", "Haushalt Rivas", "--email", "Luis@Example.org"]


def test_bootstrap_as_the_owner_then_enroll(dev_db, capsys, tmp_path):
    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": role_url(dev_db, OWNER), "HAUSBUCH_ORIGIN": ORIGIN}
    code, out, err = _cli(capsys, [*BOOTSTRAP, "--name", "Luis"], environ)

    assert (code, err) == (0, "")
    match = re.fullmatch(
        r"Workspace ([0-9a-f-]{36}) created\. Enrol the first passkey within 24 h:\n"
        rf"{re.escape(ORIGIN)}/enroll#token=([A-Za-z0-9_-]{{43}})   \(shown once; not stored\)\n",
        out,
    )
    assert match, out
    assert "luis@example.org" not in out.lower()  # no email in the output

    # The link works against an API on the same database, as the app role.
    engine = db.make_engine(dev_db, APP)
    try:
        app = _build(make_settings(), engine, tmp_path)
        client = new_client(app)
        me = enroll(client, match[2], SoftAuthenticator(RP_ID, ORIGIN))
        assert me.status_code == 201
        assert me.json()["workspace"]["id"] == match[1]
        assert me.json()["user"]["email"] == "luis@example.org"
    finally:
        engine.dispose()

    # A second bootstrap is refused, unless --another-workspace.
    code, out, err = _cli(capsys, [*BOOTSTRAP, "--name", "Luis"], environ)
    assert code == 1 and out == "" and "has a workspace already" in err
    code, out, _ = _cli(
        capsys,
        ["bootstrap", "--workspace", "Zwei", "--email", "b@example.org", "--name", "B"]
        + ["--another-workspace", "--origin", "https://other.example"],
        environ,
    )
    assert code == 0 and "https://other.example/enroll#token=" in out
    # An email that exists already is a clean CLI error, not a traceback, and names no email.
    code, out, err = _cli(
        capsys,
        ["bootstrap", "--workspace", "Drei", "--email", "b@example.org", "--name", "B"]
        + ["--another-workspace", "--origin", "https://other.example"],
        environ,
    )
    assert code == 1 and out == "" and "exists already" in err
    assert "b@example.org" not in err and "Traceback" not in err

    # Recovery: `hausbuch invite` prints a fresh link for the existing user.
    code, out, err = _cli(
        capsys,
        ["invite", "--email", "luis@example.org", "--revoke-passkeys", "--revoke-sessions"],
        environ,
    )
    assert code == 0, err
    assert "Deleted 1 passkey(s)" in out and "Revoked 0 session(s)" in out
    assert re.search(r"/enroll#token=[A-Za-z0-9_-]{43}   \(shown once", out)


def test_bootstrap_refuses_a_superuser_url(dev_db, capsys):
    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": role_url(dev_db, None), "HAUSBUCH_ORIGIN": ORIGIN}
    code, out, err = _cli(capsys, [*BOOTSTRAP, "--name", "Luis"], environ)
    assert code == 1 and out == ""
    assert "connects as a superuser or a BYPASSRLS role" in err and "hausbuch_owner" in err


def test_bootstrap_as_the_app_role_fails_without_echoing_sql(dev_db, capsys):
    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": role_url(dev_db, APP), "HAUSBUCH_ORIGIN": ORIGIN}
    code, out, err = _cli(capsys, [*BOOTSTRAP, "--name", "Luis"], environ)
    assert (code, out) == (1, "")
    assert err == "hausbuch bootstrap: the database refused (InsufficientPrivilege)\n"


@pytest.mark.parametrize(
    "environ, argv, message",
    [
        ({"HAUSBUCH_ORIGIN": ORIGIN}, [], "HAUSBUCH_ADMIN_DATABASE_URL is not set"),
        ({"HAUSBUCH_ADMIN_DATABASE_URL": "postgresql+psycopg://x/y"}, [], "HAUSBUCH_ORIGIN"),
        (
            {"HAUSBUCH_ADMIN_DATABASE_URL": "x", "HAUSBUCH_ORIGIN": "hausbuch.example"},
            [],
            "HAUSBUCH_ORIGIN",
        ),
    ],
    ids=["no-url", "no-origin", "bad-origin"],
)
def test_bootstrap_configuration_errors(capsys, environ, argv, message):
    code, out, err = _cli(capsys, [*BOOTSTRAP, "--name", "Luis", *argv], environ)
    assert code == 1 and out == "" and message in err


def test_bootstrap_invalid_input_names_the_field_not_the_value(dev_db, capsys):
    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": role_url(dev_db, OWNER), "HAUSBUCH_ORIGIN": ORIGIN}
    argv = ["bootstrap", "--workspace", "W", "--email", "kein-at-zeichen", "--name", "Luis"]
    code, out, err = _cli(capsys, argv, environ)
    assert code == 1 and "email" in err and "kein-at-zeichen" not in err


def test_invite_for_an_unknown_email(dev_db, capsys):
    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": role_url(dev_db, OWNER), "HAUSBUCH_ORIGIN": ORIGIN}
    code, out, err = _cli(capsys, ["invite", "--email", "nobody@example.org"], environ)
    assert code == 1 and "no user with that email" in err and "nobody@" not in err


def test_openapi_prints_the_auth_contract(capsys):
    code, out, err = _cli(capsys, ["openapi"], {})
    assert code == 0 and err == ""
    document = json.loads(out)
    assert document["info"]["title"] == "Hausbuch"
    auth_paths = {p for p in document["paths"] if p.startswith("/api/auth/")}
    assert auth_paths == {
        "/api/auth/enroll/options",
        "/api/auth/enroll/verify",
        "/api/auth/login/options",
        "/api/auth/login/verify",
        "/api/auth/logout",
        "/api/auth/logout-all",
        "/api/auth/me",
        "/api/auth/passkeys",
        "/api/auth/passkeys/options",
        "/api/auth/passkeys/verify",
        "/api/auth/passkeys/{passkey_id}",
        "/api/auth/invites",
        "/api/auth/invites/{invite_id}",
    }
    assert set(document["paths"]["/api/auth/passkeys/{passkey_id}"]) == {"patch", "delete"}
    assert out == cli.openapi_json()  # deterministic, so the committed copy can be diffed
