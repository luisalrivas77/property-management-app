"""Writes tests/fixtures/phase10/*.yaml (docs/phases/phase-10.md §4). Run once from backend/:

    uv run python tests/fixture_gen_phase10.py

The households are invented (owners A and B), the amounts made up to hit the awkward cases,
never the mockups' numbers. Each month is turned into phase 9's household month through the
production functions (fixture_models_phase10.household); the Budget's arithmetic is then done
here a second time, in exact fractions, without importing rules/budget, so the rule test
compares two independent computations. Once committed, a fixture is the contract: it is never
regenerated to fit the rule (a change goes through a spec PR).
"""

from __future__ import annotations

import sys
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).parent))

import fixture_models_phase10 as phase10  # noqa: E402

from hausbuch.rules.health.bands import format_money, format_multiple, format_percent  # noqa: E402
from hausbuch.rules.health.figures import savings_rate  # noqa: E402
from hausbuch.rules.health.month import cashflow_rows  # noqa: E402
from hausbuch.rules.household.taxonomy import ONE_OFF, TAXONOMY  # noqa: E402
from hausbuch.schemas.allocation import AllocationCategory as C  # noqa: E402
from hausbuch.schemas.household import HouseholdGroup, Relevance  # noqa: E402

KEYS = ["need", "want", "one_off", "uncategorized", "saving"]
LABELS = {"need": "Notwendiges", "want": "Wünsche", "saving": "Sparen"}


def m(month: str, private: list, rental: list = (), saved_links: str = "0.00") -> dict:
    return {
        "month": month,
        "private": [{"category": c, "amount": a} for c, a in private],
        "saved_links": saved_links,
        "rental": [{"category": c, "amount": a} for c, a in rental],
    }


# --- The households ----------------------------------------------------------------------

# A and B rent their own home, own a let flat with a loan, a depot and a card.
SALARY = ("hh_salary", "4200.00")
CHILD = ("hh_child_benefit", "255.00")
FLAT = [
    ("rent_cold", "850.00"),
    ("loan_interest", "-410.00"),
    ("loan_principal", "-305.00"),
    ("hausgeld_verwaltung", "-38.00"),
    ("hausgeld_umlagefaehig", "-210.00"),
]
AWKWARD = [
    m(
        "2027-03",
        [
            SALARY,
            CHILD,
            ("hh_rent", "-1100.00"),
            ("hh_energy", "-95.40"),
            ("hh_broadcast_fee", "-55.08"),
            ("hh_groceries", "-612.37"),
            ("hh_restaurant", "-84.50"),
            ("hh_cash", "-100.00"),  # a cash withdrawal
            ("hh_credit_card", "-342.18"),  # a card settlement whose statements are not in
            ("hh_family_transfers", "-150.00"),  # money to family
            ("bank_fees", "-4.95"),
            ("depot_fee", "-3.20"),
            ("hh_tax_payment", "-230.00"),  # a tax-relevant payment
            ("uncategorized", "-27.99"),
            ("hh_refund", "19.99"),
            ("hh_investment_securities", "-300.00"),  # invested, never spent
        ],
        FLAT,
        saved_links="200.00",
    ),
    m(
        "2027-04",
        [
            SALARY,
            CHILD,
            ("hh_rent", "-1100.00"),
            ("hh_energy", "-95.40"),
            ("hh_groceries", "-588.10"),
            ("hh_restaurant", "-61.20"),
            ("hh_car_purchase", "-8500.00"),  # a one-off purchase
            ("hh_cash", "-50.00"),
            ("hh_credit_card", "-298.44"),
            ("hh_family_transfers", "-150.00"),
            ("bank_fees", "-4.95"),
            ("uncategorized", "-12.00"),
            ("hh_refund", "45.00"),
        ],
        [  # the flat stood empty: its shortfall lowers the income
            ("loan_interest", "-409.10"),
            ("loan_principal", "-305.90"),
            ("hausgeld_verwaltung", "-38.00"),
            ("hausgeld_umlagefaehig", "-210.00"),
        ],
    ),
    m(
        "2027-05",
        [
            ("hh_salary", "4350.00"),
            CHILD,
            ("hh_rent", "-1100.00"),
            ("hh_energy", "-95.40"),
            ("hh_groceries", "-640.03"),
            ("hh_restaurant", "-97.80"),
            ("hh_streaming", "-12.99"),
            ("hh_credit_card", "-310.07"),
            ("hh_family_transfers", "-150.00"),
            ("bank_fees", "-4.95"),
            ("hh_tax_payment", "-85.00"),
        ],
        [
            ("rent_cold", "850.00"),
            ("loan_interest", "-408.20"),
            ("loan_principal", "-306.80"),
            ("hausgeld_verwaltung", "-38.00"),
            ("hausgeld_umlagefaehig", "-210.00"),
        ],
    ),
]


def simple(month: str, salary: str, rent: str, groceries: str, dining: str, travel: str) -> dict:
    return m(
        month,
        [
            ("hh_salary", salary),
            ("hh_rent", rent),
            ("hh_energy", "-80.00"),
            ("hh_groceries", groceries),
            ("hh_restaurant", dining),
            ("hh_travel", travel),
        ],
    )


STEADY = [
    simple("2027-01", "4000.00", "-1200.00", "-600.00", "-150.00", "-250.00"),
    simple("2027-02", "4000.00", "-1200.00", "-610.00", "-140.00", "-250.00"),
    simple("2027-03", "4000.00", "-1200.00", "-590.00", "-160.00", "-250.00"),
]
# Σ E = 12000, housing 3840 (32 %), need 5640 (47 %), want 1200 (10 %), saving 5160 (43 %).
# Σ E = 15000, housing 4740 (31.6 %), need 7140 (47.6 %), want 1500 (10 %), saving 6360 (42.4 %).
EXACT = [
    simple("2027-01", "5000.00", "-1500.00", "-800.00", "-200.00", "-300.00"),
    simple("2027-02", "5000.00", "-1500.00", "-800.00", "-200.00", "-300.00"),
    simple("2027-03", "5000.00", "-1500.00", "-800.00", "-200.00", "-300.00"),
]
LOSS = [
    m("2027-01", [("hh_salary", "0.00"), ("hh_rent", "-900.00")], FLAT[1:]),
    m("2027-02", [("hh_rent", "-900.00"), ("hh_groceries", "-300.00")], FLAT[1:]),
    m("2027-03", [("hh_wage_replacement", "300.00"), ("hh_rent", "-900.00")], FLAT[1:]),
]
for month in LOSS:  # a 0 € line is no line
    month["private"] = [row for row in month["private"] if Decimal(row["amount"])]


# --- The arithmetic, once more ------------------------------------------------------------


def frac(value: Any) -> Fraction:
    return Fraction(Decimal(str(value)))


def half_up(value: Fraction, places: int) -> Decimal:
    scale = 10**places
    scaled = value * scale
    sign = -1 if scaled < 0 else 1
    whole, rest = divmod(abs(scaled), 1)
    whole = int(whole) + (1 if rest >= Fraction(1, 2) else 0)
    return Decimal(sign * whole).scaleb(-places).quantize(Decimal(1).scaleb(-places))


def cents(value: Fraction) -> int:
    return int(half_up(value * 100, 0))


def largest_remainder(exact: dict, target: int, order: list) -> dict:
    """exact in cents; floors plus the left cents to the largest remainders, ties by `order`."""
    import math

    floors = {k: math.floor(v) for k, v in exact.items()}
    left = target - sum(floors.values())
    assert 0 <= left <= len(exact)
    ranked = sorted(exact, key=lambda k: (-(exact[k] - floors[k]), order.index(k)))
    for k in ranked[:left]:
        floors[k] += 1
    return floors


def money(c: int) -> str:
    return f"{Decimal(c).scaleb(-2):.2f}"


def dec(value: Decimal) -> str:
    return f"{value:.2f}"


def klass(code: C, overrides: dict) -> str | None:
    row = TAXONOMY.get(code)
    default = (
        "need"
        if code is C.BANK_FEES
        else (row.budget_class.value if row and row.budget_class else None)
    )
    if default is None or code in ONE_OFF:
        return default
    return overrides.get(code.value, default)


def line_of(code: C, overrides: dict) -> str:
    if code in ONE_OFF:
        return "one_off"
    if code is C.UNCATEGORIZED:
        return "uncategorized"
    return klass(code, overrides)


def mark(label: str, share: Decimal | None, monthly: Decimal, income: Decimal, target, minimum):
    if target is None or share is None:
        return None
    limit = Decimal(target) / 100
    # decided as the sentence prints the share: percent, one place, half-up (PR #490 review)
    shown = (share * 100).quantize(Decimal("0.1"), ROUND_HALF_UP)
    met = shown >= Decimal(target) if minimum else shown <= Decimal(target)
    gap_points = shown - Decimal(target)
    gap_monthly = monthly - (limit * income).quantize(Decimal("0.01"), ROUND_HALF_UP)
    goal = f"eurem Ziel von {'mindestens' if minimum else 'höchstens'} {format_percent(limit)}"
    start = f"{label}: {format_percent(share)} eures Einkommens"
    if not gap_points:
        text = f"{start}, genau auf {goal}."
    else:
        points = f"{format_multiple(abs(gap_points))} Prozentpunkte"
        side = "über" if gap_points > 0 else "unter"
        amount = format_money(abs(gap_monthly))
        if met:
            room = "mehr" if minimum else "Luft"
            text = f"{start}, {points} {side} {goal} ({amount} im Monat {room})."
        else:
            text = (
                f"{start}, {points} {side} {goal}; das sind {amount} im Monat "
                f"{'zu wenig' if minimum else 'zu viel'}."
            )
    return {
        "colour": "green" if met else "amber",
        "gap_points": f"{gap_points or abs(gap_points):.1f}",
        "gap_monthly": dec(gap_monthly),
        "text": text,
    }


def expect(case: dict) -> dict:
    inputs = [phase10.MonthInput.model_validate(x) for x in case["months"]]
    built = [phase10.household(x, case["id"]) for x in inputs]
    months = [b[0] for b in built]
    window = [x.month for x in inputs]
    out: dict = {"window": window, "enough": len(window) >= 3}
    if len(window) < 3:
        out["todos"] = ["too_few_months"]
        return out
    overrides = case.get("overrides", {})
    targets = case.get("targets", {})
    n = len(months)
    totals: dict[C, Fraction] = {}
    out["months"] = []
    uncategorized_lines = 0
    for hm, lines in built:
        sums = dict.fromkeys(KEYS, Fraction(0))
        spent = Fraction(0)
        # Each code from the month's private lines, so a group netting to 0 keeps its codes
        # (PR #490 review); spending as month_cashflow counts it.
        for code, signed in cashflow_rows(lines):
            uncategorized_lines += code is C.UNCATEGORIZED
            row = TAXONOMY.get(code)
            if row is not None and (
                row.group is HouseholdGroup.INVESTMENT
                or row.relevance not in (Relevance.SPENDING, Relevance.TAX)
            ):
                continue
            amount = -signed
            sums[line_of(code, overrides)] += frac(amount)
            totals[code] = totals.get(code, Fraction(0)) + frac(amount)
            spent += frac(amount)
        assert spent == frac(hm.spending)
        sums["saving"] = frac(hm.income) - spent
        out["months"].append(
            {"month": hm.month, "income": dec(hm.income)}
            | {k: dec(Decimal(sums[k].numerator) / sums[k].denominator) for k in KEYS}
        )
    income_total = sum((frac(hm.income) for hm in months), Fraction(0))
    line_codes: dict[str, dict[C, Fraction]] = {k: {} for k in KEYS}
    for code, total in totals.items():
        if total:
            line_codes[line_of(code, overrides)][code] = total
    line_totals = {k: sum(v.values(), Fraction(0)) for k, v in line_codes.items()}
    line_totals["saving"] = income_total - sum(totals.values(), Fraction(0))
    shown = [k for k in KEYS if k in ("need", "want", "saving") or line_totals[k]]
    income_cents = cents(income_total / n)
    line_cents = largest_remainder({k: line_totals[k] * 100 / n for k in shown}, income_cents, KEYS)
    rental_cents = cents(sum((frac(hm.rental_result) for hm in months), Fraction(0)) / n)
    out["income"] = {
        "monthly": money(income_cents),
        "total": dec(Decimal(income_total.numerator) / income_total.denominator),
        "rental_monthly": money(rental_cents),
        "private_monthly": money(income_cents - rental_cents),
    }
    shares = income_total > 0

    def share(total: Fraction) -> str | None:
        return f"{half_up(total / income_total, 4):.4f}" if shares else None

    income_monthly = Decimal(money(income_cents))
    target_of = {"need": "needs_max", "want": "wants_max", "saving": "saving_min"}
    out["lines"] = []
    code_monthly: dict[C, int] = {}
    for key in shown:
        codes = line_codes[key]
        order = sorted(codes, key=lambda c: c.value)
        per_code = (
            {}
            if key == "saving"
            else largest_remainder({c: codes[c] * 100 / n for c in order}, line_cents[key], order)
        )
        code_monthly |= per_code
        row: dict = {
            "key": key,
            "monthly": money(line_cents[key]),
            "total": dec(Decimal(line_totals[key].numerator) / line_totals[key].denominator),
            "share": share(line_totals[key]),
        }
        if key != "saving":
            row["codes"] = {}
            for c in order:
                default = klass(c, {})
                shown_class = None if key in ("one_off", "uncategorized") else klass(c, overrides)
                row["codes"][c.value] = {
                    "monthly": money(per_code[c]),
                    "total": dec(Decimal(codes[c].numerator) / codes[c].denominator),
                    "budget_class": shown_class,
                    "overridden": shown_class is not None and shown_class != default,
                }
        target = targets.get(target_of.get(key, ""))
        row["target"] = target
        row["mark"] = mark(
            LABELS.get(key, ""),
            Decimal(row["share"]) if row["share"] else None,
            Decimal(row["monthly"]),
            income_monthly,
            target,
            key == "saving",
        )
        out["lines"].append(row)
    housing = sorted(
        (
            c
            for c in code_monthly
            if TAXONOMY.get(c) and TAXONOMY[c].group is HouseholdGroup.HOUSING
        ),
        key=lambda c: c.value,
    )
    housing_total = sum((totals[c] for c in housing), Fraction(0))
    housing_monthly = Decimal(money(sum(code_monthly[c] for c in housing)))
    out["housing"] = {
        "monthly": dec(housing_monthly),
        "total": dec(Decimal(housing_total.numerator) / housing_total.denominator),
        "share": share(housing_total),
        "codes": [c.value for c in housing],
        "target": targets.get("housing_max"),
        "mark": mark(
            "Wohnen",
            Decimal(share(housing_total)) if shares else None,
            housing_monthly,
            income_monthly,
            targets.get("housing_max"),
            False,
        ),
    }
    rate = savings_rate(months).rate
    out["savings_rate"] = None if rate is None else f"{rate:.4f}"
    out["no_shares"] = not shares
    out["todos"] = ["uncategorized_lines"] if uncategorized_lines else []
    return out


# --- The files ---------------------------------------------------------------------------

PARTITION = {
    "header": """\
# Phase 10 (docs/phases/phase-10.md D272, D273, §4): rules/budget/budget.budget over one window
# that holds every awkward case at once: a one-off purchase (hh_car_purchase), a refund
# (hh_refund, a negative amount in Wünsche), a tax-relevant payment (hh_tax_payment), a cash
# withdrawal (hh_cash), a card settlement (hh_credit_card), money to family, a bank fee (outside
# the taxonomy, a need), an uncategorised line (its own line and a to-do), a depot fee, the
# flat's shortfall (April, empty) and its principal (Sparen, never spending), and a transfer
# into the depot (saved, never spent).
#
# Input per month, as phase 9 reads it: `private` the private accounts' confirmed allocations
# signed as on the account, `saved_links` the transfer links' saved amount, `rental` the rental
# account's confirmed allocations. tests/fixture_models_phase10.household turns them into
# phase 9's HouseholdMonth with the production functions.
#
# expect: per month the exact partition (need + want + one_off + uncategorized + saving =
# income, D273); over the window E's monthly mean half-up, the lines' means by largest remainder
# (ties in the order need, want, one_off, uncategorized, saving) and each line's codes the same
# way (ties by the code's value); shares of Σ E to four places half-up; savings_rate is
# rules.health.figures.savings_rate(months).rate, which saving's share must equal.
# Computed by tests/fixture_gen_phase10.py for invented owners A and B; never regenerated to
# fit the rule.
""",
    "cases": [
        {
            "id": "every_awkward_case",
            "description": "three months, every case of §4 at once, no targets",
            "months": AWKWARD,
        },
    ],
}

OVERRIDES = {
    "header": """\
# Phase 10 (docs/phases/phase-10.md D272 (1), (2), D276, §4): the household's moves of a code
# to the other class (HouseholdLevers.budget_class_overrides). A moved code's mean moves with it
# from one line to the other; the housing share is unchanged (hh_energy moved to Wünsche still
# counts in Wohnen). A move rules/budget may not make (a one-off code, uncategorized) is
# ignored on read, so a stored row still reads; saving one is refused when the scenario is
# saved (services.household_scenarios.check_levers), tested in the API suite.
#
# Same input shape and arithmetic as budget_partition.yaml. Computed by
# tests/fixture_gen_phase10.py for invented owners A and B.
""",
    "cases": [
        {
            "id": "defaults",
            "description": "the taxonomy's classes",
            "months": AWKWARD,
        },
        {
            "id": "moved",
            "description": "restaurant to Notwendiges, energy to Wünsche; housing unchanged",
            "months": AWKWARD,
            "overrides": {"hh_restaurant": "need", "hh_energy": "want"},
        },
        {
            "id": "stale_moves_ignored",
            "description": "a one-off code and uncategorized cannot move; read as the defaults",
            "months": AWKWARD,
            "overrides": {"hh_car_purchase": "want", "uncategorized": "need"},
        },
    ],
}

MARKS = {
    "header": """\
# Phase 10 (docs/phases/phase-10.md D273, D274, §4): the marks against the household's own
# targets. Green when the target is met (at or below a maximum, at or above the saving minimum;
# an equal share meets it), amber when it is missed, never red; no mark without a target; no
# share and no mark when Σ E ≤ 0 (no_shares); below three closed months no figure at all and
# the window's to-do (D231). gap_points = (share − target) in points, one place half-up;
# gap_monthly = the line's monthly amount − the target's part of the monthly income (to the
# cent half-up); the sentence names both.
#
# Same input shape and arithmetic as budget_partition.yaml. Computed by
# tests/fixture_gen_phase10.py for invented owners A and B.
""",
    "cases": [
        {
            "id": "targets_met",
            "months": STEADY,
            "targets": {
                "needs_max": "50.0",
                "wants_max": "20.0",
                "saving_min": "30.0",
                "housing_max": "35.0",
            },
        },
        {
            "id": "targets_missed",
            "months": STEADY,
            "targets": {
                "needs_max": "40.0",
                "wants_max": "5.0",
                "saving_min": "50.0",
                "housing_max": "30.0",
            },
        },
        {
            "id": "targets_exactly_met",
            "description": "a share equal to its target meets it, and the sentence says so",
            "months": EXACT,
            "targets": {
                "needs_max": "47.6",
                "wants_max": "10.0",
                "saving_min": "42.4",
                "housing_max": "31.6",
            },
        },
        {
            "id": "targets_empty",
            "description": "only housing set: no mark on the lines",
            "months": STEADY,
            "targets": {"housing_max": "30.0"},
        },
        {
            "id": "income_not_positive",
            "description": "Σ E ≤ 0: the lines stand, no share, no mark",
            "months": LOSS,
            "targets": {"needs_max": "50.0", "saving_min": "20.0", "housing_max": "30.0"},
        },
        {
            "id": "too_few_months",
            "months": STEADY[:2],
            "targets": {"needs_max": "50.0"},
        },
    ],
}

REFERENCES = """\
# Phase 10 (docs/phases/phase-10.md D275, D276, §3, §4): rules/budget/references.yaml and the
# goals sheet's one tap (rules/budget/references.fill). `references`: the keys and kinds in the
# file's order. A split's tap writes its three numbers into the form (needs_max, wants_max,
# saving_min) and leaves the housing target as it was, empty or typed; a housing reference
# fills nothing (refused). Nothing is stored by a tap.
references:
  - {key: split_50_30_20, kind: split}
  - {key: split_60_20_20, kind: split}
  - {key: split_70_20_10, kind: split}
  - {key: housing_iwu_mieterbund, kind: housing}
  - {key: housing_destatis_eu_silc, kind: housing}
  - {key: housing_sparkasse_third, kind: housing}
cases:
  - id: fill_empty_form
    reference: split_50_30_20
    expect:
      targets: {needs_max: "50", wants_max: "30", saving_min: "20", housing_max: null}
  - id: fill_keeps_housing
    description: the typed housing target stays; the other three are replaced
    reference: split_70_20_10
    form: {needs_max: "55.0", wants_max: null, saving_min: "15.0", housing_max: "35.0"}
    expect:
      targets: {needs_max: "70", wants_max: "20", saving_min: "10", housing_max: "35.0"}
  - id: fill_60_20_20
    reference: split_60_20_20
    expect:
      targets: {needs_max: "60", wants_max: "20", saving_min: "20", housing_max: null}
  - id: housing_fills_nothing
    reference: housing_iwu_mieterbund
    expect: {refused: true}
"""


def write(name: str, spec: dict) -> None:
    cases = []
    for case in spec["cases"]:
        case = dict(case)
        case["expect"] = expect(case)
        cases.append(case)
    body = yaml.safe_dump(
        {"cases": cases}, sort_keys=False, allow_unicode=True, width=100, default_flow_style=None
    )
    (phase10.FIXTURES / f"{name}.yaml").write_text(spec["header"] + body, encoding="utf-8")


def main() -> None:
    phase10.FIXTURES.mkdir(exist_ok=True)
    write("budget_partition", PARTITION)
    write("budget_overrides", OVERRIDES)
    write("budget_marks", MARKS)
    (phase10.FIXTURES / "budget_references.yaml").write_text(REFERENCES, encoding="utf-8")


if __name__ == "__main__":
    main()
