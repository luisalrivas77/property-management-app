"""Phase 6 D162 amended (8), 15 Nov 2026 under hausbuch_app and RLS: a person who confirms a
private bank line as a transfer out that the matcher paired with an exchange's EUR deposit by
amount and date alone (untied: the exchange holds no IBAN yet) teaches the exchange the line's
IBAN, so the next deposit to it is tied by IBAN, booked and linked by the matcher. Never a
rule's, the matcher's or the model's own confirmation; the guards of
accounts.learn_exchange_deposit_iban (another own account's IBAN, a shared IBAN, an exchange
with an IBAN already) stay. Invented names and IBANs only."""

from datetime import date
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.matching_support import World
from db.test_services_exchange_deposit_fee import (
    DEPOSIT_IBAN,
    PAYOUT_IBAN,
    _account,
    _booked,
    _exchange,
    _export,
    _link,
    _links,
    _movements,
    _open,
    _propose,
    _queued,
    _recheck,
)
from db.test_services_private_matching import NOW, Private, _answer_line, _user_set
from sqlalchemy import select, text

from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.jobs import enqueue
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.api import ConfirmAllocationsBody
from hausbuch.schemas.enums import ActorType
from hausbuch.schemas.household import DepositIbanOutcome, TransferMatchedBy
from hausbuch.services import allocations, matching, review, transfers
from hausbuch.services.context import Actor

pytestmark = pytest.mark.db

C = AllocationCategory
TODAY = NOW.date()
OTHER_IBAN = "DE88100000000000000093"  # another IBAN of the same exchange's bank
SYSTEM = Actor(ActorType.SYSTEM, "cli:test")


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


@pytest.fixture
def bank(world) -> Private:
    return Private(world)


def _clear_jobs(world: World) -> None:
    world.query(lambda c: c.execute(text("DELETE FROM procrastinate_jobs")))


def _account_updates(world: World, account_id):
    return world.query(
        lambda c: c.execute(
            select(event_table).where(
                event_table.c.type == "account.updated", event_table.c.entity_id == account_id
            )
        ).all()
    )


def _printed(world: World, tx) -> str:
    return world.query(
        lambda c: c.execute(
            select(transaction_table.c.counterparty_iban_masked).where(transaction_table.c.id == tx)
        ).scalar_one()
    )


def _current_pairs(world: World) -> set[tuple]:
    return {
        (r.out_transaction_id, r.in_movement_id, r.matched_by)
        for r in world.query(transfers._current_links)
    }


def _paired(world, bank, blob_store, *, guess: C = C.INTERNAL_TRANSFER_OUT):
    """The real case: a 1,000 € debit to the exchange's IBAN, the model's guess on it, then the
    exchange's export with its exact deposit the next day. The matcher links the pair by amount
    and date when the guess is the transfer; nothing is tied, so the line stays a proposal."""
    out = bank.line(date(2026, 9, 7), "-1000.00", name="Musterbörse", iban=DEPOSIT_IBAN)
    _propose(world, out, "-1000.00", guess)
    exchange = _exchange(world)
    _export(world, blob_store, exchange.id, "2026-09-08 08:00:00,Deposit,1000")
    deposit = _movements(world, exchange.id)[Decimal("1000.00")]
    _clear_jobs(world)
    return out, exchange, deposit


def _linked_payload(world: World) -> dict:
    [event] = world.query(
        lambda c: c.execute(
            select(event_table).where(event_table.c.type == "transfer.linked")
        ).all()
    )
    return event.payload


def _assert_learned(world: World, out, exchange) -> None:
    printed = _printed(world, out)
    account = _account(world, exchange.id)
    assert account.iban_masked == printed and account.iban_hmac is not None
    [updated] = _account_updates(world, exchange.id)
    assert updated.payload["changes"]["iban_masked"] == {"old": None, "new": printed}
    assert updated.actor_type == ActorType.USER.value
    assert enqueue.waiting_trigger(enqueue.TRIGGER_ACCOUNT, exchange.id) in _queued(world)


def _assert_not_learned(world: World, exchange) -> None:
    account = _account(world, exchange.id)
    assert account.iban_hmac is None and account.iban_masked is None
    assert _account_updates(world, exchange.id) == []


# --- A person's confirmation teaches the IBAN ------------------------------------------------


def test_confirming_a_matcher_linked_deposit_learns_the_exchange_iban(world, bank, blob_store):
    out, exchange, deposit = _paired(world, bank, blob_store)
    assert _current_pairs(world) == {(out, deposit, TransferMatchedBy.MATCHER.value)}
    assert _booked(world, out) == [
        ("internal_transfer_out", Decimal("-1000.00"), "proposed", "llm")
    ]
    _assert_not_learned(world, exchange)
    _answer_line(world, out, "-1000.00", "internal_transfer_out")  # the plain confirm
    assert _booked(world, out) == [
        ("internal_transfer_out", Decimal("-1000.00"), "confirmed", "llm")
    ]
    _assert_learned(world, out, exchange)
    # the link stands, and the matcher's transfer.linked names no outcome (written before)
    assert _current_pairs(world) == {(out, deposit, TransferMatchedBy.MATCHER.value)}
    linked = _linked_payload(world)
    assert linked.get("deposit_iban") is None


def test_the_exchange_export_first_then_the_bank_line(world, bank, blob_store):
    """The other import order: the bank line's own booking hook links it to the deposit."""
    exchange = _exchange(world)
    _export(world, blob_store, exchange.id, "2026-09-08 08:00:00,Deposit,1000")
    deposit = _movements(world, exchange.id)[Decimal("1000.00")]
    out = bank.line(date(2026, 9, 7), "-1000.00", name="Musterbörse", iban=DEPOSIT_IBAN)
    _propose(world, out, "-1000.00", C.INTERNAL_TRANSFER_OUT)
    assert _current_pairs(world) == {(out, deposit, TransferMatchedBy.MATCHER.value)}
    _assert_not_learned(world, exchange)
    _clear_jobs(world)
    _answer_line(world, out, "-1000.00", "internal_transfer_out")
    _assert_learned(world, out, exchange)


def test_an_answer_that_creates_the_link_learns_after_it(world, bank, blob_store):
    """The model guessed an investment, so the matcher left the pair unlinked; the person's
    answer books the transfer, its hook links the pair, and the IBAN is learned after it."""
    out, exchange, deposit = _paired(world, bank, blob_store, guess=C.HH_INVESTMENT)
    assert _links(world) == []
    _answer_line(world, out, "-1000.00", "internal_transfer_out")
    assert _booked(world, out) == [
        ("internal_transfer_out", Decimal("-1000.00"), "confirmed", "user")
    ]
    assert _current_pairs(world) == {(out, deposit, TransferMatchedBy.MATCHER.value)}
    _assert_learned(world, out, exchange)


def test_the_transfer_with_its_fee_counts_as_the_transfer_out(world, bank, blob_store):
    """A person answers the paired line as the transfer plus a bank fee within 2 €: still the
    transfer's out leg, so the IBAN is learned."""
    out, exchange, _ = _paired(world, bank, blob_store, guess=C.HH_INVESTMENT)
    answer = AllocationSet(
        transaction_id=out,
        method=AllocationMethod.USER,
        confidence=Decimal("1.000"),
        allocations=[
            AllocationDraft(amount=Decimal("-999.50"), category=C.INTERNAL_TRANSFER_OUT),
            AllocationDraft(amount=Decimal("-0.50"), category=C.BANK_FEES),
        ],
    )
    world.cmd(
        review.allocate_transaction,
        out,
        ConfirmAllocationsBody(allocation_set=answer, learn_rule=False),
        iban_key=TEST_IBAN_KEY,
    )
    deposit = _movements(world, exchange.id)[Decimal("1000.00")]
    assert _current_pairs(world) == {(out, deposit, TransferMatchedBy.MATCHER.value)}
    _assert_learned(world, out, exchange)


# --- Nothing learned ------------------------------------------------------------------------


def test_the_models_or_the_matchers_own_confirmation_teaches_nothing(world, bank, blob_store):
    out, exchange, _ = _paired(world, bank, blob_store)
    auto = AllocationSet(
        transaction_id=out,
        method=AllocationMethod.LLM,
        confidence=Decimal("0.990"),
        allocations=[AllocationDraft(amount=Decimal("-1000.00"), category=C.INTERNAL_TRANSFER_OUT)],
    )
    world.cmd(allocations.record_set, auto, confirm=True, auto=True)
    assert _booked(world, out) == [
        ("internal_transfer_out", Decimal("-1000.00"), "confirmed", "llm")
    ]
    _assert_not_learned(world, exchange)


def test_a_command_that_is_no_persons_teaches_nothing(world, bank, blob_store):
    """The answer command run by a CLI actor (no person): the IBAN stays unknown."""
    out, exchange, _ = _paired(world, bank, blob_store)
    world.cmd(
        review.allocate_transaction,
        out,
        ConfirmAllocationsBody(
            allocation_set=_user_set(out, "-1000.00", "internal_transfer_out"), learn_rule=False
        ),
        iban_key=TEST_IBAN_KEY,
        actor=SYSTEM,
    )
    _assert_not_learned(world, exchange)


def test_a_line_confirmed_as_something_else_teaches_nothing(world, bank, blob_store):
    out, exchange, _ = _paired(world, bank, blob_store)
    _answer_line(world, out, "-1000.00", "hh_investment")
    assert _current_pairs(world) == set()  # the matcher's link is withdrawn by the answer
    _assert_not_learned(world, exchange)


def test_a_line_answered_otherwise_on_a_link_by_hand_teaches_nothing(world, bank, blob_store):
    """A link a person made stays current whatever the line is booked as: an answer other than
    the transfer out never teaches the IBAN."""
    out, exchange, deposit = _paired(world, bank, blob_store, guess=C.HH_INVESTMENT)
    link = _link(world, out, deposit)
    assert link.matched_by is TransferMatchedBy.USER
    _answer_line(world, out, "-1000.00", "hh_investment")
    assert _current_pairs(world) == {(out, deposit, TransferMatchedBy.USER.value)}
    _assert_not_learned(world, exchange)


def test_a_withdrawals_bank_credit_never_teaches_the_payout_iban(world, bank, blob_store):
    exchange = _exchange(world)
    _export(world, blob_store, exchange.id, "2026-10-12 08:00:00,Withdraw,-500")
    credit = bank.line(date(2026, 10, 13), "500.00", name="Musterbörse", iban=PAYOUT_IBAN)
    _propose(world, credit, "500.00", C.INTERNAL_TRANSFER_IN)
    withdrawal = _movements(world, exchange.id)[Decimal("-500.00")]
    [link] = [r for r in _links(world) if r.in_transaction_id == credit]
    assert link.out_movement_id == withdrawal
    _answer_line(world, credit, "500.00", "internal_transfer_in")
    _assert_not_learned(world, exchange)


def test_a_shared_iban_is_refused_and_the_link_stands(world, bank, blob_store):
    """The IBAN also carries another payee's lines (a payment provider's IBAN): not learned."""
    bank.line(date(2026, 8, 3), "-19.99", name="Musterladen Versand GmbH", iban=DEPOSIT_IBAN)
    out, exchange, deposit = _paired(world, bank, blob_store)
    assert _current_pairs(world) == {(out, deposit, TransferMatchedBy.MATCHER.value)}
    _answer_line(world, out, "-1000.00", "internal_transfer_out")
    _assert_not_learned(world, exchange)
    assert _current_pairs(world) == {(out, deposit, TransferMatchedBy.MATCHER.value)}
    outcome = world.cmd(transfers.learn_deposit_iban_on_confirm, out)
    assert outcome is DepositIbanOutcome.SHARED_IBAN


def test_an_iban_another_own_account_holds_is_refused(world, bank, blob_store):
    out, exchange, deposit = _paired(world, bank, blob_store)
    world.owner_account(DEPOSIT_IBAN)  # an own account holds the IBAN since
    assert _current_pairs(world) == {(out, deposit, TransferMatchedBy.MATCHER.value)}
    _answer_line(world, out, "-1000.00", "internal_transfer_out")
    _assert_not_learned(world, exchange)
    assert world.cmd(transfers.learn_deposit_iban_on_confirm, out) is (
        DepositIbanOutcome.OWN_ACCOUNT
    )


def test_an_exchange_with_an_iban_already_learns_nothing_more(world, bank, blob_store):
    out, exchange, _ = _paired(world, bank, blob_store)
    _answer_line(world, out, "-1000.00", "internal_transfer_out")
    learned = _account(world, exchange.id).iban_hmac
    # a later deposit paid from the exchange's other IBAN, linked by hand and confirmed
    other = bank.line(date(2026, 10, 5), "-500.00", name="Musterbörse", iban=OTHER_IBAN)
    _propose(world, other, "-500.00", C.INTERNAL_TRANSFER_OUT)
    _export(world, blob_store, exchange.id, "2026-10-06 08:00:00,Deposit,500")
    _link(world, other, _movements(world, exchange.id)[Decimal("500.00")])
    _answer_line(world, other, "-500.00", "internal_transfer_out")
    assert _account(world, exchange.id).iban_hmac == learned
    assert len(_account_updates(world, exchange.id)) == 1
    assert world.cmd(transfers.learn_deposit_iban_on_confirm, other) is None


# --- The next deposit is tied by the learned IBAN --------------------------------------------


def test_the_next_deposit_is_booked_and_linked_by_the_learned_iban(world, bank, blob_store):
    out, exchange, first_deposit = _paired(world, bank, blob_store)
    _answer_line(world, out, "-1000.00", "internal_transfer_out")
    _export(world, blob_store, exchange.id, "2026-10-06 08:00:00,Deposit,1000")
    later = bank.line(date(2026, 10, 5), "-1000.00", name="Musterbörse", iban=DEPOSIT_IBAN)
    world.cmd(matching.match_line, later, iban_key=TEST_IBAN_KEY, ai_available=False)
    assert _booked(world, later) == [
        ("internal_transfer_out", Decimal("-1000.00"), "confirmed", "matcher_transfer")
    ]
    [pair] = [p for p in _current_pairs(world) if p[0] == later]
    assert pair[1] != first_deposit and pair[2] == TransferMatchedBy.MATCHER.value
    assert _open(world) == []


def test_a_waiting_deposit_is_booked_by_the_queued_recheck(
    world, bank, blob_store, app_engine, tmp_path
):
    """A second deposit the matcher had linked by amount and date while it held only the
    model's proposal is booked by the matcher once the first confirmation taught the IBAN."""
    first = bank.line(date(2026, 9, 7), "-1000.00", name="Musterbörse", iban=DEPOSIT_IBAN)
    second = bank.line(date(2026, 10, 5), "-1000.00", name="Musterbörse", iban=DEPOSIT_IBAN)
    for tx in (first, second):
        _propose(world, tx, "-1000.00", C.INTERNAL_TRANSFER_OUT)
    exchange = _exchange(world)
    _export(
        world,
        blob_store,
        exchange.id,
        "2026-09-08 08:00:00,Deposit,1000",
        "2026-10-06 08:00:00,Deposit,1000",
    )
    assert len(_current_pairs(world)) == 2
    _clear_jobs(world)
    _answer_line(world, first, "-1000.00", "internal_transfer_out")
    trigger = enqueue.waiting_trigger(enqueue.TRIGGER_ACCOUNT, exchange.id)
    assert _queued(world) == [trigger]
    _recheck(app_engine, tmp_path, world, trigger)
    assert _booked(world, second) == [
        ("internal_transfer_out", Decimal("-1000.00"), "confirmed", "matcher_transfer")
    ]
    assert len(_current_pairs(world)) == 2
    assert _open(world) == []


# --- A link by hand of an exact pair -----------------------------------------------------------


def test_a_link_by_hand_of_a_confirmed_line_learns_and_names_it(world, bank, blob_store):
    """The person had confirmed the line as a transfer before the exchange's export arrived;
    two equal deposits make the card ambiguous, and the person links one by hand."""
    out, exchange, moves = _two_equal_deposits(world, bank, blob_store, confirm=True)
    link = _link(world, out, moves[0])
    assert link.matched_by is TransferMatchedBy.USER
    linked = _linked_payload(world)
    assert linked["deposit_iban"] == "learned"
    _assert_learned(world, out, exchange)


def test_a_link_by_hand_of_a_proposed_line_waits_for_the_persons_confirmation(
    world, bank, blob_store
):
    """The line holds only the model's guess when the person links it by hand: nothing is
    learned by the link; the person's confirmation of the line afterwards teaches the IBAN."""
    out, exchange, moves = _two_equal_deposits(world, bank, blob_store)
    link = _link(world, out, moves[0])
    assert link.matched_by is TransferMatchedBy.USER
    linked = _linked_payload(world)
    assert linked.get("deposit_iban") is None
    _assert_not_learned(world, exchange)
    _answer_line(world, out, "-1000.00", "internal_transfer_out")
    _assert_learned(world, out, exchange)


def _two_equal_deposits(world, bank, blob_store, *, confirm: bool = False):
    """A 1,000 € debit and two equal deposits the next day: the matcher links none of them."""
    out = bank.line(date(2026, 9, 7), "-1000.00", name="Musterbörse", iban=DEPOSIT_IBAN)
    if confirm:
        _answer_line(world, out, "-1000.00", "internal_transfer_out")
    else:
        _propose(world, out, "-1000.00", C.INTERNAL_TRANSFER_OUT)
    exchange = _exchange(world)
    _export(
        world,
        blob_store,
        exchange.id,
        "2026-09-08 08:00:00,Deposit,1000",
        "2026-09-08 09:00:00,Deposit,1000",
    )
    assert _links(world) == []
    _assert_not_learned(world, exchange)
    moves = [
        m.id for m in world.query(_exchange_moves, exchange.id) if m.eur_amount == Decimal("1000")
    ]
    assert len(moves) == 2
    _clear_jobs(world)
    return out, exchange, moves


def _exchange_moves(conn, account_id):
    from hausbuch.services.exchange import imports

    return imports.exchange_movements(conn, account_id)
