"""The household's income inputs and the tax rate of a year (docs/phases/phase-4.md §8, D82,
D83).

Income inputs are append-only rows (expected gross pay and deductions per owner, a Bescheid's
zvE and a typed rate per household): a correction or a withdrawal is a new row naming the row
it replaces (supersedes_id); services/income_inputs.py holds the checks. /tax-rate computes the
tax effect of a property's result on read and stores nothing.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import Query

from hausbuch.schemas.performance import IncomeInput, IncomeInputCreate
from hausbuch.services.documents import LOCAL_TIME_ZONE
from hausbuch.services.income_inputs import (
    TaxRateResult,
    create_income_input,
    list_income_inputs,
    tax_rate,
)

from ..deps import Cmd, domain_router

router = domain_router()

# A signed euro amount with at most two decimals, as Money on the wire (never a JSON number).
_AMOUNT = r"^-?[0-9]{1,12}(\.[0-9]{1,2})?$"


@router.get("/income-inputs")
def income_inputs_list(
    cmd: Cmd,
    tax_year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
    owner_id: UUID | None = None,
    include_history: bool = False,
) -> list[IncomeInput]:
    """The current rows (not superseded, not withdrawn), oldest first; include_history=true
    adds the rest. tax_year and owner_id narrow the list."""
    return cmd.read(
        list_income_inputs,
        tax_year=tax_year,
        owner_id=owner_id,
        include_history=include_history,
    )


@router.post("/income-inputs", status_code=201)
def income_input_create(body: IncomeInputCreate, cmd: Cmd) -> IncomeInput:
    """One new row and its event (owner.income_input_recorded for expected_gross_pay and
    deductions_override, workspace.income_input_recorded for bescheid_zve and
    marginal_rate_override). 422 income_input.scope_invalid (a correction of a row of another
    kind, year or owner, or a withdrawal with another value; the body's own scope check is a
    validation error), income_input.not_current (supersedes_id is not the current row, or
    another current row already answers it: supersede it); 404 for an unknown owner, document
    or superseded row."""
    return cmd.run(create_income_input, body)


@router.get("/tax-rate")
def tax_rate_get(
    cmd: Cmd,
    tax_year: Annotated[int, Query(ge=2000, le=2100)],
    result: Annotated[str, Query(pattern=_AMOUNT, description="The property's result, euros")],
    property_id: UUID | None = None,
) -> TaxRateResult:
    """The tax effect of `result` (a property's taxable result of tax_year) on the household's
    income (D82): T(zvE + result) − T(zvE) on the estimated or assessed zvE, or result × rate
    under a typed rate; the effective rate, or the tariff's slope when result is 0; and the
    source (estimate, bescheid, override). missing = true when no income input applies.
    property_id is needed only when a Bescheid of an earlier year is carried forward (its
    year's result is taken out of the assessed zvE): 422 income.assessed_result_needed
    without it. 422 tariff.no_file for a year before every tariff."""
    today = datetime.now(LOCAL_TIME_ZONE).date()
    return cmd.read(
        tax_rate,
        tax_year=tax_year,
        result=Decimal(result),
        property_id=property_id,
        today=today,
    )
