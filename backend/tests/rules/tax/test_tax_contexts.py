"""Q-O29 (b) and D98: the household context and the Anlage N summaries phase 4's tax effect
reads, built by TaxContexts from return_input rows of every kind that feeds them. A field the
builder forgot would stay at its default 0 and fail here."""

from decimal import Decimal
from uuid import UUID

import pytest
from fixture_models_phase4b import current, load

from hausbuch.rules.tax import registry
from hausbuch.rules.tax.anlage_n._base import rules_for
from hausbuch.rules.tax.anlage_n.summary import TaxContexts
from hausbuch.schemas.employment import (
    ChildcareFact,
    HouseholdServicesFact,
    ReturnInputFact,
    SpecialExpenseFact,
)
from hausbuch.schemas.enums import ReturnInputKind as K
from hausbuch.schemas.performance import HouseholdTaxContext

ZERO = Decimal("0")
RENTAL = Decimal("-7948.66")


def _case():
    # Owner A pays church tax on the certificate and has a typed benefit; owner B a benefit;
    # a child born in the year (the fixture's synthetic household).
    return next(c for c in load("return_estimate").cases if c.id == "church_tax_as_special_expense")


def _row(n: int, kind: K, value, year: int = 2026) -> ReturnInputFact:
    return ReturnInputFact(id=UUID(int=0xB00 + n), kind=kind, tax_year=year, value=value)


def _household(child_id: UUID, year: int = 2026) -> list[ReturnInputFact]:
    return [
        _row(
            1,
            K.SPECIAL_EXPENSE,
            SpecialExpenseFact(
                expense_class="donation", amount=Decimal("250.00"), recipient_label="Verein"
            ),
            year,
        ),
        _row(
            2,
            K.CHILDCARE_COST,
            ChildcareFact(child_id=child_id, amount=Decimal("1000.00"), paid_to_account=True),
            year,
        ),
        _row(
            3,
            K.HOUSEHOLD_SERVICES,
            HouseholdServicesFact(
                service_class="handwerker",
                labour_cost=Decimal("500.00"),
                paid_to_account=True,
                source="own_invoice",
            ),
            year,
        ),
    ]


def _contexts(case, extra):
    return TaxContexts(
        case.resolved_owners(),
        [*current(case.household), *extra],
        rules_for,
        registry.tariff,
    )


@pytest.mark.rule
def test_every_field_of_the_household_context_comes_from_its_rows():
    case = _case()
    child = current(case.household)[0]
    employment, household = _contexts(case, _household(child.id))(2026, RENTAL)
    assert household is not None and employment is not None
    defaults = HouseholdTaxContext()
    for name in HouseholdTaxContext.model_fields:
        assert getattr(household, name) != getattr(defaults, name), name
    assert household.children == [child.value]
    assert household.replacement_benefits == Decimal("7360.00")
    assert household.church_tax_paid == Decimal("736.00")
    assert household.donations == Decimal("250.00")
    assert household.childcare == Decimal("800.00")  # 80 % of 1,000
    assert household.household_services_credit == Decimal("100.00")  # 20 % of 500
    kinds = {s.kind for s in household.sources}
    assert {"return_input", "document"} <= kinds
    assert [e.gross_source for e in employment] == ["certificate", "certificate"]
    assert all(e.vorsorge > 0 for e in employment)


@pytest.mark.rule
def test_a_later_year_reuses_the_source_year_without_its_benefits():
    """D98 carry-forward: a projected year after the last year with rows reuses its figures,
    with no wage-replacement benefit and the children in force in that year."""
    case = _case()
    child = current(case.household)[0]
    tc = _contexts(case, _household(child.id))
    assert tc.source_year(2029) == 2026
    employment, household = tc(2029, RENTAL)
    _, now = tc(2026, RENTAL)
    assert household is not None and now is not None and employment is not None
    assert household.replacement_benefits == ZERO
    assert household.children == [child.value]
    assert household.church_tax_paid == now.church_tax_paid
    assert household.donations == now.donations


@pytest.mark.rule
def test_no_yearly_row_leaves_phase_4_unchanged():
    """Only spanning rows (a child) make no source year: (None, None)."""
    case = _case()
    child = current(case.household)[0]
    tc = TaxContexts([], [child], rules_for, registry.tariff)
    assert tc.source_year(2026) is None
    assert tc(2026, RENTAL) == (None, None)
    assert _contexts(case, [])(2025, RENTAL) == (None, None)
