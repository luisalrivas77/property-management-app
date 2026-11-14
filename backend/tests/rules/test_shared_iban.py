"""A shared counterparty IBAN on a private account (phase 6 D160 amended 13 Nov 2026).

DKB's CSV export puts one clearing IBAN on every card payment, so one IBAN hash carries many
payees. The cases are inline here, fixture-style, until the spec PR moves them into a YAML
fixture. Invented names only."""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest

from hausbuch.rules import matching
from hausbuch.rules.errors import RULE_CONFLICTS, RuleError
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.allocation import (
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
    MatchContext,
    MatchingRule,
    MatchTransaction,
    NameHistory,
    RuleMatcher,
    SplitMethod,
)
from hausbuch.schemas.enums import AccountPurpose

CLEARING = "c" * 64
SINGLE = "5" * 64
PRIVATE = MatchContext(account_purpose=AccountPurpose.PRIVATE)
SHARED = MatchContext(account_purpose=AccountPurpose.PRIVATE, shared_iban_hmacs=[CLEARING])


def _tx(name: str | None, amount: str = "-23.40", iban: str | None = CLEARING, n: int = 1):
    return MatchTransaction(
        id=UUID(f"0000003a-0000-4000-8000-{n:012d}"),
        booking_date=date(2026, 10, 5),
        amount=Decimal(amount),
        counterparty_name=name,
        counterparty_iban_hmac=iban,
    )


def _answer(tx: MatchTransaction, category: C) -> AllocationSet:
    return AllocationSet(
        transaction_id=tx.id,
        method=AllocationMethod.USER,
        confidence=Decimal("1.000"),
        allocations=[AllocationDraft(amount=tx.amount, category=category)],
    )


def _rule(matcher: dict, category: C = C.HH_DINING) -> MatchingRule:
    return MatchingRule.model_validate(
        {
            "id": "0000002b-0000-4000-8000-000000000911",
            "workspace_id": "0000000a-0000-4000-8000-000000000001",
            "created_at": "2026-02-01T10:00:00Z",
            "updated_at": "2026-02-01T10:00:00Z",
            "matcher": matcher,
            "split_method": "single_category",
            "category": category,
        }
    )


# --- The predicate ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("names", "shared"),
    [
        (["Lieferdienst Beispiel", "Supermarkt Beispiel", "Drogerie Beispiel"], True),
        (["Lieferdienst Beispiel", "Supermarkt Beispiel"], True),  # two payees: shared
        (["Lieferdienst Beispiel"], False),  # one payee
        # one payee, spelled three ways: case, spacing, dots and digits are one payee
        (["Lieferdienst Beispiel", "LIEFERDIENST  BEISPIEL", "Lieferdienst.Beispiel 42"], False),
        # a branch's //<Ort>/<Land> token is no other payee
        (["Bäckerei Muster//Stadt A/DE", "Bäckerei Muster//Stadt B/DE", "Bäckerei Muster"], False),
        ([None, "", "Lieferdienst Beispiel"], False),  # no name, no payee
        ([], False),
    ],
)
def test_an_iban_is_shared_from_two_distinct_payee_names(names, shared):
    assert matching.SHARED_IBAN_MIN_NAMES == 2
    assert matching.shared_iban(names) is shared


def test_same_payee_is_what_the_payees_name_rule_hits():
    assert matching.same_payee("LIEFERDIENST BEISPIEL", "Lieferdienst Beispiel GmbH")
    assert not matching.same_payee("LIEFERDIENST BEISPIEL", "Supermarkt Beispiel")
    assert not matching.same_payee(None, "Lieferdienst Beispiel")
    assert not matching.same_payee("123", "123")  # digits only: nothing to match on


def test_iban_only_means_no_pattern_narrows_it():
    assert matching.iban_only(RuleMatcher(counterparty_iban_hmac=CLEARING, amount_sign="debit"))
    named = RuleMatcher(counterparty_iban_hmac=CLEARING, counterparty_name_pattern="^X")
    assert not matching.iban_only(named)
    purposed = RuleMatcher(counterparty_iban_hmac=CLEARING, purpose_pattern="Abo")
    assert not matching.iban_only(purposed)
    assert not matching.iban_only(RuleMatcher(counterparty_name_pattern="^X"))


# --- What a confirmed answer becomes -----------------------------------------------------


def test_a_shared_iban_learns_the_iban_and_the_payees_name():
    tx = _tx("LIEFERDIENST BEISPIEL 4711")
    rule = matching.infer_rule(tx, _answer(tx, C.HH_DINING), SHARED)
    assert rule is not None
    assert rule.split_method is SplitMethod.SINGLE_CATEGORY and rule.category is C.HH_DINING
    assert rule.matcher == RuleMatcher(
        counterparty_iban_hmac=CLEARING,
        counterparty_name_pattern="^LIEFERDIENST BEISPIEL",
        amount_sign="debit",
    )


def test_a_single_payee_iban_still_learns_the_iban_alone():
    tx = _tx("LIEFERDIENST BEISPIEL", iban=SINGLE)
    rule = matching.infer_rule(tx, _answer(tx, C.HH_DINING), PRIVATE)
    assert rule is not None
    assert rule.matcher == RuleMatcher(counterparty_iban_hmac=SINGLE, amount_sign="debit")


def test_a_shared_iban_rule_is_checked_like_a_name_rule():
    tx = _tx("LIEFERDIENST BEISPIEL")
    other = NameHistory(
        transaction_id=UUID("0000003a-0000-4000-8000-000000000099"),
        counterparty_name="Lieferdienst Beispiel",
        category=C.HH_GROCERIES,
    )
    with pytest.raises(RuleError) as refused:
        matching.infer_rule(tx, _answer(tx, C.HH_DINING), SHARED, [other])
    assert refused.value.code == RULE_CONFLICTS


def test_a_shared_iban_line_without_a_name_learns_nothing():
    tx = _tx(None)
    assert matching.infer_rule(tx, _answer(tx, C.HH_DINING), SHARED) is None


def test_a_tax_relevant_code_on_a_shared_iban_learns_nothing():
    tx = _tx("SPENDE BEISPIEL")
    assert matching.infer_rule(tx, _answer(tx, C.HH_DONATION), SHARED) is None


# --- Applying a rule ---------------------------------------------------------------------


def test_the_iban_and_name_rule_books_its_payee_only():
    rule = _rule(
        {
            "counterparty_iban_hmac": CLEARING,
            "counterparty_name_pattern": "^LIEFERDIENST BEISPIEL",
            "amount_sign": "debit",
        }
    )
    hit = matching.apply_rule(rule, _tx("Lieferdienst Beispiel", "-18.90"), SHARED)
    assert hit is not None and hit.allocations[0].category is C.HH_DINING
    assert matching.apply_rule(rule, _tx("Supermarkt Beispiel"), SHARED) is None
    # every set field must hold: the name on another IBAN is no hit either
    assert matching.apply_rule(rule, _tx("Lieferdienst Beispiel", iban=SINGLE), PRIVATE) is None


def test_an_iban_only_rule_never_applies_on_a_shared_iban():
    rule = _rule({"counterparty_iban_hmac": CLEARING, "amount_sign": "debit"})
    tx = _tx("Supermarkt Beispiel")
    assert matching.apply_rule(rule, tx, SHARED) is None
    assert matching.rule_answer([rule], tx, SHARED, []) is None
    # the same rule where the IBAN is not shared still applies, as before
    assert matching.apply_rule(rule, tx, PRIVATE) is not None


def test_an_iban_rule_narrowed_by_a_typed_purpose_pattern_still_applies():
    rule = _rule(
        {"counterparty_iban_hmac": CLEARING, "purpose_pattern": "Abo", "amount_sign": "debit"},
        C.HH_SUBSCRIPTIONS,
    )
    tx = _tx("Streaming Beispiel").model_copy(update={"purpose": "Abo Oktober"})
    assert matching.apply_rule(rule, tx, SHARED) is not None
