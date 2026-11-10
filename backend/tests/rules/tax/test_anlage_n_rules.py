"""§5.1–§5.7 the Anlage N rules against the phase 4b fixtures (payslip, commute, home_office,
work_room, equipment, telecom, anlage_n, vorsorge .yaml). Fields a case leaves out (None) are
not asserted; route lines (`commute.route_N.*`) are not compared in anlage_n.yaml (commute.yaml
asserts the amounts)."""

from decimal import Decimal

import anlage_n_compare as cmp
import pytest
from fixture_models_phase4b import load

from hausbuch.rules.errors import TAX_NO_RULE_SET, RuleError
from hausbuch.rules.tax import registry
from hausbuch.rules.tax.anlage_n._base import rules_for
from hausbuch.rules.tax.anlage_n.commute import commute_lines
from hausbuch.rules.tax.anlage_n.draft import draft_n, itemise
from hausbuch.rules.tax.anlage_n.equipment import equipment_amount
from hausbuch.rules.tax.anlage_n.home_office import home_office_line
from hausbuch.rules.tax.anlage_n.payslip import pay_certificates
from hausbuch.rules.tax.anlage_n.telecom import telecom_line
from hausbuch.rules.tax.anlage_n.vorsorge import vorsorge
from hausbuch.rules.tax.anlage_n.work_room import work_room_line

ZERO = Decimal("0")


def _cases(name):
    cases = load(name).cases
    return pytest.mark.parametrize("case", cases, ids=[c.id for c in cases])


@pytest.mark.rule
@_cases("payslip")
def test_payslip_projection(case):
    got = pay_certificates(case.certificates, case.payslips, case.tax_year)
    e = case.expect
    assert got.projected == e.projected
    assert sum((c.gross for c in got.certificates), ZERO) == e.gross_total
    assert got.gross_source == e.gross_source
    cmp.warnings(got.warnings, e.warnings, case.id)


@pytest.mark.rule
@_cases("commute")
def test_commute(case):
    got = commute_lines(case.routes, case.certificates, rules_for(case.tax_year))
    e = case.expect
    for name in ("pauschale_car", "pauschale_rest", "total", "employer_offset", "deductible"):
        assert getattr(got, name) == getattr(e, name), f"{case.id}: {name}"
    assert got.notes == e.notes
    cmp.warnings(got.warnings, e.warnings, case.id)


@pytest.mark.rule
@_cases("home_office")
def test_home_office(case):
    rs = rules_for(case.tax_year)
    e = case.expect

    def run():
        return home_office_line(case.fact, case.commute_days, case.work_room, rs)

    if e.error is not None:
        cmp.refused(e.error.code, run)
        return
    amount, warnings = run()
    assert amount == e.amount
    cmp.warnings(warnings, e.warnings, case.id)


@pytest.mark.rule
@_cases("work_room")
def test_work_room(case):
    got = work_room_line(case.fact, rules_for(case.tax_year))
    e = case.expect
    assert (got.actual, got.flat, got.method, got.amount) == (e.actual, e.flat, e.method, e.amount)
    cmp.warnings(got.warnings, e.warnings, case.id)


@pytest.mark.rule
@_cases("equipment")
def test_equipment(case):
    rs = rules_for(case.rule_set_year)
    for y in case.years:
        if y.error is not None:
            cmp.refused(y.error.code, lambda y=y: equipment_amount(case.item, y.tax_year, rs))
        else:
            assert equipment_amount(case.item, y.tax_year, rs) == y.amount, y.tax_year
    if case.full_life_sum is not None:
        years = range(case.item.bought_on.year, 2080)
        total = sum((equipment_amount(case.item, y, rs) for y in years), ZERO)
        assert total == case.full_life_sum


@pytest.mark.rule
@_cases("telecom")
def test_telecom(case):
    assert telecom_line(case.fact, rules_for(case.tax_year)) == case.expect


@pytest.mark.rule
@_cases("anlage_n")
def test_draft_n(case):
    e = case.expect
    rs, tariff = rules_for(case.tax_year), registry.tariff(case.tax_year)
    if e.error is not None:
        cmp.refused(e.error.code, lambda: draft_n(case.facts, case.tax_year, rs, tariff))
        return
    got = draft_n(case.facts, case.tax_year, rs, tariff)
    assert got.owner_id == case.facts.owner_id and got.tax_year == case.tax_year
    for name in (
        "status",
        "gross_source",
        "gross",
        "wk_itemised",
        "wk_union",
        "wk_allowance",
        "wk_applied",
        "result",
        "replacement_benefits",
    ):
        want = getattr(e, name)
        if want is not None:
            assert getattr(got, name) == want, f"{case.id}: {name}"
    if e.components is not None:
        components = vars(itemise(case.facts, case.tax_year, rs, tariff).components)
        assert components == e.components.model_dump(), case.id
    if e.lines is not None:
        lines = {x.key: x.amount for x in got.lines if not x.key.startswith("commute.route_")}
        assert lines == {x.key: x.amount for x in e.lines}, case.id
    cmp.warnings(got.warnings, e.warnings, case.id)


@pytest.mark.rule
def test_draft_lines_carry_the_form_placement_and_sources():
    case = next(c for c in load("anlage_n").cases if c.id == "owner_b_commute_day_rate")
    got = draft_n(case.facts, 2026, rules_for(2026), registry.tariff(2026))
    by_key = {line.key: line for line in got.lines}
    assert by_key["pay.gross"].kennzahl == "110" and by_key["pay.gross"].zeile == 5
    assert by_key["pay.gross"].sources, "the gross opens its certificate"
    route = by_key["commute.route_1.km_car"]
    assert route.amount == 25 and route.sources[0].kind == "return_input"
    assert by_key["wk.applied"].amount_rounded == 1230
    assert got.form.form_status == "provisional" and got.form.form_year == 2025
    assert {s.kind for s in got.sources} >= {"return_input", "anlage_n_rule_set", "tariff"}


@pytest.mark.rule
@_cases("vorsorge")
def test_vorsorge(case):
    got = vorsorge(
        case.facts, case.tax_year, rules_for(case.tax_year), registry.tariff(case.tax_year)
    )
    assert got.model_dump(include=set(case.expect.model_dump())) == case.expect.model_dump()


@pytest.mark.rule
def test_rule_set_falls_back_to_the_latest_earlier_year():
    assert rules_for(2027) == registry.anlage_n(2026)
    with pytest.raises(RuleError) as info:
        rules_for(2025)
    assert info.value.code == TAX_NO_RULE_SET
