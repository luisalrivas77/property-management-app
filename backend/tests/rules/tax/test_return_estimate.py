"""§5.8 and §5.10: the assessed tax (children.yaml) and the whole-return estimate
(return_estimate.yaml) against the phase 4b fixtures. Fields an expectation leaves out (None)
are not asserted; warnings are compared as a multiset of codes."""

from datetime import date
from decimal import Decimal

import anlage_n_compare as cmp
import pytest
from fixture_models_phase4b import current, load

from hausbuch.rules.tax import registry
from hausbuch.rules.tax.anlage_n._base import rules_for
from hausbuch.rules.tax.anlage_n.summary import employment_summary, household_context
from hausbuch.rules.tax.return_estimate import (
    assessed_tax,
    assessed_tax_with_warnings,
    filing_deadline,
    return_estimate,
)
from hausbuch.schemas.employment import PropertyIncome

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
