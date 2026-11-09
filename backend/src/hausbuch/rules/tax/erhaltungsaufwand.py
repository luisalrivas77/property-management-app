"""Erhaltungsaufwand and the §82b spread (phase 3 §5.4, D59, D62, D74).

- `repairs` → wk.maintenance in its tax year (§5.1).
- `renovation` follows its `renovation_treatment` input: erhaltung → like repairs (the input
  is a source); herstellung → a basis addition of the gross magnitude in the year paid, no
  line; no input → open (excluded_open and `renovation.undecided`).
- A `spread_82b` input in force spreads an Erhaltungsaufwand over n years: the year paid writes
  the total (Z57) and round_half_up(amount ÷ n) (Z58–60), year paid + k the same share on
  `_prior_k`, the last year the remainder; the year ownership ends takes the whole remainder.
- When the 15 % limit is exceeded (§5.3), the costs it moves into the AfA basis leave this
  rule entirely: no line, no spread, not open (pass the LimitStatus as `limit`).
"""

from collections.abc import Iterator
from decimal import Decimal
from uuid import UUID

from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.tax import (
    AllocationFact,
    BasisAddition,
    DraftFacts,
    LimitStatus,
    OpenLines,
    RuleLines,
    RuleSet,
    SourceRef,
    TaxInputFact,
    TaxInputKind,
    WarningCode,
)

from ._common import (
    ZERO,
    LineBook,
    by_allocation,
    input_sources,
    sources_of,
    warning,
    with_years,
)
from .rounding import cents, money_str

CATEGORIES = frozenset({C.REPAIRS, C.RENOVATION})


def _moved_by_limit(limit: LimitStatus | None) -> set[UUID]:
    if limit is None:
        return set()
    return {b.allocation_id for b in limit.basis_additions if b.reason == "limit_exceeded"}


def _costs(
    facts: DraftFacts, rule_set: RuleSet, limit: LimitStatus | None
) -> Iterator[tuple[AllocationFact, int, TaxInputFact | None]]:
    """Every non-zero repairs/renovation allocation of any year this rule decides: with its
    tax year and its renovation_treatment input (None for repairs and undecided renovation).
    The costs the 15 % limit moved into the basis are left out."""
    moved = _moved_by_limit(limit)
    treatments = by_allocation(facts.tax_inputs, TaxInputKind.RENOVATION_TREATMENT)
    for a, year in with_years(facts.allocations, rule_set):
        if a.category in CATEGORIES and a.allocation_id not in moved:
            treatment = treatments.get(a.allocation_id) if a.category is C.RENOVATION else None
            yield a, year.year, treatment


def herstellung_additions(facts: DraftFacts, rule_set: RuleSet) -> list[BasisAddition]:
    """The basis additions of every renovation elected `herstellung`, all years (§5.2 reads
    them back to their year). Never moved by the limit: they are additions already (D74)."""
    out = []
    for a, year, treatment in _costs(facts, rule_set, None):
        if treatment is not None and treatment.value.treatment == "herstellung":
            out.append(
                BasisAddition(
                    allocation_id=a.allocation_id,
                    tax_year=year,
                    amount=abs(a.amount),
                    reason="herstellung",
                )
            )
    return out


def _spread_parts(
    amount: Decimal, years: int, paid_year: int, ended_year: int | None
) -> list[tuple[int, Decimal]]:
    """(k, share) for k = 0 .. n−1 years after the year paid (D62)."""
    share = cents(amount / years)
    parts = []
    for k in range(years):
        if ended_year is not None and paid_year + k > ended_year:
            break
        last = k == years - 1 or (ended_year is not None and paid_year + k == ended_year)
        parts.append((k, amount - share * k if last else share))
        if last:
            break
    return parts


def lines(
    facts: DraftFacts, tax_year: int, rule_set: RuleSet, *, limit: LimitStatus | None = None
) -> RuleLines:
    spreads = by_allocation(facts.tax_inputs, TaxInputKind.SPREAD_82B)
    ended = facts.property.ownership_ended_on
    book = LineBook()
    additions: list[BasisAddition] = []
    warnings = []
    open_count, open_sum = 0, ZERO

    for a, year, treatment in _costs(facts, rule_set, limit):
        refs: list[SourceRef] = sources_of(a)
        if a.category is C.RENOVATION:
            if treatment is None:
                if year == tax_year:
                    open_count += 1
                    open_sum += a.amount
                    warnings.append(
                        warning(
                            WarningCode.RENOVATION_UNDECIDED,
                            [SourceRef(kind="allocation", id=a.allocation_id)],
                            amount=money_str(abs(a.amount)),
                        )
                    )
                continue
            if treatment.value.treatment == "herstellung":
                if year == tax_year:
                    additions.append(
                        BasisAddition(
                            allocation_id=a.allocation_id,
                            tax_year=year,
                            amount=abs(a.amount),
                            reason="herstellung",
                        )
                    )
                continue
            refs = refs + input_sources(treatment)

        spread = spreads.get(a.allocation_id)
        if spread is not None and ended is not None and year > ended.year:
            # Paid after the year ownership ended: §82b spreads only while the property is
            # owned, so a nachträgliche Werbungskosten is deducted in full in the year paid
            # (decided in the step 1 review; _spread_parts would return nothing for it).
            spread = None
        if spread is None:
            if year == tax_year:
                book.add("wk.maintenance", -a.amount, refs)
            continue
        refs = refs + input_sources(spread)
        for k, part in _spread_parts(-a.amount, spread.value.years, year, ended and ended.year):
            if year + k != tax_year:
                continue
            if k == 0:
                book.add("wk.maintenance_spread_total", -a.amount, refs)
                book.add("wk.maintenance_spread_current", part, refs)
            else:
                book.add(f"wk.maintenance_spread_prior_{k}", part, refs)

    return RuleLines(
        tax_year=tax_year,
        lines=book.lines(),
        basis_additions=additions,
        excluded_open=OpenLines(count=open_count, sum=open_sum),
        warnings=warnings,
    )
