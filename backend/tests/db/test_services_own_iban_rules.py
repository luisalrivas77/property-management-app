"""Phase 6 D160 amended (5) under hausbuch_app and RLS: a transfer by an own IBAN is never a
learned rule's, and never an earlier answer for its payee's name.

One card issuer, two cards: the first owner's card is an account of the app, its payment IBAN
confirmed (CARD_IBAN), so the settlements from the first owner's current account are
internal transfers the matcher links; the second owner's card is not in the app, and her
account pays the same issuer on another IBAN (ISSUER_IBAN). Learning „Kreditkartenabrechnung“
on her settlement must neither be refused because of the other card's transfers nor reach
them. Invented IBANs only (Bundesbank test bank code 10000000)."""

from datetime import date

import pytest
from db.db_support import TEST_IBAN_KEY
from db.matching_support import World
from db.test_services_card_settlement import Card, _booked, _links, _match
from db.test_services_private_matching import (
    NOW,
    Private,
    _answer_line,
    _answer_rebook,
    _rematch,
    _rows,
    _user_set,
)
from sqlalchemy import insert, select, update

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import transfer_link as link_table
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationMethod,
    MatchingRuleCreate,
    RuleMatcher,
    SplitMethod,
)
from hausbuch.schemas.household import TransferLinkKind, TransferMatchedBy
from hausbuch.services import allocations, matching, review, rules_learned

pytestmark = pytest.mark.db

CARD_IBAN = "DE87100000000000003021"  # the issuer's IBAN for the first owner's card (own)
ISSUER_IBAN = "DE60100000000000003022"  # the issuer's IBAN for the second owner's card
SECOND_ACCOUNT_IBAN = "DE33100000000000003023"
ISSUER = "Beispiel Kartenbank AB"
OTHER_PAYEE = "Zahlstelle Muster"
# Mondays, each settlement's card payment booked the day after
SETTLED = (date(2026, 6, 1), date(2026, 7, 6), date(2026, 8, 3), date(2026, 9, 7))


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


class Second(Private):
    """The second owner's private current account, with a reconciled import of its own."""

    def __init__(self, world: World) -> None:
        self.world = world
        with workspace_transaction(world.engine, world.ws) as conn:
            self.account_id = world._account(conn, SECOND_ACCOUNT_IBAN, owner=True)
        self.import_id = self.add_import(world.now)


def _settled_card(world: World) -> tuple[Private, Card, list]:
    """The first owner's current account, his card (payment IBAN confirmed) and four monthly
    settlements to it, each booked internal_transfer_out and linked by the matcher."""
    giro = Private(world)
    card = Card(world, CARD_IBAN)
    debits = []
    for n, day in enumerate(SETTLED):
        amount = f"-{200 + n * 37}.15"
        debit = giro.line(day, amount, name=ISSUER, iban=CARD_IBAN, purpose="Kreditkarte")
        _match(world, debit)
        paid = card.payment(date.fromordinal(day.toordinal() + 1), amount.lstrip("-"))
        _match(world, paid)
        debits.append(debit)
    assert all(_booked(world, d) == [("internal_transfer_out", "confirmed")] for d in debits)
    assert len(_links(world)) == len(SETTLED)
    return giro, card, debits


def _second(world: World, *, shared: bool) -> Second:
    """The second owner's account. `shared`: another payee is paid on the issuer's IBAN as
    well, so the IBAN names no payee and her rule is the issuer's name (D160 amended (4))."""
    second = Second(world)
    if shared:
        other = second.line(date(2026, 8, 20), "-12.00", name=OTHER_PAYEE, iban=ISSUER_IBAN)
        _answer_line(world, other, "-12.00", "hh_other", learn=False)
    return second


def _tx(conn, transaction_id):
    line = matching.load_line(conn, transaction_id)
    assert line is not None
    return line.tx


def _category(world: World, tx) -> tuple[str, str, str]:
    (row,) = _rows(world, tx)
    return row.category, row.method, row.status


@pytest.mark.parametrize("shared", [True, False], ids=["name_rule", "iban_rule"])
def test_a_card_not_in_the_app_learns_its_rule_beside_the_issuers_own_card(
    world, app_engine, tmp_path, shared
):
    giro, card, debits = _settled_card(world)
    second = _second(world, shared=shared)
    first = second.line(date(2026, 9, 8), "-189.76", name=ISSUER, iban=ISSUER_IBAN)
    # Not refused with rule.conflicts by the other card's four transfers of the same name.
    result = _answer_line(world, first, "-189.76", "hh_credit_card", learn=True)
    assert result.rule is not None
    if shared:
        assert result.rule.matcher.counterparty_iban_hmac is None
        assert result.rule.matcher.counterparty_name_pattern is not None
    # The other card's settlements are untouched and still linked.
    assert all(_booked(world, d) == [("internal_transfer_out", "confirmed")] for d in debits)
    assert len(_links(world)) == len(SETTLED)

    # Her next settlement, another amount: booked by the rule.
    again = second.line(date(2026, 10, 7), "-512.77", name=ISSUER, iban=ISSUER_IBAN)
    _rematch(app_engine, tmp_path, world, again)
    assert _category(world, again) == ("hh_credit_card", "rule", "confirmed")

    # His next settlement names the same issuer: the transfer by his card's IBAN, never the
    # rule, and linked to the card's payment.
    his = giro.line(date(2026, 10, 5), "-77.10", name=ISSUER, iban=CARD_IBAN, purpose="Karte")
    _match(world, his)
    assert _category(world, his) == ("internal_transfer_out", "matcher_transfer", "confirmed")
    paid = card.payment(date(2026, 10, 6), "77.10")
    _match(world, paid)
    assert any(link.out_transaction_id == his for link in _links(world))


def test_the_panel_names_no_transfer_of_the_issuers_own_card(world):
    _giro, _card, debits = _settled_card(world)
    second = _second(world, shared=True)
    first = second.line(date(2026, 9, 8), "-189.76", name=ISSUER, iban=ISSUER_IBAN)
    view = world.query(
        lambda c: review.transaction_allocations(
            c, first, iban_key=TEST_IBAN_KEY, today=world.now.date()
        )
    )
    assert view is not None
    assert view.can_learn_rule is True
    # Neither in the name's history nor as a line „Diese auch auf … umbuchen“ cannot re-book.
    assert view.name_history == []
    assert view.rebook_blockers == []

    # Asked to re-book the payee's earlier answers too: nothing of his is re-booked.
    result = _answer_rebook(world, first, "-189.76", "hh_credit_card")
    assert result.rule is not None and result.rebooked == 0
    assert all(_booked(world, d) == [("internal_transfer_out", "confirmed")] for d in debits)


def _linked_without_iban(world: World, giro: Private, card: Card, by: TransferMatchedBy):
    """A settlement debit that names the issuer but no IBAN, booked internal_transfer_out
    with the card's payment booked internal_transfer_in: the matcher links the pair when the
    second leg is booked (D162). `by` USER: a person's link then replaces the matcher's."""
    debit = giro.line(date(2026, 9, 21), "-64.30", name=ISSUER, purpose="Kreditkarte")
    paid = card.payment(date(2026, 9, 22), "64.30")
    for tx, amount, code in (
        (debit, "-64.30", AllocationCategory.INTERNAL_TRANSFER_OUT),
        (paid, "64.30", AllocationCategory.INTERNAL_TRANSFER_IN),
    ):
        answer = _user_set(tx, amount, code).model_copy(
            update={"method": AllocationMethod.MATCHER_TRANSFER}
        )
        world.cmd(allocations.record_set, answer, confirm=True, auto=True)
    [link] = _links(world)
    assert (link.out_transaction_id, link.matched_by) == (debit, TransferMatchedBy.MATCHER.value)
    if by is TransferMatchedBy.USER:
        with workspace_transaction(world.engine, world.ws) as conn:
            mine = conn.execute(
                insert(link_table)
                .values(
                    workspace_id=world.ws,
                    kind=TransferLinkKind.BANK.value,
                    out_transaction_id=debit,
                    in_transaction_id=paid,
                    matched_by=by.value,
                    created_at=world.now,
                )
                .returning(link_table.c.id)
            ).scalar_one()
            conn.execute(
                update(link_table).where(link_table.c.id == link.id).values(superseded_by=mine)
            )
    return debit


@pytest.mark.parametrize(
    ("by", "counted"), [(TransferMatchedBy.MATCHER, False), (TransferMatchedBy.USER, True)]
)
def test_a_line_the_matcher_linked_is_no_earlier_answer_whatever_iban_it_names(world, by, counted):
    """A transfer leg the matcher linked counts as no answer of its payee's name even when it
    names no own IBAN; a link a person made still counts (its block is named, D160)."""
    giro = Private(world)
    card = Card(world, CARD_IBAN)
    debit = _linked_without_iban(world, giro, card, by)
    second = _second(world, shared=True)
    first = second.line(date(2026, 9, 28), "-189.76", name=ISSUER, iban=ISSUER_IBAN)
    lines = world.query(
        lambda c: {line.transaction_id for line in matching.name_lines(c, _tx(c, first))}
    )
    assert (debit in lines) is counted


def test_a_name_rule_learned_before_the_card_was_in_the_app_never_books_its_transfer(
    world, app_engine, tmp_path
):
    """A rule on the issuer's name exists; then the first owner's card becomes an account with
    its payment IBAN confirmed. His next settlement is the transfer, not the rule's."""
    giro = Private(world)
    rule = world.cmd(
        rules_learned.create_rule,
        MatchingRuleCreate(
            matcher=RuleMatcher(
                counterparty_name_pattern="^BEISPIEL KARTENBANK", amount_sign="debit"
            ),
            split_method=SplitMethod.SINGLE_CATEGORY,
            category=AllocationCategory.HH_CREDIT_CARD,
        ),
    )
    before = giro.line(date(2026, 8, 3), "-140.00", name=ISSUER, iban=CARD_IBAN, purpose="Karte")
    _match(world, before)
    assert _category(world, before) == ("hh_credit_card", "rule", "confirmed")

    Card(world, CARD_IBAN)
    # A name rule names no IBAN, so the card's IBAN switches nothing off (D176 (4))...
    assert rule.id in {r.id for r in world.query(rules_learned.enabled_rules)}
    after = giro.line(date(2026, 9, 7), "-140.00", name=ISSUER, iban=CARD_IBAN, purpose="Karte")
    _match(world, after)
    # ... and still never books the line that names the card's IBAN.
    assert _category(world, after) == ("internal_transfer_out", "matcher_transfer", "confirmed")
    # A waiting line re-checked later takes the same order.
    waiting = giro.line(date(2026, 10, 5), "-95.00", name=ISSUER, iban=CARD_IBAN, purpose="Karte")
    _rematch(app_engine, tmp_path, world, waiting)
    assert _category(world, waiting) == ("internal_transfer_out", "matcher_transfer", "confirmed")
    assert world.query(
        lambda c: (
            c.execute(
                select(allocation_table.c.rule_id).where(
                    allocation_table.c.transaction_id.in_([after, waiting])
                )
            )
            .scalars()
            .all()
        )
    ) == [None, None]
