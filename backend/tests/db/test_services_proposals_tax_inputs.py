"""The two certificates through pre-fill (docs/phases/phase-3.md §9, D14): the read writes
nothing to tax_input; the apply writes the figure through services/tax_inputs, with the
document as evidence, and supersedes the row of a corrected certificate."""

from datetime import date
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.test_services_proposals import _accept_all, _ai_call, _read_ready, _v
from db.test_services_proposals import enqueued as enqueued  # noqa: F401 (a fixture)
from db.test_services_proposals import job as job  # noqa: F401 (a fixture)
from sqlalchemy import func, select

from hausbuch.db.tables import tax_input as tax_input_table
from hausbuch.schemas.ai import ClassifyOutput
from hausbuch.schemas.enums import DocumentSubtype, DocumentType, PropertyKind
from hausbuch.schemas.loan import LoanCreate
from hausbuch.schemas.property import Address, PropertyCreate
from hausbuch.schemas.proposal import ProposalApplyBody, ProposalApplyRecord
from hausbuch.schemas.tax import TaxInputKind
from hausbuch.services import proposals
from hausbuch.services.errors import RuleViolationError
from hausbuch.services.loans import create_loan
from hausbuch.services.properties import create_property
from hausbuch.services.tax_inputs import list_tax_inputs

pytestmark = pytest.mark.db

FLAT = PropertyCreate(
    label="Wohnung Lindenstraße",
    kind=PropertyKind.ETW,
    address=Address(street="Lindenstraße", house_number="12", postal_code="10115", city="Berlin"),
    build_year=1994,
    notarized_on=date(2024, 3, 15),
    purchase_date=date(2024, 5, 2),
    purchase_price=Decimal("289000.00"),
)
ZINS = ClassifyOutput(
    type=DocumentType.ZINSBESCHEINIGUNG,
    subtype=None,
    contains_tenant_data=False,
    language="de",
    confidence="high",
)
ZINS_VALUES = {
    "title": _v("Zinsbescheinigung 2026"),
    "lender": _v("Sparkasse Berlin"),
    "contract_number": _v("4711 0815"),
    "tax_year": _v(2026),
    "interest_paid": _v("8123.45", page=1, quote="Gezahlte Zinsen 8.123,45 EUR"),
}
HAUSGELD = ClassifyOutput(
    type=DocumentType.HAUSGELDABRECHNUNG,
    subtype=None,
    contains_tenant_data=False,
    language="de",
    confidence="high",
)
HAUSGELD_VALUES = {
    "street": _v("Lindenstraße"),
    "house_number": _v("12"),
    "postal_code": _v("10115"),
    "tax_year": _v(2026),
    "settlement_result": _v("-84.20"),
    "ruecklage_contribution": _v("600.00"),
    "ruecklage_spent": _v("412.50", page=2, quote="Entnahme aus der Rücklage 412,50"),
}


def _loan(run, ws, prop, number="4711 0815"):
    return run(
        ws,
        create_loan,
        LoanCreate(
            property_id=prop,
            lender="Sparkasse Berlin",
            contract_number=number,
            principal=Decimal("200000.00"),
            nominal_rate=Decimal("0.0365"),
            monthly_payment=Decimal("941.67"),
            disbursed_on=date(2024, 4, 25),
            first_payment_on=date(2024, 5, 30),
        ),
        iban_key=TEST_IBAN_KEY,
    ).id


def _rows(read, ws) -> int:
    return read(
        ws, lambda conn: conn.execute(select(func.count()).select_from(tax_input_table)).scalar()
    )


def _apply(run, ws, proposal, body=None):
    body = body or _accept_all(proposal)
    return run(ws, proposals.apply_proposal, proposal.id, body, iban_key=TEST_IBAN_KEY)


def test_a_zinsbescheinigung_proposes_the_certified_interest_and_applies_it(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    prop = run(ws, create_property, FLAT).id
    _loan(run, ws, prop, number="9999")  # another contract of the same lender
    loan = _loan(run, ws, prop)
    doc, ready, _ = _read_ready(run, job, ws, blob_store, enqueued, ZINS, ZINS_VALUES, b"%PDF z")
    records = {r.key: r for r in ready.records}
    assert list(records) == ["typing", "link:loan", "tax_input:interest_certified"]
    proposed = records["tax_input:interest_certified"]
    assert proposed.entity_type == "tax_input"
    assert {f: v.value for f, v in proposed.fields.items()} == {
        "tax_year": 2026,
        "loan_id": str(loan),
        "value.amount": "8123.45",
    }
    assert proposed.fields["value.amount"].source.quote == "Gezahlte Zinsen 8.123,45 EUR"
    assert records["link:loan"].target_id == loan
    # The read wrote nothing to tax_input (D14).
    assert _rows(read, ws) == 0

    result = _apply(run, ws, ready)
    [row] = read(ws, list_tax_inputs, prop)
    assert (row.kind, row.tax_year, row.loan_id, row.document_id) == (
        TaxInputKind.INTEREST_CERTIFIED,
        2026,
        loan,
        doc.id,
    )
    assert row.value.amount == Decimal("8123.45")
    applied = {a.key: a.entity_id for a in result.applied}
    assert applied["tax_input:interest_certified"] == row.id


def test_without_a_matching_loan_the_user_picks_it(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    prop = run(ws, create_property, FLAT).id
    _loan(run, ws, prop, number="1")
    second = _loan(run, ws, prop, number="2")
    _, ready, _ = _read_ready(run, job, ws, blob_store, enqueued, ZINS, ZINS_VALUES, b"%PDF z")
    records = {r.key: r for r in ready.records}
    assert list(records) == ["typing", "tax_input:interest_certified"]
    assert records["tax_input:interest_certified"].fields["loan_id"].value is None

    # Not picked: the apply refuses and writes nothing.
    with pytest.raises(ValueError, match="needs the loan"):
        _apply(run, ws, ready)
    assert _rows(read, ws) == 0
    body = _accept_all(ready, **{"tax_input:interest_certified": {"loan_id": str(second)}})
    _apply(run, ws, ready, body)
    [row] = read(ws, list_tax_inputs, prop)
    assert row.loan_id == second


def test_a_skipped_record_writes_nothing_and_extra_values_are_ignored(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    prop = run(ws, create_property, FLAT).id
    _loan(run, ws, prop)
    _, ready, _ = _read_ready(run, job, ws, blob_store, enqueued, ZINS, ZINS_VALUES, b"%PDF z")
    body = ProposalApplyBody(
        records=[
            ProposalApplyRecord(key="typing", values={"type": "zinsbescheinigung"}),
            ProposalApplyRecord(key="tax_input:interest_certified", skip=True),
        ]
    )
    _apply(run, ws, ready, body)
    assert _rows(read, ws) == 0

    # A second read of the same certificate; values beyond the proposed fields (a
    # withdrawal, another property) are not taken from the client.
    _, again, _ = _read_ready(run, job, ws, blob_store, enqueued, ZINS, ZINS_VALUES, b"%PDF z2")
    extra = {"withdrawn": True, "property_id": "00000000-0000-4000-8000-000000000000"}
    _apply(run, ws, again, _accept_all(again, **{"tax_input:interest_certified": extra}))
    [row] = read(ws, list_tax_inputs, prop)
    assert (row.property_id, row.withdrawn, row.supersedes_id) == (prop, False, None)


def test_a_corrected_certificate_supersedes_the_figure_and_a_repeat_writes_nothing(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    prop = run(ws, create_property, FLAT).id
    _loan(run, ws, prop)
    first_doc, first, _ = _read_ready(
        run, job, ws, blob_store, enqueued, ZINS, ZINS_VALUES, b"%PDF z1"
    )
    _apply(run, ws, first)
    [original] = read(ws, list_tax_inputs, prop)

    # The same document read again and applied again: the same figure, nothing new.
    rerun = run(ws, proposals.rerun_proposal, first_doc.id)
    classify_id = run(ws, _ai_call, first_doc.id, "classify")
    extract_id = run(ws, _ai_call, first_doc.id, "extract")
    ready = job(
        ws,
        proposals.record_read_result,
        rerun.id,
        classification=ZINS,
        classify_call_id=classify_id,
        values=ZINS_VALUES,
        extract_call_id=extract_id,
    )
    result = _apply(run, ws, ready)
    assert {a.key: a.entity_id for a in result.applied}["tax_input:interest_certified"] == (
        original.id
    )
    assert _rows(read, ws) == 1

    # The bank's corrected certificate: a new row replaces the first.
    corrected = ZINS_VALUES | {"interest_paid": _v("8100.00")}
    doc, ready, _ = _read_ready(run, job, ws, blob_store, enqueued, ZINS, corrected, b"%PDF z3")
    _apply(run, ws, ready)
    [current] = read(ws, list_tax_inputs, prop)
    assert (current.value.amount, current.document_id, current.supersedes_id) == (
        Decimal("8100.00"),
        doc.id,
        original.id,
    )
    assert len(read(ws, list_tax_inputs, prop, include_history=True)) == 2


def test_a_hausgeldabrechnung_proposes_the_ruecklage_spent(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    prop = run(ws, create_property, FLAT).id
    doc, ready, _ = _read_ready(
        run, job, ws, blob_store, enqueued, HAUSGELD, HAUSGELD_VALUES, b"%PDF hg"
    )
    records = {r.key: r for r in ready.records}
    assert list(records) == ["typing", "link:property", "tax_input:ruecklage_spent"]
    assert {f: v.value for f, v in records["tax_input:ruecklage_spent"].fields.items()} == {
        "tax_year": 2026,
        "value.amount": "412.50",
    }
    assert _rows(read, ws) == 0
    _apply(run, ws, ready)
    [row] = read(ws, list_tax_inputs, prop)
    assert (row.kind, row.tax_year, row.document_id, row.value.amount) == (
        TaxInputKind.RUECKLAGE_SPENT,
        2026,
        doc.id,
        Decimal("412.50"),
    )


def test_a_hausgeldabrechnung_of_an_unknown_address_proposes_no_figure(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    run(ws, create_property, FLAT)
    other = FLAT.address.model_copy(update={"street": "Allee", "house_number": "2"})
    run(ws, create_property, FLAT.model_copy(update={"label": "Allee", "address": other}))
    elsewhere = HAUSGELD_VALUES | {"street": _v("Bahnhofstraße")}
    _, ready, _ = _read_ready(run, job, ws, blob_store, enqueued, HAUSGELD, elsewhere, b"%PDF x")
    assert [r.key for r in ready.records] == ["typing"]


# --- A Restnutzungsdauer-Gutachten (phase 2 D147) -------------------------------------------

GUTACHTEN = ClassifyOutput(
    type=DocumentType.OTHER,
    subtype=DocumentSubtype.APPRAISAL,
    contains_tenant_data=False,
    language="de",
    confidence="high",
)
GUTACHTEN_VALUES = {
    "title": _v("Gutachten Restnutzungsdauer"),
    "document_date": _v("2024-04-10"),
    "street": _v("Lindenstraße"),
    "house_number": _v("12"),
    "postal_code": _v("10115"),
    "remaining_useful_life": _v("24", page=4, quote="Restnutzungsdauer: 24 Jahre"),
    "valuation_date": _v("2024-03-15", page=1, quote="Wertermittlungsstichtag: 15.03.2024"),
}


def test_a_gutachten_proposes_the_shorter_useful_life_with_itself_as_evidence(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    """FLAT was handed over on 02.05.2024 and completed in 1994 (50 years): 24 years at
    15.03.2024 less one whole month is 287 months, rounded up to 24 years from 2024."""
    ws, _ = fresh_workspaces
    prop = run(ws, create_property, FLAT).id
    doc, ready, _ = _read_ready(
        run, job, ws, blob_store, enqueued, GUTACHTEN, GUTACHTEN_VALUES, b"%PDF ga"
    )
    records = {r.key: r for r in ready.records}
    assert list(records) == ["typing", "link:property", "tax_input:useful_life"]
    proposed = records["tax_input:useful_life"]
    assert {f: v.value for f, v in proposed.fields.items()} == {
        "tax_year": 2024,
        "value.years": 24,
    }
    assert proposed.fields["value.years"].derived_from == [
        "remaining_useful_life",
        "valuation_date",
    ]
    assert records["link:property"].target_id == prop
    assert _rows(read, ws) == 0  # the read writes nothing (D14)

    result = _apply(run, ws, ready)
    [row] = read(ws, list_tax_inputs, prop)
    assert (row.kind, row.tax_year, row.document_id, row.value.years) == (
        TaxInputKind.USEFUL_LIFE,
        2024,
        doc.id,
        24,
    )
    assert {a.key: a.entity_id for a in result.applied}["tax_input:useful_life"] == row.id


def test_a_gutachten_read_again_writes_nothing_and_a_changed_life_supersedes(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    prop = run(ws, create_property, FLAT).id
    doc, first, _ = _read_ready(
        run, job, ws, blob_store, enqueued, GUTACHTEN, GUTACHTEN_VALUES, b"%PDF ga1"
    )
    _apply(run, ws, first)
    [original] = read(ws, list_tax_inputs, prop)

    def reread():
        rerun = run(ws, proposals.rerun_proposal, doc.id)
        return job(
            ws,
            proposals.record_read_result,
            rerun.id,
            classification=GUTACHTEN,
            classify_call_id=run(ws, _ai_call, doc.id, "classify"),
            values=GUTACHTEN_VALUES,
            extract_call_id=run(ws, _ai_call, doc.id, "extract"),
        )

    # The same Gutachten applied again: the same row, nothing new.
    result = _apply(run, ws, reread())
    assert {a.key: a.entity_id for a in result.applied}["tax_input:useful_life"] == original.id
    assert _rows(read, ws) == 1

    # A life the user raises to the statutory 50 years is refused, as on Steuern; nothing
    # is written.
    again = reread()
    raised = _accept_all(again, **{"tax_input:useful_life": {"value.years": 50}})
    with pytest.raises(RuleViolationError) as exc:
        _apply(run, ws, again, raised)
    assert exc.value.code == "tax_input.useful_life_not_shorter"
    assert _rows(read, ws) == 1

    # A life the user corrects to 26 years supersedes the first row.
    _apply(run, ws, again, _accept_all(again, **{"tax_input:useful_life": {"value.years": 26}}))
    [current] = read(ws, list_tax_inputs, prop)
    assert (current.value.years, current.document_id, current.supersedes_id) == (
        26,
        doc.id,
        original.id,
    )
