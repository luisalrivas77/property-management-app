"""§5.2 AfA linear (docs/phases/phase-3.md, afa.yaml)."""

import pytest
import tax_fixture_compare as cmp
from fixture_models_phase3 import AfaFixture, load

from hausbuch.rules.tax.afa import afa
from hausbuch.rules.tax.registry import rule_set

AFA = load("afa")
assert isinstance(AFA, AfaFixture)
CASES = [(c, e) for c in AFA.cases for e in c.expect]


@pytest.mark.rule
@pytest.mark.parametrize(("case", "expect"), CASES, ids=[f"{c.id}-{e.tax_year}" for c, e in CASES])
def test_afa(case, expect):
    line = afa(case.facts, expect.tax_year, rule_set(AFA.rule_set_year))
    where = f"{case.id} {expect.tax_year}"
    for field in ("amount", "basis", "additions", "rate", "months", "method_code"):
        assert getattr(line, field) == getattr(expect, field), (
            f"{where}: {field} {getattr(line, field)} != {getattr(expect, field)}"
        )
    if expect.sources is not None:
        assert cmp.refs(line.sources) == cmp.refs(expect.sources), where
    cmp.warnings(line.warnings, expect.warnings, where)
