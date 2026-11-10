"""The Anlage N draft's per-block Werbungskosten (`AnlageNDraft.components`) and the children's
birth months (`ReturnEstimate.children`): the blocks carry the amounts the rules computed, so
they add up to `wk_itemised` in every anlage_n.yaml case, and each block equals the rule's
own figure. Fixtures are read, never changed."""

from decimal import Decimal

import pytest
from fixture_models_phase4b import load
from test_return_estimate import _estimate

from hausbuch.rules.tax import registry
from hausbuch.rules.tax.anlage_n._base import rules_for
from hausbuch.rules.tax.anlage_n.draft import draft_n, itemise

ZERO = Decimal("0")
BLOCKS = (
    "commute",
    "home_office",
    "work_room",
    "equipment",
    "telecom",
    "dues",
    "training",
    "other",
)

_CASES = [c for c in load("anlage_n").cases if c.expect.error is None]


@pytest.mark.rule
@pytest.mark.parametrize("case", _CASES, ids=[c.id for c in _CASES])
def test_components_add_up_to_the_itemised_total(case):
    rs, tariff = rules_for(case.tax_year), registry.tariff(case.tax_year)
    got = draft_n(case.facts, case.tax_year, rs, tariff)
    assert got.components.total() == got.wk_itemised, case.id
    rule = vars(itemise(case.facts, case.tax_year, rs, tariff).components)
    for name in BLOCKS:
        block = getattr(got.components, name)
        assert (block.amount if block is not None else ZERO) == rule[name], f"{case.id}: {name}"
    if got.components.equipment is not None:
        items = got.components.equipment.items
        assert sum((i.amount for i in items), ZERO) == got.components.equipment.amount
    for name in ("dues", "training", "other"):
        block = getattr(got.components, name)
        if block is not None:
            assert sum((i.amount for i in block.items), ZERO) == block.amount, f"{case.id}: {name}"


@pytest.mark.rule
def test_commute_block_explains_its_amount():
    case = next(c for c in load("anlage_n").cases if c.id == "owner_b_commute_day_rate")
    got = draft_n(case.facts, 2026, rules_for(2026), registry.tariff(2026))
    commute = got.components.commute
    assert commute is not None and commute.routes
    route = commute.routes[0]
    assert route.km_car + route.km_rest == route.km
    assert [t.rate for t in commute.rates] == [t.rate for t in rules_for(2026).commute_rates]
    assert commute.amount == max(ZERO, commute.total - commute.employer_offset)


@pytest.mark.rule
def test_children_carry_their_birth_month_never_the_day():
    case = next(c for c in load("return_estimate").cases if c.id == "household_2026")
    got = _estimate(case)
    assert [c.model_dump() for c in got.children] == [{"born_month": "2026-03", "months": 10}]
    assert got.assessed.children is not None
    assert sum(c.months for c in got.children) == got.assessed.children.months
