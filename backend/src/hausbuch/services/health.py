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
- `read_months`, `health`, `health_months`, `net_worth_series` (§13): the household month
  of rules.health.month.household_month over the Haushalt screen's own private month and
  the rental blocks; the window of the closed months; the key figures over it. GET /health
  takes the reserve and the split from today's net worth and the debt figures from the
  window's newest month-end (its stored run, else that day on read); the free cash is the
  window's mean of each month's F (D244; rules/plan/surplus is step 2's).
- `store_snapshot`, `store_missing_snapshots` (D242, §5.5): a closed month's figures as of
  that month, the reserve, debt and split from the newest run of its month-end, stored with
  `rule_version` (the SHA-256 of rules/health/ and rules/plan/) and its
  `health.snapshot_stored` event, one write at a time per workspace (an advisory lock).
- `health_check` (D259 amended, §16.1): each stored month recomputed from the stored rows
  with the run and the reserve levers it names, figure by figure within the tolerances.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from sqlalchemy import Connection, Row, and_, exists, func, insert, or_, select

import hausbuch.rules as rules_package
from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import bank_sync as sync_table
from hausbuch.db.tables import health_snapshot as health_snapshot_table
from hausbuch.db.tables import household_scenario as household_scenario_table
from hausbuch.db.tables import property_ as property_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.rules.errors import (
    HEALTH_MONTH_END_INCOMPLETE,
    HEALTH_MONTH_END_MISSING,
    HEALTH_MONTH_NOT_CLOSED,
    HEALTH_RANGE_INVALID,
)
from hausbuch.rules.health.bands import bands_todos, lights, proposals, with_texts
from hausbuch.rules.health.figures import debt, reserve, savings_rate, series, split
from hausbuch.rules.health.month import household_month, rental_month
from hausbuch.rules.health.window import closed, window, window_todos
from hausbuch.rules.net_worth import FIRST_MONTH_END
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
    EntityType,
    ImportStatus,
)
from hausbuch.schemas.events import EventType, HealthSnapshotStored
from hausbuch.schemas.health import (
    AccountCoverage,
    ClosedMonth,
    Debt,
    EstimateSource,
    ExactShare,
    Health,
    HealthCheckInput,
    HealthCheckResult,
    HealthCheckRow,
    HealthMonthView,
    HealthSnapshot,
    HealthSnapshotFigures,
    HealthSnapshotOutcome,
    HealthSnapshotResult,
    HealthTodo,
    HealthTodoCode,
    HouseholdMonth,
    MonthFacts,
    MonthLine,
    NetWorthPoint,
    RentalMonth,
    Reserve,
    ReserveBalance,
    ReserveOrigin,
    RunTotals,
    SavingsRate,
    Window,
    month_of,
)
from hausbuch.schemas.net_worth import (
    NetWorth,
    NetWorthLine,
    NetWorthLineKind,
    NetWorthRun,
    NetWorthTodo,
    NetWorthTodoCode,
    SnapshotOutcome,
)
from hausbuch.schemas.plan import HouseholdLevers, ReserveAccounts

from .allocations import user_id
from .context import CommandContext
from .documents import LOCAL_TIME_ZONE
from .errors import NotFoundError, RuleViolationError
from .events import append_event
from .household import month_bounds, months_between, private_months
from .net_worth import (
    capture_month_end,
    last_month_end,
    latest_run,
    list_runs,
    month_end_figure,
    net_worth,
    shares_on,
    stored_run,
)
from .reminders import idle_cards
from .scenarios import estimate_on_day
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
    """One account D231 (a) checks for coverage: every bank account and credit card of purpose
    private or rental. A depot's cash lines do count in the household month, and a waiting one
    keeps its month open (`_lines_waiting`); its statements are not required to reach the
    month's last day, coverage stays on bank accounts and cards (review finding 4, decided by
    the coordinator). An exchange's or a wallet's movements are not bank lines and do not count.

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


def _lines_waiting(conn: Connection, start: date, end: date) -> dict[str, int]:
    """Per month the lines no confirmed allocation explains on every account whose lines feed
    the household month (review finding 4): each account of purpose private or rental,
    whatever its kind, so a depot's cash line waiting in Zu prüfen keeps its month open too."""
    a, t, acc = allocation_table.c, transaction_table.c, account_table.c
    confirmed = exists().where(
        a.transaction_id == t.id, a.status == AllocationStatus.CONFIRMED.value
    )
    feeding = select(acc.id).where(
        acc.purpose.in_([AccountPurpose.PRIVATE.value, AccountPurpose.RENTAL.value])
    )
    counts: dict[str, int] = defaultdict(int)
    for (day,) in conn.execute(
        select(t.booking_date).where(
            t.account_id.in_(feeding.scalar_subquery()),
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

    `lines_waiting`: the month's lines on every account feeding the household month (each
    private or rental account, a depot's cash account included; the coverage check stays on
    the bank accounts and cards) that no confirmed allocation
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
    waiting = _lines_waiting(conn, start, end)
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


# --- The household month and the window (§4.2, D231) ---------------------------------------

FIRST_MONTH = month_of(FIRST_MONTH_END)  # the household's bank history starts in Jan 2026
MAX_MONTHS = 24  # GET /health/months reads at most two years at once
CENT = Decimal("0.01")


@dataclass(frozen=True)
class MonthRead:
    """One calendar month on read: the household month (with sources), its rental blocks
    (the debt figures' interest), and whether it is closed (D231)."""

    household: HouseholdMonth
    rental: list[RentalMonth]
    closed: ClosedMonth


def read_months(conn: Connection, months: Sequence[str], today: date) -> dict[str, MonthRead]:
    """`months` (consecutive, oldest first) on read: rules.health.month.household_month over
    the private accounts' month (services.household.private_months, the Haushalt screen's
    own read) and the rental blocks (rental_months), and rules.health.window.closed over
    the month's facts."""
    if not months:
        return {}
    first, last = months[0], months[-1]
    private = private_months(conn, first, last)
    rental = rental_months(conn, first, last)
    facts = closed_month_facts(conn, months, today)
    out = {}
    for month in months:
        p = private[month]
        blocks = rental.months.get(month, [])
        out[month] = MonthRead(
            household=household_month(
                p.cashflow, blocks, lines=p.lines, link_sources=p.link_sources
            ),
            rental=blocks,
            closed=closed(facts[month]),
        )
    return out


def ended_months(today: date) -> list[str]:
    """Every month from January 2026 to the last one that has ended before `today`."""
    last = month_of(last_month_end(today))
    return months_between(FIRST_MONTH, last) if last >= FIRST_MONTH else []


def _window(reads: dict[str, MonthRead], up_to: str | None = None) -> Window:
    return window({m: r.closed.closed for m, r in reads.items() if up_to is None or m <= up_to})


# --- The levers (D248: the default household scenario in force) ----------------------------


def household_levers(conn: Connection, scenario_id: UUID | None = None) -> HouseholdLevers:
    """The levers of `scenario_id` (any row of the table, a withdrawn one included, so an
    old scenario can be looked at), else of the default household scenario in force (not
    withdrawn, not superseded; the newest when two are), else the defaults. The rows are
    written by services/household_scenarios.py (POST /household-scenarios, step 2), which
    keeps at most one default in force."""
    hs = household_scenario_table.c
    if scenario_id is not None:
        row = conn.execute(select(hs.levers).where(hs.id == scenario_id)).one_or_none()
        if row is None:
            raise NotFoundError(EntityType.HOUSEHOLD_SCENARIO, scenario_id)
        return HouseholdLevers.model_validate(row.levers)
    later = household_scenario_table.alias("later")
    row = conn.execute(
        select(hs.levers)
        .where(
            hs.is_default.is_(True),
            hs.withdrawn.is_(False),
            ~exists().where(later.c.supersedes_id == hs.id),
        )
        .order_by(hs.created_at.desc(), hs.id)
        .limit(1)
    ).one_or_none()
    return HouseholdLevers() if row is None else HouseholdLevers.model_validate(row.levers)


# --- The figures of a window (§5) -----------------------------------------------------------


def _reserve_balances(conn: Connection, figure: NetWorth) -> list[ReserveBalance]:
    """Every valued account balance of a net worth (a position is no balance), with its
    purpose and its owner shares on the figure's day."""
    lines = [
        line
        for line in figure.lines
        if line.kind is NetWorthLineKind.ACCOUNT
        and line.account_id is not None
        and line.account_kind is not None
        and line.value_eur is not None
    ]
    ids = [line.account_id for line in lines]
    acc = account_table.c
    query = select(acc.id, acc.purpose).where(acc.id.in_(ids))
    purposes = {r.id: r.purpose for r in conn.execute(query)}
    shares = shares_on(conn, ids, figure.on)
    return [
        ReserveBalance(
            account_id=line.account_id,
            label=line.account_label or line.label,
            kind=line.account_kind,
            rental=purposes.get(line.account_id) == AccountPurpose.RENTAL.value,
            balance=line.value_eur,
            as_of=line.as_of or figure.on,
            owners={
                owner: ExactShare(numerator=share.numerator, denominator=share.denominator)
                for owner, share in shares.get(line.account_id, {}).items()
            },
        )
        for line in lines
    ]


def estimate_sources(conn: Connection, figure: NetWorth) -> dict[UUID, EstimateSource]:
    """Per property with an estimate line: whether its estimate on the figure's day is the
    default scenario's „Wert heute“ or the purchase price (D239 (1)), decided as the net worth
    decided it (services.scenarios.estimate_on_day)."""
    ids = {
        line.property_id
        for line in figure.lines
        if line.kind is NetWorthLineKind.PROPERTY and line.property_id is not None
    }
    if not ids:
        return {}
    p = property_table.c
    prices = conn.execute(select(p.id, p.purchase_price).where(p.id.in_(ids))).all()
    return {
        r.id: estimate_on_day(conn, r.id, r.purchase_price, figure.on)[0].source for r in prices
    }


@dataclass(frozen=True)
class KeyFigures:
    savings_rate: SavingsRate | None
    reserve: Reserve | None
    debt: Debt | None
    free_cash: Decimal | None


def _free_cash(months: Sequence[HouseholdMonth]) -> Decimal:
    """D244: mean private income − mean regular spending + mean C over the window, to the
    cent half-up: the mean of the months' F (F is linear in the three)."""
    total = sum((m.free_cash for m in months), Decimal("0.00"))
    return (total / len(months)).quantize(CENT, rounding=ROUND_HALF_UP)


def key_figures(
    conn: Connection,
    reads: dict[str, MonthRead],
    win: Window,
    levers: HouseholdLevers,
    *,
    balances: NetWorth,
    debt_figure: NetWorth | None,
    today: date,
) -> KeyFigures:
    """The flow figures over the window's months, None below three closed months (D231):
    the savings rate (D235), the reserve from `balances` (D237), the debt figures from
    `debt_figure` with the window's rental interest (D239) and the free cash (D244)."""
    if not win.enough:
        return KeyFigures(None, None, None, None)
    months = [reads[m].household for m in win.months]
    interest = sum(
        (block.interest for m in win.months for block in reads[m].rental), Decimal("0.00")
    )
    debt_figures = None
    if debt_figure is not None:
        debt_figures = debt(
            debt_figure,
            months,
            interest=interest,
            estimate_sources=estimate_sources(conn, debt_figure),
        )
    return KeyFigures(
        savings_rate=savings_rate(months),
        reserve=reserve(_reserve_balances(conn, balances), months, levers, today=today),
        debt=debt_figures,
        free_cash=_free_cash(months),
    )


def _run_figure(conn: Connection, month_end: date) -> NetWorth | None:
    run = latest_run(conn, month_end)
    return None if run is None else month_end_figure(conn, run)


def run_totals(conn: Connection) -> list[RunTotals]:
    """The newest stored run of every month-end, summed as phase 6 sums it."""
    newest: dict[date, NetWorthRun] = {}
    for run in list_runs(conn):  # the newest capture of a month-end first
        newest.setdefault(run.as_of, run)
    out = []
    for run in newest.values():
        figure = month_end_figure(conn, run)
        out.append(
            RunTotals(
                net_worth_run_id=run.net_worth_run_id,
                month_end=run.as_of,
                created_at=run.captured_at,
                total=figure.total,
                by_class=figure.by_class,
                by_owner=figure.by_owner,
            )
        )
    return out


# --- GET /health, GET /health/months, GET /health/net-worth-series (§13) --------------------


def health(conn: Connection, today: date, scenario_id: UUID | None = None) -> Health:
    """GET /health on `today` (Berlin): the window of the closed months up to the last ended
    one; the savings rate, the need and the free cash over it; the reserve from today's
    balances; the debt figures from the window's newest month-end (its newest stored run,
    else that day on read); the split of today's net worth (D240); the series of the stored
    runs; the marks against the confirmed limits, the proposals while none is confirmed;
    and the to-dos: too few months, what keeps the month after the window open, the limits."""
    levers = household_levers(conn, scenario_id)
    reads = read_months(conn, ended_months(today), today)
    win = _window(reads)
    now = net_worth(conn, today)
    debt_figure = None
    if win.enough:
        end = month_bounds(win.months[-1])[1]
        debt_figure = _run_figure(conn, end) or net_worth(conn, end)
    figures = key_figures(
        conn, reads, win, levers, balances=now, debt_figure=debt_figure, today=today
    )
    newest = win.months[-1] if win.months else None
    todos = window_todos(win)
    after = [m for m, r in reads.items() if not r.closed.closed and (newest is None or m > newest)]
    if after:
        todos += reads[after[0]].closed.todos
    todos += bands_todos(levers.bands)
    out = Health(
        as_of=today,
        window=win,
        newest=None if newest is None else reads[newest].household,
        savings_rate=figures.savings_rate,
        reserve=figures.reserve,
        debt=figures.debt,
        split=split(now),
        free_cash=figures.free_cash,
        series=series(run_totals(conn), [r.household for r in reads.values() if r.closed.closed]),
        proposals=[] if levers.bands else list(proposals()[1].values()),
        todos=todos,
    )
    return out.model_copy(update={"marks": with_texts(out, lights(out, levers.bands))})


def _check_range(first: str, last: str) -> list[str]:
    months = months_between(first, last)
    if not months or len(months) > MAX_MONTHS:
        raise RuleViolationError(
            [HEALTH_RANGE_INVALID],
            f"{first} to {last}: the first month comes first, at most {MAX_MONTHS} months",
        )
    return months


def health_months(conn: Connection, first: str, last: str, today: date) -> list[HealthMonthView]:
    """GET /health/months?from=&to=: each month on read, with the newest snapshot of the
    month and, when its stored month differs from the month on read, the stored one. A month
    that has ended, is closed and has no snapshot gets a to-do when something keeps it from
    being stored: its month-end read again is incomplete (the first gap's text and button,
    `_incomplete`), else no month-end run is stored yet while the daily task would store the
    month (the month before it closed, `_waits_for_previous`). The net worth is read only for
    such months."""
    months = _check_range(first, last)
    before = _previous(months[0])
    reads = read_months(conn, ([before] if before >= FIRST_MONTH else []) + months, today)
    stored = _newest_snapshots(conn, months)
    start = _first_history_month(conn)
    out = []
    for month in months:
        read = reads[month]
        snapshot = stored.get(month)
        held = None
        if snapshot is not None:
            held = snapshot.figures.month
            if held == read.household.model_copy(update={"sources": {}}):
                held = None
        end = month_bounds(month)[1]
        todo = None
        if snapshot is None and read.closed.closed and end < today:
            figure = net_worth(conn, end)
            if _incomplete(figure):
                todo = _incomplete_todo(end, figure)
            elif latest_run(conn, end) is None and not _waits_for_previous(reads, month, start):
                todo = _month_end_todo(end)
        out.append(
            HealthMonthView(
                month=month,
                household=read.household,
                closed=read.closed,
                snapshot_id=None if snapshot is None else snapshot.id,
                stored=held,
                todo=todo,
            )
        )
    return out


def _month_end_todo(end: date) -> HealthTodo:
    """A closed month whose complete month-end has no run yet: the daily task captures it and
    stores the key figures beside it the next morning; the Übersicht's Nettovermögen card
    holds the button that captures it at once (POST /net-worth/snapshots {as_of} takes a past
    month-end, D241)."""
    return HealthTodo(
        code=HealthTodoCode.MONTH_END_MISSING,
        text=(
            f"Für den {end.strftime('%d.%m.%Y')} ist noch kein Monatsabschluss gespeichert; "
            "Hausbuch hält ihn am nächsten Morgen selbst fest, mit „Monatsabschluss "
            "festhalten“ sofort."
        ),
        action="Monatsabschluss festhalten",
        href=f"/?month_end={end.isoformat()}",
    )


# The net worth's to-dos that leave a month-end without a value it should hold (review of PR
# #426, findings 1 and 2): a position with no price or a stale one, a depot report that does
# not agree or lists an unbooked fund, a balance not reconciled to the day or older than it, a
# wallet's waiting coins, a loan without a balance. The owner-share to-dos move no total and
# do not count.
MONTH_END_GAPS = frozenset(
    {
        NetWorthTodoCode.BALANCE_UNRECONCILED,
        NetWorthTodoCode.BALANCE_OUTDATED,
        NetWorthTodoCode.PRICE_MISSING,
        NetWorthTodoCode.PRICE_STALE,
        NetWorthTodoCode.POSITION_NOT_RECONCILED,
        NetWorthTodoCode.POSITION_NOT_BOOKED,
        NetWorthTodoCode.LOAN_NOT_VALUED,
        NetWorthTodoCode.WALLET_WAITING,
    }
)


def _incomplete(figure: NetWorth) -> list[NetWorthTodo]:
    """The gaps of a month-end read again (MONTH_END_GAPS), empty when it is complete; a
    position without a value and no to-do of its own (none is known) still counts."""
    gaps = [todo for todo in figure.todos if todo.code in MONTH_END_GAPS]
    if not gaps and figure.unvalued_positions:
        gaps = [
            NetWorthTodo(
                code=NetWorthTodoCode.PRICE_MISSING,
                text=f"{figure.unvalued_positions} Positionen haben am Monatsende keinen Wert.",
                action="Anlagen öffnen",
                href="/assets",
            )
        ]
    return gaps


def _incomplete_todo(end: date, figure: NetWorth) -> HealthTodo:
    """The month's to-do while its month-end is incomplete: the first gap's text, button and
    link, and how many more there are. The code is MONTH_END_MISSING (a complete month-end is
    what is missing), so HealthTodoCode is unchanged."""
    gaps = _incomplete(figure)
    more = len(gaps) - 1
    tail = "" if not more else f" ({more} weitere Lücke{'n' if more > 1 else ''} im Nettovermögen)"
    return HealthTodo(
        code=HealthTodoCode.MONTH_END_MISSING,
        text=(
            f"Das Nettovermögen zum {end.strftime('%d.%m.%Y')} ist noch unvollständig, deshalb "
            f"werden die Kennzahlen des Monats noch nicht festgehalten: {gaps[0].text}{tail}"
        ),
        action=gaps[0].action,
        href=gaps[0].href,
    )


def _previous(month: str) -> str:
    return month_of(month_bounds(month)[0] - timedelta(days=1))


def _first_history_month(conn: Connection) -> str | None:
    """The first month of the household's history (review of PR #426, finding 3): the month
    of the day after the earliest opening balance of the accounts D231 checks (an opening
    balance is the balance at the end of its day, so the history starts the day after); None
    without such an account."""
    acc = account_table.c
    day = conn.execute(
        select(func.min(acc.opening_balance_on)).where(
            acc.kind.in_([k.value for k in COVERED_KINDS]),
            acc.purpose.in_([AccountPurpose.PRIVATE.value, AccountPurpose.RENTAL.value]),
        )
    ).scalar()
    return None if day is None else month_of(day + timedelta(days=1))


def _waits_for_previous(reads: dict[str, MonthRead], month: str, start: str | None) -> bool:
    """The daily task stores a month only once the month before it is closed, or when that
    month is before the household's history (before January 2026 or before `start`), so a
    month is never frozen with a window that a still-open month before it would later
    lengthen (review of PR #426, finding 3; POST /health/snapshots does not wait)."""
    before = _previous(month)
    if before < FIRST_MONTH or (start is not None and before < start):
        return False
    read = reads.get(before)
    return read is None or not read.closed.closed


def net_worth_series(conn: Connection, today: date) -> list[NetWorthPoint]:
    """GET /health/net-worth-series: the stored runs per month-end (D241), with each closed
    month's S beside its change."""
    reads = read_months(conn, ended_months(today), today)
    return series(run_totals(conn), [r.household for r in reads.values() if r.closed.closed])


# --- health_snapshot (D242, §5.5) ------------------------------------------------------------


@cache
def rule_version() -> str:
    """The SHA-256 of rules/health/ and rules/plan/: every file's path and bytes, sorted by
    path (§5.5), so a stored row names the rules that computed it."""
    digest = sha256()
    base = Path(rules_package.__file__).parent
    for folder in ("health", "plan"):
        for path in sorted((base / folder).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            digest.update(path.relative_to(base).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def _to_snapshot(row: Row) -> HealthSnapshot:
    return HealthSnapshot.model_validate(row._asdict())


def _newest_snapshots(conn: Connection, months: Sequence[str]) -> dict[str, HealthSnapshot]:
    """Per month the newest stored row (by created_at, then id)."""
    if not months:
        return {}
    h = health_snapshot_table.c
    ends = [month_bounds(m)[1] for m in months]
    out: dict[str, HealthSnapshot] = {}
    for row in conn.execute(
        select(health_snapshot_table)
        .where(h.month_end.in_(ends))
        .order_by(h.month_end, h.created_at.desc(), h.id)
    ):
        out.setdefault(month_of(row.month_end), _to_snapshot(row))
    return out


def list_snapshots(conn: Connection) -> list[HealthSnapshot]:
    """Every stored row, the newest month first, within a month the newest row first."""
    h = health_snapshot_table.c
    rows = conn.execute(
        select(health_snapshot_table).order_by(h.month_end.desc(), h.created_at.desc(), h.id)
    )
    return [_to_snapshot(r) for r in rows]


def snapshot_figures(
    conn: Connection,
    reads: dict[str, MonthRead],
    month: str,
    run_figure: NetWorth,
    levers: HouseholdLevers,
) -> HealthSnapshotFigures:
    """The figures of a closed month as of that month (§5.5): the window of the closed
    months up to it; the reserve, the debt figures and the split from its stored run, the
    reserve's day the run's; the month without sources."""
    win = _window(reads, up_to=month)
    figures = key_figures(
        conn,
        reads,
        win,
        levers,
        balances=run_figure,
        debt_figure=run_figure,
        today=run_figure.on,
    )
    return HealthSnapshotFigures(
        month=reads[month].household.model_copy(update={"sources": {}}),
        window=win,
        savings_rate=figures.savings_rate,
        reserve=figures.reserve,
        debt=figures.debt,
        split=split(run_figure),
        free_cash=figures.free_cash,
    )


def _lock(ctx: CommandContext) -> None:
    """One snapshot write at a time per workspace (the button and the daily task)."""
    key = func.hashtextextended(f"health:{ctx.workspace_id}", 0)
    ctx.conn.execute(select(func.pg_advisory_xact_lock(key)))


def _refuse_unclosed(month: str, read: MonthRead | None, today: date) -> None:
    if month < FIRST_MONTH or month_bounds(month)[1] >= today or read is None:
        raise RuleViolationError(
            [HEALTH_MONTH_NOT_CLOSED], f"{month} has not ended yet, or is before January 2026"
        )
    if not read.closed.closed:
        texts = "; ".join(todo.text for todo in read.closed.todos)
        raise RuleViolationError([HEALTH_MONTH_NOT_CLOSED], f"{month} is not closed: {texts}")


def _store(
    ctx: CommandContext,
    reads: dict[str, MonthRead],
    month: str,
    levers: HouseholdLevers,
    today: date,
    figure: NetWorth | None = None,
) -> HealthSnapshotResult:
    """Store `month` beside a complete, current month-end (review of PR #426, findings 1 and
    2): its month-end is read again (`figure` when the caller has read it); an incomplete
    reading is refused with health.month_end_incomplete, naming its gaps; a complete one is
    captured through net_worth.capture_month_end, insert-only, which writes a run when none
    holds the day or the newest differs from the reading and otherwise returns the newest,
    and the snapshot is stored beside that run. A day with nothing to value is refused with
    health.month_end_missing."""
    _refuse_unclosed(month, reads.get(month), today)
    end = month_bounds(month)[1]
    figure = net_worth(ctx.conn, end) if figure is None else figure
    gaps = _incomplete(figure)
    if gaps:
        raise RuleViolationError(
            [HEALTH_MONTH_END_INCOMPLETE],
            f"the net worth of {end.isoformat()} is incomplete: "
            + "; ".join(gap.text for gap in gaps),
        )
    captured = capture_month_end(ctx, end)
    run = captured.run
    if captured.outcome is SnapshotOutcome.EMPTY or run is None:
        raise RuleViolationError(
            [HEALTH_MONTH_END_MISSING], f"nothing holds a value on {end.isoformat()}"
        )
    figures = snapshot_figures(ctx.conn, reads, month, month_end_figure(ctx.conn, run), levers)
    version = rule_version()
    held = _newest_snapshots(ctx.conn, [month]).get(month)
    if (
        held is not None
        and held.net_worth_run_id == run.net_worth_run_id
        and held.rule_version == version
        and held.figures == figures
    ):
        return HealthSnapshotResult(outcome=HealthSnapshotOutcome.UNCHANGED, snapshot=held)
    row = ctx.conn.execute(
        insert(health_snapshot_table)
        .values(
            workspace_id=ctx.workspace_id,
            month_end=end,
            net_worth_run_id=run.net_worth_run_id,
            figures=figures.model_dump(mode="json"),
            rule_version=version,
            created_by=user_id(ctx),
            created_at=ctx.now,
        )
        .returning(*health_snapshot_table.c)
    ).one()
    snapshot = _to_snapshot(row)
    append_event(
        ctx,
        EventType.HEALTH_SNAPSHOT_STORED,
        snapshot.id,
        HealthSnapshotStored(month=month, net_worth_run_id=run.net_worth_run_id),
    )
    return HealthSnapshotResult(outcome=HealthSnapshotOutcome.STORED, snapshot=snapshot)


def store_snapshot(ctx: CommandContext, month: str) -> HealthSnapshotResult:
    """POST /health/snapshots {month}: store a closed month's key figures beside its
    month-end run, captured first when none holds the day or the newest differs from the day
    read again (`_store`). Refused with health.month_not_closed (not ended, or D231's checks
    fail; the message names the month's to-dos), health.month_end_incomplete (the month-end
    read again has a gap) or health.month_end_missing (nothing to value that day). It does
    not wait for the month before (the daily task does). A new row only when the newest row
    of the month differs (its run, its rule version or its figures); otherwise that row,
    `unchanged`."""
    _lock(ctx)
    today = ctx.now.astimezone(LOCAL_TIME_ZONE).date()
    months = months_between(FIRST_MONTH, month) if month >= FIRST_MONTH else []
    reads = read_months(ctx.conn, months, today)
    return _store(ctx, reads, month, household_levers(ctx.conn), today)


def store_missing_snapshots(ctx: CommandContext) -> list[HealthSnapshotResult]:
    """The daily task's step (§11.5): a snapshot for every month that has ended, is closed
    and has no snapshot yet, oldest first, when the month before it is closed or before the
    household's history (`_waits_for_previous`, review of PR #426 finding 3) and its
    month-end read again is complete (`_incomplete`). The month-end is captured as `_store`
    does, so past months fill in without a click and a run frozen before the month's lines
    arrived is captured again first. A month that waits or is incomplete is skipped, its
    to-do on GET /health/months. Idempotent: a second run the same day stores nothing."""
    _lock(ctx)
    today = ctx.now.astimezone(LOCAL_TIME_ZONE).date()
    reads = read_months(ctx.conn, ended_months(today), today)
    held = _newest_snapshots(ctx.conn, list(reads))
    levers = household_levers(ctx.conn)
    start = _first_history_month(ctx.conn)
    out = []
    for month, read in reads.items():
        if month in held or not read.closed.closed or _waits_for_previous(reads, month, start):
            continue
        figure = net_worth(ctx.conn, month_bounds(month)[1])
        if _incomplete(figure) or not any(line.value_eur is not None for line in figure.lines):
            continue
        out.append(_store(ctx, reads, month, levers, today, figure))
    return out


# --- hausbuch health-check (D259 amended 17 Nov 2026, §16.1) --------------------------------

EURO = Decimal("1")
POINT = Decimal("0.001")  # 0.1 percentage points of a ratio
TENTH = Decimal("0.1")


def _stored_levers(figures: HealthSnapshotFigures) -> HouseholdLevers:
    """The reserve levers a snapshot was computed with, read back from its stored reserve
    (the target in months and the accounts the lever added or removed), so a lever changed
    since does not move the recomputation."""
    if figures.reserve is None:
        return HouseholdLevers()
    accounts = figures.reserve.accounts
    return HouseholdLevers(
        reserve_target_months=figures.reserve.target_months,
        reserve_accounts=ReserveAccounts(
            added=[a.account_id for a in accounts if a.origin is ReserveOrigin.ADDED],
            removed=[a.account_id for a in accounts if a.origin is ReserveOrigin.REMOVED],
        ),
    )


def _compared(
    figures: HealthSnapshotFigures, net_worth_total: Decimal
) -> dict[str, tuple[Decimal | None, Decimal]]:
    """§16.1's figures with their tolerance step: amounts to the euro, ratios to 0.1
    percentage points, months to 0.1."""
    rate, reserve_, debt_ = figures.savings_rate, figures.reserve, figures.debt
    out: dict[str, tuple[Decimal | None, Decimal]] = {
        "net_worth": (net_worth_total, EURO),
        "savings_rate_month": (figures.month.savings_rate, POINT),
        "savings_rate_window": (None if rate is None else rate.rate, POINT),
        "reserve_amount": (None if reserve_ is None else reserve_.amount, EURO),
        "reserve_months": (None if reserve_ is None else reserve_.months, TENTH),
        "debt_ratio": (None if debt_ is None else debt_.debt_ratio, POINT),
        "debt_service_ratio": (None if debt_ is None else debt_.debt_service_ratio, POINT),
        "debt_to_income": (None if debt_ is None else debt_.debt_to_income, TENTH / 10),
        "free_cash": (figures.free_cash, EURO),
    }
    for item in [] if debt_ is None else debt_.properties:
        out[f"ltv:{item.property_id}"] = (item.ltv, POINT)
    return out


def _rounded(value: Decimal | None, step: Decimal) -> Decimal | None:
    return None if value is None else value.quantize(step, rounding=ROUND_HALF_UP)


def _input_checks(month: str, stored: NetWorth, now: NetWorth) -> list[HealthCheckInput]:
    """Each account and position line of the month-end run against the same day read again
    from its sources (a statement's balance, a depot's report, an exchange's typed balance,
    a wallet's chain balance, as the net worth reads them): agrees when the value is equal
    to the cent. The reading's own gaps disagree too (review of PR #426): a line of the
    reading without a value that the run does not hold (a holding the run omitted), and each
    gap to-do of the reading (`_incomplete`) beyond what such a line already names, its text
    the reason."""
    run_id = None if stored.run is None else stored.run.net_worth_run_id

    def key(line: NetWorthLine) -> tuple:
        return (line.account_id, line.asset_id)

    current = {key(line): line for line in now.lines if line.account_id is not None}
    out = []
    for line in stored.lines:
        if line.account_id is None:
            continue
        again = current.get(key(line))
        if again is None or again.value_eur is None:
            reason = "Am Monatsende heute nicht mehr bewertet"
        elif again.value_eur != line.value_eur:
            reason = "Der Wert am Monatsende weicht heute vom gespeicherten ab"
        else:
            reason = None
        out.append(
            HealthCheckInput(
                month=month,
                net_worth_run_id=run_id,
                account_id=line.account_id,
                agrees=reason is None,
                reason=reason,
            )
        )
    held = {key(line) for line in stored.lines if line.account_id is not None}
    for line in now.lines:
        if line.account_id is None or line.value_eur is not None or key(line) in held:
            continue
        where = (
            line.label if line.account_label is None else f"{line.label} („{line.account_label}“)"
        )
        out.append(
            HealthCheckInput(
                month=month,
                net_worth_run_id=run_id,
                account_id=line.account_id,
                agrees=False,
                reason=f"Am Monatsende ohne Wert und nicht im gespeicherten Abschluss: {where}",
            )
        )
    for gap in _incomplete(now):
        if gap.code in UNVALUED_LINE_GAPS:
            continue  # its line, valued or not, is checked above
        out.append(
            HealthCheckInput(month=month, net_worth_run_id=run_id, agrees=False, reason=gap.text)
        )
    return out


# The gaps a position line without a value carries itself (net_worth._positions and
# _unbooked_funds), so health-check names the line rather than the to-do as well.
UNVALUED_LINE_GAPS = frozenset(
    {
        NetWorthTodoCode.PRICE_MISSING,
        NetWorthTodoCode.POSITION_NOT_RECONCILED,
        NetWorthTodoCode.POSITION_NOT_BOOKED,
    }
)


def health_check(conn: Connection, first: str, last: str, today: date) -> HealthCheckResult:
    """D259 amended: each month from `first` to `last` with a stored snapshot (its newest
    row) is recomputed from the stored rows, with the month-end run the snapshot names and
    the reserve levers it stored, and each §16.1 figure compared within its tolerance; the
    net worth is the run's total against the same day read again, and each of the run's
    account lines is an input checked against that read. A month without a snapshot is
    left out at either end of the range; raises health.range_invalid for an empty range and
    health.month_end_missing when no month of it has a snapshot or one between two that do
    has none (the months compared are consecutive)."""
    months = months_between(first, last)
    if not months:
        raise RuleViolationError([HEALTH_RANGE_INVALID], f"{first} comes after {last}")
    stored = _newest_snapshots(conn, months)
    if not stored:
        raise RuleViolationError(
            [HEALTH_MONTH_END_MISSING], f"no month from {first} to {last} has a snapshot"
        )
    checked = sorted(stored)
    gaps = [m for m in months_between(checked[0], checked[-1]) if m not in stored]
    if gaps:
        raise RuleViolationError(
            [HEALTH_MONTH_END_MISSING],
            f"the months between have no snapshot: {', '.join(gaps)} („Kennzahlen festhalten“)",
        )
    reads = read_months(conn, months_between(FIRST_MONTH, checked[-1]), today)
    rows: list[HealthCheckRow] = []
    inputs: list[HealthCheckInput] = []
    for month in checked:
        snapshot = stored[month]
        run = stored_run(conn, snapshot.net_worth_run_id)
        if run is None:  # unreachable: runs are append-only
            raise RuleViolationError([HEALTH_MONTH_END_MISSING], f"{month}: its run is gone")
        again = net_worth(conn, run.on)
        recomputed = snapshot_figures(conn, reads, month, run, _stored_levers(snapshot.figures))
        held = _compared(snapshot.figures, run.total)
        fresh = _compared(recomputed, again.total)
        for figure in sorted(held.keys() | fresh.keys()):
            value, step = held.get(figure, (None, EURO))
            new, step = fresh.get(figure, (None, step))
            rows.append(
                HealthCheckRow(
                    month=month,
                    figure=figure,
                    stored=None if value is None else str(value),
                    recomputed=None if new is None else str(new),
                    equal=_rounded(value, step) == _rounded(new, step),
                )
            )
        inputs += _input_checks(month, run, again)
    return HealthCheckResult(
        months=checked,
        rows=rows,
        inputs=inputs,
        match=all(r.equal for r in rows) and all(i.agrees for i in inputs),
    )
