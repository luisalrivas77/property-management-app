"""One wallet address read from its chain (docs/phases/phase-7.md D182, D186, D187, D192).

`sync_address` is one run of one address, in three steps so no database transaction stays
open while a provider answers:

1. *Start* (a short transaction): the address, its cursor (the last succeeded run's
   `cursor_after`), whether its history was ever read to the end, the context the family
   classifier needs (every own address on the chain, the account's own addresses with their
   ids, the native coin's asset, the workspace's decoders, the held tokens), the source
   (services/chain/base.source_for; an unset key ends here with `wallet.key_missing`, a chain
   whose source is not built yet with `wallet.source_unavailable`, and a budget the run has
   already spent with `wallet.sync_deferred`, each with no row), and the `wallet_sync` row as
   `running`.
2. *Read* (no transaction): the source's pages from the cursor until a page brings nothing
   new or the run's budget ends (`deferred`, the page note `wallet.sync_deferred`), each
   transaction classified by its family (rules/chain/classify); then, when the history is
   read to its end, the balances the source reconciles against (of the address, or of every
   own address of the account when the source's `reconcile_scope` is `account`). Deferred is
   decided by the budget, never by the cursor: a spent budget after any page, or after the
   balance calls, ends the run deferred with no balance.
3. *Write* (one transaction, a savepoint per group): each leg hashed (D182 (2)) with the
   HMAC of the own address it sits on, and only legs not stored yet written, joining the live
   group of the same transaction when another own address already wrote one (the joining
   legs routed on their own, so a first sync never confirms them); each new group routed by
   D186 (1) and written proposed or confirmed with its movements (a group with a question and
   no legs is written once, without movements); when at least one movement or
   CHAIN_UNSTORABLE group is new, the provider's records stored as the run's original
   (`document(subtype = chain_sync)`, NDJSON) with its `movement_import(format = chain_sync)`
   row (D182 (3)); Σ movements reconciled against the balances in base units (D186 (2)), or,
   for a source that declares `balance_only` (BNB Smart Chain, D181 (c)), the balance against
   the deposits linked to the address by rules/chain/evm.reconcile_balance_only; the row
   finished once.

One transaction never blocks the address's history: a spam leg whose quantity
numeric(38,18) cannot hold is left out (D187, counted as `spam_dropped`), and a non-spam
transaction Hausbuch cannot store (a quantity beyond the column, two legs of different assets
on one (address, index, role), a row the database refuses) becomes a group without movements
or question, reason `routing.chain_unstorable` (counted as `unstorable`); its difference
stays a mismatch. No quantity is ever rounded.

A failure ends its own address's row only (`failed`, `throttled` on a 429, `refused` on a
401 or 403) and writes nothing else: the cursor does not move, so the next run reads the same
range again. A balance read that fails after the history was read is such a failure when the
run stored nothing new (no movement, no question card, no original); only a run that stored
something keeps it and reads `succeeded` with no balance (below). A start that fails
otherwise (the address gone) ends that address only in `sync_wallets`, and the next address
runs. Nothing logged, raised or stored in an event holds an address, a cursor, a
transaction hash, a URL or a key: the logs carry the chain, the outcome, counts and the
exception class.

How a run's row reads (step 1's wallet page):

- `succeeded`, `balances` null: the budget ended first (`wallet.sync_deferred`); nothing was
  reconciled, since a half-read history cannot match the balance. Or the history was read to
  its end, the run stored something new, and the balance read failed (refused, throttled,
  failed): the new movements, cards and original are written all the same, the row is never
  reconciled (`reconciled` null, so the address's first sync is still ahead of it), the code
  is on the outcome, the log line and the wallet.sync_completed event (`error_code`,
  `deferred` false), not on the row (its CHECK keeps `error_code` to failed rows), so the
  wallet page reads it as deferred; the next run, starting at the tip, reads the balance
  again (a follow-up of the EVM wiring's review). When the run stored nothing new, the row is
  `failed`, `throttled` or `refused` with the code and the cursor stays (review of PR #397).
  Telling a run that brings new movements every day while its balance keeps failing from a
  deferred one needs a migration (docs/phases/phase-7.md §15.1, Sync follow-ups).
- `succeeded`, `balances` set, `reconciled` true or false: the history is read to its end;
  false raises `wallet.unreconciled` with `difference` = balance − Σ per asset.
- `succeeded`, `balances` set, `reconciled` null: every asset that differs is named by an
  open question of the run's scope (the assets of its legs on the scope's addresses, the
  native coin of the address whose run asked a question without legs, such as a Bitcoin
  change card), so the difference is not a mismatch but „wartet auf Antwort“ (the event's
  `waiting`); a difference in any other asset is `reconciled` false; or, on an account-scope
  source, another
  address of the same account is read after this one in the same job (`sync_wallets`), and
  that last address's run carries the account's verdict once every address's movements are
  stored (a receive on address B is in B's history only, so A's run cannot have it yet).

Readings of the spec (step 2 core):

- *First sync* (D186 (1)) is per address: true until a run of this address has read its
  history to the end (a succeeded run with `balances` set).
- *Priced* and *registry assets* (D186 (1)) mean every leg's asset is the chain's native coin
  or a registry token, the assets a price source covers (D180 (3)); the price rows
  themselves are step 6's.
- A chain asset is created on first sight: the native coin by the chain registry, a registry
  token by its row, any other token, spam or not, without a symbol (D180 amended (a)) and
  named by its masked contract with the symbol the chain tells, cleaned of control
  characters and capped (`told_symbol`, D187 (2)), so a token calling itself „BTC“ never
  meets a price by that symbol. `priced_as_asset_id` points at the off-chain asset of the
  coin when the workspace has it; creating that asset is task "assets"'s.
"""

import json
import logging
import re
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, and_, cast, exists, func, insert, null, select, update
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.exc import DataError, IntegrityError

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import chain_decoder as decoder_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import movement_group as group_table
from hausbuch.db.tables import movement_import as import_table
from hausbuch.db.tables import wallet_address as address_table
from hausbuch.db.tables import wallet_sync as sync_table
from hausbuch.rules.chain import movement_hash
from hausbuch.rules.chain.classify import ChainGroup, ChainLeg, ClassifyContext, classify
from hausbuch.rules.chain.evm import reconcile_balance_only
from hausbuch.rules.chain.reconcile import reconcile, sum_balance_parts
from hausbuch.rules.chain.registry import registry
from hausbuch.rules.chain.spam import listed, token_label
from hausbuch.rules.errors import (
    WALLET_KEY_MISSING,
    WALLET_SOURCE_REFUSED,
    WALLET_SOURCE_THROTTLED,
    WALLET_SOURCE_UNAVAILABLE,
    WALLET_SYNC_DEFERRED,
    WALLET_SYNC_ERROR,
    WALLET_TOKEN_UNREADABLE,
)
from hausbuch.rules.household.assets import registry as asset_registry
from hausbuch.rules.review import CHAIN_UNSTORABLE, route_group
from hausbuch.schemas.allocation import RoutingDecision
from hausbuch.schemas.chain import (
    AssetBalance,
    AssetKey,
    Chain,
    ChainSource,
    ChainSourceKind,
    ChainTx,
    DecoderRow,
    GroupRoutingInput,
    GroupStatus,
    WalletSyncStatus,
)
from hausbuch.schemas.common import TAX_YEAR_MIN
from hausbuch.schemas.document import DocumentTyping
from hausbuch.schemas.enums import (
    AccountKind,
    DocumentSource,
    DocumentSubtype,
    DocumentType,
    ImportStatus,
)
from hausbuch.schemas.events import (
    AssetRecorded,
    EventType,
    MovementGroupRecorded,
    MovementImportRecorded,
    MovementRecorded,
    WalletDepositLookedUp,
    WalletSyncRecorded,
)
from hausbuch.schemas.household import (
    AssetKind,
    MovementImportFormat,
    MovementKind,
    MovementSource,
    MovementStatus,
)
from hausbuch.services.chain.base import (
    WALLET_SOURCE_FAILED,
    ChainSourceError,
    RequestBudget,
    budget_limit,
    error_from,
    reconcile_scope,
    source_for,
)
from hausbuch.services.chain.learned import counterparty, deposit_predicate
from hausbuch.services.chain.payee_answers import answered_payee
from hausbuch.services.context import Actor, CommandContext, new_command
from hausbuch.services.documents import read_original, type_document, upload_document
from hausbuch.services.events import append_event
from hausbuch.services.prices.http import SourceError
from hausbuch.settings import Settings
from hausbuch.storage import BlobStore

logger = logging.getLogger("hausbuch.services.chain.sync")

_STATUS_OF = {
    WALLET_SOURCE_THROTTLED: WalletSyncStatus.THROTTLED,
    WALLET_SOURCE_REFUSED: WalletSyncStatus.REFUSED,
}
_HOST = re.compile(r"^[a-z0-9.-]{1,253}$")
_MAX_CURSOR = 200
_NDJSON = "application/x-ndjson"
# asset_movement.quantity is numeric(38,18) since migration 0042 (asset.decimals 0..18): a
# quantity with more places, or 10^20 units or more, cannot be stored exactly, and nothing is
# ever rounded (D186 (2) reconciles to the base unit).
_QUANTITY_SCALE = movement_table.c.quantity.type.scale
_QUANTITY_INTEGER_DIGITS = movement_table.c.quantity.type.precision - _QUANTITY_SCALE
_TOLD_SYMBOL_MAX = 32  # what a chain says a token is called, kept in its name only (D180 (a))
# D181 (c) amended 16 Nov 2026: a balance-only address's run looks up at most this many
# exchange withdrawals by their hash (each up to three requests of the public node's budget),
# the oldest first; the rest wait for the next run.
_MAX_LOOKUPS = 10
# A withdrawal the node does not know is remembered only once it is a day old: a node that
# lags behind the exchange answers `unknown` for a few minutes, and that is asked again.
_UNKNOWN_AFTER = timedelta(days=1)
_EVM_HASH = re.compile(r"^0x[0-9a-f]{64}$")

Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    """One address's run, for the job's log line and the route (no address, no cursor)."""

    address_id: UUID
    chain: Chain
    sync_id: UUID | None  # None: no row (wallet.key_missing)
    status: WalletSyncStatus | None
    # On a succeeded run: the balance read failed after the history was read and the run
    # stored something new (the pages are written, the row has no balance and is never
    # reconciled; the next run reads it again). With nothing new the run is a failed row.
    error_code: str | None = None
    txs_seen: int = 0
    movements_new: int = 0
    deferred: bool = False
    reconciled: bool | None = None
    waiting: bool = False
    spam_dropped: int = 0  # spam legs whose quantity cannot be stored, left out (D187)
    unstorable: int = 0  # transactions written as a group without movements (CHAIN_UNSTORABLE)
    # A balance-only address's verdict card (D181 (c)): wallet.coins_left (also stored as
    # reconciled false with the negative difference) or transfer.unmatched_leg (a balance
    # above its linked deposits, reconciled); logged only, nothing renders it yet.
    card: str | None = None


@dataclass(frozen=True, slots=True)
class _Own:
    id: UUID
    hmac: str  # hex, as the movement hash takes it


@dataclass(frozen=True, slots=True)
class _Start:
    sync_id: UUID
    address_id: UUID
    account_id: UUID
    chain: Chain
    address: str
    own: dict[str, _Own]  # the account's active addresses on the chain
    cursor: str | None
    first_sync: bool
    started_at: datetime
    host: str
    scope: str
    balance_only: bool = False  # the source's `balance_only` (D181 (c): BNB Smart Chain)
    # The source's `reads_tokens` (EVM over Etherscan): `balances` is passed the non-spam
    # tokens each own address has movements in, these stored ones and the run's own legs.
    reads_tokens: bool = False
    stored_tokens: dict[str, frozenset[AssetKey]] = field(default_factory=dict)
    # D261: the IBAN HMAC key, so a plain send's counterparty is stored as its HMAC and
    # masked form (`asset_movement.counterparty_*`); None stores none.
    hmac_key: bytes | None = field(default=None, repr=False)
    # D181 (c) amended 16 Nov 2026: a balance-only address's exchange withdrawals to look up
    # by their hash after the balances (_deposit_candidates), at most _MAX_LOOKUPS a run.
    lookups: tuple["_Lookup", ...] = ()


@dataclass(frozen=True, slots=True)
class _Lookup:
    """One exchange withdrawal a balance-only address's run asks its node about: the
    withdrawal's movement, its hash as the node takes it, and when the exchange sent it."""

    movement_id: UUID
    tx_hash: str = field(repr=False)
    occurred_at: datetime


@dataclass(slots=True)
class _Read:
    txs: list[ChainTx] = field(default_factory=list)
    groups: list[ChainGroup] = field(default_factory=list)
    cursor: str | None = None
    deferred: bool = False
    balances: list[AssetBalance] | None = None
    # The source refused or failed the balance read after the history was read to its end:
    # what the pages bring that is new is written, nothing is reconciled, the next run reads
    # the balance again; a run with nothing new ends failed (`_write`, _BalanceUnread).
    balance_error: str | None = None
    balance_exc: BaseException | None = None
    # The tokens whose balance the source could not read (an EVM source's `unreadable`: a
    # reverted `balanceOf`): left out of both sides, and the run never reads reconciled.
    unreadable: frozenset[AssetKey] = frozenset()
    # The lookups asked (D181 (c) amended) with their outcome; `absent` and an old `unknown`
    # are recorded as wallet.deposit_looked_up so they are not asked again.
    looked_up: list[tuple[_Lookup, str]] = field(default_factory=list)


# --- Assets ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChainAsset:
    id: UUID
    decimals: int
    spam: bool


def _priced_as(conn: Connection, symbol: str) -> UUID | None:
    return conn.execute(
        select(asset_table.c.id).where(
            asset_table.c.symbol == symbol,
            asset_table.c.chain.is_(None),
            asset_table.c.contract_address.is_(None),
        )
    ).scalar_one_or_none()


def told_symbol(symbol: str | None) -> str | None:
    """What a chain says an unlisted token is called, fit for its name only: every control,
    format and surrogate character dropped (a NUL, C0 and C1 controls, bidi overrides),
    trimmed and capped; None when nothing is left. Never a stored symbol (D180 amended (a))."""
    if not symbol:
        return None
    kept = "".join(c for c in symbol if unicodedata.category(c) not in ("Cc", "Cf", "Cs"))
    return kept.strip()[:_TOLD_SYMBOL_MAX].strip() or None


@dataclass(frozen=True, slots=True)
class _NewAsset:
    symbol: str | None
    name: str
    decimals: int
    priced: UUID | None
    spam: bool


def _find_asset(conn: Connection, key: AssetKey) -> ChainAsset | None:
    found = conn.execute(
        select(asset_table.c.id, asset_table.c.decimals, asset_table.c.spam).where(
            asset_table.c.chain == key.chain.value,
            asset_table.c.contract_address.is_(None)
            if key.contract is None
            else asset_table.c.contract_address == key.contract,
        )
    ).one_or_none()
    return None if found is None else ChainAsset(found.id, found.decimals, found.spam)


def _new_asset(conn: Connection, key: AssetKey, leg: ChainLeg | None) -> _NewAsset:
    """The row a chain asset seen for the first time gets (nothing written): the native coin
    by the chain registry, a registry token by its row; any other token has no symbol (D180
    amended (a): a token is its contract, its symbol a label), its name the masked contract
    with the symbol the chain tells, cleaned (D187 (2)), and no price."""
    if key.contract is None:
        chain_row = registry().chain(key.chain)
        symbol = chain_row.native_symbol
        return _NewAsset(
            symbol,
            f"{symbol} ({key.chain.value})",
            chain_row.native_decimals,
            _priced_as(conn, symbol),
            False,
        )
    token = asset_registry().token(key.chain, key.contract)
    if token is not None:
        return _NewAsset(
            token.symbol,
            token.name or f"{token.symbol} ({key.chain.value})",
            token.decimals,
            _priced_as(conn, asset_registry().priced_as(token).symbol),
            False,
        )
    told = told_symbol(leg.symbol) if leg else None
    decimals = (leg.decimals if leg else None) or 0
    return _NewAsset(None, token_label(key, told), decimals, None, bool(leg and leg.spam))


def chain_asset(ctx: CommandContext, key: AssetKey, leg: ChainLeg | None = None) -> ChainAsset:
    """The workspace's asset of a chain and contract, created on first sight (D180, D187)."""
    found = _find_asset(ctx.conn, key)
    if found is not None:
        return found
    new = _new_asset(ctx.conn, key, leg)
    name = new.name[:200]
    asset_id = ctx.conn.execute(
        insert(asset_table)
        .values(
            workspace_id=ctx.workspace_id,
            kind=AssetKind.CRYPTO.value,
            symbol=new.symbol,
            name=name,
            decimals=new.decimals,
            chain=key.chain.value,
            contract_address=key.contract,
            priced_as_asset_id=new.priced,
            spam=new.spam,
            created_at=ctx.now,
            updated_at=ctx.now,
        )
        .returning(asset_table.c.id)
    ).scalar_one()
    append_event(
        ctx,
        EventType.ASSET_CREATED,
        asset_id,
        AssetRecorded(kind=AssetKind.CRYPTO, symbol=new.symbol, name=name),
    )
    return ChainAsset(asset_id, new.decimals, new.spam)


class _Unstorable(Exception):
    """A leg asset_movement cannot hold exactly; its transaction becomes CHAIN_UNSTORABLE."""


class _BalanceUnread(Exception):
    """The balance read failed and the run stored nothing new: the run ends as a failure
    with the read's code (`failed`, `throttled`, `refused`), its cursor unmoved, as a
    failed balance read did before the pages were kept (review of PR #397)."""

    def __init__(self, code: str, cause: BaseException | None) -> None:
        super().__init__(code)
        self.code = code
        self.cause = cause


def _storable(units: int, decimals: int) -> bool:
    """numeric(38,18) holds the quantity units × 10^-decimals exactly: at most 18 places
    (asset.decimals 0..18) and fewer than 10^20 whole units."""
    return 0 <= decimals <= _QUANTITY_SCALE and abs(units) < 10 ** (
        _QUANTITY_INTEGER_DIGITS + decimals
    )


def _units_to_quantity(units: int, decimals: int) -> Decimal:
    if not _storable(units, decimals):
        raise _Unstorable("a quantity numeric(38,18) cannot hold exactly")
    # The string constructor is exact; Decimal arithmetic would round to the context's 28
    # digits, and a numeric(38,18) quantity has up to 38.
    return Decimal(f"{units}E-{decimals}")


def _quantity_to_units(quantity: Decimal, decimals: int) -> int:
    """Exact, by the digits: scaleb would round to the context's 28 digits."""
    sign, digits, exponent = quantity.as_tuple()
    if not isinstance(exponent, int):
        raise ValueError("a chain quantity that is not a number")
    whole = int("".join(map(str, digits)) or "0")
    shift = exponent + decimals
    if shift >= 0:
        units = whole * 10**shift
    else:
        units, rest = divmod(whole, 10**-shift)
        if rest:
            raise ValueError("a chain quantity below its asset's smallest unit")
    return -units if sign else units


# --- Step 1: start ---------------------------------------------------------------------------


def _last_cursor(conn: Connection, address_id: UUID) -> tuple[str | None, bool]:
    """The cursor of the address's last succeeded run, and whether any run read the history
    to its end (one that read the balances)."""
    done = and_(
        sync_table.c.address_id == address_id,
        sync_table.c.status == WalletSyncStatus.SUCCEEDED.value,
    )
    last = conn.execute(
        select(sync_table.c.cursor_after)
        .where(done)
        .order_by(sync_table.c.started_at.desc(), sync_table.c.created_at.desc())
        .limit(1)
    ).one_or_none()
    caught_up = conn.execute(
        select(exists().where(done, sync_table.c.balances.is_not(None)))
    ).scalar_one()
    return (None if last is None else last.cursor_after), bool(caught_up)


def _own_of(conn: Connection, account_id: UUID, chain: Chain) -> dict[str, _Own]:
    return {
        r.address: _Own(r.id, bytes(r.address_hmac).hex())
        for r in conn.execute(
            select(address_table.c.id, address_table.c.address, address_table.c.address_hmac).where(
                address_table.c.account_id == account_id,
                address_table.c.chain == chain.value,
                address_table.c.active.is_(True),
            )
        )
    }


def _context(
    conn: Connection,
    chain: Chain,
    address: str,
    account_id: UUID,
    own: dict[str, _Own],
    native_asset_id: UUID,
    answered_payee: Callable[[str], bool] | None = None,
    hmac_key: bytes | None = None,
) -> ClassifyContext:
    """The classifier's context for one synced address. With `hmac_key`, a send to a deposit
    address the workspace learned for one of its exchanges (D261) becomes an
    exchange_transfer."""
    every_own = conn.execute(
        select(address_table.c.address).where(
            address_table.c.chain == chain.value, address_table.c.active.is_(True)
        )
    ).scalars()
    decoders = [
        DecoderRow(chain=chain, program=r.program, method=r.method, kind=r.kind)
        for r in conn.execute(
            select(decoder_table).where(
                decoder_table.c.chain == chain.value, decoder_table.c.superseded_by.is_(None)
            )
        )
    ]
    held = conn.execute(
        select(asset_table.c.contract_address)
        .distinct()
        .select_from(
            movement_table.join(asset_table, movement_table.c.asset_id == asset_table.c.id)
        )
        .where(
            asset_table.c.chain == chain.value,
            asset_table.c.contract_address.is_not(None),
            asset_table.c.spam.is_(False),
            movement_table.c.status == MovementStatus.CONFIRMED.value,
            movement_table.c.superseded_by.is_(None),
        )
    ).scalars()
    asset_ids = {
        AssetKey(chain=chain, contract=r.contract_address): r.id
        for r in conn.execute(
            select(asset_table.c.id, asset_table.c.contract_address).where(
                asset_table.c.chain == chain.value
            )
        )
    }
    # D183 (3)(b): the hashes the workspace's own exchange withdrawals name, so a token an
    # exchange sent to an own address is an exchange_transfer, never spam. Read under RLS
    # like every query here; kept as stored (a base58 signature is case-sensitive; the EVM
    # classifier lower-cases), held in memory only, never logged or put into an event.
    exchange_hashes = conn.execute(
        select(movement_table.c.tx_hash)
        .distinct()
        .select_from(
            movement_table.join(account_table, movement_table.c.account_id == account_table.c.id)
        )
        .where(
            account_table.c.kind == AccountKind.EXCHANGE.value,
            movement_table.c.kind == MovementKind.INTERNAL_TRANSFER_OUT.value,
            movement_table.c.tx_hash.is_not(None),
            movement_table.c.superseded_by.is_(None),
        )
    ).scalars()
    return ClassifyContext(
        chain=chain,
        address=address,
        own_addresses=frozenset(every_own),
        decoders=tuple(decoders),
        held=frozenset(AssetKey(chain=chain, contract=c) for c in held),
        account_id=account_id,
        own_address_ids={a: o.id for a, o in own.items()},
        native_asset_id=native_asset_id,
        asset_ids=asset_ids,
        answered_payee=answered_payee,
        exchange_hashes=frozenset(h.strip() for h in exchange_hashes if h.strip()),
        exchange_deposit=(
            deposit_predicate(conn, chain, hmac_key) if hmac_key is not None else None
        ),
    )


def _stored_tokens(
    conn: Connection, chain: Chain, own: dict[str, _Own]
) -> dict[str, frozenset[AssetKey]]:
    """Per own address, the tokens (contract assets, spam left out) it has live movements in:
    a source that `reads_tokens` reads their balances too, so a token the registry does not
    list is reconciled like a listed one."""
    by_id = {o.id: a for a, o in own.items()}
    rows = conn.execute(
        select(movement_table.c.address_id, asset_table.c.contract_address)
        .distinct()
        .select_from(
            movement_table.join(asset_table, movement_table.c.asset_id == asset_table.c.id)
        )
        .where(
            movement_table.c.address_id.in_(list(by_id)),
            movement_table.c.superseded_by.is_(None),
            asset_table.c.chain == chain.value,
            asset_table.c.contract_address.is_not(None),
            asset_table.c.spam.is_(False),
        )
    ).all()
    out: dict[str, set[AssetKey]] = {}
    for r in rows:
        out.setdefault(by_id[r.address_id], set()).add(
            AssetKey(chain=chain, contract=r.contract_address)
        )
    return {a: frozenset(keys) for a, keys in out.items()}


def _balances(source: ChainSource, start: _Start, out: _Read) -> list[AssetBalance]:
    """The balances the run reconciles against, of the address or of every own address of
    the account (`reconcile_scope`). A source that `reads_tokens` is also asked for each
    non-spam token the address has movements in, stored or read in this run; the others are
    called with the address alone."""
    addresses = list(start.own) if start.scope == "account" else [start.address]
    if not start.reads_tokens:
        return [b for a in addresses for b in source.balances(a)]
    unreadable: set[AssetKey] = set()
    read: dict[str, set[AssetKey]] = {}
    for group in out.groups:
        for leg in group.legs:
            if leg.spam or leg.asset_key.contract is None:
                continue
            read.setdefault(leg.address or start.address, set()).add(leg.asset_key)
    balances = []
    for a in addresses:
        tokens = read.get(a, set()) | start.stored_tokens.get(a, frozenset())
        ordered = tuple(sorted(tokens, key=lambda k: k.contract or ""))
        balances.extend(source.balances(a, tokens=ordered))
        unreadable |= set(getattr(source, "unreadable", ()) or ())
    out.unreadable = frozenset(unreadable)
    return balances


def _host(source: ChainSource) -> str:
    host = str(getattr(source, "host", "") or "").lower()
    return host if _HOST.fullmatch(host) else "unknown"


# --- Step 2: read ----------------------------------------------------------------------------


def _read(
    source: ChainSource,
    start: _Start,
    ctx: ClassifyContext,
    budget: RequestBudget,
    stored: Callable[[], Iterator[dict[str, Any]]] | None = None,
) -> _Read:
    out = _Read(cursor=start.cursor)
    seen: set[str] = set()
    # A source that `reads_stored` (Solana: the own token accounts earlier runs found) is
    # handed the address's stored records, which it reads only when it needs them.
    asks = stored is not None and getattr(source, "reads_stored", False)
    extra = {"stored": stored} if asks else {}
    while True:
        page = source.transactions(start.address, out.cursor, **extra)
        for tx in page.items:
            out.txs.append(tx)
            if tx.hash in seen:
                continue
            seen.add(tx.hash)
            group = classify(tx, ctx)
            if group is not None and (group.legs or group.question):
                out.groups.append(group)
        moved = page.next_cursor is not None and page.next_cursor != out.cursor
        if moved:
            if len(page.next_cursor) > _MAX_CURSOR:
                raise ChainSourceError(WALLET_SOURCE_FAILED)
            out.cursor = page.next_cursor
        # Deferred is the budget's verdict, not the cursor's: a source refused by a spent
        # budget ends its page with the cursor where it stood, which reads like the tip.
        if budget.exhausted:
            out.deferred = True
            return out
        if not moved:  # the tip reached: None, or the cursor it was given
            break
    try:
        balances = _balances(source, start, out)
    except (ChainSourceError, SourceError) as exc:
        # The history is read: a failed balance read never discards it. The run is written
        # with no balance (never reconciled), and the next run, at the tip, reads it again.
        code = exc.code if isinstance(exc, ChainSourceError) else error_from(exc).code
        out.balance_error, out.balance_exc = code, exc
        _log_balance_unread(start, code, exc)
        return out
    if budget.exhausted:  # a balance call may have been refused: the next run reads them
        out.deferred = True
        return out
    out.balances = balances
    if start.lookups:
        _lookup_deposits(source, start, ctx, out)
    return out


def _lookup_deposits(source: ChainSource, start: _Start, ctx: ClassifyContext, out: _Read) -> None:
    """D181 (c) amended 16 Nov 2026: a balance-only address has no history, so the exchange
    withdrawals on its chain that name a hash are looked up by it (the source's `lookup`),
    after the balances, so a budget spent here never costs the run its balance (the run is
    not deferred by it). A deposit found is classified like a page's transaction (its hash
    is an exchange's, so an exchange_transfer, D183 (3)(b)) and written as the run's; step
    5's matcher then links it by the hash and confirms both legs (D186 (1)). A node's error
    or a spent budget stops the lookups only; they are asked again next run."""
    lookup = getattr(source, "lookup", None)
    if lookup is None:
        return
    for wanted in start.lookups[:_MAX_LOOKUPS]:
        try:
            got = lookup(wanted.tx_hash, start.address)
        except (ChainSourceError, SourceError) as exc:
            code = exc.code if isinstance(exc, ChainSourceError) else error_from(exc).code
            fields = {"event": "wallet_deposit_lookup", "chain": start.chain.value, "code": code}
            logger.warning("a deposit lookup failed", extra={"fields": fields})
            return
        if got.outcome == "not_asked":
            return  # the run's budget is spent: the next run asks
        out.looked_up.append((wanted, got.outcome))
        if got.tx is None:
            continue
        out.txs.append(got.tx)
        group = classify(got.tx, ctx)
        if group is not None and (group.legs or group.question):
            out.groups.append(group)


def _log_balance_unread(start: _Start, code: str, exc: BaseException) -> None:
    """A balance read that failed after the history was read: the chain, the code and the
    exception class only (no address, URL or cursor)."""
    fields = {
        "event": "wallet_sync_balance_unread",
        "chain": start.chain.value,
        "error_code": code,
        "exc_type": type(exc).__name__,
    }
    logger.warning("a wallet balance could not be read", extra={"fields": fields})


# --- Step 3: write ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Plan:
    """A group's legs not stored yet, with their own address and hash. `unstorable`: the
    classifier gave two legs of different assets the same (address, index, role), so one
    movement hash for two movements; the transaction is written as CHAIN_UNSTORABLE."""

    group: ChainGroup
    legs: list[tuple[ChainLeg, _Own, str]]
    unstorable: bool = False


def _own_for(start: _Start, leg: ChainLeg) -> _Own:
    found = start.own.get(leg.address or start.address)
    if found is None:
        raise ValueError("a leg on an address the account does not hold")
    return found


def _stored_hashes(ctx: CommandContext, account_id: UUID, hashes: list[str]) -> set[str]:
    found: set[str] = set()
    for i in range(0, len(hashes), 1000):
        found.update(
            ctx.conn.execute(
                select(movement_table.c.movement_hash).where(
                    movement_table.c.account_id == account_id,
                    movement_table.c.movement_hash.in_(hashes[i : i + 1000]),
                )
            ).scalars()
        )
    return found


def _plan(ctx: CommandContext, start: _Start, read: _Read) -> list[_Plan]:
    plans: list[_Plan] = []
    hashes: list[str] = []
    for group in read.groups:
        legs: list[tuple[ChainLeg, _Own, str]] = []
        by_hash: dict[str, ChainLeg] = {}
        collided = False
        for leg in group.legs:
            own = _own_for(start, leg)
            digest = movement_hash(start.chain, group.tx_hash, own.hmac, leg.index, leg.role)
            first = by_hash.get(digest)
            if first is not None:
                # the same leg twice is one movement; two assets on one place are a
                # classifier error, refused here, never left to the unique index
                collided = collided or first.asset_key != leg.asset_key
                continue
            by_hash[digest] = leg
            legs.append((leg, own, digest))
        if collided:
            _log_refused(start, "leg_collision")
        plans.append(_Plan(group, legs, unstorable=collided))
        hashes.extend(h for _, _, h in legs)
    stored = _stored_hashes(ctx, start.account_id, hashes)
    out: list[_Plan] = []
    planned: set[str] = set()
    for plan in plans:
        if plan.unstorable or not plan.legs:  # written once (see _write_group)
            out.append(_Plan(plan.group, [], plan.unstorable))
            continue
        new = [(leg, own, h) for leg, own, h in plan.legs if h not in stored and h not in planned]
        planned.update(h for _, _, h in new)
        if new:
            out.append(_Plan(plan.group, new))
    return out


def _log_refused(start: _Start, reason: str) -> None:
    """A transaction written as CHAIN_UNSTORABLE: the chain and why only (no hash, address)."""
    fields = {"event": "wallet_sync_unstorable", "chain": start.chain.value, "reason": reason}
    logger.warning("a chain transaction could not be stored", extra={"fields": fields})


def _live_group(ctx: CommandContext, start: _Start, tx_hash: str):
    """The transaction's live group: id, status, question and whether it has no movement."""
    no_movement = ~exists().where(movement_table.c.group_id == group_table.c.id)
    return ctx.conn.execute(
        select(
            group_table.c.id,
            group_table.c.status,
            group_table.c.question,
            no_movement.label("legless"),
        )
        .where(
            group_table.c.account_id == start.account_id,
            group_table.c.chain == start.chain.value,
            group_table.c.tx_hash == tx_hash,
            group_table.c.superseded_by.is_(None),
        )
        .order_by(group_table.c.created_at, group_table.c.id)
        .limit(1)
    ).one_or_none()


def _route(start: _Start, group: ChainGroup):
    every_listed = all(listed(leg.asset_key) for leg in group.legs)
    return route_group(
        GroupRoutingInput(
            kind=group.kind,
            fee_only=group.fee_only,
            decoder_listed=group.decoder_listed,
            priced=every_listed,
            registry_assets=every_listed,
            first_sync=start.first_sync,
            linked=group.linked,
            pair_learned=group.pair_learned,
            unregistered_output=group.unregistered_output,
            question=group.question is not None,
        )
    )


def _stored_records(
    engine: Engine, workspace_id: UUID, store: BlobStore, address_id: UUID
) -> Callable[[], Iterator[dict[str, Any]]]:
    """The address's stored originals, every record as the runs kept it, read on demand in a
    transaction of its own (a source that `reads_stored` asks once per run at most), one
    original at a time and one record at a time, so a long history is never held whole.
    Nothing read here is logged or put into an event."""

    def load() -> Iterator[dict[str, Any]]:
        with workspace_transaction(engine, workspace_id) as conn:
            documents = conn.execute(
                select(sync_table.c.document_id)
                .where(
                    sync_table.c.address_id == address_id,
                    sync_table.c.document_id.is_not(None),
                )
                .order_by(sync_table.c.started_at, sync_table.c.id)
            ).scalars()
            for document_id in list(documents):
                original = read_original(conn, store, document_id)
                for line in original.splitlines():
                    if line.strip():
                        yield json.loads(line)
                del original

    return load


@dataclass(slots=True)
class _Original:
    """The run's stored original, created by the first group that needs it (inside that
    group's savepoint, so a group rolled back takes it along and the next one stores it)."""

    store: BlobStore
    read: _Read
    last: datetime | None
    id: UUID | None = None

    def ensure(self, ctx: CommandContext, start: _Start) -> UUID:
        if self.id is None:
            self.id = _store_original(ctx, self.store, start, self.read, self.last)
        return self.id


@dataclass(slots=True)
class _Written:
    movements: int = 0
    spam_dropped: int = 0
    unstorable: int = 0
    occurred_at: datetime | None = None  # set when the group wrote a row


def _savepoint(ctx: CommandContext, original: _Original, write: Callable[[], int]) -> int:
    """`write` in a savepoint: a refusal (_Unstorable, or the database refusing the data:
    DataError, IntegrityError) rolls back its rows, events, assets and an original it
    stored, and is raised on. Any other database error (a deadlock, a lost connection) is
    not a refusal of this transaction: it fails the run, the cursor unmoved, so the next run
    reads it again."""
    had_original = original.id is not None
    try:
        with ctx.conn.begin_nested():
            return write()
    except (_Unstorable, DataError, IntegrityError):
        if not had_original:
            original.id = None
        raise


def _insert_group(
    ctx: CommandContext,
    start: _Start,
    group: ChainGroup,
    status: GroupStatus,
    reason: str,
    question: str | None,
    decoder: str | None,
) -> UUID:
    group_id = ctx.conn.execute(
        insert(group_table)
        .values(
            workspace_id=ctx.workspace_id,
            account_id=start.account_id,
            chain=start.chain.value,
            tx_hash=group.tx_hash,
            occurred_at=group.occurred_at,
            kind=group.kind.value,
            decoder=decoder,
            status=status.value,
            question=question,
            created_at=ctx.now,
        )
        .returning(group_table.c.id)
    ).scalar_one()
    append_event(
        ctx,
        EventType.MOVEMENT_GROUP_CONFIRMED
        if status is GroupStatus.CONFIRMED
        else EventType.MOVEMENT_GROUP_PROPOSED,
        group_id,
        MovementGroupRecorded(
            account_id=start.account_id,
            chain=start.chain.value,
            kind=group.kind.value,
            reason=reason,
            sync_id=start.sync_id,
        ),
    )
    return group_id


def _write_movements(
    ctx: CommandContext,
    start: _Start,
    plan: _Plan,
    live,
    original: _Original,
    replaces: UUID | None = None,
) -> int:
    """The group (or the live group of the same transaction) and its new legs' movements.
    `replaces`: a live legless card the new group supersedes (its legs arrived). A new card
    without legs keeps the run's original too, so it can be answered from the stored
    original alone (D182 (3) amended, D192; review of PR #387)."""
    group = plan.group
    routing = _route(start, group)  # the new legs alone when they join a live group
    auto = routing.decision is RoutingDecision.AUTO_CONFIRM
    if live is not None:
        # D186 (1) per leg: a leg joining a confirmed group is confirmed only when its own
        # routing would confirm it (never by the group it joins, nor on a first sync); a
        # proposed group's legs wait with it.
        group_id = live.id
        confirmed = GroupStatus(live.status) is GroupStatus.CONFIRMED and auto
    else:
        status = GroupStatus.CONFIRMED if auto else GroupStatus.PROPOSED
        group_id = _insert_group(
            ctx,
            start,
            group,
            status,
            routing.reason,
            None if auto else group.question,
            group.decoder,
        )
        confirmed = auto
        if replaces is not None:
            ctx.conn.execute(
                update(group_table)
                .where(group_table.c.id == replaces, group_table.c.superseded_by.is_(None))
                .values(superseded_by=group_id)
            )
        if not plan.legs and not auto and group.question is not None:
            original.ensure(ctx, start)  # the question's transaction, for its answer
    if not plan.legs:
        return 0
    movement_status = MovementStatus.CONFIRMED if confirmed else MovementStatus.PROPOSED
    document_id = original.ensure(ctx, start)
    for leg, own, digest in plan.legs:
        asset = chain_asset(ctx, leg.asset_key, leg)
        # D261: the HMAC and masked form of the one external address a plain send paid, so a
        # person's link can learn it as an exchange's deposit address; never the plaintext.
        paid = (
            counterparty(start.chain, leg.counterparty, start.hmac_key)
            if leg.counterparty is not None and start.hmac_key is not None
            else None
        )
        movement_id = ctx.conn.execute(
            insert(movement_table)
            .values(
                workspace_id=ctx.workspace_id,
                account_id=start.account_id,
                asset_id=asset.id,
                kind=leg.kind.value,
                quantity=_units_to_quantity(leg.signed, asset.decimals),
                occurred_at=group.occurred_at,
                tx_hash=group.tx_hash,
                movement_hash=digest,
                source=MovementSource.CHAIN.value,
                document_id=document_id,
                status=movement_status.value,
                group_id=group_id,
                address_id=own.id,
                index=leg.index,
                counterparty_hmac=paid[0] if paid else None,
                counterparty_masked=paid[1] if paid else None,
                created_at=ctx.now,
            )
            .returning(movement_table.c.id)
        ).scalar_one()
        append_event(
            ctx,
            EventType.MOVEMENT_CONFIRMED
            if movement_status is MovementStatus.CONFIRMED
            else EventType.MOVEMENT_PROPOSED,
            movement_id,
            MovementRecorded(
                account_id=start.account_id,
                asset_id=asset.id,
                kind=leg.kind,
                source=MovementSource.CHAIN,
                document_id=document_id,
            ),
        )
    return len(plan.legs)


def _write_unstorable(
    ctx: CommandContext, start: _Start, group: ChainGroup, original: _Original, reason: str
) -> _Written:
    """A transaction Hausbuch cannot store as movements: one group without movements and
    without a question, status proposed, reason CHAIN_UNSTORABLE, so the cursor moves on and
    the address's history is read past it. Its difference stays visible: Σ movements misses
    it, so the run reads reconciled = false (it is no question, so never „wartet auf
    Antwort“). The run's original is stored with it (D182 (3), D192)."""
    _log_refused(start, reason)
    no_movement = ~exists().where(movement_table.c.group_id == group_table.c.id)
    recorded = ctx.conn.execute(
        select(
            exists().where(
                group_table.c.account_id == start.account_id,
                group_table.c.chain == start.chain.value,
                group_table.c.tx_hash == group.tx_hash,
                group_table.c.superseded_by.is_(None),
                group_table.c.question.is_(None),
                no_movement,
            )
        )
    ).scalar_one()
    if recorded:
        return _Written(unstorable=1)

    def write() -> int:
        original.ensure(ctx, start)
        _insert_group(ctx, start, group, GroupStatus.PROPOSED, CHAIN_UNSTORABLE, None, None)
        return 0

    try:
        _savepoint(ctx, original, write)
    except (_Unstorable, DataError, IntegrityError):
        # not even the group fits (a hash over 200 characters)
        _log_refused(start, "group_refused")
        return _Written(unstorable=1)
    return _Written(unstorable=1, occurred_at=group.occurred_at)


def _write_group(ctx: CommandContext, start: _Start, plan: _Plan, original: _Original) -> _Written:
    group = plan.group
    if plan.unstorable:
        return _write_unstorable(ctx, start, group, original, "leg_collision")
    live = _live_group(ctx, start, group.tx_hash)
    if live is not None and not plan.legs:
        return _Written()  # the question is already asked
    keep: list[tuple[ChainLeg, _Own, str]] = []
    dropped = 0
    for leg, own, digest in plan.legs:
        asset = _find_asset(ctx.conn, leg.asset_key) or _new_asset(ctx.conn, leg.asset_key, leg)
        if _storable(leg.signed, asset.decimals):
            keep.append((leg, own, digest))
        elif asset.spam:  # hidden, never priced, never a lot (D187): left out, counted
            dropped += 1
        else:
            written = _write_unstorable(ctx, start, group, original, "quantity")
            written.spam_dropped = dropped
            return written
    if dropped and not keep and (group.question is None or live is not None):
        return _Written(spam_dropped=dropped)  # nothing of the transaction is left to write
    replaces = None
    if live is not None and keep and live.legless and live.question is not None:
        # legs arrived for a question card (an output's address registered since): the card
        # gives way to the group derived now, never a card with legs joined into it (review
        # of PR #387). A group that already has legs is joined as before (D182 (2)).
        replaces, live = live.id, None
    kept = _Plan(replace(group, legs=[leg for leg, _, _ in keep]), keep)
    try:
        n = _savepoint(
            ctx, original, lambda: _write_movements(ctx, start, kept, live, original, replaces)
        )
    except (_Unstorable, DataError, IntegrityError):  # a leg or the group the database refuses
        written = _write_unstorable(ctx, start, group, original, "refused")
        written.spam_dropped = dropped
        return written
    return _Written(movements=n, spam_dropped=dropped, occurred_at=group.occurred_at)


def _store_original(
    ctx: CommandContext, store: BlobStore, start: _Start, read: _Read, last: datetime
) -> UUID:
    lines = [json.dumps(tx.raw, sort_keys=True, separators=(",", ":")) for tx in read.txs]
    data = ("\n".join(lines) + "\n").encode("utf-8")
    document = upload_document(
        ctx,
        store,
        data,
        media_type=_NDJSON,
        source=DocumentSource.BANK_SYNC,
        original_filename=f"chain-sync-{start.chain.value}-{last.date().isoformat()}.ndjson",
    )
    if document.typing is None:
        day = last.date()
        type_document(
            ctx,
            document.id,
            DocumentTyping(
                type=DocumentType.STATEMENT,
                subtype=DocumentSubtype.CHAIN_SYNC,
                document_date=day,
                tax_year=day.year if day.year >= TAX_YEAR_MIN else None,
            ),
        )
    return document.id


def _key_text(key: AssetKey) -> str:
    return key.contract or "native"


def _spam_keys(conn: Connection, chain: Chain, keys: set[AssetKey]) -> set[AssetKey]:
    contracts = [k.contract for k in keys if k.contract is not None]
    if not contracts:
        return set()
    found = conn.execute(
        select(asset_table.c.contract_address).where(
            asset_table.c.chain == chain.value,
            asset_table.c.contract_address.in_(contracts),
            asset_table.c.spam.is_(True),
        )
    ).scalars()
    return {AssetKey(chain=chain, contract=c) for c in found}


def _reconcile(
    ctx: CommandContext,
    start: _Start,
    balances: list[AssetBalance],
    unreadable: frozenset[AssetKey] = frozenset(),
) -> tuple[dict[str, str], dict[str, str], str | None]:
    """D186 (2): Σ confirmed and proposed movements (spam left out) of the address, or of the
    account's addresses on the chain when the source reconciles per account, against the
    balance's parts, per asset in base units. Returns (balances, difference) as stored, and
    a balance-only address's card.

    A balance-only source (D181 (c)) has no history, so its address's movements are only the
    deposits linked to it (step 5): rules/chain/evm.reconcile_balance_only compares the
    balance with them, over the assets the source read a balance of (a deposit in an asset
    it did not read is no shortfall). A balance above them is reconciled (the card
    transfer.unmatched_leg), one below is reconciled false with the negative shortfall as
    the difference (wallet.coins_left).

    A token whose balance the source could not read (`unreadable`) is left out of both
    sides, never compared as if its balance were 0; the caller marks the run unreconciled."""
    scope = (
        movement_table.c.address_id.in_([o.id for o in start.own.values()])
        if start.scope == "account"
        else movement_table.c.address_id == start.address_id
    )
    rows = ctx.conn.execute(
        select(
            asset_table.c.contract_address,
            asset_table.c.decimals,
            asset_table.c.spam,
            func.sum(movement_table.c.quantity).label("total"),
        )
        .select_from(
            movement_table.join(asset_table, movement_table.c.asset_id == asset_table.c.id)
        )
        .where(scope, movement_table.c.superseded_by.is_(None))
        .group_by(asset_table.c.contract_address, asset_table.c.decimals, asset_table.c.spam)
    ).all()
    sums: dict[AssetKey, int] = {}
    spam_keys: set[AssetKey] = set()
    for r in rows:
        key = AssetKey(chain=start.chain, contract=r.contract_address)
        if r.spam:
            spam_keys.add(key)
            continue
        if key in unreadable:
            continue
        sums[key] = sums.get(key, 0) + _quantity_to_units(r.total, r.decimals)
    spam_keys |= _spam_keys(ctx.conn, start.chain, {b.asset_key for b in balances})
    have = sum_balance_parts(
        (b.asset_key, b.units) for b in balances if b.asset_key not in spam_keys
    )
    stored = {_key_text(k): str(v) for k, v in sorted(have.items(), key=lambda i: _key_text(i[0]))}
    if start.balance_only:
        verdict = reconcile_balance_only(
            {_key_text(k): v for k, v in have.items()},
            {_key_text(k): v for k, v in sums.items() if k in have},
        )
        return stored, {k: str(v) for k, v in verdict.difference.items()}, verdict.card
    diff = reconcile(sums, have)
    return stored, {_key_text(k): str(v) for k, v in diff.items()}, None


def _scope_ids(start: _Start) -> list[UUID]:
    if start.scope == "account":
        return [o.id for o in start.own.values()]
    return [start.address_id]


def _waiting_keys(ctx: CommandContext, start: _Start) -> set[str]:
    """The assets of the run's scope that wait for a person's answer („wartet auf Antwort“),
    as _reconcile keys them: those named by a live proposed group with a question, the
    assets of its legs on an address of the scope, and for a question without legs (a
    Bitcoin change card) the native coin of the address whose run asked it (its
    movement_group.proposed event's sync). Nothing else waits: any other difference is a
    mismatch. A CHAIN_UNSTORABLE group has no question, so it never waits."""
    g, m, e, w = group_table.c, movement_table.c, event_table.c, sync_table.c
    scope = _scope_ids(start)
    open_question = and_(
        g.account_id == start.account_id,
        g.chain == start.chain.value,
        g.superseded_by.is_(None),
        g.status == GroupStatus.PROPOSED.value,
        g.question.is_not(None),
    )
    keys = {
        _key_text(AssetKey(chain=start.chain, contract=contract))
        for contract in ctx.conn.execute(
            select(asset_table.c.contract_address)
            .distinct()
            .select_from(
                movement_table.join(group_table, m.group_id == g.id).join(
                    asset_table, m.asset_id == asset_table.c.id
                )
            )
            .where(open_question, m.superseded_by.is_(None), m.address_id.in_(scope))
        ).scalars()
    }
    legless = ctx.conn.execute(
        select(
            exists()
            .select_from(
                group_table.join(
                    event_table,
                    and_(
                        e.entity_id == g.id,
                        e.type == EventType.MOVEMENT_GROUP_PROPOSED.value,
                    ),
                ).join(sync_table, w.id == cast(e.payload["sync_id"].astext, PG_UUID))
            )
            .where(
                open_question,
                ~exists().where(m.group_id == g.id),
                w.address_id.in_(scope),
            )
        )
    ).scalar_one()
    if legless:
        keys.add(_key_text(AssetKey(chain=start.chain)))
    return keys


def _finish(ctx: CommandContext, start: _Start, values: dict, payload: WalletSyncRecorded) -> None:
    ctx.conn.execute(
        update(sync_table)
        .where(sync_table.c.id == start.sync_id)
        .values(finished_at=max(ctx.now, start.started_at), **values)
    )
    event = (
        EventType.WALLET_SYNC_COMPLETED
        if payload.status == WalletSyncStatus.SUCCEEDED.value
        else EventType.WALLET_SYNC_FAILED
    )
    append_event(ctx, event, start.account_id, payload)


def _write(
    ctx: CommandContext,
    store: BlobStore,
    start: _Start,
    read: _Read,
    account_pending: bool = False,
) -> SyncOutcome:
    # The wallet's account row, locked before any live group is read: a person's answer locks
    # it too (wallet_groups), so a sync never adds a leg under a group just superseded.
    ctx.conn.execute(
        select(account_table.c.id).where(account_table.c.id == start.account_id).with_for_update()
    )
    plans = _plan(ctx, start, read)
    writing = [p for p in plans if p.legs or p.unstorable or p.group.question is not None]
    original = _Original(store, read, max((p.group.occurred_at for p in writing), default=None))
    new = spam_dropped = unstorable = 0
    written_at: list[datetime] = []
    for plan in plans:  # each group in its own savepoint: one refusal never blocks the rest
        written = _write_group(ctx, start, plan, original)
        new += written.movements
        spam_dropped += written.spam_dropped
        unstorable += written.unstorable
        if written.occurred_at is not None:
            written_at.append(written.occurred_at)
    document_id = original.id
    if read.balance_error is not None and not written_at and document_id is None:
        # Nothing new (no movement, no question card, no original): a run that only says
        # the balance is unread is a failure, never a quiet succeeded row with no balance,
        # which the wallet page would show as deferred day after day (a BNB Smart Chain
        # address whose node refuses every call). Decided by what was stored, not by the
        # transactions read: Solana re-reads its tip, Etherscan's cursor moves on none.
        raise _BalanceUnread(read.balance_error, read.balance_exc)
    import_id = None
    if document_id is not None:
        last, first = max(written_at), min(written_at)
        total = sum(len(g.legs) for g in read.groups)
        import_id = ctx.conn.execute(
            insert(import_table)
            .values(
                workspace_id=ctx.workspace_id,
                account_id=start.account_id,
                document_id=document_id,
                format=MovementImportFormat.CHAIN_SYNC.value,
                rows_total=total,
                rows_new=new,
                rows_skipped=total - new,
                first_at=first,
                last_at=last,
                status=ImportStatus.IMPORTED.value,
                created_at=ctx.now,
                updated_at=ctx.now,
            )
            .returning(import_table.c.id)
        ).scalar_one()
        append_event(
            ctx,
            EventType.ACCOUNT_MOVEMENTS_IMPORTED,
            start.account_id,
            MovementImportRecorded(
                import_id=import_id,
                document_id=document_id,
                format=MovementImportFormat.CHAIN_SYNC,
                rows_total=total,
                rows_new=new,
                rows_skipped=total - new,
            ),
            effective_date=last.date(),
        )
    _remember_lookups(ctx, start, read)
    balances = difference = reconciled = card = None
    waiting = False
    block = max((tx.block for tx in read.txs), default=None)
    if read.balances is not None:
        balances, difference, card = _reconcile(ctx, start, read.balances, read.unreadable)
        reconciled = not difference
        if difference and set(difference) <= _waiting_keys(ctx, start):
            reconciled, difference, waiting = None, None, True
        elif difference and start.scope == "account" and account_pending:
            reconciled, difference = None, None  # the account's last address decides
        if read.unreadable:  # a balance not read is never reconciled, nor only waiting
            reconciled, waiting, card = False, False, WALLET_TOKEN_UNREADABLE
        block = max((b.at_block for b in read.balances), default=block)
    _finish(
        ctx,
        start,
        {
            "status": WalletSyncStatus.SUCCEEDED.value,
            "cursor_after": read.cursor,
            "block": block,
            "txs_seen": len(read.txs),
            "movements_new": new,
            # JSONB: a Python None would store JSON null, not SQL NULL (the CHECKs read NULL)
            "balances": null() if balances is None else balances,
            "reconciled": reconciled,
            "difference": difference or null(),
            "import_id": import_id,
            "document_id": document_id,
        },
        WalletSyncRecorded(
            sync_id=start.sync_id,
            address_id=start.address_id,
            status="succeeded",
            source_host=start.host,
            txs_seen=len(read.txs),
            movements_new=new,
            reconciled=reconciled,
            deferred=read.deferred,
            waiting=waiting,
            document_id=document_id,
            error_code=read.balance_error,
        ),
    )
    return SyncOutcome(
        start.address_id,
        start.chain,
        start.sync_id,
        WalletSyncStatus.SUCCEEDED,
        txs_seen=len(read.txs),
        movements_new=new,
        deferred=read.deferred,
        reconciled=reconciled,
        waiting=waiting,
        spam_dropped=spam_dropped,
        unstorable=unstorable,
        card=card,
        error_code=read.balance_error,
    )


# --- Deposits looked up by hash (D181 (c) amended 16 Nov 2026) --------------------------------


def _evm_hash(value: str) -> str | None:
    """An exchange's printed hash as an EVM node takes it (lower case, 0x, 64 hex digits), or
    None for anything else (never sent: a malformed hash costs no request)."""
    text = value.strip().lower()
    if not text.startswith("0x"):
        text = "0x" + text
    return text if _EVM_HASH.fullmatch(text) else None


def _hashed_withdrawals(conn: Connection, chain: Chain) -> list:
    """Every live, confirmed exchange withdrawal (`internal_transfer_out`) of the workspace
    (row-level security) whose network is `chain`'s and that names a hash, the oldest first."""
    m, a = movement_table.c, account_table.c
    rows = conn.execute(
        select(m.id, m.tx_hash, m.network, m.occurred_at)
        .select_from(movement_table.join(account_table, a.id == m.account_id))
        .where(
            a.kind == AccountKind.EXCHANGE.value,
            m.kind == MovementKind.INTERNAL_TRANSFER_OUT.value,
            m.status == MovementStatus.CONFIRMED.value,
            m.superseded_by.is_(None),
            m.tx_hash.is_not(None),
            m.network.is_not(None),
        )
        .order_by(m.occurred_at, m.id)
    ).all()
    return [r for r in rows if registry().network(r.network) is chain]


def _linked_legs(conn: Connection) -> set[UUID]:
    from hausbuch.services.transfers import _current_links, _leg_ids

    return {i for link in _current_links(conn) for i in _leg_ids(link)}


def _held_hashes(conn: Connection, account_id: UUID) -> set[str]:
    """The transaction hashes the wallet holds a group of, superseded ones too (a deposit
    written once, or answered by a person, is never looked up again)."""
    g = group_table.c
    return {
        h.lower()
        for h in conn.execute(select(g.tx_hash).where(g.account_id == account_id)).scalars()
    }


def _remembered(conn: Connection, account_id: UUID, address_id: UUID) -> list[dict]:
    e = event_table.c
    return [
        p or {}
        for p in conn.execute(
            select(e.payload).where(
                e.type == EventType.WALLET_DEPOSIT_LOOKED_UP.value,
                e.entity_id == account_id,
                e.payload["address_id"].astext == str(address_id),
            )
        ).scalars()
    ]


def _deposit_candidates(
    conn: Connection, chain: Chain, account_id: UUID, address_id: UUID, host: str
) -> tuple[_Lookup, ...]:
    """The withdrawals a balance-only address's run looks up: every live, confirmed exchange
    withdrawal (`internal_transfer_out`) of the workspace (row-level security) whose network
    is this chain's and that names a well-formed hash, the oldest first, except one already
    in a current link, one whose hash the wallet holds a group of already (written, or
    answered by a person), one this address's node said credited it nothing (`absent`), and
    one the same node did not know a day after it was sent (`unknown`)."""
    rows = _hashed_withdrawals(conn, chain)
    if not rows:
        return ()
    linked = _linked_legs(conn)
    held = _held_hashes(conn, account_id)
    absent: set[str] = set()
    unknown: set[str] = set()
    for p in _remembered(conn, account_id, address_id):
        if p.get("outcome") == "absent":
            absent.add(str(p.get("movement_id")))
        elif p.get("outcome") == "unknown" and p.get("source_host") == host:
            unknown.add(str(p.get("movement_id")))
    out: list[_Lookup] = []
    seen: set[str] = set()
    for r in rows:
        tx_hash = _evm_hash(r.tx_hash)
        if tx_hash is None or tx_hash in seen or tx_hash in held or r.id in linked:
            continue
        if str(r.id) in absent or str(r.id) in unknown:
            continue
        seen.add(tx_hash)
        out.append(_Lookup(r.id, tx_hash, r.occurred_at))
    return tuple(out)


def _remember_lookups(ctx: CommandContext, start: _Start, read: _Read) -> None:
    """The lasting answers of the run's lookups, one wallet.deposit_looked_up each (ids
    only): `absent`, and `unknown` once the withdrawal is a day old (_UNKNOWN_AFTER)."""
    for wanted, outcome in read.looked_up:
        if outcome == "unknown" and ctx.now - wanted.occurred_at < _UNKNOWN_AFTER:
            continue
        if outcome not in ("absent", "unknown"):
            continue
        append_event(
            ctx,
            EventType.WALLET_DEPOSIT_LOOKED_UP,
            start.account_id,
            WalletDepositLookedUp(
                sync_id=start.sync_id,
                address_id=start.address_id,
                movement_id=wanted.movement_id,
                outcome=outcome,
                source_host=start.host,
            ),
        )


def remembered_unknown(conn: Connection, movement_ids: list[UUID]) -> set[UUID]:
    """The withdrawals some balance-only address's node did not know by their hash a day
    after they were sent: the card offers them to an own wallet by hand like a withdrawal
    with no hash."""
    if not movement_ids:
        return set()
    e = event_table.c
    wanted = {str(i) for i in movement_ids}
    return {
        UUID(p["movement_id"])
        for p in conn.execute(
            select(e.payload).where(
                e.type == EventType.WALLET_DEPOSIT_LOOKED_UP.value,
                e.payload["outcome"].astext == "unknown",
            )
        ).scalars()
        if p and p.get("movement_id") in wanted
    }


def wallets_awaiting_deposits(conn: Connection) -> list[UUID]:
    """The wallets with an active address on a balance-only chain (BNB Smart Chain) for
    which some exchange withdrawal on that chain names a hash that is not linked, not held
    as a group of the wallet and not remembered as looked up for it: their next sync has a
    deposit to look up."""
    chains = [r.code for r in registry().chains if r.source is ChainSourceKind.EVM_RPC_BALANCE]
    if not chains:
        return []
    ad = address_table.c
    wallets = conn.execute(
        select(ad.account_id, ad.chain)
        .distinct()
        .where(ad.active.is_(True), ad.chain.in_([c.value for c in chains]))
        .order_by(ad.account_id, ad.chain)
    ).all()
    if not wallets:
        return []
    linked: set[UUID] | None = None
    out: list[UUID] = []
    for account_id, chain in wallets:
        if account_id in out:
            continue
        rows = _hashed_withdrawals(conn, Chain(chain))
        if not rows:
            continue
        linked = _linked_legs(conn) if linked is None else linked
        held = _held_hashes(conn, account_id)
        e = event_table.c
        remembered = {
            str(p.get("movement_id"))
            for p in conn.execute(
                select(e.payload).where(
                    e.type == EventType.WALLET_DEPOSIT_LOOKED_UP.value,
                    e.entity_id == account_id,
                )
            ).scalars()
            if p
        }
        if any(
            r.id not in linked
            and str(r.id) not in remembered
            and (h := _evm_hash(r.tx_hash)) is not None
            and h not in held
            for r in rows
        ):
            out.append(account_id)
    return out


def queue_deposit_lookups(ctx: CommandContext) -> int:
    """D181 (c) amended 16 Nov 2026: a sync queued, in the caller's transaction, for each
    wallet that has an exchange withdrawal to look up (wallets_awaiting_deposits), so the
    deposit is written and linked without waiting for the daily run: called after an
    exchange import or a person's answer on an exchange's movement, and by `hausbuch
    relink-chain`. Never calls the node itself (the caller holds a request's transaction).
    The count of syncs queued."""
    from hausbuch.jobs.sync_wallets import enqueue_sync_wallet

    accounts = wallets_awaiting_deposits(ctx.conn)
    for account_id in accounts:
        enqueue_sync_wallet(ctx.conn, workspace_id=ctx.workspace_id, account_id=account_id)
    return len(accounts)


# --- The run ---------------------------------------------------------------------------------


def _log(outcome: SyncOutcome, exc: BaseException | None = None) -> None:
    fields = {
        "event": "wallet_sync",
        "chain": outcome.chain.value,
        "status": None if outcome.status is None else outcome.status.value,
        "error_code": outcome.error_code,
        "txs_seen": outcome.txs_seen,
        "movements_new": outcome.movements_new,
        "deferred": outcome.deferred,
        "reconciled": outcome.reconciled,
        "waiting": outcome.waiting,
        "spam_dropped": outcome.spam_dropped,
        "unstorable": outcome.unstorable,
        "card": outcome.card,
    }
    if exc is not None:
        fields["exc_type"] = type(exc).__name__
    quiet = outcome.error_code in (None, WALLET_SYNC_DEFERRED) and not outcome.unstorable
    quiet = quiet and outcome.card != WALLET_TOKEN_UNREADABLE
    level = logging.INFO if quiet else logging.WARNING
    logger.log(level, "a wallet address was read from its chain", extra={"fields": fields})


def _fail(
    engine: Engine,
    workspace_id: UUID,
    actor: Actor,
    clock: Clock,
    start: _Start,
    code: str,
    txs_seen: int,
    exc: BaseException,
) -> SyncOutcome:
    status = _STATUS_OF.get(code, WalletSyncStatus.FAILED)
    with workspace_transaction(engine, workspace_id) as conn:
        _finish(
            new_command(conn, workspace_id, actor, now=clock()),
            start,
            {"status": status.value, "error_code": code, "txs_seen": txs_seen},
            WalletSyncRecorded(
                sync_id=start.sync_id,
                address_id=start.address_id,
                status=status.value,
                source_host=start.host,
                txs_seen=txs_seen,
                movements_new=0,
                error_code=code,
            ),
        )
    outcome = SyncOutcome(
        start.address_id, start.chain, start.sync_id, status, error_code=code, txs_seen=txs_seen
    )
    _log(outcome, exc)
    return outcome


def _begin(
    engine: Engine,
    workspace_id: UUID,
    address_id: UUID,
    settings: Settings,
    actor: Actor,
    clock: Clock,
    budgets: dict,
) -> tuple[_Start, ChainSource, ClassifyContext, RequestBudget] | SyncOutcome:
    with workspace_transaction(engine, workspace_id) as conn:
        row = conn.execute(
            select(address_table).where(address_table.c.id == address_id)
        ).one_or_none()
        if row is None:
            raise LookupError("wallet address not found")
        chain = Chain(row.chain)
        kind = registry().chain(chain).source
        budget = budgets.get(kind)
        if budget is None:
            budget = budgets[kind] = RequestBudget(budget_limit(kind, settings))
        try:
            source = source_for(chain, settings, budget, conn=conn)  # D260: typed keys first
        except ChainSourceError as exc:
            outcome = SyncOutcome(address_id, chain, None, None, error_code=exc.code)
            _log(outcome)
            return outcome
        except NotImplementedError:  # base.factory_for: no source of this kind is built yet
            outcome = SyncOutcome(
                address_id, chain, None, None, error_code=WALLET_SOURCE_UNAVAILABLE
            )
            _log(outcome)
            return outcome
        if budget.exhausted:
            # The run's budget ended before this address: no row, so the address keeps its
            # place (never synced, or synced longest ago) and is read first the next day.
            outcome = SyncOutcome(
                address_id, chain, None, None, error_code=WALLET_SYNC_DEFERRED, deferred=True
            )
            _log(outcome)
            return outcome
        now = clock()
        native = chain_asset(new_command(conn, workspace_id, actor, now=now), AssetKey(chain=chain))
        own = _own_of(conn, row.account_id, chain)
        own.setdefault(row.address, _Own(row.id, bytes(row.address_hmac).hex()))
        cursor, caught_up = _last_cursor(conn, address_id)
        # Bitcoin: the account's „Nein, Empfänger“ answers (D185 (2)), so none is asked again
        payees = (
            answered_payee(conn, row.account_id, settings.iban_hmac_key)
            if chain is Chain.BITCOIN
            else None
        )
        ctx = _context(
            conn,
            chain,
            row.address,
            row.account_id,
            own,
            native.id,
            payees,
            settings.iban_hmac_key,
        )
        reads_tokens = getattr(source, "reads_tokens", False) is True
        stored_tokens = _stored_tokens(conn, chain, own) if reads_tokens else {}
        host = _host(source)
        balance_only = getattr(source, "balance_only", False) is True
        lookups = (
            _deposit_candidates(conn, chain, row.account_id, address_id, host)
            if balance_only
            else ()
        )
        if lookups:  # the hash as the node gives it back: the deposit reads as the exchange's
            ctx = replace(ctx, exchange_hashes=ctx.exchange_hashes | {x.tx_hash for x in lookups})
        sync_id = conn.execute(
            insert(sync_table)
            .values(
                workspace_id=workspace_id,
                account_id=row.account_id,
                address_id=address_id,
                started_at=now,
                status=WalletSyncStatus.RUNNING.value,
                source_host=host,
                cursor_before=cursor,
                created_at=now,
            )
            .returning(sync_table.c.id)
        ).scalar_one()
    start = _Start(
        sync_id=sync_id,
        address_id=address_id,
        account_id=row.account_id,
        chain=chain,
        address=row.address,
        own=own,
        cursor=cursor,
        first_sync=not caught_up,
        started_at=now,
        host=host,
        scope=reconcile_scope(source),
        balance_only=balance_only,
        reads_tokens=reads_tokens,
        stored_tokens=stored_tokens,
        hmac_key=settings.iban_hmac_key,
        lookups=lookups,
    )
    return start, source, ctx, budget


def sync_address(
    engine: Engine,
    workspace_id: UUID,
    address_id: UUID,
    *,
    settings: Settings,
    store: BlobStore,
    actor: Actor,
    clock: Clock,
    budgets: dict | None = None,
    account_pending: bool = False,
) -> SyncOutcome:
    """One run of one address (see the module docstring). Never raises for the provider's,
    the classifier's or the write's failure: the address's row says it. A start that fails
    (an address not found) raises; sync_wallets ends that address only. `budgets` holds
    one RequestBudget per source kind, shared by the addresses of one job run.
    `account_pending`: another address of the same account and chain runs after this one in
    the same job, so an account-scope difference is left to that run."""
    begun = _begin(
        engine,
        workspace_id,
        address_id,
        settings,
        actor,
        clock,
        {} if budgets is None else budgets,
    )
    if isinstance(begun, SyncOutcome):
        return begun
    start, source, ctx, budget = begun
    try:
        stored = _stored_records(engine, workspace_id, store, start.address_id)
        read = _read(source, start, ctx, budget, stored)
    except ChainSourceError as exc:
        return _fail(engine, workspace_id, actor, clock, start, exc.code, 0, exc)
    except SourceError as exc:
        return _fail(engine, workspace_id, actor, clock, start, error_from(exc).code, 0, exc)
    except Exception as exc:  # noqa: BLE001 - a classifier's failure: its class name only
        return _fail(engine, workspace_id, actor, clock, start, WALLET_SYNC_ERROR, 0, exc)
    try:
        with workspace_transaction(engine, workspace_id) as conn:
            ctx_w = new_command(conn, workspace_id, actor, now=clock())
            outcome = _write(ctx_w, store, start, read, account_pending)
    except _BalanceUnread as exc:  # nothing written: the failure the balance read was
        return _fail(engine, workspace_id, actor, clock, start, exc.code, 0, exc.cause or exc)
    except Exception as exc:  # noqa: BLE001 - the write rolled back; its class name only
        return _fail(
            engine, workspace_id, actor, clock, start, WALLET_SYNC_ERROR, len(read.txs), exc
        )
    _log(outcome)
    return outcome


# --- A wallet, a workspace -------------------------------------------------------------------


def addresses_to_sync(conn: Connection, account_id: UUID | None = None) -> list[UUID]:
    """The active addresses (of one wallet, or of every wallet), the one synced longest ago
    first, never synced before all (D182 (1))."""
    last = (
        select(sync_table.c.address_id, func.max(sync_table.c.started_at).label("at"))
        .group_by(sync_table.c.address_id)
        .subquery()
    )
    query = (
        select(address_table.c.id)
        .select_from(address_table.outerjoin(last, last.c.address_id == address_table.c.id))
        .where(address_table.c.active.is_(True))
        .order_by(last.c.at.asc().nulls_first(), address_table.c.created_at, address_table.c.id)
    )
    if account_id is not None:
        query = query.where(address_table.c.account_id == account_id)
    return list(conn.execute(query).scalars())


def sync_wallets(
    engine: Engine,
    workspace_id: UUID,
    *,
    settings: Settings,
    store: BlobStore,
    actor: Actor,
    clock: Clock,
    account_id: UUID | None = None,
    budgets: dict | None = None,
) -> list[SyncOutcome]:
    """Every active address of one wallet (account_id) or of the workspace, oldest sync
    first, each in its own run; the budgets are shared per source kind (D182 (1)). The
    addresses of one account and chain run one after another, so the last of them reconciles
    an account-scope source with every address's movements stored."""
    budgets = {} if budgets is None else budgets
    with workspace_transaction(engine, workspace_id) as conn:
        ids = addresses_to_sync(conn, account_id)
        keys = (
            {
                r.id: (r.account_id, r.chain)
                for r in conn.execute(
                    select(
                        address_table.c.id, address_table.c.account_id, address_table.c.chain
                    ).where(address_table.c.id.in_(ids))
                )
            }
            if ids
            else {}
        )
    groups: dict[tuple, list[UUID]] = {}
    for address_id in ids:  # dicts keep order: each group where its oldest address stood
        groups.setdefault(keys[address_id], []).append(address_id)
    outcomes: list[SyncOutcome] = []
    for group in groups.values():
        for n, address_id in enumerate(group):
            try:
                outcome = sync_address(
                    engine,
                    workspace_id,
                    address_id,
                    settings=settings,
                    store=store,
                    actor=actor,
                    clock=clock,
                    budgets=budgets,
                    account_pending=n < len(group) - 1,
                )
            except Exception as exc:  # noqa: BLE001 - the start failed (the address gone): this
                # address only, the next one runs; its class name only
                outcome = SyncOutcome(
                    address_id, Chain(keys[address_id][1]), None, None, error_code=WALLET_SYNC_ERROR
                )
                _log(outcome, exc)
            outcomes.append(outcome)
    return outcomes


__all__ = [
    "WALLET_KEY_MISSING",
    "WALLET_SYNC_ERROR",
    "ChainAsset",
    "SyncOutcome",
    "addresses_to_sync",
    "chain_asset",
    "sync_address",
    "sync_wallets",
]
