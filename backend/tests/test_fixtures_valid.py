"""Every rule fixture conforms to the spec schemas. Runs before any rule exists."""

from decimal import Decimal
from typing import get_args

import fixture_models as phase1
import fixture_models_phase2 as phase2
import fixture_models_phase3 as phase3
import fixture_models_phase4 as phase4
import fixture_models_phase4b as phase4b
import fixture_models_phase5 as phase5
import fixture_models_phase6 as phase6
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

PHASES = {
    "phase1": phase1,
    "phase2": phase2,
    "phase3": phase3,
    "phase4": phase4,
    "phase4b": phase4b,
    "phase5": phase5,
    "phase6": phase6,
}
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
        "cost_row_with_bill_unpaid_no_warning",  # D143: the draft asks for the proof
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
        # phase 4b D111 (docs/phases/phase-4b.md §5.12): privately paid rental costs
        "private_costs_in_other",
        "travel_frequency_warning",
        # phase 4b D134: a privately paid financing cost on the Geldbeschaffungskosten line
        "private_financing_cost_in_financing",
        # phase 2 D135: a private cost whose bill a bank line paid is not counted twice
        "private_cost_paid_from_account_not_counted",
        # D143: every counted bill has its proof of payment
        "notary_bill_proof_linked_no_todo",
        "notary_bill_proof_amount_unread_counts",
        "private_repair_proof_matches_no_todo",
        "private_repair_proof_amount_mismatch",
        # D149: a stored proof offered on the to-do of the bill it fits
        "private_repair_unlinked_proof_offered_on_its_todo",
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
        # phase 4b D98 (docs/phases/phase-4b.md §5.9, §11): the employment and household contexts
        "household_2026_contexts",
        "deductions_override_beats_employment",
        "payslip_gross_source",
        "plain_tariff_without_context",
    },
    "actuals": {
        "anlage_v_2026_case_a",
        "current_year_partial",
        "no_income_inputs",
        "included_tenancy_no_umlagen",
        "purchase_flows_not_cash",
        "flat_rate_costs_above_pauschale",  # D91, amended 9 Nov 2026
        "flat_rate_electricity_from_handover",  # D92: electricity and the Pauschale months
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
        "kitchen_afa_added_back",  # D94: the kitchen's AfA added back at a taxable sale
    },
    "irr": {
        "two_flows_10pct",
        "three_flows",
        "property_like",
        "no_sign_change",
        "cagr_ten_and_a_half_years",  # §5.5: E₀ 85,000, profit 20,000, 10.5 years
    },
    # D155 (6): the Nebenkostenpauschale rise test
    "flat_rate_rise": {
        "hausgeld_rise_three_rooms",
        "rise_exactly_ten_no_todo",
        "rise_ten_01_todo",
        "fall_lowers",
        "electricity_rise_no_todo",
        "pauschale_changed_by_rent_row",
        "lease_before_first_payment",
        "grundsteuer_from_schedule",
        "grundsteuer_known_later_no_rise",  # D155 (2): the first Bescheid stands for before it
        "grundsteuer_from_twelve_months_of_lines",
    },
    # D155 (7): the letter's figures
    "flat_rate_letter": {
        "rise_sent_on_the_15th",
        "rise_sent_on_the_1st",  # still the month after next
        "rise_just_above_ten_sent_later",  # the start runs from the sending date
        "fall_from_the_date_the_cost_fell",
        "no_letter_below_the_threshold",
        "no_letter_for_electricity",
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
        ("actuals", ("expect", "flat_rate_months"), 12),  # D92: ÷ 12, not the months in force
        ("marginal_rate", ("expect", "effect", "effect"), "-2687.05"),
        ("projection", ("expect", "years", 0, "equity"), "65220.77"),
        ("projection", ("expect", "sale", "afa_claimed"), "49140.01"),
        ("sale", ("expect", "profit"), "-42151.77"),
        ("sale", ("expect", "afa_claimed_movable"), "1300.01"),  # D94: the parts add up
        ("flat_rate_rise", ("expect", "tenancies", 0, "rise"), "15.01"),  # D155: the share
        ("flat_rate_rise", ("expect", "levels", 0, "betrkv"), "170.01"),  # Σ of the rates
        ("flat_rate_rise", ("expect", "levels", 0, "rates", 2, "monthly"), "20.01"),  # ÷ 12
        ("flat_rate_letter", ("expect", "new_pauschale"), "172.51"),  # D155 (7): old + rise
        ("flat_rate_letter", ("expect", "starts_on"), "2027-03-01"),  # the month after next
        ("flat_rate_letter", ("expect", "reasons", 0, "now"), "195.01"),  # the check's level
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


# docs/phases/phase-4b.md §11: the cases each phase 4b fixture must carry (a file may carry
# more). A case renamed or dropped here is a spec change.
PHASE4B_CASES = {
    "payslip": {
        "july_projection",
        "january_zero_remaining",
        "december_no_projection",
        "missing_month_warning",
        "no_ytd_low_confidence",
        "certificate_replaces_projection",
        "two_employers_summed",
    },
    "commute": {
        "car_25km_220_days",
        "public_transport_capped",
        "ticket_costs_exceed",
        "mixed_car_and_train",
        "employer_benefit_offset",
        "no_commute_with_subsidy",
        "two_routes_in_year",
        "km_floored",
        "four_routes_warning",
    },
    "home_office": {
        "days_90_owner_b",
        "days_210_at_cap",
        "days_250_capped",
        "overlap_warning",
        "with_work_room_error",
        "with_work_room_nine_months_warning",
        "zero_days_no_line",
    },
    "work_room": {
        "actual_beats_flat",
        "flat_beats_actual",
        "nine_months",
        "prefer_flat",
        "room_only_costs_added",
    },
    "equipment": {
        "laptop_1500_full_in_year",
        "monitor_gwg_70pct",
        "phone_2025_slice_2026",
        "phone_disposed_2027",
        "subscription_half_share",
        "desk_afa_156_months",
        "chair_bought_december",
        "net_typed_wins",
        "other_without_life_error",
    },
    "telecom": {"flat_under_cap", "capped", "reimbursed", "six_months"},
    "anlage_n": {
        "owner_a_remote_work_room",
        "owner_b_commute_day_rate",
        "pauschbetrag_wins",
        "union_beside_allowance",
        "allowance_capped_at_pay",
        "estimate_from_payslips",
        "two_certificates_one_year",
        "church_mismatch_warning",
        "tax_class_6_warning",
        "work_room_and_day_rate_error",
        "first_workplace_check_warning",
        "telecom_added",
    },
    "vorsorge": {
        "owner_a_60k",
        "owner_b_32k",
        "low_pay_15k_av_counts",
        "low_pay_other_insurance",
        "other_insurance_no_effect",
        "above_bbg_120k",
        "from_payslip_projection",
        "override_private",
        "no_krankengeld_no_cut",
    },
    "children": {
        "born_march_ten_months",
        "born_november_two_months",
        "born_january_full_year",
        "two_children",
        "kindergeld_wins_62k",
        "allowances_win_200k",
        "soli_on_est_with",
        "soli_after_35a_credit",
        "child_over_18_warning",
        "no_context_equals_total_tax",
    },
    "return_estimate": {
        "household_2026",
        "no_benefits_no_child",
        "allowances_win_high_income",
        "back_payment",
        "with_35a_credit",
        "35a_capped",
        "35a_cash_refused",
        "donation_within_cap",
        "donation_over_cap",
        "childcare_2027",
        "childcare_turns_14",
        "estimate_from_payslips_status",
        "benefit_letter_pending",
        "church_tax_as_special_expense",
        "benefit_double_warning",
        "mutterschutz_pay_stop",
        "filing_required_rental_positive",
        "filing_optional",
        "questions_open",
        "filing_deadline_next_working_day",
        "filing_required_factor",
    },
    "questionnaire": {
        "prefill_copy_commute",
        "prefill_reconfirm_phone",
        "durable_kinds_not_copied",
        "not_applicable_recorded",
        "progress_count",
        "removed_key_not_prefilled",
        "new_key_open",
        "reminder_on_sheet",
        "extraordinary_threshold_household",
        "default_yes_account_fee",
    },
}


def test_phase4b_cases_cover_the_spec():
    assert set(PHASE4B_CASES) == set(phase4b.MODELS)
    for name, required in PHASE4B_CASES.items():
        ids = {c.id for c in phase4b.load(name).cases}
        assert required <= ids, (name, required - ids)


@pytest.mark.parametrize(
    ("name", "case_id", "path", "slip"),
    [
        ("payslip", "july_projection", ("expect", "projected", 0, "income_tax"), "9200.00"),
        ("commute", "mixed_car_and_train", ("expect", "total"), "2464.00"),
        ("home_office", "days_250_capped", ("expect", "amount"), "1500.00"),
        (
            "home_office",
            "with_work_room_nine_months_warning",
            ("expect", "warnings", 0, "params", "months"),
            "3",
        ),
        ("work_room", "nine_months", ("expect", "flat"), "1260.00"),
        ("equipment", "phone_2025_slice_2026", ("years", 5, "amount"), "19.97"),
        ("telecom", "reimbursed", ("expect",), "240.00"),
        ("anlage_n", "owner_a_remote_work_room", ("expect", "result"), "57224.11"),
        ("anlage_n", "owner_b_commute_day_rate", ("expect", "lines", 3, "amount"), "0.00"),
        ("anlage_n", "union_beside_allowance", ("expect", "wk_applied"), "1230.00"),
        # the Steuerklasse-6 pay summed into the first column (the draft before F4)
        ("anlage_n", "tax_class_6_warning", ("expect", "lines", 0, "amount"), "66000.00"),
        ("vorsorge", "low_pay_15k_av_counts", ("expect", "other"), "0.00"),
        ("children", "kindergeld_wins_62k", ("expect", "assessed", "est_without"), "9975"),
        ("children", "soli_after_35a_credit", ("expect", "assessed", "soli"), "2014.67"),
        ("children", "child_over_18_warning", ("expect", "assessed", "children", "months"), 7),
        ("return_estimate", "household_2026", ("expect", "result"), "-2427.00"),
        ("return_estimate", "household_2026", ("expect", "zve"), "62034"),
        ("return_estimate", "donation_over_cap", ("expect", "donations"), "16009.09"),
        ("return_estimate", "mutterschutz_pay_stop", ("expect", "vorsorge_total"), "12226.50"),
        (
            "return_estimate",
            "mutterschutz_pay_stop",
            ("expect", "warnings", 0, "code"),
            "return.benefit_possible_double",
        ),
        (
            "return_estimate",
            "benefit_double_warning",
            ("expect", "warnings", 0, "code"),
            "return.kindergeld_apply",
        ),
        ("return_estimate", "filing_optional", ("expect", "filing_required", "required"), True),
        ("return_estimate", "childcare_turns_14", ("expect", "childcare"), "800.00"),
        (
            "return_estimate",
            "childcare_turns_14",
            ("expect", "warnings", 0, "code"),
            "return.child_over_18",
        ),
        (
            "return_estimate",
            "filing_required_factor",
            ("expect", "filing_required", "required"),
            False,
        ),
        (
            "return_estimate",
            "filing_deadline_next_working_day",
            ("expect", "filing_required", "deadline"),
            "2027-07-31",
        ),
        ("questionnaire", "progress_count", ("expect", "answered"), 33),
        ("questionnaire", "extraordinary_threshold_household", ("expect", "threshold"), "2537.11"),
        (
            "questionnaire",
            "prefill_reconfirm_phone",
            ("expect", "slots", 0, "reconfirm_ids"),
            [],
        ),
        (
            "questionnaire",
            "default_yes_account_fee",
            ("expect", "slots", 0, "proposed_answer"),
            None,
        ),
    ],
)
def test_phase4b_fixtures_check_their_arithmetic(name, case_id, path, slip):
    """A one-cent slip in a hand-filled figure fails on load (the validators recompute it)."""
    import copy

    data = copy.deepcopy(phase4b.raw(name))
    case = next(c for c in data["cases"] if c["id"] == case_id)
    node = case
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = slip
    with pytest.raises(ValidationError):
        phase4b.MODELS[name].model_validate(data)


# docs/phases/phase-5.md §12: the cases each phase 5 fixture must carry (a file may carry
# more). A case renamed or dropped here is a spec change.
PHASE5_CASES = {
    "sync_window": {
        "first_sync_from_anchor",
        "after_success_minus_14_days",
        "end_is_today_never_future",
        "long_window_may_need_approval",
        "short_window_no_flag",
        "anchor_after_today_error",
    },
    "sync_archive": {
        "one_stream_one_member",
        "three_streams_ordered",
        "same_input_same_bytes",
        "empty_raises_nothing_new",
        "too_many_members_rejected",
        "too_large_rejected",
    },
    "tan_flow": {
        "manual_tan_waits",
        "scheduled_tan_aborts",
        "confirmed_continues",
        "timeout_fails",
        "worker_lost_stays_restartable",
        "second_start_refused",
        "empty_streams_nothing_new",
        "all_lines_known_nothing_new",
        "new_lines_importing_then_done",
    },
    "sync_balance": {
        "hksal_matches_reconciled",
        "hksal_mismatch_unreconciled",
        "file_balance_wins_over_hksal",
        "duplicate_reference_skipped_lines_new_zero",
        "different_reference_double_insert_unreconciled",
        "nothing_new_balance_equal_no_drift",
        "nothing_new_balance_differs_drift",
    },
    "inbound_mail": {
        "member_dkim_pass_pdf_accepted",
        "member_spf_pass_only_accepted",
        "sender_not_member_rejected",
        "auth_from_other_authserv_ignored_rejected",
        "auth_missing_rejected",
        "auth_fail_rejected",
        "recipient_without_token_rejected",
        "delivered_to_token_accepted",
        "no_attachment_rejected",
        "nested_rfc822_attachments_collected",
        "nested_two_levels_not_followed",
        "too_many_attachments_rejected",
        "too_large_rejected",
        "unknown_type_skipped_counted",
        "no_message_id_rejected",
        "case_folded_sender",
    },
    "reminders": {
        "grundsteuer_bescheid_expected_first_full_year",
        "grundsteuer_bescheid_present_silent",
        "grundsteuer_quarters_without_schedule",
        "grundsteuer_dates_from_schedule",
        "grundsteuer_schedule_carried_to_next_year",
        "grundsteuer_remind_cleared_by_matching_debit",
        "grundsteuer_mode_inferred_after_direct_debit",
        "grundsteuer_transfer_clears_but_infers_nothing",
        "grundsteuer_mode_set_sepa",
        "grundsteuer_muted",
        "checklist_due_missing_fires",
        "checklist_present_silent",
        "return_deadline_2026_on_2_aug_2027",
        "questionnaire_open_from_jan_1",
        "wage_certificate_missing_from_mar_1",
        "wage_certificate_present_silent",
        "fixed_rate_end_12_and_6_months",
        "special_termination_info",
        "speculation_end_12_months_before",
        "settlement_only_for_prepayment",
        "rent_missing_fires_when_statements_cover_date",
        "rent_missing_silent_without_statements",
        "rent_missing_cleared_by_allocation",
        "rent_missing_label_names_unit_only",
        "sync_stale_after_35_days",
        "snoozed_hidden_until_date",
        "done_hides_instance_only",
        "ids_stable_across_days",
        "order_independent",
    },
    "notification_schedule": {
        "lead_day_sends_once",
        "missed_day_sends_next_day",
        "already_sent_not_repeated",
        "two_leads_two_sends",
        "info_kind_never_mailed",
        "two_recipients_two_rows",
        "sepa_mode_sends_after_due_only_if_open",
        # The PR #125 review's retry decision: a failed send is retried, three attempts in all.
        "failed_send_retried_next_day",
        "third_failure_gives_up",
    },
    # Step 2b, D154 (§4.8).
    "bank_statements": {
        "first_run_asks_number_one",
        "continues_after_the_highest_stored",
        "january_asks_the_previous_year_first",
        "february_previous_year_closed",
        "first_line_in_2025_never_before_2026",
        "cap_of_fifteen_requests",
        "delivered_0020_with_hiekp",
        "none_3010",
        "refused_9210_on_the_segment",
        "partly_faulty_9050_with_9210",
        "dialog_ended_9800",
        "locked_9930",
        "continuation_3040_with_touchdown",
        "not_a_pdf",
    },
}


def test_phase5_cases_cover_the_spec():
    assert set(PHASE5_CASES) == set(phase5.MODELS)
    for name, required in PHASE5_CASES.items():
        ids = {c.id for c in phase5.load(name).cases}
        assert required <= ids, (name, required - ids)


@pytest.mark.parametrize(
    ("name", "case_id", "path", "slip"),
    [
        ("sync_window", "after_success_minus_14_days", ("expect", "window", "start"), "2026-10-20"),
        (
            "sync_window",
            "long_window_may_need_approval",
            ("expect", "window", "may_need_approval"),
            False,
        ),
        ("sync_window", "first_sync_from_anchor", ("expect", "window", "start"), "2026-03-31"),
        ("tan_flow", "scheduled_tan_aborts", ("steps", 1, "expect", "end_dialog"), False),
        ("tan_flow", "timeout_fails", ("steps", 0, "expect", "error_code"), None),
        ("sync_archive", "three_streams_ordered", ("expect", "lines"), 4),
        ("sync_archive", "too_many_members_rejected", ("streams", 0, "count"), 400),
        ("sync_archive", "too_large_rejected", ("streams", 0, "bytes"), 10485760),
        ("sync_balance", "hksal_mismatch_unreconciled", ("expect", "result", "difference"), "9.99"),
        (
            "sync_balance",
            "different_reference_double_insert_unreconciled",
            ("expect", "lines_new"),
            1,
        ),
        ("sync_balance", "nothing_new_balance_differs_drift", ("expect", "drift"), "-4.91"),
        ("sync_balance", "file_balance_wins_over_hksal", ("expect", "balance_source"), "bank"),
        (
            "inbound_mail",
            "auth_from_other_authserv_ignored_rejected",
            ("expect", "code"),
            "email.auth_failed",
        ),
        ("inbound_mail", "unknown_type_skipped_counted", ("expect", "skipped"), 0),
        (
            "inbound_mail",
            "nested_two_levels_not_followed",
            ("expect", "attachments"),
            ["2.2.2", "3"],
        ),
        ("inbound_mail", "exactly_25_mib_accepted", ("facts", "parts", 1, "size_bytes"), 26214401),
        (
            "reminders",
            "return_deadline_2026_on_2_aug_2027",
            ("expect", 0, "visible_from"),
            "2027-05-05",
        ),
        (
            "reminders",
            "fixed_rate_end_12_and_6_months",
            ("expect", 1, "label_args", "months_before"),
            7,
        ),
        (
            "reminders",
            "settlement_only_for_prepayment",
            ("expect", 0, "visible_from"),
            "2027-10-03",
        ),
        (
            "reminders",
            "rent_missing_label_names_unit_only",
            ("expect", 0, "label_args", "tenant"),
            "X",
        ),
        (
            "reminders",
            "speculation_end_12_months_before",
            ("expect", 0, "label_args", "tax_free_from"),
            "2027-02-10",
        ),
        ("reminders", "special_termination_info", ("expect", 0, "email_leads"), [0]),
        ("reminders", "grundsteuer_mode_set_sepa", ("expect", 0, "email_leads"), [7]),
        (
            "notification_schedule",
            "late_start_sends_latest_lead_only",
            ("expect", 0, "lead_days"),
            60,
        ),
        ("notification_schedule", "sepa_not_before_due_plus_3", ("today",), "2027-05-18"),
        ("notification_schedule", "failed_send_retried_next_day", ("expect", 0, "attempt"), 1),
        (
            "notification_schedule",
            "retry_that_succeeded_not_repeated",
            ("sent", 1, "status"),
            "failed",
        ),
        ("notification_schedule", "third_failure_gives_up", ("today",), "2027-07-19"),
    ],
)
def test_phase5_fixtures_check_their_contract(name, case_id, path, slip):
    """A slip in a hand-filled expectation fails on load (the validators recompute it)."""
    import copy

    data = copy.deepcopy(phase5.raw(name))
    case = next(c for c in data["cases"] if c["id"] == case_id)
    node = case
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = slip
    with pytest.raises(ValidationError):
        phase5.MODELS[name].model_validate(data)


@pytest.mark.parametrize(
    ("group", "case_id", "path", "slip"),
    [
        ("plan_cases", "january_asks_the_previous_year_first", ("expect", "probes"), ["2027/1"]),
        (
            "plan_cases",
            "february_previous_year_closed",
            ("expect", "probes"),
            ["2026/13", "2027/2"],
        ),
        (
            "plan_cases",
            "first_line_in_2025_never_before_2026",
            ("expect", "probes"),
            ["2025/1", "2026/1"],
        ),
        ("plan_cases", "cap_of_fifteen_requests", ("bank_has",), ["2026/1", "2026/2"]),
        ("answer_cases", "partly_faulty_9050_with_9210", ("expect", "outcome"), "stopped"),
        ("answer_cases", "refusal_that_ends_the_dialog", ("expect", "dialog_ended"), False),
        ("answer_cases", "locked_wins_over_a_delivery", ("expect", "outcome"), "statement"),
        ("answer_cases", "not_a_pdf", ("expect", "reason"), "no_eof"),
        ("answer_cases", "continuation_last_part_joined", ("expect", "size"), 108),
    ],
)
def test_bank_statements_fixture_checks_its_contract(group, case_id, path, slip):
    """D154's fixture: a slip in a hand-filled expectation fails on load."""
    import copy

    data = copy.deepcopy(phase5.raw("bank_statements"))
    node = next(c for c in data[group] if c["id"] == case_id)
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = slip
    with pytest.raises(ValidationError):
        phase5.MODELS["bank_statements"].model_validate(data)


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
        D("-3160.32"),
        D("65220.76"),
    )
    assert (a28.taxable_result, a28.tax_effect.effect, a28.net_cash) == (
        D("-4314.80"),
        D("-1473.12"),
        D("-3205.68"),
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
        D("-3117.58"),
        D("312120.00"),
        D("8466.77"),
    )
    assert {y.afa for y in c.years} == {D("11031.43")}
    c30 = c.years[-1]
    assert (c30.taxable_result, c30.tax_effect.effect, c30.net_cash) == (
        D("-10387.92"),
        D("-3488.40"),
        D("-1190.40"),
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
        D("-4180.39"),
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
