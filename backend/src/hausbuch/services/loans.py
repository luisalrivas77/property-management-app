"""Loan commands: current-state row plus loan.* events in the same transaction (D4).

The repayment schedule is computed from the stored terms on every read, never stored (D10).
Principal and disbursement date are the loan.disbursed milestone, so update_loan leaves them;
they change only through correct_disbursement, which appends a correcting loan.disbursed.
Loan events carry the property's History scope.
"""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Row, insert, select, update

from hausbuch.db.tables import loan as loan_table
from hausbuch.db.tables import property_ as property_table
from hausbuch.rules.loan_schedule import build_schedule
from hausbuch.schemas.enums import EntityType
from hausbuch.schemas.events import EventType, LoanCreated, LoanDisbursed, Updated
from hausbuch.schemas.loan import Loan, LoanCreate, LoanSchedule, LoanTerms

from .context import CommandContext
from .errors import NotFoundError
from .events import append_event, diff_changes, latest_in_chain, milestone_root
from .iban import protect_iban
from .lookup import require_row
from .owners import UNSET, Unset

_TERMS = tuple(LoanTerms.model_fields)
_CREATE_FIELDS = set(LoanCreate.model_fields) - {"account_iban"}


def create_loan(
    ctx: CommandContext, data: LoanCreate, *, iban_key: bytes, signed_on: date | None = None
) -> Loan:
    """The loan row plus loan.created (History date = signed_on, when given) and
    loan.disbursed (disbursed_on, amount = principal)."""
    if signed_on is not None and signed_on > data.disbursed_on:
        raise ValueError("a loan cannot be paid out before its contract is signed")
    account = None if data.account_iban is None else protect_iban(data.account_iban, iban_key)
    require_row(ctx.conn, property_table, EntityType.PROPERTY, data.property_id)
    row = ctx.conn.execute(
        insert(loan_table)
        .values(
            workspace_id=ctx.workspace_id,
            **data.model_dump(exclude={"account_iban"}),
            account_iban_masked=None if account is None else account.masked,
            account_iban_hmac=None if account is None else account.hmac,
            created_at=ctx.now,
            updated_at=ctx.now,
        )
        .returning(*loan_table.c)
    ).one()
    loan = _to_loan(row)
    terms, scope = loan.terms, {"property_id": loan.property_id}
    append_event(
        ctx,
        EventType.LOAN_CREATED,
        loan.id,
        LoanCreated(
            lender=loan.lender,
            principal=terms.principal,
            nominal_rate=terms.nominal_rate,
            monthly_payment=terms.monthly_payment,
            first_payment_on=terms.first_payment_on,
            fixed_until=terms.fixed_until,
            follow_up_rate=terms.follow_up_rate,
        ),
        effective_date=signed_on,
        **scope,
    )
    append_event(
        ctx,
        EventType.LOAN_DISBURSED,
        loan.id,
        LoanDisbursed(amount=terms.principal),
        effective_date=terms.disbursed_on,
        **scope,
    )
    return loan


def update_loan(
    ctx: CommandContext,
    loan_id: UUID,
    *,
    lender: str | Unset = UNSET,
    contract_number: str | None | Unset = UNSET,
    nominal_rate: Decimal | Unset = UNSET,
    monthly_payment: Decimal | Unset = UNSET,
    first_payment_on: date | Unset = UNSET,
    fixed_until: date | None | Unset = UNSET,
    follow_up_rate: Decimal | None | Unset = UNSET,
    account_iban: str | None | Unset = UNSET,
    iban_key: bytes | None = None,
) -> Loan:
    """Change only the fields passed (None clears an optional one). The account IBAN shows in
    the event as its mask only. No-op if nothing changes."""
    requested = {
        name: value
        for name, value in (
            ("lender", lender),
            ("contract_number", contract_number),
            ("nominal_rate", nominal_rate),
            ("monthly_payment", monthly_payment),
            ("first_payment_on", first_payment_on),
            ("fixed_until", fixed_until),
            ("follow_up_rate", follow_up_rate),
        )
        if value is not UNSET
    }
    account = None
    if account_iban is not UNSET and account_iban is not None:
        if iban_key is None:
            raise ValueError("iban_key is required to change a loan's account IBAN")
        account = protect_iban(account_iban, iban_key)

    row = require_row(ctx.conn, loan_table, EntityType.LOAN, loan_id, for_update=True)
    current = _to_loan(row)
    fields = set(requested)
    flat = _flat(current)
    old = {name: flat[name] for name in fields}
    # Validate the result as a whole loan, with the same rules as on create.
    new = LoanCreate.model_validate({name: flat[name] for name in _CREATE_FIELDS} | requested)
    values = new.model_dump(include=fields)
    if not diff_changes(old, values):
        values = {}
    if account_iban is not UNSET:
        new_hmac = None if account is None else account.hmac
        if new_hmac != row.account_iban_hmac:
            values["account_iban_masked"] = None if account is None else account.masked
            values["account_iban_hmac"] = new_hmac
    if not values:
        return current

    updated_row = ctx.conn.execute(
        update(loan_table)
        .where(loan_table.c.id == loan_id)
        .values(**values, updated_at=ctx.now)
        .returning(*loan_table.c)
    ).one()
    updated = _to_loan(updated_row)
    shown = fields | ({"account_iban_masked"} if "account_iban_hmac" in values else set())
    before = flat | {"account_iban_masked": current.account_iban_masked}
    after = _flat(updated) | {"account_iban_masked": updated.account_iban_masked}
    changes = diff_changes(
        {name: before[name] for name in shown}, {name: after[name] for name in shown}
    )
    append_event(
        ctx,
        EventType.LOAN_UPDATED,
        loan_id,
        Updated(changes=changes),
        property_id=updated.property_id,
    )
    return updated


def correct_disbursement(
    ctx: CommandContext,
    loan_id: UUID,
    *,
    new_date: date | None = None,
    principal: Decimal | None = None,
) -> Loan:
    """Correct the loan.disbursed milestone: the payout date and/or the principal (None keeps
    the recorded value). Updates the row, so the schedule follows it, and appends a correcting
    loan.disbursed against the latest event of its chain. The loan as a whole must pass
    create's rules, and it cannot be paid out before its contract date (loan.created).
    No-op (no write, no event) if nothing changes."""
    row = require_row(ctx.conn, loan_table, EntityType.LOAN, loan_id, for_update=True)
    current = _to_loan(row)
    flat = _flat(current)
    changes = {
        "disbursed_on": current.terms.disbursed_on if new_date is None else new_date,
        "principal": current.terms.principal if principal is None else principal,
    }
    LoanCreate.model_validate({name: flat[name] for name in _CREATE_FIELDS} | changes)
    if all(flat[name] == value for name, value in changes.items()):
        return current
    signed = latest_in_chain(ctx.conn, milestone_root(ctx.conn, EventType.LOAN_CREATED, loan_id).id)
    if signed.effective_date is not None and signed.effective_date > changes["disbursed_on"]:
        raise ValueError("a loan cannot be paid out before its contract is signed")

    latest = latest_in_chain(
        ctx.conn, milestone_root(ctx.conn, EventType.LOAN_DISBURSED, loan_id).id
    )
    updated_row = ctx.conn.execute(
        update(loan_table)
        .where(loan_table.c.id == loan_id)
        .values(**changes, updated_at=ctx.now)
        .returning(*loan_table.c)
    ).one()
    updated = _to_loan(updated_row)
    append_event(
        ctx,
        EventType.LOAN_DISBURSED,
        loan_id,
        LoanDisbursed(amount=updated.terms.principal),
        property_id=updated.property_id,
        effective_date=updated.terms.disbursed_on,
        corrects_event_id=latest.id,
    )
    return updated


def get_loan(conn: Connection, loan_id: UUID) -> Loan | None:
    row = conn.execute(select(loan_table).where(loan_table.c.id == loan_id)).one_or_none()
    return None if row is None else _to_loan(row)


def list_loans(conn: Connection, property_id: UUID) -> list[Loan]:
    rows = conn.execute(
        select(loan_table)
        .where(loan_table.c.property_id == property_id)
        .order_by(loan_table.c.disbursed_on, loan_table.c.id)
    )
    return [_to_loan(r) for r in rows]


def loan_schedule(conn: Connection, loan_id: UUID) -> LoanSchedule:
    """The repayment schedule from the stored terms (rules.loan_schedule.build_schedule).
    Raises RuleError loan.payment_does_not_cover_interest for a loan that never amortizes."""
    loan = get_loan(conn, loan_id)
    if loan is None:
        raise NotFoundError(EntityType.LOAN, loan_id)
    return build_schedule(loan.terms)


def _flat(loan: Loan) -> dict[str, Any]:
    return {
        "property_id": loan.property_id,
        "lender": loan.lender,
        "contract_number": loan.contract_number,
        **loan.terms.model_dump(),
    }


def _to_loan(row: Row) -> Loan:
    values = dict(row._mapping)
    terms = {name: values.pop(name) for name in _TERMS}
    values.pop("account_iban_hmac")
    return Loan.model_validate(values | {"terms": terms})
