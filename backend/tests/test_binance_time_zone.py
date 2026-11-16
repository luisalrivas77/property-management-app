"""Phase 6 D178 amended (4), 16 Nov 2026: a Binance export's times are written in the zone the
person chose when exporting, which only the file names name; they are read in that zone and
stored as UTC, while the movement hash keeps the time as the file writes it (so the same export
uploaded again is still recognised). Invented names and rows only."""

from datetime import UTC, datetime, timedelta

import pytest

from hausbuch.rules import exchange as rules
from hausbuch.services.exchange import binance_csv, read_export

HEADER = "User ID,Time,Account,Operation,Coin,Change,Remark\n"
ROWS = (
    "100000001,2026-03-02 14:57:10,Spot,Transaction Buy,BTC,0.0010,\n"
    "100000001,2026-03-02 14:57:10,Spot,Transaction Spend,EUR,-60.00,\n"
    "100000001,2026-03-02 15:04:32,Spot,Withdraw,BTC,-0.0010,Withdraw fee is included\n"
)


@pytest.mark.parametrize(
    ("member", "filename", "minutes"),
    [
        ("Transaction-History-20261116(UTC+1)-part1-of1.csv", None, 60),
        ("Transaction-History-20261116(UTC-5)-part1-of1.csv", None, -300),
        ("Transaction-History-20261116(UTC+5:30)-part1-of1.csv", None, 330),
        ("Transaction-History-20261116(UTC)-part1-of1.csv", None, 0),
        (None, "Binance-Transaction-History-202611161200UTC1_123456.zip", 60),
        (None, "Binance-Transaction-History-202611161200UTC-5_123456.zip", -300),
        (None, "Binance-Transaction-History-202611161200UTC+5:30_123456.zip", 330),
        (None, "C:\\Downloads\\Binance-Withdraw-History-202611161200UTC+2_9.zip", 120),
        # The member's name wins over the upload's.
        ("History(UTC-3).csv", "Binance-Transaction-History-202611161200UTC1_1.zip", -180),
        # A member that names nothing leaves the upload's name to decide.
        ("export.csv", "Binance-Transaction-History-202611161200UTC1_1.zip", 60),
        # UTC alone right after the stamp, before the download's id.
        (None, "Binance-Transaction-History-202611161200UTC_91402f5c.zip", 0),
        (None, "Binance-Transaction-History-202611161200UTC1_100001.zip", 60),
    ],
)
def test_the_zone_is_read_from_the_names(member, filename, minutes):
    assert binance_csv.utc_offset(member, filename) == timedelta(minutes=minutes)


@pytest.mark.parametrize(
    ("member", "filename"),
    [
        (None, None),
        ("export.csv", "export.csv"),
        ("export-202611131200.csv", "binance.zip"),
        (None, "COUTCOME-2026.csv"),  # „UTC“ inside a word is no zone
        (None, "Binance-History-UTC15_1.zip"),  # beyond UTC+14
        (None, "Binance-History-UTC+5:75_1.zip"),  # no such minute
        # Review E (16 Nov 2026): a zone the reader can only read in part names none, never
        # the part it reads (these had been read as UTC+5 or as UTC).
        ("Transaction-History-20261116(UTC+5.5)-part1-of1.csv", None),
        (None, "Binance-Transaction-History-202611161200UTC5-30_123456.zip"),
        (None, "Binance-Transaction-History-202611161200UTC5.5_123456.zip"),
        (None, "Binance-Transaction-History-202611161200UTC5_30_123456.zip"),
        (None, "Binance-Transaction-History-202611161200UTC+5_30_123456.zip"),
        ("Transaction-History-20261116(UTC +1)-part1-of1.csv", None),
        ("Transaction-History-20261116(UTC%2B1)-part1-of1.csv", None),
        (None, "Binance-Transaction-History_UTC_1_.zip"),
    ],
)
def test_no_zone_named(member, filename):
    assert binance_csv.utc_offset(member, filename) is None


def test_a_zip_carries_its_member_name():
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Transaction-History-20261116(UTC+1)-part1-of1.csv", HEADER + ROWS)
    export = read_export(buffer.getvalue())
    assert export.member_name == "Transaction-History-20261116(UTC+1)-part1-of1.csv"
    assert binance_csv.utc_offset(export.member_name, "upload.zip") == timedelta(hours=1)
    plain = read_export((HEADER + ROWS).encode())
    assert plain.member_name is None


def test_times_are_stored_as_utc_and_hashed_as_written():
    offset = timedelta(hours=1)
    utc = rules.binance_movements(binance_csv.parse_transactions_text(HEADER + ROWS), timedelta(0))
    shifted = rules.binance_movements(
        binance_csv.parse_transactions_text(HEADER + ROWS, offset), offset
    )
    assert [m.occurred_at for m in shifted] == [m.occurred_at - offset for m in utc]
    withdraw = next(m for m in shifted if m.quantity < 0 and m.asset == "BTC")
    assert withdraw.occurred_at == datetime(2026, 3, 2, 14, 4, 32, tzinfo=UTC)
    # The hash is built from the time the file writes, so the same file read in either zone,
    # or uploaded again, carries the same hashes.
    assert [m.movement_hash for m in shifted] == [m.movement_hash for m in utc]


def test_the_withdrawal_history_is_read_in_its_zone():
    text = (
        "Time,Coin,Network,Amount,Fee,Address,TXID,Status\n"
        "2026-03-02 15:05:00,BTC,BTC,0.0009,0.0001,bc1qinventedaddress000000000000000000,"
        "inventedtxid0001,Completed\n"
    )
    [row] = binance_csv.parse_withdrawals_text(text, timedelta(hours=1))
    assert row.occurred_at == datetime(2026, 3, 2, 14, 5, tzinfo=UTC)
