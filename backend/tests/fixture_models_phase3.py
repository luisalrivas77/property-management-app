"""Shapes of the phase 3 YAML rule fixtures (docs/phases/phase-3.md §13, §14).

Same contract as fixture_models.py and fixture_models_phase2.py: loading a fixture through
these models is itself a test (tests/test_fixtures_valid.py), so a fixture that does not match
the spec schemas fails before any rule is implemented. Inputs are the §7 schemas themselves
(PaymentFacts, AfaFacts, LimitFacts, DraftFacts, ChecklistFacts); expectations are the result
schemas or a projection of them, with these comparison rules for the rule tests (step 1):

- `lines`: exactly the lines the rule writes with a non-zero amount, a ticked box or the
  provisional flag, in any order; a line not listed is absent or 0.00. `unit_rows` the same:
  exactly the rows with a non-zero amount; `rooms` asserted only where listed.
- `sources` (on a line or a unit row): the exact set of (kind, id); amounts are not compared.
  Omitted (None) = not asserted by this case.
- `warnings`: the exact multiset of codes; each warning's listed params must match, params
  not listed are free.
- Lists of ids (`pre_handover`, `counted`) compare as sets.

The draft's expectations also check their own arithmetic on load (totals are the sums of
their lines, the owners' parts add up to the result), so a hand-calculation slip fails here.
"""

from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any, Literal, Self
from uuid import UUID

import yaml
from fixture_models import Lenient
from pydantic import Field, model_validator

from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.checklist import ChecklistFacts, ChecklistItem
from hausbuch.schemas.common import Area, Money, NonNegativeMoney, Percent, Schema, TaxYear
from hausbuch.schemas.tax import (
    AfaFacts,
    AfaLine,
    BasisAddition,
    Coverage,
    DraftFacts,
    DraftHeader,
    DraftTotals,
    LimitFacts,
    LimitStatus,
    MovedPayment,
    OpenLines,
    PaymentFacts,
    ReducedRentCheck,
    ReducedRentResult,
    RoomRent,
    RuleSetStamp,
    SourceRef,
    TaxYearResult,
    UnitRow,
    WarningCode,
)

FIXTURES = Path(__file__).parent / "fixtures" / "phase3"


@cache
def raw(name: str) -> dict:
    with (FIXTURES / f"{name}.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


# --- Shared expectation pieces ---------------------------------------------------------


class WarningExpect(Schema):
    code: WarningCode
    params: dict[str, str] = Field(default_factory=dict)  # these must match; others free


class LineExpect(Schema):
    key: str
    amount: Money | None = None
    ticked: bool | None = None
    provisional: bool = False
    einzelangaben: list[str] | None = None
    amount_rounded: int | None = None  # draft only
    sources: list[SourceRef] | None = None

    @model_validator(mode="after")
    def _amount_or_box(self) -> Self:
        if (self.amount is None) == (self.ticked is None):
            raise ValueError(f"{self.key}: an amount line or a ticked box, not both")
        return self


class UnitRowExpect(UnitRow):
    rooms: list[RoomRent] | None = None  # type: ignore[assignment]
    sources: list[SourceRef] | None = None  # type: ignore[assignment]


class TrackedExpect(Schema):
    category: AllocationCategory
    amount: NonNegativeMoney
    count: int = Field(ge=0)


def _unique_years(expect: list[Any], what: str) -> None:
    years = [e.tax_year for e in expect]
    if len(years) != len(set(years)):
        raise ValueError(f"{what}: one expectation per tax year")


def _unique_keys(lines: list[LineExpect], what: str) -> None:
    keys = [line.key for line in lines]
    if len(keys) != len(set(keys)):
        raise ValueError(f"{what}: each line key once")


class _Fixture(Schema):
    ids: dict[str, UUID] = Field(default_factory=dict)
    bases: dict[str, Any] = Field(default_factory=dict)  # YAML anchors merged into cases


# --- tax_year.yaml (§5.1) --------------------------------------------------------------


class TaxYearCase(Lenient):
    payment: PaymentFacts
    expect: TaxYearResult


class TaxYearFixture(_Fixture):
    rule_set_year: TaxYear
    cases: list[TaxYearCase]


# --- afa.yaml (§5.2) -------------------------------------------------------------------


class AfaExpect(AfaLine):
    sources: list[SourceRef] | None = None  # type: ignore[assignment]
    warnings: list[WarningExpect] = Field(default_factory=list)  # type: ignore[assignment]


class AfaCase(Lenient):
    facts: AfaFacts
    expect: list[AfaExpect]  # one per tax year

    @model_validator(mode="after")
    def _years(self) -> Self:
        _unique_years(self.expect, self.id)
        return self


class AfaFixture(_Fixture):
    rule_set_year: TaxYear
    cases: list[AfaCase]


# --- movable_asset.yaml (D80) ---------------------------------------------------------


class MovableExpect(Schema):
    """One tax year of the case's one movable asset (MovableAfaLine's figures)."""

    tax_year: TaxYear
    amount: NonNegativeMoney
    basis: NonNegativeMoney
    months: int = Field(ge=0, le=12)
    method_code: Literal[1, 2] | None
    book_value_start: NonNegativeMoney
    remaining_months: int = Field(ge=0)
    sources: list[SourceRef] | None = None
    warnings: list[WarningExpect] = Field(default_factory=list)


class MovableCase(Lenient):
    facts: AfaFacts
    expect: list[MovableExpect]

    @model_validator(mode="after")
    def _one_asset(self) -> Self:
        if len(self.facts.movable_assets) != 1:
            raise ValueError(f"{self.id}: one movable asset per case")
        _unique_years(self.expect, self.id)
        return self


class MovableFixture(_Fixture):
    cases: list[MovableCase]


# --- anschaffungsnah.yaml (§5.3) -------------------------------------------------------


class LimitExpect(LimitStatus):
    warnings: list[WarningExpect] = Field(default_factory=list)  # type: ignore[assignment]

    @model_validator(mode="after")
    def _arithmetic(self) -> Self:
        if sum((c.net for c in self.counted), Decimal("0")) != self.used_net:
            raise ValueError(f"used_net {self.used_net} is not the sum of the counted nets")
        if self.limit is not None and self.ratio_percent is not None:
            ratio = (self.used_net / self.limit * 100).quantize(Decimal("0.1"), "ROUND_HALF_UP")
            if ratio != self.ratio_percent:
                raise ValueError(f"ratio_percent {self.ratio_percent} != {ratio}")
            if self.exceeded != (self.used_net > self.limit):
                raise ValueError("exceeded is used_net > limit, strictly")
        return self


class LimitCase(Lenient):
    facts: LimitFacts
    expect: list[LimitExpect]

    @model_validator(mode="after")
    def _years(self) -> Self:
        _unique_years(self.expect, self.id)
        return self


class LimitFixture(_Fixture):
    rule_set_year: TaxYear
    cases: list[LimitCase]


# --- Line rules: erhaltungsaufwand, schuldzinsen, hausgeld, umlagen, vorab -------------
# <module>.lines(facts: DraftFacts, tax_year, rule_set) -> RuleLines (vorab: split(...)).


class RuleLinesExpect(Schema):
    tax_year: TaxYear
    lines: list[LineExpect] = Field(default_factory=list)
    unit_rows: list[UnitRowExpect] = Field(default_factory=list)
    tracked: list[TrackedExpect] = Field(default_factory=list)
    basis_additions: list[BasisAddition] = Field(default_factory=list)
    pre_handover: list[UUID] = Field(default_factory=list)  # allocation ids, as a set
    excluded_open: OpenLines = OpenLines(count=0)
    warnings: list[WarningExpect] = Field(default_factory=list)

    @model_validator(mode="after")
    def _keys(self) -> Self:
        _unique_keys(self.lines, f"tax year {self.tax_year}")
        return self


class LineRuleCase(Lenient):
    facts: DraftFacts
    expect: list[RuleLinesExpect]  # one per tax year

    @model_validator(mode="after")
    def _years(self) -> Self:
        _unique_years(self.expect, self.id)
        return self


class LineRuleFixture(_Fixture):
    rule_set_year: TaxYear
    cases: list[LineRuleCase]


# --- verbilligte_vermietung.yaml (§5.8) ------------------------------------------------


class ReducedRentCheckExpect(ReducedRentCheck):
    sources: list[SourceRef] | None = None  # type: ignore[assignment]

    @model_validator(mode="after")
    def _arithmetic(self) -> Self:
        if self.market_warm is not None:
            assert self.market_cold_per_m2 is not None and self.umlagefaehig_monthly is not None
            cold = (self.market_cold_per_m2 * self.area_m2).quantize(Decimal("0.01"))
            if cold + self.umlagefaehig_monthly != self.market_warm:
                raise ValueError("market_warm = cold rent per m² × area + umlagefähige costs")
            ratio = (self.agreed_warm / self.market_warm * 100).quantize(
                Decimal("0.1"), "ROUND_HALF_UP"
            )
            if ratio != self.ratio_percent:
                raise ValueError(f"ratio_percent {self.ratio_percent} != {ratio}")
        return self


class ReducedRentExpect(ReducedRentResult):
    checks: list[ReducedRentCheckExpect] = Field(default_factory=list)  # type: ignore[assignment]
    warnings: list[WarningExpect] = Field(default_factory=list)  # type: ignore[assignment]


class ReducedRentCase(Lenient):
    facts: DraftFacts
    expect: ReducedRentExpect


class ReducedRentFixture(_Fixture):
    rule_set_year: TaxYear
    cases: list[ReducedRentCase]


# --- spekulation.yaml (§5.9) -----------------------------------------------------------


class SpekulationCase(Lenient):
    notarized_on: date
    expect: date


class SpekulationFixture(_Fixture):
    rule_set_year: TaxYear
    cases: list[SpekulationCase]


# --- checklist_phase3.yaml (§8) --------------------------------------------------------


class ChecklistCase(Lenient):
    tax_year: TaxYear
    today: date
    facts: ChecklistFacts
    expect: list[ChecklistItem]


class ChecklistFixture(_Fixture):
    cases: list[ChecklistCase]


# --- anlage_v_2026.yaml (§14) ----------------------------------------------------------


class OwnerExpect(Schema):
    owner_id: UUID
    amount: Money
    amount_rounded: int


class LimitSummary(Schema):
    basis_building_cost: NonNegativeMoney
    limit: NonNegativeMoney
    window_start: date
    window_end: date
    counted: list[UUID]  # allocation ids, as a set
    used_net: NonNegativeMoney
    ratio_percent: Percent
    exceeded: bool


class AfaSummary(Schema):
    amount: NonNegativeMoney | None
    basis: NonNegativeMoney | None
    months: int
    method_code: int | None


class ReducedRentSummary(Schema):
    id: UUID
    agreed_warm: NonNegativeMoney
    area_m2: Area
    outcome: str


class FieldSheetRow(Schema):
    key: str
    zeile: int
    kennzahl: str | None
    amount: Money
    amount_rounded: int


# The keys whose amounts make up income.total and wk.total (§4.2: Z32 and Z83).
INCOME_KEYS = ("income.rent_total", "income.umlagen", "income.umlagen_settlement", "income.other")
COST_KEYS = (
    "wk.afa",
    "wk.afa_movable",  # D80
    "wk.interest",
    "wk.financing_costs",
    "wk.maintenance",
    "wk.maintenance_spread_current",
    "wk.maintenance_spread_prior_1",
    "wk.maintenance_spread_prior_2",
    "wk.maintenance_spread_prior_3",
    "wk.maintenance_spread_prior_4",
    "wk.allocable_costs",
    "wk.non_allocable_costs",
    "wk.other",
)
NO_SOURCES = {"income.total", "wk.total", "result.surplus", "result.owner_a", "result.owner_b"}


class DraftExpect(Schema):
    """A projection of AnlageVDraft. Fields left out (None) are not asserted by the case."""

    rule_set: RuleSetStamp | None = None
    header: DraftHeader | None = None
    lines: list[LineExpect]
    unit_rows: list[UnitRowExpect] | None = None
    totals: DraftTotals
    owners: list[OwnerExpect]
    afa: AfaSummary | None = None
    tracked_not_deductible: list[TrackedExpect] | None = None
    moved_to_other_year: list[MovedPayment] | None = None
    pre_handover: list[UUID] | None = None
    excluded_open: OpenLines | None = None
    warnings: list[WarningExpect] | None = None
    missing_documents: list[ChecklistItem] | None = None
    limit_15: LimitSummary | None = None
    reduced_rent: list[ReducedRentSummary] | None = None
    coverage: Coverage | None = None
    tax_free_from: date | None = None
    field_sheet: list[FieldSheetRow] | None = None  # export_roundtrip
    roundtrip: bool | None = None  # export_roundtrip: the draft serialised and loaded again

    @model_validator(mode="after")
    def _arithmetic(self) -> Self:
        _unique_keys(self.lines, "draft")
        by_key = {line.key: line for line in self.lines}

        def cents(keys: tuple[str, ...]) -> Decimal:
            return sum((by_key[k].amount for k in keys if k in by_key), Decimal("0"))

        def euros(keys: tuple[str, ...]) -> int:
            return sum(by_key[k].amount_rounded or 0 for k in keys if k in by_key)

        t = self.totals
        checks = [
            ("income.total", t.income, cents(INCOME_KEYS), t.income_rounded, euros(INCOME_KEYS)),
            ("wk.total", t.costs, cents(COST_KEYS), t.costs_rounded, euros(COST_KEYS)),
        ]
        for key, total, summed, total_eur, summed_eur in checks:
            if total != summed or total_eur != summed_eur:
                raise ValueError(f"{key}: {total}/{total_eur} != Σ lines {summed}/{summed_eur}")
            if key in by_key and (by_key[key].amount, by_key[key].amount_rounded) != (
                total,
                total_eur,
            ):
                raise ValueError(f"{key} line differs from totals")
        if t.result != t.income - t.costs or t.result_rounded != t.income_rounded - t.costs_rounded:
            raise ValueError("result = income − costs (cents and rounded)")
        if sum((o.amount for o in self.owners), Decimal("0")) != t.result:
            raise ValueError("the owners' parts add up to the result")
        if sum(o.amount_rounded for o in self.owners) != t.result_rounded:
            raise ValueError("the owners' rounded parts add up to the rounded result")
        for line in self.lines:
            if line.sources is not None and line.key not in NO_SOURCES and not line.sources:
                raise ValueError(f"{line.key}: every line opens its sources")
        if self.unit_rows is not None and "income.rent_total" in by_key:
            rows = sum((r.amount for r in self.unit_rows), Decimal("0"))
            if rows != by_key["income.rent_total"].amount:
                raise ValueError("income.rent_total is the sum of the unit rows")
        return self


class DraftCase(Lenient):
    tax_year: TaxYear
    rule_set_year: TaxYear
    facts: DraftFacts
    expect: DraftExpect


class DraftFixture(_Fixture):
    cases: list[DraftCase]


MODELS: dict[str, type[Schema]] = {
    "tax_year": TaxYearFixture,
    "afa": AfaFixture,
    "movable_asset": MovableFixture,
    "anschaffungsnah": LimitFixture,
    "erhaltungsaufwand": LineRuleFixture,
    "schuldzinsen": LineRuleFixture,
    "hausgeld": LineRuleFixture,
    "umlagen": LineRuleFixture,
    "verbilligte_vermietung": ReducedRentFixture,
    "vorab": LineRuleFixture,
    "spekulation": SpekulationFixture,
    "checklist_phase3": ChecklistFixture,
    "anlage_v_2026": DraftFixture,
}


@cache
def load(name: str) -> Schema:
    return MODELS[name].model_validate(raw(name))
