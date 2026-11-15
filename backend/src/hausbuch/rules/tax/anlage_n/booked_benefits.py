"""Wage-replacement benefits booked on the accounts but not typed as Lohnersatzleistung (phase 6
D159 amended (8), 15 Nov 2026; phase 4b D102). Pure, no I/O, Decimal throughout.

The household's private lines categorised `hh_wage_replacement` (Elterngeld, Mutterschaftsgeld,
Krankengeld, Arbeitslosengeld, Kurzarbeitergeld) are evidence of the year's
Lohnersatzleistungen, which only the `replacement_income` return inputs count (the
Progressionsvorbehalt). Principle 7: when the confirmed lines booked in the tax year (by booking
date) sum to more than the typed rows, the estimate carries the to-do `return.benefit_booked`
with the difference, and the person confirms it through the existing return-input form; nothing
is written here, and the estimate's figures are unchanged.

- A line's owner is its account's only current owner. A line on an account with several
  owners (or none recorded) has no owner: those lines form one group whose to-do asks the
  person to pick the owner, and they count as covered only by what the owners typed beyond
  their own lines.
- Per owner: booked = Σ the owner's lines (signed, so a payment back lowers it), typed = Σ the
  owner's current `replacement_income` rows of the year; a to-do when booked > typed, with
  difference = booked − typed.
- The unowned group: difference = Σ its lines − Σ over the owners of max(0, typed − booked).
- The benefit kind is read by code from the lines' text: a kind only when every line of the
  group names exactly one kind and all name the same; otherwise none, and the person picks
  (Mutterschaftsgeld and Krankengeld both come from the Krankenkasse, so a line naming
  neither is never guessed). „Arbeitslosengeld II“ is not a Lohnersatzleistung and reads as no
  kind.

The warning's params are strings: `tax_year`, `booked`, `typed`, `difference` (cents) and,
when known, `owner_id` and `benefit`; never the lines' text. Its refs are the lines'
allocations with their amounts and the typed rows.
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
    ("arbeitslosengeld", re.compile(r"arbeitslosengeld(?!\s*(?:ii|2)\b)")),
    ("kurzarbeitergeld", re.compile(r"kurzarbeitergeld")),
)


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
        if r.tax_year == tax_year and r.kind is ReturnInputKind.REPLACEMENT_INCOME
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
    """The `return.benefit_booked` to-dos of `tax_year` (the module docstring): one per owner
    whose booked lines exceed the typed rows, in `owner_facts` order (then owners without
    facts), and one for the lines without an owner."""
    of_year = [x for x in booked if x.booking_date.year == tax_year]
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
    order = [f.owner_id for f in owner_facts]
    order += [o for o in by_owner if o not in typed_rows]

    out: list[DraftWarning] = []
    surplus = ZERO
    for owner_id in order:
        lines = by_owner.get(owner_id, [])
        rows = typed_rows.get(owner_id, [])
        booked_sum = sum((x.amount for x in lines), ZERO)
        typed_sum = sum((r.value.amount for r in rows), ZERO)
        if booked_sum > typed_sum:
            out.append(_todo(tax_year, lines, rows, booked_sum, typed_sum, owner_id))
        else:
            surplus += typed_sum - booked_sum
    if unowned:
        booked_sum = sum((x.amount for x in unowned), ZERO)
        if booked_sum > surplus:
            rows = [r for rows in typed_rows.values() for r in rows]
            out.append(_todo(tax_year, unowned, rows, booked_sum, surplus, None))
    return out
