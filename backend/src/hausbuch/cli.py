"""`hausbuch bootstrap | invite | openapi | evals` (phase 1 step 3, §5, §6.5, §6.7; phase 2 D41).

bootstrap and invite run as the schema owner (HAUSBUCH_ADMIN_DATABASE_URL), never through the
API's engine and never as a superuser: they run from the operator's computer with `uv`, the
only place the owner URL lives (ARCHITECTURE Decisions, admin commands, revised 5 Nov 2026).
They print one enrollment URL, once; the token is not stored, and nothing else they print names
a token or an email.

openapi prints the API's OpenAPI document (built without a database connection), which is
committed as frontend/src/api/openapi.json.

`evals report` reads the proposal.applied events with the same owner URL, read-only, and
prints pre-fill accuracy (hausbuch.evals.report). `evals run` sends the documents in
HAUSBUCH_EVAL_DIR to the model, live, and compares each reading with its golden file
(hausbuch.evals.run). Neither prints the connection URL or the API key, also not on an error.
"""

import argparse
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import Engine, create_engine, pool
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from hausbuch.db.session import UnsafeDatabaseRoleError, create_app_engine
from hausbuch.settings import SettingsError, load_admin_database_url

EXIT_OK, EXIT_ERROR, EXIT_USAGE = 0, 1, 2


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] = os.environ) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.command(args, environ)
    except CliError as exc:
        print(f"hausbuch {args.command_name}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except UnsafeDatabaseRoleError:
        print(
            f"hausbuch {args.command_name}: HAUSBUCH_ADMIN_DATABASE_URL connects as a superuser "
            "or a BYPASSRLS role; the CLI must run as the schema owner (hausbuch_owner), never "
            "as a superuser",
            file=sys.stderr,
        )
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
    from hausbuch.auth.errors import DuplicateUserError, WorkspaceExistsError
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
    except DuplicateUserError:
        # No email in the message: the operator typed it, and stderr may reach a log.
        raise CliError(
            "a user with that email exists already; use `hausbuch invite` instead"
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
    from hausbuch.api.app import create_app, openapi_document
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
        return openapi_document(app)  # the committed file's exact format (chunk 1)
    finally:
        engine.dispose()


class _NoSessions:
    def resolve(self, token: str, *, now: datetime) -> None:
        return None


# --- evals ----------------------------------------------------------------------------


def evals_report(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    from hausbuch.evals.report import format_report, read_report

    since = None if args.since is None else datetime.combine(args.since, datetime.min.time(), UTC)
    engine = owner_engine(environ)
    try:
        report, workspaces = read_report(engine, workspace_id=args.workspace, since=since)
    except SQLAlchemyError as exc:
        # Never the message: a connection error names the host and user. The class only.
        detail = type(getattr(exc, "orig", None) or exc).__name__
        raise CliError(
            f"could not read the events ({detail}); check HAUSBUCH_ADMIN_DATABASE_URL and "
            "that the database accepts this computer's address"
        ) from None
    finally:
        engine.dispose()
    sys.stdout.write(format_report(report, workspaces=workspaces, since=args.since))
    return EXIT_OK


def evals_run(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    from hausbuch.evals import run

    return run.main(args, environ)


# --- Helpers --------------------------------------------------------------------------


def owner_engine(environ: Mapping[str, str]) -> Engine:
    """The schema-owner engine: HAUSBUCH_ADMIN_DATABASE_URL, through create_app_engine so a
    superuser or BYPASSRLS role is refused on connect. Never the API's settings."""
    try:
        url = load_admin_database_url(environ)
    except SettingsError as exc:
        raise CliError(str(exc)) from None
    try:
        return create_app_engine(url, poolclass=pool.NullPool)
    except Exception:  # SQLAlchemy's ArgumentError quotes the URL, password included
        raise CliError("HAUSBUCH_ADMIN_DATABASE_URL is not a valid database URL") from None


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

    evals = commands.add_parser("evals", help="pre-fill accuracy and real-document evals")
    evals_commands = evals.add_subparsers(required=True, metavar="evals-command")
    report = evals_commands.add_parser(
        "report",
        help="per kind and field: what was accepted, edited, cleared or skipped (read-only)",
    )
    report.add_argument("--workspace", type=UUID, help="one workspace (default: all)")
    report.add_argument(
        "--since", type=date.fromisoformat, help="only proposals applied on or after (YYYY-MM-DD)"
    )
    report.set_defaults(command=evals_report, command_name="evals report")

    live = evals_commands.add_parser(
        "run",
        help="read the documents in HAUSBUCH_EVAL_DIR with the model (live, costs money) "
        "and compare with their <name>.expected.yaml",
    )
    live.add_argument("--dir", type=Path, help="the documents (default: HAUSBUCH_EVAL_DIR)")
    live.add_argument(
        "--show-values", action="store_true", help="print the values, not only match/differs"
    )
    live.add_argument(
        "--write-expected",
        action="store_true",
        help="write <name>.expected.yaml from this reading (check it first)",
    )
    live.add_argument(
        "--force", action="store_true", help="with --write-expected: overwrite existing files"
    )
    live.add_argument("names", nargs="*", help="only these documents (file names)")
    live.set_defaults(command=evals_run, command_name="evals run")

    return parser


if __name__ == "__main__":
    sys.exit(main())
