"""Phase 6 D162 amended (5), 15 Nov 2026, under hausbuch_app and RLS: a private bank line that
holds only the model's proposal and is a candidate a transfer card's button books (a deposit
the exchange credited short, or a line tied to the leg by an own IBAN) is listed on the card
only, not among Zu prüfen's lines to allocate, so the decision is listed and counted once.
A line holding a person's or a rule's answer, a line holding nothing, an untied candidate of
the same amount and a rental line stay listed. Invented names and IBANs only."""

from datetime import date
from decimal import Decimal

import pytest
from db.matching_support import World
from db.test_services_exchange_deposit_fee import (
    DEPOSIT_IBAN,
    TODAY,
    _exchange,
    _export,
    _link,
    _movements,
    _open,
    _propose,
)
from db.test_services_private_matching import NOW, Private, _answer_line

from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.api import InboxItem, InboxItemKind
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.household import (
    OpenTransfer,
    OpenTransferCode,
    TransferLegSource,
    TransferLegView,
)
from hausbuch.services import allocations, household_resort, matching, review, transfers

pytestmark = pytest.mark.db

C = AllocationCategory
LINE_KINDS = (InboxItemKind.ALLOCATION_PROPOSED, InboxItemKind.UNMATCHED)
CARD_KINDS = (InboxItemKind.TRANSFER_LINK, InboxItemKind.TRANSFER_UNMATCHED)


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


@pytest.fixture
def bank(world) -> Private:
    return Private(world)


def _inbox(world: World) -> list[InboxItem]:
    return world.query(review.inbox, TODAY)


def _lines(items: list[InboxItem]) -> set:
    return {i.transaction_id for i in items if i.kind in LINE_KINDS}


def _cards(items: list[InboxItem]) -> list[InboxItem]:
    return [i for i in items if i.kind in CARD_KINDS]


def _mentions(items: list[InboxItem], tx) -> int:
    """How many items of Zu prüfen name the line: as a line to allocate or as a card's
    candidate."""
    as_line = sum(1 for i in items if i.kind in LINE_KINDS and i.transaction_id == tx)
    on_card = sum(
        1 for i in _cards(items) if i.transfer and tx in {c.id for c in i.transfer.candidates}
    )
    return as_line + on_card


def _short_deposit(world: World, bank: Private, blob_store, *, amount: str = "-901.00"):
    """Luis's case: a DKB debit to the exchange's deposit IBAN, credited 1 € short."""
    out = bank.line(date(2026, 9, 7), amount, name="Binance", iban=DEPOSIT_IBAN)
    exchange = _exchange(world)
    credited = Decimal(amount) * -1 - 1
    _export(world, blob_store, exchange.id, f"2026-09-08 08:00:00,Deposit,{credited}")
    deposit = _movements(world, exchange.id)[credited]
    return out, exchange, deposit


# --- Hidden: the model's proposal on a card whose button books it ---------------------------


def test_a_model_proposed_deposit_is_listed_on_its_card_only(world, bank, blob_store):
    out, _, deposit = _short_deposit(world, bank, blob_store)
    _propose(world, out, "-901.00", C.UNCATEGORIZED)
    # Without the transfer cards (no day given) the line is listed as before.
    assert out in _lines(world.query(review.inbox))
    items = _inbox(world)
    [card] = _cards(items)
    assert card.transfer is not None and card.transfer.leg.id == deposit
    assert [c.id for c in card.transfer.candidates] == [out]
    assert out not in _lines(items)
    assert _mentions(items, out) == 1  # one decision, counted once
    # GET /transfers/open still lists it: the card is where it is answered.
    assert [c.id for c in _open(world)[0].candidates] == [out]


def test_after_the_link_the_line_is_gone_everywhere(world, bank, blob_store):
    out, _, deposit = _short_deposit(world, bank, blob_store)
    _propose(world, out, "-901.00", C.HH_INVESTMENT)
    assert out not in _lines(_inbox(world))
    _link(world, out, deposit)  # „Als Einzahlung verknüpfen“: the card's body, no rebook
    items = _inbox(world)
    assert _cards(items) == []
    assert _mentions(items, out) == 0  # booked as the transfer and its fee: nothing waits


def test_every_short_candidate_of_an_ambiguous_card_is_hidden(world, bank, blob_store):
    a = bank.line(date(2026, 9, 7), "-901.00", name="Binance", iban=DEPOSIT_IBAN)
    b = bank.line(date(2026, 9, 7), "-901.50", name="Binance", iban=DEPOSIT_IBAN)
    _propose(world, a, "-901.00", C.UNCATEGORIZED)
    _propose(world, b, "-901.50", C.HH_INVESTMENT)
    exchange = _exchange(world)
    _export(world, blob_store, exchange.id, "2026-09-08 08:00:00,Deposit,900")
    items = _inbox(world)
    [card] = _cards(items)
    assert card.transfer is not None
    assert card.transfer.code is OpenTransferCode.UNMATCHED_LEG
    assert {c.id for c in card.transfer.candidates} == {a, b}
    assert not _lines(items) & {a, b}
    assert (_mentions(items, a), _mentions(items, b)) == (1, 1)


def test_a_tied_candidate_the_model_proposed_is_read_as_on_its_card(world, bank):
    """rebook_on_link: the card's button re-books the line as a transfer (D176 (4)), so it is
    on the card only while it holds the model's proposal; a line holding nothing is not."""
    proposed = bank.line(date(2026, 9, 7), "-250.00", name="Karte")
    _propose(world, proposed, "-250.00", C.HH_OTHER)
    empty = bank.line(date(2026, 9, 7), "-250.00", name="Karte")
    untied = bank.line(date(2026, 9, 7), "-250.00", name="Karte")
    _propose(world, untied, "-250.00", C.HH_OTHER)

    def view(tx, rebook: bool) -> TransferLegView:
        return TransferLegView(
            source=TransferLegSource.TRANSACTION,
            id=tx,
            account_id=bank.account_id,
            account_label="Giro",
            booked_on=date(2026, 9, 7),
            amount=Decimal("-250.00"),
            account_kind=AccountKind.BANK_CURRENT,
            rebook_on_link=rebook,
        )

    leg = view(proposed, False).model_copy(update={"amount": Decimal("250.00")})
    card = InboxItem(
        kind=InboxItemKind.TRANSFER_LINK,
        created_at=NOW,
        reasoning="",
        question="",
        transfer=OpenTransfer(
            code=OpenTransferCode.AMBIGUOUS,
            leg=leg,
            candidates=[view(proposed, True), view(empty, True), view(untied, False)],
            window_end=TODAY,
        ),
    )
    assert world.query(transfers.lines_on_cards, [card]) == {proposed}


# --- Kept: a person's or a rule's answer, an untied exact fit, the rental account -------------


def test_a_code_proposal_on_the_card_stays_listed(world, bank, blob_store):
    """Only the model's own proposal moves to the card; a code's (or a rule's) is not it."""
    out, _, _ = _short_deposit(world, bank, blob_store)
    rule = AllocationSet(
        transaction_id=out,
        method=AllocationMethod.DOCUMENT_MATCH,
        confidence=Decimal("0.900"),
        reasoning="Beleg.",
        allocations=[AllocationDraft(amount=Decimal("-901.00"), category=C.HH_INVESTMENT)],
    )
    world.cmd(allocations.record_set, rule, confirm=False, auto=False)
    items = _inbox(world)
    [card] = _cards(items)
    assert card.transfer is not None and [c.id for c in card.transfer.candidates] == [out]
    assert out in _lines(items)


def test_a_resort_proposal_over_a_persons_answer_stays_listed(world, bank, blob_store):
    """The one-off re-sort's model answer over a person's confirmed fallback row is no free
    line (_held_answer). After the exchange's IBAN is learned the later deposit is tied, so its
    card offers it, and it stays among the lines too."""
    first, exchange, first_deposit = _short_deposit(world, bank, blob_store)
    _propose(world, first, "-901.00", C.HH_INVESTMENT)
    _link(world, first, first_deposit)  # learns the deposit IBAN
    later = bank.line(date(2026, 10, 5), "-1001.00", name="Binance", iban=DEPOSIT_IBAN)
    _answer_line(world, later, "-1001.00", "hh_other")
    line = world.query(matching.load_line, later)
    answer = AllocationSet(
        transaction_id=later,
        method=AllocationMethod.LLM,
        confidence=Decimal("0.700"),
        reasoning="Vermutlich Porto.",
        allocations=[AllocationDraft(amount=Decimal("-1001.00"), category=C.HH_POSTAGE)],
    )
    plan = world.cmd(household_resort.finish_resort, later, answer, household_resort.seen(line))
    assert plan.outcome == "written"
    _export(world, blob_store, exchange.id, "2026-10-06 08:00:00,Deposit,1000")
    world.cmd(transfers.relink_all)
    items = _inbox(world)
    candidates = {c.id for i in _cards(items) if i.transfer for c in i.transfer.candidates}
    assert later in candidates
    assert later in _lines(items)


def test_an_untied_exact_fit_stays_listed(world, bank, blob_store):
    """A plain „Verknüpfen“ leaves the model's proposal on an untied line, so it would come
    back after the link: it stays among the lines (and on its card)."""
    out = bank.line(date(2026, 9, 7), "-900.00", name="Binance", iban=DEPOSIT_IBAN)
    _propose(world, out, "-900.00", C.HH_INVESTMENT)
    exchange = _exchange(world)
    _export(world, blob_store, exchange.id, "2026-09-08 08:00:00,Deposit,900")
    items = _inbox(world)
    [card] = _cards(items)
    assert card.transfer is not None
    [candidate] = card.transfer.candidates
    assert (candidate.id, candidate.deposit_fee, candidate.rebook_on_link) == (out, None, False)
    assert out in _lines(items)


def test_a_line_holding_nothing_stays_listed(world, bank, blob_store):
    out, _, _ = _short_deposit(world, bank, blob_store)
    items = _inbox(world)
    assert [c.id for i in _cards(items) if i.transfer for c in i.transfer.candidates] == [out]
    assert out in _lines(items)


def test_a_rental_line_stays_listed(world, blob_store):
    """A rental line is never a deposit's candidate (D162 amended 15 Nov 2026) nor re-booked
    as a transfer between own accounts: it stays among the lines."""
    rental = world.line(
        date(2026, 9, 7), "-901.00", name="Binance", iban=DEPOSIT_IBAN, purpose="Einzahlung"
    )
    _propose(world, rental, "-901.00", C.UNCATEGORIZED)
    exchange = _exchange(world)
    _export(world, blob_store, exchange.id, "2026-09-08 08:00:00,Deposit,900")
    assert rental in _lines(_inbox(world))


# --- The way out: answered otherwise, the card behaves as PR #321 built it ---------------------


def test_answered_otherwise_an_untied_deposit_leaves_the_card(world, bank, blob_store):
    """„Keine Überweisung – Zahlung anders zuordnen“ opens the line's own answer; answered as
    spending, it is a person's answer: no longer hidden, no longer waiting, and the untied
    deposit's card no longer offers it."""
    out, _, deposit = _short_deposit(world, bank, blob_store)
    _propose(world, out, "-901.00", C.UNCATEGORIZED)
    assert out not in _lines(_inbox(world))
    _answer_line(world, out, "-901.00", "hh_investment")
    items = _inbox(world)
    [card] = _cards(items)
    assert card.transfer is not None
    assert (card.transfer.leg.id, card.transfer.candidates) == (deposit, [])
    assert _mentions(items, out) == 0


def test_answered_otherwise_a_tied_deposit_keeps_its_rebook(world, bank, blob_store):
    first, exchange, first_deposit = _short_deposit(world, bank, blob_store)
    _propose(world, first, "-901.00", C.HH_INVESTMENT)
    _link(world, first, first_deposit)  # learns the deposit IBAN: later deposits are tied
    later = bank.line(date(2026, 10, 5), "-1001.00", name="Binance", iban=DEPOSIT_IBAN)
    _answer_line(world, later, "-1001.00", "hh_online_shopping")
    _export(world, blob_store, exchange.id, "2026-10-06 08:00:00,Deposit,1000")
    world.cmd(transfers.relink_all)
    items = _inbox(world)
    [candidate] = [
        c for i in _cards(items) if i.transfer for c in i.transfer.candidates if c.id == later
    ]
    assert (candidate.deposit_fee, candidate.rebook_on_link) == (Decimal("1.00"), True)
    assert later not in _lines(items)  # confirmed by the person: it never waited
