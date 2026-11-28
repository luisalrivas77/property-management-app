"""Kept answers computed after a change, not at the next load (phase 10 §7.5 (c)).

The store (api/kept_answers.py) records, per member, the paths that member's own requests found
kept or stored. This task, started and stopped with the API's lifespan, watches the data version
of every workspace with recorded paths. Once a workspace's version has moved and then stayed
unchanged for `quiet_s` (HAUSBUCH_KEPT_REFRESH_QUIET_S, 30 s), it asks each recorded path again
as its member through `KeptAnswers.internal_get`, one route at a time, so the next visit finds
the answers kept. The morning's bank, wallet and price runs move the version too, so their pages
are ready before the first visit as well.

Pacing (the API is one 0.25 vCPU process):
- a person's request comes first: before each route the task waits until no request (a
  person's, or the warm step's or the check's internal one) is running and no person's request
  has ended for `IDLE_S` (1 s), so a page's next request, sent just after the last one ended,
  does not find an internal computation started; it starts its own only then, with no await in
  between; one internal computation at a time;
- a path whose answer is already kept and unexpired is skipped, so a run after a write that
  changed nothing a member reads, or the first sighting of a workspace, costs only lookups; an
  expired inbox answer (§7.5 (b)) is not kept, so it is recomputed by the same path;
- if the version (or the day) moves during a run, the run stops and the next quiet period
  starts a new one;
- a recorded path that answers 404 (its record was deleted) is forgotten; another failure
  is logged and the path kept.

After a person's own write (§7.13 (e) 4) the middleware wakes the task at once and the quiet
is `quick_quiet_s` (QUICK_QUIET_S, 4 s) instead of 30 s, the writing member's recorded paths
asked first; the pacing above is unchanged, so a person's request still goes first. A write by
the worker (a bank sync, a document read) keeps the 30 s quiet.

Just after each midnight, Berlin's and UTC's (both days are in a kept answer's key, so every
kept answer turns stale then), the task runs the full warm (jobs/kept_answers.run_warm, every
enrolled member's routes in their last language) with the same pacing, `trigger` = `midnight`,
so the first load of the new day finds its answers kept (§7.13 (e) 4).

No model call, no new table, no new container. With no store, or the store switched off, the
task does nothing. Every failure is logged (the route's template, never its path, which holds
ids) and never ends the task.
"""

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from starlette.concurrency import run_in_threadpool

from .kept_answers import KeptAnswers, Key, read_data_version

logger = logging.getLogger("hausbuch.api.kept_refresh")

POLL_S = 5.0  # how often the versions are read while anything is recorded
PAUSE_S = 0.05  # how often a waiting run looks again whether a request still runs
IDLE_S = 1.0  # how long no person's request must have run before an internal one starts
QUICK_QUIET_S = 4.0  # the quiet after a person's own write (§7.13 (e) 4)
# How long stop() waits for the cancelled task. Cancelling ends the task at once; a route it
# was running in a worker thread runs on by itself and its answer is dropped.
STOP_TIMEOUT_S = 5.0


@dataclass
class Watch:
    """One workspace's data version as the task last saw it."""

    version: int
    since: datetime  # when this version was first seen
    done: int | None = None  # the version the last complete run refreshed
    writer: UUID | None = None  # the member whose own write this version follows (§7.13 (e) 4)


@dataclass
class RefreshRun:
    workspace_id: UUID
    result: str = "done"  # done, moved (the version or the day moved during the run)
    requested: int = 0
    stored: int = 0
    skipped: int = 0  # already kept
    failed: int = 0
    templates: list[str] = field(default_factory=list)  # in the order requested


class KeptRefresher:
    def __init__(
        self,
        kept: KeptAnswers,
        *,
        quiet_s: float,
        poll_s: float = POLL_S,
        pause_s: float = PAUSE_S,
        idle_s: float = IDLE_S,
        quick_quiet_s: float = QUICK_QUIET_S,
    ) -> None:
        self.kept = kept
        self.quiet = timedelta(seconds=quiet_s)
        self.quick_quiet = timedelta(seconds=min(quick_quiet_s, quiet_s))
        self.poll_s = poll_s
        self.pause_s = pause_s
        self.idle_s = idle_s
        self.watch: dict[UUID, Watch] = {}
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._day: tuple[date, date] | None = None  # (Berlin, UTC) as the loop last saw it

    # --- Lifespan ---------------------------------------------------------------------

    def start(self) -> None:
        self.kept.on_person_write = self._wake.set  # the middleware runs on the same loop
        self._task = asyncio.create_task(self._loop(), name="kept_answers_refresh")
        logger.info("kept answers refresh started", extra={"fields": {"event": "refresh_started"}})

    async def stop(self, timeout: float = STOP_TIMEOUT_S) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        self.kept.on_person_write = None
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=timeout)
        if not done:
            fields = {"event": "refresh_stop_timeout", "timeout_s": timeout}
            logger.warning("kept answers refresh did not stop in time", extra={"fields": fields})
            return
        logger.info("kept answers refresh stopped", extra={"fields": {"event": "refresh_stopped"}})

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _loop(self) -> None:
        while True:
            # every poll_s, or at once when a person's own write has ended
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_s)
            self._wake.clear()
            try:
                await self.day_turn()
                await self.tick()
            except Exception as exc:  # noqa: BLE001 - the task outlives any one failure
                fields = {"event": "refresh_failed", "exc_type": type(exc).__name__}
                logger.warning("kept answers refresh failed", extra={"fields": fields})

    # --- The midnight warm ------------------------------------------------------------

    async def day_turn(self) -> Any:
        """Run the full warm once the Berlin or the UTC day has changed since the last call
        (never on the first call); its WarmRun, or None. Afterwards every workspace's recorded
        paths are asked again by the next pass, the ones the warm kept skipped."""
        from hausbuch.services.documents import LOCAL_TIME_ZONE

        kept = self.kept
        now = kept.clock()
        day = (now.astimezone(LOCAL_TIME_ZONE).date(), now.astimezone(UTC).date())
        last, self._day = self._day, day
        if last is None or last == day:
            return None
        if not kept.enabled or kept.engine is None or kept.app is None:
            return None
        from hausbuch.jobs.kept_answers import run_warm  # jobs import the api package

        from .kept_start_warm import _WarmDeps

        deps = _WarmDeps(kept.engine, kept.clock, kept)
        run = await run_warm(deps, before=self._wait_idle, trigger="midnight")  # type: ignore[arg-type]
        for watch in self.watch.values():
            watch.done = None
        return run

    # --- One pass ---------------------------------------------------------------------

    async def tick(self) -> list[RefreshRun]:
        """Read the version of each workspace with recorded paths; refresh the ones whose
        version moved and has stayed unchanged for the quiet period since."""
        kept = self.kept
        if not kept.enabled or kept.engine is None or kept.app is None:
            return []
        workspaces = kept.recorded_workspaces()
        for workspace_id in set(self.watch) - workspaces:
            del self.watch[workspace_id]
        runs = []
        for workspace_id in sorted(workspaces):
            version = await run_in_threadpool(read_data_version, kept.engine, workspace_id)
            if version is None:
                continue
            now = kept.clock()
            watch = self.watch.get(workspace_id)
            writer = kept.take_person_write(workspace_id)  # ended since the last look
            if watch is None or watch.version != version:
                done = None if watch is None else watch.done
                # §7.13 (e) 4: a person's own write since the last look made this move
                moved_by = writer if watch is not None else None
                self.watch[workspace_id] = Watch(version, now, done, moved_by)
                continue
            quiet = self.quiet if watch.writer is None else self.quick_quiet
            if watch.done == version or now - watch.since < quiet:
                continue
            run = await self.refresh(workspace_id, version, first=watch.writer)
            runs.append(run)
            if run.result == "done":
                watch.done = version
            # moved: the next pass sees the new version and its quiet period starts then
        return runs

    def _idle(self) -> bool:
        """No request is running and no person's request has ended for `idle_s`."""
        kept = self.kept
        if kept.busy:
            return False
        ended = kept.person_ended_at
        return ended is None or time.monotonic() - ended >= self.idle_s

    async def _wait_idle(self) -> None:
        while not self._idle():
            await asyncio.sleep(self.pause_s)

    async def refresh(
        self, workspace_id: UUID, version: int, *, first: UUID | None = None
    ) -> RefreshRun:
        """Ask every recorded path of `workspace_id`'s members that is not kept, one at a
        time, as long as the version stays `version` and the day does not change; the member
        `first` (whose own write it follows) before the others."""
        kept = self.kept
        run = RefreshRun(workspace_id)
        stamp = await run_in_threadpool(kept.stamp, workspace_id)
        if stamp is None or stamp.version != version:
            run.result = "moved"
            return run
        members = kept.recorded(workspace_id)
        members.sort(key=lambda member: member[0].user_id != first)  # stable: recency kept
        for auth, paths in members:
            for path, query, seen in paths:
                key = Key(
                    path=path,
                    query=query,
                    workspace_id=workspace_id,
                    user_id=auth.user_id,
                    stamp=stamp,
                    locale=seen.locale,
                    app_version=kept.app_version,
                    rule_version=kept.rule_version,
                )
                if kept.is_kept(key):
                    run.skipped += 1
                    continue
                while True:  # idle, and still the same data, with no await before the request
                    await self._wait_idle()
                    if await run_in_threadpool(kept.stamp, workspace_id) != stamp:
                        run.result = "moved"
                        self._log(run)
                        return run
                    if self._idle():
                        break
                run.requested += 1
                run.templates.append(seen.template)
                try:
                    response = await kept.internal_get(auth, path, list(query), locale=seen.locale)
                except Exception as exc:  # noqa: BLE001 - one route never ends the run
                    run.failed += 1
                    fields = {
                        "event": "kept_answer_refresh_failed",
                        "route": seen.template,
                        "exc_type": type(exc).__name__,
                    }
                    logger.warning("a kept answer was not refreshed", extra={"fields": fields})
                    continue
                if response.status != 200:
                    run.failed += 1
                    if response.status == 404:  # its record was deleted; a 5xx may pass
                        kept.forget(auth, path, query)
                    fields = {
                        "event": "kept_answer_refresh_failed",
                        "route": seen.template,
                        "status": response.status,
                    }
                    logger.warning("a kept answer was not refreshed", extra={"fields": fields})
                    continue
                if response.kept == "stored":
                    run.stored += 1
        self._log(run)
        return run

    def _log(self, run: RefreshRun) -> None:
        fields = {
            "event": "kept_answers_refreshed",
            "result": run.result,
            "requested": run.requested,
            "stored": run.stored,
            "skipped": run.skipped,
            "failed": run.failed,
        }
        logger.info("kept answers refreshed", extra={"fields": fields})
