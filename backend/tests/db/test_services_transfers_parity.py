"""PR #516 (21 Nov 2026) batched the transfer reads with three hand copies of rules whose
originals the write path still uses: `_hmacs_of` beside `own_account_hmacs`, `_held_answers`
beside `_held_answer`, and the current-allocations query inlined in `lines_on_cards` beside
`allocations.current_allocations` with `_free_for_matcher`. If one side changes and the other
does not, Zu prüfen's cards silently disagree with what the matcher links. This test pins each
copy to its original on one workspace seeded with the cases that tell them apart; `saving_links`
reads each leg's account kind with the leg since then, pinned to the account table. Invented
IBANs only (Bundesbank test bank code 10000000)."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.matching_support import World
from db.test_services_card_settlement import PAYMENT_IBAN, Card
from db.test_services_exchange_deposit_fee import (
    DEPOSIT_IBAN,
    TODAY,
    _exchange,
    _export,
    _link,
    _movements,
    _propose,
)
from db.test_services_private_matching import NOW, PRIVATE_IBAN, Private, _answer_line
from db.test_services_transfers import _savings
from db.test_services_transfers_statements import _match
from sqlalchemy import select

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.api import InboxItem, InboxItemKind
from hausbuch.schemas.household import (
    OpenTransfer,
    OpenTransferCode,
    TransferLegSource,
    TransferLegView,
)
from hausbuch.services import allocations, household_resort, matching, transfers
from hausbuch.services.iban import protect_iban

pytestmark = pytest.mark.db

C = AllocationCategory
RANGE = {"start": date(2026, 6, 1), "end": date(2026, 10, 31)}


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


class _Seed:
    """The accounts and lines whose differences the copies must read alike."""

    def __init__(self, world: World, blob_store) -> None:
        self.world = world
        self.private = Private(world)
        self.savings = _savings(world)
        # A credit card with only a payment IBAN (no account's own IBAN), and a second card
        # sharing it: two accounts, one IBAN.
        self.card = Card(world, PAYMENT_IBAN)
        self.twin = Card(world, PAYMENT_IBAN, label="Zweite Karte")
        # A card whose payment IBAN is the private account's own IBAN (an account may hold
        # either, never both: account_payment_iban_check, account_identity_check).
        self.mirror = Card(world, PRIVATE_IBAN, label="Dritte Karte")
        # An exchange with no IBAN.
        self.exchange = _exchange(world)

        p = self.private
        # A bank line linked by hand to an exchange deposit; it names no IBAN, so none is
        # learned and the exchange keeps none.
        self.saved = p.line(date(2026, 8, 3), "-500.00", name="Binance")
        _propose(world, self.saved, "-500.00", C.HH_INVESTMENT)
        _export(world, blob_store, self.exchange.id, "2026-08-04 08:00:00,Deposit,500")
        self.deposit = _movements(world, self.exchange.id)[Decimal("500.00")]
        _link(world, self.saved, self.deposit)

        # A deposit the exchange credited 1 € short, the model's proposal on its debit,
        # proposed twice (the first proposal superseded): a free line on a proposed card.
        self.free = p.line(date(2026, 9, 7), "-901.00", name="Binance", iban=DEPOSIT_IBAN)
        _propose(world, self.free, "-901.00", C.UNCATEGORIZED)
        _propose(world, self.free, "-901.00", C.HH_INVESTMENT)
        _export(world, blob_store, self.exchange.id, "2026-09-08 08:00:00,Deposit,900")

        # A line a code matcher confirmed (no person): the card's settlement debit.
        self.settled = p.line(date(2026, 10, 5), "-250.00", iban=PAYMENT_IBAN)
        paid = self.card.payment(date(2026, 10, 6), "250.00")
        _match(world, paid)
        _match(world, self.settled)

        # A person's confirmed answer, superseded by the model's re-sort proposal.
        self.resorted = p.line(date(2026, 10, 9), "-40.00", name="Laden Beispiel")
        _answer_line(world, self.resorted, "-40.00", "hh_other")
        line = world.query(matching.load_line, self.resorted)
        answer = AllocationSet(
            transaction_id=self.resorted,
            method=AllocationMethod.LLM,
            confidence=Decimal("0.700"),
            reasoning="Vermutlich Porto.",
            allocations=[AllocationDraft(amount=Decimal("-40.00"), category=C.HH_POSTAGE)],
        )
        plan = world.cmd(
            household_resort.finish_resort, self.resorted, answer, household_resort.seen(line)
        )
        assert plan.outcome == "written"

        # A person's current answer; a code's proposal; a line holding nothing.
        self.answered = p.line(date(2026, 10, 12), "-60.00", name="Laden Beispiel")
        _answer_line(world, self.answered, "-60.00", "hh_other")
        self.coded = p.line(date(2026, 10, 13), "-70.00", name="Laden Beispiel")
        coded = AllocationSet(
            transaction_id=self.coded,
            method=AllocationMethod.DOCUMENT_MATCH,
            confidence=Decimal("0.900"),
            reasoning="Beleg.",
            allocations=[AllocationDraft(amount=Decimal("-70.00"), category=C.HH_OTHER)],
        )
        world.cmd(allocations.record_set, coded, confirm=False, auto=False)
        self.empty = p.line(date(2026, 10, 14), "-80.00", name="Laden Beispiel")
        # A rental line with only the model's proposal.
        self.rental = world.line(date(2026, 10, 15), "-90.00", name="Handwerker Beispiel")
        _propose(world, self.rental, "-90.00", C.UNCATEGORIZED)


def _ids(world: World, table) -> list[uuid.UUID]:
    return world.query(lambda c: list(c.execute(select(table.c.id)).scalars()))


def _view(tx: uuid.UUID, account_id: uuid.UUID, *, rebook: bool, fee: Decimal | None):
    return TransferLegView(
        source=TransferLegSource.TRANSACTION,
        id=tx,
        account_id=account_id,
        account_label="Konto",
        booked_on=date(2026, 10, 1),
        amount=Decimal("-1.00"),
        rebook_on_link=rebook,
        deposit_fee=fee,
    )


def _card(code: OpenTransferCode, candidates: list[TransferLegView]) -> InboxItem:
    leg = candidates[0].model_copy(update={"amount": Decimal("1.00")})
    return InboxItem(
        kind=InboxItemKind.TRANSFER_LINK,
        created_at=NOW,
        reasoning="",
        question="",
        transfer=OpenTransfer(code=code, leg=leg, candidates=candidates, window_end=TODAY),
    )


def _lines_on_cards_before_516(conn, items) -> set[uuid.UUID]:
    """lines_on_cards as it read before PR #516 (3c379a0a^): per candidate line,
    allocations.current_allocations and transfers._free_for_matcher."""
    found: set[uuid.UUID] = set()
    seen: set[uuid.UUID] = set()
    for item in items:
        if item.transfer is None:
            continue
        proposed = item.transfer.code is OpenTransferCode.PROPOSED
        for candidate in item.transfer.candidates:
            if candidate.source is not TransferLegSource.TRANSACTION or candidate.id in seen:
                continue
            if not candidate.rebook_on_link and not (
                proposed and candidate.deposit_fee is not None
            ):
                continue
            seen.add(candidate.id)
            current = allocations.current_allocations(conn, candidate.id)
            if current and transfers._free_for_matcher(conn, candidate.id, current):
                found.add(candidate.id)
    return found


def test_the_batched_copies_read_as_their_originals(world, blob_store):
    seed = _Seed(world, blob_store)
    accounts = _ids(world, account_table)
    lines = _ids(world, transaction_table)

    # The seed holds what it must.
    exchange = world.query(
        lambda c: c.execute(
            select(account_table).where(account_table.c.id == seed.exchange.id)
        ).one()
    )
    assert exchange.iban_hmac is None and exchange.payment_iban_hmac is None
    assert len(accounts) == 7  # rental, private, savings, three cards, the exchange

    # 1. _hmacs_of against own_account_hmacs, for every account and for none.
    def hmacs(conn) -> None:
        rows = transfers._account_ibans(conn)
        for account_id in accounts:
            assert transfers._hmacs_of(rows, except_account=account_id) == (
                transfers.own_account_hmacs(conn, except_account=account_id)
            ), account_id
        assert transfers._hmacs_of(rows, except_account=uuid.uuid4()) == (
            transfers.own_account_hmacs(conn)
        )
        # The payment-only card's IBAN is there because of the fallback alone.
        payment = protect_iban(PAYMENT_IBAN, TEST_IBAN_KEY).hmac.hex()
        assert payment in transfers.own_account_hmacs(conn, except_account=seed.card.account_id)

    world.query(hmacs)

    # 2. _held_answers against _held_answer, for every line and an id with no rows.
    nobody = uuid.uuid4()
    held = world.query(transfers._held_answers, [*lines, nobody])
    assert held == {i: world.query(transfers._held_answer, i) for i in [*lines, nobody]}
    assert held[seed.resorted] is not None  # the superseded person answer still holds
    assert held[seed.settled] is None  # a code matcher's confirmation is no answer
    assert held[seed.free] is None and held[nobody] is None

    # 3. lines_on_cards against the per-line rule before PR #516, on the real cards and on
    # hand-built ones naming every line in each way a card can.
    giro = seed.private.account_id
    real = world.query(transfers.transfer_inbox_items, TODAY)
    hand = [
        _card(
            OpenTransferCode.AMBIGUOUS,
            [_view(tx, giro, rebook=True, fee=None) for tx in lines],
        ),
        _card(
            OpenTransferCode.PROPOSED,
            [_view(tx, giro, rebook=False, fee=Decimal("1.00")) for tx in lines],
        ),
        _card(
            OpenTransferCode.AMBIGUOUS,
            [_view(tx, giro, rebook=False, fee=Decimal("1.00")) for tx in lines],
        ),
    ]
    for items in (real, hand, real + hand):
        expected = world.query(_lines_on_cards_before_516, items)
        assert world.query(transfers.lines_on_cards, items) == expected
    on_real = world.query(transfers.lines_on_cards, real)
    assert seed.free in on_real
    on_hand = world.query(_lines_on_cards_before_516, hand)
    # Only the lines holding nothing but the model's proposal: not the superseded person
    # answer, a person's current answer, a code's proposal, a matcher's booking, nothing. (The
    # line linked by hand keeps the model's proposal: a plain link does not re-book it.)
    assert on_hand == {seed.free, seed.rental, seed.saved}

    # saving_links reads each leg's account kind with the leg: the account table's kind.
    saving = world.query(transfers.saving_links, **RANGE)
    assert [(v.out_leg.id, v.in_leg.id) for v in saving] == [(seed.saved, seed.deposit)]
    kinds = world.query(
        lambda c: dict(c.execute(select(account_table.c.id, account_table.c.kind)).all())
    )
    for view in world.query(transfers.list_links, **RANGE):
        for leg in (view.out_leg, view.in_leg):
            assert leg.account_kind.value == kinds[leg.account_id]
