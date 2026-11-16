"""The wallet page's API shapes (docs/phases/phase-7.md §4, §13; step 1).

The step 0 shapes (WalletAddressCreate, WalletAddress, WalletSyncSummary, AssetBalance) live in
schemas/chain.py; this module adds what the page and its commands need around them. No read
shape here carries an address in full except WalletAddressPlain, the one answer of the reveal
endpoint (D179).
"""

from uuid import UUID

from pydantic import ConfigDict, Field

from .api import Patch
from .bank import Account
from .chain import AssetBalance, Chain, WalletAddress, WalletAddressCreate, WalletSyncSummary
from .common import Schema

_HIDE_INPUT = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class WalletAddressBody(WalletAddressCreate):
    """POST /wallets/{id}/addresses (WalletAddressCreate's fields): a validation error never
    echoes the pasted address. `also_on` takes EVM chains only, for an Ethereum address the
    shortcut „Auch auf Arbitrum und BNB Smart Chain lesen“ (D179 (2))."""

    model_config = _HIDE_INPUT


class WalletAddressPatch(Patch):
    """PATCH /wallets/{id}/addresses/{aid}: the label (null clears it) and active (false
    retires the address, true reactivates it). Nothing else of a row ever changes (0041's
    trigger)."""

    model_config = _HIDE_INPUT

    label: str | None = Field(default=None, min_length=1, max_length=100)
    active: bool = Field(default=None)


class WalletAddressPlain(Schema):
    """GET /wallets/{id}/addresses/{aid}/plain: the full address for „kopieren“, sent with
    Cache-Control: no-store and audited as wallet.address_revealed. The page writes it to the
    clipboard and never renders it."""

    id: UUID
    chain: Chain
    address: str


class WalletPage(Schema):
    """GET /wallets/{id}: the wallet account with its owner shares, its addresses (masked,
    active first, then by chain and the order added, each with its last finished sync), and
    the balances and syncs, still empty: the runs are GET /wallets/{id}/syncs, the balances
    the screens step's."""

    account: Account
    addresses: list[WalletAddress]
    balances: list[AssetBalance] = Field(default_factory=list)
    syncs: list[WalletSyncSummary] = Field(default_factory=list)
