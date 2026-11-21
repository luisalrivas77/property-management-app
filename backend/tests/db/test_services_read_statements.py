"""How many SQL statements the Übersicht's reads send (21 Nov 2026: the page sent about 1,550
per load on a 0.25 vCPU container). The tests assert that the month-end series does not grow
with the number of stored month-ends, that the price cards do not grow with the number of
held funds, and that the net worth card reads each depot's movements once however many days
and checks read them (one query per depot remains, unchanged); the answers themselves are
the other test files'."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

import pytest
from db.test_services_net_worth import NOW, _account, _asset, _held, _ledger, _own, _price
from sqlalchemy import event, insert

from hausbuch.db.tables import holding_statement as holding_table
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.events import EventType, FundSourceNote, PricesFetched
from hausbuch.schemas.property import OwnerCreate
from hausbuch.services import health, net_worth, reminders
from hausbuch.services.events import append_event
from hausbuch.services.owners import create_owner
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


@contextmanager
def _texts(engine) -> Iterator[list[str]]:
    seen: list[str] = []

    def keep(conn, cursor, statement, *args, **kwargs):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", keep)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", keep)


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


def _depots(run, ws, count: int) -> None:
    """`count` depots, each holding one priced fund its 30 Sep report declares."""
    owner = run(ws, create_owner, OwnerCreate(full_name="Eigentümer A"), now=NOW).id
    for i in range(count):
        depot = _account(run, ws, f"Depot {i}", AccountKind.DEPOT)
        _own(run, ws, depot, {owner: Fraction(1)})

        def stored(ctx, depot=depot, i=i):
            fund = _asset(ctx, kind="etf", isin=f"IE00DPT{i:04d}9", name=f"Fonds {i}")
            _held(ctx, depot, fund, "3")
            _price(ctx, fund, date(2026, 9, 30), "100.00", "statement")
            ctx.conn.execute(
                insert(holding_table).values(
                    workspace_id=ctx.workspace_id,
                    account_id=depot,
                    asset_id=fund,
                    as_of=date(2026, 9, 30),
                    declared_units=Decimal("3"),
                    source="report",
                    created_at=ctx.now,
                )
            )

        run(ws, stored, now=NOW)


def _movement_reads(statements: list[str]) -> int:
    """The statements that read one depot's movements (services.depot._movements)."""
    return sum(
        1
        for s in statements
        if "FROM asset_movement" in s and "asset_movement.external_ref" in s and "sum(" not in s
    )


def test_the_net_worth_card_reads_each_depots_movements_once(
    run, read, app_engine, fresh_workspaces
):
    """net_worth.overview (the Übersicht's net worth card) computes two days when the stored
    month-end is the last one, today's and that month-end's, which are different figures and
    so two computations; each depot's movements, which the Anlagen overview's report guard
    and the report check both read on each day, are read once for the whole call."""
    ws, _ = fresh_workspaces
    _depots(run, ws, 3)
    run(ws, net_worth.capture_month_end, date(2026, 10, 31), now=NOW)
    today = date(2026, 11, 21)
    with _texts(app_engine) as seen:
        card = read(ws, net_worth.overview, today)
    assert card.month_end is not None and card.month_end.on == date(2026, 10, 31)
    assert _movement_reads(seen) == 3
    with _texts(app_engine) as seen:
        read(ws, net_worth.net_worth, today)
    assert _movement_reads(seen) == 3
