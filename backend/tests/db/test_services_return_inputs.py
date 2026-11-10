"""services.return_inputs and services.questionnaire under hausbuch_app and RLS (phase-4b.md
D97, D106, D110, §6, §9): append-only rows, the scope and conflict checks, the owner or
workspace event with amounts and codes only, the questionnaire's answer written with its rows
in one transaction, the Zu prüfen card, and the D98 hook through GET /tax-rate.

Synthetic people, employers and amounts only (the repo is public)."""

import itertools
from datetime import date, timedelta
from decimal import Decimal

import pytest
from db.matching_support import World
from sqlalchemy import select

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import event as event_table
from hausbuch.rules.errors import (
    ANLAGE_N_USEFUL_LIFE_MISSING,
    QUESTIONNAIRE_NOT_CURRENT,
    QUESTIONNAIRE_SCOPE_INVALID,
    QUESTIONNAIRE_UNKNOWN_KEY,
    RETURN_CHILDCARE_AGE,
    RETURN_HOUSEHOLD_SERVICES_CASH,
    RETURN_INPUT_CARRIED_FROM_INVALID,
    RETURN_INPUT_NOT_CURRENT,
    RETURN_INPUT_SCOPE_INVALID,
    RETURN_INPUT_WORK_ROOM_CONFLICT,
    RuleError,
)
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.employment import ReturnInputCreate
from hausbuch.schemas.enums import ReturnInputKind
from hausbuch.schemas.events import EventType
from hausbuch.schemas.performance import IncomeInputCreate
from hausbuch.schemas.property import OwnerCreate
from hausbuch.services import review
from hausbuch.services.errors import RuleViolationError
from hausbuch.services.income_inputs import create_income_input, tax_rate
from hausbuch.services.owners import create_owner
from hausbuch.services.questionnaire import (
    QuestionnaireAnswerBody,
    list_answers,
    questionnaire_view,
    record_answer,
)
from hausbuch.services.return_inputs import (
    create_input,
    household_return_estimate,
    list_return_inputs,
)

pytestmark = pytest.mark.db

K = ReturnInputKind
_TICK = itertools.count(1)


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0])


def _owner(world: World, name: str = "Person A"):
    return world.cmd(create_owner, OwnerCreate(full_name=name, church_tax=False, tax_state=None))


def _later(world: World):
    return world.now + timedelta(seconds=next(_TICK))


def _create(world: World, **body):
    return world.cmd(create_input, ReturnInputCreate.model_validate(body), now=_later(world))


def _refused(world: World, code: str, **body) -> None:
    with pytest.raises((RuleViolationError, RuleError)) as info:
        _create(world, **body)
    got = info.value.codes if isinstance(info.value, RuleViolationError) else [info.value.code]
    assert list(got) == [code]


def _document(world: World, type_: str = "lohnsteuerbescheinigung"):
    with workspace_transaction(world.engine, world.ws) as conn:
        return world._document_row(conn, type=type_, subtype=None, title=None)


def _events(world: World, type_: EventType) -> list:
    return world.query(
        lambda conn: conn.execute(
            select(event_table).where(event_table.c.type == type_.value).order_by(event_table.c.seq)
        ).all()
    )


def _certificate(owner_id, document_id, gross: str = "60000.00") -> dict:
    return {
        "owner_id": owner_id,
        "kind": "wage_certificate",
        "tax_year": 2026,
        "document_id": document_id,
        "value": {
            "period_from": "2026-01-01",
            "period_to": "2026-12-31",
            "employer": "Muster GmbH",
            "tax_class": 4,
            "gross": gross,
            "income_tax": "9000.00",
            "pension_employee": "5580.00",
            "health_employee": "4800.00",
            "care_employee": "1080.00",
            "unemployment_employee": "780.00",
        },
    }


def _home_office(owner_id, year: int = 2026, days: int = 90, **extra) -> dict:
    return {
        "owner_id": owner_id,
        "kind": "home_office",
        "tax_year": year,
        "value": {"days_no_other_workplace": days},
    } | extra


def _work_room(owner_id, months: int = 12) -> dict:
    return {
        "owner_id": owner_id,
        "kind": "work_room",
        "tax_year": 2026,
        "value": {
            "months": months,
            "room_m2": "12",
            "home_m2": "80",
            "rent_year": "12000.00",
        },
    }


def _child(born: str = "2026-03-15", year: int = 2026) -> dict:
    return {"kind": "child", "tax_year": year, "value": {"born_on": born}}


# --- Rows and events -------------------------------------------------------------------


def test_a_certificate_is_recorded_with_amounts_only(world):
    owner = _owner(world)
    doc = _document(world)
    row = _create(world, **_certificate(owner.id, doc), note="Muster GmbH, Personalnummer")
    (event,) = _events(world, EventType.OWNER_RETURN_INPUT_RECORDED)
    assert (event.entity_type, event.entity_id) == ("owner", owner.id)
    payload = event.payload
    assert payload["return_input_id"] == str(row.id)
    assert payload["amounts"]["gross"] == "60000.00"
    assert payload["months_in_year"] is None
    assert "Muster" not in str(payload) and "Personalnummer" not in str(payload)
    assert world.query(list_return_inputs, tax_year=2026) == [row]


def test_a_certificate_needs_its_kind_of_document(world):
    owner = _owner(world)
    doc = _document(world, "bescheid")
    _refused(world, RETURN_INPUT_SCOPE_INVALID, **_certificate(owner.id, doc))


def _payslip(owner_id, document_id, employer: str) -> dict:
    return {
        "owner_id": owner_id,
        "kind": "payslip",
        "tax_year": 2026,
        "document_id": document_id,
        "value": {
            "period_month": "2026-07-01",
            "employer": employer,
            "tax_class": 4,
            "months_remaining_paid": 5,
            "gross": {"month": "5000.00", "ytd": "35000.00"},
        },
    }


def test_one_payslip_per_employer_and_month_however_the_name_is_spelled(world):
    owner = _owner(world)

    def slip(employer: str) -> dict:
        return _payslip(owner.id, _document(world, "gehaltsabrechnung"), employer)

    first = _create(world, **slip("Muster GmbH"))
    _refused(world, RETURN_INPUT_NOT_CURRENT, **slip("MUSTER GMBH"))
    assert first.value.employer == "Muster GmbH"  # the stored spelling is kept
    other = _create(world, **slip("Beispiel AG"))
    assert world.query(list_return_inputs, tax_year=2026) == [first, other]


def test_a_child_event_carries_months_never_the_birth_date(world):
    _create(world, **_child())
    (event,) = _events(world, EventType.WORKSPACE_RETURN_INPUT_RECORDED)
    assert (event.entity_type, event.entity_id) == ("workspace", world.ws)
    assert event.payload["months_in_year"] == 10
    assert "2026-03-15" not in str(event.payload)


def test_the_listing_keeps_spanning_rows_of_earlier_years(world):
    owner = _owner(world)
    item = _create(
        world,
        owner_id=owner.id,
        kind="work_equipment",
        tax_year=2025,
        value={
            "label": "Notebook",
            "item_class": "computer",
            "gross": "1500.00",
            "bought_on": "2025-05-01",
        },
    )
    office = _create(world, **_home_office(owner.id))
    assert world.query(list_return_inputs, tax_year=2026) == [item, office]
    assert world.query(list_return_inputs, tax_year=2025) == [item]


def test_a_yearly_row_before_the_first_rule_set_is_refused(world):
    owner = _owner(world)
    _refused(world, "tax.no_rule_set", **_home_office(owner.id, year=2025))


def test_one_current_home_office_row_and_its_correction(world):
    owner = _owner(world)
    first = _create(world, **_home_office(owner.id))
    _refused(world, RETURN_INPUT_NOT_CURRENT, **_home_office(owner.id, days=100))
    second = _create(world, **_home_office(owner.id, days=100, supersedes_id=first.id))
    _refused(world, RETURN_INPUT_NOT_CURRENT, **_home_office(owner.id, supersedes_id=first.id))
    _create(world, **_home_office(owner.id, days=100, supersedes_id=second.id, withdrawn=True))
    assert world.query(list_return_inputs, tax_year=2026) == []
    assert len(world.query(list_return_inputs, include_history=True)) == 3


def test_a_full_year_work_room_and_the_day_rate_exclude_each_other(world):
    owner = _owner(world)
    _create(world, **_work_room(owner.id))
    _refused(world, RETURN_INPUT_WORK_ROOM_CONFLICT, **_home_office(owner.id))
    other = _owner(world, "Person B")
    _create(world, **_home_office(other.id))
    _refused(world, RETURN_INPUT_WORK_ROOM_CONFLICT, **_work_room(other.id))
    _create(world, **_work_room(other.id, months=9) | {"owner_id": other.id})


def test_household_values_the_rules_would_refuse_are_refused_at_write(world):
    old = _create(world, **_child(born="2010-06-01", year=2010))
    _refused(
        world,
        RETURN_CHILDCARE_AGE,
        kind="childcare_cost",
        tax_year=2026,
        value={"child_id": str(old.id), "amount": "1000.00", "paid_to_account": True},
    )
    _refused(
        world,
        RETURN_INPUT_SCOPE_INVALID,
        kind="childcare_cost",
        tax_year=2026,
        value={"child_id": str(world.ws), "amount": "1000.00", "paid_to_account": True},
    )
    _refused(
        world,
        RETURN_HOUSEHOLD_SERVICES_CASH,
        kind="household_services",
        tax_year=2026,
        value={
            "service_class": "handwerker",
            "labour_cost": "500.00",
            "paid_to_account": False,
            "source": "own_invoice",
        },
    )
    owner = _owner(world)
    _refused(
        world,
        ANLAGE_N_USEFUL_LIFE_MISSING,
        owner_id=owner.id,
        kind="work_equipment",
        tax_year=2026,
        value={
            "label": "Sonstiges",
            "item_class": "other",
            "gross": "900.00",
            "bought_on": "2026-02-01",
        },
    )


def test_a_carried_row_names_last_years_current_row(world):
    owner = _owner(world)
    telecom = {"owner_id": owner.id, "kind": "telecom", "value": {"bills_year": "720.00"}}
    origin = _create(world, **telecom, tax_year=2026)
    _create(world, **telecom, tax_year=2027, carried_from_id=origin.id)
    other = _owner(world, "Person B")
    _refused(
        world,
        RETURN_INPUT_CARRIED_FROM_INVALID,
        **telecom | {"owner_id": other.id},
        tax_year=2027,
        carried_from_id=origin.id,
    )


# --- The questionnaire -----------------------------------------------------------------


def _answer(world: World, **body):
    return world.cmd(record_answer, QuestionnaireAnswerBody.model_validate(body), now=_later(world))


def _answer_refused(world: World, code: str, **body) -> None:
    with pytest.raises(RuleViolationError) as info:
        _answer(world, **body)
    assert list(info.value.codes) == [code]


def test_an_answer_is_written_with_its_rows_or_not_at_all(world):
    owner = _owner(world)
    answer = {
        "question_key": "owner.home_office_days",
        "tax_year": 2026,
        "owner_id": str(owner.id),
        "answer": "yes",
    }
    created = _answer(world, answer=answer, return_inputs=[_home_office(owner.id)])
    assert world.query(list_answers, tax_year=2026) == [created]
    assert len(world.query(list_return_inputs, tax_year=2026)) == 1
    (event,) = _events(world, EventType.WORKSPACE_QUESTIONNAIRE_ANSWERED)
    assert event.payload["question_key"] == "owner.home_office_days"

    # A second answer to the slot is refused, and a refused row takes its answer with it.
    _answer_refused(world, QUESTIONNAIRE_NOT_CURRENT, answer=answer)
    other = _owner(world, "Person B")
    room = answer | {"question_key": "owner.work_room", "owner_id": str(other.id)}
    _create(world, **_home_office(other.id))
    with pytest.raises(RuleViolationError) as info:
        _answer(world, answer=room, return_inputs=[_work_room(other.id)])
    assert list(info.value.codes) == [RETURN_INPUT_WORK_ROOM_CONFLICT]
    assert [a.question_key for a in world.query(list_answers, tax_year=2026)] == [
        "owner.home_office_days"
    ]


def test_an_answer_names_a_question_of_the_year_and_its_own_rows(world):
    owner = _owner(world)
    _answer_refused(
        world,
        QUESTIONNAIRE_UNKNOWN_KEY,
        answer={
            "question_key": "owner.no_such_question",
            "tax_year": 2026,
            "owner_id": str(owner.id),
            "answer": "yes",
        },
    )
    _answer_refused(
        world,
        QUESTIONNAIRE_SCOPE_INVALID,
        answer={"question_key": "household.children", "tax_year": 2026, "answer": "yes"},
        return_inputs=[_home_office(owner.id)],
    )


def test_the_questionnaire_lists_every_slot_and_the_progress(world):
    _owner(world)
    _owner(world, "Person B")
    view = world.query(questionnaire_view, 2026)
    assert view.progress.answered == 0
    assert view.progress.total == len(view.slots)
    # The World fixture has one property, so its questions are listed too.
    assert {s.scope for s in view.slots} == {"owner", "household", "property"}


def test_the_review_card_shows_last_years_open_questions(world):
    _owner(world)
    (card,) = [
        i
        for i in world.query(review.inbox, today=date(2027, 1, 5))
        if i.kind is InboxItemKind.QUESTIONNAIRE_OPEN
    ]
    assert card.tax_year == 2026
    # Before the first questionnaire year there is nothing to ask, and no error.
    assert all(
        i.kind is not InboxItemKind.QUESTIONNAIRE_OPEN
        for i in world.query(review.inbox, today=date(2026, 11, 9))
    )


# --- The D98 hook and the estimate -----------------------------------------------------


def test_the_tax_rate_takes_the_certified_pay(world):
    owner = _owner(world)
    world.cmd(
        create_income_input,
        IncomeInputCreate.model_validate(
            {
                "owner_id": owner.id,
                "kind": "expected_gross_pay",
                "tax_year": 2026,
                "value": {"amount": "50000.00"},
            }
        ),
        now=_later(world),
    )
    _create(world, **_certificate(owner.id, _document(world)))
    got = world.query(tax_rate, tax_year=2026, result=Decimal("-5000.00"), today=date(2026, 11, 9))
    (per_owner,) = got.zve.per_owner
    assert (per_owner.gross, per_owner.gross_source) == (Decimal("60000.00"), "certificate")
    assert per_owner.deductions_source == "employment"
    assert got.zve.household is not None


def test_the_household_estimate_is_computed_on_read(world):
    owner = _owner(world)
    _create(world, **_certificate(owner.id, _document(world)))
    estimate = world.query(household_return_estimate, tax_year=2026, today=date(2026, 11, 9))
    assert estimate.tax_year == 2026
    assert estimate.withheld.income_tax == Decimal("9000.00")
    assert estimate.result == estimate.assessed.total - estimate.withheld.total
