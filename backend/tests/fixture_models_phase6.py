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
from decimal import ROUND_HALF_UP, Decimal
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

from hausbuch.rules import categories, depot, review, statements, valuation
from hausbuch.rules import errors as E
from hausbuch.rules.household.taxonomy import PRIVATE_ONLY, SHARED, TAXONOMY, allowed_categories
from hausbuch.rules.iban import mask_iban, normalize_iban
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
from hausbuch.schemas.bank import (
    ImportStatus,
    LineForHash,
    ReconcileStatus,
    StatementFormat,
)
from hausbuch.schemas.bank_sync import PRIMARY_SLOT, CredentialSlot
from hausbuch.schemas.common import Money, Schema
from hausbuch.schemas.enums import (
    BANK_ACCOUNT_KINDS,
    AccountKind,
    AccountPurpose,
    BalanceSource,
)
from hausbuch.schemas.household import (
    AssetSymbol,
    CurrencyCode,
    HouseholdGroup,
    Isin,
    MovementKind,
    MovementLine,
    MovementStatus,
    NavPoint,
    NetWorthClass,
    NonNegativeQuantity,
    PriceEur,
    PriceSourceKind,
    ProposedInput,
    Quantity,
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


# --- depot_lines.yaml: rules.depot.categorise (D165 (1)) ------------------------------------


def _depot_category(booking_text: str) -> AllocationCategory:
    for pattern, category in depot.BOOKING_PATTERNS:
        if pattern.search(booking_text):
            return category
    return AllocationCategory.UNCATEGORIZED


class DepotLineCase(_Case):
    booking_text: str = Field(min_length=1)
    expect: AllocationCategory

    @model_validator(mode="after")
    def _recomputed(self) -> Self:
        if _depot_category(self.booking_text) is not self.expect:
            raise ValueError(
                f"{self.id}: BOOKING_PATTERNS gives {_depot_category(self.booking_text)}"
            )
        return self


class DepotLinesFixture(_Fixture):
    cases: list[DepotLineCase] = Field(min_length=1)


# --- holdings_reconcile.yaml: rules.depot.reconcile_holdings (§7) ---------------------------


class DeclaredPosition(Schema):
    asset: str
    units: NonNegativeQuantity
    value_eur: Money


class HeldMovement(Schema):
    asset: str
    quantity: Quantity
    occurred_on: date
    external_ref: str | None = None
    status: MovementStatus = MovementStatus.CONFIRMED


class ReportTrade(Schema):
    asset: str
    reference: str


class PositionExpect(Schema):
    asset: str
    listed: bool = True  # False: held by the movements, absent from the report (0 by omission)
    reconciled: bool
    difference_units: Quantity
    price_stored: bool
    error: str | None = None
    missing_references: list[str] | None = None


class HoldingsExpect(Schema):
    reconciled_through: date | None
    positions: list[PositionExpect]


class HoldingsCase(_Case):
    as_of: date
    declared: list[DeclaredPosition] = Field(min_length=1)
    movements: list[HeldMovement]
    report_trades: list[ReportTrade]
    expect: HoldingsExpect

    def recompute(self) -> HoldingsExpect:
        present = {m.external_ref for m in self.movements if m.external_ref}

        def held(asset: str) -> Decimal:
            return sum(
                (
                    m.quantity
                    for m in self.movements
                    if m.asset == asset
                    and m.status is MovementStatus.CONFIRMED
                    and m.occurred_on <= self.as_of
                ),
                Decimal(0),
            )

        declared = {p.asset for p in self.declared}
        listed = [(p.asset, p.units, True) for p in self.declared]
        unlisted = [
            (asset, Decimal(0), False)
            for asset in dict.fromkeys(m.asset for m in self.movements)
            if asset not in declared and held(asset) != 0
        ]
        positions = []
        for asset, units, is_listed in listed + unlisted:
            difference = units - held(asset)
            reconciled = abs(difference) <= depot.UNITS_TOLERANCE
            missing = [
                t.reference
                for t in self.report_trades
                if t.asset == asset and t.reference not in present
            ]
            positions.append(
                PositionExpect(
                    asset=asset,
                    listed=is_listed,
                    reconciled=reconciled,
                    difference_units=difference,
                    price_stored=reconciled and units > 0,
                    error=None if reconciled else E.DEPOT_HOLDINGS_MISMATCH,
                    missing_references=None if reconciled else missing,
                )
            )
        through = self.as_of if all(p.reconciled for p in positions) else None
        return HoldingsExpect(reconciled_through=through, positions=positions)

    @model_validator(mode="after")
    def _recomputed(self) -> Self:
        if self.recompute() != self.expect:
            raise ValueError(f"{self.id}: the reconciliation gives {self.recompute()}")
        return self


class HoldingsFixture(_Fixture):
    assets: dict[str, Isin]
    cases: list[HoldingsCase] = Field(min_length=1)

    @model_validator(mode="after")
    def _known_assets(self) -> Self:
        for case in self.cases:
            named = {p.asset for p in case.declared} | {m.asset for m in case.movements}
            if named - set(self.assets):
                raise ValueError(f"{case.id}: unknown assets {sorted(named - set(self.assets))}")
        return self


# --- quirion_documents.yaml: parse_lines for the depot kinds and the proposals (D165) --------


class ProposedMovement(Schema):
    kind: MovementKind
    isin: Isin
    quantity: Quantity
    eur_amount: Money | None = None
    fee_eur: Money | None = None
    tax_withheld_eur: Money | None = None
    occurred_on: date
    external_ref: str | None = None


class ProposedHolding(Schema):
    isin: Isin
    as_of: date
    declared_units: NonNegativeQuantity
    declared_value_eur: Money


class ProposedTrade(Schema):
    isin: Isin
    trade_date: date
    units: Quantity
    amount: Money
    reference: str


class DepotProposals(Schema):
    movements: list[ProposedMovement] = Field(default_factory=list)
    holdings: list[ProposedHolding] = Field(default_factory=list)
    trades: list[ProposedTrade] = Field(default_factory=list)


def _german(text: str) -> Decimal:
    return Decimal(text.strip().replace(".", "").replace(",", "."))


def _german_date(text: str) -> date:
    day, month, year = text.strip().split(".")
    return date(int(year), int(month), int(day))


_CENT = Decimal("0.01")


class QuirionCase(phase2.ParseCase):
    pending: str | None = None  # the proposals' step
    parse_pending: str | None = None  # a parse that needs a later step (the scrub)
    proposes: DepotProposals | None = None

    def _value(self, field: str):
        return self.expect[field].value if field in self.expect else None

    def _decimal(self, field: str) -> Decimal | None:
        value = self._value(field)
        return None if value is None else Decimal(str(value))

    def recompute(self) -> DepotProposals | None:
        v, d = self._value, self._decimal
        if self.kind == "trade_confirmation" and "side" in self.expect:
            gross, fees, taxes, net = (
                d(f) for f in ("gross_amount", "fees", "taxes", "net_amount")
            )
            buy = v("side") == "Kauf"
            if net != (gross + fees + taxes if buy else gross - fees - taxes):
                raise ValueError(f"{self.id}: net amount does not follow gross, fees and taxes")
            units = d("units")
            return DepotProposals(
                movements=[
                    ProposedMovement(
                        kind=MovementKind.ACQUISITION if buy else MovementKind.DISPOSAL,
                        isin=v("isin"),
                        quantity=units if buy else -units,
                        eur_amount=-net if buy else net,
                        fee_eur=fees,
                        tax_withheld_eur=taxes,
                        occurred_on=date.fromisoformat(v("trade_date")),
                        external_ref=v("order_reference"),
                    )
                ]
            )
        if self.kind == "distribution_note":
            gross, held, german, net = (
                d(f) for f in ("gross_amount", "withholding_tax", "german_taxes", "net_amount")
            )
            if net != gross - held - german:
                raise ValueError(f"{self.id}: net = gross − both taxes")
            if (d("units") * d("gross_per_unit")).quantize(_CENT) != gross:
                raise ValueError(f"{self.id}: gross = units × gross per unit")
            gross_eur, taxes_eur, net_eur = (
                d(f) for f in ("gross_amount_eur", "german_taxes_eur", "net_amount_eur")
            )
            if abs(gross_eur - taxes_eur - net_eur) > _CENT * 3:
                raise ValueError(f"{self.id}: the euro net is not the euro gross − taxes")
            return DepotProposals(
                movements=[
                    ProposedMovement(
                        kind=MovementKind.DISTRIBUTION,
                        isin=v("isin"),
                        quantity=Decimal(0),
                        eur_amount=gross_eur,
                        tax_withheld_eur=gross_eur - net_eur,
                        occurred_on=date.fromisoformat(v("payment_date")),
                    )
                ]
            )
        if self.kind == "depot_report":
            as_of = date.fromisoformat(v("report_date"))
            holdings = []
            for item in v("positions"):
                isin, _name, units, price, value, _currency = item.split(" | ")
                if (_german(units) * _german(price)).quantize(_CENT) != _german(value):
                    raise ValueError(f"{self.id}: {isin} value = units × price")
                holdings.append(
                    ProposedHolding(
                        isin=isin,
                        as_of=as_of,
                        declared_units=_german(units),
                        declared_value_eur=_german(value),
                    )
                )
            trades = []
            for item in v("trades"):
                isin, day, units, amount, reference = item.split(" | ")
                trades.append(
                    ProposedTrade(
                        isin=isin,
                        trade_date=_german_date(day),
                        units=_german(units),
                        amount=_german(amount),
                        reference=reference,
                    )
                )
            return DepotProposals(holdings=holdings, trades=trades)
        if self.kind == "securities_transfer" and "positions" in self.expect:
            inward = v("direction") == "Einbuchung"
            movements = []
            for item in v("positions"):
                isin, _name, units = item.split(" | ")
                movements.append(
                    ProposedMovement(
                        kind=MovementKind.INTERNAL_TRANSFER_IN
                        if inward
                        else MovementKind.INTERNAL_TRANSFER_OUT,
                        isin=isin,
                        quantity=_german(units) if inward else -_german(units),
                        occurred_on=date.fromisoformat(v("transfer_date")),
                        external_ref=v("reference"),
                    )
                )
            return DepotProposals(movements=movements)
        return None

    @model_validator(mode="after")
    def _recomputed(self) -> Self:
        if self.recompute() != self.proposes:
            raise ValueError(f"{self.id}: the proposals recompute to {self.recompute()}")
        return self


class QuirionFixture(Schema):
    cases: list[QuirionCase] = Field(min_length=1)


# --- bison_csv.yaml: rules.exchange.classify / movement_hash, the typed balance (D166) -------

_INWARD = {
    MovementKind.ACQUISITION,
    MovementKind.INTERNAL_TRANSFER_IN,
    MovementKind.STAKING_REWARD,
    MovementKind.CASHBACK_REWARD,
    MovementKind.UNCATEGORIZED,
}


def _exchange_kind(line: MovementLine) -> tuple[MovementKind, MovementStatus]:
    kind, eur = line.type.casefold(), line.asset == "EUR"
    confirmed, proposed = MovementStatus.CONFIRMED, MovementStatus.PROPOSED
    if kind == "buy":
        return MovementKind.ACQUISITION, confirmed
    if kind == "sell":
        return MovementKind.DISPOSAL, confirmed
    if kind == "deposit":
        return (
            (MovementKind.INTERNAL_TRANSFER_IN, confirmed)
            if eur
            else (MovementKind.CASHBACK_REWARD, proposed)
        )
    if kind == "withdrawal":
        return MovementKind.INTERNAL_TRANSFER_OUT, confirmed if eur else proposed
    if kind.startswith(("staking", "reward")):
        return MovementKind.STAKING_REWARD, confirmed
    return MovementKind.UNCATEGORIZED, proposed


class ExpectedMovement(Schema):
    row: int = Field(ge=0)
    kind: MovementKind
    status: MovementStatus
    quantity: Quantity
    eur_amount: Money | None = None
    fee_eur: Money | None = None
    fee_quantity: NonNegativeQuantity | None = None
    external_ref: str | None = None
    network: str | None = None
    tx_hash: str | None = None
    question: str | None = None
    position: int | None = Field(default=None, ge=0)  # among identical rows without an id


class AssetBalanceExpect(Schema):
    asset: AssetSymbol
    reconciled: bool
    difference_units: Quantity
    error: str | None = None


class ExchangeExpect(Schema):
    rows_new: int = Field(ge=0)
    rows_skipped: int = Field(ge=0)
    movements: list[ExpectedMovement]
    status: ImportStatus | None = None  # with `typed`
    assets: list[AssetBalanceExpect] | None = None


class ExchangeCase(_Case):
    rows: list[MovementLine] = Field(min_length=1)
    stored_refs: list[str] = Field(default_factory=list)
    held_before: dict[AssetSymbol, Quantity] = Field(default_factory=dict)
    typed: dict[AssetSymbol, NonNegativeQuantity] | None = None
    expect: ExchangeExpect

    def recompute(self) -> ExchangeExpect:
        seen, identical = set(self.stored_refs), {}
        movements, skipped = [], 0
        for index, line in enumerate(self.rows):
            if line.external_ref is not None:
                if line.external_ref in seen:
                    skipped += 1
                    continue
                seen.add(line.external_ref)
                position = None
            else:
                key = line.model_dump_json()
                position = identical.get(key, 0)
                identical[key] = position + 1
            kind, status = _exchange_kind(line)
            sign = 1 if kind in _INWARD else -1
            eur = line.quantity * sign if line.asset == "EUR" else line.eur_amount
            if eur is not None and kind is MovementKind.ACQUISITION:
                eur = -eur
            movements.append(
                ExpectedMovement(
                    row=index,
                    kind=kind,
                    status=status,
                    quantity=line.quantity * sign,
                    eur_amount=eur,
                    fee_eur=line.fee_eur,
                    fee_quantity=line.fee_quantity,
                    external_ref=line.external_ref,
                    network=line.network,
                    tx_hash=line.tx_hash,
                    question=(
                        E.EXCHANGE_DEPOSIT_QUESTION
                        if kind is MovementKind.CASHBACK_REWARD
                        else None
                    ),
                    position=position,
                )
            )
        result = ExchangeExpect(rows_new=len(movements), rows_skipped=skipped, movements=movements)
        if self.typed is None:
            return result
        assets = []
        for asset, units in self.typed.items():
            held = self.held_before.get(asset, Decimal(0)) + sum(
                (m.quantity for m in movements if self.rows[m.row].asset == asset), Decimal(0)
            )
            difference = units - held
            assets.append(
                AssetBalanceExpect(
                    asset=asset,
                    reconciled=difference == 0,
                    difference_units=difference,
                    error=None if difference == 0 else E.EXCHANGE_UNRECONCILED,
                )
            )
        reconciled = all(a.reconciled for a in assets)
        return result.model_copy(
            update={
                "status": ImportStatus.RECONCILED if reconciled else ImportStatus.UNRECONCILED,
                "assets": assets,
            }
        )

    @model_validator(mode="after")
    def _recomputed(self) -> Self:
        if self.recompute() != self.expect:
            raise ValueError(f"{self.id}: the import gives {self.recompute()}")
        return self


class ExchangeFixture(_Fixture):
    cases: list[ExchangeCase] = Field(min_length=1)


# --- dkb_csv.yaml: services.bank.dkb_csv.parse and its import (D173 (3), D164) --------------


class DkbImportExpect(Schema):
    account_iban: str  # the IBAN of the account the file is imported onto
    status: ReconcileStatus | None = None
    prefilled_closing: Money | None = None
    prefilled_on: date | None = None
    question: str | None = None
    after_confirm: dict[str, str] | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _one_outcome(self) -> Self:
        if (self.status is None) == (self.error is None):
            raise ValueError("an import has a status or an error")
        return self


class DkbCase(phase2.ParserCase):
    pending: str | None = None
    import_: DkbImportExpect = Field(alias="import")

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        expect, outcome = self.expect, self.import_
        if expect is None or expect.format is not StatementFormat.DKB_CSV:
            raise ValueError(f"{self.id}: a DKB file parses as dkb_csv")
        mismatch = expect.account_iban != outcome.account_iban
        if mismatch != (outcome.error == E.STATEMENT_ACCOUNT_MISMATCH):
            raise ValueError(f"{self.id}: refused exactly when the file names another IBAN")
        if outcome.status is not None:
            if outcome.status is not ReconcileStatus.PENDING_BALANCE:
                raise ValueError(f"{self.id}: a DKB import waits at pending_balance (Q-O75)")
            day = expect.last_booking_on.strftime("%d.%m.%Y")
            if outcome.question != f"„Ist das der Saldo nach der letzten Buchung vom {day}?“":
                raise ValueError(f"{self.id}: the question names the last booking day")
            if outcome.after_confirm != {
                "status": ReconcileStatus.RECONCILED,
                "balance_source": BalanceSource.USER,
            }:
                raise ValueError(f"{self.id}: a confirmed balance is the user's")
        pinned = [p.line for p in expect.lines]
        if expect.lines and len(pinned) == expect.line_count:
            if sum((line.amount for line in pinned), Decimal(0)) != expect.total:
                raise ValueError(f"{self.id}: the pinned lines sum to the total")
            for_hash = [
                LineForHash(
                    **line.model_dump(include=set(LineForHash.model_fields)),
                    counterparty_iban_hmac=None,
                )
                for line in pinned
            ]
            hashes = statements.line_hashes(UUID(int=1), for_hash)
            if len(set(hashes)) != len(hashes):
                raise ValueError(f"{self.id}: every line keeps its own hash")
        return self


class DkbFixture(_Fixture):
    cases: list[DkbCase] = Field(min_length=1)


# --- card_pdf.yaml: services.bank.card_pdf and its import (D176 (3), (4)) ------------------

# Any run of digits as long as a masked card number's visible parts, with or without stars.
_CARD_DIGITS = re.compile(r"\d{6}\*+\d{4}|\*{4,}\d{4}")


class CardInput(Schema):
    """The statement's text layer, one string per page; or another case's pages with
    `replace` ([old, new], each old occurs, every occurrence replaced) applied. `fresh_file`:
    the same text drawn into a new file (other bytes), so the document is not deduplicated."""

    pages: list[str] | None = Field(default=None, min_length=1)
    same_as: str | None = None
    replace: list[tuple[str, str]] = Field(default_factory=list)
    fresh_file: bool = False

    @model_validator(mode="after")
    def _one_source(self) -> Self:
        if (self.pages is None) == (self.same_as is None):
            raise ValueError("exactly one of pages/same_as")
        if self.same_as is None and (self.replace or self.fresh_file):
            raise ValueError("replace and fresh_file need same_as")
        return self


class CardExpect(Schema):
    format: Literal["card_pdf"]
    account_iban: None = None
    account_ref: str = Field(pattern=r"^[0-9]+$")
    card_last4: str | None = Field(default=None, pattern=r"^[0-9]{4}$")
    payment_iban: str | None = None
    opening_balance: Money
    closing_balance: Money
    balance_error: str | None = None  # statement.report_unbalanced
    line_count: int = Field(ge=1)
    total: Money
    first_booking_on: date
    last_booking_on: date
    warnings: list[str] = Field(default_factory=list)
    lines: list[phase2.PinnedLine] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.payment_iban is not None:
            normalize_iban(self.payment_iban)  # a checksum-valid IBAN
        if self.balance_error is None and self.opening_balance + self.total != self.closing_balance:
            raise ValueError("opening + total must equal closing when nothing breaks")
        if self.balance_error not in (None, statements.REPORT_UNBALANCED):
            raise ValueError("a card statement's only file error is statement.report_unbalanced")
        if any(p.index >= self.line_count for p in self.lines):
            raise ValueError("a pinned line index is past line_count")
        pinned = [p.line for p in self.lines]
        if len(pinned) == self.line_count:
            if sum((line.amount for line in pinned), Decimal(0)) != self.total:
                raise ValueError("the pinned lines sum to the total")
            dates = [line.booking_date for line in pinned]
            if (min(dates), max(dates)) != (self.first_booking_on, self.last_booking_on):
                raise ValueError("first and last booking are the lines' own")
        for line in pinned:
            if line.bank_reference is None or not re.fullmatch(r"[0-9]{10}", line.bank_reference):
                raise ValueError("every line's bank reference is its ten-digit Bew.ID")
            if line.counterparty_iban is not None:
                raise ValueError("a card line names no IBAN")
            text = " ".join([line.purpose, line.counterparty_name or ""])
            if _CARD_DIGITS.search(text):
                raise ValueError("no part of a card number in a line")
        refs = [line.bank_reference for line in pinned]
        if len(set(refs)) != len(refs):
            raise ValueError("every Bew.ID once")
        return self


class CardBooking(Schema):
    index: int = Field(ge=0)
    category: str  # an AllocationCategory value (hh_interest_fees from step 2b)


class CardImportExpect(Schema):
    after: list[str]  # the cases imported onto the same account before this one, in order
    status: ImportStatus | None = None
    error_code: str | None = None  # the stored import's file error
    error: str | None = None  # refused before anything is written
    lines_new: int | None = Field(default=None, ge=0)
    lines_skipped: int | None = Field(default=None, ge=0)
    account_ref_masked: str | None = Field(default=None, pattern=r"^\*{4} [A-Z0-9]{4}$")
    payment_iban_masked: str | None = None  # proposed on the import, confirmed by a person
    booked: list[CardBooking] = Field(default_factory=list)
    none_matched: bool = False

    @model_validator(mode="after")
    def _one_outcome(self) -> Self:
        if (self.status is None) == (self.error is None):
            raise ValueError("an import has a status or an error")
        if self.error is not None and not self.error.startswith("statement."):
            raise ValueError("a refusal is a statement.* code")
        if self.status is not None and None in (self.lines_new, self.lines_skipped):
            raise ValueError("a stored import counts its new and skipped lines")
        return self


class CardCase(_Case):
    input: CardInput
    expect: CardExpect
    import_: CardImportExpect = Field(alias="import")

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        expect, outcome = self.expect, self.import_
        if outcome.status is not None:
            if outcome.lines_new + outcome.lines_skipped != expect.line_count:
                raise ValueError(f"{self.id}: every line is new or skipped")
            if (outcome.error_code is not None) != (expect.balance_error is not None):
                raise ValueError(f"{self.id}: a file error is stored exactly when the file breaks")
            if outcome.error_code is not None and outcome.error_code != expect.balance_error:
                raise ValueError(f"{self.id}: the stored error is the file's")
            broken = outcome.error_code is not None
            if broken != (outcome.status is ImportStatus.UNRECONCILED) or broken != (
                outcome.none_matched
            ):
                raise ValueError(f"{self.id}: a broken file is unreconciled and never matched")
        masked_ref = f"**** {expect.account_ref[-4:]}"
        if outcome.account_ref_masked not in (None, masked_ref):
            raise ValueError(f"{self.id}: the reference is masked to its last four")
        if outcome.payment_iban_masked is not None:
            if expect.payment_iban is None:
                raise ValueError(f"{self.id}: only a printed payment IBAN is proposed")
            if outcome.payment_iban_masked != mask_iban(normalize_iban(expect.payment_iban)):
                raise ValueError(f"{self.id}: the proposed payment IBAN is masked")
        by_index = {p.index: p.line for p in expect.lines}
        for booking in outcome.booked:
            line = by_index.get(booking.index)
            if line is None or line.counterparty_name is not None:
                raise ValueError(f"{self.id}: code books a pinned line without a merchant")
            # D176 (4) as amended (review of step 2b): the payment „Bezahlung“, a credit, is
            # the card's transfer leg; any other line code books is a debit (interest, fee).
            payment = line.amount > 0 and line.booking_text == "Bezahlung"
            if payment != (booking.category == "internal_transfer_in") or line.amount == 0:
                raise ValueError(f"{self.id}: a credit booked by code is the payment, a transfer")
        return self


class CardFixture(_Fixture):
    cases: list[CardCase] = Field(min_length=1)

    @model_validator(mode="after")
    def _references(self) -> Self:
        seen: set[str] = set()
        for case in self.cases:
            if case.input.same_as is not None and case.input.same_as not in seen:
                raise ValueError(f"{case.id}: same_as names an earlier case")
            if any(name not in seen for name in case.import_.after):
                raise ValueError(f"{case.id}: after names earlier cases")
            seen.add(case.id)
            self.pages(case.id)  # every replacement applies
        return self

    def pages(self, case_id: str) -> list[str]:
        """The case's page texts, with another case's pages and the replacements resolved."""
        case = next(c for c in self.cases if c.id == case_id)
        if case.input.pages is not None:
            return list(case.input.pages)
        pages = self.pages(case.input.same_as)
        for old, new in case.input.replace:
            if not any(old in page for page in pages):
                raise ValueError(f"{case.id}: {old!r} occurs in no page")
            pages = [page.replace(old, new) for page in pages]
        return pages


# --- valuation.yaml: rules.valuation.price_on / value (D167 revised) ------------------------

_CENT, _PRICE_PLACES = Decimal("0.01"), Decimal("0.00000001")


class PriceRow(Schema):
    on: date
    price_eur: PriceEur
    source: PriceSourceKind


def _price_on(prices: list[PriceRow], on: date) -> PriceRow | None:
    days = [p.on for p in prices if p.on <= on]
    if not days:
        return None
    rows = [p for p in prices if p.on == max(days)]
    return min(rows, key=lambda p: valuation.SOURCE_PRECEDENCE.index(p.source))


class FxRate(Schema):
    on: date
    per_eur: PriceEur

    @model_validator(mode="after")
    def _ecb_day(self) -> Self:
        if self.on.weekday() >= 5:
            raise ValueError(f"the ECB publishes no rate on a weekend ({self.on})")
        return self


def _to_eur(amount: Decimal, on: date, rates: list[FxRate]) -> tuple[date, Decimal]:
    rate = max((r for r in rates if r.on <= on), key=lambda r: r.on)
    return rate.on, (amount / rate.per_eur).quantize(_PRICE_PLACES, ROUND_HALF_UP)


class HeldPosition(Schema):
    units: Quantity
    reconciled_through: date
    cash: bool = False


class PriceRef(Schema):
    on: date
    source: PriceSourceKind


class ValuationExpect(Schema):
    price: PriceRef | None
    stale: bool
    value_eur: Money | None
    not_valued_reason: str | None = None
    warning: str | None = None


class ValuationCase(_Case):
    on: date
    prices: list[PriceRow]
    position: HeldPosition
    expect: ValuationExpect

    def recompute(self) -> ValuationExpect:
        if self.position.cash:
            return ValuationExpect(price=None, stale=False, value_eur=self.position.units)
        price = _price_on(self.prices, self.on)
        if price is None:
            raise ValueError(f"{self.id}: no price on or before {self.on}")
        stale = (self.on - price.on).days > valuation.STALE_AFTER_DAYS
        past = self.on > self.position.reconciled_through
        return ValuationExpect(
            price=PriceRef(on=price.on, source=price.source),
            stale=stale,
            value_eur=(
                None
                if past
                else (self.position.units * price.price_eur).quantize(_CENT, ROUND_HALF_UP)
            ),
            not_valued_reason=E.VALUATION_NOT_VALUED if past else None,
            warning=E.VALUATION_STALE_PRICE if stale else None,
        )

    @model_validator(mode="after")
    def _recomputed(self) -> Self:
        if self.recompute() != self.expect:
            raise ValueError(f"{self.id}: the rule gives {self.recompute()}")
        return self


class Figure(Schema):
    on: date
    amount: PriceEur
    currency: CurrencyCode


class FxExpect(Schema):
    rate_on: date
    price_eur: PriceEur


class FxCase(_Case):
    figure: Figure
    rates: list[FxRate] = Field(min_length=1)
    expect: FxExpect

    @model_validator(mode="after")
    def _recomputed(self) -> Self:
        rate_on, price = _to_eur(self.figure.amount, self.figure.on, self.rates)
        if FxExpect(rate_on=rate_on, price_eur=price) != self.expect:
            raise ValueError(f"{self.id}: the ECB rule gives {rate_on}, {price}")
        return self


class ValuationFixture(_Fixture):
    cases: list[ValuationCase] = Field(min_length=1)
    fx_cases: list[FxCase] = Field(min_length=1)


# --- issuer_nav.yaml: the issuer adapters' rows as prices (D174) -----------------------------


class StoredPrice(Schema):
    on: date
    price_eur: PriceEur


class IssuerNavExpect(Schema):
    stored: list[StoredPrice]
    stale: bool | None = None
    warning: str | None = None
    card: str | None = None
    valued_by: PriceSourceKind | None = None
    label: str | None = None


REPORT_PRICE_LABEL = "„Kurs aus dem letzten Bericht“"


class IssuerNavCase(_Case):
    sample: str | None = None  # a parse case's id: `navs` is that recorded file's parse
    first_movement_on: date
    navs: list[NavPoint] = Field(default_factory=list)
    rates: list[FxRate] = Field(default_factory=list)
    unparsable: bool = False
    last_price_on: date | None = None
    today: date | None = None
    failed_on: list[date] = Field(default_factory=list)
    no_adapter: bool = False
    prices: list[PriceRow] = Field(default_factory=list)  # rows already stored
    expect: IssuerNavExpect

    def recompute(self) -> IssuerNavExpect:
        stored = []
        if not self.unparsable and not self.no_adapter:
            for nav in self.navs:
                if nav.on < self.first_movement_on:
                    continue
                price = (
                    nav.nav if nav.currency == "EUR" else _to_eur(nav.nav, nav.on, self.rates)[1]
                )
                stored.append(StoredPrice(on=nav.on, price_eur=price))
        result = IssuerNavExpect(stored=stored)
        if self.unparsable:
            stale = (self.today - self.last_price_on).days > valuation.STALE_AFTER_DAYS
            result = result.model_copy(
                update={"stale": stale, "warning": E.VALUATION_STALE_PRICE if stale else None}
            )
        if self.failed_on:
            days = sorted(self.failed_on)
            run = 1
            for before, after in zip(days, days[1:], strict=False):
                run = run + 1 if (after - before).days == 1 else 1
            failed = run >= valuation.SOURCE_FAILED_AFTER_DAYS
            result = result.model_copy(update={"card": E.PRICE_SOURCE_FAILED if failed else None})
        if self.prices or self.no_adapter:
            rows = self.prices + [
                PriceRow(on=s.on, price_eur=s.price_eur, source=PriceSourceKind.ISSUER_NAV)
                for s in stored
            ]
            best = _price_on(rows, max(r.on for r in rows))
            result = result.model_copy(update={"valued_by": best.source})
            if self.no_adapter:
                result = result.model_copy(update={"label": REPORT_PRICE_LABEL, "card": None})
        return result

    @model_validator(mode="after")
    def _recomputed(self) -> Self:
        if self.recompute() != self.expect:
            raise ValueError(f"{self.id}: the rule gives {self.recompute()}")
        return self


SAMPLES = FIXTURES / "samples"


class ParseExpect(Schema):
    isin: Isin | None = None
    currency: CurrencyCode | None = None
    navs: list[NavPoint] = Field(default_factory=list)
    error: str | None = None  # SourceError.kind of a file the adapter refuses

    @model_validator(mode="after")
    def _one(self) -> Self:
        if (self.error is None) == (self.isin is None or not self.navs):
            raise ValueError("a parse case expects an ISIN and NAV rows, or an error")
        return self


class ParseCase(_Case):
    """Step 5b: an issuer adapter's parse of a recorded file under fixtures/phase6/samples/
    (the issuer's real layout, invented values), replayed by tests/test_prices_sources.py."""

    issuer: str
    sample: str
    expect: ParseExpect

    @model_validator(mode="after")
    def _exists(self) -> Self:
        if not (SAMPLES / self.sample).is_file():
            raise ValueError(f"{self.id}: no recorded file {self.sample}")
        return self


class IssuerNavFixture(_Fixture):
    cases: list[IssuerNavCase] = Field(min_length=1)
    parse_cases: list[ParseCase] = Field(default_factory=list)

    @model_validator(mode="after")
    def _samples(self) -> Self:
        parsed = {c.id: c.expect for c in self.parse_cases}
        for case in self.cases:
            if case.sample is None:
                continue
            if case.sample not in parsed or parsed[case.sample].error is not None:
                raise ValueError(f"{case.id}: {case.sample} is no parse case with rows")
            if sorted(parsed[case.sample].navs, key=lambda n: n.on) != sorted(
                case.navs, key=lambda n: n.on
            ):
                raise ValueError(f"{case.id}: navs differ from the parse of {case.sample}")
        return self


# --- net_worth.yaml: services.net_worth over a fixture ledger (D167 (4)) --------------------

_CLASS_OF_KIND = {
    AccountKind.BANK_CURRENT: NetWorthClass.BANK,
    AccountKind.BANK_SAVINGS: NetWorthClass.BANK,
    AccountKind.DEPOT: NetWorthClass.DEPOT,
    AccountKind.EXCHANGE: NetWorthClass.CRYPTO,
}


class ValuedPosition(Schema):
    asset: str
    units: Quantity
    price_eur: PriceEur | None = None
    source: PriceSourceKind | None = None
    price_on: date | None = None
    cash: bool = False

    @model_validator(mode="after")
    def _priced(self) -> Self:
        if self.cash == (self.price_eur is not None):
            raise ValueError(f"{self.asset}: a price unless it is cash")
        return self

    @property
    def value(self) -> Decimal:
        if self.cash:
            return self.units
        return (self.units * self.price_eur).quantize(_CENT, ROUND_HALF_UP)


class WorthAccount(Schema):
    id: str
    kind: AccountKind
    shares: dict[str, Fraction]
    balance: Money | None = None
    positions: list[ValuedPosition] = Field(default_factory=list)

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if sum(self.shares.values()) != 1:
            raise ValueError(f"{self.id}: shares sum to 1")
        if (self.kind in BANK_ACCOUNT_KINDS) != (self.balance is not None):
            raise ValueError(f"{self.id}: a bank account has a balance, others positions")
        return self


class WorthProperty(Schema):
    shares: dict[str, Fraction]
    estimate: Money
    loan_balance: Money


class SnapshotExpect(Schema):
    account: str
    asset: str | None
    value_eur: Money


class NetWorthExpect(Schema):
    pending: str | None = None
    by_class: dict[NetWorthClass, Money]
    by_owner: dict[str, Money]
    total: Money
    snapshots: list[SnapshotExpect]


def _split(amount: Decimal, shares: dict[str, Fraction]) -> dict[str, Decimal]:
    parts = {}
    for owner, share in shares.items():
        exact = Fraction(amount) * share
        if (exact * 100).denominator != 1:
            raise ValueError(f"{amount} × {share} is not a whole number of cents")
        parts[owner] = Decimal(exact.numerator) / Decimal(exact.denominator)
    return parts


class NetWorthFixture(Schema):
    on: date
    owners: list[str] = Field(min_length=1)
    accounts: list[WorthAccount] = Field(min_length=1)
    property: WorthProperty
    expect: NetWorthExpect

    def recompute(self) -> NetWorthExpect:
        by_class = dict.fromkeys(NetWorthClass, Decimal("0.00"))
        by_owner = dict.fromkeys(self.owners, Decimal("0.00"))
        snapshots = []

        def add(cls: NetWorthClass, amount: Decimal, shares: dict[str, Fraction]) -> None:
            by_class[cls] += amount
            for owner, part in _split(amount, shares).items():
                by_owner[owner] += part

        for account in self.accounts:
            cls = _CLASS_OF_KIND[account.kind]
            if account.balance is not None:
                add(cls, account.balance, account.shares)
                snapshots.append(
                    SnapshotExpect(account=account.id, asset=None, value_eur=account.balance)
                )
            for position in account.positions:
                add(cls, position.value, account.shares)
                snapshots.append(
                    SnapshotExpect(
                        account=account.id, asset=position.asset, value_eur=position.value
                    )
                )
        add(NetWorthClass.PROPERTY, self.property.estimate, self.property.shares)
        add(NetWorthClass.LIABILITIES, -self.property.loan_balance, self.property.shares)
        total = sum(by_class.values(), Decimal(0))
        if total != sum(by_owner.values(), Decimal(0)):
            raise ValueError("the owners' figures sum to the total")
        return NetWorthExpect(
            pending=self.expect.pending,
            by_class=by_class,
            by_owner=by_owner,
            total=total,
            snapshots=snapshots,
        )

    @model_validator(mode="after")
    def _recomputed(self) -> Self:
        if self.recompute() != self.expect:
            raise ValueError(f"net worth recomputes to {self.recompute()}")
        if any(set(a.shares) - set(self.owners) for a in self.accounts):
            raise ValueError("a share names an owner of the workspace")
        return self


MODELS: dict[str, type[Schema]] = {
    "ownership": OwnershipFixture,
    "household_categories": HouseholdCategoriesFixture,
    "fints_slots": FintsSlotsFixture,
    "merchant_pattern": MerchantPatternFixture,
    "matching_order": MatchingOrderFixture,
    "transfers": TransfersFixture,
    "private_cost_link": PrivateCostLinkFixture,
    "depot_lines": DepotLinesFixture,
    "holdings_reconcile": HoldingsFixture,
    "quirion_documents": QuirionFixture,
    "bison_csv": ExchangeFixture,
    "dkb_csv": DkbFixture,
    "card_pdf": CardFixture,
    "valuation": ValuationFixture,
    "issuer_nav": IssuerNavFixture,
    "net_worth": NetWorthFixture,
}


@cache
def load(name: str) -> Schema:
    return MODELS[name].model_validate(raw(name))
