"""Wage-replacement benefits booked on the accounts but not typed as Lohnersatzleistung (phase 6
D159 amended (8), 15 Nov 2026; phase 4b D102). Pure, no I/O, Decimal throughout.

The household's private lines categorised `hh_wage_replacement` (Elterngeld, Mutterschaftsgeld,
Krankengeld, Arbeitslosengeld) are evidence of the year's Lohnersatzleistungen, which only the
`replacement_income` return inputs count (the Progressionsvorbehalt). Principle 7: when the
confirmed lines booked in the tax year (by booking date) sum to more than the typed rows, the
estimate carries the to-do `return.benefit_booked` with the difference, and the person confirms
it through the existing return-input form; nothing is written here, and the estimate's figures
are unchanged.

- Kurzarbeitergeld is never counted, on either side: the employer pays it with the pay and it is
  the wage certificate's Nr. 15, which already enters the Progressionsvorbehalt (D102), so a
  booked line whose text names it (or a typed `kurzarbeitergeld` row) would count it twice.
- The spouses are assessed jointly, so the gate is the household's: a to-do only while
  Σ booked > Σ typed over every owner of the year, whichever owner the lines or the rows are on.
  The to-dos' differences sum to exactly that gap.
- The lines are grouped by owner: a line's owner is its account's only current owner; a line on
  an account with several owners (or none recorded) is in the one group without an owner. Each
  group's booked sum is signed (a payment back lowers it) and then clamped at zero, so a payment
  back larger than the benefits never counts as typed. An owner's group falls short by
  max(0, booked − the owner's typed rows); the group without an owner by its booked sum.
- The gap is handed to the groups that fall short, owners first (in `owner_facts` order, then
  owners without facts), then the group without an owner, each capped by its shortfall: one
  to-do per group that gets part of it. The owner is only the pre-fill's suggestion (the person
  may save it on the other spouse); the group without an owner asks the person to pick.
- The benefit kind is read by code from the lines' text: a kind only when every line of the
  group names exactly one kind and all name the same; otherwise none, and the person picks
  (Mutterschaftsgeld and Krankengeld both come from the Krankenkasse, so a line naming
  neither is never guessed). „Arbeitslosengeld II“ (also „-II“, „2“) is not a
  Lohnersatzleistung and reads as no kind.

The warning's params are strings: `tax_year`, `booked`, `typed` (booked − difference, what
counts as covered for the group), `difference` (cents) and, when known, `owner_id` and
`benefit`; never the lines' text. Its refs are the group's allocations with their amounts and
the typed rows that cover it (the owner's own, or every owner's when other rows count).
"""

import re
from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID

from hausbuch.rules.tax._common import warning
from hausbuch.rules.tax.anlage_n._base import ZERO, cents, in_year, row_sources
from hausbuch.schemas.employment import BookedBenefit, OwnerReturnFacts, ReturnInputFact
from hausbuch.schemas.enums import ReturnInputKind
from hausbuch.schemas.tax import DraftWarning, SourceRef, WarningCode

# The benefit codes of ReplacementFact.benefit and the words that name them in a purpose text.
_KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("elterngeld", re.compile(r"elterngeld")),
    ("mutterschaftsgeld", re.compile(r"mutterschaftsgeld")),
    ("krankengeld", re.compile(r"krankengeld")),
    ("arbeitslosengeld", re.compile(r"arbeitslosengeld(?!\s*-?\s*(?:ii|2)\b)")),
)

# Kurzarbeitergeld (also „Kurzarbeit“, „KUG“): on the wage certificate's Nr. 15, never counted here.
_SHORT_TIME = re.compile(r"kurzarbeit|\bkug\b")
_SHORT_TIME_BENEFIT = "kurzarbeitergeld"


def is_short_time_allowance(text: str) -> bool:
    """Whether `text` names Kurzarbeitergeld, which the wage certificate already carries."""
    return _SHORT_TIME.search(text.lower()) is not None


def kind_of_text(text: str) -> str | None:
    """The one benefit kind `text` names, else None (none or several)."""
    lowered = text.lower()
    found = {code for code, pattern in _KINDS if pattern.search(lowered)}
    return found.pop() if len(found) == 1 else None


def benefit_kind(lines: Sequence[BookedBenefit]) -> str | None:
    """The kind every line names, else None (the person picks)."""
    kinds = {kind_of_text(line.text) for line in lines}
    if len(kinds) != 1:
        return None
    return kinds.pop()


def _typed(facts: OwnerReturnFacts, tax_year: int) -> list[ReturnInputFact]:
    return [
        r
        for r in in_year(facts.inputs, tax_year)
        if r.tax_year == tax_year
        and r.kind is ReturnInputKind.REPLACEMENT_INCOME
        and r.value.benefit != _SHORT_TIME_BENEFIT
    ]


def _refs(lines: Sequence[BookedBenefit], typed: Sequence[ReturnInputFact]) -> list[SourceRef]:
    refs = [SourceRef(kind="allocation", id=x.allocation_id, amount=x.amount) for x in lines]
    return refs + row_sources(typed)


def _todo(
    tax_year: int,
    lines: Sequence[BookedBenefit],
    typed_rows: Sequence[ReturnInputFact],
    booked: Decimal,
    typed: Decimal,
    owner_id: UUID | None,
) -> DraftWarning:
    params = {
        "tax_year": str(tax_year),
        "booked": str(cents(booked)),
        "typed": str(cents(typed)),
        "difference": str(cents(booked - typed)),
    }
    if owner_id is not None:
        params["owner_id"] = str(owner_id)
    kind = benefit_kind(lines)
    if kind is not None:
        params["benefit"] = kind
    return warning(WarningCode.RETURN_BENEFIT_BOOKED, _refs(lines, typed_rows), **params)


def booked_benefit_warnings(
    tax_year: int,
    owner_facts: Sequence[OwnerReturnFacts],
    booked: Sequence[BookedBenefit],
) -> list[DraftWarning]:
    """The `return.benefit_booked` to-dos of `tax_year` (the module docstring): while the
    household's booked lines exceed its typed rows, one per group that falls short, owners
    first (in `owner_facts` order, then owners without facts), then the lines without an
    owner; their differences sum to the household's gap."""
    of_year = [
        x for x in booked if x.booking_date.year == tax_year and not is_short_time_allowance(x.text)
    ]
    if not of_year:
        return []
    by_owner: dict[UUID, list[BookedBenefit]] = {}
    unowned: list[BookedBenefit] = []
    for line in of_year:
        if len(set(line.owner_ids)) == 1:
            by_owner.setdefault(line.owner_ids[0], []).append(line)
        else:
            unowned.append(line)

    typed_rows = {f.owner_id: _typed(f, tax_year) for f in owner_facts}
    all_rows = [r for rows in typed_rows.values() for r in rows]
    order = [f.owner_id for f in owner_facts]
    order += [o for o in by_owner if o not in typed_rows]

    # (owner, lines, own rows, booked, shortfall) per group, the group without an owner last.
    groups: list[tuple[UUID | None, list[BookedBenefit], list[ReturnInputFact], Decimal, Decimal]]
    groups = []
    for owner_id in order:
        lines = by_owner.get(owner_id, [])
        rows = typed_rows.get(owner_id, [])
        booked_sum = max(ZERO, sum((x.amount for x in lines), ZERO))
        typed_sum = sum((r.value.amount for r in rows), ZERO)
        groups.append((owner_id, lines, rows, booked_sum, max(ZERO, booked_sum - typed_sum)))
    if unowned:
        booked_sum = max(ZERO, sum((x.amount for x in unowned), ZERO))
        groups.append((None, unowned, [], booked_sum, booked_sum))

    booked_total = sum((g[3] for g in groups), ZERO)
    typed_total = sum((r.value.amount for r in all_rows), ZERO)
    gap = booked_total - typed_total
    out: list[DraftWarning] = []
    for owner_id, lines, rows, booked_sum, shortfall in groups:
        if gap <= ZERO:
            break
        difference = min(gap, shortfall)
        if difference <= ZERO:
            continue
        gap -= difference
        typed = booked_sum - difference
        own_rows_cover = owner_id is not None and difference == shortfall
        refs_rows = rows if own_rows_cover else all_rows
        out.append(_todo(tax_year, lines, refs_rows, booked_sum, typed, owner_id))
    return out
