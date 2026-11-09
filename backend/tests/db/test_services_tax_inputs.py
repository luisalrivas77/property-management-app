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
from hausbuch.rules.errors import (
    TAX_INPUT_EXTENSION_NEEDS_HERSTELLUNG,
    TAX_INPUT_MOVABLE_ASSET_EXCEEDS_BUILDING,
    TAX_INPUT_MOVABLE_ASSET_NO_SPLIT,
    TAX_INPUT_MOVABLE_ASSET_YEAR,
    TAX_INPUT_USEFUL_LIFE_NOT_SHORTER,
)
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.enums import AcquisitionCostKind, PriceSplitSource
from hausbuch.schemas.events import EventType
from hausbuch.schemas.property import (
    AcquisitionCostCreate,
    Address,
    PriceSplitCreate,
    PropertyCreate,
    UnitCreate,
)
from hausbuch.schemas.tax import TAX_INPUT_SCOPE_INVALID, TaxInputCreate, TaxInputKind
from hausbuch.services.allocations import record_set
from hausbuch.services.errors import NotFoundError, RuleViolationError
from hausbuch.services.properties import (
    add_acquisition_cost,
    create_property,
    record_price_split,
    select_price_split,
)
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
        _input(
            world, K.EXCLUDE_FROM_15PCT, {"reason": "annual_maintenance"}, allocation_id=allocation
        ),
    )
    with pytest.raises(RuleViolationError) as exc:
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
    assert _codes(exc) == (TAX_INPUT_SCOPE_INVALID,)
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
    assert world.query(list_tax_inputs, world.property_id) == []
    # A withdrawn row is not current: the next answer is a fresh row.
    with pytest.raises(RuleViolationError) as exc:
        _create(
            world,
            _input(
                world,
                K.EXCLUDE_FROM_15PCT,
                {"reason": "annual_maintenance"},
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
            {"reason": "annual_maintenance"},
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


# --- an Erweiterung needs a herstellung election (D78) -----------------------------------

EXTENSION = {"reason": "extension"}
HERSTELLUNG = {"treatment": "herstellung"}
ERHALTUNG = {"treatment": "erhaltung"}


def _refused_extension(world: World, data: TaxInputCreate) -> None:
    """The write is refused with extension_needs_herstellung and leaves no row or event."""
    rows_before = world.query(list_tax_inputs, world.property_id, include_history=True)
    events_before = len(_events(world))
    with pytest.raises(RuleViolationError) as exc:
        _create(world, data)
    assert _codes(exc) == (TAX_INPUT_EXTENSION_NEEDS_HERSTELLUNG,)
    assert world.query(list_tax_inputs, world.property_id, include_history=True) == rows_before
    assert len(_events(world)) == events_before


def test_an_extension_is_refused_on_a_repair(world):
    """A repair is always deducted at once; excluding it as an Erweiterung would take it out
    of the 15 % count while it stays deductible."""
    repair = _allocation(world, "repairs")
    _refused_extension(world, _input(world, K.EXCLUDE_FROM_15PCT, EXTENSION, allocation_id=repair))


def test_an_extension_is_refused_on_a_renovation_without_an_election(world):
    renovation = _allocation(world, "renovation")
    _refused_extension(
        world, _input(world, K.EXCLUDE_FROM_15PCT, EXTENSION, allocation_id=renovation)
    )


def test_an_extension_is_refused_on_a_renovation_elected_erhaltung(world):
    renovation = _allocation(world, "renovation")
    _create(world, _input(world, K.RENOVATION_TREATMENT, ERHALTUNG, allocation_id=renovation))
    _refused_extension(
        world, _input(world, K.EXCLUDE_FROM_15PCT, EXTENSION, allocation_id=renovation)
    )


def test_an_extension_is_refused_once_its_herstellung_election_was_withdrawn(world):
    renovation = _allocation(world, "renovation")
    herstellung = _create(
        world, _input(world, K.RENOVATION_TREATMENT, HERSTELLUNG, allocation_id=renovation)
    )
    _create(
        world,
        _input(
            world,
            K.RENOVATION_TREATMENT,
            HERSTELLUNG,
            allocation_id=renovation,
            supersedes_id=herstellung.id,
            withdrawn=True,
        ),
    )
    _refused_extension(
        world, _input(world, K.EXCLUDE_FROM_15PCT, EXTENSION, allocation_id=renovation)
    )


def test_an_extension_is_accepted_on_a_renovation_elected_herstellung(world):
    renovation = _allocation(world, "renovation")
    treatment = _create(
        world, _input(world, K.RENOVATION_TREATMENT, HERSTELLUNG, allocation_id=renovation)
    )
    exclusion = _create(
        world, _input(world, K.EXCLUDE_FROM_15PCT, EXTENSION, allocation_id=renovation)
    )
    assert world.query(list_tax_inputs, world.property_id) == [treatment, exclusion]
    # Another reason superseding the extension needs no election.
    corrected = _create(
        world,
        _input(
            world,
            K.EXCLUDE_FROM_15PCT,
            {"reason": "damage_after_acquisition"},
            allocation_id=renovation,
            supersedes_id=exclusion.id,
        ),
    )
    assert world.query(list_tax_inputs, world.property_id) == [treatment, corrected]


@pytest.mark.parametrize("reason", ["annual_maintenance", "damage_after_acquisition"])
@pytest.mark.parametrize("category", ["repairs", "renovation"])
def test_the_other_reasons_need_no_election(world, category, reason):
    allocation = _allocation(world, category)
    row = _create(
        world, _input(world, K.EXCLUDE_FROM_15PCT, {"reason": reason}, allocation_id=allocation)
    )
    assert world.query(list_tax_inputs, world.property_id) == [row]


def test_the_herstellung_election_stays_while_an_extension_is_current(world):
    """While the Erweiterung is excluded, neither an erhaltung answer nor a withdrawal may end
    the herstellung election; once the exclusion is withdrawn, both are allowed again."""
    renovation = _allocation(world, "renovation")
    herstellung = _create(
        world, _input(world, K.RENOVATION_TREATMENT, HERSTELLUNG, allocation_id=renovation)
    )
    exclusion = _create(
        world, _input(world, K.EXCLUDE_FROM_15PCT, EXTENSION, allocation_id=renovation)
    )
    to_erhaltung = _input(
        world,
        K.RENOVATION_TREATMENT,
        ERHALTUNG,
        allocation_id=renovation,
        supersedes_id=herstellung.id,
    )
    withdrawal = _input(
        world,
        K.RENOVATION_TREATMENT,
        HERSTELLUNG,
        allocation_id=renovation,
        supersedes_id=herstellung.id,
        withdrawn=True,
    )
    _refused_extension(world, to_erhaltung)
    _refused_extension(world, withdrawal)
    # Herstellung again (a correction of the same answer, e.g. a new note) stays allowed.
    again = _create(
        world,
        _input(
            world,
            K.RENOVATION_TREATMENT,
            HERSTELLUNG,
            allocation_id=renovation,
            supersedes_id=herstellung.id,
            note="Anbau Wintergarten",
        ),
    )
    assert world.query(list_tax_inputs, world.property_id) == [exclusion, again]

    # Withdraw the exclusion first; then the election can change and be withdrawn.
    _create(
        world,
        _input(
            world,
            K.EXCLUDE_FROM_15PCT,
            EXTENSION,
            allocation_id=renovation,
            supersedes_id=exclusion.id,
            withdrawn=True,
        ),
    )
    erhaltung = _create(
        world,
        _input(
            world,
            K.RENOVATION_TREATMENT,
            ERHALTUNG,
            allocation_id=renovation,
            supersedes_id=again.id,
        ),
    )
    assert world.query(list_tax_inputs, world.property_id) == [erhaltung]
    _create(
        world,
        _input(
            world,
            K.RENOVATION_TREATMENT,
            ERHALTUNG,
            allocation_id=renovation,
            supersedes_id=erhaltung.id,
            withdrawn=True,
        ),
    )
    assert world.query(list_tax_inputs, world.property_id) == []


def test_the_herstellung_election_can_be_withdrawn_once_the_extension_is(world):
    """Herstellung + Erweiterung: withdrawing the election is refused until the exclusion is
    withdrawn, then the same withdrawal goes through."""
    renovation = _allocation(world, "renovation")
    herstellung = _create(
        world, _input(world, K.RENOVATION_TREATMENT, HERSTELLUNG, allocation_id=renovation)
    )
    exclusion = _create(
        world, _input(world, K.EXCLUDE_FROM_15PCT, EXTENSION, allocation_id=renovation)
    )
    withdrawal = _input(
        world,
        K.RENOVATION_TREATMENT,
        HERSTELLUNG,
        allocation_id=renovation,
        supersedes_id=herstellung.id,
        withdrawn=True,
    )
    _refused_extension(world, withdrawal)
    _create(
        world,
        _input(
            world,
            K.EXCLUDE_FROM_15PCT,
            EXTENSION,
            allocation_id=renovation,
            supersedes_id=exclusion.id,
            withdrawn=True,
        ),
    )
    _create(world, withdrawal)
    assert world.query(list_tax_inputs, world.property_id) == []


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


# --- a shorter actual useful life (D79) ------------------------------------------------


def _building(world: World, build_year: int, purchase_date: date) -> uuid.UUID:
    return world.cmd(
        create_property,
        PropertyCreate(
            label=f"Altbau {build_year}",
            kind="etw",
            address=Address(street="Ring", house_number="7", postal_code="10115", city="Berlin"),
            build_year=build_year,
            notarized_on=purchase_date - timedelta(days=60),
            purchase_date=purchase_date,
            purchase_price=Decimal("375000.00"),
        ),
    ).id


def _gutachten(world: World) -> uuid.UUID:
    return world.document("manual", {}, type="contract", subtype="appraisal")


def _life(property_id, years: int, tax_year: int, document_id, **extra) -> TaxInputCreate:
    return TaxInputCreate.model_validate(
        {
            "property_id": property_id,
            "kind": K.USEFUL_LIFE,
            "tax_year": tax_year,
            "document_id": document_id,
            "value": {"years": years},
        }
        | extra
    )


@pytest.mark.parametrize(
    ("build_year", "too_long", "shorter"),
    [(1904, 40, 39), (1994, 50, 49), (2023, 34, 33)],  # 2.5 %, 2 %, 3 % (33⅓ years)
)
def test_a_useful_life_must_be_shorter_than_the_statutory_life(
    world, build_year, too_long, shorter
):
    prop = _building(world, build_year, date(2026, 10, 15))
    doc = _gutachten(world)
    with pytest.raises(RuleViolationError) as exc:
        world.cmd(create_tax_input, prop, _life(prop, too_long, 2026, doc))
    assert _codes(exc) == (TAX_INPUT_USEFUL_LIFE_NOT_SHORTER,)
    assert world.query(list_tax_inputs, prop, include_history=True) == []
    assert _events(world) == []
    row = world.cmd(create_tax_input, prop, _life(prop, shorter, 2026, doc))
    assert (row.kind, row.tax_year, row.document_id, row.value.years) == (
        K.USEFUL_LIFE,
        2026,
        doc,
        shorter,
    )
    (event,) = _events(world)
    assert event.payload["kind"] == "useful_life"
    assert event.payload["value"] == {"years": shorter}
    assert event.payload["tax_year"] == 2026


def test_a_later_useful_life_must_be_shorter_than_what_is_left_of_the_statutory_life(world):
    # D79 review F1: 1904 (480 months), handed over 15 Oct 2026; on 1 Jan 2028 465 are left.
    prop = _building(world, 1904, date(2026, 10, 15))
    doc = _gutachten(world)
    with pytest.raises(RuleViolationError) as exc:
        world.cmd(create_tax_input, prop, _life(prop, 39, 2028, doc))  # 468 months
    assert _codes(exc) == (TAX_INPUT_USEFUL_LIFE_NOT_SHORTER,)
    row = world.cmd(create_tax_input, prop, _life(prop, 38, 2028, doc))  # 456 months
    assert (row.tax_year, row.value.years) == (2028, 38)


def test_a_useful_life_needs_its_gutachten_and_starts_no_earlier_than_the_purchase(world):
    # The world's flat: completed 1994 (50 years), handed over 1 May 2024.
    with pytest.raises(ValueError, match="tax_input.scope_invalid"):
        _input(world, K.USEFUL_LIFE, {"years": 30}, tax_year=2026)  # no document
    with pytest.raises(NotFoundError):
        _create(world, _life(world.property_id, 30, 2026, uuid.uuid4()))
    doc = _gutachten(world)
    with pytest.raises(RuleViolationError) as exc:
        _create(world, _life(world.property_id, 30, 2023, doc))
    assert _codes(exc) == (TAX_INPUT_SCOPE_INVALID,)
    assert world.query(list_tax_inputs, world.property_id, include_history=True) == []
    row = _create(world, _life(world.property_id, 30, 2024, doc))
    assert row.tax_year == 2024


def test_one_useful_life_per_year_superseded_and_withdrawn_as_usual(world):
    doc = _gutachten(world)
    first = _create(world, _life(world.property_id, 30, 2026, doc))
    # The same year is the same question: a second answer must supersede the first.
    with pytest.raises(RuleViolationError) as exc:
        _create(world, _life(world.property_id, 28, 2026, doc))
    assert _codes(exc) == (TAX_INPUT_NOT_CURRENT,)
    corrected = _create(world, _life(world.property_id, 28, 2026, doc, supersedes_id=first.id))
    # A later Gutachten from another year stands beside it.
    later = _create(world, _life(world.property_id, 20, 2030, doc))
    current = world.query(find_current, world.property_id, K.USEFUL_LIFE, tax_year=2026)
    assert current == [corrected]
    assert world.query(list_tax_inputs, world.property_id, kind=K.USEFUL_LIFE) == [
        corrected,
        later,
    ]
    # A withdrawal repeats the value and is not re-checked.
    _create(
        world,
        _life(world.property_id, 28, 2026, doc, supersedes_id=corrected.id, withdrawn=True),
    )
    assert world.query(list_tax_inputs, world.property_id, kind=K.USEFUL_LIFE) == [later]


# --- a movable asset, the Einbauküche (D80) --------------------------------------------
# The world's flat: price 289,000.00, handed over 1 May 2024. With 11,000.00 of Nebenkosten
# and a 72 % building share the building's basis is 300,000.00 × 0.72 = 216,000.00; a kitchen
# of A in the price has the basis A × 300,000 ÷ 289,000.


def _split(world: World) -> None:
    world.cmd(
        add_acquisition_cost,
        AcquisitionCostCreate(
            property_id=world.property_id,
            kind=AcquisitionCostKind.REAL_ESTATE_TRANSFER_TAX,
            amount=Decimal("11000.00"),
        ),
    )
    split = world.cmd(
        record_price_split,
        PriceSplitCreate(
            property_id=world.property_id,
            source=PriceSplitSource.PURCHASE_CONTRACT,
            building_share=Decimal("0.720000"),
        ),
    )
    world.cmd(select_price_split, world.property_id, split.id)


def _kitchen(world: World, amount: str, tax_year: int, **extra) -> TaxInputCreate:
    value = {"label": "einbaukueche", "amount": amount}
    value |= extra.pop("value", {})
    return _input(world, K.MOVABLE_ASSET, value, tax_year=tax_year, **extra)


def _refused(world: World, data: TaxInputCreate) -> tuple[str, ...]:
    with pytest.raises(RuleViolationError) as exc:
        _create(world, data)
    return _codes(exc)


def test_a_kitchen_in_the_price_needs_a_split_and_stays_below_the_building(world):
    assert _refused(world, _kitchen(world, "8500.00", 2024)) == (TAX_INPUT_MOVABLE_ASSET_NO_SPLIT,)
    _split(world)
    # 208,080.00 × 300,000 ÷ 289,000 = 216,000.00: not below the building's basis.
    assert _refused(world, _kitchen(world, "208080.00", 2024)) == (
        TAX_INPUT_MOVABLE_ASSET_EXCEEDS_BUILDING,
    )
    assert world.query(list_tax_inputs, world.property_id, include_history=True) == []
    assert _events(world) == []
    # No document needed: the value may be an estimate (the draft warns, D80).
    row = _create(world, _kitchen(world, "8500.00", 2024))
    assert (row.kind, row.tax_year, row.document_id) == (K.MOVABLE_ASSET, 2024, None)
    assert (row.value.amount, row.value.useful_life_years, row.value.in_purchase_price) == (
        Decimal("8500.00"),
        10,
        True,
    )
    (event,) = _events(world)
    assert event.payload["kind"] == "movable_asset"
    assert event.payload["tax_year"] == 2024


def test_a_kitchen_starts_in_the_purchase_year_and_one_bought_later_not_before_it(world):
    _split(world)
    for year in (2023, 2025):  # in the price: the purchase year (2024) only
        assert _refused(world, _kitchen(world, "8500.00", year)) == (TAX_INPUT_MOVABLE_ASSET_YEAR,)
    separate = {"value": {"in_purchase_price": False}}
    assert _refused(world, _kitchen(world, "6000.00", 2023, **separate)) == (
        TAX_INPUT_MOVABLE_ASSET_YEAR,
    )
    assert world.query(list_tax_inputs, world.property_id, include_history=True) == []
    row = _create(world, _kitchen(world, "6000.00", 2026, **separate))
    assert (row.tax_year, row.value.in_purchase_price) == (2026, False)


def test_a_kitchen_bought_separately_needs_no_split(world):
    row = _create(world, _kitchen(world, "6000.00", 2025, value={"in_purchase_price": False}))
    assert row.tax_year == 2025


def test_one_kitchen_per_flat_corrected_in_its_year_and_withdrawn_as_usual(world):
    _split(world)
    doc = world.document("invoice", {}, type="other")
    first = _create(world, _kitchen(world, "8500.00", 2024))
    # The label is the question, whatever the year: a second kitchen must supersede the first.
    later = _kitchen(world, "6000.00", 2026, value={"in_purchase_price": False})
    assert _refused(world, later) == (TAX_INPUT_NOT_CURRENT,)
    # A correction keeps the first year (withdraw and record anew to move it) ...
    moved = _kitchen(world, "6000.00", 2026, supersedes_id=first.id, value=later.value.model_dump())
    assert _refused(world, moved) == (TAX_INPUT_SCOPE_INVALID,)
    # ... and is checked against the building without the row it replaces.
    corrected = _create(
        world,
        _kitchen(world, "208000.00", 2024, supersedes_id=first.id, document_id=doc),
    )
    assert world.query(find_current, world.property_id, K.MOVABLE_ASSET, tax_year=2024) == [
        corrected
    ]
    _create(
        world,
        _kitchen(world, "208000.00", 2024, supersedes_id=corrected.id, withdrawn=True),
    )
    assert world.query(list_tax_inputs, world.property_id, kind=K.MOVABLE_ASSET) == []
    # Withdrawn, the kitchen can be recorded anew, from another year.
    again = _create(world, later)
    assert world.query(list_tax_inputs, world.property_id, kind=K.MOVABLE_ASSET) == [again]
