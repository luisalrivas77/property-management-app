"""The Einkommensteuerbescheid through pre-fill (docs/phases/phase-4b.md D129, §8.4, §12 step 6,
D14, D103): the read writes no income_input row; the apply writes the household bescheid_zve
through services/income_inputs with this document as the Bescheid; a second Bescheid for the
year supersedes the first; the tax rate's source becomes "bescheid"; no identifier is stored
anywhere and no name enters an event.

All names, figures and identifiers are invented.
"""

import json
from datetime import date
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.test_jobs_document_read import _classify, _pdf, _run
from db.test_jobs_document_read import setup as setup  # noqa: F401 (a fixture)
from db.test_services_proposals import _accept_all, _ai_call, _read_ready, _v
from db.test_services_proposals import enqueued as enqueued  # noqa: F401 (a fixture)
from db.test_services_proposals import job as job  # noqa: F401 (a fixture)
from db.test_services_proposals_return_inputs import _everything_stored
from sqlalchemy import select

from hausbuch.ai.fake import FakeModelClient, cited_response, text_response
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import income_input as income_input_table
from hausbuch.schemas.ai import ClassifyOutput
from hausbuch.schemas.enums import DocumentSubtype, DocumentType, ProposalStatus
from hausbuch.schemas.events import EventType
from hausbuch.schemas.performance import IncomeInputCreate
from hausbuch.schemas.property import OwnerCreate
from hausbuch.services import income_inputs, proposals
from hausbuch.services.owners import create_owner

pytestmark = pytest.mark.db

# --- The invented identifiers printed on the synthetic Bescheid --------------------------

STEUERNUMMER = "143/123/45678"
ELSTER_NUMBER = "2143012345678"  # the 13-digit ELSTER form of a Steuernummer
ID_NR_A = "12 345 678 903"
ID_NR_B = "98 765 432 109"
STREET = "Musterweg 9"
TOWN = "60311 Frankfurt am Main"
IBAN = "DE89 3704 0044 0532 0130 00"
FORBIDDEN = (
    STEUERNUMMER,
    "123/45678",
    "45678",
    "3012345",
    "345 678",
    "12345678903",
    "765 432",
    "98765432109",
    "Musterweg",
    "60311",
    "Frankfurt",
    "3704",
    "0532",
)
NAMES = ("Max", "Erika", "Muster")

BESCHEID_ANSWER = {
    "title": (
        "Bescheid für 2026 über Einkommensteuer, Solidaritätszuschlag und Kirchensteuer",
        1,
        f"Finanzamt Musterstadt Steuernummer {STEUERNUMMER} Bescheid für 2026 über "
        "Einkommensteuer, Solidaritätszuschlag und Kirchensteuer",
    ),
    "document_date": ("12.05.2027", 1, f"Datum 12.05.2027 St.-Nr. {STEUERNUMMER}"),
    "tax_year": ("2026", 1, f"Bescheid für 2026 über Einkommensteuer ELSTER {ELSTER_NUMBER}"),
    "taxable_income": (
        "84.300 €",
        2,
        "Summe der Einkünfte 92.500 Gesamtbetrag der Einkünfte 91.200 Einkommen 86.400 "
        "zu versteuerndes Einkommen 84.300",
    ),
    "assessment_type": (
        "Zusammenveranlagung",
        1,
        f"Zusammenveranlagung IdNr. Ehemann {ID_NR_A} IdNr. Ehefrau {ID_NR_B}",
    ),
    # The model wrote the Steuernummer into the names: the scrub takes it out of the value,
    # the quote and the stored raw text.
    "addressee": (
        f"Max und Erika Muster ({STEUERNUMMER})",
        1,
        f"Herrn und Frau Max und Erika Muster, {STREET}, {TOWN}; Erstattung auf {IBAN}",
    ),
}

BESCHEID = ClassifyOutput(
    type=DocumentType.BESCHEID,
    subtype=DocumentSubtype.EINKOMMENSTEUERBESCHEID,
    contains_tenant_data=False,
    language="de",
    confidence="high",
)


def _values(amount: str, on: str):
    return {
        "title": _v("Bescheid für 2026 über Einkommensteuer"),
        "document_date": _v(on),
        "tax_year": _v(2026),
        "taxable_income": _v(amount, page=2),
        "assessment_type": _v("Zusammenveranlagung"),
        "addressee": _v("Max und Erika Muster"),
    }


def _owners(run, ws):
    for name in ("Erika Muster", "Max Muster"):
        run(ws, create_owner, OwnerCreate(full_name=name))


def _rows(read, ws):
    def query(conn):
        rows = conn.execute(
            select(income_input_table).order_by(income_input_table.c.recorded_at)
        ).all()
        return [r._asdict() for r in rows]

    return read(ws, query)


def _events_text(read, ws) -> str:
    """Every event of the read and the apply: the owners' own creation events, which carry
    their names by design (phase 1), left out."""

    def query(conn):
        rows = conn.execute(select(event_table).where(event_table.c.entity_type != "owner"))
        return json.dumps([r._asdict() for r in rows], default=str, ensure_ascii=False)

    return read(ws, query)


def _apply(run, ws, proposal, body=None):
    body = body or _accept_all(proposal)
    return run(ws, proposals.apply_proposal, proposal.id, body, iban_key=TEST_IBAN_KEY)


def _stored_with_income_inputs(read, ws) -> str:
    def query(conn):
        rows = [r._asdict() for r in conn.execute(select(income_input_table)).all()]
        return json.dumps(rows, default=str, ensure_ascii=False)

    return _everything_stored(read, ws) + read(ws, query)


def _tax_rate(read, ws):
    return read(
        ws,
        income_inputs.tax_rate,
        tax_year=2026,
        result=Decimal("0.00"),
        today=date(2027, 6, 1),
    )


# --- Read by the job and applied ------------------------------------------------------------


def test_a_bescheid_read_and_applied_records_the_zve_and_no_identifier(setup, run, read):
    client = FakeModelClient(
        [
            text_response(_classify("bescheid", "einkommensteuerbescheid")),
            cited_response(BESCHEID_ANSWER, model="claude-sonnet-5-5"),
        ]
    )
    deps, ws, doc, proposal_id = setup(_pdf(2), client)
    _owners(run, ws)
    _run(deps, ws, doc, proposal_id)

    ready = read(ws, proposals.get_proposal, proposal_id)
    assert ready.status is ProposalStatus.READY
    assert ready.kind == "einkommensteuerbescheid"
    records = {r.key: r for r in ready.records}
    assert list(records) == ["typing", "income_input:bescheid_zve"]
    assert records["typing"].fields["subtype"].value == "einkommensteuerbescheid"
    zve = records["income_input:bescheid_zve"]
    assert zve.fields["tax_year"].value == 2026
    amount = zve.fields["value.amount"]
    assert amount.value == "84300.00" and amount.confidence == "high"
    assert amount.source is not None and amount.source.page == 2
    assert "zu versteuerndes Einkommen 84.300" in amount.source.quote
    assert zve.notes == []  # joint, addressed to both owners, the year's first Bescheid
    assert _rows(read, ws) == []  # the read wrote no row (D14)
    assert _tax_rate(read, ws).missing  # nothing entered yet

    result = _apply(run, ws, ready)
    [row] = _rows(read, ws)
    assert (row["kind"], row["tax_year"], row["owner_id"], row["document_id"]) == (
        "bescheid_zve",
        2026,
        None,
        doc,
    )
    assert row["value"] == {"amount": "84300.00"}
    assert row["supersedes_id"] is None and row["note"] is None
    applied = {a.key: a.entity_id for a in result.applied}
    assert applied["income_input:bescheid_zve"] == row["id"]

    # The Steuerwirkung's base is now the Bescheid's.
    rate = _tax_rate(read, ws)
    assert rate.zve is not None
    assert rate.zve.source == "bescheid" and rate.zve.amount == Decimal("84300.00")

    stored = _stored_with_income_inputs(read, ws)
    assert "84300.00" in stored and "Max und Erika Muster" in stored  # sanity: the extraction
    for fragment in FORBIDDEN:
        assert fragment not in stored, fragment
    events = _events_text(read, ws)
    assert EventType.WORKSPACE_INCOME_INPUT_RECORDED.value in events
    for name in NAMES:
        assert name not in events, name


def test_a_second_bescheid_for_the_year_supersedes_the_first(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    _owners(run, ws)
    first_doc, first, _ = _read_ready(
        run, job, ws, blob_store, enqueued, BESCHEID, _values("84300.00", "2027-05-12"), b"%PDF 1"
    )
    _apply(run, ws, first)

    # An Änderungsbescheid: the review names the Bescheid it replaces, by its date.
    second_doc, second, _ = _read_ready(
        run, job, ws, blob_store, enqueued, BESCHEID, _values("83900.00", "2027-08-20"), b"%PDF 2"
    )
    notes = {r.key: r for r in second.records}["income_input:bescheid_zve"].notes
    assert any("ersetzt den Bescheid vom 12.05.2027" in n for n in notes)
    _apply(run, ws, second)
    old, new = _rows(read, ws)
    assert old["document_id"] == first_doc.id and new["document_id"] == second_doc.id
    assert new["supersedes_id"] == old["id"] and new["value"] == {"amount": "83900.00"}
    current = read(ws, income_inputs.list_income_inputs, tax_year=2026)
    assert [r.id for r in current] == [new["id"]]
    assert _tax_rate(read, ws).zve.amount == Decimal("83900.00")


def test_the_same_bescheid_again_writes_nothing_and_another_year_is_refused(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    _owners(run, ws)
    doc, ready, _ = _read_ready(
        run, job, ws, blob_store, enqueued, BESCHEID, _values("84300.00", "2027-05-12"), b"%PDF"
    )
    _apply(run, ws, ready)
    [row] = _rows(read, ws)

    def rerun(values):
        again = run(ws, proposals.request_read, doc.id)
        return job(
            ws,
            proposals.record_read_result,
            again.id,
            classification=BESCHEID,
            classify_call_id=run(ws, _ai_call, doc.id, "classify"),
            values=values,
            extract_call_id=run(ws, _ai_call, doc.id, "extract"),
        )

    # Read again and applied unchanged: the row stands, nothing is written.
    same = rerun(_values("84300.00", "2027-05-12"))
    notes = {r.key: r for r in same.records}["income_input:bescheid_zve"].notes
    assert any("schon übernommen" in n for n in notes)
    result = _apply(run, ws, same)
    assert {a.key: a.entity_id for a in result.applied}["income_input:bescheid_zve"] == row["id"]
    assert len(_rows(read, ws)) == 1

    # The same document confirmed for another year: refused, nothing written.
    other = rerun(_values("84300.00", "2027-05-12"))
    body = _accept_all(other, **{"income_input:bescheid_zve": {"tax_year": 2025}})
    with pytest.raises(ValueError, match="already recorded as income_input"):
        _apply(run, ws, other, body)
    assert len(_rows(read, ws)) == 1


def test_a_typed_bescheid_zve_is_superseded_by_the_read_one(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    """A zvE typed on the Eigentümer page (phase 4) is replaced by the Bescheid read later."""
    ws, _ = fresh_workspaces
    _owners(run, ws)
    doc, ready, _ = _read_ready(
        run, job, ws, blob_store, enqueued, BESCHEID, _values("84300.00", "2027-05-12"), b"%PDF a"
    )
    typed_doc, _, _ = _read_ready(
        run, job, ws, blob_store, enqueued, BESCHEID, _values("1.00", "2027-01-02"), b"%PDF b"
    )
    typed = run(
        ws,
        income_inputs.create_income_input,
        IncomeInputCreate(
            kind="bescheid_zve",
            tax_year=2026,
            document_id=typed_doc.id,
            value={"amount": "80000.00"},
        ),
    )
    _apply(run, ws, ready)
    old, new = _rows(read, ws)
    assert old["id"] == typed.id and new["supersedes_id"] == typed.id
    assert new["document_id"] == doc.id
