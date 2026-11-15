"""A wallet's addresses (docs/phases/phase-7.md D179, §4, §13; step 1).

- A wallet is an account of kind `wallet` (create_account, D179 (1)); its addresses are
  `wallet_address` rows, one per chain it is read on.
- Adding an address: rules/chain/address.validate (the normalised form, or
  wallet.address_invalid naming the chain's shape), the mask, and the keyed HMAC under
  HAUSBUCH_IBAN_HMAC_KEY (address_hmac, domain-separated from the IBANs). An address on another
  own account is refused (wallet.address_taken), whichever chain it is on there: the HMAC is
  of the address alone, and one address is one key. „Auch auf Arbitrum und BNB Smart Chain
  lesen“ (`also_on`) writes the same 0x address on the other EVM chains in the one transaction,
  skipping a chain this wallet already reads it on.
- Relabel, retire (active = false; its movements stay) and reactivate: wallet.address_updated.
- „kopieren“ is the one read of the full address (reveal_address), audited as
  wallet.address_revealed and sent no-store by the route.

The plaintext address leaves `wallet_address` only through reveal_address. Every event carries
the masked form, the chain and the label, never the address or its HMAC, and no RuleError
message here repeats the input. The model never sees a wallet row (D179 (3)).
"""

import hashlib
from uuid import UUID

from sqlalchemy import Connection, Row, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import wallet_address as address_table
from hausbuch.rules.chain.address import EVM_CHAINS, address_hmac, mask, validate
from hausbuch.rules.errors import WALLET_ADDRESS_INVALID, WALLET_ADDRESS_TAKEN, RuleError
from hausbuch.schemas.chain import AddressKind, Chain, WalletAddress
from hausbuch.schemas.enums import AccountKind, EntityType
from hausbuch.schemas.events import (
    EventType,
    WalletAddressAdded,
    WalletAddressRevealed,
    WalletAddressUpdated,
)
from hausbuch.schemas.wallets import WalletAddressBody, WalletAddressPlain, WalletPage

from .accounts import get_account
from .context import CommandContext
from .errors import NotFoundError, violated_constraint
from .events import append_event, diff_changes
from .owners import UNSET, Unset

# Every column a read shape may carry: the address itself and its HMAC never leave.
_READ_COLUMNS = [c for c in address_table.c if c.name not in {"address", "address_hmac"}]
_TAKEN = "wallet_address_workspace_id_chain_address_hmac_key"
_CHAIN_ORDER = {chain: i for i, chain in enumerate(Chain)}
_LOCK_DOMAIN = b"wallet_address_lock\x00"

_TAKEN_ELSEWHERE = "Diese Adresse ist schon auf einem anderen eigenen Konto eingetragen."
_TAKEN_HERE = "Diese Adresse ist auf dieser Wallet für diese Chain schon eingetragen."
_ALSO_ON_EVM_ONLY = (
    "Nur eine 0x-Adresse (Ethereum, Arbitrum, BNB Smart Chain) lässt sich zusätzlich auf "
    "weiteren Chains lesen."
)


def wallet_page(conn: Connection, account_id: UUID) -> WalletPage | None:
    """GET /wallets/{id}: None when there is no such account or it is no wallet. Balances,
    syncs and each address's last_sync stay empty until step 2 reads a chain."""
    account = get_account(conn, account_id)
    if account is None or account.kind is not AccountKind.WALLET:
        return None
    rows = conn.execute(
        select(*_READ_COLUMNS).where(address_table.c.account_id == account_id)
    ).all()
    addresses = sorted(
        (_to_address(row) for row in rows),
        key=lambda a: (not a.active, _CHAIN_ORDER[a.chain]),
    )
    return WalletPage(account=account, addresses=addresses)


def add_address(
    ctx: CommandContext, account_id: UUID, body: WalletAddressBody, *, key: bytes
) -> list[WalletAddress]:
    """POST /wallets/{id}/addresses: the rows written, the body's chain first, then `also_on`
    in registry order. A chain this wallet already reads the address on is skipped; when every
    chain is, wallet.address_taken (reactivate a retired row through PATCH instead)."""
    _lock_wallet(ctx.conn, account_id)
    normalised = validate(body.chain, body.address)
    extra = {chain for chain in body.also_on if chain is not body.chain}
    if extra and not (body.chain in EVM_CHAINS and extra <= EVM_CHAINS):
        raise RuleError(WALLET_ADDRESS_INVALID, _ALSO_ON_EVM_ONLY)
    chains = [body.chain, *sorted(extra, key=_CHAIN_ORDER.__getitem__)]
    digest = address_hmac(normalised, key)
    _lock_address(ctx, digest)
    known = ctx.conn.execute(
        select(address_table.c.account_id, address_table.c.chain).where(
            address_table.c.address_hmac == digest
        )
    ).all()
    if any(row.account_id != account_id for row in known):
        raise RuleError(WALLET_ADDRESS_TAKEN, _TAKEN_ELSEWHERE)
    here = {Chain(row.chain) for row in known}
    chains = [chain for chain in chains if chain not in here]
    if not chains:
        raise RuleError(WALLET_ADDRESS_TAKEN, _TAKEN_HERE)
    masked = mask(body.chain, normalised)
    created = []
    for chain in chains:
        try:
            row = ctx.conn.execute(
                insert(address_table)
                .values(
                    workspace_id=ctx.workspace_id,
                    account_id=account_id,
                    chain=chain.value,
                    address=normalised,
                    address_masked=masked,
                    address_hmac=digest,
                    label=body.label,
                    kind=AddressKind.RECEIVE.value,
                    created_at=ctx.now,
                )
                .returning(*_READ_COLUMNS)
            ).one()
        except IntegrityError as exc:
            # A concurrent add of the same address to another wallet; the DBAPI text names the
            # values, so it is never chained.
            if violated_constraint(exc) == _TAKEN:
                raise RuleError(WALLET_ADDRESS_TAKEN, _TAKEN_ELSEWHERE) from None
            raise
        address = _to_address(row)
        append_event(
            ctx,
            EventType.WALLET_ADDRESS_ADDED,
            account_id,
            WalletAddressAdded(
                address_id=address.id,
                chain=address.chain,
                address_masked=address.address_masked,
                label=address.label,
                kind=address.kind,
            ),
        )
        created.append(address)
    enqueue_first_sync(ctx, [address.id for address in created])
    return created


def enqueue_first_sync(ctx: CommandContext, address_ids: list[UUID]) -> None:
    """The hook where an added address gets its first sync (D182). Step 1 reads no chain, so
    this enqueues nothing yet; phase 7 step 2 fills it with the sync job of each address, in
    the caller's transaction."""
    del ctx, address_ids


def update_address(
    ctx: CommandContext,
    account_id: UUID,
    address_id: UUID,
    *,
    label: str | None | Unset = UNSET,
    active: bool | Unset = UNSET,
) -> WalletAddress:
    """PATCH /wallets/{id}/addresses/{aid}: only the fields passed; no change records nothing.
    Retiring keeps the row and its movements; a retired address is not synced."""
    current = _to_address(_address_row(ctx.conn, account_id, address_id, for_update=True))
    requested = {
        name: value for name, value in (("label", label), ("active", active)) if value is not UNSET
    }
    changes = diff_changes(current.model_dump(include=set(requested)), requested)
    if not changes:
        return current
    row = ctx.conn.execute(
        update(address_table)
        .where(address_table.c.id == address_id)
        .values(**{name: requested[name] for name in changes})
        .returning(*_READ_COLUMNS)
    ).one()
    updated = _to_address(row)
    append_event(
        ctx,
        EventType.WALLET_ADDRESS_UPDATED,
        account_id,
        WalletAddressUpdated(
            address_id=updated.id,
            chain=updated.chain,
            address_masked=updated.address_masked,
            changes=changes,
        ),
    )
    return updated


def reveal_address(ctx: CommandContext, account_id: UUID, address_id: UUID) -> WalletAddressPlain:
    """GET /wallets/{id}/addresses/{aid}/plain, „kopieren“: the full address, audited."""
    row = _address_row(ctx.conn, account_id, address_id, plain=True)
    append_event(
        ctx,
        EventType.WALLET_ADDRESS_REVEALED,
        account_id,
        WalletAddressRevealed(
            address_id=row.id, chain=Chain(row.chain), address_masked=row.address_masked
        ),
    )
    return WalletAddressPlain(id=row.id, chain=Chain(row.chain), address=row.address)


def _lock_wallet(conn: Connection, account_id: UUID) -> None:
    """The wallet account, locked: adds to one wallet run one after the other. 404 when there
    is no such account or it is no wallet."""
    kind = conn.execute(
        select(account_table.c.kind).where(account_table.c.id == account_id).with_for_update()
    ).scalar_one_or_none()
    if kind != AccountKind.WALLET.value:
        raise NotFoundError(EntityType.WALLET, account_id)


def _lock_address(ctx: CommandContext, digest: bytes) -> None:
    """pg_advisory_xact_lock on the address (workspace and HMAC), released at commit or
    rollback (review of PR #374, 16 Nov 2026). The wallet lock serialises adds to one wallet
    only, and the unique key is per chain, so two adds of one 0x address to two wallets on two
    chains both committed. One lock covers every chain of an add (`also_on` included): the
    HMAC is of the address alone, so the rows of one add share one key. Taken after the
    wallet's row lock and before the „taken elsewhere“ read, which then sees the other add's
    committed row (READ COMMITTED). The unique key stays the backstop.

    The key is a signed 64-bit hash of workspace and HMAC computed here, so neither the HMAC
    nor a text holding it is a bind parameter a lock timeout's error could print."""
    material = _LOCK_DOMAIN + ctx.workspace_id.bytes + digest
    lock_key = int.from_bytes(hashlib.sha256(material).digest()[:8], "big", signed=True)
    ctx.conn.execute(select(func.pg_advisory_xact_lock(lock_key)))


def _address_row(
    conn: Connection,
    account_id: UUID,
    address_id: UUID,
    *,
    for_update: bool = False,
    plain: bool = False,
) -> Row:
    columns = [*_READ_COLUMNS, address_table.c.address] if plain else _READ_COLUMNS
    query = select(*columns).where(
        address_table.c.id == address_id, address_table.c.account_id == account_id
    )
    if for_update:
        query = query.with_for_update()
    row = conn.execute(query).one_or_none()
    if row is None:
        raise NotFoundError(EntityType.WALLET_ADDRESS, address_id)
    return row


def _to_address(row: Row) -> WalletAddress:
    return WalletAddress(
        id=row.id,
        account_id=row.account_id,
        chain=Chain(row.chain),
        address_masked=row.address_masked,
        label=row.label,
        kind=AddressKind(row.kind),
        active=row.active,
    )
