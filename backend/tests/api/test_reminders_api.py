"""/api/reminders and /api/reminders/{id}/ack over HTTP (docs/phases/phase-5.md §6.3, §10):
the computed list, an acknowledgement that hides its instance, the error codes and workspace
isolation. The routes use today's date in Europe/Berlin, so the loan's dates are set
relative to it. Synthetic data only."""

from datetime import datetime

import pytest
from api_support import create, loan_body, new_property, ok
from test_tax_inputs import sessions as sessions  # noqa: F401 (users exist: recorded_by FK)

from hausbuch.rules.tax._common import add_months
from hausbuch.services.documents import LOCAL_TIME_ZONE

pytestmark = pytest.mark.db


def _loan_due_in_ten_days(client) -> tuple[str, str]:
    """A loan whose fixed rate ends in 12 months + 10 days: its first fixed_rate_end
    instance is due in 10 days (an action card visible on the day, inside the horizon)."""
    today = datetime.now(LOCAL_TIME_ZONE).date()
    due = today.fromordinal(today.toordinal() + 10)
    pid = new_property(client)["id"]
    loan = create(
        client,
        "/api/loans",
        loan_body(pid, fixed_until=add_months(due, 12).isoformat()),
    )
    # add_months clamps to the month's end, so the first instance is the earliest listed.
    instance = min(
        (
            r
            for r in ok(client.get("/api/reminders"))
            if r["kind"] == "loan.fixed_rate_end" and r["scope_id"] == loan["id"]
        ),
        key=lambda r: r["due_on"],
    )
    return loan["id"], instance["id"]


def test_an_ack_hides_its_instance(client_a):
    loan, rid = _loan_due_in_ten_days(client_a)
    listed = ok(client_a.get("/api/reminders"))
    (r,) = [x for x in listed if x["id"] == rid]
    assert (r["scope"], r["severity"], r["label_args"]["lender"]) == (
        "loan",
        "action",
        "Sparkasse Musterstadt",
    )
    window = ok(client_a.get("/api/reminders", params={"from": r["due_on"], "to": r["due_on"]}))
    assert rid in [x["id"] for x in window]
    assert all(x["due_on"] == r["due_on"] for x in window)

    ack = ok(client_a.post(f"/api/reminders/{rid}/ack", json={"action": "done"}), 201)
    assert (ack["reminder_key"], ack["scope_id"], ack["due_on"], ack["action"]) == (
        "loan.fixed_rate_end",
        loan,
        r["due_on"],
        "done",
    )
    assert rid not in [x["id"] for x in ok(client_a.get("/api/reminders"))]


def test_errors(client_a, client_b):
    _, rid = _loan_due_in_ten_days(client_a)
    bad = client_a.post("/api/reminders/nonsense/ack", json={"action": "done"})
    assert (bad.status_code, bad.json()["code"]) == (422, "reminder.unknown")
    other = client_b.post(f"/api/reminders/{rid}/ack", json={"action": "done"})
    assert (other.status_code, other.json()["code"]) == (422, "reminder.scope_invalid")
    snooze = client_a.post(f"/api/reminders/{rid}/ack", json={"action": "snoozed"})
    assert (snooze.status_code, snooze.json()["error"]) == (422, "validation")
    assert rid not in [x["id"] for x in ok(client_b.get("/api/reminders"))]
    assert rid in [x["id"] for x in ok(client_a.get("/api/reminders"))]
