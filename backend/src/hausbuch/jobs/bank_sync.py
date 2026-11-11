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
3. finish_run: the state stored with its MAC, then nothing_new, or the ZIP imported → done.

Failures: bank.unreachable is retried by Procrastinate, at most MAX_UNREACHABLE_RETRIES
times (RetryLater), the row back to queued meanwhile, except while the job waits for an
approval, where it records `failed` at once; every other failure records `failed`
with its code and waits for a person. A 9xxx answer is bank.refused with its four digits on
the event. A WriteRefused (a bug: a write segment reached the wire guard) fails the run as
bank.failed and is re-raised so the job shows failed in Procrastinate too.

Logging: one line per run with the sync id, the outcome, codes and exception class names;
the camt formats the bank offers at INFO (they answer §16 Q-O34 on the first real run).
Never the PIN, the product id, the login, an IBAN, an amount or any text from the bank.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from hausbuch.db.session import workspace_transaction
from hausbuch.rules import errors as E
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.enums import ActorType
from hausbuch.services import bank_sync as service
from hausbuch.services.bank.fints import (
    Approval,
    BankError,
    ClientFactory,
    Confirmed,
    FintsSession,
    WriteRefused,
    make_client,
    open_session,
)
from hausbuch.services.bank_sync import Fetched, RunPlan, Superseded
from hausbuch.services.context import Actor, new_command
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
    session.close()
    return Fetched(
        streams=list(streams),
        balance=balance,
        state=session.state(),
        tan_mechanism=session.tan_mechanism,
        approved=run.approved,
    )


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
