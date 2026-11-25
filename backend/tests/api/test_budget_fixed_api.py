"""GET /api/household-budget's fixed payments over HTTP (docs/phases/phase-10.md §7.10 (c),
D300): Notwendiges and Wünsche carry `fixed` and `variable`, a series POST
/api/household/recurring-marks hides as „kein Abo“ leaves the fixed rows of the kept answer
at once (the mark moves the data version), and the series id is the one GET
/api/household/recurring's panel uses. The months are the six before today's, so the test
reads the route's own clock. The arithmetic is tests/fixtures/phase10/budget_fixed.yaml and
tests/db/test_services_budget_fixed.py; invented payees and amounts only."""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from api_support import ok
from db.db_support import _admin
from db.matching_support import World
from db.test_services_private_matching import Private
from db.test_services_recurring import _booked
from sqlalchemy import update

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import statement_import as import_table
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.services.documents import LOCAL_TIME_ZONE

pytestmark = pytest.mark.db

C = AllocationCategory
BUDGET = "/api/household-budget"
RECURRING = "/api/household/recurring"
SERIES = "/api/household/recurring-marks"


@pytest.fixture
def person(auth_a, dbname):
    with _admin(dbname) as conn:
        conn.execute(
            "INSERT INTO app_user (id, workspace_id, email, display_name)"
            " VALUES (%s, %s, %s, 'Test')",
            (auth_a.user_id, auth_a.workspace_id, f"{auth_a.user_id}@example.test"),
        )


def _months(today: date, count: int) -> list[tuple[int, int]]:
    """The `count` months before today's, oldest first."""
    out = []
    year, month = today.year, today.month
    for _ in range(count):
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
        out.append((year, month))
    return out[::-1]


def _household(app_engine, workspace_id) -> None:
    today = datetime.now(LOCAL_TIME_ZONE).date()
    months = _months(today, 6)
    first = date(months[0][0], months[0][1], 1)
    last = today.replace(day=1) - timedelta(days=1)
    world = World(app_engine, workspace_id, now=datetime.now(LOCAL_TIME_ZONE))
    private = Private(world)
    with workspace_transaction(world.engine, world.ws) as conn:
        for import_id in (world.import_id, private.import_id):
            conn.execute(
                update(import_table)
                .where(import_table.c.id == import_id)
                .values(first_booking_on=first, last_booking_on=last)
            )
    places = [
        "Trattoria Roma",
        "Pizzeria Napoli",
        "Bistro Lindt",
        "Taverne Kreta",
        "Imbiss Ost",
        "Kantine Nord",
    ]
    for (year, month), place in zip(months, places, strict=True):
        _booked(world, world.line(date(year, month, 3), "950.00"), "950.00", C.RENT_COLD)
        salary = private.line(date(year, month, 27), "3100.00", name="Arbeitgeber")
        _booked(world, salary, "3100.00", C.HH_SALARY)
        tv = private.line(date(year, month, 5), "-12.99", name="Streamdienst Muster")
        _booked(world, tv, "-12.99", C.HH_STREAMING)
        meal = private.line(date(year, month, 14), "-20.00", name=place)  # no series
        _booked(world, meal, "-20.00", C.HH_RESTAURANT)


def _want(client):
    (line,) = [line for line in ok(client.get(BUDGET))["lines"] if line["key"] == "want"]
    return line


def test_a_hidden_series_leaves_the_kept_fixed_rows(app_engine, auth_a, person, client_a):
    _household(app_engine, auth_a.workspace_id)
    got = ok(client_a.get(BUDGET))
    for line in got["lines"]:
        if line["key"] in ("need", "want"):
            means = sum((Decimal(f["mean"]) for f in line["fixed"]), Decimal(line["variable"]))
            assert means == Decimal(line["monthly"])
            fixed = sum((Decimal(f["mean"]) for f in line["fixed"]), Decimal("0.00"))
            assert Decimal(line["fixed_monthly"]) == fixed
        else:
            assert line["fixed"] is None and line["variable"] is None
            assert line["fixed_monthly"] is None
    want = _want(client_a)
    assert want == _want(client_a)  # kept
    (row,) = want["fixed"]
    assert (row["monthly"], row["mean"], row["interval"]) == ("12.99", "12.99", "monthly")
    (series,) = ok(client_a.get(RECURRING))["series"]
    assert row["key"] == series["sources"][0] and row["label"] == series["label"]

    body = {"key": series["key"], "interval": series["interval"], "answer": "hidden"}
    assert client_a.post(SERIES, json=body).status_code == 201
    after = _want(client_a)
    assert after["fixed"] == []
    assert after["variable"] == after["monthly"] == want["monthly"]
    assert after["fixed_monthly"] == "0.00"
