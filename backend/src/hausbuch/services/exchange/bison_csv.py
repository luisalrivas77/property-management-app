"""BISON's transaction export (docs/phases/phase-6.md D166 as amended 13 Nov 2026; fixture
tests/fixtures/phase6/bison_csv.yaml).

Nine columns separated by „; “ (every cell after the first starts with a space), no quoting;
one row per trade with both legs; amounts unsigned with a point as decimal mark; the asset
written capitalised (Btc) and read upper-case; the date in UTC. An EUR row (Currency Eur, no
Asset) is the EUR asset, its quantity and eur_amount both its EUR amount. The Fee is in the
units of the row's own asset and kept as fee_quantity; a zero fee is no fee. The market price
is not kept. Parsed into Decimal from the text, never through float.
"""

import re
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import ValidationError

from hausbuch.rules import errors as E
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.household import MovementLine

from . import BISON_HEADER, ExportKind, read_export

_AMOUNT = re.compile(r"^[0-9]+(\.[0-9]+)?$")
_DATE = "%Y-%m-%d %H:%M:%S"
_EUR = "eur"


def _amount(cell: str, n: int) -> Decimal | None:
    if cell == "":
        return None
    if not _AMOUNT.match(cell):
        raise RuleError(E.EXCHANGE_UNREADABLE, f"row {n}: an amount is not readable")
    return Decimal(cell)


def parse_text(text: str) -> list[MovementLine]:
    lines = [line for line in text.replace("\r\n", "\n").split("\n") if line.strip()]
    if not lines or tuple(c.strip() for c in lines[0].split(";")) != BISON_HEADER:
        raise RuleError(E.EXCHANGE_UNREADABLE, "not BISON's transaction export")
    out = []
    for n, line in enumerate(lines[1:], start=1):
        cells = [c.strip() for c in line.split(";")]
        if len(cells) != len(BISON_HEADER):
            raise RuleError(E.EXCHANGE_UNREADABLE, f"row {n}: {len(cells)} cells, not 9")
        ref, kind, currency, asset, eur, units, _price, fee, when = cells
        try:
            occurred_at = datetime.strptime(when, _DATE).replace(tzinfo=UTC)
        except ValueError:
            raise RuleError(E.EXCHANGE_UNREADABLE, f"row {n}: the date is not readable") from None
        eur_amount, quantity = _amount(eur, n), _amount(units, n)
        fee_units = _amount(fee, n)
        if asset:
            symbol = asset.upper()
        elif currency.casefold() == _EUR:
            symbol, quantity = "EUR", eur_amount
        else:
            raise RuleError(E.EXCHANGE_UNREADABLE, f"row {n}: neither an asset nor EUR")
        if quantity is None or not kind:
            raise RuleError(E.EXCHANGE_UNREADABLE, f"row {n}: no type or no amount")
        try:
            out.append(
                MovementLine(
                    occurred_at=occurred_at,
                    type=kind,
                    asset=symbol,
                    quantity=quantity,
                    eur_amount=eur_amount,
                    fee_quantity=fee_units if fee_units else None,
                    external_ref=ref or None,
                )
            )
        except ValidationError:
            raise RuleError(E.EXCHANGE_UNREADABLE, f"row {n}: a value is out of range") from None
    return out


def parse(data: bytes) -> list[MovementLine]:
    """The upload's bytes (the CSV, or a ZIP with it) to BISON's rows."""
    export = read_export(data)
    if export.kind is not ExportKind.BISON:
        raise RuleError(E.EXCHANGE_UNREADABLE, "not BISON's transaction export")
    return parse_text(export.text)
