"""A name rule's checks count only lines of its own amount sign (phase 6 D160, 14 Nov 2026).

A refund („Erstattung“, a credit) from the merchant a person now answers a purchase for must
not block the purchase's rule, nor a purchase a refund rule. Invented names only."""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest

from hausbuch.rules import matching
from hausbuch.rules.errors import RULE_CONFLICTS, RULE_TOO_BROAD, RuleError
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.allocation import (
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
    MatchContext,
    MatchTransaction,
    NameHistory,
    SplitMethod,
)
from hausbuch.schemas.enums import AccountPurpose

PRIVATE = MatchContext(account_purpose=AccountPurpose.PRIVATE)
NAME = "Lieferdienst Beispiel"


def _tx(amount: str, n: int = 1) -> MatchTransaction:
    return MatchTransaction(
        id=UUID(f"0000003c-0000-4000-8000-{n:012d}"),
        booking_date=date(2026, 11, 5),
        amount=Decimal(amount),
        counterparty_name=NAME,
    )


def _answer(tx: MatchTransaction, category: C) -> AllocationSet:
    return AllocationSet(
        transaction_id=tx.id,
        method=AllocationMethod.USER,
        confidence=Decimal("1.000"),
        allocations=[AllocationDraft(amount=tx.amount, category=category)],
    )


def _seen(n: int, category: C, amount: str | None, name: str = NAME) -> NameHistory:
    return NameHistory(
        transaction_id=UUID(f"0000003c-0000-4000-8000-{100 + n:012d}"),
        counterparty_name=name,
        category=category,
        amount=None if amount is None else Decimal(amount),
    )


def test_a_refund_of_the_merchant_does_not_block_a_purchase_rule():
    tx = _tx("-23.40")
    refund = _seen(1, C.HH_REFUND, "12.00")
    rule = matching.infer_rule(tx, _answer(tx, C.HH_DINING), PRIVATE, [refund])
    assert rule is not None
    assert rule.split_method is SplitMethod.SINGLE_CATEGORY and rule.category is C.HH_DINING
    assert rule.matcher.amount_sign == "debit"


def test_a_payment_to_the_name_does_not_block_a_credit_rule():
    # A credit is learned on a learnable income code (hh_refund never is: no threshold).
    tx = _tx("2100.00").model_copy(update={"counterparty_name": "Arbeitgeber Beispiel"})
    paid = _seen(1, C.HH_GIFTS, "-50.00", name="Arbeitgeber Beispiel")
    rule = matching.infer_rule(tx, _answer(tx, C.HH_SALARY), PRIVATE, [paid])
    assert rule is not None and rule.category is C.HH_SALARY
    assert rule.matcher.amount_sign == "credit"


def test_two_kinds_of_the_other_sign_are_not_too_broad():
    tx = _tx("-23.40")
    names = [_seen(1, C.HH_REFUND, "12.00"), _seen(2, C.HH_OTHER_INCOME, "5.00")]
    assert matching.infer_rule(tx, _answer(tx, C.HH_DINING), PRIVATE, names) is not None


@pytest.mark.parametrize(
    ("names", "code"),
    [
        ([("hh_leisure_travel", "-18.90")], RULE_CONFLICTS),
        ([("hh_leisure_travel", "-18.90"), ("hh_groceries", "-7.00")], RULE_TOO_BROAD),
        # without an amount the caller has filtered by sign: the line counts, as before
        ([("hh_leisure_travel", None)], RULE_CONFLICTS),
    ],
)
def test_lines_of_the_same_sign_still_refuse(names, code):
    tx = _tx("-23.40")
    history = [_seen(i, C(c), a) for i, (c, a) in enumerate(names)]
    with pytest.raises(RuleError) as refused:
        matching.infer_rule(tx, _answer(tx, C.HH_DINING), PRIVATE, history)
    assert refused.value.code == code


def test_name_hits_is_the_set_the_checks_use():
    tx = _tx("-23.40")
    own = _seen(0, C.HH_DINING, "-23.40")
    own = own.model_copy(update={"transaction_id": tx.id})  # the line itself
    same = _seen(1, C.HH_LEISURE_TRAVEL, "-18.90", name="LIEFERDIENST BEISPIEL 4711")
    refund = _seen(2, C.HH_REFUND, "12.00")
    other = _seen(3, C.HH_GROCERIES, "-7.00", name="Supermarkt Beispiel")
    zero = _seen(4, C.HH_OTHER, "0.00")
    assert matching.name_hits(tx, [own, same, refund, other, zero]) == [same]
    nameless = tx.model_copy(update={"counterparty_name": "4711"})
    assert matching.name_hits(nameless, [same]) == []
