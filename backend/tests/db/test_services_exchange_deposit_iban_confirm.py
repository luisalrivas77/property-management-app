"""Phase 6 D162 amended (8), 15 Nov 2026 under hausbuch_app and RLS: a person who confirms a
private bank line as a transfer out that the matcher paired with an exchange's EUR deposit by
amount and date alone (untied: the exchange holds no IBAN yet), with the panel's box „IBAN als
Einzahlungskonto von <Börse> merken“ ticked (its review the same day: never silently, since
an unrelated own account paying the same amount the day before would teach a foreign IBAN),
teaches the exchange the line's IBAN, so the next deposit to it is tied by IBAN, booked and
linked by the matcher. Never unticked, never a rule's, the matcher's or the model's own
confirmation; the guards of accounts.learn_exchange_deposit_iban (another own account's IBAN,
a shared IBAN, an exchange with an IBAN already) stay. Invented names and IBANs only."""

from datetime import date
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.matching_support import USER, World
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

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.jobs import enqueue
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.api import ConfirmAllocationsBody, DepositIbanOffer, TransactionAllocations
from hausbuch.schemas.enums import ActorType
from hausbuch.schemas.household import DepositIbanOutcome, TransferMatchedBy
from hausbuch.services import allocations, matching, review, transfers
from hausbuch.services.context import Actor, new_command

pytestmark = pytest.mark.db

C = AllocationCategory
TODAY = NOW.date()
OTHER_IBAN = "DE88100000000000000093"  # another IBAN of the same exchange's bank
SYSTEM = Actor(ActorType.SYSTEM, "cli:test")
LEARN_TEXT = (
    "„Verknüpfen“ merkt sich die IBAN dieser Zahlung als Einzahlungskonto von Binance."
    " Spätere Zahlungen an dieselbe IBAN gelten dann als Einzahlungen auf Binance."
)
OFFER_TEXT = (
    "Diese Zahlung ist mit der Einzahlung vom 08.09.2026 über 1.000,00 € auf Binance verknüpft."
    " „Bestätigen“ mit Häkchen merkt sich die IBAN dieser Zahlung als Einzahlungskonto von"
    " Binance. Spätere Zahlungen an dieselbe IBAN gelten dann als Einzahlungen auf Binance."
    " Ohne Häkchen wird die IBAN nicht übernommen."
)


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


def _confirm(
    world: World,
    tx,
    amount: str,
    category: str,
    *,
    box: bool | None,
    actor: Actor | None = None,
    answer: AllocationSet | None = None,
) -> TransactionAllocations:
    """The panel's „Bestätigen“: the person's answer with the box „IBAN als Einzahlungskonto
    von <Börse> merken“ ticked (True), unticked (False) or not sent at all (None, an older
    client)."""
    body = ConfirmAllocationsBody(
        allocation_set=answer or _user_set(tx, amount, category), learn_rule=False
    )
    if box is not None:
        body = body.model_copy(update={"learn_deposit_iban": box})
    extra = {} if actor is None else {"actor": actor}
    return world.cmd(review.allocate_transaction, tx, body, iban_key=TEST_IBAN_KEY, **extra)


def _offer(world: World, tx) -> DepositIbanOffer | None:
    """What the panel's GET shows for the box (None: no box)."""
    view = world.query(review.transaction_allocations, tx, iban_key=TEST_IBAN_KEY, today=TODAY)
    return view.deposit_iban_offer


def _assert_offer(offer: DepositIbanOffer | None, exchange, deposit) -> None:
    assert offer is not None
    assert offer.deposit_movement_id == deposit
    assert offer.exchange_account_id == exchange.id
    assert offer.exchange_label == "Binance"
    assert (offer.deposit_date, offer.deposit_amount) == (date(2026, 9, 8), Decimal("1000.00"))
    assert offer.text == OFFER_TEXT


def _stale_offer(exchange, deposit) -> DepositIbanOffer:
    """An offer as an earlier GET showed it (a client that kept it), for the deposit."""
    return DepositIbanOffer(
        deposit_movement_id=deposit,
        exchange_account_id=exchange.id,
        exchange_label="Binance",
        deposit_date=date(2026, 9, 8),
        deposit_amount=Decimal("1000.00"),
        text=OFFER_TEXT,
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
    _assert_offer(_offer(world, out), exchange, deposit)  # the box, naming the deposit
    view = _confirm(world, out, "-1000.00", "internal_transfer_out", box=True)
    assert _booked(world, out) == [
        ("internal_transfer_out", Decimal("-1000.00"), "confirmed", "llm")
    ]
    _assert_learned(world, out, exchange)
    assert view.deposit_iban_offer is None  # learned: no box any more
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
    _assert_offer(_offer(world, out), exchange, deposit)
    _confirm(world, out, "-1000.00", "internal_transfer_out", box=True)
    _assert_learned(world, out, exchange)


def test_an_answer_that_creates_the_link_learns_only_once_its_box_is_shown(world, bank, blob_store):
    """The model guessed an investment, so the matcher left the pair unlinked and the panel
    showed no box; the person's answer books the transfer and its hook links the pair. A flag
    sent with it teaches nothing (the pair was never on the panel); the panel then shows the
    box, and „Bestätigen“ with it ticked, the same answer, learns."""
    out, exchange, deposit = _paired(world, bank, blob_store, guess=C.HH_INVESTMENT)
    assert _links(world) == [] and _offer(world, out) is None
    view = _confirm(world, out, "-1000.00", "internal_transfer_out", box=True)
    assert _booked(world, out) == [
        ("internal_transfer_out", Decimal("-1000.00"), "confirmed", "user")
    ]
    assert _current_pairs(world) == {(out, deposit, TransferMatchedBy.MATCHER.value)}
    _assert_not_learned(world, exchange)
    _assert_offer(view.deposit_iban_offer, exchange, deposit)
    _confirm(world, out, "-1000.00", "internal_transfer_out", box=True)
    _assert_learned(world, out, exchange)


def test_the_transfer_with_its_fee_counts_as_the_transfer_out(world, bank, blob_store):
    """A person answers the paired line as the transfer plus a bank fee within 2 €: still the
    transfer's out leg, so the IBAN is learned."""
    out, exchange, deposit = _paired(world, bank, blob_store)
    _assert_offer(_offer(world, out), exchange, deposit)
    answer = AllocationSet(
        transaction_id=out,
        method=AllocationMethod.USER,
        confidence=Decimal("1.000"),
        allocations=[
            AllocationDraft(amount=Decimal("-999.50"), category=C.INTERNAL_TRANSFER_OUT),
            AllocationDraft(amount=Decimal("-0.50"), category=C.BANK_FEES),
        ],
    )
    _confirm(world, out, "", "", box=True, answer=answer)
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
    _confirm(world, out, "-1000.00", "internal_transfer_out", box=True, actor=SYSTEM)
    _assert_not_learned(world, exchange)


def test_a_line_confirmed_as_something_else_teaches_nothing(world, bank, blob_store):
    out, exchange, _ = _paired(world, bank, blob_store)
    _confirm(world, out, "-1000.00", "hh_investment", box=True)
    assert _current_pairs(world) == set()  # the matcher's link is withdrawn by the answer
    _assert_not_learned(world, exchange)


def test_a_line_answered_otherwise_on_a_link_by_hand_teaches_nothing(world, bank, blob_store):
    """A link a person made stays current whatever the line is booked as: an answer other than
    the transfer out never teaches the IBAN."""
    out, exchange, deposit = _paired(world, bank, blob_store, guess=C.HH_INVESTMENT)
    link = _link(world, out, deposit)
    assert link.matched_by is TransferMatchedBy.USER
    _assert_offer(_offer(world, out), exchange, deposit)  # the panel shows it for a transfer
    _confirm(world, out, "-1000.00", "hh_investment", box=True)
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
    assert _offer(world, credit) is None
    _confirm(world, credit, "500.00", "internal_transfer_in", box=True)
    _assert_not_learned(world, exchange)


def test_a_shared_iban_is_refused_and_the_link_stands(world, bank, blob_store):
    """The IBAN also carries another payee's lines (a payment provider's IBAN): not learned."""
    bank.line(date(2026, 8, 3), "-19.99", name="Musterladen Versand GmbH", iban=DEPOSIT_IBAN)
    out, exchange, deposit = _paired(world, bank, blob_store)
    assert _current_pairs(world) == {(out, deposit, TransferMatchedBy.MATCHER.value)}
    assert _offer(world, out) is None  # a refused outcome shows no box
    _confirm(world, out, "-1000.00", "internal_transfer_out", box=True)
    _assert_not_learned(world, exchange)
    assert _current_pairs(world) == {(out, deposit, TransferMatchedBy.MATCHER.value)}
    # a client that kept an earlier offer: refused by the guard
    stale = _stale_offer(exchange, deposit)
    outcome = world.cmd(transfers.learn_deposit_iban_on_confirm, out, stale)
    assert outcome is DepositIbanOutcome.SHARED_IBAN


def test_an_iban_another_own_account_holds_is_refused(world, bank, blob_store):
    out, exchange, deposit = _paired(world, bank, blob_store)
    world.owner_account(DEPOSIT_IBAN)  # an own account holds the IBAN since
    assert _current_pairs(world) == {(out, deposit, TransferMatchedBy.MATCHER.value)}
    assert _offer(world, out) is None
    _confirm(world, out, "-1000.00", "internal_transfer_out", box=True)
    _assert_not_learned(world, exchange)
    stale = _stale_offer(exchange, deposit)
    assert world.cmd(transfers.learn_deposit_iban_on_confirm, out, stale) is (
        DepositIbanOutcome.OWN_ACCOUNT
    )


def test_an_exchange_with_an_iban_already_learns_nothing_more(world, bank, blob_store):
    out, exchange, _ = _paired(world, bank, blob_store)
    _confirm(world, out, "-1000.00", "internal_transfer_out", box=True)
    learned = _account(world, exchange.id).iban_hmac
    # a later deposit paid from the exchange's other IBAN, linked by hand and confirmed
    other = bank.line(date(2026, 10, 5), "-500.00", name="Musterbörse", iban=OTHER_IBAN)
    _propose(world, other, "-500.00", C.INTERNAL_TRANSFER_OUT)
    _export(world, blob_store, exchange.id, "2026-10-06 08:00:00,Deposit,500")
    second = _movements(world, exchange.id)[Decimal("500.00")]
    _link(world, other, second)
    assert _offer(world, other) is None
    _confirm(world, other, "-500.00", "internal_transfer_out", box=True)
    assert _account(world, exchange.id).iban_hmac == learned
    assert len(_account_updates(world, exchange.id)) == 1
    stale = _stale_offer(exchange, second)
    assert world.cmd(transfers.learn_deposit_iban_on_confirm, other, stale) is None


# --- The next deposit is tied by the learned IBAN --------------------------------------------


def test_the_next_deposit_is_booked_and_linked_by_the_learned_iban(world, bank, blob_store):
    out, exchange, first_deposit = _paired(world, bank, blob_store)
    _confirm(world, out, "-1000.00", "internal_transfer_out", box=True)
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
    _confirm(world, first, "-1000.00", "internal_transfer_out", box=True)
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
    # the cards say beforehand that the link learns the IBAN; the button stays „Verknüpfen“
    cards = {c.leg.id: c for c in _open(world)}
    assert {c.id for c in cards[out].candidates} == set(moves)
    for candidate in cards[out].candidates:
        assert candidate.deposit_fee is None
        assert candidate.deposit_fee_text == LEARN_TEXT
    link = _link(world, out, moves[0])
    assert link.matched_by is TransferMatchedBy.USER
    linked = _linked_payload(world)
    assert linked["deposit_iban"] == "learned"
    _assert_learned(world, out, exchange)


def test_a_card_names_a_shared_iban_and_a_proposed_line_says_nothing(world, bank, blob_store):
    bank.line(date(2026, 8, 3), "-19.99", name="Musterladen Versand GmbH", iban=DEPOSIT_IBAN)
    out, _, moves = _two_equal_deposits(world, bank, blob_store, confirm=True)
    cards = {c.leg.id: c for c in _open(world)}
    assert {c.deposit_fee_text for c in cards[out].candidates} == {
        "Die IBAN wird nicht übernommen, weil an sie auch andere Empfänger gehen."
    }
    _link(world, out, moves[0])
    assert _linked_payload(world)["deposit_iban"] == "shared_iban"


def test_a_proposed_lines_card_says_nothing_about_the_iban(world, bank, blob_store):
    out, _, _ = _two_equal_deposits(world, bank, blob_store)
    cards = {c.leg.id: c for c in _open(world)}
    assert {c.deposit_fee_text for c in cards[out].candidates} == {None}


def test_a_click_on_a_pair_the_matcher_linked_since_learns(world, bank, blob_store):
    """Not covered by the confirmation: the person confirmed the line before the exchange's
    export arrived, and the matcher linked the pair at the import (no person's act, nothing
    learned). A click on the card shown before (link_by_hand finds the link) learns it."""
    out = bank.line(date(2026, 9, 7), "-1000.00", name="Musterbörse", iban=DEPOSIT_IBAN)
    _answer_line(world, out, "-1000.00", "internal_transfer_out")
    exchange = _exchange(world)
    _export(world, blob_store, exchange.id, "2026-09-08 08:00:00,Deposit,1000")
    deposit = _movements(world, exchange.id)[Decimal("1000.00")]
    assert _current_pairs(world) == {(out, deposit, TransferMatchedBy.MATCHER.value)}
    _assert_not_learned(world, exchange)
    _clear_jobs(world)
    link = _link(world, out, deposit)
    assert link.matched_by is TransferMatchedBy.MATCHER  # the matcher's link is returned
    _assert_learned(world, out, exchange)


def test_a_link_by_hand_of_a_proposed_line_waits_for_the_persons_confirmation(
    world, bank, blob_store
):
    """The line holds only the model's guess when the person links it by hand: nothing is
    learned by the link; the person's confirmation of the line afterwards, with the panel's
    box ticked, teaches the IBAN."""
    out, exchange, moves = _two_equal_deposits(world, bank, blob_store)
    link = _link(world, out, moves[0])
    assert link.matched_by is TransferMatchedBy.USER
    linked = _linked_payload(world)
    assert linked.get("deposit_iban") is None
    _assert_not_learned(world, exchange)
    assert _offer(world, out) is not None  # the person's link: the panel shows the box
    _confirm(world, out, "-1000.00", "internal_transfer_out", box=True)
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


# --- The box: an explicit choice, never a silent one (D162 amended (8), review) ----------------

UNRELATED_IBAN = "DE88100000000000000093"  # an own account that is not in the app


def _wrong_pair(world, bank, blob_store):
    """The review's scenario: a transfer to an own account that is not in the app, and an
    exchange deposit of the same amount the next day paid from elsewhere. The matcher pairs
    them by amount and date alone; the person sees the pair on the panel."""
    out = bank.line(
        date(2026, 9, 7),
        "-1000.00",
        name="Erika Beispiel",
        iban=UNRELATED_IBAN,
        purpose="Uebertrag Sparkonto",
    )
    _propose(world, out, "-1000.00", C.INTERNAL_TRANSFER_OUT)
    exchange = _exchange(world)
    _export(world, blob_store, exchange.id, "2026-09-08 08:00:00,Deposit,1000")
    deposit = _movements(world, exchange.id)[Decimal("1000.00")]
    assert _current_pairs(world) == {(out, deposit, TransferMatchedBy.MATCHER.value)}
    _clear_jobs(world)
    return out, exchange, deposit


@pytest.mark.parametrize("box", [False, None], ids=["unticked", "older_client"])
def test_a_wrong_pair_confirmed_with_the_box_unticked_teaches_nothing(world, bank, blob_store, box):
    """The panel names the deposit the line is paired with, so the person sees it is not the
    transfer to the exchange, unticks the box and confirms the category: no IBAN is learned,
    and an older client that sends no box learns nothing either."""
    out, exchange, deposit = _wrong_pair(world, bank, blob_store)
    _assert_offer(_offer(world, out), exchange, deposit)
    _confirm(world, out, "-1000.00", "internal_transfer_out", box=box)
    assert _booked(world, out) == [
        ("internal_transfer_out", Decimal("-1000.00"), "confirmed", "llm")
    ]
    _assert_not_learned(world, exchange)
    assert _queued(world) == []


def test_the_same_pair_confirmed_with_the_box_ticked_learns_what_the_panel_named(
    world, bank, blob_store
):
    """Ticked, the same confirmation learns: the box is the person's own choice, made with the
    deposit's date and amount in front of them."""
    out, exchange, deposit = _wrong_pair(world, bank, blob_store)
    offer = _offer(world, out)
    _assert_offer(offer, exchange, deposit)
    assert "vom 08.09.2026 über 1.000,00 €" in offer.text
    _confirm(world, out, "-1000.00", "internal_transfer_out", box=True)
    _assert_learned(world, out, exchange)


def test_an_offer_for_another_deposit_teaches_nothing(world, bank, blob_store):
    """A box shown for one deposit never learns from a link to another: the line must still be
    the out leg of the link to the deposit the panel named."""
    out, exchange, _ = _paired(world, bank, blob_store)
    _confirm(world, out, "-1000.00", "internal_transfer_out", box=False)
    other = _stale_offer(exchange, out)  # names no deposit of this link
    assert world.cmd(transfers.learn_deposit_iban_on_confirm, out, other) is None
    _assert_not_learned(world, exchange)


# --- The link by hand learns only from a person's own confirmation (_confirmed_by_person) -------


def test_a_line_auto_confirmed_inside_a_persons_command_teaches_nothing_on_a_hand_link(
    world, bank, blob_store
):
    """The model's or the matcher's confirmation written with auto=True while the command's
    actor is the person (as one inside a person's request): no IBAN, and the card says
    nothing about it."""
    out, exchange, moves = _two_equal_deposits(world, bank, blob_store)
    auto = AllocationSet(
        transaction_id=out,
        method=AllocationMethod.LLM,
        confidence=Decimal("0.990"),
        allocations=[AllocationDraft(amount=Decimal("-1000.00"), category=C.INTERNAL_TRANSFER_OUT)],
    )
    world.cmd(allocations.record_set, auto, confirm=True, auto=True)  # the actor is the person
    assert _links(world) == []
    cards = {c.leg.id: c for c in _open(world)}
    assert {c.deposit_fee_text for c in cards[out].candidates} == {None}
    _link(world, out, moves[0])
    assert _linked_payload(world).get("deposit_iban") is None
    _assert_not_learned(world, exchange)


def test_a_persons_answer_rebooked_automatically_teaches_nothing_on_a_hand_link(
    world, bank, blob_store
):
    """A person answers the line first; an automatic re-booking as the transfer out follows
    (auto=True): the latest confirmation is not the person's, so the link teaches nothing."""
    out, exchange, moves = _two_equal_deposits(world, bank, blob_store)
    _answer_line(world, out, "-1000.00", "hh_investment")
    auto = AllocationSet(
        transaction_id=out,
        method=AllocationMethod.LLM,
        confidence=Decimal("0.990"),
        allocations=[AllocationDraft(amount=Decimal("-1000.00"), category=C.INTERNAL_TRANSFER_OUT)],
    )
    world.cmd(allocations.record_set, auto, confirm=True, auto=True)
    _link(world, out, moves[0])
    _assert_not_learned(world, exchange)


# --- Lock order (review): the exchange's row is locked only to learn -------------------------


def test_an_answer_never_waits_for_an_exchange_that_holds_its_iban(world, bank, blob_store):
    """Another transaction holds the exchange's account row (an import of its export takes it
    first). A person's changed answer on a line linked to its deposit, the box sent, still
    completes: the exchange holds an IBAN already, so nothing locks its row. Also a client
    that kept an earlier offer reads the outcome first and never waits."""
    out, exchange, _ = _paired(world, bank, blob_store)
    _confirm(world, out, "-1000.00", "internal_transfer_out", box=True)
    assert _account(world, exchange.id).iban_hmac is not None
    other = bank.line(date(2026, 10, 5), "-500.00", name="Musterbörse", iban=OTHER_IBAN)
    _propose(world, other, "-500.00", C.HH_INVESTMENT)
    _export(world, blob_store, exchange.id, "2026-10-06 08:00:00,Deposit,500")
    second = _movements(world, exchange.id)[Decimal("500.00")]
    _link(world, other, second)
    with workspace_transaction(world.engine, world.ws) as holder:
        holder.execute(
            select(account_table.c.id).where(account_table.c.id == exchange.id).with_for_update()
        )
        with workspace_transaction(world.engine, world.ws) as conn:
            conn.execute(text("SET LOCAL lock_timeout = '2s'"))
            ctx = new_command(conn, world.ws, USER, now=world.now)
            body = ConfirmAllocationsBody(
                allocation_set=_user_set(other, "-500.00", "internal_transfer_out"),
                learn_rule=False,
                learn_deposit_iban=True,
            )
            review.allocate_transaction(ctx, other, body, iban_key=TEST_IBAN_KEY)
            stale = _stale_offer(exchange, second)
            assert transfers.learn_deposit_iban_on_confirm(ctx, other, stale) is None
    assert _booked(world, other) == [
        ("internal_transfer_out", Decimal("-500.00"), "confirmed", "user")
    ]
    assert len(_account_updates(world, exchange.id)) == 1
