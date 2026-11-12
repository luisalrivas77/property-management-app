"""Shapes of the phase 4 YAML rule fixtures (docs/phases/phase-4.md §5, §10).

Same contract as fixture_models_phase3.py: loading a fixture through these models is itself a
test (tests/test_fixtures_valid.py), so a fixture that does not match the spec schemas fails
before any rule is implemented. Inputs are the §7 schemas the §5 signatures take
(OwnerTaxProfile, IncomeInputFact, Levers, LoanTerms, DraftFacts); expectations are the result
schemas themselves, with these comparison rules for the rule tests (steps 1–2):

- Every field compares exactly, None included, except `sources`: the fixtures leave every
  `sources` list empty and a test compares with them excluded (every figure carries sources
  by construction; step 2's tests check their kinds).
- `flags` compare as sets.
- Projection `years`: exactly the projected years, in order.
- In marginal_rate.yaml an expectation names what the case checks (`zve`, `effect`,
  `tariff`); one it leaves out is not asserted.

The facts of actuals.yaml and projection.yaml are phase 3's `anlage_v_2026.yaml` facts by
reference (`facts: {case: case_a, ...}`), with typed modifications, so the two phases cannot
drift apart. The expectations check their own arithmetic on load (cash out, net cash, equity,
the Pauschale gap, the bridge, the sale's AfA and cash-flow totals), so a hand-calculation
slip fails here.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from pathlib import Path
from typing import Literal, Self
from uuid import UUID

import fixture_models_phase3 as phase3
import yaml
from fixture_models import Lenient
from pydantic import Field, model_validator

from hausbuch.rules.tax import registry
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.common import (
    Fraction,
    Money,
    NonNegativeMoney,
    PositiveMoney,
    Rate,
    ReturnRate,
    Schema,
    TaxYear,
)
from hausbuch.schemas.employment import OwnerEmployment
from hausbuch.schemas.enums import OperatingCostsTerms
from hausbuch.schemas.loan import LoanTerms
from hausbuch.schemas.performance import (
    FLAT_RATE_BETRKV,
    CostLevel,
    DatedFlow,
    FlatRateCheck,
    FlatRateLetterFigures,
    FlatRateVerdict,
    HouseholdTaxContext,
    IncomeInputFact,
    Levers,
    OwnerTaxProfile,
    RateBasis,
    SaleResult,
    TariffStamp,
    TaxBreakdown,
    TaxEffect,
    YearFigures,
    YearFlag,
    YearKind,
    ZveEstimate,
)
from hausbuch.schemas.tax import AllocationFact, DraftFacts, TaxInputFact

FIXTURES = Path(__file__).parent / "fixtures" / "phase4"
CENT = Decimal("0.01")


@cache
def raw(name: str) -> dict:
    with (FIXTURES / f"{name}.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


class _Fixture(Schema):
    ids: dict[str, UUID] = Field(default_factory=dict)
    bases: dict[str, object] = Field(default_factory=dict)  # YAML anchors merged into cases


# --- Shared input pieces ---------------------------------------------------------------


class FactsRef(Schema):
    """A phase 3 anlage_v_2026.yaml case's DraftFacts, with typed modifications applied in
    this order: drop, recategorize, add, then the tenancy terms."""

    fixture: Literal["anlage_v_2026"] = "anlage_v_2026"
    case: str
    drop_allocations: list[UUID] = Field(default_factory=list)
    recategorize: dict[AllocationCategory, AllocationCategory] = Field(default_factory=dict)
    add_allocations: list[AllocationFact] = Field(default_factory=list)
    add_tax_inputs: list[TaxInputFact] = Field(default_factory=list)
    operating_costs_terms: OperatingCostsTerms | None = None  # every tenancy's

    def resolve(self) -> DraftFacts:
        cases = {c.id: c for c in phase3.load(self.fixture).cases}
        facts = cases[self.case].facts
        known = {a.allocation_id for a in facts.allocations}
        dropped = set(self.drop_allocations)
        allocations = [
            a.model_copy(update={"category": self.recategorize.get(a.category, a.category)})
            for a in facts.allocations
            if a.allocation_id not in dropped
        ] + list(self.add_allocations)
        tenancies = facts.tenancies
        if self.operating_costs_terms is not None:
            tenancies = [
                t.model_copy(update={"operating_costs_terms": self.operating_costs_terms})
                for t in tenancies
            ]
        if not dropped <= known:
            raise ValueError(f"{self.case}: dropping allocations the case does not have")
        return DraftFacts.model_validate(
            facts.model_dump()
            | {
                "allocations": [a.model_dump() for a in allocations],
                "tax_inputs": [t.model_dump() for t in [*facts.tax_inputs, *self.add_tax_inputs]],
                "tenancies": [t.model_dump() for t in tenancies],
            }
        )

    @model_validator(mode="after")
    def _resolves(self) -> Self:
        if self.case not in {c.id for c in phase3.load(self.fixture).cases}:
            raise ValueError(f"{self.fixture} has no case {self.case}")
        self.resolve()
        return self


class LoanInput(Schema):
    """A loan's terms for build_schedule (DraftFacts carries only id and lender)."""

    loan_id: UUID
    terms: LoanTerms


def _check_effect(e: TaxEffect, what: str) -> None:
    w, wo = e.breakdown_with, e.breakdown_without
    if (w is None) != (wo is None):
        raise ValueError(f"{what}: both breakdowns or neither")
    if w is not None and wo is not None and w.total - wo.total != e.effect:
        raise ValueError(f"{what}: effect = T(base + R) − T(base)")
    if e.source == "override" and w is not None:
        raise ValueError(f"{what}: an override effect is R × rate, without breakdowns")
    if (e.effective_rate is None) == (e.slope is None):
        raise ValueError(f"{what}: the effective rate, or the slope when R = 0")


def _monthly(amount: Decimal, months: int) -> Decimal:
    return (amount / months).quantize(CENT, ROUND_HALF_UP)


class YearExpect(YearFigures):
    """YearFigures that checks its own arithmetic."""

    @model_validator(mode="after")
    def _arithmetic(self) -> Self:
        y = self.year
        if self.umlagen_net != self.umlagen_in - self.umlagen_out:
            raise ValueError(f"{y}: umlagen_net = in − out")
        cash_out = self.interest + self.principal_repaid + self.running_costs + self.umlagen_out
        if self.cash_out != cash_out:
            raise ValueError(f"{y}: cash_out = interest + principal + running + umlagen_out")
        effect = self.tax_effect.effect if self.tax_effect is not None else Decimal("0")
        net = self.rent_in + self.other_income + self.umlagen_in - self.cash_out - effect
        if self.net_cash != net:
            raise ValueError(f"{y}: net_cash {self.net_cash} != {net}")
        if self.value is not None and self.equity != self.value - self.loan_balance:
            raise ValueError(f"{y}: equity = value − loan balance")
        if (self.flat_rate_gap_monthly is None) != (self.flat_rate_months is None):
            raise ValueError(f"{y}: the Pauschale gap exactly with its months (D92)")
        if self.flat_rate_gap_monthly is not None and self.flat_rate_months is not None:
            if not 1 <= self.flat_rate_months <= 12:
                raise ValueError(f"{y}: flat_rate_months is 1 to 12")
            gap = _monthly(self.flat_rate_in - self.umlagen_out, self.flat_rate_months)
            if self.flat_rate_gap_monthly != gap:
                raise ValueError(f"{y}: the Pauschale gap is (flat_rate_in − umlagen_out) ÷ months")
        elif self.flat_rate_in != 0:
            raise ValueError(f"{y}: flat-rate charges without a gap")
        if (self.tax_effect is None) != (YearFlag.INCOME_MISSING in self.flags):
            raise ValueError(f"{y}: no tax effect exactly when income.missing is flagged")
        if self.tax_effect is not None:
            _check_effect(self.tax_effect, str(y))
            assessed = self.tax_effect.source == "bescheid"
            if assessed == (YearFlag.TAX_ESTIMATED in self.flags):
                raise ValueError(f"{y}: tax_estimated exactly when the effect is not a Bescheid's")
        if (self.kind is YearKind.ACTUAL_PARTIAL) != (YearFlag.SO_FAR in self.flags):
            raise ValueError(f"{y}: so_far exactly for the current, partial year")
        return self


# --- tariff.yaml (§5.1) ----------------------------------------------------------------


class TariffCheck(Schema):
    """One call: income_tax(zve, joint), soli(est, joint), church_tax(est, owners) or
    total_tax(zve, owners) under the case's tariff year."""

    op: Literal["income_tax", "soli", "church_tax", "total_tax"]
    zve: Money | None = None
    est: NonNegativeMoney | None = None
    expect: NonNegativeMoney | None = None
    breakdown: TaxBreakdown | None = None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        by_zve = self.op in ("income_tax", "total_tax")
        if (self.zve is not None) != by_zve or (self.est is not None) == by_zve:
            raise ValueError(f"{self.op}: income_tax and total_tax take zve; soli, church est")
        if (self.breakdown is not None) != (self.op == "total_tax"):
            raise ValueError("total_tax expects a breakdown, the others an amount")
        if (self.expect is None) == (self.breakdown is None):
            raise ValueError("an amount or a breakdown")
        if self.op == "income_tax" and self.expect != self.expect.to_integral_value():
            raise ValueError("the Einkommensteuer is whole euros")
        return self


class TariffCase(Lenient):
    tariff_year: TaxYear = 2026
    joint: bool = True
    owners: list[OwnerTaxProfile] = Field(default_factory=list)  # church_tax, total_tax
    checks: list[TariffCheck] = Field(min_length=1)

    @model_validator(mode="after")
    def _owners(self) -> Self:
        if any(c.op in ("church_tax", "total_tax") for c in self.checks) and not self.owners:
            raise ValueError(f"{self.id}: church tax needs the owners")
        return self


class TariffFixture(_Fixture):
    cases: list[TariffCase]


# --- marginal_rate.yaml (§5.1 tax_effect, §5.2 zve_base) ---------------------------------


class MarginalExpect(Schema):
    zve: ZveEstimate | None = None
    effect: TaxEffect | None = None
    tariff: TariffStamp | None = None
    missing: bool = False  # no input for the year or before: zve_base and the effect are None

    @model_validator(mode="after")
    def _something(self) -> Self:
        if self.missing and (self.zve or self.effect):
            raise ValueError("missing means no estimate and no effect")
        if not (self.missing or self.zve or self.effect or self.tariff):
            raise ValueError("a case expects something")
        if self.effect is not None:
            _check_effect(self.effect, "effect")
        return self


class MarginalCase(Lenient):
    # A case written ahead of its rule (phase 4b step 0 wrote the D98 context cases for step
    # 1): the step that builds it. test_income.py skips the case until it is removed.
    pending: str | None = None
    tax_year: TaxYear
    owners: list[OwnerTaxProfile] = Field(default_factory=list)
    inputs: list[IncomeInputFact] = Field(default_factory=list)
    # bescheid_zve: the property's assessed result of the year, which the Bescheid's zvE
    # contains; zvE_base = the Bescheid's zvE − this result (§5.2).
    assessed_result: Money | None = None
    # For tax_effect: a stated zvE_base (None: zve_base's amount from the inputs) and R.
    zve_base: NonNegativeMoney | None = None
    result: Money | None = None
    # Phase 4b D98: the owners' Anlage N summaries and the household context. Given, they
    # replace D84's default deductions per owner (a deductions_override row still wins) and
    # tax_effect evaluates return_estimate.assessed_tax at both points instead of total_tax.
    employment: list[OwnerEmployment] = Field(default_factory=list)
    household: HouseholdTaxContext | None = None
    expect: MarginalExpect

    @model_validator(mode="after")
    def _employment(self) -> Self:
        """D98: an owner's part from a summary takes its gross and source, and deducts
        wk_applied + vorsorge + the Sonderausgaben-Pauschbetrag (36)."""
        zve = self.expect.zve
        if zve is None or not self.employment:
            return self
        allowance = registry.tariff(self.tax_year).special_expenses_allowance
        by_owner = {e.owner_id: e for e in self.employment}
        for part in zve.per_owner:
            e = by_owner.get(part.owner_id)
            if e is None:
                continue
            if (part.gross, part.gross_source) != (e.gross, e.gross_source):
                raise ValueError(f"{self.id}: the gross and its source are the summary's")
            if part.deductions_source == "employment" and part.deductions != (
                e.wk_applied + e.vorsorge + allowance
            ):
                raise ValueError(f"{self.id}: deductions = wk_applied + vorsorge + 36")
            if part.amount != max(Decimal("0"), part.gross - part.deductions):
                raise ValueError(f"{self.id}: amount = gross − deductions")
        return self


class MarginalFixture(_Fixture):
    cases: list[MarginalCase]


# --- actuals.yaml (§5.3) ---------------------------------------------------------------


class ActualsCase(Lenient):
    facts: FactsRef
    year: TaxYear
    today: date
    owners: list[OwnerTaxProfile]
    income_inputs: list[IncomeInputFact] = Field(default_factory=list)
    loans: list[LoanInput] = Field(default_factory=list)
    expect: YearExpect

    @model_validator(mode="after")
    def _year(self) -> Self:
        if self.expect.year != self.year:
            raise ValueError(f"{self.id}: the expectation is for the case's year")
        partial = self.year == self.today.year and self.today < date(self.year, 12, 31)
        if partial != (self.expect.kind is YearKind.ACTUAL_PARTIAL):
            raise ValueError(f"{self.id}: today's year, before 31 Dec, is actual_partial")
        return self


class ActualsFixture(_Fixture):
    cases: list[ActualsCase]


# --- sale.yaml, irr.yaml (§5.5) --------------------------------------------------------


class SaleOwner(OwnerTaxProfile):
    share: Fraction


class SaleFacts(Schema):
    """What sale() reads, flattened: the property record, the sale year's projected figures
    and the years' net cash (actual and projected)."""

    sale_year: TaxYear
    purchase_date: date
    tax_free_from: date | None  # spekulation.py's date (notarized + 10 years + 1 day)
    purchase_price: PositiveMoney
    cost_rows: NonNegativeMoney  # Σ the property's acquisition cost rows
    loan_principal: NonNegativeMoney  # Σ the loans' principal (E₀)
    value: PositiveMoney  # V at the sale (the projection's value of the sale year)
    selling_costs_rate: Rate
    afa_claimed: NonNegativeMoney  # Σ building AfA of every year owned through the sale year
    afa_claimed_movable: NonNegativeMoney = Decimal("0.00")  # Σ the D80 kitchen's AfA (D94)
    loan_balance: NonNegativeMoney  # L at the sale
    zve_base: NonNegativeMoney  # the sale year's household zvE without the property
    result_sale_year: Money  # the property's taxable result of the sale year
    owners: list[SaleOwner] = Field(min_length=1)
    cash_flows: list[DatedFlow]  # CF_y on 31 Dec, kind cash_flow


def _check_sale(sale: SaleResult, cash_flows: list[DatedFlow], what: str, sale_year: int) -> None:
    cf = sum((f.amount for f in cash_flows), Decimal("0"))
    if sale.profit != sale.net_proceeds + cf - sale.initial_equity:
        raise ValueError(f"{what}: profit = net proceeds + Σ CF − E₀")
    rows = {r.key: r.amount for r in sale.bridge}
    if rows.get("cash_flow") != cf or rows.get("sale_tax") != -sale.sale_tax:
        raise ValueError(f"{what}: the bridge's cash_flow row is Σ CF, its sale_tax row −T")
    expected = [
        (sale.flows[0].on if sale.flows else None, -sale.initial_equity, "initial_equity"),
        *((f.on, f.amount, "cash_flow") for f in cash_flows),
        (sale.sale_date, sale.net_proceeds, "net_proceeds"),
    ]
    if [(f.on, f.amount, f.kind) for f in sale.flows] != expected:
        raise ValueError(f"{what}: flows = −E₀, each CF_y on 31 Dec, the net proceeds")
    owners = sale.gain_per_owner
    if sale.gain is None:
        if owners or sale.freigrenze_applied:
            raise ValueError(f"{what}: no gain, no owners' gains")
        return
    if sum((o.gain for o in owners), Decimal("0")) != sale.gain:
        raise ValueError(f"{what}: the owners' gains add up to the gain")
    limit = registry.rule_set_or_latest(sale_year).speculation_freigrenze  # D93
    if any(o.below_freigrenze != (0 < o.gain < limit) for o in owners):
        raise ValueError(f"{what}: below the Freigrenze means 0 < the owner's gain < {limit}")
    if sale.freigrenze_applied != any(o.below_freigrenze for o in owners):
        raise ValueError(f"{what}: freigrenze_applied when an owner's gain is below it")
    if all(o.gain <= 0 or o.below_freigrenze for o in owners) and sale.sale_tax != 0:
        raise ValueError(f"{what}: no taxable gain, no sale tax")


class SaleCase(Lenient):
    facts: SaleFacts
    expect: SaleResult

    @model_validator(mode="after")
    def _arithmetic(self) -> Self:
        f, s = self.facts, self.expect
        if (s.value, s.afa_claimed_building, s.afa_claimed_movable, s.loan_balance) != (
            f.value,
            f.afa_claimed,
            f.afa_claimed_movable,
            f.loan_balance,
        ):
            raise ValueError(f"{self.id}: the sale takes the facts' value, AfA and balance")
        if s.sale_date != date(f.sale_year, 12, 31) or s.tax_free_from != f.tax_free_from:
            raise ValueError(f"{self.id}: the sale is on 31 Dec of the sale year (D88)")
        if s.taxable != (f.tax_free_from is not None and s.sale_date < f.tax_free_from):
            raise ValueError(f"{self.id}: §23 applies before tax_free_from")
        if not s.flows or s.flows[0].on != f.purchase_date:
            raise ValueError(f"{self.id}: −E₀ is dated the purchase date")
        if s.initial_equity != f.purchase_price + f.cost_rows - f.loan_principal:
            raise ValueError(f"{self.id}: E₀ = price + cost rows − loan principal")
        if s.selling_costs != (s.value * f.selling_costs_rate).quantize(CENT, ROUND_HALF_UP):
            raise ValueError(f"{self.id}: selling costs = value × rate, half-up")
        if s.taxable and s.gain != (
            f.value - s.selling_costs - (f.purchase_price + f.cost_rows - s.afa_claimed)
        ):
            raise ValueError(f"{self.id}: gain = V − C − (price + cost rows − AfA claimed)")
        _check_sale(s, f.cash_flows, self.id, f.sale_year)
        return self


class SaleFixture(_Fixture):
    cases: list[SaleCase]


class CagrInput(Schema):
    initial_equity: PositiveMoney
    profit: Money
    years: Decimal = Field(gt=0)  # D89: days from purchase_date to the sale ÷ 365.25


class IrrCase(Lenient):
    flows: list[DatedFlow] | None = None  # xirr
    cagr: CagrInput | None = None
    expect: ReturnRate | None  # None: no sign change (xirr) / no positive end value (cagr)

    @model_validator(mode="after")
    def _one(self) -> Self:
        if (self.flows is None) == (self.cagr is None):
            raise ValueError(f"{self.id}: flows for xirr or the inputs of cagr")
        return self


class IrrFixture(_Fixture):
    cases: list[IrrCase]


# --- projection.yaml (§5.4, §10.3) -----------------------------------------------------


class ProjectionExpect(Schema):
    """`years` always; the sale block (with the actual years it adds) only for the three
    scenarios of §10.3 and C's control, the other cases being about a year's figures."""

    years: list[YearExpect]
    sale: SaleResult | None = None
    afa_actual: NonNegativeMoney | None = None  # Σ building AfA of the actual years
    afa_movable_actual: NonNegativeMoney | None = None  # Σ kitchen AfA of them (D94); None: 0
    cash_flow_actual: list[DatedFlow] | None = None  # CF of the actual years, for the sale

    @model_validator(mode="after")
    def _together(self) -> Self:
        if {self.sale is None, self.afa_actual is None, self.cash_flow_actual is None} != {
            self.sale is None
        }:
            raise ValueError("the sale comes with afa_actual and cash_flow_actual")
        return self


class ProjectionCase(Lenient):
    facts: FactsRef
    today: date
    owners: list[OwnerTaxProfile]
    income_inputs: list[IncomeInputFact]
    loans: list[LoanInput]
    levers: Levers
    expect: ProjectionExpect

    @model_validator(mode="after")
    def _arithmetic(self) -> Self:
        years = [y.year for y in self.expect.years]
        if years != list(range(self.today.year + 1, self.levers.sale_year + 1)):
            raise ValueError(f"{self.id}: one row per year from today's year + 1 to the sale")
        if any(y.kind is not YearKind.PROJECTED for y in self.expect.years):
            raise ValueError(f"{self.id}: projected years only")
        sale = self.expect.sale
        if sale is None:
            return self
        last = self.expect.years[-1]
        if (sale.value, sale.loan_balance) != (last.value, last.loan_balance):
            raise ValueError(f"{self.id}: the sale takes the sale year's value and balance")
        afa = (self.expect.afa_actual or Decimal("0")) + sum(
            (y.afa or Decimal("0") for y in self.expect.years), Decimal("0")
        )
        movable = (self.expect.afa_movable_actual or Decimal("0")) + sum(
            (y.afa_movable or Decimal("0") for y in self.expect.years), Decimal("0")
        )
        if (sale.afa_claimed_building, sale.afa_claimed_movable) != (afa, movable):
            raise ValueError(f"{self.id}: AfA claimed = actual years + projected years")
        flows = list(self.expect.cash_flow_actual or []) + [
            DatedFlow(on=date(y.year, 12, 31), amount=y.net_cash, kind="cash_flow")
            for y in self.expect.years
        ]
        _check_sale(sale, flows, self.id, last.year)
        return self


class ProjectionFixture(_Fixture):
    cases: list[ProjectionCase]


# --- flat_rate_rise.yaml (D155) --------------------------------------------------------


def _rate_arithmetic(level: CostLevel, what: str) -> None:
    """Each rate from its own sources: a payment is −Σ its lines, a yearly premium and twelve
    months of lines −Σ ÷ 12, a schedule its yearly total ÷ 12 (half-up to the cent)."""
    for r in level.rates:
        paid = -sum((s.amount or Decimal("0") for s in r.sources), Decimal("0"))
        kinds = {s.kind for s in r.sources}
        if r.basis is RateBasis.MONTHLY_PAYMENT:
            want = paid
        elif r.basis in (RateBasis.YEARLY_PREMIUM, RateBasis.TWELVE_MONTHS):
            want = _monthly(paid, 12)
        elif r.basis is RateBasis.SCHEDULE:
            if kinds != {"tax_input"} or len(r.sources) != 1:
                raise ValueError(f"{what}: a schedule rate names its one tax input")
            want = _monthly(r.sources[0].amount or Decimal("0"), 12)
        else:
            want = Decimal("0.00")
        if r.basis not in (RateBasis.SCHEDULE, RateBasis.NONE) and kinds != {"allocation"}:
            raise ValueError(f"{what}: a {r.basis} rate names its allocations")
        if r.monthly != want:
            raise ValueError(f"{what} {level.as_of} {r.category}: {r.monthly} != {want}")


class FlatRateExpect(FlatRateCheck):
    """FlatRateCheck that checks its own arithmetic: every rate from its sources, every
    tenancy's changes from the levels on its set_on and today, its share of the rise half-up
    to the cent, its share in percent, and the earliest start from today."""

    @model_validator(mode="after")
    def _arithmetic(self) -> Self:
        levels = {lv.as_of: lv for lv in self.levels}
        for lv in self.levels:
            _rate_arithmetic(lv, "flat_rate_rise")
        start = date(self.as_of.year, self.as_of.month, 1)
        index = start.year * 12 + start.month - 1 + 2
        after_next = date(index // 12, index % 12 + 1, 1)
        for t in self.tenancies:
            then = {r.category: r.monthly for r in levels[t.set_on].rates}
            now = {r.category: r.monthly for r in levels[self.as_of].rates}
            for c in t.changes:
                if (c.then, c.now) != (then[c.category], now[c.category]):
                    raise ValueError(f"{t.unit_label} {c.category}: then/now are the levels'")
            betrkv = sum((now[c] - then[c] for c in FLAT_RATE_BETRKV), Decimal("0"))
            if t.flat_rise != betrkv:
                raise ValueError(f"{t.unit_label}: flat_rise = Σ BetrKV now − then")
            total = self.pauschalen_total
            rise = (
                (t.flat_rise * t.pauschale / total).quantize(CENT, ROUND_HALF_UP) + 0
                if total
                else Decimal("0.00")
            )
            if t.rise != rise:
                raise ValueError(f"{t.unit_label}: rise {t.rise} != {rise}")
            share = (
                (t.pauschale * 100 / total).quantize(Decimal("0.1"), ROUND_HALF_UP)
                if total
                else None
            )
            if t.share_percent != share:
                raise ValueError(f"{t.unit_label}: share_percent {t.share_percent} != {share}")
            if t.verdict is FlatRateVerdict.RAISE and t.earliest_start != after_next:
                raise ValueError(f"{t.unit_label}: the earliest start is {after_next}")
        return self


class FlatRateCase(Lenient):
    today: date
    facts: DraftFacts
    expect: FlatRateExpect

    @model_validator(mode="after")
    def _scope(self) -> Self:
        if self.expect.as_of != self.today or self.expect.property_id != self.facts.property.id:
            raise ValueError(f"{self.id}: the expectation is for the case's property and day")
        return self


class FlatRateFixture(_Fixture):
    cases: list[FlatRateCase]


def _month_after_next(day: date) -> date:
    index = day.year * 12 + day.month - 1 + 2
    return date(index // 12, index % 12 + 1, 1)


class LetterExpect(FlatRateLetterFigures):
    """FlatRateLetterFigures that checks its own arithmetic: the share of the rise half-up to
    the cent and in percent, and a raise's start from the sending date."""

    @model_validator(mode="after")
    def _arithmetic(self) -> Self:
        rise = (self.flat_rise * self.pauschale / self.pauschalen_total).quantize(
            CENT, ROUND_HALF_UP
        )
        if self.rise != rise:
            raise ValueError(f"{self.unit_label}: rise {self.rise} != {rise}")
        share = (self.pauschale * 100 / self.pauschalen_total).quantize(
            Decimal("0.1"), ROUND_HALF_UP
        )
        if self.share_percent != share:
            raise ValueError(f"{self.unit_label}: share_percent {self.share_percent} != {share}")
        if self.kind is FlatRateVerdict.RAISE and self.starts_on != _month_after_next(self.sent_on):
            raise ValueError(f"{self.unit_label}: a raise starts {_month_after_next(self.sent_on)}")
        return self


class FlatRateLetterCase(Lenient):
    """A letter for one tenancy of a flat_rate_rise.yaml case (its facts and today); `expect`
    None: no letter (no rise above 10 € and no fall). The figures are checked against that
    case's expected check: the tenancy's row, and each reason's then/now and sources from the
    levels on its set_on and today."""

    check_case: str
    tenancy_id: UUID
    sent_on: date
    expect: LetterExpect | None

    @model_validator(mode="after")
    def _against_the_check(self) -> Self:
        rise = {c.id: c for c in load("flat_rate_rise").cases}  # type: ignore[attr-defined]
        if self.check_case not in rise:
            raise ValueError(f"{self.id}: no flat_rate_rise case {self.check_case}")
        check = rise[self.check_case].expect
        [row] = [t for t in check.tenancies if t.tenancy_id == self.tenancy_id]
        if self.expect is None:
            if row.verdict is not FlatRateVerdict.NONE:
                raise ValueError(f"{self.id}: a {row.verdict} gets a letter")
            return self
        e = self.expect
        mine = (e.tenancy_id, e.unit_label, e.as_of, e.sent_on, e.kind, e.set_on)
        theirs = (row.tenancy_id, row.unit_label, check.as_of, self.sent_on, row.verdict)
        if mine != (*theirs, row.set_on):
            raise ValueError(f"{self.id}: the letter is for the case's tenancy, day and verdict")
        if (e.pauschale, e.pauschalen_total, e.flat_rise, e.rise) != (
            row.pauschale,
            check.pauschalen_total,
            row.flat_rise,
            row.rise,
        ):
            raise ValueError(f"{self.id}: the figures are the check's")
        if e.kind is FlatRateVerdict.LOWER and e.starts_on != row.fell_on:
            raise ValueError(f"{self.id}: a lower starts on the date the cost fell")
        levels = {lv.as_of: lv for lv in check.levels}
        then = {r.category: r for r in levels[row.set_on].rates}
        now = {r.category: r for r in levels[check.as_of].rates}
        want = [c for c in FLAT_RATE_BETRKV if then[c].monthly or now[c].monthly]
        if [r.category for r in e.reasons] != want:
            raise ValueError(f"{self.id}: a reason per BetrKV category with a cost, {want}")
        for r in e.reasons:
            c = r.category
            got = (r.then, r.now, r.then_sources, r.now_sources)
            if got != (then[c].monthly, now[c].monthly, then[c].sources, now[c].sources):
                raise ValueError(f"{self.id} {c}: then/now and sources are the levels'")
        return self


class FlatRateLetterFixture(_Fixture):
    cases: list[FlatRateLetterCase]


MODELS: dict[str, type[Schema]] = {
    "tariff": TariffFixture,
    "marginal_rate": MarginalFixture,
    "actuals": ActualsFixture,
    "projection": ProjectionFixture,
    "sale": SaleFixture,
    "irr": IrrFixture,
    "flat_rate_rise": FlatRateFixture,
    "flat_rate_letter": FlatRateLetterFixture,
}


@cache
def load(name: str) -> Schema:
    return MODELS[name].model_validate(raw(name))
