"""Phase 6 step 5a: the exchanges' parsers and pure rules replay bison_csv.yaml and
binance_csv.yaml (docs/phases/phase-6.md D166 as amended, D175, D178)."""

import io
import zipfile
from decimal import Decimal

import fixture_models_phase6 as fixtures
import pytest

from hausbuch.rules import errors as E
from hausbuch.rules import exchange
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.household import ExchangeMovement, MovementKind
from hausbuch.services.exchange import ExportKind, binance_csv, bison_csv, read_export

BISON = fixtures.load("bison_csv")
BINANCE = fixtures.load("binance_csv")


def _wrapped(text: str, wrap: list[str]) -> bytes:
    if "crlf" in wrap:
        text = text.replace("\n", "\r\n")
    data = text.encode("utf-8")
    if "bom" in wrap:
        data = b"\xef\xbb\xbf" + data
    if "zip" in wrap:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("export-202611131200(UTC+1)-test.csv", data)
        data = buffer.getvalue()
    return data


# --- BISON ---------------------------------------------------------------------------------


@pytest.mark.parametrize("case", BISON.parse_cases, ids=lambda c: c.id)
def test_bison_parse(case):
    data = _wrapped(case.text, case.wrap)
    if case.error is not None:
        with pytest.raises(RuleError) as caught:
            bison_csv.parse(data)
        assert caught.value.code == case.error
        return
    assert bison_csv.parse(data) == case.expect.lines


def _expected_fields(expected, movement: ExchangeMovement) -> dict:
    fields = expected.model_dump(exclude={"row", "position"})
    got = movement.model_dump(include=set(fields))
    return {"expected": fields, "got": got}


@pytest.mark.parametrize("case", BISON.cases, ids=lambda c: c.id)
def test_bison_movements(case):
    movements = exchange.bison_movements(case.rows)
    seen = {exchange.movement_hash("bison", ("id", ref), 0) for ref in case.stored_refs}
    kept, skipped = [], 0
    for index, movement in enumerate(movements):
        if movement.movement_hash in seen:
            skipped += 1
            continue
        seen.add(movement.movement_hash)
        kept.append((index, movement))
    assert (len(kept), skipped) == (case.expect.rows_new, case.expect.rows_skipped)
    assert [i for i, _ in kept] == [m.row for m in case.expect.movements]
    for expected, (_, movement) in zip(case.expect.movements, kept, strict=True):
        pair = _expected_fields(expected, movement)
        assert pair["got"] == pair["expected"]
        if expected.position is not None:
            assert movement.position == expected.position
    if case.typed is not None:
        held = exchange.positions((m for _, m in kept), case.held_before)
        checks = exchange.reconcile(held, case.typed)
        assert [(c.asset, c.reconciled, c.difference_units) for c in checks] == [
            (a.asset, a.reconciled, a.difference_units) for a in case.expect.assets
        ]


def test_bison_hash_is_the_transaction_id():
    line = BISON.cases[0].rows[0]
    [movement] = exchange.bison_movements([line])
    assert movement.movement_hash == exchange.movement_hash("bison", ("id", line.external_ref), 0)
    moved = line.model_copy(update={"quantity": Decimal("1")})
    assert exchange.bison_movements([moved])[0].movement_hash == movement.movement_hash


def test_bison_identical_rows_get_distinct_hashes():
    case = next(c for c in BISON.cases if c.id == "identical_rows_without_id_kept_by_position")
    hashes = [m.movement_hash for m in exchange.bison_movements(case.rows)]
    assert len(set(hashes)) == 2


# --- Binance -------------------------------------------------------------------------------

REAL_WRAP = ["zip", "bom", "crlf"]


def _binance_import(case, wrap: list[str]) -> tuple[list[ExchangeMovement], int, int, int]:
    """Replays what the import does: parse, rows to movements, dedup against an earlier
    export, complete the withdrawals. Returns (new movements, total, skipped, superseded)."""
    rows = binance_csv.parse_transactions(_wrapped(case.transactions, wrap))
    movements = exchange.binance_movements(rows)
    stored = set()
    if case.stored is not None:
        earlier = binance_csv.parse_transactions(_wrapped(case.stored, wrap))
        stored = {m.movement_hash for m in exchange.binance_movements(earlier)}
    new = [m for m in movements if m.movement_hash not in stored]
    superseded = 0
    if case.withdrawals is not None:
        history = binance_csv.parse_withdrawals(_wrapped(case.withdrawals, wrap))
        found = exchange.match_withdrawals(new, history)
        new = [exchange.completed(m, found[i]) if i in found else m for i, m in enumerate(new)]
        superseded = len(found) if case.withdrawals_later else 0
    return new, len(movements), len(movements) - len(new), superseded


@pytest.mark.parametrize("wrap", [[], REAL_WRAP], ids=["plain", "zip_bom_crlf"])
@pytest.mark.parametrize("case", BINANCE.cases, ids=lambda c: c.id)
def test_binance_import(case, wrap):
    if case.error is not None:
        with pytest.raises(RuleError) as caught:
            _binance_import(case, wrap)
        assert caught.value.code == case.error
        return
    new, total, skipped, superseded = _binance_import(case, wrap)
    expect = case.expect
    assert (total, len(new), skipped) == (expect.rows_total, expect.rows_new, expect.rows_skipped)
    assert superseded == expect.superseded
    for expected, movement in zip(expect.movements, new, strict=True):
        fields = expected.model_dump(exclude={"position"})
        assert movement.model_dump(include=set(fields)) == fields
        if expected.position is not None:
            assert movement.position == expected.position
    open_withdrawal = any(
        m.kind is MovementKind.INTERNAL_TRANSFER_OUT and m.tx_hash is None for m in new
    )
    todo = E.EXCHANGE_WITHDRAWAL_HISTORY_MISSING if open_withdrawal else None
    assert todo == expect.todo
    if case.typed is not None:
        checks = exchange.reconcile(exchange.positions(new), case.typed)
        assert [(c.asset, c.reconciled, c.difference_units) for c in checks] == [
            (a.asset, a.reconciled, a.difference_units) for a in expect.assets
        ]


def test_binance_identical_rows_keep_distinct_hashes():
    case = next(c for c in BINANCE.cases if c.id == "identical_rows_kept_by_position")
    new, *_ = _binance_import(case, [])
    assert len({m.movement_hash for m in new}) == 2


def test_binance_hash_ignores_trailing_zeros():
    rows = binance_csv.parse_transactions_text(
        "User ID,Time,Account,Operation,Coin,Change,Remark\n"
        "1,2026-02-01 08:00:00,Spot,Deposit,EUR,500,\n"
    )
    again = binance_csv.parse_transactions_text(
        "User ID,Time,Account,Operation,Coin,Change,Remark\n"
        "1,2026-02-01 08:00:00,Spot,Deposit,EUR,500.00000000,\n"
    )
    assert (
        exchange.binance_movements(rows)[0].movement_hash
        == exchange.binance_movements(again)[0].movement_hash
    )


def test_never_read_user_id_or_address():
    """D178 (2): neither the User ID nor the Address reaches a parsed row or a movement."""
    secrets = (BINANCE.never_stored.user_id, BINANCE.never_stored.address)
    for case in BINANCE.cases:
        if case.error is not None:
            continue
        rows = binance_csv.parse_transactions_text(case.transactions)
        dumped = [r.model_dump_json() for r in rows]
        dumped += [m.model_dump_json() for m in exchange.binance_movements(rows)]
        if case.withdrawals is not None:
            dumped += [
                w.model_dump_json() for w in binance_csv.parse_withdrawals_text(case.withdrawals)
            ]
        assert not any(s in text for s in secrets for text in dumped), case.id


def test_an_error_message_never_repeats_a_cell():
    text = (
        "User ID,Time,Account,Operation,Coin,Change,Remark\n"
        f"{BINANCE.never_stored.user_id},not-a-time,Spot,Deposit,EUR,500,\n"
    )
    with pytest.raises(RuleError) as caught:
        binance_csv.parse_transactions_text(text)
    assert BINANCE.never_stored.user_id not in str(caught.value)


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (
            "Transaction ID; Transaction type; Currency; Asset; Eur (amount); Asset (amount); "
            "Asset (market price); Fee; Date (UTC - Coordinated Universal Time)\n",
            ExportKind.BISON,
        ),
        ("User ID,Time,Account,Operation,Coin,Change,Remark\n", ExportKind.BINANCE_TRANSACTIONS),
        ("Time,Coin,Network,Amount,Fee,Address,TXID,Status\n", ExportKind.BINANCE_WITHDRAWALS),
    ],
)
def test_recognised_by_header(text, kind):
    assert read_export(_wrapped(text, REAL_WRAP)).kind is kind


def test_zip_with_two_members_refused():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.csv", "x")
        archive.writestr("b.csv", "y")
    with pytest.raises(RuleError) as caught:
        read_export(buffer.getvalue())
    assert caught.value.code == E.EXCHANGE_UNREADABLE


def test_withdrawal_takes_the_nearest_history_row_once():
    [w1, w2] = exchange.binance_movements(
        binance_csv.parse_transactions_text(
            "User ID,Time,Account,Operation,Coin,Change,Remark\n"
            "1,2026-03-05 12:30:00,Spot,Withdraw,BTC,-0.0105,Withdraw fee is included\n"
            "1,2026-03-05 12:20:00,Spot,Withdraw,BTC,-0.0105,Withdraw fee is included\n"
        )
    )
    history = binance_csv.parse_withdrawals_text(
        "Time,Coin,Network,Amount,Fee,Address,TXID,Status\n"
        "2026-03-05 12:05:00,BTC,BTC,0.01,0.0005,addr,aaa1,Completed\n"
        "2026-03-05 12:25:00,BTC,BTC,0.01,0.0005,addr,aaa2,Completed\n"
        "2026-03-05 12:26:00,BTC,BTC,0.01,0.0005,addr,aaa3,Failed\n"
    )
    found = exchange.match_withdrawals([w1, w2], history)
    assert {i: r.tx_hash for i, r in found.items()} == {0: "aaa1", 1: "aaa2"}
