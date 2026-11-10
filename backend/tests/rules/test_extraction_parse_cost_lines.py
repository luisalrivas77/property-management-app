"""A bill's cost lines (phase 2 D135): parsed `text | amount | code`, kept only when they sum
to the total (rules.extraction_parse.derive_fields). Synthetic amounts only."""

from decimal import Decimal

import pytest

from hausbuch.rules.extraction_parse import (
    cost_lines,
    cost_lines_balance,
    derive_fields,
    parse_lines,
)
from hausbuch.schemas.ai import CitedTextBlock, ExtractedValue, SourceRef
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.enums import ExtractionKind

ITEMS = [
    "Beurkundung Kaufvertrag | 600,00 € | acquisition_cost",
    "Grundschuldbestellung | 300,00 € | loan_fees",
    "Umsatzsteuer 19 % | 171,00 € | vat",
]


def _read(value, quote="Rechnung"):
    return ExtractedValue(
        value=value,
        source=SourceRef(page=1, quote=quote),
        checks=["cited", "validates"],
        confidence="medium",
    )


def test_items_parse_into_text_amount_and_category():
    lines = cost_lines(ITEMS)
    assert [line.text for line in lines] == [
        "Beurkundung Kaufvertrag",
        "Grundschuldbestellung",
        "Umsatzsteuer 19 %",
    ]
    assert [line.amount for line in lines] == [
        Decimal("600.00"),
        Decimal("300.00"),
        Decimal("171.00"),
    ]
    assert [line.category for line in lines] == [C.ACQUISITION_COST, C.LOAN_FEES, None]
    assert [line.vat for line in lines] == [False, False, True]


def test_a_vat_line_is_known_by_its_text_too():
    (line,) = cost_lines(["19 % USt. auf 900,00 € | 171,00 € | other_expense"])
    assert line.vat and line.category is None


def test_an_unknown_code_leaves_the_line_without_a_category():
    (line,) = cost_lines(["Gebühr | 10,00 € | rent_cold"])
    assert line.category is None and not line.vat


@pytest.mark.parametrize(
    "items",
    [
        None,
        [],
        "Gebühr | 10,00 € | repairs",
        ["Gebühr 10,00 € repairs"],
        ["Gebühr | zehn Euro | repairs"],
        ["| 10,00 € | repairs"],
        ["Gebühr | 10,00 € | repairs | extra"],
    ],
)
def test_a_malformed_list_is_none(items):
    assert cost_lines(items) is None


def test_balance_within_a_cent():
    lines = cost_lines(ITEMS)
    assert cost_lines_balance(lines, "1071.00")
    assert cost_lines_balance(lines, "1071.01")
    assert not cost_lines_balance(lines, "1071.02")
    assert not cost_lines_balance(lines, None)


@pytest.mark.parametrize("kind", [ExtractionKind.INVOICE, ExtractionKind.BESCHEID])
def test_derive_keeps_validates_when_the_lines_sum_to_the_total(kind):
    values = {"amount_total": _read("1071.00"), "cost_lines": _read(ITEMS)}
    got = derive_fields(kind, values)["cost_lines"]
    assert got == values["cost_lines"]


@pytest.mark.parametrize("total", ["1100.00", None])
def test_derive_drops_validates_when_the_lines_do_not_sum(total):
    total_value = _read(total) if total else ExtractedValue.missing()
    values = {"amount_total": total_value, "cost_lines": _read(ITEMS)}
    got = derive_fields(ExtractionKind.INVOICE, values)["cost_lines"]
    assert got.value == ITEMS
    assert got.checks == ["cited"]
    assert got.confidence == "low"


def test_derive_drops_validates_when_a_line_does_not_parse():
    values = {"amount_total": _read("10.00"), "cost_lines": _read(["Gebühr 10,00 €"])}
    got = derive_fields(ExtractionKind.INVOICE, values)["cost_lines"]
    assert "validates" not in got.checks and got.confidence == "low"


def test_derive_without_cost_lines_changes_nothing():
    values = {"amount_total": _read("10.00")}
    assert derive_fields(ExtractionKind.INVOICE, values) == values


def test_the_reading_parses_a_cost_lines_line():
    text = (
        "amount_total: 1.071,00 €\n"
        "cost_lines: Beurkundung Kaufvertrag | 600,00 € | acquisition_cost; "
        "Grundschuldbestellung | 300,00 € | loan_fees; Umsatzsteuer 19 % | 171,00 € | vat\n"
    )
    values = parse_lines([CitedTextBlock(text=text, citations=[])], ExtractionKind.INVOICE)
    got = derive_fields(ExtractionKind.INVOICE, values)
    assert got["cost_lines"].value == ITEMS
    assert "validates" in got["cost_lines"].checks
