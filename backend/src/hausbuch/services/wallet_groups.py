"""A wallet's open movement groups and a person's answers to them (docs/phases/phase-7.md
§13, D183 (1), (5), D186 (1), D187; the wallet screens).

- `list_groups`: one card per live proposed group of a wallet, its legs in plain words by
  the masked address and the asset's symbol, never a transaction hash or a full address.
- `confirm_group`: „Bestätigen“ keeps a group as the sync proposed it: the group and its
  proposed movements become confirmed (the triggers allow only that change). Refused for a
  group that needs an answer: one with a question (other than „nothing moved“), one with a
  leg still `uncategorized` (a first sync's receive or send: confirming it as is would put
  an uncategorised leg into the lots), and one without legs (a change card, a transaction
  Hausbuch could not store).
- `answer_group`: the answers of D183 (5) that need no leg picked and no link: Bezahlung (out
  legs → `disposal`), Geschenk/Zahlung erhalten (in legs → `acquisition` at a typed cost or
  0), Belohnung (in legs → `staking_reward`) and Spam (in token legs → `spam`, and the
  token marked as spam so later syncs hide it too, D187). The answer is a new confirmed group
  that supersedes the old one, with one new movement per live leg (`source = user`, no
  movement hash: the superseded chain row keeps its hash, so a later sync recognises the leg
  and writes nothing again; the address and index stay, so reconciliation counts the new row
  on the same address). Fee legs keep their kind. „Tausch“ and „Übertrag an eigene
  Wallet/Börse“ are not built here (a leg pick and a transfer link, step 5).
- `hidden_tokens`, `promote_token`, `demote_token` (D187 (3)): the spam verdict is the
  asset's `spam` flag, which a person may flip; promoting supersedes each spam group of the
  token by a proposed `airdrop` group (a card, D186 (1)), demoting supersedes each live group
  whose legs in the token are not spam by a confirmed `spam` one.
"""

from collections.abc import Callable
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Connection, func, insert, select, update

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import movement_group as group_table
from hausbuch.db.tables import wallet_address as address_table
from hausbuch.rules.chain.solana import QUESTION_UNREADABLE
from hausbuch.rules.chain.utxo import FOREIGN_INPUTS_QUESTION, NO_MOVEMENT_QUESTION
from hausbuch.rules.errors import (
    WALLET_ANSWER_UNFIT,
    WALLET_GROUP_DONE,
    WALLET_GROUP_NEEDS_ANSWER,
    RuleError,
)
from hausbuch.schemas.chain import Chain, GroupKind, GroupStatus
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
_LIMIT = 200

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
_COST_ONE_LEG = (
    "Ein Kaufpreis lässt sich nur angeben, wenn genau ein Token eingegangen ist; bei "
    "mehreren bitte ohne Betrag antworten."
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


def card_kind(question: str | None, has_legs: bool) -> WalletCardKind:
    """What a live proposed group asks of a person (its question and whether it has legs)."""
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


def _answers(legs: list[WalletLeg], native: set[UUID]) -> list[WalletAnswer]:
    moving = [leg for leg in legs if not _fee(leg)]
    if not moving:
        return []
    if all(leg.quantity < 0 for leg in moving):
        return [WalletAnswer.PAYMENT]
    if all(leg.quantity > 0 for leg in moving):
        answers = [WalletAnswer.GIFT, WalletAnswer.REWARD]
        if not any(leg.asset_id in native for leg in moving):
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
    words = ", ".join(_leg_words(leg) for leg in legs)
    if all(leg.kind is not MovementKind.UNCATEGORIZED for leg in legs):
        return f"Vorschlag von Hausbuch: {words}. Stimmt das, „Bestätigen“."
    return f"{words}. Was war das?"


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
                quantity=r.quantity,
            )
        )
    return out


def _native_assets(conn: Connection) -> set[UUID]:
    t = asset_table.c
    return set(
        conn.execute(
            select(t.id).where(t.chain.is_not(None), t.contract_address.is_(None))
        ).scalars()
    )


def _to_card(row, legs: list[WalletLeg], native: set[UUID]) -> WalletGroup:
    card = card_kind(row.question, bool(legs))
    kind = GroupKind(row.kind)
    return WalletGroup(
        id=row.id,
        account_id=row.account_id,
        chain=Chain(row.chain),
        kind=kind,
        status=GroupStatus(row.status),
        occurred_at=row.occurred_at,
        card=card,
        question=None if card is WalletCardKind.CHANGE else row.question,
        text=card_text(card, kind, legs),
        legs=legs,
        confirmable=_confirmable(row.question, legs),
        answers=_answers(legs, native) if card is WalletCardKind.PROPOSED else [],
    )


def _live_proposed(account_id: UUID | None = None):
    g = group_table.c
    query = select(g.id, g.account_id, g.chain, g.kind, g.status, g.occurred_at, g.question).where(
        g.chain.is_not(None),
        g.status == GroupStatus.PROPOSED.value,
        g.superseded_by.is_(None),
    )
    if account_id is not None:
        query = query.where(g.account_id == account_id)
    return query


def list_groups(conn: Connection, account_id: UUID, *, limit: int = _LIMIT) -> list[WalletGroup]:
    """GET /wallets/{id}/groups: the live proposed groups, those asking a question first,
    then the newest (at most `limit`)."""
    _require_wallet(conn, account_id)
    g = group_table.c
    rows = conn.execute(
        _live_proposed(account_id)
        .order_by(g.question.is_(None), g.occurred_at.desc(), g.id)
        .limit(limit)
    ).all()
    legs = _legs(conn, [r.id for r in rows])
    native = _native_assets(conn)
    return [_to_card(r, legs.get(r.id, []), native) for r in rows]


def wallet_group_cards(conn: Connection) -> list[WalletGroup]:
    """Zu prüfen: the live proposed groups of every wallet that ask a question or have no
    legs (change, unreadable, unstorable and other questions), each its own card. Groups
    with legs and no question are counted per wallet (`proposed_counts`), not listed: a first
    sync proposes the wallet's whole history, which belongs on the wallet page."""
    g = group_table.c
    has_legs = (
        select(movement_table.c.id)
        .where(movement_table.c.group_id == g.id, movement_table.c.superseded_by.is_(None))
        .exists()
    )
    rows = conn.execute(
        _live_proposed()
        .where(g.question.is_not(None) | ~has_legs)
        .order_by(g.occurred_at.desc(), g.id)
        .limit(_LIMIT)
    ).all()
    legs = _legs(conn, [r.id for r in rows])
    native = _native_assets(conn)
    return [_to_card(r, legs.get(r.id, []), native) for r in rows]


def proposed_counts(conn: Connection) -> dict[UUID, tuple[int, object]]:
    """Per wallet account, (live proposed groups with legs and no question, the newest one's
    creation time)."""
    g = group_table.c
    has_legs = (
        select(movement_table.c.id)
        .where(movement_table.c.group_id == g.id, movement_table.c.superseded_by.is_(None))
        .exists()
    )
    rows = conn.execute(
        select(g.account_id, func.count(), func.max(g.created_at))
        .where(
            g.chain.is_not(None),
            g.status == GroupStatus.PROPOSED.value,
            g.superseded_by.is_(None),
            g.question.is_(None),
            has_legs,
        )
        .group_by(g.account_id)
    ).all()
    return {account_id: (n, newest) for account_id, n, newest in rows}


# --- Writing ------------------------------------------------------------------------------


def _open_group(ctx: CommandContext, group_id: UUID):
    """The group locked after its wallet (services/wallets' lock order): 404 when it is no
    group of a wallet of this workspace, wallet.group_done when it is no open card."""
    g = group_table.c
    account_id = ctx.conn.execute(
        select(g.account_id).where(g.id == group_id, g.chain.is_not(None))
    ).scalar_one_or_none()
    if account_id is None:
        raise NotFoundError(EntityType.MOVEMENT_GROUP, group_id)
    _require_wallet(ctx.conn, account_id, lock=True)
    row = ctx.conn.execute(select(group_table).where(g.id == group_id).with_for_update()).one()
    if row.status != GroupStatus.PROPOSED.value or row.superseded_by is not None:
        raise RuleError(WALLET_GROUP_DONE, _DONE)
    return row


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


def _group_event(
    ctx: CommandContext, group_id: UUID, row, status: GroupStatus, kind: GroupKind
) -> None:
    append_event(
        ctx,
        EventType.MOVEMENT_GROUP_CONFIRMED
        if status is GroupStatus.CONFIRMED
        else EventType.MOVEMENT_GROUP_PROPOSED,
        group_id,
        MovementGroupRecorded(
            account_id=row.account_id, chain=row.chain, kind=kind.value, reason=PERSON
        ),
    )


def confirm_group(ctx: CommandContext, group_id: UUID) -> UUID:
    """POST /groups/{gid}/confirm: „Bestätigen“."""
    row = _open_group(ctx, group_id)
    moves = _live_movements(ctx, group_id)
    kinds = {MovementKind(m.kind) for m in moves}
    if moves:
        if row.question is not None or MovementKind.UNCATEGORIZED in kinds:
            raise RuleError(WALLET_GROUP_NEEDS_ANSWER, _NEEDS_ANSWER)
    elif row.question != NO_MOVEMENT_QUESTION:
        raise RuleError(WALLET_GROUP_NEEDS_ANSWER, _NEEDS_ANSWER)
    g, m = group_table.c, movement_table.c
    ctx.conn.execute(
        update(group_table).where(g.id == group_id).values(status=GroupStatus.CONFIRMED.value)
    )
    _group_event(ctx, group_id, row, GroupStatus.CONFIRMED, GroupKind(row.kind))
    for move in moves:
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


def _supersede(
    ctx: CommandContext,
    row,
    moves: list,
    *,
    kind: GroupKind,
    status: GroupStatus,
    leg_kind: Callable[[object], MovementKind],
    eur: dict[UUID, Decimal] | None = None,
) -> UUID:
    """A new group of `kind` and `status` for the same transaction, one new movement per live
    leg (`source = user`, no hash, the same address and index), each old row superseded."""
    g, m = group_table.c, movement_table.c
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
            question=None,
            created_at=ctx.now,
        )
        .returning(g.id)
    ).scalar_one()
    _group_event(ctx, new_id, row, status, kind)
    keep = {"account_id", "asset_id", "quantity", "fee_eur", "fee_quantity", "occurred_at"}
    keep |= {"tax_withheld_eur", "external_ref", "network", "tx_hash", "document_id"}
    keep |= {"transaction_id", "address_id", "index", "eur_amount", "note"}
    movement_status = MovementStatus(status.value)
    for move in moves:
        values = {name: move._mapping[name] for name in keep}
        new_kind = leg_kind(move)
        if eur is not None and move.id in eur:
            values["eur_amount"] = eur[move.id]
        moved_id = ctx.conn.execute(
            insert(movement_table)
            .values(
                workspace_id=ctx.workspace_id,
                **values,
                kind=new_kind.value,
                source=MovementSource.USER.value,
                movement_hash=None,
                status=movement_status.value,
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
            if movement_status is MovementStatus.CONFIRMED
            else EventType.MOVEMENT_PROPOSED,
            moved_id,
            MovementRecorded(
                account_id=move.account_id,
                asset_id=move.asset_id,
                kind=new_kind,
                source=MovementSource.USER,
                document_id=move.document_id,
            ),
        )
    ctx.conn.execute(update(group_table).where(g.id == row.id).values(superseded_by=new_id))
    return new_id


def answer_group(ctx: CommandContext, group_id: UUID, body: WalletGroupAnswerBody) -> UUID:
    """POST /groups/{gid}/answer: the group that now stands for the transaction."""
    row = _open_group(ctx, group_id)
    moves = _live_movements(ctx, group_id)
    moving = [m for m in moves if m.kind != MovementKind.FEE.value]
    if not moving or row.question == QUESTION_UNREADABLE:
        raise RuleError(WALLET_ANSWER_UNFIT, _NEEDS_ANSWER)
    out = all(m.quantity < 0 for m in moving)
    into = all(m.quantity > 0 for m in moving)
    answer = body.answer
    if body.cost_eur is not None and (answer is not WalletAnswer.GIFT or len(moving) != 1):
        raise RuleError(WALLET_ANSWER_UNFIT, _COST_ONE_LEG)
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
        tokens = _token_assets(ctx.conn, {m.asset_id for m in moving})
        if len(tokens) != len({m.asset_id for m in moving}):
            raise RuleError(WALLET_ANSWER_UNFIT, _SPAM_NATIVE)
        for asset_id in sorted(tokens):
            _set_spam(ctx, asset_id, True)
    eur = None
    if body.cost_eur is not None:
        eur = {moving[0].id: -body.cost_eur}  # signed like cash: the cost was paid
    new_id = _supersede(
        ctx,
        row,
        moves,
        kind=kind,
        status=GroupStatus.CONFIRMED,
        leg_kind=lambda m: MovementKind.FEE if m.kind == MovementKind.FEE.value else leg,
        eur=eur,
    )
    return new_id


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


def promote_token(ctx: CommandContext, asset_id: UUID) -> int:
    """POST /assets/{id}/promote: „Kein Spam“. The token is no longer hidden; every live
    spam group of it becomes a proposed airdrop (a card the person answers, D186 (1)), its
    other legs unchanged. Returns the number of groups re-proposed."""
    token = _token_or_404(ctx, asset_id)
    if not token.spam:
        raise RuleError(WALLET_ANSWER_UNFIT, _NOT_SPAM)
    groups = _groups_with(ctx, asset_id)
    _set_spam(ctx, asset_id, False)
    n = 0
    for row in groups:
        moves = _live_movements(ctx, row.id)
        if not any(m.asset_id == asset_id and m.kind == MovementKind.SPAM.value for m in moves):
            continue
        _supersede(
            ctx,
            row,
            moves,
            kind=GroupKind.AIRDROP,
            status=GroupStatus.PROPOSED,
            leg_kind=lambda m: (
                MovementKind.AIRDROP
                if m.asset_id == asset_id and m.kind == MovementKind.SPAM.value
                else MovementKind(m.kind)
            ),
        )
        n += 1
    return n


def demote_token(ctx: CommandContext, asset_id: UUID) -> int:
    """POST /assets/{id}/demote: „Als Spam verbergen“. The token is hidden from now on (later
    syncs keep it hidden); every live group whose moving legs are all in this token becomes
    a confirmed spam group. A group with other assets too keeps its kind, its legs in this
    token become spam. Returns the number of groups changed."""
    token = _token_or_404(ctx, asset_id)
    if token.spam:
        raise RuleError(WALLET_ANSWER_UNFIT, _ALREADY_SPAM)
    groups = _groups_with(ctx, asset_id)
    _set_spam(ctx, asset_id, True)
    n = 0
    for row in groups:
        moves = _live_movements(ctx, row.id)
        moving = [m for m in moves if m.kind != MovementKind.FEE.value]
        only = all(m.asset_id == asset_id for m in moving)
        _supersede(
            ctx,
            row,
            moves,
            kind=GroupKind.SPAM if only else GroupKind(row.kind),
            status=GroupStatus.CONFIRMED if only else GroupStatus(row.status),
            leg_kind=lambda m: (
                MovementKind.SPAM if m.asset_id == asset_id else MovementKind(m.kind)
            ),
        )
        n += 1
    return n
