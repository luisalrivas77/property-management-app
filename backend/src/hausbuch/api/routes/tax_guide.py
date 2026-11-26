"""Steuern as one guided path (phase 10 §7.6, D290; four steps since D294):
GET /tax/{tax_year}/guide, and the field help of „Eure Angaben“: GET /tax/{tax_year}/field-help.

The guide composes the Steuern screens' existing reads (services/tax_guide.py); the field help
is rule data (rules/tax/field_help/). Neither computes a tax figure or stores anything.
"""

from datetime import datetime
from typing import Annotated

from fastapi import Path

from hausbuch.rules.tax import registry
from hausbuch.schemas.field_help import FieldHelp
from hausbuch.schemas.tax_guide import TaxGuide
from hausbuch.services.documents import LOCAL_TIME_ZONE
from hausbuch.services.tax_guide import tax_guide

from ..deps import Cmd, domain_router

router = domain_router()


@router.get("/tax/{tax_year}/guide")
def get_tax_guide(cmd: Cmd, tax_year: Annotated[int, Path(ge=2000, le=2100)]) -> TaxGuide:
    """The year's four steps with their states, „Jetzt dran“, the Belege with their sender and
    usual month, every open point with the step and form it belongs to, each form's result and
    the whole-return estimate's. A source that cannot be read for the year is named in
    `unavailable`; the rest is served."""
    today = datetime.now(LOCAL_TIME_ZONE).date()
    return cmd.read(tax_guide, tax_year=tax_year, today=today)


@router.get("/tax/{tax_year}/field-help")
def get_tax_field_help(tax_year: Annotated[int, Path(ge=2000, le=2100)]) -> FieldHelp:
    """Per form and field of „Eure Angaben“ what it is, where to find it and an invented
    example, in German and English, with the documents it points into (D294). A year without
    its own file gets the latest earlier one with `reused_from`; before every file 422
    tax.no_rule_set. The same for every household: no workspace data is read."""
    return registry.field_help(tax_year)
