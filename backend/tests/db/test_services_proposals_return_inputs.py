"""The two pay documents through pre-fill (docs/phases/phase-4b.md §8.2, §8.3, D14, D96,
D103, D105): the read writes no return_input row; the apply writes it through
services/return_inputs.create_input for an owner of this workspace only; no identifier is
stored anywhere (extraction, ai_call, proposal, events, rows).

services/return_inputs is step 1's (built in parallel): while it is absent, a stand-in with
its public signature `create_input(ctx, data: ReturnInputCreate)` writes the row and its event
the way step 0's schema says; once step 1 is merged these tests run against the real one.
All names, employers, figures and identifiers are invented.
"""

import importlib.util
import json
import re
import sys
import types
from datetime import date
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.test_jobs_document_read import _classify, _pdf, _run
from db.test_jobs_document_read import setup as setup  # noqa: F401 (a fixture)
from db.test_services_proposals import _accept_all, _ai_call, _read_ready, _v
from db.test_services_proposals import enqueued as enqueued  # noqa: F401 (a fixture)
from db.test_services_proposals import job as job  # noqa: F401 (a fixture)
from sqlalchemy import insert, select

import hausbuch.services as services_package
from hausbuch.ai.fake import FakeModelClient, cited_response, text_response
from hausbuch.db.tables import ai_call as ai_call_table
from hausbuch.db.tables import document_extraction as extraction_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import proposal as proposal_table
from hausbuch.db.tables import return_input as return_input_table
from hausbuch.schemas.ai import ClassifyOutput
from hausbuch.schemas.employment import CERTIFICATE_AMOUNTS, ReturnInputCreate, return_input_amounts
from hausbuch.schemas.enums import DocumentType, ProposalStatus
from hausbuch.schemas.events import EventType, ReturnInputRecorded
from hausbuch.schemas.property import OwnerCreate
from hausbuch.schemas.proposal import ProposalApplyBody, ProposalApplyRecord
from hausbuch.services import proposals
from hausbuch.services.allocations import user_id
from hausbuch.services.errors import NotFoundError
from hausbuch.services.events import append_event
from hausbuch.services.owners import create_owner

pytestmark = pytest.mark.db

_REAL = importlib.util.find_spec("hausbuch.services.return_inputs") is not None


def _stand_in_create_input(ctx, data: ReturnInputCreate):
    """Step 1's create_input as step 0's schema fixes it: one row, one owner event."""
    row = ctx.conn.execute(
        insert(return_input_table)
        .values(
            workspace_id=ctx.workspace_id,
            **data.model_dump(exclude={"value"}),
            value=data.value.model_dump(mode="json"),
            recorded_at=ctx.now,
            recorded_by=user_id(ctx),
        )
        .returning(*return_input_table.c)
    ).one()
    append_event(
        ctx,
        EventType.OWNER_RETURN_INPUT_RECORDED,
        data.owner_id,
        ReturnInputRecorded(
            return_input_id=row.id,
            kind=data.kind,
            tax_year=data.tax_year,
            document_id=data.document_id,
            supersedes_id=data.supersedes_id,
            amounts=return_input_amounts(data.value),
        ),
    )
    return row


@pytest.fixture(autouse=True)
def return_inputs_service(monkeypatch):
    if _REAL:
        yield
        return
    module = types.ModuleType("hausbuch.services.return_inputs")
    module.create_input = _stand_in_create_input
    monkeypatch.setitem(sys.modules, "hausbuch.services.return_inputs", module)
    monkeypatch.setattr(services_package, "return_inputs", module, raising=False)
    yield


# --- The invented identifiers printed on the synthetic documents ---------------------------

ID_NR = "12 345 678 903"
SV_NR = "65 140388 M 00 3"
ETIN = "MSTRERKA88C14K"
BIRTH = "14.03.1988"
PERSONNEL = "P-731942"
STREET = "Musterweg 9a"
TOWN = "60311 Frankfurt am Main"
IBAN = "DE89 3704 0044 0532 0130 00"
# What must never be stored, in any spelling the scrub or the parser could leave.
FORBIDDEN = (
    "345 678",
    "12345678903",
    "140388",
    "MSTRERKA",
    BIRTH,
    "1988-03-14",
    "731942",
    "Musterweg",
    "60311",
    "Frankfurt",
    "3704",
    "0532",
)
HEADER = (
    f"Erika Musterfrau, geb. {BIRTH}, {STREET}, {TOWN}; IdNr. {ID_NR}; eTIN {ETIN}; "
    f"Personalnummer {PERSONNEL}; SV-Nr. {SV_NR}"
)
CERTIFICATE_ANSWER = {
    # The model wrote the identification number into the name: the scrub takes it out of
    # the value, the quote and the stored raw text.
    "employee_name": (f"Erika Musterfrau (IdNr. {ID_NR})", 1, HEADER),
    "employer_name": ("Muster GmbH", 1, f"Muster GmbH, Industriestraße 5, {TOWN}"),
    "tax_year": ("2026", 1, f"Lohnsteuerbescheinigung 2026 eTIN {ETIN}"),
    "period_from": ("01.01.2026", 1, "1. Dauer des Dienstverhältnisses vom 01.01.2026"),
    "period_to": ("31.12.2026", 1, "bis 31.12.2026"),
    "tax_class": ("4 / 0,950", 1, "Steuerklasse/Faktor 4 / 0,950"),
    "church_tax_mark": ("ev", 1, "Kirchensteuermerkmale ev"),
    "gross": ("60.000,00", 1, f"Personalnummer {PERSONNEL} 3. Bruttoarbeitslohn 60.000,00"),
    "income_tax": ("9.192,00", 1, "4. Einbehaltene Lohnsteuer von 3. 9.192,00"),
    "pension_employee": ("5.580,00", 1, "23. a) Arbeitnehmeranteil 5.580,00"),
    "health_employee": ("4.890,00", 1, f"25. Arbeitnehmerbeiträge {SV_NR} 4.890,00"),
    "care_employee": ("1.080,00", 1, "26. Pflegeversicherung 1.080,00"),
    "unemployment_employee": ("780,00", 1, f"27. Arbeitslosenversicherung 780,00 {IBAN}"),
    "soli": "unbekannt",
}

PAYSLIP = ClassifyOutput(
    type=DocumentType.GEHALTSABRECHNUNG,
    subtype=None,
    contains_tenant_data=False,
    language="de",
    confidence="high",
)
PAYSLIP_VALUES = {
    "employee_name": _v("Erika Musterfrau"),
    "employer_name": _v("Muster GmbH"),
    "period_month": _v("2026-07-01", quote="Abrechnungszeitraum 01.07.2026 - 31.07.2026"),
    "tax_class": _v("4"),
    "gross_month": _v("5000.00", quote="Steuer-Brutto 5.000,00 35.000,00"),
    "gross_ytd": _v("35000.00", quote="Steuer-Brutto 5.000,00 35.000,00"),
    "income_tax_month": _v("766.00"),
    "income_tax_ytd": _v("5362.00"),
}
CERTIFICATE = ClassifyOutput(
    type=DocumentType.LOHNSTEUERBESCHEINIGUNG,
    subtype=None,
    contains_tenant_data=False,
    language="de",
    confidence="high",
)
CERTIFICATE_VALUES = {
    "employee_name": _v("Erika Musterfrau"),
    "employer_name": _v("Muster GmbH"),
    "tax_year": _v(2026),
    "period_from": _v("2026-01-01"),
    "period_to": _v("2026-12-31"),
    "tax_class": _v("4"),
    "gross": _v("60000.00"),
    "income_tax": _v("9192.00"),
}


def _owner(run, ws, name="Erika Musterfrau"):
    return run(ws, create_owner, OwnerCreate(full_name=name)).id


def _pay_rows(read, ws):
    def query(conn):
        rows = conn.execute(
            select(return_input_table).order_by(return_input_table.c.recorded_at)
        ).all()
        return [r._asdict() for r in rows]

    return read(ws, query)


def _apply(run, ws, proposal, body=None):
    body = body or _accept_all(proposal)
    return run(ws, proposals.apply_proposal, proposal.id, body, iban_key=TEST_IBAN_KEY)


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _everything_stored(read, ws) -> str:
    """Every stored text of the workspace this feature writes: extraction, ai_call output and
    raw text, the proposal, events and return_input rows. Ids removed (random hex)."""

    def query(conn):
        tables = (extraction_table, ai_call_table, proposal_table, event_table, return_input_table)
        rows = [r._asdict() for t in tables for r in conn.execute(select(t)).all()]
        return json.dumps(rows, default=str, ensure_ascii=False)

    return _UUID.sub("<id>", read(ws, query))


# --- The certificate, read by the job and applied -------------------------------------------


def test_a_certificate_read_and_applied_stores_no_identifier_anywhere(setup, run, read):
    client = FakeModelClient(
        [
            text_response(_classify("lohnsteuerbescheinigung")),
            cited_response(CERTIFICATE_ANSWER, model="claude-sonnet-5-5"),
        ]
    )
    deps, ws, doc, proposal_id = setup(_pdf(1), client)
    owner = _owner(run, ws)
    _run(deps, ws, doc, proposal_id)

    ready = read(ws, proposals.get_proposal, proposal_id)
    assert ready.status is ProposalStatus.READY
    records = {r.key: r for r in ready.records}
    assert list(records) == ["typing", "link:owner", "return_input:wage_certificate"]
    assert records["link:owner"].target_id == owner
    row = records["return_input:wage_certificate"]
    assert row.fields["owner_id"].value == str(owner)
    assert row.fields["owner_id"].confidence == "medium"  # the name carried more than the name
    assert row.fields["value.gross"].value == "60000.00"
    assert row.fields["value.soli"].value is None
    assert _pay_rows(read, ws) == []  # the read wrote no row (D14)

    result = _apply(run, ws, ready)
    [stored] = _pay_rows(read, ws)
    assert (stored["owner_id"], stored["kind"], stored["tax_year"], stored["document_id"]) == (
        owner,
        "wage_certificate",
        2026,
        doc,
    )
    value = stored["value"]
    assert value["gross"] == "60000.00" and value["tax_class"] == 4
    assert value["tax_class_factor"] is True and value["church_tax_marked"] is True
    # Q-O29: every line not read is 0 and named in unread, never a silent 0
    read_lines = {k for k in CERTIFICATE_ANSWER if k in CERTIFICATE_AMOUNTS} - {"soli"}
    assert value["unread"] == [n for n in CERTIFICATE_AMOUNTS if n not in read_lines]
    assert all(Decimal(value[n]) == 0 for n in value["unread"])
    applied = {a.key: a.entity_id for a in result.applied}
    assert applied["return_input:wage_certificate"] == stored["id"]

    stored_text = _everything_stored(read, ws)
    assert "Erika Musterfrau" in stored_text and "60000.00" in stored_text  # sanity
    for fragment in FORBIDDEN:
        assert fragment not in stored_text, fragment


# --- The payslip ------------------------------------------------------------------------------


def test_a_payslip_row_shows_its_projection_and_replaces_the_same_month(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    owner = _owner(run, ws)
    doc, ready, _ = _read_ready(
        run, job, ws, blob_store, enqueued, PAYSLIP, PAYSLIP_VALUES, b"%PDF juli"
    )
    row = {r.key: r for r in ready.records}["return_input:payslip"]
    assert any("Hochrechnung 2026: 60.000,00 €" in n for n in row.notes)
    assert row.fields["value.months_remaining_paid"].value == 5
    _apply(run, ws, ready)
    [first] = _pay_rows(read, ws)
    assert (first["owner_id"], first["kind"], first["document_id"]) == (owner, "payslip", doc.id)
    assert first["value"]["months_remaining_paid"] == 5
    assert first["value"]["gross"] == {"month": "5000.00", "ytd": "35000.00"}
    assert first["value"]["period_month"] == "2026-07-01"

    # A corrected July payslip from another upload: the review says so, the row replaces it.
    values = PAYSLIP_VALUES | {"gross_month": _v("5100.00"), "gross_ytd": _v("35100.00")}
    _, again, _ = _read_ready(run, job, ws, blob_store, enqueued, PAYSLIP, values, b"%PDF juli 2")
    notes = {r.key: r for r in again.records}["return_input:payslip"].notes
    assert any("Juli 2026" in n and "ersetzt" in n for n in notes)
    _apply(run, ws, again)
    first_row, second = _pay_rows(read, ws)
    assert second["supersedes_id"] == first_row["id"]

    # August is another month: a new row next to July's.
    august = PAYSLIP_VALUES | {"period_month": _v("2026-08-01")}
    _, aug, _ = _read_ready(run, job, ws, blob_store, enqueued, PAYSLIP, august, b"%PDF aug")
    _apply(run, ws, aug)
    third = _pay_rows(read, ws)[-1]
    assert third["supersedes_id"] is None and third["value"]["months_remaining_paid"] == 4


def test_the_same_document_read_again_with_the_same_values_writes_nothing(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    _owner(run, ws)
    doc, ready, _ = _read_ready(
        run, job, ws, blob_store, enqueued, CERTIFICATE, CERTIFICATE_VALUES, b"%PDF lstb"
    )
    _apply(run, ws, ready)
    [row] = _pay_rows(read, ws)
    # a re-run of the same document, applied unchanged
    rerun = run(ws, proposals.request_read, doc.id)
    again = job(
        ws,
        proposals.record_read_result,
        rerun.id,
        classification=CERTIFICATE,
        classify_call_id=run(ws, _ai_call, doc.id, "classify"),
        values=CERTIFICATE_VALUES,
        extract_call_id=run(ws, _ai_call, doc.id, "extract"),
    )
    notes = {r.key: r for r in again.records}["return_input:wage_certificate"].notes
    assert any("schon übernommen" in n for n in notes)
    result = _apply(run, ws, again)
    assert _pay_rows(read, ws) == [row]
    assert {a.key: a.entity_id for a in result.applied}["return_input:wage_certificate"] == row[
        "id"
    ]


# --- The owner: this workspace's only ---------------------------------------------------------


def test_another_workspaces_owner_is_never_linked_nor_accepted(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, other = fresh_workspaces
    stranger = _owner(run, other)  # same name, other workspace
    _owner(run, ws, "Max Mustermann")
    _, ready, _ = _read_ready(
        run, job, ws, blob_store, enqueued, CERTIFICATE, CERTIFICATE_VALUES, b"%PDF lstb b"
    )
    records = {r.key: r for r in ready.records}
    assert records["link:owner"].target_id is None
    assert records["return_input:wage_certificate"].fields["owner_id"].value is None

    body = _accept_all(ready, **{"return_input:wage_certificate": {"owner_id": str(stranger)}})
    body = ProposalApplyBody(
        records=[
            r if r.key != "link:owner" else ProposalApplyRecord(key=r.key, skip=True)
            for r in body.records
        ]
    )
    with pytest.raises(NotFoundError):
        _apply(run, ws, ready, body)
    assert _pay_rows(read, ws) == [] and _pay_rows(read, other) == []
    assert read(ws, proposals.get_proposal, ready.id).status is ProposalStatus.READY


def test_an_owner_picked_in_the_review_is_the_rows_owner(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    picked = _owner(run, ws, "Erika Beispiel-Musterfrau")
    _, ready, _ = _read_ready(
        run, job, ws, blob_store, enqueued, CERTIFICATE, CERTIFICATE_VALUES, b"%PDF lstb c"
    )
    assert {r.key: r for r in ready.records}["link:owner"].target_id is None
    body = _accept_all(ready, **{"link:owner": {"target_id": str(picked)}})
    _apply(run, ws, ready, body)
    [row] = _pay_rows(read, ws)
    assert row["owner_id"] == picked


def test_an_owner_id_other_than_the_linked_owner_is_refused(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    ws, _ = fresh_workspaces
    _owner(run, ws)
    second = _owner(run, ws, "Max Mustermann")
    _, ready, _ = _read_ready(
        run, job, ws, blob_store, enqueued, CERTIFICATE, CERTIFICATE_VALUES, b"%PDF lstb d"
    )
    body = _accept_all(ready, **{"return_input:wage_certificate": {"owner_id": str(second)}})
    with pytest.raises(ValueError, match="another owner"):
        _apply(run, ws, ready, body)
    assert _pay_rows(read, ws) == []


def test_only_the_records_fields_are_taken_from_the_values(
    run, read, job, fresh_workspaces, blob_store, enqueued
):
    """A client cannot smuggle a supersedes_id, a withdrawal, a note or its own unread list."""
    ws, _ = fresh_workspaces
    _owner(run, ws)
    _, ready, _ = _read_ready(
        run, job, ws, blob_store, enqueued, CERTIFICATE, CERTIFICATE_VALUES, b"%PDF lstb e"
    )
    extra = {
        "supersedes_id": "00000000-0000-0000-0000-000000000001",
        "withdrawn": True,
        "note": "x",
        "value.unread": [],
    }
    body = _accept_all(ready, **{"return_input:wage_certificate": extra})
    _apply(run, ws, ready, body)
    [row] = _pay_rows(read, ws)
    assert row["supersedes_id"] is None and row["withdrawn"] is False and row["note"] is None
    assert "soli" in row["value"]["unread"]


def test_period_dates_in_the_row(run, read, job, fresh_workspaces, blob_store, enqueued):
    ws, _ = fresh_workspaces
    _owner(run, ws)
    _, ready, _ = _read_ready(
        run, job, ws, blob_store, enqueued, CERTIFICATE, CERTIFICATE_VALUES, b"%PDF lstb f"
    )
    _apply(run, ws, ready)
    [row] = _pay_rows(read, ws)
    assert date.fromisoformat(row["value"]["period_from"]) == date(2026, 1, 1)
    assert date.fromisoformat(row["value"]["period_to"]) == date(2026, 12, 31)
