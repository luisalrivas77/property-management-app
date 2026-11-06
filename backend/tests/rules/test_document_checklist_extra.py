"""hausbuch.rules.document_checklist beyond the fixture: the years offered, and the facts'
own checks."""

from datetime import date
from uuid import UUID

import pytest
from pydantic import ValidationError

from hausbuch.rules.document_checklist import CATALOGUE, available_years
from hausbuch.schemas.checklist import (
    ChecklistFacts,
    ChecklistPriceSplit,
    ChecklistTenancy,
    RequirementCode,
)
from hausbuch.schemas.enums import PriceSplitSource, PropertyKind

P = UUID("0000000b-0000-4000-8000-000000000001")


def _facts(**kw) -> ChecklistFacts:
    return ChecklistFacts(
        property_id=P, kind=PropertyKind.ETW, purchase_date=date(2024, 5, 1), **kw
    )


def test_years_run_from_the_purchase_year_to_this_year():
    assert available_years(_facts(), date(2026, 11, 6)) == [2024, 2025, 2026]


def test_years_start_at_the_tax_year_floor_for_an_old_purchase():
    # Review finding: a pre-2000 purchase used to offer 1999, which TaxYear refuses, so
    # every checklist request for such a property failed with 422.
    facts = ChecklistFacts(property_id=P, kind=PropertyKind.EFH, purchase_date=date(1999, 5, 1))
    years = available_years(facts, date(2026, 11, 6))
    assert years[0] == 2000 and years[-1] == 2026


def test_years_end_with_the_year_ownership_ended():
    facts = _facts(ownership_ended_on=date(2025, 6, 30))
    assert available_years(facts, date(2026, 11, 6)) == [2024, 2025]


def test_only_the_purchase_year_while_it_lies_ahead():
    assert available_years(_facts(), date(2023, 12, 31)) == [2024]


def test_the_catalogue_has_one_entry_per_code_in_code_order():
    assert [r.code for r in CATALOGUE] == list(RequirementCode)


def test_a_tenancy_must_be_on_one_of_the_units():
    with pytest.raises(ValidationError, match="every tenancy must be on one of the units"):
        _facts(
            tenancies=[
                ChecklistTenancy(id=UUID(int=1), unit_id=UUID(int=2), start_date=date(2024, 5, 1))
            ]
        )


def test_an_appraisal_split_needs_its_evidence():
    with pytest.raises(ValidationError, match="evidence_document_id"):
        ChecklistPriceSplit(id=UUID(int=1), source=PriceSplitSource.APPRAISAL)
