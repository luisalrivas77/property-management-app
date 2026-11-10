"""The Anlage N rule set as data (docs/phases/phase-4b.md §4, §4.1, D99): the 2026 figures and
the 2025 Zeilen/Kennzahlen are pinned, every key the draft writes is mapped, and a broken file
fails to load with its code. No Kennzahl-uniqueness check: the form repeats Kz across blocks."""

from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from hausbuch.rules.errors import TAX_NO_RULE_SET, RuleError
from hausbuch.rules.tax.registry import anlage_n, anlage_n_years
from hausbuch.schemas.employment import ANLAGE_N_LINE_KEYS, ITEM_CLASSES, AnlageNRuleSet
from hausbuch.schemas.tax import FormStatus

# key: (zeile_from, zeile, kennzahl), from the printed 2025 form (2025AnlN031NET).
PINNED_2025 = {
    "header.tax_class": (None, 4, "168"),
    "pay.gross": (None, 5, "110"),
    "pay.income_tax": (None, 6, "140"),
    "pay.soli": (None, 7, "150"),
    "pay.church_tax": (None, 8, "142"),
    "pay.church_tax_spouse": (None, 9, "144"),
    "pay.replacement_benefits": (None, 20, "119"),
    "commute.route_1.kind": (None, 27, None),
    "commute.route_1.absent_days": (None, 28, None),
    "commute.route_1.days": (None, 29, "110"),
    "commute.route_1.km": (None, 30, "111"),
    "commute.route_1.km_car": (None, 31, "112"),
    "commute.route_1.km_collective": (None, 32, "113"),
    "commute.route_1.km_other": (None, 33, None),
    "commute.route_1.public_transport_costs": (None, 34, "114"),
    "commute.route_2.kind": (None, 35, None),
    "commute.route_2.absent_days": (None, 36, None),
    "commute.route_2.days": (None, 37, "130"),
    "commute.route_2.km": (None, 38, "131"),
    "commute.route_2.public_transport_costs": (None, 42, "134"),
    "commute.route_3.kind": (None, 43, None),
    "commute.route_3.absent_days": (None, 44, None),
    "commute.route_3.days": (None, 45, "150"),
    "commute.route_3.public_transport_costs": (None, 50, "154"),
    "commute.employer_tax_free": (None, 51, "290"),
    "commute.employer_flat_taxed": (None, 51, "295"),
    "commute.agency_subsidy": (None, 52, "291"),
    "dues.associations": (None, 53, "310"),
    "equipment.items": (54, 55, None),
    "equipment.total": (None, 56, "320"),
    "work_room.amount": (None, 57, "325"),
    "home_office.days_other_workplace": (None, 58, "335"),
    "home_office.days_no_other_workplace": (None, 59, "336"),
    "training.total": (None, 60, "330"),
    "other.ferry_flight": (None, 61, None),
    "other.items": (62, 63, None),
    "other.total": (None, 64, "380"),
    "wk.itemised": (None, None, None),
    "wk.allowance": (None, None, None),
    "wk.applied": (None, None, None),
    "result": (None, None, None),
}


def test_the_shipped_years():
    assert anlage_n_years() == (2026,)


def test_a_year_without_a_file_raises_no_rule_set():
    with pytest.raises(RuleError) as excinfo:
        anlage_n(2025)
    assert excinfo.value.code == TAX_NO_RULE_SET


def test_the_2026_figures_are_pinned():
    r = anlage_n(2026)
    assert r.tax_year == 2026
    assert [(t.from_km, t.rate) for t in r.commute_rates] == [(1, D("0.38"))]
    assert r.commute_cap_non_car == D("4500")
    assert (r.home_office_day_rate, r.home_office_cap) == (D("6"), D("1260"))
    assert r.work_room_flat == D("1260")
    assert (r.gwg_limit_net, r.default_vat_rate) == (D("800"), D("0.19"))
    assert (r.telecom_share, r.telecom_cap_monthly) == (D("0.20"), D("20"))
    assert r.account_fee_flat == D("16")
    assert r.union_dues_beside_allowance is True
    assert (r.other_vorsorge_cap_employee, r.other_vorsorge_cap_self_paying) == (
        D("1900"),
        D("2800"),
    )
    assert r.pension_max_contribution == D("30826")
    assert r.weekdays_in_year == 261
    lives = {u.item_class: (u.months, u.full_in_year) for u in r.useful_lives}
    assert lives == {
        "computer": (None, True),
        "software": (None, True),
        "phone": (60, False),  # Q-O21: five years, not one
        "furniture": (156, False),
        "tools": (60, False),
        "subscription": (None, True),
        "other": (None, False),
    }
    assert set(lives) == set(ITEM_CLASSES)


def test_the_2026_mapping_is_a_provisional_copy_of_2025():
    form = anlage_n(2026).form
    assert (form.form_year, form.status) == (2025, FormStatus.PROVISIONAL)
    assert form.version_stamp == "2025AnlN031NET – Oktober 2025"


def test_every_line_the_draft_writes_is_mapped():
    lines = anlage_n(2026).form.lines
    assert set(lines) == set(ANLAGE_N_LINE_KEYS)
    assert len(ANLAGE_N_LINE_KEYS) == 57


def test_the_2025_zeilen_and_kennzahlen_are_pinned():
    lines = anlage_n(2026).form.lines
    got = {k: (lines[k].zeile_from, lines[k].zeile, lines[k].kennzahl) for k in PINNED_2025}
    assert got == PINNED_2025


def test_kennzahlen_repeat_across_blocks_without_failing():
    lines = anlage_n(2026).form.lines
    assert lines["pay.gross"].kennzahl == lines["commute.route_1.days"].kennzahl == "110"


def _data(**changes) -> dict:
    return anlage_n(2026).model_dump(mode="json") | changes


def test_a_mapping_missing_a_draft_line_fails_to_load():
    form = _data()["form"]
    del form["lines"]["work_room.amount"]
    with pytest.raises(ValidationError, match="form_mapping.incomplete"):
        AnlageNRuleSet.model_validate(_data(form=form))


@pytest.mark.parametrize(
    "changes",
    [
        {"commute_rates": [{"from_km": 21, "rate": "0.38"}]},  # does not start at km 1
        {"commute_rates": [{"from_km": 1, "rate": "0.30"}, {"from_km": 1, "rate": "0.38"}]},
        {"useful_lives": _data()["useful_lives"][:-1]},  # a class without its default
        {
            "useful_lives": [
                u | {"full_in_year": False} if u["item_class"] == "subscription" else u
                for u in _data()["useful_lives"]
            ]
        },
        {
            "useful_lives": [
                u | {"months": None} if u["item_class"] == "phone" else u
                for u in _data()["useful_lives"]
            ]
        },
        {"other_vorsorge_cap_employee": "3000.00"},  # above the self-paying cap
        {"form": _data()["form"] | {"status": "published"}},  # a 2025 form published for 2026
    ],
)
def test_an_invalid_rule_set_is_refused_with_its_code(changes):
    with pytest.raises(ValidationError, match="anlage_n.rule_set_invalid"):
        AnlageNRuleSet.model_validate(_data(**changes))


def test_a_rate_must_be_a_string():
    with pytest.raises(ValidationError):
        AnlageNRuleSet.model_validate(_data(home_office_day_rate=6.0))
