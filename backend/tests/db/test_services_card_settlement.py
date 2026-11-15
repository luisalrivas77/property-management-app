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
from db.test_services_private_matching import NOW, Private, _answer_line
from sqlalchemy import insert, select, text, update

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.db.tables import transfer_link as link_table
from hausbuch.jobs import enqueue
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
    MatchingRuleCreate,
    RuleMatcher,
    SplitMethod,
)
from hausbuch.schemas.bank import AccountCreate
from hausbuch.schemas.enums import AccountKind, AccountPurpose, ImportStatus
from hausbuch.schemas.events import EventType
from hausbuch.schemas.household import (
    OpenTransferCode,
    TransferLinkKind,
    TransferLinkRequest,
    TransferMatchedBy,
)
from hausbuch.services import (
    accounts,
    allocations,
    household,
    matching,
    rules_learned,
    transfers,
)
from hausbuch.services.errors import RuleViolationError
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

    def purchase(self, booking_date: date, amount: str, merchant: str) -> uuid.UUID:
        """A purchase on the card: a debit naming its merchant."""
        with workspace_transaction(self.world.engine, self.world.ws) as conn:
            return conn.execute(
                insert(transaction_table)
                .values(
                    workspace_id=self.world.ws,
                    account_id=self.account_id,
                    import_id=self.import_id,
                    line_hash=hashlib.sha256(uuid.uuid4().bytes).digest(),
                    booking_date=booking_date,
                    value_date=booking_date,
                    amount=Decimal(amount),
                    counterparty_name=merchant,
                    booking_text="Kauf",
                    purpose=merchant,
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
    ("paid_on", "tied", "linked"),
    [
        (MONDAY, True, True),  # the same day
        (date(2026, 10, 8), True, True),  # the third banking day after Monday
        (date(2026, 10, 9), True, False),  # the fourth
        # D162 amended (5), 15 Nov 2026: the debit names the card's payment IBAN, so the
        # issuer's credit may come up to three banking days before it (Consors credited a
        # payment on 1 Jan, the Sparkasse booked the debit on 2 Jan).
        (date(2026, 10, 2), True, True),  # the banking day before the debit
        (date(2026, 9, 30), True, True),  # the third banking day before
        (date(2026, 9, 29), True, False),  # the fourth
        # Without the IBAN tie (a debit a person booked as a transfer, naming no IBAN), the
        # credit is never before the debit.
        (MONDAY, False, True),
        (date(2026, 10, 2), False, False),
    ],
)
def test_the_link_needs_the_d162_window(world, giro, paid_on, tied, linked):
    card = Card(world, PAYMENT_IBAN)
    if tied:
        debit = giro.line(MONDAY, "-312.75", iban=PAYMENT_IBAN)
        _match(world, debit)
    else:
        debit = giro.line(MONDAY, "-312.75", purpose="Kreditkarte")
        _answer_line(world, debit, "-312.75", "internal_transfer_out")
    assert _booked(world, debit) == [("internal_transfer_out", "confirmed")]
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


def _refund(card: Card, booking_date: date, amount: str) -> uuid.UUID:
    """A merchant's refund on the card: a credit naming its merchant, kind word Gutschrift,
    with the model's proposal on it."""
    with workspace_transaction(card.world.engine, card.world.ws) as conn:
        tx = conn.execute(
            insert(transaction_table)
            .values(
                workspace_id=card.world.ws,
                account_id=card.account_id,
                import_id=card.import_id,
                line_hash=hashlib.sha256(uuid.uuid4().bytes).digest(),
                booking_date=booking_date,
                value_date=booking_date,
                amount=Decimal(amount),
                counterparty_name="BEISPIEL VERSAND",
                booking_text="Gutschrift",
                purpose="BEISPIEL VERSAND",
                bank_reference=str(uuid.uuid4().int)[:10],
                created_at=card.world.now,
            )
            .returning(transaction_table.c.id)
        ).scalar_one()
    proposal = AllocationSet(
        transaction_id=tx,
        method=AllocationMethod.LLM,
        confidence=Decimal("0.700"),
        reasoning="Vermutlich eine Erstattung.",
        allocations=[
            AllocationDraft(amount=Decimal(amount), category=AllocationCategory.HH_REFUND)
        ],
    )
    card.world.cmd(allocations.record_set, proposal, confirm=False, auto=False)
    return tx


def test_a_merchant_refund_before_the_debit_is_never_booked_as_the_payment(world, giro):
    """Review of D162 amended (5), 15 Nov 2026: a refund of exactly the settlement amount, one
    banking day before a debit naming the payment IBAN, fits the early window, but only the
    card reader's own payment line is booked as the card's leg: the refund keeps the model's
    proposal, nothing is linked, and the debit's card names it for a person to decide. Once
    the real payment is in too, both fit: a question, never an automatic link."""
    card = Card(world, PAYMENT_IBAN)
    refund = _refund(card, date(2026, 10, 2), "312.75")
    debit = giro.line(MONDAY, "-312.75", iban=PAYMENT_IBAN, purpose="Kreditkarte")
    _match(world, debit)
    assert _booked(world, debit) == [("internal_transfer_out", "confirmed")]
    assert _booked(world, refund) == [("hh_refund", "proposed")]
    assert _links(world) == []
    [item] = [o for o in world.query(transfers.open_transfers, TODAY) if o.leg.id == debit]
    assert [c.id for c in item.candidates] == [refund]

    paid = card.payment(TUESDAY, "312.75")
    _match(world, paid)
    assert _booked(world, paid) == [("internal_transfer_in", "confirmed")]
    assert _booked(world, refund) == [("hh_refund", "proposed")]
    assert _links(world) == []
    [item] = [o for o in world.query(transfers.open_transfers, TODAY) if o.leg.id == debit]
    assert item.code is OpenTransferCode.AMBIGUOUS
    assert {c.id for c in item.candidates} == {refund, paid}


# --- A card bill answered as spending before the card account existed (review of step 2b) -----


def _spent(world: World, *months: str) -> Decimal:
    return sum(
        (
            sum(world.query(household.cashflow, month).spending_by_group.values(), Decimal(0))
            for month in months
        ),
        Decimal(0),
    )


def test_a_bill_answered_as_card_spending_is_re_booked_when_linked(world, giro):
    """The debit to the issuer was answered hh_credit_card by hand while no card account
    existed. Once the card's statement is in, its „Bezahlung“ card proposes that debit; the
    button re-books it as a transfer by the person's answer and links the two, so Haushalt
    counts the card's purchase once, not the purchase and its settlement."""
    debit = giro.line(MONDAY, "-312.75", iban=PAYMENT_IBAN, name="Beispiel Bank", purpose="Karte")
    _match(world, debit)
    _answer_line(world, debit, "-312.75", "hh_credit_card", learn=False)
    card = Card(world, PAYMENT_IBAN)
    purchase = card.purchase(date(2026, 9, 20), "-312.75", "BEISPIEL MARKT")
    _answer_line(world, purchase, "-312.75", "hh_groceries", learn=False)
    paid = card.payment(TUESDAY, "312.75")
    _match(world, paid)
    assert _spent(world, "2026-09", "2026-10") == Decimal("625.50")  # counted twice

    [item] = world.query(transfers.open_transfers, TODAY)
    assert (item.code, item.leg.id) == (OpenTransferCode.PROPOSED, paid)
    [candidate] = item.candidates
    assert candidate.id == debit
    assert (candidate.booked_category, candidate.booked_as_transfer) == ("hh_credit_card", False)
    assert candidate.rebook_on_link is True
    assert item.leg.booked_as_transfer is True

    link = world.cmd(
        transfers.link_by_hand,
        TransferLinkRequest(
            out_transaction_id=debit,
            in_transaction_id=paid,
            kind=TransferLinkKind.BANK,
            rebook=True,
        ),
        iban_key=TEST_IBAN_KEY,
    )
    assert (link.out_transaction_id, link.in_transaction_id) == (debit, paid)
    assert _booked(world, debit) == [("internal_transfer_out", "confirmed")]
    with workspace_transaction(world.engine, world.ws) as conn:
        methods = conn.execute(
            select(allocation_table.c.method, allocation_table.c.status).where(
                allocation_table.c.transaction_id == debit
            )
        ).all()
    assert ("user", "confirmed") in methods and any(s == "superseded" for _, s in methods)
    [current] = _links(world)
    assert current.id == link.id
    assert world.query(transfers.open_transfers, TODAY) == []
    assert _spent(world, "2026-09", "2026-10") == Decimal("312.75")  # the purchase, once


def test_a_candidate_tied_by_no_own_iban_keeps_the_plain_link(world, giro):
    """A debit naming no IBAN fits the card's payment by amount and date only: its card links
    it as it is, and a request to re-book it is refused (nothing written)."""
    card = Card(world, PAYMENT_IBAN)
    debit = giro.line(MONDAY, "-312.75", name="Barauszahlung", purpose="Karte")
    _answer_line(world, debit, "-312.75", "hh_credit_card", learn=False)
    paid = card.payment(TUESDAY, "312.75")
    _match(world, paid)
    [item] = world.query(transfers.open_transfers, TODAY)
    [candidate] = item.candidates
    assert candidate.id == debit
    assert (candidate.booked_as_transfer, candidate.rebook_on_link) == (False, False)
    body = dict(out_transaction_id=debit, in_transaction_id=paid, kind=TransferLinkKind.BANK)
    with pytest.raises(RuleViolationError):
        world.cmd(
            transfers.link_by_hand,
            TransferLinkRequest(**body, rebook=True),
            iban_key=TEST_IBAN_KEY,
        )
    assert _booked(world, debit) == [("hh_credit_card", "confirmed")]
    link = world.cmd(transfers.link_by_hand, TransferLinkRequest(**body))
    assert link.matched_by is TransferMatchedBy.USER
    assert _booked(world, debit) == [("hh_credit_card", "confirmed")]


def test_linking_a_pair_the_matcher_linked_meanwhile_returns_that_link(world, giro):
    """The card was shown before the matcher linked the pair (a re-check after another
    account's import reconciled): the click is already true, so it returns the link instead
    of 409 „a leg of this transfer is linked already“, with or without re-booking."""
    card = Card(world, PAYMENT_IBAN)
    debit = giro.line(MONDAY, "-312.75", iban=PAYMENT_IBAN)
    paid = card.payment(TUESDAY, "312.75")
    _match(world, debit)
    _match(world, paid)
    [link] = _links(world)
    for rebook in (False, True):
        again = world.cmd(
            transfers.link_by_hand,
            TransferLinkRequest(
                out_transaction_id=debit,
                in_transaction_id=paid,
                kind=TransferLinkKind.BANK,
                rebook=rebook,
            ),
            iban_key=TEST_IBAN_KEY,
        )
        assert again.id == link.id
    assert len(_links(world)) == 1


def test_a_new_private_account_rechecks_the_lines_that_name_its_iban(world, giro):
    """A line paying an account that was not yet in Hausbuch waits with the model's answer;
    creating that account (a private one, with its IBAN) queues the re-check that books it
    as a transfer, not only an owner account's creation."""
    other_iban = "DE02100100100006820101"
    debit = giro.line(MONDAY, "-1500.00", iban=other_iban, name="Gemeinsames Konto")
    _match(world, debit)
    assert not any(cat.startswith("internal_transfer") for cat, _ in _booked(world, debit))
    created = world.cmd(
        accounts.create_account,
        AccountCreate(
            label="Gemeinschaftskonto",
            bank="Beispielbank",
            iban=other_iban,
            purpose=AccountPurpose.PRIVATE,
            opening_balance=Decimal("0.00"),
            opening_balance_on=date(2025, 12, 31),
        ),
        iban_key=TEST_IBAN_KEY,
    )
    lock = enqueue.waiting_lock(enqueue.waiting_trigger(enqueue.TRIGGER_ACCOUNT, created.id))
    with workspace_transaction(world.engine, world.ws) as conn:
        queued = conn.execute(
            text("SELECT count(*) FROM procrastinate_jobs WHERE lock = :lock"), {"lock": lock}
        ).scalar_one()
    assert queued == 1
    waiting = world.query(matching.waiting_lines, enqueue.TRIGGER_ACCOUNT, created.id)
    assert waiting == [debit]
    world.cmd(matching.match_line, debit, iban_key=TEST_IBAN_KEY, ai_available=False, rematch=True)
    assert _booked(world, debit) == [("internal_transfer_out", "confirmed")]


# --- D160 amended (5): an IBAN that becomes an own account's switches off its rules -------------

SAVINGS_IBAN = "DE66100000000000003011"  # a savings account at another bank, not yet in the app


def _rule_on(world: World, iban: str, category: AllocationCategory):
    matcher = RuleMatcher(
        counterparty_iban_hmac=protect_iban(iban, TEST_IBAN_KEY).hmac.hex(), amount_sign="debit"
    )
    data = MatchingRuleCreate(
        matcher=matcher, split_method=SplitMethod.SINGLE_CATEGORY, category=category
    )
    return world.cmd(rules_learned.create_rule, data)


def _enabled_rules(world: World) -> set[uuid.UUID]:
    return {r.id for r in world.query(rules_learned.enabled_rules)}


def _disabled_events(world: World) -> list[uuid.UUID]:
    return world.query(
        lambda c: (
            c.execute(
                select(event_table.c.entity_id).where(
                    event_table.c.type == EventType.RULE_DISABLED.value
                )
            )
            .scalars()
            .all()
        )
    )


@pytest.mark.parametrize(
    "kind",
    [AccountKind.BANK_SAVINGS, AccountKind.BANK_CURRENT, AccountKind.DEPOT],
)
def test_an_account_created_with_the_iban_of_a_learned_rule_switches_the_rule_off(
    world, giro, kind
):
    """15 Nov 2026 (D160 amended (5)): the monthly payment to a savings account at another
    bank was answered as „Sparen & Tagesgeld“ with „Künftig immer so zuordnen“. A rule books
    before the transfer matcher, so once that account is added to Hausbuch (a bank account
    with its IBAN, or a depot with its IBAN typed) the rule is switched off (rule.disabled)
    and the next payment to it is a transfer between own accounts. A rule on another IBAN
    stays."""
    on_savings = _rule_on(world, SAVINGS_IBAN, AllocationCategory.HH_INVESTMENT_SAVINGS)
    other = _rule_on(world, OTHER_PAYMENT_IBAN, AllocationCategory.HH_GROCERIES)
    before = giro.line(date(2026, 9, 1), "-200.00", iban=SAVINGS_IBAN, name="Tagesgeld Beispiel")
    _match(world, before)
    assert _booked(world, before) == [("hh_investment_savings", "confirmed")]
    created = world.cmd(
        accounts.create_account,
        AccountCreate(
            label="Tagesgeld",
            bank="Beispielbank",
            iban=SAVINGS_IBAN,
            kind=kind,
            purpose=AccountPurpose.PRIVATE,
            opening_balance=Decimal("0.00"),
            opening_balance_on=date(2025, 12, 31),
        ),
        iban_key=TEST_IBAN_KEY,
    )
    assert _enabled_rules(world) == {other.id}
    assert _disabled_events(world) == [on_savings.id]
    lock = enqueue.waiting_lock(enqueue.waiting_trigger(enqueue.TRIGGER_ACCOUNT, created.id))
    with workspace_transaction(world.engine, world.ws) as conn:
        queued = conn.execute(
            text("SELECT count(*) FROM procrastinate_jobs WHERE lock = :lock"), {"lock": lock}
        ).scalar_one()
    assert queued == 1
    after = giro.line(date(2026, 10, 1), "-200.00", iban=SAVINGS_IBAN, name="Tagesgeld Beispiel")
    _match(world, after)
    assert _booked(world, after) == [("internal_transfer_out", "confirmed")]
    # The line the rule booked before stays as it was.
    assert _booked(world, before) == [("hh_investment_savings", "confirmed")]


def test_an_account_created_without_a_rule_on_its_iban_switches_nothing_off(world, giro):
    other = _rule_on(world, OTHER_PAYMENT_IBAN, AllocationCategory.HH_GROCERIES)
    world.cmd(
        accounts.create_account,
        AccountCreate(
            label="Tagesgeld",
            bank="Beispielbank",
            iban=SAVINGS_IBAN,
            kind=AccountKind.BANK_SAVINGS,
            purpose=AccountPurpose.PRIVATE,
            opening_balance=Decimal("0.00"),
            opening_balance_on=date(2025, 12, 31),
        ),
        iban_key=TEST_IBAN_KEY,
    )
    assert _enabled_rules(world) == {other.id}
    assert _disabled_events(world) == []
