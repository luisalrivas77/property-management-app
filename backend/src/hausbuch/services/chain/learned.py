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

from sqlalchemy import Connection, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

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
    if current is None:
        # Two learns of one address at once (two people, two tabs): the live index lets one
        # insert; the other writes nothing and re-reads who holds the address, so it answers
        # "already" or the plain 409 below, never a unique violation (review fixes 16 Nov 2026)
        d = deposit_table.c
        row_id = ctx.conn.execute(
            pg_insert(deposit_table)
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
            .on_conflict_do_nothing(
                index_elements=[d.workspace_id, d.chain, d.address_hmac],
                index_where=d.ended_at.is_(None),
            )
            .returning(d.id)
        ).scalar_one_or_none()
        if row_id is None:
            current = deposit_exchange(ctx.conn, chain, hmac)
    else:
        row_id = None
    if row_id is None:
        if current == exchange_id:
            return "already"
        raise ConflictError(
            "Diese Adresse ist schon als Einzahlungsadresse einer anderen Börse gemerkt."
        )
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
    p = pair_table.c
    row_id = ctx.conn.execute(
        pg_insert(pair_table)
        .values(
            workspace_id=ctx.workspace_id,
            account_id=exchange_id,
            address_id=address_id,
            learned_from=withdrawal_id,
            set_by=user_id(ctx),
            created_at=ctx.now,
        )
        .on_conflict_do_nothing(  # learned at the same moment elsewhere: the same pair
            index_elements=[p.workspace_id, p.account_id, p.address_id],
            index_where=p.ended_at.is_(None),
        )
        .returning(p.id)
    ).scalar_one_or_none()
    if row_id is None:
        return "already"
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


def _end(ctx: CommandContext, table, row, exchange_id: UUID, unlinked: UUID | None = None) -> None:  # noqa: ANN001 - a learned table and its locked row
    """End one live learned row with its account.*_forgotten event; `unlinked` the link whose
    „Verknüpfung lösen“ ended it."""
    c = table.c
    ctx.conn.execute(
        update(table).where(c.id == row.id).values(ended_at=ctx.now, ended_by=user_id(ctx))
    )
    if table is deposit_table:
        append_event(
            ctx,
            EventType.ACCOUNT_DEPOSIT_ADDRESS_FORGOTTEN,
            exchange_id,
            ExchangeDepositAddressForgotten(
                learned_id=row.id,
                chain=Chain(row.chain),
                address_masked=row.address_masked,
                unlinked_link_id=unlinked,
            ),
        )
    else:
        append_event(
            ctx,
            EventType.ACCOUNT_WITHDRAWAL_PAIR_FORGOTTEN,
            exchange_id,
            ExchangeWithdrawalPairForgotten(
                learned_id=row.id, address_id=row.address_id, unlinked_link_id=unlinked
            ),
        )


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
        _end(ctx, table, row, exchange_id)
        return
    raise NotFoundError("learned address", learned_id)


def forget_for_link(
    ctx: CommandContext,
    reason: Literal["deposit_address", "withdrawal_pair"],
    out_id: UUID,
    in_id: UUID,
    link_id: UUID,
) -> UUID | None:
    """„Verknüpfung lösen“ on a link code wrote from a learned row (review fixes 16 Nov 2026):
    the live row that produced it ends with it, its event naming the link, so the next relink
    cannot write the pair again. A deposit address: the exchange of the deposit (`in_id`), the
    chain and counterparty of the wallet send (`out_id`). A withdrawal pair: the exchange of the
    withdrawal (`out_id`) and the own address of the receipt (`in_id`). The row's id, or None
    when it was forgotten already."""
    m, g = movement_table.c, group_table.c
    legs = {
        r.id: r
        for r in ctx.conn.execute(
            select(m.id, m.account_id, m.address_id, m.counterparty_hmac, g.chain)
            .select_from(movement_table.outerjoin(group_table, m.group_id == g.id))
            .where(m.id.in_([out_id, in_id]))
        )
    }
    out, in_ = legs[out_id], legs[in_id]
    if reason == "deposit_address":
        if out.counterparty_hmac is None or out.chain is None:
            return None
        exchange_id, table, c = in_.account_id, deposit_table, deposit_table.c
        where = (
            c.account_id == exchange_id,
            c.chain == out.chain,
            c.address_hmac == bytes(out.counterparty_hmac),
        )
    else:
        if in_.address_id is None:
            return None
        exchange_id, table, c = out.account_id, pair_table, pair_table.c
        where = (c.account_id == exchange_id, c.address_id == in_.address_id)
    row = ctx.conn.execute(
        select(table).where(*where, c.ended_at.is_(None)).with_for_update()
    ).one_or_none()
    if row is None:
        return None
    _end(ctx, table, row, exchange_id, unlinked=link_id)
    return row.id
