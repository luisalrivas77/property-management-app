"""The to-dos' English (phase 10 D284, step 4c): the key figures', the plan's, the Budget's and
the net worth's to-dos, and a wallet's waiting coins, translated when a response is read.

Their German is written in `rules/health/`, `rules/plan/` and `rules/budget/` (which may not
hold English: the first two feed `services.health.rule_version()`) and in the services that
build the net worth, the key figures and the plan. Those builders also serve the month-end
capture, the stored key figures and the daily task, so they stay German; the read routes pass
their answer through `english()` instead, which swaps each to-do's text and button words for
the English of the pattern its German matches. A German text no pattern matches stays German
(`tests/test_i18n_todos.py` runs every producer against this catalogue).
"""

import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from hausbuch import i18n
from hausbuch.schemas.chain import WalletWaiting
from hausbuch.schemas.goal import GoalAssumption
from hausbuch.schemas.health import HealthTodo
from hausbuch.schemas.net_worth import NetWorthTodo
from hausbuch.services.category_texts import english_label
from hausbuch.services.read_texts import SWAPS


def _nested(text: str) -> str:
    """A to-do's text inside another (a month-end's first gap, a wallet's waiting coins)."""
    return PATTERNS.find(text) or text


_GERMAN_NUMBER = re.compile(r"(?<![\w.,])(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?)(?= )")


def _coins(text: str) -> str:
    """„1.234,5 SOL, 2 ETH und 0,5 Ether (Portal)“ → "1,234.5 SOL, 2 ETH and 0.5 Ether (Portal)"."""
    text = _GERMAN_NUMBER.sub(lambda m: i18n.CONVERTERS["number"](m.group(1)), text)
    head, sep, last = text.rpartition(" und ")
    return f"{head} and {last}" if sep else text


def _waiting_count(text: str) -> str:
    """„1 Transaktion wartet“, „3 Transaktionen warten“ → "1 transaction waiting"."""
    count = text.split(" ")[0]
    return f"{count} transaction waiting" if count == "1" else f"{count} transactions waiting"


_CLASSES = {
    "Fonds und Wertpapiere": "funds and securities",
    "Krypto": "crypto",
    "Fonds und Wertpapiere und Krypto": "funds and securities and crypto",
}


def _classes(text: str) -> str:
    return _CLASSES.get(text, text)


def _quoted(text: str) -> str:
    """A position named with its account, „MSCI World („Depot“)“ → "MSCI World (“Depot”)"."""
    return text.translate(_QUOTES)


_QUOTES = str.maketrans({"„": "“", "“": "”"})


def _counted(text: str) -> str:
    """„noch kein abgeschlossener Monat“, „erst ein …“, „erst 2 abgeschlossene Monate“."""
    if text == "noch kein abgeschlossener Monat":
        return "no closed month yet"
    if text == "erst ein abgeschlossener Monat":
        return "only one closed month"
    count = text.removeprefix("erst ").removesuffix(" abgeschlossene Monate")
    return f"only {count} closed months" if count.isdigit() else text


_UNTIL = "Der Monat zählt erst mit, wenn sie"
_SCENARIO_ONE_OFF = "der nach dem Ende des Zeitraums der Rechnung liegt; sie zählt nicht mit."
_SCENARIO_ONE_OFFS = "die nach dem Ende des Zeitraums der Rechnung liegen; sie zählen nicht mit."
_KEY_FIGURES = "deshalb werden die Kennzahlen des Monats noch nicht festgehalten"
_KEY_FIGURES_EN = "so the month's key figures are not recorded yet"
_PROJECTION = "deshalb rechnet Hausbuch das Vermögen in einigen Jahren noch nicht"
_PROJECTION_EN = "so Hausbuch does not calculate the net worth in some years yet"
_INCOMPLETE = "Das Nettovermögen zum {d:date} ist noch unvollständig, "
_INCOMPLETE_EN = "The net worth on {d} is still incomplete, "

PATTERNS = i18n.Patterns(
    "todos",
    [
        # rules/health/window.py: a month that is not closed yet, and too few closed months.
        (
            "Für „{label}“ fehlen Umsätze bis zum Ende von {m}; der Monat zählt erst mit, wenn"
            " sie da sind.",
            "Transactions of “{label}” are missing up to the end of {m}; the month counts only"
            " once they are in.",
        ),
        (
            f"Ein Umsatz aus {{m}} wartet in „Zu prüfen“. {_UNTIL} bestätigt sind.",
            "One transaction from {m} is waiting in “To review”. The month counts only once it"
            " is confirmed.",
        ),
        (
            f"{{n}} Umsätze aus {{m}} warten in „Zu prüfen“. {_UNTIL} bestätigt sind.",
            "{n} transactions from {m} are waiting in “To review”. The month counts only once"
            " they are confirmed.",
        ),
        (
            f"Eine Umbuchung aus {{m}} hat noch keine Gegenbuchung. {_UNTIL} zugeordnet sind.",
            "A transfer between own accounts from {m} has no matching transaction yet. The"
            " month counts only once it is matched.",
        ),
        (
            f"{{n}} Umbuchungen aus {{m}} haben noch keine Gegenbuchung. {_UNTIL} zugeordnet sind.",
            "{n} transfers between own accounts from {m} have no matching transaction yet. The"
            " month counts only once they are matched.",
        ),
        (
            "Für Sparquote, Reserve und Schuldenkennzahlen braucht es mindestens {min}"
            " abgeschlossene Monate in Folge; bisher ist es {n:counted}.",
            "The savings rate, the reserve and the debt figures need at least {min} closed"
            " months in a row; so far there is {n}.",
        ),
        # rules/health/bands.py
        (
            "Die Kennzahlen bekommen erst eine Ampel, wenn ihr eure Grenzen festgelegt habt;"
            " die App schlägt Werte vor, die ihr übernehmen oder ändern könnt.",
            "The key figures get a traffic light only once you have set your limits; the app"
            " suggests values you can apply or change.",
        ),
        # rules/health/figures.py
        (
            "Der Kontostand von „{label}“ ist vom {d:date}; bis zum {t:date} fehlt der nächste"
            " Kontoauszug, und die Reserve zählt den alten Stand.",
            "The balance of “{label}” is from {d}; the next statement up to {t} is missing, and"
            " the reserve counts the old balance.",
        ),
        (
            "Für den {d:date} ist kein Monatsabschluss gespeichert; der Verlauf hat dort eine"
            " Lücke.",
            "No month-end is stored for {d}; the history has a gap there.",
        ),
        # rules/plan/projection.py
        (
            "Die Rechnung beginnt beim Monatsabschluss vom {d:date}; er ist noch nicht"
            " gespeichert. Hausbuch hält ihn am nächsten Morgen selbst fest, mit"
            " „Monatsabschluss festhalten“ sofort.",
            "The calculation starts at the month-end of {d}; it is not stored yet. Hausbuch"
            " records it by itself the next morning, or at once with “Record month-end”.",
        ),
        (
            "Für die Wohnung ist noch kein Standard-Szenario gespeichert; ohne es kennt die"
            " Rechnung weder ihren Wert noch ihre Zahlungen in den kommenden Jahren.",
            "No default scenario is saved for the flat yet; without it the calculation knows"
            " neither its value nor its payments in the coming years.",
        ),
        (
            "Für {words:classes} fehlt eure angenommene Wertänderung pro Jahr. Hausbuch nimmt"
            " keinen Wert an; tragt ihr eine ein, rechnet die Karte mit ihr.",
            "Your assumed yearly change in value is missing for {words}. Hausbuch assumes no"
            " value; once you enter one, the card calculates with it.",
        ),
        # rules/investment_split.py (phase 10 §7.11 (a) 2)
        (
            "Für {words:classes} fehlt eure angenommene Rendite pro Jahr. Ohne sie legt euer Ziel"
            " das Geld über der Reserve nicht an; Hausbuch nimmt keinen Wert an.",
            "Your assumed yearly return is missing for {words}. Without it your goal does not"
            " invest the money above the reserve; Hausbuch assumes no value.",
        ),
        # rules/budget/budget.py
        (
            "Im Zeitraum sind eine Zahlung ohne Einordnung gebucht, zusammen {a:money}; sie"
            " zählen weder als Notwendiges noch als Wünsche.",
            "In the period one payment of {a} is booked without a category; it counts neither"
            " as needs nor as wants.",
        ),
        (
            "Im Zeitraum sind {n} Zahlungen ohne Einordnung gebucht, zusammen {a:money}; sie"
            " zählen weder als Notwendiges noch als Wünsche.",
            "In the period {n} payments are booked without a category, {a} together; they count"
            " neither as needs nor as wants.",
        ),
        # services/health.py
        (
            "Für den {d:date} ist noch kein Monatsabschluss gespeichert; Hausbuch hält ihn am"
            " nächsten Morgen selbst fest, mit „Monatsabschluss festhalten“ sofort.",
            "No month-end is stored for {d} yet; Hausbuch records it by itself the next"
            " morning, or at once with “Record month-end”.",
        ),
        (
            "{n} Positionen haben am Monatsende keinen Wert.",
            "{n} positions have no value at the month-end.",
        ),
        (
            f"{_INCOMPLETE}{_KEY_FIGURES}: {{gap:todo}} (1 weitere Lücke im Nettovermögen)",
            f"{_INCOMPLETE_EN}{_KEY_FIGURES_EN}: {{gap}} (1 more gap in the net worth)",
        ),
        (
            f"{_INCOMPLETE}{_KEY_FIGURES}: {{gap:todo}}"
            " ({more:count} weitere Lücken im Nettovermögen)",
            f"{_INCOMPLETE_EN}{_KEY_FIGURES_EN}: {{gap}} ({{more}} more gaps in the net worth)",
        ),
        (
            f"{_INCOMPLETE}{_KEY_FIGURES}: {{gap:todo}}",
            f"{_INCOMPLETE_EN}{_KEY_FIGURES_EN}: {{gap}}",
        ),
        # services/plan.py
        (
            f"{_INCOMPLETE}{_PROJECTION}: {{gap:todo}} (1 weitere Lücke im Nettovermögen)",
            f"{_INCOMPLETE_EN}{_PROJECTION_EN}: {{gap}} (1 more gap in the net worth)",
        ),
        (
            f"{_INCOMPLETE}{_PROJECTION}: {{gap:todo}}"
            " ({more:count} weitere Lücken im Nettovermögen)",
            f"{_INCOMPLETE_EN}{_PROJECTION_EN}: {{gap}} ({{more}} more gaps in the net worth)",
        ),
        (
            f"{_INCOMPLETE}{_PROJECTION}: {{gap:todo}}",
            f"{_INCOMPLETE_EN}{_PROJECTION_EN}: {{gap}}",
        ),
        (
            "Das Szenario nennt ein Konto für die Reserve, das es nicht mehr gibt; es zählt"
            " nicht mit.",
            "The scenario names an account for the reserve that no longer exists; it does not"
            " count.",
        ),
        (
            "Das Szenario zählt ein Konto zur Reserve, das kein Giro-, Spar-,"
            " Kreditkarten- oder Depotkonto ist; es zählt nicht mit.",
            "The scenario counts an account toward the reserve that is not a current, savings,"
            " credit card or depot account; it does not count.",
        ),
        (
            "Das Szenario ändert einen Sparplan, den Hausbuch in den letzten drei"
            " abgeschlossenen Monaten nicht mehr findet; die Änderung zählt nicht mit.",
            "The scenario changes a savings plan Hausbuch no longer finds in the last three"
            " closed months; the change does not count.",
        ),
        (
            f"Das Szenario enthält eine Einmalzahlung im Monat {{m}}, {_SCENARIO_ONE_OFF}",
            "The scenario contains a one-off payment in {m}, after the end of the"
            " calculation's period; it does not count.",
        ),
        (
            f"Das Szenario enthält Einmalzahlungen in den Monaten {{ms}}, {_SCENARIO_ONE_OFFS}",
            "The scenario contains one-off payments in {ms}, after the end of the calculation's"
            " period; they do not count.",
        ),
        (
            "Das Szenario ändert die Ausgaben für „{label:category}“ um einen Prozentsatz, aber"
            " in den abgeschlossenen Monaten gibt es dafür keine gemessenen Ausgaben; die Änderung"
            " zählt gerade nicht mit.",
            "The scenario changes the spending on “{label}” by a percentage, but the closed"
            " months hold no measured spending for it; the change does not count for now.",
        ),
        (
            "Das Szenario ändert euer Einkommen. Damit die Steuerwirkung der Wohnung und die"
            " Steuerschätzung dieselbe Änderung kennen, passt dort das erwartete Bruttogehalt"
            " an.",
            "The scenario changes your income. So that the flat's tax effect and the tax"
            " estimate know the same change, adjust the expected gross pay there.",
        ),
        (
            "Das Vermögen in einigen Jahren rechnet Hausbuch nur mit euren eigenen Annahmen;"
            " ohne gespeichertes Szenario zeigt es diese Rechnung nicht.",
            "Hausbuch calculates the net worth in some years only with your own assumptions;"
            " without a saved scenario it does not show this calculation.",
        ),
        (
            "Das Szenario der Wohnung lässt sich gerade nicht rechnen, deshalb rechnet der Plan"
            " mit dem gemessenen Durchschnitt der Wohnung und zeigt kein Vermögen in einigen"
            " Jahren.",
            "The flat's scenario cannot be calculated right now, so the plan uses the flat's"
            " measured average and shows no net worth in some years.",
        ),
        # services/net_worth.py
        (
            "Der Kontostand von „{label}“ ist nur bis zum {d:date} abgestimmt; ein Umsatz"
            " danach passt noch nicht zu einem Kontoauszug und zählt noch nicht mit.",
            "The balance of “{label}” is reconciled only up to {d}; one transaction after it"
            " does not match a statement yet and does not count yet.",
        ),
        (
            "Der Kontostand von „{label}“ ist nur bis zum {d:date} abgestimmt; {n} Umsätze"
            " danach passen noch nicht zu einem Kontoauszug und zählen noch nicht mit.",
            "The balance of “{label}” is reconciled only up to {d}; {n} transactions after it"
            " do not match a statement yet and do not count yet.",
        ),
        (
            "Der Kontostand von „{label}“ ist vom {d:date}; bis zum {t:date} hat der Bankabruf"
            " nichts Neueres geholt. Im Bankabruf „Jetzt abrufen“ wählen, sonst den"
            " Kontoauszug hochladen.",
            "The balance of “{label}” is from {d}; up to {t} the bank sync has fetched nothing"
            " newer. Choose “Fetch now” in the bank sync, otherwise upload the statement.",
        ),
        (
            "Der Kontostand von „{label}“ ist vom {d:date}; bis zum {t:date} fehlt noch die"
            " Abrechnung; der Betrag zählt bis dahin mit dem älteren Stand.",
            "The balance of “{label}” is from {d}; the card statement up to {t} is still"
            " missing; until then the amount counts at the older balance.",
        ),
        (
            "Der Kontostand von „{label}“ ist vom {d:date}; bis zum {t:date} fehlt noch ein"
            " Kontoauszug; der Betrag zählt bis dahin mit dem älteren Stand.",
            "The balance of “{label}” is from {d}; a statement up to {t} is still missing;"
            " until then the amount counts at the older balance.",
        ),
        (
            "Der Depotbericht zu {where:quoted} stimmt nicht mit den gebuchten Umsätzen"
            " überein; die Position zählt erst mit, wenn er stimmt.",
            "The depot report on {where} does not match the booked transactions; the position"
            " counts only once it does.",
        ),
        (
            "Für {where:quoted} fehlt ein Kurs; die Position zählt noch nicht im Nettovermögen.",
            "A price is missing for {where}; the position does not count in the net worth yet.",
        ),
        ("Der Kurs von {where:quoted} ist vom {d:date}.", "The price of {where} is from {d}."),
        (
            "Der Depotbericht vom {d:date} nennt {n:number} Anteile {name} („{label}“),"
            " gebucht ist keiner; der Fonds zählt noch nicht im Nettovermögen. Die Karte"
            " „Depotbestand“ in Zu prüfen bucht die Käufe mit „Umsätze aus dem Bericht"
            " buchen“ oder nennt die fehlende Wertpapierabrechnung.",
            "The depot report of {d} states {n} units of {name} (“{label}”), none of them"
            " booked; the fund does not count in the net worth yet. The card “Depot holdings”"
            " in To review books the purchases with “Book trades from the report” or names the"
            " missing trade confirmation.",
        ),
        (
            "Für „{label}“ sind keine Eigentumsanteile eingetragen; ihr Wert zählt im"
            " Gesamtwert, aber bei keinem Eigentümer.",
            "No ownership shares are entered for “{label}”; its value counts in the total, but"
            " for no owner.",
        ),
        (
            "Der Tilgungsplan des Darlehens „{lender}“ lässt sich nicht berechnen; seine"
            " Restschuld fehlt im Nettovermögen.",
            "The repayment schedule of the loan “{lender}” cannot be calculated; its remaining"
            " balance is missing from the net worth.",
        ),
        (
            "Keine Auszahlung ist mit dem Darlehen „{lender}“ verknüpft; bis zur Übergabe"
            " fehlt seine Restschuld im Nettovermögen.",
            "No payout is linked to the loan “{lender}”; until the handover its remaining"
            " balance is missing from the net worth.",
        ),
        (
            "Für das Konto „{label}“ sind keine Eigentümer eingetragen; sein Betrag zählt im"
            " Gesamtwert, aber bei keinem Eigentümer.",
            "No owners are entered for the account “{label}”; its amount counts in the total,"
            " but for no owner.",
        ),
        (
            "{waiting:todo} Bis dahin fehlen sie im Nettovermögen.",
            "{waiting} Until then they are missing from the net worth.",
        ),
        (
            "{waiting:todo} Bis dahin ist das Nettovermögen um diese Abgänge zu hoch.",
            "{waiting} Until then the net worth is too high by these outflows.",
        ),
        (
            "{waiting:todo} Bis dahin stimmt das Nettovermögen für diese Wallet nicht.",
            "{waiting} Until then the net worth for this wallet is not right.",
        ),
        # services/wallet_groups.py: a wallet's coins read but not yet confirmed.
        (
            "{added:coins} auf „{label}“ sind gelesen, zählen aber erst nach Ihrer Bestätigung"
            " mit; Abgänge von {taken:coins} sind noch nicht abgezogen ({count:waiting}).",
            "{added} on “{label}” have been read but count only after your confirmation;"
            " outflows of {taken} are not deducted yet ({count}).",
        ),
        (
            "{added:coins} auf „{label}“ sind gelesen, zählen aber erst nach Ihrer Bestätigung"
            " mit ({count:waiting}).",
            "{added} on “{label}” have been read but count only after your confirmation ({count}).",
        ),
        (
            "Abgänge von {taken:coins} auf „{label}“ sind gelesen, werden aber erst nach Ihrer"
            " Bestätigung abgezogen ({count:waiting}).",
            "Outflows of {taken} on “{label}” have been read but are deducted only after your"
            " confirmation ({count}).",
        ),
        (
            "Auf „{label}“ wartet 1 Transaktion auf Ihre Bestätigung; zusammen ändern sie den"
            " Bestand nicht.",
            "On “{label}”, 1 transaction awaits your confirmation; it does not change the holding.",
        ),
        (
            "Auf „{label}“ warten {n} Transaktionen auf Ihre Bestätigung; zusammen ändern sie"
            " den Bestand nicht.",
            "On “{label}”, {n} transactions await your confirmation; together they do not"
            " change the holding.",
        ),
        # rules/goal/goal.py: the walk's assumption for a property kept without a scenario.
        (
            "Annahme: Wohnung wird behalten, Wert unverändert.",
            "Assumption: the flat is kept, its value unchanged.",
        ),
    ],
    converters={
        "todo": _nested,
        "coins": _coins,
        "waiting": _waiting_count,
        "classes": _classes,
        "counted": _counted,
        "quoted": _quoted,
        "category": english_label,
    },
)

# The buttons' and links' words, by the German the builders write.
ACTIONS = i18n.Patterns(
    "todo_actions",
    [
        ("Kreditkartenabrechnung hochladen", "Upload card statement"),
        ("Kontoauszug hochladen", "Upload statement"),
        ("Zu prüfen öffnen", "Open To review"),
        ("Gegenbuchung zuordnen", "Assign matching transaction"),
        ("Ampel-Grenzen festlegen", "Set traffic-light limits"),
        ("Monatsabschluss festhalten", "Record month-end"),
        ("Szenario für die Wohnung anlegen", "Create a scenario for the flat"),
        ("Szenario anlegen", "Create scenario"),
        ("Annahme eintragen", "Enter assumption"),
        ("Rendite eintragen", "Enter return"),
        ("1 Zahlung einordnen", "Categorise 1 payment"),
        ("{n} Zahlungen einordnen", "Categorise {n} payments"),
        ("Anlagen öffnen", "Open Investments"),
        ("Szenario prüfen", "Check scenario"),
        ("Erwartetes Bruttogehalt anpassen", "Adjust expected gross pay"),
        ("Szenario der Wohnung öffnen", "Open the flat's scenario"),
        ("Konto öffnen", "Open account"),
        ("Kursquelle wählen", "Choose price source"),
        ("Kurs eintragen", "Enter price"),
        ("Immobilie öffnen", "Open property"),
        ("Darlehen öffnen", "Open loan"),
        ("Eigentümer festlegen", "Set owners"),
        ("Transaktionen prüfen", "Review transactions"),
    ],
)

_TODO_TYPES = (HealthTodo, NetWorthTodo, WalletWaiting, GoalAssumption)


def _swap(todo: BaseModel) -> BaseModel:
    text, action = todo.text, todo.action  # type: ignore[attr-defined]
    return todo.model_copy(
        update={"text": PATTERNS.translate(text), "action": ACTIONS.translate(action)}
    )


def _walk(value: Any, swap: Callable[[BaseModel], BaseModel]) -> Any:
    if isinstance(value, _TODO_TYPES):
        return swap(value)
    if isinstance(value, BaseModel):
        update = {}
        for name in type(value).model_fields:
            item = getattr(value, name)
            walked = _walk(item, swap)
            if walked is not item:
                update[name] = walked
        copied = value.model_copy(update=update) if update else value
        # The figures' own sentences (services/read_texts): a mark's, a gap's, a note's.
        own = SWAPS.get(type(value))
        return copied if own is None else own(copied)
    if isinstance(value, list):
        walked = [_walk(item, swap) for item in value]
        return walked if any(a is not b for a, b in zip(walked, value, strict=True)) else value
    if isinstance(value, dict):
        walked = {key: _walk(item, swap) for key, item in value.items()}
        return walked if any(walked[k] is not value[k] for k in value) else value
    return value


def english[M](response: M) -> M:
    """The response with every to-do (and a wallet's waiting coins) in the request's
    language, and the figures' own sentences with them (`read_texts.SWAPS`): unchanged in
    German, a copy with the English texts and button words in English.
    Read routes only; nothing a write path stores passes through here."""
    if not i18n.english():
        return response
    return _walk(response, _swap)
