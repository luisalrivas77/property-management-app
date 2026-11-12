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
from db.test_services_private_matching import NOW, PRIVATE_IBAN, Private
from sqlalchemy import select, update

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import transfer_link as link_table
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.common import Fraction
from hausbuch.schemas.household import (
    OpenTransferCode,
    TransferLinkCreate,
    TransferLinkKind,
    TransferMatchedBy,
)
from hausbuch.schemas.tax import PrivateCostValue, TaxInputCreate, TaxInputKind, WarningCode
from hausbuch.services import allocations, matching, review, tax_inputs, transfers
from hausbuch.services import tax as tax_service
from hausbuch.services.errors import ConflictError, RuleViolationError

pytestmark = pytest.mark.db

C = AllocationCategory
TODAY = NOW.date()
MONDAY = date(2026, 10, 5)
FRIEND_IBAN = "DE46100000000000000073"  # a third party's account, not the household's
SAVINGS_IBAN = "DE73100000000000000072"  # Bundesbank test bank code 10000000


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
    with pytest.raises(ConflictError):
        world.cmd(transfers.link_by_hand, body)

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
