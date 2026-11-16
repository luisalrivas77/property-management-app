"""A chain asset created before the registry listed its token is brought up to the registry at
the next sync (services/chain/sync.adopt_registry, 16 Nov 2026; docs/phases/phase-7.md D180
(3), step 6's as-built note). Invented rows only; the one real contract is the registry's own
„Ether (Portal)“ mint, public market data."""

import uuid

import pytest
from sqlalchemy import insert, select

from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import event as event_table
from hausbuch.rules.household.assets import registry as asset_registry
from hausbuch.schemas.chain import Chain
from hausbuch.services.chain.sync import adopt_registry

pytestmark = pytest.mark.db

PORTAL = "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs"
UNLISTED = "Unknown1MintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"  # base58, invented


def _chain_asset(ctx, *, contract, symbol, name, decimals, spam=False) -> uuid.UUID:
    return ctx.conn.execute(
        insert(asset_table)
        .values(
            workspace_id=ctx.workspace_id,
            kind="crypto",
            symbol=symbol,
            name=name,
            decimals=decimals,
            chain="solana",
            contract_address=contract,
            spam=spam,
        )
        .returning(asset_table.c.id)
    ).scalar_one()


def _rows(conn):
    a = asset_table.c
    return {
        r.contract_address or f"{r.symbol}@{r.chain or 'off-chain'}": r
        for r in conn.execute(
            select(a.id, a.symbol, a.name, a.chain, a.contract_address, a.priced_as_asset_id)
        )
    }


def test_the_portal_mint_is_listed_as_ether():
    token = asset_registry().token(Chain.SOLANA, PORTAL)
    assert token is not None and token.decimals == 8 and token.name == "Ether (Portal)"
    assert asset_registry().priced_as(token).symbol == "ETH"


def test_a_token_stored_without_a_symbol_gets_the_registry_row_once(run, read, fresh_workspaces):
    ws, other = fresh_workspaces

    def setup(ctx):
        return {
            "native": _chain_asset(
                ctx, contract=None, symbol="SOL", name="SOL (solana)", decimals=9
            ),
            "portal": _chain_asset(
                ctx, contract=PORTAL, symbol=None, name="Token 7vfC…voxs", decimals=8
            ),
            "unlisted": _chain_asset(
                ctx, contract=UNLISTED, symbol=None, name="Token Unkn…XXXX", decimals=6
            ),
            "spam": _chain_asset(
                ctx,
                contract="Spam1MintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
                symbol=None,
                name="Token Spam…XXXX",
                decimals=0,
                spam=True,
            ),
        }

    ids = run(ws, setup)
    assert run(ws, adopt_registry, Chain.SOLANA) == 2  # the native coin and the Portal token

    rows = read(ws, _rows)
    portal, native = rows[PORTAL], rows["SOL@solana"]
    assert (portal.symbol, portal.name) == ("ETH", "Ether (Portal)")
    # Each priced through the symbol-level coin, created from the registry when missing.
    eth, sol = rows["ETH@off-chain"], rows["SOL@off-chain"]
    assert portal.priced_as_asset_id == eth.id
    assert native.priced_as_asset_id == sol.id
    assert rows[UNLISTED].symbol is None and rows[UNLISTED].priced_as_asset_id is None
    spam = next(r for r in rows.values() if r.contract_address and r.contract_address[:4] == "Spam")
    assert spam.priced_as_asset_id is None

    def events(conn):
        e = event_table.c
        return conn.execute(
            select(e.payload).where(e.type == "asset.updated", e.entity_id == ids["portal"])
        ).all()

    [event] = read(ws, events)
    assert event.payload["changes"]["symbol"] == {"old": None, "new": "ETH"}

    # Idempotent: a second sync changes nothing and writes no event; the other workspace
    # never sees the rows (RLS).
    assert run(ws, adopt_registry, Chain.SOLANA) == 0
    assert len(read(ws, events)) == 1
    assert read(other, _rows) == {}


def test_a_listed_contract_with_other_decimals_is_left_alone(run, read, fresh_workspaces):
    ws, _ = fresh_workspaces
    run(
        ws,
        lambda ctx: _chain_asset(
            ctx, contract=PORTAL, symbol=None, name="Token 7vfC…voxs", decimals=6
        ),
    )
    run(ws, adopt_registry, Chain.SOLANA)
    portal = read(ws, _rows)[PORTAL]
    assert portal.symbol is None and portal.priced_as_asset_id is None
