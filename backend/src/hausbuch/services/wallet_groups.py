"""A wallet's open movement groups and a person's answers to them (docs/phases/phase-7.md
§13, §15.1, D183 (1), (5), D186 (1), D187; the wallet screens).

A group is open, and a card, while it is live and proposed or holds a live proposed leg: a
later sync can add a leg to a group a person already answered (a second own address of the
same transaction), and that leg waits for a person like any other (review of PR #398,
finding 2). Confirming or answering such a group handles its proposed legs only; its
confirmed legs stay as they are.

- `list_groups`: one card per open group of a wallet, its legs in plain words by the masked
  address and the asset's symbol, never a transaction hash or a full address.
- `confirm_group`: „Bestätigen“ keeps a group as the sync proposed it: the group and its
  proposed movements become confirmed (the triggers allow only that change). Refused for a
  group that needs an answer: one with a question (other than „nothing moved“), one with a
  proposed leg still `uncategorized` (a first sync's receive or send: confirming it as is
  would put an uncategorised leg into the lots), and one without legs (a change card, a
  transaction Hausbuch could not store).
- `answer_group`: the answers of D183 (5) that need no leg picked and no link: Bezahlung (out
  legs → `disposal`), Geschenk/Zahlung erhalten (in legs → `acquisition`; a typed cost is its
  cost, a typed 0 is 0, and no cost leaves it NULL, unknown: the lots price it by the day or
  show „Kurs fehlt“, D188), Belohnung (in legs → `staking_reward`) and Spam (in token legs →
  `spam`, the token marked as spam so later syncs hide it too, and every other live receipt of
  the token marked as `demote_token` does, so no card stays open for a hidden token, D187).
  „Spam“ is neither offered nor accepted for a token the registry lists or one the household
  already holds by a confirmed movement (review finding 1). The answer is a new group that
  supersedes the old one, one new movement per live leg (`source = user`, no movement hash:
  the superseded chain row keeps its hash, so a later sync recognises the leg and writes
  nothing again; the address and index stay, so reconciliation counts the new row on the same
  address). Each leg the answer is about becomes confirmed with the answer's kind; every other
  leg keeps its kind and status. „Tausch“ and „Übertrag an eigene Wallet/Börse“ are not built
  here (a leg pick and a transfer link, step 5).
- `answered_groups`, `undo_group`: „Antwort zurücknehmen“ on a group a person answered or
  confirmed while no transfer link touches its legs: a new group with the original
  classification (each leg's kind and euro amount from the row the sync wrote, the group's
  kind and question from the sync's group), all its legs proposed, so the card shows again.
  Not for a spam group: „Kein Spam“ under the hidden tokens shows that one again.
- `hidden_tokens`, `promote_token`, `demote_token` (D187 (3)): the spam verdict is the
  asset's `spam` flag, which a person may flip. Both touch only the token's incoming legs of
  kind airdrop, uncategorized or spam, never an acquisition, a disposal, a swap's legs or
  another asset's legs: demoting makes those legs confirmed spam (a group whose moving legs
  are all such legs becomes a spam group); promoting makes its spam legs proposed airdrops (a
  card, D186 (1)). A group with no such leg is left as it is. Demoting is offered through the
  API only; it hides the token, held or not, until „Kein Spam“.
"""

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from decimal import Decimal, localcontext
from uuid import UUID

from sqlalchemy import Connection, func, insert, or_, select, update

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import movement_group as group_table
from hausbuch.db.tables import transfer_link as link_table
from hausbuch.db.tables import wallet_address as address_table
from hausbuch.rules.chain.solana import QUESTION_UNREADABLE
from hausbuch.rules.chain.spam import listed
from hausbuch.rules.chain.utxo import FOREIGN_INPUTS_QUESTION, NO_MOVEMENT_QUESTION
from hausbuch.rules.errors import (
    WALLET_ANSWER_UNFIT,
    WALLET_GROUP_DONE,
    WALLET_GROUP_NEEDS_ANSWER,
    RuleError,
)
from hausbuch.rules.lots import BERLIN
from hausbuch.schemas.chain import (
    AssetKey,
    Chain,
    GroupKind,
    GroupStatus,
    WaitingCoin,
    WalletWaiting,
)
from hausbuch.schemas.enums import AccountKind, EntityType
from hausbuch.schemas.events import (
    EventType,
    FieldChange,
    MovementGroupRecorded,
    MovementRecorded,
    MovementSuperseded,
    Updated,
)
from hausbuch.schemas.household import MovementKind, MovementSource, MovementStatus
from hausbuch.schemas.wallet_cards import (
    HiddenToken,
    WalletAnswer,
    WalletAnsweredGroup,
    WalletCardKind,
    WalletGroup,
    WalletGroupAnswerBody,
    WalletLeg,
)

from .context import CommandContext
from .errors import NotFoundError
from .events import append_event

# The reason a person's answer or confirmation is recorded under (MovementGroupRecorded).
PERSON = "routing.person"
# The reason „Antwort zurücknehmen“ records the re-proposed group under.
PERSON_UNDONE = "routing.person_undone"
# The reason a spam verdict's sweep (demote, promote, the „Spam“ answer's other receipts)
# records the groups it marks under: no person answered those, so none is listed as answered.
PERSON_SPAM = "routing.person_spam"
_LIMIT = 200
_ANSWERED_LIMIT = 50

# D187 (3): the incoming legs of a token a spam verdict may change.
_RECEIPT_KINDS = {MovementKind.AIRDROP.value, MovementKind.UNCATEGORIZED.value}

_DONE = "Diese Transaktion ist schon beantwortet oder bestätigt. Bitte die Seite neu laden."
_NEEDS_ANSWER = (
    "Diese Transaktion braucht eine Antwort, bevor sie gebucht werden kann: bitte unten "
    "auswählen, was sie war."
)
_OUT_ONLY = "„Bezahlung“ passt nur zu einer Transaktion, bei der nur etwas abgeht."
_IN_ONLY = "Diese Antwort passt nur zu einer Transaktion, bei der nur etwas eingeht."
_SPAM_NATIVE = (
    "„Spam“ passt nur zu Token, nicht zur eigenen Währung der Blockchain (BTC, ETH, SOL, BNB)."
)
_SPAM_KNOWN = (
    "„Spam“ passt nicht zu einem Token, den Hausbuch kennt oder den Sie schon gebucht halten: "
    "das würde den ganzen Bestand verbergen. Bitte „Geschenk oder Zahlung erhalten“ oder "
    "„Belohnung“ wählen."
)
_COST_ONE_LEG = (
    "Ein Kaufpreis lässt sich nur angeben, wenn genau ein Token eingegangen ist; bei "
    "mehreren bitte ohne Betrag antworten."
)
_LINKED = (
    "Diese Transaktion ist mit einer Übertragung zwischen eigenen Konten verknüpft; solange "
    "die Verknüpfung besteht, lässt sie sich hier nicht ändern."
)
_NOT_ANSWERED = "Diese Transaktion hat niemand beantwortet; es gibt keine Antwort zurückzunehmen."
_UNDO_SPAM = (
    "Ein als Spam verborgener Token wird unter „Als Spam verborgene Tokens“ mit „Kein Spam“ "
    "wieder gezeigt."
)
_NOT_SPAM = "Dieser Token ist nicht als Spam verborgen."
_ALREADY_SPAM = "Dieser Token ist schon als Spam verborgen."
_NATIVE_NOT_SPAM = "Die eigene Währung einer Blockchain kann nicht als Spam verborgen werden."


# --- Reading ------------------------------------------------------------------------------


def _require_wallet(conn: Connection, account_id: UUID, *, lock: bool = False) -> None:
    query = select(account_table.c.kind).where(account_table.c.id == account_id)
    if lock:
        query = query.with_for_update()
    if conn.execute(query).scalar_one_or_none() != AccountKind.WALLET.value:
        raise NotFoundError(EntityType.WALLET, account_id)


def open_clause():
    """A live chain group waits for a person: proposed, or holding a live proposed leg (a
    leg a later sync added to a group already answered)."""
    g, m = group_table.c, movement_table.c
    proposed_leg = (
        select(m.id)
        .where(
            m.group_id == g.id,
            m.superseded_by.is_(None),
            m.status == MovementStatus.PROPOSED.value,
        )
        .exists()
    )
    return (
        g.chain.is_not(None)
        & g.superseded_by.is_(None)
        & ((g.status == GroupStatus.PROPOSED.value) | proposed_leg)
    )


def card_kind(question: str | None, has_legs: bool) -> WalletCardKind:
    """What an open group asks of a person (its question and whether it has legs)."""
    if question == QUESTION_UNREADABLE:
        return WalletCardKind.UNREADABLE
    if has_legs:
        return WalletCardKind.PROPOSED
    if question is None:
        return WalletCardKind.UNSTORABLE
    if question in (FOREIGN_INPUTS_QUESTION, NO_MOVEMENT_QUESTION):
        return WalletCardKind.QUESTION
    return WalletCardKind.CHANGE


def _fee(leg: WalletLeg) -> bool:
    return leg.kind is MovementKind.FEE


def _pending(legs: list[WalletLeg]) -> list[WalletLeg]:
    """The legs a card's answer is about: its proposed legs (all of a sync's new group), or
    every leg of a proposed group that holds no proposed one."""
    return [leg for leg in legs if leg.status is MovementStatus.PROPOSED] or legs


def _answers(legs: list[WalletLeg], spam_unfit: set[UUID]) -> list[WalletAnswer]:
    moving = [leg for leg in legs if not _fee(leg)]
    if not moving:
        return []
    if all(leg.quantity < 0 for leg in moving):
        return [WalletAnswer.PAYMENT]
    if all(leg.quantity > 0 for leg in moving):
        answers = [WalletAnswer.GIFT, WalletAnswer.REWARD]
        if not any(leg.asset_id in spam_unfit for leg in moving):
            answers.append(WalletAnswer.SPAM)
        return answers
    return []


def _confirmable(question: str | None, legs: list[WalletLeg]) -> bool:
    if not legs:
        return question == NO_MOVEMENT_QUESTION  # „nothing moved“: noted, nothing to book
    return question is None and all(leg.kind is not MovementKind.UNCATEGORIZED for leg in legs)


def card_text(card: WalletCardKind, kind: GroupKind, legs: list[WalletLeg]) -> str:
    """The card's sentence in plain German: what happened (principle 7)."""
    match card:
        case WalletCardKind.CHANGE:
            return (
                "Bei dieser Bitcoin-Zahlung ging Geld an Adressen, die Hausbuch nicht kennt. "
                "Eine davon kann Ihr Wechselgeld sein (eine neue eigene Adresse der Wallet). "
                "Für jede Adresse bitte sagen, ob sie Ihnen gehört."
            )
        case WalletCardKind.UNREADABLE:
            return (
                "Hausbuch kann diese Transaktion nicht vollständig lesen: welche Token sie "
                "bewegt hat, steht nicht lesbar in den Daten der Blockchain. Bis sie gelesen "
                "ist, weicht der Saldo dieser Wallet ab. Eine Antwort lässt sich hier noch "
                "nicht geben."
            )
        case WalletCardKind.UNSTORABLE:
            return (
                "Hausbuch konnte diese Transaktion nicht speichern: ein Betrag ist größer oder "
                "feiner, als Hausbuch ihn speichern kann, oder sie ist nicht lesbar aufgeteilt. "
                "Die übrigen Transaktionen der Adresse sind eingelesen; der Saldo weicht um "
                "diese Transaktion ab, bis Hausbuch sie lesen kann. Hier ist nichts zu tun."
            )
        case WalletCardKind.QUESTION:
            return (
                "Diese Transaktion berührt eine eigene Adresse, Hausbuch kann aber nicht sagen, "
                "was sie für die Wallet bedeutet."
            )
    words = _words(legs)
    if all(leg.kind is not MovementKind.UNCATEGORIZED for leg in legs):
        return f"Vorschlag von Hausbuch: {words}. Stimmt das, „Bestätigen“."
    return f"{words}. Was war das?"


def _words(legs: list[WalletLeg]) -> str:
    return ", ".join(_leg_words(leg) for leg in legs)


def _amount(quantity: Decimal) -> str:
    text = format(abs(quantity).normalize(), "f")
    return text.replace(".", ",")


def _leg_words(leg: WalletLeg) -> str:
    where = f" ({leg.address_masked})" if leg.address_masked else ""
    if leg.kind is MovementKind.FEE:
        return f"Gebühr {_amount(leg.quantity)} {leg.symbol}"
    verb = "erhalten" if leg.quantity > 0 else "gesendet"
    return f"{_amount(leg.quantity)} {leg.symbol} {verb}{where}"


def _legs(conn: Connection, group_ids: list[UUID]) -> dict[UUID, list[WalletLeg]]:
    if not group_ids:
        return {}
    m, t, a = movement_table.c, asset_table.c, address_table.c
    rows = conn.execute(
        select(
            m.id,
            m.group_id,
            m.asset_id,
            m.kind,
            m.status,
            m.quantity,
            func.coalesce(t.symbol, t.name).label("symbol"),
            a.address_masked,
        )
        .select_from(
            movement_table.join(asset_table, t.id == m.asset_id).outerjoin(
                address_table, a.id == m.address_id
            )
        )
        .where(m.group_id.in_(group_ids), m.superseded_by.is_(None))
        .order_by(m.group_id, m.kind == MovementKind.FEE.value, m["index"], m.id)
    ).all()
    out: dict[UUID, list[WalletLeg]] = {}
    for r in rows:
        out.setdefault(r.group_id, []).append(
            WalletLeg(
                movement_id=r.id,
                address_masked=r.address_masked,
                asset_id=r.asset_id,
                symbol=r.symbol,
                kind=MovementKind(r.kind),
                status=MovementStatus(r.status),
                quantity=r.quantity,
            )
        )
    return out


def spam_unfit(conn: Connection, asset_ids: set[UUID]) -> set[UUID]:
    """The assets „Spam“ does not fit (review finding 1): a chain's native coin, a token the
    registry lists by its chain and contract, and one the workspace holds by a live confirmed
    movement that is not spam (hiding it would hide a real position)."""
    if not asset_ids:
        return set()
    t, m = asset_table.c, movement_table.c
    unfit: set[UUID] = set()
    for r in conn.execute(
        select(t.id, t.chain, t.contract_address).where(t.id.in_(asset_ids))
    ).all():
        native = r.chain is None or r.contract_address is None
        if native or listed(AssetKey(chain=Chain(r.chain), contract=r.contract_address)):
            unfit.add(r.id)
    unfit |= set(
        conn.execute(
            select(m.asset_id)
            .distinct()
            .where(
                m.asset_id.in_(asset_ids),
                m.superseded_by.is_(None),
                m.status == MovementStatus.CONFIRMED.value,
                m.kind != MovementKind.SPAM.value,
            )
        ).scalars()
    )
    return unfit


def _to_card(row, legs: list[WalletLeg], unfit: set[UUID]) -> WalletGroup:
    card = card_kind(row.question, bool(legs))
    kind = GroupKind(row.kind)
    pending = _pending(legs)
    return WalletGroup(
        id=row.id,
        account_id=row.account_id,
        chain=Chain(row.chain),
        kind=kind,
        status=GroupStatus(row.status),
        occurred_at=row.occurred_at,
        card=card,
        question=None if card is WalletCardKind.CHANGE else row.question,
        text=card_text(card, kind, pending),
        legs=legs,
        confirmable=_confirmable(row.question, pending),
        answers=_answers(pending, unfit) if card is WalletCardKind.PROPOSED else [],
    )


def _cards(conn: Connection, rows: list) -> list[WalletGroup]:
    legs = _legs(conn, [r.id for r in rows])
    unfit = spam_unfit(conn, {leg.asset_id for group in legs.values() for leg in group})
    return [_to_card(r, legs.get(r.id, []), unfit) for r in rows]


def _open_groups(account_id: UUID | None = None):
    g = group_table.c
    query = select(g.id, g.account_id, g.chain, g.kind, g.status, g.occurred_at, g.question).where(
        open_clause()
    )
    if account_id is not None:
        query = query.where(g.account_id == account_id)
    return query


def list_groups(conn: Connection, account_id: UUID, *, limit: int = _LIMIT) -> list[WalletGroup]:
    """GET /wallets/{id}/groups: the open groups, those asking a question first, then the
    newest (at most `limit`)."""
    _require_wallet(conn, account_id)
    g = group_table.c
    rows = conn.execute(
        _open_groups(account_id)
        .order_by(g.question.is_(None), g.occurred_at.desc(), g.id)
        .limit(limit)
    ).all()
    return _cards(conn, rows)


def wallet_group_cards(conn: Connection) -> list[WalletGroup]:
    """Zu prüfen: the open groups of every wallet that ask a question or have no legs
    (change, unreadable, unstorable and other questions), each its own card. Groups with legs
    and no question are counted per wallet (`proposed_counts`), not listed: a first sync
    proposes the wallet's whole history, which belongs on the wallet page."""
    g = group_table.c
    has_legs = (
        select(movement_table.c.id)
        .where(movement_table.c.group_id == g.id, movement_table.c.superseded_by.is_(None))
        .exists()
    )
    rows = conn.execute(
        _open_groups()
        .where(g.question.is_not(None) | ~has_legs)
        .order_by(g.occurred_at.desc(), g.id)
        .limit(_LIMIT)
    ).all()
    return _cards(conn, rows)


def proposed_counts(conn: Connection) -> dict[UUID, tuple[int, object]]:
    """Per wallet account, (open groups with legs and no question, the newest one's creation
    time)."""
    g = group_table.c
    has_legs = (
        select(movement_table.c.id)
        .where(movement_table.c.group_id == g.id, movement_table.c.superseded_by.is_(None))
        .exists()
    )
    rows = conn.execute(
        select(g.account_id, func.count(), func.max(g.created_at))
        .where(open_clause(), g.question.is_(None), has_legs)
        .group_by(g.account_id)
    ).all()
    return {account_id: (n, newest) for account_id, n, newest in rows}


WAITING_ACTION = "Transaktionen prüfen"


def waiting(conn: Connection, on: date) -> list[WalletWaiting]:
    """Per wallet, the coins its live proposed movements of the Berlin day `on` or before hold
    (spam left out, fees included, summed exactly in SQL) and how many open transactions
    they are in: the Krypto tab, the Anlagen overview and the net worth count confirmed
    movements only, so a wallet whose history waits for a person shows fewer coins than it
    holds (16 Nov 2026, Luis: „It seems it booked less coins than are in the wallet.“). The
    link opens the wallet's page, whose „Transaktionen prüfen“ lists them; Zu prüfen shows
    only a count that points there. By label, then account."""
    m, g, a, acc = movement_table.c, group_table.c, asset_table.c, account_table.c
    end = datetime.combine(on + timedelta(days=1), time(), BERLIN)
    live = (
        select(m.account_id, m.asset_id, m.group_id, m.quantity)
        .join(group_table, g.id == m.group_id)
        .join(account_table, acc.id == m.account_id)
        .join(asset_table, a.id == m.asset_id)
        .where(
            acc.kind == AccountKind.WALLET.value,
            m.status == MovementStatus.PROPOSED.value,
            m.superseded_by.is_(None),
            g.superseded_by.is_(None),
            g.chain.is_not(None),
            a.spam.is_(False),
            m.occurred_at < end,
        )
        .subquery()
    )
    groups = dict(
        conn.execute(
            select(live.c.account_id, func.count(live.c.group_id.distinct())).group_by(
                live.c.account_id
            )
        ).all()
    )
    if not groups:
        return []
    sums = conn.execute(
        select(live.c.account_id, live.c.asset_id, func.sum(live.c.quantity).label("units"))
        .group_by(live.c.account_id, live.c.asset_id)
        .having(func.sum(live.c.quantity) != 0)
    ).all()
    assets = {
        r.id: r
        for r in conn.execute(
            select(a.id, a.symbol, a.name, a.contract_address).where(
                a.id.in_({s.asset_id for s in sums})
            )
        )
    }
    labels = dict(conn.execute(select(acc.id, acc.label).where(acc.id.in_(list(groups)))).all())
    coins: dict[UUID, list[WaitingCoin]] = {}
    for s in sums:
        asset = assets[s.asset_id]
        coins.setdefault(s.account_id, []).append(
            WaitingCoin(
                asset_id=s.asset_id,
                symbol=asset.symbol,
                name=asset.name,
                units=s.units,
            )
        )
    out = []
    for account_id, n in groups.items():
        listed = sorted(
            coins.get(account_id, []), key=lambda c: (_coin_label(c, assets), str(c.asset_id))
        )
        out.append(
            WalletWaiting(
                account_id=account_id,
                label=labels[account_id],
                groups=n,
                coins=listed,
                text=_waiting_text(labels[account_id], n, listed, assets),
                action=WAITING_ACTION,
                href=f"/accounts/{account_id}",
            )
        )
    return sorted(out, key=lambda w: (w.label, str(w.account_id)))


def _coin_label(coin: WaitingCoin, assets: dict) -> str:
    """A token by its name („Ether (Portal)“, so it never reads as the chain's own ETH), the
    chain's own coin by its symbol."""
    if assets[coin.asset_id].contract_address is not None or coin.symbol is None:
        return coin.name
    return coin.symbol


def _german_units(units: Decimal) -> str:
    """Every digit the quantity has (never rounded: code states the units exactly), a
    German decimal comma and thousands points."""
    with localcontext() as wide:
        wide.prec = 60
        text = f"{abs(units).normalize():f}"
    whole, _, fraction = text.partition(".")
    whole = f"{int(whole):,}".replace(",", ".")
    return f"{whole},{fraction}" if fraction else whole


def _listing(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} und {items[-1]}"


def _waiting_text(label: str, groups: int, coins: list[WaitingCoin], assets: dict) -> str:
    def words(selected: list[WaitingCoin]) -> str:
        return _listing([f"{_german_units(c.units)} {_coin_label(c, assets)}" for c in selected])

    added = [c for c in coins if c.units > 0]
    taken = [c for c in coins if c.units < 0]
    count = f"{groups} Transaktion wartet" if groups == 1 else f"{groups} Transaktionen warten"
    if added:
        text = (
            f"{words(added)} auf „{label}“ sind gelesen, zählen aber erst nach Ihrer "
            "Bestätigung mit"
        )
        if taken:
            text += f"; Abgänge von {words(taken)} sind noch nicht abgezogen"
    elif taken:
        text = (
            f"Abgänge von {words(taken)} auf „{label}“ sind gelesen, werden aber erst nach "
            "Ihrer Bestätigung abgezogen"
        )
    else:
        verb = "wartet" if groups == 1 else "warten"
        noun = "Transaktion" if groups == 1 else "Transaktionen"
        return (
            f"Auf „{label}“ {verb} {groups} {noun} auf Ihre Bestätigung; zusammen ändern sie "
            "den Bestand nicht."
        )
    return f"{text} ({count})."


def _answered_by_person():
    """The group carries a person's confirmation or answer (its movement_group.confirmed
    event under PERSON)."""
    e, g = event_table.c, group_table.c
    return (
        select(e.id)
        .where(
            e.entity_id == g.id,
            e.type == EventType.MOVEMENT_GROUP_CONFIRMED.value,
            e.payload["reason"].astext == PERSON,
        )
        .exists()
    )


def _spam_leg_clause():
    """The group holds a live spam leg (a hidden token's receipt): „Kein Spam“ shows it."""
    g, m = group_table.c, movement_table.c
    return (
        select(m.id)
        .where(
            m.group_id == g.id,
            m.superseded_by.is_(None),
            m.kind == MovementKind.SPAM.value,
        )
        .exists()
    )


def _linked_clause():
    """A transfer link not superseded touches one of the group's live legs."""
    g, m, k = group_table.c, movement_table.c, link_table.c
    return (
        select(k.id)
        .select_from(
            link_table.join(
                movement_table,
                or_(k.out_movement_id == m.id, k.in_movement_id == m.id),
            )
        )
        .where(m.group_id == g.id, m.superseded_by.is_(None), k.superseded_by.is_(None))
        .exists()
    )


def answered_groups(
    conn: Connection, account_id: UUID, *, limit: int = _ANSWERED_LIMIT
) -> list[WalletAnsweredGroup]:
    """GET /wallets/{id}/answered: the live groups a person answered or confirmed, newest
    first (at most `limit`), each with whether „Antwort zurücknehmen“ applies. A spam group
    is not listed: „Kein Spam“ shows its token again."""
    _require_wallet(conn, account_id)
    g = group_table.c
    rows = conn.execute(
        select(
            g.id,
            g.account_id,
            g.chain,
            g.kind,
            g.occurred_at,
            _linked_clause().label("linked"),
        )
        .where(
            g.account_id == account_id,
            g.chain.is_not(None),
            g.superseded_by.is_(None),
            g.status == GroupStatus.CONFIRMED.value,
            g.kind != GroupKind.SPAM.value,
            ~_spam_leg_clause(),
            _answered_by_person(),
        )
        .order_by(g.occurred_at.desc(), g.id)
        .limit(limit)
    ).all()
    legs = _legs(conn, [r.id for r in rows])
    out = []
    for r in rows:
        group_legs = legs.get(r.id, [])
        if not group_legs:
            continue
        out.append(
            WalletAnsweredGroup(
                id=r.id,
                account_id=r.account_id,
                chain=Chain(r.chain),
                kind=GroupKind(r.kind),
                occurred_at=r.occurred_at,
                text=_words(group_legs),
                legs=group_legs,
                undoable=not r.linked,
            )
        )
    return out


# --- Writing ------------------------------------------------------------------------------


def _locked_group(ctx: CommandContext, group_id: UUID):
    """The group locked after its wallet (services/wallets' lock order, which the sync's
    write follows too): 404 when it is no group of a wallet of this workspace."""
    g = group_table.c
    account_id = ctx.conn.execute(
        select(g.account_id).where(g.id == group_id, g.chain.is_not(None))
    ).scalar_one_or_none()
    if account_id is None:
        raise NotFoundError(EntityType.MOVEMENT_GROUP, group_id)
    _require_wallet(ctx.conn, account_id, lock=True)
    return ctx.conn.execute(select(group_table).where(g.id == group_id).with_for_update()).one()


def _open_group(ctx: CommandContext, group_id: UUID) -> tuple:
    """The locked group and its live legs; wallet.group_done when it is no open card."""
    row = _locked_group(ctx, group_id)
    if row.superseded_by is not None:
        raise RuleError(WALLET_GROUP_DONE, _DONE)
    moves = _live_movements(ctx, group_id)
    if row.status != GroupStatus.PROPOSED.value and not any(
        m.status == MovementStatus.PROPOSED.value for m in moves
    ):
        raise RuleError(WALLET_GROUP_DONE, _DONE)
    return row, moves


def _pending_moves(row, moves: list) -> list:
    """The live legs an answer is about (as `_pending`): the proposed ones, or every leg of
    a proposed group that holds no proposed leg."""
    proposed = [m for m in moves if m.status == MovementStatus.PROPOSED.value]
    if proposed or row.status != GroupStatus.PROPOSED.value:
        return proposed
    return moves


def _live_movements(ctx: CommandContext, group_id: UUID) -> list:
    m = movement_table.c
    return list(
        ctx.conn.execute(
            select(movement_table)
            .where(m.group_id == group_id, m.superseded_by.is_(None))
            .order_by(m["index"], m.id)
            .with_for_update()
        )
    )


def _linked(ctx: CommandContext, movement_ids: list[UUID]) -> bool:
    k = link_table.c
    if not movement_ids:
        return False
    return (
        ctx.conn.execute(
            select(k.id)
            .where(
                k.superseded_by.is_(None),
                or_(k.out_movement_id.in_(movement_ids), k.in_movement_id.in_(movement_ids)),
            )
            .limit(1)
        ).first()
        is not None
    )


def _group_event(
    ctx: CommandContext,
    group_id: UUID,
    row,
    status: GroupStatus,
    kind: GroupKind,
    reason: str = PERSON,
) -> None:
    append_event(
        ctx,
        EventType.MOVEMENT_GROUP_CONFIRMED
        if status is GroupStatus.CONFIRMED
        else EventType.MOVEMENT_GROUP_PROPOSED,
        group_id,
        MovementGroupRecorded(
            account_id=row.account_id, chain=row.chain, kind=kind.value, reason=reason
        ),
    )


def confirm_group(ctx: CommandContext, group_id: UUID) -> UUID:
    """POST /groups/{gid}/confirm: „Bestätigen“. Confirms the group's proposed legs (and a
    proposed group itself); its confirmed legs stay as they are."""
    row, moves = _open_group(ctx, group_id)
    pending = _pending_moves(row, moves)
    kinds = {MovementKind(m.kind) for m in pending}
    if pending:
        if row.question is not None or MovementKind.UNCATEGORIZED in kinds:
            raise RuleError(WALLET_GROUP_NEEDS_ANSWER, _NEEDS_ANSWER)
    elif row.question != NO_MOVEMENT_QUESTION:
        raise RuleError(WALLET_GROUP_NEEDS_ANSWER, _NEEDS_ANSWER)
    g, m = group_table.c, movement_table.c
    if row.status == GroupStatus.PROPOSED.value:  # the trigger refuses confirmed → confirmed
        ctx.conn.execute(
            update(group_table).where(g.id == group_id).values(status=GroupStatus.CONFIRMED.value)
        )
    _group_event(ctx, group_id, row, GroupStatus.CONFIRMED, GroupKind(row.kind))
    for move in pending:
        if move.status != MovementStatus.PROPOSED.value:
            continue
        ctx.conn.execute(
            update(movement_table)
            .where(m.id == move.id)
            .values(status=MovementStatus.CONFIRMED.value)
        )
        append_event(
            ctx,
            EventType.MOVEMENT_CONFIRMED,
            move.id,
            MovementRecorded(
                account_id=move.account_id,
                asset_id=move.asset_id,
                kind=MovementKind(move.kind),
                source=MovementSource(move.source),
                document_id=move.document_id,
            ),
        )
    return group_id


# A leg's change in a superseding group: its new kind and status and, for an undo, its euro
# amount; None keeps the leg as it is (kind, status, amounts).
_Change = dict | None


def _supersede(
    ctx: CommandContext,
    row,
    moves: list,
    *,
    kind: GroupKind,
    change: Callable[[object], _Change],
    reason: str = PERSON,
    question: str | None = None,
) -> UUID:
    """A new group of `kind` for the same transaction, one new movement per live leg
    (`source = user`, no hash, the same address and index), each old row superseded. Each
    leg takes what `change` returns for it, or stays as it was; the group is proposed while
    a proposed leg remains in it, confirmed otherwise."""
    g, m = group_table.c, movement_table.c
    keep = {"account_id", "asset_id", "quantity", "fee_eur", "fee_quantity", "occurred_at"}
    keep |= {"tax_withheld_eur", "external_ref", "network", "tx_hash", "document_id"}
    keep |= {"transaction_id", "address_id", "index", "eur_amount", "note"}
    keep |= {"counterparty_hmac", "counterparty_masked"}  # D261: an answered send still teaches
    plans = []
    for move in moves:
        values = {name: move._mapping[name] for name in keep}
        values["kind"], values["status"] = move.kind, move.status
        values.update(change(move) or {})
        plans.append((move, values))
    open_ = any(v["status"] == MovementStatus.PROPOSED.value for _, v in plans)
    status = GroupStatus.PROPOSED if open_ else GroupStatus.CONFIRMED
    new_id = ctx.conn.execute(
        insert(group_table)
        .values(
            workspace_id=ctx.workspace_id,
            account_id=row.account_id,
            chain=row.chain,
            tx_hash=row.tx_hash,
            occurred_at=row.occurred_at,
            kind=kind.value,
            decoder=None,
            status=status.value,
            question=question,
            created_at=ctx.now,
        )
        .returning(g.id)
    ).scalar_one()
    _group_event(ctx, new_id, row, status, kind, reason)
    for move, values in plans:
        moved_id = ctx.conn.execute(
            insert(movement_table)
            .values(
                workspace_id=ctx.workspace_id,
                **values,
                source=MovementSource.USER.value,
                movement_hash=None,
                group_id=new_id,
                created_at=ctx.now,
            )
            .returning(m.id)
        ).scalar_one()
        ctx.conn.execute(
            update(movement_table).where(m.id == move.id).values(superseded_by=moved_id)
        )
        append_event(
            ctx, EventType.MOVEMENT_SUPERSEDED, move.id, MovementSuperseded(superseded_by=moved_id)
        )
        append_event(
            ctx,
            EventType.MOVEMENT_CONFIRMED
            if values["status"] == MovementStatus.CONFIRMED.value
            else EventType.MOVEMENT_PROPOSED,
            moved_id,
            MovementRecorded(
                account_id=move.account_id,
                asset_id=move.asset_id,
                kind=MovementKind(values["kind"]),
                source=MovementSource.USER,
                document_id=move.document_id,
            ),
        )
    ctx.conn.execute(update(group_table).where(g.id == row.id).values(superseded_by=new_id))
    return new_id


def _confirmed(kind: MovementKind) -> dict:
    return {"kind": kind.value, "status": MovementStatus.CONFIRMED.value}


def answer_group(ctx: CommandContext, group_id: UUID, body: WalletGroupAnswerBody) -> UUID:
    """POST /groups/{gid}/answer: the group that now stands for the transaction. The answer
    is about the group's proposed legs; its confirmed legs stay as they are."""
    row, moves = _open_group(ctx, group_id)
    pending = _pending_moves(row, moves)
    moving = [m for m in pending if m.kind != MovementKind.FEE.value]
    if not moving or row.question == QUESTION_UNREADABLE:
        raise RuleError(WALLET_ANSWER_UNFIT, _NEEDS_ANSWER)
    if _linked(ctx, [m.id for m in moves]):
        raise RuleError(WALLET_ANSWER_UNFIT, _LINKED)
    out = all(m.quantity < 0 for m in moving)
    into = all(m.quantity > 0 for m in moving)
    answer = body.answer
    if body.cost_eur is not None and (answer is not WalletAnswer.GIFT or len(moving) != 1):
        raise RuleError(WALLET_ANSWER_UNFIT, _COST_ONE_LEG)
    tokens: set[UUID] = set()
    if answer is WalletAnswer.PAYMENT:
        if not out:
            raise RuleError(WALLET_ANSWER_UNFIT, _OUT_ONLY)
        kind, leg = GroupKind.TRANSFER, MovementKind.DISPOSAL
    elif not into:
        raise RuleError(WALLET_ANSWER_UNFIT, _IN_ONLY)
    elif answer is WalletAnswer.GIFT:
        kind, leg = GroupKind.TRANSFER, MovementKind.ACQUISITION
    elif answer is WalletAnswer.REWARD:
        kind, leg = GroupKind.REWARD, MovementKind.STAKING_REWARD
    else:
        kind, leg = GroupKind.SPAM, MovementKind.SPAM
        assets = {m.asset_id for m in moving}
        tokens = _token_assets(ctx.conn, assets)
        if len(tokens) != len(assets):
            raise RuleError(WALLET_ANSWER_UNFIT, _SPAM_NATIVE)
        if spam_unfit(ctx.conn, tokens):
            raise RuleError(WALLET_ANSWER_UNFIT, _SPAM_KNOWN)
    answered = {m.id for m in pending}
    others = [m for m in moves if m.id not in answered and m.kind != MovementKind.FEE.value]
    if others:  # confirmed legs of another answer stay; so does the group's kind
        kind = GroupKind(row.kind)
    cost = None if body.cost_eur is None else -body.cost_eur  # signed like cash: paid

    def change(m) -> _Change:
        if m.id not in answered:
            return None
        if m.kind == MovementKind.FEE.value:
            return _confirmed(MovementKind.FEE)
        values = _confirmed(leg)
        if cost is not None:
            values["eur_amount"] = cost
        return values

    new_id = _supersede(ctx, row, moves, kind=kind, change=change)
    for asset_id in sorted(tokens):  # every other receipt of the token: no card stays open
        _set_spam(ctx, asset_id, True)
        _demote_groups(ctx, asset_id)
    return new_id


def _root_movement(ctx: CommandContext, move):
    """The row the sync wrote for this leg: back along superseded_by to the first one."""
    m = movement_table.c
    current = move
    while True:
        before = ctx.conn.execute(
            select(movement_table).where(m.superseded_by == current.id).limit(1)
        ).first()
        if before is None:
            return current
        current = before


def _root_group(ctx: CommandContext, row):
    g = group_table.c
    current = row
    while True:
        before = ctx.conn.execute(
            select(group_table).where(g.superseded_by == current.id).limit(1)
        ).first()
        if before is None:
            return current
        current = before


def undo_group(ctx: CommandContext, group_id: UUID) -> UUID:
    """POST /groups/{gid}/undo: „Antwort zurücknehmen“. A new group with the original
    classification, all its legs proposed (a card again); the answer stays in the history
    (append-only)."""
    row = _locked_group(ctx, group_id)
    if row.superseded_by is not None:
        raise RuleError(WALLET_GROUP_DONE, _DONE)
    if row.kind == GroupKind.SPAM.value:
        raise RuleError(WALLET_ANSWER_UNFIT, _UNDO_SPAM)
    e = event_table.c
    answered = ctx.conn.execute(
        select(e.id)
        .where(
            e.entity_id == group_id,
            e.type == EventType.MOVEMENT_GROUP_CONFIRMED.value,
            e.payload["reason"].astext == PERSON,
        )
        .limit(1)
    ).first()
    moves = _live_movements(ctx, group_id)
    if any(m.kind == MovementKind.SPAM.value for m in moves):
        raise RuleError(WALLET_ANSWER_UNFIT, _UNDO_SPAM)
    if answered is None or row.status != GroupStatus.CONFIRMED.value or not moves:
        raise RuleError(WALLET_ANSWER_UNFIT, _NOT_ANSWERED)
    if _linked(ctx, [m.id for m in moves]):
        raise RuleError(WALLET_ANSWER_UNFIT, _LINKED)
    roots = {m.id: _root_movement(ctx, m) for m in moves}
    first = _root_group(ctx, row)
    had_legs = ctx.conn.execute(
        select(movement_table.c.id).where(movement_table.c.group_id == first.id).limit(1)
    ).first()
    question = first.question if had_legs is not None else None  # never a change question

    # A token the sync first read as spam and „Kein Spam“ showed again goes back to what
    # promote made of it (an airdrop to answer), never to a proposed spam leg.
    def change(m) -> _Change:
        root = roots[m.id]
        kind = MovementKind.AIRDROP.value if root.kind == MovementKind.SPAM.value else root.kind
        return {
            "kind": kind,
            "status": MovementStatus.PROPOSED.value,
            "eur_amount": root.eur_amount,
        }

    first_kind = GroupKind.AIRDROP if first.kind == GroupKind.SPAM.value else GroupKind(first.kind)
    return _supersede(
        ctx,
        row,
        moves,
        kind=first_kind,
        change=change,
        reason=PERSON_UNDONE,
        question=question,
    )


# --- Spam (D187 (3)) ------------------------------------------------------------------------


def _token_assets(conn: Connection, asset_ids: set[UUID]) -> set[UUID]:
    t = asset_table.c
    return set(
        conn.execute(
            select(t.id).where(
                t.id.in_(asset_ids), t.chain.is_not(None), t.contract_address.is_not(None)
            )
        ).scalars()
    )


def _set_spam(ctx: CommandContext, asset_id: UUID, spam: bool) -> bool:
    """Flip the asset's spam verdict (asset.updated); False when it already holds it."""
    t = asset_table.c
    current = ctx.conn.execute(
        select(t.spam).where(t.id == asset_id).with_for_update()
    ).scalar_one()
    if current is spam:
        return False
    ctx.conn.execute(
        update(asset_table).where(t.id == asset_id).values(spam=spam, updated_at=ctx.now)
    )
    append_event(
        ctx,
        EventType.ASSET_UPDATED,
        asset_id,
        Updated(
            changes={"spam": FieldChange(old=current, new=spam)},
            reason="Spam-Urteil von Hand geändert (D187)",
        ),
    )
    return True


def hidden_tokens(conn: Connection, account_id: UUID) -> list[HiddenToken]:
    """GET /wallets/{id}/hidden-tokens: the tokens hidden as spam that the wallet holds legs
    of, by the asset's name (its masked contract and the symbol the chain told)."""
    _require_wallet(conn, account_id)
    m, t = movement_table.c, asset_table.c
    rows = conn.execute(
        select(
            t.id,
            t.chain,
            t.name,
            func.sum(m.quantity).label("units"),
            func.count(func.distinct(m.group_id)).label("groups"),
        )
        .select_from(movement_table.join(asset_table, t.id == m.asset_id))
        .where(m.account_id == account_id, m.superseded_by.is_(None), t.spam.is_(True))
        .group_by(t.id, t.chain, t.name)
        .order_by(t.chain, t.name)
    ).all()
    return [
        HiddenToken(
            asset_id=r.id, chain=Chain(r.chain), name=r.name, units=r.units, groups=r.groups
        )
        for r in rows
    ]


def _token_or_404(ctx: CommandContext, asset_id: UUID):
    t = asset_table.c
    row = ctx.conn.execute(
        select(t.id, t.chain, t.contract_address, t.spam).where(t.id == asset_id)
    ).one_or_none()
    if row is None or row.chain is None:
        raise NotFoundError(EntityType.ASSET, asset_id)
    if row.contract_address is None:
        raise RuleError(WALLET_ANSWER_UNFIT, _NATIVE_NOT_SPAM)
    return row


def _groups_with(ctx: CommandContext, asset_id: UUID) -> list:
    """The live groups with a live leg in the asset, each locked after its wallet."""
    g, m = group_table.c, movement_table.c
    ids = list(
        ctx.conn.execute(
            select(m.group_id)
            .distinct()
            .select_from(movement_table.join(group_table, g.id == m.group_id))
            .where(m.asset_id == asset_id, m.superseded_by.is_(None), g.chain.is_not(None))
        ).scalars()
    )
    if not ids:
        return []
    accounts = sorted(
        set(ctx.conn.execute(select(g.account_id).where(g.id.in_(ids)).distinct()).scalars())
    )
    for account_id in accounts:
        _require_wallet(ctx.conn, account_id, lock=True)
    return list(
        ctx.conn.execute(
            select(group_table)
            .where(g.id.in_(ids), g.superseded_by.is_(None))
            .order_by(g.occurred_at, g.id)
            .with_for_update()
        )
    )


def _retarget(ctx: CommandContext, asset_id: UUID, targets_of, new_kind, group_kind) -> int:
    """Supersede each live group with a target leg of the asset: the target legs take
    `new_kind`'s change; a group whose moving legs are all targets takes `group_kind(row)`,
    any other keeps its kind. A group without a target leg is left as it is."""
    n = 0
    for row in _groups_with(ctx, asset_id):
        moves = _live_movements(ctx, row.id)
        targets = {m.id for m in moves if targets_of(m)}
        if not targets:
            continue
        moving = [m for m in moves if m.kind != MovementKind.FEE.value]
        only = all(m.id in targets for m in moving)
        _supersede(
            ctx,
            row,
            moves,
            kind=group_kind(row) if only else GroupKind(row.kind),
            change=lambda m, targets=targets: new_kind if m.id in targets else None,
            reason=PERSON_SPAM,
        )
        n += 1
    return n


def _demote_groups(ctx: CommandContext, asset_id: UUID) -> int:
    """The token's incoming airdrop and uncategorised legs become confirmed spam (D187 (3))."""
    return _retarget(
        ctx,
        asset_id,
        lambda m: m.asset_id == asset_id and m.quantity > 0 and m.kind in _RECEIPT_KINDS,
        _confirmed(MovementKind.SPAM),
        lambda row: GroupKind.SPAM,
    )


def promote_token(ctx: CommandContext, asset_id: UUID) -> int:
    """POST /assets/{id}/promote: „Kein Spam“. The token is no longer hidden; each of its
    incoming spam legs becomes a proposed airdrop (a card the person answers, D186 (1)), a
    spam group an airdrop group; every other leg stays as it is. Returns the number of groups
    re-proposed."""
    token = _token_or_404(ctx, asset_id)
    if not token.spam:
        raise RuleError(WALLET_ANSWER_UNFIT, _NOT_SPAM)
    _set_spam(ctx, asset_id, False)
    return _retarget(
        ctx,
        asset_id,
        lambda m: m.asset_id == asset_id and m.quantity > 0 and m.kind == MovementKind.SPAM.value,
        {"kind": MovementKind.AIRDROP.value, "status": MovementStatus.PROPOSED.value},
        lambda row: GroupKind.AIRDROP if row.kind == GroupKind.SPAM.value else GroupKind(row.kind),
    )


def demote_token(ctx: CommandContext, asset_id: UUID) -> int:
    """POST /assets/{id}/demote: „Als Spam verbergen“ (API only). The token is hidden from now
    on (later syncs keep it hidden); its incoming airdrop and uncategorised legs become
    confirmed spam, a group of only such legs a spam group. An acquisition, a disposal, a
    swap and another asset's legs stay as they are. Returns the number of groups changed."""
    token = _token_or_404(ctx, asset_id)
    if token.spam:
        raise RuleError(WALLET_ANSWER_UNFIT, _ALREADY_SPAM)
    _set_spam(ctx, asset_id, True)
    return _demote_groups(ctx, asset_id)
