"""§5.5 sale() against sale.yaml (docs/phases/phase-4.md D88, D89, §10.4)."""

from decimal import Decimal

import pytest
from fixture_models_phase4 import SaleFixture, load
from test_perf_irr import assert_rate

from hausbuch.rules.performance.sale import SaleFacts, sale
from hausbuch.rules.tax import registry
from hausbuch.schemas.performance import OwnerTaxProfile
from hausbuch.schemas.tax import TaxOwner

SALE = load("sale")
assert isinstance(SALE, SaleFixture)
RATES = {"cagr", "irr"}


def facts_of(case) -> SaleFacts:
    f = case.facts
    return SaleFacts(
        sale_year=f.sale_year,
        purchase_date=f.purchase_date,
        tax_free_from=f.tax_free_from,
        purchase_price=f.purchase_price,
        cost_rows=f.cost_rows,
        loan_principal=f.loan_principal,
        value=f.value,
        selling_costs_rate=f.selling_costs_rate,
        afa_claimed_building=f.afa_claimed,
        afa_claimed_movable=f.afa_claimed_movable,
        loan_balance=f.loan_balance,
        zve_base=f.zve_base,
        result_sale_year=f.result_sale_year,
        shares=[TaxOwner(owner_id=o.owner_id, share=o.share) for o in f.owners],
        profiles=[
            OwnerTaxProfile(owner_id=o.owner_id, church_tax=o.church_tax, tax_state=o.tax_state)
            for o in f.owners
        ],
        cash_flows=f.cash_flows,
        freigrenze=registry.rule_set_or_latest(f.sale_year).speculation_freigrenze,
    )


def assert_sale(got, want, where: str) -> None:
    """Every field exactly except `sources`; CAGR and IRR within ±0.000001 (§10.4)."""
    exclude = {
        "sources": True,
        "bridge": {"__all__": {"sources"}},
        "flows": {"__all__": {"sources"}},
    }
    g = got.model_dump(exclude=exclude | {k: True for k in RATES})
    w = want.model_dump(exclude=exclude | {k: True for k in RATES})
    assert g == w, where
    for key in RATES:
        assert_rate(getattr(got, key), getattr(want, key), f"{where}.{key}")


@pytest.mark.rule
@pytest.mark.parametrize("case", SALE.cases, ids=lambda c: c.id)
def test_sale_fixture(case):
    got = sale(facts_of(case), registry.tariff(case.facts.sale_year))
    assert_sale(got, case.expect, case.id)


def test_an_override_taxes_the_gain_at_the_rate():
    case = next(c for c in SALE.cases if c.id == "scenario_c_sale")
    f = facts_of(case)
    got = sale(
        SaleFacts(**{**f.__dict__, "override_rate": Decimal("0.42")}),
        registry.tariff(2030),
    )
    assert got.sale_tax == Decimal("19766.95")  # 47,064.16 × 0.42, half-up


def test_basis_additions_lower_the_gain():
    case = next(c for c in SALE.cases if c.id == "scenario_c_sale")
    f = facts_of(case)
    got = sale(
        SaleFacts(**{**f.__dict__, "basis_additions": Decimal("4000.00")}),
        registry.tariff(2030),
    )
    assert got.gain == Decimal("43064.16")


def test_the_freigrenze_is_the_facts_own():
    """D93: the Freigrenze comes from the sale year's rule set, not a constant. Under the
    600.00 of the years before 2024, gain_below_freigrenze's 900.00 per spouse is taxed."""
    case = next(c for c in SALE.cases if c.id == "gain_below_freigrenze")
    f = facts_of(case)
    assert f.freigrenze == Decimal("1000")
    got = sale(SaleFacts(**{**f.__dict__, "freigrenze": Decimal("600")}), registry.tariff(2027))
    assert [o.below_freigrenze for o in got.gain_per_owner] == [False, False]
    assert not got.freigrenze_applied and got.sale_tax > 0
