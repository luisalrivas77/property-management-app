"""Shapes of the phase 6 YAML rule fixtures (docs/phases/phase-6.md §14).

Same contract as fixture_models_phase5.py: loading a fixture through these models is itself a
test (tests/test_fixtures_valid.py), so a fixture that does not match the spec schemas fails
before any rule exists. Every rule of phase 6 is a stub in step 0 (NotImplementedError), so
each case that a rule will replay carries `pending: <the step that builds it>`; the rule tests
of those steps drop the marker. Expectations are recomputed on load where the arithmetic is
small or a table step 0 wrote is the oracle:

- ownership by `_validate` (Σ shares = 1 exactly, every owner of the workspace) and
  `_supersede` (the account's current rows);
- household_categories against rules/household/taxonomy.TAXONOMY, rules/categories (label,
  treatment) and rules/review.threshold, plus the completeness check (the listed codes are
  exactly the private-only codes);
- fints_slots by `_pin_variable` (the slot pattern of D163);
- merchant_pattern by `_merchant_pattern` (D160's normalisation, written once more).

Every interpretation step 0 had to make is in the fixture file's header, and listed in the
step 0 report as a spec finding.
"""

from __future__ import annotations

import re
from decimal import Decimal
from fractions import Fraction
from functools import cache
from pathlib import Path
from typing import Self
from uuid import UUID

import yaml
from fixture_models import Lenient, RuleErrorExpect
from pydantic import Field, model_validator

from hausbuch.rules import categories, review
from hausbuch.rules import errors as E
from hausbuch.rules.household.taxonomy import PRIVATE_ONLY, SHARED, TAXONOMY, allowed_categories
from hausbuch.schemas.allocation import AllocationCategory, TaxTreatment
from hausbuch.schemas.bank_sync import PRIMARY_SLOT, CredentialSlot
from hausbuch.schemas.common import Schema
from hausbuch.schemas.enums import AccountPurpose
from hausbuch.schemas.household import HouseholdGroup, ProposedInput, Relevance

FIXTURES = Path(__file__).parent / "fixtures" / "phase6"


@cache
def raw(name: str) -> dict:
    with (FIXTURES / f"{name}.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


class _Fixture(Schema):
    ids: dict[str, UUID] = Field(default_factory=dict)
    bases: dict[str, object] = Field(default_factory=dict)  # YAML anchors merged into cases


class _Case(Lenient):
    # The step that builds the rule this case replays; None for a case checked on load only.
    pending: str | None = None


class Refused(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# --- ownership.yaml: rules.household.ownership.validate / supersede (D157) ----------------


class ShareRow(Schema):
    owner: str  # a key of the case's owners
    share_numerator: int = Field(gt=0)
    share_denominator: int = Field(gt=0)
    reason: str | None = Field(default=None, max_length=500)


class CurrentRow(ShareRow):
    id: str
    superseded: bool = False  # superseded_by set by an earlier change


class OwnershipExpect(Schema):
    valid: bool | None = None
    error: RuleErrorExpect | None = None
    superseded: list[str] | None = None  # the current rows the new set replaces

    @model_validator(mode="after")
    def _one(self) -> Self:
        if (self.valid is None) == (self.error is None):
            raise ValueError("expect valid: true or an error")
        return self


def _validate(shares: list[ShareRow], owners: list[str]) -> None:
    if any(s.owner not in owners for s in shares):
        raise Refused(E.OWNERSHIP_OWNER_MISSING)
    if sum(Fraction(s.share_numerator, s.share_denominator) for s in shares) != 1:
        raise Refused(E.OWNERSHIP_SUM_NOT_ONE)


class OwnershipCase(_Case):
    owners: list[str] = Field(min_length=1)
    current: list[CurrentRow] = Field(default_factory=list)
    shares: list[ShareRow] = Field(min_length=1)
    expect: OwnershipExpect

    @model_validator(mode="after")
    def _recompute(self) -> Self:
        try:
            _validate(self.shares, self.owners)
            got: bool | str = True
        except Refused as refused:
            got = refused.code
        want = self.expect.valid if self.expect.error is None else self.expect.error.code
        if got != want:
            raise ValueError(f"{self.id}: validate gives {got}, the case says {want}")
        if self.expect.superseded is not None:
            current = [r.id for r in self.current if not r.superseded]
            if got is not True or current != self.expect.superseded:
                raise ValueError(f"{self.id}: the new set supersedes {current}")
        return self


class OwnershipFixture(_Fixture):
    cases: list[OwnershipCase] = Field(min_length=1)


# --- household_categories.yaml: rules/household/taxonomy (D159) ---------------------------


class CodeRow(Schema):
    category: AllocationCategory
    label: str | None = None  # where D159 names it
    group: HouseholdGroup
    relevance: Relevance
    treatment: TaxTreatment
    threshold: Decimal | None
    proposes: ProposedInput | None = None


class HouseholdCategoriesFixture(_Fixture):
    codes: list[CodeRow] = Field(min_length=1)
    shared: list[AllocationCategory] = Field(min_length=1)

    @model_validator(mode="after")
    def _against_the_tables(self) -> Self:
        listed = [row.category for row in self.codes]
        if len(listed) != len(set(listed)):
            raise ValueError("a code is listed twice")
        # The completeness check: every private-only code, and nothing else.
        if set(listed) != set(PRIVATE_ONLY) or set(listed) != set(TAXONOMY):
            raise ValueError(f"listed codes differ: {set(listed) ^ set(TAXONOMY)}")
        for row in self.codes:
            table = TAXONOMY[row.category]
            got = (
                table.group,
                table.relevance,
                table.proposes,
                categories.tax_treatment(row.category),
                review.threshold(row.category),
            )
            want = (row.group, row.relevance, row.proposes, row.treatment, row.threshold)
            if got != want:
                raise ValueError(f"{row.category}: the tables say {got}, the fixture {want}")
            if row.label is not None and categories.CATEGORY_LABEL[row.category] != row.label:
                raise ValueError(f"{row.category}: label {categories.CATEGORY_LABEL[row.category]}")
            # D159: a tax-relevant code never auto-confirms
            if row.relevance is Relevance.TAX and row.threshold is not None:
                raise ValueError(f"{row.category}: a tax-relevant code has a threshold")
        if set(self.shared) != set(SHARED):
            raise ValueError("the shared codes differ")
        private = allowed_categories(AccountPurpose.PRIVATE)
        rental = allowed_categories(AccountPurpose.RENTAL)
        if private != set(listed) | set(self.shared) or rental & set(listed):
            raise ValueError("allowed_categories disagrees with the fixture")
        return self


# --- fints_slots.yaml: rules.bank_sync.pin_variable, services.bank_sync (D163, D173) -------

_SLOT = re.compile(r"^[a-z][a-z0-9_]{0,31}$")  # the CredentialSlot pattern and the CHECK


def _pin_variable(slot: str) -> str | None:
    """None: the slot name is refused."""
    if not _SLOT.match(slot):
        return None
    return "HAUSBUCH_FINTS_PIN" if slot == PRIMARY_SLOT else f"HAUSBUCH_FINTS_PIN_{slot.upper()}"


class SlotCase(_Case):
    slot: str
    variable: str | None  # None: refused

    @model_validator(mode="after")
    def _recompute(self) -> Self:
        if _pin_variable(self.slot) != self.variable:
            raise ValueError(f"{self.id}: pin_variable gives {_pin_variable(self.slot)}")
        return self


class SyncSlotCase(_Case):
    """The sync's check before any dialog (services.bank_sync, step 3)."""

    slot: CredentialSlot
    secrets: list[CredentialSlot]  # the slots whose variable is set, names only
    expect_error: RuleErrorExpect | None = None
    names_variable: str | None = None  # what the card says to set

    @model_validator(mode="after")
    def _recompute(self) -> Self:
        missing = self.slot not in self.secrets
        if missing != (self.expect_error is not None):
            raise ValueError(f"{self.id}: a slot without its secret fails, only then")
        if missing and (
            self.expect_error.code != E.BANK_PIN_MISSING
            or self.names_variable != _pin_variable(self.slot)
        ):
            raise ValueError(f"{self.id}: bank.pin_missing naming {_pin_variable(self.slot)}")
        return self


class ApprovalTextCase(_Case):
    bank_name: str  # the BPD's bank name
    text: str | None  # None: the phase 5 card text, unchanged


class AutoSyncCase(_Case):
    bank_name: str
    # The connection's manual syncs, oldest first: did each need an approval?
    manual_syncs: list[bool]
    allowed: bool

    @model_validator(mode="after")
    def _recompute(self) -> Self:
        dkb = self.bank_name.upper().startswith("DKB")
        last_two = self.manual_syncs[-2:]
        allowed = (not dkb) or (len(last_two) == 2 and not any(last_two))
        if allowed != self.allowed:
            raise ValueError(f"{self.id}: auto_sync allowed is {allowed}")
        return self


class FintsSlotsFixture(_Fixture):
    slots: list[SlotCase] = Field(min_length=1)
    sync_checks: list[SyncSlotCase] = Field(min_length=1)
    approval_texts: list[ApprovalTextCase] = Field(min_length=1)
    auto_sync: list[AutoSyncCase] = Field(min_length=1)


# --- merchant_pattern.yaml: rules.matching.merchant_pattern (D160) ------------------------

_LOCATION = re.compile(r"//[^/]*/[A-Za-z]{2}\s*$")  # the trailing //<Ort>/<Land>
_DROPPED = re.compile(r"[0-9/.]")


def _merchant_pattern(name: str) -> str:
    text = _LOCATION.sub("", name).upper()
    text = " ".join(_DROPPED.sub(" ", text).split())
    return "^" + re.escape(text).replace("\\ ", " ")


class MerchantCase(_Case):
    name: str
    pattern: str
    note: str | None = None

    @model_validator(mode="after")
    def _recompute(self) -> Self:
        if _merchant_pattern(self.name) != self.pattern:
            raise ValueError(f"{self.id}: the normalisation gives {_merchant_pattern(self.name)}")
        return self


class MerchantPatternFixture(_Fixture):
    cases: list[MerchantCase] = Field(min_length=10)


MODELS: dict[str, type[Schema]] = {
    "ownership": OwnershipFixture,
    "household_categories": HouseholdCategoriesFixture,
    "fints_slots": FintsSlotsFixture,
    "merchant_pattern": MerchantPatternFixture,
}


@cache
def load(name: str) -> Schema:
    return MODELS[name].model_validate(raw(name))
