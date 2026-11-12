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
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
from functools import cache
from pathlib import Path
from typing import Literal, Self
from uuid import UUID

import fixture_models_phase2 as phase2
import fixture_models_phase3 as phase3
import yaml
from fixture_models import Lenient, RuleErrorExpect
from pydantic import Field, model_validator

from hausbuch.rules import categories, review
from hausbuch.rules import errors as E
from hausbuch.rules.household.taxonomy import PRIVATE_ONLY, SHARED, TAXONOMY, allowed_categories
from hausbuch.schemas.allocation import (
    HMAC_PATTERN,
    AllocationCategory,
    AllocationDraft,
    MatchContext,
    MatchingRule,
    MatchTransaction,
    TaxTreatment,
    TransferLegSource,
)
from hausbuch.schemas.bank_sync import PRIMARY_SLOT, CredentialSlot
from hausbuch.schemas.common import Money, Schema
from hausbuch.schemas.enums import AccountKind, AccountPurpose
from hausbuch.schemas.household import (
    HouseholdGroup,
    ProposedInput,
    Relevance,
    TransferLinkKind,
)

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


# --- matching_order.yaml: the matching order per account purpose (D158) -------------------

Stage = Literal[
    "rule", "owner_transfer", "rent", "loan", "hausgeld", "transfer", "document", "model"
]
# D158: the stages each purpose runs, in order. "model" is the household model on a private
# account (D168) and phase 2's categorisation on the rental one.
STAGES: dict[AccountPurpose, tuple[Stage, ...]] = {
    AccountPurpose.RENTAL: (
        "rule",
        "owner_transfer",
        "rent",
        "loan",
        "hausgeld",
        "document",
        "model",
    ),
    AccountPurpose.PRIVATE: ("rule", "transfer", "document", "model"),
}
TENANT_QUESTION = "Diese Zahlung kommt von einem Mieter: gehört sie zum Mietobjekt?"
LENDER_QUESTION = "Diese Zahlung geht an die Bank des Darlehens: gehört sie zum Mietobjekt?"


class OrderExpect(Schema):
    outcome: Literal["booked", "review"]
    by: Stage | None = None  # the stage that booked it
    allocations: list[AllocationDraft] | None = None
    question: str | None = None
    error: RuleErrorExpect | None = None  # the card's code
    offered: list[AllocationCategory] | None = None  # the answers the card offers


class MatchingOrderCase(_Case):
    purpose: AccountPurpose
    ai_private_accounts: bool = False  # the switch of D168
    rules: list[str] = Field(default_factory=list)  # names of the fixture's rules
    transaction: MatchTransaction | None = None
    # A rental-account case replayed from phase 2 matchers.yaml by its id: the same
    # transaction and the same expectation, the order unchanged.
    replay: str | None = None
    expect: OrderExpect | None = None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if (self.replay is None) == (self.transaction is None):
            raise ValueError(f"{self.id}: a transaction or a replay")
        if self.replay is not None and self.purpose is not AccountPurpose.RENTAL:
            raise ValueError(f"{self.id}: only the rental account replays phase 2")
        if self.replay is None and self.expect is None:
            raise ValueError(f"{self.id}: expect")
        e = self.expect
        if e is not None:
            if e.by is not None and e.by not in STAGES[self.purpose]:
                raise ValueError(f"{self.id}: {e.by} does not run on a {self.purpose} account")
            if (e.outcome == "booked") != (e.by is not None):
                raise ValueError(f"{self.id}: a booked line names its stage, only then")
            if e.allocations is not None and self.transaction is not None:
                total = sum((a.amount for a in e.allocations), Decimal("0"))
                if total != self.transaction.amount:
                    raise ValueError(f"{self.id}: the allocations sum to the amount")
            allowed = allowed_categories(self.purpose)
            for category in [a.category for a in e.allocations or []] + (e.offered or []):
                if category not in allowed:
                    raise ValueError(f"{self.id}: {category} not allowed on {self.purpose}")
        return self


class MatchingOrderFixture(_Fixture):
    context: MatchContext
    rules: dict[str, MatchingRule] = Field(default_factory=dict)
    cases: list[MatchingOrderCase] = Field(min_length=1)

    @model_validator(mode="after")
    def _references(self) -> Self:
        replayed = {c.id: c for c in phase2.load("matchers").cases}
        for case in self.cases:
            for name in case.rules:
                if name not in self.rules:
                    raise ValueError(f"{case.id}: unknown rule {name}")
            if case.replay is not None and (
                case.replay not in replayed or replayed[case.replay].expect is None
            ):
                raise ValueError(f"{case.id}: {case.replay} is no booked phase 2 case")
        return self


# --- transfers.yaml: rules.matching.match_transfer, services.transfers.relink (D162) -------

WINDOW_BANKING_DAYS = 3


def _add_banking_days(day: date, n: int) -> date:
    """Mon-Fri; no holidays (step 0 reading: the fixture's dates avoid them)."""
    while n:
        day += timedelta(days=1)
        if day.weekday() < 5:
            n -= 1
    return day


class TransferAccount(Schema):
    id: UUID
    kind: AccountKind
    purpose: AccountPurpose
    iban_hmac: str | None = Field(default=None, pattern=HMAC_PATTERN)


class LegRow(Schema):
    """A leg as the fixture states it; `account` is a key of the fixture's accounts."""

    id: UUID
    account: str
    source: TransferLegSource = TransferLegSource.TRANSACTION
    booked_on: date
    amount: Money
    fee_eur: Money | None = None
    counterparty_iban_hmac: str | None = Field(default=None, pattern=HMAC_PATTERN)
    # A depot or exchange leg's category from its booking text or type (D165 (1), D166),
    # not from an IBAN.
    booked_as: AllocationCategory | None = None
    note: str | None = None


class LinkExpect(Schema):
    out_leg: UUID
    in_leg: UUID
    kind: TransferLinkKind


class TransferExpect(Schema):
    category: AllocationCategory | None  # None: not a transfer by IBAN or type
    confidence: Decimal | None = None
    link: LinkExpect | None = None  # written by the matcher (matched_by = matcher)
    proposed_link: LinkExpect | None = None  # proposed for review, never auto
    card: RuleErrorExpect | None = None
    candidates: list[UUID] = Field(default_factory=list)  # transfer.ambiguous lists them


_TRANSFER_CODES = {
    (AccountPurpose.PRIVATE, False): AllocationCategory.INTERNAL_TRANSFER_OUT,
    (AccountPurpose.PRIVATE, True): AllocationCategory.INTERNAL_TRANSFER_IN,
    (AccountPurpose.RENTAL, False): AllocationCategory.OWNER_TRANSFER_OUT,
    (AccountPurpose.RENTAL, True): AllocationCategory.OWNER_TRANSFER_IN,
}
_TRANSFER_CATEGORIES = set(_TRANSFER_CODES.values())


def _link_kind(a: TransferAccount, b: TransferAccount) -> TransferLinkKind:
    kinds = {a.kind, b.kind}
    if AccountKind.EXCHANGE in kinds:
        return TransferLinkKind.EXCHANGE_EUR
    if AccountKind.DEPOT in kinds:
        return TransferLinkKind.DEPOT_CASH
    return TransferLinkKind.BANK


def _fits(out: LegRow, in_: LegRow, accounts: dict[str, TransferAccount], *, exact: bool) -> bool:
    if not out.booked_on <= in_.booked_on <= _add_banking_days(out.booked_on, WINDOW_BANKING_DAYS):
        return False
    gap = -out.amount - in_.amount  # what the exchange kept
    exchange = AccountKind.EXCHANGE in {accounts[out.account].kind, accounts[in_.account].kind}
    if exact or not exchange:
        return gap == 0
    fee = (out.fee_eur or Decimal("0")) + (in_.fee_eur or Decimal("0"))
    return Decimal("0") <= gap <= fee


class TransferCase(_Case):
    leg: LegRow
    open_legs: list[LegRow] = Field(default_factory=list)
    today: date
    relink: bool = False  # run by services.transfers.relink after an import
    expect: TransferExpect


class TransfersFixture(_Fixture):
    accounts: dict[str, TransferAccount]
    cases: list[TransferCase] = Field(min_length=10)

    @model_validator(mode="after")
    def _recompute(self) -> Self:
        for case in self.cases:
            got = self._expect(case)
            if got != case.expect:
                raise ValueError(f"{case.id}: the transfer rule gives {got}")
        return self

    def _expect(self, case: TransferCase) -> TransferExpect:
        accounts = self.accounts
        leg = case.leg
        own = accounts[leg.account]
        by_iban = {
            a.iban_hmac: key
            for key, a in accounts.items()
            if a.iban_hmac is not None and key != leg.account
        }
        credit = leg.amount > 0
        other = [o for o in case.open_legs if o.account != leg.account and (o.amount > 0) != credit]
        if leg.counterparty_iban_hmac in by_iban:
            category = _TRANSFER_CODES[(own.purpose, credit)]
            other = [o for o in other if o.account == by_iban[leg.counterparty_iban_hmac]]
        elif leg.booked_as in _TRANSFER_CATEGORIES:
            category = leg.booked_as
        else:
            # D162: no own IBAN; an exchange or depot EUR movement that fits exactly is
            # proposed for review, never linked.
            fitting = [
                o
                for o in other
                if accounts[o.account].kind in (AccountKind.EXCHANGE, AccountKind.DEPOT)
                and _fits(*self._ordered(leg, o), accounts, exact=True)
            ]
            proposed = self._link(leg, fitting[0]) if len(fitting) == 1 else None
            return TransferExpect(category=None, proposed_link=proposed)
        fitting = [o for o in other if _fits(*self._ordered(leg, o), accounts, exact=False)]
        result = TransferExpect(category=category, confidence=Decimal("1.000"))
        if len(fitting) == 1:
            return result.model_copy(update={"link": self._link(leg, fitting[0])})
        if len(fitting) > 1:
            return result.model_copy(
                update={
                    "card": RuleErrorExpect(code=E.TRANSFER_AMBIGUOUS),
                    "candidates": [o.id for o in fitting],
                }
            )
        if case.today > _add_banking_days(leg.booked_on, WINDOW_BANKING_DAYS):
            return result.model_copy(
                update={"card": RuleErrorExpect(code=E.TRANSFER_UNMATCHED_LEG)}
            )
        return result

    @staticmethod
    def _ordered(a: LegRow, b: LegRow) -> tuple[LegRow, LegRow]:
        return (a, b) if a.amount < 0 else (b, a)

    def _link(self, a: LegRow, b: LegRow) -> LinkExpect:
        out, in_ = self._ordered(a, b)
        kind = _link_kind(self.accounts[out.account], self.accounts[in_.account])
        return LinkExpect(out_leg=out.id, in_leg=in_.id, kind=kind)


# --- private_cost_link.yaml: D161's three paths and D159's per-purpose category set --------


class PrivateCostRow(Schema):
    """A phase 3 private_cost tax input of the property (only the fields the cases read)."""

    id: UUID
    document_id: UUID | None = None
    category: AllocationCategory
    amount: Money


class Booking(Schema):
    """What the user, a matcher or the model books on the line."""

    by: Literal["document_match", "user", "model", "relink"]
    category: AllocationCategory
    property_id: UUID | None = None
    document_id: UUID | None = None


class ProposedRow(Schema):
    """D161 (2): the private_cost row proposed from the bill's parts and the line."""

    document_id: UUID
    category: AllocationCategory
    amount: Money
    tax_year: int


class DraftCount(Schema):
    """What the Anlage V counts for the bill: one source, once. line: the line's allocation
    (by the transaction's id); tax_input: the private_cost row."""

    source: Literal["line", "tax_input"]
    id: UUID


class LinkExpectPC(Schema):
    allocation: Booking | None = None  # what the line is booked as (None: refused)
    status: Literal["confirmed", "proposed"] | None = None
    error: RuleErrorExpect | None = None
    counted: list[DraftCount] | None = None  # None: not asserted here (see draft_case)
    proof_of: UUID | None = None  # the private_cost row the line proves
    proposes: ProposedRow | None = None
    todos: list[str] = Field(default_factory=list)  # warning codes or the to-do's label


class PrivateCostLinkCase(_Case):
    purpose: AccountPurpose
    line: MatchTransaction | None = None
    private_cost_rows: list[PrivateCostRow] = Field(default_factory=list)
    booking: Booking | None = None
    # The Anlage V draft of this path, a case of phase 3 anlage_v_2026.yaml by its id.
    draft_case: str | None = None
    expect: LinkExpectPC

    @model_validator(mode="after")
    def _recompute(self) -> Self:
        if self.booking is not None:
            allowed = self.booking.category in allowed_categories(self.purpose)
            refused = self.expect.error is not None
            if allowed == refused:
                raise ValueError(f"{self.id}: allowed_categories({self.purpose}) says {allowed}")
            if refused and self.expect.error.code != E.ALLOCATION_CATEGORY_NOT_FOR_ACCOUNT:
                raise ValueError(f"{self.id}: refused with allocation.category_not_for_account")
            if refused == (self.expect.allocation is not None):
                raise ValueError(f"{self.id}: a refused booking books nothing, only then")
        counted = self.expect.counted or []
        if len({c.id for c in counted}) != len(counted):
            raise ValueError(f"{self.id}: a bill is counted once")
        return self


class PrivateCostLinkFixture(_Fixture):
    context: MatchContext
    cases: list[PrivateCostLinkCase] = Field(min_length=1)

    @model_validator(mode="after")
    def _draft_cases(self) -> Self:
        drafts = {c.id for c in phase3.load("anlage_v_2026").cases}
        for case in self.cases:
            if case.draft_case is not None and case.draft_case not in drafts:
                raise ValueError(f"{case.id}: no draft case {case.draft_case}")
        return self


MODELS: dict[str, type[Schema]] = {
    "ownership": OwnershipFixture,
    "household_categories": HouseholdCategoriesFixture,
    "fints_slots": FintsSlotsFixture,
    "merchant_pattern": MerchantPatternFixture,
    "matching_order": MatchingOrderFixture,
    "transfers": TransfersFixture,
    "private_cost_link": PrivateCostLinkFixture,
}


@cache
def load(name: str) -> Schema:
    return MODELS[name].model_validate(raw(name))
