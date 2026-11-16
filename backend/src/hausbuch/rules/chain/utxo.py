"""Bitcoin: the UTXO accounting of D185 (the net amount out of the own addresses, its fee,
the outputs to unregistered addresses and the change-address question) and the movement group
(docs/phases/phase-7.md D185, §6 (12)); built in step 4. `utxo_net` is the body of
`rules/chain/normalise.utxo_net`. Fixture tests/fixtures/phase7/btc_utxo.yaml.

A Bitcoin `ChainTx` (services/chain/bitcoin_esplora.to_chain_tx) carries one RawTransfer per
input, index `vin:<n>`, `from_addr` the spent output's address (None for a coinbase input),
and one per output, index `<n>` (the vout number), `to_addr` its address (None for an output
without one, an OP_RETURN). Every amount is in satoshi.

The rules (D185 (1), (2), (2) amended 15 Nov 2026; §15.1 item 19):

- `own` is the account's registered Bitcoin addresses, the change addresses a person
  confirmed („Ja, eigene Adresse“) among them.
- A **receive** (no own input): one `in` movement per own output, index the vout number.
- A **spend** whose every input is own: one `out` movement of Σ own inputs − Σ own outputs −
  fee (index `net`, none when it is 0, a consolidation) and one `fee` movement, both on the
  first own input's address; the change is no movement.
- A transaction with foreign inputs too (a CoinJoin, a shared PSBT) is `unknown` (review).
- While **two or more** outputs of a spend go to unregistered addresses (one entry per
  address, counted against `own`) and **any** of them is unanswered, the group is `unknown`
  with the card `wallet.change_candidate` listing the unanswered ones. „Nein, Empfänger“
  (`answered_payee`) settles only the address it names (D185 amended: a truthful „Nein“ on
  the payee must not book the unregistered change as sent too); „Ja, eigene Adresse“
  registers it (`wallet_address(kind = change)`) and so puts it into `own`. The spend is a
  transfer once every unregistered address is answered, or when only one is unregistered
  (that one is the payee and nothing is asked).
- A transaction that touches no own address is a programming error (`ValueError`): a source
  returns only the transactions of the address it was asked about. A reading with no
  movement (an own output of 0 sats, a spend between own addresses with no fee) is never a
  `transfer`: it is `unknown` with `group.unknown`, so a person sees it.

No model and no guess: a change address is only ever own because a person said so (D185).
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal
from uuid import UUID

from hausbuch.rules import errors
from hausbuch.rules.chain.address import mask
from hausbuch.rules.chain.classify import ChainGroup, ChainLeg, ClassifyContext
from hausbuch.schemas.chain import (
    AssetKey,
    Chain,
    ChainTx,
    GroupKind,
    MovementGroupDraft,
    RawTransfer,
)
from hausbuch.schemas.household import AssetMovementDraft, MovementKind, MovementSource

VIN = "vin:"  # the index prefix of an input's RawTransfer; an output's index is its vout
NET = "net"
FEE = "fee"
SATS_PER_BTC = 100_000_000
_DECIMALS = 8


def inputs(tx: ChainTx) -> list[RawTransfer]:
    return [t for t in tx.transfers if t.index.startswith(VIN)]


def outputs(tx: ChainTx) -> list[RawTransfer]:
    return [t for t in tx.transfers if not t.index.startswith(VIN)]


def _unique(addresses: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(addresses))


def utxo_net(tx: ChainTx, own: Collection[str]) -> tuple[int, int, list[str]]:
    """(net_out, fee, unregistered_outputs) of D185 (1), (2).

    A receive gives (0, 0, []): nothing left an own address and the sender paid the fee. A
    spend whose every input is own gives Σ own inputs − Σ own outputs − fee, the fee (Σ inputs
    − Σ outputs, all of it paid by the own addresses), and the addresses of the outputs that
    are not own, in vout order, each once (an output without an address, an OP_RETURN, is
    never one). A transaction with foreign inputs too gives (Σ own inputs − Σ own outputs, 0,
    []): its fee cannot be told apart from the others' share, and `read_utxo` sends it to
    review."""
    own = set(own)
    ins, outs = inputs(tx), outputs(tx)
    out_own = sum(t.units for t in ins if t.from_addr in own)
    if out_own == 0:
        return 0, 0, []
    in_own = sum(t.units for t in outs if t.to_addr in own)
    if any(t.from_addr not in own for t in ins):
        return out_own - in_own, 0, []
    fee = sum(t.units for t in ins) - sum(t.units for t in outs)
    unregistered = _unique([t.to_addr for t in outs if t.to_addr and t.to_addr not in own])
    return out_own - in_own - fee, fee, unregistered


# --- The reading (no ids) and the movement group -----------------------------------------


@dataclass(frozen=True)
class Leg:
    """One movement as D185 gives it, before the ids of the account, asset and address."""

    kind: MovementKind
    role: Literal["in", "out", "fee"]
    address: str  # the own address the movement touches
    sats: int
    index: str


@dataclass(frozen=True)
class ChangeCandidate:
    """An unregistered output the change card asks about: the card shows the address masked
    and the amount; the plaintext is read again from the stored original when „Ja, eigene
    Adresse“ registers it."""

    address: str
    sats: int  # Σ of the transaction's outputs to this address
    vout: int  # the first of them


@dataclass(frozen=True)
class UtxoReading:
    kind: GroupKind
    legs: list[Leg] = field(default_factory=list)
    candidates: list[ChangeCandidate] = field(default_factory=list)
    card: str | None = None


def change_candidates(
    tx: ChainTx, own: Collection[str], answered_payee: Collection[str] = ()
) -> list[ChangeCandidate]:
    """The outputs of an own spend that go to an unregistered address not in
    `answered_payee`, one per address in vout order (two outputs to one address are one
    candidate, their amounts summed); [] for a receive or a transaction with foreign inputs."""
    own = set(own)
    ins = inputs(tx)
    if not ins or any(t.from_addr not in own for t in ins):
        return []
    found: dict[str, ChangeCandidate] = {}
    for t in outputs(tx):
        address = t.to_addr
        if not address or address in own or address in answered_payee:
            continue
        seen = found.get(address)
        found[address] = ChangeCandidate(
            address=address,
            sats=(seen.sats if seen else 0) + t.units,
            vout=seen.vout if seen else int(t.index),
        )
    return list(found.values())


def read_utxo(
    tx: ChainTx, own: Collection[str], answered_payee: Collection[str] = ()
) -> UtxoReading:
    """D185 (1), (2) for one transaction, without ids (the fixture's shape). Raises
    `ValueError` for a transaction that touches no own address."""
    own = set(own)
    ins = inputs(tx)
    own_ins = [t for t in ins if t.from_addr in own]
    if not own_ins:
        own_outs = [t for t in outputs(tx) if t.to_addr in own]
        if not own_outs:
            raise ValueError("a Bitcoin transaction that touches no own address")
        legs = [
            Leg(MovementKind.UNCATEGORIZED, "in", t.to_addr, t.units, t.index)
            for t in own_outs
            if t.units > 0
        ]
        return _settled(legs)
    if len(own_ins) != len(ins):
        return UtxoReading(kind=GroupKind.UNKNOWN, card=errors.GROUP_UNKNOWN)
    unregistered = change_candidates(tx, own)
    unanswered = [c for c in unregistered if c.address not in answered_payee]
    if len(unregistered) >= 2 and unanswered:
        return UtxoReading(
            kind=GroupKind.UNKNOWN, candidates=unanswered, card=errors.WALLET_CHANGE_CANDIDATE
        )
    net, fee, _ = utxo_net(tx, own)
    payer = own_ins[0].from_addr
    assert payer is not None  # an own input has an address
    legs = []
    if net:
        legs.append(Leg(MovementKind.UNCATEGORIZED, "out", payer, net, NET))
    if fee:
        legs.append(Leg(MovementKind.FEE, "fee", payer, fee, FEE))
    return _settled(legs)


def _settled(legs: list[Leg]) -> UtxoReading:
    """A transfer when something moved; a group with no movement is never a transfer, so a
    person sees it (`unknown`, `group.unknown`)."""
    if not legs:
        return UtxoReading(kind=GroupKind.UNKNOWN, card=errors.GROUP_UNKNOWN)
    return UtxoReading(kind=GroupKind.TRANSFER, legs=legs)


def btc(sats: int) -> Decimal:
    """Satoshi as BTC, exact (8 places)."""
    return Decimal(sats).scaleb(-_DECIMALS)


def btc_text(sats: int) -> str:
    """„0,0299 BTC“: the German decimal comma, no trailing zeros."""
    value = format(btc(sats).normalize(), "f")
    return f"{value.replace('.', ',')} BTC"


QUESTION_LIMIT = 500  # MovementGroupDraft.question


def change_question(candidates: Sequence[ChangeCandidate]) -> str:
    """The card's question (D185 (2)): the masked addresses and their amounts, never the
    plaintext. A spend with very many outputs names as many as fit the question's 500
    characters and how many more there are; the card lists them all from the stored
    original."""
    head = (
        "Ist diese Adresse Ihr Wechselgeld?"
        if len(candidates) == 1
        else "Ist eine dieser Adressen Ihr Wechselgeld?"
    )
    named = [f"{mask(Chain.BITCOIN, c.address)} ({btc_text(c.sats)})" for c in candidates]
    for shown in range(len(named), 0, -1):
        rest = len(named) - shown
        text = f"{head} {', '.join(named[:shown])}" + (f" und {rest} weitere" if rest else "")
        if len(text) <= QUESTION_LIMIT:
            return text
    return f"{head} {len(named)} Adressen"


def classify_utxo(
    account_id: UUID,
    tx: ChainTx,
    own: Mapping[str, UUID],
    asset_id: UUID,
    answered_payee: Collection[str] = (),
) -> MovementGroupDraft:
    """One Bitcoin transaction to its movement group. `own` maps each registered address of
    the account on Bitcoin to its `wallet_address.id`; `asset_id` is the account's BTC asset.
    Quantities are signed on the account: in positive, out and fee negative (D186 (2)).
    Raises `ValueError` for a transaction that touches no own address (a caller's error)."""
    reading = read_utxo(tx, own.keys(), answered_payee)
    sign = {"in": 1, "out": -1, "fee": -1}
    movements = [
        AssetMovementDraft(
            account_id=account_id,
            asset_id=asset_id,
            kind=leg.kind,
            quantity=btc(sign[leg.role] * leg.sats),
            occurred_at=tx.occurred_at,
            tx_hash=tx.hash,
            source=MovementSource.CHAIN,
            address_id=own[leg.address],
            index=leg.index,
        )
        for leg in reading.legs
    ]
    question = None
    if reading.card == errors.WALLET_CHANGE_CANDIDATE:
        question = change_question(reading.candidates)
    return MovementGroupDraft(
        account_id=account_id,
        chain=Chain.BITCOIN,
        tx_hash=tx.hash,
        occurred_at=tx.occurred_at,
        kind=reading.kind,
        movements=movements,
        question=question,
    )


def answer_change(
    account_id: UUID,
    tx: ChainTx,
    own: Mapping[str, UUID],
    asset_id: UUID,
    change: str,
    change_id: UUID,
    answered_payee: Collection[str] = (),
) -> MovementGroupDraft:
    """„Ja, eigene Adresse“: the address is registered as change (`change_id` its new
    `wallet_address` row) and the spend re-derived with it; the new group supersedes the
    unknown one. When only one unregistered output is left, it is the payee; with more, each
    still needs its own answer."""
    return classify_utxo(account_id, tx, {**own, change: change_id}, asset_id, answered_payee)


def answer_payee(
    account_id: UUID,
    tx: ChainTx,
    own: Mapping[str, UUID],
    asset_id: UUID,
    payee: str,
    answered_payee: Collection[str] = (),
) -> MovementGroupDraft:
    """„Nein, Empfänger“: the output stays part of the amount sent; the spend is re-derived
    without asking about it again. It settles this address only: while another unregistered
    output is unanswered the group stays `unknown` (D185 amended 15 Nov 2026)."""
    return classify_utxo(account_id, tx, own, asset_id, {*answered_payee, payee})


# --- Balance (D185 (3)) -----------------------------------------------------------------


def address_balance(funded_txo_sum: int, spent_txo_sum: int) -> int:
    """An address's chain balance: Esplora's `chain_stats.funded_txo_sum − spent_txo_sum`."""
    return funded_txo_sum - spent_txo_sum


def account_balance(per_address: Mapping[str, int]) -> int:
    """The account's Bitcoin: the sum over its Bitcoin addresses. A spend's movements sit on
    its first input's address while its change lands on another, so only this sum compares
    with Σ movements (D186 (2), wallet_reconcile.yaml `bitcoin_two_addresses`)."""
    return sum(per_address.values())


def balance_at(units_at_tip: int, txs: Sequence[ChainTx], address: str, block: int) -> int:
    """The address's balance at `block` (the sync's `tip − confirmations_final`, D182 (4)),
    from its balance at the tip and the transactions confirmed after `block`: Esplora's
    `chain_stats` counts every confirmed block, the sync's movements stop six before."""
    later = 0
    for tx in txs:
        if tx.block <= block:
            continue
        later += sum(t.units for t in outputs(tx) if t.to_addr == address)
        later -= sum(t.units for t in inputs(tx) if t.from_addr == address)
    return units_at_tip - later


# --- The family classifier the sync calls (rules/chain/classify, step 4 wiring) ----------

BTC = AssetKey(chain=Chain.BITCOIN)

# A group without movements waits for a person; its question names no address or amount.
FOREIGN_INPUTS_QUESTION = (
    "Diese Bitcoin-Transaktion hat auch Eingänge fremder Adressen (etwa ein CoinJoin oder "
    "eine gemeinsam signierte Zahlung). Was ist hier passiert?"
)
NO_MOVEMENT_QUESTION = (
    "Diese Bitcoin-Transaktion berührt eine eigene Adresse, bewegt aber kein Guthaben. "
    "Was ist hier passiert?"
)


def settled_outputs(tx: ChainTx, own: Collection[str], ctx: ClassifyContext) -> set[str]:
    """The outputs to unregistered addresses settled as not this account's change: a
    „Nein, Empfänger“ answer stored for the account (`ctx.answered_payee`), and an own address
    of another wallet of the workspace (it is no change of this account; D184 links it as a
    transfer between own accounts). An address the account registers later is in `own` and
    so wins over an earlier „Nein“."""
    found: set[str] = set()
    for t in outputs(tx):
        address = t.to_addr
        if not address or address in own:
            continue
        if address in ctx.own_addresses or (
            ctx.answered_payee is not None and ctx.answered_payee(address)
        ):
            found.add(address)
    return found


def classify_tx(tx: ChainTx, ctx: ClassifyContext) -> ChainGroup | None:
    """D185 for the sync: `read_utxo` over every own Bitcoin address of the account (so a
    spend's legs sit on its first own input's address, `ChainLeg.address`, and the change to
    a second own address is no movement), with the stored „Nein“ answers and the other
    wallets' addresses as answered payees.

    - a change card (two or more unregistered outputs, one of them unanswered) is a group of
      kind `unknown` without legs, its question the card's (masked addresses only), flagged
      `unregistered_output` so rules/review routes it `routing.chain_change_candidate`;
    - a reading without movements (foreign inputs, or nothing moved) is `unknown` without
      legs and with a question, so the sync keeps it and a person sees it (`group.unknown`);
    - a transaction that touches no own address of the account gives None."""
    own = set(ctx.own_address_ids) | {ctx.address}
    ins = inputs(tx)
    own_in = [t for t in ins if t.from_addr in own]
    if not own_in and not any(t.to_addr in own for t in outputs(tx)):
        return None
    reading = read_utxo(tx, own, settled_outputs(tx, own, ctx))
    if reading.card == errors.WALLET_CHANGE_CANDIDATE:
        return ChainGroup(
            tx_hash=tx.hash,
            occurred_at=tx.occurred_at,
            block=tx.block,
            kind=GroupKind.UNKNOWN,
            question=change_question(reading.candidates),
            unregistered_output=True,
        )
    if not reading.legs:
        foreign = bool(own_in) and len(own_in) != len(ins)
        return ChainGroup(
            tx_hash=tx.hash,
            occurred_at=tx.occurred_at,
            block=tx.block,
            kind=GroupKind.UNKNOWN,
            question=FOREIGN_INPUTS_QUESTION if foreign else NO_MOVEMENT_QUESTION,
        )
    # D261: the one output that is no own address, the send's counterparty; several such
    # outputs (or one to another own wallet) name none, so no deposit address is learned.
    external = _unique([t.to_addr for t in outputs(tx) if t.to_addr and t.to_addr not in own])
    payee = (
        external[0]
        if own_in
        and len(own_in) == len(ins)  # no foreign input (a CoinJoin names no one payee)
        and len(external) == 1
        and external[0] not in ctx.own_addresses
        else None
    )
    legs = [
        ChainLeg(
            asset_key=BTC,
            units=leg.sats,
            role=leg.role,
            kind=leg.kind,
            index=leg.index,
            address=leg.address,
            counterparty=payee if leg.role == "out" else None,
        )
        for leg in reading.legs
    ]
    return ChainGroup(
        tx_hash=tx.hash, occurred_at=tx.occurred_at, block=tx.block, kind=reading.kind, legs=legs
    )
