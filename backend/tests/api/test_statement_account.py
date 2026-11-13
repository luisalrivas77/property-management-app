"""POST /documents/{id}/statement-account, „Konto anlegen und einlesen“ (phase 6, 13 Nov 2026,
amends D176 (3) and D165 (b)), and the offer's `new_account` on GET /documents/{id}. The
service is covered in tests/db/test_services_statement_account.py; these check the route:
status codes, the read shapes, that the IBAN never travels in clear, and isolation. Invented
files only (the anonymised Sparkasse samples, tests/test_bank_parsers.py)."""

from datetime import UTC, datetime

import pytest
from api_support import create, ok, upload
from test_bank_parsers import CAMT, build

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import proposal as proposal_table
from hausbuch.schemas.enums import ProposalStatus
from hausbuch.services.proposals import PROPOSAL_BANK_FILE_NOT_READ

pytestmark = pytest.mark.db

T0 = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
CAMT_ZIP = build(next(c for c in CAMT.cases if c.id == "sparkasse_zip_of_daily_reports").input)
IBAN = "DE50100000000000000001"


def _refused_upload(client, app_engine, workspace_id):
    """The camt ZIP uploaded through Dokumente, and the failed read the document job records
    for it (proposal.bank_file_not_read), written directly: the job is covered in tests/db."""
    document = ok(upload(client, CAMT_ZIP, media_type="application/zip", filename="camt.zip"), 201)
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


def test_the_offer_then_create_and_import_201(client_a, app_engine, auth_a):
    document = _refused_upload(client_a, app_engine, auth_a.workspace_id)
    path = f"/api/documents/{document['id']}"
    response = client_a.get(path)
    assert IBAN not in response.text
    offer = ok(response)["statement_offer"]
    assert offer["format"] == "camt"
    assert offer["account_kind"] == "bank_current"
    assert offer["candidates"] == [] and offer["suggested_account_id"] is None
    assert offer["new_account"] == {
        "kind": "bank_current",
        "label": "Girokonto",
        "bank": None,
        "provider": None,
        "iban_masked": "DE50 **** 0001",
        "external_ref_masked": None,
        # The starting balance the button saves, read from the file and shown first (D132).
        "opening_balance": "0.00",
        "opening_balance_on": "2026-03-31",
        "opening_from_file": True,
    }

    owner = create(client_a, "/api/owners", {"full_name": "Owner A"})
    body = {
        "label": "Girokonto",
        "bank": "Sparkasse",
        "shares": [{"owner_id": owner["id"], "share_numerator": 1, "share_denominator": 1}],
    }
    response = client_a.post(f"{path}/statement-account", json=body)
    assert IBAN not in response.text
    created = ok(response, 201)
    account, imported = created["account"], created["statement_import"]
    assert account["kind"] == "bank_current" and account["purpose"] == "private"
    assert account["iban_masked"] == "DE50 **** 0001"
    assert account["ownership_missing"] is False
    assert imported["document_id"] == document["id"]
    assert imported["account_id"] == account["id"]
    assert imported["status"] == "reconciled"

    detail = ok(client_a.get(path))
    assert detail["statement_offer"] is None
    assert [i["account_id"] for i in detail["statement_imports"]] == [account["id"]]
    assert [i for i in ok(client_a.get("/api/inbox")) if i["kind"] == "read_failed"] == []

    # Done once: nothing left to create from.
    again = ok(client_a.post(f"{path}/statement-account", json=body), 422)
    assert again["code"] == "statement.no_account_offer"


def test_an_account_with_the_files_iban_is_409(client_a, app_engine, auth_a):
    document = _refused_upload(client_a, app_engine, auth_a.workspace_id)
    create(
        client_a,
        "/api/accounts",
        {
            "label": "Girokonto",
            "bank": "Beispielbank",
            "iban": IBAN,
            "opening_balance": "0.00",
            "opening_balance_on": "2026-03-31",
            "is_owner_account": True,
            "purpose": "private",
        },
    )
    path = f"/api/documents/{document['id']}"
    assert ok(client_a.get(path))["statement_offer"]["new_account"] is None
    response = client_a.post(f"{path}/statement-account", json={"label": "X", "bank": "Y"})
    assert response.status_code == 409
    assert IBAN not in response.text
    assert len(ok(client_a.get("/api/accounts"))) == 1


def test_a_document_without_an_offer_is_422(client_a):
    document = ok(upload(client_a), 201)
    response = client_a.post(
        f"/api/documents/{document['id']}/statement-account", json={"label": "X", "bank": "Y"}
    )
    assert ok(response, 422)["code"] == "statement.no_account_offer"


def test_shares_not_summing_to_one_are_422(client_a, app_engine, auth_a):
    document = _refused_upload(client_a, app_engine, auth_a.workspace_id)
    owner = create(client_a, "/api/owners", {"full_name": "Owner A"})
    body = {
        "label": "Girokonto",
        "bank": "Sparkasse",
        "shares": [{"owner_id": owner["id"], "share_numerator": 1, "share_denominator": 2}],
    }
    response = client_a.post(f"/api/documents/{document['id']}/statement-account", json=body)
    assert response.status_code == 422
    assert ok(client_a.get("/api/accounts")) == []


def test_another_workspace_gets_404(client_a, client_b, app_engine, auth_a):
    document = _refused_upload(client_a, app_engine, auth_a.workspace_id)
    response = client_b.post(
        f"/api/documents/{document['id']}/statement-account", json={"label": "X", "bank": "Y"}
    )
    assert response.status_code == 404
    assert ok(client_b.get("/api/accounts")) == []
    assert ok(client_a.get("/api/accounts")) == []
