"""The per-year rule sets load and map every category (docs/phases/phase-3.md §4).

The 2025 Zeilen and Kennzahlen below were read from the official form "080 – Anlage V
(2025)", version 2025AnlV101NET – September 2025, on 9 Nov 2026. A change to them is a data
PR against a newly read form, never a fix to make a test pass.
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from hausbuch.rules.errors import TAX_NO_RULE_SET, RuleError
from hausbuch.rules.tax.registry import available_years, rule_set
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.tax import DRAFT_LINE_KEYS, FormStatus, RuleSet

# key: (zeile_from, zeile, kennzahl), from the printed 2025 form.
FORM_2025 = {
    "header.address": (4, 5, None),
    "header.aktenzeichen": (None, 6, None),
    "header.contract_date": (None, 7, None),
    "header.transfer_date": (None, 7, None),
    "header.completed": (None, 7, None),
    "header.sold": (None, 8, "53"),
    "header.living_area": (None, 11, "54"),
    "income.rent_units": (13, 14, None),
    "income.rent_total": (None, 15, "01"),
    "income.umlagen": (None, 20, "04"),
    "income.umlagen_settlement": (None, 21, "11"),
    "income.umlagen_not_agreed": (None, 24, "13"),
    "income.other": (29, 31, "08"),
    "income.total": (None, 32, None),
    "wk.afa": (33, 35, "30"),
    "wk.afa_movable": (42, 45, "60"),  # D80, read from page 2 (2025AnlV102NET) on 9 Nov 2026
    "wk.interest": (46, 48, "33"),
    "wk.financing_costs": (49, 51, "34"),
    "wk.maintenance": (None, 55, "36"),
    "wk.maintenance_spread_total": (None, 57, "57"),
    "wk.maintenance_spread_current": (58, 60, "38"),
    "wk.maintenance_spread_prior_4": (61, 63, "39"),  # "aus 2021" on the 2025 form
    "wk.maintenance_spread_prior_3": (64, 66, "40"),
    "wk.maintenance_spread_prior_2": (67, 69, "41"),
    "wk.maintenance_spread_prior_1": (70, 72, "42"),  # "aus 2024"
    "wk.allocable_costs": (73, 75, "52"),
    "wk.non_allocable_costs": (76, 78, "48"),
    "wk.other": (80, 82, "49"),
    "wk.total": (None, 83, None),
    "result.surplus": (None, 85, None),
    "result.owner_a": (None, 86, "20"),
    "result.owner_b": (None, 86, "21"),
    "reduced_rent.cut_percent": (None, 87, "50"),
}

# Category → line (§4.2); None = not on the form.
CATEGORY_LINES = {
    C.RENT_COLD: "income.rent_units",
    C.OPERATING_COSTS_PREPAYMENT: "income.umlagen",
    C.OPERATING_COSTS_SETTLEMENT: "income.umlagen_settlement",
    C.OTHER_INCOME: "income.other",
    C.LOAN_INTEREST: "wk.interest",
    C.LOAN_FEES: "wk.financing_costs",
    C.REPAIRS: "wk.maintenance",
    C.RENOVATION: "wk.maintenance",
    C.HAUSGELD_UMLAGEFAEHIG: "wk.allocable_costs",
    C.GRUNDSTEUER: "wk.allocable_costs",
    C.INSURANCE: "wk.allocable_costs",
    C.ELECTRICITY: "wk.allocable_costs",  # phase 4 D92
    C.HAUSGELD_VERWALTUNG: "wk.non_allocable_costs",
    C.PROPERTY_MANAGEMENT: "wk.non_allocable_costs",
    C.BANK_FEES: "wk.non_allocable_costs",
    C.PROFESSIONAL_FEES: "wk.other",
    C.LOAN_PRINCIPAL: None,
    C.HAUSGELD_RUECKLAGE: None,
    C.DEPOSIT_IN: None,
    C.DEPOSIT_OUT: None,
    C.OWNER_TRANSFER_IN: None,
    C.OWNER_TRANSFER_OUT: None,
    C.LOAN_DISBURSEMENT: None,
    C.PURCHASE_PRICE: None,
    C.ACQUISITION_COST: None,
    C.OTHER_EXPENSE: None,
    C.UNCATEGORIZED: None,
    # Phase 6: every household and depot code, never on the form (D159, D161, D165).
    C.HH_SALARY: None,
    C.HH_CHILD_BENEFIT: None,
    C.HH_TAX_REFUND: None,
    C.HH_CAPITAL_INCOME: None,
    C.HH_OTHER_INCOME: None,
    C.HH_HOUSING: None,
    C.HH_GROCERIES: None,
    C.HH_FAMILY_CHILD: None,
    C.HH_MOBILITY: None,
    C.HH_INSURANCE: None,
    C.HH_SUBSCRIPTIONS: None,
    C.HH_LEISURE_TRAVEL: None,
    C.HH_HEALTH: None,
    C.HH_OTHER: None,
    C.HH_DONATION: None,
    C.HH_HOUSEHOLD_SERVICES: None,
    C.HH_CHILDCARE: None,
    C.HH_PROPERTY_COST: None,
    C.HH_TAX_PAYMENT: None,
    C.INTERNAL_TRANSFER_IN: None,
    C.INTERNAL_TRANSFER_OUT: None,
    C.HH_CASH: None,
    C.HH_CREDIT_CARD: None,
    C.HH_INTEREST_FEES: None,
    C.HH_PERSONAL_CARE: None,
    C.HH_CLOTHING: None,
    C.HH_DINING: None,
    C.HH_HOME: None,
    C.HH_ELECTRONICS: None,
    C.HH_ONLINE_SHOPPING: None,
    C.HH_FEES: None,
    C.HH_GIFTS: None,
    C.HH_FAMILY_TRANSFERS: None,
    C.HH_TELECOM: None,
    C.HH_WORK_EDUCATION: None,
    C.HH_SPORT: None,
    C.HH_TRAVEL: None,
    C.HH_INVESTMENT: None,
    C.HH_REFUND: None,
    C.HH_FUEL: None,
    C.SECURITY_PURCHASE: None,
    C.SECURITY_SALE: None,
    C.DISTRIBUTION: None,
    C.CAPITAL_INCOME_TAX: None,
    C.DEPOT_FEE: None,
}


def test_the_rule_set_years_are_2025_and_2026():
    assert available_years() == (2025, 2026)


@pytest.mark.parametrize("year", [2025, 2026])
def test_each_rule_set_loads_and_is_its_year(year):
    rs = rule_set(year)
    assert rs.tax_year == year
    assert rs.version == f"{year}.1"
    assert rule_set(year) is rs  # cached


def test_a_year_without_a_file_raises_no_rule_set():
    with pytest.raises(RuleError) as err:
        rule_set(2024)
    assert err.value.code == TAX_NO_RULE_SET


@pytest.mark.parametrize("year", [2025, 2026])
def test_every_category_maps_to_a_line_or_explicitly_to_none(year):
    mapping = rule_set(year).form
    assert set(mapping.category_lines) == set(C)  # none left implicit
    assert dict(mapping.category_lines) == CATEGORY_LINES
    assert mapping.tracked == [C.LOAN_PRINCIPAL, C.HAUSGELD_RUECKLAGE]
    assert mapping.open == [C.OTHER_EXPENSE, C.UNCATEGORIZED]


@pytest.mark.parametrize("year", [2025, 2026])
def test_every_line_the_draft_writes_is_mapped(year):
    assert set(rule_set(year).form.lines) == set(DRAFT_LINE_KEYS)


def test_the_2025_mapping_is_the_published_form():
    form = rule_set(2025).form
    assert form.status is FormStatus.PUBLISHED
    assert form.form_year == 2025
    assert form.version_stamp == "2025AnlV101NET – September 2025"
    got = {k: (line.zeile_from, line.zeile, line.kennzahl) for k, line in form.lines.items()}
    assert got == FORM_2025


def test_the_2026_mapping_is_a_provisional_copy_of_2025():
    form_2025, form_2026 = rule_set(2025).form, rule_set(2026).form
    assert form_2026.status is FormStatus.PROVISIONAL
    assert form_2026.form_year == 2025
    assert form_2026.version_stamp == form_2025.version_stamp
    assert form_2026.lines == form_2025.lines
    assert form_2026.category_lines == form_2025.category_lines


@pytest.mark.parametrize("year", [2025, 2026])
def test_the_rates_and_thresholds(year):
    rs = rule_set(year)
    assert rs.afa_rate(1924) == Decimal("0.025")  # completed before 1 Jan 1925
    assert rs.afa_rate(1925) == Decimal("0.02")
    assert rs.afa_rate(2022) == Decimal("0.02")
    assert rs.afa_rate(2023) == Decimal("0.03")  # completed after 31 Dec 2022
    assert rs.short_period_days == 10
    assert rs.anschaffungsnah_share == Decimal("0.15")
    assert rs.anschaffungsnah_years == 3
    assert (rs.spread_82b_min_years, rs.spread_82b_max_years) == (2, 5)
    assert (rs.reduced_rent_split, rs.reduced_rent_full) == (Decimal("0.50"), Decimal("0.66"))
    assert rs.speculation_years == 10
    assert rs.speculation_freigrenze == Decimal("1000")  # §23 Abs. 3 S. 5 from VZ 2024 (D93)


def test_income_rounds_down_and_costs_round_up():
    """D60: whole euros in the taxpayer's favour; totals come from the rounded lines."""
    for line in rule_set(2025).form.lines.values():
        expected = {"income": "down", "cost": "up", "afa": "up"}.get(line.kind, "none")
        assert line.rounding == expected, line.key


def _raw_2025() -> dict:
    return rule_set(2025).model_dump(mode="json")


def test_a_mapping_missing_a_category_fails_to_load():
    data = _raw_2025()
    del data["form"]["category_lines"]["bank_fees"]
    with pytest.raises(ValidationError, match="form_mapping.incomplete: .*bank_fees"):
        RuleSet.model_validate(data)


def test_a_mapping_missing_a_draft_line_fails_to_load():
    data = _raw_2025()
    del data["form"]["lines"]["wk.maintenance_spread_prior_4"]
    with pytest.raises(ValidationError, match="form_mapping.incomplete: .*spread_prior_4"):
        RuleSet.model_validate(data)


def test_afa_rates_with_a_gap_fail_to_load():
    data = _raw_2025()
    data["afa_rates"][1]["completed_from"] = 1926
    with pytest.raises(ValidationError, match="contiguous"):
        RuleSet.model_validate(data)


def test_a_published_mapping_must_be_the_years_own_form():
    data = rule_set(2026).model_dump(mode="json")
    data["form"]["status"] = "published"
    with pytest.raises(ValidationError, match="published form mapping"):
        RuleSet.model_validate(data)
