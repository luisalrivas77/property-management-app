"""The funds series route 3 compares the needed return with (docs/phases/phase-10.md §7.11
(c) 3, „Route 3 as decided for the build“ point 8, D310 amended).

Kenneth French's annual Developed market factors may not be committed, so they are downloaded
into `reference_return` (migration 0057) per workspace, for own use only, and read back from
there. This module holds the four steps and nothing else:

- `download` asks for the file (the price helper: no redirect followed, a size cap, a
  SourceError with the exception class and the HTTP status only);
- `parse` reads the ZIP in memory and returns {year: (Mkt−RF, RF)} in percent, as published.
  It fails closed with SourceError("parse") on anything it does not expect: not one CSV
  member, no „Annual Factors: January-December“ section with the header `,Mkt-RF,SMB,HML,RF`,
  a year that does not follow the previous one, a first year other than 1991, the missing
  marker -99.99 or -999, a value outside the table's CHECKs or a total return of −100 % or
  less. Nothing of a refused file is stored, so the newest good download keeps counting;
- `store` inserts one download (all its years in one transaction, one `download_id`), also
  when it equals the previous one, so its `fetched_at` says when a download last succeeded;
- `newest` reads the newest download, `due` says whether the daily task should ask again:
  when none is stored, or when the newest lacks last year and is more than 30 days old.

A failed download writes nothing: the answer then says `series_missing` for a household that
never had one, and the daily task asks again the next day. No event is written: a download is
reference data, as a fetched price is; its insert moves the workspace's data version (§7.4), so
the goal's kept answer is computed again.

`queue_refresh` queues the job `refresh_reference_returns` for one workspace (POST
/household-goal/reference-returns/refresh), refused while one waits or runs.
"""

import csv
import io
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from sqlalchemy import Connection, func, insert, select, text

from hausbuch.db.tables import reference_return as T
from hausbuch.jobs.enqueue import _DEFER_APP
from hausbuch.rules.goal.series import FUNDS_LICENCE, FUNDS_SOURCE, FUNDS_URL
from hausbuch.services.context import CommandContext
from hausbuch.services.prices.http import Fetch, SourceError, get, urllib_fetch

SERIES = "french_developed"
CURRENCY = "USD"
SERIES_KEY = "Annual Factors: January-December, Mkt-RF + RF"
ATTRIBUTION = "Kenneth R. French Data Library, Tuck School of Business at Dartmouth"
MAX_ZIP_BYTES = 1024 * 1024  # the file is about 5 KB
MAX_CSV_BYTES = 2 * 1024 * 1024
FIRST_YEAR = 1991  # the Developed series starts in July 1990: its first full year
RETRY_AFTER = timedelta(days=30)

REFRESH_TASK = "refresh_reference_returns"
FETCH_TASK = "fetch_reference_returns"
LOCK = "reference:fetch"  # the daily run and a refresh never write at once

_ANNUAL = "Annual Factors: January-December"
_HEADER = ["", "Mkt-RF", "SMB", "HML", "RF"]
_MISSING = {Decimal("-99.99"), Decimal("-999")}


@dataclass(frozen=True)
class Download:
    rows: dict[int, tuple[Decimal, Decimal]]  # year → (Mkt−RF, RF), percent, USD
    read_on: date
    fetched_at: datetime


def _refuse() -> SourceError:
    return SourceError("parse")


def _value(cell: str) -> Decimal:
    try:
        value = Decimal(cell.strip())
    except InvalidOperation:
        raise _refuse() from None
    if not value.is_finite() or value in _MISSING or value.as_tuple().exponent < -4:
        raise _refuse()
    return value


def _csv_text(zip_bytes: bytes) -> str:
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
        members = [m for m in archive.infolist() if not m.is_dir()]
        if len(members) != 1 or not members[0].filename.lower().endswith(".csv"):
            raise _refuse()
        with archive.open(members[0]) as handle:
            raw = handle.read(MAX_CSV_BYTES + 1)
    except SourceError:
        raise
    except Exception:  # noqa: BLE001 - a broken archive is refused like any other file
        raise _refuse() from None
    if len(raw) > MAX_CSV_BYTES:
        raise _refuse()
    return raw.decode("latin-1")


def parse(zip_bytes: bytes) -> dict[int, tuple[Decimal, Decimal]]:
    """{year: (Mkt−RF, RF)} of the annual section, or SourceError("parse")."""
    lines = _csv_text(zip_bytes).splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == _ANNUAL]
    if len(starts) != 1 or starts[0] + 1 >= len(lines):
        raise _refuse()
    header = [cell.strip() for cell in next(csv.reader([lines[starts[0] + 1]]))]
    if header != _HEADER:
        raise _refuse()
    rows: dict[int, tuple[Decimal, Decimal]] = {}
    for line in lines[starts[0] + 2 :]:
        cells = [cell.strip() for cell in next(csv.reader([line]), [])]
        if not cells or not cells[0].isdigit() or len(cells[0]) != 4:
            break  # the section ends at a blank line or the copyright note
        if len(cells) != len(_HEADER):
            raise _refuse()
        year = int(cells[0])
        if year != (FIRST_YEAR if not rows else max(rows) + 1):
            raise _refuse()
        excess, risk_free = _value(cells[1]), _value(cells[4])
        if not (-200 < excess < 1000 and -50 < risk_free < 100) or excess + risk_free <= -100:
            raise _refuse()
        rows[year] = (excess, risk_free)
    if not rows:
        raise _refuse()
    return rows


def download(fetch: Fetch = urllib_fetch) -> dict[int, tuple[Decimal, Decimal]]:
    """The file, parsed; SourceError when it cannot be read or is refused."""
    return parse(get(fetch, FUNDS_URL, max_bytes=MAX_ZIP_BYTES))


def store(
    conn: Connection, workspace_id: UUID, rows: dict[int, tuple[Decimal, Decimal]], read_on: date
) -> UUID:
    """One download for the workspace of `conn`, every year as one row; its id."""
    download_id = uuid4()
    conn.execute(
        insert(T),
        [
            {
                "workspace_id": workspace_id,
                "download_id": download_id,
                "series": SERIES,
                "year": year,
                "market_excess_pct": excess,
                "risk_free_pct": risk_free,
                "currency": CURRENCY,
                "source": FUNDS_SOURCE,
                "url": FUNDS_URL,
                "series_key": SERIES_KEY,
                "read_on": read_on,
                "licence": FUNDS_LICENCE,
                "attribution": ATTRIBUTION,
            }
            for year, (excess, risk_free) in sorted(rows.items())
        ],
    )
    return download_id


def newest(conn: Connection) -> Download | None:
    """The newest download of the workspace of `conn`, or None."""
    head = conn.execute(
        select(T.c.download_id, T.c.read_on, T.c.fetched_at)
        .where(T.c.series == SERIES)
        .order_by(T.c.fetched_at.desc(), T.c.download_id.desc())
        .limit(1)
    ).one_or_none()
    if head is None:
        return None
    rows = conn.execute(
        select(T.c.year, T.c.market_excess_pct, T.c.risk_free_pct).where(
            T.c.download_id == head.download_id, T.c.series == SERIES
        )
    ).all()
    return Download(
        rows={r.year: (r.market_excess_pct, r.risk_free_pct) for r in rows},
        read_on=head.read_on,
        fetched_at=head.fetched_at,
    )


def due(conn: Connection, today: date, now: datetime) -> bool:
    """Whether the daily task asks again: no download yet, or the newest lacks last year and
    was fetched more than 30 days before `now`."""
    last = newest(conn)
    if last is None:
        return True
    return today.year - 1 not in last.rows and now - last.fetched_at > RETRY_AFTER


# --- POST /household-goal/reference-returns/refresh ------------------------------------------

_OPEN = text(
    "SELECT j.id FROM procrastinate_jobs j"
    " WHERE j.task_name = :task AND j.args->>'workspace_id' = :workspace"
    " AND j.status IN ('todo', 'doing') ORDER BY j.id DESC LIMIT 1"
)


def queue_refresh(ctx: CommandContext) -> tuple[int, bool]:
    """Queue one refresh for the household unless one waits or runs: (its job id, queued)."""
    key = func.hashtextextended(f"reference_refresh:{ctx.workspace_id}", 0)
    ctx.conn.execute(select(func.pg_advisory_xact_lock(key)))
    open_job = ctx.conn.execute(
        _OPEN, {"task": REFRESH_TASK, "workspace": str(ctx.workspace_id)}
    ).scalar_one_or_none()
    if open_job is not None:
        return open_job, False
    job_id = _DEFER_APP.configure_task(
        REFRESH_TASK,
        allow_unknown=True,
        lock=LOCK,
        connection=ctx.conn.connection.driver_connection,
    ).defer(workspace_id=str(ctx.workspace_id))
    return job_id, True
