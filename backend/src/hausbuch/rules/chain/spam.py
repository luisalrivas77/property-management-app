"""Spam tokens, family-neutral (docs/phases/phase-7.md D187, §6 examples 6 and 7).

A token credited to an own address is spam when the registry does not list its (chain,
contract), the workspace does not hold it with a confirmed lot, the own address paid no fee
for the transaction and no swap decoder's received leg bought it (D187 (1), (4)). Its
symbol is attacker-controlled, so an unlisted token is always labelled by its masked
contract with the symbol it gives itself („Token 0x9f…21ab (nennt sich USDC)“, D187 (2)),
never by the symbol alone. A promotion („Das ist echt“) makes it an airdrop that waits for a
person and a price source; a demotion makes it spam. Fixture tests/fixtures/phase7/spam.yaml.
The family classifiers (rules/chain/solana, evm, utxo) call these.
"""

from dataclasses import dataclass
from typing import Literal

from hausbuch.rules.chain.registry import registry
from hausbuch.rules.household.assets import registry as asset_registry
from hausbuch.schemas.chain import AssetKey, GroupKind
from hausbuch.schemas.household import MovementKind


def mask_contract(contract: str) -> str:
    """The first four and the last four characters (D179's mask, migration 0041's CHECK)."""
    return f"{contract[:4]}…{contract[-4:]}"


def listed(key: AssetKey) -> bool:
    """A native coin, or a token the public registry lists by its chain and contract."""
    return key.contract is None or asset_registry().token(key.chain, key.contract) is not None


def is_spam(key: AssetKey, *, held: bool, fee_paid: bool, bought: bool) -> bool:
    """D187 (1), (4): unlisted, not held, no fee paid by the own address, not bought."""
    return not listed(key) and not held and not fee_paid and not bought


def token_label(key: AssetKey, chain_symbol: str | None) -> str:
    """The name shown for a token: the registry's symbol, else the masked contract with the
    symbol it calls itself (D187 (2)); the native coin's symbol for a native coin."""
    if key.contract is None:
        return registry().chain(key.chain).native_symbol
    row = asset_registry().token(key.chain, key.contract)
    if row is not None:
        return row.symbol
    named = f" (nennt sich {chain_symbol})" if chain_symbol else ""
    return f"Token {mask_contract(key.contract)}{named}"


@dataclass(frozen=True, slots=True)
class SpamVerdict:
    group: GroupKind
    movement: MovementKind
    hidden: bool
    card: bool
    label: str
    price_source_needed: bool


def verdict(
    key: AssetKey,
    chain_symbol: str | None,
    *,
    held: bool = False,
    fee_paid: bool = False,
    bought: bool = False,
    action: Literal["promote", "demote"] | None = None,
) -> SpamVerdict:
    """How a credited token reads (D187 (1)–(4)): spam (hidden, confirmed, no card, no price
    asked); bought in a swap (an acquisition); else an airdrop with a card, needing a price
    source when the registry does not list it. A promotion turns spam into an airdrop, a
    demotion anything into spam."""
    spam = is_spam(key, held=held, fee_paid=fee_paid, bought=bought)
    if action == "promote" and spam:
        spam = False
        group, movement = GroupKind.AIRDROP, MovementKind.AIRDROP
    elif action == "demote":
        spam = True
        group, movement = GroupKind.SPAM, MovementKind.SPAM
    elif spam:
        group, movement = GroupKind.SPAM, MovementKind.SPAM
    elif bought:
        group, movement = GroupKind.SWAP, MovementKind.ACQUISITION
    else:
        group, movement = GroupKind.AIRDROP, MovementKind.AIRDROP
    is_listed = listed(key)
    label = token_label(key, chain_symbol) if is_listed and not spam else None
    if label is None:
        assert key.contract is not None
        label = f"Token {mask_contract(key.contract)} (nennt sich {chain_symbol})"
    return SpamVerdict(
        group=group,
        movement=movement,
        hidden=spam,
        card=group is GroupKind.AIRDROP,
        label=label,
        price_source_needed=not spam and not is_listed,
    )
