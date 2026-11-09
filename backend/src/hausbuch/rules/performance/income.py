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

Public signatures (phase 4 step 2 imports these; keep them stable):

    in_force(inputs) -> list[IncomeInputFact]
    default_deductions(gross, tariff) -> Decimal
    zve_base(year, inputs, owners, tariff, *, assessed_result=None) -> ZveEstimate | None
    owner_estimates(year, inputs, owners, tariff) -> list[OwnerZve]
    year_tax_effect(year, result, inputs, owners, tariff, *, assessed_result=None)
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

from hausbuch.rules.tax.rounding import cents
from hausbuch.rules.tax.tariff import tax_effect
from hausbuch.schemas.enums import IncomeInputKind
from hausbuch.schemas.performance import (
    BescheidZve,
    DeductionsOverride,
    ExpectedGrossPay,
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


def in_force(inputs: Iterable[IncomeInputFact]) -> list[IncomeInputFact]:
    """The rows neither withdrawn nor superseded by another row, in their order."""
    rows = list(inputs)
    superseded = {r.supersedes_id for r in rows if r.supersedes_id is not None}
    return [r for r in rows if not r.withdrawn and r.id not in superseded]


def default_deductions(gross: Decimal, tariff: Tariff) -> Decimal:
    """D84's default deductions of one owner on an expected gross pay, half-up to the cent:
    the two Pauschbeträge plus the employee's pension, health (× 0.96) and care shares up to
    the Beitragsbemessungsgrenzen. Unemployment insurance is not deducted."""
    si = tariff.social_insurance
    pension_base = min(gross, si.bbg_pension)
    health_base = min(gross, si.bbg_health)
    total = (
        tariff.employee_allowance
        + tariff.special_expenses_allowance
        + pension_base * si.pension_rate
        + health_base * (si.health_rate + si.health_extra_rate) * si.health_deductible_share
        + health_base * si.care_rate
    )
    return cents(total)


@dataclass(frozen=True)
class _YearRows:
    year: int  # the year whose rows apply (≤ the year asked)
    carried_forward: bool
    rows: list[IncomeInputFact]

    def last(self, kind: IncomeInputKind) -> IncomeInputFact | None:
        found = [r for r in self.rows if r.kind is kind]
        return found[-1] if found else None


def _year_rows(year: int, inputs: Iterable[IncomeInputFact]) -> _YearRows | None:
    current = in_force(inputs)
    years = [r.tax_year for r in current if r.tax_year <= year]
    if not years:
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
    rows: _YearRows, owners: Sequence[OwnerTaxProfile], tariff: Tariff
) -> tuple[list[OwnerZve], list[IncomeInputFact]]:
    """Each owner with an owner-scoped row: gross, deductions and the estimate; and the rows
    that went into it."""
    by_owner: dict[UUID, dict[IncomeInputKind, IncomeInputFact]] = {}
    for r in rows.rows:
        if r.kind in (K.EXPECTED_GROSS_PAY, K.DEDUCTIONS_OVERRIDE):
            assert r.owner_id is not None  # IncomeInputCreate's scope validator
            by_owner.setdefault(r.owner_id, {})[r.kind] = r
    order = [o.owner_id for o in owners if o.owner_id in by_owner]
    order += [owner_id for owner_id in by_owner if owner_id not in order]
    estimates: list[OwnerZve] = []
    used: list[IncomeInputFact] = []
    for owner_id in order:
        kinds = by_owner[owner_id]
        gross_row = kinds.get(K.EXPECTED_GROSS_PAY)
        override_row = kinds.get(K.DEDUCTIONS_OVERRIDE)
        gross = ZERO
        if gross_row is not None:
            assert isinstance(gross_row.value, ExpectedGrossPay)
            gross = gross_row.value.amount
            used.append(gross_row)
        if override_row is not None:
            assert isinstance(override_row.value, DeductionsOverride)
            deductions = override_row.value.amount
            deductions_source = "override"
            used.append(override_row)
        else:
            deductions = default_deductions(gross, tariff)
            deductions_source = "default"
        estimates.append(
            OwnerZve(
                owner_id=owner_id,
                gross=gross,
                deductions=deductions,
                deductions_source=deductions_source,
                amount=max(ZERO, cents(gross - deductions)),
            )
        )
    return estimates, used


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
    per_owner, used = _owner_estimates(rows, owners, tariff)
    if source == "override":
        used = [*used, _override_row(rows)]
    # None only under an override with nothing else entered for the year.
    amount = sum((o.amount for o in per_owner), ZERO) if per_owner else None
    return ZveEstimate(
        amount=amount,
        source=source,
        carried_forward=rows.carried_forward,
        tax_year=rows.year,
        per_owner=per_owner,
        sources=_refs(used),
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
) -> ZveEstimate | None:
    """zvE_base of `year` (D83, D84): the household's taxable income without the property, from
    the rows that apply to the year (see the module doc), or None when no row applies
    (income.missing). `tariff` is the year's (registry.tariff(year)); its deduction defaults
    are used for carried-forward rows too."""
    rows = _year_rows(year, inputs)
    if rows is None:
        return None
    return _estimate(rows, owners, tariff, assessed_result)


def owner_estimates(
    year: int,
    inputs: Iterable[IncomeInputFact],
    owners: Sequence[OwnerTaxProfile],
    tariff: Tariff,
) -> list[OwnerZve]:
    """Each owner's gross pay, deductions (default or override) and estimate from the rows
    that apply to `year`, whatever the year's source (for the Eigentümer page, which shows the
    computed default deductions under a Bescheid or an override too); [] when none apply."""
    rows = _year_rows(year, inputs)
    if rows is None:
        return []
    estimates, _ = _owner_estimates(rows, owners, tariff)
    return estimates


def year_tax_effect(
    year: int,
    result: Decimal,
    inputs: Iterable[IncomeInputFact],
    owners: Sequence[OwnerTaxProfile],
    tariff: Tariff,
    *,
    assessed_result: AssessedResult = None,
) -> tuple[ZveEstimate | None, TaxEffect | None]:
    """The household base of `year` and the tax effect of the property's result R on it, with
    D83's precedence: an override gives R × rate, else T(base + R) − T(base) on the Bescheid's
    or the estimated base. (None, None) when no row applies (income.missing: the screen shows
    "— · Einkommen eintragen"). The effect carries the base's source, carried_forward flag and
    rows, plus the tariff."""
    rows = _year_rows(year, inputs)
    if rows is None:
        return None, None
    estimate = _estimate(rows, owners, tariff, assessed_result)
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
    )
    return estimate, effect
