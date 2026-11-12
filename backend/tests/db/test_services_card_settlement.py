"""Phase 6 step 2b, D176 (4) under hausbuch_app and RLS: a credit card's monthly settlement.

The debit on the paying account names the card's confirmed payment IBAN, so it is an internal
transfer by that IBAN alone; the card statement's payment line („Bezahlung“) is booked by code
as internal_transfer_in; D162's matcher links the two when the amounts are equal to the cent
and the card's line is booked within three banking days of the debit. Invented IBANs only
(Bundesbank test bank code 10000000)."""

import hashlib
import uuid
from datetime import date
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.matching_support import World
from db.test_services_private_matching import NOW, Private
from sqlalchemy import insert, select, update

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.db.tables import transfer_link as link_table
from hausbuch.jobs import enqueue
from hausbuch.schemas.enums import ImportStatus
from hausbuch.schemas.household import OpenTransferCode, TransferLinkKind, TransferMatchedBy
from hausbuch.services import matching, transfers
from hausbuch.services.iban import protect_iban

pytestmark = pytest.mark.db

TODAY = NOW.date()
MONDAY = date(2026, 10, 5)
TUESDAY = date(2026, 10, 6)
PAYMENT_IBAN = "DE77100000000000003007"  # the issuer's account for payments to the card
OTHER_PAYMENT_IBAN = "DE50100000000000003008"


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


@pytest.fixture
def giro(world) -> Private:
    return Private(world)


class Card:
    """A credit card account of the world with a reconciled card statement import; its payment
    IBAN confirmed (`payment_iban`) or not (None)."""

    def __init__(self, world: World, payment_iban: str | None, label: str = "Kreditkarte"):
        self.world = world
        protected = None if payment_iban is None else protect_iban(payment_iban, TEST_IBAN_KEY)
        with workspace_transaction(world.engine, world.ws) as conn:
            self.account_id = conn.execute(
                insert(account_table)
                .values(
                    workspace_id=world.ws,
                    label=label,
                    bank="Beispielbank",
                    kind="credit_card",
                    purpose="private",
                    is_owner_account=True,
                    opening_balance=Decimal("0.00"),
                    opening_balance_on=date(2024, 1, 1),
                    payment_iban_masked=None if protected is None else protected.masked,
                    payment_iban_hmac=None if protected is None else protected.hmac,
                    created_at=world.now,
                    updated_at=world.now,
                )
                .returning(account_table.c.id)
            ).scalar_one()
            doc = world._document_row(conn, type="statement", subtype=None, title=None)
            self.import_id = conn.execute(
                insert(import_table)
                .values(
                    workspace_id=world.ws,
                    account_id=self.account_id,
                    document_id=doc,
                    format="card_pdf",
                    status=ImportStatus.RECONCILED.value,
                    balance_source="file",
                    declared_closing=Decimal("0.00"),
                    computed_closing=Decimal("0.00"),
                    created_at=world.now,
                    updated_at=world.now,
                )
                .returning(import_table.c.id)
            ).scalar_one()

    def payment(self, booking_date: date, amount: str) -> uuid.UUID:
        """The statement's payment line: a credit, no merchant, kind word Bezahlung."""
        with workspace_transaction(self.world.engine, self.world.ws) as conn:
            return conn.execute(
                insert(transaction_table)
                .values(
                    workspace_id=self.world.ws,
                    account_id=self.account_id,
                    import_id=self.import_id,
                    line_hash=hashlib.sha256(uuid.uuid4().bytes).digest(),
                    booking_date=booking_date,
                    value_date=None,
                    amount=Decimal(amount),
                    booking_text="Bezahlung",
                    purpose="Lastschrift",
                    bank_reference=str(uuid.uuid4().int)[:10],
                    created_at=self.world.now,
                )
                .returning(transaction_table.c.id)
            ).scalar_one()


def _match(world: World, tx):
    return world.cmd(matching.match_line, tx, iban_key=TEST_IBAN_KEY, ai_available=False)


def _booked(world: World, tx):
    return world.query(
        lambda c: [
            (r.category, r.status)
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


def test_the_debit_to_the_payment_iban_and_the_card_payment_are_one_transfer(world, giro):
    card = Card(world, PAYMENT_IBAN)
    debit = giro.line(MONDAY, "-312.75", iban=PAYMENT_IBAN, purpose="Kreditkarte")
    _match(world, debit)
    assert _booked(world, debit) == [("internal_transfer_out", "confirmed")]
    assert _links(world) == []  # the card's statement is not in yet
    paid = card.payment(TUESDAY, "312.75")
    _match(world, paid)
    assert _booked(world, paid) == [("internal_transfer_in", "confirmed")]
    [link] = _links(world)
    assert (link.out_transaction_id, link.in_transaction_id) == (debit, paid)
    assert (link.kind, link.matched_by) == (
        TransferLinkKind.BANK.value,
        TransferMatchedBy.MATCHER.value,
    )
    assert world.query(transfers.open_transfers, TODAY) == []


def test_the_card_statement_first_links_when_the_debit_arrives(world, giro):
    """The other order: the card's import is matched before the paying account's."""
    card = Card(world, PAYMENT_IBAN)
    paid = card.payment(TUESDAY, "312.75")
    _match(world, paid)
    assert _booked(world, paid) == [("internal_transfer_in", "confirmed")]
    debit = giro.line(MONDAY, "-312.75", iban=PAYMENT_IBAN)
    _match(world, debit)
    [link] = _links(world)
    assert (link.out_transaction_id, link.in_transaction_id) == (debit, paid)


def test_before_the_payment_iban_is_confirmed_the_debit_is_no_transfer(world, giro):
    card = Card(world, None)
    debit = giro.line(MONDAY, "-312.75", iban=PAYMENT_IBAN)
    _match(world, debit)
    assert not any(cat.startswith("internal_transfer") for cat, _ in _booked(world, debit))
    paid = card.payment(TUESDAY, "312.75")
    _match(world, paid)
    assert _booked(world, paid) == [("internal_transfer_in", "confirmed")]
    assert _links(world) == []
    # Confirming the IBAN re-checks the debits to it that wait (rematch_waiting's trigger).
    protected = protect_iban(PAYMENT_IBAN, TEST_IBAN_KEY)
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(
            update(account_table)
            .where(account_table.c.id == card.account_id)
            .values(payment_iban_masked=protected.masked, payment_iban_hmac=protected.hmac)
        )
    waiting = world.query(matching.waiting_lines, enqueue.TRIGGER_ACCOUNT, card.account_id)
    assert waiting == [debit]
    world.cmd(matching.match_line, debit, iban_key=TEST_IBAN_KEY, ai_available=False, rematch=True)
    assert _booked(world, debit) == [("internal_transfer_out", "confirmed")]
    [link] = _links(world)
    assert (link.out_transaction_id, link.in_transaction_id) == (debit, paid)


@pytest.mark.parametrize(
    ("paid_on", "linked"),
    [
        (MONDAY, True),  # the same day
        (date(2026, 10, 8), True),  # the third banking day after Monday
        (date(2026, 10, 9), False),  # the fourth
        (date(2026, 10, 2), False),  # before the debit
    ],
)
def test_the_link_needs_the_d162_window(world, giro, paid_on, linked):
    card = Card(world, PAYMENT_IBAN)
    debit = giro.line(MONDAY, "-312.75", iban=PAYMENT_IBAN)
    _match(world, debit)
    paid = card.payment(paid_on, "312.75")
    _match(world, paid)
    assert _booked(world, paid) == [("internal_transfer_in", "confirmed")]
    assert bool(_links(world)) is linked


def test_the_link_needs_the_amount_to_the_cent(world, giro):
    card = Card(world, PAYMENT_IBAN)
    debit = giro.line(MONDAY, "-312.75", iban=PAYMENT_IBAN)
    _match(world, debit)
    paid = card.payment(TUESDAY, "312.70")
    _match(world, paid)
    assert _links(world) == []


def test_a_debit_to_a_third_party_is_never_the_card_payment(world, giro):
    """The card's leg stands on its payment IBAN, so a debit of the same amount to someone else
    is no counterpart (ibans_agree)."""
    card = Card(world, PAYMENT_IBAN)
    other = giro.line(MONDAY, "-312.75", iban=OTHER_PAYMENT_IBAN, name="Jemand")
    _match(world, other)
    paid = card.payment(TUESDAY, "312.75")
    _match(world, paid)
    assert _links(world) == []


def test_two_cards_with_one_payment_iban_link_by_amount(world, giro):
    """Both cards print the issuer's one account: the debit is a transfer either way, and the
    amount picks the card."""
    first = Card(world, PAYMENT_IBAN)
    second = Card(world, PAYMENT_IBAN, label="Zweite Karte")
    small = first.payment(TUESDAY, "100.00")
    large = second.payment(TUESDAY, "250.00")
    _match(world, small)
    _match(world, large)
    debit = giro.line(MONDAY, "-250.00", iban=PAYMENT_IBAN)
    _match(world, debit)
    assert _booked(world, debit) == [("internal_transfer_out", "confirmed")]
    [link] = _links(world)
    assert (link.out_transaction_id, link.in_transaction_id) == (debit, large)


def test_two_cards_with_one_payment_iban_and_one_amount_ask_which(world, giro):
    first = Card(world, PAYMENT_IBAN)
    second = Card(world, PAYMENT_IBAN, label="Zweite Karte")
    a = first.payment(TUESDAY, "250.00")
    b = second.payment(TUESDAY, "250.00")
    _match(world, a)
    _match(world, b)
    debit = giro.line(MONDAY, "-250.00", iban=PAYMENT_IBAN)
    _match(world, debit)
    assert _links(world) == []
    [card] = [o for o in world.query(transfers.open_transfers, TODAY) if o.leg.id == debit]
    assert card.code is OpenTransferCode.AMBIGUOUS
    assert {c.id for c in card.candidates} == {a, b}
