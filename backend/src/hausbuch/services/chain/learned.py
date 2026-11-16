"""What a person's link taught the app about an exchange (docs/phases/phase-7.md D261).

Luis, 16 Nov 2026: „Do I need to link every transaction between the exchange and my wallet? I
would prefer if the app would learn the address and book the rest by itself.“

- **A deposit address** (`exchange_deposit_address`): when a person links a wallet's plain
  send to an exchange's crypto deposit, the address the send paid (stored on the send as
  `asset_movement.counterparty_hmac`, never in clear) becomes that exchange's deposit address
  on the chain. A later send to it is classified `exchange_transfer` with an
  `internal_transfer_out` leg (`ClassifyContext.exchange_deposit`), and the linker
  (services.chain.transfers) pairs it with that exchange's deposit and confirms both.
- **A withdrawal pair** (`exchange_withdrawal_pair`): when a person links an exchange's
  withdrawal to a receipt on an own wallet address, the pair (exchange, address) lets the
  linker link the next withdrawal to that address by quantity, for an exchange whose export
  carries no hash (BISON). Never from the chain's sender address: an exchange's hot wallets
  are shared by all its customers, as phase 6's shared IBAN.

Never learned: an address that is an own wallet address; an address already learned for
another exchange (refused with a plain error); a send that names no single counterparty (a
Bitcoin spend paying several outputs that are no own address): the link stands, nothing is
learned, and the outcome says why. A learned row ends once with „Vergessen“; the links already
written stay. Every event carries ids and the masked address only (D179).

This module holds the readers the sync needs (no command imports) and the commands that learn
and forget; the link itself is services.chain.transfers'.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import Connection, insert, select, update

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import exchange_deposit_address as deposit_table
from hausbuch.db.tables import exchange_withdrawal_pair as pair_table
from hausbuch.db.tables import movement_group as group_table
from hausbuch.db.tables import wallet_address as address_table
from hausbuch.rules.chain.address import address_hmac, mask, validate
from hausbuch.rules.chain.registry import registry
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.chain import Chain, ChainSourceKind
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.events import (
    EventType,
    ExchangeDepositAddressForgotten,
    ExchangeDepositAddressLearned,
    ExchangeWithdrawalPairForgotten,
    ExchangeWithdrawalPairLearned,
)
from hausbuch.schemas.household import LearnOutcome

from ..allocations import user_id
from ..context import CommandContext
from ..errors import ConflictError, NotFoundError
from ..events import append_event

# --- Readers (the sync's) -------------------------------------------------------------------


def counterparty(chain: Chain, address: str, key: bytes) -> tuple[bytes, str] | None:
    """(HMAC, masked) of a send's counterparty, normalised as wallet_address stores an own
    address (EVM lower-case), so a learned row and a later send compare equal; None for a
    string that is no address of the chain."""
    try:
        normalised = validate(chain, address)
    except RuleError:
        return None
    return address_hmac(normalised, key), mask(chain, normalised)


def deposit_predicate(conn: Connection, chain: Chain, key: bytes) -> Callable[[str], bool] | None:
    """`ClassifyContext.exchange_deposit` for one chain: whether an address is a live learned
    deposit address of an exchange of the workspace; None when none is learned on the chain."""
    d = deposit_table.c
    hmacs = frozenset(
        bytes(h)
        for h in conn.execute(
            select(d.address_hmac).where(d.chain == chain.value, d.ended_at.is_(None))
        ).scalars()
    )
    if not hmacs:
        return None

    def learned(address: str) -> bool:
        found = counterparty(chain, address, key)
        return found is not None and found[0] in hmacs

    return learned


def deposit_exchange(conn: Connection, chain: str, hmac: bytes) -> UUID | None:
    """The exchange account a live learned deposit address belongs to, or None."""
    d = deposit_table.c
    return conn.execute(
        select(d.account_id).where(d.chain == chain, d.address_hmac == hmac, d.ended_at.is_(None))
    ).scalar_one_or_none()


def deposit_addresses(conn: Connection) -> dict[tuple[str, bytes], UUID]:
    """The linker's view: every live learned deposit address, (chain, HMAC) → exchange."""
    d = deposit_table.c
    return {
        (r.chain, bytes(r.address_hmac)): r.account_id
        for r in conn.execute(
            select(d.chain, d.address_hmac, d.account_id).where(d.ended_at.is_(None))
        )
    }


def withdrawal_pairs(conn: Connection) -> set[tuple[UUID, UUID]]:
    """The linker's view: every live learned withdrawal pair, (exchange, own address)."""
    p = pair_table.c
    return {
        (r.account_id, r.address_id)
        for r in conn.execute(select(p.account_id, p.address_id).where(p.ended_at.is_(None)))
    }


def paired(conn: Connection, exchange_id: UUID, address_id: UUID) -> bool:
    """Whether the exchange's withdrawals to the own address are learned (a live pair)."""
    p = pair_table.c
    return (
        conn.execute(
            select(p.id).where(
                p.account_id == exchange_id, p.address_id == address_id, p.ended_at.is_(None)
            )
        ).first()
        is not None
    )


# --- Learning ---------------------------------------------------------------------------------


def _own_hmac(conn: Connection, chain: str, hmac: bytes) -> bool:
    a = address_table.c
    return (
        conn.execute(select(a.id).where(a.chain == chain, a.address_hmac == hmac).limit(1)).first()
        is not None
    )


def learn_deposit_address(ctx: CommandContext, exchange_id: UUID, send_id: UUID) -> LearnOutcome:
    """Learn the address the wallet send `send_id` paid as the exchange's deposit address on
    its chain (D261 (2)). "learned" when stored; else why nothing was (the caller's
    link stands). Raises ConflictError when the address is a live deposit address of another
    exchange of the workspace."""
    m, g = movement_table.c, group_table.c
    send = ctx.conn.execute(
        select(m.counterparty_hmac, m.counterparty_masked, g.chain)
        .select_from(movement_table.join(group_table, m.group_id == g.id))
        .where(m.id == send_id)
    ).one_or_none()
    if send is None or send.counterparty_hmac is None:
        return "no_counterparty"
    chain = send.chain
    if registry().chain(Chain(chain)).source is ChainSourceKind.EVM_RPC_BALANCE:
        return "balance_only"
    hmac = bytes(send.counterparty_hmac)
    if _own_hmac(ctx.conn, chain, hmac):
        return "own_address"
    current = deposit_exchange(ctx.conn, chain, hmac)
    if current == exchange_id:
        return "already"
    if current is not None:
        raise ConflictError(
            "Diese Adresse ist schon als Einzahlungsadresse einer anderen Börse gemerkt."
        )
    row_id = ctx.conn.execute(
        insert(deposit_table)
        .values(
            workspace_id=ctx.workspace_id,
            account_id=exchange_id,
            chain=chain,
            address_hmac=hmac,
            address_masked=send.counterparty_masked,
            learned_from=send_id,
            set_by=user_id(ctx),
            created_at=ctx.now,
        )
        .returning(deposit_table.c.id)
    ).scalar_one()
    append_event(
        ctx,
        EventType.ACCOUNT_DEPOSIT_ADDRESS_LEARNED,
        exchange_id,
        ExchangeDepositAddressLearned(
            learned_id=row_id,
            chain=Chain(chain),
            address_masked=send.counterparty_masked,
            learned_from=send_id,
        ),
    )
    return "learned"


def learn_withdrawal_pair(
    ctx: CommandContext, exchange_id: UUID, address_id: UUID, withdrawal_id: UUID
) -> LearnOutcome:
    """Learn that the exchange's withdrawals arrive on the own address (D261 (3)). "learned" when
    learned, "already" when a live pair exists, "balance_only" on BNB Smart Chain."""
    a = address_table.c
    chain = ctx.conn.execute(select(a.chain).where(a.id == address_id)).scalar_one()
    if registry().chain(Chain(chain)).source is ChainSourceKind.EVM_RPC_BALANCE:
        return "balance_only"
    if paired(ctx.conn, exchange_id, address_id):
        return "already"
    row_id = ctx.conn.execute(
        insert(pair_table)
        .values(
            workspace_id=ctx.workspace_id,
            account_id=exchange_id,
            address_id=address_id,
            learned_from=withdrawal_id,
            set_by=user_id(ctx),
            created_at=ctx.now,
        )
        .returning(pair_table.c.id)
    ).scalar_one()
    append_event(
        ctx,
        EventType.ACCOUNT_WITHDRAWAL_PAIR_LEARNED,
        exchange_id,
        ExchangeWithdrawalPairLearned(
            learned_id=row_id, address_id=address_id, learned_from=withdrawal_id
        ),
    )
    return "learned"


# --- The exchange page ------------------------------------------------------------------------


@dataclass(frozen=True)
class LearnedRow:
    id: UUID
    kind: Literal["deposit_address", "withdrawal_pair"]
    chain: str
    address_masked: str
    wallet_label: str | None
    created_at: datetime


def learned_of(conn: Connection, exchange_id: UUID) -> list[LearnedRow]:
    """The exchange's live learned deposit addresses and withdrawal pairs, oldest first."""
    d, p, a, acc = deposit_table.c, pair_table.c, address_table.c, account_table.c
    out = [
        LearnedRow(r.id, "deposit_address", r.chain, r.address_masked, None, r.created_at)
        for r in conn.execute(
            select(d.id, d.chain, d.address_masked, d.created_at).where(
                d.account_id == exchange_id, d.ended_at.is_(None)
            )
        )
    ]
    out += [
        LearnedRow(r.id, "withdrawal_pair", r.chain, r.address_masked, r.label, r.created_at)
        for r in conn.execute(
            select(p.id, a.chain, a.address_masked, acc.label, p.created_at)
            .select_from(
                pair_table.join(address_table, p.address_id == a.id).join(
                    account_table, a.account_id == acc.id
                )
            )
            .where(p.account_id == exchange_id, p.ended_at.is_(None))
        )
    ]
    return sorted(out, key=lambda r: (r.created_at, str(r.id)))


def forget(ctx: CommandContext, exchange_id: UUID, learned_id: UUID) -> None:
    """„Vergessen“: end a live learned row of the exchange (a deposit address or a withdrawal
    pair); the links already written stay. 404 for a row of another exchange or none; 409
    when it was forgotten already."""
    kind = ctx.conn.execute(
        select(account_table.c.kind).where(account_table.c.id == exchange_id)
    ).scalar_one_or_none()
    if kind != AccountKind.EXCHANGE.value:
        raise NotFoundError("account", exchange_id)
    for table in (deposit_table, pair_table):
        c = table.c
        row = ctx.conn.execute(
            select(table).where(c.id == learned_id, c.account_id == exchange_id).with_for_update()
        ).one_or_none()
        if row is None:
            continue
        if row.ended_at is not None:
            raise ConflictError("Schon vergessen.")
        ctx.conn.execute(
            update(table).where(c.id == learned_id).values(ended_at=ctx.now, ended_by=user_id(ctx))
        )
        if table is deposit_table:
            append_event(
                ctx,
                EventType.ACCOUNT_DEPOSIT_ADDRESS_FORGOTTEN,
                exchange_id,
                ExchangeDepositAddressForgotten(
                    learned_id=learned_id,
                    chain=Chain(row.chain),
                    address_masked=row.address_masked,
                ),
            )
        else:
            append_event(
                ctx,
                EventType.ACCOUNT_WITHDRAWAL_PAIR_FORGOTTEN,
                exchange_id,
                ExchangeWithdrawalPairForgotten(learned_id=learned_id, address_id=row.address_id),
            )
        return
    raise NotFoundError("learned address", learned_id)
