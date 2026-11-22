"""The household's net worth on a day (docs/phases/phase-6.md D167 (4), D176 (5), §9; fixture
tests/fixtures/phase6/net_worth.yaml). Pure: services/net_worth.py reads the stored rows and
the Anlagen overview's valuations and hands them here. Built in phase 6 step 6.

- `class_of(kind)`: the bar's class (mockup 07) by the account's kind, never by the asset's:
  a bank account is Bank, a depot (its funds and its cash) Depot, an exchange or a wallet
  (its coins and the EUR it holds) Krypto, a credit card's balance Verbindlichkeiten (D176
  (5)); the property's estimate is Immobilie and its loans Verbindlichkeiten.
- `bank_balance(anchor, anchor_on, lines, through)`: the anchor plus the lines (those not
  excluded as duplicates) booked after the anchor day up to and including `through`, the sum
  `rules.statements.window_sums` reconciles. The service caps `through` at the last day a
  reconciled import covers (D167 (4): the last reconciled balance on or before the day).
- `loan_balance(schedule, principal, disbursed_on, on)`: what is owed on `on` by the
  repayment schedule: nothing before the payout, the principal until the first payment is
  due, then the closing balance of the last payment due on or before `on` (phase 4's
  convention, rules/performance/synthesize.py, by day instead of by year).
- `split(amount, shares)`: one figure split to the owners to the cent, the parts summing to
  the figure: each owner gets the cents of amount × share rounded down, and the cents left
  over go one each to the largest remainders (ties by the owner's sort key). An exact split
  (the fixture's 1/2 of an even amount) is unchanged.
- `totals(parts, owners)`: Σ per class, per owner and in all; a part whose account has no
  shares counts in its class and the total but in no owner, and is reported as unassigned.
  A part without a value (not valued: no price, past its reconciled date) counts nowhere.
- `month_end_codes(as_of, today)`: which month-ends may be stored (phase 9 D241, amending
  D167 (5)): any closed month-end from 31 Jan 2026 on (the bank history starts on 1 Jan
  2026, design §10) up to the last one before `today`, not only the last one.
- `in_transit(paid_on, opened_on, through, receiver_history, linked=)`: phase 6 D292 (3),
  whether a booked transfer to an own account counts as „unterwegs zu <Konto>“ on a day.
"""

from collections.abc import Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction

from hausbuch.rules.errors import (
    NET_WORTH_MONTH_END_BEFORE_HISTORY,
    NET_WORTH_MONTH_END_NOT_CLOSED,
    NET_WORTH_NOT_MONTH_END,
)
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.household import NetWorthClass
from hausbuch.schemas.loan import ScheduleEntry

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
FIRST_MONTH_END = date(2026, 1, 31)  # the household's bank history starts on 1 Jan 2026

_CLASS_OF_KIND = {
    AccountKind.BANK_CURRENT: NetWorthClass.BANK,
    AccountKind.BANK_SAVINGS: NetWorthClass.BANK,
    AccountKind.DEPOT: NetWorthClass.DEPOT,
    AccountKind.EXCHANGE: NetWorthClass.CRYPTO,
    AccountKind.WALLET: NetWorthClass.CRYPTO,
    AccountKind.CREDIT_CARD: NetWorthClass.LIABILITIES,
}
if set(_CLASS_OF_KIND) != set(AccountKind):  # pragma: no cover
    raise RuntimeError("every account kind needs a net worth class")


def class_of(kind: AccountKind) -> NetWorthClass:
    return _CLASS_OF_KIND[kind]


def bank_balance(
    anchor: Decimal, anchor_on: date, lines: Iterable[tuple[date, Decimal]], through: date
) -> Decimal:
    """The balance at the end of `through` from (booking day, amount) of the account's lines
    not excluded as duplicates. The anchor is the balance at the end of its day, so the
    anchor day's own lines are in it already."""
    total = anchor
    for booking_date, amount in lines:
        if anchor_on < booking_date <= through:
            total += amount
    return total.quantize(CENT)


def loan_balance(
    entries: Sequence[ScheduleEntry], principal: Decimal, disbursed_on: date, on: date
) -> Decimal:
    if on < disbursed_on:
        return ZERO
    due = [e for e in entries if e.due_on <= on]
    if not due:
        return principal
    return max(due, key=lambda e: e.number).closing_balance


def split[K: Hashable](amount: Decimal, shares: Mapping[K, Fraction]) -> dict[K, Decimal]:
    """`shares` sum to 1 (account_ownership's rule, D157); amount has two places."""
    if not shares:
        return {}
    if sum(shares.values()) != 1:
        raise ValueError("the shares sum to 1")
    cents = int((amount / CENT).to_integral_value())
    exact = {owner: cents * share for owner, share in shares.items()}
    parts = {owner: value.numerator // value.denominator for owner, value in exact.items()}
    left = cents - sum(parts.values())
    by_remainder = sorted(exact, key=lambda o: (-(exact[o] - parts[o]), str(o)))
    for owner in by_remainder[:left]:
        parts[owner] += 1
    return {owner: (Decimal(c) * CENT).quantize(CENT) for owner, c in parts.items()}


@dataclass(frozen=True)
class Part[K: Hashable]:
    """One figure of the net worth: an account's balance, a position, the property's
    estimate or a loan's balance (negative). `value` None: not valued, counted nowhere."""

    net_worth_class: NetWorthClass
    value: Decimal | None
    shares: Mapping[K, Fraction] = field(default_factory=dict)
    ref: object = None  # the caller's key (the account, for the unassigned list)


@dataclass
class Totals[K: Hashable]:
    total: Decimal
    by_class: dict[NetWorthClass, Decimal]
    by_owner: dict[K, Decimal]
    unassigned: list[object]
    unvalued: int


def totals[K: Hashable](parts: Iterable[Part[K]], owners: Iterable[K]) -> Totals[K]:
    by_class = dict.fromkeys(NetWorthClass, ZERO)
    by_owner: dict[K, Decimal] = dict.fromkeys(owners, ZERO)
    unassigned: list[object] = []
    unvalued = 0
    for part in parts:
        if part.value is None:
            unvalued += 1
            continue
        by_class[part.net_worth_class] += part.value
        if not part.shares:
            if part.ref not in unassigned:
                unassigned.append(part.ref)
            continue
        for owner, amount in split(part.value, part.shares).items():
            by_owner[owner] = by_owner.get(owner, ZERO) + amount
    return Totals(
        total=sum(by_class.values(), ZERO),
        by_class=by_class,
        by_owner=by_owner,
        unassigned=unassigned,
        unvalued=unvalued,
    )


def in_transit(
    paid_on: date,
    opened_on: date,
    through: date,
    receiver_history: date | None,
    *,
    linked: bool,
) -> bool:
    """Phase 6 D292 (3), 22 Nov 2026: whether a booked transfer from an own account to another
    own account counts in the net worth as money „unterwegs zu <Konto>“ on a day.

    `paid_on` is the debit's booking day on the sending account, `opened_on` and `through`
    that account's anchor day and the last day its balance counts on that day (bank_balance),
    `receiver_history` the day the receiving account's imported history reaches (the history
    end of D162 amended (9); None when it has none), `linked` whether the debit is a leg of a
    current transfer link. It counts when the sending balance holds the debit (the anchor day
    before it, `through` on or after it) and the receiver's history ends before the payment's
    day, so neither account counts the money; then once, on the receiving account. A linked
    debit never counts: its counterpart is booked, so the receiving account holds it (a
    depot's or card's credit can be booked up to three banking days before the debit, D162).
    A receiver without any history never counts: nothing says the money reached an account
    the app follows."""
    if linked or receiver_history is None:
        return False
    if not opened_on < paid_on <= through:
        return False
    return receiver_history < paid_on


__all__ = [
    "Part",
    "Totals",
    "bank_balance",
    "class_of",
    "in_transit",
    "loan_balance",
    "split",
    "totals",
]


def month_end_codes(as_of: date, today: date) -> list[str]:
    """Why `as_of` cannot be stored as a month-end on `today` (both Berlin days), empty when
    it can: it must be a month's last day, not before FIRST_MONTH_END, and before today's
    month (a month still running has no month-end yet)."""
    if (as_of + timedelta(days=1)).day != 1:
        return [NET_WORTH_NOT_MONTH_END]
    if as_of < FIRST_MONTH_END:
        return [NET_WORTH_MONTH_END_BEFORE_HISTORY]
    if as_of >= today.replace(day=1):
        return [NET_WORTH_MONTH_END_NOT_CLOSED]
    return []
