"""A portfolio's performance since its start (docs/phases/phase-10.md §7.6 Anlagen, items 2
and 3, build decision (b)). Pure, Decimal; services/portfolio_performance.py picks the flows.

A **flow** is one payment into or out of a portfolio (a depot, an exchange, a wallet), dated,
in euros, signed from the portfolio's side: + paid in, − taken out. Its `amount` is None when
the euros are unknown (units moved on a day with no price): the portfolio's figures are then
not computed (None with the reason `unpriced`), never computed as if it were 0. Likewise a
portfolio holding a position today's value leaves out (no price, or units a depot report
contradicts) is `unvalued`: its value would read too low while the purchase stays paid in, so a
gain would read as a loss; its figures are None, and so are a set's that holds it.

- gain since the start = today's value + everything taken out − everything paid in;
- percent = gain ÷ everything paid in (None when nothing was paid in);
- the yearly return is money-weighted: `irr.xirr` over every flow from the investor's side
  (paid in negative, taken out positive) and today's value as a last inflow on `today`. None
  for less than MIN_DAYS of history (the start to today): a rate over weeks misleads (item 3),
  so only the euros and the percent are shown.

**Combined totals** (ETF, Krypto, both): a move between two own portfolios is out of one and in
to the other, so the total counts only money from or to outside the set. A flow is internal to
a set when its own portfolio and the portfolio of its counterpart flow (`pair`, the key of the
other leg's flow) are both in the set; a counterpart that is no flow (a bank line, a leg not
confirmed) leaves it external.
"""

from collections.abc import Collection, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from .irr import xirr

ZERO = Decimal("0.00")
SIX = Decimal("0.000001")
MIN_DAYS = 365


class NoRate(StrEnum):
    """Why a portfolio has no yearly return."""

    SHORT = "short_history"  # less than MIN_DAYS since the start
    UNPRICED = "unpriced"  # a flow's euros are unknown
    UNVALUED = "unvalued"  # a position held today has no value, so today's value is short
    NO_FLOWS = "no_flows"  # nothing was paid in or taken out
    NO_RATE = "no_rate"  # the flows have no rate (xirr found none)


@dataclass(frozen=True, slots=True)
class Flow:
    portfolio: Hashable  # the account
    on: date
    amount: Decimal | None  # + paid in, − taken out; None: no price that day
    key: Hashable  # this leg (a transaction, a movement, an opening balance)
    pair: Hashable | None = None  # the other leg's key, when a link or a pair names one


@dataclass(frozen=True, slots=True)
class Performance:
    start: date | None
    paid_in: Decimal | None
    taken_out: Decimal | None
    value: Decimal
    gain: Decimal | None
    gain_ratio: Decimal | None  # gain ÷ paid in, six places
    yearly_return: Decimal | None  # xirr, six places
    no_rate: NoRate | None
    unpriced: int  # flows whose euros are unknown
    unvalued: int = 0  # positions held today without a value


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    return (numerator / denominator).quantize(SIX, rounding=ROUND_HALF_UP) + 0


def performance(
    flows: Sequence[Flow], value: Decimal, today: date, unvalued: int = 0
) -> Performance:
    """One portfolio's (or one set's) figures from its flows and today's value; `unvalued`
    counts the positions held today that `value` leaves out for want of a value."""
    start = min((f.on for f in flows), default=None)
    unpriced = sum(1 for f in flows if f.amount is None)
    if unpriced or unvalued:
        return Performance(
            start=start,
            paid_in=None,
            taken_out=None,
            value=value,
            gain=None,
            gain_ratio=None,
            yearly_return=None,
            no_rate=NoRate.UNVALUED if unvalued else NoRate.UNPRICED,
            unpriced=unpriced,
            unvalued=unvalued,
        )
    amounts = [(f.on, f.amount) for f in flows if f.amount is not None and f.amount != 0]
    paid_in = sum((a for _, a in amounts if a > 0), ZERO)
    taken_out = -sum((a for _, a in amounts if a < 0), ZERO)
    gain = value + taken_out - paid_in
    ratio = _ratio(gain, paid_in) if paid_in > 0 else None
    if not amounts:
        rate, reason = None, NoRate.NO_FLOWS
    elif start is None or (today - start).days < MIN_DAYS:
        rate, reason = None, NoRate.SHORT
    else:
        dated = [(on, -a) for on, a in amounts]
        if value != 0:
            dated.append((today, value))
        rate = xirr(dated)
        reason = None if rate is not None else NoRate.NO_RATE
    return Performance(
        start=start,
        paid_in=paid_in,
        taken_out=taken_out,
        value=value,
        gain=gain,
        gain_ratio=ratio,
        yearly_return=rate,
        no_rate=reason,
        unpriced=0,
    )


def external(flows: Iterable[Flow], members: Collection[Hashable]) -> list[Flow]:
    """The flows of the portfolios in `members` that come from or go to outside them: a flow
    whose counterpart flow belongs to another member is a move inside the set and is left out
    (both its legs are, since each names the other)."""
    flows = list(flows)
    owner = {f.key: f.portfolio for f in flows}
    return [
        f
        for f in flows
        if f.portfolio in members and not (f.pair is not None and owner.get(f.pair) in members)
    ]


def combined(
    flows: Iterable[Flow],
    values: dict[Hashable, Decimal],
    members: Collection[Hashable],
    today: date,
    unvalued: Mapping[Hashable, int] | None = None,
) -> Performance:
    """A set's figures: its external flows and the sum of its members' values; a member with
    a position without a value (`unvalued`, per portfolio) leaves the set's figures unknown."""
    value = sum((values.get(m, ZERO) for m in members), ZERO)
    missing = sum((unvalued or {}).get(m, 0) for m in members)
    return performance(external(flows, members), value, today, missing)


__all__ = ["MIN_DAYS", "Flow", "NoRate", "Performance", "combined", "external", "performance"]
