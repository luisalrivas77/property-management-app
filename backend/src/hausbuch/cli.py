"""`hausbuch bootstrap | invite | openapi` (phase 1 step 3, §5, §6.5, §6.7).

bootstrap and invite run as the schema owner (HAUSBUCH_ADMIN_DATABASE_URL), never through the
API's engine and never as a superuser: on Scaleway they are Serverless Jobs, the only place
the owner URL is injected (§14). They print one enrollment URL, once; the token is not
stored, and nothing else they print names a token or an email.

openapi prints the API's OpenAPI document (built without a database connection), which is
committed as frontend/src/api/openapi.json.
"""

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import Engine, create_engine, pool
from sqlalchemy.exc import DBAPIError

from hausbuch.db.session import UnsafeDatabaseRoleError, create_app_engine
from hausbuch.settings import SettingsError, load_admin_database_url

EXIT_OK, EXIT_ERROR, EXIT_USAGE = 0, 1, 2


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] = os.environ) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.command(args, environ)
    except (CliError, UnsafeDatabaseRoleError) as exc:
        print(f"hausbuch {args.command_name}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except DBAPIError as exc:
        # Never the DBAPI message or SQLAlchemy's statement/parameters: they carry values
        # (§10.5). The class name (e.g. InsufficientPrivilege) and constraint are enough.
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        detail = type(exc.orig).__name__ + (f", constraint {constraint}" if constraint else "")
        print(f"hausbuch {args.command_name}: the database refused ({detail})", file=sys.stderr)
        return EXIT_ERROR


class CliError(Exception):
    """A refusal the operator can fix; printed without a traceback."""


# --- bootstrap ----------------------------------------------------------------------


def bootstrap(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    from hausbuch.auth.errors import WorkspaceExistsError
    from hausbuch.auth.invites import enrollment_url
    from hausbuch.auth.lookup import provisioned_workspace_ids
    from hausbuch.auth.provisioning import provision_workspace

    origin = _origin(args, environ)
    engine = owner_engine(environ)
    try:
        if not args.another_workspace and provisioned_workspace_ids(engine):
            raise WorkspaceExistsError("the database has a workspace already")
        provisioned = provision_workspace(
            engine,
            name=args.workspace,
            email=args.email,
            display_name=args.name,
            now=datetime.now(UTC),
        )
    except WorkspaceExistsError:
        raise CliError(
            "the database has a workspace already; pass --another-workspace to add one "
            "(SaaS path, not used in v1), or use `hausbuch invite` for a new enrollment link"
        ) from None
    except ValueError as exc:  # pydantic's ValidationError too: bad email, empty name
        raise CliError(f"invalid input ({_fields(exc)})") from None
    finally:
        engine.dispose()
    print(f"Workspace {provisioned.workspace_id} created. Enrol the first passkey within 24 h:")
    print(f"{enrollment_url(origin, provisioned.invite.token)}   (shown once; not stored)")
    return EXIT_OK


# --- invite ---------------------------------------------------------------------------


def invite(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    from hausbuch.auth.errors import UserNotFoundError
    from hausbuch.auth.invites import enrollment_url
    from hausbuch.auth.provisioning import reissue_enrollment

    origin = _origin(args, environ)
    engine = owner_engine(environ)
    try:
        reissued = reissue_enrollment(
            engine,
            email=args.email,
            workspace_id=args.workspace_id,
            revoke_passkeys=args.revoke_passkeys,
            revoke_sessions=args.revoke_sessions,
            now=datetime.now(UTC),
        )
    except UserNotFoundError:
        raise CliError("no user with that email (pass --workspace-id to narrow)") from None
    except ValueError as exc:
        raise CliError(f"invalid input ({_fields(exc)})") from None
    finally:
        engine.dispose()
    if args.revoke_passkeys:
        print(f"Deleted {reissued.passkeys_deleted} passkey(s) and ended their sessions.")
    if args.revoke_sessions:
        print(f"Revoked {reissued.sessions_revoked} session(s).")
    print("Enrol a passkey within 24 h (earlier unused links no longer work):")
    print(f"{enrollment_url(origin, reissued.invite.token)}   (shown once; not stored)")
    return EXIT_OK


# --- openapi --------------------------------------------------------------------------


def openapi(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    sys.stdout.write(openapi_json())
    return EXIT_OK


def openapi_json() -> str:
    """The OpenAPI document of the real app, built with placeholder settings and an engine
    that never connects (create_app does not touch the database)."""
    from hausbuch.api.app import create_app
    from hausbuch.settings import Settings
    from hausbuch.storage import LocalBlobStore

    settings = Settings(
        database_url="postgresql+psycopg://openapi.invalid/hausbuch",
        iban_hmac_key=b"\x01" * 32,
        session_key=b"\x02" * 32,
        rp_id="hausbuch.invalid",
        origin="https://hausbuch.invalid",
        blob_store="local",
        blob_root=Path(tempfile.gettempdir()),
        run_worker=False,
    )
    engine = create_engine("postgresql+psycopg://", poolclass=pool.NullPool)
    try:
        app = create_app(settings, engine, LocalBlobStore(settings.blob_root), _NoSessions())
        return json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"
    finally:
        engine.dispose()


class _NoSessions:
    def resolve(self, token: str, *, now: datetime) -> None:
        return None


# --- Helpers --------------------------------------------------------------------------


def owner_engine(environ: Mapping[str, str]) -> Engine:
    """The schema-owner engine: HAUSBUCH_ADMIN_DATABASE_URL, through create_app_engine so a
    superuser or BYPASSRLS role is refused on connect. Never the API's settings."""
    try:
        url = load_admin_database_url(environ)
    except SettingsError as exc:
        raise CliError(str(exc)) from None
    return create_app_engine(url, poolclass=pool.NullPool)


def _origin(args: argparse.Namespace, environ: Mapping[str, str]) -> str:
    origin = (args.origin or environ.get("HAUSBUCH_ORIGIN", "")).strip()
    parts = urlsplit(origin)
    if parts.scheme not in ("https", "http") or not parts.hostname or parts.path or parts.query:
        raise CliError(
            "the web origin is needed for the enrollment URL: set HAUSBUCH_ORIGIN or pass "
            "--origin, e.g. https://hausbuch.example"
        )
    return origin


def _fields(exc: ValueError) -> str:
    """Which inputs were refused, never their values."""
    errors = getattr(exc, "errors", None)
    if callable(errors):
        names = sorted({".".join(map(str, e["loc"])) or "input" for e in errors()})
        return ", ".join(names)
    return "see the command's help"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hausbuch", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(required=True, metavar="command")

    boot = commands.add_parser(
        "bootstrap", help="create the workspace and its first user; prints an enrollment URL"
    )
    boot.add_argument("--workspace", required=True, help='workspace name, e.g. "Haushalt Rivas"')
    boot.add_argument("--email", required=True, help="the first user's email (sign-in name)")
    boot.add_argument("--name", required=True, help="the first user's display name")
    boot.add_argument(
        "--another-workspace",
        action="store_true",
        help="allow a second workspace in the same database (SaaS path, not used in v1)",
    )
    boot.add_argument("--origin", help="web origin for the URL (default: HAUSBUCH_ORIGIN)")
    boot.set_defaults(command=bootstrap, command_name="bootstrap")

    inv = commands.add_parser(
        "invite", help="a fresh enrollment URL for an existing user (recovery)"
    )
    inv.add_argument("--email", required=True)
    inv.add_argument("--workspace-id", type=UUID, help="the user's workspace, if ambiguous")
    inv.add_argument(
        "--revoke-passkeys",
        action="store_true",
        help="delete all of the user's passkeys and end the sessions they opened",
    )
    inv.add_argument(
        "--revoke-sessions", action="store_true", help="revoke all of the user's sessions"
    )
    inv.add_argument("--origin", help="web origin for the URL (default: HAUSBUCH_ORIGIN)")
    inv.set_defaults(command=invite, command_name="invite")

    api = commands.add_parser("openapi", help="print the API's OpenAPI document")
    api.set_defaults(command=openapi, command_name="openapi")
    return parser


if __name__ == "__main__":
    sys.exit(main())
