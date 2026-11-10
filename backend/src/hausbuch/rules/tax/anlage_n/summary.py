"""§5.9, D98: what phase 4's zvE estimate and the whole-return estimate take from phase 4b.

`employment_summary`: one owner's gross, its source, the Werbungskosten applied and the
Vorsorge deductible (from `draft_n`'s figures and `vorsorge`). `household_figures`: the
household's context for the assessed tax (D109, D112, D102):

- Lohnersatzleistungen `L` per owner = Σ Nr. 15 of the certificates (real or projected) + the
  typed `replacement_income` rows, less the part of the Arbeitnehmer-Pauschbetrag not used
  against pay (§32b Abs. 2: `unused = min(own, max(0, 1,230 − gross))`); a typed amount equal to
  one of that owner's non-zero Nr. 15 amounts raises `return.benefit_possible_double`;
- church tax paid = Σ Nr. 6 + Nr. 7 over the certificates;
- donations within `20 % × max(0, GdE)`, cut to the cent (§10b Abs. 1); the excess raises
  `return.donation_over_cap {excess}` (no carry-forward);
- childcare per row `min(cents(amount × 80 %), 4,800)` (§10 Abs. 1 Nr. 5); a child 14 before the
  year began: RuleError `return.childcare_age`; one `return.childcare_until_14` per child turning
  14 in the year;
- §35a: any row paid in cash: RuleError `return.household_services_cash`; the credit per class
  `min(cents(labour × 20 %), cap)`.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_DOWN, Decimal
from uuid import UUID

from hausbuch.rules.errors import (
    RETURN_CHILDCARE_AGE,
    RETURN_HOUSEHOLD_SERVICES_CASH,
    RETURN_INPUT_SCOPE_INVALID,
    RuleError,
)
from hausbuch.rules.tax._common import unique, warning
from hausbuch.schemas.employment import (
    AnlageNRuleSet,
    OwnerEmployment,
    OwnerReturnFacts,
    ReturnInputFact,
)
from hausbuch.schemas.enums import ReturnInputKind as K
from hausbuch.schemas.performance import HouseholdTaxContext, Tariff
from hausbuch.schemas.tax import DraftWarning, SourceRef, WarningCode

from ._base import ZERO, cents, in_year, row_sources
from .draft import Itemised, itemise
from .vorsorge import vorsorge

CENT = Decimal("0.01")
_SERVICE_CLASSES = ("handwerker", "haushaltsnah", "minijob")


def employment_summary(
    facts: OwnerReturnFacts, tax_year: int, rs: AnlageNRuleSet, tariff: Tariff
) -> OwnerEmployment:
    """One owner's Anlage N summary for phase 4's zvE estimate. RuleError as `itemise`
    (anlage_n.no_pay when the owner has no pay source at all)."""
    item = itemise(facts, tax_year, rs, tariff)
    v = vorsorge(facts, tax_year, rs, tariff)
    return OwnerEmployment(
        owner_id=facts.owner_id,
        gross=item.gross,
        gross_source=item.gross_source,
        wk_applied=item.applied,
        vorsorge=v.deductible,
        sources=unique([*(ref for refs in item.line_sources.values() for ref in refs), *v.sources]),
    )


@dataclass
class HouseholdFigures:
    context: HouseholdTaxContext
    allowance_unused: Decimal = ZERO
    donated: Decimal = ZERO
    warnings: list[DraftWarning] = field(default_factory=list)


def household_figures(
    items: dict[UUID, Itemised],
    owner_facts: list[OwnerReturnFacts],
    household_rows: list[ReturnInputFact],
    gde: Decimal,
    tax_year: int,
    tariff: Tariff,
) -> HouseholdFigures:
    """The household context of `tax_year` from the owners' figures and the household rows
    (the module docstring). `items` holds each owner's `itemise` result."""
    warnings: list[DraftWarning] = []
    refs: list[SourceRef] = []
    benefits = unused_total = ZERO
    church_paid = ZERO
    for facts in owner_facts:
        item = items[facts.owner_id]
        year_rows = [r for r in in_year(facts.inputs, tax_year) if r.tax_year == tax_year]
        typed = [r for r in year_rows if r.kind is K.REPLACEMENT_INCOME]
        own = item.replacement_benefits + sum((r.value.amount for r in typed), ZERO)
        unused = min(own, max(ZERO, tariff.employee_allowance - item.gross))
        benefits += own - unused
        unused_total += unused
        certs = item.pay.certificates
        if own:
            refs += row_sources(typed)
            if item.replacement_benefits:
                refs += item.pay.sources()
        nr15 = {
            r.value.replacement_benefits
            for r in year_rows
            if r.kind is K.WAGE_CERTIFICATE and r.value.replacement_benefits > 0
        }
        if any(r.value.amount in nr15 for r in typed):
            warnings.append(warning(WarningCode.RETURN_BENEFIT_POSSIBLE_DOUBLE, row_sources(typed)))
        paid = sum((c.church_tax + c.church_tax_spouse for c in certs), ZERO)
        if paid:
            church_paid += paid
            refs += item.pay.sources()

    rows = in_year(household_rows, tax_year)
    of_year = [r for r in rows if r.tax_year == tax_year]

    donation_rows = [r for r in of_year if r.kind is K.SPECIAL_EXPENSE]
    donated = sum((r.value.amount for r in donation_rows), ZERO)
    cap = (tariff.donation_cap_share * max(ZERO, gde)).quantize(CENT, ROUND_DOWN)
    donations = min(donated, cap)
    if donated > cap:
        warnings.append(
            warning(
                WarningCode.RETURN_DONATION_OVER_CAP,
                row_sources(donation_rows),
                excess=str(donated - cap),
            )
        )
    refs += row_sources(donation_rows)

    child_rows = {r.id: r for r in rows if r.kind is K.CHILD}
    children = [r.value for r in child_rows.values()]
    care = tariff.childcare
    care_rows = [r for r in of_year if r.kind is K.CHILDCARE_COST]
    childcare = ZERO
    turning: set[UUID] = set()
    if care_rows:
        assert care is not None, "the tariff's childcare figures (D112)"
        for r in care_rows:
            child = child_rows.get(r.value.child_id)
            if child is None:
                raise RuleError(
                    RETURN_INPUT_SCOPE_INVALID, "a childcare cost names a child the household lacks"
                )
            born = child.value.born_on
            if born <= date(tax_year - care.max_age, 1, 1):
                raise RuleError(
                    RETURN_CHILDCARE_AGE,
                    f"the child was {care.max_age} before {tax_year} began (§10 Abs. 1 Nr. 5)",
                )
            if born.year == tax_year - care.max_age:
                turning.add(child.id)
            childcare += min(cents(r.value.amount * care.share), care.cap_per_child)
        refs += row_sources(care_rows)
    warnings += [
        warning(WarningCode.RETURN_CHILDCARE_UNTIL_14, row_sources([child_rows[c]]))
        for c in sorted(turning)
    ]
    refs += row_sources(child_rows.values())

    service_rows = [r for r in of_year if r.kind is K.HOUSEHOLD_SERVICES]
    if any(not r.value.paid_to_account for r in service_rows):
        raise RuleError(
            RETURN_HOUSEHOLD_SERVICES_CASH,
            "a §35a cost paid in cash does not count (§35a Abs. 5 S. 3)",
        )
    credit = ZERO
    if service_rows:
        caps = tariff.household_services
        assert caps is not None, "the tariff's §35a caps (D112)"
        for service_class in _SERVICE_CLASSES:
            labour = sum(
                (
                    r.value.labour_cost
                    for r in service_rows
                    if r.value.service_class == service_class
                ),
                ZERO,
            )
            credit += min(cents(labour * caps.rate), getattr(caps, service_class))
        refs += row_sources(service_rows)

    context = HouseholdTaxContext(
        children=children,
        replacement_benefits=benefits,
        church_tax_paid=church_paid,
        donations=donations,
        childcare=childcare,
        household_services_credit=credit,
        sources=unique(refs),
    )
    return HouseholdFigures(
        context=context, allowance_unused=unused_total, donated=donated, warnings=warnings
    )


def household_context(
    owner_facts: list[OwnerReturnFacts],
    household_rows: list[ReturnInputFact],
    tax_year: int,
    rs: AnlageNRuleSet,
    tariff: Tariff,
    *,
    rental_result: Decimal = ZERO,
) -> HouseholdTaxContext:
    """The household context for phase 4's tax effect (D98): `household_figures` with the GdE
    of the owners' Anlage N results plus `rental_result` (the Anlage V results of the year).
    Every owner given must have a pay source (RuleError anlage_n.no_pay otherwise)."""
    items = {f.owner_id: itemise(f, tax_year, rs, tariff) for f in owner_facts}
    gde = sum((i.result for i in items.values()), ZERO) + rental_result
    return household_figures(items, owner_facts, household_rows, gde, tax_year, tariff).context
