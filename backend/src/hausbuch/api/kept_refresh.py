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
  person's, or the warm step's or the check's internal one) is running, and starts its own only
  then, with no await in between; one internal computation at a time;
- a path whose answer is already kept and unexpired is skipped, so a run after a write that
  changed nothing a member reads, or the first sighting of a workspace, costs only lookups; an
  expired inbox answer (§7.5 (b)) is not kept, so it is recomputed by the same path;
- if the version (or the day) moves during a run, the run stops and the next quiet period
  starts a new one;
- a recorded path that answers 404 (its record was deleted) is forgotten; another failure
  is logged and the path kept.

No model call, no new table, no new container. With no store, or the store switched off, the
task does nothing. Every failure is logged (the route's template, never its path, which holds
ids) and never ends the task.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from starlette.concurrency import run_in_threadpool

from .kept_answers import KeptAnswers, Key, read_data_version

logger = logging.getLogger("hausbuch.api.kept_refresh")

POLL_S = 5.0  # how often the versions are read while anything is recorded
PAUSE_S = 0.05  # how often a waiting run looks again whether a request still runs
STOP_TIMEOUT_S = 5.0  # a route running in a thread finishes before the task can end


@dataclass
class Watch:
    """One workspace's data version as the task last saw it."""

    version: int
    since: datetime  # when this version was first seen
    done: int | None = None  # the version the last complete run refreshed


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
    ) -> None:
        self.kept = kept
        self.quiet = timedelta(seconds=quiet_s)
        self.poll_s = poll_s
        self.pause_s = pause_s
        self.watch: dict[UUID, Watch] = {}
        self._task: asyncio.Task | None = None

    # --- Lifespan ---------------------------------------------------------------------

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="kept_answers_refresh")
        logger.info("kept answers refresh started", extra={"fields": {"event": "refresh_started"}})

    async def stop(self, timeout: float = STOP_TIMEOUT_S) -> None:
        task, self._task = self._task, None
        if task is None:
            return
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
            await asyncio.sleep(self.poll_s)
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001 - the task outlives any one failure
                fields = {"event": "refresh_failed", "exc_type": type(exc).__name__}
                logger.warning("kept answers refresh failed", extra={"fields": fields})

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
            if watch is None or watch.version != version:
                done = None if watch is None else watch.done
                self.watch[workspace_id] = Watch(version, now, done)
                continue
            if watch.done == version or now - watch.since < self.quiet:
                continue
            run = await self.refresh(workspace_id, version)
            runs.append(run)
            if run.result == "done":
                watch.done = version
            # moved: the next pass sees the new version and its quiet period starts then
        return runs

    async def _wait_idle(self) -> None:
        while self.kept.busy:
            await asyncio.sleep(self.pause_s)

    async def refresh(self, workspace_id: UUID, version: int) -> RefreshRun:
        """Ask every recorded path of `workspace_id`'s members that is not kept, one at a
        time, as long as the version stays `version` and the day does not change."""
        kept = self.kept
        run = RefreshRun(workspace_id)
        stamp = await run_in_threadpool(kept.stamp, workspace_id)
        if stamp is None or stamp.version != version:
            run.result = "moved"
            return run
        for auth, paths in kept.recorded(workspace_id):
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
                    if not kept.busy:
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
