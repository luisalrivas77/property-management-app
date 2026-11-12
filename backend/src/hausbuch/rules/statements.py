"""Statement dedup, reconciliation and camt report checks (docs/phases/phase-2.md §7.2, D21,
D22, D34). Pure functions: no I/O, no model calls; amounts are Decimal throughout.

Fixtures: tests/fixtures/phase2/line_hash.yaml, duplicates.yaml, reconcile.yaml, camt.yaml.

- line_hash / line_hashes: what makes an import idempotent (D22). The encoding is pinned by
  line_hash.yaml ("hausbuch-line-v1"); changing it re-hashes every stored line.
- likely_duplicates / resolve_duplicate: cross-format duplicates go to review (D22).
- window_sums / reconcile: balance anchors (D21). The service sums the stored window AFTER
  inserting the file's new lines, so an overlapping export reconciles.
- check_archive_members / check_reports: the ZIP rules and the per-report sum and chain
  checks for a camt ZIP of daily reports (D34).
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from hausbuch.schemas.bank import (
    LineForHash,
    ReconcileResult,
    ReconcileStatus,
    StoredLine,
    WindowSums,
)
from hausbuch.schemas.enums import DuplicateStatus

from .errors import RuleError

# --- Error codes (phase-2.md §12, plus the step 3 additions) ---------------------------

UNRECONCILED = "statement.unreconciled"
OPENING_MISMATCH = "statement.opening_mismatch"  # wins over unreconciled
BEFORE_ANCHOR = "statement.before_anchor"
UNKNOWN_FORMAT = "statement.unknown_format"
# Added in step 3 (not in §12's list):
UNPARSABLE = "statement.unparsable"  # the format is known but the content is malformed
ARCHIVE_REJECTED = "statement.archive_rejected"  # a ZIP breaking D34's member rules
ENTITY_DECLARED = "statement.entity_declared"  # XML with a DTD / entity declaration
REPORT_UNBALANCED = "statement.report_unbalanced"  # a report's OPBD + Σ Ntry != CLBD
CHAIN_BREAK = "statement.chain_break"  # a report's OPBD != the previous report's CLBD
ACCOUNT_MISMATCH = "statement.account_mismatch"  # the file is another account's statement
# Phase 6 D176 (4): a card statement that prints no payment IBAN has none to confirm.
NO_PAYMENT_IBAN = "statement.no_payment_iban"
UNSUPPORTED_CURRENCY = "statement.unsupported_currency"  # v1 is EUR only
EMPTY = "statement.empty"  # no booked line in the file
OWNER_ACCOUNT = "statement.owner_account"  # owner accounts never hold transactions
BALANCE_FROM_FILE = "statement.balance_from_file"  # the file declares its own balances
DUPLICATE_ACCOUNTS_DIFFER = "statement.duplicate_accounts_differ"
DUPLICATE_EXCLUDES_BOTH = "statement.duplicate_excludes_both"
# The account's opening balance cannot be derived from this import (no longer its earliest
# unreconciled import with a typed closing, or another import already reconciles).
OPENING_NOT_DERIVABLE = "statement.opening_not_derivable"

# Unit separator between the hashed fields (line_hash.yaml).
_SEP = "\x1f"
_VERSION = "hausbuch-line-v1"
_NOT_PROVIDED = "NOTPROVIDED"

# D34: a camt ZIP of daily reports.
MAX_ARCHIVE_MEMBERS = 400
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024  # uncompressed, all members together


# --- Line hash (D22) ------------------------------------------------------------------


def norm(value: str | None) -> str:
    """casefold, every run of whitespace collapsed to one space, ends stripped."""
    return " ".join((value or "").casefold().split())


def _amount(value: Decimal) -> str:
    # quantize, not float formatting: "-1234.56", "1400.00".
    return str(value.quantize(Decimal("0.01")))


def _reference(line: LineForHash) -> str | None:
    bank = (line.bank_reference or "").strip()
    if bank:
        return bank
    e2e = (line.end_to_end_ref or "").strip()
    if e2e and e2e != _NOT_PROVIDED:
        return e2e
    return None


def _tuple_key(account_id: UUID, line: LineForHash) -> tuple[str, ...]:
    """Every tuple-path field except the ordinal: what makes two lines 'identical'."""
    return (
        _VERSION,
        "line",
        str(account_id),
        line.booking_date.isoformat(),
        "" if line.value_date is None else line.value_date.isoformat(),
        _amount(line.amount),
        line.counterparty_iban_hmac or "",
        norm(line.counterparty_name),
        norm(line.purpose),
        line.booking_text or "",
    )


def _digest(fields: Sequence[str]) -> str:
    return hashlib.sha256(_SEP.join(fields).encode("utf-8")).hexdigest()


def line_hash(account_id: UUID, line: LineForHash, ordinal: int) -> str:
    """SHA-256 (lower-case hex) of one line. The bank's reference wins; otherwise the
    canonical tuple plus the line's ordinal among identical lines of its file."""
    if ordinal < 0:
        raise ValueError("ordinal must be >= 0")
    reference = _reference(line)
    if reference is not None:
        return _digest(
            (
                _VERSION,
                "ref",
                str(account_id),
                line.booking_date.isoformat(),
                _amount(line.amount),
                reference,
            )
        )
    return _digest((*_tuple_key(account_id, line), str(ordinal)))


def line_hashes(account_id: UUID, lines: Sequence[LineForHash]) -> list[str]:
    """The hashes of one file's lines, in order. A line's ordinal is the number of earlier
    lines of the file with an identical tuple, so [A, B, A] -> 0, 0, 1."""
    seen: dict[tuple[str, ...], int] = {}
    hashes = []
    for line in lines:
        key = _tuple_key(account_id, line)
        ordinal = seen.get(key, 0)
        seen[key] = ordinal + 1
        hashes.append(line_hash(account_id, line, ordinal))
    return hashes


# --- Cross-format duplicates (D22) --------------------------------------------------------


def _same_counterparty(a: StoredLine, b: StoredLine) -> bool:
    if a.counterparty_iban_hmac and a.counterparty_iban_hmac == b.counterparty_iban_hmac:
        return True
    name_a, name_b = norm(a.counterparty_name), norm(b.counterparty_name)
    return bool(name_a) and name_a == name_b


def likely_duplicates(new_line: StoredLine, existing: Sequence[StoredLine]) -> list[UUID]:
    """The ids (sorted) of existing lines that are likely the same payment as new_line: same
    account, amount and booking date, the same counterparty (IBAN hash or normalized name),
    a different hash and a different format. Excluded lines are no candidates."""
    return sorted(
        other.id
        for other in existing
        if other.id != new_line.id
        and other.excluded_as_duplicate_of is None
        and other.account_id == new_line.account_id
        and other.amount == new_line.amount
        and other.booking_date == new_line.booking_date
        and other.line_hash != new_line.line_hash
        and other.format != new_line.format
        and _same_counterparty(new_line, other)
    )


@dataclass(frozen=True)
class Exclusion:
    """A merged duplicate: excluded_id leaves every balance sum and gets no allocations."""

    excluded_id: UUID
    excluded_as_duplicate_of: UUID


def resolve_duplicate(
    transaction_id: UUID, other_transaction_id: UUID, status: DuplicateStatus
) -> Exclusion | None:
    """merged excludes the later-imported line (other_transaction_id); kept_both excludes
    nothing. The service checks both lines are on one account and that the keeper is not
    itself excluded (both copies would leave the balance)."""
    status = DuplicateStatus(status)
    if transaction_id == other_transaction_id:
        raise ValueError("a line is not its own duplicate")
    if status is DuplicateStatus.MERGED:
        return Exclusion(excluded_id=other_transaction_id, excluded_as_duplicate_of=transaction_id)
    if status is DuplicateStatus.KEPT_BOTH:
        return None
    raise ValueError("decide kept_both or merged")


# --- Reconciliation (D21) ---------------------------------------------------------------


def window_sums(
    lines: Sequence[StoredLine],
    *,
    anchor_on: date,
    first_booking_on: date,
    last_booking_on: date,
) -> WindowSums:
    """Σ of stored, non-excluded lines after the anchor day: before the file's first booking
    day, and up to and including its last one."""
    if last_booking_on < first_booking_on:
        raise ValueError("last_booking_on is before first_booking_on")
    before = through = Decimal("0.00")
    for line in lines:
        if line.excluded_as_duplicate_of is not None or line.booking_date <= anchor_on:
            continue
        if line.booking_date < first_booking_on:
            before += line.amount
        if line.booking_date <= last_booking_on:
            through += line.amount
    return WindowSums(stored_before=_money(before), stored_through=_money(through))


def reconcile(
    anchor: Decimal,
    stored_before: Decimal,
    stored_through: Decimal,
    declared_closing: Decimal | None,
    file_opening: Decimal | None,
    *,
    anchor_on: date,
    first_booking_on: date,
) -> ReconcileResult:
    """reconcile.yaml: before the anchor -> unreconciled (statement.before_anchor); no
    closing declared -> pending_balance; else reconciled iff both differences are zero, with
    statement.opening_mismatch winning over statement.unreconciled."""
    computed_opening = _money(anchor + stored_before)
    computed_closing = _money(anchor + stored_through)
    if first_booking_on <= anchor_on:
        return ReconcileResult(
            status=ReconcileStatus.UNRECONCILED,
            computed_opening=computed_opening,
            computed_closing=computed_closing,
            difference=None,
            error_code=BEFORE_ANCHOR,
        )
    if declared_closing is None:
        return ReconcileResult(
            status=ReconcileStatus.PENDING_BALANCE,
            computed_opening=computed_opening,
            computed_closing=computed_closing,
            difference=None,
        )
    difference = _money(declared_closing - computed_closing)
    opening_difference = None if file_opening is None else _money(file_opening - computed_opening)
    if opening_difference:
        status, code = ReconcileStatus.UNRECONCILED, OPENING_MISMATCH
    elif difference:
        status, code = ReconcileStatus.UNRECONCILED, UNRECONCILED
    else:
        status, code = ReconcileStatus.RECONCILED, None
    return ReconcileResult(
        status=status,
        computed_opening=computed_opening,
        computed_closing=computed_closing,
        difference=difference,
        opening_difference=opening_difference,
        error_code=code,
    )


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


# --- camt ZIP of daily reports (D34) -------------------------------------------------------


@dataclass(frozen=True)
class ArchiveMember:
    """One ZIP entry as its central directory describes it (read before any member is)."""

    name: str
    size: int  # uncompressed, as declared
    is_dir: bool = False
    encrypted: bool = False


def check_archive_members(members: Sequence[ArchiveMember]) -> None:
    """D34: flat `.xml` names only (no `/`, `\\` or `..`), no directories, nested archives
    or encrypted members, at most MAX_ARCHIVE_MEMBERS members and MAX_ARCHIVE_BYTES declared
    uncompressed bytes in total. Raises RuleError(statement.archive_rejected); the message
    never repeats a member name. The reader must still bound what it actually reads, since
    the declared sizes come from the file."""
    if not members:
        raise RuleError(ARCHIVE_REJECTED, "the archive holds no reports")
    if len(members) > MAX_ARCHIVE_MEMBERS:
        raise RuleError(ARCHIVE_REJECTED, f"more than {MAX_ARCHIVE_MEMBERS} members")
    names = set()
    for member in members:
        name = member.name
        if member.is_dir or "/" in name or "\\" in name or ".." in name or "\x00" in name:
            raise RuleError(ARCHIVE_REJECTED, "members must be flat file names")
        if not name.lower().endswith(".xml") or len(name) <= len(".xml"):
            raise RuleError(ARCHIVE_REJECTED, "members must be .xml reports")
        if member.encrypted:
            raise RuleError(ARCHIVE_REJECTED, "encrypted members are not read")
        if member.size < 0:
            raise RuleError(ARCHIVE_REJECTED, "a member has a negative size")
        if name in names:
            raise RuleError(ARCHIVE_REJECTED, "two members share a name")
        names.add(name)
    if sum(m.size for m in members) > MAX_ARCHIVE_BYTES:
        raise RuleError(ARCHIVE_REJECTED, f"more than {MAX_ARCHIVE_BYTES} bytes uncompressed")


@dataclass(frozen=True)
class ReportBalances:
    """One camt report (Rpt / Stmt): its booked balances, signed, and Σ of its booked
    entries. closing_on is the CLBD date: the report's own booking day."""

    opening: Decimal  # OPBD
    closing: Decimal  # CLBD
    closing_on: date
    entries_sum: Decimal


def order_reports(reports: Sequence[ReportBalances]) -> list[ReportBalances]:
    """Reports by their CLBD date. (D34 says by OPBD date; on the real export each OPBD is
    dated the previous report's day, so the CLBD date is the report's own day.)"""
    return sorted(reports, key=lambda r: r.closing_on)


def check_reports(reports: Sequence[ReportBalances]) -> str | None:
    """The first break, as an error code, or None. Each report must satisfy
    OPBD + Σ entries = CLBD (statement.report_unbalanced), and each OPBD must equal the
    previous report's CLBD (statement.chain_break). Two reports for the same day are a chain
    break: one day's balance cannot be stated twice. A break sends the whole import to review;
    it does not reject the file."""
    ordered = order_reports(reports)
    for report in ordered:
        if report.opening + report.entries_sum != report.closing:
            return REPORT_UNBALANCED
    for previous, report in zip(ordered, ordered[1:], strict=False):
        if report.closing_on == previous.closing_on or report.opening != previous.closing:
            return CHAIN_BREAK
    return None


# --- Direct debits (phase 5 D126) ---------------------------------------------------------

# ISO 20022 bank transaction codes (camt Domn/Fmly): payments (PMNT), received direct debits
# (RDDT) and issued direct debits (IDDT). Checked on the real rental account on 10 Nov 2026
# (codes only): direct debits read PMNT/RDDT/ESDD, the owners' own transfers PMNT/ICDT/ESCT,
# incoming rent PMNT/RCDT/ESCT or PMNT/RRCT/ESCT.
DIRECT_DEBIT_DOMAIN = "PMNT"
DIRECT_DEBIT_FAMILIES = frozenset({"RDDT", "IDDT"})


def is_direct_debit(raw: object) -> bool:
    """Whether a stored line (transaction.raw) is a SEPA direct debit: its camt bank
    transaction code has domain PMNT and family RDDT or IDDT. A line without a code (a CSV
    import) is never one, so the Grundsteuer mode is never inferred from it (D126)."""
    if not isinstance(raw, dict):
        return False
    code = raw.get("bank_transaction_code")
    if not isinstance(code, dict):
        return False
    return code.get("domain") == DIRECT_DEBIT_DOMAIN and code.get("family") in DIRECT_DEBIT_FAMILIES
