"""Actual years and the projection to the sale (docs/phases/phase-4.md §5.3, §5.4, D85–D89).

Pure: the rule sets and tariffs are parameters (by default the registry's, which reads the
packaged YAML files once). Nothing is stored.

- actual_year: one owned year from the real facts: the draft under the year's rule set (the
  latest earlier one for a year without a file, D86), its §11 allocations and the tax effect
  on its result (income.year_tax_effect, D83's precedence).
- project: the actual years (purchase year … today's year, for the AfA claimed and the cash
  flows of the sale) and every projected year (today.year + 1 … sale year): synthesized rows
  join the real facts, phase 3's draft gives each year's result and AfA, the tax effect uses
  the year's base (carried forward), value = today_value × (1 + value_growth)^n half-up,
  equity = value − the loans' balance. The sale on 31 Dec of the sale year closes it.

Drafts of projected years run with checklist, today, coverage and pending cleared, over every
synthesized row of every projected year (as phase 3 reads every year's allocations); their
warnings are dropped except `draft.form_provisional`, which becomes an assumption.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import date
from decimal import Decimal
from functools import cache
from uuid import UUID

from hausbuch.rules.tax import registry
from hausbuch.rules.tax._common import of_year
from hausbuch.rules.tax.draft import draft
from hausbuch.rules.tax.rounding import cents
from hausbuch.rules.tax.spekulation import tax_free_from
from hausbuch.schemas.enums import IncomeInputKind
from hausbuch.schemas.loan import LoanTerms
from hausbuch.schemas.performance import (
    Assumption,
    DatedFlow,
    IncomeInputFact,
    Levers,
    MarginalRateOverride,
    OwnerTaxProfile,
    Projection,
    Tariff,
    TariffStamp,
    YearFigures,
    YearKind,
)
from hausbuch.schemas.tax import (
    AnlageVDraft,
    DraftFacts,
    OpenLines,
    RuleSet,
    SourceRef,
    TaxOwner,
    WarningCode,
)

from .income import in_force, year_tax_effect
from .sale import SaleFacts, sale
from .synthesize import synthesize
from .year_figures import year_figures

ZERO = Decimal("0.00")
ONE = Decimal("1")
RuleSets = Callable[[int], RuleSet]
Tariffs = Callable[[int], Tariff]


def year_kind(year: int, today: date) -> YearKind:
    """The current year before 31 Dec is partial ("bisher"); earlier years are complete."""
    if year == today.year and today < date(year, 12, 31):
        return YearKind.ACTUAL_PARTIAL
    return YearKind.ACTUAL


class _Drafts:
    """The real facts' drafts, computed once per year (the actual years, and the result a
    Bescheid's zvE contains, D84)."""

    def __init__(self, facts: DraftFacts, rule_sets: RuleSets):
        self.facts = facts
        self.rule_sets = rule_sets
        self._get = cache(self._draft)

    def _draft(self, year: int) -> AnlageVDraft:
        return draft(self.facts, year, self.rule_sets(year))

    def __call__(self, year: int) -> AnlageVDraft:
        return self._get(year)

    def result(self, year: int) -> Decimal:
        """The property's result in a Bescheid's year. A year the property was not owned in
        (before the purchase, after the end of ownership) has none: its assessment contains
        nothing of the property, and there may be no rule set to draft it with."""
        prop = self.facts.property
        ended = prop.ownership_ended_on
        if year < prop.purchase_date.year or (ended is not None and year > ended.year):
            return ZERO
        return self(year).totals.result


def actual_year(
    facts: DraftFacts,
    year: int,
    today: date,
    owners: Sequence[OwnerTaxProfile],
    inputs: Sequence[IncomeInputFact],
    *,
    rule_sets: RuleSets = registry.rule_set_or_latest,
    tariffs: Tariffs = registry.tariff,
    drafts: _Drafts | None = None,
) -> YearFigures:
    """§5.3 for one year of the real facts (D85)."""
    drafts = drafts or _Drafts(facts, rule_sets)
    year_draft = drafts(year)
    _, effect = year_tax_effect(
        year,
        year_draft.totals.result,
        inputs,
        owners,
        tariffs(year),
        assessed_result=drafts.result,
    )
    return year_figures(
        year,
        year_kind(year, today),
        of_year(facts.allocations, year, rule_sets(year)),
        year_draft,
        effect,
        tenancies=facts.tenancies,
        purchase_year=facts.property.purchase_date.year,
    )


def actual_years(
    facts: DraftFacts,
    today: date,
    owners: Sequence[OwnerTaxProfile],
    inputs: Sequence[IncomeInputFact],
    *,
    rule_sets: RuleSets = registry.rule_set_or_latest,
    tariffs: Tariffs = registry.tariff,
) -> list[YearFigures]:
    """Every owned year from the purchase year through today's (the last one partial before
    31 Dec), oldest first; none before the purchase or after the end of ownership."""
    prop = facts.property
    last = today.year
    if prop.ownership_ended_on is not None:
        last = min(last, prop.ownership_ended_on.year)
    drafts = _Drafts(facts, rule_sets)
    return [
        actual_year(
            facts, y, today, owners, inputs, rule_sets=rule_sets, tariffs=tariffs, drafts=drafts
        )
        for y in range(prop.purchase_date.year, last + 1)
    ]


def _projected_facts(facts: DraftFacts, rows, tax_inputs) -> DraftFacts:
    return facts.model_copy(
        update={
            "allocations": [*facts.allocations, *rows],
            "tax_inputs": [*facts.tax_inputs, *tax_inputs],
            "checklist": None,
            "today": None,
            "coverage": [],
            "pending": OpenLines(count=0),
        }
    )


def _value(levers: Levers, purchase_price: Decimal, n: int) -> Decimal:
    base = levers.today_value if levers.today_value is not None else purchase_price
    return cents(base * (ONE + levers.value_growth) ** n)


def _shares(facts: DraftFacts, owners: Sequence[OwnerTaxProfile]) -> list[TaxOwner]:
    """The owners' shares of the property; equal shares of the profiles when it lists none."""
    if facts.owners:
        return list(facts.owners)
    return [
        TaxOwner(owner_id=o.owner_id, share={"numerator": 1, "denominator": len(owners)})
        for o in owners
    ]


def project(
    facts: DraftFacts,
    levers: Levers,
    owners: Sequence[OwnerTaxProfile],
    inputs: Sequence[IncomeInputFact],
    today: date,
    loans: Mapping[UUID, LoanTerms],
    *,
    scenario_id: UUID | None = None,
    rule_sets: RuleSets = registry.rule_set_or_latest,
    tariffs: Tariffs = registry.tariff,
) -> Projection:
    """§5.4 and the sale (§5.5). levers.sale_year must be after today's year (the caller
    refuses it otherwise: scenario.sale_year_before_today)."""
    if levers.sale_year <= today.year:
        raise ValueError("the sale year is after today's year")
    prop = facts.property
    drafts = _Drafts(facts, rule_sets)
    actual = [
        actual_year(
            facts, y, today, owners, inputs, rule_sets=rule_sets, tariffs=tariffs, drafts=drafts
        )
        for y in range(prop.purchase_date.year, today.year + 1)
    ]

    synthesis = synthesize(
        facts, levers, loans, today, scenario_id=scenario_id, rule_sets=rule_sets
    )
    projected_facts = _projected_facts(facts, synthesis.all_rows(), synthesis.tax_inputs)
    scenario_refs = [SourceRef(kind="scenario", id=scenario_id)] if scenario_id else []
    assumptions: list[Assumption] = [
        Assumption(code="sale.on_31_december", params={"year": str(levers.sale_year)}),
        Assumption(code="tax.no_child_allowances"),
    ]

    def ref_of(row) -> SourceRef:
        return synthesis.refs.get(row.allocation_id) or SourceRef(
            kind="allocation", id=row.allocation_id, amount=row.amount
        )

    years: list[YearFigures] = []
    year_drafts: dict[int, AnlageVDraft] = {}
    zve_bases: dict[int, tuple] = {}
    provisional: list[int] = []
    for year in range(today.year + 1, levers.sale_year + 1):
        n = year - today.year
        rule_set = rule_sets(year)
        year_draft = draft(projected_facts, year, rule_set)
        year_drafts[year] = year_draft
        if any(w.code is WarningCode.DRAFT_FORM_PROVISIONAL for w in year_draft.warnings):
            provisional.append(year)
        base, effect = year_tax_effect(
            year,
            year_draft.totals.result,
            inputs,
            owners,
            tariffs(year),
            assessed_result=drafts.result,
        )
        zve_bases[year] = (base, effect)
        value = _value(levers, prop.purchase_price, n)
        figures = year_figures(
            year,
            YearKind.PROJECTED,
            of_year(projected_facts.allocations, year, rule_set),
            year_draft,
            effect,
            tenancies=facts.tenancies,
            purchase_year=prop.purchase_date.year,
            value=value,
            loan_balance=synthesis.balances.get(year, ZERO),
            extra_sources=scenario_refs,
            ref_of=ref_of,
        )
        years.append(figures)
    if provisional:
        assumptions.append(
            Assumption(
                code="draft.form_provisional",
                params={"years": ",".join(str(y) for y in provisional)},
            )
        )

    sale_year = levers.sale_year
    last = years[-1]
    sale_rule_set = rule_sets(sale_year)
    sale_tariff = tariffs(sale_year)
    if sale_tariff.reused_from is not None:
        assumptions.append(
            Assumption(
                code="tariff.reused",
                params={"year": str(sale_year), "from": str(sale_tariff.reused_from)},
            )
        )
    free_from = None
    if prop.notarized_on is not None:
        free_from = tax_free_from(prop.notarized_on, sale_rule_set)
    else:
        assumptions.append(Assumption(code="sale.no_contract_date"))
    base, effect = zve_bases[sale_year]
    override = None
    if effect is not None and effect.source == "override" and effect.breakdown_with is None:
        override = _override_rate(inputs, sale_year)
    if effect is None:
        assumptions.append(Assumption(code="sale.tax_without_income"))

    cash_flows = [
        DatedFlow(
            on=date(y.year, 12, 31),
            amount=y.net_cash,
            kind="cash_flow",
            sources=[SourceRef(kind="property", id=prop.id)],
        )
        for y in [*actual, *years]
    ]
    afa_claimed = sum((y.afa or ZERO for y in [*actual, *years]), ZERO)
    sale_afa = year_drafts[sale_year].afa
    additions = sale_afa.additions if sale_afa is not None else ZERO
    result = sale(
        SaleFacts(
            sale_year=sale_year,
            purchase_date=prop.purchase_date,
            tax_free_from=free_from,
            purchase_price=prop.purchase_price,
            cost_rows=sum((c.amount for c in facts.cost_rows), ZERO),
            loan_principal=sum((t.principal for t in loans.values()), ZERO),
            value=last.value or ZERO,
            selling_costs_rate=levers.selling_costs_rate,
            afa_claimed=afa_claimed,
            loan_balance=last.loan_balance or ZERO,
            zve_base=base.amount if base is not None else None,
            result_sale_year=last.taxable_result or ZERO,
            shares=_shares(facts, owners),
            profiles=owners,
            cash_flows=cash_flows,
            basis_additions=additions,
            override_rate=override,
            value_sources=[*scenario_refs, SourceRef(kind="property", id=prop.id)],
            balance_sources=[SourceRef(kind="loan", id=loan_id) for loan_id in loans],
            tax_sources=[*(effect.sources if effect is not None else [])],
        ),
        sale_tariff,
    )
    sale_draft = year_drafts[sale_year]
    return Projection(
        property_id=prop.id,
        scenario=levers,
        scenario_id=scenario_id,
        tariff=TariffStamp(
            tax_year=sale_tariff.tax_year,
            version=sale_tariff.version,
            reused_from=sale_tariff.reused_from,
        ),
        rule_set=sale_draft.rule_set,
        years=years,
        sale=result,
        assumptions=assumptions,
    )


def _override_rate(inputs: Sequence[IncomeInputFact], year: int) -> Decimal | None:
    """The marginal_rate_override that applies to `year` (D83: the latest year ≤ `year` with
    rows in force), as income.year_tax_effect used it."""
    rows = [r for r in in_force(inputs) if r.tax_year <= year]
    if not rows:
        return None
    used = max(r.tax_year for r in rows)
    overrides = [
        r for r in rows if r.tax_year == used and r.kind is IncomeInputKind.MARGINAL_RATE_OVERRIDE
    ]
    if not overrides:
        return None
    value = overrides[-1].value
    assert isinstance(value, MarginalRateOverride)
    return value.rate
