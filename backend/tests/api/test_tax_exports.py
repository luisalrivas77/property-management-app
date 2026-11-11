"""Exports of the Anlage V draft over HTTP (docs/phases/phase-3.md §10, §12, D59, D65): POST
queues the job, the job (run here directly, as the worker would) writes the row, file and
event, GET reports the state with a signed download URL, the list shows the history. Also:
another workspace sees none of it, the sheet, the CSVs and the payment proofs carry no name,
full IBAN or purpose text (D5), the archive holds every linked original and the bank's PDF
statements untouched (D144), and an archive over the limit is refused, the statements left
out before any Beleg."""

import csv
import hashlib
import io
import zipfile
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from api_support import TOKEN_A, create, flat, ok, upload
from db.db_support import _admin
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import FreeText
from test_tax import SAMPLE_IBAN, ZIP, _confirm, sessions  # noqa: F401  (fixture by name)

from hausbuch.jobs.document_read import JobDeps
from hausbuch.jobs.enqueue import TAX_EXPORT_TASK
from hausbuch.jobs.tax_export import run_tax_export
from hausbuch.schemas.tax import TaxExportKind
from hausbuch.services import tax_export as tax_export_service

pytestmark = pytest.mark.db

COUNTERPARTY_IBAN = "DE16100000000000001001"  # the camt fixture's "Partei A"
TENANT_NAME = "Anna Beispiel"  # api_support.new_tenant


# --- Fixtures and helpers --------------------------------------------------------------


@pytest.fixture
def booked(client_a) -> dict:
    """As test_tax.booked, keeping the tenancy: the flat (bought 1 May 2024), the camt fixture
    imported on its rental account, a repair and an other income confirmed for it."""
    setup = flat(client_a)
    pid = setup["property"]["id"]
    account = create(
        client_a,
        "/api/accounts",
        {
            "label": "Mietkonto",
            "bank": "Sparkasse",
            "iban": SAMPLE_IBAN,
            "opening_balance": "0.00",
            "opening_balance_on": "2026-03-31",
        },
    )
    imported = client_a.post(
        f"/api/accounts/{account['id']}/imports",
        files={"file": ("umsaetze.zip", ZIP, "application/zip")},
    )
    assert ok(imported, 201)["status"] == "reconciled"
    lines = ok(client_a.get(f"/api/accounts/{account['id']}/transactions"))
    repair = next(x for x in lines if x["amount"] == "-360.18")
    income = next(x for x in lines if x["amount"] == "241.58")  # from "Partei A"
    _confirm(client_a, repair, "repairs", pid)
    _confirm(client_a, income, "other_income", pid)
    return {"pid": pid, "tenancy_id": setup["tenancy"]["id"]}


@pytest.fixture
def ws_a(sessions) -> UUID:  # noqa: F811
    return sessions[TOKEN_A].workspace_id


@pytest.fixture
def deps(app_engine, settings, blob_store) -> JobDeps:
    return JobDeps(engine=app_engine, settings=settings, blob_store=blob_store, model_client=None)


@pytest.fixture
def jobs(dbname):
    """The tax_export jobs of a workspace: (job id, args, status), oldest first."""

    def _jobs(workspace_id: UUID) -> list[tuple[int, dict, str]]:
        with _admin(dbname) as conn:
            rows = conn.execute(
                "SELECT id, args, status FROM procrastinate_jobs"
                " WHERE task_name = %s AND args->>'workspace_id' = %s ORDER BY id",
                (TAX_EXPORT_TASK, str(workspace_id)),
            ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    return _jobs


@pytest.fixture
def set_job_status(dbname):
    def _set(job_id: int, status: str) -> None:
        with _admin(dbname) as conn:
            conn.execute(
                "UPDATE procrastinate_jobs SET status = %s WHERE id = %s", (status, job_id)
            )

    return _set


def _job_of(jobs, ws, export_id: str) -> tuple[int, dict, str]:
    (job,) = [j for j in jobs(ws) if j[1]["export_id"] == export_id]
    return job


def _run_job(deps, args: dict, attempt: int = 0) -> str:
    return run_tax_export(
        deps,
        workspace_id=UUID(args["workspace_id"]),
        export_id=UUID(args["export_id"]),
        property_id=UUID(args["property_id"]),
        tax_year=int(args["tax_year"]),
        kind=TaxExportKind(args["kind"]),
        created_by=UUID(args["created_by"]) if args["created_by"] else None,
        attempt=attempt,
    )


def _request(client, property_id, kind="field_sheet", tax_year=2026):
    return client.post(
        f"/api/properties/{property_id}/anlage-v/exports",
        json={"tax_year": tax_year, "kind": kind},
    )


def _export(client, deps, jobs, ws, property_id, kind="field_sheet") -> dict:
    """POST, check it is queued, run its job, and return the ready state."""
    export_id = ok(_request(client, property_id, kind), 202)["export_id"]
    state = ok(client.get(f"/api/tax-exports/{export_id}"))
    assert (state["status"], state["download_url"], state["kind"]) == ("queued", None, kind)
    _, args, status = _job_of(jobs, ws, export_id)
    assert status == "todo" and args["property_id"] == property_id
    assert _run_job(deps, args) == "written"
    state = ok(client.get(f"/api/tax-exports/{export_id}"))
    assert state["status"] == "ready" and state["error_code"] is None
    return state


def _download(client, state) -> bytes:
    response = client.get(state["download_url"])
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith("attachment;")
    return response.content


def _typed(client, data: bytes, *, type_: str, subtype: str, title: str, target: tuple) -> dict:
    document = ok(upload(client, data, filename=f"{subtype}.pdf"), 201)
    typing = {"typing": {"type": type_, "subtype": subtype, "title": title}}
    ok(client.put(f"/api/documents/{document['id']}/typing", json=typing))
    create(
        client,
        f"/api/documents/{document['id']}/links",
        {"target_type": target[0], "target_id": target[1]},
    )
    return document


def _signed_pdf(text: str) -> bytes:
    """A real PDF with a free-text annotation: a page that needs a Finanzamt copy."""
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_annotation(0, FreeText(text=text, rect=(10, 10, 150, 40)))
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _pdf_text(data: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


def _exported_events(client, pid) -> list[dict]:
    events = ok(client.get("/api/events", params={"entity_id": pid}))
    return [e for e in events if e["type"] == "property.anlage_v_exported"]


# --- Field sheet -------------------------------------------------------------------------


def test_a_field_sheet_is_queued_written_and_downloaded(client_a, booked, deps, jobs, ws_a):
    pid = booked["pid"]
    state = _export(client_a, deps, jobs, ws_a, pid)
    export = state["export"]
    assert (export["property_id"], export["tax_year"], export["kind"]) == (pid, 2026, "field_sheet")
    assert (export["form_status"], export["form_year"]) == ("provisional", 2025)
    assert export["created_by"] is not None
    data = _download(client_a, state)
    assert data.startswith(b"%PDF")
    assert hashlib.sha256(data).hexdigest() == export["file_sha256"]
    assert len(data) == export["size_bytes"]
    assert "ELSTER-Ausfüllhilfe Anlage V 2026" in _pdf_text(data)

    history = f"/api/properties/{pid}/anlage-v/exports"
    listed = ok(client_a.get(history, params={"tax_year": 2026}))
    assert [e["id"] for e in listed] == [export["id"]]
    assert ok(client_a.get(history, params={"tax_year": 2025})) == []

    (event,) = _exported_events(client_a, pid)
    payload = event["payload"]
    assert payload["tax_export_id"] == export["id"]
    assert payload["file_sha256"] == export["file_sha256"]
    assert payload["result"] == "-118.60" and payload["document_count"] is None


def test_the_export_stores_the_draft_it_was_built_from(client_a, booked, deps, jobs, ws_a, dbname):
    """D59: the stored draft equals the draft computed when the export was written."""
    pid = booked["pid"]
    state = _export(client_a, deps, jobs, ws_a, pid)
    current = ok(client_a.get(f"/api/properties/{pid}/anlage-v", params={"tax_year": 2026}))
    with _admin(dbname) as conn:
        (stored,) = conn.execute(
            "SELECT draft FROM tax_export WHERE id = %s", (state["id"],)
        ).fetchone()
    assert stored["totals"] == current["totals"]
    assert stored["rule_set"] == current["rule_set"]
    assert [line["key"] for line in stored["lines"]] == [line["key"] for line in current["lines"]]


def test_a_second_run_writes_nothing(client_a, booked, deps, jobs, ws_a):
    pid = booked["pid"]
    state = _export(client_a, deps, jobs, ws_a, pid)
    _, args, _ = _job_of(jobs, ws_a, state["id"])
    assert _run_job(deps, args) == "exists"
    assert len(ok(client_a.get(f"/api/properties/{pid}/anlage-v/exports"))) == 1
    assert len(_exported_events(client_a, pid)) == 1


def test_the_sheet_and_the_csvs_carry_no_name_iban_or_purpose_text(
    client_a, booked, deps, jobs, ws_a
):
    pid = booked["pid"]
    create(client_a, "/api/owners", {"full_name": "Luis Rivas"})
    _typed(
        client_a,
        b"%PDF-1.7 Mietvertrag",
        type_="contract",
        subtype="lease",
        title=f"Mietvertrag {TENANT_NAME}",
        target=("tenancy", booked["tenancy_id"]),
    )
    sheet = _download(client_a, _export(client_a, deps, jobs, ws_a, pid))
    archived = _download(client_a, _export(client_a, deps, jobs, ws_a, pid, "archive"))
    archive = zipfile.ZipFile(io.BytesIO(archived))
    texts = {
        "sheet": _pdf_text(sheet),
        "ausfuellhilfe.pdf": _pdf_text(archive.read("ausfuellhilfe.pdf")),
        "anlage-v-2026.csv": archive.read("anlage-v-2026.csv").decode("utf-8"),
        "zahlungen.csv": archive.read("zahlungen.csv").decode("utf-8"),
        "zahlungsnachweise.pdf": _pdf_text(archive.read("zahlungsnachweise.pdf")),
    }
    secrets = (
        TENANT_NAME,
        "Luis Rivas",
        "Partei A",  # the counterparties' names, purpose texts and IBANs (camt fixture)
        "Partei B",
        "Verwendungszweck",
        "ÜBERWEISUNG",
        SAMPLE_IBAN,
        COUNTERPARTY_IBAN,
        "DE86100000000000001002",
    )
    for where, content in texts.items():
        for secret in secrets:
            assert secret not in content, (where, secret)
    rows = list(csv.reader(io.StringIO(texts["zahlungen.csv"].lstrip("﻿")), delimiter=";"))
    assert {"-360,18", "241,58"} <= {r[1] for r in rows[1:]}
    assert all(r[-1] == "" or "*" in r[-1] for r in rows[1:])  # a masked IBAN at most
    # The originals are the documents themselves: the lease is in the archive by its name.
    assert any(TENANT_NAME in n for n in archive.namelist() if n.startswith("dokumente/"))


# --- Archive -----------------------------------------------------------------------------


def test_the_archive_holds_every_linked_original(client_a, booked, deps, jobs, ws_a):
    pid = booked["pid"]
    contract_bytes = _signed_pdf("unterschrieben")
    _typed(
        client_a,
        contract_bytes,
        type_="contract",
        subtype="purchase_contract",
        title="Kaufvertrag Lindenstraße",
        target=("property", pid),
    )
    lease_bytes = b"%PDF-1.7 Mietvertrag Archiv"
    _typed(
        client_a,
        lease_bytes,
        type_="contract",
        subtype="lease",
        title="Mietvertrag WE 3",
        target=("tenancy", booked["tenancy_id"]),
    )
    state = _export(client_a, deps, jobs, ws_a, pid, "archive")
    data = _download(client_a, state)
    assert state["export"]["kind"] == "archive"
    assert hashlib.sha256(data).hexdigest() == state["export"]["file_sha256"]
    archive = zipfile.ZipFile(io.BytesIO(data))
    assert archive.testzip() is None
    names = archive.namelist()
    assert names[:5] == [
        "ausfuellhilfe.pdf",
        "anlage-v-2026.csv",
        "zahlungen.csv",
        "zahlungsnachweise.pdf",
        "README.txt",
    ]
    originals = {n: archive.read(n) for n in names if n.startswith("dokumente/")}
    assert contract_bytes in originals.values() and lease_bytes in originals.values()
    (contract_path,) = [n for n, d in originals.items() if d == contract_bytes]
    copies = archive.read("README.txt").decode("utf-8").split("Kopie nötig", 1)[1]
    assert contract_path in copies  # its annotation needs a Finanzamt copy
    (event,) = _exported_events(client_a, pid)
    assert event["payload"]["document_count"] == len(originals)


def test_an_archive_over_the_limit_is_refused(client_a, booked, jobs, ws_a, monkeypatch):
    pid = booked["pid"]
    _typed(
        client_a,
        b"%PDF-1.7 Kaufvertrag",
        type_="contract",
        subtype="purchase_contract",
        title="Kaufvertrag",
        target=("property", pid),
    )
    # The limit set to the allowance for the sheet and CSVs: any original goes over it.
    monkeypatch.setattr(
        tax_export_service, "MAX_ARCHIVE_BYTES", tax_export_service._ARCHIVE_OVERHEAD
    )
    response = _request(client_a, pid, "archive")
    assert response.status_code == 422
    assert response.json()["code"] == "tax_export.too_large"
    assert jobs(ws_a) == []
    # The field sheet carries no originals and is not limited.
    assert _request(client_a, pid).status_code == 202


def _pdf_statement(client, dbname, ws, data: bytes, first: str, last: str) -> str:
    """A PDF statement of the rental account: the file uploaded as a document and its import
    row written directly (reading a PDF statement takes the model, which this test skips)."""
    document = ok(upload(client, data, filename="kontoauszug.pdf"), 201)
    with _admin(dbname) as conn:
        (account_id,) = conn.execute(
            "SELECT account_id FROM statement_import WHERE workspace_id = %s LIMIT 1", (ws,)
        ).fetchone()
        conn.execute(
            "INSERT INTO statement_import (workspace_id, account_id, document_id, format,"
            " first_booking_on, last_booking_on) VALUES (%s, %s, %s, 'pdf', %s, %s)",
            (ws, account_id, document["id"], first, last),
        )
    return document["id"]


# Invented statement bytes, big enough that the limit below can hold the Beleg but not them.
STATEMENT = b"%PDF-1.7 Kontoauszug Beispielbank\n" + bytes(range(256)) * 800


def test_the_archive_carries_payment_proofs_and_the_bank_statements(
    client_a, booked, deps, jobs, ws_a, dbname
):
    pid = booked["pid"]
    _pdf_statement(client_a, dbname, ws_a, STATEMENT, "2026-04-01", "2026-04-30")
    _pdf_statement(client_a, dbname, ws_a, b"%PDF-1.7 Auszug 2025", "2025-11-01", "2025-11-30")
    archive = zipfile.ZipFile(
        io.BytesIO(_download(client_a, _export(client_a, deps, jobs, ws_a, pid, "archive")))
    )
    statements = [n for n in archive.namelist() if n.startswith("kontoauszuege/")]
    assert statements == ["kontoauszuege/01 Kontoauszug 2026-04-01 bis 2026-04-30.pdf"]
    assert archive.read(statements[0]) == STATEMENT  # untouched; the 2025 one is not the year's
    proofs = _pdf_text(archive.read("zahlungsnachweise.pdf"))
    flat = " ".join(proofs.split())
    assert "Zahlungsnachweise 2026" in flat
    assert "-360,18 €" in flat and "241,58 €" in flat
    assert "Sparkasse, IBAN DE50 **** 0001" in flat  # the account, masked as stored
    camt_sha = hashlib.sha256(ZIP).hexdigest()
    assert camt_sha in proofs.replace("\n", "")  # the bank file each line came from
    assert "camt-XML der Bank" in flat
    readme = archive.read("README.txt").decode("utf-8")
    assert "kontoauszuege/  1 Kontoauszüge der Bank, unverändert" in readme
    assert hashlib.sha256(STATEMENT).hexdigest() in readme


def test_the_limit_leaves_out_the_bank_statements_before_any_beleg(
    client_a, booked, deps, jobs, ws_a, dbname, monkeypatch
):
    pid = booked["pid"]
    contract = b"%PDF-1.7 Kaufvertrag Grenze"
    _typed(
        client_a,
        contract,
        type_="contract",
        subtype="purchase_contract",
        title="Kaufvertrag",
        target=("property", pid),
    )
    _pdf_statement(client_a, dbname, ws_a, STATEMENT, "2026-04-01", "2026-04-30")
    limit = tax_export_service._ARCHIVE_OVERHEAD + len(contract) + len(STATEMENT) // 2
    monkeypatch.setattr(tax_export_service, "MAX_ARCHIVE_BYTES", limit)
    archive = zipfile.ZipFile(
        io.BytesIO(_download(client_a, _export(client_a, deps, jobs, ws_a, pid, "archive")))
    )
    names = archive.namelist()
    assert contract in [archive.read(n) for n in names if n.startswith("dokumente/")]
    assert not any(n.startswith("kontoauszuege/") for n in names)
    readme = archive.read("README.txt").decode("utf-8")
    assert "1 Kontoauszug/Kontoauszüge der Bank nicht enthalten" in readme


def test_a_statement_that_overflows_the_rendered_archive_is_dropped(
    client_a, booked, deps, jobs, ws_a, dbname, monkeypatch
):
    """The backstop: when the finished ZIP is still over the limit, the statements go first
    and the archive is rendered again without them."""
    pid = booked["pid"]
    _pdf_statement(client_a, dbname, ws_a, STATEMENT, "2026-04-01", "2026-04-30")
    whole = _download(client_a, _export(client_a, deps, jobs, ws_a, pid, "archive"))
    assert STATEMENT in whole
    monkeypatch.setattr(tax_export_service, "_ARCHIVE_OVERHEAD", 0)
    monkeypatch.setattr(tax_export_service, "_fitting", lambda files, budget: files)
    monkeypatch.setattr(tax_export_service, "MAX_ARCHIVE_BYTES", len(whole) - len(STATEMENT) // 2)
    archive = zipfile.ZipFile(
        io.BytesIO(_download(client_a, _export(client_a, deps, jobs, ws_a, pid, "archive")))
    )
    assert not any(n.startswith("kontoauszuege/") for n in archive.namelist())
    assert "nicht enthalten: das Archiv wäre größer" in archive.read("README.txt").decode()


def test_a_job_that_outgrew_the_limit_reads_as_too_large(
    client_a, booked, deps, jobs, ws_a, set_job_status, monkeypatch
):
    pid = booked["pid"]
    export_id = ok(_request(client_a, pid, "archive"), 202)["export_id"]
    job_id, args, _ = _job_of(jobs, ws_a, export_id)
    monkeypatch.setattr(tax_export_service, "MAX_ARCHIVE_BYTES", 100)
    assert _run_job(deps, args) == "too_large"
    set_job_status(job_id, "succeeded")
    state = ok(client_a.get(f"/api/tax-exports/{export_id}"))
    assert (state["status"], state["error_code"]) == ("failed", "tax_export.too_large")
    assert ok(client_a.get(f"/api/properties/{pid}/anlage-v/exports")) == []


def test_a_failed_job_reads_as_failed(client_a, booked, jobs, ws_a, set_job_status):
    export_id = ok(_request(client_a, booked["pid"]), 202)["export_id"]
    job_id, _, _ = _job_of(jobs, ws_a, export_id)
    set_job_status(job_id, "failed")
    state = ok(client_a.get(f"/api/tax-exports/{export_id}"))
    assert (state["status"], state["error_code"]) == ("failed", "tax_export.failed")
    assert (state["kind"], state["tax_year"], state["download_url"]) == ("field_sheet", 2026, None)


# --- Errors and isolation -------------------------------------------------------------


def test_the_draft_errors_apply(client_a, booked, jobs, ws_a):
    pid = booked["pid"]
    for year, code in ((2023, "tax.year_not_available"), (2024, "tax.no_rule_set")):
        response = _request(client_a, pid, tax_year=year)
        assert response.status_code == 422
        assert response.json()["code"] == code
    assert _request(client_a, pid, kind="other").status_code == 422
    assert _request(client_a, str(uuid4())).status_code == 404
    assert jobs(ws_a) == []


def test_another_workspace_sees_nothing(client_a, client_b, booked, deps, jobs, ws_a):
    pid = booked["pid"]
    ready = _export(client_a, deps, jobs, ws_a, pid)
    queued = ok(_request(client_a, pid), 202)["export_id"]
    assert _request(client_b, pid).status_code == 404
    assert client_b.get(f"/api/properties/{pid}/anlage-v/exports").status_code == 404
    for export_id in (ready["id"], queued, str(uuid4())):
        response = client_b.get(f"/api/tax-exports/{export_id}")
        assert response.status_code == 404
        assert response.json()["error"] == "not_found"
    # A's signed URL opens nothing in B's session: the signature names A's workspace.
    assert client_b.get(ready["download_url"]).status_code == 404


def test_a_wrong_or_expired_signature_is_404(client_a, booked, deps, jobs, ws_a):
    state = _export(client_a, deps, jobs, ws_a, booked["pid"])
    url = urlsplit(state["download_url"])
    query = parse_qs(url.query)
    expires, signature = int(query["expires"][0]), query["signature"][0]
    wrong = {"expires": expires, "signature": "0" * len(signature)}
    assert client_a.get(url.path, params=wrong).status_code == 404
    past = int(datetime.now(UTC).timestamp()) - 10
    expired = {"expires": past, "signature": signature}
    assert client_a.get(url.path, params=expired).status_code == 404
    assert client_a.get(url.path).status_code == 422  # both parameters are required
