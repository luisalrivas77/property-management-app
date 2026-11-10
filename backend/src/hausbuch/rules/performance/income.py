"""The household's taxable income without the property, and the tax effect of a year
(docs/phases/phase-4.md §5.2, D83, D84, §10.4).

Pure functions over the income_input rows (IncomeInputFact, the full history: superseded and
withdrawn rows included, the rule picks the rows in force itself), the owners' church tax
profiles and the year's tariff. No I/O, Decimal throughout.

Which rows count for a year Y (D83): the rows in force (not withdrawn, not superseded by any
other row) of the latest year ≤ Y that has any row in force at all; when that year is earlier
than Y the result is marked `carried_forward`. A later year's rows never count back; no such
year at all is `income.missing` (None). The rows of one year are taken together: a year with
only a rate override does not borrow an earlier year's gross pay.

The source of the year (D83): `override` when a marginal_rate_override is among the rows, else
`bescheid` when a bescheid_zve is, else `estimate`.

- estimate (D84): per owner max(0, gross − deductions), deductions = the owner's
  deductions_override, else the default: Arbeitnehmer-Pauschbetrag + Sonderausgaben-
  Pauschbetrag + min(gross, BBG RV) × pension rate + min(gross, BBG KV) × (health + extra
  rate) × 0.96 + min(gross, BBG KV) × care rate, summed and rounded half-up to the cent once.
  Unemployment insurance is never deducted (D84). The household base is the owners' sum.
  per_owner lists the owners that have an owner-scoped row, in the order of `owners` (owners
  not in that list after them, in row order).
- bescheid: base = the Bescheid's joint zvE − R_assessed (the property's result of the
  Bescheid's year, which the assessed zvE contains), floored at 0; per_owner is empty.
- override: the effect is R × rate (rules/tax/tariff.tax_effect); the estimate's amount is the
  Bescheid's base when a Bescheid row is there too, else the owners' estimate, else None.

When two rows in force answer the same question (same kind, year and owner; the service
refuses that), the later one in the list wins.

Phase 4b D98 (`employment`, `household`, both optional; built by rules/tax/anlage_n/summary
from the return_input rows of the year):
- gross per owner: the Anlage N summary's (a certificate, else the payslip projection) when it
  has one, else the expected_gross_pay row; an owner with a summary needs no income_input row.
  A summary whose gross is itself the expected pay (no certificate, no payslip) is not used:
  its Vorsorge would be 0, and D84's default estimates the social insurance instead.
- deductions: a deductions_override row first (the user's figure always wins), else the
  summary's wk_applied + vorsorge + the Sonderausgaben-Pauschbetrag (36), else D84's default.
- household: the Sonderausgaben beyond Vorsorge become max(2 × 36, church tax paid +
  donations) + childcare instead of 36 per owner, so `household_deductions` = max(0, church +
  donations − 72) + childcare comes off the owners' sum; the tax effect is assessed_tax at both
  points (tariff.tax_effect). Only for an estimate: a Bescheid's zvE and a rate override keep
  their D83 precedence, and their effect is computed as before.

Public signatures (phase 4 step 2 imports these; keep them stable):

    in_force(inputs) -> list[IncomeInputFact]
    default_deductions(gross, tariff) -> Decimal
    zve_base(year, inputs, owners, tariff, *, assessed_result=None, employment=None,
             household=None) -> ZveEstimate | None
    owner_estimates(year, inputs, owners, tariff, *, employment=None) -> list[OwnerZve]
    year_tax_effect(year, result, inputs, owners, tariff, *, assessed_result=None,
                    employment=None, household=None)
        -> tuple[ZveEstimate | None, TaxEffect | None]

`assessed_result` is the property's taxable result of the Bescheid's year: a Decimal (used
for whichever year's rows apply; right when that is the year asked), or a callable taking
that year (for a Bescheid carried forward from an earlier year). It is needed only when a
bescheid_zve row applies; missing then, zve_base raises ValueError.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from hausbuch.rules.tax._common import unique
from hausbuch.rules.tax.rounding import cents
from hausbuch.rules.tax.tariff import tax_effect
from hausbuch.schemas.employment import OwnerEmployment
from hausbuch.schemas.enums import IncomeInputKind
from hausbuch.schemas.performance import (
    BescheidZve,
    DeductionsOverride,
    ExpectedGrossPay,
    HouseholdTaxContext,
    IncomeInputFact,
    MarginalRateOverride,
    OwnerTaxProfile,
    OwnerZve,
    RateSource,
    Tariff,
    TaxEffect,
    ZveEstimate,
)
from hausbuch.schemas.tax import SourceRef

ZERO = Decimal("0.00")
K = IncomeInputKind

AssessedResult = Decimal | Callable[[int], Decimal] | None
Employment = Sequence[OwnerEmployment] | None


def in_force(inputs: Iterable[IncomeInputFact]) -> list[IncomeInputFact]:
    """The rows neither withdrawn nor superseded by another row, in their order."""
    rows = list(inputs)
    superseded = {r.supersedes_id for r in rows if r.supersedes_id is not None}
    return [r for r in rows if not r.withdrawn and r.id not in superseded]


def _employee_shares(gross: Decimal, tariff: Tariff) -> tuple[Decimal, Decimal]:
    """The employee's pension share and health (× 0.96) plus care shares on `gross`, each up
    to its Beitragsbemessungsgrenze, unrounded."""
    si = tariff.social_insurance
    pension_base = min(gross, si.bbg_pension)
    health_base = min(gross, si.bbg_health)
    pension = pension_base * si.pension_rate
    health_care = (
        health_base * (si.health_rate + si.health_extra_rate) * si.health_deductible_share
        + health_base * si.care_rate
    )
    return pension, health_care


def default_deductions(gross: Decimal, tariff: Tariff) -> Decimal:
    """D84's default deductions of one owner on an expected gross pay, half-up to the cent:
    the two Pauschbeträge plus the employee's pension, health (× 0.96) and care shares up to
    the Beitragsbemessungsgrenzen. Unemployment insurance is not deducted."""
    pension, health_care = _employee_shares(gross, tariff)
    return cents(
        tariff.employee_allowance + tariff.special_expenses_allowance + pension + health_care
    )


def default_vorsorge(gross: Decimal, tariff: Tariff) -> tuple[Decimal, Decimal]:
    """The social-insurance part of D84's default deductions as (pension, health and care
    basis): default_deductions less the two Pauschbeträge, so the two always agree to the
    cent; pension half-up to the cent, the basis the rest. Phase 4b's whole-return estimate
    uses it as the Vorsorge of an owner with expected pay only (D113)."""
    total = (
        default_deductions(gross, tariff)
        - tariff.employee_allowance
        - tariff.special_expenses_allowance
    )
    pension = cents(_employee_shares(gross, tariff)[0])
    return pension, total - pension


def household_deductions(household: HouseholdTaxContext, tariff: Tariff) -> Decimal:
    """D98: the household's Sonderausgaben beyond the 36 per owner already in the owners'
    deductions: max(0, church tax paid + donations − 2 × 36) + childcare."""
    paid = household.church_tax_paid + household.donations
    return cents(max(ZERO, paid - 2 * tariff.special_expenses_allowance) + household.childcare)


def _usable(employment: Employment) -> dict[UUID, OwnerEmployment]:
    """The owners' Anlage N summaries D98 uses: those whose gross is a certificate's or the
    payslip projection's (the module doc)."""
    return {e.owner_id: e for e in employment or () if e.gross_source != "expected"}


@dataclass(frozen=True)
class _YearRows:
    year: int  # the year whose rows apply (≤ the year asked)
    carried_forward: bool
    rows: list[IncomeInputFact]

    def last(self, kind: IncomeInputKind) -> IncomeInputFact | None:
        found = [r for r in self.rows if r.kind is kind]
        return found[-1] if found else None


def _year_rows(
    year: int, inputs: Iterable[IncomeInputFact], employment: Employment = None
) -> _YearRows | None:
    current = in_force(inputs)
    years = [r.tax_year for r in current if r.tax_year <= year]
    if not years:
        if _usable(employment):  # D98: the year's Anlage N summaries stand on their own
            return _YearRows(year=year, carried_forward=False, rows=[])
        return None
    used = max(years)
    rows = [r for r in current if r.tax_year == used]
    return _YearRows(year=used, carried_forward=used != year, rows=rows)


def _source(rows: _YearRows) -> RateSource:
    if rows.last(K.MARGINAL_RATE_OVERRIDE) is not None:
        return "override"
    if rows.last(K.BESCHEID_ZVE) is not None:
        return "bescheid"
    return "estimate"


def _owner_estimates(
    rows: _YearRows,
    owners: Sequence[OwnerTaxProfile],
    tariff: Tariff,
    employment: Employment = None,
) -> tuple[list[OwnerZve], list[IncomeInputFact], list[SourceRef]]:
    """Each owner with an owner-scoped row or an Anlage N summary: gross, deductions and the
    estimate; the rows that went into it; and the summaries' sources (D98)."""
    summaries = _usable(employment)
    by_owner: dict[UUID, dict[IncomeInputKind, IncomeInputFact]] = {}
    for r in rows.rows:
        if r.kind in (K.EXPECTED_GROSS_PAY, K.DEDUCTIONS_OVERRIDE):
            assert r.owner_id is not None  # IncomeInputCreate's scope validator
            by_owner.setdefault(r.owner_id, {})[r.kind] = r
    for owner_id in summaries:
        by_owner.setdefault(owner_id, {})
    order = [o.owner_id for o in owners if o.owner_id in by_owner]
    order += [owner_id for owner_id in by_owner if owner_id not in order]
    estimates: list[OwnerZve] = []
    used: list[IncomeInputFact] = []
    refs: list[SourceRef] = []
    for owner_id in order:
        kinds = by_owner[owner_id]
        summary = summaries.get(owner_id)
        gross_row = kinds.get(K.EXPECTED_GROSS_PAY)
        override_row = kinds.get(K.DEDUCTIONS_OVERRIDE)
        gross = ZERO
        gross_source = "expected"
        if gross_row is not None:
            # listed even when a summary replaces it: the precedence decision read it
            assert isinstance(gross_row.value, ExpectedGrossPay)
            gross = gross_row.value.amount
            used.append(gross_row)
        if summary is not None:
            gross, gross_source = summary.gross, summary.gross_source
            refs += summary.sources
        if override_row is not None:
            assert isinstance(override_row.value, DeductionsOverride)
            deductions = override_row.value.amount
            deductions_source = "override"
            used.append(override_row)
        elif summary is not None:
            deductions = cents(
                summary.wk_applied + summary.vorsorge + tariff.special_expenses_allowance
            )
            deductions_source = "employment"
        else:
            deductions = default_deductions(gross, tariff)
            deductions_source = "default"
        estimates.append(
            OwnerZve(
                owner_id=owner_id,
                gross=gross,
                gross_source=gross_source,
                deductions=deductions,
                deductions_source=deductions_source,
                amount=max(ZERO, cents(gross - deductions)),
            )
        )
    return estimates, used, refs


def _assessed(assessed_result: AssessedResult, year: int) -> Decimal:
    if assessed_result is None:
        raise ValueError(f"a Bescheid's zvE of {year} needs the property's assessed result")
    if callable(assessed_result):
        return assessed_result(year)
    return assessed_result


def _refs(rows: Iterable[IncomeInputFact]) -> list[SourceRef]:
    return [SourceRef(kind="income_input", id=r.id) for r in rows]


def _estimate(
    rows: _YearRows,
    owners: Sequence[OwnerTaxProfile],
    tariff: Tariff,
    assessed_result: AssessedResult,
    employment: Employment = None,
    household: HouseholdTaxContext | None = None,
) -> ZveEstimate:
    source = _source(rows)
    bescheid = rows.last(K.BESCHEID_ZVE)
    if bescheid is not None:
        assert isinstance(bescheid.value, BescheidZve)
        base = bescheid.value.amount - _assessed(assessed_result, rows.year)
        used = [bescheid]
        if source == "override":
            used.append(_override_row(rows))
        return ZveEstimate(
            amount=max(ZERO, cents(base)),
            source=source,
            carried_forward=rows.carried_forward,
            tax_year=rows.year,
            per_owner=[],
            sources=_refs(used),
        )
    per_owner, used, employment_refs = _owner_estimates(rows, owners, tariff, employment)
    if source == "override":
        used = [*used, _override_row(rows)]
    context = household if source == "estimate" and per_owner else None
    extra = household_deductions(context, tariff) if context is not None else ZERO
    # None only under an override with nothing else entered for the year.
    amount = None
    if per_owner:
        amount = max(ZERO, sum((o.amount for o in per_owner), ZERO) - extra)
    refs = [*_refs(used), *employment_refs, *(context.sources if context is not None else ())]
    return ZveEstimate(
        amount=amount,
        source=source,
        carried_forward=rows.carried_forward,
        tax_year=rows.year,
        per_owner=per_owner,
        household=context,
        household_deductions=extra,
        sources=unique(refs),
    )


def _override_row(rows: _YearRows) -> IncomeInputFact:
    row = rows.last(K.MARGINAL_RATE_OVERRIDE)
    assert row is not None
    return row


def zve_base(
    year: int,
    inputs: Iterable[IncomeInputFact],
    owners: Sequence[OwnerTaxProfile],
    tariff: Tariff,
    *,
    assessed_result: AssessedResult = None,
    employment: Employment = None,
    household: HouseholdTaxContext | None = None,
) -> ZveEstimate | None:
    """zvE_base of `year` (D83, D84): the household's taxable income without the property, from
    the rows that apply to the year (see the module doc), or None when no row applies
    (income.missing). `tariff` is the year's (registry.tariff(year)); its deduction defaults
    are used for carried-forward rows too. `employment` and `household`: phase 4b D98."""
    rows = _year_rows(year, inputs, employment)
    if rows is None:
        return None
    return _estimate(rows, owners, tariff, assessed_result, employment, household)


def owner_estimates(
    year: int,
    inputs: Iterable[IncomeInputFact],
    owners: Sequence[OwnerTaxProfile],
    tariff: Tariff,
    *,
    employment: Employment = None,
) -> list[OwnerZve]:
    """Each owner's gross pay, deductions (default, override or, D98, the Anlage N summary) and
    estimate from the rows that apply to `year`, whatever the year's source (for the Eigentümer
    page, which shows the computed default deductions under a Bescheid or an override too); []
    when none apply."""
    rows = _year_rows(year, inputs, employment)
    if rows is None:
        return []
    estimates, _, _ = _owner_estimates(rows, owners, tariff, employment)
    return estimates


def year_tax_effect(
    year: int,
    result: Decimal,
    inputs: Iterable[IncomeInputFact],
    owners: Sequence[OwnerTaxProfile],
    tariff: Tariff,
    *,
    assessed_result: AssessedResult = None,
    employment: Employment = None,
    household: HouseholdTaxContext | None = None,
) -> tuple[ZveEstimate | None, TaxEffect | None]:
    """The household base of `year` and the tax effect of the property's result R on it, with
    D83's precedence: an override gives R × rate, else T(base + R) − T(base) on the Bescheid's
    or the estimated base. (None, None) when no row applies (income.missing: the screen shows
    "— · Einkommen eintragen"). The effect carries the base's source, carried_forward flag and
    rows, plus the tariff. With a household context on an estimate, T is the assessed tax at
    both points (phase 4b D98)."""
    rows = _year_rows(year, inputs, employment)
    if rows is None:
        return None, None
    estimate = _estimate(rows, owners, tariff, assessed_result, employment, household)
    override_rate = None
    if estimate.source == "override":
        value = _override_row(rows).value
        assert isinstance(value, MarginalRateOverride)
        override_rate = value.rate
    effect = tax_effect(
        estimate.amount,
        result,
        tariff,
        owners,
        source=estimate.source,
        carried_forward=estimate.carried_forward,
        override_rate=override_rate,
        sources=estimate.sources,
        household=estimate.household,
    )
    return estimate, effect
