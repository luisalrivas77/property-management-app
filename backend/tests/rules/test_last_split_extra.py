"""as_last_split (D51) edge cases beyond rule_history.yaml and repeat_answer.yaml: the rule's
schema, and the copy refusing anything it would have to compute."""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from hausbuch.rules import matching
from hausbuch.schemas.allocation import (
    AllocationCategory as C,
)
from hausbuch.schemas.allocation import (
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
    HistoryLine,
    HistoryRow,
    MatchContext,
    MatchingRule,
    MatchingRuleCreate,
    MatchTransaction,
    SplitMethod,
)
from hausbuch.schemas.enums import AllocationStatus

HV = "8" * 64
PROPERTY = UUID("0000000b-0000-4000-8000-000000000001")
KIND = [C.HAUSGELD_RUECKLAGE, C.HAUSGELD_UMLAGEFAEHIG]
MATCHER = {"counterparty_iban_hmac": HV, "amount_sign": "debit"}


def _rule(**fields: object) -> MatchingRule:
    return MatchingRule.model_validate(
        {
            "id": "0000002b-0000-4000-8000-000000000901",
            "workspace_id": "0000000a-0000-4000-8000-000000000001",
            "created_at": "2026-02-01T10:00:00Z",
            "updated_at": "2026-02-01T10:00:00Z",
            "matcher": MATCHER,
            "split_method": "as_last_split",
            "categories": KIND,
            "recurring": True,
            **fields,
        }
    )


def _tx(amount: str, tx_id: int = 1) -> MatchTransaction:
    return MatchTransaction(
        id=UUID(f"0000002a-0000-4000-8000-{tx_id:012d}"),
        booking_date=date(2026, 4, 2),
        amount=Decimal(amount),
        counterparty_iban_hmac=HV,
        purpose="Hausgeld 04/2026",
    )


def _line(*rows: HistoryRow, amount: str = "-380.00") -> HistoryLine:
    return HistoryLine(
        transaction_id=UUID("0000002a-0000-4000-8000-000000000999"),
        booking_date=date(2026, 3, 2),
        amount=Decimal(amount),
        counterparty_iban_hmac=HV,
        rows=list(rows),
    )


def _row(category: C, amount: str | None) -> HistoryRow:
    return HistoryRow(
        category=category,
        status=AllocationStatus.CONFIRMED,
        amount=None if amount is None else Decimal(amount),
        property_id=PROPERTY,
        recurring=True,
    )


def test_a_history_row_without_its_amount_is_not_copied():
    history = [_line(_row(C.HAUSGELD_UMLAGEFAEHIG, "-260.00"), _row(C.HAUSGELD_RUECKLAGE, None))]
    assert matching.apply_rule(_rule(), _tx("-380.00"), MatchContext(), history) is None


def test_parts_that_do_not_sum_to_the_line_are_not_booked():
    # A confirmed line always sums; the copy still refuses a set that would not.
    history = [
        _line(_row(C.HAUSGELD_UMLAGEFAEHIG, "-260.00"), _row(C.HAUSGELD_RUECKLAGE, "-100.00"))
    ]
    assert matching.apply_rule(_rule(), _tx("-380.00"), MatchContext(), history) is None


def test_the_copy_is_the_latest_lines_parts():
    history = [
        _line(_row(C.HAUSGELD_UMLAGEFAEHIG, "-260.00"), _row(C.HAUSGELD_RUECKLAGE, "-120.00"))
    ]
    found = matching.apply_rule(_rule(), _tx("-380.00"), MatchContext(), history)
    assert found is not None
    assert found.method is AllocationMethod.RULE and found.confidence == matching.EXACT
    assert sorted((a.category, a.amount) for a in found.allocations) == [
        (C.HAUSGELD_RUECKLAGE, Decimal("-120.00")),
        (C.HAUSGELD_UMLAGEFAEHIG, Decimal("-260.00")),
    ]
    assert all(a.document_id is None for a in found.allocations)


@pytest.mark.parametrize(
    "fields",
    [
        {"categories": None},  # as_last_split needs its kind
        {"categories": [C.HAUSGELD_RUECKLAGE]},  # one category is no split
        {"categories": [C.HAUSGELD_UMLAGEFAEHIG, C.HAUSGELD_RUECKLAGE]},  # not sorted
        {"categories": [C.HAUSGELD_RUECKLAGE, C.HAUSGELD_RUECKLAGE]},  # not distinct
        {"split_method": "single_category", "category": "insurance"},  # kind on another method
    ],
)
def test_the_kind_is_exactly_for_as_last_split(fields):
    with pytest.raises(ValidationError):
        MatchingRuleCreate.model_validate(
            {"matcher": MATCHER, "split_method": "as_last_split", "categories": KIND, **fields}
        )


def _answer(tx: MatchTransaction, *parts: tuple[C, str]) -> AllocationSet:
    return AllocationSet(
        transaction_id=tx.id,
        allocations=[
            AllocationDraft(amount=Decimal(a), category=c, property_id=PROPERTY) for c, a in parts
        ],
        method=AllocationMethod.USER,
        confidence=Decimal("1.000"),
    )


def test_several_parts_of_one_category_learn_no_rule():
    tx = _tx("-380.00")
    answer = _answer(tx, (C.REPAIRS, "-300.00"), (C.REPAIRS, "-80.00"))
    assert matching.infer_rule(tx, answer, MatchContext()) is None


def test_a_split_with_a_loan_part_learns_no_rule():
    tx = _tx("-380.00")
    answer = _answer(tx, (C.LOAN_INTEREST, "-80.00"), (C.BANK_FEES, "-300.00"))
    assert matching.infer_rule(tx, answer, MatchContext()) is None


def test_a_split_rule_is_amount_checked():
    assert SplitMethod.AS_LAST_SPLIT in matching.AMOUNT_CHECKED
