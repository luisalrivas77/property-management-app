"""A wallet's read movements that wait for a person are named on the Krypto tab, the Anlagen
overview and the net worth (services/wallet_groups.waiting, 16 Nov 2026; docs/phases/phase-7.md
step 6's as-built note). Luis: „It seems it booked less coins than are in the wallet.“ Invented
wallets, coins and quantities only."""

import hashlib
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from db.test_services_holdings import _account, _movement
from sqlalchemy import insert, update

from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import movement_group as group_table
from hausbuch.db.tables import wallet_address as address_table
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.net_worth import NetWorthTodoCode
from hausbuch.services import holdings, net_worth
from hausbuch.services.prices.overview import assets_overview
from hausbuch.services.wallet_groups import waiting

pytestmark = pytest.mark.db

ON = date(2026, 11, 16)
AT = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
TOKEN = "Tok1nMintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"  # base58, invented
JUNK = "Junk1MintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"


def _coin(ctx, symbol, name, *, contract=None, spam=False) -> uuid.UUID:
    return ctx.conn.execute(
        insert(asset_table)
        .values(
            workspace_id=ctx.workspace_id,
            kind="crypto",
            symbol=symbol,
            name=name,
            chain="solana",
            contract_address=contract,
            spam=spam,
        )
        .returning(asset_table.c.id)
    ).scalar_one()


def _group(ctx, account_id, legs, *, status="proposed", at=AT) -> uuid.UUID:
    """One Solana transaction with its legs: (asset, quantity, kind) each."""
    group_id = ctx.conn.execute(
        insert(group_table)
        .values(
            workspace_id=ctx.workspace_id,
            account_id=account_id,
            chain="solana",
            tx_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            occurred_at=at,
            kind="transfer",
            status=status,
        )
        .returning(group_table.c.id)
    ).scalar_one()
    address = ctx.conn.execute(
        insert(address_table)
        .values(
            workspace_id=ctx.workspace_id,
            account_id=account_id,
            chain="solana",
            address="So1" + hashlib.sha256(group_id.bytes).hexdigest()[:40].replace("0", "z"),
            address_masked="So1x…xxxx",
            address_hmac=hashlib.sha256(group_id.bytes).digest(),
        )
        .returning(address_table.c.id)
    ).scalar_one()
    for index, (asset_id, units, kind) in enumerate(legs):
        _movement(
            ctx,
            account_id,
            asset_id,
            units,
            at,
            address_id=address,
            kind=kind,
            index=str(index),
            status=status,
            group_id=group_id,
        )
    return group_id


@pytest.fixture
def wallet(run, fresh_workspaces):
    """A Solana wallet „Testwallet“: one confirmed receipt of 1 SOL, two proposed
    transactions (a receipt of 2.5 SOL with its fee, a token receipt of 0.07) and a proposed
    receipt of a spam token."""
    ws, other = fresh_workspaces

    def setup(ctx):
        account = _account(ctx, AccountKind.WALLET, "Testwallet")
        sol = _coin(ctx, "SOL", "Solana")
        token = _coin(ctx, "ETH", "Ether (Testbrücke)", contract=TOKEN)
        junk = _coin(ctx, None, "Token Junk…XXXX", contract=JUNK, spam=True)
        _group(ctx, account, [(sol, "1", "internal_transfer_in")], status="confirmed")
        received = _group(
            ctx,
            account,
            [(sol, "2.5", "internal_transfer_in"), (sol, "-0.000005", "fee")],
        )
        _group(ctx, account, [(token, "0.07", "internal_transfer_in")])
        _group(ctx, account, [(junk, "1000", "airdrop")])
        return {"account": account, "sol": sol, "token": token, "received": received}

    return ws, other, run(ws, setup)


def test_the_proposed_legs_are_named_per_coin_with_the_count(read, wallet):
    ws, other, ids = wallet
    [line] = read(ws, waiting, ON)
    assert (line.account_id, line.label, line.groups) == (ids["account"], "Testwallet", 2)
    assert [(c.asset_id, c.units) for c in line.coins] == [
        (ids["token"], Decimal("0.07")),
        (ids["sol"], Decimal("2.499995")),
    ]
    assert line.text == (
        "0,07 Ether (Testbrücke) und 2,499995 SOL auf „Testwallet“ sind gelesen, zählen aber "
        "erst nach Ihrer Bestätigung mit (2 Transaktionen warten)."
    )
    assert (line.action, line.href) == ("Transaktionen prüfen", f"/accounts/{ids['account']}")
    assert "So1" not in line.text  # never an address
    assert read(other, waiting, ON) == []

    # A transaction after the day asked for is not counted on it.
    assert read(ws, waiting, date(2026, 9, 30)) == []


def test_the_krypto_tab_the_overview_and_the_net_worth_carry_it(read, wallet):
    ws, _, ids = wallet
    tab = read(ws, holdings.crypto_holdings, ON)
    [sol] = tab.coins
    assert sol.units == Decimal("1")  # the confirmed receipt only
    assert [w.account_id for w in tab.waiting] == [ids["account"]]

    overview = read(ws, assets_overview, ON)
    assert [w.groups for w in overview.waiting] == [2]

    figure = read(ws, net_worth.net_worth, ON)
    [todo] = [t for t in figure.todos if t.code is NetWorthTodoCode.WALLET_WAITING]
    assert todo.text.endswith("Bis dahin fehlen sie im Nettovermögen.")
    assert todo.href == f"/accounts/{ids['account']}"


def test_confirmed_groups_leave_no_line(run, read, wallet):
    ws, _, ids = wallet

    def settle(ctx):
        # Every live proposed leg and group confirmed: nothing waits.
        from hausbuch.db.tables import asset_movement as movement_table

        ctx.conn.execute(
            update(movement_table)
            .where(movement_table.c.status == "proposed")
            .values(status="confirmed")
        )
        ctx.conn.execute(
            update(group_table).where(group_table.c.status == "proposed").values(status="confirmed")
        )

    run(ws, settle)
    assert read(ws, waiting, ON) == []
    assert read(ws, holdings.crypto_holdings, ON).waiting == []
    figure = read(ws, net_worth.net_worth, ON)
    assert not [t for t in figure.todos if t.code is NetWorthTodoCode.WALLET_WAITING]


def test_a_wallet_with_only_spam_waiting_gets_no_line(run, read, fresh_workspaces):
    ws, _ = fresh_workspaces

    def setup(ctx):
        account = _account(ctx, AccountKind.WALLET, "Nur Spam")
        junk = _coin(ctx, None, "Token Junk…XXXX", contract=JUNK, spam=True)
        _group(ctx, account, [(junk, "5", "airdrop")])

    run(ws, setup)
    assert read(ws, waiting, ON) == []


def test_an_outgoing_transaction_is_worded_as_not_yet_taken_off(run, read, fresh_workspaces):
    ws, _ = fresh_workspaces

    def setup(ctx):
        account = _account(ctx, AccountKind.WALLET, "Ausgang")
        sol = _coin(ctx, "SOL", "Solana")
        _group(ctx, account, [(sol, "3", "internal_transfer_in")], status="confirmed")
        _group(ctx, account, [(sol, "-1.25", "internal_transfer_out")])

    run(ws, setup)
    [line] = read(ws, waiting, ON)
    assert [c.units for c in line.coins] == [Decimal("-1.25")]
    assert line.text == (
        "Abgänge von 1,25 SOL auf „Ausgang“ sind gelesen, werden aber erst nach Ihrer "
        "Bestätigung abgezogen (1 Transaktion wartet)."
    )
