"""Phase 5 reminders on read (docs/phases/phase-5.md §6, §10; D122, D126).

- `facts(conn)` assembles ReminderFacts from the stored data and the existing services: the
  checklist's facts per property (`checklist_facts`, so `build_checklist` decides every
  `checklist.due` and the Bescheid's presence), the `grundsteuer_schedule` tax inputs in force,
  the confirmed `grundsteuer` and `rent_cold` allocations, the accounts' reconciled coverage
  and connections, the owners' pay and certificate years, the questionnaire's progress
  (`questionnaire_progress`) and the `reminder_ack` rows. No date is computed here: every due
  day comes from `rules.reminders.build_reminders`.
- `list_reminders(conn, today, from_, to)` is GET /reminders: the computed list, optionally
  kept to due days in [from_, to].
- `acknowledge(ctx, reminder_id, body)` is POST /reminders/{id}/ack (§6.3): one append-only
  `reminder_ack` row and the event `workspace.reminder_acknowledged`. The id names a kind, a
  scope and a due day; the scope must be a row of this workspace (row-level security hides
  other workspaces' rows) of a type the kind uses. The instance need not be in today's list:
  a `done` for a due day that is not computed (yet, or any more) hides nothing and harms
  nothing, and a `muted` row for a kind and scope stops instances that only appear later.
- `reminder_inbox_items(conn, today)` gives the Zu prüfen cards (group "Automatisch"): every
  `action` reminder visible today, except `tax.questionnaire_open`, whose card phase 4b D110
  already shows (the reminder adds only the email, §6.2).

A reminder never carries an amount or a tenant's name (D5): the facts hold unit labels, not
tenants, and rent payments by service period, not amount.
"""

from collections import defaultdict
from datetime import UTC, date, datetime, time
from uuid import UUID

from sqlalchemy import Connection, Table, and_, func, insert, or_, select

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import bank_connection as connection_table
from hausbuch.db.tables import loan as loan_table
from hausbuch.db.tables import owner as owner_table
from hausbuch.db.tables import property_ as property_table
from hausbuch.db.tables import reminder_ack as ack_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import tenancy as tenancy_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.db.tables import unit as unit_table
from hausbuch.rules.errors import REMINDER_SCOPE_INVALID, REMINDER_UNKNOWN, RuleError
from hausbuch.rules.reminders import KIND_SCOPES, build_reminders, parse_reminder_id
from hausbuch.rules.statements import is_direct_debit
from hausbuch.schemas.allocation import AllocationCategory, AllocationStatus
from hausbuch.schemas.api import InboxItem, InboxItemKind
from hausbuch.schemas.enums import (
    ImportStatus,
    IncomeInputKind,
    OperatingCostsTerms,
    ReturnInputKind,
)
from hausbuch.schemas.events import EventType, ReminderAcknowledged
from hausbuch.schemas.reminders import (
    Reminder,
    ReminderAccount,
    ReminderAck,
    ReminderAckAction,
    ReminderAckCreate,
    ReminderFacts,
    ReminderGrundsteuerPayment,
    ReminderGrundsteuerSchedule,
    ReminderKind,
    ReminderLoan,
    ReminderOwner,
    ReminderProperty,
    ReminderRentPayment,
    ReminderScope,
    ReminderTaxYear,
    ReminderTenancy,
)
from hausbuch.schemas.tax import GrundsteuerSchedule, TaxInputKind

from .allocations import user_id
from .checklist import checklist_facts
from .context import CommandContext
from .documents import LOCAL_TIME_ZONE
from .errors import RuleViolationError
from .events import append_event
from .income_inputs import list_income_inputs
from .properties import list_properties
from .return_inputs import list_return_inputs
from .tax_inputs import list_tax_inputs

_CONFIRMED = AllocationStatus.CONFIRMED.value
_SCOPE_TABLES: dict[ReminderScope, Table] = {
    ReminderScope.PROPERTY: property_table,
    ReminderScope.LOAN: loan_table,
    ReminderScope.TENANCY: tenancy_table,
    ReminderScope.OWNER: owner_table,
    ReminderScope.ACCOUNT: account_table,
}
# A tax year's return is due in the summer after it; two years back covers the floor of
# build_reminders (1 January of last year), so older years cannot produce an instance.
_TAX_YEARS_BACK = 2


# --- Facts (§6.1) ---------------------------------------------------------------------------


def facts(conn: Connection, today: date) -> ReminderFacts:
    """Everything build_reminders reads, from this workspace's rows (RLS)."""
    properties = [_property(conn, p) for p in list_properties(conn)]
    owners = _owners(conn)
    first_years = [p.purchase_date.year for p in properties] + [
        y for o in owners for y in o.pay_years
    ]
    return ReminderFacts(
        properties=properties,
        loans=_loans(conn),
        tenancies=_tenancies(conn),
        owners=owners,
        accounts=_accounts(conn),
        tax_years=_tax_years(conn, today, min(first_years) if first_years else None),
        acks=_acks(conn),
    )


def _property(conn: Connection, prop) -> ReminderProperty:
    checklist, _ = checklist_facts(conn, prop.id)
    schedules = []
    for row in list_tax_inputs(conn, prop.id, kind=TaxInputKind.GRUNDSTEUER_SCHEDULE):
        value = row.value
        assert isinstance(value, GrundsteuerSchedule)
        schedules.append(
            ReminderGrundsteuerSchedule(
                id=row.id,
                tax_year=row.tax_year,
                instalments=value.instalments,
                payment_mode=value.payment_mode,
            )
        )
    return ReminderProperty(
        id=prop.id,
        label=prop.label,
        purchase_date=prop.purchase_date,
        notarized_on=prop.notarized_on,
        ownership_ended_on=prop.ownership_ended_on,
        checklist=checklist,
        grundsteuer_schedules=schedules,
        grundsteuer_payments=_grundsteuer_payments(conn, prop.id),
    )


def _grundsteuer_payments(conn: Connection, property_id: UUID) -> list[ReminderGrundsteuerPayment]:
    """The property's confirmed `grundsteuer` allocations (and, as the Anlage V draft reads
    them, those without a property on an account that carries the property's allocations),
    with whether their bank line is a SEPA direct debit (D126: only that infers the mode)."""
    a, t = allocation_table.c, transaction_table.c
    accounts = (
        select(t.account_id)
        .select_from(transaction_table.join(allocation_table, a.transaction_id == t.id))
        .where(a.property_id == property_id, a.status == _CONFIRMED)
        .distinct()
        .scalar_subquery()
    )
    rows = conn.execute(
        select(a.id, a.amount, t.booking_date, t.raw)
        .select_from(allocation_table.join(transaction_table, t.id == a.transaction_id))
        .where(
            a.status == _CONFIRMED,
            a.category == AllocationCategory.GRUNDSTEUER.value,
            a.amount != 0,
            t.excluded_as_duplicate_of.is_(None),
            or_(
                a.property_id == property_id,
                and_(a.property_id.is_(None), t.account_id.in_(accounts)),
            ),
        )
        .order_by(t.booking_date, a.id)
    )
    return [
        ReminderGrundsteuerPayment(
            id=r.id,
            booking_date=r.booking_date,
            amount=r.amount,
            direct_debit=is_direct_debit(r.raw),
        )
        for r in rows
    ]


def _loans(conn: Connection) -> list[ReminderLoan]:
    c = loan_table.c
    rows = conn.execute(select(c.id, c.lender, c.disbursed_on, c.fixed_until).order_by(c.id))
    return [ReminderLoan.model_validate(dict(r._mapping)) for r in rows]


def _tenancies(conn: Connection) -> list[ReminderTenancy]:
    """Every tenancy with its unit's label (never a tenant) and its confirmed cold-rent
    allocations by service period; a rent line booked without one counts for its booking
    month (§6.2: the reminder asks whether rent arrived, never how much)."""
    t, u = tenancy_table.c, unit_table.c
    tenancies = conn.execute(
        select(
            t.id,
            u.property_id,
            u.label.label("unit_label"),
            t.start_date,
            t.end_date,
            t.operating_costs_terms,
            t.rent_due_day,
        )
        .select_from(tenancy_table.join(unit_table, u.id == t.unit_id))
        .order_by(t.id)
    ).all()
    a, tx = allocation_table.c, transaction_table.c
    payments: dict[UUID, list[ReminderRentPayment]] = defaultdict(list)
    for r in conn.execute(
        select(a.id, a.tenancy_id, a.service_period_start, a.service_period_end, tx.booking_date)
        .select_from(allocation_table.join(transaction_table, tx.id == a.transaction_id))
        .where(
            a.status == _CONFIRMED,
            a.category == AllocationCategory.RENT_COLD.value,
            a.tenancy_id.is_not(None),
            tx.excluded_as_duplicate_of.is_(None),
        )
        .order_by(a.id)
    ):
        start, end = r.service_period_start, r.service_period_end
        if start is None or end is None:
            start = r.booking_date.replace(day=1)
            end = _month_end(start)
        payments[r.tenancy_id].append(
            ReminderRentPayment(id=r.id, service_period_start=start, service_period_end=end)
        )
    return [
        ReminderTenancy(
            id=r.id,
            property_id=r.property_id,
            unit_label=r.unit_label,
            start_date=r.start_date,
            end_date=r.end_date,
            operating_costs_terms=OperatingCostsTerms(r.operating_costs_terms),
            rent_due_day=r.rent_due_day,
            rent_payments=payments[r.id],
        )
        for r in tenancies
    ]


def _month_end(first: date) -> date:
    following = date(first.year + first.month // 12, first.month % 12 + 1, 1)
    return date.fromordinal(following.toordinal() - 1)


def _owners(conn: Connection) -> list[ReminderOwner]:
    """Per owner, the years with a payslip or an expected gross pay, and the years with a
    wage certificate (current rows only)."""
    pay: dict[UUID, set[int]] = defaultdict(set)
    certificates: dict[UUID, set[int]] = defaultdict(set)
    for row in list_return_inputs(conn):
        if row.owner_id is None:
            continue
        if row.kind is ReturnInputKind.PAYSLIP:
            pay[row.owner_id].add(row.tax_year)
        elif row.kind is ReturnInputKind.WAGE_CERTIFICATE:
            certificates[row.owner_id].add(row.tax_year)
    for row in list_income_inputs(conn):
        if row.owner_id is not None and row.kind is IncomeInputKind.EXPECTED_GROSS_PAY:
            pay[row.owner_id].add(row.tax_year)
    ids = conn.execute(select(owner_table.c.id).order_by(owner_table.c.id)).scalars()
    return [
        ReminderOwner(
            id=i,
            pay_years=sorted(pay[i]),
            wage_certificate_years=sorted(certificates[i]),
        )
        for i in ids
    ]


def _accounts(conn: Connection) -> list[ReminderAccount]:
    """Each account with the last booking day of its reconciled imports and its connection's
    first day and last success (days in Europe/Berlin)."""
    a, s, b = account_table.c, import_table.c, connection_table.c
    reconciled = dict(
        conn.execute(
            select(s.account_id, func.max(s.last_booking_on))
            .where(s.status == ImportStatus.RECONCILED.value)
            .group_by(s.account_id)
        ).all()
    )
    connections = {
        r.account_id: r for r in conn.execute(select(b.account_id, b.created_at, b.last_sync_at))
    }
    out = []
    for r in conn.execute(
        select(a.id, a.label, a.is_owner_account, a.opening_balance_on).order_by(a.id)
    ):
        connection = connections.get(r.id)
        out.append(
            ReminderAccount(
                id=r.id,
                label=r.label,
                is_owner_account=r.is_owner_account,
                opening_balance_on=r.opening_balance_on,
                reconciled_until=reconciled.get(r.id),
                connection=connection is not None,
                connected_on=None if connection is None else _local_day(connection.created_at),
                last_sync_on=(
                    None
                    if connection is None or connection.last_sync_at is None
                    else _local_day(connection.last_sync_at)
                ),
            )
        )
    return out


def _local_day(moment: datetime) -> date:
    return moment.astimezone(LOCAL_TIME_ZONE).date()


def _tax_years(conn: Connection, today: date, first_year: int | None) -> list[ReminderTaxYear]:
    """The years the household files for: from the first purchase or pay year to today's year,
    at most _TAX_YEARS_BACK back. A year without a questionnaire file (before the first one)
    counts as complete: there is nothing to answer."""
    from .questionnaire import questionnaire_progress  # it imports the tax services

    if first_year is None:
        return []
    out = []
    for year in range(max(first_year, today.year - _TAX_YEARS_BACK), today.year + 1):
        try:
            complete = not questionnaire_progress(conn, year).open
        except (RuleError, ValueError):
            complete = True
        out.append(ReminderTaxYear(tax_year=year, questionnaire_complete=complete))
    return out


def _acks(conn: Connection) -> list[ReminderAck]:
    c = ack_table.c
    rows = conn.execute(
        select(
            c.reminder_key,
            c.scope_type,
            c.scope_id,
            c.due_on,
            c.action,
            c.snoozed_until,
            c.recorded_at,
        ).order_by(c.recorded_at, c.id)
    )
    return [ReminderAck.model_validate(dict(r._mapping)) for r in rows]


# --- Reads ----------------------------------------------------------------------------------


def list_reminders(
    conn: Connection, today: date, from_: date | None = None, to: date | None = None
) -> list[Reminder]:
    """GET /reminders: every open instance (both severities), by (due_on, id); `from_` and `to`
    keep the due days in that range (inclusive)."""
    return [
        r
        for r in build_reminders(facts(conn, today), today)
        if (from_ is None or r.due_on >= from_) and (to is None or r.due_on <= to)
    ]


def reminder_inbox_items(conn: Connection, today: date) -> list[InboxItem]:
    """Zu prüfen's reminder cards: every `action` reminder visible today, except the
    questionnaire's (its D110 card exists). The frontend words each card from label_code and
    label_args (GET /reminders); the card carries only the id."""
    return [
        InboxItem(
            kind=InboxItemKind.REMINDER,
            created_at=datetime.combine(r.visible_from, time(), tzinfo=UTC),
            reminder_id=r.id,
        )
        for r in build_reminders(facts(conn, today), today)
        if r.severity == "action"
        and r.visible_from <= today
        and r.kind is not ReminderKind.QUESTIONNAIRE_OPEN
    ]


# --- Acknowledgements (§6.3) ----------------------------------------------------------------


def acknowledge(ctx: CommandContext, reminder_id: str, body: ReminderAckCreate) -> ReminderAck:
    """POST /reminders/{reminder_id}/ack: one reminder_ack row and its event. 422
    reminder.unknown for an id that is not <known key>:<scope>:<date>; 422
    reminder.scope_invalid when the scope is not a row of this workspace of a type the kind
    uses. `muted` and `unmuted` apply to the kind and scope, so their row has no due day."""
    parsed = parse_reminder_id(reminder_id)
    if parsed is None:
        raise RuleViolationError([REMINDER_UNKNOWN], f"{reminder_id!r} is not a reminder id")
    kind, scope_id, due_on = parsed
    scope = _scope_of(ctx.conn, kind, scope_id)
    whole = body.action in (ReminderAckAction.MUTED, ReminderAckAction.UNMUTED)
    values = {
        "reminder_key": kind.value,
        "scope_type": scope.value,
        "scope_id": scope_id,
        "due_on": None if whole else due_on,
        "action": body.action.value,
        "snoozed_until": body.snoozed_until,
    }
    row = ctx.conn.execute(
        insert(ack_table)
        .values(
            workspace_id=ctx.workspace_id,
            **values,
            recorded_at=ctx.now,
            recorded_by=user_id(ctx),
        )
        .returning(*ack_table.c)
    ).one()
    append_event(
        ctx,
        EventType.WORKSPACE_REMINDER_ACKNOWLEDGED,
        ctx.workspace_id,
        ReminderAcknowledged.model_validate(values),
    )
    return ReminderAck.model_validate(
        {k: v for k, v in row._mapping.items() if k in ReminderAck.model_fields}
    )


def _scope_of(conn: Connection, kind: ReminderKind, scope_id: UUID | None) -> ReminderScope:
    """The scope type of the id's scope: the workspace for None, else the one table among the
    kind's scope types that holds the row (RLS: another workspace's row is not found)."""
    scopes = KIND_SCOPES[kind]
    if scope_id is None:
        if ReminderScope.WORKSPACE in scopes:
            return ReminderScope.WORKSPACE
        raise _scope_invalid(f"{kind.value} is not a household reminder")
    for scope in scopes:
        table = _SCOPE_TABLES.get(scope)
        if table is None:
            continue
        if conn.execute(select(table.c.id).where(table.c.id == scope_id)).first() is not None:
            return scope
    raise _scope_invalid(f"{scope_id} is not a {'/'.join(s.value for s in scopes)} of yours")


def _scope_invalid(message: str) -> RuleViolationError:
    return RuleViolationError([REMINDER_SCOPE_INVALID], message)
