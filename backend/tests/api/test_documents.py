"""Documents over HTTP (§4.7): upload and dedup, 413 and 415, typing, links, the original
and its headers, retention."""

import hashlib
from uuid import uuid4

import pytest
from api_support import (
    FakeResolver,
    client_for,
    create,
    flat,
    make_settings,
    new_property,
    ok,
    upload,
)

from hausbuch.api.app import create_app
from hausbuch.api.commands import Commands
from hausbuch.api.limits import MULTIPART_OVERHEAD
from hausbuch.api.routes.documents import content_disposition
from hausbuch.services.documents import upload_document

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
        client_a.put(f"{base}/typing", json={"typing": typing, "fields": {"reference": "MV-1"}})
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


def test_documents_of_unknown_targets_are_404(client_a):
    for target in ("tenants", "tenancies", "loans"):
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
