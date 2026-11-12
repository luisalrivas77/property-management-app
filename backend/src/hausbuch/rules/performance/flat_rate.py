"""The Nebenkostenpauschale rise test (docs/phases/phase-4.md D155; fixture
tests/fixtures/phase4/flat_rate_rise.yaml). Pure, Decimal.

§560 Abs. 1 BGB lets a landlord pass on a rise of the Betriebskosten since a Pauschale was set;
Abs. 3 requires lowering it when they fall. So for every flat_rate tenancy in force today the
check compares the flat's monthly cost level when its Pauschale was set with today's, splits
the flat's rise by the Pauschalen, and gives a verdict per tenancy. Information, not advice:
§560 BGB decides, and the screens say so.

The cost level on a date (D155 (2)), per category, from the confirmed allocations and tax
inputs of DraftFacts (the interpretations the spec left open are marked *):

- A line's reference date is its service period's start when that period is one calendar
  month*, else its booking date (a purpose naming a whole year gives a year's period, which
  says nothing about the month). A payment is the chosen line's transaction: −Σ its
  allocations in the category (one unless a payment was split)*.
- hausgeld_umlagefaehig, electricity: the latest payment on or before the date, else the first
  one after it (`from_later`). When the category has a `recurring` line, only recurring lines
  are payments and the others are settlements (hints)*; without one, every line is a payment.
- insurance: the latest payment on or before the date, else the first after it; a yearly
  premium (÷ 12) when no other insurance line lies within 6 months either side of it*, else a
  month's payment.
- grundsteuer: the grundsteuer_schedule in force for the date's year (the latest row with
  tax_year ≤ the year) ÷ 12; else Σ the lines of the 12 months up to the date ÷ 12; else*, with
  nothing known on or before the date, the first later schedule, else the first 12 months of
  lines after it (`from_later`; D155 says 0, which would turn the first Bescheid into a rise);
  else 0.00.

Every rate is half-up to the cent and names its allocations or tax input (principle 2).
"""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from uuid import UUID

from hausbuch.rules.tax._common import add_months, in_force
from hausbuch.rules.tax.rounding import cents, one_decimal
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.enums import OperatingCostsTerms
from hausbuch.schemas.performance import (
    FLAT_RATE_BETRKV,
    FLAT_RATE_CATEGORIES,
    FLAT_RATE_RAISE_ABOVE,
    CategoryChange,
    CostLevel,
    CostRate,
    FlatRateCheck,
    FlatRateLetterFigures,
    FlatRateVerdict,
    LetterReason,
    RateBasis,
    SettlementHint,
    TenancyFlatRate,
)
from hausbuch.schemas.tax import (
    AllocationFact,
    DraftFacts,
    GrundsteuerSchedule,
    SourceRef,
    TaxInputFact,
    TaxInputKind,
    TaxRent,
    TaxTenancy,
)

ZERO = Decimal("0.00")
MONTHLY = frozenset({C.HAUSGELD_UMLAGEFAEHIG, C.ELECTRICITY})
PREMIUM_WINDOW_MONTHS = 6  # a yearly premium has no other line within 6 months either side


def reference_date(a: AllocationFact) -> date:
    """The month a line pays for: its service period's start when the period is one calendar
    month, else its booking date."""
    p = a.service_period
    if p is not None and (p.start.year, p.start.month) == (p.end.year, p.end.month):
        return p.start
    return a.booking_date


def _ref(a: AllocationFact) -> SourceRef:
    return SourceRef(kind="allocation", id=a.allocation_id, amount=a.amount)


def _order(a: AllocationFact) -> tuple[date, date, UUID]:
    return (reference_date(a), a.booking_date, a.allocation_id)


class _Category:
    """One category's lines, split into payments (rates) and settlements (hints)."""

    def __init__(self, category: C, lines: Sequence[AllocationFact]):
        self.category = category
        lines = sorted((a for a in lines if a.category is category), key=_order)
        if category in MONTHLY and any(a.recurring for a in lines):
            self.payments = [a for a in lines if a.recurring]
            self.settlements = [a for a in lines if not a.recurring]
        else:
            self.payments, self.settlements = lines, []

    def chosen(self, on: date) -> tuple[AllocationFact, bool] | None:
        """The latest payment on or before `on`, else the first after it (from_later)."""
        before = [a for a in self.payments if reference_date(a) <= on]
        if before:
            return before[-1], False
        if self.payments:
            return self.payments[0], True
        return None

    def payment(self, line: AllocationFact) -> list[AllocationFact]:
        """The chosen line with the other parts of its transaction in the category."""
        if line.transaction_id is None:
            return [line]
        ref = reference_date(line)
        return [
            a
            for a in self.payments
            if a.transaction_id == line.transaction_id and reference_date(a) == ref
        ]

    def run_start(self, line: AllocationFact, rate: Decimal) -> date:
        """The first line of the unbroken run of payments, ending at `line`, at `rate`."""
        start = reference_date(line)
        before = [a for a in self.payments if _order(a) < _order(line)]
        for a in reversed(before):
            if cents(-sum((p.amount for p in self.payment(a)), ZERO)) != rate:
                break
            start = reference_date(a)
        return start


def _monthly(cat: _Category, on: date) -> tuple[CostRate, date | None]:
    found = cat.chosen(on)
    if found is None:
        return CostRate(category=cat.category, monthly=ZERO, basis=RateBasis.NONE), None
    line, later = found
    parts = cat.payment(line)
    paid = -sum((a.amount for a in parts), ZERO)
    basis = RateBasis.MONTHLY_PAYMENT
    if cat.category is C.INSURANCE:
        ref = reference_date(line)
        low, high = add_months(ref, -PREMIUM_WINDOW_MONTHS), add_months(ref, PREMIUM_WINDOW_MONTHS)
        own = {a.allocation_id for a in parts}
        others = [
            a for a in cat.payments if a.allocation_id not in own and low < reference_date(a) < high
        ]
        if not others:
            basis = RateBasis.YEARLY_PREMIUM
            paid = paid / 12
    rate = CostRate(
        category=cat.category,
        monthly=cents(paid),
        basis=basis,
        from_later=later,
        sources=[_ref(a) for a in parts],
    )
    if basis is RateBasis.YEARLY_PREMIUM:
        return rate, reference_date(line)
    return rate, cat.run_start(line, rate.monthly)


def _schedules(tax_inputs: Sequence[TaxInputFact]) -> list[tuple[int, TaxInputFact, Decimal]]:
    rows = []
    for row in in_force(tax_inputs, TaxInputKind.GRUNDSTEUER_SCHEDULE):
        value = row.value
        if row.tax_year is not None and isinstance(value, GrundsteuerSchedule):
            rows.append((row.tax_year, row, value.annual_amount))
    return sorted(rows, key=lambda r: (r[0], str(r[1].id)))


def _grundsteuer(
    cat: _Category, schedules: list[tuple[int, TaxInputFact, Decimal]], on: date
) -> tuple[CostRate, date | None]:
    def from_schedule(year: int, row: TaxInputFact, annual: Decimal, later: bool):
        rate = CostRate(
            category=C.GRUNDSTEUER,
            monthly=cents(annual / 12),
            basis=RateBasis.SCHEDULE,
            from_later=later,
            sources=[SourceRef(kind="tax_input", id=row.id, amount=annual)],
        )
        return rate, date(year, 1, 1)

    def from_lines(lines: list[AllocationFact], later: bool):
        rate = CostRate(
            category=C.GRUNDSTEUER,
            monthly=cents(-sum((a.amount for a in lines), ZERO) / 12),
            basis=RateBasis.TWELVE_MONTHS,
            from_later=later,
            sources=[_ref(a) for a in lines],
        )
        latest = lines[-1]
        return rate, cat.run_start(latest, cents(-latest.amount))

    known = [s for s in schedules if s[0] <= on.year]
    if known:
        return from_schedule(*known[-1], False)
    window = add_months(on, -12)
    lines = [a for a in cat.payments if window < reference_date(a) <= on]
    if lines:
        return from_lines(lines, False)
    later = [s for s in schedules if s[0] > on.year]
    if later:
        return from_schedule(*later[0], True)
    after = [a for a in cat.payments if reference_date(a) > on]
    if after:
        end = add_months(reference_date(after[0]), 12)
        return from_lines([a for a in after if reference_date(a) < end], True)
    return CostRate(category=C.GRUNDSTEUER, monthly=ZERO, basis=RateBasis.NONE), None


class _Levels:
    def __init__(self, facts: DraftFacts):
        self.cats = {c: _Category(c, facts.allocations) for c in FLAT_RATE_CATEGORIES}
        self.schedules = _schedules(facts.tax_inputs)

    def at(self, on: date) -> tuple[CostLevel, dict[C, date | None]]:
        rates, since = [], {}
        for c in FLAT_RATE_CATEGORIES:
            if c is C.GRUNDSTEUER:
                rate, start = _grundsteuer(self.cats[c], self.schedules, on)
            else:
                rate, start = _monthly(self.cats[c], on)
            rates.append(rate)
            since[c] = start
        betrkv = sum((r.monthly for r in rates if r.category in FLAT_RATE_BETRKV), ZERO)
        return CostLevel(as_of=on, rates=rates, betrkv=betrkv), since

    def settlements(self, today: date) -> list[SettlementHint]:
        window = add_months(today, -12)
        hints = [
            a
            for c in FLAT_RATE_CATEGORIES
            for a in self.cats[c].settlements
            if window < reference_date(a) <= today
        ]
        return [
            SettlementHint(
                category=a.category,
                allocation_id=a.allocation_id,
                booking_date=a.booking_date,
                amount=-a.amount,
            )
            for a in sorted(hints, key=_order)
        ]


def in_force_on(t: TaxTenancy, day: date) -> bool:
    return t.start_date <= day and (t.end_date is None or t.end_date >= day)


def _rows(t: TaxTenancy, today: date) -> list[TaxRent]:
    rows = sorted(t.rents, key=lambda r: r.effective_from)
    current = [r for r in rows if r.effective_from <= today]
    return current or rows[:1]


def pauschale_set(t: TaxTenancy, today: date) -> tuple[Decimal, date, str]:
    """The Pauschale in force today, the date it was set and how: the latest rent row (in force
    through today) whose operating_costs_prepayment differs from the row before it, else the
    tenancy's start."""
    rows = _rows(t, today)
    if not rows:
        return ZERO, t.start_date, "start"
    set_on, set_by = t.start_date, "start"
    for prev, row in zip(rows, rows[1:], strict=False):
        if row.operating_costs_prepayment != prev.operating_costs_prepayment:
            set_on, set_by = row.effective_from, "rent_change"
    return rows[-1].operating_costs_prepayment, set_on, set_by


def cold_rent_on(t: TaxTenancy, today: date) -> Decimal:
    rows = _rows(t, today)
    return rows[-1].cold_rent if rows else ZERO


def month_after_next(day: date) -> date:
    """The first day of the month after next (§560 Abs. 2 BGB)."""
    return add_months(date(day.year, day.month, 1), 2)


def flat_rate_check(facts: DraftFacts, today: date) -> FlatRateCheck:
    """D155 (3), (4): every flat_rate tenancy in force today with its share of the rise."""
    flat = [
        t
        for t in facts.tenancies
        if t.operating_costs_terms is OperatingCostsTerms.FLAT_RATE and in_force_on(t, today)
    ]
    labels = {u.id: u.label for u in facts.units}
    property_id = facts.property.id
    if not flat:
        return FlatRateCheck(property_id=property_id, as_of=today, pauschalen_total=ZERO)

    levels = _Levels(facts)
    now, since = levels.at(today)
    set_info = {t.id: pauschale_set(t, today) for t in flat}
    by_date = {today: now}
    for _, set_on, _ in set_info.values():
        if set_on not in by_date:
            by_date[set_on] = levels.at(set_on)[0]
    total = sum((p for p, _, _ in set_info.values()), ZERO)
    now_rates = {r.category: r.monthly for r in now.rates}

    tenancies = []
    for t in flat:
        pauschale, set_on, set_by = set_info[t.id]
        then_rates = {r.category: r.monthly for r in by_date[set_on].rates}
        changes = [
            CategoryChange(
                category=c,
                then=then_rates[c],
                now=now_rates[c],
                change=now_rates[c] - then_rates[c],
            )
            for c in FLAT_RATE_CATEGORIES
        ]
        flat_rise = sum((ch.change for ch in changes if ch.category in FLAT_RATE_BETRKV), ZERO)
        rise = cents(flat_rise * pauschale / total) if total else ZERO
        if rise > FLAT_RATE_RAISE_ABOVE:
            verdict = FlatRateVerdict.RAISE
        elif rise < 0:
            verdict = FlatRateVerdict.LOWER
        else:
            verdict = FlatRateVerdict.NONE
        fell = [
            since[ch.category]
            for ch in changes
            if ch.category in FLAT_RATE_BETRKV and ch.change < 0 and since[ch.category]
        ]
        tenancies.append(
            TenancyFlatRate(
                tenancy_id=t.id,
                unit_id=t.unit_id,
                unit_label=labels.get(t.unit_id, ""),
                pauschale=pauschale,
                set_on=set_on,
                set_by=set_by,
                share_percent=one_decimal(pauschale * 100 / total) if total else None,
                changes=changes,
                flat_rise=flat_rise,
                rise=rise,
                verdict=verdict,
                earliest_start=month_after_next(today)
                if verdict is FlatRateVerdict.RAISE
                else None,
                # a category whose rate fell to nothing has no date of its own: today
                fell_on=(min(fell) if fell else today)
                if verdict is FlatRateVerdict.LOWER
                else None,
            )
        )
    return FlatRateCheck(
        property_id=property_id,
        as_of=today,
        pauschalen_total=total,
        tenancies=tenancies,
        levels=[by_date[d] for d in sorted(by_date)],
        settlements=levels.settlements(today),
    )


def flat_rate_letter(
    facts: DraftFacts, today: date, tenancy_id: UUID, sent_on: date
) -> FlatRateLetterFigures | None:
    """D155 (7): the letter's figures for one tenancy, from today's check (fixture
    tests/fixtures/phase4/flat_rate_letter.yaml). None when the tenancy is not a flat_rate
    tenancy in force today or its verdict is none. The reasons are the BetrKV categories with
    a cost then or now, never electricity; the new Pauschale is the old one plus the tenancy's
    rise, never below 0.00; a raise starts on the first day of the month after next from the
    sending date (§560 Abs. 2 BGB), a lower on the date the cost fell (Abs. 3)."""
    check = flat_rate_check(facts, today)
    row = next((t for t in check.tenancies if t.tenancy_id == tenancy_id), None)
    if row is None or row.verdict is FlatRateVerdict.NONE:
        return None
    levels = {lv.as_of: lv for lv in check.levels}
    then = {r.category: r for r in levels[row.set_on].rates}
    now = {r.category: r for r in levels[today].rates}
    reasons = [
        LetterReason(
            category=c,
            then=then[c].monthly,
            now=now[c].monthly,
            change=now[c].monthly - then[c].monthly,
            then_sources=then[c].sources,
            now_sources=now[c].sources,
        )
        for c in FLAT_RATE_BETRKV
        if then[c].monthly or now[c].monthly
    ]
    if row.verdict is FlatRateVerdict.RAISE:
        starts_on = month_after_next(sent_on)
    else:
        assert row.fell_on is not None  # TenancyFlatRate's validator
        starts_on = row.fell_on
    tenancy = next(t for t in facts.tenancies if t.id == tenancy_id)
    cold_rent = cold_rent_on(tenancy, max(today, starts_on))
    new_pauschale = max(ZERO, row.pauschale + row.rise)
    assert row.share_percent is not None  # a rise or a fall means Σ Pauschalen > 0
    return FlatRateLetterFigures(
        tenancy_id=tenancy_id,
        unit_label=row.unit_label,
        kind=row.verdict,
        as_of=today,
        sent_on=sent_on,
        starts_on=starts_on,
        set_on=row.set_on,
        reasons=reasons,
        flat_rise=row.flat_rise,
        pauschale=row.pauschale,
        pauschalen_total=check.pauschalen_total,
        share_percent=row.share_percent,
        rise=row.rise,
        new_pauschale=new_pauschale,
        cold_rent=cold_rent,
        new_total=cold_rent + new_pauschale,
    )
