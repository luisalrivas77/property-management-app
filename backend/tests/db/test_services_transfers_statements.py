"""How many SQL statements the transfer and household-month reads send (21 Nov 2026: Übersicht
sent about 1,550 per load, `saving_links` one account query per link). Each read must send a
number of statements that does not grow with the links or the cards it returns: the seed
grows from a few links and cards to more, and the count must stay the same. Invented IBANs
only."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.matching_support import ACCOUNT_IBAN, World
from db.test_services_private_matching import NOW, PRIVATE_IBAN, Private
from db.test_services_transfers import SAVINGS_IBAN, _savings
from sqlalchemy import event

from hausbuch.db.session import workspace_transaction
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.household import OpenTransferCode
from hausbuch.services import allocations, household, matching, transfers

pytestmark = pytest.mark.db

C = AllocationCategory
TODAY = NOW.date()
START = date(2026, 6, 1)
OCT = {"start": date(2026, 6, 1), "end": date(2026, 10, 31)}


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


def _book(world: World, tx, amount: str, category: C) -> None:
    world.cmd(
        allocations.record_set,
        AllocationSet(
            transaction_id=tx,
            allocations=[AllocationDraft(amount=Decimal(amount), category=category)],
            method=AllocationMethod.USER,
            confidence=Decimal("1.000"),
        ),
        confirm=True,
        auto=False,
    )


def _match(world: World, tx) -> None:
    world.cmd(matching.match_line, tx, iban_key=TEST_IBAN_KEY, ai_available=False)


def _counted(world: World, read, *args, **kwargs):
    """The read's result and the statements it sent (the transaction's own set-up excluded)."""
    sent: list[str] = []

    def count(conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
        sent.append(statement)

    def run(conn):  # noqa: ANN001, ANN202
        event.listen(conn, "before_cursor_execute", count)
        try:
            return read(conn, *args, **kwargs)
        finally:
            event.remove(conn, "before_cursor_execute", count)

    return world.query(run), len(sent)


def _iban(number: int) -> str:
    """A valid German IBAN at the Bundesbank's test bank code 10000000."""
    bban = f"10000000{number:010d}"
    check = 98 - int(bban + "131400") % 97
    return f"DE{check:02d}{bban}"


def _current(world: World, iban: str) -> Private:
    """Another private current account of the household, with its own reconciled import."""
    account = Private.__new__(Private)
    account.world = world
    with workspace_transaction(world.engine, world.ws) as conn:
        account.account_id = world._account(conn, iban, owner=True)
    account.import_id = account.add_import(world.now)
    return account


class _Seed:
    """A private current account and a Tagesgeld account. `extras` adds links that are no
    saving: a withdrawal from the savings account, a top-up from the rental account, and a
    saving whose in-leg is booked after the range. `grow` adds `n` rounds, each a savings
    transfer (linked by the matcher) and three open cards: on the private account a transfer
    out naming no IBAN and no counterpart (transfer.unmatched_leg), a transfer out whose
    counterpart on the savings account a person booked as other income (transfer.proposed),
    and on a new current account of its own an unmatched transfer out, so the cards span
    more accounts as the seed grows."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.private = Private(world)
        self.savings = _savings(world)
        self.months = 0
        self.saved: list = []

    def _pair(self, out_account, in_account, out_iban, in_iban, day, amount, in_day=None):
        out = out_account.line(day, f"-{amount}", iban=in_iban, purpose="Umbuchung")
        in_ = in_account.line(in_day or day, amount, iban=out_iban, purpose="Umbuchung")
        _match(self.world, out)
        _match(self.world, in_)
        return in_

    def extras(self) -> None:
        self._pair(self.savings, self.private, SAVINGS_IBAN, PRIVATE_IBAN, START, "900.00")
        self._pair(self.world, self.private, ACCOUNT_IBAN, PRIVATE_IBAN, START, "800.00")
        self._pair(
            self.private,
            self.savings,
            PRIVATE_IBAN,
            SAVINGS_IBAN,
            date(2026, 10, 30),
            "700.00",
            in_day=date(2026, 11, 2),
        )

    def grow(self, n: int) -> None:
        for _ in range(n):
            day = START + timedelta(days=7 * self.months)
            amount = f"{100 + self.months}.00"
            self.saved.append(
                self._pair(self.private, self.savings, PRIVATE_IBAN, SAVINGS_IBAN, day, amount)
            )
            lone = self.private.line(day + timedelta(days=1), f"-{50 + self.months}.00")
            _book(self.world, lone, f"-{50 + self.months}.00", C.INTERNAL_TRANSFER_OUT)
            proposed = f"{20 + self.months}.00"
            leg = self.private.line(day + timedelta(days=2), f"-{proposed}")
            _book(self.world, leg, f"-{proposed}", C.INTERNAL_TRANSFER_OUT)
            gift = self.savings.line(day + timedelta(days=2), proposed, name="Freund")
            _book(self.world, gift, proposed, C.HH_OTHER_INCOME)
            other = _current(self.world, _iban(200 + self.months))
            elsewhere = other.line(day + timedelta(days=3), f"-{70 + self.months}.00")
            _book(self.world, elsewhere, f"-{70 + self.months}.00", C.INTERNAL_TRANSFER_OUT)
            self.months += 1


def _codes(cards) -> list[OpenTransferCode]:
    return sorted(c.code for c in cards)


def test_the_reads_send_as_many_statements_for_many_links_as_for_few(world):
    seed = _Seed(world)
    seed.extras()
    seed.grow(2)
    few_links, few_saving = _counted(world, transfers.saving_links, **OCT)
    few_cards, few_open = _counted(world, transfers.open_transfers, TODAY)
    _, few_months = _counted(world, household.private_months, "2026-06", "2026-10")
    assert [v.in_leg.id for v in few_links] == seed.saved
    assert _codes(few_cards) == sorted(
        [OpenTransferCode.PROPOSED] * 2 + [OpenTransferCode.UNMATCHED_LEG] * 4
    )

    seed.grow(4)
    many_links, many_saving = _counted(world, transfers.saving_links, **OCT)
    many_cards, many_open = _counted(world, transfers.open_transfers, TODAY)
    _, many_months = _counted(world, household.private_months, "2026-06", "2026-10")
    assert [v.in_leg.id for v in many_links] == seed.saved
    assert len(seed.saved) == 6
    assert len(many_cards) == 18
    assert (many_saving, many_open, many_months) == (few_saving, few_open, few_months)
