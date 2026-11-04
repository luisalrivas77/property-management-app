"""Pending until hausbuch.rules.retention exists; then these must pass unchanged."""

import pytest
from fixture_models import RetentionFixture, load

rules = pytest.importorskip("hausbuch.rules.retention", reason="phase 1 rule not implemented")
FX = load("retention")
assert isinstance(FX, RetentionFixture)


@pytest.mark.rule
@pytest.mark.parametrize("case", FX.cases, ids=lambda c: c.id)
def test_document_retention(case):
    assert rules.document_retention(case.input, case.policy) == case.expect


@pytest.mark.rule
@pytest.mark.parametrize("case", FX.tenant_cases, ids=lambda c: c.id)
def test_tenant_retention(case):
    assert rules.tenant_retention(case.input, case.policy) == case.expect
