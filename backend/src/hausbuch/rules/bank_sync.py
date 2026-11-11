"""The FinTS sync's pure rules (docs/phases/phase-5.md §4, D115-D120, D127).

No I/O, no network, no database, no logging. Fixtures: tests/fixtures/phase5/sync_window.yaml,
tan_flow.yaml, sync_archive.yaml, sync_balance.yaml (loaded through
tests/fixture_models_phase5.py, whose reference functions these must equal).

- `request_window(facts, today)`: the HKCAZ date range (D120).
- `next_state(status, event, trigger)`: the sync row's state machine (D118). The fixture's
  table, plus the pairs step 0 left to step 1 (§12.1), decided here:
    * queued + WORKER_LOST and running + WORKER_LOST → queued: the job's retry (or the
      service, for a row whose job is gone) starts the run over with a fresh dialog. Nothing
      of a run is written before the import, and the import commits with `done` in one
      transaction (jobs/bank_sync.py), so a restart never repeats a write.
    * importing + WORKER_LOST is undefined: a row is never left `importing` (same reason).
    * running + RETRY → queued: `bank.unreachable` before the job asks Procrastinate to retry.
    * any event on a final row, and every pair not listed (e.g. queued + TAN_DEMANDED), is
      undefined and raises ValueError: a caller that reaches one has a bug.
- `build_archive(streams)`: the deterministic camt ZIP (D115).
- `balance_drift`, `declared_closing`: D117.
- `blz_from_iban`, `refused_code`, `product_id_usable`, `is_stale`: small helpers the service,
  the job and the card share.
- `BankAnswer`, `deciding_code`, `redact_bank_text`: the bank's answers and which of them says
  why a run was refused (D151).
"""

import io
import re
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from hausbuch.schemas.bank_sync import (
    SyncEvent,
    SyncStatus,
    SyncTransition,
    SyncTrigger,
    SyncWindow,
    SyncWindowFacts,
)
from hausbuch.schemas.enums import BalanceSource

from . import errors as E
from .errors import RuleError
from .statements import ARCHIVE_REJECTED, MAX_ARCHIVE_BYTES, MAX_ARCHIVE_MEMBERS

SCA_EXEMPT_DAYS = 90  # §3: the SCA exemption covers the last 90 days of account history
OVERLAP_DAYS = 14  # D120: a later window starts 14 days before the last booking day
PRODUCT_ID_MAX = 25  # the DK registration number's length (registration letter, 11 Nov 2026)
MANUAL_STARTS_PER_HOUR = 4  # §4.7: a stuck phone cannot run up the bank's SCA counters
# A live row older than the approval timeout plus this is taken as abandoned by its job (a
# worker lost for good, a job that died without writing the row) and may be restarted.
STALE_GRACE_S = 300

# --- The window (sync_window.yaml) ---------------------------------------------------------


def request_window(facts: SyncWindowFacts, today: date) -> SyncWindow:
    """[last_booking_on − 14 days, today] after a successful sync, else from the day after the
    anchor (phase 2's reconcile refuses a line on the anchor day), never before it. A window
    longer than 90 days may need a pushTAN approval (§3). Raises bank.window_invalid when the
    start would be after today (an anchor dated today or later)."""
    first_storable = facts.opening_balance_on + timedelta(days=1)
    if facts.last_booking_on is None:
        start = first_storable
    else:
        start = max(facts.last_booking_on - timedelta(days=OVERLAP_DAYS), first_storable)
    if start > today:
        raise RuleError(E.BANK_WINDOW_INVALID, "the sync window would start after today")
    return SyncWindow(
        start=start, end=today, may_need_approval=(today - start).days > SCA_EXEMPT_DAYS
    )


# --- The state machine (tan_flow.yaml) -----------------------------------------------------

_S, _V = SyncStatus, SyncEvent
LIVE_SYNC_STATUSES = frozenset({_S.QUEUED, _S.RUNNING, _S.NEEDS_APPROVAL, _S.IMPORTING})

_TABLE = {
    (_S.QUEUED, _V.STARTED): SyncTransition(status=_S.RUNNING),
    (_S.NEEDS_APPROVAL, _V.STARTED): SyncTransition(status=_S.RUNNING),
    (_S.NEEDS_APPROVAL, _V.TAN_CONFIRMED): SyncTransition(status=_S.RUNNING),
    (_S.NEEDS_APPROVAL, _V.TAN_TIMEOUT): SyncTransition(
        status=_S.FAILED, error_code=E.BANK_APPROVAL_TIMEOUT, end_dialog=True
    ),
    (_S.NEEDS_APPROVAL, _V.WORKER_LOST): SyncTransition(status=_S.NEEDS_APPROVAL),
    (_S.RUNNING, _V.STREAMS_EMPTY): SyncTransition(status=_S.NOTHING_NEW),
    (_S.RUNNING, _V.NOTHING_NEW): SyncTransition(status=_S.NOTHING_NEW),
    (_S.RUNNING, _V.NEW_LINES): SyncTransition(status=_S.IMPORTING),
    (_S.IMPORTING, _V.IMPORTED): SyncTransition(status=_S.DONE),
    # Step 1's decisions (module docstring):
    (_S.QUEUED, _V.WORKER_LOST): SyncTransition(status=_S.QUEUED),
    (_S.RUNNING, _V.WORKER_LOST): SyncTransition(status=_S.QUEUED),
    (_S.RUNNING, _V.RETRY): SyncTransition(status=_S.QUEUED),
}


def next_state(status: SyncStatus, event: SyncEvent, trigger: SyncTrigger) -> SyncTransition:
    """The sync row's next status, the code a rule-decided failure carries, and whether the
    job ends the FinTS dialog now. A scheduled run that meets a TAN demand ends the dialog at
    once (D120: no one is watching the phone). Raises RuleError(bank.sync_running) for a
    start while a run is running or importing, ValueError for an undefined pair."""
    if event is _V.STARTED and status in (_S.RUNNING, _S.IMPORTING):
        raise RuleError(E.BANK_SYNC_RUNNING, "a sync of this account is running")
    if event is _V.ERROR and status in LIVE_SYNC_STATUSES:
        return SyncTransition(status=_S.FAILED)
    if status is _S.RUNNING and event is _V.TAN_DEMANDED:
        return SyncTransition(status=_S.NEEDS_APPROVAL, end_dialog=trigger is SyncTrigger.SCHEDULED)
    try:
        return _TABLE[(status, event)]
    except KeyError:
        raise ValueError(f"no transition from {status.value} on {event.value}") from None


def is_stale(updated_at: datetime, now: datetime, approval_timeout_s: int) -> bool:
    """A live row nobody has touched for longer than a run can take (the approval wait plus
    STALE_GRACE_S): its job is gone, so a person's start may take the row over."""
    return now - updated_at > timedelta(seconds=approval_timeout_s + STALE_GRACE_S)


# --- The archive (sync_archive.yaml) -------------------------------------------------------


def member_name(number: int) -> str:
    return f"camt-{number:04d}.xml"


def build_archive(streams: list[bytes]) -> bytes:
    """The booked camt streams as one ZIP, byte for byte reproducible (D115): stored (no
    compression), members camt-0001.xml … in the order the bank sent them, every timestamp
    1980-01-01 00:00. No stream → bank.nothing_new; more than D34's member or size limit →
    statement.archive_rejected (the importer would refuse it anyway)."""
    if not streams:
        raise RuleError(E.BANK_NOTHING_NEW, "the bank sent no booked statement")
    if len(streams) > MAX_ARCHIVE_MEMBERS:
        raise RuleError(ARCHIVE_REJECTED, f"more than {MAX_ARCHIVE_MEMBERS} members")
    if sum(len(s) for s in streams) > MAX_ARCHIVE_BYTES:
        raise RuleError(ARCHIVE_REJECTED, f"more than {MAX_ARCHIVE_BYTES} bytes")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for number, data in enumerate(streams, 1):
            info = zipfile.ZipInfo(member_name(number), date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, data)
    return buffer.getvalue()


# --- The balance (sync_balance.yaml) -------------------------------------------------------


def balance_drift(hksal_balance: Decimal, last_reconciled_closing: Decimal) -> Decimal | None:
    """D117: HKSAL's booked balance minus the account's last reconciled closing balance, None
    when they agree. Only for a run that brought nothing new."""
    difference = hksal_balance - last_reconciled_closing
    return None if difference == 0 else difference


def declared_closing(
    file_closing: Decimal | None, hksal_balance: Decimal | None
) -> tuple[Decimal, BalanceSource] | None:
    """D117: the closing balance a sync's import is reconciled against. The file's own last
    CLBD wins (balance_source file, phase 2's D34 unchanged); else HKSAL's booked balance
    (balance_source bank); None when the bank stated neither (the import then waits at
    pending_balance, as a CSV upload does)."""
    if file_closing is not None:
        return file_closing, BalanceSource.FILE
    if hksal_balance is not None:
        return hksal_balance, BalanceSource.BANK
    return None


# --- Small helpers -------------------------------------------------------------------------

_GERMAN_IBAN = re.compile(r"^DE[0-9]{2}([0-9]{8})[0-9]{10}$")
_REFUSED = re.compile(r"^9[0-9]{3}$")


def blz_from_iban(iban: str | None) -> str | None:
    """The Bankleitzahl inside a German IBAN (characters 5-12), else None. The account page
    pre-fills the connection form with it (principle 7: nobody has to know where it is)."""
    if not iban:
        return None
    match = _GERMAN_IBAN.match(iban.replace(" ", "").upper())
    return match.group(1) if match else None


def refused_code(code: object) -> str | None:
    """A FinTS answer code that refuses the request (9xxx), as its four digits, else None.
    Only the code is ever kept: the bank's text may quote the login (§4.3)."""
    text = str(code) if code is not None else ""
    return text if _REFUSED.match(text) else None


# Answer codes that only say "something failed" without a reason (D151, from the DK's list
# of answer codes, Rückmeldungscodes 3.0/4.x of 3 Feb 2026): 9000 status unclear, 9010
# processing not possible (its examples range from "too large" to "system not available"),
# 9050 the message is partly faulty, 9800 dialog aborted, 9999 not processed for technical
# reasons. The bank sends the reason as another code of the same answer, usually at the
# segment level (HIRMS).
GENERAL_REFUSALS = frozenset({"9000", "9010", "9050", "9800", "9999"})
# A warning (3xxx) that still ends the run: python-fints raises on 3938 "access temporarily
# locked" (FinTSClientTemporaryAuthError). It decides only when no 9xxx code was sent.
REFUSING_WARNINGS = frozenset({"3938"})
# At most this many answers go into the log line (the first real refusal had seven).
REFUSED_CODES_LOGGED = 12

MESSAGE_LEVEL = "message"  # HIRMG: about the whole message
SEGMENT_LEVEL = "segment"  # HIRMS: about one segment of ours


@dataclass(frozen=True)
class BankAnswer:
    """One answer of the bank (D151): its level, its four-digit code, for a segment-level
    answer the segment of ours it refers to (`HKVVB:4`, else `segment 7` or `unknown`), and
    its text redacted by `redact_bank_text` (empty when not kept)."""

    level: str
    code: str
    segment: str | None = None
    text: str = ""

    def describe(self) -> str:
        """`9078 → HKVVB:4: text`, as the trace and the job's log print it."""
        target = MESSAGE_LEVEL if self.level == MESSAGE_LEVEL else self.segment or "unknown"
        return f"{self.code} → {target}: {self.text}"


# A bank answer's text in the log (Luis, 11 Nov 2026, after the first syncs were refused with
# 9010 and no text): the login and every run of five or more digits are blanked, the text cut.
_LONG_NUMBER = re.compile(r"[0-9][0-9 ]{3,}[0-9]")
BANK_TEXT_MAX = 160


def redact_bank_text(text: object, *hidden: str | None) -> str:
    """The bank's answer text with each of `hidden` (the login, the customer id) replaced,
    case-insensitively, and every number of five digits or more blanked, at most
    BANK_TEXT_MAX characters. The caller hides the PIN as well, should a bank ever echo it."""
    out = str(text or "")
    for value in hidden:
        if value and value.strip():
            out = re.sub(re.escape(value.strip()), "<hidden>", out, flags=re.IGNORECASE)
    out = _LONG_NUMBER.sub(
        lambda m: "#" * 5 if sum(c.isdigit() for c in m.group()) >= 5 else m.group(), out
    )
    return out[:BANK_TEXT_MAX]


def deciding_code(answers: Sequence[BankAnswer]) -> str | None:
    """The code that says why the bank refused (D151): the first 9xxx code that is not
    merely general, a segment-level one before any message-level one (the bank sends HIRMG's
    9050/9800/9010 first and the segment's reason, e.g. 9078 or 9942, after it); else the
    first 9xxx code; else a refusing warning (3938); None when there is none of these."""
    refusals = [a for a in answers if refused_code(a.code) is not None]
    for level in (SEGMENT_LEVEL, MESSAGE_LEVEL):
        for answer in refusals:
            if answer.level == level and answer.code not in GENERAL_REFUSALS:
                return answer.code
    if refusals:
        return refusals[0].code
    return next((a.code for a in answers if a.code in REFUSING_WARNINGS), None)


def product_id_usable(product_id: str | None) -> bool:
    """The DK registration number fits HKVVB's product name: set, not blank, at most 25
    characters, no surrounding blanks. Anything else fails a sync with bank.not_configured;
    it never stops the container (Settings only refuses a PIN without a product id, or the
    reverse)."""
    return (
        product_id is not None
        and 0 < len(product_id) <= PRODUCT_ID_MAX
        and product_id == product_id.strip()
    )
