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

    @field_validator("fints_url")
    @classmethod
    def _https(cls, value: str | None) -> str | None:
        return _https_only(value)


class BankConnectionPatch(Patch):
    """PATCH /accounts/{id}/bank-connection. A changed URL or login keeps the stored client
    state only while its MAC verifies (step 1); null is not accepted for any field."""

    auto_sync: bool = Field(default=None)
    fints_url: FintsUrl = Field(default=None)
    login_name: LoginName = Field(default=None)

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
    tan_mechanism: str | None = Field(default=None, max_length=8)  # the security function
    auto_sync: bool = False
    sca_confirmed_at: datetime | None = None  # the last approval; validity shown as an estimate
    last_sync_at: datetime | None = None  # the last successful run
    last_booking_on: date | None = None  # the last booking day a successful run brought
    state_present: bool = False  # client_data stored (and MAC'd)


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
