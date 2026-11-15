"""Phase 6 D162 amended (6), 15 Nov 2026, under hausbuch_app and RLS: when the matcher links a
pair tied by an own IBAN, a line of the pair that holds only the model's proposal of that very
transfer is booked as the transfer's leg by the matcher, so it leaves Zahlungen zuordnen. An
untied pair, a non-transfer proposal, a person's answer, a code's proposal and a card's refund
stay as they were; `hausbuch relink-transfers` books the legs of links written before
(transfers.confirm_linked), idempotently. Invented names and IBANs only."""

from datetime import date
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.matching_support import World
from db.test_cli_evals_report import _owner_url
from db.test_services_card_settlement import PAYMENT_IBAN, Card
from db.test_services_card_settlement import _refund as card_refund
from db.test_services_exchange_deposit_fee import DEPOSIT_IBAN, _exchange, _export, _movements
from db.test_services_private_matching import NOW, Private
from db.test_services_transfer_named_leg import DEPOT_IBAN, FRIDAY, NAMED, _depot
from sqlalchemy import select, update

from hausbuch import cli
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import transfer_link as link_table
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.household import OpenTransferCode, TransferLinkKind, TransferMatchedBy
from hausbuch.services import allocations, matching, review, transfers
from hausbuch.services.iban import protect_iban

pytestmark = pytest.mark.db

C = AllocationCategory
TODAY = NOW.date()
LINE_KINDS = (InboxItemKind.ALLOCATION_PROPOSED, InboxItemKind.UNMATCHED)
OUT = ("internal_transfer_out", "confirmed", "matcher_transfer")


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


@pytest.fixture
def bank(world) -> Private:
    return Private(world)


def _record(world: World, tx, amount: str, category: C, method: AllocationMethod, *, confirm):
    answer = AllocationSet(
        transaction_id=tx,
        method=method,
        confidence=Decimal("0.700") if method is AllocationMethod.LLM else Decimal("1.000"),
        reasoning="Vermutlich ein Übertrag.",
        question="Stammt die Zahlung von einem eigenen Konto?",
        allocations=[AllocationDraft(amount=Decimal(amount), category=category)],
    )
    world.cmd(allocations.record_set, answer, confirm=confirm, auto=False)


def _propose(world: World, tx, amount: str, category: C) -> None:
    """The model's guess, as the categorize job leaves it."""
    _record(world, tx, amount, category, AllocationMethod.LLM, confirm=False)


def _match(world: World, tx):
    return world.cmd(matching.match_line, tx, iban_key=TEST_IBAN_KEY, ai_available=False)


def _booked(world: World, tx):
    return world.query(
        lambda c: [
            (r.category, r.status, r.method)
            for r in c.execute(
                select(allocation_table).where(
                    allocation_table.c.transaction_id == tx,
                    allocation_table.c.status != "superseded",
                )
            ).all()
        ]
    )


def _links(world: World):
    return world.query(lambda c: c.execute(select(link_table)).all())


def _listed(world: World) -> set:
    """The lines Zu prüfen lists to allocate („Zahlungen zuordnen“)."""
    items = world.query(review.inbox, TODAY)
    return {i.transaction_id for i in items if i.kind in LINE_KINDS}


# --- A depot's cash line the model proposed as the transfer ------------------------------------


def test_bank_first_the_depot_line_proposed_as_the_transfer_is_booked(world, bank):
    depot = _depot(world)
    out = bank.line(FRIDAY, "-600.00", iban=DEPOT_IBAN, name="Beispiel Depot")
    _match(world, out)
    assert _booked(world, out) == [OUT]
    in_ = depot.line(FRIDAY, "600.00", purpose="Lastschrift Einzug")
    _propose(world, in_, "600.00", C.INTERNAL_TRANSFER_IN)
    assert _booked(world, in_) == [NAMED]
    [link] = _links(world)
    assert (link.out_transaction_id, link.in_transaction_id) == (out, in_)
    assert (link.kind, link.matched_by) == (
        TransferLinkKind.DEPOT_CASH.value,
        TransferMatchedBy.MATCHER.value,
    )
    assert in_ not in _listed(world) and out not in _listed(world)
    assert world.query(transfers.open_transfers, TODAY) == []


def test_depot_first_the_proposed_line_is_booked_when_the_debit_arrives(world, bank):
    depot = _depot(world)
    in_ = depot.line(FRIDAY, "600.00", purpose="Lastschrift Einzug")
    _propose(world, in_, "600.00", C.INTERNAL_TRANSFER_IN)
    assert in_ in _listed(world)
    out = bank.line(FRIDAY, "-600.00", iban=DEPOT_IBAN)
    _match(world, out)
    assert _booked(world, in_) == [NAMED]
    assert len(_links(world)) == 1
    assert in_ not in _listed(world)


# --- An exchange's deposit, the bank line naming its deposit IBAN -----------------------------
def _learn_deposit_iban(world: World, account_id) -> None:
    protected = protect_iban(DEPOSIT_IBAN, TEST_IBAN_KEY)
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(
            update(account_table)
            .where(account_table.c.id == account_id)
            .values(iban_hmac=protected.hmac, iban_masked=protected.masked)
        )


def test_an_exchange_deposit_books_the_bank_line_the_model_proposed(world, bank, blob_store):
    exchange = _exchange(world)
    # the deposit IBAN, learned since the model answered the line
    _learn_deposit_iban(world, exchange.id)
    out = bank.line(date(2026, 9, 7), "-900.00", name="Binance", iban=DEPOSIT_IBAN)
    _propose(world, out, "-900.00", C.INTERNAL_TRANSFER_OUT)
    _export(world, blob_store, exchange.id, "2026-09-08 08:00:00,Deposit,900")
    deposit = _movements(world, exchange.id)[Decimal("900")]
    [link] = _links(world)
    assert (link.out_transaction_id, link.in_movement_id, link.kind) == (
        out,
        deposit,
        TransferLinkKind.EXCHANGE_EUR.value,
    )
    assert _booked(world, out) == [OUT]
    assert out not in _listed(world)


def test_a_non_transfer_proposal_keeps_its_card(world, bank, blob_store):
    """The model read the deposit as an investment: nothing is linked, the line keeps its
    proposal and the deposit's card offers it with its re-booking (D176 (4), unchanged)."""
    exchange = _exchange(world)
    _learn_deposit_iban(world, exchange.id)
    out = bank.line(date(2026, 9, 7), "-900.00", name="Binance", iban=DEPOSIT_IBAN)
    _propose(world, out, "-900.00", C.HH_INVESTMENT)
    _export(world, blob_store, exchange.id, "2026-09-08 08:00:00,Deposit,900")
    assert _booked(world, out) == [("hh_investment", "proposed", "llm")]
    assert _links(world) == []
    [card] = world.query(transfers.open_transfers, TODAY)
    assert card.code is OpenTransferCode.PROPOSED
    assert [(c.id, c.rebook_on_link) for c in card.candidates] == [(out, True)]


# --- What stays as it was ------------------------------------------------------------------------


def test_an_untied_pair_the_matcher_linked_stays_proposed_and_listed(world, bank):
    """Neither line names the other's account: the link rests on the model's guess and an
    amount and date, so both proposals stay for a person (principle 3)."""
    depot = _depot(world)
    out = bank.line(FRIDAY, "-600.00")
    in_ = depot.line(FRIDAY, "600.00")
    _propose(world, out, "-600.00", C.INTERNAL_TRANSFER_OUT)
    _propose(world, in_, "600.00", C.INTERNAL_TRANSFER_IN)
    [link] = _links(world)
    assert (link.out_transaction_id, link.in_transaction_id) == (out, in_)
    assert _booked(world, out) == [("internal_transfer_out", "proposed", "llm")]
    assert _booked(world, in_) == [("internal_transfer_in", "proposed", "llm")]
    assert {out, in_} <= _listed(world)
    assert world.cmd(transfers.confirm_linked) == 0


def test_a_persons_answer_is_never_replaced(world, bank):
    depot = _depot(world)
    in_ = depot.line(FRIDAY, "600.00")
    _record(world, in_, "600.00", C.INTERNAL_TRANSFER_IN, AllocationMethod.USER, confirm=True)
    out = bank.line(FRIDAY, "-600.00", iban=DEPOT_IBAN)
    _match(world, out)
    assert len(_links(world)) == 1
    assert _booked(world, in_) == [("internal_transfer_in", "confirmed", "user")]


def test_a_code_proposal_is_never_booked(world, bank):
    """Only the model's own proposal is free for the matcher, never a matcher's or a rule's."""
    depot = _depot(world)
    in_ = depot.line(FRIDAY, "600.00")
    _record(
        world, in_, "600.00", C.INTERNAL_TRANSFER_IN, AllocationMethod.MATCHER_DEPOT, confirm=False
    )
    out = bank.line(FRIDAY, "-600.00", iban=DEPOT_IBAN)
    _match(world, out)
    assert len(_links(world)) == 1
    assert _booked(world, in_) == [("internal_transfer_in", "proposed", "matcher_depot")]
    assert world.cmd(transfers.confirm_linked) == 0


def test_a_card_refund_the_model_read_as_the_payment_is_never_booked(world, bank):
    """D162 amended (5) (7)(a): only the card reader's own payment line is the settlement leg;
    a merchant's refund the model proposed as a transfer keeps its proposal."""
    card = Card(world, PAYMENT_IBAN)
    refund = card_refund(card, date(2026, 10, 5), "312.75")
    _propose(world, refund, "312.75", C.INTERNAL_TRANSFER_IN)
    debit = bank.line(date(2026, 10, 5), "-312.75", iban=PAYMENT_IBAN, purpose="Kreditkarte")
    _match(world, debit)
    assert _booked(world, refund) == [("internal_transfer_in", "proposed", "llm")]
    assert refund in _listed(world)


# --- The one-off: links written before this change ----------------------------------------------


def _legacy_link(world: World, bank: Private, monkeypatch):
    """A tied link the matcher wrote while the depot line kept the model's proposal (the
    state before D162 amended (6))."""
    depot = _depot(world)
    out = bank.line(FRIDAY, "-600.00", iban=DEPOT_IBAN)
    _match(world, out)
    in_ = depot.line(FRIDAY, "600.00")
    with monkeypatch.context() as patched:
        patched.setattr(transfers, "_confirm_tied_legs", lambda ctx, out, in_: 0)
        _propose(world, in_, "600.00", C.INTERNAL_TRANSFER_IN)
    assert len(_links(world)) == 1
    assert _booked(world, in_) == [("internal_transfer_in", "proposed", "llm")]
    assert in_ in _listed(world)
    return out, in_


def test_confirm_linked_books_the_proposed_leg_once(world, bank, monkeypatch):
    out, in_ = _legacy_link(world, bank, monkeypatch)
    assert world.cmd(transfers.confirm_linked) == 1
    assert _booked(world, in_) == [NAMED]
    assert in_ not in _listed(world)
    assert len(_links(world)) == 1  # the same link, never a second row
    assert world.cmd(transfers.confirm_linked) == 0
    assert _booked(world, in_) == [NAMED]


def test_relink_transfers_command_books_the_linked_legs(dbname, world, bank, monkeypatch, capsys):
    _, in_ = _legacy_link(world, bank, monkeypatch)
    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)}
    argv = ["relink-transfers", "--workspace", str(world.ws)]

    assert cli.main([*argv, "--dry-run"], environ) == cli.EXIT_OK
    text = capsys.readouterr().out
    assert "would link 0 transfer(s)" in text and "would book 1 linked line(s)" in text
    assert _booked(world, in_) == [("internal_transfer_in", "proposed", "llm")]

    assert cli.main(argv, environ) == cli.EXIT_OK
    assert "booked 1 linked line(s)" in capsys.readouterr().out
    assert _booked(world, in_) == [NAMED]
    assert cli.main(argv, environ) == cli.EXIT_OK  # a second run books nothing more
    assert "booked 0 linked line(s)" in capsys.readouterr().out
