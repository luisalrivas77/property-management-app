"""The income tax return's read routes over HTTP (docs/phases/phase-4b.md §9, §10): the years an
Anlage N exists for, and a return input's evidence (its Beleg and, for a pre-filled row, each
value's page and quote; tests/db/test_services_proposals_return_inputs.py covers that part)."""

import pytest
from api_support import TOKEN_A, TOKEN_B, auth_for, create, ok
from db.db_support import _admin

from hausbuch.rules.tax import registry

pytestmark = pytest.mark.db


@pytest.fixture
def sessions(fresh_workspaces, dbname):
    """Sessions whose users exist, so a row's recorded_by (FK to app_user) is real."""
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


def test_the_anlage_n_years_are_the_rule_set_files(client_a):
    got = ok(client_a.get("/api/tax/anlage-n/years"))
    assert got == {"years": list(registry.anlage_n_years())}
    assert 2026 in got["years"]


def test_the_evidence_of_an_unknown_row_is_404(client_a):
    got = client_a.get("/api/return-inputs/00000000-0000-4000-8000-000000000001/evidence")
    assert got.status_code == 404


def test_a_typed_rows_evidence_names_no_document(client_a, client_b):
    owner = create(client_a, "/api/owners", {"full_name": "Person A"})
    row = create(
        client_a,
        "/api/return-inputs",
        {
            "owner_id": owner["id"],
            "kind": "training",
            "tax_year": 2026,
            "value": {"label": "Fachbuch", "amount": "40.00"},
        },
    )
    got = ok(client_a.get(f"/api/return-inputs/{row['id']}/evidence"))
    assert got["return_input_id"] == row["id"]
    assert got["document_id"] is None and got["values"] == [] and got["opens_inline"] is False
    # another workspace's row is not found (RLS)
    assert client_b.get(f"/api/return-inputs/{row['id']}/evidence").status_code == 404


def test_the_anlage_n_years_need_a_session(api_app):
    from api_support import client_for

    with client_for(api_app, None) as anonymous:
        assert anonymous.get("/api/tax/anlage-n/years").status_code == 401
