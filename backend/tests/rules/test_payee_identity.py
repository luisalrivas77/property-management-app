"""One payee, however a statement spells it (phase 6 D160 amended (4), 14 Nov 2026).

Replays fixtures/phase6/payee_identity.yaml on rules.matching (payee, same_payee,
shared_iban), and checks that a learned rule hits what the fixture calls one payee: the
Disney+ lines through PayPal under two spellings of PayPal's name, two PayPal merchants on one
IBAN, a Klarna line, Lieferando on a bank account and a card, and an old rule on PayPal's name
alone ignored. Invented and well-known merchant names only."""

from datetime import date
from decimal import Decimal
from uuid import UUID

import fixture_models_phase6 as phase6
import pytest

from hausbuch.rules import matching
from hausbuch.rules.errors import RULE_NO_MERCHANT, RULE_TOO_BROAD, RuleError
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
)
from hausbuch.schemas.enums import AccountPurpose

FIXTURE = phase6.load("payee_identity")
PAYPAL_IBAN = "a" * 64
OTHER_IBAN = "b" * 64
CLEARING = "c" * 64
PRIVATE = MatchContext(account_purpose=AccountPurpose.PRIVATE)
LONG = "PayPal Europe S.a.r.l. et Cie S.C.A 22-24 Boulevard Royal"
SHORT = "PayPal Europe S.a.r.l. et Cie S.C.A"
DISNEY = "1041234567890 PP.1234.PP . DisneyPlus, Ihr Einkauf bei DisneyPlus"
MICROSOFT = "1041234567891 PP.1234.PP . MicrosoftPay, Ihr Einkauf bei MicrosoftPay"


def _tx(
    name: str | None,
    purpose: str = "",
    *,
    amount: str = "-9.99",
    iban: str | None = PAYPAL_IBAN,
    n: int = 1,
) -> MatchTransaction:
    return MatchTransaction(
        id=UUID(f"0000003c-0000-4000-8000-{n:012d}"),
        booking_date=date(2026, 10, 5),
        amount=Decimal(amount),
        counterparty_name=name,
        counterparty_iban_hmac=iban,
        purpose=purpose,
    )


def _answer(tx: MatchTransaction, category: C) -> AllocationSet:
    return AllocationSet(
        transaction_id=tx.id,
        method=AllocationMethod.USER,
        confidence=Decimal("1.000"),
        allocations=[AllocationDraft(amount=tx.amount, category=category)],
    )


def _rule(matcher: RuleMatcher, category: C = C.HH_STREAMING) -> MatchingRule:
    return MatchingRule.model_validate(
        {
            "id": "0000002b-0000-4000-8000-000000000931",
            "workspace_id": "0000000a-0000-4000-8000-000000000001",
            "created_at": "2026-02-01T10:00:00Z",
            "updated_at": "2026-02-01T10:00:00Z",
            "matcher": matcher.model_dump(),
            "split_method": "single_category",
            "category": category,
        }
    )


def _learned(tx: MatchTransaction, category: C = C.HH_STREAMING, **context) -> MatchingRule:
    ctx = MatchContext(account_purpose=AccountPurpose.PRIVATE, **context)
    created = matching.infer_rule(tx, _answer(tx, category), ctx)
    assert created is not None
    return _rule(created.matcher, category)


# --- The fixture ---------------------------------------------------------------------------


def _pairs():
    for section in (FIXTURE.same, FIXTURE.different, FIXTURE.either):
        for pair in section:
            yield pytest.param(pair, id=pair.id)


@pytest.mark.parametrize("pair", list(_pairs()))
def test_same_payee_reads_the_fixture_pairs(pair):
    expected = pair.today if pair.same is None else pair.same
    a, b = pair.a, pair.b
    assert matching.same_payee(a.name, b.name, a.purpose, b.purpose) is expected
    assert matching.same_payee(b.name, a.name, b.purpose, a.purpose) is expected  # symmetric


@pytest.mark.parametrize("case", FIXTURE.payees, ids=lambda c: c.id)
def test_payee_label_and_patterns(case):
    found = matching.payee(case.name, case.purpose)
    assert found.label == case.label
    assert found.unreadable is case.unreadable
    assert found.name_pattern == case.name_pattern
    assert found.purpose_pattern == case.purpose_pattern


@pytest.mark.parametrize("case", FIXTURE.shared_ibans, ids=lambda c: c.id)
def test_shared_iban_counts_payees(case):
    lines = [(line.name, line.purpose) for line in case.lines]
    assert matching.shared_iban(lines) is case.shared


# --- Item 1: a longer spelling is the same payee, for a rule too ---------------------------


def test_a_rule_learned_on_a_short_name_hits_the_longer_spelling_and_back():
    short = _learned(_tx("EPRIMO GMBH", iban=None), C.HH_ENERGY)
    longer = _tx("EPRIMO GMBH FLUGHAFENSTRASE 20", iban=None, n=2)
    assert matching.apply_rule(short, longer, PRIVATE) is not None
    long_rule = _learned(longer, C.HH_ENERGY)
    assert matching.apply_rule(long_rule, _tx("EPRIMO GMBH", iban=None, n=3), PRIVATE)


def test_a_typed_pattern_keeps_its_regular_expression_meaning():
    # a person's own pattern (lower case, not merchant_pattern's form) is no payee
    rule = _rule(RuleMatcher(counterparty_name_pattern="^Eprimo GmbH Flughafen"), C.HH_ENERGY)
    assert matching.apply_rule(rule, _tx("EPRIMO GMBH", iban=None), PRIVATE) is None


# --- Item 2: a payment service's merchants ------------------------------------------------


def test_disney_rule_on_the_short_name_hits_the_long_names_lines():
    rule = _learned(_tx(SHORT, DISNEY))
    assert rule.matcher.counterparty_iban_hmac is None
    assert rule.matcher.purpose_pattern == r"Ihr Einkauf bei DisneyPlus(?!\w)"
    for n in (2, 3, 4):
        hit = matching.apply_rule(rule, _tx(LONG, DISNEY, iban=OTHER_IBAN, n=n), PRIVATE)
        assert hit is not None and hit.allocations[0].category is C.HH_STREAMING


def test_two_paypal_merchants_on_one_iban_learn_two_rules():
    disney = _learned(_tx(SHORT, DISNEY))
    microsoft = _learned(_tx(SHORT, MICROSOFT, n=2), C.HH_SUBSCRIPTIONS)
    assert matching.apply_rule(disney, _tx(SHORT, MICROSOFT, n=3), PRIVATE) is None
    assert matching.apply_rule(microsoft, _tx(SHORT, DISNEY, n=4), PRIVATE) is None
    answer = matching.rule_answer([disney, microsoft], _tx(LONG, MICROSOFT, n=5), PRIVATE, [])
    assert answer is not None and answer.allocations[0].category is C.HH_SUBSCRIPTIONS


def test_a_klarna_line_learns_the_merchant():
    purpose = "Purchase at H&M Gläubiger-ID: SE00ZZZ0000000000000000001"
    rule = _learned(_tx("Klarna Bank AB", purpose, iban=OTHER_IBAN), C.HH_CLOTHING)
    assert rule.matcher.counterparty_name_pattern == r"^KLARNA\b"
    again = _tx("KLARNA BANK AB", "Purchase at H&M Gläubiger-ID: SE00ZZZ0000000000000000001", n=2)
    assert matching.apply_rule(rule, again, PRIVATE) is not None
    other = _tx("Klarna Bank AB", "Purchase at Zalando SE Gläubiger-ID: SE00ZZZ01", n=3)
    assert matching.apply_rule(rule, other, PRIVATE) is None


def test_a_paypal_line_without_a_merchant_learns_nothing():
    tx = _tx(SHORT, "1041234567893 PP.1234.PP")
    with pytest.raises(RuleError) as refused:
        matching.infer_rule(tx, _answer(tx, C.HH_STREAMING), PRIVATE)
    assert refused.value.code == RULE_NO_MERCHANT


def test_an_old_rule_on_paypals_name_alone_is_ignored():
    # learned before 14 Nov 2026 on a then-shared IBAN: the IBAN and PayPal's name
    old = _rule(
        RuleMatcher(
            counterparty_iban_hmac=PAYPAL_IBAN,
            counterparty_name_pattern="^PAYPAL EUROPE S A R L ET CIE S C A",
            amount_sign="debit",
        )
    )
    assert matching.service_only(old.matcher)
    assert matching.rule_hits(old.matcher, _tx(SHORT, MICROSOFT))  # it still names the line
    assert matching.apply_rule(old, _tx(SHORT, MICROSOFT), PRIVATE) is None  # never applied
    iban_only = _rule(RuleMatcher(counterparty_iban_hmac=PAYPAL_IBAN, amount_sign="debit"))
    assert matching.apply_rule(iban_only, _tx(SHORT, DISNEY), PRIVATE) is None
    # a plain payee's line on the IBAN-only rule's IBAN is still booked
    assert matching.apply_rule(iban_only, _tx("EPRIMO GMBH"), PRIVATE) is not None


def test_a_card_paypal_rule_hits_the_merchant_through_paypal_only():
    rule = _learned(_tx("4029357733 PAYPAL *DISNEYPLUS 35314369001 LU", iban=None))
    assert rule.matcher.counterparty_name_pattern == r"^PAYPAL \*DISNEYPLUS"
    assert matching.apply_rule(rule, _tx("DisneyPlus - PayPal", iban=None, n=2), PRIVATE)
    assert matching.apply_rule(rule, _tx(SHORT, DISNEY, n=3), PRIVATE)
    assert matching.apply_rule(rule, _tx(SHORT, MICROSOFT, n=4), PRIVATE) is None


def test_a_paypal_history_without_purposes_is_still_too_broad():
    # NameHistory without a purpose: the merchant is not known, so the line counts
    tx = _tx(SHORT, DISNEY)
    names = [
        NameHistory(
            transaction_id=UUID(f"0000003c-0000-4000-8000-{n:012d}"),
            counterparty_name=SHORT,
            category=category,
        )
        for n, category in ((90, C.HH_STREAMING), (91, C.HH_LEISURE_TRAVEL))
    ]
    with pytest.raises(RuleError) as refused:
        matching.infer_rule(tx, _answer(tx, C.HH_STREAMING), PRIVATE, names)
    assert refused.value.code == RULE_TOO_BROAD
    # with their purposes, the other merchant's line no longer counts
    named = [
        names[0].model_copy(update={"purpose": DISNEY}),
        names[1].model_copy(update={"purpose": MICROSOFT}),
    ]
    assert matching.infer_rule(tx, _answer(tx, C.HH_STREAMING), PRIVATE, named) is not None


# --- Item 3: a per-purchase code ------------------------------------------------------------


def test_a_card_line_with_an_order_code_repeats():
    rule = _learned(_tx("AMZN Mktp DE*ZD2HG0G54 5 rue plaetis", iban=None), C.HH_ONLINE_SHOPPING)
    again = _tx("AMZN Mktp DE*AB7KK2Q11 5 rue plaetis", iban=None, n=2)
    assert matching.apply_rule(rule, again, PRIVATE) is not None


# --- Item 4: one rule across a shared IBAN and a card --------------------------------------


def test_lieferando_learned_on_either_account_hits_the_other():
    shared = {"shared_iban_hmacs": [CLEARING]}
    bank = _tx("Lieferando", iban=CLEARING, amount="-24.50")
    card = _tx("Lieferando.de lieferse Oosterdoksstraat 80", iban=None, amount="-31.00", n=2)
    on_bank = _learned(bank, C.HH_DINING, **shared)
    assert on_bank.matcher.counterparty_iban_hmac is None
    assert matching.apply_rule(on_bank, card, PRIVATE) is not None
    on_card = _learned(card, C.HH_DINING)
    assert matching.apply_rule(on_card, bank, MatchContext(**shared, account_purpose="private"))
    # another payee on the clearing IBAN is not hit
    other = _tx("KAUFLAND", iban=CLEARING, n=3)
    assert matching.apply_rule(on_bank, other, PRIVATE) is None
