"""Pending until hausbuch.rules.extraction_parse exists (phase 2); then these pass unchanged."""

import pytest
from fixture_models_phase2 import ParseFixture, load

rules = pytest.importorskip(
    "hausbuch.rules.extraction_parse", reason="phase 2 rule not implemented"
)
FX = load("extraction_parse")
assert isinstance(FX, ParseFixture)


@pytest.mark.rule
@pytest.mark.parametrize("case", FX.cases, ids=lambda c: c.id)
def test_parse_lines(case):
    got = rules.parse_lines(case.blocks, case.kind, page_offset=case.page_offset)
    assert got == case.expect
