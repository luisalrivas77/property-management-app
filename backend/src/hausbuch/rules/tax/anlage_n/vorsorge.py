"""§5.7: the Vorsorgeaufwendungen of one owner and year.

- Altersvorsorge (§10 Abs. 1 Nr. 2, Abs. 3): `min(Nr. 23a + 23b + 22a, Höchstbetrag) − Nr. 22a`,
  floored at 0 (the tax-free employer share comes off, 100 %).
- Basis KV/PV (Nr. 3): `cents(Nr. 25 × 0.96) + Nr. 26` (the 4 % cut for Krankengeld; the share
  is the tariff's `social_insurance.health_deductible_share`).
- Other (Nr. 3a): `max(basis, min(1,900, basis + Nr. 27 + other insurance)) − basis` (Abs. 4
  S. 4: the basis always counts, the rest only fills the cap).

A `vorsorge_override` row replaces the three figures as typed (a privately insured owner, a
civil servant). The certificates are the year's real ones plus the payslip projection. An owner
with neither, but with an expected gross pay, gets phase 4's D84 default shares
(`income.default_vorsorge`, source `expected`, D113), so the estimate and the Steuerwirkung
deduct the same; a certificate or payslip always wins.
"""

from hausbuch.rules.performance.income import default_vorsorge
from hausbuch.schemas.employment import (
    AnlageNRuleSet,
    OwnerReturnFacts,
    VorsorgeResult,
    WageCertificate,
)
from hausbuch.schemas.enums import ReturnInputKind as K
from hausbuch.schemas.performance import Tariff
from hausbuch.schemas.tax import SourceRef

from ._base import ZERO, cents, in_year, row_sources
from .payslip import pay_of_rows


def _total(certs: list[WageCertificate], name: str):
    return sum((getattr(c, name) for c in certs), ZERO)


def vorsorge(
    facts: OwnerReturnFacts, tax_year: int, rs: AnlageNRuleSet, tariff: Tariff
) -> VorsorgeResult:
    rows = [r for r in in_year(facts.inputs, tax_year) if r.tax_year == tax_year]
    overrides = [r for r in rows if r.kind is K.VORSORGE_OVERRIDE]
    if overrides:
        o = overrides[0].value
        return VorsorgeResult(
            owner_id=facts.owner_id,
            pension=o.pension,
            health_care_basis=o.health_care_basis,
            other=o.other,
            deductible=o.pension + o.health_care_basis + o.other,
            source="override",
            sources=row_sources(overrides[:1]),
        )
    pay = pay_of_rows(rows, tax_year)
    certs = pay.certificates
    expected = facts.expected_gross_pay
    if not certs and expected is not None:
        # D113: expected pay only. Phase 4's D84 default (the employee's pension, health and
        # care shares), so the Gesamt card and the Steuerwirkung agree on the same inputs.
        pension, basis = default_vorsorge(expected.value.amount, tariff)
        return VorsorgeResult(
            owner_id=facts.owner_id,
            pension=pension,
            health_care_basis=basis,
            other=ZERO,
            deductible=pension + basis,
            source="expected",
            sources=[SourceRef(kind="income_input", id=expected.id)],
        )
    source = pay.gross_source or "none"
    contributions = (
        _total(certs, "pension_employee")
        + _total(certs, "pension_employee_professional")
        + _total(certs, "pension_employer")
    )
    pension = max(
        ZERO,
        min(contributions, rs.pension_max_contribution) - _total(certs, "pension_employer"),
    )
    share = tariff.social_insurance.health_deductible_share
    basis = cents(_total(certs, "health_employee") * share) + _total(certs, "care_employee")
    insurance = [r for r in rows if r.kind is K.OTHER_INSURANCE]
    other_raw = _total(certs, "unemployment_employee") + sum(
        (r.value.amount for r in insurance), ZERO
    )
    cap = rs.other_vorsorge_cap_employee
    other = max(basis, min(cap, basis + other_raw)) - basis
    return VorsorgeResult(
        owner_id=facts.owner_id,
        pension=pension,
        health_care_basis=basis,
        other=other,
        deductible=pension + basis + other,
        source=source,
        sources=row_sources(pay.real_rows + pay.projected_rows + insurance),
    )
