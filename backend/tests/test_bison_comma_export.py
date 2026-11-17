"""BISON's export separated by commas (docs/phases/phase-6.md D166 amended (3), 17 Nov 2026):
read exactly as the semicolon export, the hour possibly written with one digit. Invented data."""

from datetime import UTC, datetime
from decimal import Decimal

import fixture_models_phase6 as fixtures
import pytest

from hausbuch.rules import errors as E
from hausbuch.rules.errors import RuleError
from hausbuch.services.exchange import ExportKind, bison_csv, read_export

BISON = fixtures.load("bison_csv")
_READABLE = [c for c in BISON.parse_cases if c.error is None and "zip" not in c.wrap]

_HEADER = (
    "Transaction ID,Transaction type,Currency,Asset,Eur (amount),Asset (amount),"
    "Asset (market price),Fee,Date (UTC - Coordinated Universal Time)"
)


def _commas(text: str) -> str:
    lines = text.split("\n")
    return "\n".join(",".join(c.strip() for c in line.split(";")) for line in lines)


@pytest.mark.parametrize("case", _READABLE, ids=lambda c: c.id)
def test_a_comma_export_reads_as_its_semicolon_twin(case):
    assert bison_csv.parse(_commas(case.text).encode()) == bison_csv.parse(case.text.encode())


def test_a_comma_export_is_recognised_as_bison():
    text = _HEADER + "\nabc1ord,Buy,Eur,Sol,50,0.5,100,0,2026-11-13 9:47:36\n"
    assert read_export(text.encode()).kind is ExportKind.BISON


def test_one_digit_hour_and_both_kinds_of_deposit():
    text = "\n".join(
        [
            _HEADER,
            "dep-1,Deposit,Eur,,250,,,0,2026-11-13 9:45:20",
            "dep-2,Deposit,,Btc,,0.0000123,50000.1,0,2026-08-05 17:38:17",
            "buy-1ord,Buy,Eur,Btc,200,0.004,50000,0,2026-11-13 9:46:51",
            "wd-1wdrc,Withdraw,,Sol,,1.25,70.5,0,2026-11-14 16:28:04",
        ]
    )
    lines = bison_csv.parse(text.encode())
    assert [(x.type, x.asset, x.quantity, x.eur_amount) for x in lines] == [
        ("Deposit", "EUR", Decimal("250"), Decimal("250")),
        ("Deposit", "BTC", Decimal("0.0000123"), None),
        ("Buy", "BTC", Decimal("0.004"), Decimal("200")),
        ("Withdraw", "SOL", Decimal("1.25"), None),
    ]
    assert lines[0].occurred_at == datetime(2026, 11, 13, 9, 45, 20, tzinfo=UTC)


def test_a_comma_inside_a_row_is_refused():
    text = _HEADER + "\nx1ord,Buy,Eur,Sol,1,000,0.5,100,0,2026-11-13 09:47:36\n"
    with pytest.raises(RuleError) as caught:
        bison_csv.parse(text.encode())
    assert caught.value.code == E.EXCHANGE_UNREADABLE
