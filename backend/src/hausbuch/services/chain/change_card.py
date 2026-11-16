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
   names only the outputs still unanswered;
4. does the same for every other open change card of the account whose reading the answer
   changes (the first sync writes one card per past spend, often to the same payee); a card
   whose spend is in no stored original is left as it is.

A sync run whose only new item was the card keeps its original since migration 0044 (D182 (3)
amended, review of PR #387), so every card written since can be answered. A card written
before it, by such a run, has its transaction in no stored original: the answer is refused
with `wallet.original_missing`. „Ja, eigene Adresse“ is not built here.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
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
from hausbuch.rules.chain.classify import ClassifyContext, classify
from hausbuch.rules.chain.utxo import outputs, read_utxo, settled_outputs
from hausbuch.rules.errors import WALLET_CHANGE_ANSWERED, WALLET_ORIGINAL_MISSING, RuleError
from hausbuch.schemas.chain import AssetKey, Chain, ChainTx, GroupKind, GroupStatus
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


def _record(conn, store: BlobStore, account_id: UUID, first: UUID, tx_hash: str):
    """(the Esplora record, its document) from the stored sync originals of the account's
    Bitcoin addresses, the card's own run first, then the newest; None when none holds it."""
    w, a = sync_table.c, address_table.c
    bitcoin = select(a.id).where(a.account_id == account_id, a.chain == Chain.BITCOIN.value)
    documents = list(
        conn.execute(
            select(w.document_id)
            .where(
                w.account_id == account_id,
                w.address_id.in_(bitcoin),
                w.document_id.is_not(None),
            )
            .order_by((w.id == first).desc(), w.started_at.desc())
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


@dataclass(frozen=True)
class _Reading:
    card: object  # the movement_group row
    run: object  # the wallet_sync row that asked it
    tx: ChainTx
    document_id: UUID
    own: dict
    ctx: ClassifyContext


def _open_cards(conn, account_id: UUID, *, but: UUID | None = None):
    """The account's live legless Bitcoin cards that ask a question, locked."""
    g = group_table.c
    query = (
        select(group_table)
        .where(
            g.account_id == account_id,
            g.chain == Chain.BITCOIN.value,
            g.kind == GroupKind.UNKNOWN.value,
            g.status == GroupStatus.PROPOSED.value,
            g.superseded_by.is_(None),
            g.question.is_not(None),
            ~exists().where(movement_table.c.group_id == g.id),
        )
        .order_by(g.occurred_at, g.id)
        .with_for_update()
    )
    if but is not None:
        query = query.where(g.id != but)
    return list(conn.execute(query))


def _reading(conn, cmd, store: BlobStore, settings: Settings, card) -> _Reading | None:
    """The card's spend read again from a stored original, with the account's own addresses
    and current answers; None when no original holds it. Raises when the card has no run."""
    e = event_table.c
    sync_id = conn.execute(
        select(e.payload["sync_id"].astext).where(
            e.entity_id == card.id, e.type == EventType.MOVEMENT_GROUP_PROPOSED.value
        )
    ).scalar_one_or_none()
    run = (
        conn.execute(select(sync_table).where(sync_table.c.id == UUID(sync_id))).one_or_none()
        if sync_id
        else None
    )
    if run is None:
        raise RuleError(WALLET_CHANGE_ANSWERED)
    synced = conn.execute(select(address_table).where(address_table.c.id == run.address_id)).one()
    found = _record(conn, store, card.account_id, run.id, card.tx_hash)
    if found is None:
        return None
    raw, document_id = found
    own = sync._own_of(conn, card.account_id, Chain.BITCOIN)
    own.setdefault(synced.address, sync._Own(synced.id, bytes(synced.address_hmac).hex()))
    native = sync.chain_asset(cmd, AssetKey(chain=Chain.BITCOIN))
    ctx = sync._context(
        conn,
        Chain.BITCOIN,
        synced.address,
        card.account_id,
        own,
        native.id,
        answered_payee(conn, card.account_id, settings.iban_hmac_key),
    )
    return _Reading(card, run, to_chain_tx(raw, synced.address), document_id, own, ctx)


def _supersede(conn, cmd, store: BlobStore, reading: _Reading, group) -> UUID:
    """Write the re-derived spend through the sync's writer and supersede the card by it."""
    card, run = reading.card, reading.run
    start = sync._Start(
        sync_id=run.id,  # the card's run, so a narrower card still waits on its address
        address_id=run.address_id,
        account_id=card.account_id,
        chain=Chain.BITCOIN,
        address=reading.ctx.address,
        own=reading.own,
        cursor=None,
        first_sync=False,
        started_at=cmd.now,
        host=run.source_host,
        scope="account",
    )
    original = sync._Original(store, sync._Read(), None, id=reading.document_id)
    for plan in sync._plan(cmd, start, sync._Read(groups=[group])):
        sync._write_movements(cmd, start, plan, None, original)
    g = group_table.c
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
        account_id = conn.execute(select(g.account_id).where(g.id == group_id)).scalar_one_or_none()
        if account_id is None:
            raise RuleError(WALLET_CHANGE_ANSWERED)
        # every open card of the account, locked in one order, so two answers never deadlock
        cards = _open_cards(conn, account_id)
        card = next((c for c in cards if c.id == group_id), None)
        if card is None:
            raise RuleError(WALLET_CHANGE_ANSWERED)
        cmd = new_command(conn, workspace_id, actor, now=clock())
        reading = _reading(conn, cmd, store, settings, card)
        if reading is None:
            raise RuleError(WALLET_ORIGINAL_MISSING)
        tx, ctx, mine = reading.tx, reading.ctx, set(reading.own)
        asked = {c.address for c in read_utxo(tx, mine, settled_outputs(tx, mine, ctx)).candidates}
        output = next((t for t in outputs(tx) if t.index == str(vout)), None)
        if output is None or output.to_addr not in asked:
            raise RuleError(WALLET_CHANGE_ANSWERED)
        key = settings.iban_hmac_key
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
        answers = answered_payee(conn, card.account_id, key)
        new_id = _supersede(
            conn, cmd, store, reading, classify(tx, replace(ctx, answered_payee=answers))
        )
        for other in cards:
            if other.id == card.id:
                continue
            again = _reading(conn, cmd, store, settings, other)
            if again is None:
                continue  # its spend is in no stored original (§15.1's gap): left as it is
            group = classify(again.tx, again.ctx)  # the answers now include this one
            if group is not None and not group.legs and group.question == other.question:
                continue  # the answer does not concern it
            if group is None:
                continue  # never on a stored spend of the account; nothing to write
            _supersede(conn, cmd, store, again, group)
        return new_id
