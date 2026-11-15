"""A returned payment paired with its original under hausbuch_app and RLS (docs/phases/
phase-6.md, D162 amended (7), 15 Nov 2026): the „Rücküberweisung erkannt“ card computed on
read, its button booking both lines through the person's answer and linking them, what the
pair then counts as (nothing), and `hausbuch pair-returns`. The pure half is
tests/rules/test_household_returns.py. Invented names, IBANs and amounts only.

The recall case is seeded as it reached Zu prüfen: the debit to a third party waits with the
model's wrong guess (hh_property_cost); the credit names another own account's IBAN, so the
transfer matcher books it as internal_transfer_in, and with no counterpart it would wait as
„Überweisung ohne Gegenstück“ for ever."""

from datetime import date
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.matching_support import ACCOUNT_IBAN, World
from db.test_cli_evals_report import _owner_url
from db.test_services_private_matching import NOW, PRIVATE_IBAN, Private
from db.test_services_transfers import FRIEND_IBAN, SAVINGS_IBAN, _savings
from sqlalchemy import select

from hausbuch import cli
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import transfer_link as link_table
from hausbuch.rules.errors import TRANSFER_RETURN_INVALID
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.household import (
    OpenTransferCode,
    ReturnedPaymentCreate,
    TransferLinkKind,
    TransferMatchedBy,
)
from hausbuch.services import (
    allocations,
    household,
    matching,
    returns,
    review,
    statements,
    transfers,
)
from hausbuch.services.errors import ConflictError, RuleViolationError

pytestmark = pytest.mark.db

C = AllocationCategory
TODAY = NOW.date()
SENT = date(2026, 7, 28)
BACK = date(2026, 7, 30)
ORIGINAL = "Umbuchung Ruecklage Juli"
RECALL = f"RUECKUEBERWEISUNG Rueckgabe aufgrund eines Recalls (Rueckrufes) {ORIGINAL}"
TRANSFER_CARDS = {InboxItemKind.TRANSFER_LINK, InboxItemKind.TRANSFER_UNMATCHED}


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


@pytest.fixture
def private(world) -> Private:
    return Private(world)


def _match(world: World, tx):
    return world.cmd(matching.match_line, tx, iban_key=TEST_IBAN_KEY, ai_available=False)


def _answer(world: World, tx, amount: str, category: C, *, method=AllocationMethod.USER):
    """A person's answer (confirmed), or the model's guess (method LLM, a proposal)."""
    person = method is AllocationMethod.USER
    world.cmd(
        allocations.record_set,
        AllocationSet(
            transaction_id=tx,
            allocations=[AllocationDraft(amount=Decimal(amount), category=category)],
            method=method,
            confidence=Decimal("1.000") if person else Decimal("0.700"),
            reasoning=None if person else "Vermutlich Kosten der Wohnung.",
        ),
        confirm=person,
        auto=False,
    )


def _rows(world: World, tx):
    return world.query(
        lambda c: c.execute(
            select(allocation_table.c.category, allocation_table.c.status).where(
                allocation_table.c.transaction_id == tx,
                allocation_table.c.status != "superseded",
            )
        ).all()
    )


def _links(world: World):
    return world.query(
        lambda c: c.execute(select(link_table).where(link_table.c.superseded_by.is_(None))).all()
    )


def _inbox(world: World):
    return world.query(review.inbox, TODAY)


def _cards(world: World):
    return [i for i in _inbox(world) if i.kind is InboxItemKind.RETURNED_PAYMENT]


def _book(world: World, out, in_):
    return world.cmd(
        returns.book_return,
        ReturnedPaymentCreate(out_transaction_id=out, in_transaction_id=in_),
        iban_key=TEST_IBAN_KEY,
    )


def _recall(world: World, private: Private):
    """The 3,200 € sent on 28 Jul with the model's wrong guess, and its recall on 30 Jul,
    booked by the transfer matcher as a transfer in from the savings account's IBAN."""
    _savings(world)
    debit = private.line(SENT, "-3200.00", iban=FRIEND_IBAN, name="Beispiel Bau", purpose=ORIGINAL)
    _answer(world, debit, "-3200.00", C.HH_PROPERTY_COST, method=AllocationMethod.LLM)
    credit = private.line(BACK, "3200.00", iban=SAVINGS_IBAN, name="A Muster", purpose=RECALL)
    _match(world, credit)
    assert _rows(world, credit) == [("internal_transfer_in", "confirmed")]
    assert _rows(world, debit) == [("hh_property_cost", "proposed")]
    return debit, credit


# --- The card ------------------------------------------------------------------------------


def test_the_recall_is_one_card_naming_both_lines(world, private):
    debit, credit = _recall(world, private)
    # The transfer service alone still sees a leg without a counterpart …
    [open_] = world.query(transfers.open_transfers, TODAY)
    assert (open_.code, open_.leg.id) == (OpenTransferCode.UNMATCHED_LEG, credit)
    # … but Zu prüfen asks one question about the pair, in one place.
    items = _inbox(world)
    [card] = [i for i in items if i.kind is InboxItemKind.RETURNED_PAYMENT]
    assert card.transaction_id == credit
    assert card.account_id == private.account_id
    assert card.question == returns.QUESTION_ONE
    assert "zählen die beiden Buchungen als nichts" in card.reasoning
    assert card.returned.leg.id == credit
    assert [c.id for c in card.returned.candidates] == [debit]
    assert card.returned.candidates[0].amount == Decimal("-3200.00")
    assert not [i for i in items if i.kind in TRANSFER_CARDS]
    assert not [i for i in items if i.transaction_id in (debit, credit) and i is not card]


def test_booking_the_pair_makes_both_lines_count_as_nothing(world, private):
    debit, credit = _recall(world, private)
    link = _book(world, debit, credit)
    assert _rows(world, debit) == [("internal_transfer_out", "confirmed")]
    assert _rows(world, credit) == [("internal_transfer_in", "confirmed")]
    [row] = _links(world)
    assert (row.id, row.out_transaction_id, row.in_transaction_id) == (link.id, debit, credit)
    assert (row.kind, row.matched_by) == (TransferLinkKind.BANK.value, TransferMatchedBy.USER.value)
    [event] = world.query(
        lambda c: c.execute(
            select(event_table).where(event_table.c.type == "transfer.linked")
        ).all()
    )
    assert event.entity_id == link.id
    # No card of any kind is left for either line.
    items = _inbox(world)
    assert not [i for i in items if i.transaction_id in (debit, credit)]
    assert not [i for i in items if i.kind in TRANSFER_CARDS | {InboxItemKind.RETURNED_PAYMENT}]
    assert world.query(transfers.open_transfers, TODAY) == []
    # The Haushalt: neither income nor spending nor saved, and nothing left to explain.
    july = world.query(household.cashflow, "2026-07")
    assert (july.income, july.saved, july.open_lines) == (Decimal("0"), Decimal("0"), 0)
    assert july.spending_by_group == {}
    assert world.query(transfers.list_links, start=date(2026, 7, 1), end=date(2026, 7, 31)) == []
    # Konten › Umsätze names each half's partner.
    lines = {t.id: t for t in world.query(statements.list_transactions, private.account_id)}
    assert lines[debit].returned_with.transaction_id == credit
    assert lines[debit].returned_with.booking_date == BACK
    assert lines[credit].returned_with.transaction_id == debit
    assert lines[credit].returned_with.amount == Decimal("-3200.00")
    # The same pair again returns its link and writes nothing.
    assert _book(world, debit, credit).id == link.id
    assert len(_links(world)) == 1


def test_a_debit_a_person_booked_is_still_offered_and_rebooked(world, private):
    debit, credit = _recall(world, private)
    _answer(world, debit, "-3200.00", C.HH_OTHER)
    [card] = _cards(world)
    assert [c.id for c in card.returned.candidates] == [debit]
    assert card.returned.candidates[0].booked_category is C.HH_OTHER
    _book(world, debit, credit)
    assert _rows(world, debit) == [("internal_transfer_out", "confirmed")]


def test_rebooking_one_half_withdraws_the_pair_and_the_card_returns(world, private):
    debit, credit = _recall(world, private)
    _book(world, debit, credit)
    _answer(world, debit, "-3200.00", C.HH_OTHER)
    assert world.query(transfers._current_links) == []
    lines = {t.id: t for t in world.query(statements.list_transactions, private.account_id)}
    assert lines[credit].returned_with is None
    [card] = _cards(world)
    assert [c.id for c in card.returned.candidates] == [debit]
    # Booked again from the card: the same link counts again, no second one is written.
    again = _book(world, debit, credit)
    assert _rows(world, debit) == [("internal_transfer_out", "confirmed")]
    assert [r.id for r in _links(world)] == [again.id]
    assert [r.id for r in world.query(transfers._current_links)] == [again.id]
    assert _cards(world) == []
    lines = {t.id: t for t in world.query(statements.list_transactions, private.account_id)}
    assert lines[credit].returned_with.transaction_id == debit


def test_a_return_on_a_savings_account_is_never_saved(world, private):
    """A Tagesgeld account's recalled payment: booked, it is neither saved nor spent."""
    savings = _savings(world)
    debit = savings.line(SENT, "-800.00", iban=FRIEND_IBAN, purpose=ORIGINAL)
    credit = savings.line(BACK, "800.00", iban=FRIEND_IBAN, purpose=RECALL)
    [card] = _cards(world)
    assert (card.transaction_id, [c.id for c in card.returned.candidates]) == (credit, [debit])
    _book(world, debit, credit)
    july = world.query(household.cashflow, "2026-07")
    assert (july.income, july.saved, july.open_lines) == (Decimal("0"), Decimal("0"), 0)
    assert july.spending_by_group == {}


def test_a_debit_the_matcher_links_elsewhere_rolls_the_booking_back(world, private):
    """Booked as a transfer, the debit turns out to be one: the savings account holds its
    counterpart, the matcher links it there, and nothing of the answer is kept."""
    savings = _savings(world)
    debit = private.line(SENT, "-3200.00", iban=SAVINGS_IBAN, purpose=ORIGINAL)
    _answer(world, debit, "-3200.00", C.HH_OTHER)
    mirror = savings.line(SENT, "3200.00", iban=PRIVATE_IBAN, purpose=ORIGINAL)
    _match(world, mirror)
    assert _rows(world, mirror) == [("internal_transfer_in", "confirmed")]
    assert _links(world) == []
    credit = private.line(BACK, "3200.00", iban=FRIEND_IBAN, purpose=RECALL)
    [card] = _cards(world)
    assert [c.id for c in card.returned.candidates] == [debit]
    with pytest.raises(ConflictError):
        _book(world, debit, credit)
    assert _rows(world, debit) == [("hh_other", "confirmed")]
    assert _rows(world, credit) == []
    assert _links(world) == []


def test_a_persons_other_answer_on_the_credit_ends_the_card(world, private):
    """A person who books the credit as something else says it is no return."""
    _debit, credit = _recall(world, private)
    _answer(world, credit, "3200.00", C.HH_OTHER_INCOME)
    assert _cards(world) == []


# --- What is no returned payment ------------------------------------------------------------


def test_an_original_older_than_thirty_days_gives_no_card(world, private):
    _savings(world)
    private.line(date(2026, 6, 29), "-3200.00", iban=FRIEND_IBAN, purpose=ORIGINAL)
    credit = private.line(BACK, "3200.00", iban=SAVINGS_IBAN, purpose=RECALL)
    _match(world, credit)
    assert _cards(world) == []
    [unmatched] = [i for i in _inbox(world) if i.kind in TRANSFER_CARDS]
    assert (unmatched.kind, unmatched.transaction_id) == (InboxItemKind.TRANSFER_UNMATCHED, credit)


def test_two_debits_that_fit_are_both_offered(world, private):
    first = private.line(date(2026, 7, 10), "-450.00", iban=FRIEND_IBAN, purpose="Juli")
    second = private.line(date(2026, 7, 28), "-450.00", iban=FRIEND_IBAN, purpose="Juli")
    credit = private.line(BACK, "450.00", iban=FRIEND_IBAN, purpose="Rücküberweisung")
    [card] = _cards(world)
    assert card.question == returns.QUESTION_SEVERAL
    assert [c.id for c in card.returned.candidates] == [first, second]
    # Neither debit's own waiting state is hidden, since the card guesses neither.
    proposed = {i.transaction_id for i in _inbox(world) if i.kind is InboxItemKind.UNMATCHED}
    assert {first, second} <= proposed
    assert credit not in proposed  # the credit's question is the card
    _book(world, second, credit)
    assert _rows(world, second) == [("internal_transfer_out", "confirmed")]
    assert _rows(world, first) == []
    assert _cards(world) == []


def test_return_words_without_an_equal_debit_change_nothing(world, private):
    _savings(world)
    private.line(SENT, "-3100.00", iban=FRIEND_IBAN, purpose=ORIGINAL)
    credit = private.line(BACK, "3200.00", iban=SAVINGS_IBAN, purpose=RECALL)
    _match(world, credit)
    assert _cards(world) == []
    [unmatched] = [i for i in _inbox(world) if i.kind in TRANSFER_CARDS]
    assert (unmatched.kind, unmatched.transaction_id) == (InboxItemKind.TRANSFER_UNMATCHED, credit)


def test_the_rental_account_is_never_read(world, private):
    """Phase 2's matchers keep the rental account: a return there gets no card."""
    world.line(SENT, "-3200.00", iban=FRIEND_IBAN, purpose=ORIGINAL)
    credit = world.line(BACK, "3200.00", iban=FRIEND_IBAN, purpose=RECALL)
    assert _cards(world) == []
    with pytest.raises(RuleViolationError) as refused:
        _book(world, world.line(SENT, "-3200.00", iban=FRIEND_IBAN), credit)
    assert refused.value.codes == (TRANSFER_RETURN_INVALID,)
    assert _rows(world, credit) == []


def test_a_pair_that_is_no_return_is_refused(world, private):
    debit = private.line(SENT, "-3200.00", iban=FRIEND_IBAN, purpose=ORIGINAL)
    plain = private.line(BACK, "3200.00", iban=FRIEND_IBAN, purpose="Gutschrift")
    with pytest.raises(RuleViolationError) as refused:
        _book(world, debit, plain)
    assert refused.value.codes == (TRANSFER_RETURN_INVALID,)
    other = private.line(SENT, "-100.00", iban=FRIEND_IBAN)
    credit = private.line(BACK, "3200.00", iban=FRIEND_IBAN, purpose=RECALL)
    with pytest.raises(RuleViolationError):
        _book(world, other, credit)
    assert _rows(world, debit) == [] and _rows(world, credit) == []


def test_a_line_linked_elsewhere_is_a_conflict(world, private):
    """A debit that was a transfer to the rental account after all keeps its link."""
    debit = private.line(SENT, "-3200.00", iban=ACCOUNT_IBAN, purpose=ORIGINAL)
    _match(world, debit)
    mirror = world.line(SENT, "3200.00", iban=PRIVATE_IBAN, purpose=ORIGINAL)
    _match(world, mirror)
    assert len(_links(world)) == 1
    credit = private.line(BACK, "3200.00", iban=FRIEND_IBAN, purpose=RECALL)
    assert _cards(world) == []  # a linked debit is never offered
    with pytest.raises(ConflictError):
        _book(world, debit, credit)
    assert _rows(world, credit) == []


# --- hausbuch pair-returns -------------------------------------------------------------------


def test_pair_returns_lists_the_cards_and_writes_nothing(world, private, dbname, capsys):
    debit, credit = _recall(world, private)
    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)}
    argv = ["pair-returns", "--workspace", str(world.ws)]
    assert cli.main(argv, environ) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "found 1 returned payment(s)" in out
    assert "0 of them offer more than one debit" in out
    assert cli.main([*argv, "--apply"], environ) == cli.EXIT_OK
    assert "nothing to apply" in capsys.readouterr().out
    assert _links(world) == []
    assert _rows(world, debit) == [("hh_property_cost", "proposed")]
    assert len(_cards(world)) == 1
