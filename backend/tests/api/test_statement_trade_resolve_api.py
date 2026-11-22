"""POST /depot/statement-trades/{transaction_id}/resolve, the card „Kontoauszug und
Abrechnung weichen ab“ (phase 6 D292 amended (b), 23 Nov 2026): status codes and isolation.
The decision itself is tests/db/test_services_depot_statement_trades.py."""

import uuid

import pytest
from api_support import ok

pytestmark = pytest.mark.db


def test_resolve_needs_a_line_of_this_workspace_and_a_known_choice(client_a, client_b):
    url = f"/api/depot/statement-trades/{uuid.uuid4()}/resolve"
    assert ok(client_a.post(url, json={"keep": "statement"}), 404)["error"] == "not_found"
    assert client_b.post(url, json={"keep": "document"}).status_code == 404
    assert client_a.post(url, json={"keep": "both"}).status_code == 422
    assert client_a.post(url, json={}).status_code == 422
