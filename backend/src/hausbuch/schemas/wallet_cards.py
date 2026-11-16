"""The wallet page's sync status and balances, and the wallet cards (docs/phases/phase-7.md
§4, §13, §14, D183, D185, D186, D187; the wallet screens).

No shape here carries an address in full, a transaction hash, a URL or a key: an address is
its masked form, a missing key is named by its container variable only (principle 7: what to
do), never by its value.
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from .chain import Chain, GroupKind, GroupStatus
from .common import NonNegativeMoney, Schema
from .household import MovementKind, Quantity


class WalletAddressState(StrEnum):
    """One address's state on the wallet page, from its last finished `wallet_sync` row and
    its chain's source as configured now (services/wallet_cards.address_statuses)."""

    RETIRED = "retired"  # stillgelegt: no longer read
    KEY_MISSING = "key_missing"  # wallet.key_missing: no row is written
    SOURCE_UNAVAILABLE = "source_unavailable"  # wallet.source_unavailable: no row either
    NOT_SYNCED = "not_synced"  # no finished run yet
    FAILED = "failed"  # wallet.source_failed or wallet.sync_error
    THROTTLED = "throttled"  # wallet.source_throttled (429)
    REFUSED = "refused"  # wallet.source_refused (401/403)
    DEFERRED = "deferred"  # wallet.sync_deferred: the history is still being read
    RECONCILED = "reconciled"  # Σ movements = the chain's balance (D186 (2))
    WAITING = "waiting"  # „wartet auf Antwort“: an open question names the difference
    PENDING = "pending"  # an account-scope chain: another address of the wallet decides
    UNRECONCILED = "unreconciled"  # wallet.unreconciled
    COINS_LEFT = "coins_left"  # wallet.coins_left: a balance-only address lost coins


class WalletAddressStatus(Schema):
    """An address's state in plain German (`text`) with what to do (`todo`), and the
    Scaleway container variable to set or check (`setting`) when the cure is one."""

    address_id: UUID
    chain: Chain
    address_masked: str
    state: WalletAddressState
    text: str
    todo: str | None = None
    setting: str | None = None
    error_code: str | None = None
    finished_at: datetime | None = None
    block: int | None = None
    movements_new: int | None = None


class WalletHolding(Schema):
    """One asset's balance on the chain at the last read beside what Hausbuch books for it
    (D186 (2)). `address_id` is None when the chain reconciles the wallet's addresses
    together (Bitcoin). `booked_units` is the chain's balance less the stored difference,
    None while the run waits for an answer (the difference is then not stored)."""

    chain: Chain
    address_id: UUID | None = None
    address_masked: str | None = None
    asset_id: UUID | None = None
    symbol: str
    chain_units: Quantity
    booked_units: Quantity | None = None
    state: WalletAddressState
    at_block: int | None = None
    read_at: datetime | None = None


class WalletCardKind(StrEnum):
    """What a live proposed movement group asks of a person."""

    PROPOSED = "proposed"  # legs to confirm or correct (the first sync, a review routing)
    CHANGE = "change"  # a Bitcoin change card (D185 (2)): „Ja, eigene Adresse“ / „Nein“
    UNREADABLE = "unreadable"  # wallet.record_unreadable: Hausbuch cannot read its legs
    QUESTION = "question"  # another question without legs (a CoinJoin, nothing moved)
    UNSTORABLE = "unstorable"  # routing.chain_unstorable: no movements, no question


class WalletAnswer(StrEnum):
    """A person's answer to a proposed group (D183 (5)), built for groups whose legs all go
    one way: „Bezahlung“ (out: a disposal), „Geschenk oder Zahlung erhalten“ (in: an
    acquisition at a typed cost or 0), „Belohnung“ (in: a staking reward) and „Spam“ (in,
    tokens only: hidden from then on, D187). „Tausch“ and
    „Übertrag an eigene Wallet/Börse“ need the legs picked and a transfer link (step 5)."""

    PAYMENT = "payment"
    GIFT = "gift"
    REWARD = "reward"
    SPAM = "spam"


class WalletLeg(Schema):
    movement_id: UUID
    address_masked: str | None = None
    asset_id: UUID
    symbol: str
    kind: MovementKind
    quantity: Quantity


class WalletGroup(Schema):
    """GET /wallets/{id}/groups: one card per live proposed group (D183 (1)). `confirmable`:
    „Bestätigen“ keeps it as it stands (no question, no leg left uncategorized); `answers`:
    the corrections that fit its legs."""

    id: UUID
    account_id: UUID
    chain: Chain
    kind: GroupKind
    status: GroupStatus
    occurred_at: datetime
    card: WalletCardKind
    question: str | None = None
    text: str
    legs: list[WalletLeg] = Field(default_factory=list)
    confirmable: bool = False
    answers: list[WalletAnswer] = Field(default_factory=list)


class WalletGroupAnswerBody(Schema):
    """POST /groups/{gid}/answer. `cost_eur` only with `gift` (the acquisition's cost; 0 for
    a gift, D183 (5))."""

    answer: WalletAnswer
    cost_eur: NonNegativeMoney | None = None


class WalletGroupDone(Schema):
    """The group that now stands for the transaction (the same id after „Bestätigen“)."""

    group_id: UUID


class ChangeCandidateOut(Schema):
    """One output a change card asks about: its vout, masked address and amount in BTC."""

    vout: int
    address_masked: str
    amount_btc: Quantity


class ChangeCard(Schema):
    """GET /groups/{gid}/change: the outputs the card asks about, read again from the stored
    original (D182 (3)); `original_missing` when no stored original holds the spend."""

    group_id: UUID
    candidates: list[ChangeCandidateOut] = Field(default_factory=list)
    original_missing: bool = False


class ChangeAnswerBody(Schema):
    """POST /groups/{gid}/change/payee and /change/own: the output answered."""

    vout: int = Field(ge=0)


class HiddenToken(Schema):
    """GET /wallets/{id}/hidden-tokens (D187 (3)): a token held as spam, by its asset's name
    (the masked contract with the symbol the chain tells), never priced."""

    asset_id: UUID
    chain: Chain
    name: str
    units: Quantity
    groups: int


class WalletState(Schema):
    """GET /wallets/{id}/state: each address's state, the holdings per asset at the last
    read, and the live proposed groups of the wallet (those with a question among them)."""

    statuses: list[WalletAddressStatus] = Field(default_factory=list)
    holdings: list[WalletHolding] = Field(default_factory=list)
    open_groups: int = 0
    open_questions: int = 0


class SpamVerdict(Schema):
    """POST /assets/{id}/promote and /demote: the token's verdict now and the number of
    groups the flip changed (D187 (3))."""

    asset_id: UUID
    spam: bool
    groups: int
