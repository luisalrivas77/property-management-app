"""Pending until hausbuch.rules.matching exists (phase 2); then these must pass unchanged."""

import pytest
from fixture_models_phase2 import MatchersFixture, RuleHistoryFixture, RulesFixture, load

rules = pytest.importorskip("hausbuch.rules.matching", reason="phase 2 rule not implemented")
MATCHERS = load("matchers")
assert isinstance(MATCHERS, MatchersFixture)
RULES = load("rules")
assert isinstance(RULES, RulesFixture)
HISTORY = load("rule_history")
assert isinstance(HISTORY, RuleHistoryFixture)


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


@pytest.mark.rule
@pytest.mark.parametrize("case", HISTORY.cases, ids=lambda c: c.id)
def test_rule_answer_reads_the_history(case):
    """D49: a learned rule checked against what the ledger already booked from the payer."""
    rules_in_order = [HISTORY.rules[name] for name in case.rules]
    got = rules.rule_answer(rules_in_order, case.transaction, HISTORY.context, case.history)
    if case.expect is None:
        assert got is None
    else:
        case.expect.check(got, case.transaction)
