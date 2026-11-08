"""Bank accounts, statement imports and transactions (docs/phases/phase-2.md §4.3, §7).

Transactions are immutable as imported (principle 6 applied to bank lines): the one column
that ever changes is excluded_as_duplicate_of, set once when a cross-format duplicate is
merged. Reconciliation works on balance anchors (D21): an opening balance per account and a
declared closing balance per import.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Protocol, Self
from uuid import UUID

from pydantic import ConfigDict, Field, JsonValue, model_validator

from .allocation import AllocationCategory
from .common import IbanInput, MaskedIban, Money, MutableRecord, Record, Schema, Sha256Hex
from .enums import AccountSource, BalanceSource, Currency, DuplicateStatus, ImportStatus

# --- Accounts ------------------------------------------------------------------------


class AccountCreate(Schema):
    # Validation errors never echo input: IBANs must not reach a log or an API error.
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    label: str = Field(min_length=1, max_length=100)
    bank: str = Field(min_length=1)
    iban: IbanInput  # write-only; stored as iban_masked + keyed HMAC
    opening_balance: Money  # the anchor (D21)
    opening_balance_on: date
    # One of the owners' private accounts: recognized by IBAN for owner transfers (phase 2
    # input point 1). It never holds transactions.
    is_owner_account: bool = False


class Account(MutableRecord):
    label: str
    bank: str
    iban_masked: MaskedIban
    source: AccountSource
    opening_balance: Money
    opening_balance_on: date
    is_owner_account: bool


# --- Parsing (BankSource) ------------------------------------------------------------


class StatementFormat(StrEnum):
    """statement_import.format (migration 0006)."""

    CSV_CAMT = "csv_camt"  # Sparkasse CSV-CAMT V2: no balances
    CAMT = "camt"  # camt.052 / .053 XML, one file or a ZIP of daily reports; balances (D33/D34)
    PDF = "pdf"  # Kontoauszug, read by the extract job


class StatementLine(Schema):
    """The BankSource output, format-independent. The counterparty IBAN is plaintext here
    only; it is masked and HMAC'd on store."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    booking_date: date
    value_date: date | None = None
    amount: Money  # signed: credit positive, debit negative
    currency: Currency = Currency.EUR
    counterparty_name: str | None = None
    counterparty_iban: str | None = None
    purpose: str = ""
    booking_text: str | None = None  # Buchungstext
    bank_reference: str | None = None  # camt AcctSvcrRef, else NtryRef
    end_to_end_ref: str | None = None  # camt EndToEndId / CSV Kundenreferenz (End-to-End)
    mandate_ref: str | None = None
    pending: bool = False  # camt PDNG; CSV "Umsatz vorgemerkt" lines never get this far
    raw: dict[str, JsonValue] = Field(default_factory=dict)


class ParsedStatement(Schema):
    format: StatementFormat
    account_iban: str | None
    opening_balance: Money | None  # None for CSV (D21)
    closing_balance: Money | None
    lines: list[StatementLine]
    warnings: list[str] = Field(default_factory=list)


class BankSource(Protocol):
    """services/bank/: csv_camt.py, camt.py, pdf.py (via the extract job)."""

    def parse(self, data: bytes, media_type: str) -> ParsedStatement: ...


# --- Imports and transactions --------------------------------------------------------


class StatementImport(MutableRecord):
    account_id: UUID
    document_id: UUID
    format: StatementFormat
    lines_total: int = Field(ge=0)
    lines_new: int = Field(ge=0)
    lines_skipped: int = Field(ge=0)
    first_booking_on: date | None
    last_booking_on: date | None
    declared_opening: Money | None
    declared_closing: Money | None
    balance_source: BalanceSource
    computed_opening: Money | None
    computed_closing: Money | None
    status: ImportStatus
    error_code: str | None


class DeclareBalanceBody(Schema):
    """POST /imports/{id}/balances: the closing balance Luis reads in online banking."""

    closing_balance: Money
    opening_balance: Money | None = None


class TransactionAllocationStatus(StrEnum):
    """Where a bank line stands, for Konten › Umsätze (read-only, computed per read from the
    line, its import, its open possible duplicate and its current allocation rows; first
    match wins):

    - excluded: merged away as a duplicate (excluded_as_duplicate_of is set);
    - not_reconciled: its import does not reconcile (yet), so the line cannot be allocated
      (allocation.import_not_reconciled) and is not in the review queue;
    - duplicate_open: the later line of an open possible duplicate, decided first;
    - proposed: at least one current (not superseded, D23) row is proposed;
    - confirmed: every current row is confirmed;
    - open: no current row ("Nicht zugeordnet").
    """

    EXCLUDED = "excluded"
    NOT_RECONCILED = "not_reconciled"
    DUPLICATE_OPEN = "duplicate_open"
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    OPEN = "open"


class Transaction(Record):
    """Read shape. The counterparty IBAN is shown masked only. allocation_status and
    allocation_categories are computed on read, never stored: the distinct categories of
    the line's current allocation rows, the largest amount first."""

    account_id: UUID
    import_id: UUID
    line_hash: Sha256Hex
    booking_date: date
    value_date: date | None
    amount: Money
    currency: Currency
    counterparty_name: str | None
    counterparty_iban_masked: MaskedIban | None
    purpose: str
    booking_text: str | None
    bank_reference: str | None
    end_to_end_ref: str | None
    excluded_as_duplicate_of: UUID | None = None
    allocation_status: TransactionAllocationStatus
    allocation_categories: list[AllocationCategory]


class PossibleDuplicate(Record):
    """transaction_id was stored first; other_transaction_id is the later-imported line,
    the one `merged` excludes."""

    transaction_id: UUID
    other_transaction_id: UUID
    status: DuplicateStatus
    decided_at: datetime | None = None


class DuplicateDecisionBody(Schema):
    """POST /possible-duplicates/{id}."""

    status: DuplicateStatus

    @model_validator(mode="after")
    def _decided(self) -> Self:
        if self.status is DuplicateStatus.OPEN:
            raise ValueError("decide kept_both or merged")
        return self


# --- Inputs and outputs of rules.statements -------------------------------------------


class LineForHash(Schema):
    """A parsed line as rules.statements.line_hash reads it (line_hash.yaml). The service
    builds it from a StatementLine after computing the counterparty IBAN's keyed HMAC: the
    hash never sees the plaintext IBAN, so a stored hash cannot be brute-forced back to it."""

    booking_date: date
    value_date: date | None = None
    amount: Money
    counterparty_name: str | None = None
    counterparty_iban_hmac: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    purpose: str = ""
    booking_text: str | None = None
    bank_reference: str | None = None
    end_to_end_ref: str | None = None


class StoredLine(Schema):
    """A transaction as the dedup and balance rules see it. counterparty_iban_hmac is hex;
    counterparty_name is as stored (the rule normalizes it)."""

    id: UUID
    account_id: UUID
    format: StatementFormat
    line_hash: Sha256Hex
    booking_date: date
    amount: Money
    counterparty_iban_hmac: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    counterparty_name: str | None = None
    excluded_as_duplicate_of: UUID | None = None


class WindowSums(Schema):
    """Σ of stored, non-excluded transactions before the file's first booking day and up to
    and including its last one (§7.2)."""

    stored_before: Money
    stored_through: Money


class ReconcileStatus(StrEnum):
    RECONCILED = "reconciled"
    UNRECONCILED = "unreconciled"
    PENDING_BALANCE = "pending_balance"  # stored as import status `imported`


class ReconcileResult(Schema):
    """difference = declared_closing - computed_closing, null while no closing is declared;
    opening_difference = the file's declared opening - computed_opening, null when the file
    declares no opening. Both are reported whenever they can be computed, so a mismatch can
    be explained. error_code: statement.before_anchor; else statement.opening_mismatch when
    the opening differs (it wins); else statement.unreconciled when the closing differs."""

    status: ReconcileStatus
    computed_opening: Money
    computed_closing: Money
    difference: Money | None
    opening_difference: Money | None = None
    error_code: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.status is ReconcileStatus.UNRECONCILED) != (self.error_code is not None):
            raise ValueError("error_code exactly when unreconciled")
        if self.status is ReconcileStatus.RECONCILED and (
            self.difference != 0 or self.opening_difference not in (None, 0)
        ):
            raise ValueError("a reconciled import has no difference")
        return self
