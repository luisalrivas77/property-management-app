"""Phase 6 D167 amended (c), 15 Nov 2026, as hausbuch_app under RLS: a quarterly report taken
over before one of its trade confirmations stores its price once the confirmation is applied
(or the report is read again, or `hausbuch store-report-prices` runs), and its fund gets no
„Kein Kurs“ card while the report disagrees. Invented ISINs, references and figures only."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from db.test_cli_evals_report import _owner_url
from db.test_services_depot_securities import (
    ISIN_CASH,
    ISIN_WORLD,
    _account,
    _apply,
    _classified,
    _events,
    _read,
    _report,
    _rows,
    _trade,
)
from db.test_services_proposals import _read_ready, _v
from db.test_services_proposals import enqueued as enqueued  # noqa: F401 (a fixture)
from db.test_services_proposals import job as job  # noqa: F401 (a fixture)
from sqlalchemy import select

from hausbuch import cli
from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import holding_statement as holding_table
from hausbuch.db.tables import price as price_table
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.enums import DocumentSubtype
from hausbuch.schemas.events import EventType
from hausbuch.services import depot, review

pytestmark = pytest.mark.db

# The report of 30 Jun lists 7.2 units of the money-market fund; the first trade (4.8 units)
# is booked before it, the second (2.4 units on 15 Apr) only after it.
LATE_ROW = f"{ISIN_CASH} | Beispiel Euro Geldmarkt | 7,2000 | 104,50 | 752,40 | EUR"
LATE_TRADE = _trade(units="2.4000", net="250.00", reference="5500000003", on="2026-04-15")


def _report_ahead_of_its_trade(run, job, ws, blob_store, enqueued, data=None):  # noqa: F811
    """A depot with the first trade booked and the report taken over before the second."""
    depot_id = _account(run, ws).id
    _apply(
        run,
        ws,
        _read(run, job, ws, blob_store, enqueued, DocumentSubtype.TRADE_CONFIRMATION, _trade()),
    )
    _, report, _ = _read_ready(
        run,
        job,
        ws,
        blob_store,
        enqueued,
        _classified(DocumentSubtype.DEPOT_REPORT),
        _report(LATE_ROW),
        data or b"%PDF-1.7 depot " + uuid.uuid4().hex.encode(),
    )
    _apply(run, ws, report)
    return depot_id, report


def _booked_without_the_hook(run, ws, depot_id):
    """The second trade confirmed as before this change (no price stored with it)."""

    def book(ctx):
        asset_id = ctx.conn.execute(
            select(asset_table.c.id).where(asset_table.c.isin == ISIN_CASH)
        ).scalar_one()
        ctx.conn.execute(
            movement_table.insert().values(
                workspace_id=ctx.workspace_id,
                account_id=depot_id,
                asset_id=asset_id,
                kind="acquisition",
                quantity=Decimal("2.4"),
                occurred_at=datetime(2026, 4, 15, tzinfo=UTC),
                source="user",
                status="confirmed",
                created_at=ctx.now,
            )
        )

    run(ws, book)


NOW = datetime(2026, 11, 15, 8, 0, tzinfo=UTC)


def _cards(read, ws, kind):
    """Zu prüfen with `now`, so the price cards are read too."""
    items = read(ws, review.inbox, today=NOW.date(), now=NOW)
    return [i for i in items if i.kind is kind]


def test_a_trade_applied_after_its_report_stores_the_reports_price_once(
    run,
    read,
    fresh_workspaces,
    blob_store,
    job,
    enqueued,  # noqa: F811
):
    ws, _ = fresh_workspaces
    depot_id, report = _report_ahead_of_its_trade(run, job, ws, blob_store, enqueued)

    # Taken over before its trade: no price, the holdings card, and no price card beside it.
    (holding,) = _rows(read, ws, holding_table)
    assert holding.reconciled is False
    assert _rows(read, ws, price_table) == []
    (card,) = _cards(read, ws, InboxItemKind.HOLDINGS_MISMATCH)
    assert card.question.endswith("Sobald der Bericht stimmt, liefert er auch die Kurse der Fonds.")
    assert _cards(read, ws, InboxItemKind.PRICE_NO_SOURCE) == []

    _apply(
        run,
        ws,
        _read(run, job, ws, blob_store, enqueued, DocumentSubtype.TRADE_CONFIRMATION, LATE_TRADE),
    )
    (price,) = _rows(read, ws, price_table)
    assert (price.on, price.source, price.document_id) == (
        date(2026, 6, 30),
        "statement",
        report.document_id,
    )
    assert price.price_eur == Decimal("104.50000000")
    ((_, recorded),) = _events(read, ws, EventType.ASSET_PRICE_RECORDED)
    assert recorded["document_id"] == str(report.document_id)
    assert _cards(read, ws, InboxItemKind.HOLDINGS_MISMATCH) == []
    assert _cards(read, ws, InboxItemKind.PRICE_NO_SOURCE) == []
    # The stored row keeps what the check said when the report came in.
    (holding,) = _rows(read, ws, holding_table)
    assert holding.reconciled is False
    (position,) = read(ws, depot.etf_overview).accounts[0].positions
    assert position.report_price_eur == Decimal("104.50")
    assert position.value_eur == Decimal("752.40")

    # Another movement on the depot later: the price is not stored twice.
    other = _trade(units="1.0000", net="100.00", reference="5500000004", on="2026-07-02")
    other["isin"] = _v(ISIN_WORLD)
    other["security_name"] = _v("Beispiel Welt ETF")
    _apply(
        run,
        ws,
        _read(run, job, ws, blob_store, enqueued, DocumentSubtype.TRADE_CONFIRMATION, other),
    )
    assert len(_rows(read, ws, price_table)) == 1
    assert run(ws, depot.store_report_prices, depot_id) == 0


def test_a_report_read_again_after_its_trade_stores_its_price(
    run,
    read,
    fresh_workspaces,
    blob_store,
    job,
    enqueued,  # noqa: F811
):
    """„Neu auslesen“ heals too: the same figures are the stored row (the twin), which stores
    the price it never stored once the position agrees."""
    ws, _ = fresh_workspaces
    data = b"%PDF-1.7 depot " + uuid.uuid4().hex.encode()
    depot_id, report = _report_ahead_of_its_trade(run, job, ws, blob_store, enqueued, data)
    _booked_without_the_hook(run, ws, depot_id)
    assert _rows(read, ws, price_table) == []

    _, again, _ = _read_ready(
        run,
        job,
        ws,
        blob_store,
        enqueued,
        _classified(DocumentSubtype.DEPOT_REPORT),
        _report(LATE_ROW),
        data,
    )
    assert again.document_id == report.document_id
    _apply(run, ws, again)
    assert len(_rows(read, ws, holding_table)) == 1  # the twin, not a new row
    (price,) = _rows(read, ws, price_table)
    assert price.document_id == report.document_id and price.price_eur == Decimal("104.5")


def test_store_report_prices_command_dry_run_then_run(
    dbname,
    run,
    read,
    fresh_workspaces,
    blob_store,
    job,
    enqueued,  # noqa: F811
    capsys,
):
    ws, _ = fresh_workspaces
    depot_id, _proposal = _report_ahead_of_its_trade(run, job, ws, blob_store, enqueued)
    _booked_without_the_hook(run, ws, depot_id)
    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)}
    argv = ["store-report-prices", "--workspace", str(ws)]

    assert cli.main([*argv, "--dry-run"], environ) == cli.EXIT_OK
    assert "would store 1 price(s)" in capsys.readouterr().out
    assert _rows(read, ws, price_table) == []

    assert cli.main(argv, environ) == cli.EXIT_OK
    out_text = capsys.readouterr().out
    assert "stored 1 price(s)" in out_text and "postgresql" not in out_text
    assert len(_rows(read, ws, price_table)) == 1
    assert cli.main(argv, environ) == cli.EXIT_OK  # a second run stores nothing more
    assert "stored 0 price(s)" in capsys.readouterr().out
    assert len(_rows(read, ws, price_table)) == 1
