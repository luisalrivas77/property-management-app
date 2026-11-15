"""Phase 6 D162 amended (8), lock order (fixed 16 Nov 2026) under hausbuch_app and RLS: an
exchange import locks the exchange account's row and then takes the transfer lock in its
relink, so every command that may learn the exchange's deposit IBAN (which locks that row)
takes the row first too. Before, the first link by hand took the transfer lock, then the bank
line, then the row, and a person's answer with the box „IBAN als Einzahlungskonto von <Börse>
merken“ ticked took the line and the transfer lock (its hook) before the row: run at the same
moment as an import, Postgres had to fail one of them as a deadlock.

Each test holds the exchange's row in one transaction, starts the command in another thread,
waits until that command waits for a lock, and then checks from a third transaction that the
command holds neither the transfer lock nor the bank line: it waits for the row before taking
them. Invented names and IBANs only."""

import threading
import time
from datetime import date
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.matching_support import World
from db.test_services_exchange_deposit_fee import (
    DEPOSIT_IBAN,
    _exchange,
    _export,
    _movements,
    _propose,
)
from db.test_services_exchange_deposit_fee import _assert_learned as _assert_split_and_learned
from db.test_services_exchange_deposit_iban_confirm import _assert_learned, _confirm, _paired
from db.test_services_private_matching import NOW, Private
from sqlalchemy import func, select, text

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.schemas.household import TransferLinkKind, TransferLinkRequest
from hausbuch.services import transfers

pytestmark = pytest.mark.db

WAIT = 10.0  # seconds; generous for a loaded box


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


@pytest.fixture
def bank(world) -> Private:
    return Private(world)


def _in_thread(command) -> tuple[threading.Thread, list, list]:
    results: list = []
    errors: list[Exception] = []

    def run() -> None:
        try:
            results.append(command())
        except Exception as exc:  # noqa: BLE001 - collected for the assertion below
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    return thread, results, errors


def _wait_until_blocked(world: World, thread: threading.Thread) -> None:
    """Until a session of this test's database waits for a lock (the command, on the held
    row; each xdist worker has its own database, so no other test's wait counts)."""
    deadline = time.monotonic() + WAIT
    while time.monotonic() < deadline:
        with workspace_transaction(world.engine, world.ws) as conn:
            waiting = conn.execute(
                text(
                    "SELECT count(*) FROM pg_locks WHERE NOT granted AND pid IN"
                    " (SELECT pid FROM pg_stat_activity WHERE datname = current_database())"
                )
            )
            if waiting.scalar_one() > 0:
                return
        assert thread.is_alive(), "the command finished without waiting for the exchange's row"
        time.sleep(0.05)
    raise AssertionError("the command never waited for a lock")


def _transfer_lock_free(world: World) -> bool:
    """Whether a third transaction can take the transfer lock now (released at its end)."""
    key = func.hashtextextended(f"transfer_link:{world.ws}", 0)
    with workspace_transaction(world.engine, world.ws) as conn:
        return bool(conn.execute(select(func.pg_try_advisory_xact_lock(key))).scalar_one())


def _line_free(world: World, tx) -> bool:
    """Whether a third transaction can lock the bank line now (released at its end)."""
    t = transaction_table.c
    with workspace_transaction(world.engine, world.ws) as conn:
        found = conn.execute(
            select(t.id).where(t.id == tx).with_for_update(skip_locked=True)
        ).first()
        return found is not None


def _holding_the_exchange_row(world: World, exchange_id, command, *, line):
    """Run `command` in another thread while this transaction holds the exchange's row, as an
    exchange import does before its relink; the command's result once the row is released."""
    acc = account_table.c
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(select(acc.id).where(acc.id == exchange_id).with_for_update()).one()
        thread, results, errors = _in_thread(command)
        _wait_until_blocked(world, thread)
        # The command waits for the row before the transfer lock and before the line.
        assert _transfer_lock_free(world)
        assert _line_free(world, line)
    # The row is released here, as the import's commit would release it.
    thread.join(timeout=WAIT)
    assert not thread.is_alive()
    assert errors == []
    [result] = results
    return result


def test_a_link_by_hand_takes_the_exchange_row_before_the_transfer_lock(world, bank, blob_store):
    out = bank.line(date(2026, 9, 7), "-901.00", name="Binance", iban=DEPOSIT_IBAN)
    _propose(world, out, "-901.00")
    exchange = _exchange(world)
    _export(world, blob_store, exchange.id, "2026-09-08 08:00:00,Deposit,900")
    deposit = _movements(world, exchange.id)[Decimal("900.00")]
    world.query(lambda c: c.execute(text("DELETE FROM procrastinate_jobs")))
    body = TransferLinkRequest(
        kind=TransferLinkKind.EXCHANGE_EUR, out_transaction_id=out, in_movement_id=deposit
    )
    link = _holding_the_exchange_row(
        world,
        exchange.id,
        lambda: world.cmd(transfers.link_by_hand, body, iban_key=TEST_IBAN_KEY),
        line=out,
    )
    # Once the row is free, the link by hand splits the line and learns the IBAN as before.
    _assert_split_and_learned(world, out, exchange, deposit, link)


def test_an_answer_with_the_deposit_box_takes_the_exchange_row_first(world, bank, blob_store):
    out, exchange, _deposit = _paired(world, bank, blob_store)
    _holding_the_exchange_row(
        world,
        exchange.id,
        lambda: _confirm(world, out, "-1000.00", "internal_transfer_out", box=True),
        line=out,
    )
    _assert_learned(world, out, exchange)


def test_an_answer_without_the_box_never_locks_the_exchange_row(world, bank, blob_store):
    """Unticked, the answer takes no lock on the exchange: it runs while the row is held."""
    out, exchange, _deposit = _paired(world, bank, blob_store)
    acc = account_table.c
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(select(acc.id).where(acc.id == exchange.id).with_for_update()).one()
        thread, _results, errors = _in_thread(
            lambda: _confirm(world, out, "-1000.00", "internal_transfer_out", box=False)
        )
        thread.join(timeout=WAIT)
        assert not thread.is_alive() and errors == []
    assert _account_iban(world, exchange.id) is None


def _account_iban(world: World, account_id):
    acc = account_table.c
    return world.query(
        lambda c: c.execute(select(acc.iban_hmac).where(acc.id == account_id)).scalar_one()
    )
