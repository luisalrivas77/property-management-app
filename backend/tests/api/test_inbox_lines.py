"""GET /inbox/lines (phase 10 §7.3 (5), 21 Nov 2026): the bank lines and statement imports
Zu prüfen's cards look up, instead of every account's lines and imports. Pinned here: the ids
it covers (each item's line and import, both lines of every possible duplicate and their
imports), each object equal to what /accounts/{id}/transactions and /imports return for it,
and workspace isolation. Invented IBANs and amounts only."""

from datetime import date
from uuid import UUID

import pytest
from api_support import create, ok
from db.matching_support import World
from db.test_services_private_matching import NOW, Private
from test_accounts import ACCOUNT, CSV_BYTES, _upload, user_a  # noqa: F401 (fixture)

from hausbuch.services import statements

pytestmark = pytest.mark.db

FRIEND_IBAN = "DE46100000000000000073"
RECALL = "RUECKUEBERWEISUNG Rueckgabe aufgrund eines Recalls (Rueckrufes) Umbuchung Juli"


def _per_account(client) -> tuple[dict[str, dict], dict[str, dict]]:
    """The old way: every account's lines and imports, by id."""
    lines: dict[str, dict] = {}
    imports: dict[str, dict] = {}
    for account in ok(client.get("/api/accounts")):
        for line in ok(client.get(f"/api/accounts/{account['id']}/transactions")):
            lines[line["id"]] = line
        for imp in ok(client.get(f"/api/accounts/{account['id']}/imports")):
            imports[imp["id"]] = imp
    return lines, imports


def _check(client) -> dict:
    """GET /inbox/lines covers exactly the ids the cards look up, each object as the
    per-account reads serialise it."""
    items = ok(client.get("/api/inbox"))
    pairs = ok(client.get("/api/possible-duplicates"))
    got = ok(client.get("/api/inbox/lines"))
    lines, imports = _per_account(client)
    pair_ids = {p[k] for p in pairs for k in ("transaction_id", "other_transaction_id")}
    want_lines = {i["transaction_id"] for i in items if i["transaction_id"]} | pair_ids
    want_imports = {i["import_id"] for i in items if i["import_id"] in imports} | {
        lines[i]["import_id"] for i in pair_ids
    }
    got_lines = {line["id"]: line for line in got["transactions"]}
    got_imports = {imp["id"]: imp for imp in got["imports"]}
    assert set(got_lines) == want_lines
    assert set(got_imports) == want_imports
    assert all(got_lines[i] == lines[i] for i in got_lines)
    assert all(got_imports[i] == imports[i] for i in got_imports)
    return got


def test_the_lines_and_imports_of_the_cards_and_duplicates(client_a, client_b):
    account = create(client_a, "/api/accounts", ACCOUNT)
    from_csv = ok(_upload(client_a, account["id"], CSV_BYTES, "text/csv", "umsaetze.csv"), 201)
    ok(
        client_a.post(
            f"/api/imports/{from_csv['id']}/balances", json={"closing_balance": "-3028.51"}
        )
    )
    ok(_upload(client_a, account["id"]), 201)
    pairs = ok(client_a.get("/api/possible-duplicates", params={"status": "open"}))
    assert len(pairs) == 19
    # A decided pair: still listed by /possible-duplicates, its later line excluded.
    ok(client_a.post(f"/api/possible-duplicates/{pairs[0]['id']}", json={"status": "merged"}))

    got = _check(client_a)
    lines = {line["id"]: line for line in got["transactions"]}
    assert lines[pairs[0]["other_transaction_id"]]["excluded_as_duplicate_of"] is not None
    assert len(got["imports"]) == 2
    # Another workspace sees none of it.
    assert ok(client_b.get("/api/inbox/lines")) == {"transactions": [], "imports": []}
    assert _check(client_b) == {"transactions": [], "imports": []}


def test_an_import_card_gets_its_derived_opening(client_a):
    account = create(client_a, "/api/accounts", ACCOUNT)
    imported = ok(_upload(client_a, account["id"], CSV_BYTES, "text/csv", "umsaetze.csv"), 201)
    ok(
        client_a.post(
            f"/api/imports/{imported['id']}/balances", json={"closing_balance": "-3000.00"}
        )
    )
    items = ok(client_a.get("/api/inbox"))
    assert any(
        i["kind"] == "import_unreconciled" and i["import_id"] == imported["id"] for i in items
    )
    [imp] = _check(client_a)["imports"]
    assert imp["id"] == imported["id"]
    assert imp["derived_opening_balance"] == "28.51"


@pytest.fixture
def world(app_engine, auth_a) -> World:
    return World(app_engine, auth_a.workspace_id, now=NOW)


def test_a_returned_payment_reads_as_on_the_account(client_a, user_a, world):  # noqa: F811
    """The lines' read is the account read's code: a booked returned payment names its other
    half (returned_with) either way, and a card's line is covered."""
    private = Private(world)
    debit = private.line(date(2026, 7, 28), "-3200.00", iban=FRIEND_IBAN, purpose="Umbuchung Juli")
    credit = private.line(date(2026, 7, 30), "3200.00", iban=FRIEND_IBAN, purpose=RECALL)
    other = private.line(date(2026, 7, 29), "-100.00", iban=FRIEND_IBAN)
    _check(client_a)
    body = {"out_transaction_id": str(debit), "in_transaction_id": str(credit)}
    ok(client_a.post("/api/returns", json=body), 201)
    _check(client_a)
    on_account = ok(client_a.get(f"/api/accounts/{private.account_id}/transactions"))
    by_id = world.query(statements.transactions_by_id, [debit, credit, other])
    assert [t.model_dump(mode="json") for t in by_id] == on_account
    assert by_id[0].returned_with is not None


def _dumped(items) -> list[dict]:
    return [item.model_dump(mode="json") for item in items]


def test_one_line_or_import_reads_as_on_its_account_alone(client_a, user_a, world):  # noqa: F811
    """transactions_by_id and imports_by_id serialise an id the same whatever else is asked
    for: one half of a booked returned payment alone, one of an account's two imports alone
    (one unreconciled with a derived opening), and imports of three accounts at once."""
    private = Private(world)
    world.line(date(2026, 7, 1), "-12.00")
    debit = private.line(date(2026, 7, 28), "-3200.00", iban=FRIEND_IBAN, purpose="Umbuchung Juli")
    credit = private.line(date(2026, 7, 30), "3200.00", iban=FRIEND_IBAN, purpose=RECALL)
    body = {"out_transaction_id": str(debit), "in_transaction_id": str(credit)}
    ok(client_a.post("/api/returns", json=body), 201)
    account = create(client_a, "/api/accounts", ACCOUNT)
    from_csv = ok(_upload(client_a, account["id"], CSV_BYTES, "text/csv", "umsaetze.csv"), 201)
    ok(
        client_a.post(
            f"/api/imports/{from_csv['id']}/balances", json={"closing_balance": "-3000.00"}
        )
    )
    ok(_upload(client_a, account["id"]), 201)
    lines, imports = _per_account(client_a)

    for one in (debit, credit):
        [line] = _dumped(world.query(statements.transactions_by_id, [one]))
        assert line == lines[str(one)] and line["returned_with"] is not None
    own = ok(client_a.get(f"/api/accounts/{account['id']}/imports"))
    assert len(own) == 2
    assert any(imp["derived_opening_balance"] is not None for imp in own)
    for imp in own:
        assert _dumped(world.query(statements.imports_by_id, [UUID(imp["id"])])) == [imp]
    spanning = _dumped(world.query(statements.imports_by_id, [UUID(i) for i in imports]))
    assert len(spanning) == len(imports)
    assert len({imp["account_id"] for imp in spanning}) == 3
    assert all(imp == imports[imp["id"]] for imp in spanning)
