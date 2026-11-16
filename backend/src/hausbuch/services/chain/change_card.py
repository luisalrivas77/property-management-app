"""„Nein, Empfänger“ on a Bitcoin change card (docs/phases/phase-7.md D185 (2), step 4).

The card is a live, proposed, legless `movement_group` of kind unknown that the sync wrote
for an own spend with two or more unregistered outputs (`utxo.classify_tx`). Answering one
output „Nein, Empfänger“:

1. reads the transaction again from a stored original of the account (D182 (3): never from
   the provider) and checks the output is one the card asks about;
2. records `wallet.payee_answered` on the wallet account with the address's masked form and
   HMAC (services/chain/payee_answers), so the address is never asked again on the account;
3. re-derives the spend with every answer and writes the result as a new group through the
   sync's own writer (the same routing, hashes, assets and events), which supersedes the
   card: a transfer once every unregistered output is answered, else a narrower card that
   names only the outputs still unanswered.

Known gap (§15.1): a sync run whose only new item was the card stores no original (D182 (3),
`wallet_sync`'s CHECK: an original only with a new movement), so its transaction is in no
stored original and the answer is refused with `wallet.original_missing`. „Ja, eigene
Adresse“ is not built here.
"""

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine, exists, select, update

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import movement_group as group_table
from hausbuch.db.tables import wallet_address as address_table
from hausbuch.db.tables import wallet_sync as sync_table
from hausbuch.rules.chain.address import address_hmac, mask
from hausbuch.rules.chain.classify import classify
from hausbuch.rules.chain.utxo import outputs, read_utxo, settled_outputs
from hausbuch.rules.errors import WALLET_CHANGE_ANSWERED, WALLET_ORIGINAL_MISSING, RuleError
from hausbuch.schemas.chain import AssetKey, Chain, GroupKind, GroupStatus
from hausbuch.schemas.events import EventType, WalletPayeeAnswered
from hausbuch.services.chain import sync
from hausbuch.services.chain.bitcoin_esplora import to_chain_tx
from hausbuch.services.chain.payee_answers import answered_payee
from hausbuch.services.context import Actor, new_command
from hausbuch.services.documents import read_original
from hausbuch.services.events import append_event
from hausbuch.settings import Settings
from hausbuch.storage import BlobStore

Clock = Callable[[], datetime]


def _record(conn, store: BlobStore, account_id: UUID, first: UUID | None, tx_hash: str):
    """(the Esplora record, its document) from the account's stored sync originals, the
    card's own run first, then the newest; None when no original holds it."""
    w = sync_table.c
    documents = list(
        conn.execute(
            select(w.document_id)
            .where(w.account_id == account_id, w.document_id.is_not(None))
            .order_by((w.id == first).desc() if first else w.started_at.desc(), w.started_at.desc())
        ).scalars()
    )
    for document_id in dict.fromkeys(documents):
        for line in read_original(conn, store, document_id).decode("utf-8").splitlines():
            if not line:
                continue
            raw = json.loads(line)
            if isinstance(raw, dict) and raw.get("txid") == tx_hash:
                return raw, document_id
    return None


def answer_payee(
    engine: Engine,
    workspace_id: UUID,
    group_id: UUID,
    vout: int,
    *,
    settings: Settings,
    store: BlobStore,
    actor: Actor,
    clock: Clock,
) -> UUID:
    """„Nein, Empfänger“ for output `vout` of the card `group_id`; returns the group that
    supersedes the card."""
    g = group_table.c
    with workspace_transaction(engine, workspace_id) as conn:
        card = conn.execute(
            select(group_table).where(g.id == group_id).with_for_update()
        ).one_or_none()
        legless = (
            card is not None
            and not conn.execute(
                select(exists().where(movement_table.c.group_id == card.id))
            ).scalar_one()
        )
        if (
            card is None
            or not legless
            or card.chain != Chain.BITCOIN.value
            or card.kind != GroupKind.UNKNOWN.value
            or card.status != GroupStatus.PROPOSED.value
            or card.superseded_by is not None
            or card.question is None
        ):
            raise RuleError(WALLET_CHANGE_ANSWERED)
        e = event_table.c
        sync_id = conn.execute(
            select(e.payload["sync_id"].astext).where(
                e.entity_id == card.id, e.type == EventType.MOVEMENT_GROUP_PROPOSED.value
            )
        ).scalar_one_or_none()
        run = (
            conn.execute(select(sync_table).where(sync_table.c.id == UUID(sync_id))).one()
            if sync_id
            else None
        )
        if run is None:
            raise RuleError(WALLET_CHANGE_ANSWERED)
        synced = conn.execute(
            select(address_table).where(address_table.c.id == run.address_id)
        ).one()
        found = _record(conn, store, card.account_id, run.id, card.tx_hash)
        if found is None:
            raise RuleError(WALLET_ORIGINAL_MISSING)
        raw, document_id = found
        tx = to_chain_tx(raw, synced.address)

        cmd = new_command(conn, workspace_id, actor, now=clock())
        own = sync._own_of(conn, card.account_id, Chain.BITCOIN)
        own.setdefault(synced.address, sync._Own(synced.id, bytes(synced.address_hmac).hex()))
        native = sync.chain_asset(cmd, AssetKey(chain=Chain.BITCOIN))
        key = settings.iban_hmac_key
        ctx = sync._context(
            conn,
            Chain.BITCOIN,
            synced.address,
            card.account_id,
            own,
            native.id,
            answered_payee(conn, card.account_id, key),
        )
        mine = set(own)
        asked = {c.address for c in read_utxo(tx, mine, settled_outputs(tx, mine, ctx)).candidates}
        output = next((t for t in outputs(tx) if t.index == str(vout)), None)
        if output is None or output.to_addr not in asked:
            raise RuleError(WALLET_CHANGE_ANSWERED)
        append_event(
            cmd,
            EventType.WALLET_PAYEE_ANSWERED,
            card.account_id,
            WalletPayeeAnswered(
                group_id=card.id,
                chain=Chain.BITCOIN,
                address_masked=mask(Chain.BITCOIN, output.to_addr),
                address_hmac=address_hmac(output.to_addr, key).hex(),
            ),
        )
        group = classify(
            tx, replace(ctx, answered_payee=answered_payee(conn, card.account_id, key))
        )
        start = sync._Start(
            sync_id=run.id,  # the card's run, so a narrower card still waits on its address
            address_id=run.address_id,
            account_id=card.account_id,
            chain=Chain.BITCOIN,
            address=synced.address,
            own=own,
            cursor=None,
            first_sync=False,
            started_at=cmd.now,
            host=run.source_host,
            scope="account",
        )
        original = sync._Original(store, sync._Read(), None, id=document_id)
        for plan in sync._plan(cmd, start, sync._Read(groups=[group])):
            sync._write_movements(cmd, start, plan, None, original)
        new_id = conn.execute(
            select(g.id)
            .where(
                g.account_id == card.account_id,
                g.chain == Chain.BITCOIN.value,
                g.tx_hash == card.tx_hash,
                g.superseded_by.is_(None),
                g.id != card.id,
            )
            .order_by(g.created_at.desc(), g.id)
            .limit(1)
        ).scalar_one()
        conn.execute(update(group_table).where(g.id == card.id).values(superseded_by=new_id))
        return new_id
