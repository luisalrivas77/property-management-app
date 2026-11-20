"""The plan explanation in English (phase 10 D284, Q-O178): the app's own template text.

The validator, its lexicon and the evals are German (D255, D258), so a model text is never
shown in English, and never translated: in English the screen gets the template text of
`rules/advice/template.py`, chosen from the same facts by the same rules, each German sentence
part swapped for its English beside it below and its slots filled in English formats. Nothing
here is stored; the German parts stay the source, and `rules/advice/` is unchanged.
"""

import re
from decimal import ROUND_HALF_UP, Decimal

from hausbuch import i18n
from hausbuch.rules.advice import rules
from hausbuch.rules.advice import template as T
from hausbuch.schemas.advice import (
    AdviceMode,
    ExplainFacts,
    Fact,
    FigureType,
    Paragraph,
    RenderedParagraph,
)
from hausbuch.schemas.household import NetWorthClass

# Each sentence part of the template (split as `template.written` splits it), with its English.
PARTS: dict[str, str] = {
    "Im Schnitt der letzten {f:basis_months} Monate bleiben euch {f:free_cash} im Monat.": (
        "On average over the last {f:basis_months} months you have {f:free_cash} left each month."
    ),
    "Nach dem Puffer von {f:buffer} könnt ihr {f:investable} anlegen.": (
        "After the buffer of {f:buffer} you can invest {f:investable}."
    ),
    "Im letzten abgeschlossenen Monat bleiben euch {f:free_cash}.": (
        "In the last closed month you have {f:free_cash} left."
    ),
    "Der Betrag geht ganz in den Puffer von {f:buffer}, zum Anlegen ist nichts übrig.": (
        "The amount goes entirely into the buffer of {f:buffer}; nothing is left to invest."
    ),
    "Im letzten abgeschlossenen Monat ist nach den laufenden Ausgaben nichts zum Anlegen übrig.": (
        "In the last closed month nothing is left to invest after the running costs."
    ),
    "Im Schnitt der letzten {f:basis_months} Monate ist nach den laufenden Ausgaben nichts "
    "zum Anlegen übrig.": (
        "On average over the last {f:basis_months} months nothing is left to invest after the "
        "running costs."
    ),
    "Davon fließen schon {f:already_invested} im Monat in eure Sparpläne, danach sind es noch "
    "{f:investable_left}.": (
        "Of this, {f:already_invested} a month already goes into your savings plans, which "
        "leaves {f:investable_left}."
    ),
    "Für eure Sparpläne reicht der Betrag nicht, den ihr nach dem Puffer anlegen könnt.": (
        "The amount you can invest after the buffer does not cover your savings plans."
    ),
    "Eure Reserve reicht für {f:reserve_months}.": "Your reserve lasts {f:reserve_months}.",
    "Euer Ziel sind {f:reserve_target_months}, es fehlen noch {f:reserve_gap}.": (
        "Your target is {f:reserve_target_months}; {f:reserve_gap} is still missing."
    ),
    "Eure Reserve liegt bei {f:reserve} und reicht damit für keinen Monat.": (
        "Your reserve stands at {f:reserve} and so does not last a single month."
    ),
    "Eure Reserve liegt bei {f:reserve}.": "Your reserve stands at {f:reserve}.",
    "Eure Reserve von {f:reserve} hat euer Ziel erreicht.": (
        "Your reserve of {f:reserve} has reached your target."
    ),
    "Wenn alles so weiterläuft wie bisher, ist die Reserve im {f:path_as_is_reached} voll.": (
        "If everything carries on as before, the reserve is full in {f:path_as_is_reached}."
    ),
    "Mit allen Sparplänen pausiert wäre die Reserve im {f:path_reserve_first_reached} voll.": (
        "With all savings plans paused, the reserve would be full in "
        "{f:path_reserve_first_reached}."
    ),
    "Von eurem Einkommen habt ihr {f:savings_rate} gespart.": (
        "You saved {f:savings_rate} of your income."
    ),
    "Ohne die Einmalausgaben wären es {f:savings_rate_regular}.": (
        "Without the one-off expenses it would be {f:savings_rate_regular}."
    ),
    "Davon sind {f:principal_total} Tilgung des Kredits der Wohnung.": (
        "Of this, {f:principal_total} is repayment of the flat's loan."
    ),
    "Ihr habt mehr ausgegeben als eingenommen, eure Sparquote liegt bei {f:savings_rate}.": (
        "You spent more than you earned; your savings rate stands at {f:savings_rate}."
    ),
    "Die Kredite der Wohnung machen {f:ltv} ihres geschätzten Werts aus.": (
        "The flat's loans amount to {f:ltv} of its estimated value."
    ),
    "Für Zins und Tilgung gehen {f:debt_service_ratio} eures Einkommens an die Bank.": (
        "{f:debt_service_ratio} of your income goes to the bank for interest and repayment."
    ),
    "Euer Vermögen liegt bei {f:net_worth}, das sind {f:net_worth_change} mehr als im Vormonat.": (
        "Your net worth stands at {f:net_worth}, {f:net_worth_change} more than the month before."
    ),
    "Euer Vermögen liegt bei {f:net_worth}.": "Your net worth stands at {f:net_worth}.",
    "Die Veränderung zum Vormonat beträgt {f:net_worth_change}.": (
        "The change from the month before is {f:net_worth_change}."
    ),
    "Die Zahlen stützen sich erst auf {f:basis_months} abgeschlossene Monate und sind auf das "
    "Jahr hochgerechnet.": (
        "The figures rest on only {f:basis_months} closed months and are projected to the year."
    ),
    "Die Zahlen stützen sich erst auf einen abgeschlossenen Monat und sind auf das Jahr "
    "hochgerechnet.": ("The figures rest on only one closed month and are projected to the year."),
    "Die Zahlen stützen sich auf {f:basis_months} abgeschlossene Monate.": (
        "The figures rest on {f:basis_months} closed months."
    ),
    "Mit eurer Annahme von {f:assumed_change_depot} pro Jahr für {c:depot} läge euer Vermögen "
    "Ende {f:projection_until} bei {f:projection_total}.": (
        "With your assumption of {f:assumed_change_depot} a year for {c:depot}, your net worth "
        "would stand at {f:projection_total} at the end of {f:projection_until}."
    ),
    "Mit eurer Annahme von {f:assumed_change_crypto} pro Jahr für {c:crypto} läge euer "
    "Vermögen Ende {f:projection_until} bei {f:projection_total}.": (
        "With your assumption of {f:assumed_change_crypto} a year for {c:crypto}, your net "
        "worth would stand at {f:projection_total} at the end of {f:projection_until}."
    ),
    "Mit euren Annahmen von {f:assumed_change_depot} für {c:depot} und "
    "{f:assumed_change_crypto} für {c:crypto} pro Jahr stammen {f:projection_assumption_part} "
    "der Veränderung aus diesen Annahmen.": (
        "With your assumptions of {f:assumed_change_depot} for {c:depot} and "
        "{f:assumed_change_crypto} for {c:crypto} a year, {f:projection_assumption_part} of the "
        "change comes from these assumptions."
    ),
    "Für diesen Zeitraum gibt es noch keine Zahlen.": "There are no figures for this period yet.",
}

CLASS_WORDS = {
    NetWorthClass.BANK: "bank balances",
    NetWorthClass.DEPOT: "funds and securities",
    NetWorthClass.CRYPTO: "crypto",
    NetWorthClass.PROPERTY: "property",
    NetWorthClass.LIABILITIES: "debts",
}


def parts(text: str) -> list[str]:
    """A German template text's sentence parts, as `template.written` cuts them."""
    return [part.strip() for part in T._SENTENCE.findall(text)]


def _whole(value: Decimal, places: int) -> str:
    rounded = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return i18n.number(rounded, places, i18n.EN)


def format_fact(fact: Fact) -> str:
    """The English of `rules/advice/render.format_fact`: „9.990 €“ → "€9,990", „73,9 %“ →
    "73.9%", „3,8 Monate“ → "3.8 months", „März 2028“ → "March 2028"."""
    if fact.type is FigureType.MONTH:
        year, month = fact.value.split("-")
        return i18n.month(int(year), int(month), i18n.EN)
    value = Decimal(fact.value)
    match fact.type:
        case FigureType.MONEY:
            sign = "-" if value.quantize(Decimal(1), rounding=ROUND_HALF_UP) < 0 else ""
            return f"{sign}€{_whole(abs(value), 0)}"
        case FigureType.PERCENT:
            return f"{_whole(value * 100, 1)}%"
        case FigureType.MONTHS:
            return f"{_whole(value, 1)} months"
        case FigureType.MULTIPLE:
            return _whole(value, 1)
        case _:
            return str(int(value))


def _fill(text: str, facts: ExplainFacts) -> str:
    def one(match: re.Match[str]) -> str:
        letter, key = match.groups()
        if letter == "f":
            fact = facts.figure(key)
            if fact is not None:
                return format_fact(fact)
        elif letter == "c" and key in {c.value for c in facts.classes}:
            return CLASS_WORDS[NetWorthClass(key)]
        raise ValueError(f"the template cannot fill {match.group(0)}")  # never printed

    return re.sub(rules()["slot_pattern"], one, text)


def english(paragraphs: list[Paragraph], facts: ExplainFacts) -> list[RenderedParagraph]:
    """Template paragraphs (German, slotted) rendered in English."""
    return [
        RenderedParagraph(
            topic=p.topic, text=" ".join(_fill(PARTS[part], facts) for part in parts(p.text))
        )
        for p in paragraphs
    ]


def template(facts: ExplainFacts, mode: AdviceMode) -> list[RenderedParagraph]:
    """The app's template text from these facts, in English."""
    return english(T.render(facts, mode), facts)
