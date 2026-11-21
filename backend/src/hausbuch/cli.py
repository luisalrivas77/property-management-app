"""`hausbuch bootstrap | invite | openapi | evals | household-reask | rematch-bills |
rematch-waiting | household-resort | follow-accounts | repair-periods | relink-transfers |
pair-returns | store-report-prices | group-exchange-trades | repair-exchange-times` (phase 1 step
3, §5, §6.5, §6.7;
phase 2 D41, D27 amended 15 Nov 2026; phase 6 D159 amended 13 and 14 Nov 2026, D161 widened 14
Nov 2026, D160 amended (4) 14 Nov 2026, D165 (a) and D176 (3) amended 14 Nov 2026, D162 and
D167 (c) amended 15 Nov 2026, D162 amended (6) and (7) 15 Nov 2026, D178 amended (3) and (4) 16
Nov 2026).

Also `hausbuch relink-chain` (phase 7 D184, step 5), `hausbuch survey-solana` (phase 7,
17 Nov 2026), `hausbuch repair-solana-lamports` (phase 7 D186 (2) amended 18 Nov 2026),
`hausbuch health-check` (phase 9 D259 amended 17 Nov 2026), `hausbuch recapture-months`
(phase 6 D265 (5), 20 Nov 2026) and `hausbuch perf-overview` (21 Nov 2026), described below.

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
that fee as its fee_eur, as an import writes them now. A second run finds nothing. An exchange
account whose provider is neither BISON nor Binance is skipped and named by its label. --dry-run
rolls back and prints what it would group.

`repair-exchange-times` runs services.exchange.times.repair_times once for a workspace, with the
same owner URL (phase 6 D178 amended (4), 16 Nov 2026): every Binance import read as UTC whose
stored file name names the zone Binance wrote it in (`…UTC1_…zip`) has its rows moved to UTC,
each a new row that supersedes the stored one (status, kind, amounts and hash unchanged), its
trade groups, balance corrections, written deposits and transfer links following, then the
relinks an import runs. A second run finds nothing. An import whose name named no zone stays as
it is and keeps the exchange page's to-do. --dry-run rolls back and prints what it would move.

`relink-chain` runs services.chain.transfers.relink once for a workspace, with the same owner
URL (phase 7 step 5, D184): every open coin leg (an exchange's coin withdrawal or deposit, a
wallet's transfer leg) is linked to its counterpart where both are confirmed, or where they
share their transaction hash and code may confirm the proposed one (D186 (1)), as each wallet
sync and exchange import now does. A second run finds nothing. --dry-run rolls back and prints
what it would link and confirm.

`health-check` runs services.health.health_check once for a workspace, with the same owner URL,
in a transaction it rolls back (phase 9 D259 amended 17 Nov 2026, §16.1): each month from
--from to --to (default: the last month that has ended) with a stored key-figures snapshot is
recomputed from the stored rows, with the month-end run and the reserve levers the snapshot
names, and printed per figure as „gleich“ or the difference within the tolerances (amounts to
the euro, ratios to 0.1 percentage points, months to 0.1); each account line of the month-end
run is read again for its day and printed with its reason when it no longer agrees. It exits 0
when everything matches and 1 when anything differs or the months cannot be checked. The
figures are printed on the operator's terminal only; the repository records only the date, the
months and whether they matched (§15.3).

`recapture-months` runs services.health.recapture_months once for a workspace, with the same
owner URL (phase 6 D265 (5), after the deploy that counts the down payment before the
handover): each month from --from to --to that already holds a stored month-end run is captured
again through net_worth.capture_month_end (a new run only when the reading differs; the old run
stays), and where the month holds a stored key-figures snapshot a new one is stored beside it
through the path of „Kennzahlen festhalten“ (a new row only when the figures differ). It prints
per month „unverändert“, „neu festgehalten“ or the refusal with its codes, never an amount; a
month with no stored run is skipped and left to the daily task, and a month whose month-end has
a gap (D172's to-dos, a loan's missing payout among them) is skipped with the gaps' codes and
nothing stored (D265 amended). The key figures' lock is taken before the month-end's, as the
button and the daily task do. Insert-only; --dry-run rolls everything back.

`perf-overview` times every API read the Übersicht page makes (hausbuch.perf_probe), with the
same owner URL on a pooled engine whose every connection is read-only (a write fails with
SQLSTATE 25006 and its path is printed as WRITE-REFUSED). It drives the real app in process as
the workspace's member whose session was seen last, without its lifespan (no worker), and
prints per request the route (ids as p1, u1, o1), status, ms, SQL statements, ms in SQL,
commits/rollbacks and the body's size in bytes, never a response's content; then per pass the
total and the five slowest, and the number of requests the page fires. --repeat N passes
(default 2: the first warms the process, the rest are warm). --profile SUBSTRING (repeatable)
runs each request of the last pass whose route contains it under cProfile and prints the top
functions by cumulative and own time (module:function:line, calls, times, never a value).
--digest adds to the last pass the first 16 hex characters of the SHA-256 of each body (JSON
canonicalised: sorted keys, no spaces), to check a speed-up returns the same bodies.

`follow-accounts` queues one worker job (jobs.follow_accounts) once after the deploy of
14 Nov 2026: every statement waiting under Dokumente is imported onto the account that already
holds its IBAN or number, and every ready depot suggestion is rebuilt with the depot it prints
pre-selected and its Steuerjahr. The worker runs it because the stored files are read from the
object store, which this computer does not hold. --dry-run counts and queues nothing.

`repair-solana-lamports` queues one worker job (jobs.lamports_repair) once after the deploy of
18 Nov 2026 (phase 7 D186 (2) amended): every active Solana address's stored originals are read
again with the own token accounts they name, never the provider, and a transaction in which such
a token account received lamports the earlier reading missed (a stranger's address-poisoning
dust) gets its group superseded with the corrected SOL leg; a confirmed Kleinstbetrag (D262)
stays confirmed while it is still dust, anything else is proposed for a person. A second run
changes nothing. The worker runs it because the originals are in the object store. --dry-run
queues the same job in a mode that writes nothing; both print the number of Solana addresses
here and log their counts (never an address or a hash) in the worker.

`survey-solana` (phase 7, 17 Nov 2026, before liquidity pools are supported) reads one Solana
wallet's whole history from its RPC and reports what it holds and how the current classifier
and routing would treat it (services/chain/survey.py). It only reads: it books nothing and
writes nothing to the database. The address comes from a file (--address-file) and is never
printed, logged or written outside the cache under --out-dir, where every RPC answer is kept so
a second run resumes without asking again; report.md goes there too. The RPC address comes from
--rpc-url-env, else the one typed in the app for --workspace (read in a read-only transaction,
which needs HAUSBUCH_SESSION_KEY to decrypt), else HAUSBUCH_SOLANA_RPC_URL, else the public
endpoint; it is never printed. With --workspace the classifier also knows the workspace's own
addresses, decoder rows, held tokens and exchange withdrawals, as the first sync would.
"""

import argparse
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import Engine, create_engine, pool
from sqlalchemy.exc import DBAPIError, OperationalError, SQLAlchemyError

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


# --- health-check ---------------------------------------------------------------------


def health_check(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    """services.health.health_check once for a workspace (phase 9 D259 amended 17 Nov 2026):
    per stored month and figure „gleich“ or the difference, per input that no longer agrees
    its reason. Read-only: the transaction is rolled back. A refusal (no snapshot in the range,
    a month between two without one) is printed as an error."""
    from hausbuch.db.session import workspace_transaction
    from hausbuch.services.documents import LOCAL_TIME_ZONE
    from hausbuch.services.errors import RuleViolationError
    from hausbuch.services.health import health_check as check
    from hausbuch.services.net_worth import last_month_end

    today = datetime.now(LOCAL_TIME_ZONE).date()
    last = args.to or last_month_end(today).isoformat()[:7]
    engine = owner_engine(environ)
    try:
        try:
            with workspace_transaction(engine, args.workspace) as conn:
                result = check(conn, args.first, last, today)
                raise _DryRun  # read-only: roll back whatever the read touched
        except _DryRun:
            pass
        except RuleViolationError as exc:
            raise CliError(str(exc)) from None
    except SQLAlchemyError as exc:
        detail = type(getattr(exc, "orig", None) or exc).__name__
        raise CliError(
            f"could not reach the database ({detail}); check HAUSBUCH_ADMIN_DATABASE_URL and "
            "that the database accepts this computer's address"
        ) from None
    finally:
        engine.dispose()
    for row in result.rows:
        if row.equal:
            verdict = "gleich"
        elif row.stored is None or row.recomputed is None:
            verdict = f"gespeichert {row.stored or '–'}, neu berechnet {row.recomputed or '–'}"
        else:
            difference = Decimal(row.recomputed) - Decimal(row.stored)
            verdict = f"Differenz {difference:+} (gespeichert {row.stored})"
        print(f"{row.month} {row.figure}: {verdict}")
    for item in result.inputs:
        if not item.agrees:
            print(f"{item.month} Konto {item.account_id}: {item.reason}")
    months = f"{result.months[0]} bis {result.months[-1]}"
    if result.match:
        print(f"{months}: alles gleich")
        return EXIT_OK
    print(f"{months}: abweichend")
    return EXIT_ERROR


# --- perf-overview --------------------------------------------------------------------


def perf_overview(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    """hausbuch.perf_probe once for a workspace: every read of Übersicht timed, read-only."""
    from hausbuch.perf_probe import ProbeError, read_only_engine, run_probe

    if args.repeat < 1:
        raise CliError("--repeat must be at least 1")
    if args.profile and args.repeat < 2:
        raise CliError("--profile needs --repeat 2 or more: it profiles the warm pass")
    if args.check_kept and args.repeat < 2:
        raise CliError("--check-kept needs --repeat 2 or more: a later pass reads the kept")
    try:
        url = load_admin_database_url(environ)
    except SettingsError as exc:
        raise CliError(str(exc)) from None
    try:
        engine = read_only_engine(url)
    except Exception:  # as owner_engine: no driver message may reach stderr
        raise CliError("HAUSBUCH_ADMIN_DATABASE_URL is not a valid database URL") from None
    try:
        run_probe(
            engine,
            args.workspace,
            repeat=args.repeat,
            profile=args.profile,
            digest=args.digest,
            check_kept=args.check_kept,
        )
    except ProbeError as exc:
        raise CliError(str(exc)) from None
    except OperationalError as exc:
        detail = type(getattr(exc, "orig", None) or exc).__name__
        raise CliError(
            f"could not reach the database ({detail}); check HAUSBUCH_ADMIN_DATABASE_URL and "
            "that the database accepts this computer's address"
        ) from None
    finally:
        engine.dispose()
    return EXIT_OK


# --- recapture-months -----------------------------------------------------------------


_RUN_WORDS = {
    "written": "neu festgehalten",
    "recaptured": "neu festgehalten",
    "unchanged": "unverändert",
    "empty": "nichts zu bewerten, nicht festgehalten",
}
_SNAPSHOT_WORDS = {"stored": "neu festgehalten", "unchanged": "unverändert"}


def recapture_months(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    """services.health.recapture_months once for a workspace (phase 6 D265 (5)): per month
    the month-end's and the key figures' outcome, or the refusal's codes; never an amount.
    `--dry-run` rolls everything back."""
    from hausbuch.db.session import workspace_transaction
    from hausbuch.schemas.enums import ActorType
    from hausbuch.services.context import Actor, new_command
    from hausbuch.services.health import recapture_months as recapture

    if args.to < args.first:
        raise CliError("--to is before --from")
    engine = owner_engine(environ)
    try:
        try:
            with workspace_transaction(engine, args.workspace) as conn:
                actor = Actor(ActorType.SYSTEM, "cli:recapture-months")
                ctx = new_command(conn, args.workspace, actor, now=datetime.now(UTC))
                results = recapture(ctx, args.first, args.to)
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
    for result in results:
        if result.gaps:  # the codes only: a to-do's text can carry amounts
            gaps = ", ".join(result.gaps)
            print(f"{result.month}: übersprungen, Monatsabschluss mit Lücken ({gaps})")
            continue
        if result.run is None and not result.refused:
            print(f"{result.month}: kein gespeicherter Monatsabschluss, übersprungen")
            continue
        if result.run is None:
            print(f"{result.month}: Monatsabschluss abgelehnt ({', '.join(result.refused)})")
            continue
        parts = [f"Monatsabschluss {_RUN_WORDS[result.run.value]}"]
        if result.snapshot is not None:
            parts.append(f"Kennzahlen {_SNAPSHOT_WORDS[result.snapshot.value]}")
        elif result.refused:
            parts.append(f"Kennzahlen abgelehnt ({', '.join(result.refused)})")
        print(f"{result.month}: {'; '.join(parts)}")
    if args.dry_run:
        print("Probelauf: nichts gespeichert")
    return EXIT_OK


def _month(value: str) -> str:
    if len(value) != 7 or value[4] != "-" or not (value[:4] + value[5:]).isdigit():
        raise argparse.ArgumentTypeError("expected YYYY-MM")
    if not 1 <= int(value[5:]) <= 12:
        raise argparse.ArgumentTypeError("expected YYYY-MM")
    return value


# --- relink-chain ---------------------------------------------------------------------


def relink_chain(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    """services.chain.transfers.relink once for a workspace (phase 7 step 5, D184): the coin
    transfers between own accounts linked as an import or sync links them. `--dry-run` rolls
    everything back. Then (D181 (c) amended 16 Nov 2026) a sync is queued for each wallet on
    a balance-only chain with an exchange withdrawal to look up by its hash; the job reads
    the node, never this command."""
    from hausbuch.db.session import workspace_transaction
    from hausbuch.schemas.enums import ActorType
    from hausbuch.services.chain.sync import queue_deposit_lookups
    from hausbuch.services.chain.transfers import relink
    from hausbuch.services.context import Actor, new_command

    engine = owner_engine(environ)
    queued = 0
    try:
        try:
            with workspace_transaction(engine, args.workspace) as conn:
                actor = Actor(ActorType.SYSTEM, "cli:relink-chain")
                ctx = new_command(conn, args.workspace, actor)
                result = relink(ctx)
                if args.dry_run:
                    raise _DryRun
                # D181 (c) amended: a wallet on a balance-only chain looks its deposits up by
                # hash in its next sync, queued now (never under --dry-run)
                queued = queue_deposit_lookups(ctx)
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
    print(f"{verb} {result.linked} coin transfer(s)")
    verb = "would confirm" if args.dry_run else "confirmed"
    print(f"{verb} {result.confirmed} movement(s) by their transaction hash")
    if result.reopened:
        verb = "would reopen" if args.dry_run else "reopened"
        print(f"{verb} {result.reopened} movement(s) a link no longer current had confirmed")
    if result.skipped_locked:
        print(f"left {result.skipped_locked} for the next run (a leg was being answered)")
    if queued:
        print(f"queued {queued} wallet sync(s) to look up exchange withdrawals by their hash")
    return EXIT_OK


# --- survey-solana --------------------------------------------------------------------

DEFAULT_SURVEY_DIR = "~/hausbuch-samples/solana-survey"


def _survey_url(value: str, where: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise CliError(f"the Solana RPC address in {where} is not an http(s) URL")
    return value


def _hex_key(environ: Mapping[str, str], name: str) -> bytes | None:
    value = environ.get(name, "").strip()
    if not value:
        return None
    try:
        raw = bytes.fromhex(value)
    except ValueError:
        raise CliError(f"{name} is not a hex key") from None
    if len(raw) < 32:
        raise CliError(f"{name} is shorter than 32 bytes")
    return raw


def _survey_workspace(workspace: UUID, environ: Mapping[str, str], address: str, *, want_url: bool):
    """One read-only transaction in the workspace, rolled back: the Solana RPC address typed
    in the app (None: none typed, or not `want_url`, when --rpc-url-env names one) and the
    classifier's context as the address's first sync would build it (sync._context, with
    this address as one more own address)."""
    import dataclasses
    from uuid import uuid4

    from sqlalchemy import select, text

    from hausbuch.db.session import workspace_transaction
    from hausbuch.db.tables import asset as asset_table
    from hausbuch.schemas.app_secrets import AppSecretName, AppSecretSource
    from hausbuch.schemas.chain import Chain
    from hausbuch.services import app_secrets
    from hausbuch.services.chain.sync import _context, _Own
    from hausbuch.settings import Settings

    session_key = _hex_key(environ, "HAUSBUCH_SESSION_KEY")
    hmac_key = _hex_key(environ, "HAUSBUCH_IBAN_HMAC_KEY")
    settings = Settings(
        database_url="postgresql+psycopg://unused@localhost/unused",
        # placeholders only to build Settings; the session key's absence is checked below
        iban_hmac_key=hmac_key or bytes([1]) * 32,
        session_key=session_key or bytes([2]) * 32,
        rp_id="localhost",
        origin="https://localhost",
        blob_store="local",
        blob_root=tempfile.gettempdir(),
    )
    engine = owner_engine(environ)
    found = []
    try:
        try:
            with workspace_transaction(engine, workspace) as conn:
                conn.execute(text("SET TRANSACTION READ ONLY"))
                url: str | None = None
                typed = (
                    app_secrets.lookup(conn, settings, AppSecretName.SOLANA_RPC_URL)
                    if want_url
                    else None
                )
                if typed is not None and typed.source in (
                    AppSecretSource.APP,
                    AppSecretSource.UNREADABLE,
                ):
                    if session_key is None:
                        raise CliError(
                            "a Solana RPC address is typed in the app for this workspace: set "
                            "HAUSBUCH_SESSION_KEY to read it, or pass --rpc-url-env"
                        )
                    if typed.value is None:
                        raise CliError(
                            "the Solana RPC address typed in the app does not decrypt with "
                            "this HAUSBUCH_SESSION_KEY; pass --rpc-url-env instead"
                        )
                    url = typed.value.get_secret_value()
                native = conn.execute(
                    select(asset_table.c.id).where(
                        asset_table.c.chain == Chain.SOLANA.value,
                        asset_table.c.contract_address.is_(None),
                    )
                ).scalar_one_or_none()
                ctx = _context(
                    conn,
                    Chain.SOLANA,
                    address,
                    uuid4(),
                    {address: _Own(uuid4(), "")},
                    native or uuid4(),
                    None,
                    hmac_key,
                )
                ctx = dataclasses.replace(ctx, own_addresses=ctx.own_addresses | {address})
                found.append((url, ctx))
                raise _DryRun  # never commit, though nothing was written
        except _DryRun:
            pass
    except SQLAlchemyError as exc:
        detail = type(getattr(exc, "orig", None) or exc).__name__
        raise CliError(
            f"could not read the workspace ({detail}); check HAUSBUCH_ADMIN_DATABASE_URL and "
            "that the database accepts this computer's address"
        ) from None
    finally:
        engine.dispose()
    return found[0]


def survey_solana(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    """services.chain.survey.run_survey for one address read from a file (see the module
    docstring): the report to stdout and to report.md, progress counts to stderr."""
    from pydantic import SecretStr

    from hausbuch.rules.chain.address import validate
    from hausbuch.rules.chain.classify import ClassifyContext
    from hausbuch.rules.errors import RuleError
    from hausbuch.schemas.chain import Chain
    from hausbuch.services.chain import survey
    from hausbuch.services.chain.solana_rpc import SolanaRpc
    from hausbuch.settings import DEFAULT_SOLANA_RPC_RPS, SOLANA_PUBLIC_RPC_URL

    if args.rps <= 0:
        raise CliError("--rps is a positive number of requests per second")
    if args.max_tx is not None and args.max_tx < 1:
        raise CliError("--max-tx is a positive number of transactions")
    try:
        content = Path(args.address_file).expanduser().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CliError(f"cannot read the address file ({type(exc).__name__})") from None
    try:
        address = validate(Chain.SOLANA, content.strip())
    except RuleError:
        raise CliError("the address file does not hold one Solana address") from None

    ctx = ClassifyContext(chain=Chain.SOLANA, address=address, own_addresses=frozenset({address}))
    context = "the public registry and decoders, this address as the only own one"
    typed: str | None = None
    if args.workspace is not None:
        typed, ctx = _survey_workspace(
            args.workspace, environ, address, want_url=not args.rpc_url_env
        )
        context = "the workspace's own addresses, decoder rows, held tokens and withdrawals"
    if args.rpc_url_env:
        value = environ.get(args.rpc_url_env, "").strip()
        if not value:
            raise CliError(f"{args.rpc_url_env} is not set")
        url, source = _survey_url(value, args.rpc_url_env), f"the variable {args.rpc_url_env}"
    elif typed is not None:
        url, source = _survey_url(typed, "the app"), "the address typed in the app"
    elif environ.get("HAUSBUCH_SOLANA_RPC_URL", "").strip():
        value = environ["HAUSBUCH_SOLANA_RPC_URL"].strip()
        url, source = _survey_url(value, "HAUSBUCH_SOLANA_RPC_URL"), "HAUSBUCH_SOLANA_RPC_URL"
    else:
        url, source = SOLANA_PUBLIC_RPC_URL, "the public endpoint (api.mainnet-beta.solana.com)"
    try:
        sync_rps = int(environ.get("HAUSBUCH_SOLANA_RPC_RPS", "") or DEFAULT_SOLANA_RPC_RPS)
    except ValueError:
        raise CliError("HAUSBUCH_SOLANA_RPC_RPS is not a whole number") from None

    out_dir = Path(args.out_dir).expanduser()
    try:
        out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise CliError(f"cannot create the output directory ({type(exc).__name__})") from None
    transport = survey.CachedPost(out_dir / "cache", survey.default_post(), rps=args.rps)
    rpc = SolanaRpc(url=SecretStr(url), post=transport, sleep=lambda _seconds: None)
    print(f"reading through {source} at {args.rps:g} request(s) a second", file=sys.stderr)
    try:
        result = survey.run_survey(
            address,
            rpc=rpc,
            transport=transport,
            ctx=ctx,
            sync_budget=sync_rps * 600,
            max_tx=args.max_tx,
            rpc_source=source,
            context_label=context,
            progress=lambda line: print(line, file=sys.stderr),
        )
    except survey.SurveyError as exc:
        raise CliError(str(exc)) from None
    except Exception as exc:  # noqa: BLE001 - a message could carry an address or signature
        raise CliError(f"the survey failed ({type(exc).__name__}); its cache is kept") from None
    report = out_dir / "report.md"
    survey.write_report(report, result.report)
    print(result.report)
    print(f"report written to {report}", file=sys.stderr)
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
    for label in result.skipped:
        print(
            f"skipped the exchange account {label}: its provider is neither BISON nor Binance, "
            "so its trades were not grouped"
        )
    return EXIT_OK


# --- repair-exchange-times -----------------------------------------------------------


def repair_exchange_times(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    """services.exchange.times.repair_times once for a workspace (phase 6 D178 amended (4)):
    every Binance import read as UTC moved to UTC by the zone its stored file name names.
    `--dry-run` rolls everything back."""
    from hausbuch.db.session import workspace_transaction
    from hausbuch.schemas.enums import ActorType
    from hausbuch.services.context import Actor, new_command
    from hausbuch.services.exchange.times import repair_times

    engine = owner_engine(environ)
    try:
        try:
            with workspace_transaction(engine, args.workspace) as conn:
                actor = Actor(ActorType.SYSTEM, "cli:repair-exchange-times")
                result = repair_times(new_command(conn, args.workspace, actor))
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
    done = result.corrected
    verb = "would move" if args.dry_run else "moved"
    print(
        f"{verb} {done.movements} movement(s) of {done.imports} Binance import(s) to UTC "
        f"({result.accounts} Binance account(s))"
    )
    if done.unknown:
        print(
            f"left {done.unknown} import(s) as they are: their file name names no time zone, "
            "so the exchange page asks for the export again"
        )
    verb = "would then link" if args.dry_run else "then linked"
    print(f"{verb} {result.linked} transfer(s); reopened {result.reopened}, ended {result.ended}")
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


# --- repair-solana-lamports -----------------------------------------------------------


def repair_solana_lamports(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    """Queues one `repair_solana_lamports` job for the workspace (phase 7 D186 (2) amended
    18 Nov 2026); the worker runs it, since reading the stored originals needs the object
    store. `--dry-run` queues it in a mode that rolls back. Counts only, never an address."""
    from sqlalchemy import func, select

    from hausbuch.db.session import workspace_transaction
    from hausbuch.db.tables import wallet_address
    from hausbuch.jobs.enqueue import enqueue_repair_solana_lamports
    from hausbuch.schemas.chain import Chain

    engine = owner_engine(environ)
    try:
        with workspace_transaction(engine, args.workspace) as conn:
            addresses = conn.execute(
                select(func.count()).where(
                    wallet_address.c.chain == Chain.SOLANA.value,
                    wallet_address.c.active.is_(True),
                )
            ).scalar_one()
            enqueue_repair_solana_lamports(
                conn, workspace_id=args.workspace, dry_run=bool(args.dry_run)
            )
    except SQLAlchemyError as exc:
        detail = type(getattr(exc, "orig", None) or exc).__name__
        raise CliError(
            f"could not reach the database ({detail}); check HAUSBUCH_ADMIN_DATABASE_URL and "
            "that the database accepts this computer's address"
        ) from None
    finally:
        engine.dispose()
    print(f"{addresses} active Solana address(es) are read again from their stored originals")
    if args.dry_run:
        print(
            "dry run queued: the worker counts and writes nothing "
            "(see its log: solana lamports repaired, dry_run true)"
        )
    else:
        print("queued; the worker does it within a minute (see its log: solana lamports repaired)")
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

    checked = commands.add_parser(
        "health-check",
        help="recompute each stored month's key figures from the stored rows and print "
        "„gleich“ or the difference per figure (phase 9 D259 amended)",
    )
    checked.add_argument("--workspace", type=UUID, required=True, help="the workspace id")
    checked.add_argument(
        "--from", dest="first", type=_month, required=True, help="the first month, YYYY-MM"
    )
    checked.add_argument(
        "--to", type=_month, default=None, help="the last month, YYYY-MM (default: last ended)"
    )
    checked.set_defaults(command=health_check, command_name="health-check")

    perf = commands.add_parser(
        "perf-overview",
        help="time every API read the Übersicht page makes, read-only, and print per request "
        "its status, ms and SQL statements, never a response's content",
    )
    perf.add_argument("--workspace", type=UUID, required=True, help="the workspace id")
    perf.add_argument(
        "--repeat", type=int, default=2, help="passes over the page (default 2: first, warm)"
    )
    perf.add_argument(
        "--profile",
        action="append",
        default=[],
        metavar="PATH_SUBSTRING",
        help="profile the last pass's requests whose route contains this (repeatable), e.g. "
        "/api/health; prints code locations and times only",
    )
    perf.add_argument(
        "--digest",
        action="store_true",
        help="print on the last pass the first 16 hex characters of the SHA-256 of each "
        "response body (JSON with sorted keys), never the body",
    )
    perf.add_argument(
        "--check-kept",
        action="store_true",
        help="switch the kept answers on (phase 10 §7.4), mark the requests of the later "
        "passes served from them, then compare every kept answer with a recomputation and fail "
        "on any difference",
    )
    perf.set_defaults(command=perf_overview, command_name="perf-overview")

    recaptured = commands.add_parser(
        "recapture-months",
        help="capture each month's stored month-end again and, where the month holds a stored "
        "key-figures snapshot, store its key figures again beside it; prints „unverändert“, "
        "„neu festgehalten“ or the refusal per month, no amount (phase 6 D265 (5))",
    )
    recaptured.add_argument("--workspace", type=UUID, required=True, help="the workspace id")
    recaptured.add_argument(
        "--from", dest="first", type=_month, required=True, help="the first month, YYYY-MM"
    )
    recaptured.add_argument("--to", type=_month, required=True, help="the last month, YYYY-MM")
    recaptured.add_argument("--dry-run", action="store_true", help="roll everything back")
    recaptured.set_defaults(command=recapture_months, command_name="recapture-months")

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

    zones = commands.add_parser(
        "repair-exchange-times",
        help="move the times of every Binance export read as UTC to UTC by the time zone its "
        "file name names (once, after the deploy of D178 amended (4))",
    )
    zones.add_argument("--workspace", type=UUID, required=True, help="the workspace id")
    zones.add_argument("--dry-run", action="store_true", help="count only, change nothing")
    zones.set_defaults(command=repair_exchange_times, command_name="repair-exchange-times")

    chain_links = commands.add_parser(
        "relink-chain",
        help="link the coin transfers between own accounts (an exchange withdrawal to a "
        "wallet, wallet to wallet, a wrap or a bridge) once more, as each wallet sync and "
        "exchange import does (once, after the deploy of phase 7 step 5)",
    )
    chain_links.add_argument("--workspace", type=UUID, required=True, help="the workspace id")
    chain_links.add_argument("--dry-run", action="store_true", help="count only, change nothing")
    chain_links.set_defaults(command=relink_chain, command_name="relink-chain")

    surveyor = commands.add_parser(
        "survey-solana",
        help="read one Solana wallet's history and report what the current classifier would "
        "do with it; books nothing and writes nothing to the database",
    )
    surveyor.add_argument(
        "--address-file", required=True, help="a file holding the address (never printed)"
    )
    surveyor.add_argument(
        "--out-dir",
        default=DEFAULT_SURVEY_DIR,
        help=f"the cache and report.md, outside the repo (default {DEFAULT_SURVEY_DIR})",
    )
    surveyor.add_argument(
        "--max-tx", type=int, help="read at most this many transactions (an even sample)"
    )
    surveyor.add_argument(
        "--rps", type=float, default=2.0, help="requests a second sent to the RPC (default 2)"
    )
    surveyor.add_argument(
        "--rpc-url-env", help="the name of an environment variable holding the RPC address"
    )
    surveyor.add_argument(
        "--workspace",
        type=UUID,
        help="read the workspace's typed RPC address and classifier context, read-only",
    )
    surveyor.set_defaults(command=survey_solana, command_name="survey-solana")

    follow = commands.add_parser(
        "follow-accounts",
        help="import every statement waiting under Dokumente onto the account that already "
        "holds its IBAN or number, and rebuild the ready depot suggestions (pre-selected "
        "depot, Steuerjahr); once, after the deploy of 14 Nov 2026",
    )
    follow.add_argument("--workspace", type=UUID, required=True, help="the workspace id")
    follow.add_argument("--dry-run", action="store_true", help="count only, queue nothing")
    follow.set_defaults(command=follow_accounts, command_name="follow-accounts")

    lamports = commands.add_parser(
        "repair-solana-lamports",
        help="read every Solana address's stored originals again and correct a transaction "
        "whose lamports to an own token account were missed (a stranger's dust); once, after "
        "the deploy of 18 Nov 2026",
    )
    lamports.add_argument("--workspace", type=UUID, required=True, help="the workspace id")
    lamports.add_argument(
        "--dry-run", action="store_true", help="queue a run that counts and writes nothing"
    )
    lamports.set_defaults(command=repair_solana_lamports, command_name="repair-solana-lamports")

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
