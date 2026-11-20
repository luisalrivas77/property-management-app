"""services/net_worth.py before a property's handover, as hausbuch_app under RLS
(docs/phases/phase-6.md D265): the net_worth_before_handover.yaml ledger read on each of its
days, „Anzahlung Kaufpreis“ and the paid-out loan stored as a month-end and read back with the
advance's label and notes, the missing payout leaving the month-end incomplete, and phase 9's
loan-to-value naming the advance as its source. Invented owners A and B, bank and amounts."""

import hashlib
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import fixture_models_phase6 as phase6
import pytest
from db.db_support import TEST_IBAN_KEY
from db.test_services_net_worth import _import
from sqlalchemy import insert

from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.schemas.allocation import AllocationMethod
from hausbuch.schemas.bank import AccountCreate
from hausbuch.schemas.common import Fraction as ShareFraction
from hausbuch.schemas.health import EstimateSource
from hausbuch.schemas.loan import LoanCreate
from hausbuch.schemas.net_worth import NetWorthLineKind, NetWorthTodoCode, SnapshotOutcome
from hausbuch.schemas.property import Address, OwnerCreate, OwnerShare, PropertyCreate
from hausbuch.services import health, net_worth
from hausbuch.services.accounts import create_account
from hausbuch.services.loans import create_loan
from hausbuch.services.owners import create_owner
from hausbuch.services.properties import create_property, set_ownership

pytestmark = pytest.mark.db
FIXTURE = phase6.load("net_worth_before_handover")
L = NetWorthLineKind
T = NetWorthTodoCode
NOW = datetime(2026, 11, 1, 5, 0, tzinfo=UTC)
# Every account's anchor lies after the last day asked, so no balance enters the figure and
# its total is the property's lines alone (the fixture's header).
ANCHOR = date(2026, 12, 31)
IBANS = {"joint_private": "DE02120300000000202051", "rental": "DE02500105170137075030"}


def _ledger(run, ws, without=()):
    owners = {
        name: run(ws, create_owner, OwnerCreate(full_name=f"Eigentümer {name}"), now=NOW).id
        for name in FIXTURE.owners
    }
    accounts = {
        a.id: run(
            ws,
            create_account,
            AccountCreate(
                label=a.id,
                bank="Beispielbank",
                iban=IBANS[a.id],
                kind=a.kind,
                purpose=a.purpose,
                opening_balance=Decimal("0.00"),
                opening_balance_on=ANCHOR,
            ),
            iban_key=TEST_IBAN_KEY,
            now=NOW,
        ).id
        for a in FIXTURE.accounts
    }
    f = FIXTURE.property
    prop = run(
        ws,
        create_property,
        PropertyCreate(
            label=f.label,
            kind="etw",
            address=Address(
                street="Musterweg", house_number="1", postal_code="10115", city="Berlin"
            ),
            build_year=1994,
            notarized_on=f.notarized_on,
            purchase_date=f.purchase_date,
            purchase_price=f.purchase_price,
        ),
        now=NOW,
    ).id
    run(
        ws,
        set_ownership,
        prop,
        [
            OwnerShare(
                owner_id=owners[name],
                share=ShareFraction(numerator=s.numerator, denominator=s.denominator),
            )
            for name, s in f.shares.items()
        ],
        now=NOW,
    )
    terms = FIXTURE.loan
    loan = run(
        ws,
        create_loan,
        LoanCreate(property_id=prop, **terms.model_dump()),
        iban_key=TEST_IBAN_KEY,
        now=NOW,
    ).id

    def booked(ctx):
        imports = {
            name: _import(ctx, account, date(2026, 5, 1), date(2026, 9, 30))
            for name, account in accounts.items()
        }
        for a in FIXTURE.allocations:
            if a.id in without:
                continue
            tx = ctx.conn.execute(
                insert(transaction_table)
                .values(
                    workspace_id=ctx.workspace_id,
                    account_id=accounts[a.account],
                    import_id=imports[a.account],
                    line_hash=hashlib.sha256(uuid.uuid4().bytes).digest(),
                    booking_date=a.booked,
                    value_date=a.booked,
                    amount=a.amount,
                    purpose="",
                    created_at=ctx.now,
                )
                .returning(transaction_table.c.id)
            ).scalar_one()
            ctx.conn.execute(
                insert(allocation_table).values(
                    workspace_id=ctx.workspace_id,
                    transaction_id=tx,
                    amount=a.amount,
                    category=a.category.value,
                    property_id=prop if a.property else None,
                    loan_id=loan if a.loan else None,
                    method=AllocationMethod.USER.value,
                    confidence=Decimal("1.000"),
                    status=a.status,
                    recorded_at=ctx.now,
                    confirmed_at=ctx.now if a.status == "confirmed" else None,
                )
            )

    run(ws, booked, now=NOW)
    return {"owners": owners, "property": prop, "loan": loan}


def _check(seen, case, ids):
    expect = case.expect
    lines = {line.kind: line for line in seen.lines}
    assert set(lines) <= {L.PROPERTY, L.LOAN}, case.name
    flat = lines.get(L.PROPERTY)
    if expect.property_line is None:
        assert flat is None, case.name
    else:
        advance = expect.property_line.kind == "advance"
        assert flat.property_id == ids["property"]
        assert flat.value_eur == expect.property_line.value_eur, case.name
        assert flat.estimate is not advance, case.name
        if advance:
            assert flat.label == f"Anzahlung Kaufpreis – {FIXTURE.property.label}"
            assert "Übergabe am 01.09.2026" in flat.note
        else:
            assert flat.label == FIXTURE.property.label
            assert "Kaufpreis" in flat.note  # no scenario: the price, an estimate
    loan = lines.get(L.LOAN)
    if expect.loan_line is None:
        assert loan is None, case.name
    else:
        assert (loan.loan_id, loan.property_id) == (ids["loan"], ids["property"])
        assert loan.value_eur == expect.loan_line.value_eur, case.name
        assert loan.estimate is expect.loan_line.estimate, case.name
    assert seen.property_net == expect.property_net, case.name
    assert seen.total == expect.property_net, case.name
    assert seen.by_owner == {ids["owners"][k]: v for k, v in expect.by_owner.items()}
    assert [t.code.value for t in seen.todos] == expect.todos, case.name


def test_the_fixture_ledger_is_read_on_each_day(run, read, fresh_workspaces):
    full, without = fresh_workspaces
    ids = {(): _ledger(run, full)}
    for case in FIXTURE.cases:
        key = tuple(case.without)
        if key not in ids:
            assert key == ("first_payout",)
            ids[key] = _ledger(run, without, without=key)
        ws = full if not key else without
        _check(read(ws, net_worth.net_worth, case.on), case, ids[key])

    # The handover-day invariant, read from the service rather than the fixture.
    inv = FIXTURE.invariant
    before = read(full, net_worth.net_worth, inv.before).property_net
    after = read(full, net_worth.net_worth, inv.after).property_net
    assert after == before + inv.own_money


def test_the_missing_payout_leaves_the_month_end_incomplete(run, read, fresh_workspaces):
    ws, _ = fresh_workspaces
    _ledger(run, ws, without=("first_payout",))
    seen = read(ws, net_worth.net_worth, date(2026, 5, 31))
    todo = next(t for t in seen.todos if t.code is T.LOAN_PAYOUT_MISSING)
    assert todo.text == (
        f"Die Auszahlung des Darlehens „{FIXTURE.loan.lender}“ ist nicht gebucht; bis zur "
        "Übergabe fehlt seine Restschuld im Nettovermögen."
    )
    assert (todo.action, todo.href) == ("Zu prüfen öffnen", "/inbox")
    assert [t.code for t in health._incomplete(seen)] == [T.LOAN_PAYOUT_MISSING]
    # The unvalued loan is not stored: the run holds the advance alone.
    result = run(ws, net_worth.capture_month_end, date(2026, 5, 31), now=NOW)
    assert result.outcome is SnapshotOutcome.WRITTEN and result.run.rows == 1


def test_a_month_end_before_the_handover_is_stored_and_read_back(run, read, fresh_workspaces):
    ws, _ = fresh_workspaces
    ids = _ledger(run, ws)
    may = date(2026, 5, 31)
    result = run(ws, net_worth.capture_month_end, may, now=NOW)
    assert result.outcome is SnapshotOutcome.WRITTEN and result.run.rows == 2
    again = run(ws, net_worth.capture_month_end, may, now=NOW)
    assert again.outcome is SnapshotOutcome.UNCHANGED

    stored = read(ws, net_worth.month_end_figure, result.run)
    case = next(c for c in FIXTURE.cases if c.on == may and not c.without)
    _check(stored, case, ids)
    lines = {line.kind: line for line in stored.lines}
    assert lines[L.PROPERTY].note.endswith("zum Monatsende gespeichert")
    assert "Bisher ausgezahlt" in lines[L.LOAN].note
    assert lines[L.LOAN].note.endswith("zum Monatsende gespeichert")
    assert (stored.property_estimate, stored.loan_balances) == (
        Decimal("165000.00"),
        Decimal("150000.00"),
    )

    # Phase 9's loan-to-value names the advance, not the purchase price, as its source.
    assert read(ws, health.estimate_sources, stored) == {ids["property"]: EstimateSource.ADVANCE}

    # After the handover the stored run reads as before D265: an estimate and the schedule.
    september = run(ws, net_worth.capture_month_end, date(2026, 9, 30), now=NOW)
    later = read(ws, net_worth.month_end_figure, september.run)
    by_kind = {line.kind: line for line in later.lines}
    assert by_kind[L.PROPERTY].label == FIXTURE.property.label
    assert by_kind[L.PROPERTY].note == net_worth.STORED_PROPERTY_NOTE
    assert by_kind[L.LOAN].note == net_worth.STORED_LOAN_NOTE
    assert by_kind[L.PROPERTY].estimate and by_kind[L.LOAN].estimate
    assert read(ws, health.estimate_sources, later) == {
        ids["property"]: EstimateSource.PURCHASE_PRICE
    }
