"""Phase 6 step 2 under hausbuch_app and RLS: D162's transfer links (the late import, the
ambiguous and unmatched cards, a link by hand and its replacement, both events) and D161's
three paths for a property bill paid from a private account (the line proves a row, a card
proposes the row, a card asks for the bill). The pure halves are tests/rules/test_transfer_rules.py
and tests/rules/test_private_cost_link.py. Invented names and IBANs only."""

from datetime import date
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.matching_support import ACCOUNT_IBAN, PAINTER_IBAN, World
from db.test_services_card_settlement import PAYMENT_IBAN, Card
from db.test_services_exchange_deposit_fee import _exchange, _export, _movements
from db.test_services_private_matching import NOW, PRIVATE_IBAN, Private
from db.test_services_transfer_named_leg import DEPOT_B_IBAN, DEPOT_IBAN
from db.test_services_transfer_named_leg import _depot as _depot_account
from sqlalchemy import insert, select, update

from hausbuch import i18n
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import bank_connection as connection_table
from hausbuch.db.tables import bank_sync as sync_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transfer_link as link_table
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.common import Fraction
from hausbuch.schemas.enums import ImportStatus
from hausbuch.schemas.household import (
    OpenTransferCode,
    TransferLinkCreate,
    TransferLinkKind,
    TransferMatchedBy,
)
from hausbuch.schemas.tax import PrivateCostValue, TaxInputCreate, TaxInputKind, WarningCode
from hausbuch.services import allocations, matching, property_costs, review, tax_inputs, transfers
from hausbuch.services import tax as tax_service
from hausbuch.services.errors import ConflictError, RuleViolationError

pytestmark = pytest.mark.db

C = AllocationCategory
TODAY = NOW.date()
MONDAY = date(2026, 10, 5)
FRIEND_IBAN = "DE46100000000000000073"  # a third party's account, not the household's
SAVINGS_IBAN = "DE73100000000000000072"  # Bundesbank test bank code 10000000
OTHER_OWNER_IBAN = "DE19100000000000000074"  # the other owner's account, nothing imported


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


@pytest.fixture
def private(world) -> Private:
    return Private(world)


def _match(world: World, tx):
    return world.cmd(matching.match_line, tx, iban_key=TEST_IBAN_KEY, ai_available=False)


def _book(world: World, tx, amount: str, category: C, *, confirm: bool = True, **scope):
    return world.cmd(
        allocations.record_set,
        AllocationSet(
            transaction_id=tx,
            allocations=[AllocationDraft(amount=Decimal(amount), category=category, **scope)],
            method=AllocationMethod.USER,
            confidence=Decimal("1.000"),
        ),
        confirm=confirm,
        auto=False,
    )


def _rows(world: World, tx):
    return world.query(
        lambda c: c.execute(
            select(allocation_table).where(
                allocation_table.c.transaction_id == tx,
                allocation_table.c.status != "superseded",
            )
        ).all()
    )


def _links(world: World):
    return world.query(lambda c: c.execute(select(link_table)).all())


def _events(world: World, type_: str):
    return world.query(
        lambda c: c.execute(select(event_table).where(event_table.c.type == type_)).all()
    )


def _open(world: World):
    return world.query(transfers.open_transfers, TODAY)


def _savings(world: World) -> Private:
    """A Tagesgeld account of the household, with its own reconciled import."""
    savings = Private.__new__(Private)
    savings.world = world
    with workspace_transaction(world.engine, world.ws) as conn:
        savings.account_id = world._account(conn, SAVINGS_IBAN, owner=True)
        conn.execute(
            update(account_table)
            .where(account_table.c.id == savings.account_id)
            .values(kind="bank_savings")
        )
    savings.import_id = savings.add_import(world.now)
    return savings


# --- D162: links --------------------------------------------------------------------------


def test_the_late_import_books_and_links_the_transfer(world, private):
    """The rental account's top-up to the private account is booked first; the private
    account's line, imported later, is an internal transfer by the IBAN alone and is linked
    to it by the matcher, with transfer.linked."""
    out = world.line(MONDAY, "-500.00", iban=PRIVATE_IBAN, purpose="Umbuchung")
    _match(world, out)
    assert [(r.category, r.status) for r in _rows(world, out)] == [
        ("owner_transfer_out", "confirmed")
    ]
    assert _links(world) == []  # inside nothing: no counterpart yet, no card either
    in_ = private.line(date(2026, 10, 6), "500.00", iban=ACCOUNT_IBAN, purpose="Umbuchung")
    _match(world, in_)
    assert [(r.category, r.status) for r in _rows(world, in_)] == [
        ("internal_transfer_in", "confirmed")
    ]
    [link] = _links(world)
    assert (link.out_transaction_id, link.in_transaction_id) == (out, in_)
    assert (link.kind, link.matched_by, link.superseded_by) == (
        TransferLinkKind.BANK.value,
        TransferMatchedBy.MATCHER.value,
        None,
    )
    [event] = _events(world, "transfer.linked")
    assert event.entity_id == link.id
    assert event.payload["in_transaction_id"] == str(in_)
    assert _open(world) == []
    [view] = world.query(transfers.list_links, start=date(2026, 10, 1), end=date(2026, 10, 31))
    assert (view.out_leg.id, view.in_leg.id, view.in_leg.amount) == (
        out,
        in_,
        Decimal("500.00"),
    )


def test_two_equal_counterparts_are_ambiguous_from_either_side(world, private):
    out = world.line(MONDAY, "-300.00", iban=PRIVATE_IBAN, purpose="Umbuchung")
    _match(world, out)
    first = private.line(date(2026, 10, 6), "300.00", iban=ACCOUNT_IBAN)
    second = private.line(date(2026, 10, 7), "300.00", iban=ACCOUNT_IBAN)
    _match(world, first)
    _match(world, second)
    assert _links(world) == []  # neither of two equal legs is guessed
    [card] = _open(world)
    assert card.code is OpenTransferCode.AMBIGUOUS
    assert card.leg.id == out
    assert sorted(c.id for c in card.candidates) == sorted([first, second])
    [item] = [i for i in world.query(review.inbox, TODAY) if i.kind is InboxItemKind.TRANSFER_LINK]
    assert (item.transaction_id, item.question) == (out, "Welche Buchung ist die Gegenbuchung?")
    with i18n.use("en"):  # phase 10 D284: the card in the request's language
        [en] = [i for i in world.query(review.inbox, TODAY) if i.kind is item.kind]
    assert en.question == "Which transaction is the matching transaction?"
    assert en.reasoning.startswith("Several matching transactions fit")


def test_a_third_partys_credit_of_the_same_amount_is_never_the_counterpart(world, private):
    """Review F1: a friend's 500 on the private account, booked as other income, is no
    counterpart of the rental account's transfer to that account (its IBAN names a third
    account); the real mirror, imported later, is linked and no card is left."""
    out = world.line(MONDAY, "-500.00", iban=PRIVATE_IBAN, purpose="Umbuchung")
    _match(world, out)
    assert [(r.category, r.status) for r in _rows(world, out)] == [
        ("owner_transfer_out", "confirmed")
    ]
    friend = private.line(
        date(2026, 10, 6), "500.00", iban=FRIEND_IBAN, name="Freund", purpose="Geschenk"
    )
    _book(world, friend, "500.00", C.HH_OTHER_INCOME)
    assert _links(world) == []
    mirror = private.line(date(2026, 10, 6), "500.00", iban=ACCOUNT_IBAN, purpose="Umbuchung")
    _match(world, mirror)
    assert [r.category for r in _rows(world, mirror)] == ["internal_transfer_in"]
    [link] = _links(world)
    assert (link.out_transaction_id, link.in_transaction_id) == (out, mirror)
    assert link.matched_by == TransferMatchedBy.MATCHER.value
    assert _open(world) == []


def test_two_equal_transfers_into_one_account_are_told_apart_by_their_ibans(world, private):
    """Review F1, scenario B: the rental and the private account each pay 500 into the savings
    account on one day; each savings credit names its sender, so each is linked to its own
    out-leg and neither side asks which (no transfer.ambiguous)."""
    savings = _savings(world)
    from_rental = world.line(MONDAY, "-500.00", iban=SAVINGS_IBAN, purpose="Sparen")
    from_private = private.line(MONDAY, "-500.00", iban=SAVINGS_IBAN, purpose="Sparen")
    in_rental = savings.line(date(2026, 10, 6), "500.00", iban=ACCOUNT_IBAN, purpose="Sparen")
    in_private = savings.line(date(2026, 10, 6), "500.00", iban=PRIVATE_IBAN, purpose="Sparen")
    for tx in (from_rental, from_private, in_rental, in_private):
        _match(world, tx)
    assert [r.category for r in _rows(world, from_rental)] == ["owner_transfer_out"]
    assert [r.category for r in _rows(world, in_private)] == ["internal_transfer_in"]
    pairs = {(link.out_transaction_id, link.in_transaction_id) for link in _links(world)}
    assert pairs == {(from_rental, in_rental), (from_private, in_private)}
    assert _open(world) == []


def test_a_matcher_link_ends_when_its_leg_is_booked_as_something_else(world, private):
    """Review F3: a proposed transfer answer is linked; the person corrects the line to a gift,
    which withdraws the link by that booking: the rental leg is open again with its card,
    list_links no longer lists the gift and no second link row is written."""
    out = world.line(MONDAY, "-500.00", iban=PRIVATE_IBAN, purpose="Umbuchung")
    _match(world, out)
    line = private.line(date(2026, 10, 6), "500.00", name="Umbuchung", purpose="Umbuchung")
    _book(world, line, "500.00", C.INTERNAL_TRANSFER_IN, confirm=False)
    [link] = _links(world)
    assert (link.out_transaction_id, link.in_transaction_id, link.matched_by) == (
        out,
        line,
        TransferMatchedBy.MATCHER.value,
    )
    assert _open(world) == []

    _book(world, line, "500.00", C.HH_OTHER_INCOME)  # the person: it was a gift
    [row] = _links(world)
    assert row.superseded_by is None  # append-only: the row stays, it is no longer current
    # The rental leg is open again; the gift still fits it by amount and date, so its card
    # offers it for a link by hand (transfer.proposed), never linked by the matcher again.
    [card] = _open(world)
    assert (card.code, card.leg.id) == (OpenTransferCode.PROPOSED, out)
    assert [c.id for c in card.candidates] == [line]
    month = {"start": date(2026, 10, 1), "end": date(2026, 10, 31)}
    assert world.query(transfers.list_links, **month) == []


def test_a_leg_booked_as_a_transfer_again_brings_its_link_back_without_a_second_row(world, private):
    out = world.line(MONDAY, "-500.00", iban=PRIVATE_IBAN, purpose="Umbuchung")
    _match(world, out)
    line = private.line(date(2026, 10, 6), "500.00", name="Umbuchung", purpose="Umbuchung")
    _book(world, line, "500.00", C.INTERNAL_TRANSFER_IN, confirm=False)
    _book(world, line, "500.00", C.HH_OTHER_INCOME)
    _book(world, line, "500.00", C.INTERNAL_TRANSFER_IN)  # the person changes their mind
    [link] = _links(world)
    assert (link.out_transaction_id, link.in_transaction_id, link.superseded_by) == (
        out,
        line,
        None,
    )
    assert len(_events(world, "transfer.linked")) == 1
    assert _open(world) == []
    month = {"start": date(2026, 10, 1), "end": date(2026, 10, 31)}
    [view] = world.query(transfers.list_links, **month)
    assert (view.out_leg.id, view.in_leg.id) == (out, line)


def test_a_leg_past_its_window_without_a_counterpart_gets_the_unmatched_card(world, private):
    leg = private.line(MONDAY, "-200.00", iban=ACCOUNT_IBAN, purpose="Rücklage")
    _match(world, leg)
    assert [r.category for r in _rows(world, leg)] == ["internal_transfer_out"]
    [card] = _open(world)
    assert (card.code, card.leg.id, card.candidates) == (OpenTransferCode.UNMATCHED_LEG, leg, [])
    assert card.window_end == date(2026, 10, 8)  # three banking days after a Monday
    # Inside its window the same leg has no card.
    assert world.query(transfers.open_transfers, date(2026, 10, 8)) == []
    [item] = [
        i for i in world.query(review.inbox, TODAY) if i.kind is InboxItemKind.TRANSFER_UNMATCHED
    ]
    assert item.transaction_id == leg
    assert item.transfer is not None and item.transfer.code is OpenTransferCode.UNMATCHED_LEG


@pytest.mark.parametrize(
    ("first_elsewhere", "card"),
    [(date(2026, 1, 2), True), (date(2026, 1, 3), False)],
)
def test_a_leg_before_every_other_accounts_history_gets_no_card(
    world, private, blob_store, first_elsewhere, card
):
    """D162 amended (5), 15 Nov 2026 (Luis: „There are some credit card deposits that were not
    directly linked. Can you check?“): a transfer leg of 2 Dec 2025 without an IBAN, while the
    other account's lines start in January 2026, can have no counterpart: no card once its
    by-hand window (±31 days, to 2 Jan) ends before that account's first line. An older
    statement imported later brings the counterpart; booked as a transfer, it is linked."""
    leg = private.line(date(2025, 12, 2), "-200.00", purpose="Umbuchung")
    _book(world, leg, "-200.00", C.INTERNAL_TRANSFER_OUT)
    savings = _savings(world)
    savings.line(first_elsewhere, "12.00", purpose="Zinsen")
    # An exchange's export reaches back years before the bank lines: its movements do not
    # count as history, or one old trade would keep every card.
    exchange = _exchange(world)
    _export(world, blob_store, exchange.id, "2024-03-04 08:00:00,Deposit,50")
    old_deposit = _movements(world, exchange.id)[Decimal("50.00")]
    cards = [c for c in _open(world) if c.leg.id == leg]
    assert [c.code for c in cards] == ([OpenTransferCode.UNMATCHED_LEG] if card else [])
    # The exchange's own deposit of 2024 is older than every bank line: no card either.
    assert [c for c in _open(world) if c.leg.id == old_deposit] == []
    unmatched = [
        i for i in world.query(review.inbox, TODAY) if i.kind is InboxItemKind.TRANSFER_UNMATCHED
    ]
    assert [i.transaction_id for i in unmatched] == ([leg] if card else [])

    # The December statement of the other account arrives later: its line fits the leg, so
    # the card comes back proposing it, and booked as a transfer it is linked.
    older = savings.add_import(world.now)
    late = savings.line(date(2025, 12, 3), "200.00", purpose="Umbuchung", import_id=older)
    [back] = [c for c in _open(world) if c.leg.id == leg]
    assert (back.code, [c.id for c in back.candidates]) == (OpenTransferCode.PROPOSED, [late])
    _book(world, late, "200.00", C.INTERNAL_TRANSFER_IN)
    [link] = _links(world)
    assert (link.out_transaction_id, link.in_transaction_id) == (leg, late)
    assert _open(world) == []


@pytest.mark.parametrize(
    ("named", "card"),
    [("empty", True), ("savings", False)],
)
def test_a_leg_naming_an_own_account_is_decided_by_that_account(world, private, named, card):
    """Review of D162 amended (5), 15 Nov 2026: a debit of 2 Dec 2025 naming the other owner's
    account, whose statements are not imported, keeps its card although the only other account's
    lines start after the window (it had lost its one to-do); a debit naming an account whose
    first line is after the window gets none, although the rental account's lines start
    before it."""
    with workspace_transaction(world.engine, world.ws) as conn:
        world._account(conn, OTHER_OWNER_IBAN, owner=True)  # no import, no line
    savings = _savings(world)
    savings.line(date(2026, 1, 3), "12.00", purpose="Zinsen")  # after the window (2 Jan)
    if named == "empty":
        iban = OTHER_OWNER_IBAN
    else:
        iban = SAVINGS_IBAN
        world.line(date(2025, 11, 20), "-5.00", name="Bank")  # the rental account, before it
    leg = private.line(date(2025, 12, 2), "-200.00", iban=iban, purpose="Umbuchung")
    _book(world, leg, "-200.00", C.INTERNAL_TRANSFER_OUT)
    cards = [c for c in _open(world) if c.leg.id == leg]
    assert [c.code for c in cards] == ([OpenTransferCode.UNMATCHED_LEG] if card else [])


# --- D162 amended (9): the leg after the named account's history ----------------------------

NOV_DEBIT = date(2026, 11, 3)  # a Tuesday; its D162 window ends Friday 6 Nov
NOV_WINDOW_END = date(2026, 11, 6)


def _unmatched(world: World, leg) -> list[OpenTransferCode]:
    return [c.code for c in _open(world) if c.leg.id == leg]


def test_a_monthly_depot_debit_after_the_depots_last_statement_gets_no_card(world, private):
    """D162 amended (9), 15 Nov 2026: a private account pays two depots 600 € each at the start
    of every month, each debit naming its depot's IBAN. October's pairs are linked; the depot
    statements reach 30 Oct, so November's debits, booked and past their window, can have no
    counterpart yet: no card, also not in Zu prüfen. The next statement of one depot reaching
    the window without the credit brings that card back; the other's, with the credit, links
    it."""
    depot_a = _depot_account(world, DEPOT_IBAN)
    depot_b = _depot_account(world, DEPOT_B_IBAN)
    for depot, iban in ((depot_a, DEPOT_IBAN), (depot_b, DEPOT_B_IBAN)):
        out = private.line(MONDAY, "-600.00", iban=iban, purpose="Sparplan")
        _match(world, out)
        depot.line(MONDAY, "600.00", purpose="Lastschrift Einzug")
        world.cmd(transfers.relink, depot.account_id)  # what a depot statement import runs
        depot.line(date(2026, 10, 30), "-1.20", purpose="Kostenbelastung")
    assert len(_links(world)) == 2
    debit_a = private.line(NOV_DEBIT, "-600.00", iban=DEPOT_IBAN, purpose="Sparplan")
    debit_b = private.line(NOV_DEBIT, "-600.00", iban=DEPOT_B_IBAN, purpose="Sparplan")
    for leg in (debit_a, debit_b):
        _match(world, leg)
        assert [(r.category, r.status) for r in _rows(world, leg)] == [
            ("internal_transfer_out", "confirmed")
        ]
    assert len(_links(world)) == 2
    assert _open(world) == []
    kinds = {i.kind for i in world.query(review.inbox, TODAY)}
    assert InboxItemKind.TRANSFER_UNMATCHED not in kinds

    # Depot A's November statement reaches past the window, without the credit: the card is
    # back. Depot B's still ends on 30 Oct: its debit stays silent.
    later_a = depot_a.add_import(world.now)
    depot_a.line(date(2026, 11, 10), "0.40", purpose="Zinsen", import_id=later_a)
    [card] = _open(world)
    assert (card.code, card.leg.id, card.candidates) == (
        OpenTransferCode.UNMATCHED_LEG,
        debit_a,
        [],
    )
    # Depot B's November statement brings the credit: the import's relink links it.
    later_b = depot_b.add_import(world.now)
    credit_b = depot_b.line(NOV_DEBIT, "600.00", purpose="Lastschrift Einzug", import_id=later_b)
    depot_b.line(date(2026, 11, 10), "0.40", purpose="Zinsen", import_id=later_b)
    assert world.cmd(transfers.relink, depot_b.account_id) == 1
    assert (debit_b, credit_b) in {
        (link.out_transaction_id, link.in_transaction_id) for link in _links(world)
    }
    assert [c.leg.id for c in _open(world)] == [debit_a]


@pytest.mark.parametrize(
    ("last_line", "card"),
    [(NOV_WINDOW_END, True), (date(2026, 11, 5), False)],
)
def test_the_named_accounts_history_must_reach_the_window_end(world, private, last_line, card):
    """D162 amended (9): a debit naming an own bank account keeps its card when that account's
    lines reach the leg's window end (6 Nov), as before; one day short, it gets none."""
    savings = _savings(world)
    savings.line(date(2026, 10, 1), "12.00", purpose="Zinsen")
    savings.line(last_line, "0.10", purpose="Zinsen")
    leg = private.line(NOV_DEBIT, "-200.00", iban=SAVINGS_IBAN, purpose="Umbuchung")
    _match(world, leg)
    assert [r.category for r in _rows(world, leg)] == ["internal_transfer_out"]
    assert _unmatched(world, leg) == ([OpenTransferCode.UNMATCHED_LEG] if card else [])


@pytest.mark.parametrize(
    ("status", "card"),
    [("nothing_new", True), ("failed", False)],
)
def test_a_successful_sync_without_new_lines_counts_as_history(world, private, status, card):
    """D162 amended (9): an account synced over FinTS whose run on 12 Nov brought no new line
    (no import is kept, phase 5 D127) has its history up to that day, so the debit naming it
    keeps its card; a failed run proves nothing and the card stays away."""
    savings = _savings(world)
    savings.line(date(2026, 10, 30), "12.00", purpose="Zinsen")
    with workspace_transaction(world.engine, world.ws) as conn:
        connection = conn.execute(
            insert(connection_table)
            .values(
                workspace_id=world.ws,
                account_id=savings.account_id,
                bank_code="10000000",
                fints_url="https://fints.example.invalid/",
                login_name="beispiel",
            )
            .returning(connection_table.c.id)
        ).scalar_one()
        conn.execute(
            insert(sync_table).values(
                workspace_id=world.ws,
                account_id=savings.account_id,
                connection_id=connection,
                trigger="scheduled",
                status=status,
                requested_from=date(2026, 10, 29),
                requested_to=TODAY,
                lines_total=0,
                lines_new=0,
                started_at=world.now,
                finished_at=world.now,
            )
        )
    leg = private.line(NOV_DEBIT, "-200.00", iban=SAVINGS_IBAN, purpose="Umbuchung")
    _match(world, leg)
    assert _unmatched(world, leg) == ([OpenTransferCode.UNMATCHED_LEG] if card else [])


def test_a_leg_naming_no_own_account_keeps_its_card(world, private):
    """D162 amended (9): a leg booked as a transfer that names no own account is not decided by
    any account's last line, or an unrelated account whose statements stop would hide it."""
    savings = _savings(world)
    savings.line(date(2026, 10, 1), "12.00", purpose="Zinsen")  # its lines end before the window
    leg = private.line(NOV_DEBIT, "-200.00", purpose="Umbuchung")
    _book(world, leg, "-200.00", C.INTERNAL_TRANSFER_OUT)
    assert _unmatched(world, leg) == [OpenTransferCode.UNMATCHED_LEG]


def test_a_named_account_without_any_line_keeps_the_card(world, private):
    """D162 amended (9), as in D162 amended (5)'s review: an own account whose statements were
    never imported (the other owner's) is no history that ends; the debit naming it keeps its
    card."""
    with workspace_transaction(world.engine, world.ws) as conn:
        world._account(conn, OTHER_OWNER_IBAN, owner=True)  # no import, no line
    leg = private.line(NOV_DEBIT, "-200.00", iban=OTHER_OWNER_IBAN, purpose="Umbuchung")
    _match(world, leg)
    assert [r.category for r in _rows(world, leg)] == ["internal_transfer_out"]
    assert _unmatched(world, leg) == [OpenTransferCode.UNMATCHED_LEG]


def test_a_card_payment_after_its_paying_accounts_last_line_gets_no_card(world, private):
    """D162 amended (9), the mirror of (6) (7): the card statement's payment credit of 3 Nov,
    which names no IBAN, is decided by the account that pays the card (its October settlement
    names the card's payment IBAN); that account's lines end on 30 Oct, so no card until its
    next statement reaches the window without the debit."""
    card = Card(world, PAYMENT_IBAN)
    debit = private.line(MONDAY, "-300.00", iban=PAYMENT_IBAN, purpose="Kreditkarte")
    _match(world, debit)
    _match(world, card.payment(MONDAY, "300.00"))
    assert len(_links(world)) == 1
    private.line(date(2026, 10, 30), "-20.00", name="Bäckerei Beispiel")
    payment = card.payment(NOV_DEBIT, "450.00")
    _match(world, payment)
    assert [r.category for r in _rows(world, payment)] == ["internal_transfer_in"]
    assert _unmatched(world, payment) == []
    later = private.add_import(world.now)
    private.line(date(2026, 11, 9), "-15.00", name="Bäckerei Beispiel", import_id=later)
    assert _unmatched(world, payment) == [OpenTransferCode.UNMATCHED_LEG]


def _card_statement(world: World, card: Card) -> None:
    """The card's next monthly statement: a reconciled import its later lines go to."""
    with workspace_transaction(world.engine, world.ws) as conn:
        doc = world._document_row(conn, type="statement", subtype=None, title=None)
        card.import_id = conn.execute(
            insert(import_table)
            .values(
                workspace_id=world.ws,
                account_id=card.account_id,
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


def test_a_card_settlement_debit_after_the_cards_last_statement_gets_no_card(world, private):
    """D162 amended (9), the card direction (Luis: „Does the Quirion transfer card also apply to
    the credit card card?“): the bank's debit of 3 Nov names the card's payment IBAN, so it is
    decided by the card account, whose monthly statements (imports of their own, read like a
    bank's) end on 30 Oct: no card. The card's November statement reaching the window without
    the settlement credit brings the card back."""
    card = Card(world, PAYMENT_IBAN)
    card.purchase(date(2026, 10, 30), "-20.00", "Buchhandlung Beispiel")
    debit = private.line(NOV_DEBIT, "-450.00", iban=PAYMENT_IBAN, purpose="Kreditkarte")
    _match(world, debit)
    assert [(r.category, r.status) for r in _rows(world, debit)] == [
        ("internal_transfer_out", "confirmed")
    ]
    assert _unmatched(world, debit) == []
    _card_statement(world, card)
    card.purchase(date(2026, 11, 9), "-15.00", "Buchhandlung Beispiel")
    assert _unmatched(world, debit) == [OpenTransferCode.UNMATCHED_LEG]


def test_a_link_by_hand_and_its_replacement(world, private):
    leg = private.line(MONDAY, "-200.00", iban=ACCOUNT_IBAN, purpose="Rücklage")
    _match(world, leg)
    late = world.line(date(2026, 10, 20), "200.00", name="Konto B")  # outside the window
    other = world.line(date(2026, 10, 21), "200.00", name="Konto B")
    [card] = _open(world)
    assert sorted(c.id for c in card.candidates) == sorted([late, other])

    body = TransferLinkCreate(
        kind=TransferLinkKind.BANK, out_transaction_id=leg, in_transaction_id=late
    )
    link = world.cmd(transfers.link_by_hand, body)
    assert link.matched_by is TransferMatchedBy.USER
    assert _open(world) == []
    # The same pair again (a second click, a card shown before the link): no conflict.
    assert world.cmd(transfers.link_by_hand, body).id == link.id

    replacement = body.model_copy(update={"in_transaction_id": other})
    new = world.cmd(transfers.unlink, link.id, replacement)
    rows = {r.id: r for r in _links(world)}
    assert rows[link.id].superseded_by == new.id
    assert rows[new.id].superseded_by is None
    assert (rows[new.id].out_transaction_id, rows[new.id].in_transaction_id) == (leg, other)
    [unlinked] = _events(world, "transfer.unlinked")
    assert (unlinked.entity_id, unlinked.payload["superseded_by"]) == (link.id, str(new.id))
    assert len(_events(world, "transfer.linked")) == 2
    with pytest.raises(ConflictError):  # the old link is replaced already
        world.cmd(transfers.unlink, link.id, replacement)


def test_two_legs_that_are_no_transfer_are_refused(world, private):
    a = private.line(MONDAY, "-50.00")
    b = private.line(MONDAY, "50.00")  # the same account
    with pytest.raises(RuleViolationError) as refused:
        world.cmd(
            transfers.link_by_hand,
            TransferLinkCreate(
                kind=TransferLinkKind.BANK, out_transaction_id=a, in_transaction_id=b
            ),
        )
    assert refused.value.codes == ("transfer.legs_invalid",)
    c = world.line(MONDAY, "-50.00")  # both legs out
    with pytest.raises(RuleViolationError):
        world.cmd(
            transfers.link_by_hand,
            TransferLinkCreate(
                kind=TransferLinkKind.BANK, out_transaction_id=a, in_transaction_id=c
            ),
        )


# --- D161: a property bill paid from a private account ---------------------------------------


def _bill(world: World):
    return world.document(
        "invoice",
        {
            "document_date": "2026-06-10",
            "amount_total": "297.50",
            "reference": "R-2026-0815",
            "iban": PAINTER_IBAN,
            "cost_lines": [
                "Malerarbeiten Treppenhaus | 250,00 EUR | repairs",
                "Umsatzsteuer 19 % | 47,50 EUR | vat",
            ],
        },
        title="Malerarbeiten",
    )


def _paying_line(private: Private):
    return private.line(
        date(2026, 6, 15),
        "-297.50",
        iban=PAINTER_IBAN,
        name="Malerei Beispiel",
        purpose="R-2026-0815",
    )


def _missing_proofs(world: World):
    draft = world.query(tax_service.anlage_v_draft, world.property_id, tax_year=2026, today=TODAY)
    return draft, [
        w.params.get("document")
        for w in draft.warnings
        if w.code is WarningCode.PAYMENT_PROOF_MISSING
    ]


def _on_bill(world: World, doc) -> dict:
    return {"property_id": world.property_id, "document_id": doc}


def _private_cost(world: World, doc):
    return world.cmd(
        tax_inputs.create_tax_input,
        world.property_id,
        TaxInputCreate(
            property_id=world.property_id,
            kind=TaxInputKind.PRIVATE_COST,
            tax_year=2026,
            document_id=doc,
            value=PrivateCostValue(
                cost_class="other",
                label="Malerarbeiten",
                amount=Decimal("297.50"),
                share=Fraction(numerator=1, denominator=1),
                category=C.REPAIRS,
            ),
        ),
    )


def test_path_1_a_private_line_proves_the_bills_row(world, private):
    doc = _bill(world)
    row = _private_cost(world, doc)
    _, missing = _missing_proofs(world)
    assert missing == [str(doc)]  # „Zahlungsnachweis hochladen“ until a payment proves it

    tx = _paying_line(private)
    _match(world, tx)
    [booked] = _rows(world, tx)
    assert (booked.category, booked.status, booked.property_id, booked.document_id) == (
        "hh_property_cost",
        "proposed",  # D159: never auto-confirmed
        world.property_id,
        doc,
    )
    assert _missing_proofs(world)[1] == [str(doc)]  # a proposed row proves nothing yet
    world.cmd(allocations.confirm_proposed, tx)

    draft, missing = _missing_proofs(world)
    assert missing == []
    cited = [
        line
        for line in draft.lines
        if any(s.kind == "tax_input" and s.id == row.id for s in line.sources)
    ]
    assert cited and any(s.id == booked.id for line in cited for s in line.sources)
    # Path 1 needs no card, and the line is never a cost of its own.
    kinds = {i.kind for i in world.query(review.inbox, TODAY)}
    assert InboxItemKind.PROPERTY_COST_ROW not in kinds
    assert InboxItemKind.PROPERTY_COST_BILL not in kinds


def test_path_2_a_bill_without_a_row_gets_the_rows_proposed(world, private):
    doc = _bill(world)
    tx = _paying_line(private)
    _match(world, tx)
    [item] = [
        i for i in world.query(review.inbox, TODAY) if i.kind is InboxItemKind.PROPERTY_COST_ROW
    ]
    assert (item.transaction_id, item.document_id, item.property_id) == (
        tx,
        doc,
        world.property_id,
    )
    [proposed] = item.private_costs
    assert (proposed.tax_year, proposed.document_id, proposed.kind) == (
        2026,
        doc,
        TaxInputKind.PRIVATE_COST,
    )
    assert (proposed.value.category, proposed.value.amount, proposed.value.cost_class) == (
        C.REPAIRS,
        Decimal("297.50"),
        "other",
    )
    # The user takes it through the existing command; the card ends.
    world.cmd(tax_inputs.create_tax_input, world.property_id, proposed)
    kinds = {i.kind for i in world.query(review.inbox, TODAY)}
    assert InboxItemKind.PROPERTY_COST_ROW not in kinds


def test_path_3_a_line_without_a_bill_asks_for_it(world, private):
    tx = private.line(date(2026, 7, 3), "-80.00", name="Schlüsseldienst", purpose="Barzahlung")
    _book(world, tx, "-80.00", C.HH_PROPERTY_COST, property_id=world.property_id)
    [item] = [
        i for i in world.query(review.inbox, TODAY) if i.kind is InboxItemKind.PROPERTY_COST_BILL
    ]
    assert (item.transaction_id, item.property_id, item.question) == (
        tx,
        world.property_id,
        "Beleg hochladen",
    )
    # Never a cost in the draft: hh_property_cost is pass_through and never summed (D161).
    draft, _ = _missing_proofs(world)
    assert not any(s.id == tx for line in draft.lines for s in line.sources)


def _cards(world: World, kind: InboxItemKind):
    return [i for i in world.query(review.inbox, TODAY) if i.kind is kind]


def _cited_rows(world: World, year: int) -> set:
    draft = world.query(tax_service.anlage_v_draft, world.property_id, tax_year=year, today=TODAY)
    return {s.id for line in draft.lines for s in line.sources if s.kind == "tax_input"}


def test_path_1_a_row_of_another_year_is_moved_to_the_year_paid_once_confirmed(world, private):
    """Review F2: the bill's row is in 2026, the private line paid it on 5 Jan 2027. Once the
    line is confirmed a card proposes counting it in 2027 (D143's year-paid rule); until the
    person confirms, the 2026 draft keeps the row (principle 3); the button moves it."""
    doc = _bill(world)
    row = _private_cost(world, doc)
    tx = private.line(date(2027, 1, 5), "-297.50", iban=PAINTER_IBAN, purpose="R-2026-0815")
    _book(world, tx, "-297.50", C.HH_PROPERTY_COST, confirm=False, **_on_bill(world, doc))
    assert _cards(world, InboxItemKind.PROPERTY_COST_YEAR) == []  # a proposed line: not yet
    world.cmd(allocations.confirm_proposed, tx)

    [card] = _cards(world, InboxItemKind.PROPERTY_COST_YEAR)
    assert (card.transaction_id, card.document_id, card.property_id, card.tax_year) == (
        tx,
        doc,
        world.property_id,
        2027,
    )
    assert card.question == "In 2027 zählen?"
    assert "05.01.2027" in card.reasoning and "2026" in card.reasoning
    with i18n.use("en"):
        [en] = _cards(world, InboxItemKind.PROPERTY_COST_YEAR)
    assert en.question == "Count in 2027?"
    assert "paid on 5 Jan 2027" in en.reasoning and "tax year 2026" in en.reasoning
    assert _cards(world, InboxItemKind.PROPERTY_COST_ROW) == []
    assert row.id in _cited_rows(world, 2026)  # unchanged until the person confirms

    recorded = len(_events(world, "property.tax_input_recorded"))
    [moved] = world.cmd(property_costs.count_in_year_paid, tx)
    assert (moved.tax_year, moved.document_id, moved.value) == (2027, doc, row.value)
    assert len(_events(world, "property.tax_input_recorded")) == recorded + 2  # withdrawn, new
    [current] = world.query(tax_inputs.current_of_document, doc, TaxInputKind.PRIVATE_COST)
    assert current.id == moved.id
    assert row.id not in _cited_rows(world, 2026)
    assert _cards(world, InboxItemKind.PROPERTY_COST_YEAR) == []
    # Again: nothing to move, nothing written.
    assert [r.id for r in world.cmd(property_costs.count_in_year_paid, tx)] == [moved.id]
    assert len(_events(world, "property.tax_input_recorded")) == recorded + 2


def test_an_attached_bill_that_yields_no_row_keeps_a_card_with_the_reason(world, private):
    """Review F4 (i): after „Beleg hochladen“ the line keeps a card naming why no row is
    proposed, with the document to open: not read as a bill (a till receipt read as a
    general document), no property linked, or no amount or date."""
    receipt = world.document(
        "general", {"title": "Kassenbon", "document_date": "2026-07-03"}, type="other"
    )
    unlinked = world.document(
        "invoice",
        {"document_date": "2026-07-03", "amount_total": "80.00"},
        link_property=False,
    )
    undated = world.document("invoice", {"amount_total": "80.00"})
    expected = {}
    for doc, text in (
        (receipt, "nicht als Rechnung oder Bescheid gelesen"),
        (unlinked, "noch keinem Mietobjekt zugeordnet"),
        (undated, "fehlt der Betrag oder das Datum"),
    ):
        tx = private.line(date(2026, 7, 3), "-80.00", name="Schlüsseldienst", purpose="bar")
        _book(world, tx, "-80.00", C.HH_PROPERTY_COST, **_on_bill(world, doc))
        expected[tx] = (doc, text)
    cards = _cards(world, InboxItemKind.PROPERTY_COST_DOCUMENT)
    assert {c.transaction_id for c in cards} == set(expected)
    for card in cards:
        doc, text = expected[card.transaction_id]
        assert card.document_id == doc and text in card.reasoning
        assert card.question == "Beleg öffnen und prüfen"
    assert _cards(world, InboxItemKind.PROPERTY_COST_BILL) == []
    assert _cards(world, InboxItemKind.PROPERTY_COST_ROW) == []
