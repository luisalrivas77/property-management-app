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
    AllocationMethod,
    MatchContext,
    MatchingRule,
    MatchTransaction,
)
from hausbuch.schemas.enums import AccountPurpose

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
