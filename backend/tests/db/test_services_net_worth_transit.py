"""Money in transit to an own account whose statements lag (phase 6 D292 (3), 22 Nov 2026).

A booked transfer from the current account to the depot whose cash statements reach only up
to before the payment counts as „unterwegs zu <Depot>“ until a depot statement covers the
day, or until the debit is linked to the depot's credit; a stored month-end holds it inside
the depot's balance row, so the month-end does not change when the statement arrives.
Invented IBANs and amounts only."""

import hashlib
import uuid
from datetime import date
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.test_services_net_worth import NOW, ON, _account, _import, _snapshot_rows
from sqlalchemy import insert

from hausbuch.db.tables import transaction as transaction_table
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.net_worth import NetWorthLineKind, SnapshotOutcome
from hausbuch.services import allocations, net_worth, transfers
from hausbuch.services.iban import protect_iban

pytestmark = pytest.mark.db

C = AllocationCategory
CURRENT_IBAN = "DE02120300000000202051"
DEPOT_IBAN = "DE73100000000000000072"  # Bundesbank test bank code 10000000
PAID_ON = date(2026, 10, 29)


def _line(ctx, account_id, import_id, booked, amount, iban=None):
    protected = None if iban is None else protect_iban(iban, TEST_IBAN_KEY)
    return ctx.conn.execute(
        insert(transaction_table)
        .values(
            workspace_id=ctx.workspace_id,
            account_id=account_id,
            import_id=import_id,
            line_hash=hashlib.sha256(uuid.uuid4().bytes).digest(),
            booking_date=booked,
            value_date=booked,
            amount=Decimal(amount),
            counterparty_iban_masked=None if protected is None else protected.masked,
            counterparty_iban_hmac=None if protected is None else protected.hmac,
            purpose="Sparplan",
            created_at=ctx.now,
        )
        .returning(transaction_table.c.id)
    ).scalar_one()


def _book(run, ws, tx, amount, category):
    run(
        ws,
        allocations.record_set,
        AllocationSet(
            transaction_id=tx,
            allocations=[AllocationDraft(amount=Decimal(amount), category=category)],
            method=AllocationMethod.USER,
            confidence=Decimal("1.000"),
        ),
        confirm=True,
        auto=False,
        now=NOW,
    )


def _world(run, ws):
    """A current account of 1,000 € (30 Jun), reconciled to 31 Oct, that sent 500 € to the
    depot on 29 Oct; the depot's cash statement (200 € credited on 1 Oct) reaches 20 Oct."""
    current = _account(
        run, ws, "Girokonto", AccountKind.BANK_CURRENT, iban=CURRENT_IBAN, opening="1000.00"
    )
    depot = _account(run, ws, "Depot", AccountKind.DEPOT, iban=DEPOT_IBAN)

    def stored(ctx):
        statement = _import(ctx, current, date(2026, 7, 1), ON)
        debit = _line(ctx, current, statement, PAID_ON, "-500.00", iban=DEPOT_IBAN)
        cash = _import(ctx, depot, date(2026, 7, 1), date(2026, 10, 20))
        _line(ctx, depot, cash, date(2026, 10, 1), "200.00")
        return debit

    debit = run(ws, stored, now=NOW)
    _book(run, ws, debit, "-500.00", C.INTERNAL_TRANSFER_OUT)
    return current, depot, debit


def _moving(figure):
    return [line for line in figure.lines if line.in_transit]


def test_money_sent_to_a_depot_whose_statement_lags_counts_once(run, read, fresh_workspaces):
    ws, _ = fresh_workspaces
    current, depot, _ = _world(run, ws)

    seen = read(ws, net_worth.net_worth, ON)
    balances = {
        line.account_id: line.value_eur
        for line in seen.lines
        if line.kind is NetWorthLineKind.ACCOUNT and not line.in_transit
    }
    assert balances == {current: Decimal("500.00"), depot: Decimal("200.00")}
    [moving] = _moving(seen)
    assert (moving.account_id, moving.value_eur, moving.as_of) == (depot, Decimal("500.00"), None)
    assert moving.label == "Unterwegs zu „Depot“"
    assert "29.10.2026" in moving.note and "Girokonto" in moving.note
    assert seen.total == Decimal("1200.00")  # the 500 € counted once, not lost
    # The transit line comes after the depot's balance line (the account page reads the first).
    depot_lines = [line for line in seen.lines if line.account_id == depot]
    assert [line.in_transit for line in depot_lines] == [False, True]
    # Folded into the depot's balance for the stored row, the reserve and health-check.
    merged = net_worth.merge_transit(seen.lines)
    assert [line.value_eur for line in merged if line.account_id == depot] == [Decimal("700.00")]
    assert not any(line.in_transit for line in merged)

    # Before the payment day nothing is in transit.
    assert _moving(read(ws, net_worth.net_worth, date(2026, 10, 28))) == []

    # The month-end stores the depot's balance with the money in it, one row per account.
    first = run(ws, net_worth.capture_month_end, ON, now=NOW)
    assert first.outcome is SnapshotOutcome.WRITTEN and first.run.rows == 2
    rows = {r.account_id: r.value_eur for r in _snapshot_rows(read, ws)}
    assert rows == {current: Decimal("500.00"), depot: Decimal("700.00")}
    assert read(ws, net_worth.latest_month_end).total == Decimal("1200.00")

    # The depot's next statement covers the day: its own balance counts the money, the
    # transit line drops, and the stored month-end is unchanged.
    def statement(ctx):
        cash = _import(ctx, depot, date(2026, 10, 21), ON)
        _line(ctx, depot, cash, date(2026, 10, 30), "500.00", iban=CURRENT_IBAN)

    run(ws, statement, now=NOW)
    after = read(ws, net_worth.net_worth, ON)
    assert _moving(after) == []
    assert after.total == Decimal("1200.00")
    again = run(ws, net_worth.capture_month_end, ON, now=NOW.replace(hour=9))
    assert again.outcome is SnapshotOutcome.UNCHANGED


def test_a_linked_transfer_is_never_in_transit(run, read, fresh_workspaces):
    """The depot's credit booked two days before the debit (D162) and linked to it: the
    depot's balance holds the money, so it is not counted a second time."""
    ws, _ = fresh_workspaces
    current, depot, debit = _world(run, ws)

    def credit(ctx):
        cash = _import(ctx, depot, date(2026, 10, 21), date(2026, 10, 27))
        return _line(ctx, depot, cash, date(2026, 10, 27), "500.00", iban=CURRENT_IBAN)

    leg = run(ws, credit, now=NOW)
    # Unlinked, the debit is in transit: the depot's history (27 Oct) ends before it.
    assert [line.value_eur for line in _moving(read(ws, net_worth.net_worth, ON))] == [
        Decimal("500.00")
    ]
    _book(run, ws, leg, "500.00", C.INTERNAL_TRANSFER_IN)
    linked = {link.out_transaction_id for link in read(ws, transfers._current_links)}
    if debit not in linked:  # the matcher's hook did not pair them: a person links them
        from hausbuch.schemas.household import TransferLinkCreate, TransferLinkKind

        run(
            ws,
            transfers.link_by_hand,
            TransferLinkCreate(
                kind=TransferLinkKind.DEPOT_CASH,
                out_transaction_id=debit,
                in_transaction_id=leg,
            ),
            iban_key=TEST_IBAN_KEY,
            now=NOW,
        )
    seen = read(ws, net_worth.net_worth, ON)
    assert _moving(seen) == []
    assert seen.total == Decimal("1200.00")  # 500 + 700, the credit counted on the depot


def test_a_transfer_to_an_account_without_history_is_not_in_transit(run, read, fresh_workspaces):
    """A depot with no cash statement has no balance line and no history: nothing says the
    money reached an account the app follows, so it stays out (as before D292)."""
    ws, _ = fresh_workspaces
    current = _account(
        run, ws, "Girokonto", AccountKind.BANK_CURRENT, iban=CURRENT_IBAN, opening="1000.00"
    )
    _account(run, ws, "Depot", AccountKind.DEPOT, iban=DEPOT_IBAN)

    def stored(ctx):
        statement = _import(ctx, current, date(2026, 7, 1), ON)
        return _line(ctx, current, statement, PAID_ON, "-500.00", iban=DEPOT_IBAN)

    debit = run(ws, stored, now=NOW)
    _book(run, ws, debit, "-500.00", C.INTERNAL_TRANSFER_OUT)
    seen = read(ws, net_worth.net_worth, ON)
    assert _moving(seen) == [] and seen.total == Decimal("500.00")
