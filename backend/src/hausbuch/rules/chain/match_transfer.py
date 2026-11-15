"""Transfers between own accounts on a chain (docs/phases/phase-7.md D184, §7); built in step 5.

`match` links an open sending leg to its counterpart among the receiving legs it is given. The
link kind is read from the two legs, never passed in:

- `chain`: the same chain (or one side an exchange row, which has no chain) and the same asset.
  By the transaction hash when both legs carry one: the same hash is the counterpart, another
  hash never is. Otherwise by the priced_as symbol, `quantity − fee_quantity` against the
  receiving units (exact, or within one smallest unit of the chain side's asset when its
  decimals are given, D184 (1)(a)), the exchange's network name mapped through
  rules/chain/registry.network (an unknown name → no candidate, the card names it) and 48 hours.
- `bridge`: two chains joined by one of the registry's bridge pairs (either direction), the
  same priced_as symbol, 24 hours, the receiving units within the pair's max_fee_share below
  the sent units; the difference is the bridge's fee (D184 (2)).
- `wrap`: one chain, the native coin and its wrapped token by the registry's wraps (either
  direction), within one transaction or one minute, the units as for `chain` (D184 (3)).

A candidate that is not own (a third party's credit) or superseded is never the counterpart
(D162, D184 (5)). When a candidate shares the sending leg's hash, only such candidates count.
One candidate → linked; several → `transfer.ambiguous` with the candidates; none →
`transfer.unmatched_leg`. A linked transfer moves the lots when the two legs are on different
accounts (D184 (1)(c), §15.1's `lots_move`).

Pure: the caller passes only legs that are still open (not linked) and receiving legs as
candidates; the workspace's confirmed `chain_pair` rows (D191) come in as `extra_bridges` and
`extra_wraps`, a workspace bridge winning over the file's for the same chains and contracts.
Fixture tests/fixtures/phase7/chain_transfers.yaml.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from hausbuch.rules import errors as E
from hausbuch.rules.chain.registry import registry
from hausbuch.schemas.chain import BridgePair, Chain, WrapPair
from hausbuch.schemas.household import TransferLinkKind

WINDOW = {
    TransferLinkKind.CHAIN: timedelta(hours=48),  # D184 (1), D175
    TransferLinkKind.BRIDGE: timedelta(hours=24),  # D184 (2), design §2.3
    TransferLinkKind.WRAP: timedelta(minutes=1),  # D184 (3)
}

_HEX = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class ChainLeg:
    """One movement as the matcher reads it. `units` is the amount moved, always positive (the
    sending leg's quantity out, the receiving leg's quantity in); `fee` the exchange's
    fee_quantity on a withdrawal (D175), else 0. `chain` is None for an exchange row; `asset`
    is the priced_as symbol; `contract` the token's contract or mint, None for the chain's
    native coin (and for an exchange row); `decimals` the asset's smallest denomination for the
    dust tolerance, None for an exact comparison. `network` is the exchange's network name
    (Binance's withdraw history, D178). `own` is False for a credit known to be a third party's.
    `ref` is the caller's id of the movement, returned untouched."""

    ref: str
    account: str
    asset: str
    units: Decimal
    at: datetime
    chain: Chain | None = None
    contract: str | None = None
    decimals: int | None = None
    fee: Decimal = Decimal(0)
    tx_hash: str | None = None
    network: str | None = None
    own: bool = True
    superseded: bool = False


@dataclass(frozen=True)
class ChainMatch:
    """The matcher's answer. `linked`: `link`, `counterpart`, `lots_move` and, on a bridge,
    `fee_units` (the units the bridge kept, a `fee` movement on the sending side). `ambiguous`:
    `card` and `candidates` (all of them on the card), `link` set when they share one kind.
    `unmatched`: `card`, and `unknown_network` when the exchange named a network the registry
    does not list (the card names it)."""

    result: Literal["linked", "ambiguous", "unmatched"]
    link: TransferLinkKind | None = None
    counterpart: ChainLeg | None = None
    lots_move: bool | None = None
    fee_units: Decimal | None = None
    candidates: tuple[ChainLeg, ...] = field(default_factory=tuple)
    card: str | None = None
    unknown_network: str | None = None


def match(
    leg: ChainLeg,
    candidates: Sequence[ChainLeg],
    *,
    extra_bridges: Sequence[BridgePair] = (),
    extra_wraps: Sequence[tuple[Chain, WrapPair]] = (),
) -> ChainMatch:
    unknown_network: str | None = None
    network_chain: Chain | None = None
    if leg.network is not None:
        network_chain = registry().network(leg.network)
        if network_chain is None:
            unknown_network = leg.network
    fits: list[tuple[TransferLinkKind, ChainLeg]] = []
    for c in candidates:
        if c.ref == leg.ref or not c.own or c.superseded or c.asset != leg.asset:
            continue
        kind = _kind(leg, c, extra_wraps)
        if kind is None:
            continue
        if kind is TransferLinkKind.BRIDGE:
            if _bridge_fits(leg, c, extra_bridges):
                fits.append((kind, c))
        elif kind is TransferLinkKind.WRAP:
            if _units_fit(leg, c) and (
                _same_hash(leg, c) or timedelta(0) <= c.at - leg.at <= WINDOW[kind]
            ):
                fits.append((kind, c))
        elif _same_hash(leg, c):
            fits.append((kind, c))
        elif leg.tx_hash and c.tx_hash:
            continue  # another transaction is never the counterpart
        elif (
            timedelta(0) <= c.at - leg.at <= WINDOW[kind]
            and _units_fit(leg, c)
            and _network_fits(leg, c, network_chain)
        ):
            fits.append((kind, c))
    by_hash = [(k, c) for k, c in fits if _same_hash(leg, c)]
    if by_hash:
        fits = by_hash
    if len(fits) == 1:
        kind, c = fits[0]
        return ChainMatch(
            result="linked",
            link=kind,
            counterpart=c,
            lots_move=c.account != leg.account,
            fee_units=leg.units - c.units if kind is TransferLinkKind.BRIDGE else None,
        )
    if fits:
        kinds = {k for k, _ in fits}
        return ChainMatch(
            result="ambiguous",
            link=kinds.pop() if len(kinds) == 1 else None,
            candidates=tuple(c for _, c in fits),
            card=E.TRANSFER_AMBIGUOUS,
        )
    return ChainMatch(
        result="unmatched", card=E.TRANSFER_UNMATCHED_LEG, unknown_network=unknown_network
    )


def _kind(
    leg: ChainLeg, c: ChainLeg, extra_wraps: Sequence[tuple[Chain, WrapPair]]
) -> TransferLinkKind | None:
    if leg.chain is None or c.chain is None:
        return TransferLinkKind.CHAIN  # an exchange row: the symbol is the asset
    if leg.chain is not c.chain:
        return TransferLinkKind.BRIDGE
    a, b = _contract(leg.contract), _contract(c.contract)
    if a == b:
        return TransferLinkKind.CHAIN
    wraps = [*registry().chain(leg.chain).wraps, *(w for ch, w in extra_wraps if ch is leg.chain)]
    for w in wraps:
        if w.native == leg.asset and {a, b} == {None, _contract(w.wrapped)}:
            return TransferLinkKind.WRAP
    return None


def _bridge_fits(leg: ChainLeg, c: ChainLeg, extra_bridges: Sequence[BridgePair]) -> bool:
    if leg.chain is None or c.chain is None:
        return False
    sent = (leg.chain, _contract(leg.contract))
    got = (c.chain, _contract(c.contract))
    pair = next(
        (
            p
            for p in [*extra_bridges, *registry().bridge(leg.chain, c.chain)]
            if _joins(p, sent, got)
        ),
        None,
    )
    if pair is None:
        return False
    if not timedelta(0) <= c.at - leg.at <= WINDOW[TransferLinkKind.BRIDGE]:
        return False
    return leg.units * (1 - pair.max_fee_share) <= c.units <= leg.units


def _joins(pair: BridgePair, sent: tuple[Chain, str | None], got: tuple[Chain, str | None]) -> bool:
    start = (pair.from_chain, _contract(pair.from_contract))
    end = (pair.to_chain, _contract(pair.to_contract))
    return (start, end) in ((sent, got), (got, sent))


def _units_fit(leg: ChainLeg, c: ChainLeg) -> bool:
    """`quantity − fee_quantity` against the receiving units, within the chain's dust."""
    return abs(c.units - (leg.units - leg.fee)) <= _dust(leg, c)


def _network_fits(leg: ChainLeg, c: ChainLeg, network_chain: Chain | None) -> bool:
    if leg.network is None:
        return True
    return network_chain is not None and c.chain is network_chain


def _dust(leg: ChainLeg, c: ChainLeg) -> Decimal:
    """One unit of the chain side's smallest denomination (the receiving leg's when it is on a
    chain), or 0 when its decimals are not given."""
    side = c if c.chain is not None else leg
    if side.chain is None or side.decimals is None:
        return Decimal(0)
    return Decimal(1).scaleb(-side.decimals)


def _same_hash(leg: ChainLeg, c: ChainLeg) -> bool:
    return bool(leg.tx_hash and c.tx_hash and _hash(leg.tx_hash) == _hash(c.tx_hash))


def _hash(value: str) -> str:
    """A hex hash (EVM, Bitcoin) compared without case and without its `0x`; a base58
    signature as written."""
    return value.lower().removeprefix("0x") if _HEX.match(value) else value


def _contract(value: str | None) -> str | None:
    """An EVM contract compared lower-case; a Solana mint as written."""
    if value is None:
        return None
    return value.lower() if value.startswith("0x") else value
