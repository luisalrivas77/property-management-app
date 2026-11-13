"""„Alle bestätigen“ in Zu prüfen (docs/phases/phase-6.md D159 amended 13 Nov 2026):
the inbox's bulk_confirmable flag and services.review.confirm_category.

The person confirms the listed private lines' proposals of one plain household category,
each as a single confirmation is (one transaction.allocated event per line, actor user); a
listed line that changed meanwhile is skipped and named; a tax-relevant or person-only
category, a rental line and a line of another workspace are refused. Invented names and
amounts only."""

from datetime import date
from decimal import Decimal

import pytest
from db.matching_support import World
from db.test_services_private_matching import NOW, Private, _rows
from sqlalchemy import func, select

from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import matching_rule as rule_table
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.api import ConfirmCategoryBody, InboxItemKind
from hausbuch.services import allocations, review
from hausbuch.services.errors import NotFoundError, RuleViolationError

pytestmark = pytest.mark.db

C = AllocationCategory


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


@pytest.fixture
def private(world) -> Private:
    return Private(world)


def _propose(world: World, tx, *parts: tuple[str, str], question: str | None = None):
    """A model's proposal (method llm, below every threshold): `parts` are (amount, code)."""
    found = AllocationSet(
        transaction_id=tx,
        method=AllocationMethod.LLM,
        confidence=Decimal("0.700"),
        reasoning="Wahrscheinlich ein Restaurant.",
        question=question,
        allocations=[
            AllocationDraft(amount=Decimal(amount), category=code) for amount, code in parts
        ],
    )
    world.cmd(allocations.record_set, found, confirm=False, auto=False)


def _dining(world: World, private: Private, day: int, amount: str = "-31.50"):
    tx = private.line(date(2026, 10, day), amount, name=f"Restaurant Beispiel {day}")
    _propose(world, tx, (amount, "hh_dining"))
    return tx


def _confirm(world: World, category: str, *ids):
    body = ConfirmCategoryBody(category=C(category), transaction_ids=list(ids))
    return world.cmd(review.confirm_category, body)


def _items(world: World) -> dict:
    return {
        i.transaction_id: i
        for i in world.query(review.inbox)
        if i.kind is InboxItemKind.ALLOCATION_PROPOSED
    }


def _events(world: World, tx):
    return world.query(
        lambda c: c.execute(
            select(event_table.c.type, event_table.c.actor_type, event_table.c.payload).where(
                event_table.c.entity_id == tx
            )
        ).all()
    )


def _status(world: World, tx) -> tuple:
    return tuple((r.category, r.status) for r in _rows(world, tx))


def test_the_inbox_marks_the_plain_one_part_proposals_of_a_private_line(world, private):
    plain = _dining(world, private, 1)
    asked = private.line(date(2026, 10, 2), "-12.00", name="Imbiss Beispiel")
    _propose(world, asked, ("-12.00", "hh_dining"), question="Privat oder geschäftlich?")
    split = private.line(date(2026, 10, 3), "-40.00", name="Markt Beispiel")
    _propose(world, split, ("-25.00", "hh_groceries"), ("-15.00", "hh_home"))
    donation = private.line(date(2026, 10, 4), "-50.00", name="Verein Beispiel")
    _propose(world, donation, ("-50.00", "hh_donation"))
    family = private.line(date(2026, 10, 5), "-200.00", name="A. Beispiel")
    _propose(world, family, ("-200.00", "hh_family_transfers"))
    rental = world.line(date(2026, 10, 6), "-9.00", name="Händler Beispiel")
    _propose(world, rental, ("-9.00", "other_expense"))

    items = _items(world)
    assert (items[plain].category, items[plain].bulk_confirmable) == (C.HH_DINING, True)
    assert (items[asked].category, items[asked].bulk_confirmable) == (C.HH_DINING, False)
    assert (items[split].category, items[split].bulk_confirmable) == (None, False)
    assert items[donation].bulk_confirmable is False  # tax-relevant: one by one
    assert items[family].bulk_confirmable is False  # money to family: one by one
    assert (items[rental].category, items[rental].bulk_confirmable) == (C.OTHER_EXPENSE, False)


def test_only_the_listed_lines_are_confirmed_each_with_its_own_event(world, private):
    first, second, unlisted = (_dining(world, private, d) for d in (7, 8, 9))
    result = _confirm(world, "hh_dining", first, second)
    assert (result.confirmed, result.skipped) == (2, [])
    for tx in (first, second):
        assert _status(world, tx) == (("hh_dining", "confirmed"),)
        ((kind, actor, payload),) = _events(world, tx)
        assert (kind, actor, payload["auto"]) == ("transaction.allocated", "user", False)
        assert payload["method"] == "llm"  # the proposer's provenance stays, as in a single one
    assert _status(world, unlisted) == (("hh_dining", "proposed"),)  # never by category alone
    assert (
        world.query(lambda c: c.execute(select(func.count()).select_from(rule_table)).scalar_one())
        == 0
    )  # no learned rule


def test_lines_that_changed_since_the_list_are_skipped_and_named(world, private):
    ok = _dining(world, private, 10)
    answered = _dining(world, private, 11)
    world.cmd(allocations.confirm_proposed, answered)
    moved = _dining(world, private, 12)
    _propose(world, moved, ("-31.50", "hh_groceries"))  # re-proposed in another category
    split = _dining(world, private, 13)
    _propose(world, split, ("-20.00", "hh_dining"), ("-11.50", "hh_gifts"))
    asked = _dining(world, private, 14)
    _propose(world, asked, ("-31.50", "hh_dining"), question="Mit Gästen?")

    result = _confirm(world, "hh_dining", ok, answered, moved, split, asked)
    assert result.confirmed == 1
    assert {(s.transaction_id, s.reason) for s in result.skipped} == {
        (answered, "not_waiting"),
        (moved, "category_changed"),
        (split, "split"),
        (asked, "question"),
    }
    assert _status(world, moved) == (("hh_groceries", "proposed"),)
    assert len(_events(world, answered)) == 1  # its own single confirmation only


@pytest.mark.parametrize(
    "category",
    [
        "hh_donation",
        "hh_household_services",
        "hh_childcare",
        "hh_property_cost",
        "hh_family_transfers",
        "hh_work_education",
        "hh_capital_income",
        "internal_transfer_out",
        "hh_investment",
        "uncategorized",
        "security_purchase",
        "rent_cold",
    ],
)
def test_a_category_confirmed_line_by_line_is_refused(world, private, category):
    tx = _dining(world, private, 15)
    with pytest.raises(RuleViolationError) as refused:
        _confirm(world, category, tx)
    assert refused.value.code == "inbox.category_not_bulk"
    assert _status(world, tx) == (("hh_dining", "proposed"),)


def test_a_rental_line_refuses_the_whole_request(world, private):
    tx = _dining(world, private, 16)
    rental = world.line(date(2026, 10, 16), "-31.50", name="Restaurant Beispiel")
    _propose(world, rental, ("-31.50", "other_expense"))
    with pytest.raises(RuleViolationError) as refused:
        _confirm(world, "hh_dining", tx, rental)
    assert refused.value.code == "inbox.line_not_private"
    assert _status(world, tx) == (("hh_dining", "proposed"),)  # one transaction: nothing kept
    assert _events(world, tx) == []


def test_a_line_of_another_workspace_is_not_found(world, private, app_engine, fresh_workspaces):
    tx = _dining(world, private, 17)
    other = World(app_engine, fresh_workspaces[1], now=NOW)
    with pytest.raises(NotFoundError):
        body = ConfirmCategoryBody(category=C.HH_DINING, transaction_ids=[tx])
        other.cmd(review.confirm_category, body)
    assert _status(world, tx) == (("hh_dining", "proposed"),)
