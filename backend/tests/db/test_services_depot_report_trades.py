"""Phase 6 D165 amended, 15 Nov 2026, as hausbuch_app under RLS: a quarterly report books the
trades its Umsätze list names and no movement carries, from its suggestion or, for a report
already taken over, by „Umsätze aus dem Bericht buchen“; a later report repeating them and a
later trade confirmation with the same reference book nothing twice. Invented ISINs,
references and figures only."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.test_services_depot_securities import (
    ISIN_CASH,
    ISIN_WORLD,
    _account,
    _apply,
    _events,
    _read,
    _report,
    _rows,
    _trade,
)
from db.test_services_proposals import _accept_all, _v
from db.test_services_proposals import enqueued as enqueued  # noqa: F401 (a fixture)
from db.test_services_proposals import job as job  # noqa: F401 (a fixture)
from test_card_pdf import draw
from test_depot_pdf import page

from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import holding_statement as holding_table
from hausbuch.db.tables import price as price_table
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.enums import DocumentSubtype
from hausbuch.schemas.events import EventType
from hausbuch.schemas.proposal import ProposalApplyBody, ProposalApplyRecord
from hausbuch.services import depot, proposals, review
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
