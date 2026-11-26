"""rules/tax/booked_inputs against tests/fixtures/phase10/booked_inputs.yaml (phase 10 §7.8 (a),
D293). Invented owners, payees and amounts only."""

from decimal import Decimal

import fixture_models_phase10 as phase10
import pytest
from fixture_models_booked_inputs import ident

from hausbuch.rules.tax.booked_inputs import (
    booked_benefit_warnings,
    booked_proposals,
)
from hausbuch.schemas.employment import ReturnInputCreate
from hausbuch.schemas.tax import WarningCode

pytestmark = pytest.mark.rule

CASES = phase10.load("booked_inputs").cases


def _proposals(case):
    return booked_proposals(
        case.tax_year, case.owner_facts(), case.household_rows(), case.booked_lines()
    )


def _value(expect) -> dict:
    value = dict(expect.value)
    if value.get("child_id") is not None:
        value["child_id"] = str(ident(value["child_id"]))
    return value


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_proposals(case):
    got = _proposals(case)
    assert [
        {
            "group": p.group,
            "owner": p.owner_id,
            "payee": p.payee,
            "booked": p.booked,
            "typed": p.typed,
            "difference": p.difference,
            "allocations": p.allocation_ids,
            "needs": p.needs,
            "benefit": p.benefit,
            "receipt_needed": p.receipt_needed,
            "ask_party": p.ask_party,
            "blocking": p.blocking,
            "choices": [c.id for c in p.child_choices],
            "value": p.payload["value"],
        }
        for p in got
    ] == [
        {
            "group": e.group,
            "owner": None if e.owner is None else ident(e.owner),
            "payee": e.payee,
            "booked": e.booked,
            "typed": e.typed,
            "difference": e.difference,
            "allocations": [ident(a) for a in e.allocations],
            "needs": e.needs,
            "benefit": e.benefit,
            "receipt_needed": e.receipt_needed,
            "ask_party": e.ask_party,
            "blocking": e.blocking,
            "choices": [ident(c) for c in e.choices],
            "value": _value(e),
        }
        for e in case.expect
    ]


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_gap_per_kind(case):
    """The household gate: per group, the differences sum to max(0, Σ booked − Σ typed)."""
    got = _proposals(case)
    for group, gap in case.gap.items():
        assert sum((p.difference for p in got if p.group == group), Decimal(0)) == gap


def _filled(payload: dict, needs: list[str], owner) -> dict:
    """The payload with what the card asks filled in, as a person would."""
    body = {**payload, "value": dict(payload["value"])}
    v = body["value"]
    for need in needs:
        if need == "owner":
            body["owner_id"] = str(owner)
        elif need == "benefit":
            v["benefit"] = "other"
        elif need == "child":
            v["child_id"] = str(ident("c1"))
        elif need == "labour_cost":
            v["labour_cost"] = "100.00"
        elif need == "service_class":
            v["service_class"] = "handwerker"
        elif need == "recipient":
            v["recipient_label"] = "Empfänger"
    return body


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_payload_is_a_valid_return_input_once_filled(case):
    for p in _proposals(case):
        body = _filled(p.payload, list(p.needs), ident(case.owners[0]))
        row = ReturnInputCreate.model_validate(body)
        assert row.kind is p.kind
        assert row.tax_year == case.tax_year


def test_household_services_never_carry_the_bank_amount():
    for case in CASES:
        for p in _proposals(case):
            if p.group == "household_services":
                assert "labour_cost" not in p.payload["value"]
                assert "labour_cost" in p.needs


def test_benefit_warnings_are_the_benefit_proposals():
    """The estimate's `return.benefit_booked` to-dos and the guide's benefit proposals come
    from the same groups: one each, with the same owner and difference."""
    for case in CASES:
        proposals = [p for p in _proposals(case) if p.group == "benefit"]
        warnings = booked_benefit_warnings(case.tax_year, case.owner_facts(), case.booked_lines())
        assert all(w.code is WarningCode.RETURN_BENEFIT_BOOKED for w in warnings)
        assert [(w.params.get("owner_id"), w.params["difference"]) for w in warnings] == [
            (None if p.owner_id is None else str(p.owner_id), str(p.difference)) for p in proposals
        ]
