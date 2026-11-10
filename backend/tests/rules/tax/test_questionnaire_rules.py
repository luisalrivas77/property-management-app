"""§5.11 the questionnaire rules against questionnaire.yaml: slots and pre-fill, progress,
reminders and the zumutbare Belastung. The production QuestionPrefill carries the prior answer
and the reconfirm rows themselves; the fixture names them by id."""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from fixture_models_phase4b import load

from hausbuch.rules.tax import registry
from hausbuch.rules.tax.anlage_n._base import rules_for
from hausbuch.rules.tax.questionnaire import (
    extraordinary_threshold,
    prefill,
    progress,
    reminders,
)
from hausbuch.schemas.employment import EquipmentFact, ReturnInputFact
from hausbuch.schemas.enums import ReturnInputKind as K

CASES = load("questionnaire").cases


def _prefill(case):
    return prefill(
        case.tax_year,
        registry.questionnaire(case.tax_year),
        case.prior_answers,
        case.prior_rows,
        case.owners,
        case.properties,
        prior_tax_rows=case.prior_tax_rows,
        rule_set=rules_for(case.tax_year),
    )


def _slot_dump(s) -> dict:
    return {
        "key": s.key,
        "owner_id": s.owner_id,
        "property_id": s.property_id,
        "prior_answer_id": s.prior_answer.id if s.prior_answer else None,
        "proposed_answer": s.proposed_answer,
        "proposed_rows": [r.model_dump() for r in s.proposed_rows],
        "reconfirm_ids": [r.id for r in s.reconfirm_rows],
    }


@pytest.mark.rule
@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_questionnaire(case):
    e = case.expect
    q = registry.questionnaire(case.tax_year)
    slots = _prefill(case)
    if e.slot_count is not None:
        assert len(slots) == e.slot_count
    for want in e.slots or []:
        got = next(
            (
                s
                for s in slots
                if (s.key, s.owner_id, s.property_id) == (want.key, want.owner_id, want.property_id)
            ),
            None,
        )
        assert got is not None, f"{case.id}: no slot {want.key}"
        expected = want.model_dump()
        expected["proposed_rows"] = [r.model_dump() for r in want.proposed_rows]
        assert _slot_dump(got) == expected, f"{case.id}: slot {want.key}"
    for key in e.absent_keys or []:
        assert all(s.key != key for s in slots), f"{case.id}: {key} has a slot"
    got = progress(case.tax_year, q, case.answers, case.owners, case.properties)
    if e.answered is not None:
        assert got.answered == e.answered
    if e.total is not None:
        assert got.total == e.total
    if e.open is not None:
        assert [(o.key, o.owner_id, o.property_id) for o in got.open] == [
            (o.key, o.owner_id, o.property_id) for o in e.open
        ]
    if e.reminders is not None:
        assert reminders(case.answers, q) == e.reminders
    if e.threshold is not None:
        t = registry.tariff(case.tax_year)
        assert extraordinary_threshold(case.gde, case.children, True, t) == e.threshold


@pytest.mark.rule
def test_a_spanning_item_is_never_copied_but_a_subscription_is():
    """D110: work_equipment carries only as a subscription (a yearly item)."""
    owner = UUID(int=1)
    rows = [
        ReturnInputFact(
            id=UUID(int=10 + i),
            owner_id=owner,
            kind=K.WORK_EQUIPMENT,
            tax_year=2025,
            value=EquipmentFact(
                item_class=item_class, label="Testposten", bought_on=date(2025, 3, 1), gross=price
            ),
        )
        for i, (item_class, price) in enumerate(
            [("phone", Decimal("1500.00")), ("subscription", Decimal("120.00"))]
        )
    ]
    slots = prefill(
        2026,
        registry.questionnaire(2026),
        [],
        rows,
        [owner],
        [],
        rule_set=rules_for(2026),
    )
    by_key = {s.key: s for s in slots}
    copied = [r.carried_from_id for s in slots for r in s.proposed_rows if r.carried_from_id]
    assert copied == [UUID(int=11)], "only the subscription is copied"
    assert by_key["owner.subscriptions"].proposed_rows[0].value.bought_on == date(2026, 3, 1)
    assert [r.id for r in by_key["owner.equipment_prior"].reconfirm_rows] == [UUID(int=10)]


@pytest.mark.rule
def test_the_threshold_without_children_takes_the_basic_tariff_when_not_joint():
    t = registry.tariff(2026)
    joint = extraordinary_threshold(Decimal("40000"), 0, True, t)
    single = extraordinary_threshold(Decimal("40000"), 0, False, t)
    assert single > joint > 0
