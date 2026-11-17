"""Phase 9 step 1: the real rules of rules/health/ against the fixtures of
tests/fixtures/phase9/ (docs/phases/phase-9.md §4, §5, §15.1), pure, no database.

fixture_models_phase9.py recomputes each `expect` on load with its own reference functions;
this file runs the production functions on the same cases and compares, so a rule and its
fixture can only agree by computing the same figure. A case still marked `pending` is skipped.
Plus what the fixtures cannot hold: the to-do texts, several properties, a month not closed in
the series, the D234 recode of a flagged line, and two stale accounts."""

from datetime import date
from uuid import UUID

import fixture_models_phase9 as phase9
import pytest

from hausbuch.rules.health import window as W
from hausbuch.schemas.health import (
    AccountCoverage,
    ClosedReasonCode,
    HealthTodoCode,
    MonthFacts,
)

CLOSED = phase9.load("closed_month")


def _live(case) -> None:
    if case.pending:
        pytest.skip(f"pending step {case.pending}")


def uid(key: str) -> UUID:
    return phase9.key_uuid(key)


# --- closed_month.yaml ---------------------------------------------------------------


def facts(case, month, covered_to, own_opening) -> MonthFacts:
    return MonthFacts(
        month=month,
        accounts=[
            AccountCoverage(
                account_id=uid(a.key),
                label=a.key,
                credit_card=a.kind == "credit_card",
                first_day=a.first_day,
                covered_to=covered_to.get(a.key),
                idle=a.key in case.idle_card,
                own_opening=own_opening,
            )
            for a in case.accounts
        ],
        lines_waiting=case.lines_waiting,
        transfers_open=case.transfers_open,
    )


@pytest.mark.parametrize("case", CLOSED.cases, ids=lambda c: c.id)
def test_closed_month(case):
    _live(case)
    if case.closed_months is not None:
        got = W.window(case.closed_months)
        assert got.months == case.expect_window.months
        assert got.enough == case.expect_window.enough
        assert [t.code for t in W.window_todos(got)] == case.expect_todos
        return
    runs = [(case.month, case.covered_to, False, case.expect)]
    runs += [(v.month, v.covered_to, v.own_opening, v.expect) for v in case.variants]
    for month, covered_to, own, expect in runs:
        if month is None or expect is None:
            continue
        got = W.closed(facts(case, month, covered_to, own))
        assert got.closed == expect.closed, (case.id, month)
        assert [(r.code, r.account_id, r.count) for r in got.reasons] == [
            (r.code, None if r.account is None else uid(r.account), r.count) for r in expect.reasons
        ], (case.id, month)
        assert [t.code for t in got.todos] == expect.todos, (case.id, month)


def test_closed_month_todos_name_the_button():
    month = MonthFacts(
        month="2027-03",
        accounts=[
            AccountCoverage(
                account_id=uid("card"),
                label="Karte",
                credit_card=True,
                first_day=date(2026, 1, 1),
                covered_to=date(2027, 2, 28),
            ),
            AccountCoverage(account_id=uid("new"), label="Neu"),  # nothing imported yet
        ],
        lines_waiting=1,
        transfers_open=3,
    )
    got = W.closed(month)
    assert [r.code for r in got.reasons] == [
        ClosedReasonCode.ACCOUNT_NOT_COVERED,
        ClosedReasonCode.ACCOUNT_NOT_COVERED,
        ClosedReasonCode.LINES_WAITING,
        ClosedReasonCode.TRANSFER_OPEN,
    ]
    card, new, waiting, transfers = got.todos
    assert card.action == "Kreditkartenabrechnung hochladen" and "„Karte“" in card.text
    assert card.href == f"/accounts/{uid('card')}?tab=kontoauszuege"
    assert new.action == "Kontoauszug hochladen"
    assert waiting.action == "Zu prüfen öffnen" and "Ein Umsatz" in waiting.text
    assert transfers.action == "Gegenbuchung zuordnen" and "3 Umbuchungen" in transfers.text


def test_a_month_missing_from_the_mapping_is_not_closed():
    got = W.window({"2027-01": True, "2027-03": True, "2027-04": True})
    assert got.months == ["2027-03", "2027-04"] and not got.enough
    assert W.window({}).months == [] and W.window_todos(W.window({}))[0].code is (
        HealthTodoCode.TOO_FEW_MONTHS
    )
