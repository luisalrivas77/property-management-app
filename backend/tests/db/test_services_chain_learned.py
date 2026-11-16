"""Phase 7 D261 (2), (3), (4), (6): what a person's link teaches about an exchange, and the
links code writes from it (services.chain.learned, services.chain.transfers' learned passes).

Seeded as the superuser in the shapes the wallet sync and the exchange import write (the
`Seed` of test_services_chain_transfers), the services run as the app role under RLS.
Synthetic data only: the counterparty is random HMAC bytes with an invented masked form, every
quantity is invented, the addresses are EIP-55's published examples."""

import hashlib
import os
import uuid
from datetime import timedelta

import pytest
from db.db_support import _admin, _insert, insert_event
from db.test_phase6_migration import _depot
from db.test_services_chain_transfers import (
    T0,
    Seed,
    _events,
    _group_status,
    _hash,
    _links,
    _open,
)
from psycopg.types.json import Jsonb
from sqlalchemy import select

from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import exchange_deposit_address as deposit_table
from hausbuch.db.tables import exchange_withdrawal_pair as pair_table
from hausbuch.rules.review import NO_MATCH
from hausbuch.schemas.household import (
    AnswerMovementBody,
    MovementKind,
    TransferLinkCreate,
    TransferLinkKind,
    TransferLinkRequest,
    TransferMatchedBy,
)
from hausbuch.services import transfers
from hausbuch.services.chain import learned
from hausbuch.services.chain import transfers as chain_transfers
from hausbuch.services.errors import ConflictError, NotFoundError
from hausbuch.services.exchange import imports

pytestmark = pytest.mark.db

DEPOSIT = os.urandom(32)  # the HMAC of the exchange's (invented) deposit address
MASKED = "0x1a…9f0b"
LATER = T0 + timedelta(days=5)


class LearnSeed(Seed):
    def __init__(self, dbname: str, ws: uuid.UUID) -> None:
        super().__init__(dbname, ws)
        with _admin(dbname) as conn:
            self.exchange_b = _depot(
                conn, ws, kind="exchange", external_ref_masked="**** 4711", label="Börse B"
            )

    def send(
        self,
        units: str,
        *,
        at=T0,
        hmac: bytes | None = DEPOSIT,
        chain="ethereum",
        asset=None,
        address=None,
        kind="transfer",
        leg_kind="uncategorized",
        reason=NO_MATCH,
    ):
        """A wallet's plain send with its fee leg, as the sync writes it: the out leg carries
        the counterparty's HMAC and masked form. (group, out, fee)."""
        asset = asset or self.eth
        address = address or self.address
        tx_hash = _hash(f"send {uuid.uuid4()}")
        with _admin(self.dbname) as conn:
            group = _insert(
                conn,
                "movement_group",
                {
                    "workspace_id": self.ws,
                    "account_id": self.wallet,
                    "chain": chain,
                    "tx_hash": tx_hash,
                    "occurred_at": at,
                    "kind": kind,
                    "status": "proposed",
                },
            )
            insert_event(
                conn,
                self.ws,
                type="movement_group.proposed",
                entity_type="movement_group",
                entity_id=group,
                actor_type="system",
                actor_id="sync_wallets",
                payload=Jsonb(
                    {"account_id": str(self.wallet), "chain": chain, "kind": kind, "reason": reason}
                ),
            )

            def leg(quantity, mkind, index, **extra):
                return _insert(
                    conn,
                    "asset_movement",
                    {
                        "workspace_id": self.ws,
                        "account_id": self.wallet,
                        "asset_id": asset,
                        "kind": mkind,
                        "quantity": quantity,
                        "occurred_at": at,
                        "tx_hash": tx_hash,
                        "source": "chain",
                        "status": "proposed",
                        "group_id": group,
                        "address_id": address,
                        "index": index,
                        "movement_hash": hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                    }
                    | extra,
                )

            counterparty = (
                {"counterparty_hmac": hmac, "counterparty_masked": MASKED} if hmac else {}
            )
            out = leg(f"-{units}", leg_kind, "0", **counterparty)
            fee = leg("-0.0001", "fee", "fee")
        return group, out, fee

    def deposit(self, units: str, *, at, exchange=None, kind="cashback_reward", status="proposed"):
        """An exchange's crypto Deposit row as its import writes it (a possible bonus)."""
        with _admin(self.dbname) as conn:
            return _insert(
                conn,
                "asset_movement",
                {
                    "workspace_id": self.ws,
                    "account_id": exchange or self.exchange,
                    "asset_id": self.eth_plain,
                    "kind": kind,
                    "quantity": units,
                    "occurred_at": at,
                    "source": "csv",
                    "status": status,
                    "movement_hash": hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                },
            )

    def receipt(self, units: str, *, at, address=None, account=None):
        """A wallet's receipt from an unknown sender: an uncategorized leg, routed no_match."""
        return self.group(
            [(self.eth, address or self.address, units, "uncategorized", "proposed")],
            account=account,
            kind="transfer",
            at=at,
        )

    def learn_deposit(self, exchange=None, chain="ethereum", hmac=DEPOSIT):
        """A learned deposit address seeded directly (its own learned_from send)."""
        _, out, _ = self.send("9", at=T0 - timedelta(days=30), hmac=hmac)
        with _admin(self.dbname) as conn:
            return _insert(
                conn,
                "exchange_deposit_address",
                {
                    "workspace_id": self.ws,
                    "account_id": exchange or self.exchange,
                    "chain": chain,
                    "address_hmac": hmac,
                    "address_masked": MASKED,
                    "learned_from": out,
                },
            )


@pytest.fixture
def seed(dbname, fresh_workspaces):
    return LearnSeed(dbname, fresh_workspaces[0])


def _live(read, ws, movement_id):
    """The live row that stands for `movement_id` (itself, or the end of its superseded_by
    chain)."""
    m = movement_table.c

    def follow(conn):
        row = conn.execute(select(movement_table).where(m.id == movement_id)).one()
        while row.superseded_by is not None:
            row = conn.execute(select(movement_table).where(m.id == row.superseded_by)).one()
        return row

    return read(ws, follow)


def _pick(run, ws, out_id, in_id, **flags):
    body = TransferLinkRequest(
        kind=TransferLinkKind.CHAIN, out_movement_id=out_id, in_movement_id=in_id, **flags
    )
    return run(ws, transfers.link_by_hand, body, now=T0 + timedelta(days=10))


def _learned_rows(read, ws, table=deposit_table):
    return read(ws, lambda conn: conn.execute(select(table)).all())


def _reason(read, ws, link_id):
    [(_, payload)] = _events(read, ws, link_id)
    return payload.get("reason")


# --- (2) a learned deposit address -----------------------------------------------------------


def test_a_persons_link_learns_the_deposit_address_and_code_books_the_rest(seed, run, read):
    """Luis: „I would prefer if the app would learn the address and book the rest by itself.“
    The person links one send to the exchange's deposit with the box ticked; the send that
    waited since then is linked and confirmed by code in the same request, its deposit
    re-booked from a possible bonus to a transfer in, the fee kept on the sender."""
    _, first, _ = seed.send("0.5")
    first_deposit = seed.deposit(
        "0.5", at=T0 + timedelta(minutes=20), kind="internal_transfer_in", status="confirmed"
    )
    group, waiting, fee = seed.send("0.25", at=LATER)
    bonus = seed.deposit("0.25", at=LATER + timedelta(hours=1))

    link = _pick(run, seed.ws, first, first_deposit, remember_deposit_address=True)

    assert link.learned == "learned"
    [row] = _learned_rows(read, seed.ws)
    assert (row.account_id, row.chain, bytes(row.address_hmac)) == (
        seed.exchange,
        "ethereum",
        DEPOSIT,
    )
    assert row.address_masked == MASKED and row.learned_from == first and row.ended_at is None
    [learned_event] = [t for t, _ in _events(read, seed.ws, seed.exchange)]
    assert learned_event == "account.deposit_address_learned"
    payload = _events(read, seed.ws, seed.exchange)[0].payload
    assert payload["address_masked"] == MASKED and DEPOSIT.hex() not in str(payload)

    code = [lk for lk in _links(read, seed.ws) if lk.matched_by == TransferMatchedBy.MATCHER.value]
    assert len(code) == 1
    assert _reason(read, seed.ws, code[0].id) == "deposit_address"
    sent, got = _live(read, seed.ws, waiting), _live(read, seed.ws, bonus)
    assert (code[0].out_movement_id, code[0].in_movement_id) == (sent.id, got.id)
    assert (sent.kind, sent.status) == ("internal_transfer_out", "confirmed")
    assert (got.kind, got.status) == ("internal_transfer_in", "confirmed")
    assert got.id != bonus and got.movement_hash is None  # a re-import finds the original's
    assert _group_status(read, seed.ws, group) == "confirmed"
    fee_row = _live(read, seed.ws, fee)
    assert (fee_row.kind, fee_row.status, fee_row.account_id) == ("fee", "confirmed", seed.wallet)
    assert _open(read, seed.ws) == []


def test_a_later_send_is_linked_by_the_next_relink(seed, run, read):
    seed.learn_deposit()
    _, out, _ = seed.send("1", leg_kind="internal_transfer_out", kind="exchange_transfer")
    deposit = seed.deposit("1", at=T0 + timedelta(hours=47))

    result = run(seed.ws, chain_transfers.relink)

    assert (result.linked, result.confirmed) == (1, 3)  # the send, its fee, the deposit
    [link] = _links(read, seed.ws)
    assert link.out_movement_id == out
    assert link.in_movement_id == _live(read, seed.ws, deposit).id
    assert run(seed.ws, chain_transfers.relink).linked == 0  # idempotent


@pytest.mark.parametrize(
    "deposit_at",
    [T0 - timedelta(minutes=1), T0 + timedelta(hours=48, minutes=1)],
    ids=["before_the_send", "after_the_window"],
)
def test_a_deposit_outside_the_window_is_not_linked(seed, run, read, deposit_at):
    seed.learn_deposit()
    seed.send("1")
    seed.deposit("1", at=deposit_at)
    assert run(seed.ws, chain_transfers.relink).linked == 0
    assert _links(read, seed.ws) == []


def test_units_off_by_more_than_the_dust_are_not_linked(seed, run, read):
    seed.learn_deposit()
    seed.send("1")
    seed.deposit("0.999999999999999998", at=T0 + timedelta(hours=1))  # two units of dust off
    assert run(seed.ws, chain_transfers.relink).linked == 0


def test_units_within_the_dust_are_linked(seed, run, read):
    seed.learn_deposit()
    seed.send("1")
    seed.deposit("0.999999999999999999", at=T0 + timedelta(hours=1))
    assert run(seed.ws, chain_transfers.relink).linked == 1


def test_two_deposits_that_fit_link_nothing(seed, run, read):
    seed.learn_deposit()
    seed.send("1")
    seed.deposit("1", at=T0 + timedelta(hours=1))
    seed.deposit("1", at=T0 + timedelta(hours=2))
    assert run(seed.ws, chain_transfers.relink).linked == 0


def test_two_sends_that_fit_one_deposit_link_nothing(seed, run, read):
    seed.learn_deposit()
    seed.send("1")
    seed.send("1", at=T0 + timedelta(minutes=5))
    seed.deposit("1", at=T0 + timedelta(hours=1))
    assert run(seed.ws, chain_transfers.relink).linked == 0


def test_an_address_learned_for_exchange_a_never_links_a_deposit_on_b(seed, run, read):
    seed.learn_deposit(exchange=seed.exchange)
    seed.send("1")
    seed.deposit("1", at=T0 + timedelta(hours=1), exchange=seed.exchange_b)
    assert run(seed.ws, chain_transfers.relink).linked == 0


def test_a_forgotten_address_links_nothing_and_its_links_stay(seed, run, read):
    learned_id = seed.learn_deposit()
    seed.send("1")
    seed.deposit("1", at=T0 + timedelta(hours=1))
    assert run(seed.ws, chain_transfers.relink).linked == 1

    run(seed.ws, imports.forget_learned, seed.exchange, learned_id)
    assert _learned_rows(read, seed.ws)[0].ended_at is not None
    assert len(_links(read, seed.ws)) == 1  # the link stays
    with pytest.raises(ConflictError, match="Schon vergessen"):
        run(seed.ws, imports.forget_learned, seed.exchange, learned_id)
    with pytest.raises(NotFoundError):
        run(seed.ws, imports.forget_learned, seed.exchange_b, learned_id)

    seed.send("2", at=LATER)
    seed.deposit("2", at=LATER + timedelta(hours=1))
    assert run(seed.ws, chain_transfers.relink).linked == 0
    types = [t for t, _ in _events(read, seed.ws, seed.exchange)]
    assert types == ["account.deposit_address_forgotten"]


def test_a_bonus_the_person_confirmed_is_never_rebooked(seed, run, read):
    seed.learn_deposit()
    seed.send("1")
    seed.deposit("1", at=T0 + timedelta(hours=1), status="confirmed")
    assert run(seed.ws, chain_transfers.relink).linked == 0


def test_a_first_sync_send_to_a_learned_address_is_linked_too(seed, run, read):
    from hausbuch.rules.review import CHAIN_FIRST_SYNC

    seed.learn_deposit()
    seed.send("1", reason=CHAIN_FIRST_SYNC)
    seed.deposit("1", at=T0 + timedelta(hours=1))
    assert run(seed.ws, chain_transfers.relink).linked == 1


def test_the_exchange_page_lists_and_forgets_its_learned_address(seed, run, read):
    learned_id = seed.learn_deposit()
    overview = read(seed.ws, imports.exchange_overview, seed.exchange)
    [row] = overview.learned
    assert (row.id, row.kind, row.chain, row.address_masked) == (
        learned_id,
        "deposit_address",
        "ethereum",
        MASKED,
    )
    assert read(seed.ws, imports.exchange_overview, seed.exchange_b).learned == []
    run(seed.ws, imports.forget_learned, seed.exchange, learned_id)
    assert read(seed.ws, imports.exchange_overview, seed.exchange).learned == []


# --- what is never learned --------------------------------------------------------------------


def test_an_own_address_is_never_learned_and_the_link_stands(seed, run, read):
    own = read(
        seed.ws,
        lambda conn: conn.execute(
            select(learned.address_table.c.address_hmac).where(
                learned.address_table.c.id == seed.address_b
            )
        ).scalar_one(),
    )
    _, out, _ = seed.send("1", hmac=bytes(own))
    deposit = seed.deposit(
        "1", at=T0 + timedelta(hours=1), kind="internal_transfer_in", status="confirmed"
    )
    link = _pick(run, seed.ws, out, deposit, remember_deposit_address=True)
    assert link.learned == "own_address"
    assert _learned_rows(read, seed.ws) == []
    assert len(_links(read, seed.ws)) == 1


def test_a_send_paying_several_outputs_teaches_nothing_and_the_link_stands(seed, run, read):
    _, out, _ = seed.send("1", hmac=None)  # the sync names no single counterparty
    deposit = seed.deposit(
        "1", at=T0 + timedelta(hours=1), kind="internal_transfer_in", status="confirmed"
    )
    link = _pick(run, seed.ws, out, deposit, remember_deposit_address=True)
    assert link.learned == "no_counterparty"
    assert _learned_rows(read, seed.ws) == []
    assert len(_links(read, seed.ws)) == 1


def test_an_address_of_another_exchange_is_refused_with_a_plain_error(seed, run, read):
    seed.learn_deposit(exchange=seed.exchange_b)
    _, out, _ = seed.send("1", at=LATER)
    deposit = seed.deposit(
        "1", at=LATER + timedelta(hours=1), kind="internal_transfer_in", status="confirmed"
    )
    with pytest.raises(ConflictError, match="einer anderen Börse"):
        _pick(run, seed.ws, out, deposit, remember_deposit_address=True)
    assert [r.account_id for r in _learned_rows(read, seed.ws)] == [seed.exchange_b]
    # Nothing was written, the link neither: unticked, the link alone is written.
    assert _links(read, seed.ws) == []
    assert _pick(run, seed.ws, out, deposit).learned is None


def test_a_box_that_does_not_fit_the_pair_is_ignored(seed, run, read):
    """The withdrawal box on a deposit link (and the other way round) learns nothing."""
    _, out, _ = seed.send("1")
    deposit = seed.deposit(
        "1", at=T0 + timedelta(hours=1), kind="internal_transfer_in", status="confirmed"
    )
    assert _pick(run, seed.ws, out, deposit, remember_withdrawal_pair=True).learned is None
    assert _learned_rows(read, seed.ws, pair_table) == []


def test_a_balance_only_chain_is_never_learned(seed, run, read):
    """BNB Smart Chain is read by balance only (D181 (c)): no send is read there, so none is
    learned (the service refuses a hand-made one too)."""
    with _admin(seed.dbname) as conn:
        bnb = _insert(
            conn,
            "asset",
            {
                "workspace_id": seed.ws,
                "kind": "crypto",
                "symbol": "BNB",
                "name": "BNB",
                "chain": "bsc",
                "decimals": 18,
            },
        )
    _, out, _ = seed.send("1", chain="bsc", asset=bnb)
    assert run(seed.ws, learned.learn_deposit_address, seed.exchange, out) == "balance_only"
    assert _learned_rows(read, seed.ws) == []


def test_another_workspace_neither_sees_nor_uses_a_learned_address(
    dbname, fresh_workspaces, run, read
):
    a = LearnSeed(dbname, fresh_workspaces[0])
    b = LearnSeed(dbname, fresh_workspaces[1])
    a.learn_deposit()
    b.send("1")
    b.deposit("1", at=T0 + timedelta(hours=1))
    assert _learned_rows(read, b.ws) == []
    assert run(b.ws, chain_transfers.relink).linked == 0


# --- the answer path ---------------------------------------------------------------------------


def test_answering_the_deposit_as_a_transfer_learns_from_the_one_fitting_send(seed, run, read):
    _, out, _ = seed.send("1")
    bonus = seed.deposit("1", at=T0 + timedelta(hours=1))
    body = AnswerMovementBody(kind=MovementKind.INTERNAL_TRANSFER_IN, remember_deposit_address=True)

    answered = run(seed.ws, imports.answer_movement, bonus, body)

    assert answered.learned == "learned"
    [row] = _learned_rows(read, seed.ws)
    assert row.learned_from == out
    [link] = _links(read, seed.ws)  # the relink after the answer linked the pair
    assert (link.out_movement_id, link.in_movement_id) == (
        _live(read, seed.ws, out).id,
        _live(read, seed.ws, answered.movement_id).id,
    )
    assert _reason(read, seed.ws, link.id) == "deposit_address"


def test_answering_with_two_fitting_sends_learns_nothing(seed, run, read):
    seed.send("1")
    seed.send("1", at=T0 + timedelta(minutes=5))
    bonus = seed.deposit("1", at=T0 + timedelta(hours=1))
    body = AnswerMovementBody(kind=MovementKind.INTERNAL_TRANSFER_IN, remember_deposit_address=True)
    assert run(seed.ws, imports.answer_movement, bonus, body).learned == "no_counterparty"
    assert _learned_rows(read, seed.ws) == []


# --- (3) a learned withdrawal pair -------------------------------------------------------------


def test_a_persons_link_learns_the_withdrawal_pair_and_code_links_the_next(seed, run, read):
    first = seed.exchange_out("1", fee="0.001")
    _, [first_in] = seed.receipt("0.999", at=T0 + timedelta(minutes=30))
    link = _pick(run, seed.ws, first, first_in, remember_withdrawal_pair=True)
    assert link.learned == "learned"
    [row] = _learned_rows(read, seed.ws, pair_table)
    assert (row.account_id, row.address_id, row.learned_from) == (
        seed.exchange,
        seed.address,
        first,
    )

    out = seed.exchange_out("2", fee="0.002", at=LATER, status="proposed")
    group, [receipt] = seed.receipt("1.998", at=LATER + timedelta(hours=23))
    result = run(seed.ws, chain_transfers.relink)

    assert result.linked == 1
    code = [lk for lk in _links(read, seed.ws) if lk.matched_by == TransferMatchedBy.MATCHER.value]
    [code] = code
    got = _live(read, seed.ws, receipt)
    assert (code.out_movement_id, code.in_movement_id) == (out, got.id)
    assert (got.kind, got.status) == ("internal_transfer_in", "confirmed")
    assert _live(read, seed.ws, out).status == "confirmed"
    assert _group_status(read, seed.ws, group) == "confirmed"
    assert _reason(read, seed.ws, code.id) == "withdrawal_pair"
    overview = read(seed.ws, imports.exchange_overview, seed.exchange)
    [listed] = overview.learned
    assert (listed.kind, listed.wallet_label) == ("withdrawal_pair", "Hardware-Wallet")


def _pair(seed):
    with _admin(seed.dbname) as conn:
        _insert(
            conn,
            "exchange_withdrawal_pair",
            {
                "workspace_id": seed.ws,
                "account_id": seed.exchange,
                "address_id": seed.address,
                "learned_from": seed.exchange_out("9", at=T0 - timedelta(days=30)),
            },
        )


@pytest.mark.parametrize(
    "receipt_at",
    [T0 - timedelta(minutes=1), T0 + timedelta(hours=24, minutes=1)],
    ids=["before_the_withdrawal", "after_24_hours"],
)
def test_a_receipt_outside_24_hours_is_not_linked(seed, run, read, receipt_at):
    _pair(seed)
    seed.exchange_out("1", fee="0.001")
    seed.receipt("0.999", at=receipt_at)
    assert run(seed.ws, chain_transfers.relink).linked == 0


def test_a_receipt_on_another_address_is_not_linked(seed, run, read):
    _pair(seed)
    seed.exchange_out("1", fee="0.001")
    seed.receipt("0.999", at=T0 + timedelta(hours=1), address=seed.address_b, account=seed.wallet_b)
    assert run(seed.ws, chain_transfers.relink).linked == 0


def test_a_receipt_of_other_units_is_not_linked(seed, run, read):
    _pair(seed)
    seed.exchange_out("1", fee="0.001")
    seed.receipt("1", at=T0 + timedelta(hours=1))  # the fee was not taken from it
    assert run(seed.ws, chain_transfers.relink).linked == 0


def test_two_receipts_that_fit_link_nothing(seed, run, read):
    _pair(seed)
    seed.exchange_out("1", fee="0.001")
    seed.receipt("0.999", at=T0 + timedelta(hours=1))
    seed.receipt("0.999", at=T0 + timedelta(hours=2))
    assert run(seed.ws, chain_transfers.relink).linked == 0


def test_two_withdrawals_that_fit_one_receipt_link_nothing(seed, run, read):
    _pair(seed)
    seed.exchange_out("1", fee="0.001")
    seed.exchange_out("1", fee="0.001", at=T0 + timedelta(minutes=10))
    seed.receipt("0.999", at=T0 + timedelta(hours=1))
    assert run(seed.ws, chain_transfers.relink).linked == 0


# --- (5) undo -----------------------------------------------------------------------------------


def test_replacing_a_learned_link_reopens_the_dropped_leg_and_keeps_the_address(seed, run, read):
    """The person replaces the code's link (unlink with another pick): the dropped deposit
    goes back to proposed as a hash link's would, the address stays learned."""
    seed.learn_deposit()
    _, out, _ = seed.send("1")
    bonus = seed.deposit("1", at=T0 + timedelta(hours=1))
    assert run(seed.ws, chain_transfers.relink).linked == 1
    [code] = _links(read, seed.ws)
    other = seed.deposit(
        "1", at=T0 + timedelta(hours=2), kind="internal_transfer_in", status="confirmed"
    )
    body = TransferLinkCreate(
        kind=TransferLinkKind.CHAIN,
        out_movement_id=_live(read, seed.ws, out).id,
        in_movement_id=other,
    )
    run(seed.ws, transfers.unlink, code.id, body)

    result = run(seed.ws, chain_transfers.relink)
    assert result.reopened == 1 and result.linked == 0
    dropped = _live(read, seed.ws, bonus)
    assert (dropped.kind, dropped.status) == ("internal_transfer_in", "proposed")
    assert _learned_rows(read, seed.ws)[0].ended_at is None
