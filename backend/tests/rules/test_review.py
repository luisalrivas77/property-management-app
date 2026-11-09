"""Pending until hausbuch.rules.review exists (phase 2); then these must pass unchanged."""

from decimal import Decimal

import pytest
from fixture_models_phase2 import RoutingFixture, ThresholdsFixture, load

from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationMethod,
    AllocationSet,
    RoutingDecision,
    RoutingInput,
)
from hausbuch.schemas.enums import ImportStatus

rules = pytest.importorskip("hausbuch.rules.review", reason="phase 2 rule not implemented")
THRESHOLDS = load("thresholds")
assert isinstance(THRESHOLDS, ThresholdsFixture)
ROUTING = load("review_routing")
assert isinstance(ROUTING, RoutingFixture)


@pytest.mark.rule
@pytest.mark.parametrize("row", THRESHOLDS.thresholds, ids=lambda r: r.category.value)
def test_threshold(row):
    assert rules.threshold(row.category) == row.threshold


@pytest.mark.rule
@pytest.mark.parametrize("case", ROUTING.cases, ids=lambda c: c.id)
def test_route(case):
    assert rules.route(case.item) == case.expect


@pytest.mark.rule
@pytest.mark.parametrize("row", ROUTING.per_category, ids=lambda r: r.category.value)
def test_route_per_category(row):
    item = RoutingInput(
        import_status=ImportStatus.RECONCILED,
        auto_accept_llm=True,
        allocation_set=AllocationSet(
            transaction_id=ROUTING.tx,
            method=AllocationMethod.LLM,
            confidence=Decimal("0.900"),
            allocations=[{"amount": "-10.00", "category": row.category}],
        ),
    )
    assert rules.route(item).decision == row.decision


def _zero_item(category: AllocationCategory, *, auto_accept_llm: bool, **scope) -> RoutingInput:
    return RoutingInput(
        import_status=ImportStatus.RECONCILED,
        auto_accept_llm=auto_accept_llm,
        allocation_set=AllocationSet(
            transaction_id=ROUTING.tx,
            method=AllocationMethod.LLM,
            confidence=Decimal("0.300"),
            allocations=[{"amount": "0.00", "category": category, **scope}],
        ),
    )


@pytest.mark.rule
@pytest.mark.parametrize("auto_accept_llm", [False, True])
@pytest.mark.parametrize("category", list(AllocationCategory), ids=lambda c: c.value)
def test_a_zero_line_auto_confirms_outside_the_excluded_categories(category, auto_accept_llm):
    """D52: whatever the confidence or the setting, a 0.00 model answer of one part books
    itself exactly when its category is not excluded."""
    routing = rules.route(_zero_item(category, auto_accept_llm=auto_accept_llm))
    if category in rules.ZERO_EXCLUDED:
        assert routing.reason != rules.ZERO_AMOUNT
    else:
        assert (routing.decision, routing.reason) == (
            RoutingDecision.AUTO_CONFIRM,
            rules.ZERO_AMOUNT,
        )


@pytest.mark.rule
def test_a_zero_line_split_in_two_parts_is_not_auto_confirmed():
    item = RoutingInput(
        import_status=ImportStatus.RECONCILED,
        allocation_set=AllocationSet(
            transaction_id=ROUTING.tx,
            method=AllocationMethod.LLM,
            confidence=Decimal("0.900"),
            allocations=[
                {"amount": "-5.00", "category": "bank_fees"},
                {"amount": "5.00", "category": "other_income"},
            ],
        ),
    )
    assert rules.route(item).reason == rules.LLM_NOT_ACCEPTED


@pytest.mark.rule
def test_a_zero_document_match_is_routed_by_its_threshold():
    """D52 is for the model's answers only."""
    item = RoutingInput(
        import_status=ImportStatus.RECONCILED,
        allocation_set=AllocationSet(
            transaction_id=ROUTING.tx,
            method=AllocationMethod.DOCUMENT_MATCH,
            confidence=Decimal("0.500"),
            allocations=[{"amount": "0.00", "category": "bank_fees"}],
        ),
    )
    assert rules.route(item).reason == rules.BELOW_THRESHOLD


def test_zero_excluded_holds_every_category_the_d40_guard_reads():
    """D40's guard (services.matching._llm_guarded) is not bypassed by D52: every category it
    sends to review is excluded from the 0.00 rule too."""
    from hausbuch.services.matching import _MATCHER_CATEGORIES

    assert _MATCHER_CATEGORIES <= rules.ZERO_EXCLUDED
