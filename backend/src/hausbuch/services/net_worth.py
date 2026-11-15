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
  import's card). Without any reconciled import the balance is the anchor. An account whose
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
  today's year by the default scenario in force (its „Wert heute“, else the purchase price;
  rules.performance.projection.value_today, D86), an estimate. **Its loans** from the payout,
  by the repayment schedule (the scenario's follow-up rate where it sets one, as the
  projection does), an estimate too.
- **Owners**: an account's figures by its account_ownership set in force on `on` (the newest
  recorded set whose effective_from is on or before `on`), the property and its loans by
  the property's shares; split to the cent by rules.net_worth.split.

**Month-ends (D167 (5) amended, migration 0040).** `capture_month_end` writes one
valuation_snapshot row per valued figure, all under one net_worth_run_id: per account balance
and per position (asset null for a balance), per property (its estimate) and per loan (its
balance, negative), and the event workspace.valuation_snapshot_written. The table is
immutable, so a run is never changed. The periodic task `month_end` (1st, 08:00 UTC, after
the 06:30 bank fetch and the 07:00 price fetch) writes a month-end only when no run holds it
yet. „Monatsabschluss festhalten“ writes a new run for the same month-end when the figures
read now differ from the newest stored run (a statement or a price that arrived late), and
nothing when they are the same; the newest run of a month-end is the one shown, the older
runs stay. Captures of one workspace run one at a time (an advisory lock).

A stored month-end is read from its rows alone: every euro figure, the classes and the total
are the stored values, never recomputed (a later „Wert heute“ or loan change leaves it as
it was). Only what the rows do not hold is looked up: the labels, and the owner split by the
account shares in force on the month-end and the property's shares.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from uuid import UUID, uuid4

from sqlalchemy import Connection, func, insert, select

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import account_ownership as ownership_table
from hausbuch.db.tables import asset as asset_table
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
from hausbuch.rules.performance.projection import value_today
from hausbuch.schemas.enums import BANK_ACCOUNT_KINDS, AccountKind, ImportStatus
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
from .depot import _german_units as german_units
from .depot import latest_report_checks
from .events import append_event
from .loans import list_loans
from .prices.overview import assets_overview
from .scenarios import default_scenario

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
    after `on` waits for its day. An account without such a set is missing."""
    ids = list(account_ids)
    if not ids:
        return {}
    o = ownership_table.c
    rows = conn.execute(
        select(
            o.account_id,
            o.owner_id,
            o.share_numerator,
            o.share_denominator,
            o.effective_from,
            o.created_at,
        )
        .where(o.account_id.in_(ids), o.effective_from <= on)
        .order_by(o.account_id, o.created_at.desc(), o.effective_from.desc())
    ).all()
    found: dict[UUID, dict[UUID, Fraction]] = {}
    chosen: dict[UUID, tuple[datetime, date]] = {}
    for r in rows:
        key = (r.created_at, r.effective_from)
        if chosen.setdefault(r.account_id, key) != key:
            continue
        found.setdefault(r.account_id, {})[r.owner_id] = Fraction(
            r.share_numerator, r.share_denominator
        )
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


def _balances(conn: Connection, on: date) -> tuple[list[_Item], list[NetWorthTodo]]:
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
    items: list[_Item] = []
    todos: list[NetWorthTodo] = []
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
                    stale=(on - through).days > valuation.STALE_AFTER_DAYS,
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
    return items, todos


def _positions(conn: Connection, on: date) -> tuple[list[_Item], list[NetWorthTodo], int, int]:
    overview = assets_overview(conn, on)
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
    known = {(i.line.account_id, i.line.asset_id) for i in items}
    unbooked, unbooked_todos = _unbooked_funds(conn, on, known)
    return items + unbooked, todos + unbooked_todos, stale, unvalued + len(unbooked)


def _unbooked_funds(
    conn: Connection, on: date, known: set[tuple[UUID | None, UUID | None]]
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
        for report in latest_report_checks(conn, on)
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
        select(p.id, p.label, p.purchase_price, p.purchase_date, p.ownership_ended_on)
        .where(p.purchase_date <= on)
        .order_by(p.label, p.id)
    ).all()
    rows = [r for r in rows if r.ownership_ended_on is None or r.ownership_ended_on > on]
    shares = _property_shares(conn, [r.id for r in rows])
    items: list[_Item] = []
    todos: list[NetWorthTodo] = []
    for prop in rows:
        scenario = default_scenario(conn, prop.id)
        levers = scenario.levers if scenario is not None else None
        if levers is not None and levers.today_value is not None:
            note = f"Schätzwert: „Wert heute“ aus dem Szenario „{scenario.name}“ (Planung)"
        else:
            note = (
                "Schätzwert: der Kaufpreis; einen aktuellen Wert unter Planung als "
                "„Wert heute“ eintragen"
            )
        items.append(
            _Item(
                NetWorthLine(
                    kind=L.PROPERTY,
                    net_worth_class=C.PROPERTY,
                    label=prop.label,
                    property_id=prop.id,
                    value_eur=value_today(levers, prop.purchase_price),
                    estimate=True,
                    note=note,
                ),
                prop.id,
            )
        )
        if not shares.get(prop.id):
            todos.append(
                NetWorthTodo(
                    code=T.PROPERTY_SHARES_MISSING,
                    text=(
                        f"Für „{prop.label}“ sind keine Eigentumsanteile eingetragen; ihr Wert "
                        "zählt im Gesamtwert, aber bei keinem Eigentümer."
                    ),
                    action="Immobilie öffnen",
                    href=f"/properties/{prop.id}",
                )
            )
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
                line = line.model_copy(update={"value_eur": None, "not_valued_reason": exc.code})
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
                items.append(_Item(line, prop.id))
                continue
            if on < terms.disbursed_on:
                continue
            owed = rules.loan_balance(entries, terms.principal, terms.disbursed_on, on)
            items.append(_Item(line.model_copy(update={"value_eur": -owed}), prop.id))
    return items, todos, shares


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
) -> NetWorth:
    accounts = {i.shares_ref for i in items if i.line.kind in (L.ACCOUNT, L.POSITION)}
    shares = shares_on(conn, accounts, on) | property_shares
    parts = [
        rules.Part(
            i.line.net_worth_class, i.line.value_eur, shares.get(i.shares_ref, {}), i.shares_ref
        )
        for i in items
    ]
    result = rules.totals(parts, _owner_ids(conn))
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
    balances, balance_todos = _balances(conn, on)
    positions, position_todos, stale, unvalued = _positions(conn, on)
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


def _stored(conn: Connection, run_id: UUID) -> list:
    s = snapshot_table.c
    return conn.execute(
        select(
            s.account_id,
            s.asset_id,
            s.property_id,
            s.loan_id,
            s.units,
            s.price_eur,
            s.price_source,
            s.value_eur,
        )
        .where(s.net_worth_run_id == run_id)
        .order_by(
            s.account_id.nulls_last(),
            s.asset_id.nulls_first(),
            s.property_id.nulls_last(),
            s.loan_id.nulls_last(),
        )
    ).all()


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
    rows = _stored(conn, run.net_worth_run_id)
    acc, a, p, ln = account_table.c, asset_table.c, property_table.c, loan_table.c
    account_ids = {r.account_id for r in rows if r.account_id is not None}
    accounts = {
        r.id: r
        for r in conn.execute(select(acc.id, acc.label, acc.kind).where(acc.id.in_(account_ids)))
    }
    asset_ids = {r.asset_id for r in rows if r.asset_id is not None}
    assets = {r.id: r.name for r in conn.execute(select(a.id, a.name).where(a.id.in_(asset_ids)))}
    loans = {
        r.id: r
        for r in conn.execute(
            select(ln.id, ln.lender, ln.property_id).where(
                ln.id.in_({r.loan_id for r in rows if r.loan_id is not None})
            )
        )
    }
    property_ids = {r.property_id for r in rows if r.property_id is not None} | {
        loan.property_id for loan in loans.values()
    }
    properties = {
        r.id: r.label for r in conn.execute(select(p.id, p.label).where(p.id.in_(property_ids)))
    }
    items = []
    for r in rows:
        if r.property_id is not None:
            line = NetWorthLine(
                kind=L.PROPERTY,
                net_worth_class=C.PROPERTY,
                label=properties[r.property_id],
                property_id=r.property_id,
                value_eur=r.value_eur,
                estimate=True,
                note=STORED_PROPERTY_NOTE,
            )
            items.append(_Item(line, r.property_id))
            continue
        if r.loan_id is not None:
            loan = loans[r.loan_id]
            line = NetWorthLine(
                kind=L.LOAN,
                net_worth_class=C.LIABILITIES,
                label=loan.lender,
                property_id=loan.property_id,
                loan_id=r.loan_id,
                value_eur=r.value_eur,
                estimate=True,
                note=STORED_LOAN_NOTE,
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
    return _figure(
        conn, run.as_of, items, [], _property_shares(conn, sorted(property_ids)), run=run
    )


def latest_month_end(conn: Connection) -> NetWorth | None:
    run = _latest_run(conn)
    return None if run is None else month_end_figure(conn, run)


def overview(conn: Connection, today: date) -> NetWorthOverview:
    """GET /net-worth/overview: today's figure, the latest stored month-end, and the last
    month-end when no run holds it yet."""
    due = last_month_end(today)
    return NetWorthOverview(
        today=net_worth(conn, today),
        month_end=latest_month_end(conn),
        month_end_due=None if _latest_run(conn, due) is not None else due,
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


def capture_month_end(
    ctx: CommandContext, as_of: date, *, only_if_missing: bool = False
) -> SnapshotResult:
    """Store `as_of`'s figure as a run (see the module doc). `only_if_missing`: the periodic
    task's mode, which never writes a month-end that has a run already."""
    _lock(ctx)
    existing = _latest_run(ctx.conn, as_of)
    if existing is not None and only_if_missing:
        return SnapshotResult(outcome=SnapshotOutcome.UNCHANGED, as_of=as_of, run=existing)
    figure = net_worth(ctx.conn, as_of)
    rows = [_line_key(line) for line in figure.lines if line.value_eur is not None]
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


def capture_last_month_end(ctx: CommandContext) -> SnapshotResult:
    """POST /net-worth/snapshots („Monatsabschluss festhalten“): the last month-end before
    today (Berlin time, as the screen's day)."""
    from .documents import LOCAL_TIME_ZONE

    today = ctx.now.astimezone(LOCAL_TIME_ZONE).date()
    return capture_month_end(ctx, last_month_end(today))


__all__ = [
    "capture_last_month_end",
    "capture_month_end",
    "last_month_end",
    "latest_month_end",
    "list_runs",
    "month_end_figure",
    "net_worth",
    "overview",
    "shares_on",
    "stored_run",
]
