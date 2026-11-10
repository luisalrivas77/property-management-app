"""The income tax tariff and the tax effect of a rental result (docs/phases/phase-4.md §5.1,
D81, D82, §10.4).

Pure functions over a `Tariff` (rules/tax/tariffs/<year>.yaml, loaded by registry.tariff); no
I/O, no model, Decimal throughout. `T(zvE)` = Einkommensteuer (§32a EStG, splitting) + Soli
(§§3, 4 SolZG) + church tax (§51a EStG), and the tax effect of a result R is
`T(zvE_base + R) − T(zvE_base)` (D82), never `R × rate`, except under a typed rate override.

Rounding (fixture tariff.yaml, marginal_rate.yaml):
- Einkommensteuer: the zvE is floored to whole euros, under splitting its half too (Q-O1,
  provisional until phase 4 step 4), and the tax of each zone is floored to whole euros
  (§32a Abs. 1 S. 1, S. 6). A zvE below the Grundfreibetrag, or negative, is taxed 0.
- Soli: 0 up to the Freigrenze; else min(5.5 % × ESt, 11.9 % × (ESt − Freigrenze)), floored
  to the cent (§4 S. 3 SolZG: fractions of a cent are dropped).
- Church tax: per owner in a church, ESt ÷ 2 × the owner's state rate, half-up to the cent,
  the halves summed (an approximation with one member, Q-O6). Not joint: one owner, the whole
  ESt × the rate.
- Effective rate: effect ÷ R in percent, one decimal half-up, only when R ≠ 0; the slope is
  shown instead when R = 0 (§10.4). An override effect is R × rate, half-up to the cent.

Public signatures (phase 4 step 2 imports `total_tax` and `tax_effect`; keep them stable):

    income_tax(zve, tariff, joint=True) -> Decimal
    soli(est, tariff, joint=True) -> Decimal
    church_tax(est, owners, tariff, joint=True) -> Decimal
    total_tax(zve, tariff, owners, joint=True) -> TaxBreakdown
    slope(zve_base, tariff, owners, joint=True) -> Decimal            # percent, one decimal
    tax_effect(zve_base, result, tariff, owners, *, joint=True, source="estimate",
               carried_forward=False, override_rate=None, sources=(), household=None)
        -> TaxEffect

Phase 4b D98: with a `household` context (children, wage-replacement benefits, the §35a
credit) both points are the assessed tax (rules/tax/return_estimate.assessed_tax: the
Progressionsvorbehalt, the Kindergeld/Freibetrag Günstigerprüfung, §35a, Soli and church tax
on the tax after the Freibeträge) instead of total_tax; without one nothing changes.
"""

from collections.abc import Iterable, Sequence
from decimal import ROUND_FLOOR, Decimal

from hausbuch.schemas.performance import (
    HouseholdTaxContext,
    OwnerTaxProfile,
    RateSource,
    Tariff,
    TariffZone,
    TaxBreakdown,
    TaxEffect,
)
from hausbuch.schemas.tax import SourceRef

from .rounding import CENT, cents, one_decimal

ZERO = Decimal("0")
HUNDRED = Decimal("100")
_TEN_THOUSAND = Decimal("10000")


def _floor_euros(value: Decimal) -> Decimal:
    return value.to_integral_value(rounding=ROUND_FLOOR)


def _floor_cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_FLOOR) + 0


def _zone(x: Decimal, tariff: Tariff) -> TariffZone:
    """The §32a zone of a whole-euro zvE x (the open last zone for anything above)."""
    return next(z for z in tariff.zones if z.up_to is None or x <= z.up_to)


def _single_tax(x: Decimal, tariff: Tariff) -> Decimal:
    """§32a Abs. 1 for one person's whole-euro zvE x, floored to whole euros; 0 for x ≤ 0."""
    if x <= 0:
        return ZERO
    zone = _zone(x, tariff)
    if zone.kind == "zero":
        return ZERO
    if zone.kind == "linear":
        assert zone.rate is not None
        tax = zone.rate * x - zone.c
    else:
        assert zone.a is not None and zone.b is not None and zone.offset is not None
        v = (x - zone.offset) / _TEN_THOUSAND
        tax = (zone.a * v + zone.b) * v + zone.c
    return max(_floor_euros(tax), ZERO)


def income_tax(zve: Decimal, tariff: Tariff, joint: bool = True) -> Decimal:
    """Einkommensteuer on a zvE: the Grundtabelle, or under splitting (joint, §32a Abs. 5)
    twice the tax on the floored half of the floored joint zvE. Whole euros, never negative."""
    x = _floor_euros(zve)
    if joint:
        return 2 * _single_tax(_floor_euros(x / 2), tariff)
    return _single_tax(x, tariff)


def _soli_limit(tariff: Tariff, joint: bool) -> Decimal:
    return tariff.soli_free_limit_joint if joint else tariff.soli_free_limit_single


def _soli_mitigated(est: Decimal, tariff: Tariff, joint: bool) -> bool:
    """In the Milderungszone: 11.9 % of the ESt above the Freigrenze is below 5.5 % of it."""
    excess = (est - _soli_limit(tariff, joint)) * tariff.soli_mitigation_rate
    return excess < est * tariff.soli_rate


def soli(est: Decimal, tariff: Tariff, joint: bool = True) -> Decimal:
    """Solidaritätszuschlag on an Einkommensteuer (§§3, 4 SolZG): 0 up to the Freigrenze, else
    the smaller of the full rate and the Milderungszone, floored to the cent."""
    limit = _soli_limit(tariff, joint)
    if est <= limit:
        return Decimal("0.00")
    full = est * tariff.soli_rate
    mitigated = (est - limit) * tariff.soli_mitigation_rate
    return _floor_cents(min(full, mitigated))


def _church_share(joint: bool) -> Decimal:
    return Decimal("0.5") if joint else Decimal("1")


def church_tax(
    est: Decimal, owners: Sequence[OwnerTaxProfile], tariff: Tariff, joint: bool = True
) -> Decimal:
    """Kirchensteuer on an Einkommensteuer: each owner with church_tax pays their state's rate
    on half the joint ESt (the whole ESt when not joint), half-up to the cent; summed."""
    share = _church_share(joint)
    total = Decimal("0.00")
    for owner in owners:
        if owner.church_tax:
            assert owner.tax_state is not None  # OwnerTaxProfile's validator
            total += cents(est * share * tariff.church_tax_rates[owner.tax_state])
    return total


def total_tax(
    zve: Decimal, tariff: Tariff, owners: Sequence[OwnerTaxProfile], joint: bool = True
) -> TaxBreakdown:
    """T(zvE) of D82: Einkommensteuer, Soli and church tax, and their sum."""
    est = income_tax(zve, tariff, joint)
    s = soli(est, tariff, joint)
    church = church_tax(est, owners, tariff, joint)
    return TaxBreakdown(
        est=cents(est), soli=cents(s), church=cents(church), total=cents(est + s + church)
    )


def _zone_derivative(x: Decimal, tariff: Tariff) -> Decimal:
    """d tax / d x of one person's §32a zone at the whole-euro zvE x (a fraction)."""
    if x <= 0:
        return ZERO
    zone = _zone(x, tariff)
    if zone.kind == "zero":
        return ZERO
    if zone.kind == "linear":
        assert zone.rate is not None
        return zone.rate
    assert zone.a is not None and zone.b is not None and zone.offset is not None
    v = (x - zone.offset) / _TEN_THOUSAND
    return (2 * zone.a * v + zone.b) / _TEN_THOUSAND


def slope(
    zve_base: Decimal, tariff: Tariff, owners: Sequence[OwnerTaxProfile], joint: bool = True
) -> Decimal:
    """The tariff's slope at zve_base, for display when R = 0 (§5.1), in percent to one
    decimal: the §32a zone's derivative at the floored half of the base (the floored base when
    not joint) × (1 + the Soli's marginal factor at the base's ESt + the owners' church rates
    ÷ 2). The Soli factor is 0 up to the Freigrenze, 0.119 in the Milderungszone, else 0.055.
    Comparable with the effective rate, which includes Soli and church tax."""
    x = _floor_euros(zve_base)
    if joint:
        x = _floor_euros(x / 2)
    derivative = _zone_derivative(x, tariff)
    est = income_tax(zve_base, tariff, joint)
    if est <= _soli_limit(tariff, joint):
        soli_factor = ZERO
    elif _soli_mitigated(est, tariff, joint):
        soli_factor = tariff.soli_mitigation_rate
    else:
        soli_factor = tariff.soli_rate
    share = _church_share(joint)
    church = sum(
        (
            tariff.church_tax_rates[o.tax_state] * share
            for o in owners
            if o.church_tax and o.tax_state is not None
        ),
        ZERO,
    )
    return _percent(derivative * (1 + soli_factor + church))


def _percent(value: Decimal) -> Decimal:
    """A fraction in percent, one decimal half-up; a negative zero becomes 0.0."""
    return one_decimal(value * HUNDRED) + 0


def tax_effect(
    zve_base: Decimal | None,
    result: Decimal,
    tariff: Tariff,
    owners: Sequence[OwnerTaxProfile],
    *,
    joint: bool = True,
    source: RateSource = "estimate",
    carried_forward: bool = False,
    override_rate: Decimal | None = None,
    sources: Iterable[SourceRef] = (),
    household: HouseholdTaxContext | None = None,
) -> TaxEffect:
    """The tax effect of a rental result R on a household base (D82).

    Without an override: effect = T(zve_base + R) − T(zve_base), both breakdowns given;
    effective_rate = effect ÷ R (percent, one decimal) when R ≠ 0, else effect 0.00 and the
    slope at the base. With override_rate (incl. Soli and church tax): effect = R × rate
    half-up to the cent, no breakdowns; effective_rate = the rate in percent, or the slope
    field carries it when R = 0, so exactly one of the two is set. zve_base may be None only
    under an override. `source`, `carried_forward` and `sources` are passed through (D83);
    the tariff is added to the sources. With `household` (phase 4b D98) both points are
    assessed_tax (a negative zvE taxed as 0, as total_tax does); an override still wins."""
    refs = [*sources, SourceRef(kind="tariff")]
    if override_rate is not None:
        rate_percent = _percent(override_rate)
        return TaxEffect(
            effect=cents(result * override_rate),
            effective_rate=rate_percent if result != 0 else None,
            slope=rate_percent if result == 0 else None,
            source=source,
            carried_forward=carried_forward,
            sources=refs,
        )
    if zve_base is None:
        raise ValueError("a tax effect without an override needs the household base")
    if household is not None:
        without = _assessed(zve_base, household, owners, tariff)
        with_ = _assessed(zve_base + result, household, owners, tariff)
    else:
        without = total_tax(zve_base, tariff, owners, joint)
        with_ = total_tax(zve_base + result, tariff, owners, joint)
    effect = cents(with_.total - without.total)
    return TaxEffect(
        effect=effect,
        effective_rate=_percent(effect / result) if result != 0 else None,
        slope=slope(zve_base, tariff, owners, joint) if result == 0 else None,
        source=source,
        carried_forward=carried_forward,
        breakdown_with=with_,
        breakdown_without=without,
        sources=refs,
    )


def _assessed(
    zve: Decimal,
    household: HouseholdTaxContext,
    owners: Sequence[OwnerTaxProfile],
    tariff: Tariff,
) -> TaxBreakdown:
    """The assessed tax at one point as a breakdown (joint: phase 4b assesses spouses filing
    jointly only). Imported here, not at the top: return_estimate imports this module."""
    from .return_estimate import assessed_tax

    a = assessed_tax(max(ZERO, zve), household, owners, tariff)
    return TaxBreakdown(
        est=cents(a.est),
        credit_35a=cents(a.credit_35a),
        soli=cents(a.soli),
        church=cents(a.church),
        total=cents(a.total),
    )
