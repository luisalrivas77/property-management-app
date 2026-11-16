"""Binance's exports (docs/phases/phase-6.md D178; fixture tests/fixtures/phase6/
binance_csv.yaml).

Comma-separated, a UTF-8 BOM, CRLF line ends, no quoting; times YYYY-MM-DD HH:MM:SS in the
time zone the person chose when exporting (D178 amended (4), 16 Nov 2026: this said „in UTC“
and read every time as UTC; the household's exports are UTC+1, so every Binance time was an
hour late). The header names no zone; only the file names do: the CSV inside the ZIP is
named like „Transaction-History-<stamp>(UTC+1)-part1-of1.csv“, the ZIP Binance sends like
„Binance-Transaction-History-<stamp>UTC1_<id>.zip“. `utc_offset(member_name, filename)`
reads the zone from the member name first, else from the uploaded file's own name, and None
when neither names one; the parsers take that offset and return UTC (with no offset found
the import reads the times as UTC, Binance's default, and records the zone as unknown).

- „Transaction History“ (User ID, Time, Account, Operation, Coin, Change, Remark): one row per
  leg, Change signed. Read with csv.reader by column position, so the User ID column is never
  put into a value, a dict or a message (D178 (2)); Account is not read.
- „Withdraw History“ (Time, Coin, Network, Amount, Fee, Address, TXID, Status): the Address
  column is skipped the same way. Only Completed rows are returned; another status completes
  no withdrawal.

Amounts are parsed into Decimal from the text, never through float; an error message names
the row number, never a cell, and no file name reaches a message, an event or a log.
"""

import csv
import io
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from pydantic import ValidationError

from hausbuch.rules import errors as E
from hausbuch.rules.errors import RuleError
from hausbuch.rules.exchange import COMPLETED
from hausbuch.schemas.household import BinanceRow, WithdrawalRecord

from . import (
    BINANCE_TRANSACTIONS_HEADER,
    BINANCE_WITHDRAWALS_HEADER,
    ExportKind,
    header_of,
    read_export,
)

# The zone in a file name (D178 amended (4)): „(UTC+1)“, „(UTC-5)“, „(UTC+5:30)“, „(UTC)“ in
# the member name, „UTC1“, „UTC-5“, „UTC+5:30“ in the ZIP's name. Anchored to the token UTC
# not preceded by a letter, and the hours not followed by another digit, so a stamp's or an
# id's digits are never read as an offset.
_ZONE_IN_PARENS = re.compile(r"\(UTC(?:([+-])(\d{1,2})(?::(\d{2}))?)?\)")
_ZONE_TOKEN = re.compile(r"(?<![A-Za-z(])UTC([+-]?)(\d{1,2})(?::(\d{2}))?(?![\d:])")
# UTC alone only right after the stamp and before the download's id: "…202611161200UTC_<id>".
_ZONE_TOKEN_BARE = re.compile(r"(?<=\d)UTC(?=_)")
# What may not follow a token: another number joined by "." or "-" („UTC5.5“, „UTC5-30“), or
# a short number between underscores („UTC5_30_“), which would be a part of the zone the
# reader cannot place, never the download's id (review E, 16 Nov 2026).
_ZONE_TAIL = re.compile(r"(?:[.\-]\d|_\d{1,2}_)")
_MAX_OFFSET = timedelta(hours=14)


def _offset_of(sign: str | None, hours: str | None, minutes: str | None) -> timedelta | None:
    if hours is None:
        return timedelta(0)
    h, m = int(hours), int(minutes or 0)
    if m >= 60:
        return None
    offset = timedelta(hours=h, minutes=m)
    if offset > _MAX_OFFSET:
        return None
    return -offset if sign == "-" else offset


def _zone_in(name: str | None) -> timedelta | None:
    if not name:
        return None
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    if "(UTC" in name:
        # In parentheses the zone is read whole or not at all: „(UTC+5.5)“, „(UTC +1)“ or
        # „(UTC%2B1)“ name no zone the reader knows, never UTC+5 or UTC.
        found = _ZONE_IN_PARENS.search(name)
        return _offset_of(*found.groups()) if found is not None else None
    found = _ZONE_TOKEN.search(name)
    if found is not None:
        if _ZONE_TAIL.match(name, found.end()):
            return None
        return _offset_of(*found.groups())
    if _ZONE_TOKEN_BARE.search(name) is not None:
        return timedelta(0)
    return None


def utc_offset(member_name: str | None, filename: str | None) -> timedelta | None:
    """D178 amended (4): the export's time zone as its offset from UTC, read from the CSV's
    name inside the ZIP, else from the uploaded file's own name; None when neither names a
    zone (or names one outside UTC−14 to UTC+14)."""
    found = _zone_in(member_name)
    return found if found is not None else _zone_in(filename)


_SIGNED = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")
_UNSIGNED = re.compile(r"^[0-9]+(\.[0-9]+)?$")
_DATE = "%Y-%m-%d %H:%M:%S"


def _rows(text: str, header: tuple[str, ...]) -> list[list[str]]:
    if header_of(text, ",") != header:
        raise RuleError(E.EXCHANGE_UNREADABLE, "not the expected Binance export")
    try:
        rows = [r for r in csv.reader(io.StringIO(text.lstrip("﻿"))) if any(r)]
    except csv.Error:
        raise RuleError(E.EXCHANGE_UNREADABLE, "the CSV is malformed") from None
    for n, row in enumerate(rows[1:], start=1):
        if len(row) != len(header):
            raise RuleError(E.EXCHANGE_UNREADABLE, f"row {n}: {len(row)} cells")
    return rows[1:]


def _time(cell: str, n: int, offset: timedelta) -> datetime:
    """The cell's time, written in the export's zone (UTC + offset), as UTC."""
    try:
        return datetime.strptime(cell.strip(), _DATE).replace(tzinfo=UTC) - offset
    except ValueError:
        raise RuleError(E.EXCHANGE_UNREADABLE, f"row {n}: the time is not readable") from None


def _decimal(cell: str, pattern: re.Pattern[str], n: int) -> Decimal:
    cell = cell.strip()
    if not pattern.match(cell):
        raise RuleError(E.EXCHANGE_UNREADABLE, f"row {n}: an amount is not readable")
    return Decimal(cell)


# Column positions (BINANCE_TRANSACTIONS_HEADER); 0 (User ID) and 2 (Account) are not read.
_T_TIME, _T_OPERATION, _T_COIN, _T_CHANGE, _T_REMARK = 1, 3, 4, 5, 6


def parse_transactions_text(text: str, offset: timedelta = timedelta(0)) -> list[BinanceRow]:
    out = []
    for n, row in enumerate(_rows(text, BINANCE_TRANSACTIONS_HEADER), start=1):
        try:
            out.append(
                BinanceRow(
                    occurred_at=_time(row[_T_TIME], n, offset),
                    operation=row[_T_OPERATION].strip(),
                    coin=row[_T_COIN].strip().upper(),
                    change=_decimal(row[_T_CHANGE], _SIGNED, n),
                    remark=row[_T_REMARK].strip() or None,
                )
            )
        except ValidationError:
            raise RuleError(E.EXCHANGE_UNREADABLE, f"row {n}: a value is out of range") from None
    return out


# Column positions (BINANCE_WITHDRAWALS_HEADER); 5 (Address) is not read.
_W_TIME, _W_COIN, _W_NETWORK, _W_AMOUNT, _W_FEE, _W_TXID, _W_STATUS = 0, 1, 2, 3, 4, 6, 7


def parse_withdrawals_text(text: str, offset: timedelta = timedelta(0)) -> list[WithdrawalRecord]:
    out = []
    for n, row in enumerate(_rows(text, BINANCE_WITHDRAWALS_HEADER), start=1):
        status = row[_W_STATUS].strip()
        if status != COMPLETED:
            continue
        try:
            out.append(
                WithdrawalRecord(
                    occurred_at=_time(row[_W_TIME], n, offset),
                    coin=row[_W_COIN].strip().upper(),
                    network=row[_W_NETWORK].strip(),
                    amount=_decimal(row[_W_AMOUNT], _UNSIGNED, n),
                    fee=_decimal(row[_W_FEE], _UNSIGNED, n),
                    tx_hash=row[_W_TXID].strip(),
                    status=status,
                )
            )
        except ValidationError:
            raise RuleError(E.EXCHANGE_UNREADABLE, f"row {n}: a value is out of range") from None
    return out


def parse_transactions(data: bytes) -> list[BinanceRow]:
    export = read_export(data)
    if export.kind is not ExportKind.BINANCE_TRANSACTIONS:
        raise RuleError(E.EXCHANGE_UNREADABLE, "not Binance's transaction history")
    return parse_transactions_text(export.text)


def parse_withdrawals(data: bytes) -> list[WithdrawalRecord]:
    export = read_export(data)
    if export.kind is not ExportKind.BINANCE_WITHDRAWALS:
        raise RuleError(E.EXCHANGE_UNREADABLE, "not Binance's withdrawal history")
    return parse_withdrawals_text(export.text)
