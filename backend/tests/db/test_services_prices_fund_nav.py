"""Which funds the daily fetch prices from their issuer (docs/phases/phase-6.md D174, amended
16 Nov 2026), as hausbuch_app under RLS. The registry's funds are taken from the registry
itself, so no test names a fund a household holds; every other ISIN is invented."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from sqlalchemy import insert

from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.rules.household.assets import registry
from hausbuch.schemas.bank import AccountCreate
from hausbuch.schemas.enums import AccountKind, ActorType
from hausbuch.services.accounts import create_account
from hausbuch.services.context import Actor
from hausbuch.services.depot import asset_for_isin
from hausbuch.services.prices import fetch as prices_fetch
from hausbuch.services.prices import overview as prices_overview
from hausbuch.services.prices.fetch import Sources
from hausbuch.services.prices.issuer_nav import ADAPTERS

pytestmark = pytest.mark.db

ACTOR = Actor(ActorType.SYSTEM, "fetch_prices")
FIRST = datetime(2026, 6, 24, 9, 30, tzinfo=UTC)
TODAY = date(2026, 6, 27)


class Down:
    """A Fetch that records every URL and answers 503: what was asked is the point here."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url, headers, timeout, max_bytes):
        self.urls.append(url)
        return 503, b""


def _depot(run, ws, label):
    return run(
        ws,
        create_account,
        AccountCreate(
            label=label,
            bank="Beispiel",
            kind=AccountKind.DEPOT,
            opening_balance=Decimal("0.00"),
            opening_balance_on=date(2026, 6, 1),
        ),
        iban_key=TEST_IBAN_KEY,
        now=FIRST,
    ).id


def _move(ctx, account_id, asset_id, kind, units):
    ctx.conn.execute(
        insert(movement_table).values(
            workspace_id=ctx.workspace_id,
            account_id=account_id,
            asset_id=asset_id,
            kind=kind,
            quantity=Decimal(units),
            occurred_at=FIRST,
            source="securities_transfer" if "transfer" in kind else "user",
            status="confirmed",
            created_at=ctx.now,
        )
    )


def test_every_registry_fund_a_depot_holds_is_asked_from_its_issuer(run, read, fresh_workspaces):
    """The live data of 15 Nov 2026 had no issuer NAV at all: each registry fund, created as
    the depot's documents create it (services.depot.asset_for_isin) and held by an
    Einbuchung, is planned with its registry entry and its issuer's file is asked for."""
    ws, _ = fresh_workspaces
    depot = _depot(run, ws, "Depot")
    funds = registry().funds

    def seed(ctx):
        ids = {}
        for fund in funds:
            asset_id = asset_for_isin(ctx, fund.isin, fund.name)
            _move(ctx, depot, asset_id, "internal_transfer_in", "3.5")
            ids[asset_id] = fund
        return ids

    ids = run(ws, seed, now=FIRST)
    planned = read(ws, prices_fetch.plan)
    held = {h.id: h for h in planned.held}
    assert set(held) == set(ids)
    for asset_id, fund in ids.items():
        assert held[asset_id].fund == fund, fund.issuer
        assert held[asset_id].first_on == FIRST.date()

    down = Down()
    out = prices_fetch.collect(planned, Sources(fetch=down, coingecko=None), TODAY, {})
    for fund in funds:
        url = ADAPTERS[fund.issuer](down).url(fund.fund_ref or "")
        assert down.urls.count(url) == 1, fund.issuer
    assert set(out.failed) == set(ids)  # every file answered 503 here


def test_units_moved_between_own_depots_stay_held(run, read, fresh_workspaces):
    """Units moved from one own depot to another whose purchases came before the app: the
    first depot shows −n, the second +n. The Übersicht shows the +n position; the fetch and
    the price cards treat the fund as held although the units sum to zero over both."""
    ws, _ = fresh_workspaces
    old, new = _depot(run, ws, "Altes Depot"), _depot(run, ws, "Neues Depot")

    def seed(ctx):
        moved = asset_for_isin(ctx, "IE000INVENT7", "Invented Moved Fund")
        _move(ctx, old, moved, "internal_transfer_out", "-4")
        _move(ctx, new, moved, "internal_transfer_in", "4")
        sold = asset_for_isin(ctx, "IE000INVENT8", "Invented Sold Fund")
        _move(ctx, new, sold, "acquisition", "2")
        _move(ctx, new, sold, "disposal", "-2")
        return moved, sold

    moved, sold = run(ws, seed, now=FIRST)
    held = {h.id for h in read(ws, prices_fetch.held_assets)}
    assert moved in held and sold not in held
    cards = {r.id for r in read(ws, prices_overview._held)}
    assert cards == held
