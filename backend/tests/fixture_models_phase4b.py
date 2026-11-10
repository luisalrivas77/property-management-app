"""Shapes of the phase 4b YAML rule fixtures (docs/phases/phase-4b.md §5, §11).

Same contract as fixture_models_phase3.py and fixture_models_phase4.py: loading a fixture
through these models is itself a test (tests/test_fixtures_valid.py), so a fixture that does
not match the spec schemas fails before any rule exists. Inputs are the §7 schemas the §5
signatures take (Payslip, WageCertificate, CommuteFact, …, OwnerReturnFacts,
HouseholdTaxContext, OwnerTaxProfile, QuestionnaireAnswer). Every expectation is recomputed on
load from the §5 formulas by the small reference functions below (`_projection`, `_commute`,
…, `_assessed`, `_estimate`), with the rule set from `registry.anlage_n` and the tariff from
`registry.tariff`, so a hand-calculation slip fails here and not in a rule test later. They
are the spec's arithmetic written once more, not the rules: step 1 builds the rules in
`rules/tax/anlage_n/`, `rules/tax/return_estimate.py` and `rules/tax/questionnaire.py` and its
tests compare them with these expectations. `tests/test_phase4b_fixture_arithmetic.py`
recomputes the §5.8–§5.10 figures a third time, independently of this module, with the real
`income_tax` and `soli`.

Comparison rules for the rule tests (step 1):

- Amounts compare exactly. Warnings compare as a multiset of codes; the params an expectation
  names must match, others are free (phase 3's WarningExpect).
- anlage_n.yaml `lines`: exactly the money lines the draft emits, by key, with their amounts:
  `pay.gross`, `wk.itemised`, `wk.allowance` and `wk.applied` always; the other pay lines,
  `commute.employer_tax_free`/`_flat_taxed`, `dues.associations`, `equipment.total`,
  `work_room.amount`, `training.total`, `other.total` (telecom plus the other expenses) when
  not zero; `home_office.days_*` carry the day counts. Route lines (`commute.route_N.*`) are
  not asserted here (commute.yaml asserts the amounts). Zeilen and Kennzahlen never are.
- An `expect` field left out (None) is not asserted; an `error` names the RuleError code.

Interpretations written down in step 0 (each a place to push back in the step 1 review):

- Payslip projection without a year-to-date figure: `month × (month number + remaining)`, i.e.
  the month's figure for every paid month of the year (§5.1 "month × 12 − gap"). `period_from`
  is January unless the earliest payslip's ytd equals its month (pay started that month). The
  projection carries no employer pension share (Nr. 22a = 0): a payslip does not print it, and
  for an employee below the cap the pension deduction is the employee share either way.
  `tax_free_benefits` is informational (a Kurzarbeitergeld or Zuschuss is on the certificate's
  Nr. 15 once it arrives).
- `anlage_n.periods_overlap`: two certificates of one owner whose periods overlap, unless one
  of them is tax class VI (a second job runs alongside the first by definition).
- A `vorsorge_override` replaces the three figures as typed; `krankengeld_entitlement` is the
  user's statement that the typed basis needs no 4 % cut, and the rule never cuts a typed
  figure.
- Children (D109): a child counts for the months from max(birth month, January) to December,
  and up to the month of its 18th birthday; a child who turns 18 in or before the year raises
  `return.child_over_18`. `return.kindergeld_apply` is raised in a child's birth year.
- Filing (D101): reasons are German sentences; when nothing requires a return the one reason
  is "Antragsveranlagung". The deadline moves past Saturdays, Sundays and the nationwide
  public holidays (§108 Abs. 3 AO; Q-O24).
- A return estimate for a year without its own Anlage N rule set uses the latest earlier one
  (as phase 4's projection does with the Anlage V rule set); `registry.anlage_n` itself has no
  fallback yet (childcare_2027).
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date
from decimal import ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from functools import cache
from pathlib import Path
from typing import Literal, Self
from uuid import UUID

import yaml
from fixture_models import Lenient, RuleErrorExpect
from fixture_models_phase3 import WarningExpect
from pydantic import Field, model_validator

from hausbuch.rules.tax import registry
from hausbuch.rules.tax.tariff import church_tax, income_tax, soli
from hausbuch.schemas.common import Money, NonNegativeMoney, Schema, TaxYear
from hausbuch.schemas.employment import (
    AnlageNRuleSet,
    AssessedTax,
    ChildFact,
    ChildrenResult,
    CommuteFact,
    EquipmentFact,
    FilingCheck,
    HomeOfficeFact,
    HouseholdTaxContext,
    MissingItem,
    OtherExpenseFact,
    OwnerReturnFacts,
    Payslip,
    ProgressionInfo,
    QuestionnaireAnswer,
    QuestionnaireProgress,
    ReturnInputCreate,
    ReturnInputFact,
    TelecomFact,
    VorsorgeOverride,
    WageCertificate,
    Withheld,
    WorkRoomFact,
)
from hausbuch.schemas.enums import ReturnInputKind as K
from hausbuch.schemas.performance import OwnerTaxProfile, Tariff
from hausbuch.schemas.tax import TaxInputCreate, TaxInputFact

FIXTURES = Path(__file__).parent / "fixtures" / "phase4b"
CENT = Decimal("0.01")
ZERO = Decimal("0")
_NOT_ZERO = Decimal("0.00")


@cache
def raw(name: str) -> dict:
    with (FIXTURES / f"{name}.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


class _Fixture(Schema):
    ids: dict[str, UUID] = Field(default_factory=dict)
    bases: dict[str, object] = Field(default_factory=dict)  # YAML anchors merged into cases


class Refused(Exception):
    """A RuleError of the rules, by code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


Warn = tuple[str, dict[str, str]]


# --- Small helpers -------------------------------------------------------------------------


def cents(x: Decimal) -> Decimal:
    return x.quantize(CENT, ROUND_HALF_UP)


def floor_euros(x: Decimal) -> Decimal:
    return x.to_integral_value(ROUND_FLOOR)


def anlage_n_rules(year: int) -> AnlageNRuleSet:
    """The year's rule set, else the latest earlier one (see the module docstring)."""
    return registry.anlage_n(max(y for y in registry.anlage_n_years() if y <= year))


def de(x: Decimal) -> str:
    """1234.5 → "1.234,50" (the German amount in a reason sentence)."""
    return f"{x:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def current(rows: list) -> list:
    """The rows in force: not superseded by another row, not withdrawn."""
    superseded = {r.supersedes_id for r in rows if r.supersedes_id is not None}
    return [r for r in rows if r.id not in superseded and not r.withdrawn]


def _check(what: str, expected, actual) -> None:
    if expected is not None and expected != actual:
        raise ValueError(f"{what}: expected {expected!r}, computed {actual!r}")


def _check_warnings(expected: list[WarningExpect], actual: list[Warn], what: str) -> None:
    want = sorted(str(w.code.value) for w in expected)
    got = sorted(code for code, _ in actual)
    if want != got:
        raise ValueError(f"{what}: warnings {want}, computed {got}")
    for w in expected:
        if not any(
            code == w.code.value and all(params.get(k) == v for k, v in w.params.items())
            for code, params in actual
        ):
            raise ValueError(f"{what}: no {w.code.value} with params {w.params}")


def _check_error(expected: RuleErrorExpect | None, run, what: str):
    """Run the reference; an expected error must be raised, an unexpected one fails."""
    try:
        result = run()
    except Refused as refused:
        if expected is None or expected.code != refused.code:
            raise ValueError(f"{what}: computed error {refused.code}") from None
        return None
    if expected is not None:
        raise ValueError(f"{what}: expected error {expected.code}, computed a result")
    return result


# --- §5.1 Payslip projection ---------------------------------------------------------------

_CERT_FIELD = {
    "gross": "gross",
    "income_tax": "income_tax",
    "soli": "soli",
    "church_tax": "church_tax",
    "pension_employee": "pension_employee",
    "health_employee": "health_employee",
    "care_employee": "care_employee",
    "unemployment_employee": "unemployment_employee",
}


def _projection(payslips: list[Payslip], year: int) -> tuple[WageCertificate, list[Warn]]:
    """projected_certificate for one owner and employer (§5.1, D105)."""
    slips = sorted(payslips, key=lambda p: p.period_month)
    last, first = slips[-1], slips[0]
    m = last.period_month.month
    warnings: list[Warn] = []
    if last.gross.ytd is None:
        warnings.append(("anlage_n.payslip_no_ytd", {"month": f"{year}-{m:02d}"}))
    for before, after in zip(slips, slips[1:], strict=False):
        if before.gross.ytd is None or after.gross.ytd is None:
            continue
        if after.gross.ytd != before.gross.ytd + after.gross.month:
            warnings.append(
                ("anlage_n.payslip_gap", {"month": f"{year}-{after.period_month.month:02d}"})
            )
    figures: dict[str, Decimal] = {}
    for name, field in _CERT_FIELD.items():
        pair = getattr(last, name)
        if pair.ytd is None:
            figures[field] = pair.month * (m + last.months_remaining_paid)
        else:
            figures[field] = pair.ytd + pair.month * last.months_remaining_paid
    starts_here = first.gross.ytd is not None and first.gross.ytd == first.gross.month
    end_month = m + last.months_remaining_paid
    cert = WageCertificate(
        period_from=first.period_month if starts_here else date(year, 1, 1),
        period_to=date(year, end_month, calendar.monthrange(year, end_month)[1]),
        employer=last.employer,
        tax_class=last.tax_class,
        **figures,
    )
    return cert, warnings


def _pay(
    certificates: list[WageCertificate], payslips: list[Payslip], year: int
) -> tuple[list[WageCertificate], list[WageCertificate], list[Warn]]:
    """(the certificates, the projections of employers without one, the warnings)."""
    certified = {c.employer for c in certificates}
    by_employer: dict[str, list[Payslip]] = defaultdict(list)
    for p in payslips:
        if p.employer not in certified:
            by_employer[p.employer].append(p)
    projected, warnings = [], []
    for employer in sorted(by_employer):
        cert, w = _projection(by_employer[employer], year)
        projected.append(cert)
        warnings += w
    return list(certificates), projected, warnings


# --- §5.2–§5.5 Werbungskosten pieces -------------------------------------------------------


def _rate(rs: AnlageNRuleSet) -> Decimal:
    if len(rs.commute_rates) != 1:
        raise NotImplementedError("the 2026 rule set has one tier from km 1")
    return rs.commute_rates[0].rate


def _commute(routes: list[CommuteFact], certs: list[WageCertificate], rs: AnlageNRuleSet) -> dict:
    rate = _rate(rs)
    car = sum((Decimal(r.days * r.km_car) * rate for r in routes), ZERO)
    rest = sum((Decimal(r.days * (r.km_collective + r.km_other)) * rate for r in routes), ZERO)
    total = car + min(rest, rs.commute_cap_non_car)
    notes = []
    tickets = sum((r.public_transport_costs or ZERO for r in routes), ZERO)
    if tickets > total:
        total = tickets
        notes.append("public_transport_costs_exceed")
    offset = sum((c.employer_commute_tax_free + c.employer_commute_flat_taxed for c in certs), ZERO)
    warnings: list[Warn] = []
    if len(routes) > 3:
        warnings.append(("anlage_n.too_many_routes", {"routes": str(len(routes))}))
    return {
        "pauschale_car": cents(car),
        "pauschale_rest": cents(rest),
        "total": cents(total),
        "employer_offset": offset,
        "deductible": max(ZERO, cents(total) - offset),
        "notes": notes,
        "warnings": warnings,
    }


def _home_office(
    fact: HomeOfficeFact, commute_days: int, room: WorkRoomFact | None, rs: AnlageNRuleSet
) -> tuple[Decimal, list[Warn]]:
    if room is not None and room.months == 12:
        raise Refused("anlage_n.work_room_excludes_day_rate")
    days = fact.days_other_workplace + fact.days_no_other_workplace
    full = rs.home_office_day_rate * days
    warnings: list[Warn] = []
    if commute_days + fact.days_other_workplace > rs.weekdays_in_year:
        warnings.append(
            (
                "anlage_n.days_implausible",
                {"days": str(commute_days + fact.days_other_workplace)},
            )
        )
    if full > rs.home_office_cap:
        warnings.append(("anlage_n.home_office_capped", {"days": str(days)}))
    return min(full, rs.home_office_cap), warnings


def _work_room(fact: WorkRoomFact, rs: AnlageNRuleSet) -> dict:
    actual = (
        cents(
            (fact.rent_year + fact.running_costs_year)
            * fact.room_m2
            * fact.months
            / (fact.home_m2 * 12)
        )
        + fact.room_only_costs_year
    )
    flat = cents(rs.work_room_flat * fact.months / 12)
    if fact.prefer == "actual" or (fact.prefer is None and actual > flat):
        method, amount = "actual", actual
    else:
        method, amount = "flat", flat
    warnings: list[Warn] = [("anlage_n.work_room_centre", {})]
    if method == "actual":
        warnings.append(("anlage_n.work_room_records", {}))
    return {
        "actual": actual,
        "flat": flat,
        "method": method,
        "amount": amount,
        "warnings": warnings,
    }


def _months_by_year(start: date, months: int) -> list[tuple[int, int]]:
    """The AfA months per calendar year from the purchase month, inclusive."""
    out, year, month, left = [], start.year, start.month, months
    while left > 0:
        n = min(13 - month, left)
        out.append((year, n))
        left -= n
        year, month = year + 1, 1
    return out


def _equipment(item: EquipmentFact, year: int, rs: AnlageNRuleSet) -> Decimal:
    """Year Y's amount of one item (§5.5, D108)."""
    net = item.net if item.net is not None else cents(item.gross / (1 + rs.default_vat_rate))
    share = Decimal(item.professional_share.numerator) / item.professional_share.denominator
    base = cents(item.gross * share)
    bought = item.bought_on.year
    life = rs.useful_life(item.item_class)
    if item.item_class == "subscription":
        return base if year == bought else _NOT_ZERO
    if net <= rs.gwg_limit_net or life.full_in_year:
        return base if year == bought else _NOT_ZERO
    months = item.useful_life_months or life.months
    if months is None:
        raise Refused("anlage_n.useful_life_missing")
    plan = _months_by_year(item.bought_on, months)
    slices, done = {}, ZERO
    for i, (y, n) in enumerate(plan):
        slices[y] = base - done if i == len(plan) - 1 else cents(base * n / months)
        done += slices[y]
    if item.disposed_on is not None:
        d = item.disposed_on
        if year > d.year:
            return _NOT_ZERO
        if year == d.year:
            start = item.bought_on.month if year == bought else 1
            n = d.month - start + 1
            last_year = plan[-1][0]
            return slices[year] if year == last_year else cents(base * n / months)
    return slices.get(year, _NOT_ZERO)


def _telecom(fact: TelecomFact, rs: AnlageNRuleSet) -> Decimal:
    flat = min(cents(fact.bills_year * rs.telecom_share), rs.telecom_cap_monthly * fact.months)
    return max(ZERO, flat - fact.employer_reimbursed)


# --- §5.6 The Anlage N draft -----------------------------------------------------------------


def _values(rows: list[ReturnInputFact], kind: K) -> list:
    return [r.value for r in rows if r.kind is kind]


def _certs_of(facts: OwnerReturnFacts, year: int) -> tuple[list, list, list[Warn]]:
    rows = [r for r in current(facts.inputs) if r.tax_year == year]
    return _pay(_values(rows, K.WAGE_CERTIFICATE), _values(rows, K.PAYSLIP), year)


def _draft_n(facts: OwnerReturnFacts, year: int) -> dict:
    """draft_n (§5.6). Returns the figures, the lines, the components and the warnings."""
    rs = anlage_n_rules(year)
    tariff = registry.tariff(year)
    rows = current(facts.inputs)
    this_year = [r for r in rows if r.tax_year == year]
    real, projected, warnings = _certs_of(facts, year)
    certs = real + projected
    if certs:
        gross = sum((c.gross for c in certs), ZERO)
        status, source = ("estimate", "payslip") if projected else ("certified", "certificate")
    elif facts.expected_gross_pay is not None:
        gross = facts.expected_gross_pay.value.amount
        status, source = "estimate", "expected"
        warnings.append(("anlage_n.certificate_missing", {}))
    else:
        raise Refused("anlage_n.no_pay")

    routes = _values(this_year, K.COMMUTE)
    commute = _commute(routes, certs, rs)
    warnings += commute["warnings"]
    rooms = _values(this_year, K.WORK_ROOM)
    room = rooms[0] if rooms else None
    room_amount = ZERO
    if room is not None:
        wr = _work_room(room, rs)
        room_amount = wr["amount"]
        warnings += wr["warnings"]
    offices = _values(this_year, K.HOME_OFFICE)
    office_amount, office = ZERO, offices[0] if offices else None
    if office is not None:
        office_amount, w = _home_office(office, sum(r.days for r in routes), room, rs)
        warnings += w
    equipment = sum((_equipment(v, year, rs) for v in _values(rows, K.WORK_EQUIPMENT)), ZERO)
    telecom = sum((_telecom(v, rs) for v in _values(this_year, K.TELECOM)), ZERO)
    associations = _values(this_year, K.PROFESSIONAL_ASSOCIATION)
    dues = sum((a.amount for a in associations), ZERO)
    union = sum((a.amount for a in associations if a.is_union), ZERO)
    training_rows = [r for r in this_year if r.kind is K.TRAINING]
    training = sum((r.value.amount for r in training_rows), ZERO)
    for r in training_rows:
        if r.document_id is None:
            warnings.append(("anlage_n.receipt_missing", {"kind": "training"}))
    other = sum(
        (
            v.amount if v.amount is not None else rs.account_fee_flat
            for v in _values(this_year, K.OTHER_EXPENSE)
        ),
        ZERO,
    )
    components = {
        "commute": commute["deductible"],
        "home_office": office_amount,
        "work_room": room_amount,
        "equipment": equipment,
        "telecom": telecom,
        "dues": dues,
        "training": training,
        "other": other,
    }
    itemised = sum(components.values(), ZERO)
    allowance = min(tariff.employee_allowance, gross)
    if rs.union_dues_beside_allowance:
        applied = union + max(allowance, itemised - union)
    else:
        applied = max(allowance, itemised)

    if rs.form.status.value == "provisional":
        warnings.append(("anlage_n.form_provisional", {"form_year": str(rs.form.form_year)}))
    marked = any(c.church_tax > 0 or c.church_tax_marked for c in real)
    if real and marked != facts.church_tax:
        warnings.append(("anlage_n.church_mismatch", {}))
    if any(c.tax_class == 6 for c in certs):
        warnings.append(("anlage_n.tax_class_6", {}))
    main = [c for c in real if c.tax_class != 6]
    for i, a in enumerate(main):
        for b in main[i + 1 :]:
            if a.period_from <= b.period_to and b.period_from <= a.period_to:
                warnings.append(("anlage_n.periods_overlap", {}))
    if room is not None and not routes:
        warnings.append(("anlage_n.first_workplace_check", {}))

    def total(field: str) -> Decimal:
        return sum((getattr(c, field) for c in certs), ZERO)

    lines: dict[str, Decimal] = {"pay.gross": gross}
    optional = {
        "pay.income_tax": total("income_tax"),
        "pay.soli": total("soli"),
        "pay.church_tax": total("church_tax"),
        "pay.church_tax_spouse": total("church_tax_spouse"),
        "pay.replacement_benefits": total("replacement_benefits"),
        "commute.employer_tax_free": total("employer_commute_tax_free"),
        "commute.employer_flat_taxed": total("employer_commute_flat_taxed"),
        "dues.associations": dues,
        "equipment.total": equipment,
        "work_room.amount": room_amount,
        "home_office.days_other_workplace": Decimal(office.days_other_workplace if office else 0),
        "home_office.days_no_other_workplace": Decimal(
            office.days_no_other_workplace if office else 0
        ),
        "training.total": training,
        "other.total": telecom + other,
    }
    lines |= {k: v for k, v in optional.items() if v != 0}
    lines |= {"wk.itemised": itemised, "wk.allowance": allowance, "wk.applied": applied}
    return {
        "status": status,
        "gross_source": source,
        "gross": gross,
        "wk_itemised": itemised,
        "wk_union": union,
        "wk_allowance": allowance,
        "wk_applied": applied,
        "result": gross - applied,
        "replacement_benefits": total("replacement_benefits"),
        "components": components,
        "lines": lines,
        "warnings": warnings,
        "certificates": certs,
        "projected": bool(projected),
    }


# --- §5.7 Vorsorge -------------------------------------------------------------------------


def _vorsorge(facts: OwnerReturnFacts, year: int) -> dict:
    rs = anlage_n_rules(year)
    tariff = registry.tariff(year)
    this_year = [r for r in current(facts.inputs) if r.tax_year == year]
    overrides = _values(this_year, K.VORSORGE_OVERRIDE)
    if overrides:
        o: VorsorgeOverride = overrides[0]
        figures = {"pension": o.pension, "health_care_basis": o.health_care_basis, "other": o.other}
        source = "override"
    else:
        real, projected, _ = _certs_of(facts, year)
        certs = real + projected
        source = "payslip" if projected else "certificate" if certs else "none"

        def total(field: str) -> Decimal:
            return sum((getattr(c, field) for c in certs), ZERO)

        contributions = (
            total("pension_employee")
            + total("pension_employee_professional")
            + total("pension_employer")
        )
        pension = max(
            ZERO, min(contributions, rs.pension_max_contribution) - total("pension_employer")
        )
        share = tariff.social_insurance.health_deductible_share
        basis = cents(total("health_employee") * share) + total("care_employee")
        other_raw = total("unemployment_employee") + sum(
            (v.amount for v in _values(this_year, K.OTHER_INSURANCE)), ZERO
        )
        cap = rs.other_vorsorge_cap_employee
        other = max(basis, min(cap, basis + other_raw)) - basis
        figures = {"pension": pension, "health_care_basis": basis, "other": other}
    return figures | {"deductible": sum(figures.values(), ZERO), "source": source}


# --- §5.10 Children and the assessed tax ---------------------------------------------------


def _child_months(child: ChildFact, year: int) -> tuple[int, list[Warn]]:
    born = child.born_on
    first = 1 if born.year < year else born.month if born.year == year else 13
    adult = born.year + 18
    last = 12 if adult > year else born.month if adult == year else 0
    warnings: list[Warn] = []
    if adult <= year:
        warnings.append(("return.child_over_18", {"born_year": str(born.year)}))
    if born.year == year:
        warnings.append(("return.kindergeld_apply", {}))
    return max(0, last - first + 1), warnings


def _special_rate(z: Decimal, benefits: Decimal, tariff: Tariff) -> Decimal:
    """D102: income_tax(z + L) ÷ (z + L), cut to six decimals."""
    fictitious = z + benefits
    if fictitious <= 0:
        return ZERO
    return (income_tax(fictitious, tariff) / fictitious).quantize(Decimal("0.000001"), ROUND_DOWN)


def _est(z: Decimal, benefits: Decimal, tariff: Tariff) -> Decimal:
    if benefits > 0:
        return floor_euros(z * _special_rate(z, benefits, tariff))
    return income_tax(z, tariff)


def _assessed(
    zve: Decimal,
    household: HouseholdTaxContext | None,
    owners: list[OwnerTaxProfile],
    year: int,
    allowance_unused: Decimal = ZERO,
) -> tuple[AssessedTax, list[Warn]]:
    """assessed_tax (§5.10). Without a context: total_tax."""
    tariff = registry.tariff(year)
    z = floor_euros(zve)
    h = household or HouseholdTaxContext()
    benefits = h.replacement_benefits
    warnings: list[Warn] = []
    months = 0
    for child in h.children:
        n, w = _child_months(child, year)
        months += n
        warnings += w
    est_without = _est(z, benefits, tariff)
    children = None
    est_with, after, est = est_without, z, est_without
    if months:
        figures = tariff.children
        assert figures is not None
        kindergeld = figures.kindergeld_monthly * months
        allowances = cents(
            (figures.allowance_per_parent + figures.bea_per_parent) * 2 * months / 12
        )
        after = max(ZERO, floor_euros(z - allowances))
        est_with = _est(after, benefits, tariff)
        saving = est_without - est_with
        favourable = "allowances" if saving > kindergeld else "kindergeld"
        est = est_with + kindergeld if favourable == "allowances" else est_without
        children = ChildrenResult(
            months=months,
            kindergeld=kindergeld,
            allowances=allowances,
            saving=saving,
            favourable=favourable,
        )
    credit = min(h.household_services_credit, est)
    base = max(ZERO, est_with - credit)
    s = soli(base, tariff)
    c = church_tax(base, owners, tariff)
    progression = None
    if benefits > 0:
        progression = ProgressionInfo(
            benefits=benefits,
            allowance_unused=allowance_unused,
            rate=_special_rate(z, benefits, tariff),
            fictitious_zve=z + benefits,
            fictitious_tax=income_tax(z + benefits, tariff),
        )
    assessed = AssessedTax(
        zve=z,
        zve_after_allowances=after,
        est_without=est_without,
        est_with=est_with,
        est=est,
        credit_35a=credit,
        est_final=est - credit,
        progression=progression,
        children=children,
        soli=s,
        church=c,
        total=est - credit + s + c,
    )
    return assessed, warnings


# --- §5.8 The whole-return estimate --------------------------------------------------------

_HOLIDAYS = {(1, 1), (5, 1), (10, 3), (12, 25), (12, 26)}  # nationwide, fixed dates


def filing_deadline(year: int) -> date:
    """31 July of the following year, moved to the next working day (§108 Abs. 3 AO)."""
    d = date(year + 1, 7, 31)
    while d.weekday() >= 5 or (d.month, d.day) in _HOLIDAYS:
        d = date.fromordinal(d.toordinal() + 1)
    return d


def _estimate(case: EstimateCase) -> dict:
    year = case.tax_year
    tariff = registry.tariff(year)
    facts = case.resolved_owners()
    household_rows = current(case.household)
    drafts, vorsorge = {}, {}
    for f in facts:
        drafts[f.owner_id] = _draft_n(f, year)
        vorsorge[f.owner_id] = _vorsorge(f, year)
    gde = sum((d["result"] for d in drafts.values()), ZERO) + sum(
        (p.result for p in case.properties), ZERO
    )
    warnings: list[Warn] = []
    # Lohnersatzleistungen per owner, reduced by the unused Pauschbetrag (§32b Abs. 2)
    benefits, unused_total = ZERO, ZERO
    for f in facts:
        d = drafts[f.owner_id]
        rows = [r for r in current(f.inputs) if r.tax_year == year]
        typed = _values(rows, K.REPLACEMENT_INCOME)
        own = d["replacement_benefits"] + sum((v.amount for v in typed), ZERO)
        unused = min(own, max(ZERO, tariff.employee_allowance - d["gross"]))
        benefits += own - unused
        unused_total += unused
        if any(v.benefit == "mutterschaftsgeld" for v in typed) and d["replacement_benefits"] > 0:
            warnings.append(("return.benefit_possible_double", {}))
    # Sonderausgaben
    vorsorge_total = sum((v["deductible"] for v in vorsorge.values()), ZERO)
    church_paid = sum(
        (c.church_tax + c.church_tax_spouse for d in drafts.values() for c in d["certificates"]),
        ZERO,
    )
    donated = sum((v.amount for v in _values(household_rows, K.SPECIAL_EXPENSE)), ZERO)
    cap = (tariff.donation_cap_share * max(ZERO, gde)).quantize(CENT, ROUND_DOWN)
    donations = min(donated, cap)
    if donated > cap:
        warnings.append(("return.donation_over_cap", {"excess": str(donated - cap)}))
    children = _values(household_rows, K.CHILD)
    child_rows = {r.id: r.value for r in household_rows if r.kind is K.CHILD}
    childcare = ZERO
    care = tariff.childcare
    assert care is not None
    for v in _values(household_rows, K.CHILDCARE_COST):
        child = child_rows[v.child_id]
        if child.born_on <= date(year - care.max_age, 1, 1):  # 14 before the year began
            raise Refused("return.childcare_age")
        childcare += min(cents(v.amount * care.share), care.cap_per_child)
    services = _values(household_rows, K.HOUSEHOLD_SERVICES)
    if any(not v.paid_to_account for v in services):
        raise Refused("return.household_services_cash")
    caps = tariff.household_services
    assert caps is not None
    credit = ZERO
    for service_class in ("handwerker", "haushaltsnah", "minijob"):
        labour = sum((v.labour_cost for v in services if v.service_class == service_class), ZERO)
        credit += min(cents(labour * caps.rate), getattr(caps, service_class))
    allowance = tariff.special_expenses_allowance * 2
    special = vorsorge_total + max(allowance, church_paid + donations) + childcare
    zve = max(ZERO, floor_euros(gde - special))
    household = HouseholdTaxContext(
        children=children,
        replacement_benefits=benefits,
        church_tax_paid=church_paid,
        donations=donations,
        childcare=childcare,
        household_services_credit=credit,
    )
    assessed, w = _assessed(zve, household, case.owners, year, unused_total)
    warnings += w
    certs = [c for d in drafts.values() for c in d["certificates"]]
    withheld = Withheld(
        income_tax=sum((c.income_tax for c in certs), ZERO),
        soli=sum((c.soli for c in certs), ZERO),
        church_tax=sum((c.church_tax + c.church_tax_spouse for c in certs), ZERO),
        total=sum(
            (c.income_tax + c.soli + c.church_tax + c.church_tax_spouse for c in certs), ZERO
        ),
    )
    # §46 Abs. 2
    threshold = tariff.filing_threshold_progression
    reasons = []
    rental = sum((p.result for p in case.properties), ZERO)
    if rental > threshold:
        reasons.append(f"Einkünfte ohne Lohnsteuerabzug {de(rental)} € > {de(threshold)} €")
    if benefits > threshold:
        reasons.append(f"Lohnersatzleistungen {de(benefits)} € > {de(threshold)} €")
    both_paid = sum(1 for d in drafts.values() if d["gross"] > 0) >= 2
    if both_paid and any(c.tax_class in (5, 6) for c in certs):
        reasons.append("Steuerklasse V oder VI")
    filing = FilingCheck(
        required=bool(reasons),
        reasons=reasons or ["Antragsveranlagung"],
        deadline=filing_deadline(year),
    )
    if filing.required:
        warnings.append(("return.filing_required", {}))
    # what the estimate still waits for
    missing = [
        MissingItem(kind="question", key=o.key, owner_id=o.owner_id, property_id=o.property_id)
        for o in case.questionnaire.open
    ]
    if case.questionnaire.open:
        warnings.append(("return.questionnaire_open", {"open": str(len(case.questionnaire.open))}))
    for f in facts:
        if drafts[f.owner_id]["gross_source"] != "certificate":
            missing.append(
                MissingItem(kind="document", key="wage_certificate", owner_id=f.owner_id)
            )
        typed = {
            v.benefit
            for v in _values(
                [r for r in current(f.inputs) if r.tax_year == year], K.REPLACEMENT_INCOME
            )
        }
        for a in current(case.answers):
            if (
                a.question_key == "owner.replacement_income"
                and a.owner_id == f.owner_id
                and a.answer == "yes"
                and a.choice not in (None, "none")
                and a.choice not in typed
            ):
                missing.append(
                    MissingItem(
                        kind="benefit_letter", key="replacement_income", owner_id=f.owner_id
                    )
                )
    counted = sum(1 for child in children if _child_months(child, year)[0] > 0)
    real = [
        c
        for f in facts
        for c in _values([r for r in current(f.inputs) if r.tax_year == year], K.WAGE_CERTIFICATE)
    ]
    if any(c.child_allowance_count != counted for c in real):
        warnings.append(("return.children_mismatch", {}))
    status = "certified" if all(d["status"] == "certified" for d in drafts.values()) else "estimate"
    return {
        "status": status,
        "total_income": gde,
        "vorsorge_total": vorsorge_total,
        "church_tax_paid": church_paid,
        "donations": donations,
        "childcare": childcare,
        "special_expenses": special,
        "zve": zve,
        "assessed": assessed,
        "withheld": withheld,
        "result": assessed.total - withheld.total,
        "filing_required": filing,
        "missing": missing,
        "warnings": warnings,
        "household": household,
    }


# --- §5.11 The questionnaire ---------------------------------------------------------------


def _shift(value, years: int = 1):
    """A carried value's dates move to the new year (a route's period, a subscription's
    first payment)."""
    if isinstance(value, CommuteFact):
        return value.model_copy(
            update={
                "period_from": value.period_from.replace(year=value.period_from.year + years),
                "period_to": value.period_to.replace(year=value.period_to.year + years),
            }
        )
    if isinstance(value, EquipmentFact):
        return value.model_copy(
            update={"bought_on": value.bought_on.replace(year=value.bought_on.year + years)}
        )
    return value


def _matches(value, match: dict[str, str]) -> bool:
    return all(str(getattr(value, k, None)) == v for k, v in match.items())


def _slots(case: QuestionnaireCase) -> list[tuple]:
    """(question, owner_id, property_id) per answer slot, in file order."""
    q = registry.questionnaire(case.tax_year)
    out = []
    for question in q.questions:
        if question.scope == "owner":
            out += [(question, o, None) for o in case.owners]
        elif question.scope == "property":
            out += [(question, None, p) for p in case.properties]
        else:
            out.append((question, None, None))
    return out


def _prefill(case: QuestionnaireCase) -> list[dict]:
    year, prior_year = case.tax_year, case.tax_year - 1
    q = registry.questionnaire(year)
    rs = anlage_n_rules(year)
    answers = [a for a in current(case.prior_answers) if a.tax_year == prior_year]
    rows = current(case.prior_rows)
    tax_rows = current(case.prior_tax_rows)
    # the filters other questions put on a shared kind: a target without a filter leaves them
    filters: dict[str, list[dict[str, str]]] = defaultdict(list)
    for question in q.questions:
        for t in question.targets:
            if t.input_kind is not None and t.value_match:
                filters[t.input_kind.value].append(t.value_match)
    out = []
    for question, owner, prop in _slots(case):
        prior = next(
            (
                a
                for a in answers
                if a.question_key == question.key and a.owner_id == owner and a.property_id == prop
            ),
            None,
        )
        proposed_answer = None
        if (
            question.carry_forward != "none"
            and prior is not None
            and prior.answer != "not_applicable"
        ):
            proposed_answer = prior.answer
        if prior is None and question.default_answer is not None:
            proposed_answer = question.default_answer
        proposed, reconfirm = [], []
        for t in question.targets:
            if t.kind == "reminder":
                continue
            kind = t.input_kind.value

            def wanted(value, t=t, kind=kind) -> bool:
                if t.value_match:
                    return _matches(value, t.value_match)
                return not any(_matches(value, m) for m in filters.get(kind, []))

            if question.carry_forward == "copy" and t.kind == "return_input":
                for r in rows:
                    if (
                        r.kind.value == kind
                        and r.tax_year == prior_year
                        and r.owner_id == owner
                        and wanted(r.value)
                    ):
                        proposed.append(
                            ReturnInputCreate(
                                owner_id=r.owner_id,
                                kind=r.kind,
                                tax_year=year,
                                value=_shift(r.value),
                                carried_from_id=r.id,
                            )
                        )
                if (
                    not proposed
                    and prior is None
                    and question.default_answer == "yes"
                    and t.value_match == {"expense_class": "account_fee"}
                ):
                    proposed.append(
                        ReturnInputCreate(
                            owner_id=owner,
                            kind=K.OTHER_EXPENSE,
                            tax_year=year,
                            value=OtherExpenseFact(
                                expense_class="account_fee",
                                label="Kontoführung",
                                amount=rs.account_fee_flat,
                            ),
                        )
                    )
            elif question.carry_forward == "copy" and t.kind == "tax_input":
                for r in tax_rows:
                    if (
                        r.kind.value == kind
                        and r.tax_year == prior_year
                        and r.property_id == prop
                        and wanted(r.value)
                    ):
                        proposed.append(
                            TaxInputCreate(
                                property_id=r.property_id,
                                kind=r.kind,
                                tax_year=year,
                                value=r.value,
                                carried_from_id=r.id,
                            )
                        )
            elif question.carry_forward == "reconfirm":
                for r in rows:
                    if r.kind.value != kind or r.owner_id != owner or r.tax_year > prior_year:
                        continue
                    if r.kind is K.CHILD:
                        n, _ = _child_months(r.value, year)
                        if n > 0:
                            reconfirm.append(r.id)
                    elif r.kind is K.WORK_EQUIPMENT:
                        try:
                            if _equipment(r.value, year, rs) > 0:
                                reconfirm.append(r.id)
                        except Refused:
                            pass
        out.append(
            {
                "key": question.key,
                "owner_id": owner,
                "property_id": prop,
                "prior_answer_id": prior.id if prior else None,
                "proposed_answer": proposed_answer,
                "proposed_rows": proposed,
                "reconfirm_ids": reconfirm,
            }
        )
    return out


def _progress(case: QuestionnaireCase) -> QuestionnaireProgress:
    answers = [a for a in current(case.answers) if a.tax_year == case.tax_year]
    answered, open_slots = 0, []
    for question, owner, prop in _slots(case):
        if any(
            a.question_key == question.key and a.owner_id == owner and a.property_id == prop
            for a in answers
        ):
            answered += 1
        else:
            open_slots.append(
                {
                    "key": question.key,
                    "scope": question.scope,
                    "owner_id": owner,
                    "property_id": prop,
                }
            )
    return QuestionnaireProgress(
        answered=answered, total=answered + len(open_slots), open=open_slots
    )


def _reminders(case: QuestionnaireCase) -> list[str]:
    q = registry.questionnaire(case.tax_year)
    yes = {
        a.question_key
        for a in current(case.answers)
        if a.tax_year == case.tax_year and a.answer == "yes"
    }
    return [
        t.reminder_de
        for question in q.questions
        if question.key in yes
        for t in question.targets
        if t.kind == "reminder" and t.reminder_de
    ]


def extraordinary_threshold(gde: Decimal, children: int, year: int) -> Decimal:
    """§33 Abs. 3, joint: the zumutbare Belastung over the bands."""
    bands = registry.tariff(year).extraordinary_burden_bands
    assert bands is not None
    total, lower = ZERO, ZERO
    for band in bands:
        rate = (
            band.rate_joint
            if children == 0
            else band.rate_one_two_children
            if children <= 2
            else band.rate_three_plus
        )
        upper = gde if band.up_to is None else min(gde, band.up_to)
        if upper > lower:
            total += (upper - lower) * rate
        if band.up_to is None or gde <= band.up_to:
            break
        lower = band.up_to
    return cents(total)


# --- payslip.yaml ----------------------------------------------------------------------------


class PayslipExpect(Schema):
    gross_source: Literal["certificate", "payslip"]
    gross_total: NonNegativeMoney
    projected: list[WageCertificate] = Field(default_factory=list)
    warnings: list[WarningExpect] = Field(default_factory=list)


class PayslipCase(Lenient):
    tax_year: TaxYear
    payslips: list[Payslip] = Field(default_factory=list)
    certificates: list[WageCertificate] = Field(default_factory=list)
    expect: PayslipExpect

    @model_validator(mode="after")
    def _arithmetic(self) -> Self:
        real, projected, warnings = _pay(self.certificates, self.payslips, self.tax_year)
        e = self.expect
        _check(f"{self.id}: projected", e.projected, projected)
        _check(
            f"{self.id}: gross_total",
            e.gross_total,
            sum((c.gross for c in real + projected), ZERO),
        )
        _check(
            f"{self.id}: gross_source", e.gross_source, "payslip" if projected else "certificate"
        )
        _check_warnings(e.warnings, warnings, self.id)
        return self


class PayslipFixture(_Fixture):
    cases: list[PayslipCase] = Field(min_length=1)


# --- commute.yaml ----------------------------------------------------------------------------


class CommuteExpect(Schema):
    pauschale_car: NonNegativeMoney
    pauschale_rest: NonNegativeMoney
    total: NonNegativeMoney
    employer_offset: NonNegativeMoney
    deductible: NonNegativeMoney
    notes: list[str] = Field(default_factory=list)
    warnings: list[WarningExpect] = Field(default_factory=list)


class CommuteCase(Lenient):
    tax_year: TaxYear
    routes: list[CommuteFact] = Field(default_factory=list)
    certificates: list[WageCertificate] = Field(default_factory=list)
    expect: CommuteExpect

    @model_validator(mode="after")
    def _arithmetic(self) -> Self:
        got = _commute(self.routes, self.certificates, anlage_n_rules(self.tax_year))
        e = self.expect
        for field in ("pauschale_car", "pauschale_rest", "total", "employer_offset", "deductible"):
            _check(f"{self.id}: {field}", getattr(e, field), got[field])
        _check(f"{self.id}: notes", e.notes, got["notes"])
        _check_warnings(e.warnings, got["warnings"], self.id)
        return self


class CommuteFixture(_Fixture):
    cases: list[CommuteCase] = Field(min_length=1)


# --- home_office.yaml ------------------------------------------------------------------------


class HomeOfficeExpect(Schema):
    amount: NonNegativeMoney | None = None
    warnings: list[WarningExpect] = Field(default_factory=list)
    error: RuleErrorExpect | None = None


class HomeOfficeCase(Lenient):
    tax_year: TaxYear
    fact: HomeOfficeFact
    commute_days: int = Field(default=0, ge=0)
    work_room: WorkRoomFact | None = None
    expect: HomeOfficeExpect

    @model_validator(mode="after")
    def _arithmetic(self) -> Self:
        rs = anlage_n_rules(self.tax_year)
        e = self.expect
        got = _check_error(
            e.error, lambda: _home_office(self.fact, self.commute_days, self.work_room, rs), self.id
        )
        if got is not None:
            amount, warnings = got
            if e.amount is None:
                raise ValueError(f"{self.id}: an amount or an error")
            _check(f"{self.id}: amount", e.amount, amount)
            _check_warnings(e.warnings, warnings, self.id)
        return self


class HomeOfficeFixture(_Fixture):
    cases: list[HomeOfficeCase] = Field(min_length=1)


# --- work_room.yaml --------------------------------------------------------------------------


class WorkRoomExpect(Schema):
    actual: NonNegativeMoney
    flat: NonNegativeMoney
    method: Literal["actual", "flat"]
    amount: NonNegativeMoney
    warnings: list[WarningExpect] = Field(default_factory=list)


class WorkRoomCase(Lenient):
    tax_year: TaxYear
    fact: WorkRoomFact
    expect: WorkRoomExpect

    @model_validator(mode="after")
    def _arithmetic(self) -> Self:
        got = _work_room(self.fact, anlage_n_rules(self.tax_year))
        for field in ("actual", "flat", "method", "amount"):
            _check(f"{self.id}: {field}", getattr(self.expect, field), got[field])
        _check_warnings(self.expect.warnings, got["warnings"], self.id)
        return self


class WorkRoomFixture(_Fixture):
    cases: list[WorkRoomCase] = Field(min_length=1)


# --- equipment.yaml --------------------------------------------------------------------------


class EquipmentYear(Schema):
    tax_year: TaxYear
    amount: NonNegativeMoney | None = None  # None with an error
    error: RuleErrorExpect | None = None


class EquipmentCase(Lenient):
    item: EquipmentFact
    rule_set_year: TaxYear = 2026  # the class lives of this rule set, for every year listed
    years: list[EquipmentYear] = Field(min_length=1)
    full_life_sum: NonNegativeMoney | None = None  # Σ over every year = the base

    @model_validator(mode="after")
    def _arithmetic(self) -> Self:
        rs = anlage_n_rules(self.rule_set_year)
        for y in self.years:
            got = _check_error(y.error, lambda y=y: _equipment(self.item, y.tax_year, rs), self.id)
            if got is not None:
                _check(f"{self.id} {y.tax_year}", y.amount, got)
        if self.full_life_sum is not None:
            total = sum(
                (_equipment(self.item, y, rs) for y in range(self.item.bought_on.year, 2080)), ZERO
            )
            _check(f"{self.id}: Σ of the slices", self.full_life_sum, total)
        return self


class EquipmentFixture(_Fixture):
    cases: list[EquipmentCase] = Field(min_length=1)


# --- telecom.yaml ----------------------------------------------------------------------------


class TelecomCase(Lenient):
    tax_year: TaxYear
    fact: TelecomFact
    expect: NonNegativeMoney

    @model_validator(mode="after")
    def _arithmetic(self) -> Self:
        _check(self.id, self.expect, _telecom(self.fact, anlage_n_rules(self.tax_year)))
        return self


class TelecomFixture(_Fixture):
    cases: list[TelecomCase] = Field(min_length=1)


# --- anlage_n.yaml ---------------------------------------------------------------------------


class LineAmountExpect(Schema):
    key: str
    amount: Money


class Components(Schema):
    commute: NonNegativeMoney = _NOT_ZERO
    home_office: NonNegativeMoney = _NOT_ZERO
    work_room: NonNegativeMoney = _NOT_ZERO
    equipment: NonNegativeMoney = _NOT_ZERO
    telecom: NonNegativeMoney = _NOT_ZERO
    dues: NonNegativeMoney = _NOT_ZERO
    training: NonNegativeMoney = _NOT_ZERO
    other: NonNegativeMoney = _NOT_ZERO


class DraftExpect(Schema):
    status: Literal["certified", "estimate"] | None = None
    gross_source: Literal["certificate", "payslip", "expected"] | None = None
    gross: NonNegativeMoney | None = None
    wk_itemised: NonNegativeMoney | None = None
    wk_union: NonNegativeMoney | None = None
    wk_allowance: NonNegativeMoney | None = None
    wk_applied: NonNegativeMoney | None = None
    result: Money | None = None
    replacement_benefits: NonNegativeMoney | None = None
    components: Components | None = None
    lines: list[LineAmountExpect] | None = None
    warnings: list[WarningExpect] = Field(default_factory=list)
    error: RuleErrorExpect | None = None


class AnlageNCase(Lenient):
    tax_year: TaxYear
    facts: OwnerReturnFacts
    expect: DraftExpect

    @model_validator(mode="after")
    def _arithmetic(self) -> Self:
        e = self.expect
        got = _check_error(e.error, lambda: _draft_n(self.facts, self.tax_year), self.id)
        if got is None:
            return self
        if e.result is None:
            raise ValueError(f"{self.id}: a draft case states its result")
        for field in (
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
            _check(f"{self.id}: {field}", getattr(e, field), got[field])
        if e.components is not None:
            _check(f"{self.id}: components", e.components, Components(**got["components"]))
        if e.lines is not None:
            _check(f"{self.id}: lines", {x.key: x.amount for x in e.lines}, got["lines"])
            keys = anlage_n_rules(self.tax_year).form.lines
            unknown = [x.key for x in e.lines if x.key not in keys]
            if unknown:
                raise ValueError(f"{self.id}: line keys not in the form mapping: {unknown}")
        _check_warnings(e.warnings, got["warnings"], self.id)
        return self


class AnlageNFixture(_Fixture):
    cases: list[AnlageNCase] = Field(min_length=1)


# --- vorsorge.yaml ---------------------------------------------------------------------------


class VorsorgeExpect(Schema):
    pension: NonNegativeMoney
    health_care_basis: NonNegativeMoney
    other: NonNegativeMoney
    deductible: NonNegativeMoney
    source: Literal["certificate", "payslip", "override", "none"]

    @model_validator(mode="after")
    def _sum(self) -> Self:
        if self.deductible != self.pension + self.health_care_basis + self.other:
            raise ValueError("deductible = pension + health_care_basis + other")
        return self


class VorsorgeCase(Lenient):
    tax_year: TaxYear
    facts: OwnerReturnFacts
    expect: VorsorgeExpect

    @model_validator(mode="after")
    def _arithmetic(self) -> Self:
        _check(self.id, self.expect.model_dump(), _vorsorge(self.facts, self.tax_year))
        return self


class VorsorgeFixture(_Fixture):
    cases: list[VorsorgeCase] = Field(min_length=1)


# --- children.yaml ---------------------------------------------------------------------------


class AssessedExpect(Schema):
    assessed: AssessedTax
    warnings: list[WarningExpect] = Field(default_factory=list)


class ChildrenCase(Lenient):
    tax_year: TaxYear
    zve: NonNegativeMoney
    owners: list[OwnerTaxProfile] = Field(min_length=1)
    household: HouseholdTaxContext | None = None
    expect: AssessedExpect

    @model_validator(mode="after")
    def _arithmetic(self) -> Self:
        assessed, warnings = _assessed(self.zve, self.household, self.owners, self.tax_year)
        _check(f"{self.id}: assessed", self.expect.assessed, assessed)
        _check_warnings(self.expect.warnings, warnings, self.id)
        return self


class ChildrenFixture(_Fixture):
    cases: list[ChildrenCase] = Field(min_length=1)


# --- return_estimate.yaml --------------------------------------------------------------------


class OwnerFactsRef(Schema):
    """An anlage_n.yaml case's facts, with rows dropped and added (like phase 4's FactsRef)."""

    case: str
    drop_inputs: list[UUID] = Field(default_factory=list)
    add_inputs: list[ReturnInputFact] = Field(default_factory=list)
    church_tax: bool | None = None

    def resolve(self) -> OwnerReturnFacts:
        cases = {c.id: c for c in load("anlage_n").cases}
        if self.case not in cases:
            raise ValueError(f"anlage_n has no case {self.case}")
        facts = cases[self.case].facts
        known = {r.id for r in facts.inputs}
        if not set(self.drop_inputs) <= known:
            raise ValueError(f"{self.case}: dropping inputs the case does not have")
        inputs = [r for r in facts.inputs if r.id not in set(self.drop_inputs)]
        inputs += self.add_inputs
        church = facts.church_tax if self.church_tax is None else self.church_tax
        return facts.model_copy(update={"inputs": inputs, "church_tax": church})


class PropertyResult(Schema):
    property_id: UUID
    result: Money  # the Anlage V result (phase 3's draft)


class EstimateExpect(Schema):
    status: Literal["estimate", "certified"] | None = None
    total_income: Money | None = None
    vorsorge_total: NonNegativeMoney | None = None
    church_tax_paid: NonNegativeMoney | None = None
    donations: NonNegativeMoney | None = None
    childcare: NonNegativeMoney | None = None
    special_expenses: NonNegativeMoney | None = None
    zve: NonNegativeMoney | None = None
    assessed: AssessedTax | None = None
    withheld: Withheld | None = None
    result: Money | None = None
    filing_required: FilingCheck | None = None
    missing: list[MissingItem] | None = None
    warnings: list[WarningExpect] | None = None
    error: RuleErrorExpect | None = None


class EstimateCase(Lenient):
    tax_year: TaxYear
    owners: list[OwnerTaxProfile] = Field(min_length=1)
    owner_facts: list[OwnerFactsRef | OwnerReturnFacts] = Field(min_length=1)
    household: list[ReturnInputFact] = Field(default_factory=list)
    properties: list[PropertyResult] = Field(default_factory=list)
    questionnaire: QuestionnaireProgress
    answers: list[QuestionnaireAnswer] = Field(default_factory=list)
    expect: EstimateExpect

    def resolved_owners(self) -> list[OwnerReturnFacts]:
        return [f.resolve() if isinstance(f, OwnerFactsRef) else f for f in self.owner_facts]

    @model_validator(mode="after")
    def _arithmetic(self) -> Self:
        e = self.expect
        got = _check_error(e.error, lambda: _estimate(self), self.id)
        if got is None:
            return self
        for field in EstimateExpect.model_fields:
            if field in ("warnings", "error"):
                continue
            _check(f"{self.id}: {field}", getattr(e, field), got[field])
        if e.warnings is not None:
            _check_warnings(e.warnings, got["warnings"], self.id)
        return self


class EstimateFixture(_Fixture):
    cases: list[EstimateCase] = Field(min_length=1)


# --- questionnaire.yaml ----------------------------------------------------------------------


class SlotExpect(Schema):
    key: str
    owner_id: UUID | None = None
    property_id: UUID | None = None
    prior_answer_id: UUID | None = None
    proposed_answer: Literal["yes", "no"] | None = None
    proposed_rows: list[ReturnInputCreate | TaxInputCreate] = Field(default_factory=list)
    reconfirm_ids: list[UUID] = Field(default_factory=list)


class OpenSlotExpect(Schema):
    key: str
    owner_id: UUID | None = None
    property_id: UUID | None = None


class QuestionnaireExpect(Schema):
    slot_count: int | None = None
    slots: list[SlotExpect] | None = None  # the slots named; the others are not asserted
    absent_keys: list[str] | None = None  # keys that get no slot
    answered: int | None = None
    total: int | None = None
    open: list[OpenSlotExpect] | None = None
    reminders: list[str] | None = None
    threshold: NonNegativeMoney | None = None


class QuestionnaireCase(Lenient):
    tax_year: TaxYear
    owners: list[UUID] = Field(default_factory=list)
    properties: list[UUID] = Field(default_factory=list)
    prior_answers: list[QuestionnaireAnswer] = Field(default_factory=list)
    prior_rows: list[ReturnInputFact] = Field(default_factory=list)
    prior_tax_rows: list[TaxInputFact] = Field(default_factory=list)
    answers: list[QuestionnaireAnswer] = Field(default_factory=list)
    gde: Money | None = None  # extraordinary_threshold
    children: int = Field(default=0, ge=0)
    expect: QuestionnaireExpect

    @model_validator(mode="after")
    def _arithmetic(self) -> Self:
        e = self.expect
        slots = _prefill(self)
        _check(f"{self.id}: slot_count", e.slot_count, len(slots))
        for want in e.slots or []:
            got = next(
                (
                    s
                    for s in slots
                    if (s["key"], s["owner_id"], s["property_id"])
                    == (want.key, want.owner_id, want.property_id)
                ),
                None,
            )
            if got is None:
                raise ValueError(f"{self.id}: no slot {want.key}")
            _check(f"{self.id}: slot {want.key}", want.model_dump(), SlotExpect(**got).model_dump())
        for key in e.absent_keys or []:
            if any(s["key"] == key for s in slots):
                raise ValueError(f"{self.id}: {key} has a slot")
        progress = _progress(self)
        _check(f"{self.id}: answered", e.answered, progress.answered)
        _check(f"{self.id}: total", e.total, progress.total)
        if e.open is not None:
            _check(
                f"{self.id}: open",
                [o.model_dump() for o in e.open],
                [
                    OpenSlotExpect(
                        key=o.key, owner_id=o.owner_id, property_id=o.property_id
                    ).model_dump()
                    for o in progress.open
                ],
            )
        _check(f"{self.id}: reminders", e.reminders, _reminders(self))
        if e.threshold is not None:
            if self.gde is None:
                raise ValueError(f"{self.id}: a threshold needs the gde")
            _check(
                f"{self.id}: threshold",
                e.threshold,
                extraordinary_threshold(self.gde, self.children, self.tax_year),
            )
        return self


class QuestionnaireFixture(_Fixture):
    cases: list[QuestionnaireCase] = Field(min_length=1)


MODELS: dict[str, type[Schema]] = {
    "payslip": PayslipFixture,
    "commute": CommuteFixture,
    "home_office": HomeOfficeFixture,
    "work_room": WorkRoomFixture,
    "equipment": EquipmentFixture,
    "telecom": TelecomFixture,
    "anlage_n": AnlageNFixture,
    "vorsorge": VorsorgeFixture,
    "children": ChildrenFixture,
    "return_estimate": EstimateFixture,
    "questionnaire": QuestionnaireFixture,
}


@cache
def load(name: str) -> Schema:
    return MODELS[name].model_validate(raw(name))
