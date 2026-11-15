"""EVM: Etherscan V2 records to `ChainTx` and their movement group, the wrap decoder, the
ERC-20/721/1155 spam rule and the balance-only comparison of BNB Smart Chain
(docs/phases/phase-7.md D181 (b), (c), D183, D187, §6); step 3. Fixtures
tests/fixtures/phase7/evm_tx.yaml, spam.yaml (EVM cases), wallet_reconcile.yaml (EVM cases).

Pure, no I/O, no model (principle 1): the same stored records always give the same movements,
whoever served them (D192), which is what `hausbuch replay-sync` relies on.

- `normalise(chain, records, address)`: the five Etherscan lists of one address (`txlist`,
  `txlistinternal`, `tokentx`, `tokennfttx`, `token1155tx`, each a list of result rows) to
  one `ChainTx` per transaction hash. The fee is gasUsed × gasPrice of a `txlist` row the
  address sent, also on a failed one (`isError = 1`), whose other legs are dropped. Indexes
  (D182 (2)): `value` for the top-level native value, `internal:<traceId>` for an internal
  transfer, the log index for an ERC-20 row that carries one; Etherscan's token and NFT rows
  carry no `logIndex` (its documentation, read 15 Nov 2026), so such a row is indexed
  `log:<16 hex>` (an ERC-20) or `nft:<16 hex>` (an ERC-721 or ERC-1155) over its contract,
  token id, sender, recipient and value, with `:<n>` for a repeat inside one transaction.
- `deposit_from_receipt(chain, transaction, receipt, occurred_at, address)`: BNB Smart Chain is
  balance-only (D181 (c)); an exchange withdrawal named by hash is read from
  `eth_getTransactionByHash` and `eth_getTransactionReceipt`, a native value to the address
  (index `value`) or a BEP-20 `Transfer` log to it (index: the log index in decimal).
- `classify_evm(tx, address, ...)`: D183 (3) for one address's legs, as `EvmGroup` in integer
  base units (`to_draft` converts to the stored `MovementGroupDraft` once the assets are
  known: an 18-decimal amount fits `Quantity` only after the quantity migration).
- `spam_verdict`, `token_label`: D187 (1)–(4) and the label of a token the registry does not
  list (no family-neutral spam helper existed when step 3 started; the orchestrator may move
  them to rules/chain/spam.py).
- `reconcile_balance_only`: D181 (c), D186 (2) for a balance-only address.

**Reading (D183 (3) (d) against (e))**: a registry or held token credited by a third party's
transaction is a plain transfer in (`evm_tx.yaml` `erc20_in`: an `uncategorized` leg and
`transfer.unmatched_leg`) unless it was minted to the address (the sender is the zero
address), which is an airdrop; `spam.yaml`'s `registry_token_airdropped` and
`held_token_not_spam` have no sender and replay through `spam_verdict` as an airdrop.
"""

import hashlib
from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, JsonValue

from hausbuch.rules import errors as E
from hausbuch.rules.chain.decoders import public_decoders
from hausbuch.rules.chain.registry import registry
from hausbuch.rules.household.assets import registry as asset_registry
from hausbuch.schemas.chain import (
    AssetKey,
    Chain,
    ChainFamily,
    ChainTx,
    DecoderKind,
    DecoderRow,
    GroupKind,
    MovementGroupDraft,
    RawTransfer,
)
from hausbuch.schemas.common import Schema
from hausbuch.schemas.household import (
    AssetMovementDraft,
    MovementKind,
    MovementSource,
    TransferLinkKind,
)

ACTIONS = ("txlist", "txlistinternal", "tokentx", "tokennfttx", "token1155tx")
NATIVE_INDEX = "value"
FEE_INDEX = "fee"
DEPOSIT_INDEX = "deposit"  # a wrap's in-leg: WETH9's deposit() emits no Transfer
WITHDRAW_INDEX = "withdraw"  # an unwrap's out-leg: WETH9's withdraw() emits no Transfer
NFT_PREFIX = "nft:"
DEPOSIT_SELECTOR = "0xd0e30db0"  # deposit()
WITHDRAW_SELECTOR = "0x2e1a7d4d"  # withdraw(uint256)
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO_ADDRESS = "0x" + "0" * 40

Role = Literal["in", "out", "fee"]


def _lower(value: object) -> str | None:
    text = str(value or "").strip().lower()
    return text or None


def _int(value: object) -> int:
    text = str(value or "0").strip()
    return int(text, 16) if text.startswith("0x") else int(text or "0")


def native(chain: Chain) -> AssetKey:
    return AssetKey(chain=chain, contract=None)


def _evm(chain: Chain) -> None:
    if registry().chain(chain).family is not ChainFamily.EVM:
        raise ValueError(f"{chain} is no EVM chain")


# --- Etherscan records to ChainTx -----------------------------------------------------------


def _record_index(prefix: str, row: Mapping[str, JsonValue], seen: dict[str, int]) -> str:
    parts = [
        _lower(row.get("contractAddress")) or "",
        str(row.get("tokenID") or ""),
        _lower(row.get("from")) or "",
        _lower(row.get("to")) or "",
        str(row.get("value") or row.get("tokenValue") or ""),
    ]
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:16]
    index = f"{prefix}{digest}"
    seen[index] = seen.get(index, 0) + 1
    return index if seen[index] == 1 else f"{index}:{seen[index]}"


def normalise(
    chain: Chain, records: Mapping[str, Sequence[Mapping[str, JsonValue]]], address: str
) -> list[ChainTx]:
    """One ChainTx per transaction hash of the address's Etherscan rows, by block then hash.
    `raw` holds the rows of that hash per list, so the stored original replays to it."""
    _evm(chain)
    own = address.lower()
    by_hash: dict[str, dict[str, list[Mapping[str, JsonValue]]]] = {}
    for action in ACTIONS:
        for row in records.get(action) or ():
            tx_hash = _lower(row.get("hash"))
            if tx_hash is None:
                continue
            by_hash.setdefault(tx_hash, {}).setdefault(action, []).append(row)
    out = [_tx(chain, tx_hash, rows, own) for tx_hash, rows in by_hash.items()]
    # A transaction the address neither paid for nor moved a unit in (a zero-value token row,
    # as an address-poisoning transfer sends; a third party's failed call to it) is no group.
    out = [
        t
        for t in out
        if t.fee is not None or any(own in (x.from_addr, x.to_addr) for x in t.transfers)
    ]
    return sorted(out, key=lambda t: (t.block, t.hash))


def _tx(
    chain: Chain, tx_hash: str, rows: dict[str, list[Mapping[str, JsonValue]]], own: str
) -> ChainTx:
    first = next(r for action in ACTIONS for r in rows.get(action, ()))
    nat = native(chain)
    status: Literal["ok", "failed"] = "ok"
    fee: tuple[AssetKey, int] | None = None
    transfers: list[RawTransfer] = []
    calls: list[tuple[str, str | None]] = []
    for row in rows.get("txlist", ()):
        if str(row.get("isError") or "0") == "1" or str(row.get("txreceipt_status")) == "0":
            status = "failed"
        sender = _lower(row.get("from"))
        if sender == own:
            fee = (nat, _int(row.get("gasUsed")) * _int(row.get("gasPrice")))
        to = _lower(row.get("to")) or _lower(row.get("contractAddress"))
        if to is not None:
            calls.append((to, _selector(row)))
    if status == "ok":
        for row in rows.get("txlist", ()):
            units = _int(row.get("value"))
            if units:
                to = _lower(row.get("to")) or _lower(row.get("contractAddress"))
                transfers.append(_transfer(nat, row, to, units, NATIVE_INDEX))
        seen: dict[str, int] = {}
        for row in rows.get("txlistinternal", ()):
            units = _int(row.get("value"))
            if units and str(row.get("isError") or "0") != "1":
                trace = str(row.get("traceId") or "").strip()
                index = f"internal:{trace}" if trace else _record_index("internal:", row, seen)
                transfers.append(_transfer(nat, row, _lower(row.get("to")), units, index))
        for row in rows.get("tokentx", ()):
            units = _int(row.get("value"))
            contract = _lower(row.get("contractAddress"))
            if units and contract:
                log = str(row.get("logIndex") or "").strip()
                index = log if log else _record_index("log:", row, seen)
                key = AssetKey(chain=chain, contract=contract)
                transfers.append(_transfer(key, row, _lower(row.get("to")), units, index))
        for action in ("tokennfttx", "token1155tx"):
            for row in rows.get(action, ()):
                contract = _lower(row.get("contractAddress"))
                units = 1 if action == "tokennfttx" else _int(row.get("tokenValue"))
                if units and contract:
                    key = AssetKey(chain=chain, contract=contract)
                    index = _record_index(NFT_PREFIX, row, seen)
                    transfers.append(_transfer(key, row, _lower(row.get("to")), units, index))
    return ChainTx(
        hash=tx_hash,
        block=_int(first.get("blockNumber")),
        occurred_at=datetime.fromtimestamp(_int(first.get("timeStamp")), tz=UTC),
        status=status,
        fee=fee,
        transfers=transfers,
        program_calls=calls,
        raw={action: list(r) for action, r in rows.items()},  # type: ignore[misc]
    )


def _selector(row: Mapping[str, JsonValue]) -> str | None:
    method = _lower(row.get("methodId"))
    if method and method != "0x":
        return method
    data = _lower(row.get("input")) or ""
    return data[:10] if data.startswith("0x") and len(data) >= 10 else None


def _transfer(
    key: AssetKey, row: Mapping[str, JsonValue], to: str | None, units: int, index: str
) -> RawTransfer:
    sender = _lower(row.get("from"))
    return RawTransfer(
        asset_key=key,
        from_addr=None if sender in (None, ZERO_ADDRESS) else sender,
        to_addr=None if to in (None, ZERO_ADDRESS) else to,
        units=units,
        index=index,
    )


# --- BNB Smart Chain: a deposit read by its hash (D181 (c)) ---------------------------------


def deposit_from_receipt(
    chain: Chain,
    transaction: Mapping[str, JsonValue],
    receipt: Mapping[str, JsonValue],
    occurred_at: datetime,
    address: str,
) -> ChainTx | None:
    """The legs a transaction credited to the address, from `eth_getTransactionByHash` and
    `eth_getTransactionReceipt`; None when it failed or credited the address nothing (the
    withdrawal went elsewhere). The address paid no fee: the exchange sent it."""
    _evm(chain)
    own = address.lower()
    if _int(receipt.get("status")) != 1:
        return None
    transfers: list[RawTransfer] = []
    value = _int(transaction.get("value"))
    if value and _lower(transaction.get("to")) == own:
        transfers.append(
            RawTransfer(
                asset_key=native(chain),
                from_addr=_lower(transaction.get("from")),
                to_addr=own,
                units=value,
                index=NATIVE_INDEX,
            )
        )
    logs = receipt.get("logs") or []
    for log in logs if isinstance(logs, list) else []:
        if not isinstance(log, dict):
            continue
        topics = [str(t).lower() for t in log.get("topics") or []]  # type: ignore[union-attr]
        if len(topics) != 3 or topics[0] != TRANSFER_TOPIC:
            continue  # an ERC-721 Transfer has 4 topics: never a BEP-20 deposit
        to = "0x" + topics[2][-40:]
        data = str(log.get("data") or "").strip()
        if data in ("", "0x"):
            continue  # a Transfer event with no amount moves nothing
        units = _int(data)
        contract = _lower(log.get("address"))
        if to != own or not units or contract is None:
            continue
        sender = "0x" + topics[1][-40:]
        transfers.append(
            RawTransfer(
                asset_key=AssetKey(chain=chain, contract=contract),
                from_addr=None if sender == ZERO_ADDRESS else sender,
                to_addr=own,
                units=units,
                index=str(_int(log.get("logIndex"))),
            )
        )
    if not transfers:
        return None
    return ChainTx(
        hash=str(transaction.get("hash") or receipt.get("transactionHash")).lower(),
        block=_int(transaction.get("blockNumber") or receipt.get("blockNumber")),
        occurred_at=occurred_at,
        status="ok",
        fee=None,
        transfers=transfers,
        program_calls=[],
        raw={"transaction": dict(transaction), "receipt": dict(receipt)},
    )


# --- D187: spam --------------------------------------------------------------------------


def mask(text: str) -> str:
    """D179: the first four and the last four characters (address.mask is step 1's; the same
    form for a contract)."""
    return f"{text[:4]}…{text[-4:]}"


def token_label(chain: Chain, contract: str, symbol: str) -> str:
    """D187 (2): the registry's symbol for a listed token, else the contract's mask with the
    symbol it calls itself, so a copied symbol is never shown alone."""
    listed = asset_registry().token(chain, contract)
    if listed is not None:
        return listed.symbol
    return f"Token {mask(contract)} (nennt sich {symbol})"


def is_spam(key: AssetKey, *, held: bool, fee_paid: bool, bought: bool) -> bool:
    """D187 (1), (4): a token the registry does not list, the workspace holds with no confirmed
    lot, moved in a transaction the address did not pay for and did not buy it in."""
    if key.contract is None:
        return False  # the native coin is never spam
    listed = asset_registry().token(key.chain, key.contract)
    return listed is None and not held and not fee_paid and not bought


class SpamVerdict(Schema):
    group: GroupKind
    movement: MovementKind
    hidden: bool
    card: bool
    label: str
    price_source_needed: bool


def spam_verdict(
    chain: Chain,
    contract: str,
    symbol: str,
    *,
    held: bool = False,
    fee_paid: bool = False,
    bought: bool = False,
    action: Literal["promote", "demote"] | None = None,
) -> SpamVerdict:
    """D187 (1)–(4) for one credited token: spam (confirmed, hidden, no price), an airdrop
    (review, a card), or a swap's bought leg; „Das ist echt“ (promote) turns spam into an
    airdrop that needs a price source, a demotion the reverse. Fixture spam.yaml."""
    listed = asset_registry().token(chain, contract)
    spam = is_spam(
        AssetKey(chain=chain, contract=contract), held=held, fee_paid=fee_paid, bought=bought
    )
    if action == "promote" and spam:
        spam = False
        kind, movement = GroupKind.AIRDROP, MovementKind.AIRDROP
    elif action == "demote":
        spam = True
        kind, movement = GroupKind.SPAM, MovementKind.SPAM
    elif spam:
        kind, movement = GroupKind.SPAM, MovementKind.SPAM
    elif bought:
        kind, movement = GroupKind.SWAP, MovementKind.ACQUISITION
    else:
        kind, movement = GroupKind.AIRDROP, MovementKind.AIRDROP
    label = (
        f"Token {mask(contract)} (nennt sich {symbol})" if spam or listed is None else listed.symbol
    )
    return SpamVerdict(
        group=kind,
        movement=movement,
        hidden=spam,
        card=kind is GroupKind.AIRDROP,
        label=label,
        price_source_needed=not spam and listed is None,
    )


# --- D183: one address's legs to its group -----------------------------------------------


class EvmLeg(Schema):
    asset_key: AssetKey
    kind: MovementKind
    role: Role
    units: int = Field(gt=0)  # base units, unsigned: `role` gives the sign
    index: str = Field(min_length=1, max_length=64)


class EvmGroup(Schema):
    """One transaction's group for one own address, in base units (D183 (1)). `card` is the
    code of the card it raises when a person must answer (D186's routing decides the rest);
    `fee_only` marks a group that moves nothing but its fee (an approval)."""

    chain: Chain
    tx_hash: str
    occurred_at: datetime
    kind: GroupKind
    decoder: str | None = None
    legs: list[EvmLeg] = Field(default_factory=list)
    card: str | None = None
    fee_only: bool = False
    link: TransferLinkKind | None = None  # the link the group's legs form (a wrap's pair)
    first_seen_tokens: list[AssetKey] = Field(default_factory=list)  # not in the registry


def _wrap_row(
    chain: Chain, calls: Sequence[tuple[str, str | None]], decoders: Sequence[DecoderRow]
) -> tuple[DecoderRow, str, str | None] | None:
    for to, method in calls:
        for row in decoders:
            if (
                row.chain is chain
                and row.kind is DecoderKind.WRAP
                and row.program == to
                and row.method in (None, method)
            ):
                return row, to, method
    return None


def _input_word(tx: ChainTx) -> int | None:
    rows = tx.raw.get("txlist")
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    data = str(rows[0].get("input") or "")
    if len(data) < 74:
        return None
    try:
        return int(data[10:74], 16)
    except ValueError:
        return None


def _leg(t: RawTransfer, kind: MovementKind, role: Role) -> EvmLeg:
    return EvmLeg(asset_key=t.asset_key, kind=kind, role=role, units=t.units, index=t.index)


def classify_evm(
    chain: Chain,
    tx: ChainTx,
    address: str,
    *,
    decoders: Sequence[DecoderRow] | None = None,
    own: Collection[str] = (),
    exchange_hashes: Collection[str] = (),
    held: Collection[AssetKey] = (),
) -> EvmGroup:
    """D183 (3) for the legs of `address` in one EVM transaction: (a) a counterparty among the
    other own addresses → `transfer` between own (internal legs); (b) a hash an own exchange
    withdrawal names → `exchange_transfer`; (c) the registry's wrap by shape (native into
    the wrap contract, or its withdraw) → `wrap`; (d) one plain leg in or out → `transfer`
    with an `uncategorized` leg and `transfer.unmatched_leg`; (e) a token credited with no own
    action → `airdrop` when minted to the address, `spam` when D187 says so; an ERC-721/1155
    record is always a `spam` leg until promoted; (f) the rest → `contract_call` (the address
    signed) or `unknown`. A failed transaction is `failed` with its fee only (D183 (2)). (a) and
    (b) are decided before the spam test, so their legs are never spam; an unlisted, unheld
    token moved in or out in a transaction the address did not sign is (§15.1)."""
    _evm(chain)
    rows = public_decoders() if decoders is None else decoders
    me = address.lower()
    others = {a.lower() for a in own} - {me}
    hashes = {h.lower() for h in exchange_hashes}
    fee_legs = (
        [
            EvmLeg(
                asset_key=tx.fee[0],
                kind=MovementKind.FEE,
                role="fee",
                units=tx.fee[1],
                index=FEE_INDEX,
            )
        ]
        if tx.fee and tx.fee[1] > 0
        else []
    )
    signed = tx.fee is not None

    def group(kind: GroupKind, legs: list[EvmLeg], **kw: object) -> EvmGroup:
        unlisted = sorted(
            {
                leg.asset_key
                for leg in legs
                if leg.asset_key.contract is not None
                and asset_registry().token(chain, leg.asset_key.contract) is None
            },
            key=lambda k: k.contract or "",
        )
        return EvmGroup(
            chain=chain,
            tx_hash=tx.hash,
            occurred_at=tx.occurred_at,
            kind=kind,
            legs=legs + fee_legs,
            first_seen_tokens=unlisted,
            **kw,  # type: ignore[arg-type]
        )

    if tx.status == "failed":
        return group(GroupKind.FAILED, [])

    # A send to itself nets to nothing: no leg (its in and out would share one index).
    looped = [t for t in tx.transfers if t.from_addr == me and t.to_addr == me]
    ins = [t for t in tx.transfers if t.to_addr == me and t not in looped]
    outs = [t for t in tx.transfers if t.from_addr == me and t not in looped]

    # (c) the registry's wrap, by shape: a decoder row never overrides the shape (D183 (4)).
    wrap = _wrap_row(chain, tx.program_calls, rows) if signed else None
    if wrap is not None:
        row, contract, method = wrap
        wrapped = AssetKey(chain=chain, contract=contract)
        nat = native(chain)
        if method in (None, DEPOSIT_SELECTOR):
            sent = [t for t in outs if t.asset_key == nat and t.to_addr == contract]
            got = [t for t in ins if t.asset_key == wrapped]
            if len(sent) == 1 and len(outs) == 1 and len(ins) <= 1:
                legs = [_leg(sent[0], MovementKind.INTERNAL_TRANSFER_OUT, "out")]
                if got:  # an implementation that mints with a Transfer (Arbitrum's aeWETH)
                    legs.append(_leg(got[0], MovementKind.INTERNAL_TRANSFER_IN, "in"))
                elif not ins:
                    legs.append(
                        EvmLeg(
                            asset_key=wrapped,
                            kind=MovementKind.INTERNAL_TRANSFER_IN,
                            role="in",
                            units=sent[0].units,
                            index=DEPOSIT_INDEX,
                        )
                    )
                if len(legs) == 2 and legs[0].units == legs[1].units:
                    return group(GroupKind.WRAP, legs, decoder=row.name, link=TransferLinkKind.WRAP)
        elif method == WITHDRAW_SELECTOR:
            wad = _input_word(tx)
            back = [t for t in ins if t.asset_key == nat and t.from_addr == contract]
            burned = [t for t in outs if t.asset_key == wrapped]
            if wad and len(back) == 1 and back[0].units == wad and len(ins) == 1:
                if burned and len(outs) == 1 and burned[0].units == wad:
                    out_leg = _leg(burned[0], MovementKind.INTERNAL_TRANSFER_OUT, "out")
                elif not outs:
                    out_leg = EvmLeg(
                        asset_key=wrapped,
                        kind=MovementKind.INTERNAL_TRANSFER_OUT,
                        role="out",
                        units=wad,
                        index=WITHDRAW_INDEX,
                    )
                else:
                    out_leg = None
                if out_leg is not None:
                    in_leg = _leg(back[0], MovementKind.INTERNAL_TRANSFER_IN, "in")
                    return group(
                        GroupKind.WRAP,
                        [out_leg, in_leg],
                        decoder=row.name,
                        link=TransferLinkKind.WRAP,
                    )

    # D187, after (a) and (b): a leg whose counterparty is another own address, or credited in
    # a transaction an own exchange withdrawal names, is never spam. Of the rest, NFT records
    # are spam legs, and so is an unlisted, unheld token moved in a transaction the address
    # did not sign: credited with no own action, or sent out by an event it never made (an
    # address-poisoning `Transfer(victim → lookalike)`), no card either way. What remains
    # decides the group.
    in_hash = tx.hash.lower() in hashes
    held_keys = set(held)
    spam_legs: list[EvmLeg] = []
    for t in ins + outs:
        role: Role = "in" if t.to_addr == me else "out"
        if t.index.startswith(NFT_PREFIX):
            spam_legs.append(_leg(t, MovementKind.SPAM, role))
            continue
        counterparty = t.from_addr if role == "in" else t.to_addr
        if counterparty in others or (role == "in" and in_hash):
            continue
        if is_spam(t.asset_key, held=t.asset_key in held_keys, fee_paid=signed, bought=False):
            spam_legs.append(_leg(t, MovementKind.SPAM, role))
    spam_idx = {leg.index for leg in spam_legs}
    ins = [t for t in ins if t.index not in spam_idx]
    outs = [t for t in outs if t.index not in spam_idx]

    if not ins and not outs:
        if spam_legs:
            return group(GroupKind.SPAM, spam_legs)
        if looped:
            return group(GroupKind.TRANSFER, [], fee_only=True)
        return group(GroupKind.CONTRACT_CALL, [], fee_only=True)

    # (b) an exchange withdrawal's hash: the deposit side of D175.
    if in_hash and ins and not outs:
        legs = [_leg(t, MovementKind.INTERNAL_TRANSFER_IN, "in") for t in ins]
        return group(GroupKind.EXCHANGE_TRANSFER, legs + spam_legs)

    # (a) every value leg between own addresses.
    if others and all(
        (t.from_addr in others) if t in ins else (t.to_addr in others) for t in ins + outs
    ):
        legs = [_leg(t, MovementKind.INTERNAL_TRANSFER_IN, "in") for t in ins] + [
            _leg(t, MovementKind.INTERNAL_TRANSFER_OUT, "out") for t in outs
        ]
        return group(GroupKind.TRANSFER, legs + spam_legs)

    # (d) one plain leg.
    if len(ins) == 1 and not outs:
        t = ins[0]
        if t.asset_key.contract is not None and (signed or t.from_addr is None):
            return group(GroupKind.AIRDROP, [_leg(t, MovementKind.AIRDROP, "in")] + spam_legs)
        if signed:
            return group(
                GroupKind.CONTRACT_CALL,
                [_leg(t, MovementKind.UNCATEGORIZED, "in")] + spam_legs,
                card=E.GROUP_CONTRACT_CALL,
            )
        return group(
            GroupKind.TRANSFER,
            [_leg(t, MovementKind.UNCATEGORIZED, "in")] + spam_legs,
            card=E.TRANSFER_UNMATCHED_LEG,
        )
    if len(outs) == 1 and not ins:
        return group(
            GroupKind.TRANSFER,
            [_leg(outs[0], MovementKind.UNCATEGORIZED, "out")] + spam_legs,
            card=E.TRANSFER_UNMATCHED_LEG,
        )

    # A swap by shape (one asset given, another received in a signed transaction): no EVM
    # swap decoder is listed yet, so it is a card the first time (D183 (4)).
    if signed and len(ins) == 1 and len(outs) == 1 and ins[0].asset_key != outs[0].asset_key:
        legs = [
            _leg(outs[0], MovementKind.DISPOSAL, "out"),
            _leg(ins[0], MovementKind.ACQUISITION, "in"),
        ]
        return group(GroupKind.SWAP, legs + spam_legs)

    if (
        not signed
        and not outs
        and all(t.asset_key.contract is not None and t.from_addr is None for t in ins)
    ):
        return group(
            GroupKind.AIRDROP, [_leg(t, MovementKind.AIRDROP, "in") for t in ins] + spam_legs
        )

    legs = [_leg(t, MovementKind.UNCATEGORIZED, "in") for t in ins] + [
        _leg(t, MovementKind.UNCATEGORIZED, "out") for t in outs
    ]
    if signed:
        return group(GroupKind.CONTRACT_CALL, legs + spam_legs, card=E.GROUP_CONTRACT_CALL)
    return group(GroupKind.UNKNOWN, legs + spam_legs, card=E.GROUP_UNKNOWN)


# --- to the stored shape --------------------------------------------------------------------


def _quantity(units: int, decimals: int) -> Decimal:
    """`units` base units of an asset with `decimals` decimals, built from the integer's own
    digits with no arithmetic, so the context's 28-digit precision never rounds it (2**256 − 1
    stays exact): trailing zeros dropped down to the unit, never a positive exponent (100,
    not 1E+2)."""
    if units == 0:
        return Decimal(0)
    digits = str(abs(units))
    zeros = min(len(digits) - len(digits.rstrip("0")), decimals)
    kept = digits[: len(digits) - zeros]
    return Decimal((int(units < 0), tuple(int(d) for d in kept), zeros - decimals))


def to_draft(
    group: EvmGroup,
    account_id: UUID,
    address_id: UUID,
    assets: Mapping[AssetKey, tuple[UUID, int]],
) -> MovementGroupDraft:
    """The group as movements: each leg's units in its asset's decimals (`assets` maps an
    asset key to its id and decimals), signed on the account (in positive, out and fee
    negative), `source = chain` with the address and index (D182 (2))."""
    movements = []
    for leg in group.legs:
        asset_id, decimals = assets[leg.asset_key]
        quantity = _quantity(leg.units, decimals)
        movements.append(
            AssetMovementDraft(
                account_id=account_id,
                asset_id=asset_id,
                kind=leg.kind,
                quantity=quantity if leg.role == "in" else -quantity,
                occurred_at=group.occurred_at,
                network=group.chain.value,
                tx_hash=group.tx_hash,
                source=MovementSource.CHAIN,
                address_id=address_id,
                index=leg.index,
            )
        )
    return MovementGroupDraft(
        account_id=account_id,
        chain=group.chain,
        tx_hash=group.tx_hash,
        occurred_at=group.occurred_at,
        kind=group.kind,
        decoder=group.decoder,
        movements=movements,
    )


def signed_units(group: EvmGroup) -> dict[AssetKey, int]:
    """Σ of the group's legs per asset in base units, in positive, out and fee negative, spam
    left out (D186 (2), D187 (1)): what reconciliation adds up."""
    sums: dict[AssetKey, int] = {}
    for leg in group.legs:
        if leg.kind is MovementKind.SPAM:
            continue
        sign = 1 if leg.role == "in" else -1
        sums[leg.asset_key] = sums.get(leg.asset_key, 0) + sign * leg.units
    return sums


# --- D181 (c): a balance-only address -----------------------------------------------------


class BalanceOnlyResult(Schema):
    reconciled: bool
    difference: dict[str, int] = Field(default_factory=dict)  # balance − Σ deposits, shortfalls
    card: str | None = None


def reconcile_balance_only(
    balance: Mapping[str, int], linked_deposits: Mapping[str, int]
) -> BalanceOnlyResult:
    """D181 (c), D186 (2): the balance against Σ linked deposits per asset. Below → coins
    left the address (`wallet.coins_left`, the shortfall as a negative difference); above →
    reconciled, the excess an unexplained deposit (`transfer.unmatched_leg`, D175's answers).
    Keys are any asset label (the registry symbol or an AssetKey's text), compared as given."""
    short = {
        a: balance.get(a, 0) - units
        for a, units in linked_deposits.items()
        if balance.get(a, 0) < units
    }
    if short:
        return BalanceOnlyResult(reconciled=False, difference=short, card=E.WALLET_COINS_LEFT)
    above = any(units > linked_deposits.get(a, 0) for a, units in balance.items())
    return BalanceOnlyResult(reconciled=True, card=E.TRANSFER_UNMATCHED_LEG if above else None)
