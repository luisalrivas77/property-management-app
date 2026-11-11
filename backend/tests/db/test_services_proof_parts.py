"""A payment proof of one line of its bill (docs/phases/phase-3.md D150), against the real
database: services.tax.bill_lines reads the cost lines of a bill's stored reading, the draft
facts (DraftFacts.bill_parts) and the pre-fill's bill (ExistingProofBill.parts) carry them,
and the draft then says one part is proven and what is still missing.

Synthetic data only (World's flat, bought 2024, invented amounts); the reading is written
directly, never API output."""

from datetime import date
from decimal import Decimal

import pytest
from db.matching_support import World
from sqlalchemy import insert

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import document_link as link_table
from hausbuch.rules.tax.draft import _payment_proofs, bills_without_proof
from hausbuch.schemas.enums import AcquisitionCostKind
from hausbuch.schemas.property import AcquisitionCostCreate
from hausbuch.services import proposals, tax
from hausbuch.services.properties import add_acquisition_cost

pytestmark = pytest.mark.db

TODAY = date(2026, 11, 11)
PURCHASE_YEAR = 2024  # World's flat
HALF = Decimal("5687.00")
TWO_NOTICES = [
    "Grunderwerbsteuer Käufer A | 5.687,00 EUR | acquisition_cost",
    "Grunderwerbsteuer Käufer B | 5.687,00 EUR | acquisition_cost",
]


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0])


def _notices(world: World, lines=TWO_NOTICES, total="11374.00"):
    """One document holding both buyers' Grunderwerbsteuerbescheide, read as two lines, and
    the property's ONE acquisition cost row of the total with it as Beleg."""
    doc = world.document(
        "bescheid",
        {
            "title": "Grunderwerbsteuerbescheide",
            "document_date": "2024-04-20",
            "sender": "Finanzamt Beispiel",
            "amount_total": total,
            "cost_lines": lines,
        },
        type="bescheid",
    )
    world.cmd(
        add_acquisition_cost,
        AcquisitionCostCreate(
            property_id=world.property_id,
            kind=AcquisitionCostKind.REAL_ESTATE_TRANSFER_TAX,
            amount=Decimal(total),
            document_id=doc,
        ),
    )
    return doc


def _proof(world: World, bill, amount: str):
    """A payment proof of `amount` linked to the bill (as the to-do's upload links it)."""
    proof = world.document(
        "payment_proof",
        {"title": "Überweisung", "paid_on": "2024-05-02", "amount": amount},
        type="other",
        subtype="payment_proof",
    )
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(
            insert(link_table).values(
                workspace_id=world.ws,
                document_id=proof,
                target_document_id=bill,
                created_at=world.now,
            )
        )
    return proof


def _facts(world: World):
    return world.query(tax.draft_facts, world.property_id, tax_year=2026, today=TODAY)


def test_the_bills_lines_reach_the_draft_and_the_prefill(world):
    doc = _notices(world)
    assert world.query(tax.bill_lines, doc) == [HALF, HALF]
    assert _facts(world).bill_parts == {doc: [HALF, HALF]}
    assert world.query(proposals._bill_facts, doc).parts == [HALF, HALF]


def test_a_document_without_lines_has_none(world):
    doc = world.document(
        "invoice",
        {"title": "Notarrechnung", "amount_total": "2380.00"},
        type="invoice",
    )
    assert world.query(tax.bill_lines, doc) == []
    assert world.query(proposals._bill_facts, doc).parts == []
    assert doc not in _facts(world).bill_parts


def test_one_buyers_proof_proves_a_part_and_names_the_rest(world):
    doc = _notices(world)
    _proof(world, doc, "-5687.00")
    facts = _facts(world)
    assert bills_without_proof(facts, PURCHASE_YEAR) == [doc]
    [warning] = [
        w for w in _payment_proofs(facts, PURCHASE_YEAR) if w.params["document"] == str(doc)
    ]
    assert (warning.params["proof_amount"], warning.params["proof_rest"]) == ("5687.00", "5687.00")

    _proof(world, doc, "5687.00")  # the other buyer's
    facts = _facts(world)
    assert bills_without_proof(facts, PURCHASE_YEAR) == []


def test_lines_that_do_not_sum_to_the_row_prove_no_part(world):
    doc = _notices(world, total="11000.00")
    _proof(world, doc, "5687.00")
    [warning] = [
        w for w in _payment_proofs(_facts(world), PURCHASE_YEAR) if w.params["document"] == str(doc)
    ]
    assert "proof_rest" not in warning.params
    assert warning.params["proof_amount"] == "5687.00"
