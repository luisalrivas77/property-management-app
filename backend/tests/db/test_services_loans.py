"""Loan commands and the computed schedule end to end, as hausbuch_app under RLS."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from pydantic import ValidationError
from sqlalchemy import text

from hausbuch.db.session import workspace_transaction
from hausbuch.rules.errors import RuleError
from hausbuch.rules.iban import BAD_CHECKSUM, iban_hmac
from hausbuch.rules.loan_schedule import PAYMENT_DOES_NOT_COVER_INTEREST, build_schedule
from hausbuch.schemas.enums import ActorType, PropertyKind
from hausbuch.schemas.events import EventType
from hausbuch.schemas.loan import LoanCreate, LoanTerms
from hausbuch.schemas.property import Address, PropertyCreate
from hausbuch.services.context import Actor, new_command
from hausbuch.services.errors import NotFoundError
from hausbuch.services.events import list_events
from hausbuch.services.loans import create_loan, get_loan, list_loans, loan_schedule, update_loan
from hausbuch.services.properties import create_property

CREATED_AT = datetime(2026, 11, 5, 14, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 6, 14, 0, tzinfo=UTC)
ACTOR = Actor(ActorType.USER, "user:test")
ACCOUNT = "DE89370400440532013000"


@pytest.fixture
def prop(run, fresh_workspaces):
    a, _ = fresh_workspaces
    return run(
        a,
        create_property,
        PropertyCreate(
            label="Wohnung",
            kind=PropertyKind.ETW,
            address=Address(
                street="Lindenstraße", house_number="12", postal_code="10115", city="Berlin"
            ),
            build_year=1994,
            notarized_on=date(2024, 3, 15),
            purchase_date=date(2024, 5, 1),
            purchase_price=Decimal("289000.00"),
        ),
        now=CREATED_AT,
    )


def _terms(property_id, **overrides):
    values = dict(
        property_id=property_id,
        lender="Sparkasse Musterstadt",
        contract_number="7001234",
        principal=Decimal("240000.00"),
        nominal_rate=Decimal("0.0365"),
        monthly_payment=Decimal("1130.00"),
        disbursed_on=date(2024, 4, 30),
        first_payment_on=date(2024, 5, 30),
        fixed_until=date(2034, 4, 30),
        account_iban=ACCOUNT,
    )
    return LoanCreate(**(values | overrides))


def _create(run, ws, data, **kwargs):
    return run(ws, create_loan, data, iban_key=TEST_IBAN_KEY, now=CREATED_AT, **kwargs)


def _events(read, ws, loan_id):
    return read(ws, list_events, entity_id=loan_id)


def test_create_writes_the_row_and_both_milestones(app_engine, fresh_workspaces, read, prop):
    a, _ = fresh_workspaces
    with workspace_transaction(app_engine, a) as conn:
        ctx = new_command(conn, a, ACTOR, now=CREATED_AT)
        loan = create_loan(ctx, _terms(prop.id), iban_key=TEST_IBAN_KEY, signed_on=date(2024, 3, 1))

    assert loan.terms.model_dump() == _terms(prop.id).model_dump(
        include=set(LoanTerms.model_fields)
    )
    assert (loan.property_id, loan.lender, loan.account_iban_masked) == (
        prop.id,
        "Sparkasse Musterstadt",
        "DE89 **** 3000",
    )
    assert read(a, get_loan, loan.id) == loan
    assert read(a, list_loans, prop.id) == [loan]
    with workspace_transaction(app_engine, a) as conn:
        digest = conn.execute(
            text("SELECT account_iban_hmac FROM loan WHERE id = :id"), {"id": loan.id}
        ).scalar_one()
    assert digest == iban_hmac(ACCOUNT, TEST_IBAN_KEY)

    created, disbursed = _events(read, a, loan.id)
    assert (created.type, created.effective_date) == (EventType.LOAN_CREATED, date(2024, 3, 1))
    assert created.payload == {
        "lender": "Sparkasse Musterstadt",
        "principal": "240000.00",
        "nominal_rate": "0.036500",
        "monthly_payment": "1130.00",
        "first_payment_on": "2024-05-30",
        "fixed_until": "2034-04-30",
        "follow_up_rate": None,
    }
    assert (disbursed.type, disbursed.effective_date, disbursed.payload) == (
        EventType.LOAN_DISBURSED,
        date(2024, 4, 30),
        {"amount": "240000.00"},
    )
    for e in (created, disbursed):
        assert (e.property_id, e.unit_id) == (prop.id, None)
        assert e.correlation_id == ctx.correlation_id


def test_the_schedule_is_computed_from_the_stored_terms(run, read, fresh_workspaces, prop):
    a, _ = fresh_workspaces
    loan = _create(run, a, _terms(prop.id, account_iban=None))

    schedule = read(a, loan_schedule, loan.id)

    assert schedule == build_schedule(loan.terms)
    first = schedule.entries[0]
    # 30E/360: 30 days from 30 Apr to 30 May at 3.65 % on 240,000.00.
    assert (first.due_on, first.interest, first.principal) == (
        date(2024, 5, 30),
        Decimal("730.00"),
        Decimal("400.00"),
    )
    assert schedule.entries[-1].closing_balance == Decimal("0.00")
    with pytest.raises(NotFoundError, match="loan"):
        read(a, loan_schedule, uuid.uuid4())


def test_a_loan_that_never_amortizes_is_stored_but_has_no_schedule(
    run, read, fresh_workspaces, prop
):
    a, _ = fresh_workspaces
    interest_only = _create(run, a, _terms(prop.id, monthly_payment=Decimal("730.00")))
    with pytest.raises(RuleError) as raised:
        read(a, loan_schedule, interest_only.id)
    assert raised.value.code == PAYMENT_DOES_NOT_COVER_INTEREST


def test_update_records_changes_and_masks_the_account(
    run, read, app_engine, fresh_workspaces, prop
):
    a, _ = fresh_workspaces
    loan = _create(run, a, _terms(prop.id))

    updated = run(
        a,
        update_loan,
        loan.id,
        follow_up_rate=Decimal("0.04"),
        lender="Sparkasse Musterstadt",  # unchanged
        account_iban="AT611904300234573201",
        iban_key=TEST_IBAN_KEY,
        now=LATER,
    )

    assert updated.terms.follow_up_rate == Decimal("0.040000")
    assert updated.account_iban_masked == "AT61 **** 3201" and updated.updated_at == LATER
    assert read(a, get_loan, loan.id) == updated
    changed = _events(read, a, loan.id)[-1]
    assert (changed.type, changed.property_id) == (EventType.LOAN_UPDATED, prop.id)
    assert changed.payload["changes"] == {
        "follow_up_rate": {"old": None, "new": "0.040000"},
        "account_iban_masked": {"old": "DE89 **** 3000", "new": "AT61 **** 3201"},
    }
    with workspace_transaction(app_engine, a) as conn:
        stored = conn.execute(
            text("SELECT e::text FROM event e WHERE entity_id = :id"), {"id": loan.id}
        ).scalars()
        assert not any("AT611904300234573201" in s for s in stored)

    # No-op, then clearing the account.
    assert run(a, update_loan, loan.id, follow_up_rate=Decimal("0.0400")) == updated
    cleared = run(a, update_loan, loan.id, account_iban=None, now=LATER)
    assert cleared.account_iban_masked is None
    assert len(_events(read, a, loan.id)) == 4


def test_domain_errors_write_nothing(run, read, fresh_workspaces, prop):
    a, b = fresh_workspaces
    loan = _create(run, a, _terms(prop.id, follow_up_rate=Decimal("0.04")))

    with pytest.raises(ValidationError, match="follow_up_rate needs fixed_until"):
        run(a, update_loan, loan.id, fixed_until=None)
    with pytest.raises(ValidationError):
        run(a, update_loan, loan.id, lender="")
    with pytest.raises(RuleError) as raised:
        run(a, update_loan, loan.id, account_iban="DE89370400440532013001", iban_key=TEST_IBAN_KEY)
    assert raised.value.code == BAD_CHECKSUM
    with pytest.raises(ValueError, match="iban_key"):
        run(a, update_loan, loan.id, account_iban=ACCOUNT)
    with pytest.raises(ValueError, match="signed"):
        _create(run, a, _terms(prop.id), signed_on=date(2024, 5, 1))
    with pytest.raises(NotFoundError, match="property"):
        _create(run, a, _terms(uuid.uuid4()))
    with pytest.raises(NotFoundError, match="property"):
        _create(run, b, _terms(prop.id))

    assert read(a, get_loan, loan.id) == loan
    assert read(a, list_loans, prop.id) == [loan]
    assert len(_events(read, a, loan.id)) == 2


def test_loan_is_invisible_from_another_workspace(run, read, fresh_workspaces, prop):
    a, b = fresh_workspaces
    loan = _create(run, a, _terms(prop.id))
    assert read(b, get_loan, loan.id) is None
    assert read(b, list_loans, prop.id) == []
    with pytest.raises(NotFoundError, match="loan"):
        read(b, loan_schedule, loan.id)
    with pytest.raises(NotFoundError, match="loan"):
        run(b, update_loan, loan.id, lender="Mallory Bank")
    assert read(a, get_loan, loan.id) == loan
