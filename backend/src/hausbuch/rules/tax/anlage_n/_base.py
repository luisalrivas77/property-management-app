"""Helpers the Anlage N rules share (docs/phases/phase-4b.md §5). Pure, no I/O."""

from collections.abc import Iterable
from decimal import ROUND_FLOOR, Decimal

from hausbuch.rules.tax import registry
from hausbuch.rules.tax.rounding import cents
from hausbuch.schemas.employment import AnlageNRuleSet, ReturnInputFact
from hausbuch.schemas.enums import ReturnInputKind
from hausbuch.schemas.tax import SourceRef

ZERO = Decimal("0")
NOT_ZERO = Decimal("0.00")  # a computed nothing, printed with its cents

# D97/D110: the kinds that span years, read with tax_year ≤ Y. A work_equipment item is read up
# to Y because its slices fall into later years; the equipment rule decides each year's slice
# (a subscription or a low-value item counts in its purchase year only).
SPANNING_KINDS = frozenset({ReturnInputKind.CHILD, ReturnInputKind.WORK_EQUIPMENT})

__all__ = [
    "NOT_ZERO",
    "SPANNING_KINDS",
    "ZERO",
    "cents",
    "current",
    "floor_euros",
    "in_year",
    "row_sources",
    "rules_for",
    "values",
]


def floor_euros(value: Decimal) -> Decimal:
    """Down to the whole euro (a zvE, an assessed tax)."""
    return value.to_integral_value(ROUND_FLOOR)


def current(rows: Iterable[ReturnInputFact]) -> list[ReturnInputFact]:
    """The rows in force: not withdrawn and not superseded by another row (D97)."""
    rows = list(rows)
    superseded = {r.supersedes_id for r in rows if r.supersedes_id is not None}
    return [r for r in rows if not r.withdrawn and r.id not in superseded]


def in_year(rows: Iterable[ReturnInputFact], tax_year: int) -> list[ReturnInputFact]:
    """The current rows that count in `tax_year`: a yearly kind's rows of that year, a
    spanning kind's rows from that year or earlier (D97, Q-O23)."""
    return [
        r
        for r in current(rows)
        if r.tax_year == tax_year or (r.kind in SPANNING_KINDS and r.tax_year < tax_year)
    ]


def values(rows: Iterable[ReturnInputFact], kind: ReturnInputKind) -> list:
    return [r.value for r in rows if r.kind is kind]


def row_sources(rows: Iterable[ReturnInputFact]) -> list[SourceRef]:
    """Each row and its document (principle 2), without repeats."""
    out: list[SourceRef] = []
    seen: set = set()
    for r in rows:
        refs = [SourceRef(kind="return_input", id=r.id)]
        if r.document_id is not None:
            refs.append(SourceRef(kind="document", id=r.document_id))
        for ref in refs:
            if (ref.kind, ref.id) not in seen:
                seen.add((ref.kind, ref.id))
                out.append(ref)
    return out


def rules_for(tax_year: int) -> AnlageNRuleSet:
    """The year's Anlage N rule set, else the latest earlier one (as phase 4's projection
    reuses the Anlage V rule set); RuleError tax.no_rule_set before the first file."""
    return registry.anlage_n_or_latest(tax_year)
