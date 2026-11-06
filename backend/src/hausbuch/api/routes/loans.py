"""Loans and their schedule.

Endpoints: docs/phases/phase-1-step-3.md §4.6. A loan whose payment never amortises is
refused when it is written (422 loan.payment_does_not_cover_interest with at_number), not
first noticed on GET .../schedule: POST checks the submitted terms before the command, PATCH
and the disbursement correction check the updated terms inside the command's transaction, so
a refusal writes nothing. A stored loan that still fails (data from before the check) answers
409 on GET .../schedule.
"""

from typing import Any
from uuid import UUID

from hausbuch.rules.errors import RuleError
from hausbuch.rules.loan_schedule import build_schedule
from hausbuch.schemas.api import DisbursementCorrection, ErrorKind, LoanCreateRequest, LoanPatch
from hausbuch.schemas.document import Document
from hausbuch.schemas.enums import EntityType, LinkTarget
from hausbuch.schemas.loan import Loan, LoanSchedule, LoanTerms
from hausbuch.services.context import CommandContext
from hausbuch.services.loans import (
    correct_disbursement,
    create_loan,
    get_loan,
    loan_schedule,
    update_loan,
)

from ..deps import Cmd, SettingsDep, domain_router, found
from ..errors import ApiError
from .documents import linked_documents

router = domain_router()


@router.post("/loans", status_code=201)
def loan_create(body: LoanCreateRequest, cmd: Cmd, settings: SettingsDep) -> Loan:
    build_schedule(LoanTerms.model_validate(body.model_dump(include=set(LoanTerms.model_fields))))
    return cmd.run(
        create_loan, body.as_create(), iban_key=settings.iban_hmac_key, signed_on=body.signed_on
    )


@router.get("/loans/{loan_id}")
def loan_get(loan_id: UUID, cmd: Cmd) -> Loan:
    return found(cmd.read(get_loan, loan_id), EntityType.LOAN, loan_id)


def _update_amortising(ctx: CommandContext, loan_id: UUID, **changes: Any) -> Loan:
    """update_loan, then the schedule of the updated terms: a RuleError rolls the update back
    with the rest of the transaction."""
    loan = update_loan(ctx, loan_id, **changes)
    build_schedule(loan.terms)
    return loan


@router.patch("/loans/{loan_id}")
def loan_update(loan_id: UUID, body: LoanPatch, cmd: Cmd, settings: SettingsDep) -> Loan:
    return cmd.run(_update_amortising, loan_id, **body.changes(), iban_key=settings.iban_hmac_key)


def _correct_amortising(ctx: CommandContext, loan_id: UUID, **changes: Any) -> Loan:
    """correct_disbursement, then the schedule of the corrected terms (as _update_amortising)."""
    loan = correct_disbursement(ctx, loan_id, **changes)
    build_schedule(loan.terms)
    return loan


@router.post("/loans/{loan_id}/corrections/disbursement")
def loan_correct_disbursement(loan_id: UUID, body: DisbursementCorrection, cmd: Cmd) -> Loan:
    """Correct the payout (loan.disbursed): date and/or principal. The schedule follows."""
    return cmd.run(_correct_amortising, loan_id, new_date=body.new_date, principal=body.principal)


@router.get("/loans/{loan_id}/schedule")
def loan_schedule_get(loan_id: UUID, cmd: Cmd) -> LoanSchedule:
    try:
        return cmd.read(loan_schedule, loan_id)
    except RuleError as exc:  # stored terms that never amortise: a conflict with stored data
        raise ApiError(
            409,
            ErrorKind.RULE_VIOLATION,
            str(exc),
            codes=[exc.code],
            code=exc.code,
            at_number=exc.at_number,
        ) from exc


@router.get("/loans/{loan_id}/documents")
def loan_documents(loan_id: UUID, cmd: Cmd) -> list[Document]:
    return cmd.read(linked_documents(LinkTarget.LOAN, get_loan), loan_id)
