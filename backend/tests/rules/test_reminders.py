"""rules.reminders (docs/phases/phase-5.md §6, D122, D123, D126) against reminders.yaml and
notification_schedule.yaml. Every list of the facts is shuffled, nested ones too."""

import random
from datetime import date

import pytest
from fixture_models_phase5 import load
from pydantic import BaseModel

from hausbuch.rules.reminders import build_reminders, due_notifications
from hausbuch.schemas.reminders import ReminderFacts

REMINDERS = load("reminders").cases
NOTIFICATIONS = load("notification_schedule").cases
BY_ID = {c.id: c for c in REMINDERS}


def _shuffle(value, rng: random.Random):
    """A copy with every list (at any depth) in a random order."""
    if isinstance(value, BaseModel):
        return value.model_copy(
            update={name: _shuffle(getattr(value, name), rng) for name in type(value).model_fields}
        )
    if isinstance(value, list):
        items = [_shuffle(v, rng) for v in value]
        rng.shuffle(items)
        return items
    return value


@pytest.mark.rule
@pytest.mark.parametrize("case", REMINDERS, ids=lambda c: c.id)
def test_build_reminders(case):
    assert build_reminders(case.facts, case.today) == case.expect


@pytest.mark.rule
@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("case", REMINDERS, ids=lambda c: c.id)
def test_order_of_the_facts_does_not_matter(case, seed):
    facts = _shuffle(case.facts, random.Random(seed))
    assert isinstance(facts, ReminderFacts)
    assert build_reminders(facts, case.today) == case.expect


@pytest.mark.rule
def test_ids_stable_across_days():
    """The same facts five days apart give the same instances with the same ids."""
    early, later = BY_ID["grundsteuer_quarters_without_schedule"], BY_ID["ids_stable_across_days"]
    assert early.facts == later.facts
    ids = [r.id for r in build_reminders(early.facts, early.today)]
    assert ids == [r.id for r in build_reminders(later.facts, later.today)]
    assert ids


@pytest.mark.rule
def test_labels_never_carry_an_amount_or_a_tenant():
    for case in REMINDERS:
        for r in build_reminders(case.facts, case.today):
            assert not any("amount" in k or "tenant" in k for k in r.label_args), r.id


@pytest.mark.rule
def test_pure_over_the_same_input():
    case = BY_ID["order_independent"]
    first = build_reminders(case.facts, case.today)
    assert build_reminders(case.facts, case.today) == first
    assert build_reminders(case.facts, date(2026, 11, 10)) == first


@pytest.mark.rule
@pytest.mark.parametrize("case", NOTIFICATIONS, ids=lambda c: c.id)
def test_due_notifications(case):
    got = due_notifications(case.reminders, case.sent, case.today, case.recipients)
    assert got == case.expect


@pytest.mark.rule
@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("case", NOTIFICATIONS, ids=lambda c: c.id)
def test_due_notifications_order_independent(case, seed):
    rng = random.Random(seed)
    reminders, sent, recipients = (
        rng.sample(xs, len(xs)) for xs in (case.reminders, case.sent, case.recipients)
    )
    assert due_notifications(reminders, sent, case.today, recipients) == case.expect
