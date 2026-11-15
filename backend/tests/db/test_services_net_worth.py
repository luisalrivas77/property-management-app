"""services/net_worth.py as hausbuch_app under RLS (docs/phases/phase-6.md D167 (4), (5), §9,
§13; phase 6 step 6): the net_worth.yaml month-end built as a ledger and read to the euro,
the reconciled cap of a balance, a credit card's debt, owner shares as of a day, and the
month-end runs: one row per account balance and position, append-only, a re-capture only
when the figures changed. Invented owners A and B, banks, IBANs and prices only."""

import hashlib
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from fractions import Fraction

import fixture_models_phase6 as phase6
import pytest
from db.db_support import TEST_IBAN_KEY
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import DBAPIError

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import document as document_table
from hausbuch.db.tables import document_file as file_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import holding_statement as holding_table
from hausbuch.db.tables import price as price_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.db.tables import valuation_snapshot as snapshot_table
from hausbuch.jobs.document_read import JobDeps
from hausbuch.jobs.month_end import run_month_end
from hausbuch.schemas.bank import AccountCreate
from hausbuch.schemas.common import Fraction as ShareFraction
from hausbuch.schemas.enums import AccountKind, ImportStatus
from hausbuch.schemas.events import EventType
from hausbuch.schemas.household import NetWorthClass, OwnershipShare, SetOwnershipBody
from hausbuch.schemas.loan import LoanCreate
from hausbuch.schemas.net_worth import NetWorthLineKind, NetWorthTodoCode, SnapshotOutcome
from hausbuch.schemas.performance import Levers, ScenarioCreate
from hausbuch.schemas.property import Address, OwnerCreate, OwnerShare, PropertyCreate
from hausbuch.services import net_worth
from hausbuch.services.accounts import create_account, set_account_ownership
from hausbuch.services.loans import create_loan, update_loan
from hausbuch.services.owners import create_owner
from hausbuch.services.properties import create_property, set_ownership
from hausbuch.services.scenarios import create_scenario

pytestmark = pytest.mark.db
C = NetWorthClass
T = NetWorthTodoCode
FIXTURE = phase6.load("net_worth")
ON = FIXTURE.on  # 2026-10-31
NOW = datetime(2026, 11, 1, 5, 0, tzinfo=UTC)
JOINT_IBAN = "DE02120300000000202051"
SAVINGS_IBAN = "DE02500105170137075030"


# --- the ledger -----------------------------------------------------------------------------


def _document(ctx):
    sha = hashlib.sha256(uuid.uuid4().bytes).digest()
    ctx.conn.execute(
        insert(file_table).values(
            workspace_id=ctx.workspace_id,
            sha256=sha,
            size_bytes=1,
            media_type="application/pdf",
            storage_key=f"test/{sha.hex()}",
            created_at=ctx.now,
        )
    )
    return ctx.conn.execute(
        insert(document_table)
        .values(
            workspace_id=ctx.workspace_id,
            file_sha256=sha,
            source="upload",
            type="statement",
            received_at=ctx.now,
            created_at=ctx.now,
            updated_at=ctx.now,
        )
        .returning(document_table.c.id)
    ).scalar_one()


def _import(ctx, account_id, first, last, status=ImportStatus.RECONCILED):
    return ctx.conn.execute(
        insert(import_table)
        .values(
            workspace_id=ctx.workspace_id,
            account_id=account_id,
            document_id=_document(ctx),
            format="camt",
            status=status.value,
            # A reconciled import's declared and computed closing agree (0006's CHECK); the
            # figure itself is not read here.
            balance_source="none" if status is ImportStatus.UNRECONCILED else "file",
            declared_closing=None if status is ImportStatus.UNRECONCILED else Decimal("0.00"),
            computed_closing=Decimal("0.00"),
            error_code="statement.unreconciled" if status is ImportStatus.UNRECONCILED else None,
            first_booking_on=first,
            last_booking_on=last,
            created_at=ctx.now,
            updated_at=ctx.now,
        )
        .returning(import_table.c.id)
    ).scalar_one()


def _line(ctx, account_id, import_id, booked, amount):
    ctx.conn.execute(
        insert(transaction_table).values(
            workspace_id=ctx.workspace_id,
            account_id=account_id,
            import_id=import_id,
            line_hash=hashlib.sha256(uuid.uuid4().bytes).digest(),
            booking_date=booked,
            value_date=booked,
            amount=Decimal(amount),
            purpose="",
            created_at=ctx.now,
        )
    )


def _asset(ctx, **values):
    return ctx.conn.execute(
        insert(asset_table)
        .values(workspace_id=ctx.workspace_id, created_at=ctx.now, updated_at=ctx.now, **values)
        .returning(asset_table.c.id)
    ).scalar_one()


def _held(ctx, account_id, asset_id, units):
    ctx.conn.execute(
        insert(movement_table).values(
            workspace_id=ctx.workspace_id,
            account_id=account_id,
            asset_id=asset_id,
            kind="acquisition",
            quantity=Decimal(units),
            occurred_at=datetime(2026, 7, 1, 9, 0, tzinfo=UTC),
            source="user",
            status="confirmed",
            created_at=ctx.now,
        )
    )


def _price(ctx, asset_id, on, price, source):
    ctx.conn.execute(
        insert(price_table).values(
            workspace_id=ctx.workspace_id,
            asset_id=asset_id,
            on=on,
            price_eur=Decimal(price),
            source=source,
            fetched_at=ctx.now,
            raw={},
            created_at=ctx.now,
        )
    )


def _account(run, ws, label, kind, *, iban=None, opening="0.00", on=date(2026, 6, 30)):
    return run(
        ws,
        create_account,
        AccountCreate(
            label=label,
            bank="Beispielbank",
            iban=iban,
            kind=kind,
            purpose="private",
            provider="Binance" if kind is AccountKind.EXCHANGE else None,
            opening_balance=Decimal(opening),
            opening_balance_on=on,
        ),
        iban_key=TEST_IBAN_KEY,
        now=NOW,
    ).id


def _own(run, ws, account_id, shares, effective_from=date(2026, 1, 1), now=NOW):
    run(
        ws,
        set_account_ownership,
        account_id,
        SetOwnershipBody(
            shares=[
                OwnershipShare(
                    owner_id=o, share_numerator=s.numerator, share_denominator=s.denominator
                )
                for o, s in shares.items()
            ],
            effective_from=effective_from,
        ),
        now=now,
    )


def _ledger(run, ws):
    """net_worth.yaml as stored rows: the joint account's balance from its anchor and a
    reconciled import, the savings account at its anchor, the depot's two funds and the
    exchange's coins and euros by their movements and prices, the flat at its purchase price
    (no scenario) and its loan before its first payment (the principal)."""
    owners = {
        name: run(ws, create_owner, OwnerCreate(full_name=f"Eigentümer {name}"), now=NOW).id
        for name in FIXTURE.owners
    }
    halves = {owners["A"]: Fraction(1, 2), owners["B"]: Fraction(1, 2)}
    joint = _account(
        run, ws, "Gemeinschaftskonto", AccountKind.BANK_CURRENT, iban=JOINT_IBAN, opening="4000.00"
    )
    savings = _account(
        run,
        ws,
        "Tagesgeld A",
        AccountKind.BANK_SAVINGS,
        iban=SAVINGS_IBAN,
        opening="10000.00",
        on=ON,
    )
    depot = _account(run, ws, "Depot", AccountKind.DEPOT)
    exchange = _account(run, ws, "Börse B", AccountKind.EXCHANGE)
    _own(run, ws, joint, halves)
    _own(run, ws, savings, {owners["A"]: Fraction(1)})
    _own(run, ws, depot, halves)
    _own(run, ws, exchange, {owners["B"]: Fraction(1)})

    def stored(ctx):
        imported = _import(ctx, joint, date(2026, 7, 1), ON)
        _line(ctx, joint, imported, date(2026, 6, 30), "999.00")  # in the anchor already
        _line(ctx, joint, imported, date(2026, 10, 15), "250.00")
        _line(ctx, joint, imported, ON, "-50.00")
        ids = {}
        for account in FIXTURE.accounts:
            target = {"depot": depot, "exchange_b": exchange}.get(account.id)
            for p in account.positions:
                if p.cash:
                    asset = _asset(ctx, kind="cash", symbol=p.asset, name="Euro", currency="EUR")
                elif p.asset.startswith("IE"):
                    asset = _asset(ctx, kind="etf", isin=p.asset, name=f"Fonds {p.asset[-2:]}")
                else:
                    asset = _asset(ctx, kind="crypto", symbol=p.asset, name=p.asset)
                _held(ctx, target, asset, str(p.units))
                if not p.cash:
                    _price(ctx, asset, p.price_on, str(p.price_eur), p.source.value)
                ids[p.asset] = asset
        return ids

    assets = run(ws, stored, now=NOW)
    prop = run(
        ws,
        create_property,
        PropertyCreate(
            label="Wohnung Beispielweg",
            kind="etw",
            address=Address(
                street="Beispielweg", house_number="1", postal_code="10115", city="Berlin"
            ),
            build_year=1994,
            notarized_on=date(2025, 11, 20),
            purchase_date=date(2026, 1, 15),
            purchase_price=FIXTURE.property.estimate,
        ),
        now=NOW,
    ).id
    run(
        ws,
        set_ownership,
        prop,
        [
            OwnerShare(
                owner_id=o,
                share=ShareFraction(numerator=s.numerator, denominator=s.denominator),
            )
            for o, s in halves.items()
        ],
        now=NOW,
    )
    loan = run(
        ws,
        create_loan,
        LoanCreate(
            property_id=prop,
            lender="Beispielbank",
            principal=FIXTURE.property.loan_balance,
            nominal_rate=Decimal("0.035"),
            monthly_payment=Decimal("1000.00"),
            disbursed_on=date(2026, 1, 10),
            first_payment_on=date(2026, 12, 30),
            interest_only_before_first_payment=True,
        ),
        iban_key=TEST_IBAN_KEY,
        now=NOW,
    ).id
    return {
        "owners": owners,
        "accounts": {
            "joint_current": joint,
            "savings_a": savings,
            "depot": depot,
            "exchange_b": exchange,
        },
        "assets": assets,
        "property": prop,
        "loan": loan,
    }


def _snapshot_rows(read, ws):
    s = snapshot_table.c
    return read(
        ws,
        lambda conn: conn.execute(
            select(
                s.net_worth_run_id,
                s.account_id,
                s.asset_id,
                s.property_id,
                s.loan_id,
                s.value_eur,
                s.price_source,
            )
        ).all(),
    )


def _events(read, ws, type):
    e = event_table.c
    return read(
        ws,
        lambda conn: conn.execute(select(e.payload).where(e.type == type.value)).scalars().all(),
    )


# --- on read --------------------------------------------------------------------------------


def test_the_fixture_ledger_is_read_to_the_euro(run, read, fresh_workspaces):
    ws, other = fresh_workspaces
    ids = _ledger(run, ws)
    seen = read(ws, net_worth.net_worth, ON)
    expect = FIXTURE.expect
    assert seen.by_class == expect.by_class
    assert seen.by_owner == {ids["owners"][k]: v for k, v in expect.by_owner.items()}
    assert seen.total == expect.total
    assert seen.unassigned == []
    assert (seen.property_estimate, seen.loan_balances) == (
        Decimal("320000.00"),
        Decimal("240000.00"),
    )
    assert seen.property_net == Decimal("80000.00")

    accounts = {v: k for k, v in ids["accounts"].items()}
    assets = {v: k for k, v in ids["assets"].items()}
    valued = {
        (accounts[line.account_id], assets.get(line.asset_id)): line.value_eur
        for line in seen.lines
        if line.kind in (NetWorthLineKind.ACCOUNT, NetWorthLineKind.POSITION)
    }
    assert valued == {(s.account, s.asset): s.value_eur for s in expect.snapshots}
    joint = next(line for line in seen.lines if line.account_id == ids["accounts"]["joint_current"])
    assert (joint.as_of, joint.stale) == (ON, False)
    flat = next(line for line in seen.lines if line.kind is NetWorthLineKind.PROPERTY)
    assert flat.estimate and "Kaufpreis" in flat.note
    # The statement price of 30 Sep is a month old: valued, flagged, a to-do.
    assert seen.stale_positions == 1 and seen.unvalued_positions == 0
    assert [t.code for t in seen.todos] == [T.PRICE_STALE]
    assert seen.todos[0].href == "/assets"

    # Another workspace sees none of it (RLS).
    empty = read(other, net_worth.net_worth, ON)
    assert empty.total == 0 and empty.lines == [] and empty.by_owner == {}


def test_a_balance_stops_at_its_last_reconciled_statement(run, read, fresh_workspaces):
    ws, _ = fresh_workspaces
    owner = run(ws, create_owner, OwnerCreate(full_name="Eigentümer A"), now=NOW).id
    account = _account(run, ws, "Girokonto", AccountKind.BANK_CURRENT, iban=JOINT_IBAN)
    card = _account(run, ws, "Kreditkarte", AccountKind.CREDIT_CARD, opening="-100.00")
    depot = _account(run, ws, "Depot", AccountKind.DEPOT)
    _own(run, ws, card, {owner: Fraction(1)})

    def stored(ctx):
        reconciled = _import(ctx, account, date(2026, 7, 1), date(2026, 10, 20))
        _line(ctx, account, reconciled, date(2026, 10, 1), "500.00")
        waiting = _import(
            ctx, account, date(2026, 10, 21), date(2026, 10, 25), ImportStatus.UNRECONCILED
        )
        _line(ctx, account, waiting, date(2026, 10, 25), "-80.00")
        statement = _import(ctx, card, date(2026, 7, 1), ON)
        _line(ctx, card, statement, date(2026, 10, 10), "-212.75")

    run(ws, stored, now=NOW)
    seen = read(ws, net_worth.net_worth, ON)
    by_account = {line.account_id: line for line in seen.lines}
    girokonto = by_account[account]
    assert (girokonto.value_eur, girokonto.as_of, girokonto.stale) == (
        Decimal("500.00"),
        date(2026, 10, 20),
        True,  # 11 days before the day asked
    )
    assert by_account[card].value_eur == Decimal("-312.75")
    assert by_account[card].net_worth_class is C.LIABILITIES
    assert depot not in by_account  # no cash statements, no balance line
    assert seen.by_class[C.LIABILITIES] == Decimal("-312.75")
    assert seen.by_owner == {owner: Decimal("-312.75")}
    assert seen.unassigned == [account]
    codes = [t.code for t in seen.todos]
    assert codes == [T.BALANCE_UNRECONCILED, T.OWNERSHIP_MISSING]
    assert "20.10.2026" in seen.todos[0].text and seen.todos[0].href == "/inbox"
    assert seen.todos[1].action == "Eigentümer festlegen"
    assert seen.todos[1].href == f"/accounts/{account}"
    # Before the anchor day the account has no figure that day.
    assert account not in {
        line.account_id for line in read(ws, net_worth.net_worth, date(2026, 6, 1)).lines
    }


def test_a_balance_reconciled_only_to_the_previous_month_is_a_to_do(run, read, fresh_workspaces):
    """Review of 15 Nov 2026, finding 2: statements up to 30 Sep, nothing waiting after them,
    the figure asked for 31 Oct. The balance stays the September one, shown with its day, and
    a to-do asks for the next statement on the account's Kontoauszüge tab (a card's for its
    Abrechnung). A balance a few days old is no to-do."""
    ws, _ = fresh_workspaces
    owner = run(ws, create_owner, OwnerCreate(full_name="Eigentümer A"), now=NOW).id
    account = _account(run, ws, "Girokonto", AccountKind.BANK_CURRENT, iban=JOINT_IBAN)
    card = _account(run, ws, "Kreditkarte", AccountKind.CREDIT_CARD)
    for own in (account, card):
        _own(run, ws, own, {owner: Fraction(1)})

    def stored(ctx):
        statement = _import(ctx, account, date(2026, 7, 1), date(2026, 9, 30))
        _line(ctx, account, statement, date(2026, 9, 30), "750.00")
        billed = _import(ctx, card, date(2026, 9, 1), date(2026, 10, 27))
        _line(ctx, card, billed, date(2026, 10, 27), "-40.00")

    run(ws, stored, now=NOW)
    seen = read(ws, net_worth.net_worth, ON)
    line = next(line for line in seen.lines if line.account_id == account)
    assert (line.value_eur, line.as_of, line.stale) == (Decimal("750.00"), date(2026, 9, 30), True)
    assert seen.total == Decimal("710.00")
    [todo] = seen.todos  # the card's statement of 27 Oct is 4 days old: none for it
    assert todo.code is T.BALANCE_OUTDATED and "30.09.2026" in todo.text
    assert (todo.action, todo.href) == (
        "Kontoauszug hochladen",
        f"/accounts/{account}?tab=kontoauszuege",
    )
    later = read(ws, net_worth.net_worth, date(2026, 11, 10))
    assert {(t.code, t.action) for t in later.todos} == {
        (T.BALANCE_OUTDATED, "Kontoauszug hochladen"),
        (T.BALANCE_OUTDATED, "Kreditkartenabrechnung hochladen"),
    }


def test_a_fund_the_report_lists_with_nothing_booked_is_listed_unvalued(
    run, read, fresh_workspaces
):
    """Review of 15 Nov 2026, finding 1: cash paid in, a purchase debited on the depot's cash
    statement, the quarterly report declares the fund's units, and no trade is booked. The
    Anlagen overview has no position for it; the net worth lists it unvalued with a to-do to
    the holdings card, and the total is the cash only (never the report's units, D167 (b))."""
    ws, _ = fresh_workspaces
    owner = run(ws, create_owner, OwnerCreate(full_name="Eigentümer A"), now=NOW).id
    depot = _account(run, ws, "Depot", AccountKind.DEPOT)
    _own(run, ws, depot, {owner: Fraction(1)})

    def stored(ctx):
        statement = _import(ctx, depot, date(2026, 7, 1), date(2026, 9, 30))
        _line(ctx, depot, statement, date(2026, 7, 1), "5000.00")
        _line(ctx, depot, statement, date(2026, 7, 2), "-4000.00")
        fund = _asset(ctx, kind="etf", isin="IE00BEISPL09", name="Fonds Welt")
        ctx.conn.execute(
            insert(holding_table).values(
                workspace_id=ctx.workspace_id,
                account_id=depot,
                asset_id=fund,
                as_of=date(2026, 9, 30),
                declared_units=Decimal("10"),
                source="report",
                created_at=ctx.now,
            )
        )
        return fund

    fund = run(ws, stored, now=NOW)
    seen = read(ws, net_worth.net_worth, ON)
    assert seen.total == Decimal("1000.00") and seen.by_class[C.DEPOT] == Decimal("1000.00")
    assert seen.unvalued_positions == 1
    [line] = [line for line in seen.lines if line.asset_id == fund]
    assert (line.kind, line.value_eur, line.account_id) == (NetWorthLineKind.POSITION, None, depot)
    assert "10 Anteile" in line.note
    [todo] = [t for t in seen.todos if t.code is T.POSITION_NOT_BOOKED]
    assert "Fonds Welt" in todo.text and "Umsätze aus dem Bericht buchen" in todo.text
    assert todo.href == "/inbox"
    # The month-end stores the cash only and says one position is missing.
    captured = run(ws, net_worth.capture_month_end, ON, now=NOW)
    assert captured.run.rows == 1 and captured.unvalued_positions == 1
    # A report dated after the day asked does not reach back.
    before = read(ws, net_worth.net_worth, date(2026, 9, 29))
    assert before.unvalued_positions == 0 and fund not in {line.asset_id for line in before.lines}


def test_owner_shares_follow_the_set_in_force_on_the_day(run, read, fresh_workspaces):
    ws, _ = fresh_workspaces
    a = run(ws, create_owner, OwnerCreate(full_name="Eigentümer A"), now=NOW).id
    b = run(ws, create_owner, OwnerCreate(full_name="Eigentümer B"), now=NOW).id
    account = _account(
        run, ws, "Girokonto", AccountKind.BANK_CURRENT, iban=JOINT_IBAN, opening="100.01"
    )
    _own(run, ws, account, {a: Fraction(1)}, date(2026, 1, 1), NOW)
    _own(
        run,
        ws,
        account,
        {a: Fraction(1, 2), b: Fraction(1, 2)},
        date(2026, 11, 2),
        NOW.replace(hour=6),
    )

    before = read(ws, net_worth.shares_on, [account], date(2026, 11, 1))
    after = read(ws, net_worth.shares_on, [account], date(2026, 11, 2))
    assert before == {account: {a: Fraction(1)}}
    assert after == {account: {a: Fraction(1, 2), b: Fraction(1, 2)}}
    split = read(ws, net_worth.net_worth, date(2026, 11, 2)).by_owner
    # To the cent, summing up: the cent left over goes to the first owner by id.
    first, second = sorted((a, b), key=str)
    assert split == {first: Decimal("50.01"), second: Decimal("50.00")}


# --- month-ends -----------------------------------------------------------------------------


def test_a_month_end_is_stored_once_and_recaptured_only_when_it_changed(
    run, read, fresh_workspaces
):
    ws, other = fresh_workspaces
    ids = _ledger(run, ws)
    first = run(ws, net_worth.capture_month_end, ON, now=NOW)
    # Seven account rows (two balances, five positions), the flat and its loan.
    assert first.outcome is SnapshotOutcome.WRITTEN and first.run.rows == 9
    rows = _snapshot_rows(read, ws)
    assert len(rows) == 9 and {r.net_worth_run_id for r in rows} == {first.run.net_worth_run_id}
    assert {(r.property_id, r.value_eur) for r in rows if r.account_id is None} == {
        (ids["property"], Decimal("320000.00")),
        (None, Decimal("-240000.00")),
    }
    assert [r.loan_id for r in rows if r.loan_id is not None] == [ids["loan"]]
    balances = [r for r in rows if r.account_id is not None and r.asset_id is None]
    assert {r.account_id for r in balances} == {
        ids["accounts"]["joint_current"],
        ids["accounts"]["savings_a"],
    }
    assert all(r.price_source is None for r in balances)
    [payload] = _events(read, ws, EventType.WORKSPACE_VALUATION_SNAPSHOT_WRITTEN)
    assert payload == {
        "net_worth_run_id": str(first.run.net_worth_run_id),
        "as_of": ON.isoformat(),
        "rows": 9,
    }

    # The same figures again: nothing written, by the task or by the button.
    again = run(ws, net_worth.capture_month_end, ON, now=NOW.replace(hour=9))
    assert again.outcome is SnapshotOutcome.UNCHANGED and again.run == first.run
    task = run(ws, net_worth.capture_month_end, ON, only_if_missing=True, now=NOW)
    assert task.outcome is SnapshotOutcome.UNCHANGED
    assert len(_snapshot_rows(read, ws)) == 9

    # A price typed for the month-end after the capture: the button stores a new run, the
    # task still does not, and the old run stays.
    run(
        ws,
        lambda ctx: _price(ctx, ids["assets"]["IE00BEISPL02"], ON, "41.00", "user"),
        now=NOW,
    )
    assert run(ws, net_worth.capture_month_end, ON, only_if_missing=True, now=NOW).outcome is (
        SnapshotOutcome.UNCHANGED
    )
    later = run(ws, net_worth.capture_month_end, ON, now=NOW.replace(hour=10))
    assert later.outcome is SnapshotOutcome.RECAPTURED and later.run.rows == 9
    assert len(_snapshot_rows(read, ws)) == 18
    assert len(_events(read, ws, EventType.WORKSPACE_VALUATION_SNAPSHOT_WRITTEN)) == 2

    runs = read(ws, net_worth.list_runs)
    assert [r.net_worth_run_id for r in runs] == [
        later.run.net_worth_run_id,
        first.run.net_worth_run_id,
    ]
    stored = read(ws, net_worth.latest_month_end)
    assert stored.run.net_worth_run_id == later.run.net_worth_run_id
    assert stored.total == FIXTURE.expect.total + Decimal("20.00")  # 20 units x 1.00 more
    assert stored.by_class[C.PROPERTY] == Decimal("320000.00")
    assert stored.by_class[C.LIABILITIES] == Decimal("-240000.00")

    view = read(ws, net_worth.overview, date(2026, 11, 15))
    assert view.month_end.run == stored.run and view.month_end_due is None
    assert read(ws, net_worth.overview, date(2026, 12, 1)).month_end_due == date(2026, 11, 30)

    # Other workspace: no runs.
    assert read(other, net_worth.list_runs) == []
    assert read(other, net_worth.latest_month_end) is None


def test_a_stored_month_end_is_read_from_its_rows_alone(run, read, fresh_workspaces):
    """D167 (5) amended (migration 0040): after the capture the flat's value and the loan
    change; the figure on read follows them, the stored month-end does not."""
    ws, _ = fresh_workspaces
    ids = _ledger(run, ws)
    run(ws, net_worth.capture_month_end, ON, now=NOW)
    before = read(ws, net_worth.latest_month_end)
    run(
        ws,
        create_scenario,
        ids["property"],
        ScenarioCreate(
            property_id=ids["property"],
            name="Höherer Wert",
            is_default=True,
            levers=Levers(
                value_growth=Decimal("0.05"), sale_year=2036, today_value=Decimal("400000.00")
            ),
        ),
        now=NOW,
    )
    run(
        ws,
        update_loan,
        ids["loan"],
        first_payment_on=date(2026, 2, 28),
        monthly_payment=Decimal("2000.00"),
        now=NOW,
    )
    now_read = read(ws, net_worth.net_worth, ON)
    assert now_read.property_estimate == Decimal("400000.00")
    assert now_read.loan_balances < Decimal("240000.00")

    after = read(ws, net_worth.latest_month_end)
    assert after.run == before.run
    assert (after.total, after.by_class, after.by_owner) == (
        before.total,
        before.by_class,
        before.by_owner,
    )
    assert (after.property_estimate, after.loan_balances, after.property_net) == (
        Decimal("320000.00"),
        Decimal("240000.00"),
        Decimal("80000.00"),
    )
    flat = next(line for line in after.lines if line.kind is NetWorthLineKind.PROPERTY)
    loan = next(line for line in after.lines if line.kind is NetWorthLineKind.LOAN)
    assert flat.estimate and flat.property_id == ids["property"]
    assert loan.estimate and (loan.loan_id, loan.property_id) == (ids["loan"], ids["property"])
    # A button press now stores the new figures as a new run; the old run stays.
    later = run(ws, net_worth.capture_month_end, ON, now=NOW.replace(hour=11))
    assert later.outcome is SnapshotOutcome.RECAPTURED
    assert read(ws, net_worth.latest_month_end).property_estimate == Decimal("400000.00")
    assert read(ws, net_worth.stored_run, before.run.net_worth_run_id).total == before.total


def test_a_stored_month_end_cannot_be_changed_or_deleted(run, read, app_engine, fresh_workspaces):
    ws, _ = fresh_workspaces
    _ledger(run, ws)
    run(ws, net_worth.capture_month_end, ON, now=NOW)
    for statement in (
        update(snapshot_table).values(value_eur=Decimal("1.00")),
        delete(snapshot_table),
    ):
        with pytest.raises(DBAPIError), workspace_transaction(app_engine, ws) as conn:
            conn.execute(statement)
    assert (
        read(
            ws, lambda conn: conn.execute(select(func.count()).select_from(snapshot_table)).scalar()
        )
        == 9
    )


def test_nothing_to_store_writes_nothing(run, read, fresh_workspaces):
    ws, _ = fresh_workspaces
    result = run(ws, net_worth.capture_month_end, ON, now=NOW)
    assert result.outcome is SnapshotOutcome.EMPTY and result.run is None
    assert _snapshot_rows(read, ws) == []
    assert _events(read, ws, EventType.WORKSPACE_VALUATION_SNAPSHOT_WRITTEN) == []


def test_the_button_stores_the_last_month_end_in_berlin_time(run, read, fresh_workspaces):
    ws, _ = fresh_workspaces
    _ledger(run, ws)
    # 23:30 UTC on 30 Nov is already 1 Dec in Berlin: the month-end is 30 Nov.
    result = run(
        ws, net_worth.capture_last_month_end, now=datetime(2026, 11, 30, 23, 30, tzinfo=UTC)
    )
    assert result.as_of == date(2026, 11, 30) and result.outcome is SnapshotOutcome.WRITTEN


def test_the_month_end_task_stores_each_workspace_once(run, read, app_engine, fresh_workspaces):
    ws, empty = fresh_workspaces
    _ledger(run, ws)
    deps = JobDeps(
        engine=app_engine,
        settings=None,
        blob_store=None,
        model_client=None,
        clock=lambda: datetime(2026, 11, 1, 5, 0, 3, tzinfo=UTC),
    )
    first = run_month_end(deps, workspace_ids=[ws, empty])
    assert first.as_of == "2026-10-31"
    assert first.outcomes == {"written": 1, "empty": 1} and first.failed_workspaces == 0
    again = run_month_end(deps, workspace_ids=[ws, empty])
    assert again.outcomes == {"unchanged": 1, "empty": 1}
    assert len(_snapshot_rows(read, ws)) == 9 and _snapshot_rows(read, empty) == []
    [stored] = read(ws, net_worth.list_runs)
    assert stored.as_of == ON
    e = event_table.c
    actors = read(
        ws,
        lambda conn: conn.execute(
            select(e.actor_type, e.actor_id).where(
                e.type == EventType.WORKSPACE_VALUATION_SNAPSHOT_WRITTEN.value
            )
        ).all(),
    )
    assert [tuple(a) for a in actors] == [("system", "month_end")]
