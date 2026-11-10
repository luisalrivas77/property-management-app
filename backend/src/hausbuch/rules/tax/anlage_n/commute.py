"""§5.2, D100 (1): the Entfernungspauschale over the year's routes.

Per route `days × km × rate`; the part not travelled by own or company car is capped at 4,500
per year across all routes, the car part is not (§9 Abs. 1 S. 3 Nr. 4 S. 2; BMF 18 Nov 2021
Rz. 21–22, Q-O14). Typed public-transport costs replace the year's total when higher (§9
Abs. 2 S. 2). The employer's tax-free and flat-taxed commute benefits (Nr. 17, Nr. 18) are
subtracted, floored at 0. More than three routes: `anlage_n.too_many_routes` (the form has
three blocks).
"""

from dataclasses import dataclass, field
from decimal import Decimal

from hausbuch.rules.tax._common import warning
from hausbuch.schemas.employment import (
    COMMUTE_ROUTES,
    AnlageNRuleSet,
    CommuteFact,
    WageCertificate,
)
from hausbuch.schemas.tax import DraftWarning, WarningCode

from ._base import ZERO, cents


@dataclass
class CommuteResult:
    pauschale_car: Decimal
    pauschale_rest: Decimal
    total: Decimal
    employer_offset: Decimal
    deductible: Decimal
    notes: list[str] = field(default_factory=list)
    warnings: list[DraftWarning] = field(default_factory=list)


def _per_day(km: int, rs: AnlageNRuleSet) -> Decimal:
    """One day's Pauschale for a distance of `km` full kilometres, through the rate tiers."""
    tiers = rs.commute_rates
    total = ZERO
    for i, tier in enumerate(tiers):
        upper = tiers[i + 1].from_km - 1 if i + 1 < len(tiers) else km
        span = min(km, upper) - tier.from_km + 1
        if span > 0:
            total += span * tier.rate
    return total


def _route_parts(route: CommuteFact, rs: AnlageNRuleSet) -> tuple[Decimal, Decimal]:
    """(car part, other part) of one route for the year. With one tier (2026: 0.38 from the
    first km) each part is days × its km × rate exactly. With several tiers the route's
    Pauschale goes through the tiers on the whole distance and is split by the parts' km."""
    rest_km = route.km_collective + route.km_other
    if len(rs.commute_rates) == 1:
        rate = rs.commute_rates[0].rate
        return Decimal(route.days * route.km_car) * rate, Decimal(route.days * rest_km) * rate
    whole = route.days * _per_day(route.km, rs)
    car = whole * route.km_car / route.km
    return car, whole - car


def commute_lines(
    routes: list[CommuteFact], certificates: list[WageCertificate], rs: AnlageNRuleSet
) -> CommuteResult:
    car = rest = ZERO
    for route in routes:
        c, r = _route_parts(route, rs)
        car += c
        rest += r
    total = car + min(rest, rs.commute_cap_non_car)
    notes: list[str] = []
    tickets = sum((r.public_transport_costs or ZERO for r in routes), ZERO)
    if tickets > total:
        total = tickets
        notes.append("public_transport_costs_exceed")
    offset = sum(
        (c.employer_commute_tax_free + c.employer_commute_flat_taxed for c in certificates), ZERO
    )
    warnings = []
    if len(routes) > COMMUTE_ROUTES:
        warnings.append(warning(WarningCode.ANLAGE_N_TOO_MANY_ROUTES, routes=str(len(routes))))
    return CommuteResult(
        pauschale_car=cents(car),
        pauschale_rest=cents(rest),
        total=cents(total),
        employer_offset=offset,
        deductible=max(ZERO, cents(total) - offset),
        notes=notes,
        warnings=warnings,
    )
