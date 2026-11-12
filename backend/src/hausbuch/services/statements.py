"""Statement imports, transactions and possible duplicates (docs/phases/phase-2.md §7, D21, D22).

Import flow (§7.2), one transaction per upload, under the account row's lock:

    parse -> check the file is this account's -> store the original (upload_document)
    -> hash every line -> insert the new transactions (known hashes are skipped and counted)
    -> possible_duplicate rows for likely cross-format duplicates
    -> declared balances from the file (camt) or none until POST /imports/{id}/balances (CSV)
    -> reconcile -> status + statement.imported and statement.reconciled / .unreconciled
    -> re-check the account's other imports, whose stored window the new lines may change.

A reconciliation sums the *stored* window (D21), so every write that changes what is stored
or which lines count (a new import, a merged duplicate, a moved anchor) re-checks the
account's imports and records each changed result as a new event. The account row is locked
first by every one of these writes, so their sums never interleave.

Privacy: the counterparty IBAN is stored masked + keyed HMAC only. `transaction.raw` never
holds a plaintext IBAN: the parsers leave the IBAN columns out and `_scrub` masks anything
else that parses as one. Messages and events never quote a name, purpose or IBAN.

Matching starts in `_on_reconciled`, the one place an import becomes reconciled: it queues
`match_import` in the same transaction (phase 2 step 4). A possible duplicate decided
kept_both queues `match_transaction` for the later line, which waited for the decision.
"""

import hmac
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import Connection, Row, Select, exists, func, insert, select, update
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import possible_duplicate as duplicate_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.jobs import enqueue
from hausbuch.rules import statements as rules
from hausbuch.rules.errors import RuleError
from hausbuch.rules.iban import mask_ibans_in_text
from hausbuch.schemas.api import InboxItem, InboxItemKind
from hausbuch.schemas.bank import (
    DeclareBalanceBody,
    LineForHash,
    ParsedStatement,
    PossibleDuplicate,
    ReconcileStatus,
    StatementFormat,
    StatementImport,
    StatementLine,
    StoredLine,
    Transaction,
    TransactionAllocationStatus,
)
from hausbuch.schemas.bank_sync import SyncPreview
from hausbuch.schemas.common import TAX_YEAR_MIN
from hausbuch.schemas.document import DocumentTyping
from hausbuch.schemas.enums import (
    AllocationStatus,
    BalanceSource,
    DocumentSource,
    DocumentType,
    DuplicateStatus,
    EntityType,
    ImportStatus,
)
from hausbuch.schemas.events import (
    EventType,
    StatementBalanceChecked,
    StatementDuplicateResolved,
    StatementImported,
)
from hausbuch.storage import BlobStore

from .bank import parse_statement
from .context import CommandContext
from .documents import type_document, upload_document
from .errors import ConflictError
from .events import append_event
from .iban import ProtectedIban, protect_iban
from .lookup import require_row

POSSIBLE_DUPLICATE = "possible_duplicate"  # NotFoundError entity type: it has no events

# Codes that come from the file itself (D34): no anchor or declaration can clear them.
_FILE_ERRORS = frozenset({rules.REPORT_UNBALANCED, rules.CHAIN_BREAK})
_IMPORT_COLUMNS = [c for c in import_table.c if c.name != "ai_call_id"]
# Computed on read (transaction_select), not columns of the transaction table.
_COMPUTED_TRANSACTION_FIELDS = frozenset({"allocation_status", "allocation_categories"})
_TRANSACTION_COLUMNS = [
    transaction_table.c[name]
    for name in Transaction.model_fields
    if name != "line_hash" and name not in _COMPUTED_TRANSACTION_FIELDS
] + [transaction_table.c.line_hash]


# --- Import ----------------------------------------------------------------------------


def import_statement(
    ctx: CommandContext,
    store: BlobStore,
    account_id: UUID,
    data: bytes,
    *,
    media_type: str,
    iban_key: bytes,
    original_filename: str | None = None,
) -> StatementImport:
    """Import one statement file into the account. The same bytes imported again return the
    existing import and write nothing (the document is deduplicated on its bytes, D9); a
    different file overlapping stored lines adds only the lines not stored yet (D22).

    Refused before anything is written (RuleError): an unreadable file (statement.* parser
    codes), another account's statement (statement.account_mismatch: the file's own IBAN
    routes it, phase 6 D164), a file without booked lines. A camt file whose reports do not
    sum or chain (D34) is stored, unreconciled. Since phase 6 step 1 a private account
    imports like the rental one (D156: the owner-account guard is gone)."""
    account = require_row(ctx.conn, account_table, EntityType.ACCOUNT, account_id, for_update=True)
    outcome = parse_statement(data, media_type)
    statement = outcome.statement
    _require_same_account(statement.account_iban, account.iban_hmac, iban_key)
    if not statement.lines:
        raise RuleError(rules.EMPTY, "the file holds no booked line")

    document = upload_document(
        ctx,
        store,
        data,
        media_type=media_type,
        source=DocumentSource.UPLOAD,
        original_filename=original_filename,
    )
    existing = ctx.conn.execute(
        select(*_IMPORT_COLUMNS).where(
            import_table.c.account_id == account_id, import_table.c.document_id == document.id
        )
    ).one_or_none()
    if existing is not None:
        return _to_import(existing)

    dates = [line.booking_date for line in statement.lines]
    first, last = min(dates), max(dates)
    if document.typing is None:
        # Typed only when untyped: a person's own typing of the same file is left alone.
        type_document(
            ctx,
            document.id,
            DocumentTyping(
                type=DocumentType.STATEMENT,
                document_date=last,
                tax_year=last.year if last.year >= TAX_YEAR_MIN else None,
            ),
        )

    ibans = [_protect(line.counterparty_iban, iban_key) for line in statement.lines]
    hashes = _line_hashes(account_id, statement, iban_key)
    from_file = statement.closing_balance is not None
    imported = ctx.conn.execute(
        insert(import_table)
        .values(
            workspace_id=ctx.workspace_id,
            account_id=account_id,
            document_id=document.id,
            format=statement.format.value,
            lines_total=len(statement.lines),
            first_booking_on=first,
            last_booking_on=last,
            declared_opening=statement.opening_balance if from_file else None,
            declared_closing=statement.closing_balance,
            balance_source=(BalanceSource.FILE if from_file else BalanceSource.NONE).value,
            status=ImportStatus.IMPORTED.value,
            created_at=ctx.now,
            updated_at=ctx.now,
        )
        .returning(import_table.c.id)
    ).scalar_one()

    inserted = ctx.conn.execute(
        pg_insert(transaction_table)
        .values(
            [
                _transaction_values(ctx, account_id, imported, line, iban, line_hash)
                for line, iban, line_hash in zip(statement.lines, ibans, hashes, strict=True)
            ]
        )
        .on_conflict_do_nothing(index_elements=["workspace_id", "account_id", "line_hash"])
        .returning(transaction_table.c.id, transaction_table.c.line_hash)
    ).all()
    id_by_hash = {row.line_hash.hex(): row.id for row in inserted}
    new_ids = [id_by_hash[h] for h in dict.fromkeys(hashes) if h in id_by_hash]
    _record_duplicates(ctx, account_id, first, last, new_ids)

    row = ctx.conn.execute(
        update(import_table)
        .where(import_table.c.id == imported)
        .values(lines_new=len(new_ids), lines_skipped=len(statement.lines) - len(new_ids))
        .returning(*_IMPORT_COLUMNS)
    ).one()
    append_event(
        ctx,
        EventType.STATEMENT_IMPORTED,
        imported,
        StatementImported(
            account_id=account_id,
            document_id=document.id,
            format=statement.format,
            lines_total=row.lines_total,
            lines_new=row.lines_new,
            lines_skipped=row.lines_skipped,
            first_booking_on=first,
            last_booking_on=last,
            transaction_ids=new_ids,
            stated_closing=statement.stated_balance,
            stated_closing_on=statement.stated_balance_on,
        ),
        effective_date=last,
    )
    checked = _check(ctx, account, row, announce=True, file_error=outcome.balance_error)
    recheck_account(ctx, account_id, skip=imported)
    return checked.model_copy(
        update={
            "stated_closing": statement.stated_balance,
            "stated_closing_on": statement.stated_balance_on,
        }
    )


def declare_balances(
    ctx: CommandContext, import_id: UUID, body: DeclareBalanceBody
) -> StatementImport:
    """The closing (and optionally opening) balance Luis reads in online banking, for an
    import whose file states none (CSV). May be declared again to correct a typo; each
    declaration records statement.reconciled or .unreconciled."""
    return _declare(ctx, import_id, body.closing_balance, body.opening_balance, BalanceSource.USER)


def declare_bank_balance(
    ctx: CommandContext, import_id: UUID, closing_balance: Decimal
) -> StatementImport:
    """Phase 5 D117: a FinTS sync's import whose camt states no closing balance is reconciled
    against HKSAL's booked balance, read in the same dialog (balance_source bank). The sibling
    of declare_balances; the bank states no opening balance."""
    return _declare(ctx, import_id, closing_balance, None, BalanceSource.BANK)


def _declare(
    ctx: CommandContext,
    import_id: UUID,
    closing: Decimal,
    opening: Decimal | None,
    source: BalanceSource,
) -> StatementImport:
    current = require_row(ctx.conn, import_table, EntityType.STATEMENT, import_id)
    account = _lock_account(ctx, current.account_id)
    current = require_row(ctx.conn, import_table, EntityType.STATEMENT, import_id, for_update=True)
    if current.balance_source == BalanceSource.FILE.value:
        raise RuleError(rules.BALANCE_FROM_FILE, "the file states its own balances")
    row = ctx.conn.execute(
        update(import_table)
        .where(import_table.c.id == import_id)
        .values(
            declared_closing=closing,
            declared_opening=opening,
            balance_source=source.value,
            # Back to unchecked until _check stores the new result: a reconciled row must
            # never hold a declared balance its computed one does not match (0006 CHECK).
            status=ImportStatus.IMPORTED.value,
            error_code=None,
            updated_at=ctx.now,
        )
        .returning(*_IMPORT_COLUMNS)
    ).one()
    # Compared with the row as it was before this declaration, not the reset one: an
    # identical declaration (a double click, a retry) must not count as becoming reconciled.
    return _check(ctx, account, row, announce=True, before=current)


# --- Preview (phase 5 D127) --------------------------------------------------------------


def preview_import(
    conn: Connection,
    account_id: UUID,
    data: bytes,
    *,
    iban_key: bytes,
    media_type: str = "application/zip",
) -> SyncPreview:
    """What import_statement would do with these bytes, without writing anything (D127): the
    same parser, the same account check, the same line hash over the same fields, compared
    with the account's stored hashes. A FinTS sync stores its ZIP (and imports it) only when
    lines_new > 0. Raises the same RuleErrors import_statement raises before its first
    write (unreadable file, another account's statement, no booked line)."""
    account = require_row(conn, account_table, EntityType.ACCOUNT, account_id)
    statement = parse_statement(data, media_type).statement
    _require_same_account(statement.account_iban, account.iban_hmac, iban_key)
    if not statement.lines:
        raise RuleError(rules.EMPTY, "the file holds no booked line")
    hashes = list(dict.fromkeys(_line_hashes(account_id, statement, iban_key)))
    known = {
        row.line_hash.hex()
        for row in conn.execute(
            select(transaction_table.c.line_hash).where(
                transaction_table.c.account_id == account_id,
                transaction_table.c.line_hash.in_([bytes.fromhex(h) for h in hashes]),
            )
        )
    }
    return SyncPreview(
        lines_total=len(statement.lines),
        lines_new=sum(1 for h in hashes if h not in known),
        closing_balance=statement.closing_balance,
        last_booking_on=max(line.booking_date for line in statement.lines),
    )


def _line_hashes(account_id: UUID, statement: ParsedStatement, iban_key: bytes) -> list[str]:
    """The line hashes import_statement stores, from the parsed lines. DKB's Kundenreferenz
    is the counterparty's, not the bank's, so a DKB line hashes by its content and its
    position among identical lines (phase 6 D173 (3), D22's fallback): two different lines
    of one day, amount and customer reference both survive. The reference stays on the line."""
    lines = statement.lines
    own_ref = statement.format is not StatementFormat.DKB_CSV
    ibans = [_protect(line.counterparty_iban, iban_key) for line in lines]
    return rules.line_hashes(
        account_id,
        [
            _for_hash(line, iban, end_to_end=own_ref)
            for line, iban in zip(lines, ibans, strict=True)
        ],
    )


def last_reconciled_closing(conn: Connection, account_id: UUID) -> Decimal | None:
    """Phase 5 D117: the closing balance a sync that brought nothing new compares HKSAL with.
    The account's latest import (by last booking day, then creation) when it is reconciled:
    its declared closing equals the stored window's sum. None when that import is not
    reconciled (it already has its own card in Zu prüfen, so the drift check is skipped)
    or the account has no import with lines (the caller compares with the anchor then)."""
    row = conn.execute(
        select(import_table.c.status, import_table.c.declared_closing)
        .where(
            import_table.c.account_id == account_id,
            import_table.c.last_booking_on.is_not(None),
        )
        .order_by(
            import_table.c.last_booking_on.desc(),
            import_table.c.created_at.desc(),
            import_table.c.id.desc(),
        )
        .limit(1)
    ).one_or_none()
    if row is None or row.status != ImportStatus.RECONCILED.value:
        return None
    return row.declared_closing


def account_has_lines(conn: Connection, account_id: UUID) -> bool:
    return bool(
        conn.execute(
            select(exists().where(transaction_table.c.account_id == account_id))
        ).scalar_one()
    )


def recheck_account(ctx: CommandContext, account_id: UUID, *, skip: UUID | None = None) -> None:
    """Reconcile every import of the account again (after its stored lines, their exclusions
    or its anchor changed). Only imports whose result changed get an event."""
    account = _lock_account(ctx, account_id)
    rows = ctx.conn.execute(
        select(*_IMPORT_COLUMNS)
        .where(import_table.c.account_id == account_id)
        .order_by(import_table.c.first_booking_on, import_table.c.created_at, import_table.c.id)
        .with_for_update()
    ).all()
    for row in rows:
        if row.id != skip:
            _check(ctx, account, row, announce=False)


def _check(
    ctx: CommandContext,
    account: Row,
    row: Row,
    *,
    announce: bool,
    file_error: str | None = None,
    before: Row | None = None,
) -> StatementImport:
    """Reconcile one import against the stored window and store the result. The event is
    written when the result changed, or always when `announce` (a new import or a new
    declaration). Nothing is announced while no closing balance is declared (pending).

    `before`: the row as it was before the caller's own update (declare_balances resets the
    status first); "changed" is judged against it, so the hook fires only on a real change.
    """
    before = before if before is not None else row
    if row.first_booking_on is None:  # no lines (a PDF read in a later step)
        return _to_import(row)
    sums = rules.window_sums(
        _stored_lines(ctx.conn, row.account_id, through=row.last_booking_on),
        anchor_on=account.opening_balance_on,
        first_booking_on=row.first_booking_on,
        last_booking_on=row.last_booking_on,
    )
    result = rules.reconcile(
        account.opening_balance,
        sums.stored_before,
        sums.stored_through,
        row.declared_closing,
        row.declared_opening,
        anchor_on=account.opening_balance_on,
        first_booking_on=row.first_booking_on,
    )
    file_error = file_error or (row.error_code if row.error_code in _FILE_ERRORS else None)
    if file_error is not None:
        status, code = ImportStatus.UNRECONCILED, file_error
    elif result.status is ReconcileStatus.PENDING_BALANCE:
        status, code = ImportStatus.IMPORTED, None
    else:
        status, code = ImportStatus(result.status.value), result.error_code

    values = {
        "status": status.value,
        "error_code": code,
        "computed_opening": result.computed_opening,
        "computed_closing": result.computed_closing,
    }
    changed = any(getattr(before, name) != value for name, value in values.items())
    if any(getattr(row, name) != value for name, value in values.items()):
        row = ctx.conn.execute(
            update(import_table)
            .where(import_table.c.id == row.id)
            .values(**values, updated_at=ctx.now)
            .returning(*_IMPORT_COLUMNS)
        ).one()
    if (changed or announce) and status is not ImportStatus.IMPORTED:
        reconciled = status is ImportStatus.RECONCILED
        append_event(
            ctx,
            EventType.STATEMENT_RECONCILED if reconciled else EventType.STATEMENT_UNRECONCILED,
            row.id,
            StatementBalanceChecked(
                declared_opening=row.declared_opening,
                computed_opening=result.computed_opening,
                declared_closing=row.declared_closing,
                computed_closing=result.computed_closing,
                difference=result.difference,
                opening_difference=result.opening_difference,
                balance_source=BalanceSource(row.balance_source),
            ),
        )
    if changed and status is ImportStatus.RECONCILED:
        _on_reconciled(ctx, row.id)
    return _to_import(row)


def _on_reconciled(ctx: CommandContext, import_id: UUID) -> None:
    """The import just became reconciled, so its lines may now be
    matched. `match_import(import_id)` is queued here, in the same transaction, so the
    job commits with the result. Lines of an import that is not reconciled are never
    matched (§3.4); `match_import` must re-check the status itself, since a later import or
    merge can turn a reconciled import unreconciled again.

    It fires once per change of result, not once per import: an import that stays
    reconciled while its computed balances move (an anchor change, a merge) fires again, so
    the job must be idempotent per import (match only lines not yet matched)."""
    enqueue.enqueue_match_import(ctx.conn, workspace_id=ctx.workspace_id, import_id=import_id)


# --- Possible duplicates ---------------------------------------------------------------


def resolve_duplicate(
    ctx: CommandContext, possible_duplicate_id: UUID, status: DuplicateStatus
) -> PossibleDuplicate:
    """kept_both: both lines stay. merged: the later-imported line (other_transaction_id) is
    excluded as a duplicate of the first and leaves every balance, then the account's
    imports are re-checked. Refused: lines on two accounts, a keeper that is itself excluded
    (both copies would leave the balance), an already decided pair."""
    status = DuplicateStatus(status)
    if status is DuplicateStatus.OPEN:
        raise ValueError("decide kept_both or merged")
    pair = require_row(ctx.conn, duplicate_table, POSSIBLE_DUPLICATE, possible_duplicate_id)
    keeper = require_row(ctx.conn, transaction_table, EntityType.TRANSACTION, pair.transaction_id)
    later = require_row(
        ctx.conn, transaction_table, EntityType.TRANSACTION, pair.other_transaction_id
    )
    if keeper.account_id != later.account_id:
        raise RuleError(rules.DUPLICATE_ACCOUNTS_DIFFER, "the two lines are on different accounts")
    _lock_account(ctx, keeper.account_id)
    pair = require_row(
        ctx.conn, duplicate_table, POSSIBLE_DUPLICATE, possible_duplicate_id, for_update=True
    )
    if pair.status != DuplicateStatus.OPEN.value:
        raise ConflictError(f"possible duplicate {possible_duplicate_id} was already decided")

    exclusion = rules.resolve_duplicate(keeper.id, later.id, status)
    if exclusion is not None:
        keeper, later = (
            require_row(ctx.conn, transaction_table, EntityType.TRANSACTION, tid, for_update=True)
            for tid in (keeper.id, later.id)
        )
        if keeper.excluded_as_duplicate_of is not None:
            raise RuleError(
                rules.DUPLICATE_EXCLUDES_BOTH,
                "the line to keep is itself excluded; merging would drop both copies",
            )
        if later.excluded_as_duplicate_of is not None:
            raise ConflictError(f"transaction {later.id} is already excluded as a duplicate")
        ctx.conn.execute(
            update(transaction_table)
            .where(transaction_table.c.id == exclusion.excluded_id)
            .values(excluded_as_duplicate_of=exclusion.excluded_as_duplicate_of)
        )
    row = ctx.conn.execute(
        update(duplicate_table)
        .where(duplicate_table.c.id == possible_duplicate_id)
        .values(status=status.value, decided_at=ctx.now)
        .returning(*duplicate_table.c)
    ).one()
    append_event(
        ctx,
        EventType.STATEMENT_DUPLICATE_RESOLVED,
        later.import_id,
        StatementDuplicateResolved(
            possible_duplicate_id=possible_duplicate_id,
            transaction_id=keeper.id,
            other_transaction_id=later.id,
            status=status,
        ),
    )
    if exclusion is not None:
        recheck_account(ctx, keeper.account_id)
    elif status is DuplicateStatus.KEPT_BOTH:
        # The later line waited for this decision (services.matching skips it while open).
        enqueue.enqueue_match_transaction(
            ctx.conn, workspace_id=ctx.workspace_id, transaction_id=later.id
        )
    return _to_duplicate(row)


def _record_duplicates(
    ctx: CommandContext, account_id: UUID, first: date, last: date, new_ids: Sequence[UUID]
) -> None:
    """A possible_duplicate row per (stored line, new line) pair that rules.likely_duplicates
    reports: same amount, day and counterparty in another format (D22)."""
    if not new_ids:
        return
    lines = _stored_lines(ctx.conn, account_id, since=first, through=last)
    fresh = set(new_ids)
    earlier = [line for line in lines if line.id not in fresh]
    pairs = [
        {
            "workspace_id": ctx.workspace_id,
            "transaction_id": stored_id,
            "other_transaction_id": line.id,
            "created_at": ctx.now,
        }
        for line in lines
        if line.id in fresh
        for stored_id in rules.likely_duplicates(line, earlier)
    ]
    if pairs:
        ctx.conn.execute(
            pg_insert(duplicate_table)
            .values(pairs)
            .on_conflict_do_nothing(index_elements=["transaction_id", "other_transaction_id"])
        )


# --- Reads -------------------------------------------------------------------------------


def get_import(conn: Connection, import_id: UUID) -> StatementImport | None:
    row = conn.execute(select(*_IMPORT_COLUMNS).where(import_table.c.id == import_id)).one_or_none()
    return None if row is None else _with_stated(conn, [_to_import(row)])[0]


def list_imports(conn: Connection, account_id: UUID) -> list[StatementImport]:
    """The account's imports, newest first."""
    rows = conn.execute(
        select(*_IMPORT_COLUMNS)
        .where(import_table.c.account_id == account_id)
        .order_by(import_table.c.created_at.desc(), import_table.c.id)
    )
    return _with_stated(conn, [_to_import(r) for r in rows])


def _stated_balances(
    conn: Connection, import_ids: Sequence[UUID]
) -> dict[UUID, tuple[Decimal, date]]:
    """Phase 6 D173 (3): the balance each import's file stated beside its lines, from its
    statement.imported event (only a DKB CSV states one)."""
    if not import_ids:
        return {}
    rows = conn.execute(
        select(event_table.c.entity_id, event_table.c.payload).where(
            event_table.c.type == EventType.STATEMENT_IMPORTED.value,
            event_table.c.entity_id.in_(import_ids),
            event_table.c.payload.has_key("stated_closing"),
        )
    )
    found: dict[UUID, tuple[Decimal, date]] = {}
    for row in rows:
        closing, on = row.payload.get("stated_closing"), row.payload.get("stated_closing_on")
        if closing is not None and on is not None:
            found[row.entity_id] = (Decimal(str(closing)), date.fromisoformat(on))
    return found


def _with_stated(conn: Connection, imports: list[StatementImport]) -> list[StatementImport]:
    stated = _stated_balances(
        conn, [i.id for i in imports if i.balance_source != BalanceSource.FILE]
    )
    return [
        i
        if i.id not in stated
        else i.model_copy(
            update={"stated_closing": stated[i.id][0], "stated_closing_on": stated[i.id][1]}
        )
        for i in imports
    ]


def list_transactions(
    conn: Connection,
    account_id: UUID,
    *,
    import_id: UUID | None = None,
    booked_from: date | None = None,
    booked_to: date | None = None,
) -> list[Transaction]:
    """The account's transactions, newest booking day first; optionally only one import's
    new lines, or a booking-date range (both ends inclusive). Excluded duplicates are
    included, flagged by excluded_as_duplicate_of."""
    query = transaction_select().where(transaction_table.c.account_id == account_id)
    if import_id is not None:
        query = query.where(transaction_table.c.import_id == import_id)
    if booked_from is not None:
        query = query.where(transaction_table.c.booking_date >= booked_from)
    if booked_to is not None:
        query = query.where(transaction_table.c.booking_date <= booked_to)
    rows = conn.execute(
        query.order_by(
            transaction_table.c.booking_date.desc(),
            transaction_table.c.created_at,
            transaction_table.c.id,
        )
    )
    return [_to_transaction(r) for r in rows]


def list_possible_duplicates(
    conn: Connection, *, status: DuplicateStatus | None = None
) -> list[PossibleDuplicate]:
    """Possible duplicates, newest first; optionally one status (the inbox shows open)."""
    query = select(duplicate_table)
    if status is not None:
        query = query.where(duplicate_table.c.status == DuplicateStatus(status).value)
    rows = conn.execute(query.order_by(duplicate_table.c.created_at.desc(), duplicate_table.c.id))
    return [_to_duplicate(r) for r in rows]


_IMPORT_REASONS = {
    rules.BEFORE_ANCHOR: "Der Auszug beginnt vor dem Anfangssaldo des Kontos.",
    rules.OPENING_MISMATCH: "Der Anfangssaldo der Datei passt nicht zu den gespeicherten Umsätzen.",
    rules.UNRECONCILED: "Der Endsaldo passt nicht zu den gespeicherten Umsätzen.",
    rules.REPORT_UNBALANCED: "Ein Tagesbericht der Datei geht in sich nicht auf.",
    rules.CHAIN_BREAK: "Die Tagesberichte der Datei schließen nicht lückenlos aneinander an.",
}
_IMPORT_QUESTIONS = {
    rules.BEFORE_ANCHOR: "Stimmt das Datum des Anfangssaldos des Kontos?",
    rules.OPENING_MISMATCH: "Fehlt ein früherer Auszug oder ist ein Umsatz doppelt?",
    rules.UNRECONCILED: "Fehlt ein Auszug oder ist ein Umsatz doppelt?",
    rules.REPORT_UNBALANCED: "Kannst du die Datei erneut aus dem Online-Banking exportieren?",
    rules.CHAIN_BREAK: "Kannst du die Datei erneut aus dem Online-Banking exportieren?",
}

_DEFAULT_REASON = _IMPORT_REASONS[rules.UNRECONCILED]
_DEFAULT_QUESTION = _IMPORT_QUESTIONS[rules.UNRECONCILED]


def import_inbox_items(conn: Connection) -> list[InboxItem]:
    """The review-queue items of step 3 (§8.3): unreconciled imports, imports waiting for a
    declared balance, open possible duplicates. Newest first; texts quote no bank line."""
    items: list[InboxItem] = []
    rows = conn.execute(
        select(*_IMPORT_COLUMNS).where(
            import_table.c.status.in_(
                [ImportStatus.UNRECONCILED.value, ImportStatus.IMPORTED.value]
            )
        )
    ).all()
    stated = _stated_balances(
        conn, [r.id for r in rows if r.declared_closing is None and r.last_booking_on]
    )
    for row in rows:
        if row.status == ImportStatus.UNRECONCILED.value:
            items.append(
                InboxItem(
                    kind=InboxItemKind.IMPORT_UNRECONCILED,
                    created_at=row.updated_at,
                    reasoning=_IMPORT_REASONS.get(row.error_code, _DEFAULT_REASON),
                    question=_IMPORT_QUESTIONS.get(row.error_code, _DEFAULT_QUESTION),
                    document_id=row.document_id,
                    import_id=row.id,
                )
            )
        elif row.id in stated:
            # Phase 6 D173 (3), Q-O75: the file's „Kontostand vom“ pre-fills the closing; the
            # user confirms it is the balance after the file's last booking.
            items.append(
                InboxItem(
                    kind=InboxItemKind.IMPORT_PENDING_BALANCE,
                    created_at=row.created_at,
                    reasoning=(
                        f"Die Datei nennt den Kontostand vom {stated[row.id][1]:%d.%m.%Y}, "
                        "aber keinen Saldo zu ihren Umsätzen. Hausbuch schlägt ihn als "
                        "Endsaldo vor."
                    ),
                    question=(
                        "Ist das der Saldo nach der letzten Buchung vom "
                        f"{row.last_booking_on:%d.%m.%Y}?"
                    ),
                    document_id=row.document_id,
                    import_id=row.id,
                )
            )
        elif row.declared_closing is None and row.last_booking_on is not None:
            items.append(
                InboxItem(
                    kind=InboxItemKind.IMPORT_PENDING_BALANCE,
                    created_at=row.created_at,
                    reasoning="Die Datei enthält keinen Kontostand.",
                    question=(
                        "Wie lautet der Kontostand am "
                        f"{row.last_booking_on:%d.%m.%Y} laut Online-Banking?"
                    ),
                    document_id=row.document_id,
                    import_id=row.id,
                )
            )
    for pair in list_possible_duplicates(conn, status=DuplicateStatus.OPEN):
        items.append(
            InboxItem(
                kind=InboxItemKind.POSSIBLE_DUPLICATE,
                created_at=pair.created_at,
                reasoning="Gleicher Tag, Betrag und Empfänger in zwei verschiedenen Dateiformaten.",
                question="Ist das dieselbe Buchung?",
                possible_duplicate_id=pair.id,
                transaction_id=pair.other_transaction_id,
            )
        )
    return sorted(items, key=lambda item: item.created_at, reverse=True)


# --- Helpers -------------------------------------------------------------------------------


def _lock_account(ctx: CommandContext, account_id: UUID) -> Row:
    return require_row(ctx.conn, account_table, EntityType.ACCOUNT, account_id, for_update=True)


def _require_same_account(file_iban: str | None, account_hmac: bytes, key: bytes) -> None:
    """The file's own account IBAN, when it names one, must be this account's."""
    if file_iban is None:
        return
    try:
        theirs = protect_iban(file_iban, key).hmac
    except RuleError:
        theirs = b""
    if not hmac.compare_digest(theirs, account_hmac):
        raise RuleError(rules.ACCOUNT_MISMATCH, "the file is a statement of another account")


def _protect(raw: str | None, key: bytes) -> ProtectedIban | None:
    """A counterparty IBAN as mask + HMAC. One that does not validate is dropped: it cannot
    be stored masked, and the plaintext is never stored."""
    if not raw:
        return None
    try:
        return protect_iban(raw, key)
    except RuleError:
        return None


def _for_hash(
    line: StatementLine, iban: ProtectedIban | None, *, end_to_end: bool = True
) -> LineForHash:
    return LineForHash(
        booking_date=line.booking_date,
        value_date=line.value_date,
        amount=line.amount,
        counterparty_name=line.counterparty_name,
        counterparty_iban_hmac=None if iban is None else iban.hmac.hex(),
        purpose=line.purpose,
        booking_text=line.booking_text,
        bank_reference=line.bank_reference,
        end_to_end_ref=line.end_to_end_ref if end_to_end else None,
    )


def _transaction_values(
    ctx: CommandContext,
    account_id: UUID,
    import_id: UUID,
    line: StatementLine,
    iban: ProtectedIban | None,
    line_hash: str,
) -> dict[str, Any]:
    return {
        "workspace_id": ctx.workspace_id,
        "account_id": account_id,
        "import_id": import_id,
        "line_hash": bytes.fromhex(line_hash),
        "booking_date": line.booking_date,
        "value_date": line.value_date,
        "amount": line.amount,
        "currency": line.currency.value,
        "counterparty_name": line.counterparty_name,
        "counterparty_iban_masked": None if iban is None else iban.masked,
        "counterparty_iban_hmac": None if iban is None else iban.hmac,
        "purpose": line.purpose,
        "booking_text": line.booking_text,
        "bank_reference": line.bank_reference,
        "end_to_end_ref": line.end_to_end_ref,
        "mandate_ref": line.mandate_ref,
        "raw": _scrub(line.raw),
        "is_pending": line.pending,
        "created_at": ctx.now,
    }


def _scrub(value: JsonValue) -> JsonValue:
    """`raw` with every IBAN in it masked (§14 step 3 FOCUS: raw never holds a plaintext
    IBAN). The parsers already leave the IBAN columns out; this catches the rest."""
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if not isinstance(value, str):
        return value
    return mask_ibans_in_text(value)


def _stored_lines(
    conn: Connection, account_id: UUID, *, through: date, since: date | None = None
) -> list[StoredLine]:
    query = (
        select(
            transaction_table.c.id,
            transaction_table.c.account_id,
            import_table.c.format,
            transaction_table.c.line_hash,
            transaction_table.c.booking_date,
            transaction_table.c.amount,
            transaction_table.c.counterparty_iban_hmac,
            transaction_table.c.counterparty_name,
            transaction_table.c.excluded_as_duplicate_of,
        )
        .join(import_table, import_table.c.id == transaction_table.c.import_id)
        .where(
            transaction_table.c.account_id == account_id,
            transaction_table.c.booking_date <= through,
        )
    )
    if since is not None:
        query = query.where(transaction_table.c.booking_date >= since)
    return [
        StoredLine(
            id=r.id,
            account_id=r.account_id,
            format=StatementFormat(r.format),
            line_hash=r.line_hash.hex(),
            booking_date=r.booking_date,
            amount=r.amount,
            counterparty_iban_hmac=(
                None if r.counterparty_iban_hmac is None else r.counterparty_iban_hmac.hex()
            ),
            counterparty_name=r.counterparty_name,
            excluded_as_duplicate_of=r.excluded_as_duplicate_of,
        )
        for r in conn.execute(query)
    ]


def _to_import(row: Row) -> StatementImport:
    return StatementImport.model_validate(
        {k: v for k, v in row._mapping.items() if k != "ai_call_id"}
    )


def transaction_select() -> Select:
    """The transaction columns plus what allocation_status and allocation_categories are
    computed from, in one query: the line's import status, whether it is the later line of
    an open possible duplicate, and one aggregate row per line over its current allocation
    rows (status != superseded, D23), outer-joined so a line without rows gets NULLs.

    The aggregate is two levels: per (line, category) the summed absolute amount and whether
    any row is proposed, then per line the categories ordered by that amount (largest
    first, ties by category) and whether any is proposed. Read with _to_transaction."""
    a = allocation_table.c
    t = transaction_table.c
    per_category = (
        select(
            a.transaction_id,
            a.category,
            func.sum(func.abs(a.amount)).label("weight"),
            func.bool_or(a.status == AllocationStatus.PROPOSED.value).label("proposed"),
        )
        .where(a.status != AllocationStatus.SUPERSEDED.value)
        .group_by(a.transaction_id, a.category)
        .subquery("current_per_category")
    )
    c = per_category.c
    current = (
        select(
            c.transaction_id,
            func.array_agg(aggregate_order_by(c.category, c.weight.desc(), c.category)).label(
                "categories"
            ),
            func.bool_or(c.proposed).label("any_proposed"),
        )
        .group_by(c.transaction_id)
        .subquery("current_allocations")
    )
    awaiting_duplicate = (
        exists()
        .where(
            duplicate_table.c.other_transaction_id == t.id,
            duplicate_table.c.status == DuplicateStatus.OPEN.value,
        )
        .correlate(transaction_table)
    )
    return select(
        *_TRANSACTION_COLUMNS,
        import_table.c.status.label("_import_status"),
        awaiting_duplicate.label("_awaiting_duplicate"),
        current.c.categories.label("_categories"),
        current.c.any_proposed.label("_any_proposed"),
    ).select_from(
        transaction_table.join(import_table, import_table.c.id == t.import_id).outerjoin(
            current, current.c.transaction_id == t.id
        )
    )


def allocation_status(
    *,
    excluded: bool,
    import_status: ImportStatus,
    awaiting_duplicate: bool,
    has_current_rows: bool,
    any_proposed: bool,
) -> TransactionAllocationStatus:
    """The precedence documented on TransactionAllocationStatus; the first that applies."""
    if excluded:
        return TransactionAllocationStatus.EXCLUDED
    if import_status is not ImportStatus.RECONCILED:
        return TransactionAllocationStatus.NOT_RECONCILED
    if awaiting_duplicate:
        return TransactionAllocationStatus.DUPLICATE_OPEN
    if any_proposed:
        return TransactionAllocationStatus.PROPOSED
    if has_current_rows:
        return TransactionAllocationStatus.CONFIRMED
    return TransactionAllocationStatus.OPEN


def _to_transaction(row: Row) -> Transaction:
    """A row of transaction_select."""
    values = dict(row._mapping)
    import_status = ImportStatus(values.pop("_import_status"))
    awaiting_duplicate = bool(values.pop("_awaiting_duplicate"))
    categories = values.pop("_categories") or []
    any_proposed = bool(values.pop("_any_proposed"))
    status = allocation_status(
        excluded=values["excluded_as_duplicate_of"] is not None,
        import_status=import_status,
        awaiting_duplicate=awaiting_duplicate,
        has_current_rows=bool(categories),
        any_proposed=any_proposed,
    )
    return Transaction.model_validate(
        values
        | {
            "line_hash": values["line_hash"].hex(),
            "allocation_status": status,
            "allocation_categories": categories,
        }
    )


def _to_duplicate(row: Row) -> PossibleDuplicate:
    return PossibleDuplicate.model_validate(dict(row._mapping))
