"""The English of the figures' own sentences (phase 10 D284, step 4c), swapped in when a read
route answers a request that asked for English, beside the to-dos of `todo_texts`.

Each text here is written in German by a builder that also serves something stored or a
module whose files may not hold English: the key figures' marks (`rules/health/bands.yaml`,
hashed by `services.health.rule_version()`), the debt figures' gaps (`rules/health/figures`),
the plan's comparison words (`rules/plan/spending`), the net worth's notes (written into the
stored month-end runs too), the Budget's marks and reference points (`rules/budget`). The
builders stay German; `todo_texts.english()` walks a response and hands each of the types below
to its swap here, which returns an English copy. Nothing in this module is ever stored.

Where a structured field says what the text is (a comparison's `words` and `difference`, a
reference's `key`), the English is rebuilt from it; where the sentence carries free parts (a
figure, a label a person typed), its German is matched against a pattern (`i18n.Patterns`) and
a text no pattern matches stays German (`tests/test_i18n_read_texts.py` runs every producer).
"""

import re
from collections.abc import Callable, Mapping
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel

from hausbuch import i18n
from hausbuch.rules.health.bands import proposals
from hausbuch.schemas.budget import BudgetMark, BudgetReference, BudgetThreshold, BudgetView
from hausbuch.schemas.health import DebtGap, Light, MarkedFigure
from hausbuch.schemas.net_worth import NetWorthLine
from hausbuch.schemas.plan import ComparisonWords, SpendingComparisonRow

# --- The key figures' marks (rules/health/bands.yaml) ---------------------------------------


def _months(text: str) -> str:
    """„3,8 Monate“ → "3.8 months"."""
    if not text.endswith(" Monate"):
        return text
    return f"{i18n.CONVERTERS['number'](text.removesuffix(' Monate'))} months"


_MARK_SENTENCES: Mapping[str, str] = {
    "reserve.green": "Your reserve of {f:reserve} reaches your own target of {f:reserve_target}.",
    "reserve.amber": (
        "Your reserve lasts for {f:reserve_months}. {f:reserve_gap} are still missing to reach"
        " your own target."
    ),
    "reserve.red": (
        "Your reserve of {f:reserve} is below the limit of {f:band_reserve_red} you set yourselves."
    ),
    "savings_rate.green": (
        "Your savings rate of {f:savings_rate} is at or above your limit of"
        " {f:band_savings_rate_green}."
    ),
    "savings_rate.amber": (
        "Your savings rate of {f:savings_rate} is between your limits of"
        " {f:band_savings_rate_red} and {f:band_savings_rate_green}."
    ),
    "savings_rate.red": "In this period you spent more than you earned.",
    "savings_rate.red_limit": (
        "Your savings rate of {f:savings_rate} is below your limit of {f:band_savings_rate_red}."
    ),
    "savings_rate.red_regular": (
        "Without the one-off expenses the savings rate would be {f:savings_rate_regular}."
    ),
    "ltv.intro": "The flat's loans amount to {f:ltv} of its estimated value.",
    "ltv.green": "That is below your limit of {f:band_ltv_green}.",
    "ltv.amber": "That is between your limits of {f:band_ltv_green} and {f:band_ltv_red}.",
    "ltv.red": "That is above your limit of {f:band_ltv_red}.",
    "debt_service_ratio.intro": (
        "{f:debt_service_ratio} of your income goes to the bank as interest and repayment."
    ),
    "debt_service_ratio.green": "That is below your limit of {f:band_debt_service_green}.",
    "debt_service_ratio.amber": (
        "That is between your limits of {f:band_debt_service_green} and {f:band_debt_service_red}."
    ),
    "debt_service_ratio.red": "That is above your limit of {f:band_debt_service_red}.",
    "debt_ratio.intro": "Your debts amount to {f:debt_ratio} of your assets.",
    "debt_ratio.green": "That is below your limit of {f:band_debt_ratio_green}.",
    "debt_ratio.amber": (
        "That is between your limits of {f:band_debt_ratio_green} and {f:band_debt_ratio_red}."
    ),
    "debt_ratio.red": "That is above your limit of {f:band_debt_ratio_red}.",
    "debt_to_income.intro": "Your debts are {f:debt_to_income} times your yearly income.",
    "debt_to_income.green": "That is below your limit of {f:band_debt_to_income_green}.",
    "debt_to_income.amber": (
        "That is between your limits of {f:band_debt_to_income_green} and"
        " {f:band_debt_to_income_red}."
    ),
    "debt_to_income.red": "That is above your limit of {f:band_debt_to_income_red}.",
}


def _slot_kind(slot: str) -> str:
    """How `bands._slots` formats a slot: money, months, a multiple or a percent."""
    if slot in ("reserve", "reserve_target", "reserve_gap", "band_reserve_red"):
        return "money"
    if slot == "reserve_months":
        return "months"
    if slot.startswith(("debt_to_income", "band_debt_to_income")):
        return "number"
    return "percent"


def _mark_entry(german: str, english: str) -> tuple[str, str]:
    """bands.yaml's `{f:slot}` as a typed Patterns placeholder in German, bare in English."""
    typed = re.sub(r"\{f:([a-z_]+)\}", lambda m: f"{{{m[1]}:{_slot_kind(m[1])}}}", german)
    bare = re.sub(r"\{f:([a-z_]+)\}", lambda m: f"{{{m[1]}}}", english)
    return typed, bare


def _mark_patterns() -> dict[MarkedFigure, i18n.Patterns]:
    """One catalogue per figure, so a figure's percent and another's multiple, written in the
    same German words („Das liegt unter eurer Grenze von …“), are told apart."""
    german = proposals()[2]
    missing = set(german) ^ set(_MARK_SENTENCES)
    if missing:  # pragma: no cover - a sentence added to bands.yaml without its English
        raise RuntimeError(f"services/read_texts: the mark sentences differ: {sorted(missing)}")
    return {
        figure: i18n.Patterns(
            f"marks.{figure.value}",
            [
                _mark_entry(german[code], _MARK_SENTENCES[code])
                for code in sorted(german)
                if code.split(".")[0] == figure.value
            ],
            converters={"months": _months},
        )
        for figure in MarkedFigure
    }


MARKS = _mark_patterns()


def _light(light: Light) -> Light:
    catalogue = MARKS[light.figure]
    return light.model_copy(update={"texts": [catalogue.translate(t) for t in light.texts]})


# --- The debt figures' gaps (rules/health/figures) ------------------------------------------

DEBT_GAPS = i18n.Patterns(
    "debt_gaps",
    [
        (
            "Der geschätzte Wert der Immobilie ist 0 €; der Anteil der Kredite lässt sich nicht"
            " berechnen.",
            "The property's estimated value is €0; the loans' share cannot be calculated.",
        ),
        (
            "Das Vermögen ohne Schulden ist 0 € oder weniger; der Verschuldungsgrad lässt sich"
            " nicht berechnen.",
            "The assets before debts are €0 or less; the debt ratio cannot be calculated.",
        ),
        (
            "Das Jahreseinkommen aus dem Zeitraum ist 0 € oder weniger; die Kennzahl lässt sich"
            " nicht berechnen.",
            "The yearly income from the period is €0 or less; the figure cannot be calculated.",
        ),
    ],
)


def _debt_gap(gap: DebtGap) -> DebtGap:
    return gap.model_copy(update={"reason": DEBT_GAPS.translate(gap.reason)})


# --- The plan's comparison (rules/plan/spending.words) ----------------------------------------


def _comparison(row: SpendingComparisonRow) -> SpendingComparisonRow:
    """Rebuilt from `words` and `difference` as `spending.words` builds the German: whole
    euros half-up."""
    if row.words is ComparisonWords.AS_PLANNED:
        text = "as planned"
    else:
        whole = abs(row.difference).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        amount = f"€{i18n.number(whole, locale=i18n.EN)}"
        side = "more" if row.words is ComparisonWords.MORE else "less"
        text = f"{amount} {side} than planned"
    return row.model_copy(update={"text": text})


# --- The net worth's lines (services/net_worth) ---------------------------------------------

NET_WORTH_NOTES = i18n.Patterns(
    "net_worth_notes",
    [
        ("Kurs aus dem letzten Bericht", "Price from the latest report"),
        (
            "laut Depotbericht vom {d:date} {n:number} Anteile, keiner gebucht",
            "{n} units by the depot report of {d}, none booked",
        ),
        (
            "Schätzwert: „Wert heute“ aus dem Szenario „{name}“ (Planung)",
            "Estimate: “Value today” from the scenario “{name}” (Planning)",
        ),
        (
            "Schätzwert: der Kaufpreis; einen aktuellen Wert unter Planung als „Wert heute“"
            " eintragen",
            "Estimate: the purchase price; enter a current value under Planning as “Value today”",
        ),
        ("Restschuld laut Tilgungsplan", "Remaining balance by the repayment schedule"),
        ("Schätzwert, zum Monatsende gespeichert", "Estimate, stored at the month-end"),
        (
            "Restschuld laut Tilgungsplan, zum Monatsende gespeichert",
            "Remaining balance by the repayment schedule, stored at the month-end",
        ),
        (
            "Bisher bezahlter Teil des Kaufpreises; zählt bis zur Übergabe am {d:date}, danach"
            " der Schätzwert; zum Monatsende gespeichert",
            "Part of the purchase price paid so far; counts until the handover on {d}, then the"
            " estimate; stored at the month-end",
        ),
        (
            "Bisher bezahlter Teil des Kaufpreises; zählt bis zur Übergabe am {d:date}, danach"
            " der Schätzwert",
            "Part of the purchase price paid so far; counts until the handover on {d}, then the"
            " estimate",
        ),
        (
            "Bisher ausgezahlt; bis zur Übergabe am {d:date}, danach Restschuld laut"
            " Tilgungsplan; zum Monatsende gespeichert",
            "Paid out so far; until the handover on {d}, then the remaining balance by the"
            " repayment schedule; stored at the month-end",
        ),
        (
            "Bisher ausgezahlt; bis zur Übergabe am {d:date}, danach Restschuld laut Tilgungsplan",
            "Paid out so far; until the handover on {d}, then the remaining balance by the"
            " repayment schedule",
        ),
        (
            "Am {d:date} von „{sender}“ überwiesen; zählt hier, bis ein Kontoauszug dieses"
            " Kontos den Tag abdeckt",
            "Sent from “{sender}” on {d}; counts here until a statement of this account covers"
            " that day",
        ),
    ],
)
NET_WORTH_LABELS = i18n.Patterns(
    "net_worth_labels",
    [
        ("Anzahlung Kaufpreis – {label}", "Purchase price paid in advance – {label}"),
        ("Wertpapier", "Security"),
        ("Unterwegs zu „{label}“", "In transit to “{label}”"),  # phase 6 D292 (3)
    ],
)


def _net_worth_line(line: NetWorthLine) -> NetWorthLine:
    update: dict[str, Any] = {}
    if line.note is not None:
        update["note"] = NET_WORTH_NOTES.translate(line.note)
    if line.kind.value in ("property", "position") or line.in_transit:
        update["label"] = NET_WORTH_LABELS.translate(line.label)
    return line.model_copy(update=update)


# --- The Budget (rules/budget) ----------------------------------------------------------------

_BUDGET_LABELS = {
    "Notwendiges": "Needs",
    "Wünsche": "Wants",
    "Sparen": "Saving",
    "Wohnen": "Housing",
    "Einmalige Ausgaben": "One-off expenses",
    "Ohne Einordnung": "Not categorised",
}
_WORDS = {
    "über": "above",
    "unter": "below",
    "mindestens": "at least",
    "höchstens": "at most",
    "mehr": "more",
    "Luft": "to spare",
    "zu wenig": "too little",
    "zu viel": "too much",
}


def _word(text: str) -> str:
    return _WORDS.get(text, text)


_SHARE = "{l:line}: {p:percent} eures Einkommens"
_SHARE_EN = "{l}: {p} of your income"
_GOAL = "eurem Ziel von {bound:word} {t:percent}"
_GOAL_EN = "your target of {bound} {t}"

BUDGET_MARKS = i18n.Patterns(
    "budget_marks",
    [
        (f"{_SHARE}, genau auf {_GOAL}.", f"{_SHARE_EN}, exactly at {_GOAL_EN}."),
        (
            f"{_SHARE}, {{pts:number}} Prozentpunkte {{side:word}} {_GOAL}"
            " ({m:money} im Monat {spare:word}).",
            f"{_SHARE_EN}, {{pts}} percentage points {{side}} {_GOAL_EN} ({{m}} a month"
            " {spare}).",
        ),
        (
            f"{_SHARE}, {{pts:number}} Prozentpunkte {{side:word}} {_GOAL}; das sind {{m:money}}"
            " im Monat {short:word}.",
            f"{_SHARE_EN}, {{pts}} percentage points {{side}} {_GOAL_EN}; that is {{m}} a month"
            " {short}.",
        ),
        (
            "Im Zeitraum war euer Einkommen nicht größer als 0 €; daher zeigt das Budget keine"
            " Anteile und keine Markierungen.",
            "In the period your income was not above €0, so the Budget shows no shares and no"
            " marks.",
        ),
    ],
    converters={"line": lambda text: _BUDGET_LABELS.get(text, text), "word": _word},
)


def _budget_mark(mark: BudgetMark) -> BudgetMark:
    return mark.model_copy(update={"text": BUDGET_MARKS.translate(mark.text)})


def _budget_view(view: BudgetView) -> BudgetView:
    if view.no_shares is None:
        return view
    return view.model_copy(update={"no_shares": BUDGET_MARKS.translate(view.no_shares)})


# The reference points' English by key (rules/budget/references.yaml stays German): label,
# source, what the source measures and why it is not the app's figure.
REFERENCES: Mapping[str, tuple[str, str, str, str]] = {
    "split_50_30_20": (
        "50/30/20",
        "Elizabeth Warren, Amelia Warren Tyagi: All Your Worth (2005)",
        "Needs, wants and saving as shares of net income.",
        "A rule of thumb from the USA; no German authority sets a split. Your income in"
        " Hausbuch also holds the flat's result.",
    ),
    "split_60_20_20": (
        "60/20/20",
        "Sparkasse.de",
        "Needs, wants and saving as shares of net income; named by the Sparkasse beside"
        " 50/30/20 when fixed costs are high.",
        "A rule of thumb, not a requirement; no German authority sets a split.",
    ),
    "split_70_20_10": (
        "70/20/10",
        "Sparkasse.de",
        "Needs, wants and saving as shares of net income; named by the Sparkasse beside"
        " 50/30/20 when fixed costs are high.",
        "A rule of thumb, not a requirement; no German authority sets a split.",
    ),
    "housing_iwu_mieterbund": (
        "Rent burden (IWU for the German Tenants' Association)",
        "IWU study for the Deutscher Mieterbund, as of 30 March 2026",
        "A tenant household's gross cold rent as a share of its net household income.",
        "Not the same figure as your housing share: Hausbuch counts all spending of the group"
        " Housing (energy and the broadcasting fee too) and divides by your income including"
        " the flat's result.",
    ),
    "housing_destatis_eu_silc": (
        "Housing cost overburden (Destatis, EU-SILC)",
        "Federal Statistical Office (Destatis), EU-SILC",
        "All housing costs less allowances (rent, the owners' interest and property tax,"
        " energy, upkeep) as a share of disposable household income.",
        "Not the same figure as your housing share: Destatis uses disposable household income"
        " and all housing costs, Hausbuch the spending of the group Housing and your income"
        " including the flat's result.",
    ),
    "housing_sparkasse_third": (
        "Rule of thumb: at most one third",
        "Sparkasse",
        "The cold rent as a share of net income.",
        "A rule of thumb for the cold rent alone; your housing share holds all spending of the"
        " group Housing.",
    ),
}
THRESHOLDS: Mapping[str, str] = {
    "vertretbar": "acceptable",
    "hoch": "high",
    "Überlastung": "overburden",
    "Wohnkostenüberbelastung": "housing cost overburden",
    "höchstens ein Drittel": "at most one third",
}


def _threshold(band: BudgetThreshold) -> BudgetThreshold:
    return band.model_copy(update={"label": THRESHOLDS.get(band.label, band.label)})


def _reference(ref: BudgetReference) -> BudgetReference:
    found = REFERENCES.get(ref.key)
    if found is None:
        return ref
    label, source, measures, does_not_measure = found
    return ref.model_copy(
        update={
            "label": label,
            "source": source,
            "measures": measures,
            "does_not_measure": does_not_measure,
            "thresholds": [_threshold(t) for t in ref.thresholds],
        }
    )


# What `todo_texts.english()` swaps besides the to-dos: each type's English copy, applied after
# the walk has gone through the value's own fields (a BudgetView's marks and references).
SWAPS: Mapping[type[BaseModel], Callable[[Any], BaseModel]] = {
    Light: _light,
    DebtGap: _debt_gap,
    SpendingComparisonRow: _comparison,
    NetWorthLine: _net_worth_line,
    BudgetMark: _budget_mark,
    BudgetView: _budget_view,
    BudgetReference: _reference,
}
