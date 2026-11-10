"""A bank line paying a bill sorted by its cost lines (docs/phases/phase-2.md D135): the
document matcher proposes one allocation per kind of cost, read from the stored reading by
services.matching.document_facts, always to review. Synthetic data only (World's flat, an
invented notary)."""

from datetime import date
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.matching_support import World
from db.test_services_matching import _match, _rows
from sqlalchemy import insert

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import document_link as link_table
from hausbuch.schemas.property import Address, PropertyCreate
from hausbuch.services import matching
from hausbuch.services.properties import create_property

pytestmark = pytest.mark.db

NOTARY_IBAN = "DE75512108001245126199"  # invented
MIXED_LINES = [
    "Beurkundung Kaufvertrag | 800,00 EUR | acquisition_cost",
    "Grundschuldbestellung | 200,00 EUR | loan_fees",
    "Umsatzsteuer 19 % | 190,00 EUR | vat",
]


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0])


def _bill(world: World, lines, *, subtype="cost_basis_evidence", total="1190.00"):
    fields = {
        "title": "Notarrechnung",
        "document_date": "2026-04-10",
        "sender": "Notarin Dr. Beispiel",
        "subject": "Beurkundungen vom 10.04.2026",
        "amount_total": total,
        "reference": "RE26/00077",
        "iban": NOTARY_IBAN,
        "cost_lines": lines,
    }
    return world.document("invoice", fields, subtype=subtype)


def test_a_mixed_bill_is_split_by_its_lines_to_review(world):
    loan = world.loan()
    doc = _bill(world, MIXED_LINES)
    tx = world.line(date(2026, 4, 15), "-1190.00", iban=NOTARY_IBAN, purpose="RE26/00077")
    _match(world, tx)
    rows = {r.category: r for r in _rows(world, tx)}
    assert set(rows) == {"loan_fees", "acquisition_cost"}
    fees, purchase = rows["loan_fees"], rows["acquisition_cost"]
    assert (fees.amount, fees.loan_id, fees.document_id, fees.status) == (
        Decimal("-238.00"),
        loan,
        doc,
        "proposed",
    )
    assert (purchase.amount, purchase.loan_id, purchase.document_id, purchase.status) == (
        Decimal("-952.00"),
        None,
        doc,
        "proposed",
    )


def test_lines_that_do_not_sum_leave_the_bill_whole(world):
    doc = _bill(world, MIXED_LINES[:2])  # no USt line: 1,000.00 against 1,190.00
    tx = world.line(date(2026, 4, 15), "-1190.00", iban=NOTARY_IBAN)
    _match(world, tx)
    (row,) = _rows(world, tx)
    assert (row.category, row.amount, row.document_id) == (
        "acquisition_cost",
        Decimal("-1190.00"),
        doc,
    )


def test_a_utility_bill_is_never_split(world):
    lines = ["Strom | 100,00 EUR | electricity", "Grundgebühr | 19,00 EUR | bank_fees"]
    doc = _bill(world, lines, subtype="utility_bill", total="119.00")
    tx = world.line(date(2026, 4, 15), "-119.00", iban=NOTARY_IBAN)
    _match(world, tx)
    (row,) = _rows(world, tx)
    assert (row.category, row.document_id) == ("uncategorized", doc)


def test_a_bill_of_two_properties_and_one_loan_is_read_without_error(world):
    """Review F5: the links name two properties (so no property) and one loan, and
    paid_from_rental_account reads it with an empty loan map: the Grundschuld part takes the
    loan, the property stays None, and nothing raises."""
    loan = world.loan()
    other = world.cmd(
        create_property,
        PropertyCreate(
            label="Wohnung Beispielweg",
            kind="etw",
            address=Address(
                street="Beispielweg", house_number="1", postal_code="10115", city="Berlin"
            ),
            build_year=2000,
            notarized_on=date(2025, 3, 1),
            purchase_date=date(2025, 4, 1),
            purchase_price=Decimal("200000.00"),
        ),
    ).id
    doc = _bill(world, MIXED_LINES)
    with workspace_transaction(world.engine, world.ws) as conn:
        for link in ({"property_id": other}, {"loan_id": loan}):
            conn.execute(
                insert(link_table).values(
                    workspace_id=world.ws, document_id=doc, created_at=world.now, **link
                )
            )
    assert not world.query(matching.paid_from_rental_account, doc, iban_key=TEST_IBAN_KEY)
    e = world.query(matching.evidence_of, doc)
    facts = matching.document_facts(e, iban_key=TEST_IBAN_KEY, loans_by_property={})
    assert (facts.loan_id, facts.property_id) == (loan, None)
