"""Documents over HTTP (§4.7): upload and dedup, 413 and 415, typing, links, the original
and its headers, retention."""

import hashlib
from uuid import UUID, uuid4

import pytest
from api_support import (
    FakeResolver,
    client_for,
    create,
    flat,
    loan_body,
    make_settings,
    new_property,
    new_unit,
    ok,
    upload,
)
from sqlalchemy import insert

from hausbuch.api.app import create_app
from hausbuch.api.commands import Commands
from hausbuch.api.limits import MULTIPART_OVERHEAD
from hausbuch.api.routes.documents import content_disposition
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import ai_call as ai_call_table
from hausbuch.schemas.enums import ActorType
from hausbuch.schemas.extraction import InvoiceExtraction
from hausbuch.services.context import Actor, new_command
from hausbuch.services.documents import record_ai_extraction, upload_document

pytestmark = pytest.mark.db

PDF = b"%PDF-1.7 Kaufvertrag Lindenstrasse"


def test_upload_and_dedup(client_a):
    response = upload(client_a, PDF)
    document = ok(response, 201)
    assert document["file_sha256"] == hashlib.sha256(PDF).hexdigest()
    assert (document["source"], document["original_filename"]) == ("upload", "kaufvertrag.pdf")
    assert document["typing"] is None

    again = upload(client_a, PDF, filename="copy.pdf", source="photo")
    assert (again.status_code, again.json()) == (200, document)  # the same document (D9)
    assert ok(client_a.get("/api/documents")) == [document]
    [uploaded] = ok(client_a.get(f"/api/events?entity_id={document['id']}"))
    assert uploaded["payload"]["media_type"] == "application/pdf"


def test_the_declared_type_is_normalised_and_checked(client_a):
    document = ok(upload(client_a, b"a;b\n1;2\n", "Text/CSV; charset=utf-8", "konto.csv"), 201)
    [uploaded] = ok(client_a.get(f"/api/events?entity_id={document['id']}"))
    assert uploaded["payload"]["media_type"] == "text/csv"

    for media_type in ("text/html", "image/svg+xml", "application/octet-stream"):
        response = upload(client_a, b"<svg onload=alert(1)>", media_type, "x")
        assert response.status_code == 415, media_type
        assert response.json()["error"] == "unsupported_media_type"
    response = upload(client_a, b"", "application/pdf")
    assert (response.status_code, response.json()["error"]) == (422, "invalid")  # empty


def test_uploads_over_the_limit_are_413(app_engine, sessions, tmp_path, blob_store):
    settings = make_settings(tmp_path, max_upload_bytes=1024)
    app = create_app(settings, app_engine, blob_store, FakeResolver(sessions))
    token = next(iter(sessions))
    client = client_for(app, token)

    response = upload(client, b"x" * 1025)  # the file part itself is over the limit
    assert (response.status_code, response.json()["error"]) == (413, "payload_too_large")
    assert ok(upload(client, b"x" * 1024), 201)

    huge = b"x" * (1024 + MULTIPART_OVERHEAD + 1)  # refused on Content-Length, unread
    response = upload(client, huge)
    assert (response.status_code, response.json()["error"]) == (413, "payload_too_large")

    def chunks():  # no Content-Length: counted while streaming
        for _ in range(100):
            yield b"y" * 1024

    response = client.post(
        "/api/documents",
        content=chunks(),
        headers={"Content-Type": "multipart/form-data; boundary=xyz"},
    )
    assert (response.status_code, response.json()["error"]) == (413, "payload_too_large")

    response = client.post("/api/owners", content=b"{" + b" " * (1024 * 1024) + b"}")
    assert response.status_code == 413  # JSON bodies are capped at 1 MiB


def test_typing_links_detail_and_retention(client_a):
    setup = flat(client_a)
    document = ok(upload(client_a, PDF), 201)
    base = f"/api/documents/{document['id']}"

    typing = {
        "type": "contract",
        "subtype": "lease",
        "title": "Mietvertrag",
        "document_date": "2024-06-10",
    }
    typed = ok(
        client_a.put(
            f"{base}/typing",
            json={"typing": typing, "fields": {"reference": "MV-1"}, "shown_version": 0},
        )
    )
    assert typed["typing"]["subtype"] == "lease"

    link = create(
        client_a, f"{base}/links", {"target_type": "tenancy", "target_id": setup["tenancy"]["id"]}
    )
    response = client_a.post(
        f"{base}/links", json={"target_type": "tenancy", "target_id": setup["tenancy"]["id"]}
    )
    assert (response.status_code, response.json()["error"]) == (409, "conflict")
    response = client_a.post(
        f"{base}/links", json={"target_type": "loan", "target_id": str(uuid4())}
    )
    assert (response.status_code, response.json()["entity_type"]) == (404, "loan")
    assert ok(client_a.get(f"{base}/links")) == [link]

    detail = ok(client_a.get(base))
    assert detail["document"] == typed
    assert detail["links"] == [link]
    assert detail["extraction"]["fields"]["reference"] == "MV-1"
    assert detail["retention"] == {
        "retention_class": "tenancy_records",
        "retain_until": None,
        "hold": "tenancy_active",
    }
    assert ok(client_a.get(f"{base}/retention")) == detail["retention"]
    assert (detail["media_type"], detail["opens_inline"]) == ("application/pdf", True)

    tenancy_id = setup["tenancy"]["id"]
    assert ok(client_a.get(f"/api/tenancies/{tenancy_id}/documents")) == [typed]
    assert ok(client_a.get(f"/api/documents?target_type=tenancy&target_id={tenancy_id}")) == [typed]
    assert ok(client_a.get("/api/documents?target_type=loan")) == []
    response = client_a.get(f"/api/documents?target_id={tenancy_id}")
    assert (response.status_code, response.json()["error"]) == (422, "invalid")

    response = client_a.delete(f"{base}/links/tenancy/{tenancy_id}")
    assert (response.status_code, response.content) == (204, b"")
    response = client_a.delete(f"{base}/links/tenancy/{tenancy_id}")
    assert (response.status_code, response.json()["error"]) == (404, "not_found")
    assert ok(client_a.get(f"/api/tenancies/{tenancy_id}/documents")) == []


def test_the_original_and_its_headers(client_a):
    document = ok(upload(client_a, PDF, filename="Übergabe (final).pdf"), 201)
    path = f"/api/documents/{document['id']}/original"

    response = client_a.get(path)
    assert response.status_code == 200
    assert response.content == PDF
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-disposition"] == (
        'attachment; filename="_bergabe _final_.pdf"; '
        "filename*=UTF-8''%C3%9Cbergabe%20%28final%29.pdf"
    )

    inline = client_a.get(f"{path}?disposition=inline")
    assert inline.headers["content-disposition"].startswith("inline; ")
    assert client_a.get(f"{path}?disposition=render").status_code == 422


def test_a_titled_document_downloads_under_its_date_and_title(client_a):
    """Only the download name changes; the bytes and the stored upload name stay."""
    document = ok(upload(client_a, PDF, filename="Scan_0042.PDF"), 201)
    base = f"/api/documents/{document['id']}"
    typing = {"type": "contract", "title": " Darlehensvertrag\nSparkasse / Nachtrag "}
    ok(client_a.put(f"{base}/typing", json={"typing": typing}))
    response = client_a.get(f"{base}/original")
    assert response.content == PDF
    assert response.headers["content-disposition"] == (
        'attachment; filename="Darlehensvertrag Sparkasse Nachtrag.pdf"; '
        "filename*=UTF-8''Darlehensvertrag%20Sparkasse%20Nachtrag.pdf"
    )

    typing = {"type": "contract", "title": "Übergabeprotokoll", "document_date": "2026-02-20"}
    ok(client_a.put(f"{base}/typing", json={"typing": typing, "shown_version": 0}))
    response = client_a.get(f"{base}/original?disposition=inline")
    assert response.headers["content-disposition"] == (
        'inline; filename="2026-02-20 _bergabeprotokoll.pdf"; '
        "filename*=UTF-8''2026-02-20%20%C3%9Cbergabeprotokoll.pdf"
    )
    assert ok(client_a.get(base))["document"]["original_filename"] == "Scan_0042.PDF"


def test_content_disposition_keeps_the_basename_and_quotes_nothing():
    assert content_disposition("attachment", 'C:\\scans\\03/2024 "final".pdf') == (
        "attachment; filename=\"2024 _final_.pdf\"; filename*=UTF-8''2024%20%22final%22.pdf"
    )
    assert content_disposition("inline", "/").startswith('inline; filename="original"')


def test_inline_is_refused_for_text_and_the_name_falls_back_to_the_hash(
    client_a, app_engine, auth_a, blob_store
):
    """A document with no original filename (the service allows it; a browser upload
    always has one)."""
    data = b"<script>alert(1)</script>"
    document = Commands(app_engine, auth_a, uuid4()).run(
        upload_document, blob_store, data, media_type="text/plain", source="email"
    )
    response = client_a.get(f"/api/documents/{document.id}/original?disposition=inline")
    assert response.content == data
    assert response.headers["content-type"] == "text/plain"  # verbatim, no charset added
    sha = hashlib.sha256(data).hexdigest()
    assert response.headers["content-disposition"] == (
        f"attachment; filename=\"{sha[:12]}.txt\"; filename*=UTF-8''{sha[:12]}.txt"
    )
    # The detail says so up front (§10.8 item 7), so the UI offers a download, not "open".
    detail = ok(client_a.get(f"/api/documents/{document.id}"))
    assert (detail["media_type"], detail["opens_inline"]) == ("text/plain", False)


def _link(client, document, target_type, target_id):
    body = {"target_type": target_type, "target_id": target_id}
    return create(client, f"/api/documents/{document['id']}/links", body)


def test_a_property_and_a_unit_roll_up_the_documents_of_their_children(client_a):
    setup = flat(client_a)
    prop, unit, tenancy = (setup[k]["id"] for k in ("property", "unit", "tenancy"))
    loan = create(client_a, "/api/loans", loan_body(prop))["id"]
    other_unit = new_unit(client_a, prop, label="WE 4")["id"]
    deed, lease, loan_contract, by_tenant, elsewhere = (
        ok(upload(client_a, f"%PDF-1.7 {name}".encode(), filename=f"{name}.pdf"), 201)
        for name in ("Kaufvertrag", "Mietvertrag", "Darlehensvertrag", "Ausweis", "WE4")
    )
    _link(client_a, deed, "property", prop)
    _link(client_a, lease, "tenancy", tenancy)
    _link(client_a, lease, "property", prop)
    _link(client_a, loan_contract, "loan", loan)
    _link(client_a, by_tenant, "tenant", setup["tenant"]["id"])  # never rolled up
    _link(client_a, elsewhere, "unit", other_unit)

    assert ok(client_a.get(f"/api/properties/{prop}/documents")) == [
        {"document": deed, "via": [{"target_type": "property", "target_id": prop}]},
        {
            "document": lease,
            "via": [
                {"target_type": "property", "target_id": prop},
                {"target_type": "tenancy", "target_id": tenancy},
            ],
        },
        {"document": loan_contract, "via": [{"target_type": "loan", "target_id": loan}]},
        {"document": elsewhere, "via": [{"target_type": "unit", "target_id": other_unit}]},
    ]
    assert ok(client_a.get(f"/api/units/{unit}/documents")) == [
        {"document": lease, "via": [{"target_type": "tenancy", "target_id": tenancy}]},
    ]
    assert ok(client_a.get(f"/api/units/{other_unit}/documents")) == [
        {"document": elsewhere, "via": [{"target_type": "unit", "target_id": other_unit}]},
    ]
    # The direct read is unchanged.
    query = f"/api/documents?target_type=property&target_id={prop}"
    assert ok(client_a.get(query)) == [deed, lease]


def test_documents_of_unknown_targets_are_404(client_a):
    for target in ("properties", "units", "tenants", "tenancies", "loans"):
        response = client_a.get(f"/api/{target}/{uuid4()}/documents")
        assert response.status_code == 404, target
    missing = uuid4()
    for sub in ("", "/links", "/original", "/retention"):
        response = client_a.get(f"/api/documents/{missing}{sub}")
        assert (response.status_code, response.json()["entity_type"]) == (404, "document"), sub
    response = client_a.put(f"/api/documents/{missing}/typing", json={"typing": {"type": "other"}})
    assert response.status_code == 404


def test_a_property_document_is_an_ownership_record(client_a):
    prop = new_property(client_a)
    document = ok(upload(client_a, PDF), 201)
    create(
        client_a,
        f"/api/documents/{document['id']}/links",
        {"target_type": "property", "target_id": prop["id"]},
    )
    ok(
        client_a.put(
            f"/api/documents/{document['id']}/typing",
            json={"typing": {"type": "contract", "subtype": "purchase_contract"}},
        )
    )
    retention = ok(client_a.get(f"/api/documents/{document['id']}/retention"))
    assert retention == {
        "retention_class": "ownership_records",
        "retain_until": None,
        "hold": "while_owned",
    }


def _record_reading(engine, ws, document_id: str) -> None:
    """What the document_read job records: an `ai` invoice reading tied to its ai_call."""
    cited = {"source": {"page": 1, "quote": "…"}, "checks": ["cited"], "confidence": "medium"}
    reading = InvoiceExtraction.model_validate(
        {
            "sender": cited | {"value": "Notar Dr. Beispiel"},
            "amount_total": cited | {"value": "4321.10"},
            "amount_net": cited | {"value": "3631.18"},
            "reference": cited | {"value": "UR 123/2024"},
        }
    )
    with workspace_transaction(engine, ws) as conn:
        ctx = new_command(conn, ws, Actor(ActorType.AI, "claude-test-model"))
        call_id = conn.execute(
            insert(ai_call_table)
            .values(
                workspace_id=ws,
                job="extract",
                document_id=UUID(document_id),
                model="claude-test-model",
                prompt_version="test-1",
                input_sha256=b"\x04" * 32,
            )
            .returning(ai_call_table.c.id)
        ).scalar_one()
        record_ai_extraction(ctx, UUID(document_id), reading, ai_call_id=call_id)


def test_the_detail_shows_the_hand_fields_of_an_ai_reading(client_a, app_engine, auth_a):
    document = ok(upload(client_a, b"%PDF-1.7 Notarrechnung D37"), 201)
    base = f"/api/documents/{document['id']}"
    detail = ok(client_a.get(base))
    assert detail["hand_fields"]["available"] == [
        "amount_total",
        "period_start",
        "period_end",
        "counterparty",
        "reference",
        "notes",
    ]
    assert set(detail["hand_fields"]["values"].values()) == {None}

    _record_reading(app_engine, auth_a.workspace_id, document["id"])
    detail = ok(client_a.get(base))
    assert detail["extraction"]["kind"] == "invoice"
    assert detail["hand_fields"] == {
        "values": {
            "amount_total": "4321.10",
            "period_start": None,
            "period_end": None,
            "counterparty": "Notar Dr. Beispiel",
            "reference": "UR 123/2024",
            "notes": None,
        },
        "available": ["amount_total", "counterparty", "reference", "notes"],
    }

    typing = {"type": "invoice", "title": "Notarrechnung"}
    shown = detail["hand_fields"]["values"]
    response = client_a.put(f"{base}/typing", json={"typing": typing, "fields": shown})
    assert (response.status_code, response.json()["error"]) == (422, "validation")  # no version
    body = {"typing": typing, "fields": shown, "shown_version": 1}
    ok(client_a.put(f"{base}/typing", json=body))
    assert ok(client_a.get(base))["extraction"] == detail["extraction"]  # no new version

    changed = shown | {"notes": "Beurkundung"}
    body = {"typing": typing, "fields": changed, "shown_version": 1}
    ok(client_a.put(f"{base}/typing", json=body))
    after = ok(client_a.get(base))
    assert (after["extraction"]["version"], after["extraction"]["kind"]) == (2, "invoice")
    assert (after["extraction"]["source"], after["extraction"]["ai_call_id"]) == ("manual", None)
    assert after["extraction"]["fields"]["subject"]["value"] == "Beurkundung"
    assert after["hand_fields"]["values"] == changed

    unplaced = changed | {"period_end": "2024-03-31"}
    response = client_a.put(
        f"{base}/typing", json={"typing": typing, "fields": unplaced, "shown_version": 2}
    )
    assert response.status_code == 422
    body = response.json()
    assert (body["error"], body["code"]) == ("rule_violation", "document.extraction_kind_mismatch")

    # Shown from version 1, but version 2 is current now (a re-read, a second tab).
    response = client_a.put(
        f"{base}/typing", json={"typing": typing, "fields": shown, "shown_version": 1}
    )
    assert response.status_code == 422
    body = response.json()
    assert (body["error"], body["code"]) == ("rule_violation", "document.extraction_stale")
    assert ok(client_a.get(base))["extraction"]["version"] == 2


def test_a_payment_proof_uploaded_for_its_bill_closes_the_to_do(client_a):
    """Phase 3 D143 over HTTP, the e2e case's flow: a Notar bill carried by a cost row and paid
    by no bank line is a to-do in its purchase year; the file uploaded with `proof_for` is typed
    as the bill's payment proof and linked to the bill and the property, and the to-do closes
    (without AI the proof is unread, so it counts as seen). The bill itself, an unknown bill and
    a document of another kind are refused."""
    prop = new_property(client_a, notarized_on="2026-03-10", purchase_date="2026-07-01")
    bill = ok(upload(client_a, b"%PDF-1.7 Notarrechnung D143", filename="notar.pdf"), 201)
    create(
        client_a,
        "/api/acquisition-costs",
        {
            "property_id": prop["id"],
            "kind": "notary",
            "amount": "1785.00",
            "document_id": bill["id"],
        },
    )

    def proof_warnings() -> list[dict]:
        path = f"/api/properties/{prop['id']}/anlage-v"
        draft = ok(client_a.get(path, params={"tax_year": 2026}))
        return [w for w in draft["warnings"] if w["code"] == "payment_proof.missing"]

    [warning] = proof_warnings()
    assert warning["params"] == {
        "document": bill["id"],
        "source": "acquisition_cost",
        "kind": "notary",
        "amount": "1785.00",
    }

    pdf = b"%PDF-1.7 Ueberweisung Notar D143"
    refused = upload(client_a, b"%PDF-1.7 Notarrechnung D143", proof_for=bill["id"])
    assert refused.status_code == 422  # the bill itself (its bytes are already the bill)
    assert upload(client_a, pdf, proof_for=str(uuid4())).status_code == 404
    other = ok(upload(client_a, b"%PDF-1.7 Kaufvertrag D143"), 201)
    ok(
        client_a.put(
            f"/api/documents/{other['id']}/typing",
            json={"typing": {"type": "contract", "subtype": "purchase_contract"}},
        )
    )
    assert upload(client_a, b"%PDF-1.7 Kaufvertrag D143", proof_for=bill["id"]).status_code == 422

    proof = ok(upload(client_a, pdf, filename="ueberweisung.pdf", proof_for=bill["id"]), 201)
    assert proof["typing"]["type"] == "other"
    assert proof["typing"]["subtype"] == "payment_proof"
    detail = ok(client_a.get(f"/api/documents/{proof['id']}"))
    assert sorted((link["target_type"], link["target_id"]) for link in detail["links"]) == sorted(
        [("document", bill["id"]), ("property", prop["id"])]
    )
    assert proof_warnings() == []


def test_a_stored_proof_is_linked_to_its_bill_from_the_to_do(client_a):
    """Phase 3 D149 over HTTP, „Vorhandenen Nachweis zuordnen“: a document already stored is
    linked to the bill as the upload from the to-do links it (typed as a payment proof, linked
    to the bill and the property), and the to-do closes. An unknown bill is 404, the bill
    itself, a document of another kind and an untyped document are 422 (review F3: an untyped
    file has no amount read, so it would prove any bill)."""
    prop = new_property(client_a, notarized_on="2026-03-10", purchase_date="2026-07-01")
    bill = ok(upload(client_a, b"%PDF-1.7 Notarrechnung D149", filename="notar.pdf"), 201)
    create(
        client_a,
        "/api/acquisition-costs",
        {
            "property_id": prop["id"],
            "kind": "notary",
            "amount": "1785.00",
            "document_id": bill["id"],
        },
    )
    proof = ok(upload(client_a, b"%PDF-1.7 Ueberweisung D149", filename="summary.pdf"), 201)

    def attach(document_id: str, bill_id: str):
        return client_a.post(
            f"/api/documents/{document_id}/payment-proof", json={"bill_id": bill_id}
        )

    assert attach(proof["id"], str(uuid4())).status_code == 404
    assert attach(bill["id"], bill["id"]).status_code == 422
    other = ok(upload(client_a, b"%PDF-1.7 Kaufvertrag D149"), 201)
    ok(
        client_a.put(
            f"/api/documents/{other['id']}/typing",
            json={"typing": {"type": "contract", "subtype": "purchase_contract"}},
        )
    )
    assert attach(other["id"], bill["id"]).status_code == 422
    assert attach(proof["id"], bill["id"]).status_code == 422  # still untyped
    assert ok(client_a.get(f"/api/documents/{proof['id']}"))["links"] == []
    ok(
        client_a.put(
            f"/api/documents/{proof['id']}/typing",
            json={"typing": {"type": "other", "subtype": "payment_proof"}},
        )
    )

    linked = ok(attach(proof["id"], bill["id"]))
    assert linked["typing"]["subtype"] == "payment_proof"
    detail = ok(client_a.get(f"/api/documents/{proof['id']}"))
    assert sorted((link["target_type"], link["target_id"]) for link in detail["links"]) == sorted(
        [("document", bill["id"]), ("property", prop["id"])]
    )
    draft = ok(client_a.get(f"/api/properties/{prop['id']}/anlage-v", params={"tax_year": 2026}))
    assert [w for w in draft["warnings"] if w["code"] == "payment_proof.missing"] == []


def test_a_document_nothing_depends_on_is_deleted(client_a, blob_store):
    """D193: the detail says it can go, DELETE answers 204, the file goes with it, and the
    same bytes can be uploaded again afterwards."""
    data = b"%PDF-1.7 falscher Depotauszug"
    document = ok(upload(client_a, data, filename="depot.pdf"), 201)
    detail = ok(client_a.get(f"/api/documents/{document['id']}"))
    assert (detail["deletable"], detail["delete_blocked_by"]) == (True, [])
    key = f"{document['workspace_id']}/sha256/{hashlib.sha256(data).hexdigest()}"
    assert blob_store.exists(key)

    assert client_a.delete(f"/api/documents/{document['id']}").status_code == 204

    assert client_a.get(f"/api/documents/{document['id']}").status_code == 404
    assert not blob_store.exists(key)
    assert document["id"] not in {d["id"] for d in ok(client_a.get("/api/documents"))}
    events = ok(
        client_a.get("/api/events", params={"entity_type": "document", "entity_id": document["id"]})
    )
    assert [e["type"] for e in events] == ["document.uploaded", "document.deleted"]
    assert events[-1]["payload"] == {"type": None, "subtype": None}
    again = ok(upload(client_a, data, filename="depot.pdf"), 201)
    assert again["id"] != document["id"]
    assert blob_store.exists(key)
    assert client_a.delete(f"/api/documents/{uuid4()}").status_code == 404


def test_a_linked_document_is_not_deleted(client_a):
    """D193: a link is a dependency; the detail and the 409 name it by code."""
    prop = new_property(client_a)
    document = ok(upload(client_a, b"%PDF-1.7 verknuepft"), 201)
    create(
        client_a,
        f"/api/documents/{document['id']}/links",
        {"target_type": "property", "target_id": prop["id"]},
    )
    detail = ok(client_a.get(f"/api/documents/{document['id']}"))
    assert (detail["deletable"], detail["delete_blocked_by"]) == (
        False,
        ["document.delete_blocked.link"],
    )

    body = ok(client_a.delete(f"/api/documents/{document['id']}"), 409)

    assert body["error"] == "conflict"
    assert (body["code"], body["codes"]) == (
        "document.delete_blocked.link",
        ["document.delete_blocked.link"],
    )
    assert client_a.get(f"/api/documents/{document['id']}").status_code == 200
