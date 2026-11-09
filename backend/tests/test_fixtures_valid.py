"""Every rule fixture conforms to the spec schemas. Runs before any rule exists."""

from decimal import Decimal
from typing import get_args

import fixture_models as phase1
import fixture_models_phase2 as phase2
import fixture_models_phase3 as phase3
import fixture_models_phase4 as phase4
import pytest
from fixture_models import RentFixture
from pydantic import ValidationError

from hausbuch.schemas.allocation import (
    MatchingRule,
    MatchingRuleCreate,
    RoutingDecision,
    RuleMatcher,
)
from hausbuch.schemas.common import Money
from hausbuch.schemas.events import EVENT_CATALOG, EventType, RuleRecorded

PHASES = {"phase1": phase1, "phase2": phase2, "phase3": phase3, "phase4": phase4}
FIXTURE_FILES = [(p, name) for p, mod in PHASES.items() for name in sorted(mod.MODELS)]


@pytest.mark.parametrize("phase", sorted(PHASES))
def test_every_fixture_file_has_a_model(phase):
    mod = PHASES[phase]
    assert {p.stem for p in mod.FIXTURES.glob("*.yaml")} == set(mod.MODELS)


@pytest.mark.parametrize(("phase", "name"), FIXTURE_FILES)
def test_fixture_matches_schemas(phase, name):
    PHASES[phase].load(name)


@pytest.mark.parametrize(("phase", "name"), FIXTURE_FILES)
def test_case_ids_unique(phase, name):
    data = PHASES[phase].raw(name)
    for key, value in data.items():
        if isinstance(value, list) and value and isinstance(value[0], dict) and "id" in value[0]:
            ids = [c["id"] for c in value if "seq" not in c]
            assert len(ids) == len(set(ids)), f"duplicate case id in {name}.{key}"


# docs/phases/phase-3.md §13 and §14: the cases each phase 3 fixture must carry (a file may
# carry more). A case renamed or dropped here is a spec change.
PHASE3_CASES = {
    "tax_year": {"due_missing_no_shift"},
    "afa": {
        "etw_1998_handover_july",
        "build_1920_rate_2_5",
        "build_2023_rate_3",
        "handover_january_full_year",
        "handover_december_one_twelfth",
        "nebenkosten_in_basis",
        "herstellung_addition_2027",
        "no_split_empty_line",
        "cost_row_unpaid_warning",
        "acquisition_cost_without_row_not_added",
        "purchase_price_mismatch_warning",
        # D79: a shorter actual useful life (§7 Abs. 4 S. 2 EStG)
        "useful_life_from_purchase",
        "useful_life_with_addition",
        "useful_life_from_later_year",
        "useful_life_sale_year",
        # D80: a movable asset (the Einbauküche) bought with the flat
        "movable_in_price_reduces_building",
        "movable_in_price_with_useful_life",
    },
    "movable_asset": {
        "kitchen_in_price_from_purchase",
        "kitchen_estimate_without_evidence",
        "kitchen_sold_before_ten_years",
    },
    "anschaffungsnah": {
        "under_limit_14_percent",
        "crossing_in_second_year",
        "equal_is_not_exceeded",
        "outside_window_not_counted",
        "estimated_net_without_invoice",
        "excluded_annual_maintenance",
        "herstellung_counted",
        "herstellung_extension_excluded",
        "herstellung_counted_not_re_added",
        "window_dates",
        "zero_amount_is_not_evidence",
        "performed_in_window_paid_after",
        "no_period_booked_after_window_warns",
        "movable_in_price_reduces_limit",  # D80
    },
    "erhaltungsaufwand": {
        "repairs_full_deduction",
        "renovation_elected_erhaltung",
        "renovation_elected_herstellung",
        "renovation_undecided_excluded",
        "spread_three_years",
        "spread_sale_in_second_year",
        "spread_withdrawn_back_to_full",
    },
    "schuldzinsen": {
        "interest_only_phase_plus_annuities",
        "certificate_matches",
        "certificate_mismatch_0_56",
        "january_annuity_moved",
        "financing_costs_before_handover",
        "two_loans_two_einzelangaben",
    },
    "hausgeld": {
        "six_months_split",
        "ruecklage_tracked_not_deducted",
        "ruecklage_spent_confirmed_350",
        "abrechnung_pending_provisional",
        "settlement_backpayment_next_year",
    },
    "umlagen": {
        "rent_and_prepayments_six_months",
        "settlement_backpayment_positive",
        "refund_negative",
        "pauschale_is_umlagen",
        "included_rent_on_z13",
        "all_included_ticks_z24",
        "mixed_terms_warns",
        "two_rooms_one_flat_row",
        "mfh_two_units_two_rows",
        "living_area_missing_falls_back",
        "other_income_with_review_warning",
        "deposit_not_income",
    },
    "verbilligte_vermietung": {
        "ratio_103_nothing",
        "ratio_62_prognosis",
        "ratio_45_cut_55",
        "no_market_rent_info",
        "market_rent_valid_from_mid_year",
    },
    "vorab": {
        "seller_forwarded_rent_excluded",
        "interest_before_handover_deducted",
        "purchase_date_corrected_later",
    },
    "spekulation": {"ten_years_plus_one_day", "leap_day_contract"},
    "checklist_phase3": {
        "cost_receipt_missing_for_repair",
        "cost_receipt_present",
        "cost_receipt_only_for_named_categories",
        "market_rent_missing_for_let_unit",
        "market_rent_present",
        "market_rent_withdrawn_is_missing",
        "zero_amount_is_not_evidence",
        "phase1_facts_unchanged",
    },
    "anlage_v_2026": {
        "case_a",
        "case_b_renovation_undecided",
        "pre_handover_as_rent",
        "export_roundtrip",
    },
}


def test_phase3_cases_cover_the_spec():
    assert set(PHASE3_CASES) == set(phase3.MODELS)
    for name, required in PHASE3_CASES.items():
        ids = {c.id for c in phase3.load(name).cases}
        assert required <= ids, (name, required - ids)


# docs/phases/phase-4.md §5 and §10: the cases each phase 4 fixture must carry (the names the
# spec gives; a file may carry more). A case renamed or dropped here is a spec change.
PHASE4_CASES = {
    "tariff": {
        "single_zone_boundaries",
        "joint_60k_90k_200k",
        "splitting_floors_the_half",
        "soli_below_limit_zero",
        "soli_mitigation_42000",
        "soli_full_50000",
        "soli_floors_cents",
        "church_8pct_on_17670",
        "church_one_member_half",
        "church_two_states",
    },
    "marginal_rate": {
        "deductions_default_60k",
        "deductions_default_45k",
        "deductions_capped_at_bbg",
        "household_81993",
        "effect_negative_result",
        "effect_positive_result",
        "effect_zero_shows_slope",
        "bescheid_replaces_estimate",
        "override_rate",
        "carried_forward_2028",
        "no_inputs_none",
        "tariff_reused_for_2030",
    },
    "actuals": {
        "anlage_v_2026_case_a",
        "current_year_partial",
        "no_income_inputs",
        "included_tenancy_no_umlagen",
        "purchase_flows_not_cash",
        "flat_rate_costs_above_pauschale",  # D91, amended 9 Nov 2026
    },
    "projection": {
        "baseline_no_growth_tax_free_sale",
        "growth_and_rate_switch",
        "useful_life_and_sale_inside_ten_years",
        "useful_life_control_sale_2037",  # §10.3: C's control
        "upkeep_excluded_from_15pct",
        "rent_growth_from_second_year",
        "vacancy_two_months",
    },
    "sale": {
        "scenario_c_sale",  # §5.5: the §10.3 scenario C sale
        "gain_below_freigrenze",
        "gain_at_freigrenze",
        "loss_not_refunded",
        "tax_free_after_ten_years",
    },
    "irr": {
        "two_flows_10pct",
        "three_flows",
        "property_like",
        "no_sign_change",
        "cagr_ten_and_a_half_years",  # §5.5: E₀ 85,000, profit 20,000, 10.5 years
    },
}


def test_phase4_cases_cover_the_spec():
    assert set(PHASE4_CASES) == set(phase4.MODELS)
    for name, required in PHASE4_CASES.items():
        ids = {c.id for c in phase4.load(name).cases}
        assert required <= ids, (name, required - ids)


@pytest.mark.parametrize(
    ("name", "path", "slip"),
    [
        ("actuals", ("expect", "net_cash"), "-9554.48"),
        ("actuals", ("expect", "flat_rate_in"), "1080.00"),  # a prepayment tenancy's gap is None
        ("marginal_rate", ("expect", "effect", "effect"), "-2687.05"),
        ("projection", ("expect", "years", 0, "equity"), "65220.77"),
        ("projection", ("expect", "sale", "afa_claimed"), "49140.01"),
        ("sale", ("expect", "profit"), "-42151.77"),
    ],
)
def test_phase4_fixtures_check_their_arithmetic(name, path, slip):
    """A one-cent slip in a hand-filled figure fails on load, not in a rule test later."""
    import copy

    data = copy.deepcopy(phase4.raw(name))
    case = next(c for c in data["cases"] if "expect" in c and _has(c, path))
    node = case
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = slip
    with pytest.raises(ValidationError):
        phase4.MODELS[name].model_validate(data)


def _has(case: dict, path: tuple) -> bool:
    node = case
    for key in path:
        try:
            node = node[key]
        except (KeyError, IndexError, TypeError):
            return False
    return node is not None


def _case(name: str, case_id: str):
    return next(c for c in phase4.load(name).cases if c.id == case_id)


def test_phase4_fixtures_carry_the_spec_figures():
    """The figures docs/phases/phase-4.md states (§5.1–§5.5, §10.3), read off the fixtures. The
    rest of each table was filled by the same arithmetic; these pin the spec's own numbers."""
    D = Decimal
    a = _case("projection", "baseline_no_growth_tax_free_sale").expect
    b = _case("projection", "growth_and_rate_switch").expect
    c = _case("projection", "useful_life_and_sale_inside_ten_years").expect
    a27, a28 = a.years[0], a.years[1]
    assert a27.rent_in + a27.umlagen_in == D("12360.00")
    assert (a27.taxable_result, a27.tax_effect.effect, a27.tax_effect.effective_rate) == (
        D("-4446.81"),
        D("-1518.48"),
        D("34.1"),
    )
    assert (a27.cash_out, a27.net_cash, a27.equity) == (
        D("17038.80"),
        D("-6197.28"),
        D("65220.76"),
    )
    assert (a28.taxable_result, a28.tax_effect.effect, a28.net_cash) == (
        D("-4314.80"),
        D("-1473.12"),
        D("-6151.92"),
    )
    assert not a.sale.taxable and a.sale.sale_tax == 0
    same = {"value", "equity"}  # growth starts in the second year, value growth in the first
    assert b.years[0].model_dump(exclude=same) == a27.model_dump(exclude=same)
    b28 = b.years[1]
    assert (b28.rent_in, b28.umlagen_in, b28.taxable_result, b28.tax_effect.effect) == (
        D("10404.00"),
        D("2203.20"),
        D("-4144.38"),
        D("-1414.80"),
    )
    assert (b28.cash_out, b28.net_cash, b28.value, b.years[4].interest) == (
        D("17139.58"),
        D("-5947.18"),
        D("312120.00"),
        D("8466.77"),
    )
    assert {y.afa for y in c.years} == {D("11031.43")}
    c30 = c.years[-1]
    assert (c30.taxable_result, c30.tax_effect.effect, c30.net_cash) == (
        D("-10387.92"),
        D("-3488.40"),
        D("-8167.20"),
    )
    s = c.sale
    assert (s.value, s.selling_costs, s.afa_claimed, s.gain, s.sale_tax, s.net_proceeds) == (
        D("337652.64"),
        D("12054.20"),
        D("46465.72"),
        D("47064.16"),
        D("17422.56"),
        D("85343.67"),
    )
    e = _case("actuals", "anlage_v_2026_case_a").expect
    assert (e.cash_out, e.taxable_result, e.tax_effect.effect, e.net_cash, e.invested) == (
        D("13897.43"),
        D("-7948.66"),
        D("-2687.04"),
        D("-9554.47"),
        D("320800.00"),
    )
    effect = _case("marginal_rate", "effect_negative_result").expect.effect
    assert (effect.effect, effect.effective_rate) == (D("-2808.00"), D("35.3"))
    irr = {x.id: x.expect for x in phase4.load("irr").cases}
    assert (irr["two_flows_10pct"], irr["three_flows"], irr["property_like"]) == (
        D("0.100000"),
        D("0.132326"),
        D("0.052471"),
    )
    assert irr["no_sign_change"] is None
    assert irr["cagr_ten_and_a_half_years"] == D("0.020329")


def test_rent_rows_validate():
    fx = phase1.load("rent_at")
    assert isinstance(fx, RentFixture)
    fx.entries(fx.rows)
    fx.entries(fx.tie_break.rows)


def test_event_catalog_is_complete():
    assert set(EVENT_CATALOG) == set(EventType)


def test_routing_table_agrees_with_thresholds():
    """review_routing.yaml's per-category decisions at confidence 0.9 follow thresholds.yaml."""
    table = phase2.load("thresholds").table()
    for row in phase2.load("review_routing").per_category:
        threshold = table[row.category]
        auto = threshold is not None and Decimal("0.9") >= threshold
        assert (row.decision is RoutingDecision.AUTO_CONFIRM) == auto, row.category


def _types(annotation) -> set:
    """annotation and every type nested in it (Optional, unions, Annotated, list[...])."""
    found = {annotation}
    for arg in get_args(annotation):
        found |= _types(arg)
    return found


def test_types_sees_through_optional_money():
    assert Decimal in _types(Money | None)
    assert Decimal in _types(list[Money])


def test_rule_stores_no_amount():
    """D25: no field of a learned rule, its matcher, its read shape or its events can hold
    money, however the type is wrapped (Money | None, Annotated, a list). same_amount_only
    (D49) is a flag: the amount it compares with is read from the ledger at match time."""
    for model in (RuleMatcher, MatchingRuleCreate, MatchingRule, RuleRecorded):
        for name, field in model.model_fields.items():
            flag = name == "same_amount_only" and field.annotation is bool
            assert "amount" not in name or name == "amount_sign" or flag, (model.__name__, name)
            assert Decimal not in _types(field.annotation), (model.__name__, name)


def test_rule_event_holds_no_pattern():
    """rule.created / .disabled keep no free text: every str field is held to a fixed shape
    (an IBAN hash), so a name or purpose pattern cannot be written into the event log."""
    base = {
        "split_method": "single_category",
        "category": "insurance",
        "has_name_pattern": True,
        "has_purpose_pattern": True,
    }
    RuleRecorded.model_validate(base)
    text_fields = [n for n, f in RuleRecorded.model_fields.items() if str in _types(f.annotation)]
    assert text_fields == ["counterparty_iban_hmac"]
    for name in text_fields:
        with pytest.raises(ValidationError):
            RuleRecorded.model_validate(base | {name: "allianz|versicherung"})
    rule = MatchingRuleCreate(
        matcher={"counterparty_name_pattern": "allianz", "purpose_pattern": "vers-nr"},
        split_method="single_category",
        category="insurance",
    )
    event = RuleRecorded.of(rule)
    assert event.has_name_pattern and event.has_purpose_pattern
    assert "allianz" not in event.model_dump_json() and "vers-nr" not in event.model_dump_json()


def test_only_a_disabled_rule_reads_with_an_empty_matcher():
    """phase-2.md D42 (3), migration 0008: a tenant erasure empties a disabled rule's
    matcher. A new rule, and an enabled one, must still match on something; an empty matcher
    never hits a transaction."""
    from datetime import UTC, date, datetime
    from uuid import uuid4

    from hausbuch.rules.matching import rule_hits
    from hausbuch.schemas.allocation import MatchTransaction

    for matcher in ({}, {"amount_sign": "credit"}):
        with pytest.raises(ValidationError, match="a rule matches on"):
            MatchingRuleCreate(
                matcher=matcher, split_method="single_category", category="insurance"
            )
        now = datetime(2026, 11, 7, tzinfo=UTC)
        stored = {
            "id": uuid4(),
            "workspace_id": uuid4(),
            "created_at": now,
            "updated_at": now,
            "matcher": matcher,
            "split_method": "single_category",
            "category": "insurance",
        }
        assert not MatchingRule.model_validate(stored | {"enabled": False}).enabled
        with pytest.raises(ValidationError, match="a rule matches on"):
            MatchingRule.model_validate(stored | {"enabled": True})
        tx = MatchTransaction(id=uuid4(), booking_date=date(2026, 11, 2), amount="1400.00")
        assert rule_hits(RuleMatcher.model_validate(matcher), tx) is False
