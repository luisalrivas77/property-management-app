"""Rendering a validated text (docs/phases/phase-9.md §8.4). Fixture:
tests/fixtures/phase9/explain_render.yaml.

`{f:key}` becomes the figure in German format (the formats of rules/health/bands.py, which the
marks' sentences already use: whole euros half-up with the thousands point, a percent with one
decimal, months with one decimal and „Monate“, a month as „März 2028“, a count as digits, a
multiple with one decimal), `{k:}` and `{p:}` the stored label, `{c:}` the class's fixed German
word, `{a:}` the asset's stored name. A slot the facts do not hold, a key without a label, or
anything left that looks like a slot raises: an unknown slot is never printed.
"""

import re
from collections.abc import Sequence
from decimal import Decimal

from hausbuch.rules.advice import rules
from hausbuch.rules.health.bands import (
    format_money,
    format_months,
    format_multiple,
    format_percent,
)
from hausbuch.schemas.advice import (
    ExplainFacts,
    ExplainLabels,
    ExplainOutput,
    Fact,
    FigureType,
    Paragraph,
)
from hausbuch.schemas.household import NetWorthClass

CLASS_WORDS = {
    NetWorthClass.BANK: "Bankguthaben",
    NetWorthClass.DEPOT: "Fonds und Wertpapiere",
    NetWorthClass.CRYPTO: "Krypto",
    NetWorthClass.PROPERTY: "Immobilien",
    NetWorthClass.LIABILITIES: "Schulden",
}
MONTH_NAMES = (
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)


class UnknownSlot(ValueError):
    """A slot the facts or the labels cannot fill; the text is not shown."""


def format_fact(fact: Fact) -> str:
    if fact.type is FigureType.MONTH:
        year, month = fact.value.split("-")
        return f"{MONTH_NAMES[int(month) - 1]} {year}"
    value = Decimal(fact.value)
    match fact.type:
        case FigureType.MONEY:
            return format_money(value)
        case FigureType.PERCENT:
            return format_percent(value)
        case FigureType.MONTHS:
            return format_months(value)
        case FigureType.MULTIPLE:
            return format_multiple(value)
        case _:
            return str(int(value))


def render_text(text: str, facts: ExplainFacts, labels: ExplainLabels) -> str:
    """One paragraph's text with every slot filled."""
    pattern = re.compile(rules()["slot_pattern"])

    def one(match: re.Match[str]) -> str:
        letter, key = match.groups()
        if letter == "f":
            fact = facts.figure(key)
            if fact is None:
                raise UnknownSlot(match.group(0))
            return format_fact(fact)
        if letter == "c":
            if key not in {c.value for c in facts.classes}:
                raise UnknownSlot(match.group(0))
            return CLASS_WORDS[NetWorthClass(key)]
        held, named = {
            "k": (facts.accounts, labels.accounts),
            "p": (facts.plans, labels.plans),
            "a": (facts.assets, labels.assets),
        }[letter]
        if key not in held or key not in named:
            raise UnknownSlot(match.group(0))
        return named[key]

    out = []
    end = 0
    for match in pattern.finditer(text):
        piece = text[end : match.start()]
        if "{" in piece or "}" in piece:
            raise UnknownSlot(piece)
        out += [piece, one(match)]
        end = match.end()
    rest = text[end:]
    if "{" in rest or "}" in rest:
        raise UnknownSlot(rest)
    return "".join([*out, rest])


def render(
    output: ExplainOutput | Sequence[Paragraph], facts: ExplainFacts, labels: ExplainLabels
) -> list[str]:
    """The paragraphs in German, in order. Runs on validated text only (§8.4)."""
    paragraphs = output.paragraphs if isinstance(output, ExplainOutput) else output
    return [render_text(p.text, facts, labels) for p in paragraphs]
