"""The line rules of phase 3 (docs/phases/phase-3.md §5.4–5.7, §5.10): each fixture case runs
`<module>.lines(facts, tax_year, rule_set)` (vorab: `split`) for every tax year it lists."""

import importlib

import pytest
import tax_fixture_compare as cmp
from fixture_models_phase3 import LineRuleFixture, load

from hausbuch.rules.tax.registry import rule_set

# fixture → (module, function)
RULES = {
    "erhaltungsaufwand": ("erhaltungsaufwand", "lines"),
    "schuldzinsen": ("schuldzinsen", "lines"),
    "hausgeld": ("hausgeld", "lines"),
    "umlagen": ("einnahmen", "lines"),
    "vorab": ("vorab", "split"),
}


def _cases():
    out = []
    for fixture, (module, function) in RULES.items():
        data = load(fixture)
        assert isinstance(data, LineRuleFixture)
        for case in data.cases:
            for expect in case.expect:
                out.append(
                    pytest.param(
                        module,
                        function,
                        data.rule_set_year,
                        case,
                        expect,
                        id=f"{fixture}-{case.id}-{expect.tax_year}",
                    )
                )
    return out


@pytest.mark.rule
@pytest.mark.parametrize(("module", "function", "year", "case", "expect"), _cases())
def test_line_rule(module, function, year, case, expect):
    try:
        rule = getattr(importlib.import_module(f"hausbuch.rules.tax.{module}"), function)
    except ModuleNotFoundError:
        pytest.skip(f"hausbuch.rules.tax.{module} not written yet")
    result = rule(case.facts, expect.tax_year, rule_set(year))
    cmp.rule_lines(result, expect, f"{case.id} {expect.tax_year}")
