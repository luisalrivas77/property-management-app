"""The reminder digest's text (docs/phases/phase-5.md D123): every label code the reminders
fixture produces has its German line, and the mail carries the lines, the link and fixed text
only. The fixture is read, never changed. Invented values only."""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from fixture_models_phase5 import load
from pydantic import BaseModel

from hausbuch.rules.reminder_digest import (
    FALLBACK_LABEL,
    SUBJECT,
    digest_lines,
    german_date,
    german_month,
    reminder_label,
    render_digest,
)
from hausbuch.rules.reminders import build_reminders, reminder_id
from hausbuch.schemas.reminders import Reminder, ReminderKind, ReminderScope

FIXTURE_REMINDERS = [
    r for case in load("reminders").cases for r in build_reminders(case.facts, case.today)
]
APP = "https://hausbuch.example.org/"
PROPERTY = UUID("00000000-0000-4000-8000-000000000001")
TENANCY = UUID("00000000-0000-4000-8000-000000000002")


def _reminder(kind, scope, scope_id, due_on, label_code, **args) -> Reminder:
    return Reminder(
        id=reminder_id(kind, scope_id, due_on),
        kind=kind,
        scope=scope,
        scope_id=scope_id,
        due_on=due_on,
        visible_from=due_on,
        severity="action",
        cleared_by="auto",
        label_code=label_code,
        label_args=args,
        email_leads=[0],
    )


def test_every_fixture_code_has_a_line():
    assert FIXTURE_REMINDERS
    codes = {r.label_code for r in FIXTURE_REMINDERS}
    assert len(codes) >= 12
    for r in FIXTURE_REMINDERS:
        label = reminder_label(r)
        assert label != FALLBACK_LABEL, r.label_code
        assert "None" not in label and "–" not in label, (r.id, label)


@pytest.mark.parametrize(
    ("label_code", "args", "expected"),
    [
        (
            "grundsteuer.instalment.remind",
            {"property": "Wohnung A"},
            "Grundsteuer Wohnung A überweisen",
        ),
        (
            "tenancy.rent_missing",
            {"unit": "WE 3", "month": "2026-03"},
            "Miete WE 3 für März 2026 fehlt",
        ),
        (
            "loan.fixed_rate_end",
            {"lender": "Beispielbank AG", "fixed_until": "2027-01-31", "months_before": 12},
            "Zinsbindung Beispielbank AG endet am 31.01.2027: Anschlussfinanzierung prüfen",
        ),
        (
            "checklist.due.grundsteuerbescheid",
            {"property": "Wohnung A", "year": 2027},
            "Grundsteuerbescheid für 2027 erwartet (Wohnung A)",
        ),
        ("tax.return_deadline", {"year": 2026}, "Steuererklärung 2026 abgeben"),
    ],
)
def test_lines_match_the_cards(label_code, args, expected):
    r = Reminder.model_construct(label_code=label_code, label_args=args)
    assert reminder_label(r) == expected


def test_dates_and_months_in_german():
    assert german_date(date(2027, 2, 15)) == "15.02.2027"
    assert german_date("2027-02-15") == "15.02.2027"
    assert german_month("2026-12") == "Dezember 2026"
    assert german_month("kaputt") == "kaputt"


def test_the_digest_lists_by_due_day_and_links_the_overview():
    later = _reminder(
        ReminderKind.RENT_MISSING,
        ReminderScope.TENANCY,
        TENANCY,
        date(2027, 3, 8),
        "tenancy.rent_missing",
        unit="WE 3",
        month="2027-03",
    )
    first = _reminder(
        ReminderKind.GRUNDSTEUER_INSTALMENT,
        ReminderScope.PROPERTY,
        PROPERTY,
        date(2027, 2, 15),
        "grundsteuer.instalment.remind",
        property="Wohnung <A> & Co",
    )
    digest = render_digest([later, first], APP)
    assert digest.subject == SUBJECT and len(digest.subject) >= 10
    assert digest_lines([later, first]) == [
        "15.02.2027: Grundsteuer Wohnung <A> & Co überweisen",
        "08.03.2027: Miete WE 3 für März 2027 fehlt",
    ]
    assert "- 15.02.2027: Grundsteuer Wohnung <A> & Co überweisen" in digest.text
    assert digest.text.index("15.02.2027") < digest.text.index("08.03.2027")
    assert APP in digest.text
    # A label as the user typed it is escaped in the HTML.
    assert "Wohnung &lt;A&gt; &amp; Co" in digest.html
    assert "<A>" not in digest.html
    assert f'href="{APP}"' in digest.html


def _amounts(value) -> set[Decimal]:
    """Every Decimal anywhere in a fixture's facts (instalments, payments, balances)."""
    if isinstance(value, BaseModel):
        return set().union(*(_amounts(getattr(value, n)) for n in type(value).model_fields))
    if isinstance(value, list | tuple):
        return set().union(set(), *(_amounts(v) for v in value))
    return {value} if isinstance(value, Decimal) else set()


def test_no_amount_ever_in_the_fixture_digest():
    """The fixture's facts hold amounts (Grundsteuer instalments and payments); none reaches
    the mail, since a label carries only dates, months, years and labels."""
    amounts = set().union(*(_amounts(case.facts) for case in load("reminders").cases))
    assert amounts
    digest = render_digest(FIXTURE_REMINDERS, APP)
    for body in (digest.text, digest.html):
        assert "€" not in body and "EUR" not in body
        for amount in amounts:
            if amount:
                assert f"{amount:.2f}" not in body
                assert f"{amount:.2f}".replace(".", ",") not in body


def test_an_empty_digest_is_refused():
    with pytest.raises(ValueError):
        render_digest([], APP)
