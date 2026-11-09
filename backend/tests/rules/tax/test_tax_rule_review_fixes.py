"""Cases from the step 1 review of the phase 3 rules (not fixture-backed; the fixtures change
only in a spec PR). Each starts from the §14 flat of anlage_v_2026.yaml case_a."""

from decimal import Decimal
from uuid import UUID

import pytest
from fixture_models_phase3 import load

from hausbuch.rules.tax import erhaltungsaufwand
from hausbuch.rules.tax.draft import draft
from hausbuch.rules.tax.registry import rule_set
from hausbuch.schemas.tax import DraftFacts, WarningCode

RS = rule_set(2026)
CASE_A = next(c for c in load("anlage_v_2026").cases if c.id == "case_a").facts
LATE = UUID("000000a1-0000-4000-8000-0000000009a1")
LATE_INPUT = UUID("000000f1-0000-4000-8000-0000000009a1")


def _with(facts: DraftFacts, allocations=(), tax_inputs=()) -> DraftFacts:
    data = facts.model_dump()
    data["allocations"] += list(allocations)
    data["tax_inputs"] += list(tax_inputs)
    return DraftFacts.model_validate(data)


def _codes(d) -> list[str]:
    return [w.code.value for w in d.warnings]


@pytest.mark.rule
def test_f1_repairs_with_stray_herstellung_input_still_moves():
    """A herstellung input only decides a renovation (erhaltungsaufwand._costs); on a repairs
    line it is ignored, so a crossing moves that line into the basis like any other."""
    facts = _with(
        CASE_A,
        allocations=[
            {
                "allocation_id": str(LATE),
                "booking_date": "2027-05-03",
                "amount": "-42840.00",
                "category": "repairs",
                "invoice_net": "36000.00",
            }
        ],
        tax_inputs=[
            {
                "id": str(LATE_INPUT),
                "property_id": str(CASE_A.property.id),
                "kind": "renovation_treatment",
                "allocation_id": str(LATE),
                "value": {"treatment": "herstellung"},
            }
        ],
    )
    d = draft(facts, 2027, RS)
    maintenance = next(line for line in d.lines if line.key == "wk.maintenance")
    assert maintenance.amount == Decimal("0.00") and maintenance.provisional
    assert d.afa.additions == Decimal("48790.00")
    assert d.limit_15.exceeded_in_year == 2027
    assert LATE in {b.allocation_id for b in d.limit_15.basis_additions}
    assert erhaltungsaufwand.herstellung_additions(facts, RS) == []


@pytest.mark.rule
def test_f2_crossing_after_the_window_year_still_warns():
    """Work done in June 2029 (inside the window) and paid in January 2030 crosses the limit
    in 2030, after the window's end year: 2030 warns limit.exceeded, 2026 must be amended,
    2029 carries neither."""
    facts = _with(
        CASE_A,
        allocations=[
            {
                "allocation_id": str(LATE),
                "booking_date": "2030-01-15",
                "amount": "-42840.00",
                "category": "repairs",
                "invoice_net": "36000.00",
                "service_period": {"start": "2029-06-01", "end": "2029-06-30"},
            }
        ],
    )
    d2030 = draft(facts, 2030, RS)
    assert d2030.limit_15.exceeded_in_year == 2030
    assert WarningCode.LIMIT_EXCEEDED.value in _codes(d2030)
    d2026 = draft(facts, 2026, RS)
    assert WarningCode.LIMIT_PRIOR_YEAR_AMENDMENT.value in _codes(d2026)
    assert WarningCode.LIMIT_EXCEEDED.value not in _codes(d2026)
    d2029 = draft(facts, 2029, RS)
    assert WarningCode.LIMIT_EXCEEDED.value not in _codes(d2029)
    assert WarningCode.LIMIT_PRIOR_YEAR_AMENDMENT.value not in _codes(d2029)


@pytest.mark.rule
def test_f3_spread_paid_after_ownership_ended_is_deducted_in_full():
    """A repair paid after the year ownership ended, with a §82b input: the spread applies only
    while the property is owned, so the whole amount is on wk.maintenance in the year paid."""
    facts = DraftFacts.model_validate(
        {
            "property": {
                "id": str(CASE_A.property.id),
                "label": "Beispielhaus",
                "kind": "efh",
                "build_year": 1985,
                "purchase_date": "2020-01-01",
                "ownership_ended_on": "2027-08-31",
                "purchase_price": "250000.00",
            },
            "allocations": [
                {
                    "allocation_id": str(LATE),
                    "booking_date": "2028-03-01",
                    "amount": "-7000.00",
                    "category": "repairs",
                }
            ],
            "tax_inputs": [
                {
                    "id": str(LATE_INPUT),
                    "property_id": str(CASE_A.property.id),
                    "kind": "spread_82b",
                    "allocation_id": str(LATE),
                    "value": {"years": 3},
                }
            ],
        }
    )
    lines_2028 = erhaltungsaufwand.lines(facts, 2028, RS).lines
    assert [(line.key, line.amount) for line in lines_2028] == [
        ("wk.maintenance", Decimal("7000.00"))
    ]
    for year in (2027, 2029, 2030):
        assert erhaltungsaufwand.lines(facts, year, RS).lines == []
