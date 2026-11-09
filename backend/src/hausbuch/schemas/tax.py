"""Phase 3: the tax year (docs/phases/phase-3.md §4.1, §5, §7).

Three groups:
- The rule set as data (RuleSet, FormMapping, FormLine): loaded from
  rules/tax/rulesets/<year>.yaml (D53). Form lines are keyed by a stable Hausbuch line code;
  the Zeile and the ELSTER Kennzahl belong to one form year.
- Tax inputs (D58): the user's tax decisions and verified certificate figures, one append-only
  table `tax_input` (migration 0013) with a typed `value` per kind.
- Facts and results of the pure rules in hausbuch.rules.tax (§5) and the draft they assemble
  (AnlageVDraft, §5.11). No tenant name, IBAN or purpose text appears in any of them (D5): a
  counterparty is at most a masked IBAN.

Sign conventions (shared by every rule and fixture):
- An allocation's amount is signed like its transaction: income positive, costs negative.
- An income line's amount is the signed sum of its allocations (a refund to a tenant reduces
  it); a cost line's amount is the negated sum, so a cost line is positive.
- Tracked, moved and basis-addition amounts are magnitudes (positive), as the screen shows them.
- `excluded_open.sum` is the signed sum of the excluded lines.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from .allocation import AllocationCategory, ServicePeriod
from .checklist import ChecklistFacts, ChecklistItem
from .common import (
    Area,
    BuildingShare,
    Fraction,
    LivingArea,
    MaskedIban,
    Money,
    NonNegativeMoney,
    Percent,
    PositiveMoney,
    Rate,
    Record,
    Schema,
    Sha256Hex,
    TaxYear,
    Year,
)
from .enums import AcquisitionCostKind, OperatingCostsTerms, PriceSplitSource, PropertyKind
from .property import Address

# Error codes the schemas raise inside a validation message; rules/errors.py holds the same
# strings with the other phase 3 codes (schemas do not import rules).
FORM_MAPPING_INCOMPLETE = "form_mapping.incomplete"
TAX_INPUT_SCOPE_INVALID = "tax_input.scope_invalid"

# --- The rule set as data (§4.1) -------------------------------------------------------


class FormStatus(StrEnum):
    """Whether a form mapping is the year's published form or a copy of an earlier one (§4.3)."""

    PUBLISHED = "published"
    PROVISIONAL = "provisional"


LineKind = Literal["header", "income", "cost", "afa", "total", "result", "flag"]
Rounding = Literal["down", "up", "none"]  # D60: income down, cost up


class FormLine(Schema):
    """One line of one year's Anlage V.

    zeile is the printed line that carries the Kennzahl and the amount (for a block such as
    "Schuldzinsen" Z46–48 that is the "abzugsfähig" line 48); zeile_from is the block's first
    line when it spans several (46), else None. A line the form does not print (a total the
    form leaves to ELSTER) has zeile None. The one block of repeated rows, income.rent_units
    (Z13 "Bezeichnung der Wohneinheit", Z14 "weiterer Wohneinheiten"), prints its first row on
    zeile_from (13) and rows 2..n on zeile (14).
    """

    key: str = Field(min_length=1)  # Hausbuch line code, stable across years ("wk.interest")
    zeile_from: int | None = Field(default=None, ge=1)
    zeile: int | None = Field(default=None, ge=1)
    kennzahl: str | None = Field(default=None, pattern=r"^[0-9]{2,3}$")
    label: str = Field(min_length=1)  # German, as on the form
    kind: LineKind
    rounding: Rounding

    @model_validator(mode="after")
    def _block_ordered(self) -> Self:
        if self.zeile_from is not None and (self.zeile is None or self.zeile_from > self.zeile):
            raise ValueError("zeile_from is the first line of a block that ends at zeile")
        return self


# Every line key the draft writes (§4.2). A mapping without one of them fails to load.
SPREAD_PRIOR_KEYS = tuple(f"wk.maintenance_spread_prior_{k}" for k in (1, 2, 3, 4))
DRAFT_LINE_KEYS: tuple[str, ...] = (
    "header.address",
    "header.aktenzeichen",
    "header.contract_date",
    "header.transfer_date",
    "header.completed",
    "header.sold",
    "header.living_area",
    "income.rent_units",
    "income.rent_total",
    "income.umlagen",
    "income.umlagen_settlement",
    "income.umlagen_not_agreed",
    "income.other",
    "income.total",
    "wk.afa",
    "wk.afa_movable",  # D80: AfA on movable assets (the Einbauküche), Z42–45
    "wk.interest",
    "wk.financing_costs",
    "wk.maintenance",
    "wk.maintenance_spread_total",
    "wk.maintenance_spread_current",
    *SPREAD_PRIOR_KEYS,  # _k = the spread that started k years before the tax year
    "wk.allocable_costs",
    "wk.non_allocable_costs",
    "wk.other",
    "wk.total",
    "result.surplus",
    "result.owner_a",
    "result.owner_b",
    "reduced_rent.cut_percent",
)


class FormMapping(Schema):
    """Category → line, and line → Zeile/Kennzahl, for one form year.

    category_lines names every AllocationCategory: a line key, or None when the category is
    not on the form. `tracked` lists the None categories the draft still shows as "erfasst,
    nicht abziehbar" (Tilgung, Erhaltungsrücklage); `open` the undecided ones it excludes and
    counts (D59). `renovation` maps to the maintenance line; without a renovation_treatment
    input it is open as well (§5.4), which is the rules' business, not the mapping's.
    """

    form_year: TaxYear
    status: FormStatus
    version_stamp: str = Field(min_length=1)  # "2025AnlV101NET – September 2025"
    source_url: str = Field(min_length=1)
    lines: dict[str, FormLine]
    category_lines: dict[AllocationCategory, str | None]
    tracked: list[AllocationCategory] = Field(default_factory=list)
    open: list[AllocationCategory] = Field(default_factory=list)
    spread_prior_years: int = Field(default=4, ge=1, le=4)  # Z61–72: "aus Y−4" … "aus Y−1"

    @model_validator(mode="after")
    def _complete(self) -> Self:
        problems: list[str] = []
        missing = [c.value for c in AllocationCategory if c not in self.category_lines]
        if missing:
            problems.append(f"categories without a line or None: {', '.join(missing)}")
        unknown = sorted(
            {k for k in self.category_lines.values() if k is not None and k not in self.lines}
        )
        if unknown:
            problems.append(f"categories map to unknown lines: {', '.join(unknown)}")
        absent = [k for k in DRAFT_LINE_KEYS if k not in self.lines]
        if absent:
            problems.append(f"lines the draft writes are missing: {', '.join(absent)}")
        misnamed = [k for k, line in self.lines.items() if line.key != k]
        if misnamed:
            problems.append(f"line keys differ from their entry: {', '.join(misnamed)}")
        for name, cats in (("tracked", self.tracked), ("open", self.open)):
            bad = [c.value for c in cats if self.category_lines.get(c) is not None]
            if bad:
                problems.append(f"{name} categories must map to None: {', '.join(bad)}")
        if problems:
            raise ValueError(f"{FORM_MAPPING_INCOMPLETE}: {'; '.join(problems)}")
        return self


class AfaRateRule(Schema):
    """§7 Abs. 4 S. 1 Nr. 2 EStG by completion year; bounds inclusive, None = open."""

    completed_from: Year | None = None
    completed_to: Year | None = None
    rate: Rate


class RuleSet(Schema):
    tax_year: TaxYear
    version: str = Field(min_length=1)  # "2026.1"; stamped on every export
    short_period_days: int = Field(default=10, ge=1)  # §11, H 11 EStH
    afa_rates: list[AfaRateRule] = Field(min_length=1)
    anschaffungsnah_share: Rate = Decimal("0.15")
    anschaffungsnah_years: int = Field(default=3, ge=1)
    default_vat_rate: Rate = Decimal("0.19")  # net estimate when no invoice net (D56)
    spread_82b_min_years: int = Field(default=2, ge=1)
    spread_82b_max_years: int = Field(default=5, ge=1)
    reduced_rent_full: Rate = Decimal("0.66")
    reduced_rent_split: Rate = Decimal("0.50")
    speculation_years: int = Field(default=10, ge=1)
    form: FormMapping

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.form.status is FormStatus.PUBLISHED and self.form.form_year != self.tax_year:
            raise ValueError("a published form mapping is the tax year's own form")
        if self.form.status is FormStatus.PROVISIONAL and self.form.form_year >= self.tax_year:
            raise ValueError("a provisional mapping copies an earlier year's form")
        if self.reduced_rent_split >= self.reduced_rent_full:
            raise ValueError("the split threshold lies below the full-deduction threshold")
        if self.spread_82b_min_years > self.spread_82b_max_years:
            raise ValueError("spread_82b_min_years exceeds spread_82b_max_years")
        # The AfA rates cover every completion year exactly once, in order.
        rates = self.afa_rates
        if rates[0].completed_from is not None or rates[-1].completed_to is not None:
            raise ValueError("afa_rates must be open at both ends")
        for before, after in zip(rates, rates[1:], strict=False):
            if before.completed_to is None or after.completed_from != before.completed_to + 1:
                raise ValueError("afa_rates must be contiguous and ordered")
        return self

    def afa_rate(self, build_year: int) -> Decimal:
        for r in self.afa_rates:
            if (r.completed_from is None or build_year >= r.completed_from) and (
                r.completed_to is None or build_year <= r.completed_to
            ):
                return r.rate
        raise AssertionError("afa_rates cover every year")  # guaranteed by the validator


# --- Tax inputs (D58, §6, §7) ----------------------------------------------------------


class TaxInputKind(StrEnum):
    SPREAD_82B = "spread_82b"  # §82b EStDV, per allocation (D62)
    RENOVATION_TREATMENT = "renovation_treatment"  # erhaltung | herstellung, per allocation
    EXCLUDE_FROM_15PCT = "exclude_from_15pct"  # per allocation, with a reason (D56)
    MARKET_RENT = "market_rent"  # ortsübliche Miete per Wohnung (D61, D71)
    RUECKLAGE_SPENT = "ruecklage_spent"  # the Hausgeldabrechnung's Entnahme, per year (D57)
    INTEREST_CERTIFIED = "interest_certified"  # the Zinsbescheinigung, per loan and year (D66)
    PRE_HANDOVER_INCOME = "pre_handover_income"  # how income before the handover counts (D63)
    USEFUL_LIFE = "useful_life"  # a shorter actual useful life from a Gutachten, from a year (D79)
    MOVABLE_ASSET = "movable_asset"  # a movable asset (the Einbauküche), from a year (D80)


class Spread82b(Schema):
    years: int = Field(ge=2, le=5)


class RenovationTreatment(Schema):
    treatment: Literal["erhaltung", "herstellung"]


class ExcludeFrom15Pct(Schema):
    reason: Literal["annual_maintenance", "extension", "damage_after_acquisition"]


class MarketRent(Schema):
    """The Mietspiegel's net cold rent per m² (D61); the running costs come from the ledger."""

    valid_from: date
    cold_rent_per_m2: PositiveMoney
    source: Literal["mietspiegel", "gutachten", "portal", "other"]


class RuecklageSpent(Schema):
    amount: NonNegativeMoney  # the owner's share; tax_year and document_id on the row


class InterestCertified(Schema):
    amount: NonNegativeMoney  # loan_id, tax_year and document_id on the row


class PreHandoverIncome(Schema):
    treatment: Literal["rent"]  # D63; the only reading decided (Q1)


class UsefulLife(Schema):
    """§7 Abs. 4 S. 2 EStG (D79): the building's shorter actual useful life in whole years, as
    the Gutachten (document_id on the row) states it; tax_year on the row is the first tax year
    it applies to. The service refuses a life not shorter than the statutory one."""

    years: int = Field(ge=1)


class MovableAsset(Schema):
    """A movable asset with its own AfA (D80; BFH IX R 14/15: a fitted kitchen is not part of
    the building). tax_year on the row is its first year (the purchase year when it came with
    the flat); document_id is optional (the estimate's evidence, e.g. a planner printout).

    amount is its share of the purchase price, gross (or its own price when bought separately).
    in_purchase_price: bought with the flat, so its basis (amount plus its share of the
    Nebenkosten) leaves the building's AfA basis, never the land's."""

    label: Literal["einbaukueche"]  # one asset per label; more labels may follow
    amount: PositiveMoney
    useful_life_years: int = Field(default=10, ge=1, le=25)
    in_purchase_price: bool = True


TaxInputValue = (
    Spread82b
    | RenovationTreatment
    | ExcludeFrom15Pct
    | MarketRent
    | RuecklageSpent
    | InterestCertified
    | PreHandoverIncome
    | UsefulLife
    | MovableAsset
)

K = TaxInputKind
TAX_INPUT_VALUES: dict[TaxInputKind, type[Schema]] = {
    K.SPREAD_82B: Spread82b,
    K.RENOVATION_TREATMENT: RenovationTreatment,
    K.EXCLUDE_FROM_15PCT: ExcludeFrom15Pct,
    K.MARKET_RENT: MarketRent,
    K.RUECKLAGE_SPENT: RuecklageSpent,
    K.INTEREST_CERTIFIED: InterestCertified,
    K.PRE_HANDOVER_INCOME: PreHandoverIncome,
    K.USEFUL_LIFE: UsefulLife,
    K.MOVABLE_ASSET: MovableAsset,
}
# The scope each kind needs; migration 0013's CHECKs say the same.
ALLOCATION_SCOPED = frozenset({K.SPREAD_82B, K.RENOVATION_TREATMENT, K.EXCLUDE_FROM_15PCT})
# tax_year set: the year, or the first year (useful_life D79, movable_asset D80).
YEAR_SCOPED = frozenset({K.RUECKLAGE_SPENT, K.INTEREST_CERTIFIED, K.USEFUL_LIFE, K.MOVABLE_ASSET})
# A document required: the certificate, the Abrechnung, the Gutachten (D79). A movable asset's
# evidence is optional (D80: its value is often an estimate).
DOCUMENT_REQUIRED = frozenset({K.RUECKLAGE_SPENT, K.INTEREST_CERTIFIED, K.USEFUL_LIFE})
del K


def value_by_kind(data: Any) -> Any:
    """Parse `value` by `kind`: two kinds share the shape {amount}, so a plain union would
    guess. Used by TaxInputCreate and the property.tax_input_recorded payload."""
    if isinstance(data, dict) and isinstance(data.get("value"), dict):
        try:
            model = TAX_INPUT_VALUES[TaxInputKind(data.get("kind"))]
        except ValueError:
            return data  # an unknown kind fails on its own field
        return data | {"value": model.model_validate(data["value"])}
    return data


class TaxInputCreate(Schema):
    """One tax input row as written. A correction is a new row with supersedes_id (the row it
    replaces, a current row of the same kind and scope); withdrawn = true ends a decision
    without replacing it (its value repeats the withdrawn row's). Rows are never changed."""

    property_id: UUID
    kind: TaxInputKind
    tax_year: TaxYear | None = None
    unit_id: UUID | None = None  # market_rent of an MFH unit; None = the property's flat
    loan_id: UUID | None = None
    allocation_id: UUID | None = None
    # evidence: the certificate, the Abrechnung, the Mietspiegel, the Gutachten (useful_life),
    # an estimate's printout (movable_asset, optional)
    document_id: UUID | None = None
    value: TaxInputValue
    note: str | None = Field(default=None, max_length=500)
    supersedes_id: UUID | None = None
    withdrawn: bool = False

    @model_validator(mode="before")
    @classmethod
    def _value_by_kind(cls, data: Any) -> Any:
        return value_by_kind(data)

    @model_validator(mode="after")
    def _scope(self) -> Self:
        k = self.kind
        problems = []
        if type(self.value) is not TAX_INPUT_VALUES[k]:
            problems.append("value does not match the kind")
        if (self.allocation_id is not None) != (k in ALLOCATION_SCOPED):
            problems.append("allocation_id is set exactly for the per-allocation kinds")
        if self.unit_id is not None and k is not TaxInputKind.MARKET_RENT:
            problems.append("unit_id belongs to market_rent only")
        if (self.loan_id is not None) != (k is TaxInputKind.INTEREST_CERTIFIED):
            problems.append("loan_id is set exactly for interest_certified")
        if (self.tax_year is not None) != (k in YEAR_SCOPED):
            problems.append(
                "tax_year is set exactly for ruecklage_spent, interest_certified, useful_life"
                " and movable_asset"
            )
        if k in DOCUMENT_REQUIRED and self.document_id is None:
            problems.append("a certificate figure or a useful life needs its document")
        if self.withdrawn and self.supersedes_id is None:
            problems.append("a withdrawal supersedes the row it withdraws")
        if problems:
            raise ValueError(f"{TAX_INPUT_SCOPE_INVALID}: {'; '.join(problems)}")
        return self


class TaxInputFact(TaxInputCreate):
    """A stored row as the rules see it: every row of the history, so the rules can tell the
    one in force (not superseded by another row, not withdrawn) from the rest."""

    id: UUID


class TaxInput(TaxInputFact):
    """API read model (the table names its timestamp recorded_at, so not Record)."""

    workspace_id: UUID
    recorded_at: datetime
    recorded_by: UUID | None = None


# --- Warnings and sources (§5.11, §13) -------------------------------------------------


class WarningCode(StrEnum):
    YEAR_END_STRADDLE = "year_end_straddle"
    AFA_NO_SPLIT = "afa.no_split"
    AFA_COST_ROW_UNPAID = "afa.cost_row_unpaid"
    AFA_ACQUISITION_COST_WITHOUT_ROW = "afa.acquisition_cost_without_row"
    AFA_PURCHASE_PRICE_MISMATCH = "afa.purchase_price_mismatch"
    AFA_USEFUL_LIFE_REPORT = "afa.useful_life_report"  # D79: Z33 laut Erläuterung, the Gutachten
    AFA_MOVABLE_ASSET_NO_EVIDENCE = "afa.movable_asset_no_evidence"  # D80: an estimate, no Beleg
    # D80: a movable asset's basis is not below the building's (a later split changed it)
    AFA_MOVABLE_ASSET_EXCEEDS_BUILDING = "afa.movable_asset_exceeds_building"
    LIMIT_EXCEEDED = "limit.exceeded"
    LIMIT_PRIOR_YEAR_AMENDMENT = "limit.prior_year_amendment"
    LIMIT_ESTIMATED_NET = "limit.estimated_net"
    # A cost booked soon after the window with no service period: when was the work done? (D76)
    ANSCHAFFUNGSNAH_PERFORMANCE_DATE_CHECK = "anschaffungsnah.performance_date_check"
    RENOVATION_UNDECIDED = "renovation.undecided"
    INTEREST_CERTIFICATE_MISMATCH = "interest.certificate_mismatch"
    HAUSGELD_ABRECHNUNG_PENDING = "hausgeld.abrechnung_pending"
    INCOME_OTHER_REVIEW = "income.other_review"
    INCOME_PRE_HANDOVER = "income.pre_handover"
    INCOME_LIVING_AREA_MISSING = "income.living_area_missing"  # D71
    UMLAGEN_MIXED_TERMS = "umlagen.mixed_terms"  # D72
    REDUCED_RENT_NO_MARKET_RENT = "reduced_rent.no_market_rent"
    REDUCED_RENT_PROGNOSIS_NEEDED = "reduced_rent.prognosis_needed"
    REDUCED_RENT_PARTIAL = "reduced_rent.partial"
    DRAFT_OPEN_LINES_EXCLUDED = "draft.open_lines_excluded"
    DRAFT_FORM_PROVISIONAL = "draft.form_provisional"
    DRAFT_ALLOCATION_WITHOUT_PROPERTY = "draft.allocation_without_property"
    ARCHIVE_FINANZAMT_COPY_NEEDED = "archive.finanzamt_copy_needed"  # Q8


Severity = Literal["info", "warning"]
W = WarningCode
# "info": nothing is wrong, the user should know; "warning": a figure may be wrong or open.
WARNING_SEVERITY: dict[WarningCode, Severity] = {
    W.YEAR_END_STRADDLE: "info",
    W.AFA_NO_SPLIT: "warning",
    W.AFA_COST_ROW_UNPAID: "info",
    W.AFA_ACQUISITION_COST_WITHOUT_ROW: "warning",
    W.AFA_PURCHASE_PRICE_MISMATCH: "info",
    W.AFA_USEFUL_LIFE_REPORT: "info",
    W.AFA_MOVABLE_ASSET_NO_EVIDENCE: "info",
    W.AFA_MOVABLE_ASSET_EXCEEDS_BUILDING: "warning",
    W.LIMIT_EXCEEDED: "warning",
    W.LIMIT_PRIOR_YEAR_AMENDMENT: "warning",
    W.LIMIT_ESTIMATED_NET: "info",
    W.ANSCHAFFUNGSNAH_PERFORMANCE_DATE_CHECK: "warning",
    W.RENOVATION_UNDECIDED: "warning",
    W.INTEREST_CERTIFICATE_MISMATCH: "warning",
    W.HAUSGELD_ABRECHNUNG_PENDING: "info",
    W.INCOME_OTHER_REVIEW: "warning",
    W.INCOME_PRE_HANDOVER: "warning",
    W.INCOME_LIVING_AREA_MISSING: "warning",
    W.UMLAGEN_MIXED_TERMS: "warning",
    W.REDUCED_RENT_NO_MARKET_RENT: "info",
    W.REDUCED_RENT_PROGNOSIS_NEEDED: "warning",
    W.REDUCED_RENT_PARTIAL: "warning",
    W.DRAFT_OPEN_LINES_EXCLUDED: "warning",
    W.DRAFT_FORM_PROVISIONAL: "info",
    W.DRAFT_ALLOCATION_WITHOUT_PROPERTY: "warning",
    W.ARCHIVE_FINANZAMT_COPY_NEEDED: "info",
}
del W

SourceKind = Literal[
    "allocation",
    "transaction",
    "document",
    "property",
    "price_split",
    "acquisition_cost",
    "loan",
    "tax_input",
    "rule",
]


class SourceRef(Schema):
    """What a figure came from (principle 2). `rule` is the rule set itself (no id)."""

    kind: SourceKind
    id: UUID | None = None
    amount: Money | None = None

    @model_validator(mode="after")
    def _id_unless_rule(self) -> Self:
        if (self.id is None) != (self.kind == "rule"):
            raise ValueError("every source but the rule set names its record")
        return self


class DraftWarning(Schema):
    """A warning as data; the frontend holds the German text. params are strings (amounts as
    decimal strings, dates ISO) so the draft's JSON is the same before and after a round trip.
    severity defaults from WARNING_SEVERITY and must agree with it."""

    code: WarningCode
    severity: Severity
    params: dict[str, str] = Field(default_factory=dict)
    refs: list[SourceRef] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _default_severity(cls, data: Any) -> Any:
        if isinstance(data, dict) and "severity" not in data and "code" in data:
            try:
                return data | {"severity": WARNING_SEVERITY[WarningCode(data["code"])]}
            except ValueError:
                return data
        return data

    @model_validator(mode="after")
    def _severity_fixed(self) -> Self:
        if self.severity != WARNING_SEVERITY[self.code]:
            raise ValueError(f"{self.code} has severity {WARNING_SEVERITY[self.code]}")
        return self


# --- §5.1: the tax year of a payment ---------------------------------------------------


class PaymentFacts(Schema):
    """What §5.1 reads. due_on is resolved by the service before the rule runs (§5.1 step 4):
    the loan schedule entry, the tenancy's rent_due_day, or the service period's first day."""

    allocation_id: UUID | None = None
    booking_date: date
    value_date: date | None = None
    amount: Money
    category: AllocationCategory
    recurring: bool = False
    service_period: ServicePeriod | None = None
    due_on: date | None = None


class TaxYearNote(StrEnum):
    DUE_DATE_MISSING = "due_date_missing"  # recurring, one economic year, no due date known
    YEAR_END_STRADDLE = "year_end_straddle"  # value date in another year (§5.1 step 5)


class TaxYearResult(Schema):
    year: TaxYear
    rule: Literal["paid", "short_period_shift"]
    notes: list[TaxYearNote] = Field(default_factory=list)


class AllocationFact(PaymentFacts):
    """A confirmed allocation as the draft sees it. Amount signed (income +, costs −); a 0.00
    row may be present and is ignored by every rule (D70). invoice_net is the linked invoice
    extraction's amount_net (a magnitude), for the 15 % tracker (D56)."""

    allocation_id: UUID
    transaction_id: UUID | None = None
    property_id: UUID | None = None  # None: the account's line without a property (warning)
    unit_id: UUID | None = None
    tenancy_id: UUID | None = None
    loan_id: UUID | None = None
    document_id: UUID | None = None
    invoice_net: PositiveMoney | None = None
    counterparty_iban_masked: MaskedIban | None = None


# --- §5.2 AfA and §5.3 the 15 % tracker ------------------------------------------------


class AfaCostRow(Schema):
    """An acquisition cost row of the property record. paid_by: the confirmed acquisition_cost
    allocations the service paired with it (same document, else the same amount); empty =
    `afa.cost_row_unpaid` (paid_on alone does not count: it may have been paid privately)."""

    id: UUID
    kind: AcquisitionCostKind
    amount: PositiveMoney
    paid_on: date | None = None
    document_id: UUID | None = None
    paid_by: list[UUID] = Field(default_factory=list)


class AfaSplit(Schema):
    id: UUID
    source: PriceSplitSource
    building_share: BuildingShare
    evidence_document_id: UUID | None = None


class BasisAddition(Schema):
    """Herstellungskosten added to the AfA basis in the year paid, accruing from 1 January of
    that year (§5.2). The amount is gross: input VAT a VAT-exempt landlord cannot deduct is part
    of the Herstellungskosten (§9b Abs. 1 EStG); only the 15 % test itself is net (§5.3)."""

    allocation_id: UUID
    tax_year: TaxYear
    amount: PositiveMoney
    reason: Literal["herstellung", "limit_exceeded"]


class UsefulLifeFact(Schema):
    """A useful_life tax input in force (D79): from from_year on, the AfA runs over `years`
    from that year's book value (R 7.4 Abs. 9 EStR), until a later row takes over."""

    tax_input_id: UUID
    from_year: TaxYear
    years: int = Field(ge=1)
    document_id: UUID | None = None


class MovableAssetFact(Schema):
    """A movable_asset tax input in force (D80): its own linear AfA from from_year; when
    in_purchase_price, its basis leaves the building's AfA basis."""

    tax_input_id: UUID
    label: Literal["einbaukueche"]
    from_year: TaxYear
    amount: PositiveMoney  # its share of the purchase price, gross (or its own price)
    useful_life_years: int = Field(ge=1, le=25)
    in_purchase_price: bool = True
    document_id: UUID | None = None


class AfaFacts(Schema):
    property_id: UUID
    purchase_price: PositiveMoney
    build_year: Year
    purchase_date: date  # Übergang; the AfA starts in its month
    ownership_ended_on: date | None = None
    cost_rows: list[AfaCostRow] = Field(default_factory=list)
    split: AfaSplit | None = None
    additions: list[BasisAddition] = Field(default_factory=list)
    # acquisition_cost and purchase_price allocations: evidence only, never summed (D55)
    allocations: list[AllocationFact] = Field(default_factory=list)
    useful_lives: list[UsefulLifeFact] = Field(default_factory=list)  # in force only (D79)
    movable_assets: list[MovableAssetFact] = Field(default_factory=list)  # in force only (D80)


AfaMethod = Literal["statutory", "useful_life"]


class AfaLine(Schema):
    """wk.afa for one tax year. amount, basis and rate are None without a selected split.

    method (D79): `statutory` is the rate by completion year (rate set; the useful-life fields,
    book_value_start and remaining_months None); `useful_life` is the shorter actual useful
    life of a Gutachten, the book value at the start of the year spread over the months of that
    life remaining at its start (rate None)."""

    tax_year: TaxYear
    amount: NonNegativeMoney | None
    # (price + Σ cost rows) × share − the movable assets bought with the flat, before additions
    basis: NonNegativeMoney | None
    additions: NonNegativeMoney = Decimal("0")  # Σ additions of years ≤ tax_year
    rate: Rate | None
    months: int = Field(ge=0, le=12)
    method_code: Literal[1, 2] | None  # Z33: 1 linear / wie Vorjahr, 2 laut Erläuterung
    method: AfaMethod = "statutory"
    useful_life_years: int | None = None  # the governing useful_life row's years
    useful_life_from_year: TaxYear | None = None  # and its first year
    book_value_start: NonNegativeMoney | None = None  # basis + additions − earlier AfA
    remaining_months: int | None = Field(default=None, ge=0)  # of the useful life, at its start
    # D80: the movable assets' bases taken out of the building's share (0 without one)
    movable_deducted: NonNegativeMoney = Decimal("0.00")
    sources: list[SourceRef] = Field(default_factory=list)
    warnings: list[DraftWarning] = Field(default_factory=list)


class MovableAfaLine(Schema):
    """wk.afa_movable for one asset and tax year (D80): linear over useful_life_years from the
    AfA start month of from_year (§7 Abs. 1 EStG), pro rata by the months owned; the last year
    takes the rest, then 0.00.

    basis = amount × (price + Σ cost rows) ÷ price when in_purchase_price (its share of the
    Nebenkosten goes with it), else amount. book_value_start is the basis less the AfA of
    earlier years; remaining_months the months of the life left at the start of the year."""

    tax_input_id: UUID
    label: Literal["einbaukueche"]
    tax_year: TaxYear
    amount: NonNegativeMoney
    price_amount: PositiveMoney  # the input's amount
    basis: PositiveMoney
    in_purchase_price: bool
    useful_life_years: int = Field(ge=1, le=25)
    from_year: TaxYear
    months: int = Field(ge=0, le=12)
    book_value_start: NonNegativeMoney
    remaining_months: int = Field(ge=0)
    method_code: Literal[1, 2] | None  # Z42: 1 wie Vorjahr, 2 laut Erläuterung (first year)
    sources: list[SourceRef] = Field(default_factory=list)
    warnings: list[DraftWarning] = Field(default_factory=list)


class CountedCost(Schema):
    allocation_id: UUID
    booking_date: date
    tax_year: TaxYear
    gross: PositiveMoney
    net: PositiveMoney
    net_source: Literal["invoice", "estimated"]


class LimitFacts(Schema):
    afa: AfaFacts
    allocations: list[AllocationFact] = Field(default_factory=list)  # repairs, renovation
    tax_inputs: list[TaxInputFact] = Field(default_factory=list)


class LimitStatus(Schema):
    tax_year: TaxYear
    basis_building_cost: NonNegativeMoney | None  # None without a split
    limit: NonNegativeMoney | None
    window_start: date
    window_end: date
    counted: list[CountedCost] = Field(default_factory=list)
    used_net: NonNegativeMoney
    # used_net ÷ limit × 100, one decimal, half-up: how much of the 15 % allowance is used
    # ("bisher 14,2 %"); above 100.0 once exceeded. None without a split.
    ratio_percent: Percent | None
    exceeded: bool
    exceeded_in_year: TaxYear | None = None
    basis_additions: list[BasisAddition] = Field(default_factory=list)
    warnings: list[DraftWarning] = Field(default_factory=list)


# --- Facts of the draft (§5.11) --------------------------------------------------------


class TaxProperty(Schema):
    id: UUID
    label: str
    kind: PropertyKind
    address: Address | None = None
    build_year: Year
    notarized_on: date | None = None
    purchase_date: date  # Übergang
    ownership_ended_on: date | None = None
    purchase_price: PositiveMoney
    living_area_m2: LivingArea | None = None  # D71
    grundsteuer_reference: str | None = None  # header.aktenzeichen


class TaxOwner(Schema):
    owner_id: UUID
    share: Fraction


class TaxUnit(Schema):
    id: UUID
    label: str
    floor_area_m2: Area


class TaxRent(Schema):
    effective_from: date
    cold_rent: NonNegativeMoney
    operating_costs_prepayment: NonNegativeMoney = Decimal("0")


class TaxTenancy(Schema):
    """No party names (D5): a tenancy is its unit, dates, terms and rent rows in force."""

    id: UUID
    unit_id: UUID
    start_date: date
    end_date: date | None = None
    rent_due_day: int = Field(default=3, ge=1, le=28)
    operating_costs_terms: OperatingCostsTerms = OperatingCostsTerms.PREPAYMENT
    rents: list[TaxRent] = Field(default_factory=list)


class TaxLoan(Schema):
    id: UUID
    lender: str  # the Einzelangabe on Z46


class AccountCoverage(Schema):
    account_id: UUID
    covered_through: date | None = None  # last day of reconciled statements


class OpenLines(Schema):
    count: int = Field(ge=0)
    sum: Money = Decimal("0")


class DraftFacts(Schema):
    """Everything draft() reads, built by services/tax.py. allocations: every confirmed
    allocation of the property, all years (the 15 % window, §82b and the AfA additions reach
    back); the rules give each its year by §5.1. checklist: the phase 1 checklist facts; draft
    adds the phase 3 parts (allocations, market rents) from its own facts before it runs
    build_checklist for `today`. pending: proposed and unmatched lines of the year."""

    property: TaxProperty
    owners: list[TaxOwner] = Field(default_factory=list)
    units: list[TaxUnit] = Field(default_factory=list)
    tenancies: list[TaxTenancy] = Field(default_factory=list)
    split: AfaSplit | None = None
    cost_rows: list[AfaCostRow] = Field(default_factory=list)
    loans: list[TaxLoan] = Field(default_factory=list)
    allocations: list[AllocationFact] = Field(default_factory=list)
    tax_inputs: list[TaxInputFact] = Field(default_factory=list)
    checklist: ChecklistFacts | None = None
    today: date | None = None
    coverage: list[AccountCoverage] = Field(default_factory=list)
    pending: OpenLines = OpenLines(count=0)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        unit_ids = {u.id for u in self.units}
        if any(t.unit_id not in unit_ids for t in self.tenancies):
            raise ValueError("every tenancy must be on one of the units")
        if (self.checklist is None) != (self.today is None):
            raise ValueError("checklist facts and today come together")
        if self.checklist is not None and self.checklist.property_id != self.property.id:
            raise ValueError("the checklist facts are the property's")
        return self


# --- Rule results and the draft --------------------------------------------------------


class LineAmount(Schema):
    """A line's amount as a rule returns it (sign: see the module docstring). ticked is for
    a flag line (Z24) and amount is then None."""

    key: str
    amount: Money | None = None
    ticked: bool | None = None
    sources: list[SourceRef] = Field(default_factory=list)
    provisional: bool = False
    einzelangaben: list[str] = Field(default_factory=list)  # Z46: the lenders


class RoomRent(Schema):
    unit_id: UUID
    label: str
    amount: Money


class UnitRow(Schema):
    """One Z13 row per Wohnung (D71): the flat (wohnung = property) with its rooms listed, or an
    MFH unit (wohnung = unit). pre_handover: the part booked before the handover (D63)."""

    wohnung: Literal["property", "unit"]
    id: UUID
    label: str
    area_m2: Area
    area_source: Literal["living_area", "units"]
    amount: Money
    pre_handover: Money = Decimal("0")
    rooms: list[RoomRent] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)


class TrackedAmount(Schema):
    """Booked, not deductible this year (Tilgung, Erhaltungsrücklage)."""

    category: AllocationCategory
    amount: NonNegativeMoney
    count: int = Field(ge=0)
    sources: list[SourceRef] = Field(default_factory=list)


class MovedPayment(Schema):
    """A payment booked in the draft's year that §5.1 counts in another year (or vice versa)."""

    allocation_id: UUID
    booking_date: date
    category: AllocationCategory
    amount: Money
    tax_year: TaxYear
    rule: Literal["paid", "short_period_shift"]


class RuleLines(Schema):
    """What a line rule (§5.4–5.7, 5.10) returns for one tax year."""

    tax_year: TaxYear
    lines: list[LineAmount] = Field(default_factory=list)
    unit_rows: list[UnitRow] = Field(default_factory=list)
    tracked: list[TrackedAmount] = Field(default_factory=list)
    basis_additions: list[BasisAddition] = Field(default_factory=list)
    pre_handover: list[SourceRef] = Field(default_factory=list)
    excluded_open: OpenLines = OpenLines(count=0)
    warnings: list[DraftWarning] = Field(default_factory=list)


class ReducedRentCheck(Schema):
    """§5.8 per Wohnung, monthly figures. market_warm = cold_rent_per_m2 × area + the year's
    umlagefähige costs ÷ months owned. ratio_percent: agreed ÷ market_warm, one decimal; the
    thresholds compare the exact ratio. cut_percent only below 50 %."""

    wohnung: Literal["property", "unit"]
    id: UUID
    agreed_warm: NonNegativeMoney
    area_m2: Area
    market_cold_per_m2: PositiveMoney | None = None
    umlagefaehig_monthly: NonNegativeMoney | None = None
    market_warm: PositiveMoney | None = None
    ratio_percent: Percent | None = None
    outcome: Literal["full", "prognosis_needed", "partial", "no_market_rent"]
    cut_percent: int | None = Field(default=None, ge=1, le=100)
    sources: list[SourceRef] = Field(default_factory=list)


class ReducedRentResult(Schema):
    tax_year: TaxYear
    checks: list[ReducedRentCheck] = Field(default_factory=list)
    warnings: list[DraftWarning] = Field(default_factory=list)


class DraftLine(LineAmount):
    zeile_from: int | None = None
    zeile: int | None = None
    kennzahl: str | None = None
    label: str
    amount_rounded: int | None = None  # whole euros for the field sheet (D60)
    notes: list[str] = Field(default_factory=list)


class RuleSetStamp(Schema):
    tax_year: TaxYear
    version: str
    form_year: TaxYear
    form_status: FormStatus


class DraftHeader(Schema):
    address: Address | None = None
    aktenzeichen: str | None = None
    contract_date: date | None = None  # notarized_on
    transfer_date: date  # purchase_date
    completed: Year
    sold: date | None = None
    living_area_m2: Area | None = None


class DraftTotals(Schema):
    income: Money
    costs: Money
    result: Money
    income_rounded: int
    costs_rounded: int
    result_rounded: int


class OwnerResult(Schema):
    owner_id: UUID
    share: Fraction
    amount: Money
    amount_rounded: int


class Coverage(Schema):
    months_owned: int = Field(ge=0, le=12)  # handover month (or January) to December or the end
    months_covered: int = Field(ge=0, le=12)  # months whose last day ≤ covered_through
    covered_through: date | None = None  # the earliest across the accounts


class AnlageVDraft(Schema):
    property_id: UUID
    tax_year: TaxYear
    rule_set: RuleSetStamp
    header: DraftHeader
    lines: list[DraftLine] = Field(default_factory=list)
    unit_rows: list[UnitRow] = Field(default_factory=list)
    totals: DraftTotals
    owners: list[OwnerResult] = Field(default_factory=list)
    afa: AfaLine | None = None
    movable_assets: list[MovableAfaLine] = Field(default_factory=list)  # D80, wk.afa_movable
    tracked_not_deductible: list[TrackedAmount] = Field(default_factory=list)
    moved_to_other_year: list[MovedPayment] = Field(default_factory=list)
    pre_handover: list[SourceRef] = Field(default_factory=list)
    excluded_open: OpenLines = OpenLines(count=0)
    warnings: list[DraftWarning] = Field(default_factory=list)
    missing_documents: list[ChecklistItem] = Field(default_factory=list)
    limit_15: LimitStatus | None = None
    reduced_rent: ReducedRentResult | None = None
    coverage: Coverage
    tax_free_from: date | None = None  # §23, D68


# --- Exports (§12, D65) ----------------------------------------------------------------


class TaxExportKind(StrEnum):
    FIELD_SHEET = "field_sheet"
    ARCHIVE = "archive"


class TaxExportCreate(Schema):
    tax_year: TaxYear
    kind: TaxExportKind


class TaxExport(Record):
    """A stored export (immutable). The draft it was built from is in the row's `draft`
    column; the API returns it with the file's signed download URL, not inline."""

    property_id: UUID
    tax_year: TaxYear
    kind: TaxExportKind
    rule_set_version: str
    form_year: TaxYear
    form_status: FormStatus
    file_sha256: Sha256Hex
    size_bytes: int = Field(gt=0)
    created_by: UUID | None = None
