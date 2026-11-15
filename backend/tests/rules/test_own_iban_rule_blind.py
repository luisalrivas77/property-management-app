"""D160 amended (5), 15 Nov 2026: on a private account a line naming another own account's
IBAN is the transfer matcher's, never a learned rule's; the rental order is unchanged.
Invented hashes and names only."""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest

from hausbuch.rules import matching
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.allocation import (
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
    MatchContext,
    MatchingRule,
    MatchTransaction,
)
from hausbuch.schemas.enums import AccountPurpose
from hausbuch.services import rules_learned

OWN = "a" * 64  # an own card's payment IBAN
ISSUER = "b" * 64  # the same issuer's IBAN for a card not in the app
NAME = "Beispiel Kartenbank AB"
PRIVATE = MatchContext(account_purpose=AccountPurpose.PRIVATE, own_account_hmacs=[OWN])
RENTAL = MatchContext(account_purpose=AccountPurpose.RENTAL, own_account_hmacs=[OWN])


def _tx(iban: str | None, amount: str = "-189.76") -> MatchTransaction:
    return MatchTransaction(
        id=UUID("0000003d-0000-4000-8000-000000000001"),
        booking_date=date(2026, 10, 5),
        amount=Decimal(amount),
        counterparty_name=NAME,
        counterparty_iban_hmac=iban,
    )


def _rule(matcher: dict, category: C = C.HH_CREDIT_CARD) -> MatchingRule:
    return MatchingRule.model_validate(
        {
            "id": "0000002b-0000-4000-8000-000000000d01",
            "workspace_id": "0000000a-0000-4000-8000-000000000001",
            "created_at": "2026-02-01T10:00:00Z",
            "updated_at": "2026-02-01T10:00:00Z",
            "matcher": matcher,
            "split_method": "single_category",
            "category": category,
        }
    )


NAME_RULE = _rule({"counterparty_name_pattern": "^BEISPIEL KARTENBANK", "amount_sign": "debit"})
OWN_IBAN_RULE = _rule({"counterparty_iban_hmac": OWN, "amount_sign": "debit"})


@pytest.mark.parametrize("rule", [NAME_RULE, OWN_IBAN_RULE], ids=["name", "iban"])
def test_a_private_line_to_an_own_iban_is_the_transfer_whatever_rule_hits(rule):
    tx = _tx(OWN)
    assert matching.own_rule_blind(tx, PRIVATE)
    found = matching.deterministic_answer([rule], tx, PRIVATE)
    assert found is not None
    assert found.method is AllocationMethod.MATCHER_TRANSFER
    assert [a.category for a in found.allocations] == [C.INTERNAL_TRANSFER_OUT]


def test_the_same_name_on_the_issuers_other_iban_is_the_rules():
    tx = _tx(ISSUER)
    assert not matching.own_rule_blind(tx, PRIVATE)
    found = matching.deterministic_answer([NAME_RULE], tx, PRIVATE)
    assert found is not None and found.method is AllocationMethod.RULE
    assert [a.category for a in found.allocations] == [C.HH_CREDIT_CARD]


def test_a_zero_line_to_an_own_iban_gets_no_rule_either():
    # match_transfer_line books no 0 € line; the rule still never books it.
    tx = _tx(OWN, "0.00")
    assert matching.deterministic_answer([NAME_RULE], tx, PRIVATE) is None


def test_the_rental_order_is_unchanged():
    # Rental: the learned rules still come before the owner transfer (D158, phase 2).
    rule = _rule({"counterparty_iban_hmac": OWN, "amount_sign": "debit"}, C.BANK_FEES)
    tx = _tx(OWN, "-5.00")
    assert not matching.own_rule_blind(tx, RENTAL)
    found = matching.deterministic_answer([rule], tx, RENTAL)
    assert found is not None and found.method is AllocationMethod.RULE


# --- D160 amended (5), review fixes (15 Nov 2026): no rule from a line naming an own IBAN ----


def _answer(tx: MatchTransaction, category: C) -> AllocationSet:
    return AllocationSet(
        transaction_id=tx.id,
        allocations=[AllocationDraft(amount=tx.amount, category=category)],
        method=AllocationMethod.USER,
        confidence=Decimal("1"),
    )


@pytest.mark.parametrize(
    ("name_pattern", "purpose_pattern"),
    [(None, None), ("^ANDERE FIRMA", None), (None, "^Rate")],
    ids=["inferred", "typed_name", "typed_purpose"],
)
def test_a_private_line_naming_an_own_iban_learns_no_rule(name_pattern, purpose_pattern):
    """An own IBAN is always a transfer: an answer for such a line (a second payee paid to
    the card's payment IBAN, re-booked by hand) learns no rule, typed pattern or not."""
    tx = _tx(OWN)
    answer = _answer(tx, C.HH_GROCERIES)
    assert matching.infer_rule(tx, answer, PRIVATE) is None
    assert rules_learned.proposed_rule_or_refusal(
        tx, answer, PRIVATE, name_pattern=name_pattern, purpose_pattern=purpose_pattern
    ) == (None, rules_learned.NOT_LEARNABLE)


def test_the_same_payee_on_another_iban_still_learns_its_rule():
    tx = _tx(ISSUER)
    rule, refusal = rules_learned.proposed_rule_or_refusal(
        tx, _answer(tx, C.HH_CREDIT_CARD), PRIVATE
    )
    assert refusal is None
    assert rule is not None and rule.matcher.counterparty_iban_hmac == ISSUER


def test_a_rental_line_naming_an_own_iban_still_learns_as_before():
    """Rental lines are unchanged: an owner transfer answer becomes the owner_transfer rule,
    and a typed pattern on another answer is not refused for the IBAN."""
    tx = _tx(OWN, "-500.00")
    owner = matching.infer_rule(tx, _answer(tx, C.OWNER_TRANSFER_OUT), RENTAL)
    assert owner is not None and owner.split_method.value == "owner_transfer"
    typed, refusal = rules_learned.proposed_rule_or_refusal(
        tx, _answer(tx, C.BANK_FEES), RENTAL, purpose_pattern="^Entgelt"
    )
    assert refusal is None and typed is not None
