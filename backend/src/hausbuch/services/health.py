"""The household's key figures: the reads behind them (docs/phases/phase-9.md §4, D230–D234;
phase 9 step 1). The arithmetic is rules/health/; this module reads the stored rows and
hands the rules plain facts.

- `rental_months(conn, first, last)`: §4.1's `RentalMonth` per property and calendar month:
  the confirmed, non-zero allocations of the accounts of purpose `rental` booked in the
  month (D230: the booking date, never §11), summed by phase 4's own category sets
  (rules.performance.year_figures, imported, never copied): the income blocks signed as
  `_signed_in` sums them (a refund to a tenant lowers its block), the cost blocks and
  `invested` as magnitudes as `_signed_out` does, `loan_disbursement` signed as booked (a
  payout is positive). A row is the property's it names; a row with no property is the
  property of its account (the one property the account's current rows name, else the
  workspace's only property, as the Anlage V draft reads such rows); one no property can be
  given (an account carrying rows of several properties) is not guessed but listed in
  `unattributed`, so the caller can say so. Every property owned in the month gets a row,
  zero when nothing was booked.
- `closed_month_facts(conn, months, today)`: what `rules.health.window.closed` reads for
  each month (D231, §4.4, Q-O133) as `MonthFacts`, failing closed: see `CoverageAccount`.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Connection, and_, exists, or_, select

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import bank_sync as sync_table
from hausbuch.db.tables import property_ as property_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.rules.health.month import rental_month
from hausbuch.rules.performance.year_figures import (
    INTEREST,
    INVESTED,
    OTHER_INCOME,
    PRINCIPAL,
    RENT,
    RUNNING,
    UMLAGEN_IN,
    UMLAGEN_OUT,
)
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.bank_sync import SyncStatus
from hausbuch.schemas.enums import (
    BANK_ACCOUNT_KINDS,
    AccountKind,
    AccountPurpose,
    AllocationStatus,
    ImportStatus,
)
from hausbuch.schemas.health import AccountCoverage, MonthFacts, MonthLine, RentalMonth, month_of

from .documents import LOCAL_TIME_ZONE
from .household import month_bounds, months_between
from .reminders import idle_cards
from .transfers import _first_days as history_starts
from .transfers import open_transfers

ZERO = Decimal("0.00")
C = AllocationCategory

# --- The rental block (§4.1) -------------------------------------------------------------

# Every category a RentalMonth block sums (rules.health.month.rental_month decides which).
_COUNTED = (
    RENT | OTHER_INCOME | UMLAGEN_IN | UMLAGEN_OUT | RUNNING | INTEREST | PRINCIPAL | INVESTED
) | {C.LOAN_DISBURSEMENT}


@dataclass(frozen=True)
class RentalMonths:
    """`months`: per calendar month in the range, one RentalMonth per property, by label.
    `unattributed`: per month, the allocation ids of counted rows no property could be
    given; they are in no RentalMonth."""

    months: dict[str, list[RentalMonth]]
    unattributed: dict[str, list[UUID]] = field(default_factory=dict)


def _account_property(conn: Connection, properties: Sequence[UUID]) -> dict[UUID, UUID]:
    """The property a rental account's row without a property belongs to: the one property
    the account's current rows name, else the workspace's only property."""
    a, t, acc = allocation_table.c, transaction_table.c, account_table.c
    named: dict[UUID, set[UUID]] = defaultdict(set)
    for account_id, property_id in conn.execute(
        select(t.account_id, a.property_id)
        .join(transaction_table, t.id == a.transaction_id)
        .join(account_table, acc.id == t.account_id)
        .where(
            acc.purpose == AccountPurpose.RENTAL.value,
            a.status != AllocationStatus.SUPERSEDED.value,
            a.property_id.is_not(None),
        )
        .distinct()
    ):
        named[account_id].add(property_id)
    rental = conn.execute(select(acc.id).where(acc.purpose == AccountPurpose.RENTAL.value))
    out = {}
    for (account_id,) in rental:
        names = named.get(account_id, set())
        if len(names) == 1:
            out[account_id] = next(iter(names))
        elif not names and len(properties) == 1:
            out[account_id] = properties[0]
    return out


def rental_months(conn: Connection, first: str, last: str) -> RentalMonths:
    """§4.1 for every calendar month from `first` to `last` ("YYYY-MM", inclusive)."""
    months = months_between(first, last)
    if not months:
        return RentalMonths(months={})
    start, end = month_bounds(months[0])[0], month_bounds(months[-1])[1]
    p = property_table.c
    properties = conn.execute(
        select(p.id, p.label, p.purchase_date, p.ownership_ended_on).order_by(p.label, p.id)
    ).all()
    by_account = _account_property(conn, [r.id for r in properties])
    a, t, acc = allocation_table.c, transaction_table.c, account_table.c
    rows = conn.execute(
        select(a.id, a.amount, a.category, a.property_id, t.account_id, t.booking_date)
        .join(transaction_table, t.id == a.transaction_id)
        .join(account_table, acc.id == t.account_id)
        .where(
            acc.purpose == AccountPurpose.RENTAL.value,
            a.status == AllocationStatus.CONFIRMED.value,
            a.amount != 0,
            a.category.in_([c.value for c in _COUNTED]),
            t.excluded_as_duplicate_of.is_(None),
            t.booking_date >= start,
            t.booking_date <= end,
        )
        .order_by(t.booking_date, t.id, a.id)
    ).all()
    lines: dict[tuple[str, UUID], list[MonthLine]] = defaultdict(list)
    unattributed: dict[str, list[UUID]] = defaultdict(list)
    for r in rows:
        month = month_of(r.booking_date)
        property_id = r.property_id or by_account.get(r.account_id)
        if property_id is None:
            unattributed[month].append(r.id)
            continue
        lines[(month, property_id)].append(
            MonthLine(allocation_id=r.id, category=C(r.category), amount=r.amount)
        )
    out: dict[str, list[RentalMonth]] = {}
    for month in months:
        m_start, m_end = month_bounds(month)
        out[month] = [
            rental_month(month, prop.id, lines.get((month, prop.id), []))
            for prop in properties
            if (month, prop.id) in lines
            or (
                prop.purchase_date is not None
                and prop.purchase_date <= m_end
                and (prop.ownership_ended_on is None or prop.ownership_ended_on >= m_start)
            )
        ]
    return RentalMonths(months=out, unattributed=dict(unattributed))


# --- Closed months (D231, §4.4, Q-O133) --------------------------------------------------

COVERED_KINDS = BANK_ACCOUNT_KINDS | {AccountKind.CREDIT_CARD}


@dataclass(frozen=True)
class CoverageAccount:
    """One account D231 (a) checks: every bank account and credit card of purpose private or
    rental (a depot's, an exchange's or a wallet's flows are not the household month's).

    - `first_day`: the first booking day of its lines of reconciled imports (None: no history
      at all, so no month is covered).
    - `covered_to`: the last day its history covers in full, None when nothing does: the later
      of (a) each reconciled import's last booking day, or the day before it when the import
      was made on or before that day (an export made during a day may miss that day's later
      bookings), and (b) each successful FinTS run's requested_to less one day (a run fetches
      through the moment it ran, so only a fetch after a day covers it, D231 (a)). A reconciled
      import is a gap-free chain from the anchor (a missing statement leaves the next one
      unreconciled, phase 6 D176), so no month inside it is a hole. A quiet account with no
      line after the month's last days stays uncovered until its next line or fetch.
    - `idle`: a credit card idle by phase 5 D122 amended (6) (services.reminders.idle_cards):
      covered for any month.
    - `own_opening`: the account's opening balance is its earliest reconciled statement's own
      opening, dated the day before that statement's first booking (§4.4: the account did not
      exist in the app's history before); False when the statement states no opening (a CSV,
      a sync) or the anchor was typed."""

    account_id: UUID
    label: str
    kind: AccountKind
    purpose: AccountPurpose
    first_day: date | None
    covered_to: date | None
    idle: bool
    own_opening: bool


def _local_day(moment) -> date:  # noqa: ANN001
    return moment.astimezone(LOCAL_TIME_ZONE).date()


def _covered_to(conn: Connection, ids: Sequence[UUID]) -> dict[UUID, date]:
    s, b = import_table.c, sync_table.c
    found: dict[UUID, date] = {}

    def keep(account_id: UUID, day: date) -> None:
        found[account_id] = max(found.get(account_id, day), day)

    for account_id, last, created_at in conn.execute(
        select(s.account_id, s.last_booking_on, s.created_at).where(
            s.account_id.in_(ids),
            s.status == ImportStatus.RECONCILED.value,
            s.last_booking_on.is_not(None),
        )
    ):
        keep(account_id, last if _local_day(created_at) > last else last - timedelta(days=1))
    # The runs transfers._last_days counts: one that ended nothing_new, or done with a
    # reconciled import.
    for account_id, requested_to in conn.execute(
        select(b.account_id, b.requested_to)
        .outerjoin(import_table, import_table.c.id == b.import_id)
        .where(
            b.account_id.in_(ids),
            b.requested_to.is_not(None),
            or_(
                b.status == SyncStatus.NOTHING_NEW.value,
                and_(b.status == SyncStatus.DONE.value, s.status == ImportStatus.RECONCILED.value),
            ),
        )
    ):
        keep(account_id, requested_to - timedelta(days=1))
    return found


def _own_opening(conn: Connection, accounts: Sequence) -> set[UUID]:  # noqa: ANN001
    s = import_table.c
    earliest = {
        r.account_id: r
        for r in conn.execute(
            select(s.account_id, s.first_booking_on, s.declared_opening)
            .where(
                s.account_id.in_([r.id for r in accounts]),
                s.status == ImportStatus.RECONCILED.value,
                s.first_booking_on.is_not(None),
            )
            .distinct(s.account_id)
            .order_by(s.account_id, s.first_booking_on, s.created_at, s.id)
        )
    }
    out = set()
    for r in accounts:
        first = earliest.get(r.id)
        if (
            first is not None
            and first.declared_opening is not None
            and r.opening_balance_on == first.first_booking_on - timedelta(days=1)
            and r.opening_balance == first.declared_opening
        ):
            out.add(r.id)
    return out


def coverage_accounts(conn: Connection) -> tuple[CoverageAccount, ...]:
    """Every account D231 (a) checks, by label, with its coverage (see CoverageAccount)."""
    acc = account_table.c
    accounts = conn.execute(
        select(
            acc.id, acc.label, acc.kind, acc.purpose, acc.opening_balance, acc.opening_balance_on
        )
        .where(
            acc.kind.in_([k.value for k in COVERED_KINDS]),
            acc.purpose.in_([AccountPurpose.PRIVATE.value, AccountPurpose.RENTAL.value]),
        )
        .order_by(acc.label, acc.id)
    ).all()
    if not accounts:
        return ()
    ids = [r.id for r in accounts]
    firsts = history_starts(conn)
    covered = _covered_to(conn, ids)
    idle = idle_cards(conn)
    own = _own_opening(conn, accounts)
    return tuple(
        CoverageAccount(
            account_id=r.id,
            label=r.label,
            kind=AccountKind(r.kind),
            purpose=AccountPurpose(r.purpose),
            first_day=firsts.get(r.id),
            covered_to=covered.get(r.id),
            idle=r.id in idle,
            own_opening=r.id in own,
        )
        for r in accounts
    )


def _lines_waiting(conn: Connection, ids: Sequence[UUID], start: date, end: date) -> dict[str, int]:
    a, t = allocation_table.c, transaction_table.c
    confirmed = exists().where(
        a.transaction_id == t.id, a.status == AllocationStatus.CONFIRMED.value
    )
    counts: dict[str, int] = defaultdict(int)
    for (day,) in conn.execute(
        select(t.booking_date).where(
            t.account_id.in_(ids),
            t.excluded_as_duplicate_of.is_(None),
            t.booking_date >= start,
            t.booking_date <= end,
            ~confirmed,
        )
    ):
        counts[month_of(day)] += 1
    return counts


def _coverage(account: CoverageAccount) -> AccountCoverage:
    return AccountCoverage(
        account_id=account.account_id,
        label=account.label,
        credit_card=account.kind is AccountKind.CREDIT_CARD,
        first_day=account.first_day,
        covered_to=account.covered_to,
        idle=account.idle,
        own_opening=account.own_opening,
    )


def closed_month_facts(
    conn: Connection, months: Sequence[str], today: date
) -> dict[str, MonthFacts]:
    """D231's facts for each month ("YYYY-MM"), read once for all of them (GET
    /transfers/open is computed once and bucketed by the leg's booking month), as
    rules.health.window.closed reads them.

    `lines_waiting`: the month's lines on the checked accounts that no confirmed allocation
    explains (whatever their import's state, 0 € lines, pending lines and the later line of a
    possible duplicate included: wider than Zu prüfen's list, which shows an unreconciled
    import or a duplicate as one card). `transfers_open`: the euro transfer legs booked in the
    month that GET /transfers/open lists (a leg inside its window that has a counterpart, or
    past it without one); a coin leg (no euro amount) moves no figure of the household month
    and is not counted."""
    if not months:
        return {}
    accounts = [_coverage(a) for a in coverage_accounts(conn)]
    start, end = month_bounds(min(months))[0], month_bounds(max(months))[1]
    waiting = _lines_waiting(conn, [r.account_id for r in accounts], start, end)
    legs: dict[str, int] = defaultdict(int)
    for item in open_transfers(conn, today):
        if item.leg.amount is not None and start <= item.leg.booked_on <= end:
            legs[month_of(item.leg.booked_on)] += 1
    return {
        month: MonthFacts(
            month=month,
            accounts=accounts,
            lines_waiting=waiting.get(month, 0),
            transfers_open=legs.get(month, 0),
        )
        for month in months
    }
