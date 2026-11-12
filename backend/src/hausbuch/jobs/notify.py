"""The send_reminders periodic task: the daily reminder digest (docs/phases/phase-5.md D123,
§12.1's retry amendment).

At 06:00 UTC the worker runs, for every provisioned workspace whose `reminder_emails` switch
is on:

1. the reminders as the Übersicht computes them (`services.reminders.list_reminders` on
   today's day in Europe/Berlin, acknowledgements applied);
2. the recipients: the members who enrolled a passkey (`auth.invites.list_members`), each at
   the email stored on their `app_user` row; an invited member who never enrolled gets no
   mail;
3. the `notification` rows of the email channel, and `rules.reminders.due_notifications`,
   which says which item, lead and member is due and with which attempt (a `sent` row stops
   it; a failed one is retried the next day, up to three attempts in all);
4. one digest per member listing that member's items (`rules.reminder_digest`), sent through
   the mailer, then one `notification` row per item, lead, member and attempt: `sent`, or
   `failed` with the mailer's code. A member's rows are written in a transaction of their
   own right after the send, so one member's failure never undoes another's record;
5. after every member's send, one `workspace.notification_sent` event per item and lead that
   reached at least one member this run (§8), with the number of members it reached, never
   who, in one transaction of its own.

Inert while sending is not set up (no TEM key, project or sender address): one log line,
no error, nothing read.

**Two runs never send the same digest.** The send happens before its row is inserted, and
the `notification` table is append-only with no pending state, so the unique index alone
cannot stop a second run that read the rows before the first one wrote them. Two guards: the
Procrastinate task is registered with `lock=NOTIFY_LOCK`, so at most one send_reminders job
runs at a time (a second one waits until the first has finished); and each workspace is sent
under a transaction-scoped advisory lock (`pg_try_advisory_xact_lock` on
`reminders:<workspace id>`), taken on a holder connection before the rows are read and kept
until every member's rows are committed. A run that finds the lock taken skips the workspace
(logged as `busy`): the holder is sending exactly those mails. The one gap left: a mail TEM
accepted whose row could not be written (the database gone between the send and the insert)
is sent again the next day; a recorded send is never repeated.

Nothing personal is logged: one line per run with counts and codes; a workspace that fails
is logged by its exception class only. The addresses go only to the mailer.
"""

import logging
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, func, insert, select

from hausbuch.auth.invites import list_members
from hausbuch.auth.lookup import provisioned_workspace_ids
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import notification as notification_table
from hausbuch.rules.reminder_digest import render_digest
from hausbuch.rules.reminders import due_notifications
from hausbuch.schemas.enums import ActorType
from hausbuch.schemas.events import EventType, NotificationSent
from hausbuch.schemas.reminders import (
    NotificationChannel,
    NotificationDue,
    NotificationStatus,
    Reminder,
    SentNotification,
)
from hausbuch.services.context import Actor, new_command
from hausbuch.services.documents import LOCAL_TIME_ZONE
from hausbuch.services.events import append_event
from hausbuch.services.mail import Mailer, MailSendError, OutgoingMail, tem_client
from hausbuch.services.reminders import list_reminders
from hausbuch.services.workspace_settings import get_settings

from .document_read import JobDeps

NOTIFY_TASK = "send_reminders"
NOTIFY_CRON = "0 6 * * *"  # D123: 06:00, the worker's clock (UTC in the container)
NOTIFY_LOCK = "reminders:send"  # Procrastinate lock=: one running job at a time
CHANNEL = NotificationChannel.EMAIL
SYSTEM_ACTOR = Actor(ActorType.SYSTEM, NOTIFY_TASK)

logger = logging.getLogger("hausbuch.jobs.notify")


@dataclass
class NotifyResult:
    """What one run did: `status` is done or not_configured; the counts are per run."""

    status: str
    workspaces: int = 0  # with the switch on and the lock taken
    switched_off: int = 0
    busy: int = 0  # another run held the workspace's lock
    failed_workspaces: int = 0
    mails: int = 0  # digests the mailer accepted
    failed_mails: int = 0
    items: int = 0  # notification rows written
    codes: Counter = field(default_factory=Counter)


def lock_key(workspace_id: UUID) -> str:
    return f"reminders:{workspace_id}"


def app_link(origin: str) -> str:
    """The Übersicht (the web app's root) at the origin the browser uses (HAUSBUCH_ORIGIN)."""
    return origin.rstrip("/") + "/"


def local_today(now: datetime) -> date:
    """The day the Übersicht computes reminders for (routes/reminders.py)."""
    return now.astimezone(LOCAL_TIME_ZONE).date()


def _log(result: NotifyResult, **extra: Any) -> None:
    fields = {
        "event": "reminders_sent",
        "status": result.status,
        "workspaces": result.workspaces,
        "switched_off": result.switched_off,
        "busy": result.busy,
        "failed_workspaces": result.failed_workspaces,
        "mails": result.mails,
        "failed_mails": result.failed_mails,
        "items": result.items,
        "codes": dict(sorted(result.codes.items())),
    } | extra
    level = (
        logging.INFO if not (result.failed_mails or result.failed_workspaces) else (logging.WARNING)
    )
    logger.log(level, "reminder digest run", extra={"fields": fields})


def run_send_reminders(
    deps: JobDeps,
    *,
    mailer: Mailer | None = None,
    workspace_ids: Iterable[UUID] | None = None,
) -> NotifyResult:
    """One daily run. `mailer` and `workspace_ids` are for tests; the task passes neither, so
    the mailer comes from the settings and every provisioned workspace is visited. Never
    raises for one workspace's failure (logged by class name; the next run retries it)."""
    if mailer is None:
        mailer = tem_client(deps.settings)
    if mailer is None:
        result = NotifyResult("not_configured")
        _log(result)
        return result
    now = deps.clock()
    today = local_today(now)
    link = app_link(deps.settings.origin)
    result = NotifyResult("done")
    ids = provisioned_workspace_ids(deps.engine) if workspace_ids is None else workspace_ids
    for ws in sorted(set(ids)):
        try:
            _workspace(deps, mailer, ws, today=today, now=now, link=link, result=result)
        except Exception as exc:  # noqa: BLE001 - class name only: a message can quote data
            result.failed_workspaces += 1
            fields = {"event": "reminders_workspace_failed", "exc_type": type(exc).__name__}
            logger.error("a workspace's reminders were not sent", extra={"fields": fields})
    _log(result)
    return result


def _workspace(
    deps: JobDeps,
    mailer: Mailer,
    ws: UUID,
    *,
    today: date,
    now: datetime,
    link: str,
    result: NotifyResult,
) -> None:
    with workspace_transaction(deps.engine, ws) as conn:
        if not get_settings(conn).reminder_emails:
            result.switched_off += 1
            return
    # The holder: its transaction keeps the workspace's lock until every row is committed.
    with deps.engine.begin() as holder:
        taken = holder.execute(
            select(func.pg_try_advisory_xact_lock(func.hashtextextended(lock_key(ws), 0)))
        ).scalar_one()
        if not taken:
            result.busy += 1
            return
        result.workspaces += 1
        # Read after the lock: a run that held it before has committed its rows.
        with workspace_transaction(deps.engine, ws) as conn:
            if not get_settings(conn).reminder_emails:  # switched off meanwhile
                return
            reminders = {r.id: r for r in list_reminders(conn, today)}
            members = {m.user.id: m.user.email for m in list_members(conn) if m.has_passkey}
            due = due_notifications(reminders.values(), _sent(conn), today, members)
        by_member: dict[UUID, list[NotificationDue]] = defaultdict(list)
        for item in due:
            by_member[item.recipient_user_id].append(item)
        reached: Counter[tuple[str, int]] = Counter()  # (reminder, lead) -> members sent to
        for user_id in sorted(by_member, key=str):
            items = by_member[user_id]
            if _send(deps, mailer, ws, members[user_id], items, reminders, now, link, result):
                reached.update({(i.reminder_id, i.lead_days) for i in items})
        if reached:
            _record_sent(deps, ws, reached, now)


def _sent(conn: Connection) -> list[SentNotification]:
    c = notification_table.c
    rows = conn.execute(
        select(c.reminder_id, c.lead_days, c.recipient_user_id, c.attempt, c.status).where(
            c.channel == CHANNEL.value
        )
    )
    return [SentNotification.model_validate(dict(r._mapping)) for r in rows]


def _send(
    deps: JobDeps,
    mailer: Mailer,
    ws: UUID,
    address: str,
    items: list[NotificationDue],
    reminders: dict[str, Reminder],
    now: datetime,
    link: str,
    result: NotifyResult,
) -> bool:
    """One member's digest, then its rows in their own transaction. True when the mailer
    accepted it, so every item's row is `sent`."""
    digest = render_digest(
        [reminders[rid] for rid in dict.fromkeys(i.reminder_id for i in items)], link
    )
    code: str | None = None
    try:
        mailer.send(
            OutgoingMail(to=address, subject=digest.subject, text=digest.text, html=digest.html)
        )
    except MailSendError as exc:
        code = exc.code
    except Exception as exc:  # noqa: BLE001 - counted as a failed attempt, class name only
        code = MailSendError(f"exc:{type(exc).__name__}").code
    if code is None:
        result.mails += 1
        status = NotificationStatus.SENT
    else:
        result.failed_mails += 1
        result.codes[code] += 1
        status = NotificationStatus.FAILED
    with workspace_transaction(deps.engine, ws) as conn:
        conn.execute(
            insert(notification_table),
            [
                {
                    "workspace_id": ws,
                    "reminder_id": i.reminder_id,
                    "lead_days": i.lead_days,
                    "channel": CHANNEL.value,
                    "recipient_user_id": i.recipient_user_id,
                    "sent_at": now,
                    "status": status.value,
                    "error_code": code,
                    "attempt": i.attempt,
                }
                for i in items
            ],
        )
    result.items += len(items)
    return status is NotificationStatus.SENT


def _record_sent(deps: JobDeps, ws: UUID, reached: Counter[tuple[str, int]], now: datetime) -> None:
    """workspace.notification_sent (§8): per item and lead, how many members this run's
    `sent` rows reached; never an address or a member id."""
    with workspace_transaction(deps.engine, ws) as conn:
        ctx = new_command(conn, ws, SYSTEM_ACTOR, now=now)
        for (reminder_id, lead_days), count in sorted(reached.items()):
            append_event(
                ctx,
                EventType.WORKSPACE_NOTIFICATION_SENT,
                ws,
                NotificationSent(
                    reminder_id=reminder_id,
                    lead_days=lead_days,
                    channel=CHANNEL,
                    recipient_count=count,
                ),
            )
