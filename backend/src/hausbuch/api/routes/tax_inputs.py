"""Tax inputs of a property (docs/phases/phase-3.md §10, D58).

The user's tax decisions (§82b spread, renovation treatment, exclusion from the 15 % test,
ortsübliche Miete, how income before the handover counts) and the certificate figures pre-fill
proposes and the user confirms. Rows are append-only: a correction or a withdrawal is a new
row naming the row it replaces (supersedes_id); services/tax_inputs.py holds the checks.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Query

from hausbuch.schemas.tax import TaxInput, TaxInputCreate, TaxInputKind
from hausbuch.services.tax_inputs import create_tax_input, list_tax_inputs

from ..deps import Cmd, domain_router

router = domain_router()


@router.get("/properties/{property_id}/tax-inputs")
def tax_inputs_list(
    property_id: UUID,
    cmd: Cmd,
    tax_year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
    kind: TaxInputKind | None = None,
    include_history: bool = False,
) -> list[TaxInput]:
    """The current rows (not superseded, not withdrawn), oldest first; include_history=true
    adds the rest. tax_year keeps that year's rows plus the rows without a year."""
    return cmd.read(
        list_tax_inputs,
        property_id,
        tax_year=tax_year,
        kind=kind,
        include_history=include_history,
    )


@router.post("/properties/{property_id}/tax-inputs", status_code=201)
def tax_input_create(property_id: UUID, body: TaxInputCreate, cmd: Cmd) -> TaxInput:
    """One new row and its property.tax_input_recorded event. 422 tax_input.scope_invalid,
    tax_input.not_current (supersedes_id is not the current row of this kind and scope, or
    another current row already answers it), tax_input.allocation_not_confirmed,
    tax_input.extension_needs_herstellung (an Erweiterung excluded from the 15 % test needs a
    renovation elected herstellung, and that election stays while the exclusion is current)."""
    return cmd.run(create_tax_input, property_id, body)
