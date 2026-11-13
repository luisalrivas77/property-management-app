"""The depot's three readers in rules.extraction_parse (docs/phases/phase-6.md D165 (2), (3);
fixture tests/fixtures/phase6/quirion_documents.yaml). The kinds were registered in step 0,
and the scrub (scrub_depot) is built in step 4, so every case parses, `parse_pending` ones
included (the fixture keeps the marker; it changes only in a spec PR)."""

import fixture_models_phase6 as phase6
import pytest

from hausbuch.rules.extraction_parse import parse_lines

CASES = phase6.load("quirion_documents").cases


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_parse(case):
    values = parse_lines(case.blocks, case.kind, page_offset=case.page_offset)
    assert values == case.expect
