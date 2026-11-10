"""Shapes of the phase 5 YAML rule fixtures (docs/phases/phase-5.md §4, §5, §6, §12).

Same contract as fixture_models_phase4b.py: loading a fixture through these models is itself a
test (tests/test_fixtures_valid.py), so a fixture that does not match the spec schemas fails
before any rule exists. Inputs are the §9 schemas the §12 signatures take (SyncWindowFacts,
SyncStatus/SyncEvent/SyncTrigger, InboundMailFacts and InboundPolicy, ReminderFacts, Reminder,
SentNotification). Expectations are recomputed on load where the arithmetic is small:

- sync_window, tan_flow, inbound_mail and notification_schedule by the reference functions
  below (`_window`, `_next_state`, `_decide`, `_due_notifications`): the fixture header's
  contract written once more, not the rules (step 1, 3 and 4 build `rules/bank_sync.py`,
  `rules/inbound_mail.py` and `rules/reminders.py`, and their tests compare them with these
  expectations);
- sync_archive by building the reference ZIP from phase 2's camt sample and reading it back
  with phase 2's unchanged `read_camt` (the round trip §12 asks for), and by phase 2's archive
  limits for the refused cases;
- sync_balance with phase 2's unchanged `line_hashes`, `window_sums` and `reconcile`;
- reminders by the catalogue table of §6.2 (scope, leads, severity, how it clears, label
  arguments) and each kind's due-date formula over the case's facts. Which instances exist is
  the rule's job and is written out by hand per case, with the derivation in a comment.

Every interpretation step 0 had to make is in the fixture file's header, and listed in the
step 0 report as a spec finding.
"""

from __future__ import annotations

import calendar
import io
import zipfile
from datetime import date, timedelta
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Literal, Self
from uuid import UUID

import yaml
from fixture_models import Lenient, RuleErrorExpect
from pydantic import Field, model_validator

from hausbuch.rules import errors as E
from hausbuch.rules.statements import (
    ARCHIVE_REJECTED,
    MAX_ARCHIVE_BYTES,
    MAX_ARCHIVE_MEMBERS,
    line_hashes,
    reconcile,
    window_sums,
)
from hausbuch.rules.tax import registry
from hausbuch.rules.tax._common import add_years
from hausbuch.rules.tax.return_estimate import filing_deadline
from hausbuch.rules.tax.spekulation import tax_free_from
from hausbuch.schemas.bank import LineForHash, ReconcileResult, StoredLine
from hausbuch.schemas.bank_sync import (
    SyncEvent,
    SyncStatus,
    SyncTransition,
    SyncTrigger,
    SyncWindow,
    SyncWindowFacts,
)
from hausbuch.schemas.common import Money, Schema
from hausbuch.schemas.enums import BalanceSource
from hausbuch.schemas.inbound_mail import InboundMailFacts, InboundPolicy, MailDecision, MailPart
from hausbuch.schemas.reminders import (
    HORIZON_DAYS,
    MAX_NOTIFICATION_ATTEMPTS,
    NotificationDue,
    Reminder,
    ReminderFacts,
    ReminderKind,
    ReminderScope,
    SentNotification,
)
from hausbuch.services.bank.camt import read_camt

FIXTURES = Path(__file__).parent / "fixtures" / "phase5"
CAMT_SAMPLE = Path(__file__).parent / "fixtures" / "phase2" / "bank_samples" / "camt052.zip"


@cache
def raw(name: str) -> dict:
    with (FIXTURES / f"{name}.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


class _Fixture(Schema):
    ids: dict[str, UUID] = Field(default_factory=dict)
    bases: dict[str, object] = Field(default_factory=dict)  # YAML anchors merged into cases


class Refused(Exception):
    """A RuleError of the rules, by code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _add_months(day: date, months: int) -> date:
    """The same day `months` later (negative: earlier); a day the month lacks → its last."""
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


# --- sync_window.yaml: rules.bank_sync.request_window(connection, today) (D120) ------------

SCA_EXEMPT_DAYS = 90  # §3: the SCA exemption covers the last 90 days of account history


def _window(facts: SyncWindowFacts, today: date) -> SyncWindow:
    first_storable = facts.opening_balance_on + timedelta(days=1)  # importer: > anchor only
    if facts.last_booking_on is None:
        start = first_storable
    else:
        start = max(facts.last_booking_on - timedelta(days=14), first_storable)
    if start > today:
        raise Refused(E.BANK_WINDOW_INVALID)
    return SyncWindow(
        start=start, end=today, may_need_approval=(today - start).days > SCA_EXEMPT_DAYS
    )


class SyncWindowExpect(Schema):
    window: SyncWindow | None = None
    error: RuleErrorExpect | None = None

    @model_validator(mode="after")
    def _one(self) -> Self:
        if (self.window is None) == (self.error is None):
            raise ValueError("expect a window or an error")
        return self


class SyncWindowCase(Lenient):
    today: date
    facts: SyncWindowFacts
    expect: SyncWindowExpect

    @model_validator(mode="after")
    def _recompute(self) -> Self:
        try:
            got: SyncWindow | str = _window(self.facts, self.today)
        except Refused as refused:
            got = refused.code
        want = self.expect.window or self.expect.error.code
        if got != want:
            raise ValueError(f"{self.id}: request_window gives {got}, the case says {want}")
        return self


class SyncWindowFixture(_Fixture):
    cases: list[SyncWindowCase] = Field(min_length=1)


# --- tan_flow.yaml: rules.bank_sync.next_state(status, event, trigger) (D118, D120, D127) ---

S, V = SyncStatus, SyncEvent
_LIVE = (S.QUEUED, S.RUNNING, S.NEEDS_APPROVAL, S.IMPORTING)


def _next_state(status: SyncStatus, event: SyncEvent, trigger: SyncTrigger) -> SyncTransition:
    """The header's table. A pair it does not list is undefined (a KeyError here, so a case
    that uses one fails to load: step 0 leaves those to step 1 and the spec)."""
    if event is V.STARTED and status in (S.RUNNING, S.IMPORTING):
        raise Refused(E.BANK_SYNC_RUNNING)
    if event is V.ERROR and status in _LIVE:
        return SyncTransition(status=S.FAILED)
    table = {
        (S.QUEUED, V.STARTED): SyncTransition(status=S.RUNNING),
        (S.NEEDS_APPROVAL, V.STARTED): SyncTransition(status=S.RUNNING),
        (S.RUNNING, V.TAN_DEMANDED): SyncTransition(
            status=S.NEEDS_APPROVAL, end_dialog=trigger is SyncTrigger.SCHEDULED
        ),
        (S.NEEDS_APPROVAL, V.TAN_CONFIRMED): SyncTransition(status=S.RUNNING),
        (S.NEEDS_APPROVAL, V.TAN_TIMEOUT): SyncTransition(
            status=S.FAILED, error_code=E.BANK_APPROVAL_TIMEOUT, end_dialog=True
        ),
        (S.NEEDS_APPROVAL, V.WORKER_LOST): SyncTransition(status=S.NEEDS_APPROVAL),
        (S.RUNNING, V.STREAMS_EMPTY): SyncTransition(status=S.NOTHING_NEW),
        (S.RUNNING, V.NOTHING_NEW): SyncTransition(status=S.NOTHING_NEW),
        (S.RUNNING, V.NEW_LINES): SyncTransition(status=S.IMPORTING),
        (S.IMPORTING, V.IMPORTED): SyncTransition(status=S.DONE),
    }
    return table[(status, event)]


class TanStep(Schema):
    event: SyncEvent
    expect: SyncTransition | None = None
    error: RuleErrorExpect | None = None  # a refused event leaves the status as it was

    @model_validator(mode="after")
    def _one(self) -> Self:
        if (self.expect is None) == (self.error is None):
            raise ValueError("a step expects a transition or an error")
        return self


class TanFlowCase(Lenient):
    trigger: SyncTrigger
    start: SyncStatus
    steps: list[TanStep] = Field(min_length=1)

    @model_validator(mode="after")
    def _replay(self) -> Self:
        status = self.start
        for number, step in enumerate(self.steps, 1):
            try:
                got: SyncTransition | str = _next_state(status, step.event, self.trigger)
            except Refused as refused:
                got = refused.code
            except KeyError:
                raise ValueError(f"{self.id}: step {number} is not in the table") from None
            want = step.expect or step.error.code
            if got != want:
                raise ValueError(f"{self.id}: step {number} gives {got}, the case says {want}")
            if step.expect is not None:
                status = step.expect.status
        return self


class TanFlowFixture(_Fixture):
    cases: list[TanFlowCase] = Field(min_length=1)


# --- sync_archive.yaml: rules.bank_sync.build_archive(streams) -> bytes (D115) -------------


@cache
def _sample_members() -> dict[str, bytes]:
    with zipfile.ZipFile(CAMT_SAMPLE) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def member_name(number: int) -> str:
    """The archive's member names: camt-0001.xml … in the order the bank sent the streams."""
    return f"camt-{number:04d}.xml"


def reference_archive(streams: list[bytes]) -> bytes:
    """The header's byte contract: stored (no compression), fixed 1980-01-01 timestamps, the
    members named by member_name in the order received, nothing else in the archive."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for number, data in enumerate(streams, 1):
            info = zipfile.ZipInfo(member_name(number), date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, data)
    return buffer.getvalue()


class Stream(Schema):
    """One booked camt stream: a member of phase 2's sample ZIP by name, or `count` streams of
    `bytes` filler bytes (only for the refused cases, which never read them)."""

    sample: str | None = None
    bytes: int | None = Field(default=None, ge=1)
    count: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _one(self) -> Self:
        if (self.sample is None) == (self.bytes is None):
            raise ValueError("a stream is a sample or filler bytes")
        if self.sample is not None and self.sample not in _sample_members():
            raise ValueError(f"{self.sample} is not a member of {CAMT_SAMPLE.name}")
        return self


class ArchiveExpect(Schema):
    members: list[str] = Field(default_factory=list)
    lines: int | None = Field(default=None, ge=1)  # read_camt's booked lines of the archive
    identical_builds: bool = False  # same_input_same_bytes: two builds compared
    error: RuleErrorExpect | None = None

    @model_validator(mode="after")
    def _one(self) -> Self:
        if (self.error is None) == (not self.members):
            raise ValueError("expect members or an error")
        return self


class ArchiveCase(Lenient):
    streams: list[Stream] = Field(default_factory=list)
    expect: ArchiveExpect

    @model_validator(mode="after")
    def _round_trip(self) -> Self:
        count = sum(s.count for s in self.streams)
        size = sum(
            s.count * (len(_sample_members()[s.sample]) if s.sample else s.bytes)
            for s in self.streams
        )
        if count == 0:
            got = E.BANK_NOTHING_NEW
        elif count > MAX_ARCHIVE_MEMBERS or size > MAX_ARCHIVE_BYTES:
            got = ARCHIVE_REJECTED
        else:
            got = None
        if (self.expect.error.code if self.expect.error else None) != got:
            raise ValueError(f"{self.id}: the build gives {got}, the case says otherwise")
        if got is not None:
            return self
        if any(s.sample is None for s in self.streams):
            raise ValueError(f"{self.id}: an accepted case uses sample streams only")
        data = [_sample_members()[s.sample] for s in self.streams for _ in range(s.count)]
        if self.expect.members != [member_name(n) for n in range(1, len(data) + 1)]:
            raise ValueError(f"{self.id}: members are camt-0001.xml … in the order received")
        built = reference_archive(data)
        if self.expect.identical_builds and built != reference_archive(list(data)):
            raise ValueError(f"{self.id}: two builds differ")
        result = read_camt(built)
        if result.balance_error is not None:
            raise ValueError(f"{self.id}: read_camt reports {result.balance_error}")
        if len(result.statement.lines) != self.expect.lines:
            raise ValueError(
                f"{self.id}: read_camt reads {len(result.statement.lines)} lines, "
                f"the case says {self.expect.lines}"
            )
        return self


class ArchiveFixture(_Fixture):
    cases: list[ArchiveCase] = Field(min_length=1)


# --- sync_balance.yaml: phase 2's reconcile with balance_source bank; balance_drift (D117) --


class BalanceLine(Schema):
    ref: str  # AcctSvcrRef, the hash's reference (D116)
    booking_date: date
    amount: Money
    purpose: str = ""


class Anchor(Schema):
    amount: Money  # account.opening_balance
    on_date: date  # account.opening_balance_on ("on" is a YAML 1.1 boolean)


class BalanceExpect(Schema):
    lines_total: int = Field(ge=0)
    lines_new: int = Field(ge=0)
    balance_source: BalanceSource | None = None  # None: nothing_new, no import row
    declared_closing: Money | None = None
    stored_before: Money | None = None
    stored_through: Money | None = None
    result: ReconcileResult | None = None  # None: nothing_new
    drift: Money | None = None  # balance_drift: HKSAL − last reconciled closing, None if equal


def drift(hksal_balance: Decimal, last_reconciled_closing: Decimal) -> Decimal | None:
    """The header's balance_drift (D117): HKSAL − last reconciled closing, None when equal."""
    difference = hksal_balance - last_reconciled_closing
    return None if difference == 0 else difference


class BalanceCase(Lenient):
    account_id: UUID
    anchor: Anchor
    stored: list[BalanceLine] = Field(default_factory=list)  # from an earlier upload or sync
    file: list[BalanceLine] = Field(min_length=1)  # the sync's archive
    file_opening: Money | None = None  # the archive's first OPBD, when the camt states one
    file_closing: Money | None = None  # the archive's last CLBD, when the camt states one
    hksal: Money  # get_balance's booked balance, read in the same dialog
    last_reconciled_closing: Money | None = None  # read only for nothing_new
    expect: BalanceExpect

    def _hashes(self, lines: list[BalanceLine]) -> list[str]:
        return line_hashes(
            self.account_id,
            [
                LineForHash(
                    booking_date=x.booking_date,
                    amount=x.amount,
                    purpose=x.purpose,
                    bank_reference=x.ref,
                )
                for x in lines
            ],
        )

    @model_validator(mode="after")
    def _recompute(self) -> Self:
        known = set(self._hashes(self.stored))
        file_hashes = self._hashes(self.file)
        new = [x for x, h in zip(self.file, file_hashes, strict=True) if h not in known]
        got: dict[str, object] = {"lines_total": len(self.file), "lines_new": len(new)}
        if not new:
            if self.last_reconciled_closing is None:
                raise ValueError(f"{self.id}: a nothing_new case needs last_reconciled_closing")
            got |= {"drift": drift(self.hksal, self.last_reconciled_closing)}
        else:
            from_file = self.file_closing is not None
            declared = self.file_closing if from_file else self.hksal
            stored = [
                StoredLine(
                    id=UUID(int=n + 1),
                    account_id=self.account_id,
                    format="camt",
                    line_hash=h,
                    booking_date=x.booking_date,
                    amount=x.amount,
                )
                for n, (x, h) in enumerate(
                    [*zip(self.stored, self._hashes(self.stored), strict=True)]
                    + [
                        (x, h)
                        for x, h in zip(self.file, file_hashes, strict=True)
                        if h not in known
                    ]
                )
            ]
            first = min(x.booking_date for x in self.file)
            last = max(x.booking_date for x in self.file)
            sums = window_sums(
                stored, anchor_on=self.anchor.on_date, first_booking_on=first, last_booking_on=last
            )
            result = reconcile(
                self.anchor.amount,
                sums.stored_before,
                sums.stored_through,
                declared,
                self.file_opening if from_file else None,
                anchor_on=self.anchor.on_date,
                first_booking_on=first,
            )
            got |= {
                "balance_source": BalanceSource.FILE if from_file else BalanceSource.BANK,
                "declared_closing": declared,
                "stored_before": sums.stored_before,
                "stored_through": sums.stored_through,
                "result": result,
            }
        want = self.expect.model_dump(exclude_none=True)
        have = BalanceExpect.model_validate(got).model_dump(exclude_none=True)
        if have != want:
            raise ValueError(f"{self.id}: recomputed {have}, the case says {want}")
        return self


class BalanceFixture(_Fixture):
    cases: list[BalanceCase] = Field(min_length=1)


# --- inbound_mail.yaml: rules.inbound_mail.decide(facts, policy) (D121, §5.3) --------------


def _path(index: str) -> tuple[int, ...]:
    return tuple(int(n) for n in index.split("."))


def _rfc822_level(part: MailPart, parts: list[MailPart]) -> int:
    """How many forwarded messages (message/rfc822 parts) the part sits inside."""
    path = _path(part.index)
    return sum(
        1
        for p in parts
        if p.nested_rfc822 and len(_path(p.index)) < len(path)
        if path[: len(_path(p.index))] == _path(p.index)
    )


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].casefold()


def _decide(facts: InboundMailFacts, policy: InboundPolicy) -> MailDecision:
    def reject(code: str) -> MailDecision:
        return MailDecision(accept=False, code=code)

    if not facts.message_id_present:
        return reject(E.EMAIL_NO_MESSAGE_ID)
    if policy.token_address.casefold() not in {r.casefold() for r in facts.recipients}:
        return reject(E.EMAIL_RECIPIENT_MISMATCH)
    sender = facts.from_address.casefold()
    if sender not in {s.casefold() for s in policy.allowed_senders}:
        return reject(E.EMAIL_SENDER_NOT_ALLOWED)
    trusted = [a for a in facts.auth_results if a.authserv_id == policy.authserv_id]
    if not trusted:
        return reject(E.EMAIL_AUTH_MISSING)
    if not any(
        a.method in ("dkim", "spf")
        and a.result == "pass"
        and (a.domain or "").casefold() == _domain(sender)
        for a in trusted
    ):
        return reject(E.EMAIL_AUTH_FAILED)
    attachments, skipped = [], 0
    for part in sorted(facts.parts, key=lambda p: _path(p.index)):
        if part.filename is None or part.nested_rfc822 or _rfc822_level(part, facts.parts) > 1:
            continue
        if part.media_type in policy.media_types:
            attachments.append(part)
        else:
            skipped += 1
    if not attachments:
        return reject(E.EMAIL_NO_ATTACHMENT)
    if len(attachments) > policy.max_attachments or any(
        p.size_bytes > policy.max_bytes for p in attachments
    ):
        return reject(E.EMAIL_TOO_LARGE)
    return MailDecision(
        accept=True,
        code=E.EMAIL_ACCEPTED,
        attachments=[p.index for p in attachments],
        skipped=skipped,
    )


class MailCase(Lenient):
    facts: InboundMailFacts
    policy: InboundPolicy
    expect: MailDecision

    @model_validator(mode="after")
    def _recompute(self) -> Self:
        got = _decide(self.facts, self.policy)
        if got != self.expect:
            raise ValueError(f"{self.id}: decide gives {got}, the case says {self.expect}")
        return self


class MailFixture(_Fixture):
    cases: list[MailCase] = Field(min_length=1)


# --- reminders.yaml: rules.reminders.build_reminders(facts, today) (D122, D126, §6.2) ------

K = ReminderKind


class _Row(Schema):
    scope: ReminderScope | tuple[ReminderScope, ...]
    in_app: int
    email: tuple[int, ...]
    severity: Literal["action", "info"]
    cleared_by: Literal["ack", "auto"]
    args: frozenset[str]


# §6.2's table as data, the Grundsteuer kind by its label (the mode decides the leads).
_GRUNDSTEUER = {
    "grundsteuer.instalment.remind": _Row(
        scope=ReminderScope.PROPERTY,
        in_app=7,
        email=(7,),
        severity="action",
        cleared_by="auto",
        args=frozenset({"property"}),
    ),
    "grundsteuer.instalment.sepa_mandate": _Row(
        scope=ReminderScope.PROPERTY,
        in_app=0,
        email=(-3,),
        severity="action",
        cleared_by="auto",
        args=frozenset({"property"}),
    ),
}
_CATALOGUE = {
    K.CHECKLIST_DUE: _Row(
        scope=(ReminderScope.PROPERTY, ReminderScope.LOAN),
        in_app=14,
        email=(14, 0),
        severity="action",
        cleared_by="auto",
        args=frozenset(),  # by scope: {property, year} or {lender, year}, checked below
    ),
    K.RETURN_DEADLINE: _Row(
        scope=ReminderScope.WORKSPACE,
        in_app=90,
        email=(60, 14),
        severity="action",
        cleared_by="ack",
        args=frozenset({"year"}),
    ),
    K.QUESTIONNAIRE_OPEN: _Row(
        scope=ReminderScope.WORKSPACE,
        in_app=0,
        email=(-14,),
        severity="action",
        cleared_by="auto",
        args=frozenset({"year"}),
    ),
    K.WAGE_CERTIFICATE_MISSING: _Row(
        scope=ReminderScope.OWNER,
        in_app=0,
        email=(0,),
        severity="action",
        cleared_by="auto",
        args=frozenset({"year"}),
    ),
    K.LOAN_FIXED_RATE_END: _Row(
        scope=ReminderScope.LOAN,
        in_app=0,
        email=(0,),
        severity="action",
        cleared_by="ack",
        args=frozenset({"lender", "fixed_until", "months_before"}),
    ),
    K.LOAN_SPECIAL_TERMINATION: _Row(
        scope=ReminderScope.LOAN,
        in_app=0,
        email=(),
        severity="info",
        cleared_by="ack",
        args=frozenset({"lender"}),
    ),
    K.SPECULATION_END: _Row(
        scope=ReminderScope.PROPERTY,
        in_app=0,
        email=(),
        severity="info",
        cleared_by="ack",
        args=frozenset({"property", "tax_free_from"}),
    ),
    K.SETTLEMENT_DEADLINE: _Row(
        scope=ReminderScope.TENANCY,
        in_app=90,
        email=(90, 30),
        severity="action",
        cleared_by="ack",
        args=frozenset({"unit", "year"}),
    ),
    K.RENT_MISSING: _Row(
        scope=ReminderScope.TENANCY,
        in_app=0,
        email=(0,),
        severity="action",
        cleared_by="auto",
        args=frozenset({"unit", "month"}),
    ),
    K.SYNC_STALE: _Row(
        scope=ReminderScope.ACCOUNT,
        in_app=0,
        email=(0,),
        severity="info",
        cleared_by="auto",
        args=frozenset({"account"}),
    ),
}


def _latest_rule_set():
    """§6.2: the sale year's rule set, else the latest; a property still owned has no sale
    year, so the latest published one (its speculation_years)."""
    return registry.rule_set(max(registry.available_years()))


def _row(reminder: Reminder) -> _Row:
    if reminder.kind is K.GRUNDSTEUER_INSTALMENT:
        if reminder.label_code not in _GRUNDSTEUER:
            raise ValueError(f"{reminder.id}: label_code names the mode")
        return _GRUNDSTEUER[reminder.label_code]
    return _CATALOGUE[reminder.kind]


def _check_against_facts(r: Reminder, facts: ReminderFacts) -> None:
    """The kind's due date from its §6.2 formula, and its label arguments from the facts."""
    by_id = {
        **{p.id: p for p in facts.properties},
        **{x.id: x for x in facts.loans},
        **{t.id: t for t in facts.tenancies},
        **{o.id: o for o in facts.owners},
        **{a.id: a for a in facts.accounts},
    }
    subject = by_id.get(r.scope_id) if r.scope_id is not None else None
    if r.scope_id is not None and subject is None:
        raise ValueError(f"{r.id}: the scope is not in the facts")
    args, due = r.label_args, r.due_on
    want: date | None = None
    if r.kind is K.GRUNDSTEUER_INSTALMENT:
        if args != {"property": subject.label}:
            raise ValueError(f"{r.id}: label_args name the property")
        if due.year <= subject.purchase_date.year:
            raise ValueError(f"{r.id}: no instalment before the first full year (§9 GrStG)")
    elif r.kind is K.CHECKLIST_DUE:
        if r.label_code not in (
            "checklist.due.grundsteuerbescheid",
            "checklist.due.zinsbescheinigung",
            "checklist.due.wirtschaftsplan",
            "checklist.due.hausgeldabrechnung",
        ):
            raise ValueError(f"{r.id}: label_code is checklist.due.<dated item code>")
        name = {"property": "property", "loan": "lender"}[r.scope.value]
        label = subject.label if r.scope is ReminderScope.PROPERTY else subject.lender
        if set(args) != {name, "year"} or args[name] != label:
            raise ValueError(f"{r.id}: label_args are {{{name}, year}}")
    elif r.kind is K.RETURN_DEADLINE:
        want = filing_deadline(int(args["year"]))
    elif r.kind is K.QUESTIONNAIRE_OPEN:
        want = date(int(args["year"]) + 1, 1, 1)
    elif r.kind is K.WAGE_CERTIFICATE_MISSING:
        want = date(int(args["year"]) + 1, 3, 1)
    elif r.kind is K.LOAN_FIXED_RATE_END:
        if args["lender"] != subject.lender or args["fixed_until"] != str(subject.fixed_until):
            raise ValueError(f"{r.id}: label_args are the loan's")
        if args["months_before"] not in (12, 6):
            raise ValueError(f"{r.id}: 12 or 6 months before")
        want = _add_months(subject.fixed_until, -int(args["months_before"]))
    elif r.kind is K.LOAN_SPECIAL_TERMINATION:
        if args["lender"] != subject.lender:
            raise ValueError(f"{r.id}: label_args name the lender")
        want = _add_months(add_years(subject.disbursed_on, 10), -6)
    elif r.kind is K.SPECULATION_END:
        free = tax_free_from(subject.notarized_on, _latest_rule_set())
        if args != {"property": subject.label, "tax_free_from": str(free)}:
            raise ValueError(f"{r.id}: label_args are the property and the §23 date")
        want = _add_months(free, -12)
    elif r.kind is K.SETTLEMENT_DEADLINE:
        if subject.operating_costs_terms != "prepayment":
            raise ValueError(f"{r.id}: settlements only for a prepayment tenancy (D72, D91)")
        if args["unit"] != subject.unit_label:
            raise ValueError(f"{r.id}: label_args name the unit")
        want = date(int(args["year"]) + 1, 12, 31)
    elif r.kind is K.RENT_MISSING:
        if args["unit"] != subject.unit_label:
            raise ValueError(f"{r.id}: label_args name the unit, never the tenant")
        year, month = (int(n) for n in str(args["month"]).split("-"))
        want = date(year, month, subject.rent_due_day) + timedelta(days=5)
    elif r.kind is K.SYNC_STALE:
        if args["account"] != subject.label:
            raise ValueError(f"{r.id}: label_args name the account")
        want = (subject.last_sync_on or subject.connected_on) + timedelta(days=35)
    if want is not None and due != want:
        raise ValueError(f"{r.id}: the §6.2 formula gives {want}")


def check_reminders(reminders: list[Reminder], facts: ReminderFacts, today: date) -> None:
    """Every expected reminder against §6.2's table and formulas, and the list's order."""
    ids = [r.id for r in reminders]
    if len(ids) != len(set(ids)):
        raise ValueError("reminder ids are unique")
    if reminders != sorted(reminders, key=lambda r: (r.due_on, r.id)):
        raise ValueError("reminders are sorted by (due_on, id)")
    for r in reminders:
        row = _row(r)
        scopes = row.scope if isinstance(row.scope, tuple) else (row.scope,)
        if r.scope not in scopes:
            raise ValueError(f"{r.id}: scope is {scopes}")
        if r.visible_from != r.due_on - timedelta(days=row.in_app):
            raise ValueError(f"{r.id}: visible_from is due_on − {row.in_app} days")
        if tuple(r.email_leads) != row.email:
            raise ValueError(f"{r.id}: email_leads are {row.email}")
        if (r.severity, r.cleared_by) != (row.severity, row.cleared_by):
            raise ValueError(f"{r.id}: {row.severity}, cleared by {row.cleared_by}")
        if row.args and set(r.label_args) != row.args:
            raise ValueError(f"{r.id}: label_args are {sorted(row.args)}")
        if any("tenant" in key or "amount" in key for key in r.label_args):
            raise ValueError(f"{r.id}: never a tenant or an amount (D5, D122)")
        if r.due_on > today + timedelta(days=HORIZON_DAYS):
            raise ValueError(f"{r.id}: due beyond today + {HORIZON_DAYS} days")
        if r.due_on < date(today.year - 1, 1, 1):
            raise ValueError(f"{r.id}: due before 1 Jan of last year")
        _check_against_facts(r, facts)


class ReminderCase(Lenient):
    today: date
    facts: ReminderFacts
    expect: list[Reminder] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> Self:
        try:
            check_reminders(self.expect, self.facts, self.today)
        except ValueError as problem:
            raise ValueError(f"{self.id}: {problem}") from None
        return self


class ReminderFixture(_Fixture):
    cases: list[ReminderCase] = Field(min_length=1)


# --- notification_schedule.yaml: rules.reminders.due_notifications (D123, §6.4) ------------


def _due_notifications(
    reminders: list[Reminder],
    sent: list[SentNotification],
    today: date,
    recipients: list[UUID],
) -> list[NotificationDue]:
    # A sent row stops the item, lead and recipient; failed rows count the attempts used.
    done = {(s.reminder_id, s.lead_days, s.recipient_user_id) for s in sent if s.status == "sent"}
    tried: dict[tuple[str, int, UUID], int] = {}
    for s in sent:
        key = (s.reminder_id, s.lead_days, s.recipient_user_id)
        tried[key] = max(tried.get(key, 0), s.attempt)
    out = []
    for r in reminders:
        reached = [lead for lead in r.email_leads if r.due_on - timedelta(days=lead) <= today]
        if not reached:
            continue
        lead = min(reached)  # the latest send day reached; earlier leads are never sent late
        for user in recipients:
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


class NotificationCase(Lenient):
    today: date
    reminders: list[Reminder]
    sent: list[SentNotification] = Field(default_factory=list)
    recipients: list[UUID] = Field(min_length=1)  # the members with an email (D123)
    expect: list[NotificationDue] = Field(default_factory=list)

    @model_validator(mode="after")
    def _recompute(self) -> Self:
        # The table's two unique indexes: one row per attempt, at most one sent row per item.
        rows = [(s.reminder_id, s.lead_days, s.recipient_user_id, s.attempt) for s in self.sent]
        if len(set(rows)) != len(rows):
            raise ValueError(f"{self.id}: two sent rows claim one attempt")
        sent = [
            (s.reminder_id, s.lead_days, s.recipient_user_id)
            for s in self.sent
            if s.status == "sent"
        ]
        if len(set(sent)) != len(sent):
            raise ValueError(f"{self.id}: two sent rows for one item")
        for r in self.reminders:
            if tuple(r.email_leads) != _row(r).email:
                raise ValueError(f"{self.id}: {r.id}'s email_leads are not §6.2's")
        got = _due_notifications(self.reminders, self.sent, self.today, self.recipients)
        if got != self.expect:
            raise ValueError(f"{self.id}: due_notifications gives {got}")
        return self


class NotificationFixture(_Fixture):
    cases: list[NotificationCase] = Field(min_length=1)


MODELS: dict[str, type[Schema]] = {
    "sync_window": SyncWindowFixture,
    "tan_flow": TanFlowFixture,
    "sync_archive": ArchiveFixture,
    "sync_balance": BalanceFixture,
    "inbound_mail": MailFixture,
    "reminders": ReminderFixture,
    "notification_schedule": NotificationFixture,
}


@cache
def load(name: str) -> Schema:
    return MODELS[name].model_validate(raw(name))
