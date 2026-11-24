"""Recurring payments detected by code (docs/phases/phase-10.md §7.9 (b), D296). Fixture:
tests/fixtures/phase10/recurring.yaml.

Pure: no I/O, nothing stored. Outside rules/health/ and rules/plan/, so
services.health.rule_version() does not change with it.

- **Input**: the household's confirmed private lines the household month counts as spending
  (D232), each with its payee and purpose; cash withdrawals (hh_cash) and money in are left
  out here.
- **One payee** as rules.matching.same_payee recognises it (D160 (4)): a PayPal merchant and
  its card line are one. The lines are grouped by that relation and its transitive closure;
  a group's key is the shortest rules.matching.payee_key among its lines, ties alphabetical.
- **Intervals**: monthly at least 3 payments with every gap 25–35 days, quarterly at least 3
  with gaps of 80–100, yearly at least 2 with gaps of 350–380, tried in that order. A payee
  paid more often (a supermarket) fails every gap and is no series.
- **The amount band**: a payment more than 25 % from the group's median amount is not part
  of the series (a doctor's 480 € bill among 40 € ones) — apart from a price change: the
  newest payments at one amount (monthly the last two, quarterly and yearly the last one),
  at least 0.50 € from the amount before them, stay in the series even outside the band.
- **A price change**: those newest payments; `change` = new − old, `since` = the month of
  the first payment at the new amount. yearly_total = the newest amount × 12, 4 or 1.
- **State**: missed = (today − last payment) in days ÷ the interval's upper gap, rounded
  down; 0 or 1 `active`, 2 `maybe_ended`, 3 or more left out of every list.
- **„Kein Abo“**: the newest answer per (key, interval) decides; `hidden` lists the series
  under `hidden`, `shown` (or none) under `series`; an answer matching no series is ignored,
  so a changed payee key shows the series again (a failure that shows, never one that hides).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from statistics import median
from uuid import UUID

from hausbuch.rules.budget.budget import is_spending
from hausbuch.rules.matching import payee, payee_key, same_payee
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.health import GroupPayment
from hausbuch.schemas.recurring import (
    RecurringInterval,
    RecurringPayments,
    RecurringSeries,
    RecurringState,
    SpendingMarkAnswer,
)

ZERO = Decimal("0.00")
BAND = Decimal("0.25")
MIN_CHANGE = Decimal("0.50")
YEARLY_HISTORY_DAYS = 350
LEFT_OUT_AFTER = 3  # missed intervals
MAYBE_ENDED_AT = 2


@dataclass(frozen=True)
class Rhythm:
    interval: RecurringInterval
    min_payments: int
    min_gap: int
    max_gap: int
    per_year: int
    change_run: int  # the newest payments at one amount that make a price change


RHYTHMS = (
    Rhythm(RecurringInterval.MONTHLY, 3, 25, 35, 12, 2),
    Rhythm(RecurringInterval.QUARTERLY, 3, 80, 100, 4, 1),
    Rhythm(RecurringInterval.YEARLY, 2, 350, 380, 1, 1),
)


@dataclass(frozen=True)
class SpendingLine:
    """One confirmed private allocation, its amount signed as booked (money out negative)
    and its code as the household month counts it (D234's recode applied)."""

    allocation_id: UUID
    transaction_id: UUID
    account_id: UUID
    account_label: str
    booked_on: date
    payee: str | None
    purpose: str | None
    category: AllocationCategory
    amount: Decimal

    def payment(self) -> GroupPayment:
        """The line for a panel, what was paid positive."""
        return GroupPayment(
            allocation_id=self.allocation_id,
            transaction_id=self.transaction_id,
            account_id=self.account_id,
            account_label=self.account_label,
            booked_on=self.booked_on,
            payee=self.payee,
            purpose=self.purpose or "",
            category=self.category,
            amount=-self.amount,
        )


@dataclass(frozen=True)
class SeriesMark:
    """A stored answer on a series, in the order stored."""

    key: str
    interval: RecurringInterval
    answer: SpendingMarkAnswer


@dataclass(frozen=True)
class Found:
    """A series before its marks: every state, the left-out ones included."""

    key: str
    label: str
    rhythm: Rhythm
    lines: tuple[SpendingLine, ...]  # oldest first
    missed: int
    change: Decimal | None
    since: str | None

    @property
    def last(self) -> SpendingLine:
        return self.lines[-1]

    @property
    def listed(self) -> bool:
        return self.missed < LEFT_OUT_AFTER


def counted(line: SpendingLine) -> bool:
    """Whether a line can be part of a series: money out the household month counts as
    spending, cash withdrawals left out."""
    return (
        line.amount < 0
        and line.category is not AllocationCategory.HH_CASH
        and is_spending(line.category)
    )


def payee_groups(lines: Iterable[SpendingLine]) -> list[list[SpendingLine]]:
    """The lines grouped by one payee (same_payee, closed transitively), each group oldest
    first. A line without a payee key is in no group."""
    by_words: dict[tuple[str, ...], list[SpendingLine]] = {}
    first: dict[tuple[str, ...], SpendingLine] = {}
    for line in lines:
        words = payee(line.payee, line.purpose).words
        if not words:
            continue
        by_words.setdefault(words, []).append(line)
        first.setdefault(words, line)
    keys = list(by_words)
    parent = list(range(len(keys)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(keys):
        la = first[a]
        for j in range(i + 1, len(keys)):
            lb = first[keys[j]]
            if root(i) != root(j) and same_payee(la.payee, lb.payee, la.purpose, lb.purpose):
                parent[root(j)] = root(i)
    groups: dict[int, list[SpendingLine]] = {}
    for i, words in enumerate(keys):
        groups.setdefault(root(i), []).extend(by_words[words])
    return [
        sorted(g, key=lambda li: (li.booked_on, str(li.allocation_id))) for g in groups.values()
    ]


def _key(lines: Sequence[SpendingLine]) -> str:
    keys = {payee_key(li.payee, li.purpose) for li in lines}
    return min((k for k in keys if k), key=lambda k: (len(k), k))


def _label(line: SpendingLine) -> str:
    """The name the card shows: a payment service's merchant, else the payee as printed."""
    found = payee(line.payee, line.purpose)
    return found.merchant or (line.payee or "").strip() or " ".join(found.words)


def _trailing_run(lines: Sequence[SpendingLine]) -> int:
    newest = lines[-1].amount
    run = 0
    for line in reversed(lines):
        if line.amount != newest:
            break
        run += 1
    return run


def _price_change(lines: Sequence[SpendingLine], rhythm: Rhythm) -> tuple[Decimal, str] | None:
    """(new − old, the month of the first payment at the new amount) when the newest
    `change_run` payments or more are at one amount at least MIN_CHANGE from the one before."""
    run = _trailing_run(lines)
    if run < rhythm.change_run or run >= len(lines):
        return None
    new, old = -lines[-1].amount, -lines[-1 - run].amount
    if abs(new - old) < MIN_CHANGE:
        return None
    first = lines[-run].booked_on
    return new - old, f"{first.year:04d}-{first.month:02d}"


def _in_rhythm(lines: Sequence[SpendingLine], rhythm: Rhythm) -> bool:
    if len(lines) < rhythm.min_payments:
        return False
    return all(
        rhythm.min_gap <= (b.booked_on - a.booked_on).days <= rhythm.max_gap
        for a, b in zip(lines, lines[1:], strict=False)
    )


def _series(group: Sequence[SpendingLine], rhythm: Rhythm) -> list[SpendingLine] | None:
    """The group's payments that form a series at `rhythm`, else None: those within the band
    of the group's median amount, plus the newest run of a price change."""
    middle = median([-li.amount for li in group])
    low, high = middle * (1 - BAND), middle * (1 + BAND)
    run = _trailing_run(group)
    keep_run = run >= rhythm.change_run and run < len(group)
    if keep_run:
        new, old = -group[-1].amount, -group[-1 - run].amount
        keep_run = abs(new - old) >= MIN_CHANGE
    tail = set(range(len(group) - run, len(group))) if keep_run else set()
    kept = [li for i, li in enumerate(group) if low <= -li.amount <= high or i in tail]
    return kept if _in_rhythm(kept, rhythm) else None


def find(lines: Iterable[SpendingLine], today: date) -> list[Found]:
    """Every series in the lines, the left-out ones included (one-off candidates are never
    a series' payment, whatever its state)."""
    out: list[Found] = []
    for group in payee_groups(li for li in lines if counted(li)):
        for rhythm in RHYTHMS:
            kept = _series(group, rhythm)
            if kept is None:
                continue
            change = _price_change(kept, rhythm)
            out.append(
                Found(
                    key=_key(kept),
                    label=_label(kept[-1]),
                    rhythm=rhythm,
                    lines=tuple(kept),
                    missed=(today - kept[-1].booked_on).days // rhythm.max_gap,
                    change=None if change is None else change[0],
                    since=None if change is None else change[1],
                )
            )
            break
    return out


def newest_series_marks(
    marks: Iterable[SeriesMark],
) -> dict[tuple[str, RecurringInterval], SpendingMarkAnswer]:
    out: dict[tuple[str, RecurringInterval], SpendingMarkAnswer] = {}
    for mark in marks:
        out[(mark.key, mark.interval)] = mark.answer
    return out


def _shown(found: Found, *, with_lines: bool) -> RecurringSeries:
    last = -found.last.amount
    return RecurringSeries(
        key=found.key,
        label=found.label,
        interval=found.rhythm.interval,
        state=RecurringState.ACTIVE
        if found.missed < MAYBE_ENDED_AT
        else RecurringState.MAYBE_ENDED,
        last_amount=last,
        last_on=found.last.booked_on,
        yearly_total=last * found.rhythm.per_year,
        change=found.change,
        since=found.since,
        payments=len(found.lines),
        sources=[li.allocation_id for li in found.lines],
        lines=[li.payment() for li in found.lines] if with_lines else [],
    )


def _order(series: RecurringSeries) -> tuple[object, ...]:
    return (series.change is None, -series.yearly_total, series.key, series.interval.value)


def recurring(
    lines: Iterable[SpendingLine],
    marks: Iterable[SeriesMark] = (),
    *,
    today: date,
    history_start: date | None,
    with_lines: bool = True,
) -> RecurringPayments:
    """§7.9 (b): the series shown and hidden as of `today` (see the module)."""
    answers = newest_series_marks(marks)
    shown: list[RecurringSeries] = []
    hidden: list[RecurringSeries] = []
    for found in find(lines, today):
        if not found.listed:
            continue
        row = _shown(found, with_lines=with_lines)
        answer = answers.get((found.key, found.rhythm.interval))
        (hidden if answer is SpendingMarkAnswer.HIDDEN else shown).append(row)
    shown.sort(key=_order)
    hidden.sort(key=_order)
    active = [s for s in shown if s.state is RecurringState.ACTIVE]
    return RecurringPayments(
        as_of=today,
        history_start=history_start,
        yearly_detectable=(
            history_start is not None and (today - history_start).days >= YEARLY_HISTORY_DAYS
        ),
        count=len(active),
        yearly_total=sum((s.yearly_total for s in active), ZERO),
        series=shown,
        hidden=hidden,
    )
