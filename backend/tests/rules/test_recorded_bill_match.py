"""Phase 6 D161 widened (14 Nov 2026), the pure half: when a private debit pays a bill the
property already records (rules.matching.recorded_hit), and the private card's proposal that
never repeats a recorded Nebenkosten part (rules.household.property_cost). The db half is
tests/db/test_services_recorded_bills.py. Invented amounts only."""

import uuid
from datetime import date
from decimal import Decimal

from hausbuch.rules import matching as m
from hausbuch.rules.household.property_cost import proposed_private_costs
from hausbuch.rules.proof_match import Fit
from hausbuch.schemas.allocation import (
    AllocationCategory,
    DocumentFacts,
    MatchTransaction,
    RecordedBill,
)

C = AllocationCategory
PROPERTY = uuid.uuid4()
D = Decimal


def _bill(**overrides) -> RecordedBill:
    values = {
        "document_id": uuid.uuid4(),
        "property_id": PROPERTY,
        "acquisition": [D("9000.00")],
        "lines": [D("4500.00"), D("4500.00")],
        "issued_on": date(2026, 4, 2),
        "due_on": date(2026, 5, 4),
    }
    return RecordedBill(**(values | overrides))


def _tx(amount: str, booked: date = date(2026, 5, 28), **overrides) -> MatchTransaction:
    values = {
        "id": uuid.uuid4(),
        "booking_date": booked,
        "amount": D(amount),
        "counterparty_name": "Finanzamt Beispielstadt",
    }
    return MatchTransaction(**(values | overrides))


def test_a_share_fits_as_a_part_and_the_total_as_the_whole():
    assert m.recorded_hit(_tx("-4500.00"), _bill()) is Fit.PART
    assert m.recorded_hit(_tx("-9000.00"), _bill()) is Fit.WHOLE
    assert m.recorded_hit(_tx("-4499.98"), _bill()) is None
    assert m.recorded_hit(_tx("4500.00"), _bill()) is None  # a credit pays nothing


def test_a_line_paying_more_than_the_bill_left_open_is_refused():
    assert m.recorded_hit(_tx("-4500.00"), _bill(paid=D("4500.00"))) is Fit.PART
    assert m.recorded_hit(_tx("-4500.00"), _bill(paid=D("9000.00"))) is None


def test_the_window_runs_from_ten_days_before_the_bill_to_sixty_after_its_due_date():
    assert m.recorded_window(_bill()) == (date(2026, 3, 23), date(2026, 7, 3))
    assert m.recorded_hit(_tx("-4500.00", date(2026, 3, 23)), _bill()) is Fit.PART
    assert m.recorded_hit(_tx("-4500.00", date(2026, 3, 22)), _bill()) is None
    assert m.recorded_hit(_tx("-4500.00", date(2026, 7, 3)), _bill()) is Fit.PART
    assert m.recorded_hit(_tx("-4500.00", date(2026, 7, 4)), _bill()) is None
    assert m.recorded_window(_bill(issued_on=None, due_on=None)) is None


def test_the_payee_must_be_the_bills():
    stranger = {"counterparty_name": "Möbelhaus", "purpose": "Sofa"}
    assert m.recorded_hit(_tx("-4500.00", **stranger), _bill()) is None
    hmac = "ab" * 32
    assert (
        m.recorded_hit(
            _tx("-4500.00", counterparty_iban_hmac=hmac, **stranger), _bill(iban_hmac=hmac)
        )
        is Fit.PART
    )
    assert (
        m.recorded_hit(
            _tx("-4500.00", counterparty_name="X", purpose="Az GrESt-0815 Zahlung"),
            _bill(reference="GrESt-0815"),
        )
        is Fit.PART
    )


def test_a_split_bill_is_paid_whole_by_its_combined_total_or_by_one_record():
    bill = _bill(
        acquisition=[D("450.00")],
        private=[D("150.00")],
        lines=[D("450.00"), D("150.00")],
        issued_on=date(2026, 5, 10),
        due_on=None,
    )
    name = {"counterparty_name": "Landesjustizkasse Beispielland"}
    assert m.recorded_hit(_tx("-600.00", date(2026, 6, 25), **name), bill) is Fit.WHOLE
    # The private part alone is whole too (proof_match, D143: it proves the private_cost row).
    assert m.recorded_hit(_tx("-150.00", date(2026, 6, 25), **name), bill) is Fit.WHOLE
    assert m.recorded_hit(_tx("-450.00", date(2026, 6, 25), **name), bill) is Fit.PART
    assert sorted(m.recorded_amounts(bill)) == [D("150.00"), D("450.00"), D("600.00")]


def _facts(parts) -> DocumentFacts:
    return DocumentFacts.model_validate(
        {
            "id": str(uuid.uuid4()),
            "kind": "invoice",
            "property_id": str(PROPERTY),
            "category": "acquisition_cost",
            "payments": [{"amount": "600.00", "due_on": "2026-05-10"}],
            "parts": [{"category": c, "amount": a} for c, a in parts],
        }
    )


def test_the_card_never_proposes_a_part_a_nebenkosten_row_records():
    bill = _facts([("acquisition_cost", "450.00"), ("loan_fees", "150.00")])
    booked = date(2026, 5, 15)
    assert [p.amount for p in proposed_private_costs(booked, D("-600.00"), bill)] == [D("150.00")]
    assert [
        (p.category, p.amount)
        for p in proposed_private_costs(booked, D("-600.00"), bill, [D("450.00")])
    ] == [(C.LOAN_FEES, D("150.00"))]
    assert proposed_private_costs(booked, D("-600.00"), bill, [D("600.00")]) == []
    whole = _facts([])
    assert proposed_private_costs(booked, D("-600.00"), whole, [D("600.00")]) == []
