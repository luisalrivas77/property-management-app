"""The documents a property's tax year needs (tests/fixtures/phase1/document_checklist.yaml).

Spec amendment 3 (docs/phases/phase-1.md, 6 Nov 2026). The requirement catalogue below is
data: each entry names its scope, the document type or subtype that satisfies it, the link
targets that count, its tax-year rule and its due date. Phase 3 adds entries, not code paths
(docs/phases/phase-3.md §8, D64): `cost_receipt` per confirmed allocation of the year that
needs a Beleg, and `market_rent` per Wohnung let in the year.

Matching: an entry with a subtype matches on the subtype, one with only a type on the type;
an untyped document matches nothing. A match must be linked to one of the entry's accepted
targets for that scope (P itself, P's units, the loan, the tenancy). Two entries are
satisfied by a reference instead of a link: an acquisition cost row's document_id, and the
selected price split (by its source).

Status: present (at least one match, all listed oldest first) > incomplete (only documents
of the right kind on the right target without a tax year, for "tax_year = Y" entries) >
expected (due_on > today) > missing.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from enum import Enum, auto
from uuid import UUID

from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.checklist import (
    ChecklistAllocation,
    ChecklistCost,
    ChecklistDocument,
    ChecklistFacts,
    ChecklistItem,
    ChecklistLoan,
    ChecklistScope,
    ChecklistScopeType,
    ChecklistStatus,
    ChecklistTenancy,
    RequirementCode,
)
from hausbuch.schemas.common import TAX_YEAR_MIN
from hausbuch.schemas.enums import (
    AcquisitionCostKind,
    DepositForm,
    DocumentSubtype,
    DocumentType,
    LinkTarget,
    PriceSplitSource,
    PropertyKind,
)

# --- The catalogue ---------------------------------------------------------------------


class TaxYearRule(Enum):
    ANY = auto()  # the document's tax_year does not matter
    EQUALS = auto()  # tax_year = Y; null -> incomplete
    AT_MOST = auto()  # tax_year null or <= Y; never incomplete


class Satisfier(Enum):
    LINK = auto()  # a matching document linked to an accepted target
    COST_DOCUMENT = auto()  # the cost row's document_id
    PRICE_SPLIT = auto()  # by the selected split's source
    ALLOCATION_DOCUMENT = auto()  # the allocation's document_id (phase 3, D64)
    TAX_INPUT = auto()  # a market_rent tax input in force for the Wohnung (phase 3, D64)


# Y -> due date. None = "always": due as soon as the item exists.
DueRule = Callable[[int], date] | None
# Whether the item exists for this property, scope entity and tax year.
Applies = Callable[[ChecklistFacts, object], bool]


@dataclass(frozen=True)
class Requirement:
    code: RequirementCode
    scope: ChecklistScopeType
    satisfier: Satisfier = Satisfier.LINK
    type: DocumentType | None = None
    subtype: DocumentSubtype | None = None
    targets: frozenset[LinkTarget] = frozenset()
    tax_year: TaxYearRule = TaxYearRule.ANY
    due: DueRule = None
    applies: Applies | None = None


def _etw_only(facts: ChecklistFacts, _: object) -> bool:
    return facts.kind is PropertyKind.ETW


def _started_after_purchase(facts: ChecklistFacts, tenancy: object) -> bool:
    assert isinstance(tenancy, ChecklistTenancy)
    return tenancy.start_date >= facts.purchase_date


def _has_deposit(_: ChecklistFacts, tenancy: object) -> bool:
    assert isinstance(tenancy, ChecklistTenancy)
    return tenancy.deposit_form is not DepositForm.NONE


# The categories whose payments need a Beleg in the return's paperwork (§8).
RECEIPT_CATEGORIES = frozenset(
    {
        AllocationCategory.REPAIRS,
        AllocationCategory.RENOVATION,
        AllocationCategory.LOAN_FEES,
        AllocationCategory.PROFESSIONAL_FEES,
    }
)


def _needs_receipt(_: ChecklistFacts, allocation: object) -> bool:
    """A cost of a named category; a 0.00 row is never evidence (D70)."""
    assert isinstance(allocation, ChecklistAllocation)
    return allocation.category in RECEIPT_CATEGORIES and allocation.amount != 0


_PROPERTY = frozenset({LinkTarget.PROPERTY})
_PROPERTY_OR_UNIT = frozenset({LinkTarget.PROPERTY, LinkTarget.UNIT})
_LOAN = frozenset({LinkTarget.LOAN})
_TENANCY = frozenset({LinkTarget.TENANCY})

# In output order (the spec's table).
CATALOGUE: tuple[Requirement, ...] = (
    Requirement(
        RequirementCode.PURCHASE_CONTRACT,
        ChecklistScopeType.PROPERTY,
        subtype=DocumentSubtype.PURCHASE_CONTRACT,
        targets=_PROPERTY,
    ),
    Requirement(
        RequirementCode.PRICE_SPLIT, ChecklistScopeType.PROPERTY, satisfier=Satisfier.PRICE_SPLIT
    ),
    Requirement(
        RequirementCode.ACQUISITION_COST_EVIDENCE,
        ChecklistScopeType.ACQUISITION_COST,
        satisfier=Satisfier.COST_DOCUMENT,
    ),
    Requirement(
        RequirementCode.GRUNDSTEUERBESCHEID,
        ChecklistScopeType.PROPERTY,
        subtype=DocumentSubtype.GRUNDSTEUERBESCHEID,
        targets=_PROPERTY_OR_UNIT,  # issued per flat for an ETW (spec amendment 3, revised)
        tax_year=TaxYearRule.AT_MOST,
    ),
    Requirement(
        RequirementCode.LOAN_CONTRACT,
        ChecklistScopeType.LOAN,
        subtype=DocumentSubtype.LOAN_CONTRACT,
        targets=_LOAN,
    ),
    Requirement(
        RequirementCode.ZINSBESCHEINIGUNG,
        ChecklistScopeType.LOAN,
        type=DocumentType.ZINSBESCHEINIGUNG,
        targets=_LOAN,
        tax_year=TaxYearRule.EQUALS,
        due=lambda y: date(y + 1, 2, 28),
    ),
    Requirement(
        RequirementCode.LEASE,
        ChecklistScopeType.TENANCY,
        subtype=DocumentSubtype.LEASE,
        targets=_TENANCY,
    ),
    Requirement(
        RequirementCode.HANDOVER_PROTOCOL,
        ChecklistScopeType.TENANCY,
        subtype=DocumentSubtype.HANDOVER_PROTOCOL,
        targets=_TENANCY,
        applies=_started_after_purchase,
    ),
    Requirement(
        RequirementCode.DEPOSIT_RECORD,
        ChecklistScopeType.TENANCY,
        subtype=DocumentSubtype.DEPOSIT_RECORD,
        targets=_TENANCY,
        applies=_has_deposit,
    ),
    Requirement(
        RequirementCode.WIRTSCHAFTSPLAN,
        ChecklistScopeType.PROPERTY,
        type=DocumentType.WIRTSCHAFTSPLAN,
        targets=_PROPERTY_OR_UNIT,
        tax_year=TaxYearRule.EQUALS,
        due=lambda y: date(y, 1, 1),
        applies=_etw_only,
    ),
    Requirement(
        RequirementCode.HAUSGELDABRECHNUNG,
        ChecklistScopeType.PROPERTY,
        type=DocumentType.HAUSGELDABRECHNUNG,
        targets=_PROPERTY_OR_UNIT,
        tax_year=TaxYearRule.EQUALS,
        due=lambda y: date(y + 1, 6, 30),
        applies=_etw_only,
    ),
    # Phase 3 (§8, D64)
    Requirement(
        RequirementCode.COST_RECEIPT,
        ChecklistScopeType.ALLOCATION,
        satisfier=Satisfier.ALLOCATION_DOCUMENT,
        applies=_needs_receipt,
    ),
    Requirement(
        # Per Wohnung (D71): scopes() yields the property for an ETW or EFH, units for an MFH.
        RequirementCode.MARKET_RENT,
        ChecklistScopeType.UNIT,
        satisfier=Satisfier.TAX_INPUT,
    ),
)

_BY_CODE = {r.code: r for r in CATALOGUE}
_COST_KIND_ORDER = {kind: i for i, kind in enumerate(AcquisitionCostKind)}


# --- The rule --------------------------------------------------------------------------


def owned_in(facts: ChecklistFacts, tax_year: int) -> bool:
    """Owned on at least one day of the tax year (from purchase_date, Übergang)."""
    ended = facts.ownership_ended_on
    return facts.purchase_date.year <= tax_year and (ended is None or ended.year >= tax_year)


def available_years(facts: ChecklistFacts, today: date) -> list[int]:
    """The years a checklist can be asked for: purchase year through the current year,
    ending with the year ownership ended, and from TAX_YEAR_MIN at the earliest (no document
    can carry an earlier tax year). Never empty: a property entered before the year its
    Übergang falls in (bought on 1 Jan next year) offers that purchase year alone."""
    last = today.year
    if facts.ownership_ended_on is not None:
        last = min(last, facts.ownership_ended_on.year)
    first = max(facts.purchase_date.year, TAX_YEAR_MIN)
    return list(range(first, max(first, last) + 1))


def build_checklist(facts: ChecklistFacts, tax_year: int, today: date) -> list[ChecklistItem]:
    """The checklist of one property for one tax year, in catalogue order then by scope."""
    if not owned_in(facts, tax_year):
        return []
    ctx = _Context(facts, tax_year, today)
    items: list[ChecklistItem] = []
    for requirement in CATALOGUE:
        for entity, scope in ctx.scopes(requirement.scope):
            if requirement.applies is not None and not requirement.applies(facts, entity):
                continue
            items.append(ctx.item(requirement, entity, scope))
    return items


class _Context:
    def __init__(self, facts: ChecklistFacts, tax_year: int, today: date) -> None:
        self.facts = facts
        self.tax_year = tax_year
        self.today = today
        self.unit_ids = frozenset(u.id for u in facts.units)
        self.unit_labels = {u.id: u.label for u in facts.units}
        self.documents = sorted(facts.documents, key=lambda d: (d.received_at, d.id))

    # Scopes ------------------------------------------------------------------------

    def scopes(self, kind: ChecklistScopeType) -> Iterable[tuple[object, ChecklistScope]]:
        facts, year = self.facts, self.tax_year
        match kind:
            case ChecklistScopeType.PROPERTY:
                yield facts, ChecklistScope(type=kind, id=facts.property_id)
            case ChecklistScopeType.ACQUISITION_COST:
                for cost in sorted(
                    facts.acquisition_costs,
                    key=lambda c: (_COST_KIND_ORDER[c.kind], c.amount, c.id),
                ):
                    yield (
                        cost,
                        ChecklistScope(
                            type=kind, id=cost.id, cost_kind=cost.kind, cost_amount=cost.amount
                        ),
                    )
            case ChecklistScopeType.LOAN:
                year_end = date(year, 12, 31)
                for loan in sorted(facts.loans, key=lambda lo: (lo.disbursed_on, lo.id)):
                    if loan.disbursed_on <= year_end:
                        yield loan, ChecklistScope(type=kind, id=loan.id, lender=loan.lender)
            case ChecklistScopeType.TENANCY:
                year_start, year_end = date(year, 1, 1), date(year, 12, 31)
                for tenancy in sorted(facts.tenancies, key=lambda t: (t.start_date, t.id)):
                    if tenancy.start_date <= year_end and (
                        tenancy.end_date is None or tenancy.end_date >= year_start
                    ):
                        yield (
                            tenancy,
                            ChecklistScope(
                                type=kind,
                                id=tenancy.id,
                                unit_label=self.unit_labels[tenancy.unit_id],
                                tenancy_start=tenancy.start_date,
                            ),
                        )
            case ChecklistScopeType.ALLOCATION:
                # The allocations the §5.1 rule counts in this year (resolved beforehand).
                for allocation in sorted(facts.allocations, key=lambda a: (a.booking_date, a.id)):
                    if allocation.tax_year == year:
                        yield (
                            allocation,
                            ChecklistScope(
                                type=kind,
                                id=allocation.id,
                                category=allocation.category,
                                booking_date=allocation.booking_date,
                            ),
                        )
            case ChecklistScopeType.UNIT:
                yield from self._wohnungen()

    def _wohnungen(self) -> Iterable[tuple[object, ChecklistScope]]:
        """The Wohnungen let in the year (D71): the property itself for an ETW or EFH (one
        flat, however many rooms are let), each unit with a tenancy for an MFH. None for a
        phase 1 caller (market_rents is None)."""
        facts = self.facts
        if facts.market_rents is None:
            return
        year_start, year_end = date(self.tax_year, 1, 1), date(self.tax_year, 12, 31)
        let_units = {
            t.unit_id
            for t in facts.tenancies
            if t.start_date <= year_end and (t.end_date is None or t.end_date >= year_start)
        }
        if not let_units:
            return
        if facts.kind is not PropertyKind.MFH:
            yield None, ChecklistScope(type=ChecklistScopeType.PROPERTY, id=facts.property_id)
            return
        for unit in sorted(facts.units, key=lambda u: (u.label, u.id)):
            if unit.id in let_units:
                yield (
                    unit.id,
                    ChecklistScope(type=ChecklistScopeType.UNIT, id=unit.id, unit_label=unit.label),
                )

    def _target_ids(self, requirement: Requirement, entity: object) -> dict[LinkTarget, frozenset]:
        """For each accepted link target type, the ids that count for this scope."""
        ids: dict[LinkTarget, frozenset] = {}
        for target in requirement.targets:
            match target:
                case LinkTarget.PROPERTY:
                    ids[target] = frozenset({self.facts.property_id})
                case LinkTarget.UNIT:
                    ids[target] = self.unit_ids
                case LinkTarget.LOAN:
                    assert isinstance(entity, ChecklistLoan)
                    ids[target] = frozenset({entity.id})
                case LinkTarget.TENANCY:
                    assert isinstance(entity, ChecklistTenancy)
                    ids[target] = frozenset({entity.id})
                case _:
                    raise ValueError(f"unsupported checklist link target {target}")
        return ids

    # Items -------------------------------------------------------------------------

    def item(
        self, requirement: Requirement, entity: object, scope: ChecklistScope
    ) -> ChecklistItem:
        due_on = None if requirement.due is None else requirement.due(self.tax_year)
        match requirement.satisfier:
            case Satisfier.LINK:
                status, documents = self._by_link(requirement, entity, due_on)
            case Satisfier.COST_DOCUMENT:
                assert isinstance(entity, ChecklistCost)
                status, documents = self._by_reference(entity.document_id, due_on)
            case Satisfier.PRICE_SPLIT:
                status, documents = self._by_price_split(due_on)
            case Satisfier.ALLOCATION_DOCUMENT:
                assert isinstance(entity, ChecklistAllocation)
                status, documents = self._by_reference(entity.document_id, due_on)
            case Satisfier.TAX_INPUT:
                assert entity is None or isinstance(entity, UUID)
                status, documents = self._by_market_rent(entity, due_on)
        return ChecklistItem(
            code=requirement.code, status=status, scope=scope, due_on=due_on, documents=documents
        )

    def _by_link(
        self, requirement: Requirement, entity: object, due_on: date | None
    ) -> tuple[ChecklistStatus, list]:
        targets = self._target_ids(requirement, entity)
        candidates = [
            d
            for d in self.documents
            if _is_kind(d, requirement)
            and any(lk.target_id in targets.get(lk.target_type, ()) for lk in d.links)
        ]
        matches = [d.id for d in candidates if self._year_counts(requirement, d)]
        if matches:
            return ChecklistStatus.PRESENT, matches
        if requirement.tax_year is TaxYearRule.EQUALS:
            undated = [d.id for d in candidates if d.tax_year is None]
            if undated:
                return ChecklistStatus.INCOMPLETE, undated
        return self._absent(due_on), []

    def _by_reference(self, document_id, due_on: date | None) -> tuple[ChecklistStatus, list]:
        if document_id is not None:
            return ChecklistStatus.PRESENT, [document_id]
        return self._absent(due_on), []

    def _by_price_split(self, due_on: date | None) -> tuple[ChecklistStatus, list]:
        split = self.facts.selected_price_split
        if split is None:
            return self._absent(due_on), []
        match split.source:
            case PriceSplitSource.PURCHASE_CONTRACT:
                # The same status and documents as the purchase_contract item.
                contract = _BY_CODE[RequirementCode.PURCHASE_CONTRACT]
                return self._by_link(contract, self.facts, due_on)
            case PriceSplitSource.APPRAISAL:
                return self._by_reference(split.evidence_document_id, due_on)
            case PriceSplitSource.BMF_TOOL:
                return ChecklistStatus.PRESENT, []

    def _by_market_rent(
        self, unit_id: UUID | None, due_on: date | None
    ) -> tuple[ChecklistStatus, list]:
        """Present when a market_rent input of this Wohnung (unit_id None = the property's
        flat) is in force: not withdrawn, not superseded by another row, and valid from a
        day of the year or earlier. The input is the evidence, so no documents are listed."""
        rows = self.facts.market_rents or []
        superseded = {r.supersedes_id for r in rows if r.supersedes_id is not None}
        year_end = date(self.tax_year, 12, 31)
        if any(
            r.unit_id == unit_id
            and not r.withdrawn
            and r.id not in superseded
            and r.valid_from <= year_end
            for r in rows
        ):
            return ChecklistStatus.PRESENT, []
        return self._absent(due_on), []

    def _year_counts(self, requirement: Requirement, document: ChecklistDocument) -> bool:
        match requirement.tax_year:
            case TaxYearRule.ANY:
                return True
            case TaxYearRule.EQUALS:
                return document.tax_year == self.tax_year
            case TaxYearRule.AT_MOST:
                return document.tax_year is None or document.tax_year <= self.tax_year

    def _absent(self, due_on: date | None) -> ChecklistStatus:
        if due_on is not None and due_on > self.today:
            return ChecklistStatus.EXPECTED
        return ChecklistStatus.MISSING


def _is_kind(document: ChecklistDocument, requirement: Requirement) -> bool:
    if document.type is None:
        return False
    if requirement.subtype is not None:
        return document.subtype is requirement.subtype
    return document.type is requirement.type
