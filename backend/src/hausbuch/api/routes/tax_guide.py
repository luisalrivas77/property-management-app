"""Steuern as one guided path of five steps (phase 10 §7.6, D290): GET /tax/{tax_year}/guide.

A read that composes the Steuern screens' existing reads (services/tax_guide.py); it computes
no tax figure and stores nothing.
"""

from datetime import datetime
from typing import Annotated

from fastapi import Path

from hausbuch.schemas.tax_guide import TaxGuide
from hausbuch.services.documents import LOCAL_TIME_ZONE
from hausbuch.services.tax_guide import tax_guide

from ..deps import Cmd, domain_router

router = domain_router()


@router.get("/tax/{tax_year}/guide")
def get_tax_guide(cmd: Cmd, tax_year: Annotated[int, Path(ge=2000, le=2100)]) -> TaxGuide:
    """The year's five steps with their states, „Jetzt dran“, the Belege with their sender and
    usual month, every open point with the step and form it belongs to, each form's result and
    the whole-return estimate's. A source that cannot be read for the year is named in
    `unavailable`; the rest is served."""
    today = datetime.now(LOCAL_TIME_ZONE).date()
    return cmd.read(tax_guide, tax_year=tax_year, today=today)
