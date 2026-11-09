"""Anschaffungsnahe Herstellungskosten: the 15 % tracker (phase 3 §5.3, D56, D70, D73–D76).

§6 Abs. 1 Nr. 1a EStG: repairs and renovation done within three years of the handover whose
net total exceeds 15 % of the building's cost become Herstellungskosten. The status describes
the whole window as known today, so it is the same whichever year is drafted; only the
warnings depend on the year.

- Window: purchase_date .. the same calendar day three years later, both inclusive (D75).
- The tested date is when the work was done (D76): the service period's end, else the booking
  date.
- Counted: non-zero repairs and renovation in the window, whatever the treatment (D74: a
  herstellung election counts too), unless an exclude_from_15pct input is in force. Net is the
  invoice's net, else gross ÷ (1 + default VAT) to the cent, flagged as estimated.
- exceeded is strict (used_net > limit). When exceeded, every counted cost not elected
  herstellung becomes a basis addition (limit_exceeded) at its gross amount in the year paid;
  a herstellung cost keeps its own addition (erhaltungsaufwand.herstellung_additions).
"""

from datetime import timedelta
from decimal import Decimal

from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.tax import (
    BasisAddition,
    CountedCost,
    DraftWarning,
    LimitFacts,
    LimitStatus,
    RuleSet,
    SourceRef,
    TaxInputKind,
    WarningCode,
)

from ._common import ZERO, add_years, by_allocation, warning, with_years
from .afa import basis
from .rounding import cents, money_str, one_decimal

CATEGORIES = frozenset({C.REPAIRS, C.RENOVATION})
# A cost booked this long after the window with no service period asks when the work was done.
PERFORMANCE_CHECK_DAYS = 90


def limit_status(facts: LimitFacts, tax_year: int, rule_set: RuleSet) -> LimitStatus:
    start = facts.afa.purchase_date
    end = add_years(start, rule_set.anschaffungsnah_years)
    building = basis(facts.afa)
    limit = None if building is None else cents(building * rule_set.anschaffungsnah_share)

    excluded = by_allocation(facts.tax_inputs, TaxInputKind.EXCLUDE_FROM_15PCT)
    treatments = by_allocation(facts.tax_inputs, TaxInputKind.RENOVATION_TREATMENT)
    vat = Decimal(1) + rule_set.default_vat_rate

    counted: list[CountedCost] = []
    warnings: list[DraftWarning] = []
    late: list = []  # (allocation, year) for the performance date check
    costs = sorted(
        (
            (a, r.year)
            for a, r in with_years(facts.allocations, rule_set)
            if a.category in CATEGORIES
        ),
        key=lambda x: (x[0].booking_date, str(x[0].allocation_id)),
    )
    for a, year in costs:
        if a.allocation_id in excluded:
            continue
        tested = a.service_period.end if a.service_period is not None else a.booking_date
        if not start <= tested <= end:
            if a.service_period is None and end < a.booking_date <= end + timedelta(
                days=PERFORMANCE_CHECK_DAYS
            ):
                late.append((a, year))
            continue
        gross = abs(a.amount)
        if a.invoice_net is not None:
            net, source = a.invoice_net, "invoice"
        else:
            net, source = cents(gross / vat), "estimated"
            if year == tax_year:
                warnings.append(
                    warning(
                        WarningCode.LIMIT_ESTIMATED_NET,
                        [SourceRef(kind="allocation", id=a.allocation_id)],
                        gross=money_str(gross),
                        net=money_str(net),
                    )
                )
        counted.append(
            CountedCost(
                allocation_id=a.allocation_id,
                booking_date=a.booking_date,
                tax_year=year,
                gross=gross,
                net=net,
                net_source=source,
            )
        )

    used = sum((c.net for c in counted), ZERO) + Decimal("0.00")
    exceeded = limit is not None and used > limit
    exceeded_in_year = None
    additions: list[BasisAddition] = []
    if exceeded:
        running = ZERO
        for c in counted:
            running += c.net
            if running > limit:
                exceeded_in_year = c.tax_year
                break
        for c in counted:
            treatment = treatments.get(c.allocation_id)
            if treatment is not None and treatment.value.treatment == "herstellung":
                continue  # its own herstellung addition already (D74)
            additions.append(
                BasisAddition(
                    allocation_id=c.allocation_id,
                    tax_year=c.tax_year,
                    amount=c.gross,
                    reason="limit_exceeded",
                )
            )
        if exceeded_in_year <= tax_year <= end.year:
            warnings.append(
                warning(
                    WarningCode.LIMIT_EXCEEDED,
                    [SourceRef(kind="allocation", id=b.allocation_id) for b in additions],
                    exceeded_in_year=str(exceeded_in_year),
                )
            )
        moved = [b for b in additions if b.tax_year == tax_year]
        if tax_year < exceeded_in_year and moved:
            warnings.append(
                warning(
                    WarningCode.LIMIT_PRIOR_YEAR_AMENDMENT,
                    [SourceRef(kind="allocation", id=b.allocation_id) for b in moved],
                    exceeded_in_year=str(exceeded_in_year),
                    amount=money_str(sum((b.amount for b in moved), ZERO)),
                )
            )

    for a, year in late:
        if year == tax_year:
            warnings.append(
                warning(
                    WarningCode.ANSCHAFFUNGSNAH_PERFORMANCE_DATE_CHECK,
                    [SourceRef(kind="allocation", id=a.allocation_id)],
                    booking_date=a.booking_date.isoformat(),
                    amount=money_str(abs(a.amount)),
                )
            )

    return LimitStatus(
        tax_year=tax_year,
        basis_building_cost=building,
        limit=limit,
        window_start=start,
        window_end=end,
        counted=counted,
        used_net=used,
        ratio_percent=None if not limit else one_decimal(used / limit * 100),
        exceeded=exceeded,
        exceeded_in_year=exceeded_in_year,
        basis_additions=additions,
        warnings=warnings,
    )
