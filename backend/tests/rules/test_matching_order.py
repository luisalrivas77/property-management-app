"""The matching order per account purpose (docs/phases/phase-6.md D158; fixture
tests/fixtures/phase6/matching_order.yaml), replayed on rules.matching.deterministic_answer,
the pure order services.matching runs before the model. A private line nothing explains goes
to review with rules.review.no_match_reason."""

import fixture_models_phase2 as phase2
import fixture_models_phase6 as phase6
import pytest

from hausbuch.rules import matching
from hausbuch.rules.household.taxonomy import allowed_categories
from hausbuch.rules.review import no_match_reason
from hausbuch.schemas.allocation import AllocationMethod, MatchContext
from hausbuch.schemas.enums import AccountPurpose

ORDER = phase6.load("matching_order")
MATCHERS = phase2.load("matchers")
REPLAYED = {c.id: c for c in MATCHERS.cases}
BUILT = "phase 6 step 1"


@pytest.mark.rule
@pytest.mark.parametrize("case", ORDER.cases, ids=lambda c: c.id)
def test_matching_order(case):
    if case.pending and not case.pending.startswith(BUILT):
        pytest.skip(case.pending)
    if case.replay is not None:
        replayed = REPLAYED[case.replay]
        context = MatchContext.model_validate(MATCHERS.context.model_dump())
        assert context.account_purpose is AccountPurpose.RENTAL
        got = matching.deterministic_answer([], replayed.transaction, context)
        replayed.expect.check(got, replayed.transaction)
        return
    context = ORDER.context.model_copy(update={"account_purpose": case.purpose})
    rules = [ORDER.rules[name] for name in case.rules]
    got = matching.deterministic_answer(rules, case.transaction, context)
    expect = case.expect
    if expect.outcome == "booked":
        assert got is not None
        assert got.confidence == matching.EXACT
        assert got.method is {"rule": AllocationMethod.RULE}[expect.by]
        assert [(a.amount, a.category, a.recurring) for a in got.allocations] == [
            (a.amount, a.category, a.recurring) for a in expect.allocations
        ]
        return
    if expect.question is not None:
        assert got is not None
        assert got.question == expect.question
        assert got.confidence < matching.EXACT  # never auto-confirmed
        assert [a.category for a in got.allocations] == expect.offered
        assert all(a.property_id is not None for a in got.allocations)
        return
    assert got is None
    assert no_match_reason(case.purpose, case.ai_private_accounts) == expect.error.code


def test_the_questions_are_the_fixtures():
    assert matching.TENANT_QUESTION == phase6.TENANT_QUESTION
    assert matching.LENDER_QUESTION == phase6.LENDER_QUESTION


def test_no_rental_matcher_runs_on_a_private_account():
    """The rental fixtures' own lines, moved onto a private account: rent, annuity and owner
    top-up are never booked there (D158), and no answer is outside the private codes."""
    allowed = allowed_categories(AccountPurpose.PRIVATE)
    context = MatchContext.model_validate(
        {**MATCHERS.context.model_dump(), "account_purpose": AccountPurpose.PRIVATE}
    )
    for case in MATCHERS.cases:
        got = matching.deterministic_answer([], case.transaction, context)
        if got is not None:
            assert got.confidence < matching.EXACT, case.id
            assert all(a.category in allowed for a in got.allocations), case.id


def test_a_household_rule_never_books_a_rental_line():
    """Rules are the workspace's: the REWE name rule does not hit the rental account."""
    case = next(c for c in ORDER.cases if c.id == "rule_hit_on_a_private_account")
    context = ORDER.context.model_copy(update={"account_purpose": AccountPurpose.RENTAL})
    rules = [ORDER.rules[name] for name in case.rules]
    assert matching.deterministic_answer(rules, case.transaction, context) is None


def test_switch_on_has_no_reason_to_hold_the_line():
    assert no_match_reason(AccountPurpose.PRIVATE, True) == "routing.no_match"
    assert no_match_reason(AccountPurpose.RENTAL, False) == "routing.no_match"
