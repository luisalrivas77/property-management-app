"""The Anlage N field sheet over HTTP, and the yearly archive's Anlage N pages
(docs/phases/phase-4b.md D104, D110): POST /owners/{id}/anlage-n/exports queues the job, the
job (run here directly, as the worker would) writes the row, file and owner event, GET
/tax-exports/{id} serves it like an Anlage V export. Also: a wrong kind is refused on either
route, another workspace's owner is 404, a second run writes nothing, and the archive gains
each owner's sheet and the Gesamt page, and still builds when the estimate cannot."""

import io
import zipfile
from uuid import UUID, uuid4

import pytest
from api_support import TOKEN_A, create, ok
from db.db_support import _admin
from pypdf import PdfReader
from test_tax import sessions  # noqa: F401  (fixture by name)
from test_tax_exports import booked, deps, jobs  # noqa: F401  (fixtures by name)

from hausbuch.jobs.tax_export import run_tax_export
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.tax import TaxExportKind
from hausbuch.services import tax_export as tax_export_service

pytestmark = pytest.mark.db


@pytest.fixture
def ws_a(sessions) -> UUID:  # noqa: F811
    return sessions[TOKEN_A].workspace_id


def _owner(client, name: str) -> dict:
    return create(client, "/api/owners", {"full_name": name})


def _pay(client, owner_id: str, amount: str) -> None:
    create(
        client,
        "/api/income-inputs",
        {
            "owner_id": owner_id,
            "kind": "expected_gross_pay",
            "tax_year": 2026,
            "value": {"amount": amount},
        },
    )


@pytest.fixture
def owners(client_a) -> dict:
    """Owner A with expected pay, owner B without any pay (no Anlage N for B)."""
    a = _owner(client_a, "Person A")
    b = _owner(client_a, "Person B")
    _pay(client_a, a["id"], "60000.00")
    return {"a": a["id"], "b": b["id"]}


def _request(client, owner_id, kind="anlage_n_sheet", tax_year=2026):
    return client.post(
        f"/api/owners/{owner_id}/anlage-n/exports", json={"tax_year": tax_year, "kind": kind}
    )


def _run_job(deps, args: dict) -> str:  # noqa: F811
    return run_tax_export(
        deps,
        workspace_id=UUID(args["workspace_id"]),
        export_id=UUID(args["export_id"]),
        property_id=UUID(args["property_id"]) if args["property_id"] else None,
        owner_id=UUID(args["owner_id"]) if args.get("owner_id") else None,
        tax_year=int(args["tax_year"]),
        kind=TaxExportKind(args["kind"]),
        created_by=UUID(args["created_by"]) if args["created_by"] else None,
        attempt=0,
    )


def _job_args(jobs, ws, export_id: str) -> dict:  # noqa: F811
    (args,) = [j[1] for j in jobs(ws) if j[1]["export_id"] == export_id]
    return args


def _pdf_text(data: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


def _download(client, state) -> bytes:
    response = client.get(state["download_url"])
    assert response.status_code == 200, response.text
    return response.content


def test_an_owners_sheet_is_queued_written_and_downloaded(
    client_a,
    owners,
    deps,  # noqa: F811
    jobs,  # noqa: F811
    ws_a,
    dbname,
):
    export_id = ok(_request(client_a, owners["a"]), 202)["export_id"]
    state = ok(client_a.get(f"/api/tax-exports/{export_id}"))
    assert (state["status"], state["kind"], state["owner_id"]) == (
        "queued",
        "anlage_n_sheet",
        owners["a"],
    )
    assert state["property_id"] is None
    args = _job_args(jobs, ws_a, export_id)
    assert (args["owner_id"], args["property_id"]) == (owners["a"], "")
    assert _run_job(deps, args) == "written"
    assert _run_job(deps, args) == "exists"  # a second run writes nothing

    state = ok(client_a.get(f"/api/tax-exports/{export_id}"))
    assert state["status"] == "ready"
    export = state["export"]
    assert (export["owner_id"], export["property_id"], export["kind"]) == (
        owners["a"],
        None,
        "anlage_n_sheet",
    )
    current = ok(client_a.get(f"/api/owners/{owners['a']}/anlage-n", params={"tax_year": 2026}))
    with _admin(dbname) as conn:
        (stored,) = conn.execute(
            "SELECT draft FROM tax_export WHERE id = %s", (export_id,)
        ).fetchone()
    assert stored == current  # D59: the draft the sheet was built from
    response = client_a.get(state["download_url"])
    assert response.headers["content-type"] == "application/pdf"
    assert "Anlage-N_2026_Person A.pdf" in response.headers["content-disposition"]
    text = _pdf_text(response.content)
    assert "Anlage N 2026" in text and "Person A" in text
    assert "Von Hand in ELSTER" in text
    assert "Jahresfragen offen" in text  # nothing answered yet
    assert "Keine Steuerberatung" in text or "keine Steuerberatung" in text

    listed = ok(client_a.get(f"/api/owners/{owners['a']}/anlage-n/exports"))
    assert [e["id"] for e in listed] == [export_id]
    assert ok(client_a.get(f"/api/owners/{owners['b']}/anlage-n/exports")) == []
    events = ok(client_a.get("/api/events", params={"entity_id": owners["a"]}))
    (event,) = [e for e in events if e["type"] == "owner.anlage_n_exported"]
    assert event["payload"]["tax_export_id"] == export_id
    assert event["payload"]["result"] == current["result"]


def test_the_wrong_kind_is_refused_on_either_route(client_a, booked, owners, jobs, ws_a):  # noqa: F811
    for kind in ("field_sheet", "archive"):
        response = _request(client_a, owners["a"], kind=kind)
        assert (response.status_code, response.json()["code"]) == (422, "tax_export.kind_invalid")
    response = client_a.post(
        f"/api/properties/{booked['pid']}/anlage-v/exports",
        json={"tax_year": 2026, "kind": "anlage_n_sheet"},
    )
    assert (response.status_code, response.json()["code"]) == (422, "tax_export.kind_invalid")
    assert jobs(ws_a) == []


def test_the_drafts_refusals_apply(client_a, owners, jobs, ws_a):  # noqa: F811
    response = _request(client_a, owners["b"])  # no pay
    assert (response.status_code, response.json()["code"]) == (422, "anlage_n.no_pay")
    response = _request(client_a, owners["a"], tax_year=2025)
    assert (response.status_code, response.json()["code"]) == (422, "tax.no_rule_set")
    assert _request(client_a, str(uuid4())).status_code == 404
    assert client_a.get(f"/api/owners/{uuid4()}/anlage-n/exports").status_code == 404
    assert jobs(ws_a) == []


def test_another_workspace_sees_nothing(client_a, client_b, owners, deps, jobs, ws_a):  # noqa: F811
    export_id = ok(_request(client_a, owners["a"]), 202)["export_id"]
    _run_job(deps, _job_args(jobs, ws_a, export_id))
    assert _request(client_b, owners["a"]).status_code == 404
    assert client_b.get(f"/api/owners/{owners['a']}/anlage-n/exports").status_code == 404
    assert client_b.get(f"/api/tax-exports/{export_id}").status_code == 404


def _archive(client, deps, jobs, ws, pid) -> zipfile.ZipFile:  # noqa: F811
    response = client.post(
        f"/api/properties/{pid}/anlage-v/exports", json={"tax_year": 2026, "kind": "archive"}
    )
    export_id = ok(response, 202)["export_id"]
    assert _run_job(deps, _job_args(jobs, ws, export_id)) == "written"
    state = ok(client.get(f"/api/tax-exports/{export_id}"))
    return zipfile.ZipFile(io.BytesIO(_download(client, state)))


def test_the_archive_gains_the_owners_sheets_and_the_gesamt_page(
    client_a,
    booked,  # noqa: F811
    owners,
    deps,  # noqa: F811
    jobs,  # noqa: F811
    ws_a,
):
    archive = _archive(client_a, deps, jobs, ws_a, booked["pid"])
    assert archive.testzip() is None
    names = archive.namelist()
    assert names[:4] == ["ausfuellhilfe.pdf", "anlage-v-2026.csv", "zahlungen.csv", "README.txt"]
    sheet = "einkommensteuer/Anlage-N_2026_Person A.pdf"
    total = "einkommensteuer/Gesamt_2026_Schaetzung.pdf"
    assert sheet in names and total in names
    assert not [n for n in names if "Person B" in n]  # B has no pay, so no sheet
    assert "Anlage N 2026" in _pdf_text(archive.read(sheet))
    assert "Schätzung" in _pdf_text(archive.read(total))
    readme = archive.read("README.txt").decode("utf-8")
    assert readme.index("Jahresfragen offen") < readme.index("Immobilie:")  # the banner
    assert "Anlage N Person B: nicht enthalten (kein Arbeitslohn erfasst)." in readme
    assert sheet in readme and total in readme  # listed with their SHA-256
    # The Anlage V sheet and the CSVs still name no owner.
    for name in ("anlage-v-2026.csv", "zahlungen.csv"):
        assert "Person" not in archive.read(name).decode("utf-8")
    assert "Person A" not in _pdf_text(archive.read("ausfuellhilfe.pdf"))


def test_two_owners_with_one_name_keep_both_sheets_in_the_archive(
    client_a,
    booked,  # noqa: F811
    deps,  # noqa: F811
    jobs,  # noqa: F811
    ws_a,
):
    for _ in range(2):
        owner = _owner(client_a, "Person A")
        _pay(client_a, owner["id"], "60000.00")
    archive = _archive(client_a, deps, jobs, ws_a, booked["pid"])
    assert archive.testzip() is None
    names = archive.namelist()
    assert len(set(names)) == len(names)
    sheets = [n for n in names if n.startswith("einkommensteuer/Anlage-N_2026_")]
    assert sheets == [
        "einkommensteuer/Anlage-N_2026_Person A.pdf",
        "einkommensteuer/Anlage-N_2026_Person A (2).pdf",
    ]
    readme = archive.read("README.txt").decode("utf-8")
    assert all(sheet in readme for sheet in sheets)


def test_the_archive_builds_when_the_estimate_cannot(
    client_a,
    booked,  # noqa: F811
    owners,
    deps,  # noqa: F811
    jobs,  # noqa: F811
    ws_a,
    monkeypatch,
):
    def refuse(*_args, **_kwargs):
        raise RuleError("tax.no_rule_set", "no tariff")

    monkeypatch.setattr(tax_export_service, "household_return_estimate", refuse)
    archive = _archive(client_a, deps, jobs, ws_a, booked["pid"])
    names = archive.namelist()
    assert "einkommensteuer/Anlage-N_2026_Person A.pdf" in names
    assert "einkommensteuer/Gesamt_2026_Schaetzung.pdf" not in names
    readme = archive.read("README.txt").decode("utf-8")
    assert "Gesamtschätzung 2026: nicht enthalten (kein Regelwerk für dieses Jahr)." in readme


def test_an_archive_without_owners_names_no_income_tax_pages(
    client_a,
    booked,  # noqa: F811
    deps,  # noqa: F811
    jobs,  # noqa: F811
    ws_a,
):
    archive = _archive(client_a, deps, jobs, ws_a, booked["pid"])
    names = archive.namelist()
    assert not [n for n in names if n.startswith("einkommensteuer/Anlage-N")]
