"""One warm of the kept answers right after the API starts (phase 10 §7.4, §7.5).

The kept answers live in the API process, so a new instance (a deploy, a restart, the platform
moving the container) starts with an empty store, and the first person to open Übersicht or Zu
prüfen waits for every answer to be computed, about 10 s on the live server. The morning's warm
step (jobs/kept_answers.run_warm, queued by the morning chain) fills the store once a day only.

This task runs that same warm once per start: `delay_s` (15 s) after the lifespan's start-up, so
uvicorn is serving and the worker has taken its first jobs, it asks the kept routes of every
enrolled member through `KeptAnswers.internal_get`, as the morning step does. A person comes
first: before each route it waits until no request (a person's or an internal one) runs and no
person's request has ended for `idle_s` (1 s), the rule api/kept_refresh.py follows, with no
await between that check and the request.

It runs once and ends. The lifespan starts it only when HAUSBUCH_KEPT_WARM_AT_START is on (the
default) and the store is on, and cancels it at shutdown before the worker stops and the engine
is disposed. A failure is logged by its class name only and never stops the app; one line is
logged when it is done (run_warm's `kept_answers_warmed`, with `trigger` = `start`).
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.engine import Engine

from .kept_answers import KeptAnswers
from .kept_refresh import IDLE_S, PAUSE_S, STOP_TIMEOUT_S

logger = logging.getLogger("hausbuch.api.kept_start_warm")

DELAY_S = 15.0  # after the start-up, before the first route is asked


@dataclass
class _WarmDeps:
    """What run_warm reads from its JobDeps: the engine (the enrolled members), the clock
    (today) and the store."""

    engine: Engine
    clock: Callable[[], datetime]
    kept_answers: Any


class StartWarm:
    def __init__(
        self,
        kept: KeptAnswers,
        engine: Engine,
        *,
        delay_s: float = DELAY_S,
        idle_s: float = IDLE_S,
        pause_s: float = PAUSE_S,
    ) -> None:
        self.kept = kept
        self.engine = engine
        self.delay_s = delay_s
        self.idle_s = idle_s
        self.pause_s = pause_s
        self.members: list | None = None  # None: every enrolled member (tests name theirs)
        self.result: Any = None  # run_warm's WarmRun once done
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="kept_answers_start_warm")

    async def stop(self, timeout: float = STOP_TIMEOUT_S) -> None:
        task, self._task = self._task, None
        if task is None or task.done():
            return
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=timeout)
        if not done:
            fields = {"event": "start_warm_stop_timeout", "timeout_s": timeout}
            logger.warning("the start warm did not stop in time", extra={"fields": fields})

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _idle(self) -> bool:
        kept = self.kept
        if kept.busy:
            return False
        ended = kept.person_ended_at
        return ended is None or time.monotonic() - ended >= self.idle_s

    async def wait_idle(self) -> None:
        """Return once no request runs and no person's request ended for `idle_s`; nothing is
        awaited after the last check, so the caller's request starts while that still holds."""
        while not self._idle():
            await asyncio.sleep(self.pause_s)

    async def _run(self) -> None:
        from hausbuch.jobs.kept_answers import run_warm  # jobs import the api package

        try:
            await asyncio.sleep(self.delay_s)
            deps = _WarmDeps(self.engine, self.kept.clock, self.kept)
            warm = run_warm(
                deps,  # type: ignore[arg-type]
                self.members,
                before=self.wait_idle,
                trigger="start",
            )
            self.result = await warm
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - never stops the app; class name only
            fields = {"event": "start_warm_failed", "exc_type": type(exc).__name__}
            logger.warning("the start warm failed", extra={"fields": fields})
