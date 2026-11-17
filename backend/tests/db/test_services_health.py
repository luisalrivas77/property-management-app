"""Phase 9 step 1, part 1, under hausbuch_app and RLS (docs/phases/phase-9.md §4.1, §4.4, D231,
D234, D241): the rental block per month (services.health.rental_months), a privately paid
purchase cost counted as invested on the Haushalt screen (services.household.cashflow), a past
month-end's estimate from the scenario of that day (services.scenarios.default_scenario_on)
and which month-ends may be stored, and the coverage facts a closed month reads
(services.health.closed_month_facts). Invented owners, banks, IBANs and amounts only."""

import hashlib
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.matching_support import World
from db.test_services_private_matching import Private
from sqlalchemy import insert, update

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import bank_connection as connection_table
from hausbuch.db.tables import bank_sync as sync_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.rules.errors import (
    NET_WORTH_MONTH_END_BEFORE_HISTORY,
    NET_WORTH_MONTH_END_NOT_CLOSED,
    NET_WORTH_NOT_MONTH_END,
)
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.enums import AccountKind, AccountPurpose, AcquisitionCostKind
from hausbuch.schemas.net_worth import SnapshotOutcome
from hausbuch.schemas.performance import Levers, ScenarioCreate
from hausbuch.schemas.property import AcquisitionCostCreate, Address, PropertyCreate
from hausbuch.services import allocations, health, household, net_worth
from hausbuch.services.errors import RuleViolationError
from hausbuch.services.iban import protect_iban
from hausbuch.services.properties import add_acquisition_cost, create_property
from hausbuch.services.scenarios import create_scenario, default_scenario_on

pytestmark = pytest.mark.db

C = AllocationCategory
NOW = datetime(2027, 4, 10, 8, 0, tzinfo=UTC)


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


def _book(world: World, tx, amount: str, category: C, *, confirm: bool = True, **scope):
    return world.cmd(
        allocations.record_set,
        AllocationSet(
            transaction_id=tx,
            allocations=[AllocationDraft(amount=Decimal(amount), category=category, **scope)],
            method=AllocationMethod.USER,
            confidence=Decimal("1.000"),
        ),
        confirm=confirm,
        auto=False,
    )


def _booked(world: World, booked: date, amount: str, category: C, **scope):
    """A rental line booked as `category` and confirmed."""
    tx = world.line(booked, amount)
    _book(world, tx, amount, category, **scope)
    return tx


# The rental lines of tests/fixtures/phase9/household_month.yaml, case baseline (§15.2's March).
RENTAL_MARCH = [
    (date(2027, 3, 1), "950.00", C.RENT_COLD),
    (date(2027, 3, 1), "150.00", C.OPERATING_COSTS_PREPAYMENT),
    (date(2027, 3, 3), "-210.00", C.HAUSGELD_UMLAGEFAEHIG),
    (date(2027, 3, 3), "-100.00", C.HAUSGELD_VERWALTUNG),
    (date(2027, 3, 15), "-60.00", C.INSURANCE),
    (date(2027, 3, 30), "-640.00", C.LOAN_INTEREST),
    (date(2027, 3, 30), "-310.00", C.LOAN_PRINCIPAL),
]


# --- §4.1: the rental block -------------------------------------------------------------------


def test_the_march_block_sums_by_phase_4s_sets(world):
    """The baseline case's rental lines: R = 90, the repayment 310 beside it; rows with no
    property belong to the account's only property; a proposed row, a 0 € row and a line of
    April count nowhere; every counted row is a source."""
    loan = world.loan()
    ids = []
    for booked, amount, category in RENTAL_MARCH:
        scope = (
            {"loan_id": loan, "property_id": world.property_id}
            if category in (C.LOAN_INTEREST, C.LOAN_PRINCIPAL)
            else {}
        )
        tx = world.line(booked, amount)
        ids.append(_book(world, tx, amount, category, **scope))
    # Waiting for a person, nothing booked, the next month: none of them counts in March.
    _book(world, world.line(date(2027, 3, 20), "-45.00"), "-45.00", C.REPAIRS, confirm=False)
    _booked(world, date(2027, 3, 31), "0.00", C.BANK_FEES)
    _booked(world, date(2027, 4, 1), "950.00", C.RENT_COLD)

    result = world.query(health.rental_months, "2027-02", "2027-04")
    [feb], [march], [april] = (result.months[m] for m in ("2027-02", "2027-03", "2027-04"))
    assert result.unattributed == {}
    assert (march.month, march.property_id) == ("2027-03", world.property_id)
    assert (march.rent, march.umlagen_in, march.umlagen_out, march.running) == (
        Decimal("950.00"),
        Decimal("150.00"),
        Decimal("270.00"),
        Decimal("100.00"),
    )
    assert (march.interest, march.principal, march.invested, march.loan_disbursement) == (
        Decimal("640.00"),
        Decimal("310.00"),
        Decimal("0.00"),
        Decimal("0.00"),
    )
    assert march.result == Decimal("90.00")  # the fixture's rental_result
    assert march.result - march.principal == Decimal("-220.00")  # its rental_cash
    assert len(march.sources) == len(RENTAL_MARCH)
    # A month with nothing booked still has its row: the flat was owned (from May 2024).
    assert (feb.result, feb.sources) == (Decimal("0.00"), [])
    assert (april.rent, len(april.sources)) == (Decimal("950.00"), 1)


def test_the_purchase_month_counts_the_price_and_the_payout(world):
    """household_month.yaml, case purchase_month (rental side): the price is invested, the
    loan's payout signed as booked, the owners' top-up in no block; R stays 0."""
    _booked(world, date(2026, 4, 2), "60000.00", C.OWNER_TRANSFER_IN)
    _booked(world, date(2026, 4, 10), "240000.00", C.LOAN_DISBURSEMENT)
    _booked(world, date(2026, 4, 11), "-300000.00", C.PURCHASE_PRICE)
    [april] = world.query(health.rental_months, "2026-04", "2026-04").months["2026-04"]
    assert (april.invested, april.loan_disbursement, april.result) == (
        Decimal("300000.00"),
        Decimal("240000.00"),
        Decimal("0.00"),
    )
    assert len(april.sources) == 2


def test_a_refund_to_a_tenant_lowers_its_block(world):
    """§4.1: signed as phase 4 sums income: a Nebenkosten refund paid back lowers Umlagen in."""
    _booked(world, date(2027, 3, 1), "150.00", C.OPERATING_COSTS_PREPAYMENT)
    _booked(world, date(2027, 3, 20), "-40.00", C.OPERATING_COSTS_SETTLEMENT)
    [march] = world.query(health.rental_months, "2027-03", "2027-03").months["2027-03"]
    assert march.umlagen_in == Decimal("110.00")


def test_a_row_no_property_can_be_given_is_listed_not_guessed(world):
    """Fail closed: once the rental account carries rows of two properties, a row without a
    property is in no RentalMonth and is listed as unattributed."""
    second = world.cmd(
        create_property,
        PropertyCreate(
            label="Wohnung Ahornweg",
            kind="etw",
            address=Address(
                street="Ahornweg", house_number="3", postal_code="10115", city="Berlin"
            ),
            build_year=2001,
            notarized_on=date(2027, 1, 10),
            purchase_date=date(2027, 6, 1),
            purchase_price=Decimal("150000.00"),
        ),
    ).id
    rent = _booked(world, date(2027, 3, 1), "950.00", C.RENT_COLD)
    named = _booked(world, date(2027, 3, 2), "-80.00", C.REPAIRS, property_id=second)
    named_own = _booked(
        world, date(2027, 3, 3), "-60.00", C.INSURANCE, property_id=world.property_id
    )
    result = world.query(health.rental_months, "2027-03", "2027-03")
    rows = {r.property_id: r for r in result.months["2027-03"]}
    # The second flat, not owned until June, has a row because a row names it.
    assert rows[second].running == Decimal("80.00")
    assert rows[world.property_id].umlagen_out == Decimal("60.00")
    assert rows[world.property_id].rent == Decimal("0.00")
    [unattributed] = result.unattributed["2027-03"]
    assert unattributed not in rows[world.property_id].sources + rows[second].sources
    assert len(rows[world.property_id].sources) == 1
    assert rent and named and named_own


# --- D234: a privately paid purchase cost is invested --------------------------------------


def test_a_privately_paid_nebenkosten_bill_is_invested_not_spent(world):
    """The notary's bill for the purchase, a Nebenkosten row, paid from a private account and
    booked hh_property_cost with the bill (phase 6 D161), shows under Immobilien on the
    Haushalt screen; a repair bill paid the same way stays spending (D234)."""
    private = Private(world)
    notary = world.document("invoice", {})
    world.cmd(
        add_acquisition_cost,
        AcquisitionCostCreate(
            property_id=world.property_id,
            kind=AcquisitionCostKind.NOTARY,
            amount=Decimal("4200.00"),
            document_id=notary,
        ),
    )
    repair = world.document("invoice", {})
    paid = private.line(date(2026, 4, 14), "-4200.00", name="Notarin Beispiel")
    _book(
        world,
        paid,
        "-4200.00",
        C.HH_PROPERTY_COST,
        property_id=world.property_id,
        document_id=notary,
    )
    fixed = private.line(date(2026, 4, 22), "-300.00", name="Malerei Beispiel")
    _book(
        world,
        fixed,
        "-300.00",
        C.HH_PROPERTY_COST,
        property_id=world.property_id,
        document_id=repair,
    )
    salary = private.line(date(2026, 4, 28), "3100.00", name="Arbeitgeber A")
    _book(world, salary, "3100.00", C.HH_SALARY)

    month = world.query(household.cashflow, "2026-04")
    assert month.invested_by_category == {C.HH_INVESTMENT_PROPERTY: Decimal("4200.00")}
    spent = {c: v for codes in month.spending_by_category.values() for c, v in codes.items()}
    assert spent == {C.HH_PROPERTY_COST: Decimal("300.00")}
    assert (month.income, month.surplus) == (Decimal("3100.00"), Decimal("2800.00"))

    [cost] = world.query(household.private_purchase_costs, date(2026, 4, 1), date(2026, 4, 30))
    assert (cost.transaction_id, cost.booked_on, cost.property_id, cost.amount) == (
        paid,
        date(2026, 4, 14),
        world.property_id,
        Decimal("-4200.00"),
    )
    assert world.query(household.private_purchase_costs, date(2026, 5, 1), date(2026, 5, 31)) == []


# --- D241: a past month-end --------------------------------------------------------------------


def _scenario(world: World, value: str, at: datetime, *, supersedes=None, withdrawn=False):
    return world.cmd(
        create_scenario,
        world.property_id,
        ScenarioCreate(
            property_id=world.property_id,
            name="Basis",
            is_default=True,
            supersedes_id=supersedes,
            withdrawn=withdrawn,
            levers=Levers(value_growth=Decimal("0.02"), sale_year=2036, today_value=Decimal(value)),
        ),
        now=at,
    )


def test_a_past_day_reads_the_scenario_of_that_day(world):
    """Q-O131: the default recorded on 20 Sep, corrected late on 31 Oct (Berlin time) and
    withdrawn on 5 Nov: each month-end reads the one in force at its end (the day's end in
    Berlin), the purchase price before the first and after the withdrawal."""
    first = _scenario(world, "300000.00", datetime(2026, 9, 20, 10, tzinfo=UTC))
    # 23:30 on 31 Oct in Berlin is 22:30 UTC: still 31 Oct, so October's end reads it.
    second = _scenario(
        world, "310000.00", datetime(2026, 10, 31, 22, 30, tzinfo=UTC), supersedes=first.id
    )
    _scenario(
        world,
        "310000.00",
        datetime(2026, 11, 5, 9, tzinfo=UTC),
        supersedes=second.id,
        withdrawn=True,
    )

    def value(day):
        scenario = world.query(default_scenario_on, world.property_id, day)
        return None if scenario is None else scenario.levers.today_value

    assert value(date(2026, 8, 31)) is None
    assert value(date(2026, 9, 30)) == Decimal("300000.00")
    assert value(date(2026, 10, 30)) == Decimal("300000.00")
    assert value(date(2026, 10, 31)) == Decimal("310000.00")
    assert value(date(2026, 11, 30)) is None
    assert world.query(net_worth.net_worth, date(2026, 9, 30)).property_estimate == Decimal(
        "300000.00"
    )
    assert world.query(net_worth.net_worth, date(2026, 11, 30)).property_estimate == Decimal(
        "289000.00"
    )  # the purchase price once no default is in force


def test_any_closed_month_end_from_january_2026_can_be_stored(world):
    """D241: a past month-end is stored like the last one; a day that is no month-end, one
    before the bank history and one of a month still running are refused, nothing written."""
    stored = world.cmd(net_worth.capture_month_end, date(2026, 9, 30))
    assert stored.as_of == date(2026, 9, 30)
    assert stored.outcome in (SnapshotOutcome.WRITTEN, SnapshotOutcome.EMPTY)
    via_button = world.cmd(net_worth.capture_last_month_end, date(2026, 9, 30))
    assert via_button.as_of == date(2026, 9, 30)
    assert world.cmd(net_worth.capture_last_month_end).as_of == date(2027, 3, 31)
    for day, code in [
        (date(2026, 9, 29), NET_WORTH_NOT_MONTH_END),
        (date(2025, 12, 31), NET_WORTH_MONTH_END_BEFORE_HISTORY),
        (date(2027, 4, 30), NET_WORTH_MONTH_END_NOT_CLOSED),
        (date(2027, 5, 31), NET_WORTH_MONTH_END_NOT_CLOSED),
    ]:
        with pytest.raises(RuleViolationError) as refused:
            world.cmd(net_worth.capture_month_end, day)
        assert list(refused.value.codes) == [code]


# --- D231 (a), §4.4: the facts of a closed month -----------------------------------------------


def _iban(number: int) -> str:
    """An invented German IBAN at the Bundesbank's test bank code 10000000."""
    bban = f"10000000{number:010d}"
    check = 98 - int(bban + "131400") % 97  # D=13, E=14, then 00
    return f"DE{check:02d}{bban}"


def _account(world: World, label: str, kind: AccountKind, *, opening="0.00", on=date(2026, 1, 1)):
    """A private account; a bank kind gets an invented IBAN (account_identity_check)."""
    bank = kind in (AccountKind.BANK_CURRENT, AccountKind.BANK_SAVINGS)
    iban = protect_iban(_iban(uuid.uuid4().int % 10**10), TEST_IBAN_KEY) if bank else None
    with workspace_transaction(world.engine, world.ws) as conn:
        return conn.execute(
            insert(account_table)
            .values(
                workspace_id=world.ws,
                label=label,
                bank="Beispielbank",
                kind=kind.value,
                purpose=AccountPurpose.PRIVATE.value,
                iban_masked=None if iban is None else iban.masked,
                iban_hmac=None if iban is None else iban.hmac,
                opening_balance=Decimal(opening),
                opening_balance_on=on,
                is_owner_account=True,
                created_at=world.now,
                updated_at=world.now,
            )
            .returning(account_table.c.id)
        ).scalar_one()


def _statement(world, account_id, first, last, made, *, opening=None, closing="0.00", lines=()):
    """A reconciled import made at `made` with a line on each of `lines`."""
    with workspace_transaction(world.engine, world.ws) as conn:
        doc = world._document_row(conn, type="statement", subtype=None, title=None)
        imported = conn.execute(
            insert(import_table)
            .values(
                workspace_id=world.ws,
                account_id=account_id,
                document_id=doc,
                format="camt",
                status="reconciled",
                balance_source="file",
                declared_opening=None if opening is None else Decimal(opening),
                computed_opening=None if opening is None else Decimal(opening),
                declared_closing=Decimal(closing),
                computed_closing=Decimal(closing),
                first_booking_on=first,
                last_booking_on=last,
                created_at=made,
                updated_at=made,
            )
            .returning(import_table.c.id)
        ).scalar_one()
        out = []
        for booked in lines:
            out.append(
                conn.execute(
                    insert(transaction_table)
                    .values(
                        workspace_id=world.ws,
                        account_id=account_id,
                        import_id=imported,
                        line_hash=hashlib.sha256(uuid.uuid4().bytes).digest(),
                        booking_date=booked,
                        value_date=booked,
                        amount=Decimal("-10.00"),
                        purpose="",
                        created_at=made,
                    )
                    .returning(transaction_table.c.id)
                ).scalar_one()
            )
        return out


def _synced(world, account_id, runs):
    """One FinTS connection for the account and a finished run per (status, requested_to)."""
    with workspace_transaction(world.engine, world.ws) as conn:
        connection = conn.execute(
            insert(connection_table)
            .values(
                workspace_id=world.ws,
                account_id=account_id,
                bank_code="10000000",
                fints_url="https://fints.example.invalid/",
                login_name="beispiel",
            )
            .returning(connection_table.c.id)
        ).scalar_one()
        for status, requested_to in runs:
            conn.execute(
                insert(sync_table).values(
                    workspace_id=world.ws,
                    account_id=account_id,
                    connection_id=connection,
                    trigger="scheduled",
                    status=status,
                    requested_from=date(2026, 10, 1),
                    requested_to=requested_to,
                    lines_total=0,
                    lines_new=0,
                    started_at=world.now,
                    finished_at=world.now,
                )
            )


def test_each_account_says_how_far_its_history_reaches(world):
    """CoverageAccount: an export made after its last booking day covers that day, one made on
    it only the day before; a FinTS run covers the day before it ran, a failed run nothing; a
    card whose last statement closed at 0 and was not used since is idle; an opening balance
    taken from the first statement is the account's own; an account with no reconciled line
    is covered nowhere (fail closed). Depots are not checked."""
    current = _account(
        world, "A Giro", AccountKind.BANK_CURRENT, opening="500.00", on=date(2026, 8, 31)
    )
    _statement(
        world,
        current,
        date(2026, 9, 1),
        date(2026, 10, 31),
        datetime(2026, 11, 2, 9, tzinfo=UTC),
        opening="500.00",
        lines=[date(2026, 9, 1), date(2026, 10, 31)],
    )
    same_day = _account(world, "B Giro", AccountKind.BANK_CURRENT)
    # Made at 18:00 Berlin on 31 Oct: later bookings that day may be missing.
    _statement(
        world,
        same_day,
        date(2026, 10, 1),
        date(2026, 10, 31),
        datetime(2026, 10, 31, 17, tzinfo=UTC),
        lines=[date(2026, 10, 1), date(2026, 10, 31)],
    )
    synced = _account(world, "Tagesgeld", AccountKind.BANK_SAVINGS)
    _statement(
        world,
        synced,
        date(2026, 9, 1),
        date(2026, 9, 1),
        datetime(2026, 9, 2, tzinfo=UTC),
        lines=[date(2026, 9, 1)],
    )
    _synced(world, synced, [("nothing_new", date(2026, 11, 12)), ("failed", date(2026, 12, 20))])
    card = _account(world, "Karte", AccountKind.CREDIT_CARD)
    _statement(
        world,
        card,
        date(2026, 9, 3),
        date(2026, 9, 30),
        datetime(2026, 10, 2, tzinfo=UTC),
        lines=[date(2026, 9, 3)],
    )
    depot = _account(world, "Depot", AccountKind.DEPOT)
    _statement(
        world,
        depot,
        date(2026, 9, 1),
        date(2026, 9, 30),
        datetime(2026, 10, 2, tzinfo=UTC),
        lines=[date(2026, 9, 1)],
    )

    accounts = {a.account_id: a for a in world.query(health.coverage_accounts)}
    assert depot not in accounts
    assert (accounts[current].first_day, accounts[current].covered_to) == (
        date(2026, 9, 1),
        date(2026, 10, 31),
    )
    assert accounts[current].own_opening and not accounts[same_day].own_opening
    assert accounts[same_day].covered_to == date(2026, 10, 30)
    assert accounts[synced].covered_to == date(2026, 11, 11)
    assert accounts[card].idle and not accounts[current].idle
    assert accounts[card].covered_to == date(2026, 9, 30)
    # The world's rental account has an import but no line: no history, covered nowhere.
    rental = accounts[world.account_id]
    assert (rental.purpose, rental.first_day, rental.covered_to) == (
        AccountPurpose.RENTAL,
        None,
        None,
    )


def test_a_card_paid_again_after_a_zero_statement_is_not_idle(world):
    """D122 amended (6): a debit on an own account paying the card after the window of its 0 €
    statement shows it was used again: no longer idle."""
    private = Private(world)
    card = _account(world, "Karte", AccountKind.CREDIT_CARD)
    payment = protect_iban(_iban(123456789), TEST_IBAN_KEY)
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(
            update(account_table)
            .where(account_table.c.id == card)
            .values(payment_iban_masked=payment.masked, payment_iban_hmac=payment.hmac)
        )
    _statement(
        world,
        card,
        date(2026, 9, 3),
        date(2026, 9, 30),
        datetime(2026, 10, 2, tzinfo=UTC),
        lines=[date(2026, 9, 3)],
    )
    assert {a.account_id: a for a in world.query(health.coverage_accounts)}[card].idle
    private.line(date(2026, 10, 20), "-80.00", iban=_iban(123456789))
    accounts = {a.account_id: a for a in world.query(health.coverage_accounts)}
    assert not accounts[card].idle


def test_a_month_counts_its_waiting_lines_and_open_transfer_legs(world):
    """ClosedMonthFacts: the month's lines on the checked accounts with no confirmed row (a
    proposed one does not count as explained), not a line another month or a booked one; and
    the euro transfer legs GET /transfers/open lists, in the month of their booking."""
    current = _account(world, "A Giro", AccountKind.BANK_CURRENT)
    waiting, proposed, booked, transfer, november = _statement(
        world,
        current,
        date(2026, 10, 2),
        date(2026, 11, 3),
        datetime(2026, 11, 4, tzinfo=UTC),
        lines=[
            date(2026, 10, 2),
            date(2026, 10, 9),
            date(2026, 10, 16),
            date(2026, 10, 20),
            date(2026, 11, 3),
        ],
    )
    _book(world, proposed, "-10.00", C.HH_GROCERIES, confirm=False)
    _book(world, booked, "-10.00", C.HH_GROCERIES)
    _book(world, transfer, "-10.00", C.INTERNAL_TRANSFER_OUT)  # names no own account

    facts = world.query(health.closed_month_facts, ["2026-10", "2026-11"], date(2026, 11, 12))
    october, nov = facts["2026-10"], facts["2026-11"]
    assert (october.lines_waiting, october.transfers_open) == (2, 1)
    assert (nov.lines_waiting, nov.transfers_open) == (1, 0)
    assert current in {a.account_id for a in october.accounts}
    assert waiting and november
    assert world.query(health.closed_month_facts, [], date(2026, 11, 12)) == {}


def test_a_past_month_end_counts_nothing_after_its_day(world):
    """D241: a month-end stored months later reads its own day: a line booked after it does not
    count, and the stored run equals the day's reading. An account whose opening balance is
    dated after the day has no figure that day and is absent from the run, with no to-do
    saying so (phase 6's rule, reported as a gap: before D241 only the running month met it)."""
    giro = _account(world, "A Giro", AccountKind.BANK_CURRENT, opening="100.00")
    _statement(
        world,
        giro,
        date(2026, 8, 15),
        date(2026, 10, 31),
        datetime(2026, 11, 2, 9, tzinfo=UTC),
        lines=[date(2026, 8, 15), date(2026, 9, 20), date(2026, 10, 10)],
    )
    later = _account(world, "Neu", AccountKind.BANK_SAVINGS, opening="300.00", on=date(2026, 9, 15))
    _scenario(world, "300000.00", datetime(2026, 9, 20, 10, tzinfo=UTC))

    stored = world.cmd(net_worth.capture_month_end, date(2026, 8, 31))
    assert stored.outcome == SnapshotOutcome.WRITTEN
    run = world.query(net_worth.stored_run, stored.run.net_worth_run_id)
    read = world.query(net_worth.net_worth, date(2026, 8, 31))
    lines = {line.account_id: line for line in run.lines if line.account_id is not None}
    assert lines[giro].value_eur == Decimal("90.00")  # the 15 Aug line only
    assert later not in lines
    assert (run.total, run.property_estimate) == (read.total, read.property_estimate)
    assert run.property_estimate != Decimal("300000.00")  # recorded on 20 Sep, after the day
    september = world.query(net_worth.net_worth, date(2026, 9, 30))
    by_account = {line.account_id: line.value_eur for line in september.lines}
    assert (by_account[giro], by_account[later]) == (Decimal("80.00"), Decimal("300.00"))
