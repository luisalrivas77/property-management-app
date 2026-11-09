"""services.tax_inputs under hausbuch_app and RLS (phase-3.md D58, §6, §10): append-only rows,
supersede and withdraw, the scope checks, the event in the same transaction, and workspace
isolation."""

import itertools
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from db.matching_support import World
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import tax_input as tax_input_table
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.events import EventType
from hausbuch.schemas.property import Address, PropertyCreate, UnitCreate
from hausbuch.schemas.tax import TAX_INPUT_SCOPE_INVALID, TaxInputCreate, TaxInputKind
from hausbuch.services.allocations import record_set
from hausbuch.services.errors import NotFoundError, RuleViolationError
from hausbuch.services.properties import create_property
from hausbuch.services.tax_inputs import (
    TAX_INPUT_ALLOCATION_NOT_CONFIRMED,
    TAX_INPUT_NOT_CURRENT,
    create_tax_input,
    find_current,
    list_tax_inputs,
)
from hausbuch.services.units import create_unit

K = TaxInputKind


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0])


def _allocation(world: World, category: str, amount: str = "-1190.00", *, confirm=True):
    tx = world.line(date(2026, 9, 21), amount, name="Maler Muster")
    rows = world.cmd(
        record_set,
        AllocationSet(
            transaction_id=tx,
            allocations=[
                AllocationDraft(
                    amount=Decimal(amount),
                    category=AllocationCategory(category),
                    property_id=world.property_id,
                )
            ],
            method=AllocationMethod.USER,
            confidence=Decimal("1"),
        ),
        confirm=confirm,
        auto=False,
    )
    return rows[0].id


def _input(world: World, kind: K, value: dict, **scope) -> TaxInputCreate:
    return TaxInputCreate.model_validate(
        {"property_id": world.property_id, "kind": kind, "value": value} | scope
    )


_TICK = itertools.count(1)


def _create(world: World, data: TaxInputCreate):
    """One write, a second after the previous one, so "oldest first" is well defined."""
    now = world.now + timedelta(seconds=next(_TICK))
    return world.cmd(create_tax_input, world.property_id, data, now=now)


def _codes(exc_info) -> tuple[str, ...]:
    return exc_info.value.codes


def _events(world: World) -> list:
    return world.query(
        lambda conn: conn.execute(
            select(event_table)
            .where(event_table.c.type == EventType.PROPERTY_TAX_INPUT_RECORDED.value)
            .order_by(event_table.c.seq)
        ).all()
    )


# --- create, event, list -------------------------------------------------------------


def test_a_spread_is_recorded_with_its_event(world):
    allocation = _allocation(world, "repairs")
    row = _create(world, _input(world, K.SPREAD_82B, {"years": 3}, allocation_id=allocation))
    assert (row.kind, row.allocation_id, row.value.years) == (K.SPREAD_82B, allocation, 3)
    assert row.supersedes_id is None and row.withdrawn is False
    assert row.recorded_at > world.now
    (event,) = _events(world)
    assert (event.entity_id, event.property_id, event.effective_date) == (
        world.property_id,
        world.property_id,
        None,
    )
    assert event.payload == {
        "tax_input_id": str(row.id),
        "kind": "spread_82b",
        "tax_year": None,
        "unit_id": None,
        "loan_id": None,
        "allocation_id": str(allocation),
        "document_id": None,
        "value": {"years": 3},
        "supersedes_id": None,
        "withdrawn": False,
    }
    assert world.query(list_tax_inputs, world.property_id) == [row]


def test_the_note_stays_out_of_the_event(world):
    allocation = _allocation(world, "renovation")
    data = _input(
        world,
        K.RENOVATION_TREATMENT,
        {"treatment": "erhaltung"},
        allocation_id=allocation,
        note="Bad saniert, laut Rechnung Erhaltung",
    )
    row = _create(world, data)
    assert row.note == "Bad saniert, laut Rechnung Erhaltung"
    (event,) = _events(world)
    assert "note" not in event.payload
    assert "Bad" not in str(event.payload)


def test_the_row_and_its_event_commit_together(world, app_engine):
    """A failure after the insert rolls the row back with the event (one transaction)."""
    allocation = _allocation(world, "repairs")
    data = _input(world, K.SPREAD_82B, {"years": 2}, allocation_id=allocation)
    from db.matching_support import USER

    from hausbuch.services.context import new_command

    with pytest.raises(RuntimeError), workspace_transaction(app_engine, world.ws) as conn:
        create_tax_input(new_command(conn, world.ws, USER, now=world.now), world.property_id, data)
        raise RuntimeError("after the write")
    assert world.query(list_tax_inputs, world.property_id, include_history=True) == []
    assert _events(world) == []


def test_rows_are_append_only_in_the_database(world, app_engine):
    allocation = _allocation(world, "repairs")
    row = _create(world, _input(world, K.SPREAD_82B, {"years": 3}, allocation_id=allocation))
    for statement in (
        "UPDATE tax_input SET withdrawn = true WHERE id = :id",
        "DELETE FROM tax_input WHERE id = :id",
    ):
        with pytest.raises(DBAPIError), workspace_transaction(app_engine, world.ws) as conn:
            conn.execute(text(statement), {"id": row.id})


# --- supersede and withdraw ------------------------------------------------------------


def test_a_correction_supersedes_the_current_row(world):
    allocation = _allocation(world, "repairs")
    first = _create(world, _input(world, K.SPREAD_82B, {"years": 3}, allocation_id=allocation))
    second = _create(
        world,
        _input(world, K.SPREAD_82B, {"years": 5}, allocation_id=allocation, supersedes_id=first.id),
    )
    assert world.query(list_tax_inputs, world.property_id) == [second]
    history = world.query(list_tax_inputs, world.property_id, include_history=True)
    assert [r.id for r in history] == [first.id, second.id]
    assert [e.payload["supersedes_id"] for e in _events(world)] == [None, str(first.id)]


def test_a_superseded_row_cannot_be_superseded_again(world):
    allocation = _allocation(world, "repairs")
    first = _create(world, _input(world, K.SPREAD_82B, {"years": 3}, allocation_id=allocation))
    _create(
        world,
        _input(world, K.SPREAD_82B, {"years": 4}, allocation_id=allocation, supersedes_id=first.id),
    )
    with pytest.raises(RuleViolationError) as exc:
        _create(
            world,
            _input(
                world,
                K.SPREAD_82B,
                {"years": 5},
                allocation_id=allocation,
                supersedes_id=first.id,
            ),
        )
    assert _codes(exc) == (TAX_INPUT_NOT_CURRENT,)


def test_a_second_answer_without_supersedes_is_refused(world):
    allocation = _allocation(world, "repairs")
    _create(world, _input(world, K.SPREAD_82B, {"years": 3}, allocation_id=allocation))
    with pytest.raises(RuleViolationError) as exc:
        _create(world, _input(world, K.SPREAD_82B, {"years": 4}, allocation_id=allocation))
    assert _codes(exc) == (TAX_INPUT_NOT_CURRENT,)


def test_a_withdrawal_ends_the_decision_and_repeats_its_value(world):
    allocation = _allocation(world, "renovation")
    first = _create(
        world,
        _input(world, K.EXCLUDE_FROM_15PCT, {"reason": "extension"}, allocation_id=allocation),
    )
    with pytest.raises(RuleViolationError) as exc:
        _create(
            world,
            _input(
                world,
                K.EXCLUDE_FROM_15PCT,
                {"reason": "annual_maintenance"},
                allocation_id=allocation,
                supersedes_id=first.id,
                withdrawn=True,
            ),
        )
    assert _codes(exc) == (TAX_INPUT_SCOPE_INVALID,)
    _create(
        world,
        _input(
            world,
            K.EXCLUDE_FROM_15PCT,
            {"reason": "extension"},
            allocation_id=allocation,
            supersedes_id=first.id,
            withdrawn=True,
        ),
    )
    assert world.query(list_tax_inputs, world.property_id) == []
    # A withdrawn row is not current: the next answer is a fresh row.
    with pytest.raises(RuleViolationError) as exc:
        _create(
            world,
            _input(
                world,
                K.EXCLUDE_FROM_15PCT,
                {"reason": "extension"},
                allocation_id=allocation,
                supersedes_id=first.id,
            ),
        )
    assert _codes(exc) == (TAX_INPUT_NOT_CURRENT,)
    again = _create(
        world,
        _input(
            world,
            K.EXCLUDE_FROM_15PCT,
            {"reason": "damage_after_acquisition"},
            allocation_id=allocation,
        ),
    )
    assert world.query(list_tax_inputs, world.property_id) == [again]


def test_a_correction_keeps_the_scope(world):
    one, two = _allocation(world, "repairs"), _allocation(world, "repairs")
    first = _create(world, _input(world, K.SPREAD_82B, {"years": 3}, allocation_id=one))
    with pytest.raises(RuleViolationError) as exc:
        _create(
            world,
            _input(world, K.SPREAD_82B, {"years": 3}, allocation_id=two, supersedes_id=first.id),
        )
    assert _codes(exc) == (TAX_INPUT_SCOPE_INVALID,)


def test_an_unknown_superseded_row_is_not_found(world):
    allocation = _allocation(world, "repairs")
    with pytest.raises(NotFoundError):
        _create(
            world,
            _input(
                world,
                K.SPREAD_82B,
                {"years": 3},
                allocation_id=allocation,
                supersedes_id=uuid.uuid4(),
            ),
        )


# --- scope checks ----------------------------------------------------------------------


def test_the_allocation_must_be_confirmed(world):
    proposed = _allocation(world, "repairs", confirm=False)
    with pytest.raises(RuleViolationError) as exc:
        _create(world, _input(world, K.SPREAD_82B, {"years": 3}, allocation_id=proposed))
    assert _codes(exc) == (TAX_INPUT_ALLOCATION_NOT_CONFIRMED,)


def test_the_kind_must_fit_the_category(world):
    repair = _allocation(world, "repairs")
    insurance = _allocation(world, "insurance", "-310.00")
    with pytest.raises(RuleViolationError) as exc:
        _create(
            world,
            _input(world, K.RENOVATION_TREATMENT, {"treatment": "erhaltung"}, allocation_id=repair),
        )
    assert _codes(exc) == (TAX_INPUT_SCOPE_INVALID,)
    with pytest.raises(RuleViolationError) as exc:
        _create(world, _input(world, K.SPREAD_82B, {"years": 2}, allocation_id=insurance))
    assert _codes(exc) == (TAX_INPUT_SCOPE_INVALID,)


@pytest.mark.parametrize(
    "category",
    [
        "repairs",
        "insurance",
        "grundsteuer",
        "professional_fees",
        "property_management",
        "hausgeld_ruecklage",
        "other_expense",
    ],
)
def test_renovation_treatment_only_on_a_renovation(world, category):
    """The rules read renovation_treatment only on a renovation; a stray row elsewhere could
    keep a cost deductible that the 15 % limit should capitalise, so it is refused and
    nothing is written."""
    other = _allocation(world, category)
    with pytest.raises(RuleViolationError) as exc:
        _create(
            world,
            _input(world, K.RENOVATION_TREATMENT, {"treatment": "erhaltung"}, allocation_id=other),
        )
    assert _codes(exc) == (TAX_INPUT_SCOPE_INVALID,)
    assert world.query(list_tax_inputs, world.property_id, include_history=True) == []
    assert _events(world) == []
    renovation = _allocation(world, "renovation")
    row = _create(
        world,
        _input(world, K.RENOVATION_TREATMENT, {"treatment": "erhaltung"}, allocation_id=renovation),
    )
    assert world.query(list_tax_inputs, world.property_id) == [row]


@pytest.mark.parametrize(
    ("kind", "category", "first", "second"),
    [
        (K.SPREAD_82B, "repairs", {"years": 3}, {"years": 4}),
        (
            K.RENOVATION_TREATMENT,
            "renovation",
            {"treatment": "erhaltung"},
            {"treatment": "herstellung"},
        ),
        (
            K.EXCLUDE_FROM_15PCT,
            "renovation",
            {"reason": "extension"},
            {"reason": "damage_after_acquisition"},
        ),
    ],
)
def test_one_scope_never_has_two_current_rows(world, kind, category, first, second):
    """A new answer for a scope that has a current row must supersede that row: without
    supersedes_id, or superseding a row that is no longer current, it is refused with
    not_current. Through every step exactly one row is current."""
    allocation = _allocation(world, category)

    def current():
        return world.query(find_current, world.property_id, kind, allocation_id=allocation)

    original = _create(world, _input(world, kind, first, allocation_id=allocation))
    with pytest.raises(RuleViolationError) as exc:
        _create(world, _input(world, kind, second, allocation_id=allocation))
    assert _codes(exc) == (TAX_INPUT_NOT_CURRENT,)
    # The same value again is a second row of the same question too.
    with pytest.raises(RuleViolationError) as exc:
        _create(world, _input(world, kind, first, allocation_id=allocation))
    assert _codes(exc) == (TAX_INPUT_NOT_CURRENT,)
    assert current() == [original]

    corrected = _create(
        world, _input(world, kind, second, allocation_id=allocation, supersedes_id=original.id)
    )
    assert current() == [corrected]
    # Superseding the replaced row again would fork the history into two current rows.
    with pytest.raises(RuleViolationError) as exc:
        _create(
            world, _input(world, kind, first, allocation_id=allocation, supersedes_id=original.id)
        )
    assert _codes(exc) == (TAX_INPUT_NOT_CURRENT,)
    assert current() == [corrected]
    # Another allocation is another scope: its answer stands beside this one.
    elsewhere = _allocation(world, category)
    _create(world, _input(world, kind, first, allocation_id=elsewhere))
    assert current() == [corrected]
    assert len(world.query(list_tax_inputs, world.property_id)) == 2


def test_the_path_property_must_be_the_body_property(world):
    other = world.cmd(
        create_property,
        PropertyCreate(
            label="Zweite",
            kind="etw",
            address=Address(street="Weg", house_number="1", postal_code="10115", city="Berlin"),
            build_year=2000,
            notarized_on=date(2025, 1, 1),
            purchase_date=date(2025, 2, 1),
            purchase_price=Decimal("100000.00"),
        ),
    )
    data = _input(world, K.PRE_HANDOVER_INCOME, {"treatment": "rent"})
    with pytest.raises(RuleViolationError) as exc:
        world.cmd(create_tax_input, other.id, data)
    assert _codes(exc) == (TAX_INPUT_SCOPE_INVALID,)
    # The allocation of one property cannot carry another property's input.
    allocation = _allocation(world, "repairs")
    foreign = TaxInputCreate(
        property_id=other.id,
        kind=K.SPREAD_82B,
        allocation_id=allocation,
        value={"years": 2},
    )
    with pytest.raises(RuleViolationError) as exc:
        world.cmd(create_tax_input, other.id, foreign)
    assert _codes(exc) == (TAX_INPUT_SCOPE_INVALID,)


def test_market_rent_is_the_flats_for_an_etw_and_per_unit_for_an_mfh(world):
    value = {"valid_from": "2025-08-01", "cold_rent_per_m2": "12.80", "source": "mietspiegel"}
    with pytest.raises(RuleViolationError) as exc:
        _create(world, _input(world, K.MARKET_RENT, value, unit_id=world.unit_id))
    assert _codes(exc) == (TAX_INPUT_SCOPE_INVALID,)
    flat = _create(world, _input(world, K.MARKET_RENT, value))
    # A second value from another date stands beside it; the same date needs a correction.
    later = _create(world, _input(world, K.MARKET_RENT, value | {"valid_from": "2027-08-01"}))
    with pytest.raises(RuleViolationError) as exc:
        _create(world, _input(world, K.MARKET_RENT, value | {"cold_rent_per_m2": "13.00"}))
    assert _codes(exc) == (TAX_INPUT_NOT_CURRENT,)
    assert world.query(find_current, world.property_id, K.MARKET_RENT) == [flat, later]

    mfh = world.cmd(
        create_property,
        PropertyCreate(
            label="Haus",
            kind="mfh",
            address=Address(street="Allee", house_number="2", postal_code="10115", city="Berlin"),
            build_year=1960,
            notarized_on=date(2025, 1, 1),
            purchase_date=date(2025, 2, 1),
            purchase_price=Decimal("900000.00"),
        ),
    )
    unit = world.cmd(
        create_unit,
        UnitCreate(property_id=mfh.id, label="EG links", floor_area_m2=Decimal("62.50")),
    )
    data = TaxInputCreate(property_id=mfh.id, kind=K.MARKET_RENT, value=value)
    with pytest.raises(RuleViolationError) as exc:
        world.cmd(create_tax_input, mfh.id, data)
    assert _codes(exc) == (TAX_INPUT_SCOPE_INVALID,)
    per_unit = world.cmd(create_tax_input, mfh.id, data.model_copy(update={"unit_id": unit.id}))
    assert per_unit.unit_id == unit.id
    # Another property's unit is refused.
    wrong = _input(world, K.MARKET_RENT, value | {"valid_from": "2026-01-01"}, unit_id=unit.id)
    with pytest.raises(RuleViolationError) as exc:
        _create(world, wrong)
    assert _codes(exc) == (TAX_INPUT_SCOPE_INVALID,)


def test_certificate_figures_per_year_and_loan(world):
    loan = world.loan()
    doc = world.document(
        "zinsbescheinigung", {"interest_paid": "8123.45"}, type="zinsbescheinigung"
    )
    first = _create(
        world,
        _input(
            world,
            K.INTEREST_CERTIFIED,
            {"amount": "8123.45"},
            loan_id=loan,
            tax_year=2026,
            document_id=doc,
        ),
    )
    # Another year is another question.
    _create(
        world,
        _input(
            world,
            K.INTEREST_CERTIFIED,
            {"amount": "7900.00"},
            loan_id=loan,
            tax_year=2025,
            document_id=doc,
        ),
    )
    with pytest.raises(RuleViolationError) as exc:
        _create(
            world,
            _input(
                world,
                K.INTEREST_CERTIFIED,
                {"amount": "8123.40"},
                loan_id=loan,
                tax_year=2026,
                document_id=doc,
            ),
        )
    assert _codes(exc) == (TAX_INPUT_NOT_CURRENT,)
    assert world.query(list_tax_inputs, world.property_id, tax_year=2026) == [first]
    with pytest.raises(NotFoundError):
        _create(
            world,
            _input(
                world,
                K.RUECKLAGE_SPENT,
                {"amount": "350.00"},
                tax_year=2026,
                document_id=uuid.uuid4(),
            ),
        )


def test_list_filters_by_year_and_kind(world):
    allocation = _allocation(world, "repairs")
    spread = _create(world, _input(world, K.SPREAD_82B, {"years": 3}, allocation_id=allocation))
    doc = world.document("hausgeldabrechnung", {}, type="hausgeldabrechnung")
    spent = _create(
        world,
        _input(world, K.RUECKLAGE_SPENT, {"amount": "350.00"}, tax_year=2026, document_id=doc),
    )
    assert world.query(list_tax_inputs, world.property_id, tax_year=2026) == [spread, spent]
    assert world.query(list_tax_inputs, world.property_id, tax_year=2025) == [spread]
    assert world.query(list_tax_inputs, world.property_id, kind=K.RUECKLAGE_SPENT) == [spent]
    with pytest.raises(NotFoundError):
        world.query(list_tax_inputs, uuid.uuid4())


# --- workspace isolation -------------------------------------------------------------


def test_another_workspace_sees_nothing_and_cannot_reference_rows(
    world, app_engine, fresh_workspaces
):
    allocation = _allocation(world, "repairs")
    row = _create(world, _input(world, K.SPREAD_82B, {"years": 3}, allocation_id=allocation))
    other = World(app_engine, fresh_workspaces[1])
    with pytest.raises(NotFoundError):
        other.query(list_tax_inputs, world.property_id)
    with workspace_transaction(app_engine, other.ws) as conn:
        assert conn.execute(select(tax_input_table)).all() == []
    # Their property, our allocation and our row: 404, nothing written.
    with pytest.raises(NotFoundError):
        other.cmd(
            create_tax_input,
            other.property_id,
            TaxInputCreate(
                property_id=other.property_id,
                kind=K.SPREAD_82B,
                allocation_id=allocation,
                value={"years": 2},
            ),
        )
    mine = _allocation(other, "repairs")
    with pytest.raises(NotFoundError):
        other.cmd(
            create_tax_input,
            other.property_id,
            TaxInputCreate(
                property_id=other.property_id,
                kind=K.SPREAD_82B,
                allocation_id=mine,
                value={"years": 2},
                supersedes_id=row.id,
            ),
        )
    assert other.query(list_tax_inputs, other.property_id, include_history=True) == []
