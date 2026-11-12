"""The bank_sync job: one FinTS run of one account (docs/phases/phase-5.md §4.3, D115-D120).

Plain synchronous code over the engine (jobs/tasks.py wraps it), tested by calling
run_bank_sync with a fake client (tests/fints_fake.py). No transaction is open while the bank
is talked to; each write is its own short transaction in services.bank_sync, and each checks
the run's token, so a person's restart in between makes this job stop without writing:

1. begin_run: take the row (queued or needs_approval → running), verify the client state.
2. The network: open the dialog; a pushTAN demand → needs_approval, and then for a scheduled
   run the dialog ends at once (D120); for a manual run the approval is polled every
   POLL_INTERVAL_S until it arrives (running) or SYNC_APPROVAL_TIMEOUT_S passes
   (bank.approval_timeout, the dialog ended). Then the accounts (the one whose IBAN hash is
   the account's, else bank.account_not_found), the booked camt streams of the window, the
   HKSAL balance, HKEND, and the client state.
   With the connection's switch fetch_statements on (D154, step 2b), the bank's own PDF
   statements are fetched after the balance and before HKEND, in the same dialog: the camt
   streams and the balance are in hand by then, and nothing in the statement fetch writes
   the run's row (a refusal, an invalid file, a failure end the statement fetch only; a lock
   code fails the run as D151 says). Each file is stored in its own transaction.
3. finish_run: the state stored with its MAC, then nothing_new, or the ZIP imported → done.

Failures: bank.unreachable is retried by Procrastinate, at most MAX_UNREACHABLE_RETRIES
times (RetryLater), the row back to queued meanwhile, except while the job waits for an
approval, where it records `failed` at once; every other failure records `failed`
with its code and waits for a person. A 9xxx answer is bank.refused with the four digits of
the code that says why on the event (rules.deciding_code, D151; 3938 when python-fints
raised on that warning alone). A WriteRefused (a bug: a write segment reached the wire
guard) fails the run as bank.failed and is re-raised so the job shows failed in
Procrastinate too.

Logging: one line per run with the sync id, the outcome, codes and exception class names;
the camt formats the bank offers at INFO (they answer §16 Q-O34 on the first real run). The
facade adds one trace record per FinTS exchange, tagged with the sync id (D148).
Never the PIN, the product id, the login, an IBAN, an amount or any text from the bank.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from hausbuch.db.session import workspace_transaction
from hausbuch.rules import bank_statements as statement_rules
from hausbuch.rules import errors as E
from hausbuch.rules.bank_sync import LOCK_CODES, REFUSED_CODES_LOGGED, BankAnswer
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.bank_sync import StatementKey, StatementOutcome, StatementReading
from hausbuch.schemas.enums import ActorType
from hausbuch.services import bank_sync as service
from hausbuch.services.bank.fints import (
    Approval,
    BankError,
    ClientFactory,
    Confirmed,
    FintsSession,
    WriteRefused,
    lock_refusal,
    make_client,
    open_session,
)
from hausbuch.services.bank_sync import Fetched, RunPlan, Superseded
from hausbuch.services.context import Actor, new_command
from hausbuch.services.documents import LOCAL_TIME_ZONE
from hausbuch.services.iban import protect_iban

from .document_read import JobDeps, RetryLater
from .enqueue import BANK_SYNC_TASK

logger = logging.getLogger("hausbuch.jobs.bank_sync")

SYSTEM_ACTOR = Actor(ActorType.SYSTEM, BANK_SYNC_TASK)
POLL_INTERVAL_S = 3.0  # D118: one HKTAN process S status query every few seconds
UNREACHABLE_WAITS_S = (30, 120)  # §4.3: three attempts in all
MAX_UNREACHABLE_RETRIES = len(UNREACHABLE_WAITS_S)


@dataclass
class Network:
    """What the job uses to talk to the bank and to wait; tests pass a fake client factory,
    a sleep that returns at once and a clock they move."""

    client_factory: ClientFactory = make_client
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic


@dataclass
class _Run:
    plan: RunPlan
    approved: bool = False
    # Between needs_approval and approval_confirmed: a bank.unreachable there fails the run
    # instead of a retry (the table has no needs_approval → queued move, and a retry would
    # ask the phone a second time); a person restarts it.
    awaiting_approval: bool = False
    log: dict[str, Any] = field(default_factory=dict)


class _Stop(Exception):
    """The run ended before finish_run (scheduled approval, timeout, superseded); the row
    is already written."""

    def __init__(self, outcome: str) -> None:
        super().__init__(outcome)
        self.outcome = outcome


def _actor(created_by: UUID | None) -> Actor:
    return SYSTEM_ACTOR if created_by is None else Actor(ActorType.USER, f"user:{created_by}")


def run_bank_sync(
    deps: JobDeps,
    *,
    workspace_id: UUID,
    sync_id: UUID,
    attempt: int,
    network: Network | None = None,
) -> str:
    """One run; returns its outcome for the log and the tests: the row's final status,
    `needs_approval` (a scheduled run that met a demand, or a retried job of an approval
    row), `superseded`, or `skipped` (nothing to run). Raises RetryLater for a retry."""
    net = network or Network()
    log: dict[str, Any] = {"event": BANK_SYNC_TASK, "sync_id": str(sync_id), "attempt": attempt}

    def write(step: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        with workspace_transaction(deps.engine, workspace_id) as conn:
            ctx = new_command(conn, workspace_id, actor, now=deps.clock())
            return step(ctx, *args, **kwargs)

    actor = SYSTEM_ACTOR
    plan = write(service.begin_run, sync_id, attempt=attempt, settings=deps.settings)
    if plan is None:
        logger.info("bank sync skipped", extra={"fields": log | {"outcome": "skipped"}})
        return "skipped"
    actor = _actor(plan.created_by)
    run = _Run(plan, log=log)
    session: FintsSession | None = None
    try:
        session = open_session(
            bank_code=plan.bank_code,
            login_name=plan.login_name,
            pin=deps.settings.fints_pin or "",
            url=plan.fints_url,
            product_id=deps.settings.fints_product_id or "",
            client_data=plan.client_data,
            tan_mechanism=plan.tan_mechanism,
            client_factory=net.client_factory,
            log_fields={"sync_id": str(sync_id), "attempt": attempt},
        )
        fetched = _fetch(deps, session, run, write, net, workspace_id)
        outcome = write(
            service.finish_run, deps.blob_store, plan, fetched, settings=deps.settings
        ).value
    except _Stop as stop:
        outcome = stop.outcome
    except Superseded:
        outcome = "superseded"
    except BankError as exc:
        outcome = _bank_error(deps, run, write, exc, attempt)
    except WriteRefused:
        _fail_quietly(write, plan, E.BANK_FAILED, log)
        logger.error("bank sync write refused", extra={"fields": log | {"outcome": "failed"}})
        raise
    except Exception as exc:
        _fail_quietly(write, plan, E.BANK_FAILED, log)
        fields = log | {"outcome": "failed", "exc_type": type(exc).__name__}
        logger.error("bank sync failed", extra={"fields": fields})
        return "failed"
    finally:
        if session is not None:
            session.close()
    logger.info("bank sync done", extra={"fields": log | {"outcome": outcome}})
    return outcome


def _fetch(
    deps: JobDeps,
    session: FintsSession,
    run: _Run,
    write: Callable[..., Any],
    net: Network,
    workspace_id: UUID,
) -> Fetched:
    plan = run.plan
    approval = session.open()
    if approval is not None:
        _await(deps, session, run, approval, write, net, workspace_id)
    accounts = _value(deps, session, run, session.accounts(), write, net, workspace_id)
    account = next(
        (a for a in accounts if _iban_hmac(a.iban, deps.settings.iban_hmac_key) == plan.iban_hmac),
        None,
    )
    if account is None:
        raise BankError(E.BANK_ACCOUNT_NOT_FOUND)
    formats = session.camt_formats()
    logger.info("bank camt formats", extra={"fields": run.log | {"camt_formats": formats}})
    streams = _value(
        deps,
        session,
        run,
        session.transactions_xml(account, plan.requested_from, plan.requested_to),
        write,
        net,
        workspace_id,
    )
    balance = _value(deps, session, run, session.balance(account), write, net, workspace_id)
    if plan.fetch_statements:  # D154: after the camt part, in the same dialog
        _fetch_statements(deps, session, run, account, write, workspace_id)
    session.close()
    return Fetched(
        streams=list(streams),
        balance=balance,
        state=session.state(),
        tan_mechanism=session.tan_mechanism,
        approved=run.approved,
    )


# --- The bank's PDF statements (D154, step 2b) ----------------------------------------------

_SO = StatementOutcome
_REQUEST_CAP = "request_cap"  # MAX_STATEMENT_REQUESTS reached (a log value, never stored)


@dataclass
class _StatementWalk:
    """One run's statement fetch, for its one log line: what was asked (as "number/year"),
    how many HKEKP messages were sent and files stored, and why the fetch ended (with the
    answer code, the invalid file's reason or the failure's exception classes). Never the
    PDF, its base64, the Aufsetzpunkt or the Quittungscode."""

    asked: list[str] = field(default_factory=list)
    requests: int = 0
    stored: int = 0
    end: str = "done"
    code: str | None = None
    reason: str | None = None
    failure: str | None = None


class _Locked(Exception):
    """A lock code in a statement answer: the run fails as D151 says (D154 (4))."""

    def __init__(self, answers: tuple[BankAnswer, ...]) -> None:
        super().__init__("locked")
        self.answers = answers


def _fetch_statements(
    deps: JobDeps,
    session: FintsSession,
    run: _Run,
    account: Any,
    write: Callable[..., Any],
    workspace_id: UUID,
) -> None:
    """D154 (3)-(5): ask for the statements plan_probes names, one HKEKP per message, store
    each delivered file in its own transaction, and log one line. The camt part is complete
    when this starts; nothing here writes the run's row: a refusal, an invalid file, a
    failure or any exception ends the statement fetch only. Two things leave it: a lock code
    (raised as bank.refused, so the run fails as D151 says) and a WriteRefused (a bug), and a
    person's restart (Superseded), which finish_run would meet anyway."""
    walk = _StatementWalk()
    try:
        if session.statements_offered():
            _walk_statements(deps, session, run, account, write, workspace_id, walk)
        else:
            walk.end = "not_offered"
    except _Locked as locked:
        walk.end = "locked"
        _log_statements(run, walk)
        raise lock_refusal(locked.answers) from None
    except (WriteRefused, Superseded):
        walk.end = "refused_write" if walk.end == "done" else walk.end
        _log_statements(run, walk)
        raise
    except Exception as exc:  # never the run's failure: its class name only
        walk.end, walk.failure = "error", type(exc).__name__
    _log_statements(run, walk)


def _log_statements(run: _Run, walk: _StatementWalk) -> None:
    fields = run.log | {
        "statements_end": walk.end,
        "statements_asked": walk.asked,
        "statements_requests": walk.requests,
        "statements_stored": walk.stored,
    }
    extra = (("code", walk.code), ("reason", walk.reason), ("exc_chain", walk.failure))
    fields |= {f"statements_{name}": value for name, value in extra if value is not None}
    logger.info("bank statements", extra={"fields": fields})


def _walk_statements(
    deps: JobDeps,
    session: FintsSession,
    run: _Run,
    account: Any,
    write: Callable[..., Any],
    workspace_id: UUID,
    walk: _StatementWalk,
) -> None:
    plan = run.plan
    today = deps.clock().astimezone(LOCAL_TIME_ZONE).date()
    with workspace_transaction(deps.engine, workspace_id) as conn:
        stored = service.stored_statement_keys(conn, plan.account_id)
        first_year = service.statements_first_year(conn, plan.account_id, today)
    probes = statement_rules.plan_probes(stored, today, first_year)
    answered: list[tuple[StatementKey, bool]] = []
    reopened = False
    while (key := statement_rules.next_probe(probes, answered, walk.requests)) is not None:
        with workspace_transaction(deps.engine, workspace_id) as conn:
            if not service.still_owned(conn, plan):  # a person restarted the run
                walk.end = "superseded"
                raise Superseded
        walk.asked.append(str(key))
        reading = _one_statement(session, account, key, walk)
        if reading.outcome is _SO.STOPPED:
            walk.end = _REQUEST_CAP if reading.reason == _REQUEST_CAP else "stopped"
            walk.code = reading.code
            return
        delivered = reading.outcome is _SO.STATEMENT
        if delivered:
            if write(service.store_statement, deps.blob_store, plan, key, reading.pdf):
                walk.stored += 1
        elif reading.outcome is _SO.INVALID:
            walk.reason = reading.reason  # nothing stored; the year's walk ends
        answered.append((key, delivered))
        if not reading.dialog_ended:
            continue
        # D154 (4): the bank ended the dialog. Only a refusal may open one new dialog, once,
        # for the other year's request; a reopening that asks for a pushTAN stops the fetch.
        following = statement_rules.next_probe(probes, answered, walk.requests)
        other_year = following is not None and following.year != key.year
        if reading.outcome is not _SO.NOT_AVAILABLE or not other_year or reopened:
            walk.end, walk.code = "dialog_ended", statement_rules.DIALOG_ENDED
            return
        reopened = True
        opened = session.reopen()
        if any(a.code in LOCK_CODES for a in opened.answers):
            raise _Locked(opened.answers)
        if opened.failure is not None:
            walk.end, walk.failure = "reopen_failed", opened.failure
            return
    if walk.requests >= statement_rules.MAX_STATEMENT_REQUESTS:
        walk.end = _REQUEST_CAP  # the next run goes on from what was stored


def _one_statement(
    session: FintsSession, account: Any, key: StatementKey, walk: _StatementWalk
) -> StatementReading:
    """One statement: its first HKEKP and every 3040 continuation (at most 10 parts, and never
    past MAX_STATEMENT_REQUESTS), read by rules.bank_statements.read_answer. A failed exchange
    is a stop, unless the HKEKP's own refusal code is among its answers (python-fints raises
    on some general codes the bank may send with a refused number; the dialog stays open)."""
    earlier: list[bytes] = []
    touchdown: str | None = None
    while True:
        answer = session.statement_pdf(account, key.number, key.year, touchdown)
        walk.requests += 1
        reading = statement_rules.read_answer(
            answer.answers, answer.parts, answer.touchdown is not None, earlier
        )
        if reading.outcome is _SO.LOCKED:
            raise _Locked(answer.answers)
        if answer.failure is not None:
            refused = reading.outcome is _SO.NOT_AVAILABLE and reading.code is not None
            if not refused:  # a transport error, a pushTAN demand, an unreadable answer
                walk.failure = answer.failure
                return StatementReading(
                    outcome=_SO.STOPPED, code=reading.code, dialog_ended=reading.dialog_ended
                )
        if reading.outcome is not _SO.CONTINUE:
            return reading
        if walk.requests >= statement_rules.MAX_STATEMENT_REQUESTS:
            return StatementReading(outcome=_SO.STOPPED, reason=_REQUEST_CAP)
        earlier.extend(answer.parts)
        touchdown = answer.touchdown


def _iban_hmac(iban: str | None, key: bytes) -> bytes | None:
    if not iban:
        return None
    try:
        return protect_iban(iban, key).hmac
    except RuleError:  # an IBAN the bank lists that does not validate: not ours
        return None


def _value(
    deps: JobDeps,
    session: FintsSession,
    run: _Run,
    result: Any,
    write: Callable[..., Any],
    net: Network,
    workspace_id: UUID,
) -> Any:
    """A read's result; a read the bank wants approved first goes through the same wait."""
    if isinstance(result, Approval):
        return _await(deps, session, run, result, write, net, workspace_id)
    return result


def _await(
    deps: JobDeps,
    session: FintsSession,
    run: _Run,
    approval: Approval,
    write: Callable[..., Any],
    net: Network,
    workspace_id: UUID,
) -> Any:
    """D118: needs_approval, then poll until confirmed or the timeout; a scheduled run ends
    the dialog at once (D120). Between two polls the row is read: a person's restart ends
    this job's wait."""
    plan = run.plan
    if write(service.approval_needed, plan):
        session.close()
        raise _Stop("needs_approval")
    run.awaiting_approval = True
    deadline = net.monotonic() + deps.settings.sync_approval_timeout_s
    pending: Approval | Confirmed = approval
    while isinstance(pending, Approval):
        net.sleep(POLL_INTERVAL_S)
        if net.monotonic() >= deadline:
            session.close()
            write(service.approval_timed_out, plan)
            raise _Stop("failed")
        with workspace_transaction(deps.engine, workspace_id) as conn:
            owned = service.still_owned(conn, plan)
        if not owned:
            session.close()
            raise Superseded
        pending = session.poll(pending)
    write(service.approval_confirmed, plan)
    run.awaiting_approval = False
    run.approved = True
    return pending.value


def _bank_error(
    deps: JobDeps, run: _Run, write: Callable[..., Any], exc: BankError, attempt: int
) -> str:
    plan = run.plan
    fields = run.log | {"code": exc.code}
    if exc.response_code is not None:
        fields["response_code"] = exc.response_code
    if len(exc.response_codes) > 1:
        fields["response_codes"] = list(exc.response_codes[:REFUSED_CODES_LOGGED])
    if exc.answers:  # the bank's text, login and long numbers blanked (Luis, 11 Nov 2026),
        # as "9078 → HKVVB:4: text": level and segment included (D151)
        answers = exc.answers[:REFUSED_CODES_LOGGED]
        fields["bank_answers"] = [answer.describe() for answer in answers]
    if exc.exc_chain:  # no 9xxx code: what python-fints raised, class names only (D148)
        fields["exc_chain"] = exc.exc_chain
    try:
        retry = exc.code == E.BANK_UNREACHABLE and not run.awaiting_approval
        if retry and attempt < MAX_UNREACHABLE_RETRIES:
            write(service.retry_run, plan)
            wait_s = UNREACHABLE_WAITS_S[attempt]
            logger.warning("bank sync retried", extra={"fields": fields | {"retry_in_s": wait_s}})
            raise RetryLater(wait_s)
        write(service.fail_run, plan, exc.code, exc.response_code)
    except Superseded:
        return "superseded"
    logger.warning("bank sync failed", extra={"fields": fields | {"outcome": "failed"}})
    return "failed"


def _fail_quietly(write: Callable[..., Any], plan: RunPlan, code: str, log: dict) -> None:
    """Record failed after an unexpected error; a second failure here is only logged."""
    try:
        write(service.fail_run, plan, code)
    except Exception as exc:  # the row stays live; it turns stale and may be restarted
        fields = log | {"exc_type": type(exc).__name__}
        logger.error("bank sync not recorded", extra={"fields": fields})
