"""How many SQL statements the Übersicht's reads send (21 Nov 2026: the page sent about 1,550
per load on a 0.25 vCPU container). Each test counts the statements of one read on a small
and on a larger ledger and asserts that the count does not grow with the number of stored
month-ends, held assets or depots; the answers themselves are the other test files'."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

import pytest
from db.test_services_net_worth import NOW, _account, _asset, _held, _ledger
from sqlalchemy import event

from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.events import EventType, FundSourceNote, PricesFetched
from hausbuch.services import health, net_worth, reminders
from hausbuch.services.events import append_event
from hausbuch.services.prices import overview

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


def _failing_funds(run, ws, count: int, now: datetime) -> None:
    """`count` held funds no registry names and nothing prices, every one failed in each of
    the last eight daily runs (D174's card), one with a currency note; and one held fund
    that never failed (its card reads the reports)."""
    depot = _account(run, ws, "Depot", AccountKind.DEPOT)

    def held(ctx):
        ids = []
        for i in range(count + 1):
            asset = _asset(ctx, kind="etf", isin=f"IE00CNT{i:04d}9", name=f"Fonds {i}")
            _held(ctx, depot, asset, "3")
            ids.append(asset)
        return ids

    ids = run(ws, held, now=now)
    failing = ids[:count]

    def fetched(ctx):
        append_event(
            ctx,
            EventType.WORKSPACE_PRICES_FETCHED,
            ctx.workspace_id,
            PricesFetched(
                sources=["issuer_nav"],
                assets=len(ids),
                rows_new=0,
                assets_failed=len(failing),
                failed_asset_ids=failing,
                fund_sources=[
                    FundSourceNote(asset_id=failing[0], issuer="ishares", currency="JPY")
                ],
            ),
        )

    for day in range(8):
        run(ws, fetched, now=now - timedelta(days=7 - day, hours=1))


def test_the_price_cards_read_the_runs_and_prices_once_for_every_fund(
    run, read, app_engine, fresh_workspaces
):
    """price_inbox_items (Zu prüfen's price cards) sends the same number of statements for
    one failing fund as for six; so does reminders.facts, which reads no price at all."""
    now = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
    small, large = fresh_workspaces
    _failing_funds(run, small, 1, now)
    _failing_funds(run, large, 6, now)
    assert len(read(large, overview.price_inbox_items, now)) == 7
    counts = {
        ws: (
            _statements(app_engine, read, ws, overview.price_inbox_items, now),
            _statements(app_engine, read, ws, reminders.facts, now.date()),
        )
        for ws in (small, large)
    }
    assert counts[large] == counts[small]
