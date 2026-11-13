"""Phase 6 step 5a under hausbuch_app and RLS: an exchange account and its exports (D166 as
amended, D175, D178). The texts are the fixtures' invented ones (bison_csv.yaml,
binance_csv.yaml); the pure halves are tests/test_phase6_exchange.py.

Binance's transaction history needs `binance_csv` in movement_import.format, which migration
0025's CHECK does not allow yet: until the migration that adds it, its upload is refused with
exchange.format_not_available before anything is written, and the Binance tests here skip.
"""

import io
import logging
import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import fixture_models_phase6 as fixtures
import pytest
from db.db_support import TEST_IBAN_KEY
from db.matching_support import World
from db.test_services_private_matching import Private
from db.test_services_transfers import _book
from sqlalchemy import select

from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import holding_statement as holding_table
from hausbuch.db.tables import movement_import as import_table
from hausbuch.db.tables import transfer_link as link_table
from hausbuch.rules import errors as E
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.bank import AccountCreate
from hausbuch.schemas.enums import AccountKind, AccountPurpose, ImportStatus
from hausbuch.schemas.household import (
    AnswerMovementBody,
    DeclareHoldingsBody,
    MovementImportFormat,
    MovementKind,
    MovementStatus,
    TransferLinkKind,
)
from hausbuch.services import review
from hausbuch.services.accounts import create_account
from hausbuch.services.errors import RuleViolationError
from hausbuch.services.exchange import imports

pytestmark = pytest.mark.db

T0 = datetime(2026, 11, 13, 8, 0, tzinfo=UTC)
BISON = fixtures.load("bison_csv")
BINANCE = fixtures.load("binance_csv")
REAL_LAYOUT = next(c for c in BISON.parse_cases if c.id == "real_layout").text
BINANCE_READY = "binance_csv" in {f.value for f in MovementImportFormat}
needs_binance_format = pytest.mark.skipif(
    not BINANCE_READY, reason="movement_import.format has no binance_csv yet (needs migration)"
)


def _zip(text: str, *, bom: bool = True) -> bytes:
    data = (b"\xef\xbb\xbf" if bom else b"") + text.replace("\n", "\r\n").encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("export-202611131200.csv", data)
    return buffer.getvalue()


def _exchange(run, ws, provider="BISON", **overrides):
    data = {
        "label": f"{provider}-Konto",
        "bank": provider,
        "kind": AccountKind.EXCHANGE,
        "provider": provider,
        "opening_balance": Decimal("0.00"),
        "opening_balance_on": date(2026, 1, 1),
    } | overrides
    return run(ws, create_account, AccountCreate(**data), iban_key=TEST_IBAN_KEY, now=T0)


def _upload(run, blob_store, ws, account_id, data: bytes, n=0, media_type="text/csv"):
    return run(
        ws,
        imports.import_export,
        blob_store,
        account_id,
        data,
        media_type=media_type,
        original_filename="export.csv",
        now=T0 + timedelta(minutes=n),
    )


def _cards(read, ws, kind):
    return [i for i in read(ws, review.inbox) if i.kind is kind]


def _balances(overview):
    return {p.symbol: p.units for p in overview.positions}


# --- The account (D178 (7)) ----------------------------------------------------------------


def test_an_exchange_is_a_private_account_named_by_its_exchange(run, fresh_workspaces):
    ws, _ = fresh_workspaces
    account = _exchange(run, ws)
    assert (account.kind, account.purpose, account.provider) == (
        AccountKind.EXCHANGE,
        AccountPurpose.PRIVATE,
        "BISON",
    )
    assert (account.iban_masked, account.external_ref_masked) == (None, None)
    with pytest.raises(RuleError) as caught:
        _exchange(run, ws, provider="Kraken")
    assert caught.value.code == E.EXCHANGE_PROVIDER_UNKNOWN
    with pytest.raises(RuleError) as caught:
        _exchange(run, ws, provider="Binance", external_ref="987654321")
    assert caught.value.code == E.ACCOUNT_KIND_NOT_AVAILABLE
    with pytest.raises(RuleError) as caught:
        _exchange(run, ws, purpose=AccountPurpose.RENTAL)
    assert caught.value.code == E.ACCOUNT_KIND_NOT_AVAILABLE


# --- BISON end to end (D166 amended) -------------------------------------------------------


def test_a_bison_export_imports_dedups_and_reconciles_against_the_typed_balances(
    run, read, blob_store, fresh_workspaces
):
    ws, _ = fresh_workspaces
    account = _exchange(run, ws)
    upload = _upload(run, blob_store, ws, account.id, REAL_LAYOUT.encode())
    imported = upload.movement_import
    assert (imported.rows_total, imported.rows_new, imported.rows_skipped) == (4, 4, 0)
    assert imported.status is ImportStatus.IMPORTED
    assert imported.format is MovementImportFormat.BISON_CSV
    assert (imported.first_at, imported.last_at) == (
        datetime(2026, 3, 1, 9, 0, 12, tzinfo=UTC),
        datetime(2026, 3, 20, 18, 42, 7, tzinfo=UTC),
    )
    # The same bytes again (here in the ZIP Binance-style wrapping is a different file, so
    # the plain bytes): the stored import, nothing written.
    again = _upload(run, blob_store, ws, account.id, REAL_LAYOUT.encode(), n=1)
    assert again.movement_import.id == imported.id
    movements = read(ws, imports.exchange_movements, account.id)
    assert sorted((m.kind, m.status) for m in movements) == sorted(
        [
            (MovementKind.INTERNAL_TRANSFER_IN, MovementStatus.CONFIRMED),
            (MovementKind.ACQUISITION, MovementStatus.CONFIRMED),
            (MovementKind.CASHBACK_REWARD, MovementStatus.PROPOSED),
            (MovementKind.INTERNAL_TRANSFER_OUT, MovementStatus.PROPOSED),
        ]
    )
    [deposit] = [m for m in movements if m.kind is MovementKind.CASHBACK_REWARD]
    assert deposit.question == E.EXCHANGE_DEPOSIT_QUESTION
    assert deposit.answers == [
        MovementKind.CASHBACK_REWARD,
        MovementKind.STAKING_REWARD,
        MovementKind.INTERNAL_TRANSFER_IN,
    ]
    overview = read(ws, imports.exchange_overview, account.id)
    # EUR: the deposit 500 less the buy's EUR leg 250 (D178 (6)); BTC: bought, the bonus,
    # sent away.
    assert _balances(overview) == {"EUR": Decimal("250.00"), "BTC": Decimal("0.00009965")}
    assert overview.todos == []  # BISON's export prints no hash: no history to-do
    assert overview.withdrawals_open == 1
    [card] = _cards(read, ws, InboxItemKind.EXCHANGE_PENDING_BALANCE)
    assert card.import_id == imported.id

    # A later export holding one more row adds only that row.
    longer = REAL_LAYOUT + (
        "9a1c3e5b-7d2f-4b60-8e14-3c5a7b9d1f02; Buy; Eur; Btc; 100.00; 0.00160000; "
        "62500.0000000000; 0; 2026-04-01 08:00:00\n"
    )
    second = _upload(run, blob_store, ws, account.id, longer.encode(), n=2).movement_import
    assert (second.rows_total, second.rows_new, second.rows_skipped) == (5, 1, 4)

    [
        first_view,
    ] = [i for i in read(ws, imports.exchange_overview, account.id).imports if i.id == imported.id]
    by_symbol = {b.symbol: b for b in first_view.balances}
    assert by_symbol["EUR"].held == Decimal("250.00")  # to the first import's last row only
    eur, btc = by_symbol["EUR"].asset_id, by_symbol["BTC"].asset_id

    # Every asset it holds must be typed; an asset it never moved is refused.
    with pytest.raises(RuleViolationError) as caught:
        run(
            ws,
            imports.declare_holdings,
            imported.id,
            DeclareHoldingsBody(balances=[{"asset_id": eur, "units": "250.00"}]),
        )
    assert caught.value.codes == (E.EXCHANGE_BALANCE_MISSING,)

    checked = run(
        ws,
        imports.declare_holdings,
        second.id,
        DeclareHoldingsBody(
            balances=[
                {"asset_id": eur, "units": "150.00"},
                {"asset_id": btc, "units": "0.00169965"},
            ]
        ),
        now=T0 + timedelta(minutes=3),
    )
    assert checked.status is ImportStatus.RECONCILED
    assert checked.error_code is None
    assert all(b.reconciled for b in checked.balances)
    assert _cards(read, ws, InboxItemKind.EXCHANGE_PENDING_BALANCE) == []

    # Typed again with a wrong figure: unreconciled, the difference named, never bridged;
    # the earlier typing is kept and superseded.
    wrong = run(
        ws,
        imports.declare_holdings,
        second.id,
        DeclareHoldingsBody(
            balances=[
                {"asset_id": eur, "units": "150.01"},
                {"asset_id": btc, "units": "0.00169965"},
            ]
        ),
        now=T0 + timedelta(minutes=4),
    )
    assert (wrong.status, wrong.error_code) == (ImportStatus.UNRECONCILED, E.EXCHANGE_UNRECONCILED)
    [eur_balance] = [b for b in wrong.balances if b.symbol == "EUR"]
    assert (eur_balance.reconciled, eur_balance.difference_units) == (False, Decimal("0.01"))
    rows = read(
        ws,
        lambda c: c.execute(
            select(holding_table).where(holding_table.c.import_id == second.id)
        ).all(),
    )
    assert len(rows) == 4
    assert sum(r.superseded_by is None for r in rows) == 2
    [card] = _cards(read, ws, InboxItemKind.EXCHANGE_UNRECONCILED)
    assert "EUR: laut Börse 150,01, gebucht 150" in card.reasoning
    overview = read(ws, imports.exchange_overview, account.id)
    [eur_position] = [p for p in overview.positions if p.symbol == "EUR"]
    assert (eur_position.typed_units, eur_position.reconciled) == (Decimal("150.01"), False)


def test_an_eur_deposit_is_linked_to_its_bank_leg(app_engine, fresh_workspaces, blob_store):
    """D162: the private account's transfer out and BISON's EUR deposit two days later are one
    transfer (kind exchange_eur), linked by the import."""
    world = World(app_engine, fresh_workspaces[0], now=T0)
    private = Private(world)
    out = private.line(date(2026, 2, 27), "-500.00", purpose="BISON Einzahlung")
    _book(world, out, "-500.00", C.INTERNAL_TRANSFER_OUT)
    account = world.cmd(
        create_account,
        AccountCreate(
            label="BISON",
            bank="BISON",
            kind=AccountKind.EXCHANGE,
            provider="BISON",
            opening_balance=Decimal("0.00"),
            opening_balance_on=date(2026, 1, 1),
        ),
        iban_key=TEST_IBAN_KEY,
    )
    world.cmd(
        imports.import_export,
        blob_store,
        account.id,
        REAL_LAYOUT.encode(),
        media_type="text/csv",
    )
    [link] = world.query(lambda c: c.execute(select(link_table)).all())
    deposit = world.query(
        lambda c: c.execute(
            select(movement_table.c.id).where(
                movement_table.c.kind == MovementKind.INTERNAL_TRANSFER_IN.value
            )
        ).scalar_one()
    )
    assert (link.out_transaction_id, link.in_movement_id) == (out, deposit)
    assert link.kind == TransferLinkKind.EXCHANGE_EUR.value


def test_the_withdrawals_are_answered_at_once_and_a_deposit_one_by_one(
    run, read, blob_store, fresh_workspaces
):
    ws, _ = fresh_workspaces
    account = _exchange(run, ws)
    imported = _upload(run, blob_store, ws, account.id, REAL_LAYOUT.encode()).movement_import
    [all_at_once] = _cards(read, ws, InboxItemKind.EXCHANGE_WITHDRAWALS)
    assert all_at_once.import_id == imported.id
    assert all_at_once.reasoning.startswith("1 Auszahlung von Coins")
    [single] = _cards(read, ws, InboxItemKind.EXCHANGE_MOVEMENT)
    assert single.error_code == E.EXCHANGE_DEPOSIT_QUESTION
    assert "BTC" in single.reasoning

    done = run(ws, imports.confirm_withdrawals, imported.id)
    assert done.confirmed == 1
    assert _cards(read, ws, InboxItemKind.EXCHANGE_WITHDRAWALS) == []
    assert run(ws, imports.confirm_withdrawals, imported.id).confirmed == 0

    with pytest.raises(RuleViolationError) as caught:
        run(
            ws,
            imports.answer_movement,
            single.movement_id,
            AnswerMovementBody(kind=MovementKind.DISPOSAL),
        )
    assert caught.value.codes == (E.EXCHANGE_ANSWER_INVALID,)
    new_id = run(
        ws,
        imports.answer_movement,
        single.movement_id,
        AnswerMovementBody(kind=MovementKind.INTERNAL_TRANSFER_IN),
    )
    assert new_id != single.movement_id
    old, new = (
        read(
            ws,
            lambda c, i=i: c.execute(select(movement_table).where(movement_table.c.id == i)).one(),
        )
        for i in (single.movement_id, new_id)
    )
    assert old.superseded_by == new_id
    assert old.movement_hash is not None and new.movement_hash is None
    assert (new.kind, new.status, new.quantity) == (
        MovementKind.INTERNAL_TRANSFER_IN.value,
        MovementStatus.CONFIRMED.value,
        old.quantity,
    )
    assert _cards(read, ws, InboxItemKind.EXCHANGE_MOVEMENT) == []
    with pytest.raises(RuleViolationError) as caught:
        run(ws, imports.confirm_movement, single.movement_id)
    assert caught.value.codes == (E.EXCHANGE_MOVEMENT_DONE,)
    # The superseded row's hash still dedups a re-upload.
    longer = REAL_LAYOUT + (
        "9a1c3e5b-7d2f-4b60-8e14-3c5a7b9d1f02; Buy; Eur; Btc; 100.00; 0.00160000; "
        "62500.0000000000; 0; 2026-04-01 08:00:00\n"
    )
    again = _upload(run, blob_store, ws, account.id, longer.encode(), n=5).movement_import
    assert (again.rows_new, again.rows_skipped) == (1, 4)


def test_what_does_not_belong_on_the_account_is_refused_before_anything_is_written(
    run, read, blob_store, fresh_workspaces
):
    ws, _ = fresh_workspaces
    bison = _exchange(run, ws)
    binance_text = BINANCE.cases[0].transactions
    cases = [
        (bison.id, _zip(binance_text), E.EXCHANGE_ACCOUNT_MISMATCH),
        (
            bison.id,
            _zip("Order No,Time,Pair,Side,Price,Executed,Amount,Fee\n"),
            E.EXCHANGE_BINANCE_SPOT_HISTORY,
        ),
        (bison.id, b"Datum;Betrag\n01.03.2026;1,00\n", E.EXCHANGE_UNREADABLE),
    ]
    binance = _exchange(run, ws, provider="Binance")
    history = next(c for c in BINANCE.cases if c.id == "withdrawal_history_uploaded_later")
    cases.append((binance.id, _zip(history.withdrawals), E.EXCHANGE_TRANSACTIONS_FIRST))
    if not BINANCE_READY:
        cases.append((binance.id, _zip(binance_text), E.EXCHANGE_FORMAT_NOT_AVAILABLE))
    for account_id, data, code in cases:
        with pytest.raises(RuleError) as caught:
            _upload(run, blob_store, ws, account_id, data, media_type="application/zip")
        assert caught.value.code == code
    assert read(ws, lambda c: c.execute(select(import_table)).all()) == []
    assert read(ws, lambda c: c.execute(select(movement_table)).all()) == []


# --- Binance (D178), once its format is in the CHECK ---------------------------------------


@needs_binance_format
def test_binance_withdrawals_completed_by_the_history_uploaded_later(
    run, read, blob_store, fresh_workspaces
):
    ws, _ = fresh_workspaces
    account = _exchange(run, ws, provider="Binance")
    case = next(c for c in BINANCE.cases if c.id == "withdrawal_history_uploaded_later")
    upload = _upload(run, blob_store, ws, account.id, _zip(case.transactions))
    assert upload.withdrawals_open == 1
    assert upload.movement_import.format.value == "binance_csv"
    overview = read(ws, imports.exchange_overview, account.id)
    assert overview.todos == [E.EXCHANGE_WITHDRAWAL_HISTORY_MISSING]
    completed = _upload(run, blob_store, ws, account.id, _zip(case.withdrawals), n=1)
    assert (completed.movement_import, completed.withdrawals_completed) == (None, 1)
    assert completed.withdrawals_open == 0
    [current] = read(ws, imports.exchange_movements, account.id)
    expected = case.expect.movements[0]
    assert (current.network, current.tx_hash, current.external_ref, current.fee_quantity) == (
        expected.network,
        expected.tx_hash,
        expected.external_ref,
        expected.fee_quantity,
    )
    assert current.movement_hash is None
    assert current.document_id == upload.movement_import.document_id  # still its import's
    assert read(ws, imports.exchange_overview, account.id).todos == []
    # The same history again completes nothing twice.
    again = _upload(run, blob_store, ws, account.id, _zip(case.withdrawals), n=2)
    assert (again.withdrawals_completed, again.withdrawals_open) == (0, 0)
    superseded = read(
        ws,
        lambda c: (
            c.execute(
                select(event_table.c.payload).where(event_table.c.type == "movement.superseded")
            )
            .scalars()
            .all()
        ),
    )
    assert len(superseded) == 1 and superseded[0]["document_id"] is not None


@needs_binance_format
def test_binance_typed_balances_and_a_longer_export(run, read, blob_store, fresh_workspaces):
    ws, _ = fresh_workspaces
    account = _exchange(run, ws, provider="Binance")
    case = next(c for c in BINANCE.cases if c.id == "longer_export_reuploaded")
    first = _upload(run, blob_store, ws, account.id, _zip(case.stored)).movement_import
    second = _upload(run, blob_store, ws, account.id, _zip(case.transactions), n=1)
    assert (second.movement_import.rows_total, second.movement_import.rows_new) == (
        case.expect.rows_total,
        case.expect.rows_new,
    )
    assert first.id != second.movement_import.id


@needs_binance_format
def test_the_user_id_and_the_address_are_never_stored(
    run, read, blob_store, fresh_workspaces, caplog
):
    """D178 (2): Binance's User ID and the withdrawal Address reach no movement, import,
    holding, event payload, inbox text or log line (the original file is kept as uploaded,
    principle 6, and is not searched here)."""
    caplog.set_level(logging.DEBUG)
    ws, _ = fresh_workspaces
    secrets = (BINANCE.never_stored.user_id, BINANCE.never_stored.address)
    account = _exchange(run, ws, provider="Binance")
    for n, case in enumerate(c for c in BINANCE.cases if c.error is None and not c.stored):
        try:
            _upload(run, blob_store, ws, account.id, _zip(case.transactions), n=2 * n)
            if case.withdrawals is not None:
                _upload(run, blob_store, ws, account.id, _zip(case.withdrawals), n=2 * n + 1)
        except RuleError:
            continue  # a later case may hold a second of an earlier one; dedup is not tested
    dumped = []
    for table in (movement_table, import_table, holding_table, event_table):
        rows = read(ws, lambda c, t=table: c.execute(select(t)).all())
        dumped += [str(tuple(r)) for r in rows]
    dumped += [i.model_dump_json() for i in read(ws, review.inbox)]
    dumped += [m.model_dump_json() for m in read(ws, imports.exchange_movements, account.id)]
    dumped.append(read(ws, imports.exchange_overview, account.id).model_dump_json())
    dumped += [r.getMessage() for r in caplog.records]
    assert len(dumped) > 20
    assert not [s for s in secrets for text in dumped if s in text]
