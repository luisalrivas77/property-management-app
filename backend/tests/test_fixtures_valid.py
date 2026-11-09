"""Every rule fixture conforms to the spec schemas. Runs before any rule exists."""

from decimal import Decimal
from typing import get_args

import fixture_models as phase1
import fixture_models_phase2 as phase2
import fixture_models_phase3 as phase3
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

PHASES = {"phase1": phase1, "phase2": phase2, "phase3": phase3}
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
