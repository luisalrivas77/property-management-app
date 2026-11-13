"""The exchanges' exports (docs/phases/phase-6.md D166 as amended 13 Nov 2026, D178).

`read_export(data)` takes what the upload brings, a CSV or a ZIP with the one CSV inside,
drops a UTF-8 BOM, and recognises the file by its header line: BISON's transaction history,
Binance's transaction history, Binance's withdrawal history, or Binance's spot trade history
(refused with exchange.binance_spot_history, since it adds nothing, D178 (1)). Anything else
is exchange.unreadable. No message here repeats a cell or a member name: a Binance file
carries the account's User ID and the withdrawal addresses (D178 (2)).
"""

import io
import lzma
import zipfile
import zlib
from dataclasses import dataclass
from enum import StrEnum

from hausbuch.rules import errors as E
from hausbuch.rules.errors import RuleError

_ZIP_MAGIC = b"PK\x03\x04"
MAX_EXPORT_BYTES = 20 * 1024 * 1024  # uncompressed; the real exports are a few kB

BISON_HEADER = (
    "Transaction ID",
    "Transaction type",
    "Currency",
    "Asset",
    "Eur (amount)",
    "Asset (amount)",
    "Asset (market price)",
    "Fee",
    "Date (UTC - Coordinated Universal Time)",
)
BINANCE_TRANSACTIONS_HEADER = (
    "User ID",
    "Time",
    "Account",
    "Operation",
    "Coin",
    "Change",
    "Remark",
)
BINANCE_WITHDRAWALS_HEADER = (
    "Time",
    "Coin",
    "Network",
    "Amount",
    "Fee",
    "Address",
    "TXID",
    "Status",
)
BINANCE_SPOT_HEADER_START = ("Order No", "Time", "Pair", "Side")


class ExportKind(StrEnum):
    BISON = "bison"
    BINANCE_TRANSACTIONS = "binance_transactions"
    BINANCE_WITHDRAWALS = "binance_withdrawals"


@dataclass(frozen=True)
class ExchangeExport:
    kind: ExportKind
    text: str  # the CSV's text, BOM dropped, line ends as written


def _unzip(data: bytes) -> bytes:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError):
        raise RuleError(E.EXCHANGE_UNREADABLE, "not a readable ZIP archive") from None
    with archive:
        infos = [i for i in archive.infolist() if not i.is_dir()]
        if len(infos) != 1:
            raise RuleError(E.EXCHANGE_UNREADABLE, "the ZIP must hold exactly one CSV")
        info = infos[0]
        if info.flag_bits & 0x1 or not info.filename.lower().endswith(".csv"):
            raise RuleError(E.EXCHANGE_UNREADABLE, "the ZIP must hold one readable CSV")
        if info.file_size > MAX_EXPORT_BYTES:
            raise RuleError(E.EXCHANGE_UNREADABLE, "the CSV is too large")
        try:
            with archive.open(info) as handle:
                content = handle.read(MAX_EXPORT_BYTES + 1)
        except (
            zipfile.BadZipFile,
            NotImplementedError,
            RuntimeError,
            OSError,
            EOFError,
            zlib.error,
            lzma.LZMAError,
        ):
            raise RuleError(E.EXCHANGE_UNREADABLE, "the CSV could not be read") from None
    if len(content) > MAX_EXPORT_BYTES:
        raise RuleError(E.EXCHANGE_UNREADABLE, "the CSV is too large")
    return content


def _decode(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise RuleError(E.EXCHANGE_UNREADABLE, "the file is not UTF-8 text") from None


def header_of(text: str, delimiter: str) -> tuple[str, ...]:
    first = text.lstrip("﻿").split("\n", 1)[0].rstrip("\r")
    return tuple(cell.strip() for cell in first.split(delimiter))


def sniff(text: str) -> ExportKind:
    if header_of(text, ";") == BISON_HEADER:
        return ExportKind.BISON
    header = header_of(text, ",")
    if header == BINANCE_TRANSACTIONS_HEADER:
        return ExportKind.BINANCE_TRANSACTIONS
    if header == BINANCE_WITHDRAWALS_HEADER:
        return ExportKind.BINANCE_WITHDRAWALS
    if header[: len(BINANCE_SPOT_HEADER_START)] == BINANCE_SPOT_HEADER_START:
        raise RuleError(
            E.EXCHANGE_BINANCE_SPOT_HISTORY,
            "the spot trade history is not needed: upload the transaction history and the "
            "withdrawal history",
        )
    raise RuleError(E.EXCHANGE_UNREADABLE, "not an export of BISON or Binance")


def read_export(data: bytes) -> ExchangeExport:
    if len(data) > MAX_EXPORT_BYTES:
        raise RuleError(E.EXCHANGE_UNREADABLE, "the file is too large")
    raw = _unzip(data) if data.startswith(_ZIP_MAGIC) else data
    text = _decode(raw)
    return ExchangeExport(kind=sniff(text), text=text)


def is_exchange_export(data: bytes) -> bool:
    """D166: whether the bytes are one of the exchanges' exports (BISON's, or one of
    Binance's three, the spot trade history included), by the header line only. Such a file
    is never classified or sent to the model (it holds Binance's User ID and the withdrawal
    addresses); it is read by code on its exchange account. Anything this cannot read as an
    export is False."""
    try:
        read_export(data)
    except RuleError as exc:
        return exc.code == E.EXCHANGE_BINANCE_SPOT_HISTORY
    return True
