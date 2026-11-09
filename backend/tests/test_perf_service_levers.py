"""services/performance.py without a database (phase-4.md §8, D87): which levers a projection
runs on (named scenario, default scenario, defaults with the §23 sale year), per-request
overrides, and the refusals. The reads are replaced by projection.yaml's baseline case, so
this runs everywhere; tests/db/test_services_performance.py covers the real reads."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fixture_models_phase4 import ProjectionFixture, load
from pydantic import ValidationError

from hausbuch.rules.errors import SCENARIO_SALE_YEAR_BEFORE_TODAY
from hausbuch.schemas.performance import Levers, Scenario
from hausbuch.services import performance as service
from hausbuch.services.errors import NotFoundError, RuleViolationError

PROJECTION = load("projection")
assert isinstance(PROJECTION, ProjectionFixture)
CASE = next(c for c in PROJECTION.cases if c.id == "baseline_no_growth_tax_free_sale")
FACTS = CASE.facts.resolve()
PID = FACTS.property.id
TODAY = CASE.today  # 31 Dec 2026


class Reads:
    """Stands in for the database reads the service makes."""

    def __init__(self, monkeypatch, *, default=None, scenarios=()):
        self.by_id = {s.id: s for s in scenarios}
        monkeypatch.setattr(service, "draft_facts", self.draft_facts)
        monkeypatch.setattr(service, "income_facts", lambda conn: list(CASE.income_inputs))
        monkeypatch.setattr(service, "owner_profiles", lambda conn: list(CASE.owners))
        monkeypatch.setattr(service, "list_loans", self.list_loans)
        monkeypatch.setattr(service, "get_scenario", lambda conn, sid: self.by_id.get(sid))
        monkeypatch.setattr(service, "default_scenario", lambda conn, pid: default)

    def draft_facts(self, conn, property_id, *, tax_year, today):
        if property_id != PID:
            raise NotFoundError("property", property_id)
        assert (tax_year, today) == (today.year, TODAY)
        return FACTS

    def list_loans(self, conn, property_id):
        class _Loan:
            def __init__(self, loan):
                self.id, self.terms = loan.loan_id, loan.terms

        return [_Loan(loan) for loan in CASE.loans]


def _scenario(levers: Levers, *, property_id: UUID = PID, is_default=False) -> Scenario:
    return Scenario(
        id=uuid4(),
        workspace_id=uuid4(),
        property_id=property_id,
        name="Basis",
        is_default=is_default,
        levers=levers,
        recorded_at=datetime(2026, 11, 1, tzinfo=UTC),
    )


def _project(**kwargs):
    return service.performance_projection(None, PID, today=TODAY, **kwargs)


def test_without_a_scenario_the_sale_year_is_the_first_tax_free_year(monkeypatch):
    Reads(monkeypatch)
    # Contract of 10 Mar 2026: tax-free from 11 Mar 2036, so the first 31 Dec after it.
    assert service.default_sale_year(FACTS, TODAY) == 2036
    got = _project()
    assert got.scenario == Levers(sale_year=2036)
    assert got.scenario_id is None
    assert [y.year for y in got.years] == list(range(2027, 2037))
    assert not got.sale.taxable


def test_the_default_sale_year_is_never_before_next_year():
    old = FACTS.model_copy(
        update={"property": FACTS.property.model_copy(update={"notarized_on": date(2010, 1, 5)})}
    )
    assert service.default_sale_year(old, TODAY) == 2027
    undated = FACTS.model_copy(
        update={"property": FACTS.property.model_copy(update={"notarized_on": None})}
    )
    assert service.default_sale_year(undated, TODAY) == 2036  # today + 10 years


def test_the_default_scenario_in_force_gives_the_levers(monkeypatch):
    levers = CASE.levers.model_copy(update={"sale_year": 2030})
    default = _scenario(levers, is_default=True)
    Reads(monkeypatch, default=default)
    got = _project()
    assert (got.scenario, got.scenario_id) == (levers, default.id)
    assert got.years[-1].year == 2030


def test_a_named_scenario_wins_over_the_default(monkeypatch):
    default = _scenario(Levers(sale_year=2036), is_default=True)
    named = _scenario(Levers(sale_year=2029, rent_growth=Decimal("0.03")))
    Reads(monkeypatch, default=default, scenarios=[default, named])
    got = _project(scenario_id=named.id)
    assert (got.scenario_id, got.scenario.rent_growth) == (named.id, Decimal("0.03"))
    assert got.years[-1].year == 2029


def test_overrides_change_the_read_only(monkeypatch):
    default = _scenario(Levers(sale_year=2036, upkeep_per_year=Decimal("900.00")), is_default=True)
    Reads(monkeypatch, default=default)
    got = _project(
        overrides={"sale_year": 2031, "value_growth": Decimal("0.03"), "rent_growth": None}
    )
    assert got.scenario.sale_year == 2031
    assert got.scenario.value_growth == Decimal("0.03")
    assert got.scenario.rent_growth == Decimal("0.02")  # None: not given, the scenario's stays
    assert got.scenario.upkeep_per_year == Decimal("900.00")  # kept from the scenario
    assert got.scenario_id == default.id
    assert default.levers.sale_year == 2036  # the row is untouched


def test_refusals(monkeypatch):
    theirs = _scenario(Levers(sale_year=2036), property_id=uuid4())
    Reads(monkeypatch, scenarios=[theirs])
    with pytest.raises(NotFoundError):
        _project(scenario_id=theirs.id)  # a scenario of another property
    with pytest.raises(NotFoundError):
        _project(scenario_id=uuid4())
    with pytest.raises(NotFoundError):
        service.performance_projection(None, uuid4(), today=TODAY)
    with pytest.raises(RuleViolationError) as exc:
        _project(overrides={"sale_year": 2026})
    assert exc.value.codes == (SCENARIO_SALE_YEAR_BEFORE_TODAY,)
    with pytest.raises(ValidationError):
        _project(overrides={"vacancy_months": Decimal("13")})


def test_actuals_are_the_rule_years(monkeypatch):
    Reads(monkeypatch)
    got = service.performance_actuals(None, PID, today=TODAY)
    assert got.property_id == PID
    assert [y.year for y in got.years] == [2026]
