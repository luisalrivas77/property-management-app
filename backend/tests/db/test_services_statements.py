"""Accounts and statement imports end to end, as hausbuch_app under RLS (phase 2 step 3,
docs/phases/phase-2.md §7, D21, D22, D34).

The inputs are the anonymised Sparkasse samples (fixtures/phase2/bank_samples/), built with
the parser tests' own builder, so a case here and in camt.yaml / csv_camt.yaml is the same
file. The CSV and the ZIP hold the same 22 bookings, from 0,00 on 31.03.2026 to -3.028,51.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from sqlalchemy import func, insert, select, text
from test_bank_parsers import CAMT, CSV, build

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import document as document_table
from hausbuch.db.tables import possible_duplicate as duplicate_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.rules import statements as rules
from hausbuch.rules.errors import RuleError
from hausbuch.rules.iban import normalize_iban
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.bank import AccountCreate, DeclareBalanceBody
from hausbuch.schemas.enums import BalanceSource, DocumentType, DuplicateStatus, ImportStatus
from hausbuch.schemas.events import EventType
from hausbuch.services import statements
from hausbuch.services.accounts import (
    create_account,
    get_account,
    list_accounts,
    opening_from_closing,
    update_account,
)
from hausbuch.services.bank import parse_statement
from hausbuch.services.documents import get_document
from hausbuch.services.errors import ConflictError, NotFoundError
from hausbuch.services.events import list_events
from hausbuch.services.statements import (
    declare_balances,
    get_import,
    import_inbox_items,
    import_statement,
    list_imports,
    list_possible_duplicates,
    list_transactions,
    resolve_duplicate,
)

pytestmark = pytest.mark.db

T0 = datetime(2026, 11, 6, 21, 0, tzinfo=UTC)
T1 = datetime(2026, 11, 7, 8, 0, tzinfo=UTC)
T2 = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)
T3 = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
SAMPLE_IBAN = "DE50100000000000000001"
OTHER_IBAN = "DE89370400440532013000"
CLOSING = Decimal("-3028.51")
LAST_DAY = date(2026, 11, 6)
# The three quarterly Rechnungsabschluss lines name no counterparty in camt and only an IBAN
# in the CSV, so the duplicate rule (duplicates.yaml) cannot pair them across formats.
UNPAIRED_ABSCHLUSS = 3 * Decimal("136.69")


def _case(fixture, case_id):
    return next(case for case in fixture.cases if case.id == case_id)


def _bytes(fixture, case_id) -> bytes:
    return build(_case(fixture, case_id).input)


ZIP = _bytes(CAMT, "sparkasse_zip_of_daily_reports")
CSV_BYTES = _bytes(CSV, "sparkasse_export")


def _account(run, ws, *, iban=SAMPLE_IBAN, opening_on=date(2026, 3, 31), owner=False):
    data = AccountCreate(
        label="Mietkonto",
        bank="Sparkasse",
        iban=iban,
        opening_balance=Decimal("0.00"),
        opening_balance_on=opening_on,
        is_owner_account=owner,
    )
    return run(ws, create_account, data, iban_key=TEST_IBAN_KEY, now=T0)


@pytest.fixture
def imp(run, blob_store):
    """imp(ws, account_id, data, media_type=, now=): one import_statement command."""

    def _imp(ws, account_id, data, *, media_type="application/zip", now=T1):
        return run(
            ws,
            import_statement,
            blob_store,
            account_id,
            data,
            media_type=media_type,
            iban_key=TEST_IBAN_KEY,
            original_filename="export",
            now=now,
        )

    return _imp


@pytest.fixture
def hook_calls(monkeypatch) -> list[uuid.UUID]:
    """The import ids passed to the step 4 hook (match_import goes there)."""
    calls: list[uuid.UUID] = []
    monkeypatch.setattr(
        statements, "_on_reconciled", lambda ctx, import_id: calls.append(import_id)
    )
    return calls


def _types(read, ws, entity_id):
    return [e.type for e in read(ws, list_events, entity_id=entity_id)]


def _count(app_engine, ws, table) -> int:
    with workspace_transaction(app_engine, ws) as conn:
        return conn.execute(select(func.count()).select_from(table)).scalar_one()


# --- Import, reconcile, re-upload ------------------------------------------------------


def test_camt_zip_imports_reconciled_and_types_its_document(
    run, imp, read, fresh_workspaces, hook_calls
):
    ws, _ = fresh_workspaces
    account = _account(run, ws)
    imported = imp(ws, account.id, ZIP)

    assert imported.status is ImportStatus.RECONCILED and imported.error_code is None
    assert (imported.lines_total, imported.lines_new, imported.lines_skipped) == (22, 22, 0)
    assert imported.balance_source is BalanceSource.FILE
    assert (imported.declared_opening, imported.declared_closing) == (Decimal("0.00"), CLOSING)
    assert (imported.computed_opening, imported.computed_closing) == (Decimal("0.00"), CLOSING)
    assert (imported.first_booking_on, imported.last_booking_on) == (date(2026, 4, 1), LAST_DAY)
    assert hook_calls == [imported.id]

    events = read(ws, list_events, entity_id=imported.id)
    assert [e.type for e in events] == [
        EventType.STATEMENT_IMPORTED,
        EventType.STATEMENT_RECONCILED,
    ]
    assert events[0].effective_date == LAST_DAY
    assert len(events[0].payload["transaction_ids"]) == 22
    assert events[1].payload["difference"] == "0.00"

    document = read(ws, get_document, imported.document_id)
    assert document.typing.type is DocumentType.STATEMENT
    assert (document.typing.tax_year, document.typing.document_date) == (2026, LAST_DAY)
    lines = read(ws, list_transactions, account.id)
    assert len(lines) == 22 and sum(line.amount for line in lines) == CLOSING
    assert lines[0].booking_date == LAST_DAY  # newest first
    assert read(ws, list_imports, account.id) == [imported]
    assert read(ws, import_inbox_items) == []


def test_the_same_bytes_again_return_the_import_and_write_nothing(
    run, imp, read, app_engine, fresh_workspaces
):
    ws, _ = fresh_workspaces
    account = _account(run, ws)
    first = imp(ws, account.id, ZIP)
    again = imp(ws, account.id, ZIP, now=T2)
    assert again == first
    assert _count(app_engine, ws, transaction_table) == 22
    assert _types(read, ws, first.id) == [
        EventType.STATEMENT_IMPORTED,
        EventType.STATEMENT_RECONCILED,
    ]


def test_a_second_download_adds_no_line_and_still_reconciles(
    run, imp, read, app_engine, fresh_workspaces, hook_calls
):
    """camt.yaml second_download_differs_only_in_creation_time: other bytes (a new document),
    the same lines, so every hash is known (D22)."""
    ws, _ = fresh_workspaces
    account = _account(run, ws)
    first = imp(ws, account.id, ZIP)
    second = imp(
        ws, account.id, _bytes(CAMT, "second_download_differs_only_in_creation_time"), now=T2
    )
    assert second.id != first.id and second.document_id != first.document_id
    assert (second.lines_total, second.lines_new, second.lines_skipped) == (22, 0, 22)
    assert second.status is ImportStatus.RECONCILED
    assert _count(app_engine, ws, transaction_table) == 22
    assert read(ws, get_import, first.id).status is ImportStatus.RECONCILED
    assert hook_calls == [first.id, second.id]
    assert _count(app_engine, ws, duplicate_table) == 0


def test_a_single_report_overlapping_the_zip_reconciles_on_the_stored_window(
    run, imp, fresh_workspaces
):
    """One daily report (camt.yaml single_report_xml_without_zip) imported after the ZIP: its
    lines are all known, and its opening and closing match the stored window around it."""
    ws, _ = fresh_workspaces
    account = _account(run, ws)
    imp(ws, account.id, ZIP)
    single = _bytes(CAMT, "single_report_xml_without_zip")
    imported = imp(ws, account.id, single, media_type="application/xml", now=T2)
    assert (imported.lines_new, imported.lines_skipped) == (0, 3)
    assert imported.status is ImportStatus.RECONCILED
    assert imported.computed_closing == Decimal("-608.15")


def test_csv_waits_for_a_declared_balance_then_reconciles(
    run, imp, read, fresh_workspaces, hook_calls
):
    ws, _ = fresh_workspaces
    account = _account(run, ws)
    imported = imp(ws, account.id, CSV_BYTES, media_type="text/csv")
    assert imported.status is ImportStatus.IMPORTED and imported.error_code is None
    assert imported.balance_source is BalanceSource.NONE
    assert imported.declared_closing is None and imported.computed_closing == CLOSING
    assert _types(read, ws, imported.id) == [EventType.STATEMENT_IMPORTED]
    assert hook_calls == []
    [item] = read(ws, import_inbox_items)
    assert (item.kind, item.import_id) == (InboxItemKind.IMPORT_PENDING_BALANCE, imported.id)
    assert "06.11.2026" in item.question

    declared = run(ws, declare_balances, imported.id, DeclareBalanceBody(closing_balance=CLOSING))
    assert declared.status is ImportStatus.RECONCILED
    assert declared.balance_source is BalanceSource.USER
    assert hook_calls == [imported.id]
    assert read(ws, import_inbox_items) == []

    # Declared again (a typo, then the right figure): no CHECK trips on the way.
    wrong = run(
        ws, declare_balances, imported.id, DeclareBalanceBody(closing_balance=Decimal("-3028.15"))
    )
    assert (wrong.status, wrong.error_code) == (ImportStatus.UNRECONCILED, rules.UNRECONCILED)
    [item] = read(ws, import_inbox_items)
    assert item.kind is InboxItemKind.IMPORT_UNRECONCILED
    right = run(
        ws,
        declare_balances,
        imported.id,
        DeclareBalanceBody(closing_balance=CLOSING, opening_balance=Decimal("0.00")),
    )
    assert right.status is ImportStatus.RECONCILED
    assert _types(read, ws, imported.id) == [
        EventType.STATEMENT_IMPORTED,
        EventType.STATEMENT_RECONCILED,
        EventType.STATEMENT_UNRECONCILED,
        EventType.STATEMENT_RECONCILED,
    ]
    unreconciled = read(ws, list_events, entity_id=imported.id)[2]
    assert unreconciled.payload["difference"] == "0.36"
    assert hook_calls == [imported.id, imported.id]  # reconciled twice, with a fault between


def test_the_same_declaration_again_does_not_reconcile_twice(
    run, imp, read, fresh_workspaces, hook_calls
):
    """A double click or a retried request declares the identical balance again. The
    import was reconciled and still is: the declaration is recorded (an event), but the
    step 4 hook, which queues matching, must not fire a second time."""
    ws, _ = fresh_workspaces
    account = _account(run, ws)
    imported = imp(ws, account.id, CSV_BYTES, media_type="text/csv")
    run(ws, declare_balances, imported.id, DeclareBalanceBody(closing_balance=CLOSING))
    again = run(ws, declare_balances, imported.id, DeclareBalanceBody(closing_balance=CLOSING))
    assert again.status is ImportStatus.RECONCILED
    assert hook_calls == [imported.id]
    assert _types(read, ws, imported.id) == [
        EventType.STATEMENT_IMPORTED,
        EventType.STATEMENT_RECONCILED,
        EventType.STATEMENT_RECONCILED,
    ]


def test_a_camt_import_takes_no_declared_balance(run, imp, fresh_workspaces):
    ws, _ = fresh_workspaces
    account = _account(run, ws)
    imported = imp(ws, account.id, ZIP)
    with pytest.raises(RuleError) as caught:
        run(ws, declare_balances, imported.id, DeclareBalanceBody(closing_balance=CLOSING))
    assert caught.value.code == rules.BALANCE_FROM_FILE


def test_a_chain_break_is_stored_unreconciled_and_stays_so(
    run, imp, read, fresh_workspaces, hook_calls
):
    """D34: a ZIP whose daily reports do not chain is imported, but goes to review as a
    whole; no anchor can clear a fault of the file."""
    ws, _ = fresh_workspaces
    account = _account(run, ws)
    imported = imp(ws, account.id, _bytes(CAMT, "missing_day_breaks_the_chain"))
    assert (imported.status, imported.error_code) == (ImportStatus.UNRECONCILED, rules.CHAIN_BREAK)
    assert _types(read, ws, imported.id)[-1] == EventType.STATEMENT_UNRECONCILED
    [item] = read(ws, import_inbox_items)
    assert (item.kind, item.import_id) == (InboxItemKind.IMPORT_UNRECONCILED, imported.id)

    run(ws, update_account, account.id, opening_balance=Decimal("1.00"), now=T2)
    run(ws, update_account, account.id, opening_balance=Decimal("0.00"), now=T3)
    after = read(ws, get_import, imported.id)
    assert (after.status, after.error_code) == (ImportStatus.UNRECONCILED, rules.CHAIN_BREAK)
    assert hook_calls == []


def test_lines_on_or_before_the_anchor_are_unreconciled(run, imp, fresh_workspaces, hook_calls):
    ws, _ = fresh_workspaces
    account = _account(run, ws, opening_on=date(2026, 4, 15))
    imported = imp(ws, account.id, ZIP)
    assert (imported.status, imported.error_code) == (
        ImportStatus.UNRECONCILED,
        rules.BEFORE_ANCHOR,
    )
    assert hook_calls == []


def test_moving_the_anchor_rechecks_the_imports(run, imp, read, fresh_workspaces, hook_calls):
    ws, _ = fresh_workspaces
    account = _account(run, ws)
    imported = imp(ws, account.id, ZIP)

    run(ws, update_account, account.id, opening_balance=Decimal("5.00"), now=T2)
    moved = read(ws, get_import, imported.id)
    assert (moved.status, moved.error_code) == (ImportStatus.UNRECONCILED, rules.OPENING_MISMATCH)
    assert moved.computed_opening == Decimal("5.00")

    run(ws, update_account, account.id, opening_balance=Decimal("0.00"), now=T3)
    assert read(ws, get_import, imported.id).status is ImportStatus.RECONCILED
    assert _types(read, ws, imported.id) == [
        EventType.STATEMENT_IMPORTED,
        EventType.STATEMENT_RECONCILED,
        EventType.STATEMENT_UNRECONCILED,
        EventType.STATEMENT_RECONCILED,
    ]
    assert hook_calls == [imported.id, imported.id]
    assert _types(read, ws, account.id) == [
        EventType.ACCOUNT_CREATED,
        EventType.ACCOUNT_UPDATED,
        EventType.ACCOUNT_UPDATED,
    ]
    # A label change is not an anchor move: nothing is re-checked.
    run(ws, update_account, account.id, label="Mietkonto Lindenstraße", now=T3)
    assert len(_types(read, ws, imported.id)) == 4


# --- Cross-format duplicates -----------------------------------------------------------


def test_cross_format_duplicates_go_to_review_and_merge_rereconciles(
    run, imp, read, app_engine, fresh_workspaces, hook_calls
):
    """The CSV first, then the ZIP: no hash matches across formats (D22), so all 22 lines are
    stored twice and 19 pairs go to review. Merging them takes the later copies out of every
    balance; the 3 Rechnungsabschluss lines name no counterparty in camt, so they cannot be
    paired and their double count remains."""
    ws, _ = fresh_workspaces
    account = _account(run, ws)
    from_csv = imp(ws, account.id, CSV_BYTES, media_type="text/csv")
    run(ws, declare_balances, from_csv.id, DeclareBalanceBody(closing_balance=CLOSING), now=T1)
    from_camt = imp(ws, account.id, ZIP, now=T2)

    assert (from_camt.lines_new, from_camt.status) == (22, ImportStatus.UNRECONCILED)
    assert from_camt.computed_closing == 2 * CLOSING
    # The CSV import's stored window doubled too: it was re-checked.
    assert read(ws, get_import, from_csv.id).status is ImportStatus.UNRECONCILED
    pairs = read(ws, list_possible_duplicates, status=DuplicateStatus.OPEN)
    assert len(pairs) == 19
    kinds = [item.kind for item in read(ws, import_inbox_items)]
    assert kinds.count(InboxItemKind.POSSIBLE_DUPLICATE) == 19
    csv_ids = {line.id for line in read(ws, list_transactions, account.id, import_id=from_csv.id)}
    assert all(pair.transaction_id in csv_ids for pair in pairs)

    for pair in pairs:
        decided = run(ws, resolve_duplicate, pair.id, DuplicateStatus.MERGED, now=T3)
        assert decided.status is DuplicateStatus.MERGED and decided.decided_at == T3
    after = read(ws, get_import, from_camt.id)
    assert after.computed_closing == CLOSING + UNPAIRED_ABSCHLUSS
    assert after.status is ImportStatus.UNRECONCILED
    excluded = [
        line
        for line in read(ws, list_transactions, account.id, import_id=from_camt.id)
        if line.excluded_as_duplicate_of is not None
    ]
    assert len(excluded) == 19
    event = read(ws, list_events, entity_id=from_camt.id)[-1]
    assert event.type is EventType.STATEMENT_UNRECONCILED
    assert Decimal(event.payload["difference"]) == -UNPAIRED_ABSCHLUSS
    resolved = [
        e
        for e in read(ws, list_events, entity_id=from_camt.id)
        if e.type is EventType.STATEMENT_DUPLICATE_RESOLVED
    ]
    assert len(resolved) == 19
    assert read(ws, list_possible_duplicates, status=DuplicateStatus.OPEN) == []

    with pytest.raises(ConflictError):
        run(ws, resolve_duplicate, pairs[0].id, DuplicateStatus.KEPT_BOTH)


def test_kept_both_excludes_nothing(run, imp, read, fresh_workspaces):
    ws, _ = fresh_workspaces
    account = _account(run, ws)
    imp(ws, account.id, CSV_BYTES, media_type="text/csv")
    from_camt = imp(ws, account.id, ZIP, now=T2)
    pair = read(ws, list_possible_duplicates)[0]
    kept = run(ws, resolve_duplicate, pair.id, DuplicateStatus.KEPT_BOTH, now=T3)
    assert kept.status is DuplicateStatus.KEPT_BOTH
    assert read(ws, get_import, from_camt.id).computed_closing == 2 * CLOSING
    lines = read(ws, list_transactions, account.id)
    assert all(line.excluded_as_duplicate_of is None for line in lines)


def test_merge_refuses_to_drop_both_copies(run, imp, read, app_engine, fresh_workspaces):
    """A pair whose keeper is itself excluded (here: the reverse of a merged pair) would take
    both copies out of the balance."""
    ws, _ = fresh_workspaces
    account = _account(run, ws)
    imp(ws, account.id, CSV_BYTES, media_type="text/csv")
    imp(ws, account.id, ZIP, now=T2)
    pair = read(ws, list_possible_duplicates)[0]
    run(ws, resolve_duplicate, pair.id, DuplicateStatus.MERGED, now=T3)
    reverse = _insert_pair(app_engine, ws, pair.other_transaction_id, pair.transaction_id)
    with pytest.raises(RuleError) as caught:
        run(ws, resolve_duplicate, reverse, DuplicateStatus.MERGED)
    assert caught.value.code == rules.DUPLICATE_EXCLUDES_BOTH
    # Kept both is still a valid answer to it.
    run(ws, resolve_duplicate, reverse, DuplicateStatus.KEPT_BOTH)


def test_merge_refuses_lines_of_two_accounts(run, imp, read, app_engine, fresh_workspaces):
    ws, _ = fresh_workspaces
    first = _account(run, ws)
    second = _account(run, ws, iban=OTHER_IBAN)
    imp(ws, first.id, ZIP)
    other_csv = CSV_BYTES.replace(SAMPLE_IBAN.encode(), OTHER_IBAN.encode())
    assert other_csv != CSV_BYTES
    imp(ws, second.id, other_csv, media_type="text/csv", now=T2)
    a = read(ws, list_transactions, first.id)[0]
    b = read(ws, list_transactions, second.id)[0]
    pair = _insert_pair(app_engine, ws, a.id, b.id)
    with pytest.raises(RuleError) as caught:
        run(ws, resolve_duplicate, pair, DuplicateStatus.MERGED)
    assert caught.value.code == rules.DUPLICATE_ACCOUNTS_DIFFER


def _insert_pair(app_engine, ws, transaction_id, other_transaction_id) -> uuid.UUID:
    with workspace_transaction(app_engine, ws) as conn:
        return conn.execute(
            insert(duplicate_table)
            .values(
                workspace_id=ws,
                transaction_id=transaction_id,
                other_transaction_id=other_transaction_id,
            )
            .returning(duplicate_table.c.id)
        ).scalar_one()


# --- Refusals ---------------------------------------------------------------------------


def test_another_accounts_statement_is_refused_before_anything_is_stored(
    run, imp, app_engine, fresh_workspaces
):
    ws, _ = fresh_workspaces
    account = _account(run, ws, iban=OTHER_IBAN)
    with pytest.raises(RuleError) as caught:
        imp(ws, account.id, ZIP)
    assert caught.value.code == rules.ACCOUNT_MISMATCH
    assert _count(app_engine, ws, document_table) == 0


def test_a_private_account_imports_its_statements(run, imp, fresh_workspaces):
    """Phase 6 D156: the guard that kept statements off an owner account is gone."""
    ws, _ = fresh_workspaces
    private = _account(run, ws, owner=True)
    assert private.purpose == "private"
    imported = imp(ws, private.id, ZIP)
    assert imported.lines_new == 22


def test_an_account_with_imports_may_change_its_purpose(run, imp, fresh_workspaces):
    """Phase 6 D156: the guard against an account with imports becoming an owner account is
    gone; a purpose change is checked against its bookings instead (none here)."""
    ws, _ = fresh_workspaces
    account = _account(run, ws)
    imp(ws, account.id, ZIP)
    flagged = run(ws, update_account, account.id, is_owner_account=True)
    assert (flagged.is_owner_account, flagged.purpose) == (True, "rental")
    private = run(ws, update_account, account.id, purpose="private")
    assert private.purpose == "private"


def test_an_unreadable_file_writes_nothing(run, imp, app_engine, fresh_workspaces):
    ws, _ = fresh_workspaces
    account = _account(run, ws)
    with pytest.raises(RuleError) as caught:
        imp(ws, account.id, _bytes(CAMT, "zip_inside_zip"))
    assert caught.value.code == rules.ARCHIVE_REJECTED
    assert _count(app_engine, ws, document_table) == 0


def test_the_iban_is_unique_per_workspace(run, fresh_workspaces):
    ws, other = fresh_workspaces
    _account(run, ws)
    with pytest.raises(ConflictError):
        _account(run, ws, iban="de50 1000 0000 0000 0000 01")
    _account(run, other)  # another household may hold the same account


# --- Privacy and isolation ---------------------------------------------------------------


def test_no_plaintext_iban_is_stored_in_a_bank_line(run, imp, app_engine, fresh_workspaces):
    """§14 step 3 FOCUS: transaction.raw never holds a plaintext IBAN (only the service
    enforces it), and no column but the masked one carries a trace of one. An IBAN in a raw
    CSV column (here Sammlerreferenz) is masked on store."""
    ws, _ = fresh_workspaces
    account = _account(run, ws)
    imp(ws, account.id, ZIP)
    planted = _plant_iban_in_csv(CSV_BYTES)
    imp(ws, account.id, planted, media_type="text/csv", now=T2)

    plaintexts = {SAMPLE_IBAN}
    for sample in (ZIP, CSV_BYTES):
        for line in parse_statement(sample).statement.lines:
            if line.counterparty_iban:
                plaintexts.add(normalize_iban(line.counterparty_iban))
    plaintexts.add(OTHER_IBAN)
    with workspace_transaction(app_engine, ws) as conn:
        rows = conn.execute(text("SELECT t::text AS row, t.raw FROM transaction t")).all()
    assert len(rows) == 44
    for row in rows:
        compact = "".join(row.row.split()).upper()
        assert not any(iban in compact for iban in plaintexts)
        for value in _strings(row.raw):
            with pytest.raises(RuleError):
                normalize_iban(value)
    assert any("DE89 **** 3000" in "".join(_strings(row.raw)) for row in rows)


def _plant_iban_in_csv(data: bytes) -> bytes:
    """The sample CSV with a spaced IBAN in its Sammlerreferenz column on every line."""
    text_ = data.decode("cp1252")
    lines = text_.split("\n")
    header = lines[0].split(";")
    index = next(i for i, name in enumerate(header) if "Sammlerreferenz" in name)
    out = [lines[0]]
    for line in lines[1:]:
        if not line:
            out.append(line)
            continue
        cells = line.split(";")
        cells[index] = '"Ref DE89 3704 0044 0532 0130 00"'
        out.append(";".join(cells))
    return "\n".join(out).encode("cp1252")


def _strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, str):
        yield value


def test_another_workspace_sees_nothing(run, imp, read, fresh_workspaces):
    ws, other = fresh_workspaces
    account = _account(run, ws)
    imported = imp(ws, account.id, ZIP)
    assert read(other, get_account, account.id) is None
    assert read(other, list_accounts) == []
    assert read(other, get_import, imported.id) is None
    assert read(other, list_transactions, account.id) == []
    assert read(other, list_possible_duplicates) == []
    assert read(other, import_inbox_items) == []
    with pytest.raises(NotFoundError):
        imp(other, account.id, ZIP, now=T2)
    with pytest.raises(NotFoundError):
        run(other, declare_balances, imported.id, DeclareBalanceBody(closing_balance=CLOSING))
    with pytest.raises(NotFoundError):
        run(other, update_account, account.id, label="x")


# --- The opening balance from the first import (principle 7) ----------------------------

JAN_CSV = _bytes(CSV, "utf8_with_bom")  # 04.01.2027, +1.400,00
FEB_CSV = _bytes(CSV, "header_variants_unquoted_reordered_windows_1252")  # Feb 2027, 990,00
YEAR_END = date(2026, 12, 31)


def _declare(run, ws, import_id, closing: str, now=T2):
    body = DeclareBalanceBody(closing_balance=Decimal(closing))
    return run(ws, declare_balances, import_id, body, now=now)


def test_the_first_import_offers_the_opening_balance_it_implies(
    run, imp, read, fresh_workspaces, hook_calls
):
    """A new account's balance on its anchor day is rarely known, but its first export states
    today's: anchor + sum of the lines = closing gives the anchor, and taking it reconciles."""
    ws, _ = fresh_workspaces
    account = _account(run, ws, opening_on=YEAR_END)
    jan = imp(ws, account.id, JAN_CSV, media_type="text/csv")
    feb = imp(ws, account.id, FEB_CSV, media_type="text/csv", now=T2)
    _declare(run, ws, jan.id, "5400.00")
    shown = {i.id: i for i in read(ws, list_imports, account.id)}
    assert (shown[jan.id].status, shown[jan.id].error_code) == (
        ImportStatus.UNRECONCILED,
        rules.UNRECONCILED,
    )
    assert shown[jan.id].derived_opening_balance == Decimal("4000.00")
    assert shown[jan.id].derived_opening_balance_on == YEAR_END
    assert shown[feb.id].derived_opening_balance is None  # pending, and not the first
    assert read(ws, get_import, jan.id).derived_opening_balance == Decimal("4000.00")

    taken = run(ws, opening_from_closing, jan.id, now=T3)
    assert taken.status is ImportStatus.RECONCILED and taken.derived_opening_balance is None
    moved = read(ws, get_account, account.id)
    # The anchor is the closing less the lines since the anchor day.
    assert moved.opening_balance == Decimal("5400.00") - Decimal("1400.00")
    assert moved.opening_balance_on == YEAR_END
    assert _types(read, ws, account.id)[-1] == EventType.ACCOUNT_UPDATED
    assert hook_calls == [jan.id]
    # Taken twice (a double click): the import reconciles now, so nothing is derived.
    with pytest.raises(RuleError) as caught:
        run(ws, opening_from_closing, jan.id, now=T3)
    assert caught.value.code == rules.OPENING_NOT_DERIVABLE


def test_a_later_import_does_not_offer_the_opening_balance(run, imp, read, fresh_workspaces):
    ws, _ = fresh_workspaces
    account = _account(run, ws, opening_on=YEAR_END)
    feb = imp(ws, account.id, FEB_CSV, media_type="text/csv")
    _declare(run, ws, feb.id, "5000.00")
    assert read(ws, get_import, feb.id).derived_opening_balance == Decimal("4010.00")
    imp(ws, account.id, JAN_CSV, media_type="text/csv", now=T2)  # an earlier export arrives
    assert read(ws, get_import, feb.id).derived_opening_balance is None
    with pytest.raises(RuleError) as caught:
        run(ws, opening_from_closing, feb.id, now=T3)
    assert caught.value.code == rules.OPENING_NOT_DERIVABLE
    assert read(ws, get_account, account.id).opening_balance == Decimal("0.00")


def test_a_reconciled_import_already_proves_the_opening_balance(run, imp, read, fresh_workspaces):
    ws, _ = fresh_workspaces
    account = _account(run, ws, opening_on=YEAR_END)
    jan = imp(ws, account.id, JAN_CSV, media_type="text/csv")
    feb = imp(ws, account.id, FEB_CSV, media_type="text/csv", now=T2)
    _declare(run, ws, jan.id, "5400.00")  # a typo in the earliest import
    assert _declare(run, ws, feb.id, "2390.00").status is ImportStatus.RECONCILED
    assert read(ws, get_import, jan.id).derived_opening_balance is None
    with pytest.raises(RuleError) as caught:
        run(ws, opening_from_closing, jan.id, now=T3)
    assert caught.value.code == rules.OPENING_NOT_DERIVABLE


def test_a_file_with_an_opening_balance_offers_that_opening(run, imp, read, fresh_workspaces):
    """A file that states its opening (camt, a card's Alter Saldo) is the account's earliest
    import and nothing is reconciled: the file's opening, dated the day before its first
    booking, is offered (it was offered nothing until 13 Nov 2026, when a card account
    starting at 0 € had no way out of statement.opening_mismatch)."""
    ws, _ = fresh_workspaces
    account = _account(run, ws)
    imported = imp(ws, account.id, ZIP)
    run(ws, update_account, account.id, opening_balance=Decimal("5.00"), now=T2)
    moved = read(ws, get_import, imported.id)
    assert moved.error_code == rules.OPENING_MISMATCH
    assert moved.derived_opening_balance == moved.declared_opening
    assert moved.derived_opening_balance_on == moved.first_booking_on - timedelta(days=1)
    run(ws, opening_from_closing, imported.id, now=T3)
    assert read(ws, get_import, imported.id).status is ImportStatus.RECONCILED


def test_another_workspace_cannot_take_the_opening_balance(run, imp, fresh_workspaces):
    ws, other = fresh_workspaces
    account = _account(run, ws, opening_on=YEAR_END)
    jan = imp(ws, account.id, JAN_CSV, media_type="text/csv")
    _declare(run, ws, jan.id, "5400.00")
    with pytest.raises(NotFoundError):
        run(other, opening_from_closing, jan.id, now=T3)
