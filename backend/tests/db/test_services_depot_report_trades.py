"""Phase 6 D165 amended, 15 Nov 2026, as hausbuch_app under RLS: a quarterly report books the
trades its Umsätze list names and no movement carries, from its suggestion or, for a report
already taken over, by „Umsätze aus dem Bericht buchen“; a later report repeating them and a
later trade confirmation with the same reference book nothing twice. Invented ISINs,
references and figures only."""

import threading
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
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
from db.test_services_proposals import _accept_all, _ai_call, _v
from db.test_services_proposals import enqueued as enqueued  # noqa: F401 (a fixture)
from db.test_services_proposals import job as job  # noqa: F401 (a fixture)
from test_card_pdf import draw
from test_depot_pdf import page

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import holding_statement as holding_table
from hausbuch.db.tables import price as price_table
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.enums import ActorType, DocumentSubtype, ProposalStatus
from hausbuch.schemas.events import EventType
from hausbuch.schemas.proposal import ProposalApplyBody, ProposalApplyRecord
from hausbuch.services import depot, proposals, review
from hausbuch.services.context import Actor, new_command
from hausbuch.services.statements import import_statement, list_transactions

pytestmark = pytest.mark.db

T0 = datetime(2026, 11, 13, 8, 0, tzinfo=UTC)
NOW = datetime(2026, 11, 15, 9, 0, tzinfo=UTC)

# The money-market fund: 4.8 units on 13 Mar (the cash statement's line 5500000002) and 2.4
# units on 15 Apr (no cash line), 7.2 units at 104.50 on 30 Jun.
BUY_MARCH = f"{ISIN_CASH} | 13.03.2026 | 4,8000 | -500,00 | 5500000002"
BUY_APRIL = f"{ISIN_CASH} | 15.04.2026 | 2,4000 | -250,00 | 5500000004"
CASH_ROW = f"{ISIN_CASH} | Beispiel Euro Geldmarkt | 7,2000 | 104,50 | 752,40 | EUR"


def _cash_statement(run, ws, blob_store, account_id):
    run(
        ws,
        import_statement,
        blob_store,
        account_id,
        draw([page()]),
        media_type="application/pdf",
        iban_key=TEST_IBAN_KEY,
        original_filename="kontoauszug.pdf",
        now=T0,
    )


def _report_with(rows, trades, on="2026-06-30"):
    return _report(*rows, on=on) | {"trades": _v(list(trades))}


def _movement_records(proposal):
    return [r for r in proposal.records if r.key.startswith("movement:")]


def _notes(proposal):
    return [n for r in proposal.records for n in r.notes]


def _cards(read, ws, kind):
    items = read(ws, review.inbox, today=NOW.date(), now=NOW)
    return [i for i in items if i.kind is kind]


def _skipping_trades(proposal):
    """„Übernehmen“ with every trade of the report left out: a report taken over before this
    change, whose trades were never booked."""
    skipped = {r.key for r in _movement_records(proposal)}
    return ProposalApplyBody(
        records=[
            ProposalApplyRecord(key=r.key, skip=True) if r.key in skipped else r
            for r in _accept_all(proposal).records
        ]
    )


def test_a_report_with_two_unbooked_trades_books_them_before_its_holdings(
    run,
    read,
    fresh_workspaces,
    blob_store,
    job,
    enqueued,  # noqa: F811
):
    ws, _ = fresh_workspaces
    account = _account(run, ws)
    _cash_statement(run, ws, blob_store, account.id)
    report = _read(
        run,
        job,
        ws,
        blob_store,
        enqueued,
        DocumentSubtype.DEPOT_REPORT,
        _report_with([CASH_ROW], [BUY_MARCH, BUY_APRIL]),
    )
    proposed = _movement_records(report)
    assert [r.fields["external_ref"].value for r in proposed] == ["5500000002", "5500000004"]
    assert any(n.startswith("Der Bericht nennt 2 Käufe und Verkäufe") for n in _notes(report))

    _apply(run, ws, report)

    rows = sorted(_rows(read, ws, movement_table), key=lambda r: r.occurred_at)
    assert [(r.kind, r.status, r.source) for r in rows] == [
        ("acquisition", "confirmed", "report"),
        ("acquisition", "confirmed", "report"),
    ]
    assert [(r.quantity, r.eur_amount, r.external_ref) for r in rows] == [
        (Decimal("4.8000"), Decimal("-500.00"), "5500000002"),
        (Decimal("2.4000"), Decimal("-250.00"), "5500000004"),
    ]
    assert {r.document_id for r in rows} == {report.document_id}
    cash = {t.bank_reference: t.id for t in read(ws, list_transactions, account.id)}
    assert [r.transaction_id for r in rows] == [cash["5500000002"], None]
    assert len(_events(read, ws, EventType.MOVEMENT_CONFIRMED)) == 2

    (holding,) = _rows(read, ws, holding_table)
    assert holding.reconciled is True and holding.difference_units == 0
    (price,) = _rows(read, ws, price_table)
    assert (price.on, price.source, price.price_eur) == (
        date(2026, 6, 30),
        "statement",
        Decimal("104.50000000"),
    )
    assert _cards(read, ws, InboxItemKind.HOLDINGS_MISMATCH) == []
    assert _cards(read, ws, InboxItemKind.PRICE_NO_SOURCE) == []


def test_a_sale_in_the_report_is_a_disposal_with_its_cash_line(
    run,
    read,
    fresh_workspaces,
    blob_store,
    job,
    enqueued,  # noqa: F811
):
    ws, _ = fresh_workspaces
    account = _account(run, ws)
    _cash_statement(run, ws, blob_store, account.id)
    report = _read(
        run,
        job,
        ws,
        blob_store,
        enqueued,
        DocumentSubtype.DEPOT_REPORT,
        _report_with(
            [f"{ISIN_WORLD} | Beispiel MSCI World | 4,7500 | 100,00 | 475,00 | EUR"],
            [
                f"{ISIN_WORLD} | 02.03.2026 | 20,0000 | -1.600,00 | 5500000010",
                # A sale as quirion lists it: negative units, the amount credited.
                f"{ISIN_WORLD} | 12.03.2026 | -15,2500 | 1.234,50 | 5500000001",
            ],
        ),
    )
    _apply(run, ws, report)

    sale = next(r for r in _rows(read, ws, movement_table) if r.external_ref == "5500000001")
    assert (sale.kind, sale.source, sale.quantity, sale.eur_amount) == (
        "disposal",
        "report",
        Decimal("-15.2500"),
        Decimal("1234.50"),
    )
    cash = {t.bank_reference: t.id for t in read(ws, list_transactions, account.id)}
    assert sale.transaction_id == cash["5500000001"]
    (holding,) = _rows(read, ws, holding_table)
    assert holding.reconciled is True
    assert _cards(read, ws, InboxItemKind.HOLDINGS_MISMATCH) == []


def test_a_report_taken_over_without_its_trades_books_them_from_the_card(
    run,
    read,
    fresh_workspaces,
    blob_store,
    job,
    enqueued,  # noqa: F811
):
    ws, _ = fresh_workspaces
    depot_id = _account(run, ws).id
    report = _read(
        run,
        job,
        ws,
        blob_store,
        enqueued,
        DocumentSubtype.DEPOT_REPORT,
        _report_with([CASH_ROW], [BUY_MARCH, BUY_APRIL]),
    )
    run(ws, proposals.apply_proposal, report.id, _skipping_trades(report), iban_key=TEST_IBAN_KEY)
    assert _rows(read, ws, movement_table) == []
    assert _rows(read, ws, price_table) == []

    (card,) = _cards(read, ws, InboxItemKind.HOLDINGS_MISMATCH)
    assert card.account_id == depot_id and card.report_trades == 2
    assert "2 Käufe und Verkäufe" in card.question
    assert "„Umsätze aus dem Bericht buchen“" in card.question
    assert _cards(read, ws, InboxItemKind.PRICE_NO_SOURCE) == []

    booked = run(ws, depot.book_report_trades, depot_id)
    assert (booked.booked, booked.prices_stored) == (2, 1)
    rows = _rows(read, ws, movement_table)
    assert sorted(r.external_ref for r in rows) == ["5500000002", "5500000004"]
    assert {(r.source, r.document_id) for r in rows} == {("report", report.document_id)}
    (price,) = _rows(read, ws, price_table)
    assert price.document_id == report.document_id
    assert _cards(read, ws, InboxItemKind.HOLDINGS_MISMATCH) == []
    assert _cards(read, ws, InboxItemKind.PRICE_NO_SOURCE) == []

    # Pressed again: nothing left to book.
    again = run(ws, depot.book_report_trades, depot_id)
    assert (again.booked, again.prices_stored) == (0, 0)
    assert len(_rows(read, ws, movement_table)) == 2


def test_a_second_report_repeating_the_first_ones_trades_books_none_twice(
    run,
    read,
    fresh_workspaces,
    blob_store,
    job,
    enqueued,  # noqa: F811
):
    ws, _ = fresh_workspaces
    _account(run, ws)
    first = _read(
        run,
        job,
        ws,
        blob_store,
        enqueued,
        DocumentSubtype.DEPOT_REPORT,
        _report_with(
            [f"{ISIN_CASH} | Beispiel Euro Geldmarkt | 4,8000 | 104,00 | 499,20 | EUR"],
            # The April trade is after the first report's date: not its to book.
            [BUY_MARCH, BUY_APRIL],
            on="2026-03-31",
        ),
    )
    assert [r.fields["external_ref"].value for r in _movement_records(first)] == ["5500000002"]
    # The second report is read before the first is taken over, so its suggestion still
    # proposes both trades; „Übernehmen“ must not book the March one again.
    second = _read(
        run,
        job,
        ws,
        blob_store,
        enqueued,
        DocumentSubtype.DEPOT_REPORT,
        _report_with([CASH_ROW], [BUY_MARCH, BUY_APRIL, BUY_MARCH]),
    )
    assert [r.fields["external_ref"].value for r in _movement_records(second)] == [
        "5500000002",
        "5500000004",
    ]
    _apply(run, ws, first)
    _apply(run, ws, second)

    rows = _rows(read, ws, movement_table)
    assert sorted(r.external_ref for r in rows) == ["5500000002", "5500000004"]
    by_ref = {r.external_ref: r.document_id for r in rows}
    assert by_ref == {"5500000002": first.document_id, "5500000004": second.document_id}
    assert all(h.reconciled for h in _rows(read, ws, holding_table))

    # A third report, read once both are booked, proposes none of them.
    third = _read(
        run,
        job,
        ws,
        blob_store,
        enqueued,
        DocumentSubtype.DEPOT_REPORT,
        _report_with([CASH_ROW], [BUY_MARCH, BUY_APRIL], on="2026-09-30"),
    )
    assert _movement_records(third) == []
    assert any("2 weitere Umsätze des Berichts sind schon gebucht" in n for n in _notes(third))


def test_a_later_trade_confirmation_of_a_trade_the_report_booked_books_nothing_twice(
    run,
    read,
    fresh_workspaces,
    blob_store,
    job,
    enqueued,  # noqa: F811
):
    ws, _ = fresh_workspaces
    _account(run, ws)
    report = _read(
        run,
        job,
        ws,
        blob_store,
        enqueued,
        DocumentSubtype.DEPOT_REPORT,
        _report_with([CASH_ROW], [BUY_MARCH, BUY_APRIL]),
    )
    _apply(run, ws, report)
    (march,) = [r for r in _rows(read, ws, movement_table) if r.external_ref == "5500000002"]

    same = _read(run, job, ws, blob_store, enqueued, DocumentSubtype.TRADE_CONFIRMATION, _trade())
    assert any(n.startswith("Bereits aus dem Quartalsbericht gebucht") for n in _notes(same))
    assert not any("Die Abrechnung nennt" in n for n in _notes(same))
    _apply(run, ws, same)
    assert len(_rows(read, ws, movement_table)) == 2
    (kept,) = [r for r in _rows(read, ws, movement_table) if r.external_ref == "5500000002"]
    assert (kept.id, kept.source, kept.document_id) == (march.id, "report", report.document_id)

    # A confirmation whose figures differ from the report's: the difference is named, the
    # report's booking stays.
    differing = _trade(units="2.4100", net="251.00", reference="5500000004", on="2026-04-15")
    other = _read(run, job, ws, blob_store, enqueued, DocumentSubtype.TRADE_CONFIRMATION, differing)
    (note,) = [n for n in _notes(other) if n.startswith("Die Abrechnung nennt")]
    assert "2,41 Anteile" in note and "2,4 Anteile" in note
    assert "-251,00 €" in note and "-250,00 €" in note
    _apply(run, ws, other)
    (april,) = [r for r in _rows(read, ws, movement_table) if r.external_ref == "5500000004"]
    assert (april.quantity, april.eur_amount, april.source) == (
        Decimal("2.4000"),
        Decimal("-250.00"),
        "report",
    )
    assert len(_rows(read, ws, movement_table)) == 2


# --- Review of 15 Nov 2026 -------------------------------------------------------------------

ACTOR = Actor(ActorType.USER, "user:test")


def _reread(run, job, ws, document_id, subtype, values):
    """„Neu auslesen“: a second reading of the same document, its suggestion ready."""
    second = run(ws, proposals.request_read, document_id)
    classify_id = run(ws, _ai_call, document_id, "classify")
    extract_id = run(ws, _ai_call, document_id, "extract")
    return job(
        ws,
        proposals.record_read_result,
        second.id,
        classification=_classified(subtype),
        classify_call_id=classify_id,
        values=values,
        extract_call_id=extract_id,
    )


def _interleave(app_engine, ws, first, second):
    """`first` runs and holds its transaction open; `second` starts meanwhile on a thread;
    then `first` commits. Returns the results, the errors, and whether `second` was still
    waiting while `first` held its transaction."""
    results, errors = {}, {}
    first_done, release = threading.Event(), threading.Event()

    def run_first():
        try:
            with workspace_transaction(app_engine, ws) as conn:
                results["first"] = first(new_command(conn, ws, ACTOR))
                first_done.set()
                release.wait(30)
        except Exception as exc:  # noqa: BLE001
            errors["first"] = exc
            first_done.set()

    def run_second():
        try:
            with workspace_transaction(app_engine, ws) as conn:
                results["second"] = second(new_command(conn, ws, ACTOR))
        except Exception as exc:  # noqa: BLE001
            errors["second"] = exc

    one, two = threading.Thread(target=run_first), threading.Thread(target=run_second)
    one.start()
    assert first_done.wait(30)
    two.start()
    two.join(3)
    blocked = two.is_alive()
    release.set()
    one.join(30)
    two.join(30)
    return results, errors, blocked


def _taken_over_without_trades(run, job, ws, blob_store, enqueued, values):  # noqa: F811
    report = _read(run, job, ws, blob_store, enqueued, DocumentSubtype.DEPOT_REPORT, values)
    run(ws, proposals.apply_proposal, report.id, _skipping_trades(report), iban_key=TEST_IBAN_KEY)
    return report


def _assert_booked_once(read, ws, account_id):
    rows = _rows(read, ws, movement_table)
    assert sorted(r.external_ref for r in rows) == ["5500000002", "5500000004"]
    assert len(_events(read, ws, EventType.MOVEMENT_CONFIRMED)) == 2
    cash = {t.bank_reference: t.id for t in read(ws, list_transactions, account_id)}
    linked = [r.transaction_id for r in rows if r.transaction_id is not None]
    assert linked == [cash["5500000002"]]
    assert len(_rows(read, ws, price_table)) == 1


def test_two_presses_at_once_book_every_trade_once(
    run,
    read,
    fresh_workspaces,
    blob_store,
    job,
    enqueued,  # noqa: F811
    app_engine,
):
    ws, _ = fresh_workspaces
    depot_id = _account(run, ws).id
    _cash_statement(run, ws, blob_store, depot_id)
    values = _report_with([CASH_ROW], [BUY_MARCH, BUY_APRIL])
    _taken_over_without_trades(run, job, ws, blob_store, enqueued, values)

    def press(ctx):
        return depot.book_report_trades(ctx, depot_id)

    results, errors, blocked = _interleave(app_engine, ws, press, press)
    assert errors == {}
    assert blocked is True
    assert results["first"].booked == 2 and results["second"].booked == 0
    _assert_booked_once(read, ws, depot_id)


def test_a_reports_take_over_and_the_button_at_once_book_every_trade_once(
    run,
    read,
    fresh_workspaces,
    blob_store,
    job,
    enqueued,  # noqa: F811
    app_engine,
):
    ws, _ = fresh_workspaces
    depot_id = _account(run, ws).id
    _cash_statement(run, ws, blob_store, depot_id)
    values = _report_with([CASH_ROW], [BUY_MARCH, BUY_APRIL])
    report = _taken_over_without_trades(run, job, ws, blob_store, enqueued, values)
    again = _reread(run, job, ws, report.document_id, DocumentSubtype.DEPOT_REPORT, values)
    assert [r.fields["external_ref"].value for r in _movement_records(again)] == [
        "5500000002",
        "5500000004",
    ]
    body = _accept_all(again)

    def take_over(ctx):
        return proposals.apply_proposal(ctx, again.id, body, iban_key=TEST_IBAN_KEY)

    def press(ctx):
        return depot.book_report_trades(ctx, depot_id)

    results, errors, blocked = _interleave(app_engine, ws, take_over, press)
    assert errors == {}
    assert blocked is True
    assert results["second"].booked == 0
    _assert_booked_once(read, ws, depot_id)


# Read again, the report gives April other figures and lists one more trade.
OTHER_APRIL = f"{ISIN_CASH} | 15.04.2026 | 2,5000 | -999,00 | 5500000004"
EXTRA = f"{ISIN_WORLD} | 20.04.2026 | 1,0000 | -80,00 | 5500000077"


@pytest.mark.parametrize("rejected", [True, False], ids=["rejected", "waiting"])
def test_the_button_books_the_confirmed_reading_not_a_later_one(
    run,
    read,
    fresh_workspaces,
    blob_store,
    job,
    enqueued,  # noqa: F811
    rejected,
):
    ws, _ = fresh_workspaces
    depot_id = _account(run, ws).id
    report = _taken_over_without_trades(
        run, job, ws, blob_store, enqueued, _report_with([CASH_ROW], [BUY_MARCH, BUY_APRIL])
    )
    again = _reread(
        run,
        job,
        ws,
        report.document_id,
        DocumentSubtype.DEPOT_REPORT,
        _report_with([CASH_ROW], [BUY_MARCH, OTHER_APRIL, EXTRA]),
    )
    assert again.status is ProposalStatus.READY
    if rejected:
        run(ws, proposals.reject_proposal, again.id)

    (card,) = _cards(read, ws, InboxItemKind.HOLDINGS_MISMATCH)
    assert card.report_trades == 2
    booked = run(ws, depot.book_report_trades, depot_id)
    assert (booked.booked, booked.left_out) == (2, [])
    rows = {r.external_ref: r for r in _rows(read, ws, movement_table)}
    assert sorted(rows) == ["5500000002", "5500000004"]
    assert (rows["5500000004"].quantity, rows["5500000004"].eur_amount) == (
        Decimal("2.4000"),
        Decimal("-250.00"),
    )


def test_a_trade_booked_under_another_reference_leaves_its_fund_out(
    run,
    read,
    fresh_workspaces,
    blob_store,
    job,
    enqueued,  # noqa: F811
):
    ws, _ = fresh_workspaces
    depot_id = _account(run, ws).id
    # The March purchase's confirmation names another reference than the report's list.
    confirmation = _read(
        run,
        job,
        ws,
        blob_store,
        enqueued,
        DocumentSubtype.TRADE_CONFIRMATION,
        _trade(reference="5500000099"),
    )
    _apply(run, ws, confirmation)
    _taken_over_without_trades(
        run, job, ws, blob_store, enqueued, _report_with([CASH_ROW], [BUY_MARCH, BUY_APRIL])
    )

    (card,) = _cards(read, ws, InboxItemKind.HOLDINGS_MISMATCH)
    assert card.report_trades is None
    assert (
        "Nicht aus dem Bericht gebucht: Beispiel Euro Geldmarkt (Bestand würde nicht zum "
        "Bericht passen)."
    ) in card.question
    booked = run(ws, depot.book_report_trades, depot_id)
    assert (booked.booked, booked.prices_stored, booked.left_out) == (
        0,
        0,
        ["Beispiel Euro Geldmarkt"],
    )
    assert len(_rows(read, ws, movement_table)) == 1


def test_rows_that_cannot_be_read_are_named_and_the_good_one_is_booked(
    run,
    read,
    fresh_workspaces,
    blob_store,
    job,
    enqueued,  # noqa: F811
):
    ws, _ = fresh_workspaces
    depot_id = _account(run, ws).id
    world = f"{ISIN_WORLD} | 02.03.2026 | 20,0000 | -1.600,00 | 5500000010"
    # A purchase whose amount is credited, and an amount with three decimals.
    contradicting = f"{ISIN_CASH} | 15.04.2026 | 2,4000 | 250,00 | 5500000004"
    three_places = f"{ISIN_CASH} | 16.04.2026 | 1,0000 | -100,005 | 5500000005"
    _taken_over_without_trades(
        run,
        job,
        ws,
        blob_store,
        enqueued,
        _report_with(
            [
                f"{ISIN_WORLD} | Beispiel MSCI World | 20,0000 | 80,00 | 1.600,00 | EUR",
                f"{ISIN_CASH} | Beispiel Euro Geldmarkt | 3,4000 | 104,50 | 355,30 | EUR",
            ],
            [world, contradicting, three_places],
        ),
    )

    (card,) = _cards(read, ws, InboxItemKind.HOLDINGS_MISMATCH)
    assert card.report_trades == 1
    assert (
        "Umsätze des Berichts sind nicht lesbar: vom 15.04.2026 (Beispiel Euro Geldmarkt) und "
        "vom 16.04.2026 (Beispiel Euro Geldmarkt). Bitte die Wertpapierabrechnung dieser "
        "Umsätze unter Dokumente hochladen."
    ) in card.question
    booked = run(ws, depot.book_report_trades, depot_id)
    assert (booked.booked, booked.left_out) == (1, [])
    (row,) = _rows(read, ws, movement_table)
    assert (row.external_ref, row.quantity) == ("5500000010", Decimal("20.0000"))
    (card,) = _cards(read, ws, InboxItemKind.HOLDINGS_MISMATCH)
    assert card.report_trades is None and "nicht lesbar" in card.question
