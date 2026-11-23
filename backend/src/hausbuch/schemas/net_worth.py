"""The net worth on the Übersicht and the done criterion's auto-rate (docs/phases/phase-6.md
D167 (4), (5), D172, §12, §13; phase 6 step 6). The month-end row itself is
household.ValuationSnapshot."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import Field

from .common import _STRING_ONLY, Money, Schema, _string_schema
from .enums import AccountKind
from .household import NetWorthClass, PriceEur, PriceSourceKind, Quantity


class NetWorthLineKind(StrEnum):
    ACCOUNT = "account"  # a bank, depot-cash or credit-card balance
    POSITION = "position"  # units of an asset on a depot, exchange or wallet account
    # The property's estimate (D86); before its handover the price paid so far, „Anzahlung
    # Kaufpreis“, not an estimate (D265).
    PROPERTY = "property"
    # A property loan's balance by its schedule; before the handover what it paid out (D265).
    LOAN = "loan"


class NetWorthLine(Schema):
    """One figure of the net worth, asset by asset (D172: the month-end is compared with the
    household's spreadsheet line by line). `value_eur` None: not valued, counted nowhere,
    `not_valued_reason` says why. A balance's `as_of` is the last day a reconciled statement
    covers (D167 (4)); a position's price has its own day. `estimate`: the property's value
    and a loan's balance from the handover on, both labelled as estimates on the screen;
    before the handover the price paid so far and the loan's payouts are booked figures,
    not estimates (D265)."""

    kind: NetWorthLineKind
    net_worth_class: NetWorthClass
    label: str
    account_id: UUID | None = None
    account_kind: AccountKind | None = None
    account_label: str | None = None
    asset_id: UUID | None = None
    property_id: UUID | None = None
    loan_id: UUID | None = None
    units: Quantity | None = None
    price_eur: PriceEur | None = None
    price_source: PriceSourceKind | None = None
    price_on: date | None = None
    value_eur: Money | None = None
    as_of: date | None = None
    stale: bool = False
    estimate: bool = False
    note: str | None = None  # where an estimate comes from, in the screen's words
    # Phase 6 D292 (3): money sent to the account (`account_id`, the receiver) whose statements
    # do not reach the payment's day yet, „unterwegs zu <Konto>“, a line of its own beside the
    # account's balance (`as_of` None, the day in `note`). A stored month-end folds it into
    # the account's balance row (services.net_worth.merge_transit).
    in_transit: bool = False
    not_valued_reason: str | None = None


class AccountTab(StrEnum):
    """The Konten page's tabs (phase 10 D279, §7.6 Konten (2)) by the account's kind: both
    bank kinds are one tab; the loans' tab reads `loan_balances`."""

    BANK = "bank"  # bank_current and bank_savings
    CARD = "card"  # credit_card
    DEPOT = "depot"
    EXCHANGE = "exchange"
    WALLET = "wallet"


class NetWorthTodoCode(StrEnum):
    BALANCE_UNRECONCILED = "net_worth.balance_unreconciled"
    # A balance older than the day, nothing waiting: the next statement (review 15 Nov 2026).
    BALANCE_OUTDATED = "net_worth.balance_outdated"
    PRICE_MISSING = "net_worth.price_missing"
    PRICE_STALE = "net_worth.price_stale"
    POSITION_NOT_RECONCILED = "net_worth.position_not_reconciled"
    # A fund the depot's latest report lists with no unit booked (review of 15 Nov 2026).
    POSITION_NOT_BOOKED = "net_worth.position_not_booked"
    OWNERSHIP_MISSING = "net_worth.ownership_missing"
    PROPERTY_SHARES_MISSING = "net_worth.property_shares_missing"
    LOAN_NOT_VALUED = "net_worth.loan_not_valued"
    # A loan paid out before the handover with no payout booked yet (D265 (2)).
    LOAN_PAYOUT_MISSING = "net_worth.loan_payout_missing"
    # A wallet's read movements that wait for a person (16 Nov 2026).
    WALLET_WAITING = "net_worth.wallet_waiting"


class NetWorthTodo(Schema):
    """A gap in the figure as a plain to-do with the button or page that closes it
    (principle 7)."""

    code: NetWorthTodoCode
    text: str
    action: str  # the button's or link's words
    href: str


class NetWorthRun(Schema):
    """One stored month-end: the valuation_snapshot rows of one net_worth_run_id."""

    net_worth_run_id: UUID
    as_of: date
    captured_at: datetime
    rows: int = Field(ge=1)


class NetWorth(Schema):
    """GET /net-worth?on=: NetWorth(on) of D167 (4) on read, or a stored month-end (`run`
    set). `by_owner` covers what has shares: an account or a property without shares
    counts in its class and the total, in no owner, and is listed in `unassigned`.
    `property_net` is the properties' estimate less their loans (mockup 07's „Immobilie
    (netto)“); before a property's handover its part is the price paid so far less what its
    loans paid out (D265), and `property_estimate` holds that paid price. The class
    `liabilities` holds the loans and a credit card's balance."""

    on: date
    total: Money
    by_class: dict[NetWorthClass, Money]
    by_owner: dict[UUID, Money]
    unassigned: list[UUID] = Field(default_factory=list)  # account and property ids
    property_estimate: Money
    loan_balances: Money  # positive: what is owed on the properties' loans
    property_net: Money
    # Phase 10 §7.6 Konten (2): Σ of the account and position lines (a depot's cash, an
    # account's „unterwegs“ line and every position included) per Konten tab, by the account's
    # kind; a tab only when one of its accounts has a line, a line without a value counted
    # nowhere. Not split by owner shares, like the lines. The tabs add up to `total` less the
    # property's estimate plus the loans' balance.
    by_account_tab: dict[AccountTab, Money] = Field(default_factory=dict)
    lines: list[NetWorthLine] = Field(default_factory=list)
    todos: list[NetWorthTodo] = Field(default_factory=list)
    stale_positions: int = Field(default=0, ge=0)
    unvalued_positions: int = Field(default=0, ge=0)
    run: NetWorthRun | None = None


class NetWorthOverview(Schema):
    """GET /net-worth/overview: the Übersicht's card. `today` on read; `month_end` the latest
    stored month-end, None before the first; `month_end_due` the last month-end when no run
    holds it yet (the button „Monatsabschluss festhalten“); `month_end_now` the total read
    now for the stored month-end's day when it differs from the stored total, set only when
    that day is the last month-end (the one the button stores again)."""

    today: NetWorth
    month_end: NetWorth | None = None
    month_end_due: date | None = None
    month_end_now: Money | None = None


class SnapshotOutcome(StrEnum):
    WRITTEN = "written"  # the first run for the month-end
    RECAPTURED = "recaptured"  # a new run: the figures changed since the stored one
    UNCHANGED = "unchanged"  # the stored run already holds these figures; nothing written
    EMPTY = "empty"  # nothing to store (no account, position, property or loan with a value)


class SnapshotRequest(Schema):
    """POST /net-worth/snapshots, optional body (phase 9 D241): a past month-end to store, from
    31 Jan 2026 to the last one before today. Without it, the last month-end before today."""

    as_of: date | None = None


class SnapshotResult(Schema):
    """POST /net-worth/snapshots: what „Monatsabschluss festhalten“ did."""

    outcome: SnapshotOutcome
    as_of: date
    run: NetWorthRun | None = None
    unvalued_positions: int = Field(default=0, ge=0)


# --- GET /accounts/auto-rate (D172) ----------------------------------------------------------

# A share of the confirmed allocations, 0 to 1 inclusive, to four places, as a string like
# every figure (spec amendment 2): 1.0000 is a month booked wholly without a person.
AutoRatio = Annotated[
    Decimal, Field(ge=0, le=1, max_digits=5, decimal_places=4), _STRING_ONLY, _string_schema(4)
]


class AutoRateAccount(Schema):
    """A private bank account's confirmed allocations of the month, by the line's booking
    day (lines excluded as duplicates left out): `auto` those the app booked without a
    person (D172 amended 15 Nov 2026): the method is rule, matcher_transfer, document_match
    or llm and no person confirmed it (confirmed_by null); `by_routing` splits them by who
    confirmed (auto_confirm: no person, person: a member). `first_import`: of `confirmed`,
    those on lines of the account's first import (the history, D26: the model never
    auto-confirms there), which D172 leaves out of the criterion."""

    account_id: UUID
    label: str
    confirmed: int = Field(ge=0)
    auto: int = Field(ge=0)
    by_method: dict[str, int] = Field(default_factory=dict)
    by_routing: dict[str, int] = Field(default_factory=dict)
    ratio: AutoRatio | None = None  # auto ÷ confirmed, four places; None without any
    first_import: int = Field(default=0, ge=0)
    first_import_auto: int = Field(default=0, ge=0)


class AutoRate(Schema):
    """GET /accounts/auto-rate?month=YYYY-MM (D172): per private bank account and in total.
    `criterion_*` count only the allocations outside each account's first import; `met`
    compares their ratio with the target, None when there are none (the criterion is read on
    the second calendar month after the first import)."""

    month: str
    accounts: list[AutoRateAccount] = Field(default_factory=list)
    confirmed: int = Field(ge=0)
    auto: int = Field(ge=0)
    ratio: AutoRatio | None = None
    target: AutoRatio = Decimal("0.80")
    criterion_confirmed: int = Field(default=0, ge=0)
    criterion_auto: int = Field(default=0, ge=0)
    criterion_ratio: AutoRatio | None = None
    met: bool | None = None
