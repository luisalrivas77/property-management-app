"""Phase 10 §7.3 (4), 21 Nov 2026: open_transfers reads the current links once for its EUR and
coin parts, and its card window takes the lines with a transfer row from the read that found
the waiting legs, reading only the span's other lines (_window_lines). These tests pin both to
the reads they replace: the window's lines equal _line_legs over the span, in its order, and
the cards equal those of the old reads. Invented IBANs only (Bundesbank test bank code
10000000)."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.matching_support import ACCOUNT_IBAN, PAINTER_IBAN, World
from db.test_services_private_matching import NOW, PRIVATE_IBAN, Private

from hausbuch.db.session import workspace_transaction
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.allocation import AllocationDraft, AllocationMethod, AllocationSet
from hausbuch.services import allocations, matching, transfers
from hausbuch.services.chain import transfers as chain_transfers

pytestmark = pytest.mark.db

SAVINGS_IBAN = "DE73100000000000000072"
TODAY = NOW.date()


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


def _book(world: World, tx, category: C, amount: str) -> None:
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


def _seed(world: World) -> None:
    """Owner transfers the matcher links, a painter's lines with no transfer row, and lone
    transfers out with no counterpart (cards) on the same days as other lines, so the window
    merges both reads within a day by id."""
    private = Private(world)
    for i in range(6):
        on = date(2026, 6, 1) + timedelta(days=9 * i)
        out = private.line(on, "-500.00", iban=ACCOUNT_IBAN, name="Owner A")
        inn = world.line(on, "500.00", iban=PRIVATE_IBAN, name="Owner A")
        for tx in (out, inn):
            world.cmd(matching.match_line, tx, iban_key=TEST_IBAN_KEY, ai_available=False)
        for _ in range(2):
            private.line(on + timedelta(days=2), "-37.20", iban=PAINTER_IBAN, name="Maler")
            world.line(on + timedelta(days=2), "-12.00", iban=PAINTER_IBAN, name="Maler")
        lone = private.line(on + timedelta(days=2), "-250.00", iban=SAVINGS_IBAN, name="Sparen")
        _book(world, lone, C.INTERNAL_TRANSFER_OUT, "-250.00")


def _fields(legs) -> list[tuple]:
    return [(x.leg, x.label, x.name, x.purpose, x.booked, x.category) for x in legs]


def test_the_window_lines_equal_the_span_read(world):
    _seed(world)
    t = transfers.transaction_table.c
    with workspace_transaction(world.engine, world.ws) as conn:
        with_row = transfers._line_legs(conn, t.id.in_(transfers._with_transfer_row()))
        assert with_row, "the seed has lines with a transfer row"
        for start, end in [
            (date(2026, 5, 1), date(2026, 8, 31)),
            (date(2026, 6, 3), date(2026, 6, 21)),
            (date(2026, 6, 4), date(2026, 6, 10)),
            (date(2026, 9, 1), date(2026, 9, 30)),
        ]:
            want = transfers._line_legs(conn, t.booking_date >= start, t.booking_date <= end)
            got = transfers._window_lines(conn, start, end, with_row)
            assert _fields(got) == _fields(want)
        everything = transfers._line_legs(
            conn, t.booking_date >= date(2026, 5, 1), t.booking_date <= date(2026, 8, 31)
        )
        assert {x.booked is None for x in everything} == {True, False}, "both reads merged"


def test_open_transfers_equal_the_old_reads(world, monkeypatch):
    _seed(world)
    with workspace_transaction(world.engine, world.ws) as conn:
        new = transfers.open_transfers(conn, TODAY)
        t = transfers.transaction_table.c
        real_read = chain_transfers._World.read
        monkeypatch.setattr(
            transfers,
            "_window_lines",
            lambda c, s, e, _lines: transfers._line_legs(
                c, t.booking_date >= s, t.booking_date <= e
            ),
        )
        monkeypatch.setattr(
            chain_transfers._World,
            "read",
            classmethod(lambda cls, c, linked=None: real_read.__func__(cls, c, None)),
        )
        old = transfers.open_transfers(conn, TODAY)
    assert len(new) == 6
    assert [x.model_dump_json() for x in new] == [x.model_dump_json() for x in old]
