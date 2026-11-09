"""One year's figures, for actual and projected years alike (docs/phases/phase-4.md §5.3, D85,
D86, D91, D92). Pure, Decimal.

The caller hands over the year's confirmed, non-zero allocations as phase 3's §11 rule assigns
them (`rules.tax._common.of_year` under the year's rule set), the year's draft (its taxable
result, AfA and the pre-handover lines it moved into the rent row) and the tax effect, which
the caller computes on the draft's result (rules.performance.income.year_tax_effect). The
tariff is never read here.

Cash and tax are kept apart: the figures are sums of what was paid (magnitudes, as the screen
shows them) and the result is the draft's. Investment and financing flows (`purchase_price`,
`acquisition_cost`, `loan_disbursement`, `owner_transfer_*`, `deposit_*`) are never yearly
cash; the purchase year shows the first two as `invested` (evidence beside E₀). A category
outside the table below (e.g. `uncategorized`) counts nowhere.
"""

import calendar
from collections.abc import Callable, Iterable, Sequence
from datetime import date
from decimal import Decimal
from uuid import UUID

from hausbuch.rules.tax.rounding import cents
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.enums import OperatingCostsTerms
from hausbuch.schemas.performance import TaxEffect, YearFigures, YearFlag, YearKind
from hausbuch.schemas.tax import AllocationFact, AnlageVDraft, SourceRef, TaxTenancy

ZERO = Decimal("0.00")
MOVABLE_LINE = "wk.afa_movable"  # D80's kitchen line (§5.6)

RENT = frozenset({C.RENT_COLD})
OTHER_INCOME = frozenset({C.OTHER_INCOME})
UMLAGEN_IN = frozenset({C.OPERATING_COSTS_PREPAYMENT, C.OPERATING_COSTS_SETTLEMENT})
# The flat's own electricity (D92) is paid for by the tenants' Pauschale like the operating
# costs, so the Pauschale check sees it; verbilligt.UMLAGEFAEHIG leaves it out (Q-O9).
UMLAGEN_OUT = frozenset({C.HAUSGELD_UMLAGEFAEHIG, C.GRUNDSTEUER, C.INSURANCE, C.ELECTRICITY})
INTEREST = frozenset({C.LOAN_INTEREST})
PRINCIPAL = frozenset({C.LOAN_PRINCIPAL})
RUNNING = frozenset(
    {
        C.HAUSGELD_VERWALTUNG,
        C.HAUSGELD_RUECKLAGE,
        C.PROPERTY_MANAGEMENT,
        C.BANK_FEES,
        C.REPAIRS,
        C.RENOVATION,
        C.PROFESSIONAL_FEES,
        C.LOAN_FEES,
        C.OTHER_EXPENSE,
    }
)
INVESTED = frozenset({C.PURCHASE_PRICE, C.ACQUISITION_COST})


def _signed_in(rows: Iterable[AllocationFact]) -> Decimal:
    """An income block: Σ amounts (a refund to a tenant lowers it)."""
    return sum((a.amount for a in rows), ZERO)


def _signed_out(rows: Iterable[AllocationFact]) -> Decimal:
    """A cost block as a magnitude: −Σ amounts (a refund in a cost category lowers it)."""
    return -sum((a.amount for a in rows), ZERO)


def _ref(row: AllocationFact) -> SourceRef:
    return SourceRef(kind="allocation", id=row.allocation_id, amount=row.amount)


def _flags(kind: YearKind, effect: TaxEffect | None) -> list[YearFlag]:
    flags = []
    if kind is YearKind.ACTUAL_PARTIAL:
        flags.append(YearFlag.SO_FAR)
    if effect is None:
        flags.append(YearFlag.INCOME_MISSING)
    elif effect.source != "bescheid":
        flags.append(YearFlag.TAX_ESTIMATED)
    return flags


def _moved_to_rent(
    other_income: Sequence[AllocationFact], draft: AnlageVDraft | None
) -> set[UUID | None]:
    """The other_income lines the draft moved into the flat's rent row (D63): those booked
    before the handover, when a pre_handover_income input is in force, which the draft shows
    as a unit row with a pre_handover part. Undecided ones wait in draft.pre_handover and stay
    other income (cash, outside the result)."""
    if draft is None or not any(row.pre_handover != 0 for row in draft.unit_rows):
        return set()
    handover = draft.header.transfer_date
    return {a.allocation_id for a in other_income if a.booking_date < handover}


def _movable(draft: AnlageVDraft | None) -> Decimal | None:
    lines = [] if draft is None else [ln for ln in draft.lines if ln.key == MOVABLE_LINE]
    amounts = [ln.amount for ln in lines if ln.amount is not None]
    return sum(amounts, ZERO) if amounts else None


def flat_rate_months(
    year: int,
    tenancies: Sequence[TaxTenancy],
    handover: date | None,
    until: date | None = None,
) -> int | None:
    """D92: the calendar months of `year` in which at least one flat_rate tenancy was in force
    (start to end date, any day of the month counts) and that are not before the month of the
    handover (no restriction when it is None) and, for the year that contains `until` (today,
    for an actual year still being booked), not after its month: the year's sums hold only
    what is booked so far, so the later months would shrink the gap per month. None when there
    are none: before the handover the seller paid the costs and forwarded the rent, so those
    months have neither side."""
    flat = [t for t in tenancies if t.operating_costs_terms is OperatingCostsTerms.FLAT_RATE]
    first = 1
    if handover is not None:
        if handover.year > year:
            return None
        if handover.year == year:
            first = handover.month
    last = 12
    if until is not None:
        if until.year < year:
            return None
        if until.year == year:
            last = until.month
    months = sum(
        1
        for m in range(first, last + 1)
        if any(
            t.start_date <= _month_end(year, m)
            and (t.end_date is None or t.end_date >= date(year, m, 1))
            for t in flat
        )
    )
    return months or None


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def year_figures(
    year: int,
    kind: YearKind,
    allocations: Sequence[AllocationFact],
    draft: AnlageVDraft | None,
    effect: TaxEffect | None,
    *,
    tenancies: Sequence[TaxTenancy] = (),
    purchase_year: int | None = None,
    handover: date | None = None,
    until: date | None = None,
    value: Decimal | None = None,
    loan_balance: Decimal | None = None,
    extra_sources: Iterable[SourceRef] = (),
    ref_of: Callable[[AllocationFact], SourceRef] | None = None,
) -> YearFigures:
    """§5.3's table for `year`. `allocations` are the year's confirmed allocations by §11
    (zero rows are ignored here as well); `draft` is None only when no rule set applies, and
    then the result and AfA are None. `value` and `loan_balance` are given for projected
    years (§5.4 step 6); equity is their difference. `tenancies` tell the flat-rate ones apart
    for the Pauschale check (D91) and, with the `handover` (the property's purchase_date),
    give the months it divides by (D92), up to the month of `until` (today, given for actual
    years only) in the year that contains it; `purchase_year` puts `invested` on that year
    only."""
    rows = [a for a in allocations if a.amount != 0]

    def of(categories: frozenset[C]) -> list[AllocationFact]:
        return [a for a in rows if a.category in categories]

    moved = _moved_to_rent(of(OTHER_INCOME), draft)
    rent_rows = of(RENT) + [a for a in of(OTHER_INCOME) if a.allocation_id in moved]
    other_rows = [a for a in of(OTHER_INCOME) if a.allocation_id not in moved]
    in_rows, out_rows = of(UMLAGEN_IN), of(UMLAGEN_OUT)
    interest_rows, principal_rows, running_rows = of(INTEREST), of(PRINCIPAL), of(RUNNING)

    rent_in = _signed_in(rent_rows)
    other_income = _signed_in(other_rows)
    umlagen_in = _signed_in(in_rows)
    umlagen_out = _signed_out(out_rows)
    interest = _signed_out(interest_rows)
    principal = _signed_out(principal_rows)
    running = _signed_out(running_rows)
    cash_out = interest + principal + running + umlagen_out
    tax = effect.effect if effect is not None else ZERO
    net_cash = rent_in + other_income + umlagen_in - cash_out - tax

    invested = None
    if purchase_year is not None and year == purchase_year:
        invested = _signed_out(of(INVESTED))

    flat = {t.id for t in tenancies if t.operating_costs_terms is OperatingCostsTerms.FLAT_RATE}
    flat_rate_in = _signed_in([a for a in in_rows if a.tenancy_id in flat])
    months = flat_rate_months(year, tenancies, handover, until)
    gap = cents((flat_rate_in - umlagen_out) / months) if months is not None else None

    ref = ref_of or _ref
    counted = [*rent_rows, *other_rows, *in_rows, *out_rows, *interest_rows]
    counted += [*principal_rows, *running_rows]
    if invested is not None:
        counted += of(INVESTED)
    sources = [ref(a) for a in counted]
    if effect is not None:
        sources += effect.sources
    sources += list(extra_sources)

    afa_line = draft.afa if draft is not None else None
    return YearFigures(
        year=year,
        kind=kind,
        flags=_flags(kind, effect),
        rent_in=rent_in,
        other_income=other_income,
        umlagen_in=umlagen_in,
        umlagen_out=umlagen_out,
        umlagen_net=umlagen_in - umlagen_out,
        interest=interest,
        principal_repaid=principal,
        running_costs=running,
        cash_out=cash_out,
        taxable_result=draft.totals.result if draft is not None else None,
        afa=afa_line.amount if afa_line is not None else None,
        afa_movable=_movable(draft),
        tax_effect=effect,
        net_cash=net_cash,
        invested=invested,
        value=value,
        loan_balance=loan_balance,
        equity=value - loan_balance if value is not None and loan_balance is not None else None,
        flat_rate_in=flat_rate_in,
        flat_rate_months=months,
        flat_rate_gap_monthly=gap,
        sources=sources,
    )
