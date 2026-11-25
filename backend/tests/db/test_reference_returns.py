"""The funds series' downloads on the database (phase 10 §7.11 (c) 3, point 8, D310 amended):
the newest download counts, a download is due only when none is stored or the newest lacks
last year for more than 30 days, the daily run asks the file once for every due workspace, a
failed file stores nothing, the refresh stores due or not, and each store moves the data
version (§7.4). The file is built in French's shape with INVENTED values
(tests/test_reference_returns_parse.py)."""

import dataclasses
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from db.test_cli_evals_report import _owner_url
from db.test_jobs_refresh_crypto import _deps
from test_reference_returns_parse import GOOD, MONTHLY, annual, zipped

from hausbuch import cli
from hausbuch.api.kept_answers import read_data_version
from hausbuch.jobs import fetch_reference_returns as job
from hausbuch.rules.goal.series import FUNDS_URL
from hausbuch.services import reference_returns as R
from hausbuch.services.prices import http
from hausbuch.services.prices.http import SourceError

pytestmark = pytest.mark.db

FILE = zipped([*MONTHLY, *annual(GOOD)])
LATER = [*GOOD, "1996   ,20.00    ,1.00   ,-1.00    ,3.00"]


class Fake:
    def __init__(self, answer: tuple[int, bytes] = (200, FILE)) -> None:
        self.answer = answer
        self.calls: list[str] = []

    def __call__(self, url, headers, timeout, max_bytes):
        self.calls.append(url)
        return self.answer


def _deps_at(app_engine, blob_store, now: datetime):
    return dataclasses.replace(_deps(app_engine, blob_store), clock=lambda: now)


def test_the_newest_download_counts_and_stays_in_its_workspace(run, read, fresh_workspaces):
    ws, other = fresh_workspaces
    assert read(ws, R.newest) is None
    first = R.parse(FILE)
    run(ws, lambda ctx: R.store(ctx.conn, ws, first, datetime(2026, 11, 1).date()))
    run(
        ws, lambda ctx: R.store(ctx.conn, ws, R.parse(zipped(annual(LATER))), datetime.now().date())
    )
    newest = read(ws, R.newest)
    assert sorted(newest.rows) == [1991, 1992, 1993, 1994, 1995, 1996]
    assert newest.rows[1996] == (Decimal("20.0000"), Decimal("3.0000"))
    assert read(other, R.newest) is None


def test_a_download_is_due_when_none_is_stored_or_the_newest_is_old_and_lacks_last_year(
    run, read, fresh_workspaces
):
    ws, _ = fresh_workspaces
    now = datetime.now(UTC)
    assert read(ws, R.due, now.date(), now)
    run(ws, lambda ctx: R.store(ctx.conn, ws, R.parse(FILE), now.date()))
    # The newest holds 1995: in 1996 it is not due, however old it is.
    assert not read(ws, R.due, datetime(1996, 3, 1).date(), now + timedelta(days=400))
    # In 2026 it lacks 2025: due only once more than 30 days old.
    assert not read(ws, R.due, now.date(), now + timedelta(days=29))
    assert read(ws, R.due, now.date(), now + timedelta(days=31))


def test_the_daily_run_asks_once_for_every_due_workspace(
    read, app_engine, blob_store, fresh_workspaces, caplog
):
    ws, other = fresh_workspaces
    now = datetime.now(UTC)
    before = read_data_version(app_engine, ws)
    fake = Fake()
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.fetch_reference_returns"):
        result = job.run_fetch_reference_returns(
            _deps_at(app_engine, blob_store, now), fetch=fake, workspace_ids=[ws, other]
        )
    assert (result.due, result.stored, result.failed_workspaces) == (2, 2, 0)
    assert fake.calls == [FUNDS_URL]
    assert sorted(read(ws, R.newest).rows) == sorted(read(other, R.newest).rows)
    assert read_data_version(app_engine, ws) != before
    assert "2 due, 2 stored, 0 failed" in caplog.text

    # Stored a moment ago: not due again, nothing asked.
    again = Fake()
    result = job.run_fetch_reference_returns(
        _deps_at(app_engine, blob_store, now), fetch=again, workspace_ids=[ws, other]
    )
    assert (result.due, result.stored, again.calls) == (0, 0, [])


def test_a_failed_file_stores_nothing_and_logs_its_class_and_status_only(
    read, app_engine, blob_store, fresh_workspaces, caplog
):
    ws, _ = fresh_workspaces
    deps = _deps_at(app_engine, blob_store, datetime.now(UTC))
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.fetch_reference_returns"):
        result = job.run_fetch_reference_returns(deps, fetch=Fake((503, b"")), workspace_ids=[ws])
        refused = job.run_fetch_reference_returns(
            deps, fetch=Fake((200, b"not a zip")), workspace_ids=[ws]
        )
    assert (result.error, result.status, result.stored) == ("HTTPError", 503, 0)
    assert (refused.error, refused.stored) == ("parse", 0)
    assert read(ws, R.newest) is None
    assert FUNDS_URL not in caplog.text


def test_the_refresh_stores_due_or_not_and_raises_when_the_file_fails(
    run, read, app_engine, blob_store, fresh_workspaces
):
    ws, _ = fresh_workspaces
    deps = _deps_at(app_engine, blob_store, datetime.now(UTC))
    run(ws, lambda ctx: R.store(ctx.conn, ws, R.parse(FILE), datetime.now().date()))
    job.run_refresh_reference_returns(
        deps, workspace_id=ws, fetch=Fake((200, zipped(annual(LATER))))
    )
    assert max(read(ws, R.newest).rows) == 1996
    with pytest.raises(SourceError):
        job.run_refresh_reference_returns(deps, workspace_id=ws, fetch=Fake((500, b"")))
    assert max(read(ws, R.newest).rows) == 1996  # the newest good download keeps counting


def test_the_command_stores_a_download_and_names_only_the_years(
    dbname, read, fresh_workspaces, monkeypatch, capsys
):
    ws, other = fresh_workspaces
    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)}
    argv = ["fetch-reference-returns", "--workspace", str(ws)]
    monkeypatch.setattr(http, "urllib_fetch", Fake((503, b"")))
    assert cli.main(argv, environ) != cli.EXIT_OK
    assert "HTTPError, HTTP 503" in capsys.readouterr().err
    assert read(ws, R.newest) is None

    monkeypatch.setattr(http, "urllib_fetch", Fake())
    assert cli.main(argv, environ) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "1991–1995 (5 years)" in out and "postgresql" not in out
    assert sorted(read(ws, R.newest).rows) == [1991, 1992, 1993, 1994, 1995]
    assert read(other, R.newest) is None
