"""An independent recomputation of the phase 4b spec figures (docs/phases/phase-4b.md §5.6–§5.11).

tests/fixture_models_phase4b.py recomputes every expectation through its reference functions;
this file does not use them. It starts from the household's facts as the spec states them,
uses only the real tariff (`income_tax`, `soli`, `church_tax`, `total_tax` from
rules/tax/tariff.py on `registry.tariff(2026)`) and the law's constants written out here, and
compares each figure with the expectation the YAML holds (read raw, without the validators).
So a wrong formula in the reference and a wrong figure in a fixture would both have to repeat
the same mistake here to pass.

The Progressionsvorbehalt rate is cut to six decimals and the tax floored to the euro (Q-O10,
H 32b EStH 2025 example B); the filing deadline is 31 July of the next year moved to the next
working day (Q-O24, §108 Abs. 3 AO).
"""

from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal

import fixture_models_phase4 as phase4
import fixture_models_phase4b as phase4b

from hausbuch.rules.tax import registry
from hausbuch.rules.tax.tariff import church_tax, income_tax, soli, total_tax
from hausbuch.schemas.performance import OwnerTaxProfile

D = Decimal
TARIFF = registry.tariff(2026)
ZERO = D("0")
A = "0000000a-0000-4000-8000-000000000001"
B = "0000000a-0000-4000-8000-000000000002"
PLAIN = [OwnerTaxProfile(owner_id=A), OwnerTaxProfile(owner_id=B)]
A_IN_CHURCH_BY = [OwnerTaxProfile(owner_id=A, church_tax=True, tax_state="BY"), PLAIN[1]]

# The law's figures for 2026, written out (not read from the rule data)
PAUSCHBETRAG = D("1230")  # §9a S. 1 Nr. 1a EStG
SONDERAUSGABEN_PAUSCHBETRAG_JOINT = D("72")  # §10c S. 1, doubled
KINDERFREIBETRAG_BOTH = (D("3414") + D("1464")) * 2  # §32 Abs. 6 S. 1–2
KINDERGELD_MONTHLY = D("259")  # §66 Abs. 1
KM_RATE = D("0.38")  # §9 Abs. 1 S. 3 Nr. 4, from 2026 from the first km
HOME_OFFICE_DAY = D("6")  # §4 Abs. 5 S. 1 Nr. 6c
HEALTH_SHARE = D("0.96")  # §10 Abs. 1 Nr. 3 S. 4: 4 % off for Krankengeld
OTHER_CAP = D("1900")  # §10 Abs. 4 S. 2
FILING_THRESHOLD = D("410")  # §46 Abs. 2 Nr. 1


def trunc6(x: Decimal) -> Decimal:
    return x.quantize(D("0.000001"), ROUND_DOWN)


def floor(x: Decimal) -> Decimal:
    return x.quantize(D("1"), ROUND_DOWN)


def cent(x: Decimal) -> Decimal:
    return x.quantize(D("0.01"))


def est(z: Decimal, benefits: Decimal) -> Decimal:
    """§32b: the special rate on z + L, applied to z; the plain tariff without benefits."""
    if benefits == 0:
        return income_tax(z, TARIFF)
    rate = trunc6(income_tax(z + benefits, TARIFF) / (z + benefits))
    return floor(z * rate)


def assessed(z: Decimal, benefits: Decimal, months: int, credit=ZERO, owners=PLAIN):
    """§31 with §32b, §35a and §51a Abs. 2: (est, est_with, soli, church, total)."""
    without = est(z, benefits)
    if months == 0:
        e = with_ = without
    else:
        allowances = cent(KINDERFREIBETRAG_BOTH * months / 12)
        with_ = est(floor(z - allowances), benefits)
        kindergeld = KINDERGELD_MONTHLY * months
        e = with_ + kindergeld if without - with_ > kindergeld else without
    c = min(credit, e)
    base = with_ - c
    s, ch = soli(base, TARIFF), church_tax(base, owners, TARIFF)
    return e, with_, s, ch, e - c + s + ch


def vorsorge(pension, health, care, unemployment) -> Decimal:
    basis = cent(health * HEALTH_SHARE) + care
    other = max(basis, min(OTHER_CAP, basis + unemployment)) - basis
    return pension + basis + other


def case(name: str, case_id: str) -> dict:
    return next(c for c in phase4b.raw(name)["cases"] if c["id"] == case_id)


def expect(name: str, case_id: str) -> dict:
    return case(name, case_id)["expect"]


def deadline(year: int) -> date:
    d = date(year + 1, 7, 31)
    while d.weekday() >= 5:  # 31 July and the days after it are never a nationwide holiday
        d += timedelta(days=1)
    return d


# The household of §5.6–§5.8 from its facts
WORK_ROOM_A = (D("14400") + D("2400")) * 12 / 80  # actual costs, 12 of 80 m², all year
PHONE_A = cent(D("1199") / 2 / 60 * 12)  # half of 1,199.00 over 60 months, 12 in 2026
WK_A = WORK_ROOM_A + PHONE_A + D("240") / 2 + D("16")  # + subscription share + Kontoführung
RESULT_A = D("60000") - max(PAUSCHBETRAG, WK_A)
COMMUTE_B = 100 * 25 * KM_RATE - D("400")  # less the tax-free employer ticket (Nr. 17)
WK_B = COMMUTE_B + 90 * HOME_OFFICE_DAY
RESULT_B = D("32000") - max(PAUSCHBETRAG, WK_B)
RESULT_V = D("-7948.66")
GDE = RESULT_A + RESULT_B + RESULT_V
VORSORGE_A = vorsorge(D("5580"), D("5250"), D("1080"), D("780"))
VORSORGE_B = vorsorge(D("2976"), D("2800"), D("576"), D("416"))
ZVE = floor(GDE - VORSORGE_A - VORSORGE_B - SONDERAUSGABEN_PAUSCHBETRAG_JOINT)
L = D("1800") + D("1560") + D("4000")  # Elterngeld, Krankengeld, Kurzarbeitergeld (Nr. 15)
WITHHELD = D("9200") + D("3200")


def test_household_income_and_zve():
    assert (D("2775.90"), D("57224.10"), D("30770.00")) == (WK_A, RESULT_A, RESULT_B)
    assert (D("11700.00"), D("6240.00")) == (VORSORGE_A, VORSORGE_B)
    assert (D("80045.44"), D("62033"), D("7360")) == (GDE, ZVE, L)
    n = expect("anlage_n", "owner_a_remote_work_room")
    assert D(n["result"]) == RESULT_A and D(n["wk_applied"]) == WK_A
    assert D(expect("anlage_n", "owner_b_commute_day_rate")["result"]) == RESULT_B
    assert D(expect("vorsorge", "owner_a_60k")["deductible"]) == VORSORGE_A
    assert D(expect("vorsorge", "owner_b_32k")["deductible"]) == VORSORGE_B
    e = expect("return_estimate", "household_2026")
    assert (D(e["total_income"]), D(e["zve"])) == (GDE, ZVE)


def test_household_assessed_and_result():
    assert income_tax(ZVE, TARIFF) == D("9010")  # the plain tariff the estimate must not use
    assert income_tax(ZVE + L, TARIFF) == D("11158")
    assert trunc6(D("11158") / (ZVE + L)) == D("0.160794")
    assert income_tax(D("53903") + L, TARIFF) == D("8790")
    e_, with_, s, ch, total = assessed(ZVE, L, 10)
    assert (e_, with_, s, ch, total) == (D("9974"), D("7733"), 0, 0, D("9974"))
    assert D("9974") - D("7733") == D("2241") < 10 * KINDERGELD_MONTHLY  # Kindergeld wins
    e = expect("return_estimate", "household_2026")
    a = e["assessed"]
    assert (D(a["est_without"]), D(a["est_with"]), D(a["total"])) == (e_, with_, total)
    assert D(a["progression"]["rate"]) == D("0.160794")
    assert D(e["result"]) == total - WITHHELD == D("-2426.00")
    assert e["filing_required"]["required"] is True and L > FILING_THRESHOLD
    k = expect("children", "kindergeld_wins_62k")["assessed"]
    assert (D(k["est_without"]), D(k["est_with"]), D(k["children"]["saving"])) == (
        e_,
        with_,
        D("2241"),
    )


def test_six_decimal_rule_reproduces_the_official_example():
    """H 32b EStH 2025, example B: 2,540 ÷ 37,000 printed as 6,8648 %, tax 2,745 on 40,000. Six
    decimals reproduce it; four would give 2,744 (Q-O10)."""
    rate = trunc6(D("2540") / D("37000"))
    assert rate == D("0.068648")
    assert floor(D("40000") * rate) == D("2745")
    assert floor(D("40000") * trunc6(D("2540") / D("37000")).quantize(D("0.0001"), ROUND_DOWN)) == (
        D("2744")
    )


def test_the_further_return_estimate_cases():
    zve, credit = ZVE, D("1500") * D("0.20")
    total = assessed(zve, L, 10, credit=credit)[4]
    assert D(expect("return_estimate", "with_35a_credit")["result"]) == total - WITHHELD
    assert total - WITHHELD == D("-2726.00")
    assert D(expect("return_estimate", "back_payment")["result"]) == D("9974") - D("9000")
    plain = expect("return_estimate", "no_benefits_no_child")
    assert D(plain["result"]) == income_tax(ZVE, TARIFF) - WITHHELD == D("-3390.00")
    capped = expect("return_estimate", "35a_capped")["assessed"]
    assert D(capped["credit_35a"]) == min(D("8000") * D("0.2"), D("1200")) + D("400")
    # donations 500.00: Sonderausgaben 500 instead of 72
    zve = floor(GDE - VORSORGE_A - VORSORGE_B - D("500"))
    e_, with_, _, _, total = assessed(zve, L, 10)
    d = expect("return_estimate", "donation_within_cap")
    assert (zve, e_, with_) == (D("61605"), D("9852"), D("7621"))
    assert (D(d["zve"]), D(d["assessed"]["est"]), D(d["result"])) == (zve, e_, total - WITHHELD)
    assert total - WITHHELD == D("-2548.00")
    cap = (GDE * D("0.20")).quantize(D("0.01"), ROUND_DOWN)
    assert cap == D("16009.08") == D(expect("return_estimate", "donation_over_cap")["donations"])


def test_mutterschutz_pay_stop():
    result_b = D("2700") - PAUSCHBETRAG
    vorsorge_b = vorsorge(D("251.10"), D("236.25"), D("48.60"), D("35.10"))
    gde = RESULT_A + result_b + RESULT_V
    zve = floor(gde - VORSORGE_A - vorsorge_b - SONDERAUSGABEN_PAUSCHBETRAG_JOINT)
    benefits = D("1800") + D("6000") + D("4000")
    e_, with_, _, _, total = assessed(zve, benefits, 10)
    assert (vorsorge_b, gde, zve) == (D("561.60"), D("50745.44"), D("38411"))
    assert (income_tax(zve + benefits, TARIFF), e_, with_) == (D("5756"), D("4403"), D("2632"))
    assert income_tax(zve, TARIFF) == D("2750")
    m = expect("return_estimate", "mutterschutz_pay_stop")
    assert (D(m["zve"]), D(m["assessed"]["est"]), D(m["result"])) == (
        zve,
        e_,
        total - D("9200"),
    )
    assert total - D("9200") == D("-4797.00")


def test_children_cases():
    high = expect("children", "allowances_win_200k")["assessed"]
    assert (income_tax(D("200000"), TARIFF), income_tax(D("190244"), TARIFF)) == (
        D("61728"),
        D("57630"),
    )
    e_, _, s, _, total = assessed(D("200000"), D("0"), 12)
    assert (e_, s) == (D("60738"), D("2014.67"))
    assert (D(high["est"]), D(high["soli"]), D(high["total"])) == (e_, s, total)
    credit = expect("children", "soli_after_35a_credit")["assessed"]
    assert soli(D("57630") - D("1200"), TARIFF) == D("1871.87") == D(credit["soli"])
    church = expect("children", "soli_on_est_with")["assessed"]
    _, with_, s, ch, _ = assessed(D("150000"), D("0"), 12, owners=A_IN_CHURCH_BY)
    assert (s, ch) == (0, D(church["church"])) and with_ == D("36630")
    plain = expect("children", "no_context_equals_total_tax")["assessed"]
    t = total_tax(D("62033"), TARIFF, A_IN_CHURCH_BY)
    assert (D(plain["est"]), D(plain["church"]), D(plain["total"])) == (t.est, t.church, t.total)
    for case_id, months in (
        ("born_march_ten_months", 10),
        ("born_november_two_months", 2),
        ("born_january_full_year", 12),
        ("two_children", 22),
        ("child_over_18_warning", 6),
    ):
        c = expect("children", case_id)["assessed"]["children"]
        assert c["months"] == months
        assert D(c["kindergeld"]) == KINDERGELD_MONTHLY * months
        assert D(c["allowances"]) == cent(KINDERFREIBETRAG_BOTH * months / 12)


def test_the_phase4_hook_contexts():
    """§5.9: the D98 base 69,982.10 and the tax effect −2,315.00 with the contexts."""
    base = floor(RESULT_A - VORSORGE_A - 36 + RESULT_B - VORSORGE_B - 36)
    assert base == D("69982")
    at_base = assessed(base, L, 10)
    assert income_tax(base + L, TARIFF) == D("13582")
    assert at_base[:2] == (D("12289"), D("9923"))
    assert assessed(ZVE, L, 10)[4] - at_base[4] == D("-2315")
    assert income_tax(ZVE, TARIFF) - income_tax(base, TARIFF) == D("-2324")
    hook = next(
        c for c in phase4.raw("marginal_rate")["cases"] if c["id"] == "household_2026_contexts"
    )["expect"]
    assert D(hook["zve"]["amount"]) == RESULT_A - VORSORGE_A + RESULT_B - VORSORGE_B - 72
    assert D(hook["effect"]["effect"]) == assessed(ZVE, L, 10)[4] - at_base[4]
    assert D(hook["effect"]["breakdown_without"]["total"]) == at_base[4]


def test_filing_deadline_and_threshold():
    assert date(2027, 7, 31).weekday() == 5  # a Saturday
    assert deadline(2026) == date(2027, 8, 2)
    f = expect("return_estimate", "filing_deadline_next_working_day")["filing_required"]
    assert f["deadline"] == deadline(2026)
    assert expect("return_estimate", "childcare_2027")["filing_required"]["deadline"] == deadline(
        2027
    )
    gde = D("80045.44")
    threshold = (
        D("15340") * D("0.02")
        + (D("51130") - D("15340")) * D("0.03")
        + (gde - D("51130")) * D("0.04")
    )
    q = expect("questionnaire", "extraordinary_threshold_household")
    assert cent(threshold) == D("2537.12") == D(q["threshold"])
