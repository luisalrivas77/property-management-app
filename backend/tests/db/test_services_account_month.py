"""services.account_month under hausbuch_app and RLS (docs/phases/phase-10.md D269): one
account's month for Konten › Umsätze, its balances by the net worth's reconciled rule, the
transfers kept out of Eingänge and Ausgänge, the open lines, the running month, a future month
refused and another workspace's account not found. Invented banks, IBANs and amounts only."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from db.matching_support import World
from sqlalchemy import update

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import statement_import as import_table
from hausbuch.rules.errors import ACCOUNT_MONTH_IN_FUTURE
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.services import allocations
from hausbuch.services.account_month import account_month
from hausbuch.services.errors import NotFoundError, RuleViolationError

pytestmark = pytest.mark.db

C = AllocationCategory
NOW = datetime(2027, 4, 10, 8, 0, tzinfo=UTC)
TODAY = date(2027, 4, 10)


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    w = World(app_engine, fresh_workspaces[0], now=NOW)
    with workspace_transaction(w.engine, w.ws) as conn:  # reconciled through 20 March
        conn.execute(
            update(import_table)
            .where(import_table.c.id == w.import_id)
            .values(first_booking_on=date(2027, 2, 1), last_booking_on=date(2027, 3, 20))
        )
    return w


def _line(world: World, booked: date, amount: str, category: C | None = None, *, confirm=True):
    tx = world.line(booked, amount)
    if category is not None:
        world.cmd(
            allocations.record_set,
            AllocationSet(
                transaction_id=tx,
                allocations=[AllocationDraft(amount=Decimal(amount), category=category)],
                method=AllocationMethod.USER,
                confidence=Decimal("1.000"),
            ),
            confirm=confirm,
            auto=False,
        )
    return tx


def test_the_month_card(world):
    _line(world, date(2027, 2, 10), "1000.00", C.OWNER_TRANSFER_IN)
    _line(world, date(2027, 3, 3), "-5.00", C.BANK_FEES)
    _line(world, date(2027, 3, 5), "-500.00", C.OWNER_TRANSFER_OUT)
    _line(world, date(2027, 3, 8), "1200.00")  # waiting
    _line(world, date(2027, 3, 12), "-80.00", C.OWNER_TRANSFER_OUT, confirm=False)  # proposed
    _line(world, date(2027, 3, 25), "-30.00")  # after the reconciled day
    _line(world, date(2027, 4, 2), "-7.00", C.BANK_FEES)

    march = world.query(account_month, world.account_id, "2027-03", TODAY)
    assert not march.running
    # Opening: the end of February, 0 + 1000.
    assert (march.opening_balance, march.opening_as_of) == (Decimal("1000.00"), date(2027, 2, 28))
    # Closing: only through 20 March, the day the statements are reconciled to:
    # 1000 − 5 − 500 + 1200 − 80.
    assert (march.closing_balance, march.closing_as_of) == (Decimal("1615.00"), date(2027, 3, 20))
    # The owner transfer is a transfer; a proposed one is not booked, so it counts by its sign.
    assert (march.incoming, march.outgoing) == (Decimal("1200.00"), Decimal("115.00"))
    assert (march.transfers_in, march.transfers_out) == (Decimal("0.00"), Decimal("500.00"))
    assert (march.lines, march.open_lines) == (5, 3)  # waiting, proposed, after the 20th

    april = world.query(account_month, world.account_id, "2027-04", TODAY)
    assert april.running
    assert april.closing_as_of == date(2027, 3, 20)  # never past the reconciled day
    assert (april.outgoing, april.lines, april.open_lines) == (Decimal("7.00"), 1, 0)

    feb = world.query(account_month, world.account_id, "2027-02", TODAY)
    assert feb.transfers_in == Decimal("1000.00") and feb.incoming == Decimal("0.00")


def test_a_month_before_the_opening_balance_has_no_balance(world):
    old = world.query(account_month, world.account_id, "2023-12", TODAY)
    assert old.opening_balance is None and old.closing_balance is None
    assert old.opening_as_of is None and old.closing_as_of is None
    assert old.lines == 0


def test_a_future_month_is_refused(world):
    with pytest.raises(RuleViolationError) as refused:
        world.query(account_month, world.account_id, "2027-05", TODAY)
    assert list(refused.value.codes) == [ACCOUNT_MONTH_IN_FUTURE]


def test_another_workspace_does_not_see_the_account(world, app_engine, fresh_workspaces):
    other = World(app_engine, fresh_workspaces[1], now=NOW)
    with pytest.raises(NotFoundError):
        other.query(account_month, world.account_id, "2027-03", TODAY)
