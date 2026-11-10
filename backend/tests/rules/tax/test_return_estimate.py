"""§5.8 and §5.10: the assessed tax (children.yaml) and the whole-return estimate
(return_estimate.yaml) against the phase 4b fixtures. Fields an expectation leaves out (None)
are not asserted; warnings are compared as a multiset of codes."""

from datetime import date
from decimal import ROUND_FLOOR, Decimal
from uuid import UUID

import anlage_n_compare as cmp
import pytest
from fixture_models_phase4b import current, load

from hausbuch.rules.performance.income import default_deductions, year_tax_effect
from hausbuch.rules.tax import registry
from hausbuch.rules.tax.anlage_n._base import rules_for
from hausbuch.rules.tax.anlage_n.summary import employment_summary, household_context
from hausbuch.rules.tax.anlage_n.vorsorge import vorsorge
from hausbuch.rules.tax.return_estimate import (
    assessed_tax,
    assessed_tax_with_warnings,
    filing_deadline,
    return_estimate,
)
from hausbuch.schemas.employment import PropertyIncome
from hausbuch.schemas.performance import IncomeInputFact

ZERO = Decimal("0")


def _cases(name):
    cases = load(name).cases
    return pytest.mark.parametrize("case", cases, ids=[c.id for c in cases])


@pytest.mark.rule
@_cases("children")
def test_assessed_tax(case):
    t = registry.tariff(case.tax_year)
    got, warnings = assessed_tax_with_warnings(case.zve, case.household, case.owners, t)
    assert got == case.expect.assessed, case.id
    cmp.warnings(warnings, case.expect.warnings, case.id)


def _estimate(case):
    return return_estimate(
        case.tax_year,
        case.owners,
        case.resolved_owners(),
        current(case.household),
        [PropertyIncome(property_id=p.property_id, result=p.result) for p in case.properties],
        case.questionnaire,
        current(case.answers),
        registry.tariff(case.tax_year),
        rules_for(case.tax_year),
    )


_FIELDS = (
    "status",
    "total_income",
    "vorsorge_total",
    "church_tax_paid",
    "donations",
    "childcare",
    "special_expenses",
    "zve",
    "assessed",
    "withheld",
    "result",
    "filing_required",
    "missing",
)


@pytest.mark.rule
@_cases("return_estimate")
def test_return_estimate(case):
    e = case.expect
    if e.error is not None:
        cmp.refused(e.error.code, lambda: _estimate(case))
        return
    got = _estimate(case)
    for name in _FIELDS:
        want = getattr(e, name)
        if want is not None:
            assert getattr(got, name) == want, f"{case.id}: {name}"
    if e.warnings is not None:
        cmp.warnings(got.warnings, e.warnings, case.id)


@pytest.mark.rule
def test_the_estimate_opens_its_sources_and_stamps_the_tariff():
    case = next(c for c in load("return_estimate").cases if c.id == "household_2026")
    got = _estimate(case)
    kinds = {s.kind for s in got.sources}
    assert {"return_input", "document", "property", "tariff", "anlage_n_rule_set"} <= kinds
    assert got.tariff.tax_year == 2026
    assert [i.status for i in got.income_n] == ["certified", "certified"]
    assert sum((i.result for i in got.income_n), ZERO) + Decimal("-7948.66") == got.total_income


@pytest.mark.rule
def test_an_owner_without_any_pay_counts_with_nothing():
    case = next(c for c in load("return_estimate").cases if c.id == "no_benefits_no_child")
    facts = case.resolved_owners()
    nobody = facts[1].model_copy(update={"inputs": [], "expected_gross_pay": None})
    got = return_estimate(
        case.tax_year,
        case.owners,
        [facts[0], nobody],
        current(case.household),
        [PropertyIncome(property_id=p.property_id, result=p.result) for p in case.properties],
        case.questionnaire,
        [],
        registry.tariff(case.tax_year),
        rules_for(case.tax_year),
    )
    assert got.income_n[1].status == "none" and got.income_n[1].result == 0
    assert all(m.owner_id != nobody.owner_id for m in got.missing)


def _expected_pay(owner_id, amount: str) -> IncomeInputFact:
    return IncomeInputFact.model_validate(
        {
            "id": UUID(int=99),
            "owner_id": owner_id,
            "kind": "expected_gross_pay",
            "tax_year": 2026,
            "value": {"amount": amount},
        }
    )


@pytest.mark.rule
def test_expected_pay_only_takes_phase_4s_default_vorsorge():
    """D113: an owner with expected pay only deducts phase 4's D84 social-insurance shares, so
    the Gesamt card and the Steuerwirkung agree; the estimate stays an estimate."""
    case = next(c for c in load("return_estimate").cases if c.id == "no_benefits_no_child")
    t, rs = registry.tariff(2026), rules_for(2026)
    a, b = case.resolved_owners()
    expected_b = b.model_copy(
        update={"inputs": [], "expected_gross_pay": _expected_pay(b.owner_id, "40000.00")}
    )
    got = vorsorge(expected_b, 2026, rs, t)
    default = default_deductions(Decimal("40000.00"), t)
    assert got.deductible == default - t.employee_allowance - t.special_expenses_allowance
    assert (got.source, got.other) == ("expected", ZERO)
    assert got.deductible > 0
    assert [s.id for s in got.sources] == [UUID(int=99)]

    estimate = return_estimate(
        2026,
        case.owners,
        [a, expected_b],
        current(case.household),
        [PropertyIncome(property_id=p.property_id, result=p.result) for p in case.properties],
        case.questionnaire,
        [],
        t,
        rs,
    )
    assert estimate.vorsorge_total == vorsorge(a, 2026, rs, t).deductible + got.deductible
    assert estimate.status == "estimate"

    # A confirmed certificate wins over the expected pay, as before.
    with_cert = b.model_copy(update={"expected_gross_pay": _expected_pay(b.owner_id, "1.00")})
    assert vorsorge(with_cert, 2026, rs, t) == vorsorge(b, 2026, rs, t)
    assert vorsorge(b, 2026, rs, t).source == "certificate"


@pytest.mark.rule
def test_the_household_context_matches_the_estimate():
    """household_context (phase 4's D98 hook) builds the same context the estimate uses."""
    case = next(c for c in load("return_estimate").cases if c.id == "household_2026")
    t, rs = registry.tariff(2026), rules_for(2026)
    rental = sum((p.result for p in case.properties), ZERO)
    context = household_context(
        case.resolved_owners(), current(case.household), 2026, rs, t, rental_result=rental
    )
    got = _estimate(case)
    assert context.replacement_benefits == got.assessed.progression.benefits == Decimal("7360")
    assert assessed_tax(got.zve, context, case.owners, t) == got.assessed
    summaries = [employment_summary(f, 2026, rs, t) for f in case.resolved_owners()]
    assert sum((s.vorsorge for s in summaries), ZERO) == got.vorsorge_total


@pytest.mark.rule
@pytest.mark.parametrize(
    ("year", "deadline"),
    [(2026, date(2027, 8, 2)), (2027, date(2028, 7, 31)), (2029, date(2030, 7, 31))],
)
def test_filing_deadline(year, deadline):
    assert filing_deadline(year) == deadline


@pytest.mark.rule
@pytest.mark.parametrize(
    "case_id",
    ["household_2026", "donation_within_cap", "church_tax_as_special_expense", "childcare_2027"],
)
def test_phase_4_base_plus_the_rental_result_is_the_estimates_zve(case_id):
    """D98, one number for one thing: phase 4's zvE base (from the Anlage N summaries and the
    household context) plus the property's result is the whole-return estimate's zvE, and the
    tax effect's 'with' point is the estimate's assessed tax."""
    case = next(c for c in load("return_estimate").cases if c.id == case_id)
    year = case.tax_year
    t, rs = registry.tariff(year), rules_for(year)
    facts = case.resolved_owners()
    rental = sum((p.result for p in case.properties), ZERO)
    employment = [employment_summary(f, year, rs, t) for f in facts]
    household = household_context(facts, current(case.household), year, rs, t, rental_result=rental)
    base, effect = year_tax_effect(
        year, rental, [], case.owners, t, employment=employment, household=household
    )
    got = _estimate(case)
    assert (base.amount + rental).to_integral_value(rounding=ROUND_FLOOR) == got.zve
    assert effect.breakdown_with.total == got.assessed.total
