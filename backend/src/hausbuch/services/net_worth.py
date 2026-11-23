"""The household's net worth on read and its stored month-ends (docs/phases/phase-6.md D167
(4), (5), D176 (5), §9, §12, §13; phase 6 step 6). The arithmetic is rules/net_worth.py.

NetWorth(on) = Σ balances + Σ positions × price + the properties' estimate − their loans:

- **Balances** of every bank account, every depot's cash (its PDF statements' lines, which
  the Anlagen overview leaves out because a depot's cash is no movement) and every credit
  card (a debt, signed as its statements print it: negative when owed, class
  Verbindlichkeiten, D176 (5)). A balance is the anchor plus the lines not excluded as
  duplicates, booked after the anchor day through `on`, but only as far as a reconciled
  import reaches (D167 (4): the last reconciled balance on or before the day): when the
  newest reconciled import ends before `on`, the balance is the one at its last booking day,
  shown as „Stand vom …“; lines booked after it up to `on` are a to-do (Zu prüfen holds the
  import's card), and with no line waiting a balance more than a week older than the day
  (measured to the day the account's history reaches) is a to-do to import the next
  statement or fetch it. Without any reconciled import the balance is the anchor. An account whose
  anchor is after `on` has no figure that day. A depot or a card with neither an import nor
  an opening balance has no balance line.
- **Positions** of every depot, exchange and wallet account: the Anlagen overview's own
  valuation (`services.prices.overview.assets_overview`, with D167 (b)'s report guard and an
  exchange's EUR after its trades, D178 amended (2)), so the Übersicht and the Anlagen screen
  never disagree. The class follows the account's kind, not the asset's (an exchange's EUR
  is Krypto). A position without a value counts nowhere and is a to-do. A fund a depot's
  latest report lists while no unit of it is booked (the overview shows booked units only)
  is listed unvalued too, with a to-do pointing to the holdings card in Zu prüfen.
- **The property** from its handover (purchase_date) to the end of ownership: the value in
  today's year by the default scenario as it stood on `on` (its „Wert heute“, else the
  purchase price; rules.performance.projection.value_today, D86), an estimate. Phase 9 D241
  (Q-O131): `projection_scenario` is append-only, so the scenario of a past day is the newest
  default recorded by the end of that day and not yet withdrawn or superseded then
  (services.scenarios.estimate_on_day, which hands those rows to
  rules.health.figures.estimate_on); until 17 Nov 2026 every day read today's. **Its
  loans** from the payout, by the repayment schedule (the scenario's follow-up rate where it
  sets one, as the projection does), an estimate too.
- **Before the handover (D265, 20 Nov 2026).** For a property whose handover is after `on`:
  „Anzahlung Kaufpreis – <property>“, the price paid so far (minus the confirmed allocations
  booked through `on` naming the property: `purchase_price` on any own account,
  `hh_investment_property` on a private one; capped at the price, no line while nothing is
  paid), and each loan from its disbursed_on by its confirmed payouts through `on` (capped at
  its principal; with none, not valued and the to-do net_worth.loan_payout_missing). Both are
  booked figures, not estimates, and the schedule is not read. Until 20 Nov 2026 neither
  counted before the handover, so a down payment from own money vanished until then.
- **Owners**: an account's figures by its account_ownership set in force on `on` (the newest
  recorded set whose effective_from is on or before `on`; before its first set, the earliest
  one), the property and its loans by the property's shares; split to the cent by
  rules.net_worth.split.

**Month-ends (D167 (5) amended, migration 0040).** `capture_month_end` writes one
valuation_snapshot row per valued figure, all under one net_worth_run_id: per account balance
and per position (asset null for a balance), per property (its estimate) and per loan (its
balance, negative), and the event workspace.valuation_snapshot_written. The table is
immutable, so a run is never changed. The periodic task `month_end` (1st, 08:00 UTC, after
the 06:30 bank fetch and the price fetch, from 07:00) and „Monatsabschluss festhalten“ both write a
new run for the month-end when the figures read now differ from the newest stored run (a
statement or a price that arrived late, or a button press before the morning's fetches), and
nothing when they are the same; the newest run of a month-end is the one shown, the older
runs stay. Until 15 Nov 2026 the task wrote only when no run held the month-end yet, so a
press before 08:00 on the 1st froze an early figure for good (review, finding 3). The
overview reads the stored month-end's day again and says when today's reading of it differs
(`month_end_now`). Captures of one workspace run one at a time (an advisory lock).

**Past month-ends (phase 9 D241, amending D167 (5)).** `capture_month_end` and POST
/net-worth/snapshots {as_of} take any month-end from 31 Jan 2026 to the last one before today
(rules.net_worth.month_end_codes; refused with net_worth.not_month_end,
net_worth.month_end_before_history or net_worth.month_end_not_closed), because a month often
closes weeks later. A past day is read as `net_worth(on)` reads any day: a balance by the
reconciled imports up to `on` (exact once a reconciled import reaches it, else „Stand vom …“
and its to-do), positions by the confirmed movements through `on` priced by the newest price
on or before `on` (stale after seven days, missing before the first stored price: both
to-dos), a depot's fund checked against its latest report on or before `on`, a wallet's
movements waiting for a person named as today. Two figures are not as of the day: a loan's
balance follows its terms as they are now (the loan row is edited in place), and an owner's
account shares follow the sets in force on `on` as recorded now.

A stored month-end is read from its rows alone: every euro figure, the classes and the total
are the stored values, never recomputed (a later „Wert heute“ or loan change leaves it as
it was). Only what the rows do not hold is looked up: the labels (a run before a property's
handover reads its property and loan rows as the advance and the payouts, D265 (4)), and the
owner split by the account shares in force on the month-end and the property's shares.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, func, insert, select

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import account_ownership as ownership_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import bank_connection as connection_table
from hausbuch.db.tables import loan as loan_table
from hausbuch.db.tables import owner as owner_table
from hausbuch.db.tables import property_ as property_table
from hausbuch.db.tables import property_ownership as property_ownership_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.db.tables import valuation_snapshot as snapshot_table
from hausbuch.rules import errors as E
from hausbuch.rules import net_worth as rules
from hausbuch.rules import valuation
from hausbuch.rules.errors import RuleError
from hausbuch.rules.loan_schedule import build_schedule
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.enums import (
    BANK_ACCOUNT_KINDS,
    AccountKind,
    AccountPurpose,
    AllocationStatus,
    ImportStatus,
)
from hausbuch.schemas.events import EventType, ValuationSnapshotWritten
from hausbuch.schemas.household import AssetKind, NetWorthClass, PriceSourceKind
from hausbuch.schemas.net_worth import (
    NetWorth,
    NetWorthLine,
    NetWorthLineKind,
    NetWorthOverview,
    NetWorthRun,
    NetWorthTodo,
    NetWorthTodoCode,
    SnapshotOutcome,
    SnapshotResult,
)

from .context import CommandContext
from .depot import MovementReads, report_checks_with
from .depot import _german_units as german_units
from .errors import require_valid
from .events import append_event
from .loans import list_loans
from .prices.overview import assets_overview_with
from .reminders import idle_cards
from .scenarios import estimate_on_day
from .transfers import _current_links, _own_account_sets
from .transfers import _last_days as history_ends  # D162 amended (9)'s history end

C = NetWorthClass
L = NetWorthLineKind
T = NetWorthTodoCode
ZERO = Decimal("0.00")
# Accounts whose own statements carry a balance: the bank accounts always, a depot's cash and
# a credit card when they have statements (or a typed opening balance).
_BALANCE_KINDS = (*BANK_ACCOUNT_KINDS, AccountKind.DEPOT, AccountKind.CREDIT_CARD)


def _german(day: date) -> str:
    return day.strftime("%d.%m.%Y")


def last_month_end(today: date) -> date:
    """The last day of the month before `today`'s."""
    return today.replace(day=1) - timedelta(days=1)


# --- shares ---------------------------------------------------------------------------------


def shares_on(
    conn: Connection, account_ids: Iterable[UUID], on: date
) -> dict[UUID, dict[UUID, Fraction]]:
    """Per account its ownership set in force on `on`: of the sets recorded (rows sharing
    created_at and effective_from), the newest whose effective_from is on or before `on`. A
    set recorded later for an earlier day (a correction) wins over an older one; one dated
    after `on` waits for its day when an earlier set exists. For a day before every set of
    the account, its earliest set applies (the earliest effective_from, the newest recorded
    within it): owner shares rarely change and the form dates a first set today, so an
    earlier month-end would otherwise count as unassigned with a false to-do (review of 15
    Nov 2026, finding 6). An account without any set is missing."""
    return _shares_from(_ownership_rows(conn, account_ids), on)


def _ownership_rows(conn: Connection, account_ids: Iterable[UUID]) -> list:
    """Every ownership row of `account_ids`, for `_shares_from` (one query for any number of
    days, so a batch of month-ends reads them once)."""
    ids = list(account_ids)
    if not ids:
        return []
    o = ownership_table.c
    return conn.execute(
        select(
            o.account_id,
            o.owner_id,
            o.share_numerator,
            o.share_denominator,
            o.effective_from,
            o.created_at,
        ).where(o.account_id.in_(ids))
    ).all()


def _shares_from(rows: Iterable[Any], on: date) -> dict[UUID, dict[UUID, Fraction]]:
    """`shares_on` over rows already read."""
    sets: dict[UUID, dict[tuple[datetime, date], dict[UUID, Fraction]]] = defaultdict(dict)
    for r in rows:
        sets[r.account_id].setdefault((r.created_at, r.effective_from), {})[r.owner_id] = Fraction(
            r.share_numerator, r.share_denominator
        )
    found: dict[UUID, dict[UUID, Fraction]] = {}
    for account_id, recorded in sets.items():
        in_force = [key for key in recorded if key[1] <= on]
        if in_force:
            key = max(in_force)  # the newest recorded, then the later day
        else:
            first = min(day for _, day in recorded)
            key = max(key for key in recorded if key[1] == first)
        found[account_id] = recorded[key]
    return found


def _property_shares(conn: Connection, property_ids: Sequence[UUID]) -> dict[UUID, dict]:
    if not property_ids:
        return {}
    p = property_ownership_table.c
    found: dict[UUID, dict[UUID, Fraction]] = defaultdict(dict)
    for r in conn.execute(
        select(p.property_id, p.owner_id, p.share_numerator, p.share_denominator).where(
            p.property_id.in_(property_ids)
        )
    ):
        found[r.property_id][r.owner_id] = Fraction(r.share_numerator, r.share_denominator)
    return dict(found)


def _owner_ids(conn: Connection) -> list[UUID]:
    o = owner_table.c
    return list(conn.execute(select(o.id).order_by(o.created_at, o.id)).scalars())


# --- the parts ------------------------------------------------------------------------------


@dataclass
class _Item:
    line: NetWorthLine
    shares_ref: UUID  # the account or property whose shares split it


@dataclass(frozen=True)
class _Transit:
    """A booked transfer from an own account to another (phase 6 D292 (3)): the debit, the
    euros it sent and the one own account its counterparty IBAN names."""

    transaction_id: UUID
    account_id: UUID  # the sending account
    paid_on: date
    amount: Decimal  # positive: the transfer rows of the debit, a deposit fee left out
    receiver: UUID
    linked: bool  # a leg of a current transfer link


_TRANSFER_OUT = frozenset(
    {AllocationCategory.INTERNAL_TRANSFER_OUT.value, AllocationCategory.OWNER_TRANSFER_OUT.value}
)


def _transfers_out(conn: Connection) -> list[_Transit]:
    """Phase 6 D292 (3): every booked transfer from an own account that may be in transit:
    a debit of a reconciled import, not excluded as a duplicate, whose live rows are all
    confirmed and all a transfer out (a deposit split's bank fee beside it allowed, D162
    amended), naming by its counterparty IBAN exactly one own account other than its own
    (an account's IBAN, or a card's payment IBAN two cards may share, then no receiver is
    sure and it is left out). Read once per call, whatever its days."""
    t, a, imp = transaction_table.c, allocation_table.c, import_table.c
    out_rows = select(a.transaction_id).where(
        a.status == AllocationStatus.CONFIRMED.value,
        a.amount != 0,
        a.category.in_(sorted(_TRANSFER_OUT)),
    )
    rows = conn.execute(
        select(
            t.id,
            t.account_id,
            t.booking_date,
            t.counterparty_iban_hmac,
            a.category,
            a.amount,
            a.status,
        )
        .join(import_table, imp.id == t.import_id)
        .join(allocation_table, a.transaction_id == t.id)
        .where(
            t.id.in_(out_rows),
            t.amount < 0,
            t.excluded_as_duplicate_of.is_(None),
            t.counterparty_iban_hmac.is_not(None),
            imp.status == ImportStatus.RECONCILED.value,
            a.status.in_([AllocationStatus.CONFIRMED.value, AllocationStatus.PROPOSED.value]),
            a.amount != 0,
        )
        .order_by(t.booking_date, t.id)
    ).all()
    if not rows:
        return []
    by_line: dict[UUID, list] = defaultdict(list)
    for r in rows:
        by_line[r.id].append(r)
    owners = _own_account_sets(conn)
    linked = {
        leg
        for link in _current_links(conn)
        for leg in (link.out_transaction_id, link.in_transaction_id)
        if leg is not None
    }
    found = []
    for tx_id, parts in by_line.items():
        if any(p.status != AllocationStatus.CONFIRMED.value for p in parts):
            continue
        if any(
            p.category not in _TRANSFER_OUT and p.category != AllocationCategory.BANK_FEES.value
            for p in parts
        ):
            continue
        amount = -sum((p.amount for p in parts if p.category in _TRANSFER_OUT), ZERO)
        first = parts[0]
        receivers = owners.get(bytes(first.counterparty_iban_hmac).hex(), set()) - {
            first.account_id
        }
        if amount <= 0 or len(receivers) != 1:
            continue
        found.append(
            _Transit(
                transaction_id=tx_id,
                account_id=first.account_id,
                paid_on=first.booking_date,
                amount=amount,
                receiver=next(iter(receivers)),
                linked=tx_id in linked,
            )
        )
    return found


def transit_label(receiver: str) -> str:
    return f"Unterwegs zu „{receiver}“"


def transit_note(paid_on: date, sender: str, *, partly: bool = False) -> str:
    if partly:  # D292 amended (a): a card takes only what its known debt leaves room for
        return (
            f"Am {_german(paid_on)} von „{sender}“ überwiesen; zählt hier nur, soweit es die "
            "bekannte Schuld der Karte tilgt, bis ein Kontoauszug dieses Kontos den Tag abdeckt"
        )
    return (
        f"Am {_german(paid_on)} von „{sender}“ überwiesen; zählt hier, bis ein Kontoauszug "
        "dieses Kontos den Tag abdeckt"
    )


def merge_transit(lines: Sequence[NetWorthLine]) -> list[NetWorthLine]:
    """Phase 6 D292 (3): the lines with each „unterwegs zu <Konto>“ line folded into its
    receiving account's balance line, so an account has one balance (the month-end's stored
    row per account, the key figures' reserve and health-check's comparison read it so). The
    net worth's total is the same either way; only the live figure shows the line apart."""
    moving: dict[UUID, Decimal] = {}
    for line in lines:
        if line.in_transit and line.value_eur is not None and line.account_id is not None:
            moving[line.account_id] = moving.get(line.account_id, ZERO) + line.value_eur
    if not moving:
        return list(lines)
    out: list[NetWorthLine] = []
    for line in lines:
        if line.in_transit:
            continue
        if (
            line.kind is L.ACCOUNT
            and line.asset_id is None
            and line.account_id in moving
            and line.value_eur is not None
        ):
            extra = moving.pop(line.account_id)
            line = line.model_copy(update={"value_eur": line.value_eur + extra})
        out.append(line)
    for line in lines:  # a receiver without a balance line (_balances never makes one)
        if line.in_transit and line.account_id in moving:
            out.append(
                line.model_copy(
                    update={
                        "in_transit": False,
                        "label": line.account_label or line.label,
                        "note": None,
                        "value_eur": moving.pop(line.account_id),
                    }
                )
            )
    return out


def depot_cash(conn: Connection, on: date) -> dict[UUID, list[NetWorthLine]]:
    """Per depot account the net worth's account lines on `on`: its cash balance from its
    statements and each „unterwegs zu <Depot>“ line, in the net worth's order and with its
    values (not split by owner shares). The Anlagen ETF tab adds them to the funds so each
    portfolio shows the total the depot's own app shows (Luis, 23 Nov 2026: the quirion
    portfolios still read lower than in quirion's app). Read only; the same `_balances`."""
    items, _ = _balances(conn, on, _Reads(conn))
    out: dict[UUID, list[NetWorthLine]] = defaultdict(list)
    for item in items:
        line = item.line
        if line.account_kind is AccountKind.DEPOT and line.account_id is not None:
            out[line.account_id].append(line)
    return dict(out)


class _Reads:
    """What a net worth reads that does not depend on its day, read once per call and shared
    by the days one call computes (overview: the last month-end and today). Nothing is kept
    between calls."""

    def __init__(self, conn: Connection) -> None:
        self.movements = MovementReads(conn)
        self._conn = conn
        self._idle: set[UUID] | None = None
        self._history: Any = None
        self._transit: list[_Transit] | None = None

    def transit(self) -> list[_Transit]:
        if self._transit is None:
            self._transit = _transfers_out(self._conn)
        return self._transit

    def idle(self) -> set[UUID]:
        if self._idle is None:
            self._idle = idle_cards(self._conn)
        return self._idle

    def history(self) -> Any:
        if self._history is None:
            self._history = history_ends(self._conn)
        return self._history


def _balances(conn: Connection, on: date, reads: _Reads) -> tuple[list[_Item], list[NetWorthTodo]]:
    acc, imp, t = account_table.c, import_table.c, transaction_table.c
    accounts = conn.execute(
        select(acc.id, acc.label, acc.kind, acc.opening_balance, acc.opening_balance_on)
        .where(acc.kind.in_([k.value for k in _BALANCE_KINDS]), acc.opening_balance_on <= on)
        .order_by(acc.label, acc.id)
    ).all()
    if not accounts:
        return [], []
    ids = [a.id for a in accounts]
    imports = conn.execute(
        select(
            imp.account_id,
            func.count().label("n"),
            func.max(imp.last_booking_on)
            .filter(imp.status == ImportStatus.RECONCILED.value)
            .label("reconciled_through"),
        )
        .where(imp.account_id.in_(ids))
        .group_by(imp.account_id)
    ).all()
    imported = {r.account_id: r for r in imports}
    lines: dict[UUID, list[tuple[date, Decimal]]] = defaultdict(list)
    for r in conn.execute(
        select(t.account_id, t.booking_date, t.amount).where(
            t.account_id.in_(ids),
            t.excluded_as_duplicate_of.is_(None),
            t.booking_date <= on,
        )
    ):
        lines[r.account_id].append((r.booking_date, r.amount))
    history = reads.history()
    connected = set(
        conn.execute(
            select(connection_table.c.account_id).where(connection_table.c.account_id.in_(ids))
        ).scalars()
    )
    # A credit card idle by phase 5 D122 amended (6): its latest statement closed at 0 and no
    # own account paid it since, so its issuer sends no statement and 0 is its balance on every
    # later day; no to-do asks for a statement that will never come, and the month-end is not
    # held open by it (Luis, 18 Nov 2026: the Consors Finanz card unused since January).
    idle = (
        reads.idle()
        if any(AccountKind(a.kind) is AccountKind.CREDIT_CARD for a in accounts)
        else set()
    )
    items: list[_Item] = []
    todos: list[NetWorthTodo] = []
    # each account with a balance line: the row, its `through` and its balance on `on`
    counted: dict[UUID, tuple[Any, date, Decimal]] = {}
    for a in accounts:
        kind = AccountKind(a.kind)
        facts = imported.get(a.id)
        if kind not in BANK_ACCOUNT_KINDS and facts is None and a.opening_balance == 0:
            continue  # a depot without cash statements, a card without statements
        reached = facts.reconciled_through if facts is not None else None
        if reached is not None and reached >= on:
            through = on
        else:
            through = max(reached or a.opening_balance_on, a.opening_balance_on)
        own = lines.get(a.id, [])
        balance = rules.bank_balance(a.opening_balance, a.opening_balance_on, own, through)
        counted[a.id] = (a, through, balance)
        waiting = sum(1 for day, _ in own if through < day <= on and day > a.opening_balance_on)
        items.append(
            _Item(
                NetWorthLine(
                    kind=L.ACCOUNT,
                    net_worth_class=rules.class_of(kind),
                    label=a.label,
                    account_id=a.id,
                    account_kind=kind,
                    account_label=a.label,
                    value_eur=balance,
                    as_of=through,
                    stale=a.id not in idle and (on - through).days > valuation.STALE_AFTER_DAYS,
                ),
                a.id,
            )
        )
        if waiting:
            todos.append(
                NetWorthTodo(
                    code=T.BALANCE_UNRECONCILED,
                    text=(
                        f"Der Kontostand von „{a.label}“ ist nur bis zum {_german(through)} "
                        f"abgestimmt; {waiting} Umsätze danach passen noch nicht zu einem "
                        "Kontoauszug und zählen noch nicht mit."
                        if waiting != 1
                        else f"Der Kontostand von „{a.label}“ ist nur bis zum "
                        f"{_german(through)} abgestimmt; ein Umsatz danach passt noch nicht "
                        "zu einem Kontoauszug und zählt noch nicht mit."
                    ),
                    action="Zu prüfen öffnen",
                    href="/inbox",
                )
            )
        elif (
            a.id not in idle
            and (on - max(through, history.get(a.id, through))).days > valuation.STALE_AFTER_DAYS
        ):
            todos.append(_outdated(a.id, a.label, kind, through, on, a.id in connected))
    items += _in_transit(on, counted, history, reads)
    return items, todos


def _in_transit(
    on: date, counted: dict[UUID, tuple[Any, date, Decimal]], history: Any, reads: _Reads
) -> list[_Item]:
    """Phase 6 D292 (3), 22 Nov 2026 (Luis: „The Quirion values in the app didn't match the
    values in Quirion“): a booked transfer (_transfers_out) whose debit the sending account's
    balance holds on `on` while the receiving account's history ends before the payment's day
    (rules.net_worth.in_transit) counts at its amount as „unterwegs zu <Konto>“ on the
    receiving account, one line per transfer after every balance line. It drops once a
    statement of the receiver reaches the day (its own figures then count the money), or once
    the debit is linked to its counterpart. A line of the net worth only: no movement, no lot,
    no tax figure reads it. Both accounts must have a balance line on `on`; a depot without
    cash statements has no history, so nothing is in transit to it.

    D292 amended (a), 23 Nov 2026: a credit card takes its transfers only up to the debt its
    balance line shows (rules.net_worth.transit_counted, in payment order), so money in transit
    never lifts a card above 0; a transfer counted in part says so in its note, one that counts
    nothing has no line."""
    moving: dict[UUID, list[_Transit]] = defaultdict(list)
    for move in reads.transit():
        sender, receiver = counted.get(move.account_id), counted.get(move.receiver)
        if sender is None or receiver is None:
            continue
        sent_from, through, _ = sender
        if rules.in_transit(
            move.paid_on,
            sent_from.opening_balance_on,
            through,
            history.get(move.receiver),
            linked=move.linked,
        ):
            moving[move.receiver].append(move)
    out = []
    for receiver_id, moves in moving.items():
        account, _, balance = counted[receiver_id]
        kind = AccountKind(account.kind)
        moves.sort(key=lambda m: (m.paid_on, str(m.transaction_id)))
        counts = rules.transit_counted(
            [m.amount for m in moves], balance if kind is AccountKind.CREDIT_CARD else None
        )
        for move, value in zip(moves, counts, strict=True):
            if value <= 0:
                continue
            sent_from = counted[move.account_id][0]
            partly = value < move.amount
            out.append(
                _Item(
                    NetWorthLine(
                        kind=L.ACCOUNT,
                        net_worth_class=rules.class_of(kind),
                        label=transit_label(account.label),
                        account_id=account.id,
                        account_kind=kind,
                        account_label=account.label,
                        value_eur=value,
                        note=transit_note(move.paid_on, sent_from.label, partly=partly),
                        in_transit=True,
                    ),
                    account.id,
                )
            )
    return out


def _outdated(
    account_id: UUID, label: str, kind: AccountKind, through: date, on: date, connected: bool
) -> NetWorthTodo:
    """A balance older than the day asked with no line waiting after it: the statements (or
    the fetch) stop before the day (review of 15 Nov 2026, finding 2). Only past the stale
    limit and measured to the day the account's history reaches (a FinTS run that brought
    nothing new counts, D162 amended (9)), so a quiet week or a fetch over the weekend is no
    to-do; the actions are the account page's own („Kontoauszug hochladen“ on its
    Kontoauszüge tab, as bank.statement_due links it, or the Bankabruf card's „Jetzt
    abrufen“)."""
    gap = f"Der Kontostand von „{label}“ ist vom {_german(through)}; bis zum {_german(on)} "
    if connected:
        return NetWorthTodo(
            code=T.BALANCE_OUTDATED,
            text=gap + "hat der Bankabruf nichts Neueres geholt. Im Bankabruf „Jetzt abrufen“ "
            "wählen, sonst den Kontoauszug hochladen.",
            action="Konto öffnen",
            href=f"/accounts/{account_id}",
        )
    card = kind is AccountKind.CREDIT_CARD
    return NetWorthTodo(
        code=T.BALANCE_OUTDATED,
        text=gap + f"fehlt noch {'die Abrechnung' if card else 'ein Kontoauszug'}; der Betrag "
        "zählt bis dahin mit dem älteren Stand.",
        action="Kreditkartenabrechnung hochladen" if card else "Kontoauszug hochladen",
        href=f"/accounts/{account_id}?tab=kontoauszuege",
    )


def _positions(
    conn: Connection, on: date, reads: _Reads
) -> tuple[list[_Item], list[NetWorthTodo], int, int]:
    overview = assets_overview_with(conn, on, reads.movements)
    items: list[_Item] = []
    todos: list[NetWorthTodo] = []
    stale = unvalued = 0
    for account in overview.accounts:
        kind = AccountKind(account.kind)
        for p in account.positions:
            price = p.price
            items.append(
                _Item(
                    NetWorthLine(
                        kind=L.POSITION,
                        net_worth_class=rules.class_of(kind),
                        label=p.name,
                        account_id=account.account_id,
                        account_kind=kind,
                        account_label=account.label,
                        asset_id=p.asset_id,
                        units=p.units,
                        price_eur=None if price is None else price.price_eur,
                        price_source=None if price is None else price.source,
                        price_on=None if price is None else price.on,
                        value_eur=p.value_eur,
                        stale=p.stale,
                        note="Kurs aus dem letzten Bericht" if p.from_report else None,
                        not_valued_reason=p.not_valued_reason,
                    ),
                    account.account_id,
                )
            )
            where = f"{p.name} („{account.label}“)"
            if p.value_eur is None:
                unvalued += 1
                if p.not_valued_reason == E.VALUATION_NOT_VALUED:
                    todos.append(
                        NetWorthTodo(
                            code=T.POSITION_NOT_RECONCILED,
                            text=(
                                f"Der Depotbericht zu {where} stimmt nicht mit den gebuchten "
                                "Umsätzen überein; die Position zählt erst mit, wenn er stimmt."
                            ),
                            action="Zu prüfen öffnen",
                            href="/inbox",
                        )
                    )
                else:
                    coin = p.kind is AssetKind.CRYPTO
                    todos.append(
                        NetWorthTodo(
                            code=T.PRICE_MISSING,
                            text=(
                                f"Für {where} fehlt ein Kurs; die Position zählt noch nicht "
                                "im Nettovermögen."
                            ),
                            action="Kursquelle wählen" if coin else "Kurs eintragen",
                            href="/assets",
                        )
                    )
            elif p.stale and price is not None:
                stale += 1
                todos.append(
                    NetWorthTodo(
                        code=T.PRICE_STALE,
                        text=f"Der Kurs von {where} ist vom {_german(price.on)}.",
                        action="Kurs eintragen",
                        href="/assets",
                    )
                )
    for pending in overview.waiting:  # read, not yet confirmed: counted nowhere (16 Nov 2026)
        if not pending.coins:
            continue  # its waiting transactions change no coin's units
        signs = {coin.units > 0 for coin in pending.coins}
        if signs == {True}:
            after = "Bis dahin fehlen sie im Nettovermögen."
        elif signs == {False}:
            after = "Bis dahin ist das Nettovermögen um diese Abgänge zu hoch."
        else:
            after = "Bis dahin stimmt das Nettovermögen für diese Wallet nicht."
        todos.append(
            NetWorthTodo(
                code=T.WALLET_WAITING,
                text=f"{pending.text} {after}",
                action=pending.action,
                href=pending.href,
            )
        )
    known = {(i.line.account_id, i.line.asset_id) for i in items}
    unbooked, unbooked_todos = _unbooked_funds(conn, on, known, reads)
    return items + unbooked, todos + unbooked_todos, stale, unvalued + len(unbooked)


def _unbooked_funds(
    conn: Connection, on: date, known: set[tuple[UUID | None, UUID | None]], reads: _Reads
) -> tuple[list[_Item], list[NetWorthTodo]]:
    """The funds a depot's latest report on or before `on` lists while no unit of them is
    booked: the Anlagen overview has no position for them (it lists booked units only), so
    without this they would drop out of the figure without a trace (review of 15 Nov 2026,
    finding 1). The disagreement is the holdings card's own (services.depot
    latest_report_checks); the fund is never valued from the report's units (D167 (b)), it
    is listed unvalued with a to-do pointing to that card, where „Umsätze aus dem Bericht
    buchen“ books the report's trades."""
    missing = [
        (report, check)
        for report in report_checks_with(conn, on, reads.movements)
        for check in report.wrong
        if check.listed and (report.account_id, check.asset) not in known
    ]
    if not missing:
        return [], []
    acc, a = account_table.c, asset_table.c
    labels = dict(
        conn.execute(
            select(acc.id, acc.label).where(acc.id.in_({r.account_id for r, _ in missing}))
        )
        .tuples()
        .all()
    )
    names = dict(
        conn.execute(select(a.id, a.name).where(a.id.in_({c.asset for _, c in missing})))
        .tuples()
        .all()
    )
    items: list[_Item] = []
    todos: list[NetWorthTodo] = []
    for report, check in missing:
        label, name = labels[report.account_id], names.get(check.asset, "Wertpapier")
        declared = german_units(check.declared_units)
        items.append(
            _Item(
                NetWorthLine(
                    kind=L.POSITION,
                    net_worth_class=rules.class_of(AccountKind.DEPOT),
                    label=name,
                    account_id=report.account_id,
                    account_kind=AccountKind.DEPOT,
                    account_label=label,
                    asset_id=check.asset,
                    note=(
                        f"laut Depotbericht vom {_german(report.as_of)} {declared} Anteile, "
                        "keiner gebucht"
                    ),
                    not_valued_reason=E.VALUATION_NOT_VALUED,
                ),
                report.account_id,
            )
        )
        todos.append(
            NetWorthTodo(
                code=T.POSITION_NOT_BOOKED,
                text=(
                    f"Der Depotbericht vom {_german(report.as_of)} nennt {declared} Anteile "
                    f"{name} („{label}“), gebucht ist keiner; der Fonds zählt noch nicht im "
                    "Nettovermögen. Die Karte „Depotbestand“ in Zu prüfen bucht die Käufe mit "
                    "„Umsätze aus dem Bericht buchen“ oder nennt die fehlende "
                    "Wertpapierabrechnung."
                ),
                action="Zu prüfen öffnen",
                href="/inbox",
            )
        )
    return items, todos


def _properties(
    conn: Connection, on: date
) -> tuple[list[_Item], list[NetWorthTodo], dict[UUID, dict]]:
    p = property_table.c
    rows = conn.execute(
        select(p.id, p.label, p.purchase_price, p.purchase_date, p.ownership_ended_on).order_by(
            p.label, p.id
        )
    ).all()
    rows = [r for r in rows if r.ownership_ended_on is None or r.ownership_ended_on > on]
    shares = _property_shares(conn, [r.id for r in rows])
    items: list[_Item] = []
    todos: list[NetWorthTodo] = []
    before = [r for r in rows if r.purchase_date > on]
    loans_before = {r.id: list_loans(conn, r.id) for r in before}
    advances, payouts = _paid_before_handover(
        conn,
        [r.id for r in before],
        [loan.id for found in loans_before.values() for loan in found],
        on,
    )
    for prop in rows:
        first = len(todos)  # the shares to-do goes before the loans' (as before D265)
        if prop.purchase_date > on:
            lines = _before_handover(prop, loans_before[prop.id], advances, payouts, on, todos)
        else:
            lines = _from_handover(conn, prop, on, todos)
        items.extend(_Item(line, prop.id) for line in lines)
        if lines and not shares.get(prop.id):
            todos.insert(
                first,
                NetWorthTodo(
                    code=T.PROPERTY_SHARES_MISSING,
                    text=(
                        f"Für „{prop.label}“ sind keine Eigentumsanteile eingetragen; ihr Wert "
                        "zählt im Gesamtwert, aber bei keinem Eigentümer."
                    ),
                    action="Immobilie öffnen",
                    href=f"/properties/{prop.id}",
                ),
            )
    return items, todos, shares


def _from_handover(conn: Connection, prop, on: date, todos: list[NetWorthTodo]) -> list:
    """From the handover on: the estimate and each loan by its schedule (D86, D167 (5))."""
    estimate, scenario = estimate_on_day(conn, prop.id, prop.purchase_price, on)
    levers = scenario.levers if scenario is not None else None
    if levers is not None and levers.today_value is not None:
        note = f"Schätzwert: „Wert heute“ aus dem Szenario „{scenario.name}“ (Planung)"
    else:
        note = (
            "Schätzwert: der Kaufpreis; einen aktuellen Wert unter Planung als "
            "„Wert heute“ eintragen"
        )
    lines = [
        NetWorthLine(
            kind=L.PROPERTY,
            net_worth_class=C.PROPERTY,
            label=prop.label,
            property_id=prop.id,
            value_eur=estimate.value,
            estimate=True,
            note=note,
        )
    ]
    follow_up = {lv.loan_id: lv.follow_up_rate for lv in (levers.loans if levers else [])}
    for loan in list_loans(conn, prop.id):
        terms = loan.terms
        rate = follow_up.get(loan.id)
        if rate is not None:
            terms = terms.model_copy(update={"follow_up_rate": rate})
        line = NetWorthLine(
            kind=L.LOAN,
            net_worth_class=C.LIABILITIES,
            label=loan.lender,
            property_id=prop.id,
            loan_id=loan.id,
            estimate=True,
            note="Restschuld laut Tilgungsplan",
            value_eur=ZERO,
        )
        try:
            entries = build_schedule(terms).entries
        except RuleError as exc:
            todos.append(
                NetWorthTodo(
                    code=T.LOAN_NOT_VALUED,
                    text=(
                        f"Der Tilgungsplan des Darlehens „{loan.lender}“ lässt sich nicht "
                        "berechnen; seine Restschuld fehlt im Nettovermögen."
                    ),
                    action="Darlehen öffnen",
                    href=f"/loans/{loan.id}",
                )
            )
            lines.append(line.model_copy(update={"value_eur": None, "not_valued_reason": exc.code}))
            continue
        if on < terms.disbursed_on:
            continue
        owed = rules.loan_balance(entries, terms.principal, terms.disbursed_on, on)
        lines.append(line.model_copy(update={"value_eur": -owed}))
    return lines


def advance_label(property_label: str) -> str:
    return f"Anzahlung Kaufpreis – {property_label}"


def advance_note(handover: date) -> str:
    return (
        f"Bisher bezahlter Teil des Kaufpreises; zählt bis zur Übergabe am {_german(handover)}, "
        "danach der Schätzwert"
    )


def payout_note(handover: date) -> str:
    return (
        f"Bisher ausgezahlt; bis zur Übergabe am {_german(handover)}, danach Restschuld laut "
        "Tilgungsplan"
    )


def _paid_before_handover(
    conn: Connection, property_ids: Sequence[UUID], loan_ids: Sequence[UUID], on: date
) -> tuple[dict[UUID, Decimal], dict[UUID, Decimal]]:
    """D265 (1), (2): per property, the confirmed allocations booked through `on` that pay its
    price (`purchase_price` on any own account, `hh_investment_property` on a private one,
    each naming the property); per loan, its confirmed payouts (`loan_disbursement` naming it)
    through `on`. A loan with no such payout is absent from the second map."""
    if not property_ids and not loan_ids:
        return {}, {}
    a, t, acc = allocation_table.c, transaction_table.c, account_table.c
    paid = (a.category == AllocationCategory.PURCHASE_PRICE.value) | (
        (a.category == AllocationCategory.HH_INVESTMENT_PROPERTY.value)
        & (acc.purpose == AccountPurpose.PRIVATE.value)
    )
    base = (
        select()
        .select_from(allocation_table)
        .join(transaction_table, t.id == a.transaction_id)
        .join(account_table, acc.id == t.account_id)
        .where(
            a.status == AllocationStatus.CONFIRMED.value,
            t.excluded_as_duplicate_of.is_(None),
            t.booking_date <= on,
        )
    )
    advances = {
        r.property_id: r.total
        for r in conn.execute(
            base.add_columns(a.property_id, func.sum(a.amount).label("total"))
            .where(a.property_id.in_(property_ids), paid)
            .group_by(a.property_id)
        )
    }
    payouts = {
        r.loan_id: r.total
        for r in conn.execute(
            base.add_columns(a.loan_id, func.sum(a.amount).label("total"))
            .where(
                a.loan_id.in_(loan_ids),
                a.category == AllocationCategory.LOAN_DISBURSEMENT.value,
            )
            .group_by(a.loan_id)
        )
    }
    return advances, payouts


def _before_handover(
    prop,
    loans: Sequence,
    advances: dict[UUID, Decimal],
    payouts: dict[UUID, Decimal],
    on: date,
    todos: list[NetWorthTodo],
) -> list[NetWorthLine]:
    """D265: before the handover, „Anzahlung Kaufpreis“ (the price paid so far, capped at the
    price, no line while nothing is paid) and each loan from its payout day by what it paid
    out (capped at its principal); not estimates. The schedule is not read."""
    lines: list[NetWorthLine] = []
    paid = -advances.get(prop.id, ZERO)
    if paid > 0:
        lines.append(
            NetWorthLine(
                kind=L.PROPERTY,
                net_worth_class=C.PROPERTY,
                label=advance_label(prop.label),
                property_id=prop.id,
                value_eur=min(paid, prop.purchase_price),
                estimate=False,
                note=advance_note(prop.purchase_date),
            )
        )
    for loan in loans:
        if on < loan.terms.disbursed_on:
            continue
        line = NetWorthLine(
            kind=L.LOAN,
            net_worth_class=C.LIABILITIES,
            label=loan.lender,
            property_id=prop.id,
            loan_id=loan.id,
            estimate=False,
            note=payout_note(prop.purchase_date),
            value_eur=ZERO,
        )
        if loan.id not in payouts:
            todos.append(
                NetWorthTodo(
                    code=T.LOAN_PAYOUT_MISSING,
                    text=(
                        f"Keine Auszahlung ist mit dem Darlehen „{loan.lender}“ verknüpft; bis zur "
                        "Übergabe fehlt seine Restschuld im Nettovermögen."
                    ),
                    action="Zu prüfen öffnen",
                    href="/inbox",
                )
            )
            lines.append(line.model_copy(update={"value_eur": None}))
            continue
        owed = min(payouts[loan.id], loan.terms.principal)
        lines.append(line.model_copy(update={"value_eur": -owed}))
    return lines


# --- the figure -----------------------------------------------------------------------------


def _figure(
    conn: Connection,
    on: date,
    items: list[_Item],
    todos: list[NetWorthTodo],
    property_shares: dict[UUID, dict],
    *,
    stale: int = 0,
    unvalued: int = 0,
    run: NetWorthRun | None = None,
    ownership: list | None = None,
    owners: list[UUID] | None = None,
) -> NetWorth:
    """`ownership` and `owners`, when given, are the rows `_ownership_rows` and `_owner_ids`
    read for a batch of figures (`month_end_figures`), so each figure reads neither again."""
    accounts = {i.shares_ref for i in items if i.line.kind in (L.ACCOUNT, L.POSITION)}
    if ownership is None:
        account_shares = shares_on(conn, accounts, on)
    else:
        account_shares = _shares_from((r for r in ownership if r.account_id in accounts), on)
    shares = account_shares | property_shares
    parts = [
        rules.Part(
            i.line.net_worth_class, i.line.value_eur, shares.get(i.shares_ref, {}), i.shares_ref
        )
        for i in items
    ]
    result = rules.totals(parts, _owner_ids(conn) if owners is None else owners)
    labels = {i.shares_ref: i.line.account_label for i in items if i.line.account_label}
    for ref in result.unassigned:
        if ref in labels:
            todos.append(
                NetWorthTodo(
                    code=T.OWNERSHIP_MISSING,
                    text=(
                        f"Für das Konto „{labels[ref]}“ sind keine Eigentümer eingetragen; sein "
                        "Betrag zählt im Gesamtwert, aber bei keinem Eigentümer."
                    ),
                    action="Eigentümer festlegen",
                    href=f"/accounts/{ref}",
                )
            )
    estimate = sum(
        (i.line.value_eur for i in items if i.line.kind is L.PROPERTY and i.line.value_eur),
        ZERO,
    )
    loans = -sum(
        (i.line.value_eur for i in items if i.line.kind is L.LOAN and i.line.value_eur),
        ZERO,
    )
    return NetWorth(
        on=on,
        total=result.total,
        by_class=result.by_class,
        by_owner=result.by_owner,
        unassigned=[r for r in result.unassigned if isinstance(r, UUID)],
        property_estimate=estimate,
        loan_balances=loans,
        property_net=estimate - loans,
        lines=[i.line for i in items],
        todos=todos,
        stale_positions=stale,
        unvalued_positions=unvalued,
        run=run,
    )


def net_worth(conn: Connection, on: date) -> NetWorth:
    """GET /net-worth?on=: NetWorth(on) on read."""
    return _net_worth(conn, on, _Reads(conn))


def _net_worth(conn: Connection, on: date, reads: _Reads) -> NetWorth:
    balances, balance_todos = _balances(conn, on, reads)
    positions, position_todos, stale, unvalued = _positions(conn, on, reads)
    properties, property_todos, property_shares = _properties(conn, on)
    return _figure(
        conn,
        on,
        balances + positions + properties,
        balance_todos + position_todos + property_todos,
        property_shares,
        stale=stale,
        unvalued=unvalued,
    )


# --- month-ends -----------------------------------------------------------------------------


def _runs(
    conn: Connection, *, as_of: date | None = None, run_id: UUID | None = None
) -> list[NetWorthRun]:
    """The stored runs, the newest month-end first and within one month-end the newest
    capture first."""
    s = snapshot_table.c
    query = select(
        s.net_worth_run_id,
        s.as_of,
        func.min(s.created_at).label("captured_at"),
        func.count().label("rows"),
    ).group_by(s.net_worth_run_id, s.as_of)
    if as_of is not None:
        query = query.where(s.as_of == as_of)
    if run_id is not None:
        query = query.where(s.net_worth_run_id == run_id)
    rows = conn.execute(
        query.order_by(s.as_of.desc(), func.min(s.created_at).desc(), s.net_worth_run_id)
    ).all()
    return [
        NetWorthRun(
            net_worth_run_id=r.net_worth_run_id,
            as_of=r.as_of,
            captured_at=r.captured_at.astimezone(UTC),
            rows=r.rows,
        )
        for r in rows
    ]


def _latest_run(conn: Connection, as_of: date | None = None) -> NetWorthRun | None:
    """The newest run of `as_of`, or of the latest month-end stored when None."""
    runs = _runs(conn, as_of=as_of)
    return runs[0] if runs else None


def latest_run(conn: Connection, as_of: date) -> NetWorthRun | None:
    """The newest stored run of the month-end `as_of`, None when none holds it (phase 9:
    a health_snapshot is stored beside it, D242)."""
    return _latest_run(conn, as_of)


def _stored_columns() -> list:
    s = snapshot_table.c
    return [
        s.account_id,
        s.asset_id,
        s.property_id,
        s.loan_id,
        s.units,
        s.price_eur,
        s.price_source,
        s.value_eur,
    ]


def _stored_order() -> list:
    s = snapshot_table.c
    return [
        s.account_id.nulls_last(),
        s.asset_id.nulls_first(),
        s.property_id.nulls_last(),
        s.loan_id.nulls_last(),
    ]


def _stored(conn: Connection, run_id: UUID) -> list:
    s = snapshot_table.c
    return conn.execute(
        select(*_stored_columns()).where(s.net_worth_run_id == run_id).order_by(*_stored_order())
    ).all()


def _stored_by_run(conn: Connection, run_ids: Sequence[UUID]) -> dict[UUID, list]:
    """Each run's rows in one query, every run's rows in the order `_stored` reads them."""
    if not run_ids:
        return {}
    s = snapshot_table.c
    rows = conn.execute(
        select(s.net_worth_run_id, *_stored_columns())
        .where(s.net_worth_run_id.in_(run_ids))
        .order_by(s.net_worth_run_id, *_stored_order())
    ).all()
    found: dict[UUID, list] = defaultdict(list)
    for r in rows:
        found[r.net_worth_run_id].append(r)
    return dict(found)


def _row_key(
    account_id, asset_id, property_id, loan_id, units, price_eur, price_source, value_eur
) -> tuple:
    return (account_id, asset_id, property_id, loan_id, units, price_eur, price_source, value_eur)


def _line_key(line: NetWorthLine) -> tuple:
    """A valued line as its row: an account's balance or position by its account, a property
    by itself, a loan by itself alone (its line also names its property, for the shares)."""
    loan = line.kind is L.LOAN
    return _row_key(
        line.account_id if line.kind in (L.ACCOUNT, L.POSITION) else None,
        line.asset_id,
        line.property_id if line.kind is L.PROPERTY else None,
        line.loan_id if loan else None,
        line.units,
        line.price_eur,
        None if line.price_source is None else line.price_source.value,
        line.value_eur,
    )


STORED_PROPERTY_NOTE = "Schätzwert, zum Monatsende gespeichert"
STORED_LOAN_NOTE = "Restschuld laut Tilgungsplan, zum Monatsende gespeichert"


def month_end_figure(conn: Connection, run: NetWorthRun) -> NetWorth:
    """A stored month-end, from its rows alone (see the module doc)."""
    return month_end_figures(conn, [run])[0]


def month_end_figures(conn: Connection, runs: Sequence[NetWorthRun]) -> list[NetWorth]:
    """`month_end_figure` of each run, in the order given, with one read of the rows and the
    records they name for all the runs (the Übersicht's series reads every month-end)."""
    if not runs:
        return []
    by_run = _stored_by_run(conn, list(dict.fromkeys(r.net_worth_run_id for r in runs)))
    every = [r for rows in by_run.values() for r in rows]
    acc, a, p, ln = account_table.c, asset_table.c, property_table.c, loan_table.c
    account_ids = {r.account_id for r in every if r.account_id is not None}
    accounts = (
        {
            r.id: r
            for r in conn.execute(
                select(acc.id, acc.label, acc.kind).where(acc.id.in_(account_ids))
            )
        }
        if account_ids
        else {}
    )
    asset_ids = {r.asset_id for r in every if r.asset_id is not None}
    assets = (
        {r.id: r.name for r in conn.execute(select(a.id, a.name).where(a.id.in_(asset_ids)))}
        if asset_ids
        else {}
    )
    loan_ids = {r.loan_id for r in every if r.loan_id is not None}
    loans = (
        {
            r.id: r
            for r in conn.execute(
                select(ln.id, ln.lender, ln.property_id).where(ln.id.in_(loan_ids))
            )
        }
        if loan_ids
        else {}
    )
    all_property_ids = {r.property_id for r in every if r.property_id is not None} | {
        loan.property_id for loan in loans.values()
    }
    properties = (
        {
            r.id: r
            for r in conn.execute(
                select(p.id, p.label, p.purchase_date).where(p.id.in_(all_property_ids))
            )
        }
        if all_property_ids
        else {}
    )
    property_shares = _property_shares(conn, sorted(all_property_ids))
    ownership = _ownership_rows(conn, account_ids)
    owners = _owner_ids(conn)
    return [
        _month_end(
            conn,
            run,
            by_run.get(run.net_worth_run_id, []),
            accounts,
            assets,
            loans,
            properties,
            property_shares,
            ownership,
            owners,
        )
        for run in runs
    ]


def _month_end(
    conn: Connection,
    run: NetWorthRun,
    rows: list,
    accounts: dict,
    assets: dict,
    loans: dict,
    properties: dict,
    all_property_shares: dict[UUID, dict],
    ownership: list,
    owners: list[UUID],
) -> NetWorth:
    """One run's figure from the records `month_end_figures` read."""
    run_loans = {r.loan_id for r in rows if r.loan_id is not None}
    property_ids = {r.property_id for r in rows if r.property_id is not None} | {
        loans[loan_id].property_id for loan_id in run_loans if loan_id in loans
    }
    items = []
    for r in rows:
        if r.property_id is not None:
            prop = properties[r.property_id]
            # D265 (4): a run before the handover holds the price paid so far, no estimate.
            before = run.as_of < prop.purchase_date
            line = NetWorthLine(
                kind=L.PROPERTY,
                net_worth_class=C.PROPERTY,
                label=advance_label(prop.label) if before else prop.label,
                property_id=r.property_id,
                value_eur=r.value_eur,
                estimate=not before,
                note=(
                    f"{advance_note(prop.purchase_date)}; zum Monatsende gespeichert"
                    if before
                    else STORED_PROPERTY_NOTE
                ),
            )
            items.append(_Item(line, r.property_id))
            continue
        if r.loan_id is not None:
            loan = loans[r.loan_id]
            handover = properties[loan.property_id].purchase_date
            before = run.as_of < handover
            line = NetWorthLine(
                kind=L.LOAN,
                net_worth_class=C.LIABILITIES,
                label=loan.lender,
                property_id=loan.property_id,
                loan_id=r.loan_id,
                value_eur=r.value_eur,
                estimate=not before,
                note=(
                    f"{payout_note(handover)}; zum Monatsende gespeichert"
                    if before
                    else STORED_LOAN_NOTE
                ),
            )
            items.append(_Item(line, loan.property_id))
            continue
        account = accounts[r.account_id]
        kind = AccountKind(account.kind)
        items.append(
            _Item(
                NetWorthLine(
                    kind=L.ACCOUNT if r.asset_id is None else L.POSITION,
                    net_worth_class=rules.class_of(kind),
                    label=account.label if r.asset_id is None else assets[r.asset_id],
                    account_id=r.account_id,
                    account_kind=kind,
                    account_label=account.label,
                    asset_id=r.asset_id,
                    units=r.units,
                    price_eur=r.price_eur,
                    price_source=(
                        None if r.price_source is None else PriceSourceKind(r.price_source)
                    ),
                    value_eur=r.value_eur,
                ),
                r.account_id,
            )
        )
    property_shares = {k: v for k, v in all_property_shares.items() if k in property_ids}
    return _figure(
        conn,
        run.as_of,
        items,
        [],
        property_shares,
        run=run,
        ownership=ownership,
        owners=owners,
    )


def latest_month_end(conn: Connection) -> NetWorth | None:
    run = _latest_run(conn)
    return None if run is None else month_end_figure(conn, run)


def overview(conn: Connection, today: date) -> NetWorthOverview:
    """GET /net-worth/overview: today's figure, the latest stored month-end, and the last
    month-end when no run holds it yet. When the stored month-end is the last one (the day
    the button stores), its day is read again on read, and `month_end_now` carries that
    total when it differs from the stored one (a statement or a price that arrived after the
    capture; review of 15 Nov 2026, findings 2 and 3), so the card offers „Monatsabschluss
    neu speichern“. The property's estimate is the default scenario standing at the end of
    that day (phase 9 D241, services.scenarios.estimate_on_day), so a „Wert heute“ recorded
    after it does not move it; a loan's balance is read from the loan's terms as they are now
    (with that scenario's follow-up rate), so a change of those does show as a difference."""
    due = last_month_end(today)
    stored = latest_month_end(conn)
    reads = _Reads(conn)  # both days read the movements, idle cards and history once
    now = None
    if stored is not None and stored.on == due:
        current = _net_worth(conn, due, reads).total
        now = current if current != stored.total else None
    return NetWorthOverview(
        today=_net_worth(conn, today, reads),
        month_end=stored,
        month_end_due=None if _latest_run(conn, due) is not None else due,
        month_end_now=now,
    )


def list_runs(conn: Connection) -> list[NetWorthRun]:
    """GET /net-worth/snapshots: every run, the newest month-end first."""
    return _runs(conn)


def stored_run(conn: Connection, run_id: UUID) -> NetWorth | None:
    """GET /net-worth/snapshots/{id}: one stored run, None when unknown."""
    runs = _runs(conn, run_id=run_id)
    return month_end_figure(conn, runs[0]) if runs else None


def _lock(ctx: CommandContext) -> None:
    """One capture at a time per workspace (the table's unique key is per run, so two
    concurrent captures would otherwise both write)."""
    key = func.hashtextextended(f"net_worth:{ctx.workspace_id}", 0)
    ctx.conn.execute(select(func.pg_advisory_xact_lock(key)))


def capture_month_end(ctx: CommandContext, as_of: date) -> SnapshotResult:
    """Store `as_of`'s figure as a run (see the module doc): the button and the periodic task
    alike, a new run only when the figures differ from the newest stored one. Any month-end
    from 31 Jan 2026 to the last one before today (phase 9 D241), else RuleViolationError."""
    from .documents import LOCAL_TIME_ZONE

    today = ctx.now.astimezone(LOCAL_TIME_ZONE).date()
    require_valid(rules.month_end_codes(as_of, today), "not a month-end that can be stored")
    _lock(ctx)
    existing = _latest_run(ctx.conn, as_of)
    figure = net_worth(ctx.conn, as_of)
    # D292 (3): money in transit is stored inside its receiving account's balance row.
    rows = [_line_key(line) for line in merge_transit(figure.lines) if line.value_eur is not None]
    if not rows:
        return SnapshotResult(
            outcome=SnapshotOutcome.EMPTY, as_of=as_of, unvalued_positions=figure.unvalued_positions
        )
    if existing is not None:
        stored = {_row_key(*r) for r in _stored(ctx.conn, existing.net_worth_run_id)}
        if stored == set(rows):
            return SnapshotResult(
                outcome=SnapshotOutcome.UNCHANGED,
                as_of=as_of,
                run=existing,
                unvalued_positions=figure.unvalued_positions,
            )
    run_id = uuid4()
    ctx.conn.execute(
        insert(snapshot_table),
        [
            {
                "workspace_id": ctx.workspace_id,
                "account_id": account_id,
                "asset_id": asset_id,
                "property_id": property_id,
                "loan_id": loan_id,
                "as_of": as_of,
                "units": units,
                "price_eur": price_eur,
                "price_source": price_source,
                "value_eur": value_eur,
                "net_worth_run_id": run_id,
                "created_at": ctx.now,
            }
            for (
                account_id,
                asset_id,
                property_id,
                loan_id,
                units,
                price_eur,
                price_source,
                value_eur,
            ) in rows
        ],
    )
    append_event(
        ctx,
        EventType.WORKSPACE_VALUATION_SNAPSHOT_WRITTEN,
        ctx.workspace_id,
        ValuationSnapshotWritten(net_worth_run_id=run_id, as_of=as_of, rows=len(rows)),
    )
    return SnapshotResult(
        outcome=SnapshotOutcome.WRITTEN if existing is None else SnapshotOutcome.RECAPTURED,
        as_of=as_of,
        run=NetWorthRun(
            net_worth_run_id=run_id,
            as_of=as_of,
            captured_at=ctx.now.astimezone(UTC),
            rows=len(rows),
        ),
        unvalued_positions=figure.unvalued_positions,
    )


def capture_last_month_end(ctx: CommandContext, as_of: date | None = None) -> SnapshotResult:
    """POST /net-worth/snapshots („Monatsabschluss festhalten“): `as_of` when given (a past
    month-end, phase 9 D241), else the last month-end before today (Berlin time, as the
    screen's day)."""
    from .documents import LOCAL_TIME_ZONE

    if as_of is None:
        as_of = last_month_end(ctx.now.astimezone(LOCAL_TIME_ZONE).date())
    return capture_month_end(ctx, as_of)


__all__ = [
    "capture_last_month_end",
    "capture_month_end",
    "last_month_end",
    "latest_month_end",
    "latest_run",
    "list_runs",
    "merge_transit",
    "month_end_figure",
    "month_end_figures",
    "net_worth",
    "overview",
    "shares_on",
    "stored_run",
    "transit_label",
    "transit_note",
]
