"""One transaction to its movement group (docs/phases/phase-7.md D183, D185, D187, §6).

`classify` gives the group's kind by deterministic shape rules and the decoders of
rules/chain/decoders (never a model): one movement per asset leg and one for the fee, spam by
D187 (an unlisted token credited without a fee paid), a swap's legs priced by D188 (4)
downstream. Fixtures tests/fixtures/phase7/swap_group.yaml, spam.yaml, solana_tx.yaml,
evm_tx.yaml, btc_utxo.yaml.

**The family protocol (step 2 core).** Each chain family's input differs (Solana works from a
raw `getTransaction` and its deltas, EVM from Etherscan's records, Bitcoin from an Esplora
transaction), so `classify` is a dispatcher by `CHAIN_FAMILY[ctx.chain]` to a classifier

    FamilyClassifier = Callable[[ChainTx, ClassifyContext], ChainGroup | None]

found in the family's module: `rules/chain/solana.py`, `evm.py`, `utxo.py` define
`classify_tx(tx: ChainTx, ctx: ClassifyContext) -> ChainGroup | None` (the family's source
puts whatever it needs, such as Solana's deltas inputs, into `ChainTx.raw`).
`register_family` replaces one for a test. A classifier returns None when the transaction
moves nothing of the synced address (it is then only counted in `txs_seen`).

The adapter owns its own reading: `ChainTx` carries no chain, so the chain, the account, its
own addresses with their `wallet_address` ids, the native coin's asset id, the known asset
ids, the decoders and the held tokens all come in the `ClassifyContext`. A family module's
`classify_tx` wraps its own entry points (EVM's `normalise` and `classify_evm`, Bitcoin's
`utxo` rules) and returns its group as a `ChainGroup`; a leg on another own address of the
account names it in `ChainLeg.address`.

The output is a `ChainGroup` with `ChainLeg`s keyed by `AssetKey` and in signed base units,
not a `MovementGroupDraft`: a pure rule knows no asset id, account id or address HMAC.
services/chain/sync.py resolves the assets (D180), converts the units by the asset's
decimals, hashes each leg (D182 (2)), fills the routing facts only the database knows
(`first_sync`, `priced`, `registry_assets`, `fee_only`) and routes the group (D186 (1)).
"""

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from importlib import import_module
from typing import Literal
from uuid import UUID

from hausbuch.schemas.chain import (
    CHAIN_FAMILY,
    AssetKey,
    Chain,
    ChainFamily,
    ChainTx,
    DecoderRow,
    GroupKind,
)
from hausbuch.schemas.household import MovementKind

Role = Literal["in", "out", "fee"]


@dataclass(frozen=True, slots=True)
class ClassifyContext:
    """What a family classifier may read besides the transaction. The plaintext addresses
    are held in memory only (D179): never put one into a ChainGroup's text fields."""

    chain: Chain
    address: str  # the own address being synced, as stored (EVM lower-case)
    own_addresses: frozenset[str] = frozenset()  # every active own address on this chain
    decoders: Sequence[DecoderRow] = ()  # the workspace's own rows (D191; public rows built in)
    held: frozenset[AssetKey] = frozenset()  # tokens the workspace holds with a confirmed lot
    account_id: UUID | None = None  # the wallet account being synced
    # The account's own active addresses on this chain → their wallet_address.id (a Bitcoin
    # spend's legs sit on its first input's address, not always the synced one).
    own_address_ids: Mapping[str, UUID] = field(default_factory=dict)
    native_asset_id: UUID | None = None  # the workspace's asset of this chain's native coin
    # The workspace's assets on this chain known when the run starts, by (chain, contract),
    # the native coin included. A token seen for the first time is not in it: the sync
    # creates its asset from the leg's AssetKey, so a classifier never needs an id to emit one.
    asset_ids: Mapping[AssetKey, UUID] = field(default_factory=dict)
    # Bitcoin (D185 (2)): whether a person answered „Nein, Empfänger“ for this address on
    # the account (the sync compares the address's HMAC with the stored answers, so no
    # plaintext is kept for them); None: no answer is known.
    answered_payee: Callable[[str], bool] | None = None
    # The transaction hashes the workspace's own exchange withdrawals name (D183 (3)(b)), as
    # stored on the exchange's asset_movement rows. Held in memory only: a hash is never put
    # into an event, a log line or a ChainGroup's text fields.
    exchange_hashes: frozenset[str] = frozenset()
    # D261: whether an address is a deposit address the workspace learned for one of its
    # exchanges on this chain (the sync compares its HMAC with the learned rows, so no
    # plaintext is kept for them); None: none is learned. A plain send to such an address is
    # an exchange_transfer with an internal_transfer_out leg (`learned_deposit`).
    exchange_deposit: Callable[[str], bool] | None = None


@dataclass(frozen=True, slots=True)
class ChainLeg:
    """One movement of the synced address: `units` > 0 in base units, the direction by
    `role` (`in` adds, `out` and `fee` subtract). `index` as D182 (2) and the fixture
    movement_hash.yaml spell it (`fee` for the fee leg). `symbol` and `decimals` are what the
    chain says of an asset the registry does not list (None for a registry token or the
    native coin, whose rows say it); `spam` marks an unlisted token D187 calls spam."""

    asset_key: AssetKey
    units: int
    role: Role
    kind: MovementKind
    index: str
    symbol: str | None = None
    decimals: int | None = None
    spam: bool = False
    # The own address the movement sits on (a key of ClassifyContext.own_address_ids); None:
    # the synced address. Its HMAC goes into the movement hash and its id into address_id.
    address: str | None = None
    # D261: the one external address a plain send paid (EVM's recipient, Solana's credited
    # owner, Bitcoin's one output that is no own address), in clear and in memory only: the
    # sync stores its HMAC and masked form. None on every other leg, and on a send paying
    # more than one external output.
    counterparty: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.units <= 0:
            raise ValueError("ChainLeg.units: a positive amount in base units")
        if (self.role == "fee") != (self.kind is MovementKind.FEE):
            raise ValueError("ChainLeg: the fee leg and only it has kind fee")

    @property
    def signed(self) -> int:
        return self.units if self.role == "in" else -self.units


@dataclass(frozen=True, slots=True)
class ChainGroup:
    """One transaction as it touches the synced address (D183). `decoder` the decoder row's
    name (or program) that decided the kind, None by shape alone; `decoder_listed`,
    `linked`, `pair_learned` and `unregistered_output` the routing facts the family knows
    (GroupRoutingInput); `question` the card's one question when it waits for a person,
    never holding an address in clear. A group with a question and no legs (a Bitcoin change
    card, kind unknown) is written as proposed without movements; while one waits, the
    sync records the run's difference as „wartet auf Antwort“ (reconciled null), not as a
    mismatch."""

    tx_hash: str
    occurred_at: datetime
    block: int
    kind: GroupKind
    legs: list[ChainLeg] = field(default_factory=list)
    decoder: str | None = None
    decoder_listed: bool = False
    linked: bool = False
    pair_learned: bool = False
    unregistered_output: bool = False
    question: str | None = None

    @property
    def fee_only(self) -> bool:
        return bool(self.legs) and all(leg.role == "fee" for leg in self.legs)


FamilyClassifier = Callable[[ChainTx, ClassifyContext], ChainGroup | None]

_MODULES = {
    ChainFamily.SOLANA: "hausbuch.rules.chain.solana",
    ChainFamily.EVM: "hausbuch.rules.chain.evm",
    ChainFamily.UTXO: "hausbuch.rules.chain.utxo",
}
_REGISTERED: dict[ChainFamily, FamilyClassifier] = {}


def register_family(family: ChainFamily, classifier: FamilyClassifier | None) -> None:
    """Use `classifier` for a family instead of its module's `classify_tx` (a test's fake);
    None restores the module's."""
    if classifier is None:
        _REGISTERED.pop(family, None)
    else:
        _REGISTERED[family] = classifier


def classifier_for(family: ChainFamily) -> FamilyClassifier:
    if family in _REGISTERED:
        return _REGISTERED[family]
    found = getattr(import_module(_MODULES[family]), "classify_tx", None)
    if found is None:
        raise NotImplementedError(f"rules/chain: no classify_tx for {family} yet")
    return found


def classify(tx: ChainTx, ctx: ClassifyContext) -> ChainGroup | None:
    """The transaction's group for the synced address, by its chain's family, then
    `learned_deposit`."""
    group = classifier_for(CHAIN_FAMILY[ctx.chain])(tx, ctx)
    return None if group is None else learned_deposit(group, ctx)


def plain_send(group: ChainGroup) -> ChainLeg | None:
    """The one value leg of a plain send (a transfer whose only non-fee leg is one
    `uncategorized` out with a counterparty), else None."""
    if group.kind is not GroupKind.TRANSFER or group.question is not None:
        return None
    value = [leg for leg in group.legs if leg.role != "fee" and not leg.spam]
    if len(value) != 1:
        return None
    leg = value[0]
    if leg.role != "out" or leg.kind is not MovementKind.UNCATEGORIZED:
        return None
    return leg if leg.counterparty is not None else None


def learned_deposit(group: ChainGroup, ctx: ClassifyContext) -> ChainGroup:
    """D261: a plain send to an address the workspace learned as one of its exchanges'
    deposit addresses is an `exchange_transfer` with an `internal_transfer_out` leg; the
    linker pairs it with that exchange's deposit. Any other group is returned unchanged."""
    leg = plain_send(group)
    if leg is None or ctx.exchange_deposit is None or leg.counterparty is None:
        return group
    if not ctx.exchange_deposit(leg.counterparty):
        return group
    legs = [
        replace(x, kind=MovementKind.INTERNAL_TRANSFER_OUT) if x is leg else x for x in group.legs
    ]
    return replace(group, kind=GroupKind.EXCHANGE_TRANSFER, legs=legs)


def own(ctx: ClassifyContext, address: str | None, chain: Chain | None = None) -> bool:
    """Whether a counterparty string is one of the own addresses (EVM compared lower-case)."""
    if address is None:
        return False
    if CHAIN_FAMILY[chain or ctx.chain] is ChainFamily.EVM:
        address = address.lower()
    return address == ctx.address or address in ctx.own_addresses


def legs_net(legs: Collection[ChainLeg]) -> dict[AssetKey, int]:
    """Σ signed base units per asset of a set of legs."""
    out: dict[AssetKey, int] = {}
    for leg in legs:
        out[leg.asset_key] = out.get(leg.asset_key, 0) + leg.signed
    return out
