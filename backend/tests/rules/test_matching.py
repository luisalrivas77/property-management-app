"""Pending until hausbuch.rules.matching exists (phase 2); then these must pass unchanged."""

import pytest
from fixture_models_phase2 import MatchersFixture, RulesFixture, load

rules = pytest.importorskip("hausbuch.rules.matching", reason="phase 2 rule not implemented")
MATCHERS = load("matchers")
assert isinstance(MATCHERS, MatchersFixture)
RULES = load("rules")
assert isinstance(RULES, RulesFixture)


@pytest.mark.rule
@pytest.mark.parametrize("case", MATCHERS.cases, ids=lambda c: c.id)
def test_matcher(case):
    match = getattr(rules, f"match_{case.matcher}")
    got = match(case.transaction, MATCHERS.context)
    if case.expect is None:
        assert got is None
    else:
        case.expect.check(got, case.transaction)


@pytest.mark.rule
@pytest.mark.parametrize("case", RULES.cases, ids=lambda c: c.id)
def test_apply_rule(case):
    got = rules.apply_rule(RULES.rules[case.rule], case.transaction, RULES.context)
    if case.expect is None:
        assert got is None
    else:
        case.expect.check(got, case.transaction)
