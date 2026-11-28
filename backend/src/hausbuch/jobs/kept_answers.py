"""The warm step and the daily check of the kept answers (phase 10 §7.4, api/kept_answers.py).

Warm, queued after the key figures by the morning chain (jobs.morning; 09:00 UTC the clock's
fallback): after the bank sync and the wallets at 06:30, the prices once the wallets are read,
the month-end and the key figures (scrub 03:15 and reminders 06:00 before), so the day's first
load of Übersicht and Zu prüfen finds its answers kept.
Once per enrolled member (a user with a passkey) of every provisioned workspace, the page's
kept routes are asked through the whole app (KeptAnswers.internal_get) in the language the
member last asked in. Nothing is warmed after a person's own change: the first load after it
computes. A write after 09:00 (a late bank sync, the plan's explanation the 08:30 step queued,
a document read) moves the version and the warmed answers are simply not served.

Check, daily at 15:00 UTC: one kept answer still current (its version and day unchanged) is
computed again with the store bypassed and compared byte for byte, the route rotating by day.
A difference empties the store and logs an error with the route's template only (no card in
the app: the store is the app's own optimisation, and emptied it is correct again).

Both are async tasks: the store and the app live on the API's event loop, where the
in-process worker runs. In a process without a store (deps.kept_answers None) they do nothing.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from hausbuch.api.auth_context import AuthContext
from hausbuch.api.kept_answers import KeptAnswers, warm_member
from hausbuch.auth.lookup import provisioned_workspace_ids
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import auth_passkey

from .document_read import JobDeps

WARM_TASK = "kept_answers_warm"
WARM_CRON = "0 9 * * *"  # after health_daily (08:30), the worker's clock (UTC)
WARM_LOCK = "kept_answers:warm"
CHECK_TASK = "kept_answers_check"
CHECK_CRON = "0 15 * * *"
CHECK_LOCK = "kept_answers:check"

logger = logging.getLogger("hausbuch.jobs.kept_answers")


@dataclass
class WarmRun:
    members: int = 0
    requests: int = 0
    failed: int = 0
    expected: int = 0  # refusals a page expects (kept_answers.EXPECTED_REFUSALS)


def enrolled_members(deps: JobDeps) -> list[AuthContext]:
    """One AuthContext per user with a passkey, per provisioned workspace. The session id is
    a fresh one: no kept route reads it."""
    members = []
    for ws in sorted(provisioned_workspace_ids(deps.engine)):
        with workspace_transaction(deps.engine, ws) as conn:
            rows = conn.execute(
                select(auth_passkey.c.user_id, auth_passkey.c.id)
                .distinct(auth_passkey.c.user_id)
                .order_by(auth_passkey.c.user_id, auth_passkey.c.created_at)
            ).all()
        members.extend(
            AuthContext(workspace_id=ws, user_id=row.user_id, session_id=uuid4(), passkey_id=row.id)
            for row in rows
        )
    return members


async def run_warm(
    deps: JobDeps,
    members: list[AuthContext] | None = None,
    *,
    before: Callable[[], Awaitable[None]] | None = None,
    trigger: str = "morning",
) -> WarmRun:
    """Warm every enrolled member's kept routes. `before`, when given, is awaited right
    before each route is asked (the start warm, api/kept_start_warm.py, waits there for a
    person's request to end); `trigger` names the caller in the log line."""
    kept: KeptAnswers | None = deps.kept_answers
    run = WarmRun()
    if kept is None or not kept.enabled or kept.app is None:
        return run
    if members is None:
        members = await run_in_threadpool(enrolled_members, deps)
    from hausbuch.services.documents import LOCAL_TIME_ZONE

    today = deps.clock().astimezone(LOCAL_TIME_ZONE).date()
    for auth in members:
        result = await warm_member(kept, auth, today, before=before)
        run.members += 1
        run.requests += result.requests
        run.failed += result.failed
        run.expected += result.expected
    fields = {
        "event": "kept_answers_warmed",
        "trigger": trigger,
        "members": run.members,
        "requests": run.requests,
        "failed": run.failed,
        "expected": run.expected,
        "kept": len(kept),
        "bytes": kept.size_bytes,
    }
    logger.info("kept answers warmed", extra={"fields": fields})
    return run


async def run_check(deps: JobDeps, *, now: datetime | None = None) -> str | None:
    """The day's check; its result (same, different, moved, failed) or None when nothing
    current was kept."""
    kept: KeptAnswers | None = deps.kept_answers
    if kept is None or not kept.enabled or kept.app is None:
        return None
    day = (now or deps.clock()).astimezone(UTC).date()
    outcome = await kept.check(day.toordinal())
    fields = {
        "event": "kept_answer_checked",
        "route": None if outcome is None else outcome.template,
        "result": None if outcome is None else outcome.result,
    }
    logger.info("kept answer checked", extra={"fields": fields})
    return None if outcome is None else outcome.result
