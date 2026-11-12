"""The depot's three readers in rules.extraction_parse (docs/phases/phase-6.md D165 (2), (3);
fixture tests/fixtures/phase6/quirion_documents.yaml). The kinds were registered in step 0,
so the lines parse today; the proposals and the scrub are step 4 (`pending`,
`parse_pending`)."""

import fixture_models_phase6 as phase6
import pytest

from hausbuch.rules.extraction_parse import parse_lines

CASES = phase6.load("quirion_documents").cases


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_parse(case):
    if case.parse_pending:
        pytest.skip(case.parse_pending)
    values = parse_lines(case.blocks, case.kind, page_offset=case.page_offset)
    assert values == case.expect
