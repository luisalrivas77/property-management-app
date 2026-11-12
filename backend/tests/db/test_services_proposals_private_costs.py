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
from sqlalchemy import select

from hausbuch.db.tables import event as event_table
from hausbuch.schemas.ai import ClassifyOutput, ExtractedValue, SourceRef
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.enums import DocumentSubtype, DocumentType, LinkTarget
from hausbuch.schemas.events import EventType
from hausbuch.schemas.tax import TaxInputKind
from hausbuch.services import allocations, documents, matching, property_costs, proposals, review
from hausbuch.services.documents import get_document
from hausbuch.services.tax_inputs import current_of_document, list_tax_inputs

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
        # phase 3 D137: a repair also carries the work date (the bill's date) and its net
        "value.performed_on": "2026-06-10",
        "value.amount_net": "250.00",
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
    assert (row.value.performed_on, row.value.amount_net) == (date(2026, 6, 10), Decimal("250.00"))
    applied = {a.key: a.entity_id for a in result.applied}
    assert applied["tax_input:private_cost:repairs"] == row.id
    # The event keeps the category (a code), never the label (review F3).
    [event] = world.query(
        lambda conn: conn.execute(
            select(event_table.c.payload).where(
                event_table.c.type == EventType.PROPERTY_TAX_INPUT_RECORDED.value
            )
        ).all()
    )
    assert event.payload["value"]["category"] == "repairs"
    assert "Sanitär" not in str(event.payload)

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
    # phase 3 D137: the work date and the net leave with the repairs category
    assert (row.value.performed_on, row.value.amount_net) == (None, None)


def _reread(run, job, ws, doc, values):
    rerun = run(ws, proposals.rerun_proposal, doc.id)
    return job(
        ws,
        proposals.record_read_result,
        rerun.id,
        classification=INVOICE,
        classify_call_id=run(ws, _ai_call, doc.id, "classify"),
        values=values,
        extract_call_id=run(ws, _ai_call, doc.id, "extract"),
    )


def _in_force(read, ws, prop):
    return [r for r in _private(read, ws, prop) if not r.withdrawn]


@pytest.mark.parametrize("how", ["read_as_insurance", "changed_on_the_card"])
def test_a_bill_applied_again_under_another_category_replaces_its_row(
    world, run, read, job, blob_store, enqueued, how
):
    """Review F2: the bill read again (the model now codes it insurance, or the user changes
    Kategorie on the card) replaces its repairs row instead of adding a second one."""
    ws, prop = world.ws, world.property_id
    doc, ready, _ = _read_ready(run, job, ws, blob_store, enqueued, INVOICE, _repair(), b"%PDF i")
    _apply(run, ws, ready)
    [first] = _private(read, ws, prop)
    assert first.value.category == AllocationCategory.REPAIRS

    if how == "read_as_insurance":
        values = _repair() | {
            "cost_lines": _v(
                [
                    "Arbeitszeit Monteur | 180,00 EUR | insurance",
                    "Material | 70,00 EUR | insurance",
                    "Umsatzsteuer 19 % | 47,50 EUR | vat",
                ],
                checks=("cited", "validates"),
            )
        }
        again = _reread(run, job, ws, doc, values)
        assert "tax_input:private_cost:insurance" in {r.key for r in again.records}
        _apply(run, ws, again)
    else:
        again = _reread(run, job, ws, doc, _repair())
        key = "tax_input:private_cost:repairs"
        body = _accept_all(again, **{key: {"value.category": "insurance"}})
        run(ws, proposals.apply_proposal, again.id, body, iban_key=TEST_IBAN_KEY)

    [row] = _in_force(read, ws, prop)
    assert (row.value.category, row.document_id, row.supersedes_id) == (
        AllocationCategory.INSURANCE,
        doc.id,
        first.id,
    )


def test_a_mixed_bill_applied_again_keeps_its_two_rows(world, run, read, job, blob_store, enqueued):
    """Review F2: the rows of a bill's other parts are claimed by their own records, so a
    repeat writes nothing and the two rows stay."""
    ws, prop = world.ws, world.property_id
    values = _repair(total="357.00") | {
        "cost_lines": _v(
            [
                "Schätzgebühr Bank | 200,00 EUR | loan_fees",
                "Reparatur Tür | 100,00 EUR | repairs",
                "Umsatzsteuer 19 % | 57,00 EUR | vat",
            ],
            checks=("cited", "validates"),
        )
    }
    doc, ready, _ = _read_ready(run, job, ws, blob_store, enqueued, INVOICE, values, b"%PDF m")
    keys = {r.key for r in ready.records}
    assert {"tax_input:private_cost:loan_fees", "tax_input:private_cost:repairs"} <= keys
    _apply(run, ws, ready)
    before = {r.value.category: r.id for r in _private(read, ws, prop)}
    assert set(before) == {AllocationCategory.LOAN_FEES, AllocationCategory.REPAIRS}

    _apply(run, ws, _reread(run, job, ws, doc, values))
    after = {r.value.category: r.id for r in _in_force(read, ws, prop)}
    assert after == before
    assert len(read(ws, list_tax_inputs, prop, include_history=True)) == 2


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


def test_a_grundschuld_bill_is_stored_as_a_financing_bill(
    world, run, read, job, blob_store, enqueued
):
    """D138: the classifier's cost_basis_evidence is replaced by financing_invoice, which the
    apply stores on the document (migration 0022's CHECK takes it), beside the loan cost."""
    ws, prop = world.ws, world.property_id
    classified = INVOICE.model_copy(update={"subtype": DocumentSubtype.COST_BASIS_EVIDENCE})
    values = {
        "title": _v("Notarrechnung Grundschuldbestellung"),
        "subject": _v("Grundschuld-/Hypothekenbestellung mit ZV-Unterwerfung"),
        "document_date": _v("2026-03-20"),
        "sender": _v("Notarin Beispiel"),
        "amount_total": _v("612.88"),
    }
    doc, ready, _ = _read_ready(run, job, ws, blob_store, enqueued, classified, values, b"%PDF g")
    typing = next(r for r in ready.records if r.key == "typing")
    assert typing.fields["subtype"].value == "financing_invoice"
    assert any("Von einem anderen Konto bezahlt" in n for n in typing.notes)

    _apply(run, ws, ready)
    stored = read(ws, get_document, doc.id)
    assert stored.typing.subtype is DocumentSubtype.FINANCING_INVOICE
    [row] = _private(read, ws, prop)
    assert (row.value.category, row.value.amount) == (
        AllocationCategory.LOAN_FEES,
        Decimal("612.88"),
    )


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


# --- Phase 6 D161: a bill a private account's line pays (review F4 (ii), F6) ------------------


def _private_line(world: World, booked_on: date, doc=None):
    """A line of a private account of the household paying the repair bill, booked
    hh_property_cost on the flat with the bill as its Beleg when `doc` is given (confirmed)."""
    from db.test_services_private_matching import Private

    tx = Private(world).line(booked_on, "-297.50", iban=FIRM_IBAN, purpose="R-2026-0815")
    if doc is not None:
        _on_bill(world, tx, doc)
    return tx


def _on_bill(world: World, tx, doc) -> None:
    world.cmd(
        allocations.record_set,
        AllocationSet(
            transaction_id=tx,
            allocations=[
                AllocationDraft(
                    amount=Decimal("-297.50"),
                    category=AllocationCategory.HH_PROPERTY_COST,
                    property_id=world.property_id,
                    document_id=doc,
                )
            ],
            method=AllocationMethod.USER,
            confidence=Decimal("1.000"),
        ),
        confirm=True,
        auto=False,
    )


def test_a_bill_a_private_line_pays_proposes_no_private_cost_and_says_where(
    world, run, read, job, blob_store, enqueued
):
    """Review F4 (ii): the line is a private account's, so the reading proposes no
    private_cost (the line's card in Zu prüfen does) and never says „vom Mietkonto“."""
    ws = world.ws
    _private_line(world, date(2026, 6, 14))  # a document hit by its reference
    doc, ready, _ = _read_ready(run, job, ws, blob_store, enqueued, INVOICE, _repair(), b"%PDF q")
    assert not world.query(matching.paid_from_rental_account, doc.id, iban_key=TEST_IBAN_KEY)
    assert world.query(matching.paid_from_private_account, doc.id, iban_key=TEST_IBAN_KEY)
    assert not [r.key for r in ready.records if r.key.startswith("tax_input:")]
    typing = next(r for r in ready.records if r.key == "typing")
    assert not any("Mietkonto" in n for n in typing.notes)
    assert any("privaten Konto" in n and "Zu prüfen" in n for n in typing.notes)


def _linked_bill(world, run, job, blob_store, enqueued, data: bytes):
    """The repair bill read before any line pays it (its D135 private_cost record offered,
    not applied), linked to the flat as its reading would link it."""
    doc, ready, _ = _read_ready(run, job, world.ws, blob_store, enqueued, INVOICE, _repair(), data)
    assert "tax_input:private_cost:repairs" in {r.key for r in ready.records}
    world.cmd(documents.link_document, doc.id, LinkTarget.PROPERTY, world.property_id)
    return doc, ready


def _current_rows(world: World, doc_id):
    return world.query(current_of_document, doc_id, TaxInputKind.PRIVATE_COST)


@pytest.mark.parametrize("paid_on", [date(2026, 6, 15), date(2027, 1, 5)])
def test_the_card_first_then_the_reading_leaves_one_set_of_rows(
    world, run, read, job, blob_store, enqueued, paid_on
):
    """Review F6: the private line's card records the bill's rows (the year paid); the
    reading's proposal, built before the line existed, is applied afterwards: one set of rows
    (the same year: the reading's row replaces the card's; another year: nothing written)."""
    doc, ready = _linked_bill(
        world, run, job, blob_store, enqueued, b"%PDF f6a" + str(paid_on.year).encode()
    )
    tx = _private_line(world, paid_on, doc.id)
    [card] = [
        i
        for i in world.query(review.inbox)
        if i.kind is InboxItemKind.PROPERTY_COST_ROW and i.transaction_id == tx
    ]
    [recorded] = world.cmd(property_costs.record_proposed, tx)
    assert recorded.tax_year == paid_on.year
    _apply(run, world.ws, ready)
    [row] = _current_rows(world, doc.id)
    assert (row.tax_year, row.value.category, row.value.amount) == (
        paid_on.year,
        AllocationCategory.REPAIRS,
        Decimal("297.50"),
    )


@pytest.mark.parametrize("paid_on", [date(2026, 6, 15), date(2027, 1, 5)])
def test_the_reading_first_then_the_card_leaves_one_set_of_rows(
    world, run, read, job, blob_store, enqueued, paid_on
):
    """Review F6, the other order: the reading's row exists, so the card records nothing (its
    button returns the bill's rows); a row of another year than paid gets the year card."""
    doc, ready = _linked_bill(
        world, run, job, blob_store, enqueued, b"%PDF f6b" + str(paid_on.year).encode()
    )
    _apply(run, world.ws, ready)
    [before] = _current_rows(world, doc.id)
    tx = _private_line(world, paid_on, doc.id)
    assert [r.id for r in world.cmd(property_costs.record_proposed, tx)] == [before.id]
    assert [r.id for r in _current_rows(world, doc.id)] == [before.id]
    kinds = {i.kind for i in world.query(review.inbox) if i.transaction_id == tx}
    assert InboxItemKind.PROPERTY_COST_ROW not in kinds
    assert (InboxItemKind.PROPERTY_COST_YEAR in kinds) is (paid_on.year != 2026)
