"""Pending until hausbuch.rules.review exists (phase 2); then these must pass unchanged."""

from decimal import Decimal

import pytest
from fixture_models_phase2 import RoutingFixture, ThresholdsFixture, load

from hausbuch.schemas.allocation import AllocationMethod, AllocationSet, RoutingInput
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
            allocations=[{"amount": "-10.00", "category": row.category, "tax_year": 2027}],
        ),
    )
    assert rules.route(item).decision == row.decision
