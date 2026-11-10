"""A bill's part paid from another account (docs/phases/phase-2.md D135): the read proposes
tax_input:private_cost:<category> only when no bank line of the read accounts pays the bill
(services.matching.paid_from_rental_account); the apply writes it through services/tax_inputs
with the document as evidence, one row per bill and category, and a repeat writes nothing.
Synthetic data only (World's flat, an invented repair firm)."""

from datetime import date
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.matching_support import World
from db.test_services_proposals import _accept_all, _ai_call, _read_ready
from db.test_services_proposals import enqueued as enqueued  # noqa: F401 (a fixture)
from db.test_services_proposals import job as job  # noqa: F401 (a fixture)

from hausbuch.schemas.ai import ClassifyOutput, ExtractedValue, SourceRef
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.enums import DocumentType
from hausbuch.schemas.tax import TaxInputKind
from hausbuch.services import allocations, matching, proposals
from hausbuch.services.tax_inputs import list_tax_inputs

pytestmark = pytest.mark.db

FIRM_IBAN = "DE75512108001245126199"  # invented
INVOICE = ClassifyOutput(
    type=DocumentType.INVOICE,
    subtype=None,
    contains_tenant_data=False,
    language="de",
    confidence="high",
)


def _v(value, checks=("cited",)) -> ExtractedValue:
    return ExtractedValue(
        value=value,
        source=SourceRef(page=1, quote="…"),
        checks=list(checks),
        confidence="high",
    )


def _repair(reference: str = "R-2026-0815", total: str = "297.50") -> dict:
    return {
        "title": _v("Rechnung Reparatur Sanitär"),
        "document_date": _v("2026-06-10"),
        "sender": _v("Sanitär Beispiel GmbH"),
        "amount_total": _v(total),
        "reference": _v(reference),
        "cost_lines": _v(
            [
                "Arbeitszeit Monteur | 180,00 EUR | repairs",
                "Material | 70,00 EUR | repairs",
                "Umsatzsteuer 19 % | 47,50 EUR | vat",
            ],
            checks=("cited", "validates"),
        ),
    }


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0])


def _apply(run, ws, proposal):
    return run(
        ws, proposals.apply_proposal, proposal.id, _accept_all(proposal), iban_key=TEST_IBAN_KEY
    )


def _private(read, ws, prop):
    return read(ws, list_tax_inputs, prop, kind=TaxInputKind.PRIVATE_COST)


def test_a_repair_bill_paid_from_another_account_is_a_private_cost(
    world, run, read, job, blob_store, enqueued
):
    ws, prop = world.ws, world.property_id
    doc, ready, _ = _read_ready(run, job, ws, blob_store, enqueued, INVOICE, _repair(), b"%PDF r")
    records = {r.key: r for r in ready.records}
    assert "tax_input:private_cost:repairs" in records
    proposed = records["tax_input:private_cost:repairs"]
    assert {f: v.value for f, v in proposed.fields.items()} == {
        "tax_year": 2026,
        "value.cost_class": "other",
        "value.category": "repairs",
        "value.label": "Rechnung Reparatur Sanitär",
        "value.amount": "297.50",
    }
    assert _private(read, ws, prop) == []  # the read writes nothing (D14)

    result = _apply(run, ws, ready)
    [row] = _private(read, ws, prop)
    assert (row.tax_year, row.document_id, row.value.category, row.value.amount) == (
        2026,
        doc.id,
        AllocationCategory.REPAIRS,
        Decimal("297.50"),
    )
    assert row.value.cost_class == "other" and row.value.label == "Rechnung Reparatur Sanitär"
    applied = {a.key: a.entity_id for a in result.applied}
    assert applied["tax_input:private_cost:repairs"] == row.id

    # The same bill read and applied again: the same row, nothing new.
    rerun = run(ws, proposals.rerun_proposal, doc.id)
    again = job(
        ws,
        proposals.record_read_result,
        rerun.id,
        classification=INVOICE,
        classify_call_id=run(ws, _ai_call, doc.id, "classify"),
        values=_repair(),
        extract_call_id=run(ws, _ai_call, doc.id, "extract"),
    )
    result = _apply(run, ws, again)
    assert {a.key: a.entity_id for a in result.applied}["tax_input:private_cost:repairs"] == row.id
    assert len(read(ws, list_tax_inputs, prop, include_history=True)) == 1

    # Another bill of the same year and category: a second row beside the first.
    second, ready, _ = _read_ready(
        run,
        job,
        ws,
        blob_store,
        enqueued,
        INVOICE,
        _repair("R-2026-0900", "119.00")
        | {
            "cost_lines": _v(
                ["Dichtung | 100,00 EUR | repairs", "USt | 19,00 EUR | vat"],
                checks=("cited", "validates"),
            )
        },
        b"%PDF r2",
    )
    _apply(run, ws, ready)
    rows = _private(read, ws, prop)
    assert sorted((r.document_id, r.value.amount) for r in rows) == sorted(
        [(doc.id, Decimal("297.50")), (second.id, Decimal("119.00"))]
    )
    assert all(r.supersedes_id is None for r in rows)


@pytest.mark.parametrize(
    ("category", "cost_class"), [("insurance", "other"), ("loan_fees", "financing")]
)
def test_a_category_changed_on_the_card_brings_its_class(
    world, run, read, job, blob_store, enqueued, category, cost_class
):
    """The user changes Kategorie and leaves Art as proposed: the class follows the category
    (financing exactly for loan_fees), so the apply is never refused for the pair."""
    ws, prop = world.ws, world.property_id
    _, ready, _ = _read_ready(run, job, ws, blob_store, enqueued, INVOICE, _repair(), b"%PDF c")
    key = "tax_input:private_cost:repairs"
    body = _accept_all(ready, **{key: {"value.category": category}})
    run(ws, proposals.apply_proposal, ready.id, body, iban_key=TEST_IBAN_KEY)
    [row] = _private(read, ws, prop)
    assert (row.value.category, row.value.cost_class) == (AllocationCategory(category), cost_class)


def test_a_bill_a_bank_line_pays_proposes_no_private_cost(
    world, run, read, job, blob_store, enqueued
):
    ws = world.ws
    world.line(date(2026, 6, 14), "-297.50", purpose="Rechnung R-2026-0815 Sanitär")
    _, ready, _ = _read_ready(run, job, ws, blob_store, enqueued, INVOICE, _repair(), b"%PDF p")
    keys = [r.key for r in ready.records]
    assert not [k for k in keys if k.startswith("tax_input:")]
    typing = next(r for r in ready.records if r.key == "typing")
    assert any("vom Mietkonto" in n for n in typing.notes)


# --- paid_from_rental_account -----------------------------------------------------------------


def _bill(world: World, **fields) -> object:
    values = {
        "document_date": "2026-06-10",
        "amount_total": "297.50",
        "reference": "R-2026-0815",
        "iban": FIRM_IBAN,
    } | fields
    return world.document("invoice", values)


def _paid(world: World, doc, iban_key=TEST_IBAN_KEY) -> bool:
    return world.query(matching.paid_from_rental_account, doc, iban_key=iban_key)


def test_no_line_no_payment(world):
    assert not _paid(world, _bill(world))


def test_a_line_of_the_amount_with_the_reference_pays_it(world):
    doc = _bill(world)
    world.line(date(2026, 6, 19), "-297.50", purpose="RE R-2026-0815")
    assert _paid(world, doc)
    assert _paid(world, doc, iban_key=None)


def test_a_line_outside_the_window_or_amount_does_not(world):
    doc = _bill(world)
    world.line(date(2026, 6, 21), "-297.50", purpose="R-2026-0815")  # 11 days
    world.line(date(2026, 6, 12), "-297.52", purpose="R-2026-0815")  # 0.02 off
    world.line(date(2026, 6, 12), "-297.50", purpose="Miete Juni")  # neither IBAN nor reference
    assert not _paid(world, doc)


def test_the_iban_counts_only_with_the_key(world):
    doc = _bill(world)
    world.line(date(2026, 6, 12), "-297.50", iban=FIRM_IBAN, purpose="Sanitär")
    assert _paid(world, doc)
    assert not _paid(world, doc, iban_key=None)


def test_an_allocation_carrying_the_document_pays_it(world):
    doc = _bill(world, reference="X")
    tx = world.line(date(2026, 8, 1), "-297.50", purpose="Überweisung")
    answer = AllocationSet(
        transaction_id=tx,
        allocations=[
            AllocationDraft(
                amount=Decimal("-297.50"),
                category=AllocationCategory.REPAIRS,
                property_id=world.property_id,
                document_id=doc,
            )
        ],
        method=AllocationMethod.USER,
        confidence=Decimal("1.000"),
    )
    assert not _paid(world, doc)
    world.cmd(allocations.record_set, answer, confirm=False, auto=False)
    assert _paid(world, doc)
