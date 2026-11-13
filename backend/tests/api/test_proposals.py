"""Proposals over HTTP (phase 2 spec §6.1, §6.5): upload with `ai`, poll, apply, reject,
re-run. The job is the worker chunk's: enqueue_document_read is monkeypatched, and the read
result is written through services.proposals.record_read_result as the job would."""

from uuid import UUID

import pytest
from api_support import (
    TOKEN_A,
    TOKEN_B,
    FakeResolver,
    auth_for,
    client_for,
    make_settings,
    new_property,
    ok,
    upload,
)
from db.db_support import _admin
from sqlalchemy import insert, update

from hausbuch.api.app import create_app
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import ai_call as ai_call_table
from hausbuch.db.tables import document as document_table
from hausbuch.jobs import enqueue
from hausbuch.schemas.ai import ClassifyOutput, ExtractedValue, SourceRef
from hausbuch.schemas.enums import ActorType, DocumentSubtype, DocumentType
from hausbuch.services import proposals
from hausbuch.services.context import Actor, new_command

pytestmark = pytest.mark.db

INVOICE = ClassifyOutput(
    type=DocumentType.INVOICE,
    subtype=DocumentSubtype.COST_BASIS_EVIDENCE,
    contains_tenant_data=False,
    language="de",
    confidence="high",
)


def _v(value) -> ExtractedValue:
    return ExtractedValue(
        value=value, source=SourceRef(page=1, quote="…"), checks=["cited"], confidence="high"
    )


INVOICE_VALUES = {
    "title": _v("Notarrechnung"),
    "document_date": _v("2024-03-20"),
    "sender": _v("Notar Dr. Beispiel"),
    "amount_total": _v("4321.10"),
    "reference": _v("UR 123/2024"),
}


@pytest.fixture
def sessions(fresh_workspaces, dbname):
    """Sessions whose users exist, so an apply's decided_by (FK to app_user) is real."""
    a, b = fresh_workspaces
    auths = {TOKEN_A: auth_for(a), TOKEN_B: auth_for(b)}
    with _admin(dbname) as conn:
        for auth in auths.values():
            conn.execute(
                "INSERT INTO app_user (id, workspace_id, email, display_name)"
                " VALUES (%s, %s, %s, 'Test')",
                (auth.user_id, auth.workspace_id, f"{auth.user_id}@example.test"),
            )
    return auths


@pytest.fixture
def enqueued(monkeypatch):
    calls: list[UUID] = []

    def fake(conn, *, workspace_id, document_id, proposal_id):
        calls.append(proposal_id)

    monkeypatch.setattr(enqueue, "enqueue_document_read", fake)
    return calls


def _read_by_job(
    engine, ws, proposal_id, document_id, classification=INVOICE, values=INVOICE_VALUES
):
    """What document_read does on success, as the AI actor, in its own transaction."""
    with workspace_transaction(engine, ws) as conn:
        ctx = new_command(conn, ws, Actor(ActorType.AI, "claude-test-model"))
        call_ids = [
            conn.execute(
                insert(ai_call_table)
                .values(
                    workspace_id=ws,
                    job=job,
                    document_id=document_id,
                    model="claude-test-model",
                    prompt_version="test-1",
                    input_sha256=b"\x02" * 32,
                )
                .returning(ai_call_table.c.id)
            ).scalar_one()
            for job in ("classify", "extract")
        ]
        return proposals.record_read_result(
            ctx,
            proposal_id,
            classification=classification,
            classify_call_id=call_ids[0],
            values=values,
            extract_call_id=call_ids[1],
        )


def _accept_all(proposal: dict) -> dict:
    return {
        "records": [
            {
                "key": r["key"],
                "values": {f: v["value"] for f, v in r["fields"].items() if v["value"] is not None},
            }
            for r in proposal["records"]
        ]
    }


# --- Upload ------------------------------------------------------------------------------


def test_upload_without_ai_is_the_phase_1_answer(client_a, enqueued):
    body = ok(upload(client_a), 201)
    assert "file_sha256" in body and "proposal_id" not in body
    assert enqueued == []
    assert ok(client_a.get(f"/api/documents/{body['id']}/proposals")) == []


def test_upload_with_ai_queues_a_proposal_to_poll(client_a, enqueued):
    body = ok(upload(client_a, ai="true"), 201)
    assert body["proposal_status"] == "queued"
    proposal_id = body["proposal_id"]
    assert enqueued == [UUID(proposal_id)]

    polled = ok(client_a.get(f"/api/proposals/{proposal_id}"))
    assert (polled["status"], polled["document_id"]) == ("queued", body["document"]["id"])
    listed = ok(client_a.get(f"/api/documents/{body['document']['id']}/proposals"))
    assert [p["id"] for p in listed] == [proposal_id]

    # The same bytes again: 200, the same proposal, no second read.
    again = ok(upload(client_a, ai="true"), 200)
    assert again["proposal_id"] == proposal_id
    assert enqueued == [UUID(proposal_id)]


def test_ai_defaults_to_the_setting(app_engine, sessions, tmp_path, blob_store, enqueued):
    settings = make_settings(tmp_path, ai_enabled=True, anthropic_api_key="sk-test")
    app = create_app(settings, app_engine, blob_store, FakeResolver(sessions), worker=None)
    client = client_for(app, TOKEN_A)

    assert ok(upload(client, b"%PDF one"), 201)["proposal_status"] == "queued"
    assert "proposal_id" not in ok(upload(client, b"%PDF two", ai="false"), 201)
    assert len(enqueued) == 1


def test_tenant_data_upload_is_blocked_without_a_job(client_a, enqueued):
    body = ok(upload(client_a, ai="true", contains_tenant_data="true"), 201)
    assert body["proposal_status"] == "blocked_tenant_data"
    polled = ok(client_a.get(f"/api/proposals/{body['proposal_id']}"))
    assert polled["error_code"] == "ai.blocked_tenant_data"
    assert polled["contains_tenant_data"] is True
    assert enqueued == []


# --- Decide ------------------------------------------------------------------------------


def test_apply_reject_and_rerun(client_a, client_b, app_engine, sessions, enqueued):
    ws = sessions[TOKEN_A].workspace_id
    prop = new_property(client_a)
    uploaded = ok(upload(client_a, b"%PDF notar", ai="true"), 201)
    document_id, proposal_id = uploaded["document"]["id"], uploaded["proposal_id"]

    # Not ready yet: 422 with the stable code.
    refused = client_a.post(f"/api/proposals/{proposal_id}/apply", json={"records": []})
    assert (refused.status_code, refused.json()["code"]) == (422, "proposal.not_ready")

    _read_by_job(app_engine, ws, UUID(proposal_id), UUID(document_id))
    ready = ok(client_a.get(f"/api/proposals/{proposal_id}"))
    assert ready["status"] == "ready"
    assert {r["key"] for r in ready["records"]} == {"typing", "cost:notary", "link:property"}

    # Another workspace sees nothing.
    assert client_b.get(f"/api/proposals/{proposal_id}").status_code == 404
    assert client_b.post(f"/api/proposals/{proposal_id}/reject").status_code == 404

    # A bad value: 422 whose loc starts with the record key; nothing written.
    bad = _accept_all(ready)
    for record in bad["records"]:
        if record["key"] == "cost:notary":
            record["values"]["amount"] = "-5"
    invalid = client_a.post(f"/api/proposals/{proposal_id}/apply", json=bad)
    assert invalid.status_code == 422, invalid.text
    assert [e["loc"][:2] for e in invalid.json()["detail"]] == [["cost:notary", "amount"]]
    assert '"-5"' not in invalid.text  # the value is never echoed (a random id may contain -5)
    costs = f"/api/properties/{prop['id']}/acquisition-costs"
    assert ok(client_a.get(costs)) == []

    applied = ok(client_a.post(f"/api/proposals/{proposal_id}/apply", json=_accept_all(ready)), 201)
    assert applied["proposal"]["status"] == "applied"
    by_key = {a["key"]: a for a in applied["applied"]}
    [cost] = ok(client_a.get(costs))
    assert by_key["cost:notary"]["entity_id"] == cost["id"]
    assert (cost["amount"], cost["document_id"]) == ("4321.10", document_id)

    decided = client_a.post(f"/api/proposals/{proposal_id}/reject")
    assert (decided.status_code, decided.json()["code"]) == (422, "proposal.already_decided")

    # Re-run: a new queued proposal; a re-run marked tenant data is blocked instead.
    rerun = ok(client_a.post(f"/api/documents/{document_id}/proposals"), 201)
    assert rerun["status"] == "queued" and UUID(rerun["id"]) in enqueued
    blocked = ok(
        client_a.post(
            f"/api/documents/{document_id}/proposals", json={"contains_tenant_data": True}
        ),
        201,
    )
    assert blocked["status"] == "blocked_tenant_data"
    assert (rerun["contains_tenant_data"], blocked["contains_tenant_data"]) == (False, True)
    # A re-run without a body keeps the flag of the latest proposal; it supersedes the
    # blocked one (D42 (2)), so the document never has two undecided proposals.
    kept = ok(client_a.post(f"/api/documents/{document_id}/proposals"), 201)
    assert (kept["status"], kept["contains_tenant_data"]) == ("blocked_tenant_data", True)
    statuses = [p["status"] for p in ok(client_a.get(f"/api/documents/{document_id}/proposals"))]
    assert statuses == ["blocked_tenant_data", "superseded", "superseded", "applied"]

    # Blocked before any read, it proposes nothing: there is nothing to decide.
    nothing = client_a.post(f"/api/proposals/{kept['id']}/reject")
    assert (nothing.status_code, nothing.json()["code"]) == (422, "proposal.not_ready")


def test_unknown_ids_are_404(client_a, client_b):
    missing = "00000000-0000-4000-8000-000000000000"
    assert client_a.get(f"/api/proposals/{missing}").status_code == 404
    assert client_a.post(f"/api/documents/{missing}/proposals").status_code == 404
    assert client_a.get(f"/api/documents/{missing}/proposals").status_code == 404
    doc = ok(upload(client_a, b"%PDF mine"), 201)
    assert client_b.get(f"/api/documents/{doc['id']}/proposals").status_code == 404


# --- Phase 6 D177: a depot document explains itself ----------------------------------------

TRADE = ClassifyOutput(
    type=DocumentType.LETTER,
    subtype=DocumentSubtype.TRADE_CONFIRMATION,
    contains_tenant_data=False,
    language="de",
    confidence="high",
)
# Invented: a fund, an ISIN and figures no real document holds.
TRADE_VALUES = {
    "isin": _v("IE00BEISPL01"),
    "security_name": _v("Beispiel Welt UCITS ETF"),
    "trade_date": _v("2026-05-14"),
    "side": _v("Kauf"),
    "units": _v("4.8"),
    "net_amount": _v("1234.56"),
    "fees": _v("1.00"),
}


def test_a_depot_document_and_its_card_explain_it_in_plain_words(client_a, app_engine, enqueued):
    from test_card_pdf import draw

    pdf = draw(["Quirin Privatbank AG\nWertpapierabrechnung\nKauf\nIE00BEISPL01ISIN"])
    body = ok(upload(client_a, pdf, ai="true"), 201)
    document_id, proposal_id = body["document"]["id"], body["proposal_id"]
    ws = UUID(body["document"]["workspace_id"])
    _read_by_job(app_engine, ws, UUID(proposal_id), UUID(document_id), TRADE, TRADE_VALUES)
    # The document job records the kind from the text layer (jobs.document_read
    # _note_depot_kind); this test reports the reading without running the job.
    with workspace_transaction(app_engine, ws) as conn:
        conn.execute(
            update(document_table)
            .where(document_table.c.id == UUID(document_id))
            .values(depot_kind="wertpapierabrechnung_kauf")
        )

    explanation = ok(client_a.get(f"/api/documents/{document_id}"))["explanation"]
    assert explanation["was"].startswith("Wertpapierabrechnung (Kauf): die Bestätigung")
    # The figures of the reading; no sentence names the account: the workspace has none.
    assert explanation["bedeutung"] == (
        "4,8 Anteile des Fonds Beispiel Welt UCITS ETF wurden gekauft. Handelstag: 14.05.2026. "
        "Bezahlt (Ausmachender Betrag): 1.234,56 €. Darin Gebühren: 1,00 €."
    )
    assert "„Übernehmen“" in explanation["aktion"]
    assert ok(client_a.get(f"/api/proposals/{proposal_id}"))["explanation"] == explanation
    [listed] = ok(client_a.get(f"/api/documents/{document_id}/proposals"))
    assert listed["explanation"] == explanation

    other = ok(upload(client_a, b"%PDF-1.7 Notarrechnung", ai="true"), 201)
    assert ok(client_a.get(f"/api/documents/{other['document']['id']}"))["explanation"] is None
    assert ok(client_a.get(f"/api/proposals/{other['proposal_id']}"))["explanation"] is None
