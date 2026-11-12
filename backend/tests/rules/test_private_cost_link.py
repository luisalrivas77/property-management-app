"""D161's three paths on the pure rules (docs/phases/phase-6.md D161; fixture
tests/fixtures/phase6/private_cost_link.yaml): what the deterministic order books a line as,
how it is routed, the row it proves, the row it proposes and its to-do. What the Anlage V
counts is the draft case each names, replayed by tests/rules/tax/test_tax_rule_draft.py; the
database side (the to-dos closing, the relink) is tests/db/test_services_transfers.py."""

from decimal import Decimal

import fixture_models_phase3 as phase3
import fixture_models_phase6 as phase6
import pytest

from hausbuch.rules import matching
from hausbuch.rules.household import property_cost
from hausbuch.rules.review import route
from hausbuch.schemas.allocation import AllocationMethod, RoutingDecision, RoutingInput
from hausbuch.schemas.enums import ImportStatus

LINK = phase6.load("private_cost_link")
STEP_2 = [c for c in LINK.cases if c.pending and c.pending.startswith("phase 6 step 2")]
DRAFTS = {c.id: c for c in phase3.load("anlage_v_2026").cases}
DRAFT_BUILT = ("phase 6 step 2",)


def _context(case):
    return LINK.context.model_copy(update={"account_purpose": case.purpose})


@pytest.mark.rule
@pytest.mark.parametrize("case", STEP_2, ids=lambda c: c.id)
def test_d161_paths(case):
    expect, line = case.expect, case.line
    booked = expect.allocation
    if booked.by in ("document_match", "relink"):
        got = matching.deterministic_answer([], line, _context(case))
        assert got is not None and got.method is AllocationMethod.DOCUMENT_MATCH
        (part,) = got.allocations
        assert (part.category, part.property_id, part.document_id) == (
            booked.category,
            booked.property_id,
            booked.document_id,
        )
        # A document match on hh_property_cost is never auto-confirmed (D159's None
        # threshold): proposed, or confirmed once the user says so.
        routed = route(RoutingInput(import_status=ImportStatus.RECONCILED, allocation_set=got))
        if case.purpose.value == "private":
            assert routed.decision is RoutingDecision.REVIEW
            assert expect.status in ("proposed", "confirmed")
    else:
        assert booked.by == "user" and booked.document_id is None
    rows = [(r.id, r.document_id) for r in case.private_cost_rows]
    proven = property_cost.rows_proven(booked.document_id, rows)
    if case.purpose.value == "private":
        assert proven == ([expect.proof_of] if expect.proof_of else [])
    bill = next((d for d in LINK.context.documents if d.id == booked.document_id), None)
    if expect.proposes is not None:
        assert not proven  # path 2: the bill has no row yet
        got_rows = property_cost.proposed_private_costs(
            line.booking_date, Decimal(line.amount), bill
        )
        wanted = expect.proposes
        assert [(r.document_id, r.category, r.amount, r.tax_year) for r in got_rows] == [
            (wanted.document_id, wanted.category, wanted.amount, wanted.tax_year)
        ]
    labels = (
        [property_cost.BILL_TODO]
        if property_cost.needs_bill(booked.category, booked.document_id)
        else []
    )
    if case.purpose.value == "private":
        assert labels == expect.todos
    if case.draft_case is not None:
        draft = DRAFTS[case.draft_case]
        assert not draft.pending or draft.pending.startswith(DRAFT_BUILT)


@pytest.mark.rule
def test_a_bill_of_no_property_proposes_nothing():
    bill = LINK.context.documents[0].model_copy(update={"property_id": None})
    assert property_cost.proposed_private_costs(bill.payments[0].due_on, Decimal("1"), bill) == []
