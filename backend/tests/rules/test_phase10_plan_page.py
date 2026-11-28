"""„Euer Plan“ (phase 10 §7.11 (h), D306): tests/fixtures/phase10/plan_page.yaml against
rules/goal/risks.py and rules/goal/share.py."""

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

import fixture_models_phase10 as phase10
import pytest
from test_phase10_goal import _route_inputs

from hausbuch.rules.goal.risks import (
    PlanInputs,
    PlanProperty,
    gap_changes,
    maintenance_per_m2,
    plan_cards,
    plan_rules,
)
from hausbuch.rules.goal.share import plain, round_to, share
from hausbuch.rules.plan.levers import add_months
from hausbuch.rules.plan.projection import walk
from hausbuch.schemas.plan import PlanClass

FIXTURE = phase10.load("plan_page")


def _inputs(case) -> PlanInputs:
    goal, _, _ = _route_inputs(case)
    return PlanInputs(
        goal=goal,
        net_worth_today=case.net_worth_today,
        reserve_today=case.reserve_today,
        reserve_accounts=tuple(case.reserve_accounts),
        properties=tuple(
            PlanProperty(key=key, living_area=f.living_area, value_change=f.value_change)
            for key, f in case.flats.items()
        ),
        invested_this_month=case.invested_this_month,
        budget_adopted=case.budget_adopted,
    )


def _match(got: Any, want: Any, path: str) -> None:
    """`want` a subset of `got`: every key it names present and equal (a null expects null)."""
    if isinstance(want, dict):
        assert isinstance(got, dict), f"{path}: {got!r}"
        for key, value in want.items():
            assert key in got, f"{path}.{key} missing (has {sorted(got)})"
            _match(got[key], value, f"{path}.{key}")
    elif isinstance(want, list):
        assert isinstance(got, list) and len(got) == len(want), f"{path}: {got!r} != {want!r}"
        for n, (g, w) in enumerate(zip(got, want, strict=True)):
            _match(g, w, f"{path}[{n}]")
    elif want is None:
        assert got is None, f"{path}: {got!r} != None"
    elif isinstance(got, Decimal):
        assert got == Decimal(str(want)), f"{path}: {got} != {want}"
    else:
        assert got == want, f"{path}: {got!r} != {want!r}"


def _absent(tree: Any, path: str) -> None:
    *parents, last = path.split(".")
    node = tree
    for key in parents:
        if not isinstance(node, dict) or key not in node:
            return
        node = node[key]
    assert not (isinstance(node, dict) and last in node), f"{path} is in the shared answer"


@pytest.mark.parametrize("case", FIXTURE.cases, ids=lambda c: c.id)
def test_plan_page(case) -> None:
    cards = plan_cards(_inputs(case))
    want = case.expect
    _match(plain(cards), want.cards, "cards")
    if want.share is not None or want.share_absent:
        s = case.switches
        got = share(
            cards,
            round_amounts=s.round_amounts,
            accounts=s.accounts,
            wants=s.wants,
            risks=s.risks,
            assumptions=s.assumptions,
        )
        if want.share is not None:
            _match(got, want.share, "share")
        for path in want.share_absent:
            _absent(got, path)
        assert "todos" not in got.get("risks", {}), "the to-dos are never shared"


def test_the_shared_reserve_gap_adds_up() -> None:
    """„Zum Teilen“ rounds the reserve and its target to the reserve's step and takes the gap
    from the two rounded figures, so „6.000 € von 18.000 €, noch 12.000 €“ adds up even where
    the gap rounded on its own (12,500 → 13,000) would not (§7.13 (d) 2)."""
    case = next(c for c in FIXTURE.cases if c.id == "income_gap_empties_reserve")
    cards = plan_cards(_inputs(case))
    assert cards.heute is not None
    heute = replace(cards.heute, reserve=Decimal("5500.00"), reserve_gap=Decimal("12500.00"))
    got = share(replace(cards, heute=heute))["heute"]
    assert (got["reserve"], got["reserve_target"], got["reserve_gap"]) == (
        Decimal("6000.00"),
        Decimal("18000.00"),
        Decimal("12000.00"),
    )
    unrounded = share(replace(cards, heute=heute), round_amounts=False)["heute"]
    assert unrounded["reserve_gap"] == Decimal("12500.00")


def test_rules() -> None:
    r, want = plan_rules(), FIXTURE.rules
    assert r.income_gap_months == want["income_gap_months"]
    assert r.repair_years == want["repair_years"]
    assert {k: str(v) for k, v in r.rounding.items()} == want["rounding"]


@pytest.mark.parametrize("m", FIXTURE.maintenance, ids=lambda m: str(m.year))
def test_maintenance_rate(m) -> None:
    assert maintenance_per_m2(m.year) == m.per_m2


@pytest.mark.parametrize("r", FIXTURE.rounding, ids=lambda r: r.id)
def test_rounding(r) -> None:
    assert round_to(r.value, plan_rules().rounding[r.kind]) == r.rounded


def test_draw_is_off_by_default() -> None:
    """The walk's draw from the invested money is the income gap's only: the base run of the
    case that needs it ends in Dec 2026 without it (the fixture's note)."""
    case = next(c for c in FIXTURE.cases if c.id == "income_gap_empties_reserve")
    _, p, _ = _route_inputs(case)
    first = add_months(case.newest_closed, 1)
    changes = gap_changes(p.basis.mean_income, p.levers.income_changes, first, 6)
    gapped = p.levers.model_copy(update={"income_changes": changes})
    q = replace(p, levers=gapped)
    assert walk(q).used_up == "2026-12"
    assert walk(q, draw_invested=True).used_up is None
    assert walk(q, draw_invested=True).drawn == Decimal("13000.00")


@dataclass(frozen=True)
class _Plan:
    key: str
    monthly: Decimal
    asset_class: PlanClass


def test_a_draw_month_adds_nothing_to_the_money_still_to_come() -> None:
    """§7.11 (j), D312: `Walk.invested` counts a month's payments into the classes only in a
    month that draws nothing from them. The income-gap case with a savings plan of 100 into
    funds: Nov 2026 5,000 − 3,000 − 100 = 1,900 (100 invested), Dec 2026 – Apr 2027 short and
    drawn (nothing counted), May and Jun 2027 2,000 − 100 a month (100 each): 300 through Jun
    2027, of the 800 the plan pays in those eight months. Without the draw the walk ends in
    Dec 2026 after Nov's 100."""
    case = next(c for c in FIXTURE.cases if c.id == "income_gap_empties_reserve")
    _, p, _ = _route_inputs(case)
    first = add_months(case.newest_closed, 1)
    changes = gap_changes(p.basis.mean_income, p.levers.income_changes, first, 6)
    gapped = p.levers.model_copy(update={"income_changes": changes})
    q = replace(p, levers=gapped, plans=(_Plan("plan", Decimal("100.00"), PlanClass.DEPOT),))
    assert walk(q, until="2027-06", draw_invested=True).invested == Decimal("300.00")
    assert walk(q, until="2027-06").invested == Decimal("100.00")
