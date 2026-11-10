"""Phase 5 deadline reminders, computed on read (docs/phases/phase-5.md §6, D122, D123, D126).

`build_reminders(facts, today)` returns every open reminder instance whose due day lies between
1 January of last year and today + HORIZON_DAYS, sorted by (due_on, id). It is pure and
order-independent: nothing here reads the clock, the database or the order of a list. The
catalogue below is §6.2's table; the fixture `tests/fixtures/phase5/reminders.yaml` pins it.

Dates the other rules already own are read from them, never re-encoded: the checklist's due
dates and statuses from `build_checklist`, the filing deadline from `filing_deadline`, the §23
date from `tax_free_from` over the latest rule set (§6.2).

`due_notifications(reminders, sent, today, recipients)` says which emails a daily run sends
(D123): the latest email lead reached today per reminder and member, unless a `sent` row exists
for it or three attempts failed. The job and the mail client are step 3b.

A reminder never carries an amount or a tenant's name (D5, D122): its label arguments are a
year, a month, a date, or a property, unit, account or lender label.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

from hausbuch.rules.document_checklist import build_checklist
from hausbuch.rules.tax import registry
from hausbuch.rules.tax._common import add_months, add_years
from hausbuch.rules.tax.return_estimate import filing_deadline
from hausbuch.rules.tax.spekulation import tax_free_from
from hausbuch.schemas.checklist import (
    ChecklistItem,
    ChecklistScopeType,
    ChecklistStatus,
    RequirementCode,
)
from hausbuch.schemas.enums import OperatingCostsTerms
from hausbuch.schemas.reminders import (
    HORIZON_DAYS,
    MAX_NOTIFICATION_ATTEMPTS,
    NotificationDue,
    NotificationStatus,
    Reminder,
    ReminderAck,
    ReminderAckAction,
    ReminderFacts,
    ReminderGrundsteuerSchedule,
    ReminderKind,
    ReminderProperty,
    ReminderScope,
    ReminderTenancy,
    SentNotification,
)

K = ReminderKind

# §28 Abs. 1 GrStG: a quarter each on 15 Feb, 15 May, 15 Aug and 15 Nov (no schedule row).
STATUTORY_INSTALMENTS = ((2, 15), (5, 15), (8, 15), (11, 15))
PAYMENT_WINDOW_DAYS = 10  # a Grundsteuer payment clears an instalment booked within ±10 days
AMOUNT_TOLERANCE = Decimal("0.01")  # and, with a schedule row, at the instalment ± 0.01
RENT_GRACE_DAYS = 5  # rent is missing 5 days after its due day
SYNC_STALE_DAYS = 35  # a connection without a success for 35 days (D120's daily schedule)
SPECIAL_TERMINATION_YEARS = 10  # §489 Abs. 1 Nr. 2 BGB
_DATED_SCOPES = {ChecklistScopeType.PROPERTY: ReminderScope.PROPERTY}


@dataclass(frozen=True)
class _Row:
    """One line of §6.2's table: in-app lead, email leads (days before due_on), severity and
    how an instance clears."""

    in_app: int
    email: tuple[int, ...]
    severity: Literal["action", "info"]
    cleared_by: Literal["ack", "auto"]


GRUNDSTEUER_REMIND = _Row(7, (7,), "action", "auto")
GRUNDSTEUER_SEPA = _Row(0, (-3,), "action", "auto")  # "Lastschrift prüfen" 3 days after
CATALOGUE: dict[ReminderKind, _Row] = {
    K.CHECKLIST_DUE: _Row(14, (14, 0), "action", "auto"),
    K.RETURN_DEADLINE: _Row(90, (60, 14), "action", "ack"),
    K.QUESTIONNAIRE_OPEN: _Row(0, (-14,), "action", "auto"),
    K.WAGE_CERTIFICATE_MISSING: _Row(0, (0,), "action", "auto"),
    K.LOAN_FIXED_RATE_END: _Row(0, (0,), "action", "ack"),
    K.LOAN_SPECIAL_TERMINATION: _Row(0, (), "info", "ack"),
    K.SPECULATION_END: _Row(0, (), "info", "ack"),
    K.SETTLEMENT_DEADLINE: _Row(90, (90, 30), "action", "ack"),
    K.RENT_MISSING: _Row(0, (0,), "action", "auto"),
    K.SYNC_STALE: _Row(0, (0,), "info", "auto"),
}


def reminder_id(kind: ReminderKind, scope_id: UUID | None, due_on: date) -> str:
    """The id <key>:<scope_id or workspace>:<due_on ISO> (§9), the same on any day."""
    scope = "workspace" if scope_id is None else str(scope_id)
    return f"{kind.value}:{scope}:{due_on.isoformat()}"


def _reminder(
    kind: ReminderKind,
    scope: ReminderScope,
    scope_id: UUID | None,
    due_on: date,
    label_args: dict[str, str | int],
    *,
    row: _Row | None = None,
    label_code: str | None = None,
) -> Reminder:
    row = row or CATALOGUE[kind]
    return Reminder(
        id=reminder_id(kind, scope_id, due_on),
        kind=kind,
        scope=scope,
        scope_id=scope_id,
        due_on=due_on,
        visible_from=due_on - timedelta(days=row.in_app),
        severity=row.severity,
        cleared_by=row.cleared_by,
        label_code=label_code or kind.value,
        label_args=label_args,
        email_leads=list(row.email),
    )


# --- Grundsteuer (D126) ---------------------------------------------------------------------


def _bescheid_present(p: ReminderProperty, tax_year: int, today: date) -> bool:
    """The checklist's grundsteuerbescheid item for the year is present (reused, §6.2)."""
    return any(
        item.code is RequirementCode.GRUNDSTEUERBESCHEID and item.status is ChecklistStatus.PRESENT
        for item in build_checklist(p.checklist, tax_year, today)
    )


def _schedule_for(p: ReminderProperty, tax_year: int) -> ReminderGrundsteuerSchedule | None:
    """The latest schedule row with tax_year <= Y (its days carry into later years)."""
    rows = [s for s in p.grundsteuer_schedules if s.tax_year <= tax_year]
    return max(rows, key=lambda s: s.tax_year) if rows else None


@dataclass(frozen=True)
class _Instalment:
    due_on: date
    amount: Decimal | None  # None without a schedule row: any amount clears
    mode: Literal["remind", "sepa_mandate"] | None  # the row's, if it sets one


def _instalments(p: ReminderProperty, tax_year: int) -> list[_Instalment]:
    row = _schedule_for(p, tax_year)
    if row is None:
        return [_Instalment(date(tax_year, m, d), None, None) for m, d in STATUTORY_INSTALMENTS]
    shift = tax_year - row.tax_year
    return [
        _Instalment(add_years(i.due_on, shift), i.amount, row.payment_mode) for i in row.instalments
    ]


def _clearing_payments(p: ReminderProperty, inst: _Instalment) -> list[bool]:
    """The direct-debit flags of the payments that clear the instalment (empty: open)."""
    window = timedelta(days=PAYMENT_WINDOW_DAYS)
    return [
        pay.direct_debit
        for pay in p.grundsteuer_payments
        if abs(pay.booking_date - inst.due_on) <= window
        and (inst.amount is None or abs(abs(pay.amount) - inst.amount) <= AMOUNT_TOLERANCE)
    ]


def _grundsteuer(p: ReminderProperty, today: date, last_year: int) -> Iterator[Reminder]:
    """Every instalment of each year from the first full year of ownership whose Bescheid is
    in; open until a matching payment is booked. The mode is the row's, else sepa_mandate once
    an earlier instalment was cleared by a direct-debit line (never inferred from a transfer)."""
    instalments: list[_Instalment] = []
    for year in range(p.purchase_date.year + 1, last_year + 1):
        if _bescheid_present(p, year, today):
            instalments.extend(_instalments(p, year))
    instalments.sort(key=lambda i: i.due_on)
    debited_before = False
    for inst in instalments:
        mode = inst.mode or ("sepa_mandate" if debited_before else "remind")
        cleared = _clearing_payments(p, inst)
        if any(cleared):
            debited_before = True
        if cleared:
            continue
        yield _reminder(
            K.GRUNDSTEUER_INSTALMENT,
            ReminderScope.PROPERTY,
            p.id,
            inst.due_on,
            {"property": p.label},
            row=GRUNDSTEUER_SEPA if mode == "sepa_mandate" else GRUNDSTEUER_REMIND,
            label_code=f"{K.GRUNDSTEUER_INSTALMENT.value}.{mode}",
        )


# --- Checklist ------------------------------------------------------------------------------


def _checklist_reminder(p: ReminderProperty, item: ChecklistItem, year: int) -> Reminder | None:
    if item.due_on is None or item.status not in (
        ChecklistStatus.MISSING,
        ChecklistStatus.EXPECTED,
    ):
        return None
    label_code = f"{K.CHECKLIST_DUE.value}.{item.code.value}"
    if item.scope.type is ChecklistScopeType.LOAN:
        scope, args = ReminderScope.LOAN, {"lender": item.scope.lender or "", "year": year}
    elif item.scope.type in _DATED_SCOPES:
        scope, args = _DATED_SCOPES[item.scope.type], {"property": p.label, "year": year}
    else:  # no dated catalogue entry has another scope today
        return None
    return _reminder(
        K.CHECKLIST_DUE, scope, item.scope.id, item.due_on, args, label_code=label_code
    )


def _checklist(p: ReminderProperty, today: date, last_year: int) -> Iterator[Reminder]:
    for year in range(p.purchase_date.year, last_year + 1):
        for item in build_checklist(p.checklist, year, today):
            r = _checklist_reminder(p, item, year)
            if r is not None:
                yield r


# --- Property, loans, tax years, owners -----------------------------------------------------


def _speculation(p: ReminderProperty, today: date) -> Iterator[Reminder]:
    """§23 Abs. 1 Nr. 1 EStG: an info card 12 months before the sale is tax-free, while owned.
    A property still owned has no sale year, so the latest rule set's period (§6.2)."""
    if p.notarized_on is None:
        return
    if p.ownership_ended_on is not None and p.ownership_ended_on <= today:
        return
    free = tax_free_from(p.notarized_on, registry.rule_set(max(registry.available_years())))
    yield _reminder(
        K.SPECULATION_END,
        ReminderScope.PROPERTY,
        p.id,
        add_months(free, -12),
        {"property": p.label, "tax_free_from": free.isoformat()},
    )


def _loans(facts: ReminderFacts) -> Iterator[Reminder]:
    for loan in facts.loans:
        if loan.fixed_until is not None:
            for months in (12, 6):
                yield _reminder(
                    K.LOAN_FIXED_RATE_END,
                    ReminderScope.LOAN,
                    loan.id,
                    add_months(loan.fixed_until, -months),
                    {
                        "lender": loan.lender,
                        "fixed_until": loan.fixed_until.isoformat(),
                        "months_before": months,
                    },
                )
        notice_from = add_years(loan.disbursed_on, SPECIAL_TERMINATION_YEARS)
        yield _reminder(
            K.LOAN_SPECIAL_TERMINATION,
            ReminderScope.LOAN,
            loan.id,
            add_months(notice_from, -6),
            {"lender": loan.lender},
        )


def _tax_years(facts: ReminderFacts) -> Iterator[Reminder]:
    for y in facts.tax_years:
        yield _reminder(
            K.RETURN_DEADLINE,
            ReminderScope.WORKSPACE,
            None,
            filing_deadline(y.tax_year),
            {"year": y.tax_year},
        )
        if not y.questionnaire_complete:
            yield _reminder(
                K.QUESTIONNAIRE_OPEN,
                ReminderScope.WORKSPACE,
                None,
                date(y.tax_year + 1, 1, 1),
                {"year": y.tax_year},
            )
    for owner in facts.owners:
        # §41b Abs. 1 S. 3 EStG: the employer hands the certificate out by the end of February.
        for year in sorted(set(owner.pay_years) - set(owner.wage_certificate_years)):
            yield _reminder(
                K.WAGE_CERTIFICATE_MISSING,
                ReminderScope.OWNER,
                owner.id,
                date(year + 1, 3, 1),
                {"year": year},
            )


# --- Tenancies ------------------------------------------------------------------------------


def _month_end(year: int, month: int) -> date:
    return add_months(date(year, month, 1), 1) - timedelta(days=1)


def _settlements(t: ReminderTenancy, p: ReminderProperty, last_year: int) -> Iterator[Reminder]:
    """§556 Abs. 3 S. 2 BGB: the settlement for year Y is due by 31 Dec Y+1; prepayment
    tenancies only (never flat_rate or included, D72, D91), years from the purchase year."""
    if t.operating_costs_terms is not OperatingCostsTerms.PREPAYMENT:
        return
    first = max(t.start_date.year, p.purchase_date.year)
    last = min(
        last_year,
        t.end_date.year if t.end_date else last_year,
        p.ownership_ended_on.year if p.ownership_ended_on else last_year,
    )
    for year in range(first, last + 1):
        yield _reminder(
            K.SETTLEMENT_DEADLINE,
            ReminderScope.TENANCY,
            t.id,
            date(year + 1, 12, 31),
            {"unit": t.unit_label, "year": year},
        )


def _statements_reach(facts: ReminderFacts, day: date) -> bool:
    """At least one account that is not an owner's, and each such account's reconciled lines
    reach `day` (after its anchor): otherwise the statement is not in yet."""
    accounts = [a for a in facts.accounts if not a.is_owner_account]
    return bool(accounts) and all(
        a.opening_balance_on < day and a.reconciled_until is not None and day <= a.reconciled_until
        for a in accounts
    )


def _rent_missing(
    t: ReminderTenancy, p: ReminderProperty, facts: ReminderFacts, horizon: date
) -> Iterator[Reminder]:
    """Each full month of the tenancy from the handover: missing on its due day + 5 when no rent
    allocation's service period overlaps the month and the statements reach that day. The
    label names the unit, never a tenant (D5)."""
    begin = max(t.start_date, p.purchase_date)
    month = begin.replace(day=1) if begin.day == 1 else add_months(begin.replace(day=1), 1)
    ends = [d for d in (t.end_date, p.ownership_ended_on) if d is not None]
    end = min(ends) if ends else None
    while True:
        last_day = _month_end(month.year, month.month)
        due_on = date(month.year, month.month, t.rent_due_day) + timedelta(days=RENT_GRACE_DAYS)
        if due_on > horizon or (end is not None and last_day > end):
            return
        paid = any(
            r.service_period_start <= last_day and r.service_period_end >= month
            for r in t.rent_payments
        )
        if not paid and _statements_reach(facts, due_on):
            yield _reminder(
                K.RENT_MISSING,
                ReminderScope.TENANCY,
                t.id,
                due_on,
                {"unit": t.unit_label, "month": f"{month.year:04d}-{month.month:02d}"},
            )
        month = add_months(month, 1)


def _accounts(facts: ReminderFacts) -> Iterator[Reminder]:
    for a in facts.accounts:
        since = a.last_sync_on or a.connected_on
        if a.connection and since is not None:
            yield _reminder(
                K.SYNC_STALE,
                ReminderScope.ACCOUNT,
                a.id,
                since + timedelta(days=SYNC_STALE_DAYS),
                {"account": a.label},
            )


# --- Acknowledgements (§6.3) ---------------------------------------------------------------


def _hidden(acks: Iterable[ReminderAck], today: date) -> tuple[set, set]:
    """(instances hidden by done or a running snooze, (key, scope) pairs muted)."""
    instances: set[tuple[str, str, UUID | None, date]] = set()
    latest: dict[tuple[str, str, UUID | None], ReminderAck] = {}
    for ack in acks:
        whole = (ack.reminder_key.value, ack.scope_type.value, ack.scope_id)
        if ack.action in (ReminderAckAction.MUTED, ReminderAckAction.UNMUTED):
            if whole not in latest or ack.recorded_at > latest[whole].recorded_at:
                latest[whole] = ack
        elif ack.action is ReminderAckAction.DONE or (
            ack.snoozed_until is not None and today < ack.snoozed_until
        ):
            assert ack.due_on is not None
            instances.add((*whole, ack.due_on))
    muted = {k for k, ack in latest.items() if ack.action is ReminderAckAction.MUTED}
    return instances, muted


# --- Entry points ---------------------------------------------------------------------------


def build_reminders(facts: ReminderFacts, today: date) -> list[Reminder]:
    """Every open reminder with 1 Jan of last year <= due_on <= today + 90 days, by (due_on, id)."""
    horizon = today + timedelta(days=HORIZON_DAYS)
    floor = date(today.year - 1, 1, 1)
    last_year = horizon.year
    properties = {p.id: p for p in facts.properties}

    def candidates() -> Iterator[Reminder]:
        for p in facts.properties:
            yield from _grundsteuer(p, today, last_year)
            yield from _checklist(p, today, last_year)
            yield from _speculation(p, today)
        yield from _loans(facts)
        yield from _tax_years(facts)
        for t in facts.tenancies:
            yield from _settlements(t, properties[t.property_id], last_year)
            yield from _rent_missing(t, properties[t.property_id], facts, horizon)
        yield from _accounts(facts)

    instances, muted = _hidden(facts.acks, today)
    out: dict[str, Reminder] = {}
    for r in candidates():
        if not floor <= r.due_on <= horizon:
            continue
        whole = (r.kind.value, r.scope.value, r.scope_id)
        if whole in muted or (*whole, r.due_on) in instances:
            continue
        out[r.id] = r
    return sorted(out.values(), key=lambda r: (r.due_on, r.id))


def due_notifications(
    reminders: Iterable[Reminder],
    sent: Iterable[SentNotification],
    today: date,
    recipients: Iterable[UUID],
) -> list[NotificationDue]:
    """The emails a run on `today` sends (D123): per reminder, the smallest email lead whose
    send day (due_on − lead) has come, once per member. A lead a later one overtook is never
    sent late. A `sent` row stops the item, lead and member; `failed` rows count the attempts,
    and after MAX_NOTIFICATION_ATTEMPTS the item is given up."""
    sent = list(sent)
    members = list(recipients)
    done = {
        (s.reminder_id, s.lead_days, s.recipient_user_id)
        for s in sent
        if s.status is NotificationStatus.SENT
    }
    tried: dict[tuple[str, int, UUID], int] = {}
    for s in sent:
        key = (s.reminder_id, s.lead_days, s.recipient_user_id)
        tried[key] = max(tried.get(key, 0), s.attempt)
    out: list[NotificationDue] = []
    for r in reminders:
        reached = [lead for lead in r.email_leads if r.due_on - timedelta(days=lead) <= today]
        if not reached:
            continue
        lead = min(reached)
        for user in members:
            key = (r.id, lead, user)
            attempt = tried.get(key, 0) + 1
            if key in done or attempt > MAX_NOTIFICATION_ATTEMPTS:
                continue
            out.append(
                NotificationDue(
                    reminder_id=r.id, lead_days=lead, recipient_user_id=user, attempt=attempt
                )
            )
    return sorted(out, key=lambda n: (n.reminder_id, str(n.recipient_user_id)))
