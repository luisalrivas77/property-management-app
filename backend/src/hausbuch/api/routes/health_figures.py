"""The household's key figures (docs/phases/phase-9.md §13; phase 9 step 1). Not the
container's health check, which is GET /healthz (api/health.py).

- GET /health?scenario=: the window, the key figures, the marks and the to-dos on read
  (today, Berlin time); `scenario` names a household scenario whose levers are used instead
  of the default one. 404 for an unknown scenario.
- GET /health/months?from=&to=: each calendar month on read (at most 24), with its closing
  state and its newest stored snapshot; 422 health.range_invalid.
- GET /health/net-worth-series: the stored month-end runs as a series (D241).
- POST /health/snapshots {month}: „Kennzahlen festhalten“ for a closed month (D242); 422
  health.month_not_closed or health.month_end_missing.
"""

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Query

from hausbuch.schemas.health import (
    Health,
    HealthMonthView,
    HealthSnapshotRequest,
    HealthSnapshotResult,
    NetWorthPoint,
)
from hausbuch.services import health as service
from hausbuch.services.documents import LOCAL_TIME_ZONE

from ..deps import Cmd, domain_router

router = domain_router()

Month = Annotated[str, Query(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]


def _today() -> date:
    return datetime.now(LOCAL_TIME_ZONE).date()


@router.get("/health")
def health_get(cmd: Cmd, scenario: UUID | None = None) -> Health:
    return cmd.read(service.health, _today(), scenario)


@router.get("/health/months")
def health_months(
    cmd: Cmd,
    first: Annotated[str, Query(alias="from", pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")],
    last: Annotated[str, Query(alias="to", pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")],
) -> list[HealthMonthView]:
    return cmd.read(service.health_months, first, last, _today())


@router.get("/health/net-worth-series")
def health_net_worth_series(cmd: Cmd) -> list[NetWorthPoint]:
    return cmd.read(service.net_worth_series, _today())


@router.post("/health/snapshots")
def health_snapshot(cmd: Cmd, body: HealthSnapshotRequest) -> HealthSnapshotResult:
    """200 with `stored` (a new row) or `unchanged` (the newest row of the month holds the
    same run, rule version and figures)."""
    return cmd.run(service.store_snapshot, body.month)
