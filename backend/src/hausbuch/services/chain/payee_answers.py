"""The „Nein, Empfänger“ answers of a wallet account (docs/phases/phase-7.md D185 (2), step 4).

An answer is one `wallet.payee_answered` event on the wallet account, keyed by the address's
HMAC (rules/chain/address.address_hmac under HAUSBUCH_IBAN_HMAC_KEY), never its plaintext:
a payee is no own address, so it has no wallet_address row. The sync reads them once per run
into `ClassifyContext.answered_payee`, so an answered address is never asked again on that
account, in a later spend or a replay over the stored originals (`utxo.classify_tx`). Kept
apart from services/chain/change_card.py, which writes them, so the sync imports no command.
"""

from collections.abc import Callable
from uuid import UUID

from sqlalchemy import Connection, select

from hausbuch.db.tables import event as event_table
from hausbuch.rules.chain.address import address_hmac
from hausbuch.schemas.events import EventType


def answered_hmacs(conn: Connection, account_id: UUID) -> frozenset[str]:
    e = event_table.c
    return frozenset(
        conn.execute(
            select(e.payload["address_hmac"].astext).where(
                e.type == EventType.WALLET_PAYEE_ANSWERED.value, e.entity_id == account_id
            )
        ).scalars()
    )


def answered_payee(conn: Connection, account_id: UUID, key: bytes) -> Callable[[str], bool] | None:
    """The account's predicate for `ClassifyContext.answered_payee`; None when it has no
    answer (the classifier then skips the HMAC)."""
    hmacs = answered_hmacs(conn, account_id)
    if not hmacs:
        return None
    return lambda address: address_hmac(address, key).hex() in hmacs
