"""„Differenz als Korrektur buchen“ under hausbuch_app and RLS (docs/phases/phase-6.md D178
amended 13 Nov 2026, migration 0030): one coin's difference of a typed balance booked by a
person with a reason, then the import checked again. BISON's invented export of
bison_csv.yaml (EUR 250, BTC 0.00009965 after it); every other figure invented."""

from datetime import timedelta
from decimal import Decimal

import pytest
from db.test_services_exchange_import import (
    REAL_LAYOUT,
    T0,
    _cards,
    _exchange,
    _upload,
)
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import event as event_table
from hausbuch.rules import errors as E
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.enums import ImportStatus
from hausbuch.schemas.household import (
    BalanceCorrectionBody,
    DeclareHoldingsBody,
    MovementKind,
    MovementSource,
    MovementStatus,
)
from hausbuch.services.errors import NotFoundError, RuleViolationError
from hausbuch.services.exchange import imports

pytestmark = pytest.mark.db

REASON = "Gutschrift fehlt im Export"


def _imported(run, blob_store, ws):
    account = _exchange(run, ws)
    imported = _upload(run, blob_store, ws, account.id, REAL_LAYOUT.encode()).movement_import
    return account, imported


def _ids(read, ws, account_id):
    overview = read(ws, imports.exchange_overview, account_id)
    return {p.symbol: p.asset_id for p in overview.positions}


def _type(run, ws, import_id, ids, eur="250.00", btc="0.00009965", n=1):
    return run(
        ws,
        imports.declare_holdings,
        import_id,
        DeclareHoldingsBody(
            balances=[
                {"asset_id": ids["EUR"], "units": eur},
                {"asset_id": ids["BTC"], "units": btc},
            ]
        ),
        now=T0 + timedelta(minutes=n),
    )


def _correct(run, ws, import_id, asset_id, reason=REASON, n=10):
    return run(
        ws,
        imports.book_correction,
        import_id,
        BalanceCorrectionBody(asset_id=asset_id, reason=reason),
        now=T0 + timedelta(minutes=n),
    )


def _corrections(read, ws):
    return read(
        ws,
        lambda c: c.execute(
            select(movement_table)
            .where(movement_table.c.kind == MovementKind.BALANCE_CORRECTION.value)
            .order_by(movement_table.c.created_at)
        ).all(),
    )


def _events(read, ws, type_):
    return read(
        ws,
        lambda c: c.execute(
            select(event_table.c.entity_id, event_table.c.payload)
            .where(event_table.c.type == type_)
            .order_by(event_table.c.recorded_at)
        ).all(),
    )


def test_a_correction_reconciles_a_one_coin_gap(run, read, blob_store, fresh_workspaces):
    ws, _ = fresh_workspaces
    account, imported = _imported(run, blob_store, ws)
    ids = _ids(read, ws, account.id)
    # The exchange shows 0.0001 BTC more than the export adds up to: a credit is missing.
    wrong = _type(run, ws, imported.id, ids, btc="0.00019965")
    assert wrong.status is ImportStatus.UNRECONCILED
    assert _cards(read, ws, InboxItemKind.EXCHANGE_UNRECONCILED)

    checked = _correct(run, ws, imported.id, ids["BTC"], reason=f"  {REASON}  ")
    assert (checked.status, checked.error_code) == (ImportStatus.RECONCILED, None)
    assert all(b.reconciled for b in checked.balances)
    [btc] = [b for b in checked.balances if b.symbol == "BTC"]
    assert (btc.held, btc.typed, btc.difference_units) == (
        Decimal("0.00019965"),
        Decimal("0.00019965"),
        Decimal("0"),
    )
    [row] = _corrections(read, ws)
    assert row.quantity == Decimal("0.0001")  # the stored difference, never typed
    assert (row.asset_id, row.account_id, row.occurred_at) == (
        ids["BTC"],
        account.id,
        imported.last_at,
    )
    assert (row.source, row.status, row.note) == (
        MovementSource.USER.value,
        MovementStatus.CONFIRMED.value,
        REASON,  # stripped
    )
    assert (row.eur_amount, row.document_id, row.movement_hash) == (None, None, None)
    assert _cards(read, ws, InboxItemKind.EXCHANGE_UNRECONCILED) == []

    # In the account's history with its reason; the position counts it, EUR unchanged.
    movements = read(ws, imports.exchange_movements, account.id)
    [listed] = [m for m in movements if m.kind is MovementKind.BALANCE_CORRECTION]
    assert (listed.note, listed.symbol, listed.answers) == (REASON, "BTC", [])
    overview = read(ws, imports.exchange_overview, account.id)
    assert {p.symbol: p.units for p in overview.positions} == {
        "EUR": Decimal("250.00"),
        "BTC": Decimal("0.00019965"),
    }

    # The events: the movement confirmed, the account's correction (ids only), the re-check.
    [(entity, payload)] = _events(read, ws, "account.balance_corrected")
    assert entity == account.id
    assert payload == {
        "import_id": str(imported.id),
        "movement_id": str(row.id),
        "asset_id": str(ids["BTC"]),
    }
    confirmed = _events(read, ws, "movement.confirmed")
    assert [p["kind"] for e, p in confirmed if e == row.id] == ["balance_correction"]
    assert REASON not in str(_events(read, ws, "account.balance_corrected"))
    assert len(_events(read, ws, "account.movements_reconciled")) == 1

    # Booked once: the import agrees now, so a second correction is refused.
    with pytest.raises(RuleViolationError) as caught:
        _correct(run, ws, imported.id, ids["BTC"], n=11)
    assert caught.value.codes == (E.EXCHANGE_CORRECTION_NOT_UNRECONCILED,)

    # The balance retyped differently: the new difference may be booked, once more.
    again = _type(run, ws, imported.id, ids, btc="0.00029965", n=12)
    assert again.status is ImportStatus.UNRECONCILED
    second = _correct(run, ws, imported.id, ids["BTC"], reason="Zweite Gutschrift fehlt", n=13)
    assert second.status is ImportStatus.RECONCILED
    assert [r.quantity for r in _corrections(read, ws)] == [Decimal("0.0001"), Decimal("0.0001")]


def test_a_correction_may_lower_a_coin(run, read, blob_store, fresh_workspaces):
    ws, _ = fresh_workspaces
    account, imported = _imported(run, blob_store, ws)
    ids = _ids(read, ws, account.id)
    _type(run, ws, imported.id, ids, btc="0.00009")
    checked = _correct(run, ws, imported.id, ids["BTC"])
    assert checked.status is ImportStatus.RECONCILED
    assert [r.quantity for r in _corrections(read, ws)] == [Decimal("-0.00000965")]


def test_eur_and_an_agreeing_coin_are_refused(run, read, blob_store, fresh_workspaces):
    ws, _ = fresh_workspaces
    account, imported = _imported(run, blob_store, ws)
    ids = _ids(read, ws, account.id)
    # Typed but not yet differing: the import is not unreconciled.
    _type(run, ws, imported.id, ids)
    with pytest.raises(RuleViolationError) as caught:
        _correct(run, ws, imported.id, ids["BTC"])
    assert caught.value.codes == (E.EXCHANGE_CORRECTION_NOT_UNRECONCILED,)

    # EUR differs (beyond the rounding), BTC agrees.
    _type(run, ws, imported.id, ids, eur="251.00", n=2)
    with pytest.raises(RuleViolationError) as caught:
        _correct(run, ws, imported.id, ids["EUR"])
    assert caught.value.codes == (E.EXCHANGE_CORRECTION_EUR,)
    with pytest.raises(RuleViolationError) as caught:
        _correct(run, ws, imported.id, ids["BTC"])
    assert caught.value.codes == (E.EXCHANGE_CORRECTION_NO_DIFFERENCE,)
    assert _corrections(read, ws) == []


def test_a_reason_is_required():
    asset = "00000000-0000-4000-8000-000000000001"
    for reason in ("", "  ", " ab ", "x" * 501):
        with pytest.raises(ValidationError):
            BalanceCorrectionBody(asset_id=asset, reason=reason)
    with pytest.raises(ValidationError):
        BalanceCorrectionBody(asset_id=asset)
    assert BalanceCorrectionBody(asset_id=asset, reason=" abc ").reason == "abc"


def test_a_stale_difference_and_a_later_import_are_refused(run, read, blob_store, fresh_workspaces):
    ws, _ = fresh_workspaces
    account, imported = _imported(run, blob_store, ws)
    ids = _ids(read, ws, account.id)
    _type(run, ws, imported.id, ids, btc="0.0002")
    # A later, longer export: a correction dated at the first import's end would change
    # the later import's sum unseen.
    longer = REAL_LAYOUT + (
        "9a1c3e5b-7d2f-4b60-8e14-3c5a7b9d1f02; Buy; Eur; Btc; 100.00; 0.00160000; "
        "62500.0000000000; 0; 2026-04-01 08:00:00\n"
    )
    _upload(run, blob_store, ws, account.id, longer.encode(), n=2)
    with pytest.raises(RuleViolationError) as caught:
        _correct(run, ws, imported.id, ids["BTC"])
    assert caught.value.codes == (E.EXCHANGE_CORRECTION_NOT_LATEST,)

    # The movements changed after the typing (a row inserted within the import's period):
    # the stored difference is no longer the one to book.
    ws2 = fresh_workspaces[1]
    account2, imported2 = _imported(run, blob_store, ws2)
    ids2 = _ids(read, ws2, account2.id)
    _type(run, ws2, imported2.id, ids2, btc="0.0002")

    def _insert_row(ctx):
        ctx.conn.execute(
            movement_table.insert().values(
                workspace_id=ctx.workspace_id,
                account_id=account2.id,
                asset_id=ids2["BTC"],
                kind=MovementKind.STAKING_REWARD.value,
                quantity=Decimal("0.00000035"),
                occurred_at=imported2.first_at,
                source=MovementSource.USER.value,
                status=MovementStatus.CONFIRMED.value,
                created_at=ctx.now,
            )
        )

    run(ws2, _insert_row)
    with pytest.raises(RuleViolationError) as caught:
        _correct(run, ws2, imported2.id, ids2["BTC"])
    assert caught.value.codes == (E.EXCHANGE_CORRECTION_STALE,)
    assert _corrections(read, ws2) == []


def test_a_correction_is_append_only(run, read, blob_store, fresh_workspaces):
    ws, _ = fresh_workspaces
    account, imported = _imported(run, blob_store, ws)
    ids = _ids(read, ws, account.id)
    _type(run, ws, imported.id, ids, btc="0.0002")
    _correct(run, ws, imported.id, ids["BTC"])
    [row] = _corrections(read, ws)
    for change in ({"note": "anders"}, {"quantity": Decimal("1")}):
        with pytest.raises(DBAPIError):
            run(
                ws,
                lambda ctx, change=change: ctx.conn.execute(
                    update(movement_table).where(movement_table.c.id == row.id).values(**change)
                ),
            )
    with pytest.raises(DBAPIError):
        run(
            ws,
            lambda ctx: ctx.conn.execute(
                movement_table.delete().where(movement_table.c.id == row.id)
            ),
        )
    assert _corrections(read, ws)[0].note == REASON


def test_another_workspace_cannot_book_a_correction(run, read, blob_store, fresh_workspaces):
    ws, other = fresh_workspaces
    account, imported = _imported(run, blob_store, ws)
    ids = _ids(read, ws, account.id)
    _type(run, ws, imported.id, ids, btc="0.0002")
    with pytest.raises(NotFoundError):
        _correct(run, other, imported.id, ids["BTC"])
    assert _corrections(read, ws) == []
