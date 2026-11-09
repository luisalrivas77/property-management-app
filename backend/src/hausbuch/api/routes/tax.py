"""The Anlage V draft of a property (docs/phases/phase-3.md §10).

Computed on every read and never stored (D59). Error codes, all 422 with `code`:
`tax.year_not_available` for a year outside the checklist's available_years,
`tax.no_rule_set` for a year without a rule set.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Query

from hausbuch.schemas.tax import AnlageVDraft
from hausbuch.services.documents import LOCAL_TIME_ZONE
from hausbuch.services.tax import anlage_v_draft

from ..deps import Cmd, domain_router

router = domain_router()


@router.get("/properties/{property_id}/anlage-v")
def property_anlage_v(
    property_id: UUID,
    cmd: Cmd,
    tax_year: Annotated[int, Query(ge=2000, le=2100)],
) -> AnlageVDraft:
    """The draft Anlage V of the property for tax_year: every line with its sources, the
    warnings, the open documents, the 15 % tracker and the statement coverage."""
    today = datetime.now(LOCAL_TIME_ZONE).date()
    return cmd.read(anlage_v_draft, property_id, tax_year=tax_year, today=today)
