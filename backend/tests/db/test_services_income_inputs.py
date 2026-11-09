"""services.income_inputs under hausbuch_app and RLS (phase-4.md D83, §6, §8): append-only
rows, supersede and withdraw, the scope checks, the owner or workspace event in the same
transaction, income_facts, the tax rate of a year, and workspace isolation.

Synthetic people and amounts only."""

import itertools
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from db.matching_support import USER, World
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import event as event_table
from hausbuch.rules.errors import (
    INCOME_ASSESSED_RESULT_NEEDED,
    INCOME_INPUT_NOT_CURRENT,
    INCOME_INPUT_SCOPE_INVALID,
    RuleError,
)
from hausbuch.schemas.enums import IncomeInputKind, StateCode
from hausbuch.schemas.events import EventType
from hausbuch.schemas.performance import IncomeInputCreate
from hausbuch.schemas.property import OwnerCreate
from hausbuch.services.context import new_command
from hausbuch.services.errors import NotFoundError, RuleViolationError
from hausbuch.services.income_inputs import (
    create_income_input,
    income_facts,
    list_income_inputs,
    tax_rate,
)
from hausbuch.services.owners import create_owner

pytestmark = pytest.mark.db

K = IncomeInputKind
_TICK = itertools.count(1)
TODAY = date(2026, 11, 9)


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0])


def _owner(world: World, name: str, *, church: bool = False):
    data = OwnerCreate(
        full_name=name, church_tax=church, tax_state=StateCode.BY if church else None
    )
    return world.cmd(create_owner, data)


def _create(world: World, **body):
    """One write, a second after the previous one, so "oldest first" is well defined."""
    now = world.now + timedelta(seconds=next(_TICK))
    return world.cmd(create_income_input, IncomeInputCreate.model_validate(body), now=now)


def _gross(owner_id, amount: str, year: int = 2026, **extra) -> dict:
    return {
        "owner_id": owner_id,
        "kind": "expected_gross_pay",
        "tax_year": year,
        "value": {"amount": amount},
    } | extra


def _document(world: World):
    with workspace_transaction(world.engine, world.ws) as conn:
        return world._document_row(conn, type="bescheid", subtype=None, title=None)


def _events(world: World, type_: EventType) -> list:
    return world.query(
        lambda conn: conn.execute(
            select(event_table).where(event_table.c.type == type_.value).order_by(event_table.c.seq)
        ).all()
    )


def test_gross_pay_is_recorded_with_its_owner_event(world):
    owner = _owner(world, "Person A")
    row = _create(world, **_gross(owner.id, "60000.00", note="laut Arbeitsvertrag"))
    assert (row.kind, row.owner_id, row.tax_year) == (K.EXPECTED_GROSS_PAY, owner.id, 2026)
    assert row.value.amount == Decimal("60000.00")
    (event,) = _events(world, EventType.OWNER_INCOME_INPUT_RECORDED)
    assert (event.entity_type, event.entity_id, event.effective_date) == ("owner", owner.id, None)
    assert event.payload == {
        "income_input_id": str(row.id),
        "kind": "expected_gross_pay",
        "tax_year": 2026,
        "owner_id": str(owner.id),
        "document_id": None,
        "value": {"amount": "60000.00"},
        "supersedes_id": None,
        "withdrawn": False,
    }
    assert "Arbeitsvertrag" not in str(event.payload)  # the note stays out of the event
    assert world.query(list_income_inputs) == [row]


def test_household_kinds_raise_a_workspace_event(world):
    doc = _document(world)
    bescheid = _create(
        world, kind="bescheid_zve", tax_year=2026, document_id=doc, value={"amount": "80000.00"}
    )
    rate = _create(world, kind="marginal_rate_override", tax_year=2027, value={"rate": "0.4200"})
    events = _events(world, EventType.WORKSPACE_INCOME_INPUT_RECORDED)
    assert [(e.entity_type, e.entity_id) for e in events] == [("workspace", world.ws)] * 2
    assert [e.payload["income_input_id"] for e in events] == [str(bescheid.id), str(rate.id)]
    assert _events(world, EventType.OWNER_INCOME_INPUT_RECORDED) == []


def test_a_correction_supersedes_and_history_keeps_both(world):
    owner = _owner(world, "Person A")
    first = _create(world, **_gross(owner.id, "60000.00"))
    second = _create(world, **_gross(owner.id, "62000.00", supersedes_id=first.id))
    assert world.query(list_income_inputs) == [second]
    history = world.query(list_income_inputs, include_history=True)
    assert [r.id for r in history] == [first.id, second.id]
    facts = world.query(income_facts)
    assert [f.id for f in facts] == [second.id]
    assert facts[0].value.amount == Decimal("62000.00")


def test_a_withdrawal_ends_a_row(world):
    owner = _owner(world, "Person A")
    first = _create(world, **_gross(owner.id, "60000.00"))
    with pytest.raises(RuleViolationError) as exc:  # a withdrawal repeats the value
        _create(world, **_gross(owner.id, "1.00", supersedes_id=first.id, withdrawn=True))
    assert exc.value.codes == (INCOME_INPUT_SCOPE_INVALID,)
    _create(world, **_gross(owner.id, "60000.00", supersedes_id=first.id, withdrawn=True))
    assert world.query(list_income_inputs) == []
    assert world.query(income_facts) == []


def test_one_current_answer_per_kind_year_and_owner(world):
    a, b = _owner(world, "Person A"), _owner(world, "Person B")
    first = _create(world, **_gross(a.id, "60000.00"))
    with pytest.raises(RuleViolationError) as exc:
        _create(world, **_gross(a.id, "61000.00"))
    assert exc.value.codes == (INCOME_INPUT_NOT_CURRENT,)
    # Another owner, another year or another kind is another question.
    _create(world, **_gross(b.id, "45000.00"))
    _create(world, **_gross(a.id, "61000.00", year=2027))
    _create(
        world,
        owner_id=a.id,
        kind="deductions_override",
        tax_year=2026,
        value={"amount": "14000.00"},
    )
    # A superseded row cannot be superseded again.
    second = _create(world, **_gross(a.id, "62000.00", supersedes_id=first.id))
    with pytest.raises(RuleViolationError) as exc:
        _create(world, **_gross(a.id, "63000.00", supersedes_id=first.id))
    assert exc.value.codes == (INCOME_INPUT_NOT_CURRENT,)
    assert second.supersedes_id == first.id


def test_a_correction_keeps_kind_year_and_owner(world):
    a, b = _owner(world, "Person A"), _owner(world, "Person B")
    first = _create(world, **_gross(a.id, "60000.00"))
    with pytest.raises(RuleViolationError) as exc:
        _create(world, **_gross(b.id, "60000.00", supersedes_id=first.id))
    assert exc.value.codes == (INCOME_INPUT_SCOPE_INVALID,)
    with pytest.raises((RuleViolationError, DBAPIError)):  # the composite FK says so too
        _create(world, **_gross(a.id, "60000.00", year=2027, supersedes_id=first.id))


def test_unknown_owner_document_or_row_is_not_found(world):
    with pytest.raises(NotFoundError):
        _create(world, **_gross(uuid4(), "60000.00"))
    with pytest.raises(NotFoundError):
        _create(
            world,
            kind="bescheid_zve",
            tax_year=2026,
            document_id=uuid4(),
            value={"amount": "80000.00"},
        )
    owner = _owner(world, "Person A")
    with pytest.raises(NotFoundError):
        _create(world, **_gross(owner.id, "60000.00", supersedes_id=uuid4()))


def test_the_row_and_its_event_commit_together(world, app_engine):
    owner = _owner(world, "Person A")
    data = IncomeInputCreate.model_validate(_gross(owner.id, "60000.00"))
    with pytest.raises(RuntimeError), workspace_transaction(app_engine, world.ws) as conn:
        create_income_input(new_command(conn, world.ws, USER, now=world.now), data)
        raise RuntimeError("after the write")
    assert world.query(list_income_inputs, include_history=True) == []
    assert _events(world, EventType.OWNER_INCOME_INPUT_RECORDED) == []


def test_rows_are_append_only(world, app_engine):
    owner = _owner(world, "Person A")
    row = _create(world, **_gross(owner.id, "60000.00"))
    with pytest.raises(DBAPIError), workspace_transaction(app_engine, world.ws) as conn:
        conn.execute(text("UPDATE income_input SET tax_year = 2027 WHERE id = :id"), {"id": row.id})
    with pytest.raises(DBAPIError), workspace_transaction(app_engine, world.ws) as conn:
        conn.execute(text("DELETE FROM income_input WHERE id = :id"), {"id": row.id})


def test_another_workspace_sees_nothing(world, app_engine, fresh_workspaces):
    owner = _owner(world, "Person A")
    _create(world, **_gross(owner.id, "60000.00"))
    other = fresh_workspaces[1]
    with workspace_transaction(app_engine, other) as conn:
        assert list_income_inputs(conn, include_history=True) == []
        assert income_facts(conn) == []
        # An owner of the first workspace is unknown here.
        with pytest.raises(NotFoundError):
            create_income_input(
                new_command(conn, other, USER, now=world.now),
                IncomeInputCreate.model_validate(_gross(owner.id, "1.00")),
            )


def test_the_tax_rate_of_a_year(world):
    """§10.3's household: 60,000 and 45,000 gross, both in church in Bayern, the property's
    result −7,948.66 → −2,687.04 (marginal_rate.yaml household_81993)."""
    a = _owner(world, "Person A", church=True)
    b = _owner(world, "Person B", church=True)
    missing = world.query(tax_rate, tax_year=2026, result=Decimal("-7948.66"), today=TODAY)
    assert (missing.missing, missing.zve, missing.effect, missing.owner_estimates) == (
        True,
        None,
        None,
        [],
    )
    _create(world, **_gross(a.id, "60000.00"))
    _create(world, **_gross(b.id, "45000.00"))
    got = world.query(tax_rate, tax_year=2026, result=Decimal("-7948.66"), today=TODAY)
    assert got.missing is False
    assert got.zve.amount == Decimal("81993.00") and got.zve.source == "estimate"
    assert got.effect.effect == Decimal("-2687.04")
    assert got.effect.effective_rate == Decimal("33.8")
    assert {o.deductions for o in got.owner_estimates} == {Decimal("12966.00"), Decimal("10041.00")}
    assert (got.tariff.tax_year, got.tariff.reused_from) == (2026, None)
    # 2028 carries the 2026 rows forward on the reused 2026 tariff.
    later = world.query(tax_rate, tax_year=2028, result=Decimal("-7948.66"), today=TODAY)
    assert (later.effect.effect, later.effect.carried_forward) == (Decimal("-2687.04"), True)
    assert later.tariff.reused_from == 2026


def test_a_carried_bescheid_needs_the_property(world):
    doc = _document(world)
    _create(world, kind="bescheid_zve", tax_year=2026, document_id=doc, value={"amount": "8.00"})
    same_year = world.query(tax_rate, tax_year=2026, result=Decimal("-2.00"), today=TODAY)
    assert (same_year.zve.source, same_year.zve.amount) == ("bescheid", Decimal("10.00"))
    with pytest.raises(RuleError) as exc:
        world.query(tax_rate, tax_year=2027, result=Decimal("-2.00"), today=TODAY)
    assert exc.value.code == INCOME_ASSESSED_RESULT_NEEDED


def test_a_bescheid_from_before_the_purchase_holds_no_result_of_the_property(world):
    """A Bescheid of a year before the purchase (the World's property: 1 May 2024) carried
    into a later year: nothing of the property is in it, so the base is its zvE unreduced
    (review of 9 Nov 2026: it used to fail with tax.year_not_available)."""
    doc = _document(world)
    _create(
        world, kind="bescheid_zve", tax_year=2023, document_id=doc, value={"amount": "80000.00"}
    )
    got = world.query(
        tax_rate,
        tax_year=2026,
        result=Decimal("-7948.66"),
        property_id=world.property_id,
        today=TODAY,
    )
    assert (got.zve.source, got.zve.carried_forward) == ("bescheid", True)
    assert got.zve.amount == Decimal("80000.00")
    assert got.effect is not None and got.effect.effect < 0


def test_an_override_wins(world):
    a = _owner(world, "Person A")
    _create(world, **_gross(a.id, "60000.00"))
    _create(world, kind="marginal_rate_override", tax_year=2026, value={"rate": "0.4200"})
    got = world.query(tax_rate, tax_year=2026, result=Decimal("-7948.66"), today=TODAY)
    assert (got.effect.source, got.effect.effect) == ("override", Decimal("-3338.44"))
    assert got.zve.source == "override" and got.owner_estimates[0].owner_id == a.id
