"""The Grundsteuerbescheid's schedule through pre-fill (docs/phases/phase-5.md D126, §11): the
read proposes tax_input:grundsteuer_schedule with a date and an amount per Fälligkeit, each
with its page and quote, and writes nothing; the apply records the schedule row through
services/tax_inputs with the Bescheid as its document. Synthetic data only."""

from datetime import date
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.test_services_proposals import _accept_all, _ai_call, _read_ready, _v
from db.test_services_proposals import enqueued as enqueued  # noqa: F401 (a fixture)
from db.test_services_proposals import job as job  # noqa: F401 (a fixture)
from db.test_services_proposals_tax_inputs import FLAT, _rows

from hausbuch.schemas.ai import ClassifyOutput
from hausbuch.schemas.enums import DocumentSubtype, DocumentType
from hausbuch.schemas.tax import GrundsteuerInstalment, TaxInputKind
from hausbuch.services import proposals
from hausbuch.services.properties import create_property
from hausbuch.services.tax_inputs import list_tax_inputs

pytestmark = pytest.mark.db

_ = _ai_call  # imported for the shared helpers' fixtures

BESCHEID = ClassifyOutput(
    type=DocumentType.BESCHEID,
    subtype=DocumentSubtype.GRUNDSTEUERBESCHEID,
    contains_tenant_data=False,
    language="de",
    confidence="high",
)
QUOTE_DATES = "fällig am 15.02., 15.05., 15.08. und 15.11."
VALUES = {
    "title": _v("Grundsteuerbescheid 2025"),
    "street": _v("Lindenstraße"),
    "house_number": _v("12"),
    "postal_code": _v("10115"),
    "tax_year": _v(2025, quote="ab dem Kalenderjahr 2025"),
    "yearly_amount": _v("493.80", page=2, quote="Jahresbetrag 493,80 €"),
    "installment_amount": _v("123.45", page=2, quote="Vierteljahresbetrag 123,45 €"),
    "due_dates": _v(["15.02.", "15.05.", "15.08.", "15.11."], page=2, quote=QUOTE_DATES),
}


def _apply(run, ws, proposal, **overrides):
    body = _accept_all(proposal, **overrides)
    return run(ws, proposals.apply_proposal, proposal.id, body, iban_key=TEST_IBAN_KEY)


def test_the_bescheid_proposes_its_schedule_and_the_apply_records_it(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    prop = run(ws, create_property, FLAT).id
    doc, ready, _ = _read_ready(run, job, ws, blob_store, enqueued, BESCHEID, VALUES, b"%PDF g")
    records = {r.key: r for r in ready.records}
    assert list(records) == ["typing", "link:property", "tax_input:grundsteuer_schedule"]
    proposed = records["tax_input:grundsteuer_schedule"]
    values = {f: v.value for f, v in proposed.fields.items()}
    assert values == {
        "tax_year": 2025,
        "value.annual_amount": "493.80",
        "value.due_on_1": "2025-02-15",
        "value.amount_1": "123.45",
        "value.due_on_2": "2025-05-15",
        "value.amount_2": "123.45",
        "value.due_on_3": "2025-08-15",
        "value.amount_3": "123.45",
        "value.due_on_4": "2025-11-15",
        "value.amount_4": "123.45",
        "value.rhythm": "quarterly",
    }
    # Page and quote per value: the dates keep the Fälligkeiten's, the amounts the Bescheid's.
    assert proposed.fields["value.due_on_3"].source.quote == QUOTE_DATES
    assert proposed.fields["value.amount_2"].source.quote == "Vierteljahresbetrag 123,45 €"
    assert proposed.fields["value.annual_amount"].source.page == 2
    assert proposed.notes == []
    assert _rows(read, ws) == 0  # the read writes nothing (D14)

    result = _apply(run, ws, ready)
    [row] = read(ws, list_tax_inputs, prop)
    assert (row.kind, row.tax_year, row.document_id) == (
        TaxInputKind.GRUNDSTEUER_SCHEDULE,
        2025,
        doc.id,
    )
    assert row.value.annual_amount == Decimal("493.80")
    assert row.value.instalments == [
        GrundsteuerInstalment(due_on=date(2025, m, 15), amount=Decimal("123.45"))
        for m in (2, 5, 8, 11)
    ]
    assert (row.value.rhythm, row.value.payment_mode) == ("quarterly", None)
    applied = {a.key: a.entity_id for a in result.applied}
    assert applied["tax_input:grundsteuer_schedule"] == row.id


def test_without_a_quarterly_amount_the_yearly_one_is_divided(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    """Two Fälligkeiten and no stated instalment: yearly / 2, derived; the user may set the
    payment mode before applying."""
    ws, _ = fresh_workspaces
    prop = run(ws, create_property, FLAT).id
    values = {k: v for k, v in VALUES.items() if k != "installment_amount"} | {
        "yearly_amount": _v("400.00", page=2, quote="Jahresbetrag 400,00 €"),
        "due_dates": _v(["15.08.", "15.02."], page=2, quote="fällig am 15.02. und 15.08."),
    }
    _, ready, _ = _read_ready(run, job, ws, blob_store, enqueued, BESCHEID, values, b"%PDF h")
    proposed = {r.key: r for r in ready.records}["tax_input:grundsteuer_schedule"]
    assert {f: v.value for f, v in proposed.fields.items()} == {
        "tax_year": 2025,
        "value.annual_amount": "400.00",
        "value.due_on_1": "2025-02-15",
        "value.amount_1": "200.00",
        "value.due_on_2": "2025-08-15",
        "value.amount_2": "200.00",
        "value.rhythm": "half_yearly",
    }
    assert proposed.fields["value.amount_1"].derived_from == ["yearly_amount", "due_dates"]

    _apply(
        run,
        ws,
        ready,
        **{"tax_input:grundsteuer_schedule": {"value.payment_mode": "sepa_mandate"}},
    )
    [row] = read(ws, list_tax_inputs, prop)
    assert [i.due_on for i in row.value.instalments] == [date(2025, 2, 15), date(2025, 8, 15)]
    assert row.value.payment_mode == "sepa_mandate"


def test_a_bescheid_for_no_property_proposes_no_schedule(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    """A tax input belongs to a property: without one found, the read proposes neither the
    link nor the schedule."""
    ws, _ = fresh_workspaces
    _, ready, _ = _read_ready(run, job, ws, blob_store, enqueued, BESCHEID, VALUES, b"%PDF i")
    assert [r.key for r in ready.records] == ["typing"]
    assert _rows(read, ws) == 0
