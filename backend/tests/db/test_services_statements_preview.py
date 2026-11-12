"""services.statements: phase 5's preview_import (D127), the bank-declared balance (D117) and
the last reconciled closing, as hausbuch_app under RLS.

The input is phase 2's anonymised camt ZIP and CSV (22 bookings, closing -3.028,51), built
with the parser tests' own builder; the IBANs are synthetic."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from sqlalchemy import func, select
from test_bank_parsers import CAMT, CSV, build

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import document as document_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.rules import statements as rules
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.bank import AccountCreate
from hausbuch.schemas.enums import BalanceSource, ImportStatus
from hausbuch.services import statements
from hausbuch.services.accounts import create_account
from hausbuch.services.bank import parse_statement
from hausbuch.services.statements import (
    account_has_lines,
    declare_bank_balance,
    import_statement,
    last_reconciled_closing,
    preview_import,
)

pytestmark = pytest.mark.db

T0 = datetime(2026, 11, 6, 21, 0, tzinfo=UTC)
T1 = datetime(2026, 11, 7, 8, 0, tzinfo=UTC)
SAMPLE_IBAN = "DE50100000000000000001"
OTHER_IBAN = "DE89370400440532013000"
CLOSING = Decimal("-3028.51")


def _bytes(fixture, case_id) -> bytes:
    return build(next(c for c in fixture.cases if c.id == case_id).input)


ZIP = _bytes(CAMT, "sparkasse_zip_of_daily_reports")
CSV_BYTES = _bytes(CSV, "sparkasse_export")


def _account(run, ws, *, iban=SAMPLE_IBAN, owner=False):
    data = AccountCreate(
        label="Mietkonto",
        bank="Sparkasse",
        iban=iban,
        opening_balance=Decimal("0.00"),
        opening_balance_on=date(2026, 3, 31),
        is_owner_account=owner,
    )
    return run(ws, create_account, data, iban_key=TEST_IBAN_KEY, now=T0)


def _import(run, blob_store, ws, account_id, data, media_type="application/zip"):
    return run(
        ws,
        import_statement,
        blob_store,
        account_id,
        data,
        media_type=media_type,
        iban_key=TEST_IBAN_KEY,
        original_filename="export",
        now=T1,
    )


def _counts(app_engine, ws) -> tuple[int, ...]:
    with workspace_transaction(app_engine, ws) as conn:
        return tuple(
            conn.execute(select(func.count()).select_from(t)).scalar_one()
            for t in (document_table, import_table, transaction_table, event_table)
        )


def _preview(read, ws, account_id, data=ZIP):
    return read(ws, preview_import, account_id, data, iban_key=TEST_IBAN_KEY)


def test_the_preview_counts_every_line_new_and_writes_nothing(
    run, read, app_engine, fresh_workspaces, monkeypatch
):
    ws, _ = fresh_workspaces
    account = _account(run, ws)

    def no_upload(*args, **kwargs):
        raise AssertionError("the preview must not store a document")

    monkeypatch.setattr(statements, "upload_document", no_upload)
    before = _counts(app_engine, ws)

    preview = _preview(read, ws, account.id)

    assert (preview.lines_total, preview.lines_new, preview.closing_balance) == (22, 22, CLOSING)
    assert _counts(app_engine, ws) == before


def test_after_the_import_the_preview_of_the_same_bytes_finds_nothing_new(
    run, read, app_engine, fresh_workspaces, blob_store
):
    ws, _ = fresh_workspaces
    account = _account(run, ws)
    imported = _import(run, blob_store, ws, account.id, ZIP)
    assert imported.lines_new == 22

    before = _counts(app_engine, ws)
    preview = _preview(read, ws, account.id)

    assert (preview.lines_total, preview.lines_new) == (22, 0)
    assert preview.last_booking_on == imported.last_booking_on  # D141: stored lines count
    assert _counts(app_engine, ws) == before


def test_the_preview_and_the_import_hash_the_same_bytes_alike(
    run, app_engine, fresh_workspaces, blob_store
):
    ws, _ = fresh_workspaces
    account = _account(run, ws)
    _import(run, blob_store, ws, account.id, ZIP)
    statement = parse_statement(ZIP).statement

    previewed = set(statements._line_hashes(account.id, statement, TEST_IBAN_KEY))

    with workspace_transaction(app_engine, ws) as conn:
        stored = {
            row.line_hash.hex()
            for row in conn.execute(
                select(transaction_table.c.line_hash).where(
                    transaction_table.c.account_id == account.id
                )
            )
        }
    assert previewed == stored and len(stored) == 22


def test_a_csv_of_the_same_lines_is_another_format_so_every_line_counts_new(
    run, read, fresh_workspaces, blob_store
):
    """D22: the same booking in CSV and camt hashes differently (the cross-format duplicate
    review catches it after an import); the preview says what the import would store."""
    ws, _ = fresh_workspaces
    account = _account(run, ws)
    _import(run, blob_store, ws, account.id, CSV_BYTES, media_type="text/csv")

    assert _preview(read, ws, account.id).lines_new == 22


def test_the_preview_refuses_what_the_import_refuses(run, read, fresh_workspaces):
    ws, _ = fresh_workspaces
    account = _account(run, ws, iban=OTHER_IBAN)
    with pytest.raises(RuleError) as raised:
        _preview(read, ws, account.id)
    assert raised.value.code == rules.ACCOUNT_MISMATCH


def test_a_private_account_previews_like_the_rental_one(run, read, fresh_workspaces):
    """Phase 6 D156: the owner-account guard is gone; a private account imports."""
    ws, _ = fresh_workspaces
    account = _account(run, ws, owner=True)
    assert account.purpose == "private"
    assert _preview(read, ws, account.id).lines_new == 22


def test_a_bank_declared_balance_reconciles_a_file_without_balances(
    run, read, fresh_workspaces, blob_store
):
    ws, _ = fresh_workspaces
    account = _account(run, ws)
    imported = _import(run, blob_store, ws, account.id, CSV_BYTES, media_type="text/csv")
    assert imported.status is ImportStatus.IMPORTED  # pending_balance

    checked = run(ws, declare_bank_balance, imported.id, CLOSING, now=T1)

    assert checked.status is ImportStatus.RECONCILED
    assert checked.balance_source is BalanceSource.BANK
    assert checked.declared_opening is None
    assert read(ws, last_reconciled_closing, account.id) == CLOSING


def test_a_file_that_states_its_balance_is_never_overridden_by_the_bank(
    run, fresh_workspaces, blob_store
):
    ws, _ = fresh_workspaces
    account = _account(run, ws)
    imported = _import(run, blob_store, ws, account.id, ZIP)
    with pytest.raises(RuleError) as raised:
        run(ws, declare_bank_balance, imported.id, Decimal("1.00"), now=T1)
    assert raised.value.code == rules.BALANCE_FROM_FILE


def test_last_reconciled_closing(run, read, fresh_workspaces, blob_store):
    ws, _ = fresh_workspaces
    account = _account(run, ws)
    assert read(ws, last_reconciled_closing, account.id) is None
    assert read(ws, account_has_lines, account.id) is False

    _import(run, blob_store, ws, account.id, CSV_BYTES, media_type="text/csv")
    assert read(ws, last_reconciled_closing, account.id) is None  # pending, not reconciled
    assert read(ws, account_has_lines, account.id) is True


def test_last_reconciled_closing_of_a_reconciled_file(run, read, fresh_workspaces, blob_store):
    ws, _ = fresh_workspaces
    account = _account(run, ws)
    _import(run, blob_store, ws, account.id, ZIP)
    assert read(ws, last_reconciled_closing, account.id) == CLOSING
