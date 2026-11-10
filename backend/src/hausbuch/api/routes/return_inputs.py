"""The income tax return's inputs and its computed reads (docs/phases/phase-4b.md §9).

Return inputs are append-only rows (pay documents, Werbungskosten facts per owner; children,
childcare, §35a and donations per household): a correction or a withdrawal is a new row
naming the row it replaces (supersedes_id); services/return_inputs.py holds the checks. The
Anlage N draft and the whole-return estimate are computed on read and store nothing (D59).
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Query

from hausbuch.rules.tax import registry
from hausbuch.schemas.common import Schema, TaxYear
from hausbuch.schemas.employment import AnlageNDraft, ReturnEstimate, ReturnInput, ReturnInputCreate
from hausbuch.schemas.enums import ReturnInputKind
from hausbuch.services.documents import LOCAL_TIME_ZONE
from hausbuch.services.return_inputs import (
    anlage_n_draft,
    create_input,
    household_return_estimate,
    list_return_inputs,
)

from ..deps import Cmd, domain_router

router = domain_router()

_YEAR = Query(ge=2000, le=2100)


@router.get("/return-inputs")
def return_inputs_list(
    cmd: Cmd,
    tax_year: Annotated[int | None, _YEAR] = None,
    owner_id: UUID | None = None,
    kind: ReturnInputKind | None = None,
    include_history: bool = False,
) -> list[ReturnInput]:
    """The current rows (not superseded, not withdrawn), oldest first; include_history=true
    adds the rest. tax_year Y lists the yearly kinds' rows of Y and the spanning kinds' (child,
    work_equipment) of Y or earlier (D97); owner_id and kind narrow the list."""
    return cmd.read(
        list_return_inputs,
        tax_year=tax_year,
        owner_id=owner_id,
        kind=kind,
        include_history=include_history,
    )


@router.post("/return-inputs", status_code=201)
def return_input_create(body: ReturnInputCreate, cmd: Cmd) -> ReturnInput:
    """One new row and its event (owner.return_input_recorded, or
    workspace.return_input_recorded for the household kinds). 422 return_input.scope_invalid,
    return_input.not_current, return_input.carried_from_invalid,
    return_input.work_room_conflict, return.childcare_age, return.household_services_cash,
    anlage_n.useful_life_missing, tax.no_rule_set (a yearly row before 2026); 404 for an
    unknown owner, document or referenced row."""
    return cmd.run(create_input, body)


class AnlageNYears(Schema):
    """GET /tax/anlage-n/years: the tax years with an Anlage N rule set file of their own."""

    years: list[TaxYear]


@router.get("/tax/anlage-n/years")
def anlage_n_years() -> AnlageNYears:
    """The years an Anlage N draft exists for, ascending (the tab's year picker): a draft is
    only made under the year's own rule set (GET /owners/{id}/anlage-n is 422 tax.no_rule_set
    for any other year)."""
    return AnlageNYears(years=list(registry.anlage_n_years()))


@router.get("/owners/{owner_id}/anlage-n")
def owner_anlage_n(owner_id: UUID, cmd: Cmd, tax_year: Annotated[int, _YEAR]) -> AnlageNDraft:
    """The owner's Anlage N draft, computed now. 422 tax.no_rule_set before 2026,
    anlage_n.no_pay without any pay source; 404 for an unknown owner."""
    return cmd.read(anlage_n_draft, owner_id, tax_year=tax_year)


@router.get("/tax/return-estimate")
def tax_return_estimate(cmd: Cmd, tax_year: Annotated[int, _YEAR]) -> ReturnEstimate:
    """The household's whole return (refund or back-payment), computed now from every owner's
    rows, the household rows, every property's Anlage V result and the year's questionnaire.
    422 tax.no_rule_set before 2026."""
    today = datetime.now(LOCAL_TIME_ZONE).date()
    return cmd.read(household_return_estimate, tax_year=tax_year, today=today)
