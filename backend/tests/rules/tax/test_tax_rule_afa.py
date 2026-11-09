"""§5.2 AfA linear (docs/phases/phase-3.md, afa.yaml)."""

from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

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
    fields = ("amount", "basis", "additions", "rate", "months", "method_code", "method")
    fields += ("useful_life_years", "useful_life_from_year")
    for optional in ("book_value_start", "remaining_months"):
        if expect.method == "statutory" or getattr(expect, optional) is not None:
            fields += (optional,)
    for field in fields:
        assert getattr(line, field) == getattr(expect, field), (
            f"{where}: {field} {getattr(line, field)} != {getattr(expect, field)}"
        )
    if expect.sources is not None:
        assert cmp.refs(line.sources) == cmp.refs(expect.sources), where
    cmp.warnings(line.warnings, expect.warnings, where)


def _case(case_id: str):
    return next(c for c in AFA.cases if c.id == case_id)


@pytest.mark.rule
def test_useful_life_writes_off_exactly_the_basis():
    """D79: 21 years from Oct 2026 end in Sep 2047; 2026-2048 sum to the basis to the cent,
    and no year is negative."""
    case = _case("useful_life_from_purchase")
    rs = rule_set(AFA.rule_set_year)
    amounts = [afa(case.facts, year, rs).amount for year in range(2026, 2049)]
    assert all(a is not None and a >= 0 for a in amounts)
    assert sum(amounts) == Decimal("300000.00")
    assert afa(case.facts, 2060, rs).amount == Decimal("0.00")


@pytest.mark.rule
def test_without_a_useful_life_the_line_is_statutory():
    """The statutory line carries no useful-life fields (the existing fixtures' contract)."""
    case = _case("useful_life_from_purchase")
    facts = case.facts.model_copy(update={"useful_lives": []})
    line = afa(facts, 2027, rule_set(AFA.rule_set_year))
    assert (line.method, line.rate, line.amount) == (
        "statutory",
        Decimal("0.025"),
        Decimal("7500.00"),
    )
    assert line.book_value_start is None and line.useful_life_years is None
    assert not [w for w in line.warnings if w.code == "afa.useful_life_report"]


@pytest.mark.rule
def test_a_later_useful_life_restarts_the_count():
    """A second Gutachten from 2030 (15 years) restarts the remaining life from the 2030 book
    value; its own input and document become the sources."""
    case = _case("useful_life_from_purchase")
    rs = rule_set(AFA.rule_set_year)
    first = case.facts.useful_lives[0]
    later = first.model_copy(
        update={"tax_input_id": uuid4(), "from_year": 2030, "years": 15, "document_id": uuid4()}
    )
    facts = case.facts.model_copy(update={"useful_lives": [first, later]})
    line = afa(facts, 2030, rs)
    before = afa(facts, 2029, rs)
    assert line.book_value_start == before.book_value_start - before.amount
    expected = (line.book_value_start * 12 / 180).quantize(Decimal("0.01"), ROUND_HALF_UP)
    assert line.amount == expected
    assert (line.useful_life_years, line.useful_life_from_year, line.method_code) == (15, 2030, 2)
    refs = cmp.refs(line.sources)
    assert ("tax_input", later.tax_input_id) in refs
    assert ("tax_input", first.tax_input_id) not in refs
