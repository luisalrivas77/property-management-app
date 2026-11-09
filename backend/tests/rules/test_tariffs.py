"""The per-year income tax tariffs load, are contiguous, and fall back (docs/phases/phase-4.md
§4, D81).

The 2026 figures below were read from §32a EStG, §§3–4 SolZG, §§9a, 10, 10c EStG and the 2026
social-insurance figures on 9 Nov 2026 (§3 of the spec). A change to them is a data PR against
a newly read source, never a fix to make a test pass. The tariff rule itself
(rules/tax/tariff.py) is phase 4 step 1; `_single` below only checks the data against the
spec's worked examples.
"""

from decimal import ROUND_FLOOR, Decimal

import pytest
from pydantic import ValidationError

from hausbuch.rules.errors import PROJECTION_NO_RULE_SET, TARIFF_NO_FILE, RuleError
from hausbuch.rules.tax.registry import rule_set, rule_set_or_latest, tariff, tariff_years
from hausbuch.schemas.enums import StateCode
from hausbuch.schemas.performance import Tariff, TariffZone

D = Decimal


def test_the_shipped_years():
    assert tariff_years() == (2026,)


def test_2026_zones_are_pinned():
    t = tariff(2026)
    assert (t.tax_year, t.version, t.reused_from) == (2026, "2026.1", None)
    assert [z.model_dump(exclude_defaults=True) for z in t.zones] == [
        {"up_to": 12348, "kind": "zero"},
        {"up_to": 17799, "kind": "progressive", "a": D("914.51"), "b": D("1400"), "offset": 12348},
        {
            "up_to": 69878,
            "kind": "progressive",
            "a": D("173.10"),
            "b": D("2397"),
            "c": D("1034.87"),
            "offset": 17799,
        },
        {"up_to": 277825, "kind": "linear", "rate": D("0.42"), "c": D("11135.63")},
        {"up_to": None, "kind": "linear", "rate": D("0.45"), "c": D("19470.38")},
    ]


def test_2026_soli_church_and_deduction_figures_are_pinned():
    t = tariff(2026)
    assert (t.soli_rate, t.soli_mitigation_rate) == (D("0.055"), D("0.119"))
    assert (t.soli_free_limit_joint, t.soli_free_limit_single) == (D("40700"), D("20350"))
    assert t.church_tax_rates == {
        s: D("0.08") if s in (StateCode.BY, StateCode.BW) else D("0.09") for s in StateCode
    }
    assert (t.employee_allowance, t.special_expenses_allowance) == (D("1230"), D("36"))
    si = t.social_insurance
    assert (si.pension_rate, si.unemployment_rate) == (D("0.093"), D("0.013"))
    assert (si.health_rate, si.health_extra_rate, si.care_rate) == (
        D("0.073"),
        D("0.0145"),
        D("0.018"),
    )
    assert (si.bbg_pension, si.bbg_health) == (D("101400"), D("69750"))
    assert si.health_deductible_share == D("0.96")


def _zone_tax(zone: TariffZone, x: int) -> Decimal:
    if zone.kind == "zero":
        return D(0)
    if zone.kind == "linear":
        return zone.rate * x - zone.c
    v = D(x - zone.offset) / 10000
    return (zone.a * v + zone.b) * v + zone.c


def _single(x: int, t: Tariff) -> Decimal:
    zone = next(z for z in t.zones if z.up_to is None or x <= z.up_to)
    return _zone_tax(zone, x).to_integral_value(rounding=ROUND_FLOOR)


@pytest.mark.parametrize(
    ("zve", "tax"),
    [
        (12348, 0),
        (17799, 1034),
        (17800, 1035),
        (30000, 4217),
        (69878, 18213),
        (100000, 30864),
        (277826, 105551),
    ],
)
def test_the_data_gives_the_spec_single_figures(zve, tax):
    assert _single(zve, tariff(2026)) == tax


def test_the_zones_join_without_a_step():
    """At each boundary the next zone continues the curve to within a euro."""
    t = tariff(2026)
    for before, after in zip(t.zones, t.zones[1:], strict=False):
        edge = before.up_to + 1
        assert abs(_zone_tax(after, edge) - _zone_tax(before, edge)) < 1, before.up_to


def test_a_year_without_a_file_reuses_the_latest_earlier_one():
    reused = tariff(2030)
    assert (reused.tax_year, reused.reused_from) == (2030, 2026)
    own = tariff(2026)
    assert reused.model_dump(exclude={"tax_year", "reused_from"}) == own.model_dump(
        exclude={"tax_year", "reused_from"}
    )


def test_a_year_before_every_file_has_no_tariff():
    with pytest.raises(RuleError) as excinfo:
        tariff(2025)
    assert excinfo.value.code == TARIFF_NO_FILE


def test_rule_set_or_latest_falls_back_for_the_projection_only():
    assert rule_set_or_latest(2026) is rule_set(2026)
    assert rule_set_or_latest(2031) is rule_set(2026)  # unchanged: its own tax_year
    assert rule_set_or_latest(2025) is rule_set(2025)
    with pytest.raises(RuleError) as excinfo:
        rule_set_or_latest(2024)
    assert excinfo.value.code == PROJECTION_NO_RULE_SET
    with pytest.raises(RuleError):  # the Steuern screen keeps raising
        rule_set(2031)


def _data(**changes) -> dict:
    return tariff(2026).model_dump(mode="json") | changes


def _zones(**edit) -> list[dict]:
    zones = tariff(2026).model_dump(mode="json")["zones"]
    index = edit.pop("index")
    zones[index] |= edit
    return zones


@pytest.mark.parametrize(
    "changes",
    [
        {"zones": _zones(index=0, kind="linear", rate="0.14")},  # no Grundfreibetrag
        {"zones": _zones(index=4, up_to=400000)},  # no open end
        {"zones": _zones(index=2, up_to=17000)},  # not increasing
        {"zones": _zones(index=2, offset=17800)},  # counts from the wrong euro
        {"zones": _zones(index=3, a="1")},  # a linear zone with a coefficient
        {"zones": _zones(index=1, a=914.51)},  # a float
        {"church_tax_rates": {"BY": "0.08"}},  # not every Bundesland
        {"reused_from": 2026},  # reused from itself
        {"soli_free_limit_single": "40700.00"},  # the joint limit is the larger
    ],
)
def test_an_invalid_tariff_is_refused(changes):
    with pytest.raises(ValidationError):
        Tariff.model_validate(_data(**changes))


def test_the_validator_names_the_code():
    with pytest.raises(ValidationError, match="tariff.invalid"):
        Tariff.model_validate(_data(zones=_zones(index=2, up_to=17000)))
