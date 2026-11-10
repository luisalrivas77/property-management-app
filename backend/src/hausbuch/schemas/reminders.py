"""Phase 5: reminders computed on read from a catalogue (docs/phases/phase-5.md §6, §8, §9;
D122, D123, D126).

`rules.reminders.build_reminders(facts, today)` (step 3) is pure over ReminderFacts and
returns every open reminder instance; only acknowledgements (`reminder_ack`) and sent
notifications (`notification`) are stored. A reminder never carries an amount or a tenant's
name (D5, D122): its label_args are dates, a month, a year, a property, unit, account label or
a lender.

The output contract the fixture `tests/fixtures/phase5/reminders.yaml` pins (step 0's reading
of §6.2; each point is in the fixture header too):
- An instance is returned while it is open (not cleared `auto`, not hidden by an ack) and its
  due_on is at most HORIZON_DAYS after today; the largest lead (in-app or email) is 90 days, so
  every instance that is visible today or has a send day today is in the list. An open
  instance whose due_on has passed stays in the list until it is cleared.
- id = "<key>:<scope_id or 'workspace'>:<due_on ISO>", stable for the same facts on any day.
- visible_from = due_on − the kind's in-app lead; email_leads are days before due_on (a
  negative lead is a day after it: the Lastschrift check's due_on + 3).

The sets stored in a CHECK'd column (ReminderScope, ReminderAckAction, NotificationChannel,
NotificationStatus) live here; tests/db/test_schema_consistency.py keeps them equal to migration
0019's CHECKs. ReminderKind's values are the `reminder_key` column's values (text ≤ 64, no
CHECK: the catalogue is code).
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from .checklist import ChecklistFacts
from .common import Money, Schema, TaxYear
from .enums import OperatingCostsTerms
from .tax import GrundsteuerInstalment
from .tenancy import RentDueDay

HORIZON_DAYS = 90  # §6.2's largest lead; also the Übersicht card's window (D122)

ReminderId = Annotated[str, Field(min_length=1, max_length=160)]  # notification.reminder_id


class ReminderKind(StrEnum):
    """The catalogue's eleven kinds (§6.2); the value is the reminder key."""

    GRUNDSTEUER_INSTALMENT = "grundsteuer.instalment"
    CHECKLIST_DUE = "checklist.due"
    RETURN_DEADLINE = "tax.return_deadline"
    QUESTIONNAIRE_OPEN = "tax.questionnaire_open"
    WAGE_CERTIFICATE_MISSING = "tax.wage_certificate_missing"
    LOAN_FIXED_RATE_END = "loan.fixed_rate_end"
    LOAN_SPECIAL_TERMINATION = "loan.special_termination"
    SPECULATION_END = "property.speculation_end"
    SETTLEMENT_DEADLINE = "tenancy.settlement_deadline"
    RENT_MISSING = "tenancy.rent_missing"
    SYNC_STALE = "bank.sync_stale"


class ReminderScope(StrEnum):
    """reminder_ack.scope_type (migration 0019). WORKSPACE is §6.2's "household"."""

    WORKSPACE = "workspace"
    PROPERTY = "property"
    LOAN = "loan"
    TENANCY = "tenancy"
    OWNER = "owner"
    ACCOUNT = "account"


class ReminderAckAction(StrEnum):
    """reminder_ack.action (migration 0019, §6.3)."""

    DONE = "done"  # hides this instance
    SNOOZED = "snoozed"  # hides this instance while today < snoozed_until
    MUTED = "muted"  # hides every instance of the key and scope until a later unmuted row
    UNMUTED = "unmuted"


class NotificationChannel(StrEnum):
    """notification.channel (migration 0019). Web push is deferred (§6.4)."""

    EMAIL = "email"


class NotificationStatus(StrEnum):
    """notification.status (migration 0019)."""

    SENT = "sent"
    FAILED = "failed"


# --- Output (§9) -------------------------------------------------------------------------


class Reminder(Schema):
    id: ReminderId
    kind: ReminderKind
    scope: ReminderScope
    scope_id: UUID | None = None  # None exactly for the workspace scope
    due_on: date
    visible_from: date
    severity: Literal["action", "info"]
    cleared_by: Literal["ack", "auto"]
    label_code: str = Field(min_length=1, max_length=64)
    label_args: dict[str, str | int] = Field(default_factory=dict)
    email_leads: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if (self.scope is ReminderScope.WORKSPACE) != (self.scope_id is None):
            raise ValueError("scope_id is null exactly for the workspace scope")
        scope = "workspace" if self.scope_id is None else str(self.scope_id)
        if self.id != f"{self.kind.value}:{scope}:{self.due_on.isoformat()}":
            raise ValueError("id is <key>:<scope_id or workspace>:<due_on>")
        if self.visible_from > self.due_on:
            raise ValueError("visible_from is on or before due_on")
        return self


class NotificationDue(Schema):
    """rules.reminders.due_notifications output: one mail line per item, lead and member."""

    reminder_id: ReminderId
    lead_days: int
    recipient_user_id: UUID


class ReminderAckCreate(Schema):
    """POST /reminders/{reminder_id}/ack (§6.3)."""

    action: ReminderAckAction
    snoozed_until: date | None = None

    @model_validator(mode="after")
    def _snooze(self) -> Self:
        if (self.action is ReminderAckAction.SNOOZED) != (self.snoozed_until is not None):
            raise ValueError("snoozed_until is set exactly for snoozed")
        return self


# --- Facts (§6.1): today-independent inputs, assembled by services/reminders.facts ----------


class ReminderGrundsteuerSchedule(Schema):
    """A `grundsteuer_schedule` tax input in force (D126): its first year and its value's
    instalments and payment mode. The service passes the rows in force only."""

    id: UUID
    tax_year: TaxYear
    instalments: list[GrundsteuerInstalment] = Field(min_length=1, max_length=4)
    payment_mode: Literal["remind", "sepa_mandate"] | None = None

    @model_validator(mode="after")
    def _in_year(self) -> Self:
        if any(i.due_on.year != self.tax_year for i in self.instalments):
            raise ValueError("a schedule's instalments fall in its tax_year")
        return self


class ReminderGrundsteuerPayment(Schema):
    """A confirmed `grundsteuer` allocation of the property: its bank line's booking day, the
    allocation's signed amount (a cost: negative) and whether the line is a SEPA direct debit
    (rules.statements.is_direct_debit on transaction.raw: PMNT/RDDT or PMNT/IDDT, D126)."""

    id: UUID
    booking_date: date
    amount: Money
    direct_debit: bool


class ReminderProperty(Schema):
    """A property with what the Grundsteuer, checklist and §23 kinds read. `checklist` is the
    property's ChecklistFacts, so `checklist.due` and the Bescheid's presence come from
    build_checklist itself (§6.2: reused, never re-encoded)."""

    id: UUID
    label: str
    purchase_date: date  # Übergang (D7)
    notarized_on: date | None = None  # starts the §23 period
    ownership_ended_on: date | None = None
    checklist: ChecklistFacts
    grundsteuer_schedules: list[ReminderGrundsteuerSchedule] = Field(default_factory=list)
    grundsteuer_payments: list[ReminderGrundsteuerPayment] = Field(default_factory=list)

    @model_validator(mode="after")
    def _same_property(self) -> Self:
        c = self.checklist
        if c.property_id != self.id or c.purchase_date != self.purchase_date:
            raise ValueError("the checklist facts are this property's")
        if c.ownership_ended_on != self.ownership_ended_on:
            raise ValueError("the checklist facts are this property's")
        if len({s.tax_year for s in self.grundsteuer_schedules}) != len(self.grundsteuer_schedules):
            raise ValueError("one schedule in force per first year")
        return self


class ReminderLoan(Schema):
    id: UUID
    lender: str
    disbursed_on: date  # the (last) payout: §489 Abs. 1 Nr. 2 BGB counts from full receipt
    fixed_until: date | None = None


class ReminderRentPayment(Schema):
    """A confirmed `rent` allocation of the tenancy by its service period (amount not needed:
    the reminder asks whether rent arrived, never how much)."""

    id: UUID
    service_period_start: date
    service_period_end: date


class ReminderTenancy(Schema):
    """A tenancy as the settlement and rent kinds see it: its unit label, never a tenant."""

    id: UUID
    property_id: UUID
    unit_label: str
    start_date: date
    end_date: date | None = None
    operating_costs_terms: OperatingCostsTerms
    rent_due_day: RentDueDay = 3
    rent_payments: list[ReminderRentPayment] = Field(default_factory=list)


class ReminderOwner(Schema):
    """An owner as the wage-certificate kind sees it (phase 4b return_input rows)."""

    id: UUID
    pay_years: list[TaxYear] = Field(default_factory=list)  # a payslip or expected_gross_pay
    wage_certificate_years: list[TaxYear] = Field(default_factory=list)


class ReminderAccount(Schema):
    """An account with its reconciled coverage and its connection (if any). The rent kind
    needs every non-owner account's reconciled lines to reach a date before it calls rent
    missing; the stale kind needs the connection's last success (days in Europe/Berlin)."""

    id: UUID
    label: str
    is_owner_account: bool = False
    opening_balance_on: date  # the anchor: no statement line exists on or before it
    reconciled_until: date | None = None  # the last booking day of a reconciled import
    connection: bool = False  # a bank_connection exists
    connected_on: date | None = None  # its created_at's day
    last_sync_on: date | None = None  # its last_sync_at's day

    @model_validator(mode="after")
    def _connection(self) -> Self:
        if self.connection != (self.connected_on is not None):
            raise ValueError("connected_on is set exactly when a connection exists")
        if not self.connection and self.last_sync_on is not None:
            raise ValueError("last_sync_on needs a connection")
        return self


class ReminderTaxYear(Schema):
    """A tax year the household files for (the service lists them), with the D110
    questionnaire's state."""

    tax_year: TaxYear
    questionnaire_complete: bool = False


class ReminderAck(Schema):
    """A reminder_ack row (append-only, §6.3)."""

    reminder_key: ReminderKind
    scope_type: ReminderScope
    scope_id: UUID | None = None
    due_on: date | None = None  # null exactly for muted / unmuted
    action: ReminderAckAction
    snoozed_until: date | None = None
    recorded_at: datetime

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if (self.scope_type is ReminderScope.WORKSPACE) != (self.scope_id is None):
            raise ValueError("scope_id is null exactly for the workspace scope")
        if (self.action is ReminderAckAction.SNOOZED) != (self.snoozed_until is not None):
            raise ValueError("snoozed_until is set exactly for snoozed")
        whole_key = self.action in (ReminderAckAction.MUTED, ReminderAckAction.UNMUTED)
        if whole_key != (self.due_on is None):
            raise ValueError("due_on is null exactly for muted and unmuted")
        return self


class ReminderFacts(Schema):
    """Everything build_reminders reads (§6.1). Every list is order-independent."""

    properties: list[ReminderProperty] = Field(default_factory=list)
    loans: list[ReminderLoan] = Field(default_factory=list)
    tenancies: list[ReminderTenancy] = Field(default_factory=list)
    owners: list[ReminderOwner] = Field(default_factory=list)
    accounts: list[ReminderAccount] = Field(default_factory=list)
    tax_years: list[ReminderTaxYear] = Field(default_factory=list)
    acks: list[ReminderAck] = Field(default_factory=list)

    @model_validator(mode="after")
    def _references(self) -> Self:
        property_ids = {p.id for p in self.properties}
        if any(t.property_id not in property_ids for t in self.tenancies):
            raise ValueError("every tenancy is on one of the properties")
        return self


class SentNotification(Schema):
    """A notification row as due_notifications reads it. A failed row counts as sent: the
    UNIQUE constraint on an append-only table allows one row per item, lead and recipient
    (§8), so a failed mail is not retried by the rule (reported as a step 0 spec finding)."""

    reminder_id: ReminderId
    lead_days: int
    recipient_user_id: UUID
    status: NotificationStatus = NotificationStatus.SENT
