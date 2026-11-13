"""A depot reading applied through the one pre-filled form (phase 6 step 4, D165 (2), (3),
D132), as hausbuch_app under RLS: the movement a trade confirmation writes, its cash line, a
transfer between own depots, a report's reconciliation with its price and the position of 0
by omission, and the refusals. Invented ISINs, references and figures only."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.test_services_proposals import _accept_all, _ai_call, _read_ready, _v
from db.test_services_proposals import enqueued as enqueued  # noqa: F401 (a fixture)
from db.test_services_proposals import job as job  # noqa: F401 (a fixture)
from sqlalchemy import select
from test_card_pdf import draw
from test_depot_pdf import page

from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import holding_statement as holding_table
from hausbuch.db.tables import price as price_table
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.ai import ClassifyOutput
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.bank import AccountCreate
from hausbuch.schemas.enums import AccountKind, DocumentSubtype, DocumentType, ProposalStatus
from hausbuch.schemas.events import EventType
from hausbuch.schemas.proposal import ProposalApplyBody, ProposalApplyRecord
from hausbuch.services import depot, proposals, review
from hausbuch.services.accounts import create_account
from hausbuch.services.statements import import_statement, list_transactions

pytestmark = pytest.mark.db

T0 = datetime(2026, 11, 13, 8, 0, tzinfo=UTC)
ISIN_WORLD = "IE00BEISPL01"
ISIN_CASH = "IE00BEISPL02"


def _classified(subtype: DocumentSubtype) -> ClassifyOutput:
    return ClassifyOutput(
        type=DocumentType.LETTER,
        subtype=subtype,
        contains_tenant_data=False,
        language="de",
        confidence="high",
    )


def _account(run, ws, label="Globales Portfolio", kind=AccountKind.DEPOT, **extra):
    data = {
        "label": label,
        "bank": "Beispiel-Vermögensverwaltung",
        "kind": kind,
        "opening_balance": Decimal("100.00"),
        "opening_balance_on": date(2026, 3, 1),
    } | extra
    return run(ws, create_account, AccountCreate(**data), iban_key=TEST_IBAN_KEY, now=T0)


def _read(run, job, ws, blob_store, enqueued, subtype, values):  # noqa: F811
    _, ready, _ = _read_ready(
        run,
        job,
        ws,
        blob_store,
        enqueued,
        _classified(subtype),
        values,
        b"%PDF-1.7 depot " + uuid.uuid4().hex.encode(),
    )
    assert ready.status is ProposalStatus.READY
    return ready


def _apply(run, ws, proposal, **overrides):
    return run(
        ws,
        proposals.apply_proposal,
        proposal.id,
        _accept_all(proposal, **overrides),
        iban_key=TEST_IBAN_KEY,
    )


def _trade(side="Kauf", units="4.8000", net="500.00", reference="5500000002", on="2026-03-13"):
    return {
        "isin": _v(ISIN_CASH),
        "security_name": _v("Beispiel Euro Geldmarkt UCITS ETF"),
        "trade_date": _v(on),
        "side": _v(side),
        "units": _v(units),
        "gross_amount": _v(net),
        "fees": _v("0.00"),
        "taxes": _v("0.00"),
        "net_amount": _v(net),
        "order_reference": _v(reference),
    }


def _rows(read, ws, table):
    return read(ws, lambda conn: conn.execute(select(table)).all())


def _events(read, ws, *types):
    def query(conn):
        rows = conn.execute(
            select(event_table.c.type, event_table.c.payload).where(
                event_table.c.type.in_([t.value for t in types])
            )
        )
        return [(EventType(r.type), r.payload) for r in rows]

    return read(ws, query)


def test_a_trade_confirmation_books_a_confirmed_movement_linked_to_its_cash_line(
    run,
    read,
    fresh_workspaces,
    blob_store,
    job,
    enqueued,  # noqa: F811
):
    ws, _ = fresh_workspaces
    account = _account(run, ws)
    run(
        ws,
        import_statement,
        blob_store,
        account.id,
        draw([page()]),
        media_type="application/pdf",
        iban_key=TEST_IBAN_KEY,
        original_filename="kontoauszug.pdf",
        now=T0,
    )
    proposal = _read(
        run, job, ws, blob_store, enqueued, DocumentSubtype.TRADE_CONFIRMATION, _trade()
    )
    movement = {r.key: r for r in proposal.records}["movement:1"]
    assert movement.fields["account_id"].value == str(account.id)  # the only depot

    _apply(run, ws, proposal)

    (row,) = _rows(read, ws, movement_table)
    assert (row.kind, row.status, row.source) == ("acquisition", "confirmed", "trade_confirmation")
    assert row.quantity == Decimal("4.8000") and row.eur_amount == Decimal("-500.00")
    assert row.document_id == proposal.document_id
    cash = {t.bank_reference: t.id for t in read(ws, list_transactions, account.id)}
    assert row.transaction_id == cash["5500000002"]
    (asset,) = _rows(read, ws, asset_table)
    assert (asset.isin, asset.kind) == (ISIN_CASH, "etf")
    assert [t for t, _ in _events(read, ws, EventType.MOVEMENT_CONFIRMED)] == [
        EventType.MOVEMENT_CONFIRMED
    ]


def test_the_same_trade_applied_from_a_second_upload_is_refused(
    run,
    read,
    fresh_workspaces,
    blob_store,
    job,
    enqueued,  # noqa: F811
):
    ws, _ = fresh_workspaces
    _account(run, ws)
    first = _read(run, job, ws, blob_store, enqueued, DocumentSubtype.TRADE_CONFIRMATION, _trade())
    _apply(run, ws, first)
    second = _read(run, job, ws, blob_store, enqueued, DocumentSubtype.TRADE_CONFIRMATION, _trade())
    with pytest.raises(RuleError) as refused:
        _apply(run, ws, second)
    assert refused.value.code == depot.DEPOT_MOVEMENT_EXISTS
    assert len(_rows(read, ws, movement_table)) == 1


def _distribution():
    return {
        "isin": _v(ISIN_CASH),
        "security_name": _v("Beispiel Euro Geldmarkt"),
        "payment_date": _v("2026-04-15"),
        "units": _v("4.8000"),
        "gross_amount_eur": _v("3.20"),
        "net_amount_eur": _v("2.36"),
        "german_taxes_eur": _v("0.84"),
    }


def test_a_distribution_note_read_again_and_applied_again_is_refused(
    run,
    read,
    fresh_workspaces,
    blob_store,
    job,
    enqueued,  # noqa: F811
):
    """A distribution note names no reference: its twin is the same fund, kind, day and
    amount (review finding F1: „Neu auslesen“ and a second „Übernehmen“ booked it twice)."""
    ws, _ = fresh_workspaces
    _account(run, ws)
    _apply(
        run,
        ws,
        _read(run, job, ws, blob_store, enqueued, DocumentSubtype.TRADE_CONFIRMATION, _trade()),
    )
    fields = _distribution()
    first = _read(run, job, ws, blob_store, enqueued, DocumentSubtype.DISTRIBUTION_NOTE, fields)
    _apply(run, ws, first)
    second = run(ws, proposals.request_read, first.document_id)
    classify_id = run(ws, _ai_call, first.document_id, "classify")
    extract_id = run(ws, _ai_call, first.document_id, "extract")
    second = job(
        ws,
        proposals.record_read_result,
        second.id,
        classification=_classified(DocumentSubtype.DISTRIBUTION_NOTE),
        classify_call_id=classify_id,
        values=fields,
        extract_call_id=extract_id,
    )
    with pytest.raises(RuleError) as refused:
        _apply(run, ws, second)
    assert refused.value.code == depot.DEPOT_MOVEMENT_EXISTS
    kinds = sorted(r.kind for r in _rows(read, ws, movement_table))
    assert kinds == ["acquisition", "distribution"]

    # Another day's distribution of the same amount is its own payment.
    later = _distribution() | {"payment_date": _v("2026-07-15")}
    _apply(
        run,
        ws,
        _read(run, job, ws, blob_store, enqueued, DocumentSubtype.DISTRIBUTION_NOTE, later),
    )
    assert sum(r.kind == "distribution" for r in _rows(read, ws, movement_table)) == 2


def test_a_record_on_an_account_that_is_no_depot_is_refused(
    run,
    read,
    fresh_workspaces,
    blob_store,
    job,
    enqueued,  # noqa: F811
):
    ws, _ = fresh_workspaces
    _account(run, ws)
    bank = _account(
        run,
        ws,
        label="Girokonto",
        kind=AccountKind.BANK_CURRENT,
        iban="DE89370400440532013000",
    )
    proposal = _read(
        run, job, ws, blob_store, enqueued, DocumentSubtype.TRADE_CONFIRMATION, _trade()
    )
    with pytest.raises(RuleError) as refused:
        _apply(run, ws, proposal, **{"movement:1": {"account_id": str(bank.id)}})
    assert refused.value.code == depot.DEPOT_ACCOUNT_REQUIRED
    assert _rows(read, ws, movement_table) == []


def test_an_ausbuchung_and_its_einbuchung_are_one_transfer_that_keeps_the_units(
    run,
    read,
    fresh_workspaces,
    blob_store,
    job,
    enqueued,  # noqa: F811
):
    ws, _ = fresh_workspaces
    giving = _account(run, ws, label="Depot A")
    taking = _account(run, ws, label="Depot B")

    def transfer(direction):
        return {
            "direction": _v(direction),
            "transfer_date": _v("2026-04-02"),
            "reference": _v("UMB0001"),
            "positions": _v([f"{ISIN_WORLD} | Beispiel Welt ETF | 3,0000"]),
        }

    out = _read(
        run,
        job,
        ws,
        blob_store,
        enqueued,
        DocumentSubtype.SECURITIES_TRANSFER,
        transfer("Ausbuchung"),
    )
    assert {r.key: r for r in out.records}["movement:1"].fields["account_id"].value is None
    _apply(run, ws, out, **{"movement:1": {"account_id": str(giving.id)}})
    into = _read(
        run,
        job,
        ws,
        blob_store,
        enqueued,
        DocumentSubtype.SECURITIES_TRANSFER,
        transfer("Einbuchung"),
    )
    _apply(run, ws, into, **{"movement:1": {"account_id": str(taking.id)}})

    rows = {r.kind: r for r in _rows(read, ws, movement_table)}
    assert rows["internal_transfer_out"].quantity == Decimal("-3.0000")
    assert rows["internal_transfer_in"].quantity == Decimal("3.0000")
    assert rows["internal_transfer_out"].transaction_id is None  # no cash moved
    pair = read(ws, depot.transfer_pair, rows["internal_transfer_out"].id)
    assert pair == rows["internal_transfer_in"].id
    assert read(ws, depot.transfer_pair, pair) == rows["internal_transfer_out"].id


def _report(*positions, on="2026-06-30"):
    return {
        "report_date": _v(on),
        "positions": _v(list(positions)),
        "trades": _v([]),
    }


def test_a_report_that_agrees_with_the_movements_reconciles_and_prices_the_fund(
    run,
    read,
    fresh_workspaces,
    blob_store,
    job,
    enqueued,  # noqa: F811
):
    ws, _ = fresh_workspaces
    _account(run, ws)
    _apply(
        run,
        ws,
        _read(run, job, ws, blob_store, enqueued, DocumentSubtype.TRADE_CONFIRMATION, _trade()),
    )
    report = _read(
        run,
        job,
        ws,
        blob_store,
        enqueued,
        DocumentSubtype.DEPOT_REPORT,
        _report(f"{ISIN_CASH} | Beispiel Euro Geldmarkt | 4,8000 | 104,50 | 501,60 | EUR"),
    )
    _apply(run, ws, report)

    (holding,) = _rows(read, ws, holding_table)
    assert holding.reconciled is True and holding.difference_units == 0
    assert holding.declared_value_eur == Decimal("501.60")
    (price,) = _rows(read, ws, price_table)
    assert (price.on, price.source) == (date(2026, 6, 30), "statement")
    assert price.price_eur == Decimal("104.50000000")
    (event,) = _events(
        read, ws, EventType.ACCOUNT_HOLDINGS_RECONCILED, EventType.ACCOUNT_HOLDINGS_MISMATCH
    )
    assert event[0] is EventType.ACCOUNT_HOLDINGS_RECONCILED
    assert event[1]["assets_mismatched"] == 0


def test_a_fund_held_but_missing_from_the_report_is_a_position_of_0_and_a_mismatch(
    run,
    read,
    fresh_workspaces,
    blob_store,
    job,
    enqueued,  # noqa: F811
):
    ws, _ = fresh_workspaces
    _account(run, ws)
    _apply(
        run,
        ws,
        _read(run, job, ws, blob_store, enqueued, DocumentSubtype.TRADE_CONFIRMATION, _trade()),
    )
    report = _read(
        run,
        job,
        ws,
        blob_store,
        enqueued,
        DocumentSubtype.DEPOT_REPORT,
        _report(f"{ISIN_WORLD} | Beispiel Welt ETF | 2,0000 | 50,00 | 100,00 | EUR"),
    )
    _apply(run, ws, report)

    # Only the listed position is stored; the world fund was never bought, so the report's
    # 2 units are not explained.
    (listed,) = _rows(read, ws, holding_table)
    assert listed.reconciled is False and listed.difference_units == Decimal(2)
    assert _rows(read, ws, price_table) == []  # no price from a position that disagrees
    # The money-market fund is held by the movements but not listed: 0 by omission, a
    # mismatch counted in the event but never stored as a row (review finding F2).
    (event,) = _events(
        read, ws, EventType.ACCOUNT_HOLDINGS_RECONCILED, EventType.ACCOUNT_HOLDINGS_MISMATCH
    )
    assert event[0] is EventType.ACCOUNT_HOLDINGS_MISMATCH
    assert event[1]["assets_mismatched"] == 2
    assert event[1]["holding_statement_ids"] == [str(listed.id)]
    (card,) = [i for i in read(ws, review.inbox) if i.kind is InboxItemKind.HOLDINGS_MISMATCH]
    assert "laut Bericht 0, gebucht 4,8 Anteile" in card.reasoning
    positions = {p.isin: p for p in read(ws, depot.etf_overview).accounts[0].positions}
    omitted = positions[ISIN_CASH]
    assert (omitted.report_on, omitted.report_units) == (date(2026, 6, 30), Decimal(0))
    assert omitted.reconciled is False
    assert omitted.value_eur is None and omitted.report_price_eur is None


def test_a_fund_a_first_reading_missed_is_added_from_a_second_reading(
    run,
    read,
    fresh_workspaces,
    blob_store,
    job,
    enqueued,  # noqa: F811
):
    """A report's first reading missed a fund; the second reading lists it and only that
    position is applied (the other one is stored already). Review finding F2: the stored
    0-by-omission row made that position a twin, and the card's „überspringen“ a dead end."""
    ws, _ = fresh_workspaces
    _account(run, ws)
    _apply(
        run,
        ws,
        _read(run, job, ws, blob_store, enqueued, DocumentSubtype.TRADE_CONFIRMATION, _trade()),
    )
    world = _trade(units="2.0000", net="100.00", reference="5500000005", on="2026-03-20") | {
        "isin": _v(ISIN_WORLD),
        "security_name": _v("Beispiel Welt ETF"),
    }
    _apply(
        run,
        ws,
        _read(run, job, ws, blob_store, enqueued, DocumentSubtype.TRADE_CONFIRMATION, world),
    )
    cash_row = f"{ISIN_CASH} | Beispiel Euro Geldmarkt | 4,8000 | 104,50 | 501,60 | EUR"
    world_row = f"{ISIN_WORLD} | Beispiel Welt ETF | 2,0000 | 50,00 | 100,00 | EUR"
    first = _read(
        run, job, ws, blob_store, enqueued, DocumentSubtype.DEPOT_REPORT, _report(cash_row)
    )
    _apply(run, ws, first)

    def cards():
        return [i for i in read(ws, review.inbox) if i.kind is InboxItemKind.HOLDINGS_MISMATCH]

    assert len(cards()) == 1  # the world fund is 0 by omission

    second = _read(
        run,
        job,
        ws,
        blob_store,
        enqueued,
        DocumentSubtype.DEPOT_REPORT,
        _report(cash_row, world_row),
    )
    world_record = next(r for r in second.records if r.key == "holding:2")
    body = ProposalApplyBody(
        records=[
            ProposalApplyRecord(key="holding:1", skip=True),
            ProposalApplyRecord(
                key="holding:2",
                values={f: v.value for f, v in world_record.fields.items() if v.value is not None},
            ),
        ]
    )
    run(ws, proposals.apply_proposal, second.id, body, iban_key=TEST_IBAN_KEY)

    rows = _rows(read, ws, holding_table)
    assert sorted(r.declared_units for r in rows) == [Decimal(2), Decimal("4.8")]
    assert all(r.reconciled for r in rows)
    assert cards() == []
    events = _events(
        read, ws, EventType.ACCOUNT_HOLDINGS_RECONCILED, EventType.ACCOUNT_HOLDINGS_MISMATCH
    )
    assert [t for t, _ in events] == [
        EventType.ACCOUNT_HOLDINGS_MISMATCH,
        EventType.ACCOUNT_HOLDINGS_RECONCILED,
    ]
    assert sorted(events[1][1]["holding_statement_ids"]) == sorted(str(r.id) for r in rows)


def test_a_report_applied_a_second_time_is_refused(
    run,
    read,
    fresh_workspaces,
    blob_store,
    job,
    enqueued,  # noqa: F811
):
    ws, _ = fresh_workspaces
    _account(run, ws)
    row = f"{ISIN_WORLD} | Beispiel Welt ETF | 2,0000 | 50,00 | 100,00 | EUR"
    _apply(
        run,
        ws,
        _read(run, job, ws, blob_store, enqueued, DocumentSubtype.DEPOT_REPORT, _report(row)),
    )
    again = _read(run, job, ws, blob_store, enqueued, DocumentSubtype.DEPOT_REPORT, _report(row))
    with pytest.raises(RuleError) as refused:
        _apply(run, ws, again)
    assert refused.value.code == depot.DEPOT_HOLDING_EXISTS
    assert len(_rows(read, ws, holding_table)) == 1


def test_a_report_naming_an_unbooked_trade_is_a_card_until_that_trade_is_applied(
    run,
    read,
    fresh_workspaces,
    blob_store,
    job,
    enqueued,  # noqa: F811
):
    ws, _ = fresh_workspaces
    depot_id = _account(run, ws).id
    _apply(
        run,
        ws,
        _read(run, job, ws, blob_store, enqueued, DocumentSubtype.TRADE_CONFIRMATION, _trade()),
    )
    report = _read(
        run,
        job,
        ws,
        blob_store,
        enqueued,
        DocumentSubtype.DEPOT_REPORT,
        _report(f"{ISIN_CASH} | Beispiel Euro Geldmarkt | 7,2000 | 104,50 | 752,40 | EUR")
        | {
            "trades": _v(
                [
                    f"{ISIN_CASH} | 13.03.2026 | 4,8000 | 500,00 | 5500000002",
                    f"{ISIN_CASH} | 15.04.2026 | 2,4000 | 250,00 | 5500000003",
                ]
            )
        },
    )
    _apply(run, ws, report)

    def cards():
        return [i for i in read(ws, review.inbox) if i.kind is InboxItemKind.HOLDINGS_MISMATCH]

    (card,) = cards()
    assert card.account_id == depot_id and card.document_id == report.document_id
    assert card.reasoning == (
        "Der Depotbericht zum 30.06.2026 nennt eine andere Stückzahl als die gebuchten "
        "Abrechnungen (Beispiel Euro Geldmarkt UCITS ETF: laut Bericht 7,2, gebucht 4,8 "
        "Anteile)."
    )
    # The trade booked already is not named; the one the report lists without a booking is.
    assert "15.04.2026" in card.question and "13.03.2026" not in card.question
    assert ISIN_CASH not in card.question + card.reasoning

    second = _trade(units="2.4000", net="250.00", reference="5500000003", on="2026-04-15")
    _apply(
        run,
        ws,
        _read(run, job, ws, blob_store, enqueued, DocumentSubtype.TRADE_CONFIRMATION, second),
    )
    assert cards() == []


def test_the_etf_tab_shows_the_reports_value_until_something_moves_after_it(
    run,
    read,
    fresh_workspaces,
    blob_store,
    job,
    enqueued,  # noqa: F811
):
    ws, _ = fresh_workspaces
    depot_id = _account(run, ws).id
    buy = _read(run, job, ws, blob_store, enqueued, DocumentSubtype.TRADE_CONFIRMATION, _trade())
    _apply(run, ws, buy)
    row = f"{ISIN_CASH} | Beispiel Euro Geldmarkt | 4,8000 | 104,50 | 501,60 | EUR"
    report = _read(run, job, ws, blob_store, enqueued, DocumentSubtype.DEPOT_REPORT, _report(row))
    _apply(run, ws, report)

    overview = read(ws, depot.etf_overview)
    (account,) = overview.accounts
    assert account.account_id == depot_id
    (position,) = account.positions
    assert position.units == Decimal("4.8") and position.reconciled is True
    assert position.report_on == date(2026, 6, 30)
    assert position.report_price_eur == Decimal("104.50")
    assert (position.value_eur, position.cost_eur, position.gain_eur) == (
        Decimal("501.60"),
        Decimal("500.00"),
        Decimal("1.60"),
    )
    assert {d.document_id: d.reading for d in overview.documents} == {
        buy.document_id: "applied",
        report.document_id: "applied",
    }

    # A sale after the report: the report's value no longer holds, and a plain sum is no
    # longer the cost (the lots are phase 7).
    sale = _trade(
        side="Verkauf", units="1.0000", net="105.00", reference="5500000009", on="2026-07-02"
    )
    _apply(
        run,
        ws,
        _read(run, job, ws, blob_store, enqueued, DocumentSubtype.TRADE_CONFIRMATION, sale),
    )
    (position,) = read(ws, depot.etf_overview).accounts[0].positions
    assert position.units == Decimal("3.8") and position.moved_since_report is True
    assert position.value_eur is None and position.cost_eur is None and position.gain_eur is None
    assert position.report_value_eur == Decimal("501.60")
