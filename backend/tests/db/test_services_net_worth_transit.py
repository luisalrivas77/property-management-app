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
from sqlalchemy import insert, update

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.net_worth import AccountTab, NetWorthLineKind, SnapshotOutcome
from hausbuch.services import allocations, health, net_worth, transfers
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
    # The Konten tabs count the money on its way in the depot's tab (§7.6 Konten (2)).
    assert seen.by_account_tab == {
        AccountTab.BANK: Decimal("500.00"),
        AccountTab.DEPOT: Decimal("700.00"),
    }
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


CARD_PAY_IBAN = "DE73100000000000000072"


def _card_world(run, ws):
    """D292 amended (a): a current account of 1,000 € that paid the card 300 € on 1 Oct and
    250 € on 30 Oct (both booked as transfers); the card's statements reach only its September
    statement, closing at -300 € on 25 Sep (the reviewer's case of PR #551)."""
    current = _account(
        run, ws, "Girokonto", AccountKind.BANK_CURRENT, iban=CURRENT_IBAN, opening="1000.00"
    )
    card = _account(run, ws, "Karte", AccountKind.CREDIT_CARD)

    def stored(ctx):
        paid_to = protect_iban(CARD_PAY_IBAN, TEST_IBAN_KEY)
        ctx.conn.execute(
            update(account_table)
            .where(account_table.c.id == card)
            .values(payment_iban_masked=paid_to.masked, payment_iban_hmac=paid_to.hmac)
        )
        bank = _import(ctx, current, date(2026, 7, 1), ON)
        first = _line(ctx, current, bank, date(2026, 10, 1), "-300.00", iban=CARD_PAY_IBAN)
        second = _line(ctx, current, bank, date(2026, 10, 30), "-250.00", iban=CARD_PAY_IBAN)
        september = _import(ctx, card, date(2026, 8, 26), date(2026, 9, 25), closing="-300.00")
        _line(ctx, card, september, date(2026, 9, 20), "-300.00")
        return first, second

    first, second = run(ws, stored, now=NOW)
    _book(run, ws, first, "-300.00", C.INTERNAL_TRANSFER_OUT)
    _book(run, ws, second, "-250.00", C.INTERNAL_TRANSFER_OUT)
    return current, card


def test_money_in_transit_to_a_card_offsets_only_its_known_debt(run, read, fresh_workspaces):
    """The 300 € of 1 Oct pays the September statement's debt; the 250 € of 30 Oct paid charges
    on a statement not yet in the app, so it is not counted: the card is 0, never an asset,
    and the net worth 450 € (the reviewer's repro had the card at +250 €). The month-end row,
    the reserve and health-check read the same capped figure."""
    ws, _ = fresh_workspaces
    current, card = _card_world(run, ws)

    seen = read(ws, net_worth.net_worth, ON)
    [moving] = _moving(seen)
    assert (moving.account_id, moving.value_eur) == (card, Decimal("300.00"))
    assert "01.10.2026" in moving.note
    merged = {line.account_id: line.value_eur for line in net_worth.merge_transit(seen.lines)}
    assert merged == {current: Decimal("450.00"), card: Decimal("0.00")}
    assert seen.total == Decimal("450.00")

    reserve = read(ws, lambda conn: health._reserve_balances(conn, seen))
    assert {r.account_id: r.balance for r in reserve} == merged

    first = run(ws, net_worth.capture_month_end, ON, now=NOW)
    assert first.outcome is SnapshotOutcome.WRITTEN
    rows = {r.account_id: r.value_eur for r in _snapshot_rows(read, ws)}
    assert rows == merged
    assert read(ws, net_worth.latest_month_end).total == Decimal("450.00")
    stored = read(ws, net_worth.latest_month_end)
    checks = health._input_checks("2026-10", stored, seen)
    # Each account's stored row equals the reading folded the same way (the card's missing
    # October statement is the reading's own gap to-do, not an account line).
    assert {c.account_id: c.agrees for c in checks if c.account_id is not None} == {
        current: True,
        card: True,
    }


def test_a_card_payment_counted_in_part_says_so(run, read, fresh_workspaces):
    """A card debt of 300 € and payments of 200 € and 250 €: the first counts in full, the
    second only the 100 € the debt leaves room for, its note naming the cap."""
    ws, _ = fresh_workspaces
    current = _account(
        run, ws, "Girokonto", AccountKind.BANK_CURRENT, iban=CURRENT_IBAN, opening="1000.00"
    )
    card = _account(run, ws, "Karte", AccountKind.CREDIT_CARD)

    def stored(ctx):
        paid_to = protect_iban(CARD_PAY_IBAN, TEST_IBAN_KEY)
        ctx.conn.execute(
            update(account_table)
            .where(account_table.c.id == card)
            .values(payment_iban_masked=paid_to.masked, payment_iban_hmac=paid_to.hmac)
        )
        bank = _import(ctx, current, date(2026, 7, 1), ON)
        first = _line(ctx, current, bank, date(2026, 10, 1), "-200.00", iban=CARD_PAY_IBAN)
        second = _line(ctx, current, bank, date(2026, 10, 30), "-250.00", iban=CARD_PAY_IBAN)
        september = _import(ctx, card, date(2026, 8, 26), date(2026, 9, 25), closing="-300.00")
        _line(ctx, card, september, date(2026, 9, 20), "-300.00")
        return first, second

    first, second = run(ws, stored, now=NOW)
    _book(run, ws, first, "-200.00", C.INTERNAL_TRANSFER_OUT)
    _book(run, ws, second, "-250.00", C.INTERNAL_TRANSFER_OUT)
    seen = read(ws, net_worth.net_worth, ON)
    full, part = _moving(seen)
    assert (full.value_eur, part.value_eur) == (Decimal("200.00"), Decimal("100.00"))
    assert "soweit es die bekannte Schuld der Karte tilgt" in part.note
    assert "Schuld" not in full.note
    assert seen.total == Decimal("550.00")  # 550 on the current account, the card at 0


def test_the_etf_tab_adds_the_depots_cash_and_money_on_its_way(run, read, fresh_workspaces):
    """23 Nov 2026 (the quirion portfolios still read lower than in quirion's own app): a
    portfolio on the Anlagen ETF tab shows its funds, its cash balance and the money on its way
    as the net worth counts them, and their total, so it reads as the depot's own app does."""
    from db.test_services_prices_fetch import _asset, _held
    from db.test_services_prices_overview import S, _price

    from hausbuch.services.prices import overview

    ws, _ = fresh_workspaces
    _, depot, _ = _world(run, ws)

    def fund(ctx):
        asset = _asset(ctx, kind="etf", isin="IE000CASH001", name="Ein Fonds")
        _held(ctx, depot, asset, "4")
        _price(ctx, asset, ON, "25.00", S.USER)

    run(ws, fund, now=NOW)
    tab = read(ws, overview.etf_today, ON)
    [account] = tab.accounts
    assert account.value_eur == Decimal("100.00")  # the funds alone, as before
    assert [(line.in_transit, line.value_eur) for line in account.cash] == [
        (False, Decimal("200.00")),
        (True, Decimal("500.00")),
    ]
    assert account.total_eur == Decimal("800.00")
    seen = read(ws, net_worth.net_worth, ON)
    assert account.total_eur == sum(
        (line.value_eur for line in seen.lines if line.account_id == depot), Decimal(0)
    )

    # The Anlagen Übersicht tab shows the same parts and total (Luis, 23 Nov 2026: „the
    # Overview tab doesn't include those missing values“), and its tab total adds them
    # under Bargeld; the net worth itself still counts the positions and the cash once.
    over = read(ws, overview.assets_overview, ON)
    [row] = [a for a in over.accounts if a.account_id == depot]
    assert row.value_eur == Decimal("100.00")
    assert [(line.in_transit, line.value_eur) for line in row.cash] == [
        (False, Decimal("200.00")),
        (True, Decimal("500.00")),
    ]
    assert row.total_eur == account.total_eur
    assert over.total_eur == sum(a.total_eur for a in over.accounts)
    assert over.by_kind["cash"] >= Decimal("700.00")
    assert over.total_eur == sum(over.by_kind.values(), Decimal(0))


def test_a_depot_ticked_as_reserve_counts_at_its_value_once(run, read, fresh_workspaces):
    """Phase 10 D304: a depot is reserve only when the default scenario ticks it („Zählt zur
    Reserve“). Its reserve balance is its value on the day as the net worth reads it: the
    fund at its price, the cash and the money on its way (800 €), dated by the oldest of its
    days; the Anlagen read marks it; the net worth counts it once either way."""
    from db.test_services_prices_fetch import _asset, _held
    from db.test_services_prices_overview import S, _price

    from hausbuch.rules.errors import HOUSEHOLD_SCENARIO_ACCOUNT_INVALID
    from hausbuch.rules.health.figures import reserve_set
    from hausbuch.schemas.plan import HouseholdLevers, HouseholdScenarioCreate, ReserveAccounts
    from hausbuch.services import household_scenarios
    from hausbuch.services.errors import RuleViolationError
    from hausbuch.services.prices import overview

    ws, _ = fresh_workspaces
    current, depot, _ = _world(run, ws)

    def fund(ctx):
        asset = _asset(ctx, kind="etf", isin="IE000CASH001", name="Ein Fonds")
        _held(ctx, depot, asset, "4")
        _price(ctx, asset, ON, "25.00", S.USER)

    run(ws, fund, now=NOW)
    seen = read(ws, net_worth.net_worth, ON)
    reserve = {r.account_id: r for r in read(ws, lambda conn: health._reserve_balances(conn, seen))}
    assert reserve[depot].balance == Decimal("800.00")
    assert reserve[depot].balance == sum(
        (line.value_eur for line in seen.lines if line.account_id == depot), Decimal(0)
    )
    assert reserve[depot].as_of == date(2026, 10, 20)  # the cash statement's day, before ON
    assert reserve[current].balance == Decimal("500.00")
    # never reserve by default
    kinds = [(r.account_id, r.kind, r.rental) for r in reserve.values()]
    assert reserve_set(kinds, ReserveAccounts()) == frozenset({current})
    assert not any(a.counts_to_reserve for a in read(ws, overview.assets_overview, ON).accounts)

    ticked = HouseholdLevers(reserve_accounts=ReserveAccounts(added=[depot]))
    run(
        ws,
        household_scenarios.create_scenario,
        HouseholdScenarioCreate(name="Basis", is_default=True, levers=ticked),
        now=NOW,
    )
    assert reserve_set(kinds, ticked.reserve_accounts) == frozenset({current, depot})
    [row] = [a for a in read(ws, overview.assets_overview, ON).accounts if a.account_id == depot]
    assert row.counts_to_reserve
    assert read(ws, net_worth.net_worth, ON).total == seen.total

    # a depot can be added, never removed: it is not reserve by default
    with pytest.raises(RuleViolationError) as refused:
        run(
            ws,
            household_scenarios.create_scenario,
            HouseholdScenarioCreate(
                name="Ohne",
                levers=HouseholdLevers(reserve_accounts=ReserveAccounts(removed=[depot])),
            ),
            now=NOW,
        )
    assert list(refused.value.codes) == [HOUSEHOLD_SCENARIO_ACCOUNT_INVALID]
