"""Shapes of the phase 2 YAML rule fixtures (docs/phases/phase-2.md §12, §13).

Same contract as fixture_models.py: loading a fixture through these models is itself a test
(tests/test_fixtures_valid.py), so a fixture that does not match the spec schemas fails before
any rule is implemented.
"""

from datetime import date
from functools import cache
from pathlib import Path
from typing import Literal, Self

import yaml
from fixture_models import Lenient
from pydantic import Field, model_validator

from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    ServicePeriod,
    TaxTreatment,
)
from hausbuch.schemas.common import Confidence, Money, Schema

FIXTURES = Path(__file__).parent / "fixtures" / "phase2"


@cache
def raw(name: str) -> dict:
    with (FIXTURES / f"{name}.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _every_category_once(categories: list[AllocationCategory], what: str) -> None:
    missing = set(AllocationCategory) - set(categories)
    if missing or len(categories) != len(set(categories)):
        raise ValueError(f"{what}: every category exactly once (missing {sorted(missing)})")


# --- categories.yaml -------------------------------------------------------------------


class CategoryRow(Schema):
    category: AllocationCategory
    treatment: TaxTreatment


class CategoriesFixture(Schema):
    categories: list[CategoryRow]

    @model_validator(mode="after")
    def _complete(self) -> Self:
        _every_category_once([r.category for r in self.categories], "categories.yaml")
        return self


# --- thresholds.yaml -------------------------------------------------------------------


class ThresholdRow(Schema):
    category: AllocationCategory
    threshold: Confidence | None


class ThresholdsFixture(Schema):
    thresholds: list[ThresholdRow]

    @model_validator(mode="after")
    def _complete(self) -> Self:
        _every_category_once([r.category for r in self.thresholds], "thresholds.yaml")
        return self

    def table(self) -> dict[AllocationCategory, Confidence | None]:
        return {r.category: r.threshold for r in self.thresholds}


# --- allocation_sum.yaml ---------------------------------------------------------------


class SumExpect(Schema):
    error: Literal["allocation.sum_mismatch", "allocation.empty"] | None


class SumCase(Lenient):
    transaction_amount: Money
    allocations: list[AllocationDraft]
    expect: SumExpect


class SumFixture(Schema):
    cases: list[SumCase]


# --- service_period.yaml ---------------------------------------------------------------


class ServicePeriodCase(Lenient):
    purpose: str
    booking_date: date
    recurring: bool
    expect: ServicePeriod | None


class ServicePeriodFixture(Schema):
    cases: list[ServicePeriodCase] = Field(min_length=1)


MODELS: dict[str, type[Schema]] = {
    "categories": CategoriesFixture,
    "thresholds": ThresholdsFixture,
    "allocation_sum": SumFixture,
    "service_period": ServicePeriodFixture,
}


@cache
def load(name: str) -> Schema:
    return MODELS[name].model_validate(raw(name))
