"""`hausbuch bootstrap | invite | openapi | evals | household-reask | rematch-bills |
rematch-waiting | household-resort | follow-accounts | repair-periods | relink-transfers |
pair-returns | store-report-prices | group-exchange-trades` (phase 1 step 3, §5, §6.5, §6.7;
phase 2 D41, D27 amended 15 Nov 2026; phase 6 D159 amended 13 and 14 Nov 2026, D161 widened 14
Nov 2026, D160 amended (4) 14 Nov 2026, D165 (a) and D176 (3) amended 14 Nov 2026, D162 and
D167 (c) amended 15 Nov 2026, D162 amended (6) and (7) 15 Nov 2026, D178 amended (3) 16 Nov
2026).

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

`household-reask` runs services.matching.reask_household once for a workspace, with the same
owner URL: it queues a re-ask job per private line parked in a coarse household code, which the
API container's worker runs (the model key stays there). --dry-run counts and queues nothing.
--category names the codes to re-ask (repeatable; e.g. hh_mobility after migration 0031 split
fuel off), by default the codes migration 0029 split up.

`rematch-bills` runs services.matching.rematch_recorded_bills once for a workspace, with the
same owner URL: it queues a rematch per bill recorded as a Nebenkosten or private_cost row, so
the private lines already waiting that pay one are proposed as its payment (never the model,
never a confirmed line). --dry-run counts and queues nothing.

`rematch-waiting` runs services.matching.rematch_private_lines once for a workspace, with the
same owner URL: it queues a rematch per private account, so every line of it still waiting in
Zu prüfen is checked again against the learned rules and the matchers (never the model, never
a confirmed line), once after the deploy of D160 amended (4). --dry-run counts and queues
nothing.

`repair-periods` runs services.matching.repair_periods once for a workspace, with the same
owner URL: every line still proposed with a service period outside the plausible years of its
booking date is proposed again with its period read by the current rule (phase 2 D27 amended
15 Nov 2026; never a confirmed line). --dry-run counts and changes nothing.

`household-resort` runs services.household_resort.resort_household the same way: it queues a
job per private line booked in a group's fallback, proposed or confirmed, so the model sorts it
into one of the group's subcategories (migration 0032's, and the Geldanlage group's of 0036); a
finer answer goes back to Zu prüfen as a proposal. --category names the fallback codes to take
(repeatable; e.g. hh_investment after migration 0036), by default every group's fallback.
--dry-run prints what it would take and what it leaves alone, by reason.

`relink-transfers` runs services.transfers.relink_all once for a workspace, with the same owner
URL (phase 6 D162 amended 15 Nov 2026): on every account, each transfer leg is linked where one
counterpart fits now, and a depot's cash line that a bank debit names by the depot's IBAN is
booked as that transfer's leg when it holds no confirmed answer; a line of a link already
written, tied by an own IBAN, that holds only the model's proposal of the transfer is booked as
its leg too (D162 amended (6)). It runs here, not in the worker: it reads and writes only the
database. --dry-run rolls back and prints what it would link and book.

`pair-returns` runs services.returns.count_pairs once for a workspace, with the same owner URL
(phase 6 D162 amended (7), 15 Nov 2026): it prints how many returned payments of the private
accounts the lines already imported hold, the „Rücküberweisung erkannt“ cards Zu prüfen shows,
and how many of them offer more than one debit. It never books: the cards are computed when Zu
prüfen is read, so a new import is recognised without it and there is nothing to write; it
reads only, and --apply (accepted for symmetry with the other one-off commands) says so.

`store-report-prices` runs services.depot.store_all_report_prices once for a workspace, with
the same owner URL (phase 6 D167 amended (c), 15 Nov 2026): every current quarterly-report
position of every depot that agrees with the confirmed movements now and has no price yet
stores the report's price (value ÷ units), as a trade confirmation applied after the report
does since that change. It reads and writes only the database. --dry-run rolls back and prints
what it would store.

`group-exchange-trades` runs services.exchange.imports.group_stored_trades once for a workspace,
with the same owner URL (phase 6 D178 amended (3), migration 0043): every exchange trade
imported before 0043 (Binance: the acquisition, disposal and fee movements of one second;
BISON: a Buy or Sell row) gets its movement group, each leg a grouped row that supersedes the
stored one (amounts and status unchanged), and an acquisition whose trade paid a fee in EUR
that fee as its fee_eur, as an import writes them now. A second run finds nothing. --dry-run
rolls back and prints what it would group.

`follow-accounts` queues one worker job (jobs.follow_accounts) once after the deploy of
14 Nov 2026: every statement waiting under Dokumente is imported onto the account that already
holds its IBAN or number, and every ready depot suggestion is rebuilt with the depot it prints
pre-selected and its Steuerjahr. The worker runs it because the stored files are read from the
object store, which this computer does not hold. --dry-run counts and queues nothing.
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


# --- household-reask ------------------------------------------------------------------


class _DryRun(Exception):
    """Rolls the reask's transaction back: the counts are printed, no job is queued."""


def household_reask(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    from hausbuch.db.session import workspace_transaction
    from hausbuch.schemas.allocation import AllocationCategory
    from hausbuch.schemas.enums import ActorType
    from hausbuch.services.context import Actor, new_command
    from hausbuch.services.matching import (
        REASK_CATEGORIES,
        REASKABLE_CATEGORIES,
        reask_household,
    )

    categories = REASK_CATEGORIES
    if args.category:
        allowed = {c.value for c in REASKABLE_CATEGORIES}
        unknown = sorted(set(args.category) - allowed)
        if unknown:
            raise CliError(
                f"cannot re-ask {', '.join(unknown)}; --category takes one of "
                f"{', '.join(sorted(allowed))}"
            )
        categories = frozenset(AllocationCategory(c) for c in args.category)

    engine = owner_engine(environ)
    try:
        try:
            with workspace_transaction(engine, args.workspace) as conn:
                actor = Actor(ActorType.SYSTEM, "cli:household-reask")
                ctx = new_command(conn, args.workspace, actor)
                result = reask_household(ctx, categories)
                if args.dry_run:
                    raise _DryRun
        except _DryRun:
            pass
    except SQLAlchemyError as exc:
        detail = type(getattr(exc, "orig", None) or exc).__name__
        raise CliError(
            f"could not reach the database ({detail}); check HAUSBUCH_ADMIN_DATABASE_URL and "
            "that the database accepts this computer's address"
        ) from None
    finally:
        engine.dispose()
    verb = "would queue" if args.dry_run else "queued"
    print(f"{verb} {result.queued} line(s) for the model")
    if result.held_back:
        print(
            f"held back {result.held_back} line(s): „Private Konten mit KI zuordnen“ is off "
            "(Einstellungen); switch it on and run this again"
        )
    print(f"left alone {result.purchase_costs} line(s) that may be a cost of buying the flat")
    print(f"skipped {result.already_asked} line(s) already asked with the current prompt")
    return EXIT_OK


# --- rematch-bills --------------------------------------------------------------------


def rematch_bills(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    from hausbuch.db.session import workspace_transaction
    from hausbuch.schemas.enums import ActorType
    from hausbuch.services.context import Actor, new_command
    from hausbuch.services.matching import rematch_recorded_bills

    engine = owner_engine(environ)
    try:
        try:
            with workspace_transaction(engine, args.workspace) as conn:
                actor = Actor(ActorType.SYSTEM, "cli:rematch-bills")
                result = rematch_recorded_bills(new_command(conn, args.workspace, actor))
                if args.dry_run:
                    raise _DryRun
        except _DryRun:
            pass
    except SQLAlchemyError as exc:
        detail = type(getattr(exc, "orig", None) or exc).__name__
        raise CliError(
            f"could not reach the database ({detail}); check HAUSBUCH_ADMIN_DATABASE_URL and "
            "that the database accepts this computer's address"
        ) from None
    finally:
        engine.dispose()
    verb = "would queue" if args.dry_run else "queued"
    print(f"{verb} a rematch for {result.bills} recorded bill(s)")
    print(f"{result.waiting} waiting private line(s) have the amount and date of one of them")
    return EXIT_OK


# --- rematch-waiting ------------------------------------------------------------------


def rematch_waiting(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    from hausbuch.db.session import workspace_transaction
    from hausbuch.schemas.enums import ActorType
    from hausbuch.services.context import Actor, new_command
    from hausbuch.services.matching import rematch_private_lines

    engine = owner_engine(environ)
    try:
        try:
            with workspace_transaction(engine, args.workspace) as conn:
                actor = Actor(ActorType.SYSTEM, "cli:rematch-waiting")
                result = rematch_private_lines(new_command(conn, args.workspace, actor))
                if args.dry_run:
                    raise _DryRun
        except _DryRun:
            pass
    except SQLAlchemyError as exc:
        detail = type(getattr(exc, "orig", None) or exc).__name__
        raise CliError(
            f"could not reach the database ({detail}); check HAUSBUCH_ADMIN_DATABASE_URL and "
            "that the database accepts this computer's address"
        ) from None
    finally:
        engine.dispose()
    verb = "would queue" if args.dry_run else "queued"
    print(f"{verb} a rematch for {result.accounts} private account(s)")
    print(f"{result.waiting} private line(s) are waiting and will be checked against the rules")
    return EXIT_OK


# --- relink-transfers -----------------------------------------------------------------


def relink_transfers(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    """services.transfers.relink_all once for a workspace (phase 6 D162 amended 15 Nov 2026):
    every account's transfers are linked where one counterpart fits now, and a depot's cash
    line named by the bank's debit is booked as its leg; D162 amended (6): a line of a link
    tied by an own IBAN that holds only the model's proposal of the transfer is booked as its
    leg too (transfers.confirm_linked). `--dry-run` rolls everything back."""
    from hausbuch.db.session import workspace_transaction
    from hausbuch.schemas.enums import ActorType
    from hausbuch.services.context import Actor, new_command
    from hausbuch.services.transfers import confirm_linked, relink_all

    engine = owner_engine(environ)
    try:
        try:
            with workspace_transaction(engine, args.workspace) as conn:
                actor = Actor(ActorType.SYSTEM, "cli:relink-transfers")
                ctx = new_command(conn, args.workspace, actor)
                linked = relink_all(ctx)
                confirmed = confirm_linked(ctx)
                if args.dry_run:
                    raise _DryRun
        except _DryRun:
            pass
    except SQLAlchemyError as exc:
        detail = type(getattr(exc, "orig", None) or exc).__name__
        raise CliError(
            f"could not reach the database ({detail}); check HAUSBUCH_ADMIN_DATABASE_URL and "
            "that the database accepts this computer's address"
        ) from None
    finally:
        engine.dispose()
    verb = "would link" if args.dry_run else "linked"
    print(f"{verb} {linked} transfer(s)")
    verb = "would book" if args.dry_run else "booked"
    print(f"{verb} {confirmed} linked line(s) the model had only proposed as the transfer")
    return EXIT_OK


# --- pair-returns ---------------------------------------------------------------------


def pair_returns(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    """services.returns.count_pairs once for a workspace (phase 6 D162 amended (7)): the
    returned payments the imported lines hold, never booked. Read-only, with or without
    `--apply`: the cards are computed on read."""
    from hausbuch.db.session import workspace_transaction
    from hausbuch.services.returns import count_pairs

    engine = owner_engine(environ)
    try:
        try:
            with workspace_transaction(engine, args.workspace) as conn:
                cards, several = count_pairs(conn)
                raise _DryRun  # read-only: roll back whatever the read touched
        except _DryRun:
            pass
    except SQLAlchemyError as exc:
        detail = type(getattr(exc, "orig", None) or exc).__name__
        raise CliError(
            f"could not reach the database ({detail}); check HAUSBUCH_ADMIN_DATABASE_URL and "
            "that the database accepts this computer's address"
        ) from None
    finally:
        engine.dispose()
    print(f"found {cards} returned payment(s) on the private accounts")
    print(f"{several} of them offer more than one debit to choose from")
    if args.apply:
        print(
            "nothing to apply: Zu prüfen shows each one as „Rücküberweisung erkannt“ when it is "
            "read, and only a person books it there"
        )
    return EXIT_OK


# --- store-report-prices --------------------------------------------------------------


def store_report_prices(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    """services.depot.store_all_report_prices once for a workspace (phase 6 D167 amended (c),
    15 Nov 2026): the price of every report position that agrees now and has none stored.
    `--dry-run` rolls everything back."""
    from hausbuch.db.session import workspace_transaction
    from hausbuch.schemas.enums import ActorType
    from hausbuch.services.context import Actor, new_command
    from hausbuch.services.depot import store_all_report_prices

    engine = owner_engine(environ)
    try:
        try:
            with workspace_transaction(engine, args.workspace) as conn:
                actor = Actor(ActorType.SYSTEM, "cli:store-report-prices")
                stored = store_all_report_prices(new_command(conn, args.workspace, actor))
                if args.dry_run:
                    raise _DryRun
        except _DryRun:
            pass
    except SQLAlchemyError as exc:
        detail = type(getattr(exc, "orig", None) or exc).__name__
        raise CliError(
            f"could not reach the database ({detail}); check HAUSBUCH_ADMIN_DATABASE_URL and "
            "that the database accepts this computer's address"
        ) from None
    finally:
        engine.dispose()
    verb = "would store" if args.dry_run else "stored"
    print(f"{verb} {stored} price(s) from the depot reports")
    return EXIT_OK


# --- group-exchange-trades ------------------------------------------------------------


def group_exchange_trades(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    """services.exchange.imports.group_stored_trades once for a workspace (phase 6 D178
    amended (3), migration 0043): every exchange trade imported before 0043 tied as one
    movement group, its legs superseded by grouped rows. `--dry-run` rolls everything back."""
    from hausbuch.db.session import workspace_transaction
    from hausbuch.schemas.enums import ActorType
    from hausbuch.services.context import Actor, new_command
    from hausbuch.services.exchange.imports import group_stored_trades

    engine = owner_engine(environ)
    try:
        try:
            with workspace_transaction(engine, args.workspace) as conn:
                actor = Actor(ActorType.SYSTEM, "cli:group-exchange-trades")
                result = group_stored_trades(new_command(conn, args.workspace, actor))
                if args.dry_run:
                    raise _DryRun
        except _DryRun:
            pass
    except SQLAlchemyError as exc:
        detail = type(getattr(exc, "orig", None) or exc).__name__
        raise CliError(
            f"could not reach the database ({detail}); check HAUSBUCH_ADMIN_DATABASE_URL and "
            "that the database accepts this computer's address"
        ) from None
    finally:
        engine.dispose()
    verb = "would group" if args.dry_run else "grouped"
    print(f"{verb} {result.groups} exchange trade(s) of {result.movements} movement(s)")
    verb = "would give" if args.dry_run else "gave"
    print(f"{verb} {result.fees} acquisition(s) the fee their trade paid in EUR")
    return EXIT_OK


# --- repair-periods -------------------------------------------------------------------


def repair_periods(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    from hausbuch.db.session import workspace_transaction
    from hausbuch.schemas.enums import ActorType
    from hausbuch.services.context import Actor, new_command
    from hausbuch.services.matching import repair_periods as repair

    engine = owner_engine(environ)
    try:
        try:
            with workspace_transaction(engine, args.workspace) as conn:
                actor = Actor(ActorType.SYSTEM, "cli:repair-periods")
                repaired = repair(new_command(conn, args.workspace, actor))
                if args.dry_run:
                    raise _DryRun
        except _DryRun:
            pass
    except SQLAlchemyError as exc:
        detail = type(getattr(exc, "orig", None) or exc).__name__
        raise CliError(
            f"could not reach the database ({detail}); check HAUSBUCH_ADMIN_DATABASE_URL and "
            "that the database accepts this computer's address"
        ) from None
    finally:
        engine.dispose()
    verb = "would propose" if args.dry_run else "proposed"
    print(f"{verb} {repaired} line(s) again with a period read by the current rule")
    return EXIT_OK


# --- follow-accounts ------------------------------------------------------------------


def follow_accounts(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    """Queues one `follow_accounts` job for the workspace (phase 6 D165 (a), D176 (3)
    amended 14 Nov 2026); the worker runs it, since reading the stored files needs the object
    store. `--dry-run` counts what it would look at and queues nothing."""
    from hausbuch.db.session import workspace_transaction
    from hausbuch.jobs.enqueue import enqueue_follow_accounts
    from hausbuch.services.proposals import count_ready_depot_proposals
    from hausbuch.services.statements import waiting_statement_documents

    engine = owner_engine(environ)
    try:
        with workspace_transaction(engine, args.workspace) as conn:
            statements = len(waiting_statement_documents(conn))
            suggestions = count_ready_depot_proposals(conn)
            if not args.dry_run:
                enqueue_follow_accounts(conn, workspace_id=args.workspace)
    except SQLAlchemyError as exc:
        detail = type(getattr(exc, "orig", None) or exc).__name__
        raise CliError(
            f"could not reach the database ({detail}); check HAUSBUCH_ADMIN_DATABASE_URL and "
            "that the database accepts this computer's address"
        ) from None
    finally:
        engine.dispose()
    print(
        f"{statements} statement file(s) wait under Dokumente; each one an account already "
        "holds the IBAN or number of is imported onto it"
    )
    print(f"{suggestions} ready depot suggestion(s) are rebuilt where they change")
    if args.dry_run:
        print("dry run: nothing queued")
    else:
        print("queued; the worker does it within a minute (see its log: accounts followed)")
    return EXIT_OK


# --- household-resort -----------------------------------------------------------------


def household_resort(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    """services.household_resort.resort_household once for a workspace (phase 6 D159
    amended 14 Nov 2026, migration 0032); `--dry-run` prints the counts and queues nothing.
    Each queued line is one categorization per run (a retry or the hard-model escalation
    may add a call, as jobs.matching._categorize does)."""
    from hausbuch.db.session import workspace_transaction
    from hausbuch.schemas.allocation import AllocationCategory
    from hausbuch.schemas.enums import ActorType
    from hausbuch.services.context import Actor, new_command
    from hausbuch.services.household_resort import RESORT_CATEGORIES, resort_household

    categories = RESORT_CATEGORIES
    if args.category:
        allowed = {c.value for c in RESORT_CATEGORIES}
        unknown = sorted(set(args.category) - allowed)
        if unknown:
            raise CliError(
                f"cannot re-sort {', '.join(unknown)}; --category takes one of "
                f"{', '.join(sorted(allowed))}"
            )
        categories = frozenset(AllocationCategory(c) for c in args.category)

    engine = owner_engine(environ)
    try:
        try:
            with workspace_transaction(engine, args.workspace) as conn:
                actor = Actor(ActorType.SYSTEM, "cli:household-resort")
                ctx = new_command(conn, args.workspace, actor)
                result = resort_household(ctx, categories)
                if args.dry_run:
                    raise _DryRun
        except _DryRun:
            pass
    except SQLAlchemyError as exc:
        detail = type(getattr(exc, "orig", None) or exc).__name__
        raise CliError(
            f"could not reach the database ({detail}); check HAUSBUCH_ADMIN_DATABASE_URL and "
            "that the database accepts this computer's address"
        ) from None
    finally:
        engine.dispose()
    taken = result.proposed + result.confirmed
    print(
        f"taken {taken} line(s) in a group's fallback: {result.proposed} proposed, "
        f"{result.confirmed} confirmed"
    )
    verb = "would queue" if args.dry_run else "queued"
    print(
        f"{verb} {result.queued} line(s) for the model: one categorization per line per run "
        "(a retry or the hard-model escalation may add a call)"
    )
    if result.held_back:
        print(
            f"held back {result.held_back} line(s): „Private Konten mit KI zuordnen“ is off "
            "(Einstellungen); switch it on and run this again"
        )
    print("left alone:")
    print(f"  {result.split} split line(s) (more than one row)")
    print(f"  {result.linked} line(s) with a property, Beleg, transfer link or depot movement")
    print(f"  {result.purchase_costs} line(s) that may be a cost of buying the flat")
    print(f"  {result.already_asked} line(s) already asked with the current prompt")
    return EXIT_OK


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
    # Broad on purpose: SQLAlchemy 2.0.57 raises ArgumentError (without the URL) or a plain
    # ValueError for a bad port, but no driver or future message may reach stderr.
    except Exception:
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

    reask = commands.add_parser(
        "household-reask",
        help="ask the model again for private lines still proposed in a coarse household "
        "category (after a migration that adds finer ones: 0029, 0031)",
    )
    reask.add_argument("--workspace", type=UUID, required=True, help="the workspace id")
    reask.add_argument(
        "--category",
        action="append",
        metavar="CODE",
        help="re-ask the lines proposed in this code, repeatable (e.g. hh_mobility after 0031); "
        "default: the codes 0029 split up (hh_other, uncategorized, hh_leisure_travel, "
        "hh_subscriptions, hh_housing)",
    )
    reask.add_argument("--dry-run", action="store_true", help="count only, queue nothing")
    reask.set_defaults(command=household_reask, command_name="household-reask")

    bills = commands.add_parser(
        "rematch-bills",
        help="match the waiting private lines again against the bills recorded as Nebenkosten "
        "or private_cost rows (once, after the deploy of D161 widened)",
    )
    bills.add_argument("--workspace", type=UUID, required=True, help="the workspace id")
    bills.add_argument("--dry-run", action="store_true", help="count only, queue nothing")
    bills.set_defaults(command=rematch_bills, command_name="rematch-bills")

    waiting = commands.add_parser(
        "rematch-waiting",
        help="check every waiting line of the private accounts again against the learned "
        "rules and the matchers, never the model (once, after the deploy of D160 amended (4))",
    )
    waiting.add_argument("--workspace", type=UUID, required=True, help="the workspace id")
    waiting.add_argument("--dry-run", action="store_true", help="count only, queue nothing")
    waiting.set_defaults(command=rematch_waiting, command_name="rematch-waiting")

    periods = commands.add_parser(
        "repair-periods",
        help="propose again the lines still proposed with a service period far from their "
        "booking date, read by the current rule (once, after the deploy of D27 amended)",
    )
    periods.add_argument("--workspace", type=UUID, required=True, help="the workspace id")
    periods.add_argument("--dry-run", action="store_true", help="count only, change nothing")
    periods.set_defaults(command=repair_periods, command_name="repair-periods")

    relinked = commands.add_parser(
        "relink-transfers",
        help="link the transfers between own accounts once more on every account, booking a "
        "depot's cash line the bank's debit names as its transfer, and book the lines of links "
        "tied by an own IBAN that hold only the model's proposal of the transfer (once, after "
        "the deploy of D162 amended 15 Nov 2026 and again after D162 amended (6))",
    )
    relinked.add_argument("--workspace", type=UUID, required=True, help="the workspace id")
    relinked.add_argument("--dry-run", action="store_true", help="count only, change nothing")
    relinked.set_defaults(command=relink_transfers, command_name="relink-transfers")

    returned = commands.add_parser(
        "pair-returns",
        help="count the returned payments (a recalled transfer credited back) the private "
        "accounts' imported lines hold, as Zu prüfen shows them; never books (once, after the "
        "deploy of D162 amended (7))",
    )
    returned.add_argument("--workspace", type=UUID, required=True, help="the workspace id")
    returned.add_argument(
        "--apply",
        action="store_true",
        help="accepted, changes nothing: the cards are computed when Zu prüfen is read",
    )
    returned.set_defaults(command=pair_returns, command_name="pair-returns")

    report_prices = commands.add_parser(
        "store-report-prices",
        help="store the price of every depot report position that agrees with the booked "
        "trades now and has none yet (once, after the deploy of D167 amended (c))",
    )
    report_prices.add_argument("--workspace", type=UUID, required=True, help="the workspace id")
    report_prices.add_argument("--dry-run", action="store_true", help="count only, change nothing")
    report_prices.set_defaults(command=store_report_prices, command_name="store-report-prices")

    trades = commands.add_parser(
        "group-exchange-trades",
        help="tie each exchange trade imported before migration 0043 (its acquisition, "
        "disposal and fee movements) as one movement group, so the lots read its given leg "
        "and its fee (once, after the deploy of D178 amended (3))",
    )
    trades.add_argument("--workspace", type=UUID, required=True, help="the workspace id")
    trades.add_argument("--dry-run", action="store_true", help="count only, change nothing")
    trades.set_defaults(command=group_exchange_trades, command_name="group-exchange-trades")

    follow = commands.add_parser(
        "follow-accounts",
        help="import every statement waiting under Dokumente onto the account that already "
        "holds its IBAN or number, and rebuild the ready depot suggestions (pre-selected "
        "depot, Steuerjahr); once, after the deploy of 14 Nov 2026",
    )
    follow.add_argument("--workspace", type=UUID, required=True, help="the workspace id")
    follow.add_argument("--dry-run", action="store_true", help="count only, queue nothing")
    follow.set_defaults(command=follow_accounts, command_name="follow-accounts")

    resort = commands.add_parser(
        "household-resort",
        help="re-sort the private lines booked in a group's fallback („Sonstige …“), proposed "
        "or confirmed, into the group's subcategories (migrations 0032 and 0036); each finer "
        "answer goes back to Zu prüfen as a proposal",
    )
    resort.add_argument("--workspace", type=UUID, required=True, help="the workspace id")
    resort.add_argument(
        "--category",
        action="append",
        metavar="CODE",
        help="re-sort only the lines booked in this fallback code, repeatable (e.g. "
        "hh_investment after 0036); default: every group's fallback",
    )
    resort.add_argument("--dry-run", action="store_true", help="count only, queue nothing")
    resort.set_defaults(command=household_resort, command_name="household-resort")

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
