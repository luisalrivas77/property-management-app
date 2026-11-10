"""The assessed tax (§5.10) and the whole-return estimate (§5.8, D101, D102, D109, D112).

Pure functions, no I/O, Decimal throughout; nothing here calls a model (principle 1).

`assessed_tax(zve, household, owners, tariff)`: z = floor(zvE).
- `est(z)` = income_tax(z), or with Lohnersatzleistungen L > 0 the §32b special rate:
  `rate = income_tax(z + L) ÷ (z + L)` cut to six decimals (H 32b EStH: four of the percent),
  `est = floor(z × rate)` (D102).
- Children (D109): months per child, `kindergeld = 259 × months`, `allowances = (3,414 + 1,464)
  × 2 × months ÷ 12` to the cent; `est_with = est(max(0, floor(z − allowances)))`. The
  allowances win only if `est_without − est_with > kindergeld` (§31 S. 4: then the Kindergeld
  is added back), so `est = est_with + kindergeld`, else `est = est_without`.
- §35a: `credit = min(household_services_credit, est)`; `est_final = est − credit`.
- Soli and church tax on `max(0, est_with − credit)` (§51a Abs. 2 S. 1, §3 Abs. 2 SolZG: the
  tax as if the Freibeträge applied, after the credit).
- Without a context it equals `tariff.total_tax` (test_tariff.py asserts it).

`return_estimate(...)`: total income (GdE) = Σ Anlage N results + Σ Anlage V results;
Sonderausgaben = Σ Vorsorge + max(72, church tax paid + donations within the cap) + childcare;
zvE = floor(GdE − Sonderausgaben); withheld = Σ Nr. 4–7 over the certificates (real and
projected); result = assessed.total − withheld.total (negative is a refund); the §46 Abs. 2
filing check with German reasons and the deadline (31 July of the next year, moved to the next
working day, §108 Abs. 3 AO; nationwide holidays only).
"""

from collections.abc import Sequence
from datetime import date
from decimal import ROUND_DOWN, Decimal
from uuid import UUID

from hausbuch.rules.errors import ANLAGE_N_NO_PAY, RuleError
from hausbuch.rules.tax._common import unique, warning
from hausbuch.rules.tax.anlage_n._base import ZERO, floor_euros, in_year
from hausbuch.rules.tax.anlage_n.children import child_months, children_months
from hausbuch.rules.tax.anlage_n.draft import RULE_SET_SOURCE, TARIFF_SOURCE, Itemised, itemise
from hausbuch.rules.tax.anlage_n.summary import household_figures
from hausbuch.rules.tax.anlage_n.vorsorge import vorsorge
from hausbuch.rules.tax.tariff import church_tax, income_tax, soli
from hausbuch.schemas.employment import (
    AnlageNRuleSet,
    AssessedTax,
    ChildrenResult,
    FilingCheck,
    MissingItem,
    OwnerIncome,
    OwnerReturnFacts,
    ProgressionInfo,
    PropertyIncome,
    QuestionnaireAnswer,
    QuestionnaireProgress,
    ReturnEstimate,
    ReturnInputFact,
    VorsorgeResult,
    WageCertificate,
    Withheld,
)
from hausbuch.schemas.enums import ReturnInputKind as K
from hausbuch.schemas.performance import (
    HouseholdTaxContext,
    OwnerTaxProfile,
    Tariff,
    TariffStamp,
)
from hausbuch.schemas.tax import DraftWarning, SourceRef, WarningCode

_RATE_PLACES = Decimal("0.000001")


def special_rate(z: Decimal, benefits: Decimal, tariff: Tariff) -> Decimal:
    """D102: income_tax(z + L) ÷ (z + L), cut to six decimals; 0 when z + L ≤ 0."""
    fictitious = z + benefits
    if fictitious <= 0:
        return ZERO
    return (income_tax(fictitious, tariff) / fictitious).quantize(_RATE_PLACES, ROUND_DOWN)


def _est(z: Decimal, benefits: Decimal, tariff: Tariff) -> Decimal:
    if benefits > 0:
        return floor_euros(z * special_rate(z, benefits, tariff))
    return income_tax(z, tariff)


def assessed_tax_with_warnings(
    zve: Decimal,
    household: HouseholdTaxContext | None,
    owners: Sequence[OwnerTaxProfile],
    tariff: Tariff,
    year: int | None = None,
    *,
    allowance_unused: Decimal = ZERO,
) -> tuple[AssessedTax, list[DraftWarning]]:
    """`assessed_tax` and the children's warnings (return.child_over_18,
    return.kindergeld_apply). `year` defaults to the tariff's year (a reused tariff carries
    the year asked for); `allowance_unused` is shown in the progression info only."""
    year = tariff.tax_year if year is None else year
    z = floor_euros(zve)
    h = household or HouseholdTaxContext()
    benefits = h.replacement_benefits
    est_without = _est(z, benefits, tariff)
    est_with, after, est = est_without, z, est_without
    children = None
    warnings: list[DraftWarning] = []
    if h.children:
        figures = tariff.children
        assert figures is not None, "the tariff's child figures (D109)"
        kids = children_months(h.children, year, figures)
        warnings += kids.warnings
        if kids.months:
            after = max(ZERO, floor_euros(z - kids.allowances))
            est_with = _est(after, benefits, tariff)
            saving = est_without - est_with
            favourable = "allowances" if saving > kids.kindergeld else "kindergeld"
            est = est_with + kids.kindergeld if favourable == "allowances" else est_without
            children = ChildrenResult(
                months=kids.months,
                kindergeld=kids.kindergeld,
                allowances=kids.allowances,
                saving=saving,
                favourable=favourable,
            )
    credit = min(h.household_services_credit, est)
    base = max(ZERO, est_with - credit)
    s = soli(base, tariff)
    c = church_tax(base, owners, tariff)
    progression = None
    if benefits > 0:
        progression = ProgressionInfo(
            benefits=benefits,
            allowance_unused=allowance_unused,
            rate=special_rate(z, benefits, tariff),
            fictitious_zve=z + benefits,
            fictitious_tax=income_tax(z + benefits, tariff),
        )
    assessed = AssessedTax(
        zve=z,
        zve_after_allowances=after,
        est_without=est_without,
        est_with=est_with,
        est=est,
        credit_35a=credit,
        est_final=est - credit,
        progression=progression,
        children=children,
        soli=s,
        church=c,
        total=est - credit + s + c,
    )
    return assessed, warnings


def assessed_tax(
    zve: Decimal,
    household: HouseholdTaxContext | None,
    owners: Sequence[OwnerTaxProfile],
    tariff: Tariff,
    year: int | None = None,
    *,
    allowance_unused: Decimal = ZERO,
) -> AssessedTax:
    """§5.10, the module docstring. Without a context: the plain tariff (= total_tax)."""
    return assessed_tax_with_warnings(
        zve, household, owners, tariff, year, allowance_unused=allowance_unused
    )[0]


_HOLIDAYS = {(1, 1), (5, 1), (10, 3), (12, 25), (12, 26)}  # nationwide, fixed dates


def filing_deadline(tax_year: int) -> date:
    """31 July of the following year (§149 Abs. 2 S. 1 AO), moved to the next working day when
    that is a Saturday, Sunday or a nationwide fixed public holiday (§108 Abs. 3 AO)."""
    d = date(tax_year + 1, 7, 31)
    while d.weekday() >= 5 or (d.month, d.day) in _HOLIDAYS:
        d = date.fromordinal(d.toordinal() + 1)
    return d


def de(x: Decimal) -> str:
    """1234.5 → "1.234,50" (an amount in a German reason sentence)."""
    return f"{x:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _filing(
    tax_year: int,
    rental: Decimal,
    benefits: Decimal,
    paid_owners: int,
    certs: list[WageCertificate],
    tariff: Tariff,
) -> FilingCheck:
    """§46 Abs. 2 EStG: Nr. 1 (income without Lohnsteuer above 410 €; the Anlage V result
    stands for it), Nr. 1 (Lohnersatzleistungen above 410 €), Nr. 3a (both spouses paid, one in
    class V or VI, or class IV with the Faktor). Otherwise an Antragsveranlagung."""
    threshold = tariff.filing_threshold_progression
    reasons = []
    if rental > threshold:
        reasons.append(f"Einkünfte ohne Lohnsteuerabzug {de(rental)} € > {de(threshold)} €")
    if benefits > threshold:
        reasons.append(f"Lohnersatzleistungen {de(benefits)} € > {de(threshold)} €")
    both_paid = paid_owners >= 2
    if both_paid and any(c.tax_class in (5, 6) for c in certs):
        reasons.append("Steuerklasse V oder VI")
    if both_paid and any(c.tax_class == 4 and c.tax_class_factor for c in certs):
        reasons.append("Steuerklasse IV mit Faktor")
    return FilingCheck(
        required=bool(reasons),
        reasons=reasons or ["Antragsveranlagung"],
        deadline=filing_deadline(tax_year),
    )


def _year_rows(facts: OwnerReturnFacts, tax_year: int) -> list[ReturnInputFact]:
    return [r for r in in_year(facts.inputs, tax_year) if r.tax_year == tax_year]


def return_estimate(
    tax_year: int,
    owners: Sequence[OwnerTaxProfile],
    owner_facts: Sequence[OwnerReturnFacts],
    household_rows: Sequence[ReturnInputFact],
    properties: Sequence[PropertyIncome],
    progress: QuestionnaireProgress,
    answers: Sequence[QuestionnaireAnswer],
    tariff: Tariff,
    rs: AnlageNRuleSet,
    *,
    reminders: Sequence[str] = (),
    not_modelled: Sequence[str] = (),
) -> ReturnEstimate:
    """The whole-return estimate of `tax_year` (the module docstring).

    `owner_facts` are the owners' current return_input rows (with their expected pay);
    `household_rows` the household's current rows (children of earlier years included);
    `properties` the Anlage V results of the year (phase 3's draft); `answers` the year's
    current questionnaire answers. An owner without any pay source counts with result 0 and
    status none. RuleError return.childcare_age, return.household_services_cash and those of
    the Anlage N rules."""
    items: dict[UUID, Itemised] = {}
    vorsorge_of: dict[UUID, VorsorgeResult] = {}
    income_n: list[OwnerIncome] = []
    for f in owner_facts:
        try:
            item = itemise(f, tax_year, rs, tariff)
        except RuleError as error:
            if error.code != ANLAGE_N_NO_PAY:
                raise
            income_n.append(OwnerIncome(owner_id=f.owner_id, result=ZERO, status="none"))
            continue
        items[f.owner_id] = item
        vorsorge_of[f.owner_id] = vorsorge(f, tax_year, rs, tariff)
        income_n.append(OwnerIncome(owner_id=f.owner_id, result=item.result, status=item.status))
    paid_facts = [f for f in owner_facts if f.owner_id in items]
    rental = sum((p.result for p in properties), ZERO)
    gde = sum((i.result for i in items.values()), ZERO) + rental

    figures = household_figures(items, paid_facts, list(household_rows), gde, tax_year, tariff)
    household = figures.context
    warnings = list(figures.warnings)

    vorsorge_total = sum((v.deductible for v in vorsorge_of.values()), ZERO)
    allowance = tariff.special_expenses_allowance * 2
    special = (
        vorsorge_total
        + max(allowance, household.church_tax_paid + household.donations)
        + household.childcare
    )
    zve = max(ZERO, floor_euros(gde - special))
    assessed, w = assessed_tax_with_warnings(
        zve, household, owners, tariff, tax_year, allowance_unused=figures.allowance_unused
    )
    warnings += w

    certs = [c for i in items.values() for c in i.pay.certificates]
    church_withheld = sum((c.church_tax + c.church_tax_spouse for c in certs), ZERO)
    income_withheld = sum((c.income_tax for c in certs), ZERO)
    soli_withheld = sum((c.soli for c in certs), ZERO)
    withheld = Withheld(
        income_tax=income_withheld,
        soli=soli_withheld,
        church_tax=church_withheld,
        total=income_withheld + soli_withheld + church_withheld,
    )

    paid_owners = sum(1 for i in items.values() if i.gross > 0)
    filing = _filing(tax_year, rental, household.replacement_benefits, paid_owners, certs, tariff)
    if filing.required:
        warnings.append(warning(WarningCode.RETURN_FILING_REQUIRED))

    missing = [
        MissingItem(kind="question", key=o.key, owner_id=o.owner_id, property_id=o.property_id)
        for o in progress.open
    ]
    if progress.open:
        warnings.append(
            warning(WarningCode.RETURN_QUESTIONNAIRE_OPEN, open=str(len(progress.open)))
        )
    for f in owner_facts:
        if f.owner_id in items and items[f.owner_id].gross_source != "certificate":
            missing.append(
                MissingItem(kind="document", key="wage_certificate", owner_id=f.owner_id)
            )
        typed = {r.value.benefit for r in _year_rows(f, tax_year) if r.kind is K.REPLACEMENT_INCOME}
        for a in answers:
            if (
                a.question_key == "owner.replacement_income"
                and a.owner_id == f.owner_id
                and a.answer == "yes"
                and a.choice not in (None, "none")
                and a.choice not in typed
            ):
                missing.append(
                    MissingItem(
                        kind="benefit_letter", key="replacement_income", owner_id=f.owner_id
                    )
                )

    counted = sum(1 for child in household.children if child_months(child, tax_year)[0] > 0)
    if any(c.child_allowance_count != counted for i in items.values() for c in i.pay.real):
        warnings.append(warning(WarningCode.RETURN_CHILDREN_MISMATCH))

    status = (
        "certified"
        if items and all(i.status == "certified" for i in items.values())
        else "estimate"
    )
    refs: list[SourceRef] = []
    for owner_id, item in items.items():
        refs += [ref for line in item.line_sources.values() for ref in line]
        refs += vorsorge_of[owner_id].sources
    refs += household.sources
    refs += [SourceRef(kind="property", id=p.property_id) for p in properties]
    refs += [RULE_SET_SOURCE, TARIFF_SOURCE]
    return ReturnEstimate(
        tax_year=tax_year,
        status=status,
        income_n=income_n,
        income_v=list(properties),
        total_income=gde,
        vorsorge_total=vorsorge_total,
        church_tax_paid=household.church_tax_paid,
        donations=household.donations,
        childcare=household.childcare,
        special_expenses=special,
        zve=zve,
        assessed=assessed,
        withheld=withheld,
        result=assessed.total - withheld.total,
        filing_required=filing,
        questionnaire=progress,
        missing=missing,
        not_modelled=list(not_modelled),
        reminders=list(reminders),
        warnings=warnings,
        tariff=TariffStamp(
            tax_year=tariff.tax_year, version=tariff.version, reused_from=tariff.reused_from
        ),
        sources=unique(refs),
    )
