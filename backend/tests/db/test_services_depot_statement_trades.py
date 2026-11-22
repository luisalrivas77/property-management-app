"""Phase 6 D292 as hausbuch_app under RLS: a depot's confirmed trade line on its cash statement
becomes the trade's movement (source `depot_statement`), once per line; a line without ISIN or
units is the card „Kauf ohne Stückzahl“; a later trade confirmation that agrees replaces the
statement's booking and one that differs is refused with the difference named; a quarterly
report never books a trade a statement line booked; `hausbuch book-statement-trades` is a dry
run unless --apply. Invented statements, ISINs, references and amounts only
(tests/test_depot_pdf.py)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.test_cli_evals_report import _owner_url
from db.test_services_depot_import import _depot
from db.test_services_depot_report_trades import (
    BUY_APRIL,
    CASH_ROW,
    _cards,
    _movement_records,
    _notes,
    _report_with,
    _skipping_trades,
)
from db.test_services_depot_securities import (
    ISIN_CASH,
    ISIN_WORLD,
    _apply,
    _events,
    _read,
    _rows,
    _trade,
)
from db.test_services_proposals import enqueued as enqueued  # noqa: F401 (a fixture)
from db.test_services_proposals import job as job  # noqa: F401 (a fixture)
from test_card_pdf import draw
from test_depot_pdf import ROWS, page
from test_services_card_import import _deps

from hausbuch import cli
from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.jobs.matching import run_match_transaction
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.enums import DocumentSubtype
from hausbuch.schemas.events import EventType
from hausbuch.services import depot, proposals
from hausbuch.services.statements import import_statement, list_transactions

pytestmark = pytest.mark.db

T0 = datetime(2026, 11, 13, 8, 0, tzinfo=UTC)
NOW = datetime(2026, 11, 15, 9, 0, tzinfo=UTC)

# A purchase whose detail lines carry the fund's name only: no ISIN, no units.
NO_UNITS = [
    "-50,00 EUR20.03.202618.03.2026Wertpapier Kauf, Ref.: 5500000020",
    "Beispiel Fonds ohne Angabe",
]


def _import(run, ws, blob_store, account_id, rows=ROWS, total="722,20", closing="822,20", n=0):
    return run(
        ws,
        import_statement,
        blob_store,
        account_id,
        draw([page(rows=rows, total=total, closing=closing)]),
        media_type="application/pdf",
        iban_key=TEST_IBAN_KEY,
        original_filename="kontoauszug.pdf",
        now=T0 + timedelta(minutes=n),
    )


def _match(read, ws, app_engine, tmp_path, account_id):
    """The match job for every line, as the import's queue would run it: each trade line is
    booked by its headline (matcher_depot) and confirmed by code."""
    deps = _deps(app_engine, tmp_path)
    for line in read(ws, list_transactions, account_id):
        run_match_transaction(deps, workspace_id=ws, transaction_id=line.id, attempt=0)


def _current(read, ws):
    return sorted(
        (r for r in _rows(read, ws, movement_table) if r.superseded_by is None),
        key=lambda r: (r.occurred_at, r.quantity),
    )


def _cash(read, ws, account_id):
    return {t.bank_reference: t.id for t in read(ws, list_transactions, account_id)}


def _isin(read, ws, asset_id):
    return next(a.isin for a in _rows(read, ws, asset_table) if a.id == asset_id)


def _statement_booked(run, read, ws, blob_store, app_engine, tmp_path, rows=ROWS, **totals):
    account = _depot(run, ws)
    imported = _import(run, ws, blob_store, account.id, rows=rows, **totals)
    _match(read, ws, app_engine, tmp_path, account.id)
    return account, imported


def test_a_purchase_and_a_sale_line_are_booked_from_the_statement_once(
    run, read, fresh_workspaces, blob_store, app_engine, tmp_path
):
    ws, _ = fresh_workspaces
    account, imported = _statement_booked(run, read, ws, blob_store, app_engine, tmp_path)

    sale, buy = _current(read, ws)  # the sale on 16 Mar, the purchase on 17 Mar
    cash = _cash(read, ws, account.id)
    assert (sale.kind, sale.source, sale.status) == ("disposal", "depot_statement", "confirmed")
    assert (sale.quantity, sale.eur_amount, sale.tax_withheld_eur) == (
        Decimal("-15.2500"),
        Decimal("1234.50"),
        Decimal("4.74"),
    )
    assert (sale.occurred_at.date().isoformat(), sale.transaction_id) == (
        "2026-03-16",
        cash["5500000001"],
    )
    assert _isin(read, ws, sale.asset_id) == ISIN_WORLD
    assert (buy.kind, buy.source, buy.quantity, buy.eur_amount) == (
        "acquisition",
        "depot_statement",
        Decimal("4.8000"),
        Decimal("-500.00"),
    )
    assert (buy.occurred_at.date().isoformat(), buy.transaction_id) == (
        "2026-03-17",
        cash["5500000002"],
    )
    assert _isin(read, ws, buy.asset_id) == ISIN_CASH
    assert {sale.document_id, buy.document_id} == {imported.document_id}
    assert {sale.external_ref, buy.external_ref} == {"5500000001", "5500000002"}
    assert len(_events(read, ws, EventType.MOVEMENT_CONFIRMED)) == 2

    # Running it again (the CLI, a second import of the same file) books nothing new.
    again = run(ws, depot.book_statement_trades)
    assert (again.booked, again.covered, again.unreadable) == (0, 0, 0)
    _import(run, ws, blob_store, account.id, n=1)
    assert len(_rows(read, ws, movement_table)) == 2
    assert _cards(read, ws, InboxItemKind.DEPOT_TRADE_WITHOUT_UNITS) == []


def test_a_line_without_units_is_a_card_until_a_confirmation_books_its_trade(
    run,
    read,
    fresh_workspaces,
    blob_store,
    app_engine,
    tmp_path,
    job,
    enqueued,  # noqa: F811
):
    ws, _ = fresh_workspaces
    account, _ = _statement_booked(
        run,
        read,
        ws,
        blob_store,
        app_engine,
        tmp_path,
        rows=[*ROWS, *NO_UNITS],
        total="672,20",
        closing="772,20",
    )
    assert len(_current(read, ws)) == 2  # the readable two; the third is never guessed
    (card,) = _cards(read, ws, InboxItemKind.DEPOT_TRADE_WITHOUT_UNITS)
    assert card.transaction_id == _cash(read, ws, account.id)["5500000020"]
    assert card.account_id == account.id
    assert "20.03.2026" in card.reasoning and "50,00" in card.reasoning
    assert "Umsätze aus dem Bericht buchen" in card.question
    counted = run(ws, depot.book_statement_trades)
    assert (counted.booked, counted.unreadable) == (0, 1)

    # Its trade confirmation books it, linked to the line by amount and day: the card goes.
    confirmation = _read(
        run,
        job,
        ws,
        blob_store,
        enqueued,
        DocumentSubtype.TRADE_CONFIRMATION,
        _trade(units="0.5000", net="50.00", reference="7700000001", on="2026-03-18"),
    )
    _apply(run, ws, confirmation)
    assert _cards(read, ws, InboxItemKind.DEPOT_TRADE_WITHOUT_UNITS) == []


def test_a_later_confirmation_that_agrees_replaces_the_statements_booking(
    run,
    read,
    fresh_workspaces,
    blob_store,
    app_engine,
    tmp_path,
    job,
    enqueued,  # noqa: F811
):
    ws, _ = fresh_workspaces
    account, _ = _statement_booked(run, read, ws, blob_store, app_engine, tmp_path)
    confirmation = _read(
        run, job, ws, blob_store, enqueued, DocumentSubtype.TRADE_CONFIRMATION, _trade()
    )
    assert any(n.startswith("Bereits aus dem Kontoauszug des Depots") for n in _notes(confirmation))

    _apply(run, ws, confirmation)

    rows = _rows(read, ws, movement_table)
    old = next(r for r in rows if r.source == "depot_statement" and r.quantity > 0)
    new = next(r for r in rows if r.source == "trade_confirmation")
    assert old.superseded_by == new.id
    cash = _cash(read, ws, account.id)
    assert new.transaction_id == cash["5500000002"]  # the line stays linked
    assert (new.quantity, new.eur_amount, new.document_id) == (
        Decimal("4.8000"),
        Decimal("-500.00"),
        confirmation.document_id,
    )
    assert sorted(r.source for r in _current(read, ws)) == ["depot_statement", "trade_confirmation"]
    (superseded,) = _events(read, ws, EventType.MOVEMENT_SUPERSEDED)
    assert superseded[1]["superseded_by"] == str(new.id)
    # The statement line has its movement: nothing is booked from it again.
    assert run(ws, depot.book_statement_trades).booked == 0


def test_a_later_confirmation_that_differs_changes_nothing_and_says_so(
    run,
    read,
    fresh_workspaces,
    blob_store,
    app_engine,
    tmp_path,
    job,
    enqueued,  # noqa: F811
):
    ws, _ = fresh_workspaces
    _statement_booked(run, read, ws, blob_store, app_engine, tmp_path)
    before = _rows(read, ws, movement_table)
    confirmation = _read(
        run,
        job,
        ws,
        blob_store,
        enqueued,
        DocumentSubtype.TRADE_CONFIRMATION,
        _trade(units="4.7000"),
    )
    (note,) = [n for n in _notes(confirmation) if "Kontoauszug des Depots" in n]
    assert "4,7 Anteile" in note and "4,8 Anteile" in note

    with pytest.raises(RuleError) as refused:
        _apply(run, ws, confirmation)
    assert refused.value.code == depot.DEPOT_STATEMENT_TRADE_DIFFERS
    assert _rows(read, ws, movement_table) == before


def test_a_confirmation_booked_before_the_statement_covers_its_line(
    run,
    read,
    fresh_workspaces,
    blob_store,
    app_engine,
    tmp_path,
    job,
    enqueued,  # noqa: F811
):
    ws, _ = fresh_workspaces
    account = _depot(run, ws)
    confirmation = _read(
        run, job, ws, blob_store, enqueued, DocumentSubtype.TRADE_CONFIRMATION, _trade()
    )
    _apply(run, ws, confirmation)
    _import(run, ws, blob_store, account.id)
    _match(read, ws, app_engine, tmp_path, account.id)

    rows = _current(read, ws)
    assert sorted(r.source for r in rows) == ["depot_statement", "trade_confirmation"]
    assert [r.quantity for r in rows if r.source == "trade_confirmation"] == [Decimal("4.8000")]
    assert [r.quantity for r in rows if r.source == "depot_statement"] == [Decimal("-15.2500")]


def test_a_report_never_books_a_trade_a_statement_line_booked(
    run,
    read,
    fresh_workspaces,
    blob_store,
    app_engine,
    tmp_path,
    job,
    enqueued,  # noqa: F811
):
    ws, _ = fresh_workspaces
    account, _ = _statement_booked(run, read, ws, blob_store, app_engine, tmp_path)
    # The report lists the March purchase under another reference than the statement's (so
    # only its fund, units and day tie it to the line) and an April purchase no line booked.
    march = f"{ISIN_CASH} | 13.03.2026 | 4,8000 | -500,00 | 7700000002"
    report = _read(
        run,
        job,
        ws,
        blob_store,
        enqueued,
        DocumentSubtype.DEPOT_REPORT,
        _report_with([CASH_ROW], [march, BUY_APRIL]),
    )
    assert [r.fields["external_ref"].value for r in _movement_records(report)] == ["5500000004"]
    assert any(n.startswith("1 weitere Umsätze des Berichts") for n in _notes(report))

    # Taken over without its trades, „Umsätze aus dem Bericht buchen“ books April only.
    run(ws, proposals.apply_proposal, report.id, _skipping_trades(report), iban_key=TEST_IBAN_KEY)
    plan = read(ws, depot.report_trades_plan, account.id)
    assert [t.trade.reference for t in plan.trades] == ["5500000004"]
    assert run(ws, depot.book_report_trades, account.id).booked == 1
    cash_fund = [r for r in _current(read, ws) if _isin(read, ws, r.asset_id) == ISIN_CASH]
    assert sorted(r.quantity for r in cash_fund) == [Decimal("2.4000"), Decimal("4.8000")]


def _cli(ws, dbname, *extra):
    argv = ["book-statement-trades", "--workspace", str(ws), *extra]
    return cli.main(argv, {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)})


def test_the_command_is_a_dry_run_unless_apply(
    run, read, fresh_workspaces, blob_store, app_engine, tmp_path, dbname, capsys, monkeypatch
):
    """Trade lines confirmed before D292 have no movement: the command books them, once."""
    ws, _ = fresh_workspaces
    account = _depot(run, ws)
    # The import and match hooks off, as for the lines stored before D292 was built.
    monkeypatch.setattr(
        depot, "book_statement_trades", lambda ctx, **_: depot.StatementTradesBooked()
    )
    _import(
        run, ws, blob_store, account.id, rows=[*ROWS, *NO_UNITS], total="672,20", closing="772,20"
    )
    _match(read, ws, app_engine, tmp_path, account.id)
    monkeypatch.undo()
    assert _rows(read, ws, movement_table) == []

    assert _cli(ws, dbname) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert out.startswith("would book 2 trade(s)")
    assert "1 line(s) without a readable ISIN or units" in out
    assert "dry run: nothing written" in out
    assert _rows(read, ws, movement_table) == []

    assert _cli(ws, dbname, "--apply") == cli.EXIT_OK
    assert capsys.readouterr().out.startswith("booked 2 trade(s)")
    assert sorted(r.source for r in _rows(read, ws, movement_table)) == ["depot_statement"] * 2

    assert _cli(ws, dbname, "--apply") == cli.EXIT_OK  # idempotent
    assert capsys.readouterr().out.startswith("booked 0 trade(s)")
    assert len(_rows(read, ws, movement_table)) == 2
