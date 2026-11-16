"""Wallets, chains, movement groups, lots and the year-end holdings statement
(docs/phases/phase-7.md §11, §12; migration 0041).

Phase 7 step 0 writes the shapes; the steps of §16 fill them (step 1: wallets and
addresses; step 2: Solana, the classifier's groups, D186's routing and D187's spam; step 3:
EVM and BNB Smart Chain's balance; step 4: Bitcoin; step 5: chain transfers; step 6: lots,
the holding period, the Krypto tab, the holdings statement and `priced_as`; step 7: closing).

What a chain reports is held in **integer base units** (satoshi, wei, lamports, a token's
smallest unit) up to the point where a movement is written, since a chain balance must
reconcile to the unit (D185, §9). The conversion to `Quantity` happens once, in the step that
writes the movement. An 18-decimal EVM amount does not fit numeric(28,10), the column as 0041
leaves it, so step 2's own migration widens `asset_movement.quantity` to numeric(38,18),
`asset.decimals` to 0..18 and the `Quantity` wire alias with it, before step 2 writes its
first movement (§15.1 item 9, §16; decided in the step 0 review, 15 Nov 2026).

No address in clear leaves `wallet_address`: every shape here that a screen or an event sees
carries the mask or the HMAC (D179 (1)), and none reaches a model (D189).
"""

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Protocol, Self
from uuid import UUID

from pydantic import BeforeValidator, Field, JsonValue, model_validator

from .common import _STRING_ONLY, Money, Schema, _string_schema
from .household import AssetMovementDraft, AssetSymbol, NonNegativeQuantity, Quantity

# --- Value types (decimal strings on the wire, phase 1 spec amendment 2) ----------------

# numeric(5,4), [0, 1): a bridge's fee share (chain_pair.max_fee_share, D184 (2)).
FeeShare = Annotated[
    Decimal, Field(ge=0, lt=1, max_digits=5, decimal_places=4), _STRING_ONLY, _string_schema(4)
]
# A lot's cost per unit in EUR, computed on read (D188): PriceEur's 8 places, but 0 allowed,
# since a reward lot may carry no cost (phase 8 decides, LotSource.REWARD).
CostPerUnit = Annotated[
    Decimal, Field(ge=0, max_digits=20, decimal_places=8), _STRING_ONLY, _string_schema(8)
]

# --- Value sets (the CHECKs of migration 0041) -------------------------------------------


class Chain(StrEnum):
    """The registry codes (rules/chain/chains.yaml): wallet_address.chain, asset.chain,
    movement_group.chain, chain_decoder.chain, chain_pair.from_chain / to_chain."""

    BITCOIN = "bitcoin"
    ETHEREUM = "ethereum"
    ARBITRUM = "arbitrum"
    BSC = "bsc"
    SOLANA = "solana"


class ChainFamily(StrEnum):
    UTXO = "utxo"
    EVM = "evm"
    SOLANA = "solana"


CHAIN_FAMILY: dict[Chain, ChainFamily] = {
    Chain.BITCOIN: ChainFamily.UTXO,
    Chain.ETHEREUM: ChainFamily.EVM,
    Chain.ARBITRUM: ChainFamily.EVM,
    Chain.BSC: ChainFamily.EVM,
    Chain.SOLANA: ChainFamily.SOLANA,
}


class ChainSourceKind(StrEnum):
    """services/chain/: one adapter per kind behind ChainSource (D181, D192). evm_nodereal is
    designed, not built (D181 (c))."""

    BITCOIN_ESPLORA = "bitcoin_esplora"
    EVM_ETHERSCAN = "evm_etherscan"
    EVM_RPC_BALANCE = "evm_rpc_balance"
    SOLANA_RPC = "solana_rpc"


class AddressKind(StrEnum):
    """wallet_address.kind: change only on Bitcoin (D185), stake only on Solana (D183 (4))."""

    RECEIVE = "receive"
    CHANGE = "change"
    STAKE = "stake"


class GroupKind(StrEnum):
    """movement_group.kind (D183 (2)): the thirteen shapes a transaction is classified as."""

    TRANSFER = "transfer"
    EXCHANGE_TRANSFER = "exchange_transfer"
    SWAP = "swap"
    WRAP = "wrap"
    BRIDGE = "bridge"
    STAKE = "stake"
    REWARD = "reward"
    AIRDROP = "airdrop"
    LP = "lp"
    CONTRACT_CALL = "contract_call"
    FAILED = "failed"
    SPAM = "spam"
    UNKNOWN = "unknown"


class GroupStatus(StrEnum):
    """movement_group.status: proposed -> confirmed only (the trigger group_forward)."""

    PROPOSED = "proposed"
    CONFIRMED = "confirmed"


class WalletSyncStatus(StrEnum):
    """wallet_sync.status (D182 (1)): written running, finished once."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    THROTTLED = "throttled"
    REFUSED = "refused"


class DecoderKind(StrEnum):
    """chain_decoder.kind and a decoders.yaml row's kind: the group kinds a program can name.
    A subset of GroupKind (a decoder never makes a transfer, a failure or spam, which come
    from the transaction's shape, D183 (3))."""

    SWAP = "swap"
    WRAP = "wrap"
    BRIDGE = "bridge"
    STAKE = "stake"
    REWARD = "reward"
    LP = "lp"


class ChainPairKind(StrEnum):
    """chain_pair.kind (D191): a confirmed bridge or wrap pair."""

    BRIDGE = "bridge"
    WRAP = "wrap"


class OpenWalletCode(StrEnum):
    """The wallet cards on Zu prüfen and the wallet page (§12): the error codes of
    rules/errors.py, by name."""

    WALLET_UNRECONCILED = "wallet.unreconciled"
    WALLET_COINS_LEFT = "wallet.coins_left"
    WALLET_KEY_MISSING = "wallet.key_missing"
    WALLET_SOURCE_THROTTLED = "wallet.source_throttled"
    WALLET_SOURCE_REFUSED = "wallet.source_refused"
    WALLET_CHANGE_CANDIDATE = "wallet.change_candidate"
    GROUP_UNKNOWN = "group.unknown"
    GROUP_CONTRACT_CALL = "group.contract_call"
    GROUP_UNPRICED = "group.unpriced"


# Units on a chain: a non-negative integer of the asset's smallest denomination. Never sent to
# a browser at step 0; a JSON client would need it as a string (above 2^53).
BaseUnits = int


def _evm_lower(value: object) -> object:
    """D180 amended 15 Nov 2026: an EVM address is held lower-case, in the registry files, in
    every lookup and in the database (migration 0041's CHECK), so a contract never misses on
    letter case; EIP-55 is a display form only (step 1). Base58 has no „0“, so a „0x“ prefix
    is EVM's and a Solana mint is never touched (it is case-sensitive)."""
    return value.lower() if isinstance(value, str) and value.startswith("0x") else value


# A contract, mint or program id as the chain writes it: EVM hex lower-cased, Solana base58 as
# written.
ContractId = Annotated[str, BeforeValidator(_evm_lower)]
# A token's symbol as its contract names it (D180 (2) amended 15 Nov 2026): a display label,
# not unique, any 1 to 32 characters without a control character (wstETH, USD₮0); a chain
# asset without a contract (a native coin) keeps AssetSymbol, as migration 0041's CHECK does.
TokenSymbol = Annotated[
    str, Field(min_length=1, max_length=32, pattern=r"^[^\x00-\x1f\x7f-\x9f]+$")
]


# --- The public registry (rules/chain/chains.yaml, rules/household/assets.yaml) ------------


class BridgePair(Schema):
    """One canonical bridge in the registry (D184 (2)): the same asset on two chains."""

    from_chain: Chain
    from_contract: ContractId | None = None  # None: the chain's native coin
    to_chain: Chain
    to_contract: ContractId | None = None
    max_fee_share: FeeShare

    @model_validator(mode="after")
    def _two_chains(self) -> Self:
        if self.from_chain == self.to_chain:
            raise ValueError("a bridge joins two chains")
        return self


class WrapPair(Schema):
    """One wrap in the registry (D184 (3)): the native coin and its wrapped token on one chain."""

    native: AssetSymbol
    wrapped: ContractId = Field(min_length=20, max_length=128)


class ChainRegistryRow(Schema):
    code: Chain
    family: ChainFamily
    chain_id: int | None = None  # EVM only (EIP-155)
    native_symbol: AssetSymbol
    native_decimals: int = Field(ge=0, le=18)
    address_format: Literal["bech32_or_base58check", "eip55_or_lower_hex", "base58_32_bytes"]
    source: ChainSourceKind
    explorer_tx_url: str = Field(pattern=r"^https://[^\s]+\{hash\}[^\s]*$")
    confirmations_final: int = Field(ge=0)
    # Binance's network names for this chain (D184 (1)(a)); BISON's rows name none (Q-O76).
    exchange_networks: dict[str, Chain] = Field(default_factory=dict)
    bridge_pairs: list[BridgePair] = Field(default_factory=list)
    wraps: list[WrapPair] = Field(default_factory=list)

    @model_validator(mode="after")
    def _family_fields(self) -> Self:
        if (self.family is ChainFamily.EVM) != (self.chain_id is not None):
            raise ValueError("an EVM chain, and only an EVM chain, has a chain_id")
        return self


class TokenRegistryRow(Schema):
    """A token the registry knows on one chain (rules/household/assets.yaml `tokens:`, D180):
    never spam, priced through its CoinGecko id or the coin it wraps."""

    chain: Chain
    contract: ContractId = Field(min_length=20, max_length=128)
    symbol: TokenSymbol
    decimals: int = Field(ge=0, le=18)
    coingecko_id: str | None = Field(default=None, pattern=r"^[a-z0-9-]{1,100}$")
    wraps: AssetSymbol | None = None  # priced as this coin (D180 (3))
    name: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def _priced(self) -> Self:
        if self.coingecko_id is None and self.wraps is None:
            raise ValueError("a registry token has a coingecko_id or wraps a coin")
        return self


class DecoderRow(Schema):
    """One row of rules/chain/decoders.yaml or chain_decoder (D183 (4), D191). Never an
    amount: the loader refuses any key not listed here (extra="forbid")."""

    chain: Chain
    program: ContractId = Field(min_length=20, max_length=128)
    method: str | None = Field(default=None, min_length=1, max_length=100)
    kind: DecoderKind
    legs: Literal["swap", "wrap", "stake", "reward", "lp"] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=100)


# --- Addresses (D179, D185) --------------------------------------------------------------


class WalletSyncSummary(Schema):
    finished_at: datetime | None = None
    block: int | None = None
    reconciled: bool | None = None
    error_code: str | None = None
    movements_new: int = Field(default=0, ge=0)


class WalletAddressCreate(Schema):
    """POST /wallets/{id}/addresses: the address is validated and normalised by
    rules/chain/address.validate in the service (a format error is wallet.address_invalid);
    also_on registers the same 0x address on the other EVM chains (D179 (2))."""

    chain: Chain
    address: str = Field(min_length=20, max_length=128)
    label: str | None = Field(default=None, min_length=1, max_length=100)
    also_on: list[Chain] = Field(default_factory=list)


class WalletAddress(Schema):
    id: UUID
    account_id: UUID
    chain: Chain
    address_masked: str
    label: str | None = None
    kind: AddressKind
    active: bool
    last_sync: WalletSyncSummary | None = None


# --- What a chain source answers (services/chain/, D181, D192) ----------------------------


class AssetKey(Schema):
    chain: Chain
    contract: ContractId | None = None  # None: the native coin


class AssetBalance(Schema):
    asset_key: AssetKey
    units: BaseUnits = Field(ge=0)
    at_block: int = Field(ge=0)


class RawTransfer(Schema):
    """One value movement inside a transaction. The addresses are the chain's own strings,
    held in memory only: the classifier compares them with the own addresses' HMACs and no
    row stores a counterparty in clear (D179)."""

    asset_key: AssetKey
    from_addr: str | None = None  # None: minted, or a coinbase input
    to_addr: str | None = None  # None: burned
    units: BaseUnits = Field(ge=0)
    index: str = Field(min_length=1, max_length=64)


class ChainTx(Schema):
    hash: str = Field(min_length=1, max_length=200)
    block: int = Field(ge=0)
    occurred_at: datetime
    status: Literal["ok", "failed"]
    fee: tuple[AssetKey, BaseUnits] | None = None  # paid by the own address, else None
    transfers: list[RawTransfer] = Field(default_factory=list)
    program_calls: list[tuple[str, str | None]] = Field(default_factory=list)
    raw: dict[str, JsonValue] = Field(default_factory=dict)


class SolanaDelta(Schema):
    """D181 (d): one per asset of an own address, from a transaction's pre/post balances;
    a rent delta (a token account opened or closed) is not a movement."""

    asset_key: AssetKey
    units: int  # signed base units
    token_account: str | None = None
    rent: bool = False


class ProviderLabel(Schema):
    """An optional provider's description of a transaction (D192): shown, never a decision
    input; removing the provider changes no kind, link or lot."""

    provider: Literal["helius"]
    text: str = Field(min_length=1, max_length=500)
    fetched_at: datetime


class Page[T](Schema):
    items: list[T]
    next_cursor: str | None = None


class ChainSource(Protocol):
    """services/chain/base.py: every provider replaceable by configuration (D192)."""

    kind: ChainSourceKind

    # `tokens`: token assets to read a balance of besides the source's own list (the tokens
    # the address has non-spam movements in). Passed only to a source that declares
    # `reads_tokens = True` (EVM over Etherscan); the other sources are called without it.
    def balances(self, address: str, *, tokens: Sequence[AssetKey] = ()) -> list[AssetBalance]: ...

    def transactions(self, address: str, cursor: str | None) -> Page[ChainTx]: ...


# --- Movement groups and answers (D183, D186) --------------------------------------------


class MovementGroupDraft(Schema):
    account_id: UUID
    chain: Chain
    tx_hash: str = Field(min_length=1, max_length=200)
    occurred_at: datetime
    kind: GroupKind
    decoder: str | None = Field(default=None, min_length=1, max_length=200)
    movements: list[AssetMovementDraft] = Field(default_factory=list)
    question: str | None = Field(default=None, min_length=1, max_length=500)
    label: ProviderLabel | None = None


class MovementGroup(MovementGroupDraft):
    id: UUID
    status: GroupStatus
    superseded_by: UUID | None = None


class LegPick(Schema):
    """One leg a person picks from the transaction's transfers (the swap answer of D183 (5))."""

    index: str = Field(min_length=1, max_length=64)
    role: Literal["given", "received"]


class GroupAnswer(Schema):
    """POST /groups/{gid}/answer."""

    kind: GroupKind
    legs: list[LegPick] | None = None
    counterpart_account_id: UUID | None = None
    cost_eur: Money | None = None
    learn: bool = False


class GroupRoutingInput(Schema):
    """What rules.review.route_group reads for one group (D186 (1)): fixture cases in phase 2's
    review_routing.yaml `chain_cases`."""

    kind: GroupKind
    # The group moves nothing but its own fee (a token account closed, its rent back): D186
    # (1)'s „a fee“, confirmed like a failed group, also on a first sync.
    fee_only: bool = False
    # A public or workspace decoder row named the program (a wrap: the registry's wrap pair).
    decoder_listed: bool = False
    # Every leg that carries value has a price for the group's Berlin day (D186 (1) „both
    # legs“, D188 (4); §6 examples 3 and 4).
    priced: bool = False
    # Every asset the group moves is a chain's native coin or a registry token (D186 (1)).
    registry_assets: bool = False
    first_sync: bool = False  # the address's first sync (D186 (1): a person reads it once)
    # A transfer's counterpart leg is own and linked (by hash, D184 (1)); a bridge's by a pair.
    linked: bool = False
    pair_learned: bool = False  # a bridge over a pair the workspace's chain_pair rows hold
    unregistered_output: bool = False  # a Bitcoin spend paying an unregistered address (D185)


# --- Lots and the holding period (D187, D188) -------------------------------------------


class LotSource(StrEnum):
    ACQUISITION = "acquisition"  # bought on an exchange or in a swap
    REWARD = "reward"  # a staking reward or airdrop: cost 0 or its value, phase 8 decides
    TRANSFER = "transfer"  # re-created on the receiving account (D184 (4))
    UNKNOWN = "unknown"  # an unmatched in-leg answered without a cost (D175)


class Lot(Schema):
    account_id: UUID
    asset_id: UUID
    acquired_on: date
    units: NonNegativeQuantity
    units_open: NonNegativeQuantity
    cost_eur: Money | None = None
    cost_per_unit: CostPerUnit | None = None
    source: LotSource
    moved_through: list[UUID] = Field(default_factory=list)  # transfer_link ids
    tax_free_from: date


class LotUse(Schema):
    lot_id: UUID
    units: NonNegativeQuantity
    cost_eur: Money | None = None


# --- The year-end holdings statement (D190) ---------------------------------------------


class AddressHoldings(Schema):
    address_masked: str
    chain: Chain
    units_by_asset: dict[str, Quantity] = Field(default_factory=dict)


class PositionHoldings(Schema):
    asset_id: UUID
    units: Quantity
    lots: list[Lot] = Field(default_factory=list)
    value_eur: Money | None = None
    reconciled: bool


class UnvaluedToken(Schema):
    """A token held but not shown in the value (spam, or no price source), listed so the
    statement still accounts for every unit (D187)."""

    asset_id: UUID
    units: Quantity
    reason: Literal["spam", "no_price"]


class AccountHoldings(Schema):
    account_id: UUID
    addresses: list[AddressHoldings] = Field(default_factory=list)
    positions: list[PositionHoldings] = Field(default_factory=list)
    unvalued_tokens: list[UnvaluedToken] = Field(default_factory=list)


class HoldingsStatement(Schema):
    on: date
    accounts: list[AccountHoldings] = Field(default_factory=list)
