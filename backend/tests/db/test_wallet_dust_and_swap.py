"""D262 (docs/phases/phase-7.md, 17 Nov 2026): a swap through a program Hausbuch does not
know is confirmed by „Bestätigen“ when code read its legs, and a stranger's tiny receipt
(address-poisoning dust) is proposed and booked as a Kleinstbetrag for that movement only.
Luis: „Also for 2 I had already clicked confirm but it still shows in the list“. Invented
wallets, coins, programs, quantities and prices only."""

import hashlib
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from db.test_services_holdings import _account, _movement, _price
from sqlalchemy import insert, select

from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import movement_group as group_table
from hausbuch.db.tables import wallet_address as address_table
from hausbuch.rules.chain.solana import QUESTION_PROGRAM
from hausbuch.rules.errors import WALLET_GROUP_NEEDS_ANSWER, RuleError
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.wallet_cards import WalletAnswer, WalletCardKind
from hausbuch.services import holdings
from hausbuch.services.wallet_groups import (
    DustCandidate,
    answered_groups,
    confirm_group,
    dust_groups,
    list_groups,
    undo_group,
    waiting,
)

pytestmark = pytest.mark.db

AT = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)  # Berlin day 1 Oct 2026
DAY = date(2026, 10, 1)
ON = date(2026, 11, 16)
USDC_MINT = "UsdcTestMintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"  # base58, invented
ETH_MINT = "EthTestMintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
PROGRAM_QUESTION = f"{QUESTION_PROGRAM}Prog…Ab12?"  # a masked invented program
STAKE_QUESTION = "Ist Stak…Cd34 Ihr eigenes Stake-Konto?"


def _coin(ctx, symbol, name, *, contract=None, spam=False, priced_as=None) -> uuid.UUID:
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
            priced_as_asset_id=priced_as,
        )
        .returning(asset_table.c.id)
    ).scalar_one()


def _address(ctx, account_id) -> uuid.UUID:
    seed = uuid.uuid4().bytes
    return ctx.conn.execute(
        insert(address_table)
        .values(
            workspace_id=ctx.workspace_id,
            account_id=account_id,
            chain="solana",
            address="So1" + hashlib.sha256(seed).hexdigest()[:40].replace("0", "z"),
            address_masked="So1x…xxxx",
            address_hmac=hashlib.sha256(seed).digest(),
        )
        .returning(address_table.c.id)
    ).scalar_one()


def _group(
    ctx,
    account_id,
    legs,
    *,
    kind="transfer",
    question=None,
    status="proposed",
    at=AT,
    tx_hash=None,
    address=None,
) -> uuid.UUID:
    """One Solana transaction with its legs: (asset, quantity, kind) each."""
    tx_hash = tx_hash or hashlib.sha256(uuid.uuid4().bytes).hexdigest()
    group_id = ctx.conn.execute(
        insert(group_table)
        .values(
            workspace_id=ctx.workspace_id,
            account_id=account_id,
            chain="solana",
            tx_hash=tx_hash,
            occurred_at=at,
            kind=kind,
            status=status,
            question=question,
        )
        .returning(group_table.c.id)
    ).scalar_one()
    address = address or _address(ctx, account_id)
    for index, (asset_id, units, leg_kind) in enumerate(legs):
        _movement(
            ctx,
            account_id,
            asset_id,
            units,
            at,
            address_id=address,
            kind=leg_kind,
            index=str(index),
            status=status,
            group_id=group_id,
            tx_hash=tx_hash,
        )
    return group_id


def _card(read, ws, account, group):
    [card] = [c for c in read(ws, list_groups, account) if c.id == group]
    return card


# --- the swap through an unknown program --------------------------------------------------


@pytest.fixture
def swap(run, fresh_workspaces):
    ws, _ = fresh_workspaces

    def setup(ctx):
        account = _account(ctx, AccountKind.WALLET, "Tauschwallet")
        sol = _coin(ctx, "SOL", "Solana")
        usdc = _coin(ctx, "USDC", "USD Coin (Test)", contract=USDC_MINT)
        eth = _coin(ctx, "ETH", "Ether (Testbrücke)", contract=ETH_MINT)
        group = _group(
            ctx,
            account,
            [
                (usdc, "-10", "disposal"),
                (eth, "0.003", "acquisition"),
                (sol, "-0.000005", "fee"),
            ],
            kind="swap",
            question=PROGRAM_QUESTION,
        )
        return account, group

    return ws, run(ws, setup)


def test_a_swap_that_asks_only_its_program_is_confirmable(read, swap):
    ws, (account, group) = swap
    card = _card(read, ws, account, group)
    assert card.card is WalletCardKind.PROPOSED
    assert card.confirmable is True
    assert card.text.startswith("Vorschlag von Hausbuch: 10 USDC gesendet")
    assert "Prog…Ab12" in card.text and card.text.endswith("Stimmt das, „Bestätigen“.")


def test_confirming_the_swap_books_it_and_the_card_leaves(run, read, swap):
    ws, (account, group) = swap
    assert run(ws, confirm_group, group) == group  # confirmed in place
    assert [c.id for c in read(ws, list_groups, account)] == []
    assert read(ws, waiting, ON) == []

    def facts(conn):
        g, m, e = group_table.c, movement_table.c, event_table.c
        row = conn.execute(select(g.status, g.question).where(g.id == group)).one()
        legs = conn.execute(select(m.kind, m.status).where(m.group_id == group)).all()
        events = conn.execute(
            select(e.type, e.payload).where(e.entity_id == group).order_by(e.seq)
        ).all()
        return row, legs, events

    row, legs, events = read(ws, facts)
    # The question stays on the row: the record of what the person answered by confirming.
    assert (row.status, row.question) == ("confirmed", PROGRAM_QUESTION)
    assert sorted(legs) == [
        ("acquisition", "confirmed"),
        ("disposal", "confirmed"),
        ("fee", "confirmed"),
    ]
    assert [(t, p["reason"]) for t, p in events] == [("movement_group.confirmed", "routing.person")]
    assert [g.id for g in read(ws, answered_groups, account)] == [group]


def test_undoing_a_confirmed_swap_shows_its_card_again(run, read, swap):
    ws, (account, group) = swap
    run(ws, confirm_group, group)
    again = run(ws, undo_group, group)
    card = _card(read, ws, account, again)
    assert (card.question, card.confirmable) == (PROGRAM_QUESTION, True)
    assert card.text.endswith("Stimmt das, „Bestätigen“.")


def test_a_stake_question_still_needs_its_answer(run, read, fresh_workspaces):
    ws, _ = fresh_workspaces

    def setup(ctx):
        account = _account(ctx, AccountKind.WALLET, "Stakewallet")
        sol = _coin(ctx, "SOL", "Solana")
        group = _group(
            ctx,
            account,
            [(sol, "-2", "internal_transfer_out"), (sol, "-0.000005", "fee")],
            kind="stake",
            question=STAKE_QUESTION,
        )
        return account, group

    account, group = run(ws, setup)
    card = _card(read, ws, account, group)
    assert card.confirmable is False
    assert "Bestätigen" not in card.text  # the text no longer promises a button it lacks
    assert card.text == f"2 SOL gesendet (So1x…xxxx), Gebühr 0,000005 SOL. {STAKE_QUESTION}"
    assert card.answers == [WalletAnswer.PAYMENT]
    with pytest.raises(RuleError) as refused:
        run(ws, confirm_group, group)
    assert refused.value.code == WALLET_GROUP_NEEDS_ANSWER


def test_an_uncategorised_leg_with_a_program_question_is_not_confirmable(
    run, read, fresh_workspaces
):
    ws, _ = fresh_workspaces

    def setup(ctx):
        account = _account(ctx, AccountKind.WALLET, "Unbekannt")
        token = _coin(ctx, "ETH", "Ether (Testbrücke)", contract=ETH_MINT)
        group = _group(
            ctx,
            account,
            [(token, "0.5", "uncategorized")],
            kind="contract_call",
            question=PROGRAM_QUESTION,
        )
        return account, group

    account, group = run(ws, setup)
    card = _card(read, ws, account, group)
    assert (card.confirmable, card.text.endswith("Was war das?")) == (False, True)
    with pytest.raises(RuleError):
        run(ws, confirm_group, group)


# --- dust --------------------------------------------------------------------------------


def _dust_world(ctx, *, units="0.00002", price="150", price_on=DAY, spam=False):
    """A wallet with a real receipt of 3 SOL and a stranger's receipt of `units` SOL a
    minute later; SOL priced at `price` on `price_on` (None: no price)."""
    account = _account(ctx, AccountKind.WALLET, "Staubwallet")
    sol = _coin(ctx, "SOL", "Solana", spam=spam)
    address = _address(ctx, account)
    _group(ctx, account, [(sol, "3", "internal_transfer_in")], status="confirmed", address=address)
    dust = _group(
        ctx,
        account,
        [(sol, units, "uncategorized")],
        at=AT.replace(minute=1),
        address=address,
    )
    if price is not None:
        _price(ctx, sol, price_on, price)
    return account, sol, dust


def test_dust_is_proposed_with_its_explanation(run, read, fresh_workspaces):
    ws, _ = fresh_workspaces
    account, _, dust = run(ws, _dust_world)
    card = _card(read, ws, account, dust)
    assert (card.card, card.confirmable) == (WalletCardKind.PROPOSED, True)
    assert card.text.startswith("0,00002 SOL erhalten (So1x…xxxx): ein Kleinstbetrag")
    assert "weniger als 0,01 € wert" in card.text
    assert "Adress-Spam" in card.text and "Kopieren Sie nie eine Adresse" in card.text
    assert card.text.endswith("Stimmt das, „Bestätigen“.")
    # „Spam“ never for the chain's own coin (it would hide every SOL); the other answers stay.
    assert card.answers == [WalletAnswer.GIFT, WalletAnswer.REWARD]


def test_confirming_dust_books_a_zero_euro_lot_and_never_flags_the_coin(
    run, read, fresh_workspaces
):
    ws, _ = fresh_workspaces
    account, sol, dust = run(ws, _dust_world)
    booked = run(ws, confirm_group, dust)
    assert booked != dust  # a new group supersedes the sync's
    assert [c.id for c in read(ws, list_groups, account)] == []
    assert read(ws, waiting, ON) == []

    def facts(conn):
        t, m = asset_table.c, movement_table.c
        spam = conn.execute(select(t.spam).where(t.id == sol)).scalar_one()
        legs = conn.execute(
            select(m.kind, m.status, m.eur_amount, m.quantity).where(
                m.group_id == booked, m.superseded_by.is_(None)
            )
        ).all()
        return spam, legs

    spam, legs = read(ws, facts)
    assert spam is False
    assert [tuple(r) for r in legs] == [
        ("acquisition", "confirmed", Decimal(0), Decimal("0.00002"))
    ]

    tab = read(ws, holdings.crypto_holdings, ON)
    [coin] = [c for c in tab.coins if c.asset_id == sol]
    assert coin.units == Decimal("3.00002")
    assert coin.units == sum((lot.units_open for lot in coin.lots), Decimal(0))
    [lot] = [lot for lot in coin.lots if lot.units == Decimal("0.00002")]
    assert lot.cost_eur == Decimal(0) and lot.price_needs == []


def test_undoing_dust_shows_the_dust_card_again(run, read, fresh_workspaces):
    ws, _ = fresh_workspaces
    account, _, dust = run(ws, _dust_world)
    booked = run(ws, confirm_group, dust)
    assert [g.id for g in read(ws, answered_groups, account)] == [booked]
    again = run(ws, undo_group, booked)
    card = _card(read, ws, account, again)
    assert card.confirmable is True and "Adress-Spam" in card.text


@pytest.mark.parametrize(
    "world",
    [
        {"price": None},  # no price: never dust by a guessed value
        {"price_on": date(2026, 9, 30)},  # only the day before: not that day's price
        {"units": "1"},  # 150 €: a real receipt
        {"units": "0.0000667", "price": "150"},  # 0.010005 €: at the limit, not below it
    ],
)
def test_no_dust_without_a_price_of_its_day_or_above_the_limit(run, read, fresh_workspaces, world):
    ws, _ = fresh_workspaces
    account, _, group = run(ws, lambda ctx: _dust_world(ctx, **world))
    card = _card(read, ws, account, group)
    assert card.confirmable is False
    assert card.text.endswith("Was war das?")
    with pytest.raises(RuleError):
        run(ws, confirm_group, group)


def test_a_priced_as_twin_prices_the_day(run, read, fresh_workspaces):
    ws, _ = fresh_workspaces

    def setup(ctx):
        account = _account(ctx, AccountKind.WALLET, "Zwilling")
        eth = _coin(ctx, "ETH", "Ether")
        bridged = _coin(ctx, "ETH", "Ether (Testbrücke)", contract=ETH_MINT, priced_as=eth)
        _price(ctx, eth, DAY, "3000")
        group = _group(ctx, account, [(bridged, "0.000001", "uncategorized")])
        return account, group

    account, group = run(ws, setup)
    assert _card(read, ws, account, group).confirmable is True  # 0.003 €


def test_a_hash_an_own_wallet_also_moved_is_not_dust(run, read, fresh_workspaces):
    ws, _ = fresh_workspaces

    def setup(ctx):
        account, sol, dust = _dust_world(ctx)
        other = _account(ctx, AccountKind.WALLET, "Zweite Wallet")
        tx_hash = ctx.conn.execute(
            select(group_table.c.tx_hash).where(group_table.c.id == dust)
        ).scalar_one()
        [leg] = ctx.conn.execute(
            select(movement_table.c.id, movement_table.c.quantity).where(
                movement_table.c.group_id == dust
            )
        ).all()
        # The same transaction also moved an own wallet's coins: never a stranger's dust.
        _group(ctx, other, [(sol, "-0.00002", "internal_transfer_out")], tx_hash=tx_hash)
        return dust, leg, sol

    dust, leg, sol = run(ws, setup)
    candidate = DustCandidate(
        group_id=dust,
        movement_id=leg.id,
        asset_id=sol,
        quantity=leg.quantity,
        day=DAY,
        tx_hash=None,
    )
    assert read(ws, lambda conn: dust_groups(conn, [candidate])) == {dust}
    with_hash = DustCandidate(**(candidate.__dict__ | {"tx_hash": "x"}))
    assert read(ws, lambda conn: dust_groups(conn, [with_hash])) == {dust}

    def real_hash(conn):
        tx = conn.execute(
            select(group_table.c.tx_hash).where(group_table.c.id == dust)
        ).scalar_one()
        return dust_groups(conn, [DustCandidate(**(candidate.__dict__ | {"tx_hash": tx}))])

    assert read(ws, real_hash) == set()


def test_a_spam_flagged_coin_is_never_dust(run, read, fresh_workspaces):
    ws, _ = fresh_workspaces
    _, sol, dust = run(ws, lambda ctx: _dust_world(ctx, spam=True))

    def check(conn):
        [leg] = conn.execute(
            select(movement_table.c.id).where(movement_table.c.group_id == dust)
        ).all()
        return dust_groups(
            conn,
            [DustCandidate(dust, leg.id, sol, Decimal("0.00002"), DAY, None)],
        )

    assert read(ws, check) == set()
