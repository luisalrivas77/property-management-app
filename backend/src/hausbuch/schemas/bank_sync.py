"""Phase 5: the FinTS read-only sync of the rental account (docs/phases/phase-5.md §4, §8, §9;
D115-D120, D127).

A sync is a BankSource that produces the same camt ZIP the online-banking export does and runs
the unchanged phase 2 import path (D115). These are the records and the pure rules' inputs and
outputs; the services, the job and the routes are step 1.

No model here carries a secret (D119): the PIN and the DK product id live only in Settings
(container secrets), the TAN is never typed into the app, and the FinTS client state is stored
MAC'd in bank_connection.client_data and is never part of a read model (only `state_present`).
The login name is accepted on input and returned masked (`login_masked`), never in clear.

The sets stored in a CHECK'd column (SyncStatus, SyncTrigger) live here, as phase 2 keeps
module-specific sets in their module (enums.py's docstring); tests/db/test_schema_consistency.py
keeps them equal to migration 0019's CHECKs.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import Field, field_validator

from .api import Patch
from .common import Money, MutableRecord, Schema
from .enums import BalanceSource

# --- Error codes (§4.4) used by the validators below; the full list is rules/errors.py ----
BANK_URL_NOT_HTTPS = "bank.url_not_https"

BankCode = Annotated[str, Field(pattern=r"^[0-9]{8}$")]  # Bankleitzahl, the bank_code CHECK
LoginName = Annotated[str, Field(min_length=1, max_length=100)]  # the Anmeldename (≤ 100)
FintsUrl = Annotated[str, Field(min_length=len("https://x"), max_length=500)]
# The masked login (D119): the first two and the last two characters around three stars, or
# three stars alone for a login shorter than six characters (where two plus two would show
# most of it). mask_login() is the one way to make it.
LoginMasked = Annotated[str, Field(pattern=r"^(.{2}\*{3}.{2}|\*{3})$")]
# Phase 6 D163: the named slot whose container secret holds the login's PIN
# (HAUSBUCH_FINTS_PIN for `primary`, HAUSBUCH_FINTS_PIN_<SLOT> otherwise). A label, never a
# secret; the bank_connection.credential_slot CHECK (migration 0025).
CredentialSlot = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
PRIMARY_SLOT = "primary"


def mask_login(login: str) -> str:
    """The login as events and read models show it (D119): never in clear."""
    return f"{login[:2]}***{login[-2:]}" if len(login) >= 6 else "***"


def _https_only(value: str | None) -> str | None:
    """bank_connection.fints_url CHECK (LIKE 'https://%'): a FinTS endpoint is https only.
    The message is a fixed string: it never echoes the URL."""
    if value is not None and not value.startswith("https://"):  # the CHECK is case-sensitive
        raise ValueError(f"{BANK_URL_NOT_HTTPS}: the FinTS URL must use https")
    if value is not None and any(c.isspace() for c in value):
        raise ValueError(f"{BANK_URL_NOT_HTTPS}: the FinTS URL must not contain spaces")
    return value


class SyncStatus(StrEnum):
    """bank_sync.status (migration 0019, D118). done, nothing_new and failed are final."""

    QUEUED = "queued"
    RUNNING = "running"
    NEEDS_APPROVAL = "needs_approval"  # a pushTAN approval is awaited (or, scheduled: needed)
    IMPORTING = "importing"  # the preview found new lines; upload + import_statement run
    DONE = "done"
    NOTHING_NEW = "nothing_new"  # D127: no new line, no document, no import row
    FAILED = "failed"


FINAL_SYNC_STATUSES = frozenset({SyncStatus.DONE, SyncStatus.NOTHING_NEW, SyncStatus.FAILED})


class SyncTrigger(StrEnum):
    """bank_sync.trigger (migration 0019): a person's button or the daily schedule (D120)."""

    MANUAL = "manual"
    SCHEDULED = "scheduled"


class SyncEvent(StrEnum):
    """What happened to a running sync; rules.bank_sync.next_state's input (tan_flow.yaml).

    NEW_LINES is not in §9's list: the preview finding at least one new line is what moves a
    run from running to importing (§4.3, case new_lines_importing_then_done), and none of the
    listed events says it (step 0 interpretation, reported as a spec finding)."""

    STARTED = "started"  # the job opened (or reopened) the dialog
    TAN_DEMANDED = "tan_demanded"  # a NeedTANResponse at login or on a read
    TAN_CONFIRMED = "tan_confirmed"  # the decoupled approval arrived (HKTAN process S)
    TAN_TIMEOUT = "tan_timeout"  # SYNC_APPROVAL_TIMEOUT_S passed without it
    STREAMS_EMPTY = "streams_empty"  # the bank returned no booked camt stream
    NOTHING_NEW = "nothing_new"  # the preview found every line known (D127)
    NEW_LINES = "new_lines"  # the preview found at least one new line (D127)
    IMPORTED = "imported"  # upload_document + import_statement finished
    ERROR = "error"  # any bank.* failure the job records on the row
    WORKER_LOST = "worker_lost"  # the worker stopped (a redeploy) while the row was open
    RETRY = "retry"  # step 1: bank.unreachable, the job asks Procrastinate to run it again


# --- Records (§4.2) ----------------------------------------------------------------------


class BankConnectionCreate(Schema):
    """POST /accounts/{id}/bank-connection. The PIN is never part of it (D119): it is the
    container secret HAUSBUCH_FINTS_PIN."""

    account_id: UUID
    bank_code: BankCode
    fints_url: FintsUrl
    login_name: LoginName
    credential_slot: CredentialSlot = PRIMARY_SLOT  # phase 6 D163

    @field_validator("fints_url")
    @classmethod
    def _https(cls, value: str | None) -> str | None:
        return _https_only(value)


class BankConnectionPatch(Patch):
    """PATCH /accounts/{id}/bank-connection. A changed URL or login keeps the stored client
    state only while its MAC verifies (step 1); null is not accepted for any field."""

    auto_sync: bool = Field(default=None)
    fetch_statements: bool = Field(default=None)  # D154 (7)
    fints_url: FintsUrl = Field(default=None)
    login_name: LoginName = Field(default=None)
    # Phase 6 D163: another slot names another container secret; the stored client state
    # stays (its MAC covers the login and the URL, not the PIN).
    credential_slot: CredentialSlot = Field(default=None)

    @field_validator("fints_url")
    @classmethod
    def _https(cls, value: str | None) -> str | None:
        return _https_only(value)


class BankConnection(MutableRecord):
    """GET /accounts/{id}/bank-connection: the login masked, the client state only as a flag
    (client_data and its MAC never leave the database)."""

    account_id: UUID
    bank_code: BankCode
    fints_url: FintsUrl
    login_masked: LoginMasked
    credential_slot: CredentialSlot = PRIMARY_SLOT  # phase 6 D163
    tan_mechanism: str | None = Field(default=None, max_length=8)  # the security function
    auto_sync: bool = False
    # D154 (7): every run also fetches the bank's PDF statements after its camt part.
    fetch_statements: bool = False
    sca_confirmed_at: datetime | None = None  # the last approval; validity shown as an estimate
    last_sync_at: datetime | None = None  # the last successful run
    # The last booking day a successful run brought from the bank, imported or already
    # stored (D141).
    last_booking_on: date | None = None
    state_present: bool = False  # client_data stored (and MAC'd)
    # Phase 6 step 3 (D163, D173): the container secret's name for the slot (never its value);
    # the bank's name from its parameters (HIBPA) once a run has read them; the text the
    # Bankabruf card shows while it waits for an approval; and whether the daily fetch may be
    # switched on (a DKB connection waits for two manual syncs without an approval).
    credential_variable: str = Field(default="HAUSBUCH_FINTS_PIN", max_length=60)
    bank_name: str | None = Field(default=None, max_length=60)
    approval_text: str = Field(default="In der S-pushTAN-App freigeben", max_length=100)
    auto_sync_allowed: bool = True


class BankParameters(Schema):
    """What the bank's parameters (BPD) say about the connection (phase 6 D173 (7), §14.1):
    read by the facade after the dialog's bootstrap, recorded on the connection when they
    change, logged. Public parameters of the bank only: no login, system id, IBAN, PIN or
    product id."""

    bank_name: str | None = Field(default=None, max_length=60)  # HIBPA's Kreditinstitutsname
    bpd_version: int | None = Field(default=None, ge=0)
    camt_formats: list[Annotated[str, Field(max_length=100)]] = Field(
        default_factory=list, max_length=20
    )  # HICAZS, as the bank spells them
    camt_readable: bool = False  # a camt.052 or camt.053 the import reads
    statement_pdf_by_number: bool = False  # HIEKPS v1, Kontoauszugsnummer erlaubt (D154)
    # The decoupled security functions HITANS offers (e.g. "946") and the HITANS versions.
    decoupled_methods: list[Annotated[str, Field(max_length=8)]] = Field(
        default_factory=list, max_length=20
    )
    approval_versions: list[Annotated[int, Field(ge=1, le=99)]] = Field(
        default_factory=list, max_length=10
    )


class BankCredentialSlots(Schema):
    """GET /bank-credential-slots (phase 6 D163): the slots whose container secret is set,
    names only, so the Bankabruf card can say which variable is still missing."""

    slots: list[CredentialSlot] = Field(default_factory=list)


class BankSync(MutableRecord):
    """One run (bank_sync, §4.2). lines_* are null until the preview ran; import_id is set
    once the run wrote its import (status importing or done), and a run that failed after
    that keeps it (status failed); never for nothing_new (D127)."""

    account_id: UUID
    connection_id: UUID
    trigger: SyncTrigger
    status: SyncStatus
    requested_from: date | None = None
    requested_to: date | None = None
    import_id: UUID | None = None
    lines_total: int | None = Field(default=None, ge=0)
    lines_new: int | None = Field(default=None, ge=0)
    balance_source: BalanceSource | None = None
    balance_checked: bool = False  # D117's drift check ran
    error_code: str | None = Field(default=None, max_length=64)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_by: UUID | None = None  # the user of a manual run


# --- Pure rules' inputs and outputs (rules/bank_sync.py, step 1) ---------------------------


class SyncWindowFacts(Schema):
    """rules.bank_sync.request_window's input: the account's balance anchor and the last
    booking day a successful sync brought (None before the first success)."""

    opening_balance_on: date  # account.opening_balance_on: the anchor day (D21)
    last_booking_on: date | None = None  # bank_connection.last_booking_on
    # Phase 6 D173 (4): the last reconciled closing date of the account's imports (None
    # without any); a first sync starts after it, so a CSV history and a FinTS camt that
    # share no bank reference never overlap. Built by phase 6 step 3.
    imports_through: date | None = None
    # The same over the reconciled imports whose lines carry no bank reference (every format
    # but camt: a CSV, a DKB CSV, a PDF; a FinTS sync's own import is camt), None without
    # any. After a success the 14-day overlap never reaches back into that history, which
    # dedup could not tell from the camt's lines (D173 (4), amended after the step 3 review).
    unreferenced_through: date | None = None


class SyncWindow(Schema):
    """rules.bank_sync.request_window output (sync_window.yaml): the HKCAZ date range."""

    start: date
    end: date
    may_need_approval: bool  # the window reaches beyond the SCA-exempt 90 days (§3)


class SyncPreview(Schema):
    """services.statements.preview_import output (D127): parse + hash, nothing written."""

    lines_total: int = Field(ge=0)
    lines_new: int = Field(ge=0)
    closing_balance: Money | None = None
    # The latest booking day among the file's booked lines, stored or not (D141: a run that
    # brings nothing new still moves the connection's last_booking_on there).
    last_booking_on: date | None = None


class SyncTransition(Schema):
    """rules.bank_sync.next_state output (tan_flow.yaml). §9 types it `SyncStatus | RuleError`;
    the cases also need what happens to the FinTS dialog (scheduled_tan_aborts: "dialog
    closed") and the code a transition into `failed` carries (timeout_fails), so the status
    comes with both. A refused transition (second_start_refused) is still a RuleError."""

    status: SyncStatus
    error_code: str | None = None  # set exactly when the rule itself decides the failure
    end_dialog: bool = False  # the job ends the FinTS dialog now (no polling)


class BalanceDrift(Schema):
    """rules.bank_sync.balance_drift's inputs, for sync_balance.yaml (D117)."""

    hksal_balance: Money  # HKSAL's booked balance
    last_reconciled_closing: Money  # the account's last reconciled closing


# --- The bank's PDF statements (D154, step 2b; rules/bank_statements.py) --------------------


class StatementKey(Schema):
    """One statement as the bank numbers it: its Kontoauszugsjahr and Kontoauszugsnummer
    (HKEKP v1: num 4 and num ..5)."""

    year: int = Field(ge=1000, le=9999)
    number: int = Field(ge=1, le=99999)

    def __str__(self) -> str:
        return f"{self.number}/{self.year}"


class StatementOutcome(StrEnum):
    """rules.bank_statements.read_answer: what one HKEKP answer means (D154 (2), (4))."""

    STATEMENT = "statement"  # the PDF is complete and valid: store it
    NOT_AVAILABLE = "not_available"  # refused (3010, 9xxx on HKEKP): ends that year's walk
    # 9370 on HKEKP: this access may not fetch statements at all; the fetch stops for the run
    # and the refusal is recorded on the account (account.statements_refused)
    NOT_PERMITTED = "not_permitted"
    CONTINUE = "continue"  # 3040 with an Aufsetzpunkt: ask for the next part
    INVALID = "invalid"  # delivered, but not a valid PDF: nothing stored, the year's walk ends
    STOPPED = "stopped"  # the message failed (9800, another 9xxx): the fetch stops for the run
    LOCKED = "locked"  # a lock code (9930, 9931, 3938): the run fails as D151 says


class StatementReading(Schema):
    """rules.bank_statements.read_answer output. `code` is the answer code that decided a
    refusal, a stop or a lock (four digits, never the bank's text); `reason` says why a
    delivered file is invalid; `pdf` is the decoded file (STATEMENT only) and is never logged."""

    outcome: StatementOutcome
    code: str | None = None
    dialog_ended: bool = False  # a message-level 9800: the bank ended the dialog
    reason: str | None = None
    pdf: bytes | None = Field(default=None, repr=False)


class BankStatement(Schema):
    """GET /accounts/{id}/bank-statements: one PDF statement the bank delivered (bank_statement,
    D154 (5)), with the document it is stored as and the run that fetched it. Never the PDF."""

    id: UUID
    account_id: UUID
    year: int = Field(ge=1000, le=9999)
    number: int = Field(ge=1, le=99999)
    document_id: UUID
    sync_id: UUID
    fetched_at: datetime
