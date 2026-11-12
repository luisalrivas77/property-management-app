"""A property's performance on read (docs/phases/phase-4.md §5.3–§5.5, §8, D85–D89).

Nothing here is stored (D59, D86): every call reads the property's draft facts (the same
services/tax.draft_facts the Anlage V uses, so the rent on the Planung screen equals the
Anlage V rent line), the household's income inputs and owner profiles (step 1), the loans'
terms and, for a projection, the levers of a saved scenario, and runs the pure rules in
hausbuch.rules.performance.

Projection levers: the scenario named by `scenario_id` (a row of this property, in force or
not, so a past scenario can be looked at), else the property's default scenario in force, else
the Levers defaults with the sale year = the first year whose 31 December is on or after the
§23 date (2036 for a contract of March 2026), never before next year; without a contract date
the sale year is today's year + the rule set's speculation years. Each scalar lever can be
overridden per request (D87: the screen's ± buttons) without writing anything; the per-loan
follow-up rate comes from the scenario only. A sale year not after today's year is refused
(`scenario.sale_year_before_today`).
"""

from collections.abc import Mapping
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import Connection

from hausbuch.rules.errors import SCENARIO_SALE_YEAR_BEFORE_TODAY
from hausbuch.rules.performance.flat_rate import flat_rate_check
from hausbuch.rules.performance.projection import actual_years, project
from hausbuch.rules.tax import registry
from hausbuch.rules.tax.spekulation import tax_free_from
from hausbuch.schemas.performance import Actuals, FlatRateCheck, Levers, Projection
from hausbuch.schemas.tax import DraftFacts

from .errors import NotFoundError, RuleViolationError
from .income_inputs import income_facts, owner_profiles
from .loans import list_loans
from .return_inputs import tax_contexts
from .scenarios import SCENARIO, default_scenario, get_scenario
from .tax import draft_facts


def _facts(conn: Connection, property_id: UUID, today: date) -> DraftFacts:
    """The property's draft facts (NotFoundError for an unknown property)."""
    return draft_facts(conn, property_id, tax_year=today.year, today=today)


def performance_actuals(conn: Connection, property_id: UUID, *, today: date) -> Actuals:
    """GET /properties/{id}/performance/actuals: every owned year from the purchase year through
    today's, the last one partial before 31 Dec. Every owned year is needed (the projection's
    §23 add-back and profit sum them all), so none is skipped: a purchase year before the
    first rule set raises RuleError projection.no_rule_set, one before the first tariff file
    tariff.no_file (§8 error codes); the projection raises the same."""
    facts = _facts(conn, property_id, today)
    years = actual_years(
        facts, today, owner_profiles(conn), income_facts(conn), contexts=tax_contexts(conn)
    )
    return Actuals(property_id=property_id, years=years)


def default_sale_year(facts: DraftFacts, today: date) -> int:
    """The first year whose 31 Dec is on or after the §23 date, never before next year."""
    prop = facts.property
    rule_set = registry.rule_set_or_latest(today.year)
    if prop.notarized_on is not None:
        year = tax_free_from(prop.notarized_on, rule_set).year
    else:
        year = today.year + rule_set.speculation_years
    return max(year, today.year + 1)


def _levers(
    conn: Connection,
    facts: DraftFacts,
    today: date,
    scenario_id: UUID | None,
    overrides: Mapping[str, Any],
) -> tuple[Levers, UUID | None]:
    property_id = facts.property.id
    if scenario_id is not None:
        scenario = get_scenario(conn, scenario_id)
        if scenario is None or scenario.property_id != property_id:
            raise NotFoundError(SCENARIO, scenario_id)
    else:
        scenario = default_scenario(conn, property_id)
    if scenario is not None:
        base = scenario.levers.model_dump(mode="json")
    else:
        base = {"sale_year": default_sale_year(facts, today)}
    # A lever out of bounds raises pydantic's ValidationError: a 422 validation error.
    levers = Levers.model_validate(base | {k: v for k, v in overrides.items() if v is not None})
    if levers.sale_year <= today.year:
        raise RuleViolationError(
            [SCENARIO_SALE_YEAR_BEFORE_TODAY],
            f"the sale year {levers.sale_year} is not after {today.year}: the projection"
            " starts next January",
        )
    return levers, scenario.id if scenario is not None else None


def performance_projection(
    conn: Connection,
    property_id: UUID,
    *,
    today: date,
    scenario_id: UUID | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> Projection:
    """GET /properties/{id}/performance/projection (see the module doc for the levers).
    NotFoundError for an unknown property or a scenario of another property; RuleViolation
    scenario.sale_year_before_today."""
    facts = _facts(conn, property_id, today)
    levers, used_id = _levers(conn, facts, today, scenario_id, overrides or {})
    loans = {loan.id: loan.terms for loan in list_loans(conn, property_id)}
    return project(
        facts,
        levers,
        owner_profiles(conn),
        income_facts(conn),
        today,
        loans,
        scenario_id=used_id,
        contexts=tax_contexts(conn),
    )


def property_flat_rate_check(conn: Connection, property_id: UUID, *, today: date) -> FlatRateCheck:
    """GET /properties/{id}/flat-rate-check (D155): the Pauschale rise test from the same draft
    facts as the Anlage V (confirmed allocations, tax inputs in force, tenancies with their rent
    rows in force). It needs no rule set and no tariff, so a property the actuals refuse is
    still checked. NotFoundError for an unknown property."""
    return flat_rate_check(_facts(conn, property_id, today), today)
