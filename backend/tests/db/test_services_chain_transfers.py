"""Phase 7 step 5: transfers between own accounts on a chain (docs/phases/phase-7.md D184,
D186 (1), §6 example 2, §7), services.chain.transfers against the database.

The rows are seeded as the superuser (committed, workspace ids explicit) in the shapes the
wallet sync and the exchange import write; the services then run as the app role under RLS.
Synthetic data only: the addresses are EIP-55's published examples, the transaction hashes are
SHA-256 digests of invented text, every quantity is invented (example 2's numbers are the
fixture's: 0.3 ETH out with a 0.0005 fee, 0.2995 in)."""

import hashlib
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from db.db_support import _admin, _insert, insert_event
from db.test_phase6_migration import _asset, _depot
from db.test_phase7_migration import EVM, EVM_2, _address, _wallet
from psycopg.types.json import Jsonb
from sqlalchemy import select

from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import movement_group as group_table
from hausbuch.db.tables import transfer_link as link_table
from hausbuch.rules.errors import TRANSFER_LEGS_INVALID
from hausbuch.rules.review import CHAIN_FIRST_SYNC, DETERMINISTIC, NO_MATCH
from hausbuch.schemas.household import (
    OpenTransferCode,
    TransferLinkKind,
    TransferLinkRequest,
    TransferMatchedBy,
)
from hausbuch.services import transfers
from hausbuch.services.chain import transfers as chain_transfers
from hausbuch.services.errors import ConflictError, NotFoundError, RuleViolationError

pytestmark = pytest.mark.db

T0 = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
TODAY = date(2026, 3, 20)  # every 48-hour window has passed
TODAY_AT = datetime(2026, 3, 20, 7, 0, tzinfo=UTC)
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"  # the registry's WETH on Ethereum


def _hash(text: str) -> str:
    return "0x" + hashlib.sha256(text.encode()).hexdigest()


class Seed:
    """Committed rows of one workspace, written as the superuser."""

    def __init__(self, dbname: str, ws: uuid.UUID) -> None:
        self.dbname, self.ws = dbname, ws
        with _admin(dbname) as conn:
            self.wallet = _wallet(conn, ws, label="Hardware-Wallet")
            self.wallet_b = _wallet(conn, ws, label="Zweit-Wallet")
            self.exchange = _depot(
                conn, ws, kind="exchange", external_ref_masked="**** 0815", label="Börse"
            )
            self.eth_plain = _asset(conn, ws, symbol="ETH", name="Ether")  # the exchange's
            self.eth = _asset(
                conn,
                ws,
                symbol="ETH",
                name="Ether",
                chain="ethereum",
                decimals=18,
                priced_as_asset_id=self.eth_plain,
            )
            self.eth_arb = _asset(
                conn,
                ws,
                symbol="ETH",
                name="Ether",
                chain="arbitrum",
                decimals=18,
                priced_as_asset_id=self.eth_plain,
            )
            self.weth = _asset(
                conn,
                ws,
                symbol="WETH",
                name="Wrapped Ether",
                chain="ethereum",
                contract_address=WETH,
                decimals=18,
                priced_as_asset_id=self.eth_plain,
            )
            self.address = _address(conn, ws, self.wallet, chain="ethereum", address=EVM)
            self.address_arb = _address(conn, ws, self.wallet, chain="arbitrum", address=EVM)
            self.address_b = _address(conn, ws, self.wallet_b, chain="ethereum", address=EVM_2)

    def exchange_out(
        self, units: str, *, fee=None, tx_hash=None, network=None, at=T0, status="confirmed"
    ):
        with _admin(self.dbname) as conn:
            return _insert(
                conn,
                "asset_movement",
                {
                    "workspace_id": self.ws,
                    "account_id": self.exchange,
                    "asset_id": self.eth_plain,
                    "kind": "internal_transfer_out",
                    "quantity": f"-{units}",
                    "fee_quantity": fee,
                    "occurred_at": at,
                    "tx_hash": tx_hash,
                    "network": network,
                    "source": "csv",
                    "status": status,
                    "movement_hash": hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                },
            )

    def group(
        self,
        legs,
        *,
        account=None,
        chain="ethereum",
        kind="exchange_transfer",
        status="proposed",
        reason=NO_MATCH,
        tx_hash=None,
        at=T0 + timedelta(minutes=4),
    ):
        """One movement group with its legs, each (asset, address, quantity, kind, status),
        and its movement_group.proposed or .confirmed event. The ids: (group, [movement])."""
        account = account or self.wallet
        tx_hash = tx_hash or _hash(f"invented {uuid.uuid4()}")
        with _admin(self.dbname) as conn:
            group = _insert(
                conn,
                "movement_group",
                {
                    "workspace_id": self.ws,
                    "account_id": account,
                    "chain": chain,
                    "tx_hash": tx_hash,
                    "occurred_at": at,
                    "kind": kind,
                    "status": status,
                },
            )
            insert_event(
                conn,
                self.ws,
                type=f"movement_group.{status}",
                entity_type="movement_group",
                entity_id=group,
                actor_type="system",
                actor_id="sync_wallets",
                payload=Jsonb(
                    {"account_id": str(account), "chain": chain, "kind": kind, "reason": reason}
                ),
            )
            ids = []
            for n, (asset, address, quantity, mkind, mstatus) in enumerate(legs):
                ids.append(
                    _insert(
                        conn,
                        "asset_movement",
                        {
                            "workspace_id": self.ws,
                            "account_id": account,
                            "asset_id": asset,
                            "kind": mkind,
                            "quantity": quantity,
                            "occurred_at": at,
                            "tx_hash": tx_hash,
                            "source": "chain",
                            "status": mstatus,
                            "group_id": group,
                            "address_id": address,
                            "index": str(n),
                            "movement_hash": hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                        },
                    )
                )
        return group, ids

    def rebook(self, movement_id: uuid.UUID, kind: str = "disposal") -> uuid.UUID:
        """A person's answer re-booking a leg: a new row, the old one superseded by it."""
        with _admin(self.dbname) as conn:
            old = conn.execute(
                "SELECT account_id, asset_id, quantity, occurred_at FROM asset_movement "
                "WHERE id = %s",
                (movement_id,),
            ).fetchone()
            new = _insert(
                conn,
                "asset_movement",
                {
                    "workspace_id": self.ws,
                    "account_id": old[0],
                    "asset_id": old[1],
                    "kind": kind,
                    "quantity": old[2],
                    "occurred_at": old[3],
                    "source": "user",
                    "status": "confirmed",
                },
            )
            conn.execute(
                "UPDATE asset_movement SET superseded_by = %s WHERE id = %s", (new, movement_id)
            )
        return new


@pytest.fixture
def seed(dbname, fresh_workspaces):
    return Seed(dbname, fresh_workspaces[0])


def _links(read, ws):
    return read(ws, lambda conn: conn.execute(select(link_table)).all())


def _movement(read, ws, movement_id):
    m = movement_table.c
    return read(ws, lambda conn: conn.execute(select(m.status).where(m.id == movement_id)).one())


def _group_status(read, ws, group_id):
    g = group_table.c
    return read(
        ws, lambda conn: conn.execute(select(g.status).where(g.id == group_id)).scalar_one()
    )


def _events(read, ws, entity_id):
    e = event_table.c
    return read(
        ws,
        lambda conn: conn.execute(
            select(e.type, e.payload).where(e.entity_id == entity_id).order_by(e.seq)
        ).all(),
    )


def _open(read, ws):
    return read(ws, transfers.open_transfers, TODAY)


def _pick(run, ws, out_id, in_id, kind=TransferLinkKind.CHAIN):
    body = TransferLinkRequest(kind=kind, out_movement_id=out_id, in_movement_id=in_id)
    return run(ws, transfers.link_by_hand, body, now=T0 + timedelta(days=1))


# --- Chain: an exchange withdrawal by its hash (§6 example 2) -------------------------------


def test_example_2_withdrawal_linked_by_hash_and_confirmed(seed, run, read):
    tx = _hash("example 2")
    out = seed.exchange_out("0.3", fee="0.0005", tx_hash=tx, network="ETH")
    group, [in_] = seed.group(
        [(seed.eth, seed.address, "0.2995", "internal_transfer_in", "proposed")], tx_hash=tx
    )

    result = run(seed.ws, chain_transfers.relink, now=T0 + timedelta(hours=1))

    assert (result.linked, result.confirmed) == (1, 1)
    [link] = _links(read, seed.ws)
    assert link.kind == TransferLinkKind.CHAIN.value
    assert link.matched_by == TransferMatchedBy.MATCHER.value
    assert (link.out_movement_id, link.in_movement_id) == (out, in_)
    assert link.out_transaction_id is None and link.in_transaction_id is None
    assert link.superseded_by is None
    # D186 (1): the wallet's receipt, routed routing.no_match, is confirmed by the link.
    assert _movement(read, seed.ws, in_).status == "confirmed"
    assert _group_status(read, seed.ws, group) == "confirmed"
    types = [t for t, _ in _events(read, seed.ws, group)]
    assert types == ["movement_group.proposed", "movement_group.confirmed"]
    confirmed = _events(read, seed.ws, group)[-1].payload
    assert confirmed["reason"] == DETERMINISTIC
    [(linked_type, payload)] = _events(read, seed.ws, link.id)
    assert linked_type == "transfer.linked"
    assert payload["out_movement_id"] == str(out) and payload["in_movement_id"] == str(in_)
    # D179: no hash in any event this wrote.
    for _, payload in _events(read, seed.ws, group) + _events(read, seed.ws, in_):
        assert tx not in str(payload) and tx[2:] not in str(payload)
    # A current link: no card for either leg.
    assert _open(read, seed.ws) == []


def test_relink_twice_writes_one_link(seed, run, read):
    tx = _hash("twice")
    seed.exchange_out("0.3", fee="0.0005", tx_hash=tx, network="ETH")
    seed.group([(seed.eth, seed.address, "0.2995", "internal_transfer_in", "proposed")], tx_hash=tx)

    first = run(seed.ws, chain_transfers.relink)
    second = run(seed.ws, chain_transfers.relink)

    assert (first.linked, second.linked, second.confirmed) == (1, 0, 0)
    assert len(_links(read, seed.ws)) == 1


def test_first_sync_waits_for_the_persons_pick(seed, run, read):
    """A wallet's first sync is never confirmed by code (D186 (2)): the card proposes the fit,
    and the person's pick confirms both legs and links them (matched_by user)."""
    tx = _hash("first sync")
    out = seed.exchange_out("0.3", fee="0.0005", tx_hash=tx, network="ETH")
    group, [in_] = seed.group(
        [(seed.eth, seed.address, "0.2995", "internal_transfer_in", "proposed")],
        tx_hash=tx,
        reason=CHAIN_FIRST_SYNC,
    )

    assert run(seed.ws, chain_transfers.relink).linked == 0
    [card] = _open(read, seed.ws)
    assert card.code is OpenTransferCode.PROPOSED
    assert card.leg.id == out and card.leg.amount is None
    assert card.leg.quantity == Decimal("-0.3") and card.leg.asset_symbol == "ETH"
    [candidate] = card.candidates
    assert candidate.id == in_ and candidate.link_kind is TransferLinkKind.CHAIN
    assert candidate.chain == "ethereum" and candidate.account_label == "Hardware-Wallet"
    # The Zu prüfen card names the coin transfer, no hash or address.
    [item] = read(seed.ws, transfers.transfer_inbox_items, TODAY)
    assert "Krypto" in item.reasoning
    dumped = item.model_dump_json()
    assert tx[2:] not in dumped and EVM not in dumped

    link = _pick(run, seed.ws, out, in_)

    assert link.matched_by is TransferMatchedBy.USER and link.kind is TransferLinkKind.CHAIN
    assert _movement(read, seed.ws, in_).status == "confirmed"
    assert _group_status(read, seed.ws, group) == "confirmed"
    assert _open(read, seed.ws) == []
    # A second click returns the same link.
    assert _pick(run, seed.ws, out, in_).id == link.id


def test_pick_of_a_pair_the_matcher_does_not_fit_is_refused(seed, run, read):
    out = seed.exchange_out("0.3", fee="0.0005", tx_hash=_hash("a"), network="ETH")
    _, [in_] = seed.group(
        [(seed.eth, seed.address, "0.2995", "internal_transfer_in", "confirmed")],
        tx_hash=_hash("another transaction"),
        status="confirmed",
    )
    with pytest.raises(RuleViolationError) as refused:
        _pick(run, seed.ws, out, in_)
    assert refused.value.code == TRANSFER_LEGS_INVALID
    with pytest.raises(RuleViolationError):  # the right legs, the wrong kind
        tx = _hash("b")
        out_b = seed.exchange_out("1", tx_hash=tx, network="ETH")
        _, [in_b] = seed.group(
            [(seed.eth, seed.address, "1", "internal_transfer_in", "confirmed")],
            tx_hash=tx,
            status="confirmed",
        )
        _pick(run, seed.ws, out_b, in_b, kind=TransferLinkKind.BRIDGE)
    assert _links(read, seed.ws) == []


# --- Chain: by quantity and window (a BISON withdrawal has no hash) -------------------------


def test_withdrawal_without_hash_links_when_both_legs_are_confirmed(seed, run, read):
    out = seed.exchange_out("1", fee="0.001")
    _, [in_] = seed.group(
        [(seed.eth, seed.address, "0.999", "internal_transfer_in", "confirmed")],
        status="confirmed",
        at=T0 + timedelta(hours=3),
    )
    assert run(seed.ws, chain_transfers.relink).linked == 1
    [link] = _links(read, seed.ws)
    assert (link.kind, link.out_movement_id, link.in_movement_id) == ("chain", out, in_)


def test_withdrawal_without_hash_and_a_proposed_receipt_is_only_proposed(seed, run, read):
    """Quantity and time alone never confirm a leg (principle 3): the card proposes it."""
    out = seed.exchange_out("1", fee="0.001")
    _, [in_] = seed.group(
        [(seed.eth, seed.address, "0.999", "internal_transfer_in", "proposed")],
        at=T0 + timedelta(hours=3),
    )
    assert run(seed.ws, chain_transfers.relink).linked == 0
    [card] = _open(read, seed.ws)
    assert (card.code, card.leg.id, [c.id for c in card.candidates]) == (
        OpenTransferCode.PROPOSED,
        out,
        [in_],
    )
    assert _movement(read, seed.ws, in_).status == "proposed"


def test_two_fits_are_ambiguous_and_the_pick_links_one(seed, run, read):
    out = seed.exchange_out("1")
    _, [first] = seed.group(
        [(seed.eth, seed.address, "1", "internal_transfer_in", "confirmed")],
        status="confirmed",
        at=T0 + timedelta(hours=1),
    )
    _, [second] = seed.group(
        [(seed.eth, seed.address_b, "1", "internal_transfer_in", "confirmed")],
        account=seed.wallet_b,
        status="confirmed",
        at=T0 + timedelta(hours=2),
    )
    assert run(seed.ws, chain_transfers.relink).linked == 0
    [card] = _open(read, seed.ws)
    assert card.code is OpenTransferCode.AMBIGUOUS
    assert {c.id for c in card.candidates} == {first, second}
    assert {c.link_kind for c in card.candidates} == {TransferLinkKind.CHAIN}

    link = _pick(run, seed.ws, out, second)

    assert (link.in_movement_id, link.matched_by) == (second, TransferMatchedBy.USER)
    assert _open(read, seed.ws) == []  # the other receipt was no transfer leg of this send
    with pytest.raises(ConflictError):  # the send is linked already
        _pick(run, seed.ws, out, first)


def test_unmatched_withdrawal_card_only_when_its_chain_has_a_wallet(seed, run, read):
    seed.exchange_out("2", network="ETH")  # Ethereum: the wallet has an address there
    seed.exchange_out("3", network="BSC")  # BNB Smart Chain: no own address, so no card
    [card] = _open(read, seed.ws)
    assert card.code is OpenTransferCode.UNMATCHED_LEG and card.candidates == []
    assert card.leg.quantity == Decimal("-2")
    [item] = read(seed.ws, transfers.transfer_inbox_items, TODAY)
    assert "48 Stunden" in item.reasoning
    # Inside its 48 hours: no card yet.
    assert read(seed.ws, transfers.open_transfers, T0.date()) == []


# --- Wallet to wallet, wrap and bridge ------------------------------------------------------


def test_wallet_to_wallet_by_hash(seed, run, read):
    tx = _hash("wallet to wallet")
    _, [out] = seed.group(
        [(seed.eth, seed.address, "-1", "internal_transfer_out", "proposed")],
        kind="transfer",
        tx_hash=tx,
        at=T0,
    )
    _, [in_] = seed.group(
        [(seed.eth, seed.address_b, "1", "internal_transfer_in", "proposed")],
        account=seed.wallet_b,
        kind="transfer",
        tx_hash=tx,
        at=T0,
    )
    assert run(seed.ws, chain_transfers.relink).linked == 1
    [link] = _links(read, seed.ws)
    assert (link.kind, link.out_movement_id, link.in_movement_id) == ("chain", out, in_)
    assert _movement(read, seed.ws, out).status == "confirmed"
    assert _movement(read, seed.ws, in_).status == "confirmed"


def test_wrap_in_one_confirmed_group(seed, run, read):
    """§6 example 8: 1 ETH to the WETH contract, 1 WETH back in the same transaction."""
    group, [eth_out, weth_in] = seed.group(
        [
            (seed.eth, seed.address, "-1", "internal_transfer_out", "confirmed"),
            (seed.weth, seed.address, "1", "internal_transfer_in", "confirmed"),
        ],
        kind="wrap",
        status="confirmed",
        reason=DETERMINISTIC,
    )
    assert run(seed.ws, chain_transfers.relink).linked == 1
    [link] = _links(read, seed.ws)
    assert (link.kind, link.out_movement_id, link.in_movement_id) == ("wrap", eth_out, weth_in)
    assert link.matched_by == "matcher"
    del group


def test_bridge_links_confirmed_legs_and_proposes_unconfirmed_ones(seed, run, read):
    """§6 example 9: 0.5 ETH out on Ethereum, 0.4985 in on Arbitrum 20 minutes later."""
    _, [out] = seed.group(
        [(seed.eth, seed.address, "-0.5", "internal_transfer_out", "proposed")],
        kind="bridge",
        at=T0,
    )
    _, [in_] = seed.group(
        [(seed.eth_arb, seed.address_arb, "0.4985", "internal_transfer_in", "proposed")],
        chain="arbitrum",
        kind="bridge",
        at=T0 + timedelta(minutes=20),
    )
    # Proposed legs: a bridge is never confirmed by code (D186 (1)); the card proposes it.
    assert run(seed.ws, chain_transfers.relink).linked == 0
    [card] = _open(read, seed.ws)
    assert card.code is OpenTransferCode.PROPOSED
    [candidate] = card.candidates
    assert (candidate.id, candidate.link_kind, candidate.chain) == (
        in_,
        TransferLinkKind.BRIDGE,
        "arbitrum",
    )

    link = _pick(run, seed.ws, out, in_, kind=TransferLinkKind.BRIDGE)

    assert (link.kind, link.matched_by) == (TransferLinkKind.BRIDGE, TransferMatchedBy.USER)
    assert _movement(read, seed.ws, out).status == "confirmed"
    assert _movement(read, seed.ws, in_).status == "confirmed"


# --- A re-booked leg ends its link (D184 (5)) -----------------------------------------------


def test_rebooked_leg_ends_the_matchers_link(seed, run, read):
    tx = _hash("rebooked")
    out = seed.exchange_out("0.3", fee="0.0005", tx_hash=tx, network="ETH")
    _, [in_] = seed.group(
        [(seed.eth, seed.address, "0.2995", "internal_transfer_in", "proposed")], tx_hash=tx
    )
    run(seed.ws, chain_transfers.relink)
    [link] = _links(read, seed.ws)

    seed.rebook(in_)

    assert read(seed.ws, transfers._link_of, out, in_) is None
    assert out not in read(seed.ws, transfers._linked_ids)
    assert run(seed.ws, chain_transfers.relink).linked == 0  # a sale is no transfer leg
    [card] = _open(read, seed.ws)
    assert (card.code, card.leg.id) == (OpenTransferCode.UNMATCHED_LEG, out)
    # Append-only: the row stays, unsuperseded; it is no longer current on read.
    [still] = _links(read, seed.ws)
    assert (still.id, still.superseded_by) == (link.id, None)


def test_rebooked_leg_ends_a_persons_link_too(seed, run, read):
    out = seed.exchange_out("1")
    _, [in_] = seed.group(
        [(seed.eth, seed.address, "1", "internal_transfer_in", "proposed")],
        at=T0 + timedelta(hours=1),
    )
    _pick(run, seed.ws, out, in_)
    assert out in read(seed.ws, transfers._linked_ids)

    seed.rebook(out, kind="disposal")

    assert out not in read(seed.ws, transfers._linked_ids)
    assert in_ not in read(seed.ws, transfers._linked_ids)


# --- Workspace isolation --------------------------------------------------------------------


def test_another_workspace_sees_and_links_nothing(dbname, fresh_workspaces, run, read):
    a, b = fresh_workspaces
    seed = Seed(dbname, a)
    Seed(dbname, b)  # B has wallets and an exchange of its own, no movement
    tx = _hash("isolation")
    out = seed.exchange_out("0.3", fee="0.0005", tx_hash=tx, network="ETH")
    _, [in_] = seed.group(
        [(seed.eth, seed.address, "0.2995", "internal_transfer_in", "proposed")],
        tx_hash=tx,
        reason=CHAIN_FIRST_SYNC,
    )

    assert run(b, chain_transfers.relink).linked == 0
    assert read(b, transfers.open_transfers, TODAY) == []
    with pytest.raises(NotFoundError):
        _pick(run, b, out, in_)
    assert _links(read, b) == []
    assert _links(read, a) == []
    assert len(_open(read, a)) == 1


# --- Hooks and the command ------------------------------------------------------------------


def test_confirming_an_exchange_withdrawal_links_it(seed, run, read):
    """The hook after a person's answer on an exchange's movement (D178 (4)): a withdrawal
    confirmed as going to an own wallet links to the confirmed receipt at once."""
    from hausbuch.services.exchange.imports import confirm_movement

    out = seed.exchange_out("1", fee="0.001", status="proposed")
    _, [in_] = seed.group(
        [(seed.eth, seed.address, "0.999", "internal_transfer_in", "confirmed")],
        status="confirmed",
        at=T0 + timedelta(hours=3),
    )
    assert run(seed.ws, chain_transfers.relink).linked == 0  # quantity alone confirms nothing

    run(seed.ws, confirm_movement, out)

    [link] = _links(read, seed.ws)
    assert (link.out_movement_id, link.in_movement_id, link.matched_by) == (out, in_, "matcher")


def test_answer_hook_never_waits_for_the_transfer_lock(seed, run, app_engine):
    from hausbuch.db.session import workspace_transaction
    from hausbuch.schemas.enums import ActorType
    from hausbuch.services.context import Actor, new_command

    actor = Actor(ActorType.SYSTEM, "test")
    with workspace_transaction(app_engine, seed.ws) as holder:
        transfers._lock(new_command(holder, seed.ws, actor))
        result = run(seed.ws, chain_transfers.relink, wait=False)
    assert result.busy and result.linked == 0


def test_after_sync_hook_links_and_never_raises(seed, read, app_engine, monkeypatch, caplog):
    from hausbuch.schemas.enums import ActorType
    from hausbuch.services.context import Actor

    actor = Actor(ActorType.SYSTEM, "sync_wallets")
    tx = _hash("after sync")
    seed.exchange_out("0.3", fee="0.0005", tx_hash=tx, network="ETH")
    seed.group([(seed.eth, seed.address, "0.2995", "internal_transfer_in", "proposed")], tx_hash=tx)

    chain_transfers.relink_after_sync(app_engine, seed.ws, actor=actor, clock=lambda: TODAY_AT)
    assert len(_links(read, seed.ws)) == 1

    def broken(ctx, **kw):
        raise RuntimeError(f"invented failure quoting {tx}")

    monkeypatch.setattr(chain_transfers, "relink", broken)
    with caplog.at_level("ERROR", logger="hausbuch.services.chain.transfers"):
        chain_transfers.relink_after_sync(app_engine, seed.ws, actor=actor, clock=lambda: TODAY_AT)
    [record] = [r for r in caplog.records if "not relinked" in r.getMessage()]
    assert record.fields["exc_type"] == "RuntimeError"
    assert tx not in caplog.text  # the class name only, never the message


def test_relink_chain_command(dbname, seed, read, capsys):
    from db.test_cli_evals_report import _owner_url

    from hausbuch import cli

    tx = _hash("command")
    seed.exchange_out("0.3", fee="0.0005", tx_hash=tx, network="ETH")
    seed.group([(seed.eth, seed.address, "0.2995", "internal_transfer_in", "proposed")], tx_hash=tx)
    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)}
    argv = ["relink-chain", "--workspace", str(seed.ws)]

    assert cli.main([*argv, "--dry-run"], environ) == cli.EXIT_OK
    text = capsys.readouterr().out
    assert (
        "would link 1 coin transfer(s)" in text and "would confirm 1 movement(s)" in text
    )  # the receipt; the withdrawal was confirmed
    assert _links(read, seed.ws) == []

    assert cli.main(argv, environ) == cli.EXIT_OK
    assert "linked 1 coin transfer(s)" in capsys.readouterr().out
    assert len(_links(read, seed.ws)) == 1
    assert cli.main(argv, environ) == cli.EXIT_OK  # a second run links nothing more
    assert "linked 0 coin transfer(s)" in capsys.readouterr().out
