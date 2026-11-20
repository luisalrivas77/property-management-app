"""The household's net worth and the done criterion's auto-rate (docs/phases/phase-6.md D167
(4), (5), D172, §12, §13; phase 6 step 6).

- GET /net-worth?on=: NetWorth(on) on read (today, Berlin time, by default): by class, by
  owner, line by line with each figure's source, and the gaps as to-dos.
- GET /net-worth/overview: the Übersicht's card: today's figure, the latest stored month-end
  and the month-end still to store.
- POST /net-worth/snapshots: „Monatsabschluss festhalten“, the last month-end before today
  (Berlin time), or a past month-end named in the body (phase 9 D241); a new run only when
  its figures differ from the stored one.
- GET /net-worth/snapshots: every stored run, the newest month-end first.
- GET /net-worth/snapshots/{run_id}: one stored run as a NetWorth. 404 for an unknown run.
- GET /accounts/auto-rate?month=YYYY-MM: D172's measure. This module is included before
  routes/accounts.py, whose /accounts/{account_id} would otherwise take the path.

GET /valuations?on= (§12) is not built: GET /net-worth's lines carry every valuation.
"""

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Body, Query

from hausbuch.schemas.net_worth import (
    AutoRate,
    NetWorth,
    NetWorthOverview,
    NetWorthRun,
    SnapshotRequest,
    SnapshotResult,
)
from hausbuch.services import auto_rate, net_worth
from hausbuch.services.documents import LOCAL_TIME_ZONE
from hausbuch.services.todo_texts import english

from ..deps import Cmd, domain_router, found

router = domain_router()

Month = Annotated[str, Query(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]


def _today() -> date:
    return datetime.now(LOCAL_TIME_ZONE).date()


@router.get("/accounts/auto-rate")
def accounts_auto_rate(month: Month, cmd: Cmd) -> AutoRate:
    """Per private bank account and in total; the criterion leaves each account's first
    import out (D172)."""
    return cmd.read(auto_rate.auto_rate, month)


@router.get("/net-worth")
def net_worth_get(cmd: Cmd, on: date | None = None) -> NetWorth:
    return english(cmd.read(net_worth.net_worth, on or _today()))


@router.get("/net-worth/overview")
def net_worth_overview(cmd: Cmd) -> NetWorthOverview:
    return english(cmd.read(net_worth.overview, _today()))


@router.post("/net-worth/snapshots")
def net_worth_snapshot(
    cmd: Cmd, body: Annotated[SnapshotRequest | None, Body()] = None
) -> SnapshotResult:
    """200 when the month-end may be stored: the outcome says whether a run was written
    (written, recaptured) or not (unchanged, empty). A body's `as_of` stores a past month-end
    (phase 9 D241); 422 net_worth.not_month_end, net_worth.month_end_before_history or
    net_worth.month_end_not_closed when it cannot be stored."""
    return cmd.run(net_worth.capture_last_month_end, None if body is None else body.as_of)


@router.get("/net-worth/snapshots")
def net_worth_snapshots(cmd: Cmd) -> list[NetWorthRun]:
    return cmd.read(net_worth.list_runs)


@router.get("/net-worth/snapshots/{run_id}")
def net_worth_snapshot_get(run_id: UUID, cmd: Cmd) -> NetWorth:
    return english(found(cmd.read(net_worth.stored_run, run_id), "valuation_snapshot", run_id))
