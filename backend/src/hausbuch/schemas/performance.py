"""Phase 4: performance (docs/phases/phase-4.md §4, §5, §7).

Four groups:
- The income tax tariff as data (Tariff, TariffZone, SocialInsurance): loaded from
  rules/tax/tariffs/<year>.yaml by registry.tariff (D81). A year without a file gets the latest
  earlier file, marked `reused_from`.
- Household income inputs (D83): one append-only table `income_input` (migration 0016) with a
  typed `value` per kind, like tax_input (D58).
- Scenarios (D87): the levers of a projection, one append-only table `projection_scenario`.
- The results of the pure rules in hausbuch.rules.performance and rules.tax.tariff (§5): the
  tax breakdown and effect (D82), the household zvE estimate (D84), the year figures of actual
  and projected years alike (D85, D86, D91), the sale (D88, D89) and the projection.

Sign conventions: year figures are magnitudes as the screen shows them (rent in, costs out);
`taxable_result`, `tax_effect.effect`, `umlagen_net`, `net_cash` and `flat_rate_gap_monthly`
are signed (a saving is a negative tax effect). Money is Decimal; nothing here is a float.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from .common import (
    Fraction,
    Money,
    NonNegativeMoney,
    Percent,
    PositiveMoney,
    Rate,
    ReturnRate,
    Schema,
    TariffCoefficient,
    TaxRate,
    TaxYear,
    VacancyMonths,
)
from .enums import IncomeInputKind, StateCode
from .tax import RuleSetStamp, SourceRef

# Error codes the schemas raise inside a validation message; rules/errors.py holds the same
# strings with the other phase 4 codes (schemas do not import rules).
INCOME_INPUT_SCOPE_INVALID = "income_input.scope_invalid"
TARIFF_INVALID = "tariff.invalid"

# --- The tariff as data (§4, D81) ------------------------------------------------------


class TariffZone(Schema):
    """One zone of §32a Abs. 1 EStG, for the whole-euro zvE x of one person.

    zero: 0. progressive: (a·v + b)·v + c with v = (x − offset) / 10,000. linear: rate·x − c.
    The tax is floored to whole euros by the rule (§32a Abs. 1 S. 6), not here."""

    up_to: int | None = Field(ge=0)  # last whole euro of the zone; None = open
    kind: Literal["zero", "progressive", "linear"]
    a: TariffCoefficient | None = None
    b: TariffCoefficient | None = None
    c: TariffCoefficient = Decimal("0")
    offset: int | None = Field(default=None, ge=0)
    rate: Rate | None = None

    @model_validator(mode="after")
    def _parameters(self) -> Self:
        given = {k for k in ("a", "b", "offset", "rate") if getattr(self, k) is not None}
        needed = {
            "zero": set(),
            "progressive": {"a", "b", "offset"},
            "linear": {"rate"},
        }[self.kind]
        if given != needed:
            raise ValueError(f"{TARIFF_INVALID}: a {self.kind} zone takes {sorted(needed)}")
        if self.kind == "zero" and self.c != 0:
            raise ValueError(f"{TARIFF_INVALID}: a zero zone has no c")
        return self


class SocialInsurance(Schema):
    """The employee's shares and the caps of one year (D84). health_rate is the employee half
    of the general rate (§241 SGB V), health_extra_rate half the average Zusatzbeitrag,
    care_rate half the Pflegeversicherung rate (no childless surcharge, no Saxony split)."""

    pension_rate: Rate
    unemployment_rate: Rate  # listed for completeness; never deducted (D84)
    health_rate: Rate
    health_extra_rate: Rate
    care_rate: Rate
    bbg_pension: PositiveMoney  # Beitragsbemessungsgrenze RV, per year
    bbg_health: PositiveMoney  # Beitragsbemessungsgrenze KV/PV, per year
    health_deductible_share: Rate = Decimal("0.96")  # §10 Abs. 1 Nr. 3 S. 4 EStG


class Tariff(Schema):
    """The income tax tariff of one tax year and the defaults the zvE estimate needs (§4).

    reused_from: set by registry.tariff when the year has no file of its own and the latest
    earlier file stands in (D81); `tax_year` is then the year asked for."""

    tax_year: TaxYear
    version: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    reused_from: TaxYear | None = None
    zones: list[TariffZone] = Field(min_length=2)  # §32a Abs. 1, contiguous
    soli_rate: Rate = Decimal("0.055")  # §4 S. 1 SolZG
    soli_mitigation_rate: Rate = Decimal("0.119")  # §4 S. 2 SolZG
    soli_free_limit_joint: PositiveMoney  # §3 Abs. 3 SolZG
    soli_free_limit_single: PositiveMoney
    church_tax_rates: dict[StateCode, Rate]  # every Bundesland
    employee_allowance: NonNegativeMoney  # §9a S. 1 Nr. 1a EStG
    special_expenses_allowance: NonNegativeMoney  # §10c EStG, per owner
    social_insurance: SocialInsurance

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        problems = []
        zones = self.zones
        if zones[0].kind != "zero":
            problems.append("the first zone is the Grundfreibetrag (kind zero)")
        if zones[-1].up_to is not None:
            problems.append("the last zone is open (up_to null)")
        bounds = [z.up_to for z in zones[:-1]]
        if any(b is None for b in bounds) or bounds != sorted(set(bounds)):  # type: ignore[type-var]
            problems.append("zones must be contiguous: up_to strictly increasing, one open end")
        for before, zone in zip(zones, zones[1:], strict=False):
            if zone.kind == "progressive" and zone.offset != before.up_to:
                problems.append("a progressive zone counts from the previous zone's up_to")
        if set(self.church_tax_rates) != set(StateCode):
            problems.append("church_tax_rates names every Bundesland exactly once")
        if self.reused_from is not None and self.reused_from >= self.tax_year:
            problems.append("reused_from is an earlier year")
        if self.soli_free_limit_single >= self.soli_free_limit_joint:
            problems.append("the joint Freigrenze is the larger one")
        if problems:
            raise ValueError(f"{TARIFF_INVALID}: {'; '.join(problems)}")
        return self


class TariffStamp(Schema):
    """Which tariff a figure was computed with (shown with every projection)."""

    tax_year: TaxYear
    version: str
    reused_from: TaxYear | None = None


# --- Owners as the tariff sees them ----------------------------------------------------


class OwnerTaxProfile(Schema):
    """An owner's church tax status (§5.1 church_tax). No name, no Steuer-ID."""

    owner_id: UUID
    church_tax: bool = False
    tax_state: StateCode | None = None

    @model_validator(mode="after")
    def _state_with_church(self) -> Self:
        if self.church_tax and self.tax_state is None:
            raise ValueError("church tax needs the Bundesland (tax_state)")
        return self


# --- Income inputs (D83, §6, §7) -------------------------------------------------------


class ExpectedGrossPay(Schema):
    amount: NonNegativeMoney  # the owner's expected Bruttoarbeitslohn of the year


class DeductionsOverride(Schema):
    amount: NonNegativeMoney  # replaces the whole default deduction (D84)


class BescheidZve(Schema):
    amount: Money  # the joint zvE as assessed; document_id on the row


class MarginalRateOverride(Schema):
    rate: TaxRate  # incl. Soli and church tax


IncomeInputValue = ExpectedGrossPay | DeductionsOverride | BescheidZve | MarginalRateOverride

K = IncomeInputKind
INCOME_INPUT_VALUES: dict[IncomeInputKind, type[Schema]] = {
    K.EXPECTED_GROSS_PAY: ExpectedGrossPay,
    K.DEDUCTIONS_OVERRIDE: DeductionsOverride,
    K.BESCHEID_ZVE: BescheidZve,
    K.MARGINAL_RATE_OVERRIDE: MarginalRateOverride,
}
# Per owner; the other two are household-level (joint filing: one zvE, one rate). Migration
# 0016's CHECKs say the same.
OWNER_SCOPED = frozenset({K.EXPECTED_GROSS_PAY, K.DEDUCTIONS_OVERRIDE})
del K


def income_value_by_kind(data: Any) -> Any:
    """Parse `value` by `kind`: three kinds share the shape {amount}, so a plain union would
    guess. Used by IncomeInputCreate and the two income_input_recorded payloads."""
    if isinstance(data, dict) and isinstance(data.get("value"), dict):
        try:
            model = INCOME_INPUT_VALUES[IncomeInputKind(data.get("kind"))]
        except ValueError:
            return data  # an unknown kind fails on its own field
        return data | {"value": model.model_validate(data["value"])}
    return data


class IncomeInputCreate(Schema):
    """One income input row as written. A correction is a new row with supersedes_id (a
    current row of the same kind, year and owner); withdrawn = true ends a row without
    replacing it (its value repeats the withdrawn row's). Rows are never changed."""

    owner_id: UUID | None = None
    kind: IncomeInputKind
    tax_year: TaxYear
    document_id: UUID | None = None  # the Bescheid (required for bescheid_zve)
    value: IncomeInputValue
    note: str | None = Field(default=None, max_length=500)
    supersedes_id: UUID | None = None
    withdrawn: bool = False

    @model_validator(mode="before")
    @classmethod
    def _value_by_kind(cls, data: Any) -> Any:
        return income_value_by_kind(data)

    @model_validator(mode="after")
    def _scope(self) -> Self:
        k = self.kind
        problems = []
        if type(self.value) is not INCOME_INPUT_VALUES[k]:
            problems.append("value does not match the kind")
        if (self.owner_id is not None) != (k in OWNER_SCOPED):
            problems.append(
                "owner_id is set exactly for expected_gross_pay and deductions_override"
            )
        if k is IncomeInputKind.BESCHEID_ZVE and self.document_id is None:
            problems.append("a Bescheid's zvE needs the Bescheid as its document")
        if self.withdrawn and self.supersedes_id is None:
            problems.append("a withdrawal supersedes the row it withdraws")
        if problems:
            raise ValueError(f"{INCOME_INPUT_SCOPE_INVALID}: {'; '.join(problems)}")
        return self


class IncomeInputFact(IncomeInputCreate):
    """A stored row as the rules see it: every row of the history, so the rules can tell the
    one in force (not superseded by another row, not withdrawn) from the rest."""

    id: UUID


class IncomeInput(IncomeInputFact):
    """API read model (the table names its timestamp recorded_at, so not Record)."""

    workspace_id: UUID
    recorded_at: datetime
    recorded_by: UUID | None = None


# --- Scenarios (D87) -------------------------------------------------------------------


class LoanLever(Schema):
    loan_id: UUID
    follow_up_rate: Rate | None = None  # None = the loan record's (null there = nominal rate)


class Levers(Schema):
    """What the user sets for a projection (D87). Defaults are the spec's (Q4, Luis)."""

    value_growth: Rate = Decimal("0.02")
    rent_growth: Rate = Decimal("0.02")
    cost_inflation: Rate = Decimal("0.02")
    sale_year: TaxYear
    today_value: PositiveMoney | None = None  # None = the purchase price
    upkeep_per_year: NonNegativeMoney = Decimal("600.00")
    selling_costs_rate: Rate = Decimal("0.0357")
    vacancy_months: VacancyMonths = Decimal("0")
    loans: list[LoanLever] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_lever_per_loan(self) -> Self:
        ids = [lever.loan_id for lever in self.loans]
        if len(ids) != len(set(ids)):
            raise ValueError("one lever per loan")
        return self


class ScenarioCreate(Schema):
    property_id: UUID
    name: str = Field(min_length=1, max_length=100)
    is_default: bool = False
    levers: Levers
    supersedes_id: UUID | None = None
    withdrawn: bool = False

    @model_validator(mode="after")
    def _withdrawal(self) -> Self:
        if self.withdrawn and self.supersedes_id is None:
            raise ValueError("a withdrawal supersedes the scenario it withdraws")
        return self


class Scenario(ScenarioCreate):
    id: UUID
    workspace_id: UUID
    recorded_at: datetime
    recorded_by: UUID | None = None


# --- Tax effect and the household zvE (§5.1, §5.2) -------------------------------------

RateSource = Literal["estimate", "bescheid", "override"]


class TaxBreakdown(Schema):
    """T(zvE) of D82: Einkommensteuer (splitting, whole euros), Soli and church tax (cents)."""

    est: NonNegativeMoney
    soli: NonNegativeMoney
    church: NonNegativeMoney
    total: NonNegativeMoney

    @model_validator(mode="after")
    def _sum(self) -> Self:
        if self.est + self.soli + self.church != self.total:
            raise ValueError("total = est + soli + church")
        if self.est != self.est.to_integral_value():
            raise ValueError("the Einkommensteuer is whole euros (§32a Abs. 1 S. 6)")
        return self


class TaxEffect(Schema):
    """D82: effect = T(zvE_base + R) − T(zvE_base), or R × rate under an override (then the
    two breakdowns are None). effective_rate = effect ÷ R in percent, one decimal half-up,
    None for R = 0; slope (display only) is the tariff's slope at zvE_base including Soli and
    church tax, shown when R = 0."""

    effect: Money
    effective_rate: Percent | None = None
    slope: Percent | None = None
    source: RateSource
    carried_forward: bool = False
    breakdown_with: TaxBreakdown | None = None
    breakdown_without: TaxBreakdown | None = None
    sources: list[SourceRef] = Field(default_factory=list)


class OwnerZve(Schema):
    """One owner's part of the estimate (D84). deductions = the default or the override."""

    owner_id: UUID
    gross: NonNegativeMoney
    deductions: NonNegativeMoney
    deductions_source: Literal["default", "override"]
    amount: NonNegativeMoney  # max(0, gross − deductions)


class ZveEstimate(Schema):
    """zvE_base (D83, D84): the household's taxable income without this property. amount is
    None only under a rate override with nothing else entered for the year."""

    amount: NonNegativeMoney | None
    source: RateSource
    carried_forward: bool = False
    tax_year: TaxYear  # the year whose rows were used (earlier than asked when carried)
    per_owner: list[OwnerZve] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _amount(self) -> Self:
        if self.amount is None and self.source != "override":
            raise ValueError("an estimate or a Bescheid always has an amount")
        parts = sum((o.amount for o in self.per_owner), Decimal("0"))
        if self.source == "estimate" and self.per_owner and parts != self.amount:
            raise ValueError("an estimate is the sum of the owners' parts")
        return self


# --- Year figures (§5.3, D85, D86, D91) -------------------------------------------------


class YearKind(StrEnum):
    ACTUAL = "actual"  # a complete year from confirmed allocations
    ACTUAL_PARTIAL = "actual_partial"  # today's year, as far as booked ("bisher")
    PROJECTED = "projected"  # from the scenario (D86)


class YearFlag(StrEnum):
    SO_FAR = "so_far"  # the current year: what is booked so far
    INCOME_MISSING = "income.missing"  # no income input for the year: no tax effect
    TAX_ESTIMATED = "tax_estimated"  # the tax effect of a year not yet assessed


class YearFigures(Schema):
    """One year of the actuals card and the projection table (§5.3). The figures are sums of
    confirmed, non-zero allocations of the year by §11 (projected years: of the synthesized
    rows and the real lines §11 puts into that year)."""

    year: TaxYear
    kind: YearKind
    flags: list[YearFlag] = Field(default_factory=list)
    rent_in: Money
    other_income: Money
    umlagen_in: Money
    umlagen_out: Money
    umlagen_net: Money
    interest: Money
    principal_repaid: Money
    running_costs: Money
    cash_out: Money
    taxable_result: Money | None
    afa: Money | None  # the building's (wk.afa)
    # The D80 kitchen: the draft's wk.afa_movable line amount (§5.6, Q-O4); None without one.
    # The §23 add-back takes `afa` (wk.afa) only.
    afa_movable: Money | None = None
    tax_effect: TaxEffect | None
    net_cash: Money
    invested: Money | None = None  # purchase year: Σ purchase_price + acquisition_cost
    value: Money | None = None  # projected years (§5.4 step 6)
    loan_balance: Money | None = None
    equity: Money | None = None
    # The Pauschale check (D91, amended 9 Nov 2026; D92): the flat-rate charges received
    # (umlagen_in of flat_rate tenancies); the months they were in force (the calendar months
    # of the year in which at least one flat_rate tenancy was in force, any day of the month,
    # not before the month of the handover); and (flat_rate_in − umlagen_out) ÷ those months
    # to the cent, half-up. Both None without a flat_rate tenancy or with 0 months. Negative =
    # costs above the Pauschalen.
    flat_rate_in: Money = Decimal("0")
    flat_rate_months: int | None = Field(default=None, ge=1, le=12)
    flat_rate_gap_monthly: Money | None = None
    sources: list[SourceRef] = Field(default_factory=list)


# --- Sale, bridge, returns (§5.5, D88, D89) ---------------------------------------------


class OwnerGain(Schema):
    """One spouse's share of a §23 gain, tested against the Freigrenze (< 1,000, a cliff)."""

    owner_id: UUID
    share: Fraction
    gain: Money
    below_freigrenze: bool


class BridgeRow(Schema):
    key: Literal[
        "appreciation", "loan_paid_down", "cash_flow", "buying_costs", "selling_costs", "sale_tax"
    ]
    amount: Money
    sources: list[SourceRef] = Field(default_factory=list)


class DatedFlow(Schema):
    """One flow of the IRR (D89): −E₀ on the purchase date, each year's net cash on 31 Dec,
    the net sale proceeds on the sale date."""

    on: date
    amount: Money
    kind: Literal["initial_equity", "cash_flow", "net_proceeds"]
    sources: list[SourceRef] = Field(default_factory=list)


class SaleResult(Schema):
    """D88, D89. gain is None when the sale is outside §23 (taxable False); a negative gain is
    a loss that is not offset ("nicht verrechenbar", sale_tax 0.00)."""

    sale_date: date
    tax_free_from: date | None
    taxable: bool
    value: Money
    selling_costs: Money
    afa_claimed: Money  # the building's AfA of every year owned through the sale year
    gain: Money | None
    gain_per_owner: list[OwnerGain] = Field(default_factory=list)
    freigrenze_applied: bool
    sale_tax: Money
    loan_balance: Money
    net_proceeds: Money  # value − selling costs − sale tax − loan balance
    initial_equity: Money  # E₀ = purchase price + Σ cost rows − Σ loan principal
    profit: Money
    bridge: list[BridgeRow] = Field(default_factory=list)
    cagr: ReturnRate | None = None
    irr: ReturnRate | None = None
    flows: list[DatedFlow] = Field(default_factory=list)

    @model_validator(mode="after")
    def _arithmetic(self) -> Self:
        if self.net_proceeds != (
            self.value - self.selling_costs - self.sale_tax - self.loan_balance
        ):
            raise ValueError("net_proceeds = value − selling costs − sale tax − loan balance")
        if self.bridge and sum((r.amount for r in self.bridge), Decimal("0")) != self.profit:
            raise ValueError("the bridge rows sum to the profit")
        if not self.taxable and (self.gain is not None or self.sale_tax != 0):
            raise ValueError("a sale outside §23 has no gain and no tax")
        return self


class Assumption(Schema):
    """A stated assumption of the projection, as data; the frontend holds the German text."""

    code: str = Field(min_length=1)
    params: dict[str, str] = Field(default_factory=dict)


class Actuals(Schema):
    property_id: UUID
    years: list[YearFigures]


class Projection(Schema):
    property_id: UUID
    scenario: Levers
    scenario_id: UUID | None = None  # None: levers from the request only
    tariff: TariffStamp
    rule_set: RuleSetStamp
    years: list[YearFigures]
    sale: SaleResult
    assumptions: list[Assumption] = Field(default_factory=list)
