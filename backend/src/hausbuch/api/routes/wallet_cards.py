"""A wallet's state and the answers to its cards (docs/phases/phase-7.md §13, §14, D183,
D185, D186, D187; the wallet screens).

- GET /wallets/{id}/state: each address's state in plain words with what to do, and the
  holdings at the last read (the chain's balance beside what was booked).
- GET /wallets/{id}/groups: the live proposed groups, one card each.
- POST /groups/{gid}/confirm („Bestätigen“), POST /groups/{gid}/answer (Bezahlung, Geschenk
  oder Zahlung erhalten, Belohnung, Spam).
- GET /wallets/{id}/answered: the groups a person answered or confirmed; POST
  /groups/{gid}/undo („Antwort zurücknehmen“) proposes one again as the sync classified it.
- GET /groups/{gid}/change: the outputs a Bitcoin change card asks about, read again from
  the stored original; POST /groups/{gid}/change/payee („Nein, Empfänger“) and
  /change/own („Ja, eigene Adresse“).
- GET /wallets/{id}/hidden-tokens, POST /assets/{id}/promote („Kein Spam“) and
  /assets/{id}/demote („Als Spam verbergen“).

No answer carries an address in full, a transaction hash, a URL or a key (D179).
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Connection

from hausbuch.schemas.wallet_cards import (
    ChangeAnswerBody,
    ChangeCandidateOut,
    ChangeCard,
    HiddenToken,
    SpamVerdict,
    WalletAnsweredGroup,
    WalletGroup,
    WalletGroupAnswerBody,
    WalletGroupDone,
    WalletState,
)
from hausbuch.services import wallet_cards, wallet_groups
from hausbuch.services.chain import change_card
from hausbuch.settings import Settings

from ..deps import Cmd, SettingsDep, StoreDep, domain_router

router = domain_router()

_SATS = 100_000_000


def _now() -> datetime:
    return datetime.now(UTC)


def _state(conn: Connection, account_id: UUID, settings: Settings) -> WalletState:
    wallet_groups._require_wallet(conn, account_id)
    return wallet_cards.wallet_state(conn, account_id, settings)


@router.get("/wallets/{account_id}/state")
def wallet_state(account_id: UUID, cmd: Cmd, settings: SettingsDep) -> WalletState:
    """404 for an account that is no wallet of this workspace."""
    return cmd.read(_state, account_id, settings)


@router.get("/wallets/{account_id}/groups")
def wallet_groups_list(account_id: UUID, cmd: Cmd) -> list[WalletGroup]:
    """The open groups, those asking a question first, then the newest (at most 200)."""
    return cmd.read(wallet_groups.list_groups, account_id)


@router.post("/groups/{group_id}/confirm")
def wallet_group_confirm(group_id: UUID, cmd: Cmd) -> WalletGroupDone:
    """422 wallet.group_needs_answer for a group that needs an answer, wallet.group_done for
    one no longer open."""
    return WalletGroupDone(group_id=cmd.run(wallet_groups.confirm_group, group_id))


@router.post("/groups/{group_id}/answer")
def wallet_group_answer(group_id: UUID, body: WalletGroupAnswerBody, cmd: Cmd) -> WalletGroupDone:
    """The group that now stands for the transaction. 422 wallet.answer_unfit when the
    answer does not fit its legs."""
    return WalletGroupDone(group_id=cmd.run(wallet_groups.answer_group, group_id, body))


@router.get("/wallets/{account_id}/answered")
def wallet_answered_groups(account_id: UUID, cmd: Cmd) -> list[WalletAnsweredGroup]:
    """The groups a person answered or confirmed, newest first (at most 50); no spam group."""
    return cmd.read(wallet_groups.answered_groups, account_id)


@router.post("/groups/{group_id}/undo")
def wallet_group_undo(group_id: UUID, cmd: Cmd) -> WalletGroupDone:
    """„Antwort zurücknehmen“: the new proposed group with the sync's classification. 422
    wallet.answer_unfit for a group no person answered, a spam group or one a transfer link
    touches; wallet.group_done for one no longer live."""
    return WalletGroupDone(group_id=cmd.run(wallet_groups.undo_group, group_id))


@router.get("/groups/{group_id}/change")
def wallet_change_card(
    group_id: UUID, cmd: Cmd, settings: SettingsDep, store: StoreDep
) -> ChangeCard:
    """422 wallet.change_answered when it is no open change card."""
    found = change_card.candidates(
        cmd.engine, cmd.auth.workspace_id, group_id, settings=settings, store=store
    )
    if found is None:
        return ChangeCard(group_id=group_id, original_missing=True)
    return ChangeCard(
        group_id=group_id,
        candidates=[
            ChangeCandidateOut(
                vout=c.vout,
                address_masked=c.address_masked,
                amount_btc=Decimal(c.sats) / _SATS,
            )
            for c in found
        ],
    )


@router.post("/groups/{group_id}/change/payee")
def wallet_change_payee(
    group_id: UUID, body: ChangeAnswerBody, cmd: Cmd, settings: SettingsDep, store: StoreDep
) -> WalletGroupDone:
    """„Nein, Empfänger“: the output is a payee; the card gives way to the group derived."""
    new_id = change_card.answer_payee(
        cmd.engine,
        cmd.auth.workspace_id,
        group_id,
        body.vout,
        settings=settings,
        store=store,
        actor=cmd.actor,
        clock=_now,
    )
    return WalletGroupDone(group_id=new_id)


@router.post("/groups/{group_id}/change/own")
def wallet_change_own(
    group_id: UUID, body: ChangeAnswerBody, cmd: Cmd, settings: SettingsDep, store: StoreDep
) -> WalletGroupDone:
    """„Ja, eigene Adresse“: the output's address is registered on the wallet as a change
    address; the card gives way to the group derived."""
    new_id = change_card.answer_own_change(
        cmd.engine,
        cmd.auth.workspace_id,
        group_id,
        body.vout,
        settings=settings,
        store=store,
        actor=cmd.actor,
        clock=_now,
    )
    return WalletGroupDone(group_id=new_id)


@router.get("/wallets/{account_id}/hidden-tokens")
def wallet_hidden_tokens(account_id: UUID, cmd: Cmd) -> list[HiddenToken]:
    return cmd.read(wallet_groups.hidden_tokens, account_id)


@router.post("/assets/{asset_id}/promote")
def wallet_token_promote(asset_id: UUID, cmd: Cmd) -> SpamVerdict:
    """„Kein Spam“: `groups` is the number of groups now waiting as a card."""
    n = cmd.run(wallet_groups.promote_token, asset_id)
    return SpamVerdict(asset_id=asset_id, spam=False, groups=n)


@router.post("/assets/{asset_id}/demote")
def wallet_token_demote(asset_id: UUID, cmd: Cmd) -> SpamVerdict:
    """„Als Spam verbergen“: `groups` is the number of groups changed."""
    n = cmd.run(wallet_groups.demote_token, asset_id)
    return SpamVerdict(asset_id=asset_id, spam=True, groups=n)
