"""Binance's exports (docs/phases/phase-6.md D178; fixture tests/fixtures/phase6/
binance_csv.yaml).

Comma-separated, a UTF-8 BOM, CRLF line ends, no quoting; times YYYY-MM-DD HH:MM:SS in UTC.

- „Transaction History“ (User ID, Time, Account, Operation, Coin, Change, Remark): one row per
  leg, Change signed. Read with csv.reader by column position, so the User ID column is never
  put into a value, a dict or a message (D178 (2)); Account is not read.
- „Withdraw History“ (Time, Coin, Network, Amount, Fee, Address, TXID, Status): the Address
  column is skipped the same way. Only Completed rows are returned; another status completes
  no withdrawal.

Amounts are parsed into Decimal from the text, never through float; an error message names
the row number, never a cell.
"""

import csv
import io
import re
from datetime import UTC, datetime
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


def _time(cell: str, n: int) -> datetime:
    try:
        return datetime.strptime(cell.strip(), _DATE).replace(tzinfo=UTC)
    except ValueError:
        raise RuleError(E.EXCHANGE_UNREADABLE, f"row {n}: the time is not readable") from None


def _decimal(cell: str, pattern: re.Pattern[str], n: int) -> Decimal:
    cell = cell.strip()
    if not pattern.match(cell):
        raise RuleError(E.EXCHANGE_UNREADABLE, f"row {n}: an amount is not readable")
    return Decimal(cell)


# Column positions (BINANCE_TRANSACTIONS_HEADER); 0 (User ID) and 2 (Account) are not read.
_T_TIME, _T_OPERATION, _T_COIN, _T_CHANGE, _T_REMARK = 1, 3, 4, 5, 6


def parse_transactions_text(text: str) -> list[BinanceRow]:
    out = []
    for n, row in enumerate(_rows(text, BINANCE_TRANSACTIONS_HEADER), start=1):
        try:
            out.append(
                BinanceRow(
                    occurred_at=_time(row[_T_TIME], n),
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


def parse_withdrawals_text(text: str) -> list[WithdrawalRecord]:
    out = []
    for n, row in enumerate(_rows(text, BINANCE_WITHDRAWALS_HEADER), start=1):
        status = row[_W_STATUS].strip()
        if status != COMPLETED:
            continue
        try:
            out.append(
                WithdrawalRecord(
                    occurred_at=_time(row[_W_TIME], n),
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
