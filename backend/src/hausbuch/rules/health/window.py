"""Closed months and the window (docs/phases/phase-9.md §4.4, D231). Fixture:
tests/fixtures/phase9/closed_month.yaml.

`closed(facts)` decides one month from what the service read about it (each account's
coverage, the month's waiting lines and open transfer legs); `window(closed)` takes the
unbroken run of closed months ending at the newest closed one, cut to 12; `window_todos`
states the to-do that stands in place of every flow figure below three months.
"""

import calendar
from collections.abc import Mapping
from datetime import date

from hausbuch.schemas.health import (
    AccountCoverage,
    ClosedMonth,
    ClosedReason,
    ClosedReasonCode,
    HealthTodo,
    HealthTodoCode,
    MonthFacts,
    Window,
)

MAX_MONTHS = 12
MIN_MONTHS = 3


def _last_day(month: str) -> date:
    year, number = map(int, month.split("-"))
    return date(year, number, calendar.monthrange(year, number)[1])


def _shift(month: str, k: int) -> str:
    year, number = map(int, month.split("-"))
    index = year * 12 + number - 1 + k
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _german_month(month: str) -> str:
    year, number = month.split("-")
    return f"{number}/{year}"


def _covered(account: AccountCoverage, end: date) -> bool | None:
    """True or False for an account of the month; None when it is not one of them: first
    imported after the month's end with its first statement's own opening (§4.4)."""
    first = account.first_day
    if first is not None and first > end and account.own_opening:
        return None
    if account.idle:  # a card idle by phase 5 D122 amended (6)
        return True
    return (
        first is not None
        and first <= end
        and account.covered_to is not None
        and account.covered_to >= end
    )


def _todo(reason: ClosedReason, facts: MonthFacts) -> HealthTodo:
    month = _german_month(facts.month)
    if reason.code is ClosedReasonCode.ACCOUNT_NOT_COVERED:
        account = next(a for a in facts.accounts if a.account_id == reason.account_id)
        card = account.credit_card
        return HealthTodo(
            code=HealthTodoCode.ACCOUNT_NOT_COVERED,
            text=(
                f"Für „{account.label}“ fehlen Umsätze bis zum Ende von {month}; "
                "der Monat zählt erst mit, wenn sie da sind."
            ),
            action="Kreditkartenabrechnung hochladen" if card else "Kontoauszug hochladen",
            href=f"/accounts/{account.account_id}?tab=kontoauszuege",
        )
    if reason.code is ClosedReasonCode.LINES_WAITING:
        count = reason.count or 0
        text = (
            f"Ein Umsatz aus {month} wartet in „Zu prüfen“."
            if count == 1
            else f"{count} Umsätze aus {month} warten in „Zu prüfen“."
        )
        return HealthTodo(
            code=HealthTodoCode.LINES_WAITING,
            text=text + " Der Monat zählt erst mit, wenn sie bestätigt sind.",
            action="Zu prüfen öffnen",
            href="/inbox",
        )
    count = reason.count or 0
    text = (
        f"Eine Umbuchung aus {month} hat noch keine Gegenbuchung."
        if count == 1
        else f"{count} Umbuchungen aus {month} haben noch keine Gegenbuchung."
    )
    return HealthTodo(
        code=HealthTodoCode.TRANSFER_OPEN,
        text=text + " Der Monat zählt erst mit, wenn sie zugeordnet sind.",
        action="Gegenbuchung zuordnen",
        href="/inbox",
    )


def closed(facts: MonthFacts) -> ClosedMonth:
    """Whether one month is closed: every private and rental account covered for it, no line
    of it waiting, no transfer leg of it open; the reasons and their to-dos otherwise, in
    that order (accounts, waiting lines, open transfers)."""
    end = _last_day(facts.month)
    reasons: list[ClosedReason] = []
    for account in facts.accounts:
        if _covered(account, end) is False:
            reasons.append(
                ClosedReason(
                    code=ClosedReasonCode.ACCOUNT_NOT_COVERED, account_id=account.account_id
                )
            )
    if facts.lines_waiting:
        reasons.append(ClosedReason(code=ClosedReasonCode.LINES_WAITING, count=facts.lines_waiting))
    if facts.transfers_open:
        reasons.append(
            ClosedReason(code=ClosedReasonCode.TRANSFER_OPEN, count=facts.transfers_open)
        )
    return ClosedMonth(
        month=facts.month,
        closed=not reasons,
        reasons=reasons,
        todos=[_todo(r, facts) for r in reasons],
    )


def window(closed_months: Mapping[str, bool]) -> Window:
    """The unbroken run of closed months ending at the newest closed one, cut to 12. A month
    missing from the mapping is not closed."""
    newest = max((m for m, ok in closed_months.items() if ok), default=None)
    run: list[str] = []
    month = newest
    while month is not None and closed_months.get(month) and len(run) < MAX_MONTHS:
        run.insert(0, month)
        month = _shift(month, -1)
    return Window(months=run, enough=len(run) >= MIN_MONTHS)


def window_todos(win: Window) -> list[HealthTodo]:
    """D231: below three closed months no flow figure is shown; this to-do stands in their
    place. What keeps the next month open is that month's own `ClosedMonth.todos`."""
    if win.enough:
        return []
    have = len(win.months)
    counted = (
        "noch kein abgeschlossener Monat"
        if have == 0
        else (
            "erst ein abgeschlossener Monat" if have == 1 else f"erst {have} abgeschlossene Monate"
        )
    )
    return [
        HealthTodo(
            code=HealthTodoCode.TOO_FEW_MONTHS,
            text=(
                f"Für Sparquote, Reserve und Schuldenkennzahlen braucht es mindestens "
                f"{MIN_MONTHS} abgeschlossene Monate in Folge; bisher ist es {counted}."
            ),
            action="Zu prüfen öffnen",
            href="/inbox",
        )
    ]
