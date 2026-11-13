"""A depot's cash statement imported end to end, as hausbuch_app under RLS (phase 6 step 4,
D165 (1) and D156 as amended). Invented statements (tests/test_depot_pdf.py).

A depot account is private; its first cash statement sets its account number (masked +
HMAC'd); the IBAN the statement prints is only proposed until a person confirms it from the
statement's original, and then joins the household's own IBANs. The lines are booked by code
from their headline (matcher_depot) with the model off.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from sqlalchemy import text
from test_card_pdf import draw
from test_depot_pdf import IBAN, KONTO, ROWS, page
from test_services_card_import import _deps

from hausbuch.jobs import enqueue
from hausbuch.jobs.matching import run_match_transaction
from hausbuch.rules import statements as rules
from hausbuch.rules.errors import ACCOUNT_KIND_NOT_AVAILABLE, RuleError
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.bank import AccountCreate, ConfirmPaymentIbanBody, TransactionAllocationStatus
from hausbuch.schemas.enums import AccountKind, AccountPurpose, ImportStatus
from hausbuch.services import statements
from hausbuch.services.accounts import confirm_depot_iban, create_account, get_account
from hausbuch.services.errors import ConflictError, NotFoundError
from hausbuch.services.statements import import_statement, list_transactions
from hausbuch.services.transfers import own_account_hmacs

pytestmark = pytest.mark.db

T0 = datetime(2026, 11, 13, 8, 0, tzinfo=UTC)


@pytest.fixture
def hook_calls(monkeypatch) -> list:
    calls: list = []
    monkeypatch.setattr(
        statements, "_on_reconciled", lambda ctx, import_id: calls.append(import_id)
    )
    return calls


def _depot(run, ws, **overrides):
    data = {
        "label": "Globales Portfolio",
        "bank": "Beispiel-Vermögensverwaltung",
        "kind": AccountKind.DEPOT,
        "provider": "quirion",
        "opening_balance": Decimal("100.00"),
        "opening_balance_on": date(2026, 3, 1),
    } | overrides
    return run(ws, create_account, AccountCreate(**data), iban_key=TEST_IBAN_KEY, now=T0)


def _import(run, blob_store, ws, account_id, pages=None, n=0):
    return run(
        ws,
        import_statement,
        blob_store,
        account_id,
        draw(pages or [page()]),
        media_type="application/pdf",
        iban_key=TEST_IBAN_KEY,
        original_filename="kontoauszug.pdf",
        now=T0 + timedelta(minutes=n),
    )


def test_a_cash_statement_imports_and_its_lines_are_booked_by_their_headline(
    run, read, blob_store, fresh_workspaces, hook_calls, app_engine, tmp_path
):
    ws, _ = fresh_workspaces
    account = _depot(run, ws)
    assert account.purpose is AccountPurpose.PRIVATE
    assert (account.iban_masked, account.external_ref_masked) == (None, None)
    imported = _import(run, blob_store, ws, account.id)
    assert imported.status is ImportStatus.RECONCILED
    assert hook_calls == [imported.id]
    after = read(ws, get_account, account.id)
    assert after.external_ref_masked == "**** 0123"  # set by the first statement
    assert after.iban_masked is None  # only proposed (principle 3)
    assert after.iban_proposal.import_id == imported.id
    assert after.iban_proposal.iban_masked == "DE89 **** 3000"

    deps = _deps(app_engine, tmp_path)
    for line in read(ws, list_transactions, account.id):
        run_match_transaction(deps, workspace_id=ws, transaction_id=line.id, attempt=0)
    lines = {t.bank_reference: t for t in read(ws, list_transactions, account.id)}
    assert {ref: t.allocation_categories for ref, t in lines.items()} == {
        "5500000001": [C.SECURITY_SALE],
        "5500000002": [C.SECURITY_PURCHASE],
        "5500000003": [C.DEPOT_FEE],
    }
    assert all(t.allocation_status is TransactionAllocationStatus.CONFIRMED for t in lines.values())
    stored = " ".join(f"{t.booking_text} {t.purpose}" for t in lines.values())
    assert KONTO not in stored and "Mustermann" not in stored


def test_a_headline_no_pattern_knows_waits(
    run, read, blob_store, fresh_workspaces, hook_calls, app_engine, tmp_path
):
    ws, _ = fresh_workspaces
    account = _depot(run, ws)
    rows = ["-10,00 EUR02.03.202602.03.2026Storno Lastschrift, Ref.: 5500000009"]
    _import(run, blob_store, ws, account.id, [page(rows=rows, total="-10,00", closing="90,00")])
    deps = _deps(app_engine, tmp_path)
    (line,) = read(ws, list_transactions, account.id)
    run_match_transaction(deps, workspace_id=ws, transaction_id=line.id, attempt=0)
    (line,) = read(ws, list_transactions, account.id)
    assert line.allocation_status is TransactionAllocationStatus.OPEN


def test_a_cash_statement_goes_onto_a_depot_only_and_a_depot_takes_no_other_file(
    run, blob_store, fresh_workspaces
):
    ws, _ = fresh_workspaces
    bank = run(
        ws,
        create_account,
        AccountCreate(
            label="Girokonto",
            bank="Beispielbank",
            iban="DE50100000000000003008",
            opening_balance=Decimal("0.00"),
            opening_balance_on=T0.date(),
            purpose=AccountPurpose.PRIVATE,
        ),
        iban_key=TEST_IBAN_KEY,
    )
    with pytest.raises(RuleError) as caught:
        _import(run, blob_store, ws, bank.id)
    assert caught.value.code == rules.ACCOUNT_MISMATCH


def test_a_second_depot_cannot_take_the_first_ones_statements(
    run, blob_store, fresh_workspaces, hook_calls
):
    ws, _ = fresh_workspaces
    first, second = _depot(run, ws), _depot(run, ws, label="Megatrends")
    _import(run, blob_store, ws, first.id)
    with pytest.raises(RuleError) as caught:
        _import(
            run,
            blob_store,
            ws,
            second.id,
            [page(rows=ROWS[4:7], total="-500,00", closing="-400,00")],
        )
    assert caught.value.code == rules.ACCOUNT_MISMATCH


def test_a_depot_may_be_created_with_its_number_and_iban_both_masked(run, fresh_workspaces):
    ws, _ = fresh_workspaces
    depot = _depot(run, ws, external_ref=KONTO, iban=IBAN)
    assert (depot.external_ref_masked, depot.iban_masked) == ("**** 0123", "DE89 **** 3000")
    with pytest.raises(RuleError) as caught:
        _depot(run, ws, purpose=AccountPurpose.RENTAL, label="Miete")
    assert caught.value.code == ACCOUNT_KIND_NOT_AVAILABLE


def test_the_depot_iban_is_confirmed_from_the_statement_and_joins_the_own_ibans(
    run, read, blob_store, fresh_workspaces, hook_calls, app_engine
):
    ws, _ = fresh_workspaces
    depot = _depot(run, ws)
    imported = _import(run, blob_store, ws, depot.id)
    body = ConfirmPaymentIbanBody(import_id=imported.id)
    result = run(ws, confirm_depot_iban, blob_store, depot.id, body, iban_key=TEST_IBAN_KEY)
    assert result.account.iban_masked == "DE89 **** 3000"
    assert result.account.iban_proposal is None
    assert result.rules_disabled == 0
    ours = read(ws, own_account_hmacs)
    assert len(ours) == 1
    lock = enqueue.waiting_lock(enqueue.waiting_trigger(enqueue.TRIGGER_ACCOUNT, depot.id))
    with app_engine.begin() as conn:
        conn.execute(text("DELETE FROM procrastinate_jobs WHERE lock = :lock"), {"lock": lock})
    again = run(ws, confirm_depot_iban, blob_store, depot.id, body, iban_key=TEST_IBAN_KEY)
    assert again.account == result.account
    # A statement of the same depot is still taken once the IBAN is confirmed.
    _import(run, blob_store, ws, depot.id, [page(closing="822,20")], n=1)


def test_the_depot_iban_is_refused_where_it_does_not_belong(
    run, blob_store, fresh_workspaces, hook_calls
):
    ws, _ = fresh_workspaces
    depot = _depot(run, ws)
    other = _depot(run, ws, label="Verrechnungskonto")
    imported = _import(run, blob_store, ws, depot.id)
    with pytest.raises(NotFoundError):
        run(
            ws,
            confirm_depot_iban,
            blob_store,
            other.id,
            ConfirmPaymentIbanBody(import_id=imported.id),
            iban_key=TEST_IBAN_KEY,
        )
    run(
        ws,
        create_account,
        AccountCreate(
            label="Girokonto",
            bank="Beispielbank",
            iban=IBAN,
            opening_balance=Decimal("0.00"),
            opening_balance_on=T0.date(),
            purpose=AccountPurpose.PRIVATE,
        ),
        iban_key=TEST_IBAN_KEY,
    )
    with pytest.raises(ConflictError):
        run(
            ws,
            confirm_depot_iban,
            blob_store,
            depot.id,
            ConfirmPaymentIbanBody(import_id=imported.id),
            iban_key=TEST_IBAN_KEY,
        )
