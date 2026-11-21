"""How many SQL statements the Übersicht's reads send (21 Nov 2026: the page sent about 1,550
per load on a 0.25 vCPU container). Each test counts the statements of one read on a small
and on a larger ledger and asserts that the count does not grow with the number of stored
month-ends, held assets or depots; the answers themselves are the other test files'."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

import pytest
from db.test_services_net_worth import NOW, _ledger
from sqlalchemy import event

from hausbuch.services import health, net_worth

pytestmark = pytest.mark.db


@contextmanager
def _counted(engine) -> Iterator[list[int]]:
    seen = [0]

    def count(*args, **kwargs):
        seen[0] += 1

    event.listen(engine, "before_cursor_execute", count)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", count)


def _statements(app_engine, read, ws, query, *args) -> int:
    with _counted(app_engine) as seen:
        read(ws, query, *args)
    return seen[0]


def test_the_month_end_series_reads_every_stored_run_at_once(
    run, read, app_engine, fresh_workspaces
):
    """run_totals and net_worth_series (the Übersicht's Vermögen im Verlauf) send the same
    number of statements for one stored month-end as for four."""
    ws, _ = fresh_workspaces
    _ledger(run, ws)
    run(ws, net_worth.capture_month_end, date(2026, 7, 31), now=NOW)
    today = NOW.date()
    one = (
        _statements(app_engine, read, ws, health.run_totals),
        _statements(app_engine, read, ws, health.net_worth_series, today),
    )
    for day in (date(2026, 8, 31), date(2026, 9, 30), date(2026, 10, 31)):
        run(ws, net_worth.capture_month_end, day, now=NOW)
    assert len(read(ws, health.run_totals)) == 4
    four = (
        _statements(app_engine, read, ws, health.run_totals),
        _statements(app_engine, read, ws, health.net_worth_series, today),
    )
    assert four == one
