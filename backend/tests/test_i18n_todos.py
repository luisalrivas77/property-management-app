"""The to-dos' English (phase 10 D284, step 4c, services/todo_texts.py): every producer's German
matches a pattern, English comes back under `en`, German without a header, and the shape of a
response is unchanged."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from hausbuch import i18n
from hausbuch.rules import investment_split
from hausbuch.rules.budget import budget
from hausbuch.rules.goal import goal as goal_rules
from hausbuch.rules.health import bands, figures, window
from hausbuch.rules.plan import projection
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.health import (
    ClosedReason,
    ClosedReasonCode,
    HealthTodo,
    HealthTodoCode,
    ReserveBalance,
    Window,
)
from hausbuch.schemas.net_worth import NetWorthTodo, NetWorthTodoCode
from hausbuch.services import health, plan, todo_texts
from hausbuch.services.todo_texts import ACTIONS, PATTERNS

GERMAN_MARKS = ("ä", "ö", "ü", "ß", "„", " der ", " die ", " und ", " ist ", " nicht ")


def _german_free(text: str) -> bool:
    return not any(mark in f" {text} " for mark in GERMAN_MARKS)


def _account(card: bool):
    return SimpleNamespace(account_id=uuid4(), label="Girokonto", credit_card=card)


def _window_todos() -> list[HealthTodo]:
    out = []
    for card in (False, True):
        account = _account(card)
        facts = SimpleNamespace(month="2026-03", accounts=[account])
        reason = ClosedReason(
            code=ClosedReasonCode.ACCOUNT_NOT_COVERED, account_id=account.account_id
        )
        out.append(window._todo(reason, facts))
    facts = SimpleNamespace(month="2026-03", accounts=[])
    for code in (ClosedReasonCode.LINES_WAITING, ClosedReasonCode.TRANSFER_OPEN):
        for count in (1, 4):
            out.append(window._todo(ClosedReason(code=code, count=count), facts))
    return out


def _gap(text: str, action: str = "Zu prüfen öffnen") -> NetWorthTodo:
    return NetWorthTodo(code=NetWorthTodoCode.PRICE_MISSING, text=text, action=action, href="/")


NET_WORTH_TEXTS = [
    "Der Kontostand von „Giro“ ist nur bis zum 05.03.2026 abgestimmt; ein Umsatz danach passt "
    "noch nicht zu einem Kontoauszug und zählt noch nicht mit.",
    "Der Kontostand von „Giro“ ist nur bis zum 05.03.2026 abgestimmt; 3 Umsätze danach passen "
    "noch nicht zu einem Kontoauszug und zählen noch nicht mit.",
    "Der Kontostand von „Giro“ ist vom 05.03.2026; bis zum 20.11.2026 hat der Bankabruf nichts "
    "Neueres geholt. Im Bankabruf „Jetzt abrufen“ wählen, sonst den Kontoauszug hochladen.",
    "Der Kontostand von „Karte“ ist vom 05.03.2026; bis zum 20.11.2026 fehlt noch die "
    "Abrechnung; der Betrag zählt bis dahin mit dem älteren Stand.",
    "Der Kontostand von „Giro“ ist vom 05.03.2026; bis zum 20.11.2026 fehlt noch ein "
    "Kontoauszug; der Betrag zählt bis dahin mit dem älteren Stand.",
    "Der Depotbericht zu MSCI World („Depot“) stimmt nicht mit den gebuchten Umsätzen überein; "
    "die Position zählt erst mit, wenn er stimmt.",
    "Für MSCI World („Depot“) fehlt ein Kurs; die Position zählt noch nicht im Nettovermögen.",
    "Der Kurs von MSCI World („Depot“) ist vom 30.09.2026.",
    "Der Depotbericht vom 30.09.2026 nennt 1.234,5 Anteile MSCI World („Depot“), gebucht ist "
    "keiner; der Fonds zählt noch nicht im Nettovermögen. Die Karte „Depotbestand“ in Zu prüfen "
    "bucht die Käufe mit „Umsätze aus dem Bericht buchen“ oder nennt die fehlende "
    "Wertpapierabrechnung.",
    "Für „Wohnung“ sind keine Eigentumsanteile eingetragen; ihr Wert zählt im Gesamtwert, aber "
    "bei keinem Eigentümer.",
    "Der Tilgungsplan des Darlehens „Bank“ lässt sich nicht berechnen; seine Restschuld fehlt "
    "im Nettovermögen.",
    "Keine Auszahlung ist mit dem Darlehen „Bank“ verknüpft; bis zur Übergabe fehlt seine "
    "Restschuld im Nettovermögen.",
    "Für das Konto „Giro“ sind keine Eigentümer eingetragen; sein Betrag zählt im Gesamtwert, "
    "aber bei keinem Eigentümer.",
    "1.234,5 SOL und 0,5 Ether (Portal) auf „Tangem“ sind gelesen, zählen aber erst nach Ihrer "
    "Bestätigung mit (2 Transaktionen warten). Bis dahin fehlen sie im Nettovermögen.",
    "2 ETH auf „Tangem“ sind gelesen, zählen aber erst nach Ihrer Bestätigung mit; Abgänge von "
    "0,1 BTC sind noch nicht abgezogen (1 Transaktion wartet). Bis dahin stimmt das "
    "Nettovermögen für diese Wallet nicht.",
    "Abgänge von 0,1 BTC auf „Tangem“ sind gelesen, werden aber erst nach Ihrer Bestätigung "
    "abgezogen (1 Transaktion wartet). Bis dahin ist das Nettovermögen um diese Abgänge zu "
    "hoch.",
    "Auf „Tangem“ wartet 1 Transaktion auf Ihre Bestätigung; zusammen ändern sie den Bestand "
    "nicht.",
    "Auf „Tangem“ warten 3 Transaktionen auf Ihre Bestätigung; zusammen ändern sie den Bestand "
    "nicht.",
]

PLAN_TEXTS = [
    "Das Szenario nennt ein Konto für die Reserve, das es nicht mehr gibt; es zählt nicht mit.",
    "Das Szenario zählt ein Konto zur Reserve, das kein Giro-, Spar-, Kreditkarten- oder "
    "Depotkonto ist; es zählt nicht mit.",
    "Das Szenario ändert einen Sparplan, den Hausbuch in den letzten drei abgeschlossenen "
    "Monaten nicht mehr findet; die Änderung zählt nicht mit.",
    "Das Szenario enthält eine Einmalzahlung im Monat 03/2031, der nach dem Ende des Zeitraums "
    "der Rechnung liegt; sie zählt nicht mit.",
    "Das Szenario enthält Einmalzahlungen in den Monaten 03/2031, 05/2031, die nach dem Ende "
    "des Zeitraums der Rechnung liegen; sie zählen nicht mit.",
    "Das Szenario ändert die Ausgaben für „Streaming“ um einen Prozentsatz, aber in den "
    "abgeschlossenen Monaten gibt es dafür keine gemessenen Ausgaben; die Änderung zählt gerade "
    "nicht mit.",
]


def _balance(kind: AccountKind) -> ReserveBalance:
    return ReserveBalance(
        account_id=uuid4(), label="Giro", kind=kind, balance=Decimal(0), as_of=date(2026, 3, 5)
    )


def _figure(gaps: list[NetWorthTodo]):
    return SimpleNamespace(todos=gaps, unvalued_positions=0)


def _producers() -> list[HealthTodo | NetWorthTodo]:
    end = date(2026, 3, 31)
    gap = _gap(NET_WORTH_TEXTS[6], "Kurs eintragen")
    out: list[HealthTodo | NetWorthTodo] = [
        *_window_todos(),
        *(
            todo
            for months in ([], ["2026-03"], ["2026-02", "2026-03"])
            for todo in window.window_todos(Window(months=months, enough=False))
        ),
        figures._stale(_balance(AccountKind.BANK_CURRENT), date(2026, 11, 20)),
        figures._stale(_balance(AccountKind.CREDIT_CARD), date(2026, 11, 20)),
        *bands.bands_todos(None),
        figures._gap_todo(end),
        projection._month_end_todo(end),
        projection.scenario_todo(),
        *(
            projection._rate_todo(classes)
            for classes in (
                [projection.PlanClass.DEPOT],
                [projection.PlanClass.CRYPTO],
                [projection.PlanClass.DEPOT, projection.PlanClass.CRYPTO],
            )
        ),
        *(
            investment_split.rate_todo(classes)
            for classes in (
                [projection.PlanClass.DEPOT],
                [projection.PlanClass.CRYPTO],
                [projection.PlanClass.DEPOT, projection.PlanClass.CRYPTO],
            )
        ),
        budget._uncategorized_todo(1, Decimal("12.50")),
        budget._uncategorized_todo(3, Decimal("1234.00")),
        health._month_end_todo(end),
        health._incomplete_todo(end, _figure([gap])),
        health._incomplete_todo(end, _figure([gap, gap])),
        health._incomplete_todo(end, _figure([gap, gap, gap])),
        plan._incomplete_run_todo(end, _figure([gap])),
        plan._incomplete_run_todo(end, _figure([gap, gap, gap])),
        plan._income_change_todo(),
        plan._no_scenario_todo(),
        plan._flat_unprojectable(),
        *(plan._scenario_check(text) for text in PLAN_TEXTS),
        *(_gap(text) for text in NET_WORTH_TEXTS),
    ]
    for action in [pattern for pattern, _, _ in ACTIONS.entries]:
        assert action  # every action is a compiled pattern
    return out


PRODUCED = _producers()


@pytest.mark.parametrize("todo", PRODUCED, ids=lambda t: t.text[:40])
def test_every_producer_has_english(todo):
    assert PATTERNS.find(todo.text) is not None, todo.text
    assert ACTIONS.find(todo.action) is not None, todo.action
    with i18n.use(i18n.EN):
        swapped = todo_texts.english(todo)
    assert swapped.text != todo.text
    assert _german_free(swapped.text), swapped.text
    assert _german_free(swapped.action), swapped.action
    assert swapped.href == todo.href and swapped.code == todo.code


def test_the_goals_assumption_has_english():
    """Phase 10 D301 (a) 5: the goal's „Annahme: Wohnung wird behalten …“ and its button."""
    kept = projection.FlatScenario(sale_year=None, kept_value=Decimal("300000.00"))
    [assumption] = goal_rules._assumptions(SimpleNamespace(flats={"flat": kept}))
    with i18n.use(i18n.EN):
        swapped = todo_texts.english(assumption)
    assert (swapped.text, swapped.action) == (
        "Assumption: the flat is kept, its value unchanged.",
        "Create scenario",
    )
    assert swapped.href == assumption.href


def test_formats_follow_english():
    todo = health._month_end_todo(date(2026, 3, 31))
    with i18n.use(i18n.EN):
        assert todo_texts.english(todo).text.startswith("No month-end is stored for 31 Mar 2026")
    with i18n.use(i18n.EN):
        swapped = todo_texts.english(budget._uncategorized_todo(3, Decimal("1234.00")))
    assert "€1,234" in swapped.text and swapped.action == "Categorise 3 payments"
    with i18n.use(i18n.EN):
        waiting = todo_texts.english(_gap(NET_WORTH_TEXTS[13]))
    assert waiting.text.startswith("1,234.5 SOL and 0.5 Ether (Portal) on “Tangem”")
    assert "(2 transactions waiting)" in waiting.text


def test_nested_gap_is_english_too():
    gap = _gap(NET_WORTH_TEXTS[6], "Kurs eintragen")
    todo = health._incomplete_todo(date(2026, 3, 31), _figure([gap, gap]))
    with i18n.use(i18n.EN):
        text = todo_texts.english(todo).text
    assert "A price is missing for MSCI World (“Depot”)" in text
    assert text.endswith("(1 more gap in the net worth)")


def test_german_without_a_header():
    todo = health._month_end_todo(date(2026, 3, 31))
    assert i18n.current() == i18n.DE
    assert todo_texts.english(todo) is todo


def test_walk_keeps_the_shape():
    todo = HealthTodo(
        code=HealthTodoCode.BANDS_UNCONFIRMED,
        text=bands.bands_todos(None)[0].text,
        action="Ampel-Grenzen festlegen",
        href="/",
    )
    response = SimpleNamespace()  # not a model: passed through
    assert todo_texts.english(response) is response
    with i18n.use(i18n.EN):
        out = todo_texts.english({"a": [todo], "b": 1})
    assert out["b"] == 1
    assert type(out["a"][0]) is HealthTodo
    assert out["a"][0].model_dump().keys() == todo.model_dump().keys()
    assert out["a"][0].action == "Set traffic-light limits"
