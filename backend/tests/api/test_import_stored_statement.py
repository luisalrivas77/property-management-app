"""POST /documents/{id}/import-statement, „Auf Konto importieren“ (phase 6 D176 (3), step 4),
and the offer on GET /documents/{id}. The service is covered in
tests/db/test_services_stored_statement.py; these check the route: status codes, the read
shapes and isolation. Invented statements only (tests/test_card_pdf.py)."""

from datetime import UTC, datetime

import pytest
from api_support import create, ok, upload
from test_card_pdf import CARD, case_bytes

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import proposal as proposal_table
from hausbuch.schemas.enums import ProposalStatus
from hausbuch.services.proposals import PROPOSAL_BANK_FILE_NOT_READ

pytestmark = pytest.mark.db

T0 = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
CARD_BYTES = case_bytes(next(c for c in CARD.cases if c.id == "plain_month"))


def _card_account(client, label="Kreditkarte"):
    base = CARD.bases["account"]
    return create(
        client,
        "/api/accounts",
        {
            "label": label,
            "bank": "Beispielbank",
            "kind": base["kind"],
            "provider": base["provider"],
            "opening_balance": str(base["opening_balance"]),
            "opening_balance_on": str(base["opening_balance_on"]),
        },
    )


def _refused_upload(client, app_engine, workspace_id):
    """The card statement uploaded through Dokumente, and the failed read the document job
    records for it (proposal.bank_file_not_read), written directly: the job is covered in
    tests/db."""
    document = ok(upload(client, CARD_BYTES, filename="scan.pdf"), 201)
    with workspace_transaction(app_engine, workspace_id) as conn:
        conn.execute(
            proposal_table.insert().values(
                workspace_id=workspace_id,
                document_id=document["id"],
                status=ProposalStatus.FAILED.value,
                error_code=PROPOSAL_BANK_FILE_NOT_READ,
                created_at=T0,
                updated_at=T0,
            )
        )
    return document


def test_the_offer_then_the_import_201_then_200(client_a, app_engine, auth_a):
    document = _refused_upload(client_a, app_engine, auth_a.workspace_id)
    path = f"/api/documents/{document['id']}"
    detail = ok(client_a.get(path))
    assert detail["statement_imports"] == []
    assert detail["statement_offer"] == {
        "format": "card_pdf",
        "account_kind": "credit_card",
        "candidates": [],
        "suggested_account_id": None,
    }
    inbox = [i for i in ok(client_a.get("/api/inbox")) if i["kind"] == "read_failed"]
    assert [i["error_code"] for i in inbox] == [PROPOSAL_BANK_FILE_NOT_READ]

    account = _card_account(client_a)
    offer = ok(client_a.get(path))["statement_offer"]
    assert offer["candidates"] == [
        {
            "account_id": account["id"],
            "label": "Kreditkarte",
            "bank": "Beispielbank",
            "external_ref_masked": None,
        }
    ]
    assert offer["suggested_account_id"] == account["id"]

    body = {"account_id": account["id"]}
    imported = ok(client_a.post(f"{path}/import-statement", json=body), 201)
    assert imported["document_id"] == document["id"]
    assert imported["account_id"] == account["id"]
    assert imported["format"] == "card_pdf"
    assert imported["status"] == "reconciled"
    assert ok(client_a.post(f"{path}/import-statement", json=body), 200)["id"] == imported["id"]
    assert [i["id"] for i in ok(client_a.get(f"/api/accounts/{account['id']}/imports"))] == [
        imported["id"]
    ]

    detail = ok(client_a.get(path))
    assert detail["statement_offer"] is None
    assert detail["statement_imports"] == [
        {
            "import_id": imported["id"],
            "account_id": account["id"],
            "account_label": "Kreditkarte",
            "status": "reconciled",
        }
    ]
    assert [i for i in ok(client_a.get("/api/inbox")) if i["kind"] == "read_failed"] == []


def test_an_account_of_another_kind_is_422(client_a, app_engine, auth_a):
    document = _refused_upload(client_a, app_engine, auth_a.workspace_id)
    giro = create(
        client_a,
        "/api/accounts",
        {
            "label": "Girokonto",
            "bank": "Beispielbank",
            "iban": "DE50100000000000003008",
            "opening_balance": "0.00",
            "opening_balance_on": "2026-03-31",
        },
    )
    refused = ok(
        client_a.post(
            f"/api/documents/{document['id']}/import-statement", json={"account_id": giro["id"]}
        ),
        422,
    )
    assert refused["error"] == "rule_violation"
    assert refused["code"] == "statement.account_mismatch"


def test_another_workspace_gets_404(client_a, client_b, app_engine, auth_a):
    document = _refused_upload(client_a, app_engine, auth_a.workspace_id)
    account_a = _card_account(client_a)
    account_b = _card_account(client_b)
    path = f"/api/documents/{document['id']}"
    assert client_b.get(path).status_code == 404
    response = client_b.post(f"{path}/import-statement", json={"account_id": account_b["id"]})
    assert response.status_code == 404
    response = client_b.post(f"{path}/import-statement", json={"account_id": account_a["id"]})
    assert response.status_code == 404
    # nor may workspace A import onto B's account
    response = client_a.post(f"{path}/import-statement", json={"account_id": account_b["id"]})
    assert response.status_code == 404
    assert ok(client_a.get(path))["statement_imports"] == []
