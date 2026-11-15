"""The net-worth and auto-rate routes (docs/phases/phase-6.md §12, D167 (4), (5), D172; phase 6
step 6): status codes, the read shapes and workspace isolation. The service behaviour is
tests/db/test_services_net_worth.py. Invented accounts, IBANs and amounts only."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from api_support import create, ok
from db.db_support import _admin
from db.matching_support import World
from db.test_services_private_matching import NOW, Private

from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.enums import ActorType
from hausbuch.services import allocations
from hausbuch.services.context import Actor
from hausbuch.services.net_worth import last_month_end

pytestmark = pytest.mark.db

C = AllocationCategory
M = AllocationMethod


@pytest.fixture
def person(auth_a, dbname) -> Actor:
    """client_a's user as a real app_user, so a confirmation records confirmed_by."""
    with _admin(dbname) as conn:
        conn.execute(
            "INSERT INTO app_user (id, workspace_id, email, display_name)"
            " VALUES (%s, %s, %s, 'Test')",
            (auth_a.user_id, auth_a.workspace_id, f"{auth_a.user_id}@example.test"),
        )
    return Actor(ActorType.USER, f"user:{auth_a.user_id}")


def _book(
    world: World, tx, method: M, *, auto: bool, category: C = C.HH_GROCERIES, actor=None
) -> None:
    world.cmd(
        allocations.record_set,
        AllocationSet(
            transaction_id=tx,
            allocations=[AllocationDraft(amount=Decimal("-10.00"), category=category)],
            method=method,
            confidence=Decimal("0.950"),
        ),
        confirm=True,
        auto=auto,
        **({} if actor is None else {"actor": actor}),
    )


def test_the_auto_rate_leaves_the_first_import_out(app_engine, auth_a, person, client_a, client_b):
    world = World(app_engine, auth_a.workspace_id, now=NOW)
    private = Private(world)  # its first import: the history
    _book(world, private.line(date(2026, 10, 2), "-10.00"), M.USER, auto=False, actor=person)
    _book(world, private.line(date(2026, 10, 3), "-10.00"), M.USER, auto=False, actor=person)
    later = private.add_import(NOW + timedelta(days=1))
    for method, auto in (
        (M.DOCUMENT_MATCH, True),
        (M.MATCHER_TRANSFER, True),
        (M.LLM, True),  # the routing confirmed it
        (M.LLM, False),  # a person confirmed the model's proposal
        (M.USER, False),
    ):
        line = private.line(date(2026, 10, 20), "-10.00", import_id=later)
        _book(world, line, method, auto=auto, actor=None if auto else person)
    _book(
        world,
        private.line(date(2026, 11, 2), "-10.00", import_id=later),
        M.USER,
        auto=False,
        actor=person,
    )
    # The rental account's lines are not in it.
    rental = world.line(date(2026, 10, 5), "-10.00")
    _book(world, rental, M.DOCUMENT_MATCH, auto=True, category=C.BANK_FEES)

    got = ok(client_a.get("/api/accounts/auto-rate", params={"month": "2026-10"}))
    [account] = got["accounts"]
    assert account["account_id"] == str(private.account_id)
    assert (account["confirmed"], account["auto"], account["ratio"]) == (7, 3, "0.4286")
    assert account["by_method"] == {"document_match": 1, "llm": 2, "matcher_transfer": 1, "user": 3}
    assert account["by_routing"] == {"auto_confirm": 3, "person": 4}
    assert (account["first_import"], account["first_import_auto"]) == (2, 0)
    assert (got["criterion_confirmed"], got["criterion_auto"]) == (5, 3)
    assert (got["criterion_ratio"], got["target"], got["met"]) == ("0.6000", "0.80", False)

    empty = ok(client_a.get("/api/accounts/auto-rate", params={"month": "2026-09"}))
    assert (empty["confirmed"], empty["ratio"], empty["met"]) == (0, None, None)
    other = ok(client_b.get("/api/accounts/auto-rate", params={"month": "2026-10"}))
    assert other["accounts"] == [] and other["confirmed"] == 0
    for month in ("2026-13", "2026-1", "Oktober"):
        assert client_a.get("/api/accounts/auto-rate", params={"month": month}).status_code == 422


def test_net_worth_month_ends_and_isolation(client_a, client_b):
    created = create(
        client_a,
        "/api/accounts",
        {
            "label": "Tagesgeld",
            "bank": "Beispielbank",
            "iban": "DE73100000000000000072",
            "kind": "bank_savings",
            "purpose": "private",
            "opening_balance": "1234.56",
            "opening_balance_on": "2026-01-31",
        },
    )
    seen = ok(client_a.get("/api/net-worth", params={"on": "2026-10-31"}))
    assert seen["total"] == "1234.56" and seen["by_class"]["bank"] == "1234.56"
    [line] = seen["lines"]
    assert (line["account_id"], line["kind"], line["as_of"]) == (
        created["id"],
        "account",
        "2026-01-31",
    )
    # No owner yet: counted in the total, in no owner, and a to-do with its button. Its
    # balance is the opening one of 31 Jan, with no statement since: a to-do too.
    assert seen["unassigned"] == [created["id"]] and seen["by_owner"] == {}
    outdated, owners = seen["todos"]
    assert (outdated["code"], outdated["action"]) == (
        "net_worth.balance_outdated",
        "Kontoauszug hochladen",
    )
    assert (owners["action"], owners["href"]) == (
        "Eigentümer festlegen",
        f"/accounts/{created['id']}",
    )

    view = ok(client_a.get("/api/net-worth/overview"))
    assert view["month_end"] is None and view["month_end_due"]
    due = date.fromisoformat(view["month_end_due"])
    assert due == last_month_end(date.fromisoformat(view["today"]["on"]))

    written = ok(client_a.post("/api/net-worth/snapshots"))
    assert (written["outcome"], written["as_of"]) == ("written", due.isoformat())
    assert written["run"]["rows"] == 1
    again = ok(client_a.post("/api/net-worth/snapshots"))
    assert again["outcome"] == "unchanged" and again["run"] == written["run"]
    [listed] = ok(client_a.get("/api/net-worth/snapshots"))
    assert listed == written["run"]
    run_id = listed["net_worth_run_id"]
    stored = ok(client_a.get(f"/api/net-worth/snapshots/{run_id}"))
    assert stored["run"] == listed and stored["total"] == "1234.56"
    view = ok(client_a.get("/api/net-worth/overview"))
    assert view["month_end"]["run"] == listed and view["month_end_due"] is None

    # Another workspace sees none of it.
    assert ok(client_b.get("/api/net-worth/snapshots")) == []
    assert client_b.get(f"/api/net-worth/snapshots/{run_id}").status_code == 404
    assert ok(client_b.get("/api/net-worth"))["lines"] == []
    assert ok(client_b.post("/api/net-worth/snapshots"))["outcome"] == "empty"
