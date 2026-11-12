"""A DKB CSV export imported end to end, as hausbuch_app under RLS (phase 6 step 1, D173 (3),
D164, Q-O75; fixture tests/fixtures/phase6/dkb_csv.yaml, its `import` half).

The file states no balance tied to its lines, only „Kontostand vom“: the import waits at
pending_balance, the stated balance pre-fills the closing the user confirms, and the card
asks whether it is the balance after the file's last booking. A file naming another IBAN is
refused before anything is stored (D164).

Reading: the fixture writes the question inside „…“ as a quotation; the card's question is
the text between them.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from test_bank_parsers import DKB, build

from hausbuch.rules.errors import RuleError
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.bank import AccountCreate, DeclareBalanceBody
from hausbuch.schemas.enums import AccountPurpose, BalanceSource, ImportStatus
from hausbuch.services import statements
from hausbuch.services.accounts import create_account
from hausbuch.services.statements import (
    declare_balances,
    get_import,
    import_inbox_items,
    import_statement,
    list_imports,
)

pytestmark = pytest.mark.db

T0 = datetime(2026, 11, 6, 21, 0, tzinfo=UTC)
T1 = datetime(2026, 11, 7, 8, 0, tzinfo=UTC)

BUILT = [c for c in DKB.cases if c.pending and c.pending.startswith("phase 6 step 1")]
WAITING = [c for c in BUILT if c.import_.status is not None]
REFUSED = [c for c in BUILT if c.import_.error is not None]


@pytest.fixture
def hook_calls(monkeypatch) -> list:
    calls: list = []
    monkeypatch.setattr(
        statements, "_on_reconciled", lambda ctx, import_id: calls.append(import_id)
    )
    return calls


def _account(run, ws, case):
    """A private DKB current account whose anchor makes the stated balance reconcile: the
    stated closing less the file's lines, the day before its first booking."""
    expect = case.expect
    opening = (case.import_.prefilled_closing or Decimal("0.00")) - expect.total
    data = AccountCreate(
        label="Girokonto DKB",
        bank="DKB",
        iban=case.import_.account_iban,
        opening_balance=opening,
        opening_balance_on=expect.first_booking_on - timedelta(days=1),
        purpose=AccountPurpose.PRIVATE,
        provider="DKB",
    )
    return run(ws, create_account, data, iban_key=TEST_IBAN_KEY, now=T0)


def _import(run, blob_store, ws, account_id, case):
    return run(
        ws,
        import_statement,
        blob_store,
        account_id,
        build(case.input),
        media_type=case.input.media_type,
        iban_key=TEST_IBAN_KEY,
        original_filename="dkb.csv",
        now=T1,
    )


@pytest.mark.parametrize("case", WAITING, ids=lambda c: c.id)
def test_a_dkb_file_waits_with_its_stated_balance_prefilled(
    case, run, read, blob_store, fresh_workspaces, hook_calls
):
    ws, _ = fresh_workspaces
    outcome = case.import_
    account = _account(run, ws, case)
    imported = _import(run, blob_store, ws, account.id, case)
    assert imported.status is ImportStatus.IMPORTED  # the API's pending_balance (no closing)
    assert imported.balance_source is BalanceSource.NONE and imported.declared_closing is None
    assert imported.lines_new == case.expect.line_count
    assert (imported.stated_closing, imported.stated_closing_on) == (
        outcome.prefilled_closing,
        outcome.prefilled_on,
    )
    for again in (read(ws, get_import, imported.id), read(ws, list_imports, account.id)[0]):
        assert (again.stated_closing, again.stated_closing_on) == (
            outcome.prefilled_closing,
            outcome.prefilled_on,
        )

    [item] = read(ws, import_inbox_items)
    assert (item.kind, item.import_id) == (InboxItemKind.IMPORT_PENDING_BALANCE, imported.id)
    assert item.question == outcome.question.removeprefix("„").removesuffix("“")
    assert hook_calls == []

    confirmed = run(
        ws,
        declare_balances,
        imported.id,
        DeclareBalanceBody(closing_balance=outcome.prefilled_closing),
    )
    assert {
        "status": confirmed.status.value,
        "balance_source": confirmed.balance_source.value,
    } == outcome.after_confirm
    assert hook_calls == [imported.id]
    assert read(ws, import_inbox_items) == []


@pytest.mark.parametrize("case", REFUSED, ids=lambda c: c.id)
def test_a_dkb_file_of_another_account_is_refused(case, run, read, blob_store, fresh_workspaces):
    ws, _ = fresh_workspaces
    account = _account(run, ws, case)
    with pytest.raises(RuleError) as caught:
        _import(run, blob_store, ws, account.id, case)
    assert caught.value.code == case.import_.error
    assert read(ws, list_imports, account.id) == []


def test_every_built_case_is_covered():
    assert {c.id for c in WAITING} | {c.id for c in REFUSED} == {c.id for c in BUILT}
    assert BUILT
