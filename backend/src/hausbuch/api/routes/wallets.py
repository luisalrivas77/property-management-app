"""A wallet's page and its addresses (docs/phases/phase-7.md §4, §13; step 1).

Every read shape carries an address masked; GET .../plain is the one answer with the full
address („kopieren“), sent with Cache-Control: no-store and audited as wallet.address_revealed.
No error body quotes a pasted address (wallet.address_invalid names the chain's shape only).
"""

from uuid import UUID

from fastapi import Response

from hausbuch.schemas.chain import WalletAddress
from hausbuch.schemas.enums import EntityType
from hausbuch.schemas.wallets import (
    WalletAddressBody,
    WalletAddressPatch,
    WalletAddressPlain,
    WalletPage,
)
from hausbuch.services import wallets

from ..deps import Cmd, SettingsDep, domain_router, found

router = domain_router()


@router.get("/wallets/{account_id}")
def wallet_get(account_id: UUID, cmd: Cmd) -> WalletPage:
    """The wallet account with its owner shares and its addresses, masked; balances and syncs
    are empty until a chain is read (step 2). 404 for an account that is no wallet."""
    return found(cmd.read(wallets.wallet_page, account_id), EntityType.WALLET, account_id)


@router.post("/wallets/{account_id}/addresses", status_code=201)
def wallet_address_add(
    account_id: UUID, body: WalletAddressBody, cmd: Cmd, settings: SettingsDep
) -> list[WalletAddress]:
    """The rows written: the chain given, and with `also_on` the same 0x address on the other
    EVM chains („Auch auf Arbitrum und BNB Smart Chain lesen“), a chain already registered on
    this wallet skipped. 422 wallet.address_invalid (not the chain's format, or `also_on` for a
    non-EVM address) or wallet.address_taken (on another own account, or already on every
    chain asked for here); 404 for an account that is no wallet."""
    return cmd.run(wallets.add_address, account_id, body, key=settings.iban_hmac_key)


@router.patch("/wallets/{account_id}/addresses/{address_id}")
def wallet_address_update(
    account_id: UUID, address_id: UUID, body: WalletAddressPatch, cmd: Cmd
) -> WalletAddress:
    """The label (null clears it) and active: false retires the address („Adresse
    stilllegen“), true reactivates it. 404 for an address of another wallet."""
    return cmd.run(wallets.update_address, account_id, address_id, **body.changes())


@router.get("/wallets/{account_id}/addresses/{address_id}/plain")
def wallet_address_plain(
    account_id: UUID, address_id: UUID, cmd: Cmd, response: Response
) -> WalletAddressPlain:
    """„kopieren“: the full address, for the clipboard only. Never cached; each call writes
    wallet.address_revealed. 404 for an address of another wallet."""
    response.headers["Cache-Control"] = "no-store"
    return cmd.run(wallets.reveal_address, account_id, address_id)
