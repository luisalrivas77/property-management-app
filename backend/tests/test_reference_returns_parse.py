"""services.reference_returns.parse and download (phase 10 §7.11 (c) 3, point 8, D310 amended).

The file is built here in French's shape (a monthly section first, then „Annual Factors:
January-December“), with INVENTED round values: never a published figure, never the real file.
"""

import io
import zipfile
from datetime import date
from decimal import Decimal

import procrastinate
import pytest
from procrastinate import testing

from hausbuch.jobs import fetch_reference_returns as job
from hausbuch.jobs import tasks
from hausbuch.rules.goal import series
from hausbuch.services import reference_returns as R
from hausbuch.services.prices.http import SourceError

MONTHLY = [
    "This file was created by an invented test.",
    "",
    "Missing data are indicated by -99.99.",
    "",
    ",Mkt-RF,SMB,HML,RF",
    "199101   ,1.00    ,0.00    ,0.00    ,0.10",
    "199102   ,2.00    ,0.00    ,0.00    ,0.10",
    "",
    "",
]


def annual(rows: list[str]) -> list[str]:
    return [" Annual Factors: January-December", ",Mkt-RF,SMB,HML,RF", *rows]


GOOD = [f"{1991 + i}   ,{10 + i}.00    ,1.00   ,-1.00    ,2.00" for i in range(5)]


def zipped(lines: list[str], name: str = "Developed_3_Factors.csv", extra: bool = False) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, "\r\n".join(lines) + "\r\n")
        if extra:
            archive.writestr("other.csv", "x")
    return buffer.getvalue()


def test_the_annual_section_is_read_as_published():
    rows = R.parse(zipped([*MONTHLY, *annual(GOOD), "", "Copyright 2026 Invented"]))
    assert sorted(rows) == [1991, 1992, 1993, 1994, 1995]
    assert rows[1991] == (Decimal("10.00"), Decimal("2.00"))
    assert rows[1995] == (Decimal("14.00"), Decimal("2.00"))


@pytest.mark.parametrize(
    "lines",
    [
        MONTHLY,  # no annual section
        [*MONTHLY, *annual([])],  # an empty one
        [*MONTHLY, " Annual Factors: July-June", ",Mkt-RF,SMB,HML,RF", *GOOD],
        [*MONTHLY, " Annual Factors: January-December", ",Mkt-RF,HML,RF", *GOOD],
        [*MONTHLY, *annual(GOOD), *annual(GOOD)],  # twice
        [*MONTHLY, *annual(GOOD[1:])],  # not from 1991
        [*MONTHLY, *annual([GOOD[0], GOOD[2]])],  # a year missing
        [*MONTHLY, *annual([GOOD[0], "1992   ,-99.99    ,1.00   ,-1.00    ,2.00"])],
        [*MONTHLY, *annual([GOOD[0], "1992   ,12.00    ,1.00   ,-1.00    ,-999"])],
        [*MONTHLY, *annual([GOOD[0], "1992   ,abc    ,1.00   ,-1.00    ,2.00"])],
        [*MONTHLY, *annual([GOOD[0], "1992   ,12.00    ,1.00   ,-1.00"])],
        [*MONTHLY, *annual([GOOD[0], "1992   ,-110.00    ,1.00   ,-1.00    ,2.00"])],
        [*MONTHLY, *annual([GOOD[0], "1992   ,12.00    ,1.00   ,-1.00    ,200.00"])],
        [*MONTHLY, *annual([GOOD[0], "1992   ,12.000001    ,1.00   ,-1.00    ,2.00"])],
        [*MONTHLY, *annual([*GOOD[:2], "199x   ,12.00    ,1.00   ,-1.00    ,2.00", *GOOD[2:]])],
        [*MONTHLY, *annual([*GOOD[:2], "", *GOOD[2:]])],  # a break, then more years
        [*MONTHLY, *annual([*GOOD[:2], "Note", *GOOD[2:]])],
        [*MONTHLY, *annual(GOOD), "1996" + "x" * 200_000],  # a field over csv's limit
        [*MONTHLY, " Annual Factors: January-December", ",Mkt-RF" + "x" * 200_000],
    ],
)
def test_an_unexpected_file_is_refused(lines):
    with pytest.raises(SourceError) as refused:
        R.parse(zipped(lines))
    assert refused.value.kind == "parse"


@pytest.mark.parametrize(
    "blob",
    [
        b"not a zip",
        zipped([*MONTHLY, *annual(GOOD)], extra=True),
        zipped([*MONTHLY, *annual(GOOD)], name="Developed_3_Factors.txt"),
    ],
)
def test_an_unexpected_archive_is_refused(blob):
    with pytest.raises(SourceError) as refused:
        R.parse(blob)
    assert refused.value.kind == "parse"


def test_a_member_above_the_cap_is_refused(monkeypatch):
    monkeypatch.setattr(R, "MAX_CSV_BYTES", 100)
    with pytest.raises(SourceError):
        R.parse(zipped([*MONTHLY, *annual(GOOD)]))


def test_download_asks_the_one_address_and_parses():
    asked = []

    def fetch(url, headers, timeout, max_bytes):
        asked.append((url, max_bytes))
        return 200, zipped([*MONTHLY, *annual(GOOD)])

    assert sorted(R.download(fetch)) == [1991, 1992, 1993, 1994, 1995]
    assert asked == [(series.FUNDS_URL, R.MAX_ZIP_BYTES)]


def test_a_failed_answer_is_a_source_error_with_its_status_only():
    with pytest.raises(SourceError) as failed:
        R.download(lambda url, headers, timeout, max_bytes: (503, b""))
    assert (failed.value.kind, failed.value.status) == ("HTTPError", 503)


def test_a_year_after_the_committed_rates_is_left_out_of_the_series():
    """French publishes a year before history.yaml has its year-end rate: that year has no
    euro return until the rates are written (scripts/goal_history.py), never a guess."""
    last = max(series.fx())
    rows = {year: (Decimal("10"), Decimal("2")) for year in (last - 1, last, last + 1)}
    returns = series.funds_series(rows, None).returns
    assert last in returns
    assert last + 1 not in returns


def test_both_tasks_are_registered_under_one_lock_without_retry():
    app = tasks.register_tasks(procrastinate.App(connector=testing.InMemoryConnector()))
    daily = app.tasks[job.FETCH_REFERENCE_TASK]
    refresh = app.tasks[job.REFRESH_REFERENCE_TASK]
    assert daily.lock == refresh.lock == job.REFERENCE_LOCK == "reference:fetch"
    assert daily.retry_strategy is None and refresh.retry_strategy is None
    periodic = [p for p in app.periodic_registry.periodic_tasks.values() if p.task is daily]
    assert [p.cron for p in periodic] == [job.FETCH_REFERENCE_CRON] == ["15 6 * * *"]
    assert not [p for p in app.periodic_registry.periodic_tasks.values() if p.task is refresh]


def test_a_year_not_over_yet_is_refused():
    lines = [*MONTHLY, *annual(GOOD)]  # 1991–1995
    assert sorted(R.parse(zipped(lines), date(1996, 1, 2))) == [1991, 1992, 1993, 1994, 1995]
    with pytest.raises(SourceError) as refused:
        R.parse(zipped(lines), date(1995, 12, 31))
    assert refused.value.kind == "parse"
