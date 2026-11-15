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
    HistoryLine,
    HistoryRow,
    MatchContext,
    MatchingRule,
    MatchTransaction,
    NameHistory,
    RuleMatcher,
)
from hausbuch.schemas.enums import AccountPurpose, AllocationStatus

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
    if case.words is not None:
        assert list(found.words) == case.words


@pytest.mark.parametrize("case", FIXTURE.strengths, ids=lambda c: c.id)
def test_identity_strength_reads_the_fixture(case):
    a, b = case.a, case.b
    assert matching.identity_strength(a.name, b.name, a.purpose, b.purpose) == case.strength
    assert matching.identity_strength(b.name, a.name, b.purpose, a.purpose) == case.strength
    # weak is one payee too, except a first word that only starts the other's (one_payee)
    assert matching.same_payee(a.name, b.name, a.purpose, b.purpose) is case.one_payee
    assert matching.same_payee(b.name, a.name, b.purpose, a.purpose) is case.one_payee


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


# 14 Nov 2026: the merchant rule learned on a direct debit names the merchant, whatever form
# the service's line comes in (Luis's DisneyPlus card line stayed waiting after PR #299).
CARD_DISNEY = "12345678 PAYPAL *DISNEYPLUS Taurusavenue 9 2132LS Hoofddorp"


def test_a_direct_debit_merchant_rule_hits_the_card_form_and_back():
    on_debit = _learned(_tx(SHORT, DISNEY))
    card = _tx(CARD_DISNEY, CARD_DISNEY, iban=None, n=2)
    assert "Ihr Einkauf bei" not in (card.purpose or "")  # the regular expression misses it
    found = matching.apply_rule(on_debit, card, PRIVATE)
    assert found is not None and found.confidence == matching.EXACT
    assert found.allocations[0].category is C.HH_STREAMING
    other = "12345678 PAYPAL *MICROSOFT Taurusavenue 9 2132LS Hoofddorp"
    assert matching.apply_rule(on_debit, _tx(other, other, iban=None, n=3), PRIVATE) is None

    on_card = _learned(card)
    assert on_card.matcher.counterparty_name_pattern == r"^PAYPAL \*DISNEYPLUS TAURUSAVENUE"
    back = matching.apply_rule(on_card, _tx(LONG, DISNEY, n=4), PRIVATE)
    assert back is not None and back.confidence == matching.EXACT
    assert matching.apply_rule(on_card, _tx(LONG, MICROSOFT, n=5), PRIVATE) is None


def test_a_klarna_merchant_rule_hits_only_klarnas_lines_of_the_merchant():
    rule = _learned(_tx("Klarna Bank AB", "Purchase at DisneyPlus Gläubiger-ID: SE00"))
    # the same merchant through another service is not the rule's
    assert matching.apply_rule(rule, _tx(CARD_DISNEY, CARD_DISNEY, iban=None, n=2), PRIVATE) is None
    assert matching.apply_rule(rule, _tx(SHORT, DISNEY, n=3), PRIVATE) is None


# 15 Nov 2026 (D160 amended (4), Luis: „For a booking.com bank line it didn't use the rule“):
# PayPal printed one merchant two ways; a first word that starts the other's, with the street
# shared, books; without the street it only proposes.
BOOKING_A = "12345678 PAYPAL *BOOKING BOOKIN Marktplein 3 1011AB Beispielstad"
BOOKING_B = "87654321 PAYPAL *BOOKINGCOMB BO Marktplein 3 1011AB Beispielstad"


def test_a_paypal_merchant_rule_books_the_merchant_whose_first_word_it_starts():
    rule = _learned(_tx(BOOKING_A, BOOKING_A, iban=None), C.HH_TRAVEL)
    found = matching.apply_rule(rule, _tx(BOOKING_B, BOOKING_B, iban=None, n=2), PRIVATE)
    assert found is not None and found.confidence == matching.EXACT
    assert found.allocations[0].category is C.HH_TRAVEL
    bare = "87654321 PAYPAL *BOOKINGCOMB BO"
    weak = matching.apply_rule(rule, _tx(bare, bare, iban=None, n=3), PRIVATE)
    assert weak is not None and matching.weak_proposal(weak)


def test_a_typed_purpose_pattern_on_a_service_keeps_its_regular_expression_meaning():
    typed = _rule(
        RuleMatcher(counterparty_name_pattern=r"^PAYPAL\b", purpose_pattern="Einkauf bei Disney")
    )
    assert matching.apply_rule(typed, _tx(SHORT, DISNEY), PRIVATE) is not None
    card = _tx(CARD_DISNEY, CARD_DISNEY, iban=None, n=2)
    assert matching.apply_rule(typed, card, PRIVATE) is None


def test_a_weak_service_merchant_hit_names_the_merchant_in_its_question():
    rule = _learned(_tx(SHORT, ", Ihr Einkauf bei Adler Modemarkt, "), C.HH_CLOTHING)
    card = "12345678 PAYPAL *ADLER APOTHEKE 4029357733"
    found = matching.apply_rule(rule, _tx(card, card, iban=None, n=2), PRIVATE)
    assert found is not None and matching.weak_proposal(found)
    assert found.question == "Gleicher Empfänger wie „PayPal: Adler Modemarkt“? Bitte bestätigen."


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
    booked = matching.apply_rule(on_bank, card, PRIVATE)
    assert booked is not None
    assert booked.confidence == matching.EXACT  # "^LIEFERANDO" matches the card's name
    on_card = _learned(card, C.HH_DINING)
    back = matching.apply_rule(on_card, bank, MatchContext(**shared, account_purpose="private"))
    # "LIEFERANDO" starts the card's name and has BRAND_WORD_MIN letters: a strong match
    assert back is not None
    assert back.confidence == matching.EXACT
    assert not matching.weak_proposal(back)
    # another payee on the clearing IBAN is not hit
    other = _tx("KAUFLAND", iban=CLEARING, n=3)
    assert matching.apply_rule(on_bank, other, PRIVATE) is None


# --- A reference code before the name, a web address ---------------------------------------


@pytest.mark.parametrize(
    ("learned_on", "line", "category", "weak"),
    [
        (
            "K7B2QXNE ANTHROPIC 548 Market Street PMB 1234",
            "P4M9WZTA ANTHROPIC* CLAUDE SUB 548 Market Str",
            C.HH_SUBSCRIPTIONS,
            True,
        ),
        (
            "9d2e417b SQ *ATARIGUA SL | LA C Carrer Marina",
            "4f1c09ad SQ *ATARIGUA, S.L. Carrer Marina 100",
            C.HH_DINING,
            True,
        ),
        (
            "R0555123 curiara.com Avenue Louise 10",
            "Curiara Financial Services INC",
            C.HH_SUBSCRIPTIONS,
            True,
        ),
        ("WWW.DAZN.COM Huidekoperstraat 10", "DAZN", C.HH_STREAMING, True),
        ("DAZN", "WWW.DAZN.COM Huidekoperstraat 10", C.HH_STREAMING, True),
    ],
)
def test_a_rule_learned_behind_a_reference_code_or_web_address_hits_the_payee(
    learned_on, line, category, weak
):
    """Each pair is one payee; review F2: where only the first word or a one-word prefix
    names it (all five here), the rule proposes the line with a question instead of booking
    it."""
    rule = _learned(_tx(learned_on, iban=None), category)
    found = matching.apply_rule(rule, _tx(line, iban=None, n=2), PRIVATE)
    assert found is not None
    assert found.allocations[0].category == category
    assert matching.weak_proposal(found) is weak
    assert (found.confidence == matching.EXACT) is not weak
    sky = _tx("Sky Deutschland Fernsehen GmbH Co. KG", iban=None, n=3)
    assert matching.apply_rule(rule, sky, PRIVATE) is None


# --- Review F1: Google Pay's card form names its merchant ----------------------------------


def test_a_google_netflix_rule_does_not_book_supercell():
    rule = _learned(_tx("GOOGLE *Netflix g.co/helppay#", iban=None))
    again = matching.apply_rule(rule, _tx("GOOGLE*Netflix", iban=None, n=2), PRIVATE)
    assert again is not None
    assert again.confidence == matching.EXACT
    supercell = _tx("GOOGLE *Supercell g.co/helppay#", iban=None, n=3)
    assert matching.apply_rule(rule, supercell, PRIVATE) is None


def test_a_google_line_without_a_merchant_learns_nothing():
    tx = _tx("Google Payment Ie Ltd", "GOOGLE PLAY 4711-0815-2233", iban=OTHER_IBAN)
    with pytest.raises(RuleError) as caught:
        matching.infer_rule(tx, _answer(tx, C.HH_STREAMING), PRIVATE)
    assert caught.value.code == RULE_NO_MERCHANT


# --- Review F2: a weak hit proposes, a strong one books ------------------------------------


def test_a_rule_on_one_shop_proposes_another_with_its_first_word():
    rule = _learned(_tx("ADLER MODEMARKT", iban=None), C.HH_CLOTHING)
    found = matching.apply_rule(rule, _tx("ADLER APOTHEKE", iban=None, n=2), PRIVATE)
    assert found is not None
    assert found.confidence == matching.GUARDED_CONFIDENCE
    assert found.question == "Gleicher Empfänger wie „ADLER MODEMARKT“? Bitte bestätigen."
    assert found.allocations[0].category == C.HH_CLOTHING
    # its own name is booked
    same = matching.apply_rule(rule, _tx("ADLER MODEMARKT", iban=None, n=3), PRIVATE)
    assert same is not None and same.confidence == matching.EXACT


def test_a_strong_hit_wins_over_a_weak_one():
    weak = _learned(_tx("ADLER APOTHEKE", iban=None), C.HH_HEALTH)
    strong = _learned(_tx("ADLER MODEMARKT", iban=None), C.HH_CLOTHING)
    tx = _tx("ADLER MODEMARKT FILIALE 12", iban=None, n=2)
    found = matching.rule_answer([weak, strong], tx, PRIVATE, [])
    assert found is not None
    assert found.confidence == matching.EXACT
    assert found.allocations[0].category == C.HH_CLOTHING


def test_an_iban_rule_is_not_weakened():
    rule = _rule(RuleMatcher(counterparty_iban_hmac=OTHER_IBAN, amount_sign="debit"))
    found = matching.apply_rule(rule, _tx("ANYTHING", iban=OTHER_IBAN), PRIVATE)
    assert found is not None and found.confidence == matching.EXACT


# --- Review F4: a typed bare service pattern keeps working ---------------------------------


def test_a_typed_paypal_pattern_is_no_service_only_rule():
    typed = RuleMatcher(counterparty_name_pattern="^PAYPAL", amount_sign="debit")
    assert not matching.service_only(typed)
    tx = _tx(SHORT, DISNEY)
    assert matching.apply_rule(_rule(typed), tx, PRIVATE) is not None
    learned = RuleMatcher(
        counterparty_name_pattern="^PAYPAL EUROPE S A R L ET CIE S C A", amount_sign="debit"
    )
    assert matching.service_only(learned)
    assert matching.apply_rule(_rule(learned), tx, PRIVATE) is None


# --- 14 Nov 2026: an IBAN rule reaches its payee on another IBAN -----------------------------

SKY = "Sky Deutschland Fernsehen GmbH Co. KG"
SKY_OLD = "40123456 01/26 SKY ABO - MEHR INFO AUF SKY.DE/KONTO"
SKY_NEW = "Ihr Sky Abo - 1234567890 - Zahlung 04/26"
NEW_IBAN = "d" * 64


def _iban_rule(
    name: str = SKY,
    purpose: str = SKY_NEW,
    category: C = C.HH_PAY_TV,
    amount: str | None = "-30.00",
    **fields,
) -> MatchingRule:
    rule = _rule(RuleMatcher(counterparty_iban_hmac=NEW_IBAN, amount_sign="debit"), category)
    if fields:
        rule = rule.model_copy(update=fields)
    return rule.with_learned_from(name, purpose, None if amount is None else Decimal(amount))


def _own_line(
    amount: str,
    day: date,
    n: int,
    category: C = C.HH_PAY_TV,
    status: AllocationStatus = AllocationStatus.CONFIRMED,
) -> HistoryLine:
    """A line on the rule's own IBAN (NEW_IBAN) with one row."""
    return HistoryLine(
        transaction_id=UUID(f"0000003d-0000-4000-8000-{n:012d}"),
        booking_date=day,
        amount=Decimal(amount),
        counterparty_iban_hmac=NEW_IBAN,
        rows=[HistoryRow(category=category, status=status, amount=Decimal(amount))],
    )


def test_an_iban_rule_books_its_payee_on_another_iban():
    rule = _iban_rule()
    old = _tx(SKY, SKY_OLD, amount="-30.00", iban=OTHER_IBAN)
    found = matching.apply_rule(rule, old, PRIVATE)
    assert found is not None and found.confidence == matching.EXACT
    assert found.allocations[0].category is C.HH_PAY_TV
    answer = matching.rule_answer([rule], old, PRIVATE, [])
    assert answer is not None and answer.confidence == matching.EXACT
    # another company on that IBAN, a credit, a line without an IBAN: no hit
    assert matching.apply_rule(rule, _tx("EPRIMO GMBH", iban=OTHER_IBAN, n=2), PRIVATE) is None
    refund = _tx(SKY, SKY_OLD, amount="30.00", iban=OTHER_IBAN, n=3)
    assert matching.apply_rule(rule, refund, PRIVATE) is None
    assert matching.apply_rule(rule, _tx(SKY, SKY_OLD, iban=None, n=4), PRIVATE) is None


def test_an_iban_rule_without_its_origin_line_stays_on_its_iban():
    bare = _rule(RuleMatcher(counterparty_iban_hmac=NEW_IBAN, amount_sign="debit"), C.HH_PAY_TV)
    assert bare.learned_from is None
    assert matching.apply_rule(bare, _tx(SKY, SKY_OLD, iban=OTHER_IBAN), PRIVATE) is None
    assert "learned_from" not in _iban_rule().model_dump()  # never serialised, never stored


@pytest.mark.parametrize(
    "context",
    [
        MatchContext(account_purpose=AccountPurpose.RENTAL),
        MatchContext(account_purpose=AccountPurpose.PRIVATE, shared_iban_hmacs=[OTHER_IBAN]),
        MatchContext(account_purpose=AccountPurpose.PRIVATE, shared_iban_hmacs=[NEW_IBAN]),
        MatchContext(account_purpose=AccountPurpose.PRIVATE, own_account_hmacs=[OTHER_IBAN]),
        MatchContext(account_purpose=AccountPurpose.PRIVATE, own_account_hmacs=[NEW_IBAN]),
    ],
    ids=["rental", "line_iban_shared", "rule_iban_shared", "line_iban_own", "rule_iban_own"],
)
def test_an_iban_rule_never_reaches_another_iban_here(context):
    old = _tx(SKY, SKY_OLD, iban=OTHER_IBAN)
    assert matching.cross_iban_hit(_iban_rule(), old, context) is None
    assert matching.apply_rule(_iban_rule(), old, context) is None


def test_a_weak_or_same_amount_only_cross_iban_hit_is_a_proposal():
    weak = _iban_rule("ADLER MODEMARKT", "", C.HH_CLOTHING)
    found = matching.apply_rule(weak, _tx("ADLER APOTHEKE", iban=OTHER_IBAN), PRIVATE)
    assert found is not None and matching.weak_proposal(found)
    assert found.confidence == matching.GUARDED_CONFIDENCE
    assert found.question == "Gleicher Empfänger wie „ADLER MODEMARKT“? Bitte bestätigen."

    exact = _iban_rule(same_amount_only=True)
    found = matching.apply_rule(exact, _tx(SKY, SKY_OLD, iban=OTHER_IBAN, n=2), PRIVATE)
    assert found is not None and matching.weak_proposal(found)
    assert found.question == f"Gleicher Empfänger wie „{SKY}“? Bitte bestätigen."


def test_a_strong_cross_iban_hit_books_only_at_the_rules_latest_amount():
    """Review F1 (Luis, 14 Nov 2026): one company's other product on another IBAN
    (Kfz-Versicherung and Hausrat) is never booked as the first, only proposed."""
    line = _tx(SKY, SKY_OLD, amount="-59.00", iban=OTHER_IBAN)  # booked 5 Oct 2026
    question = f"Gleicher Empfänger wie „{SKY}“? Bitte bestätigen."

    def answer(rule: MatchingRule, history: list[HistoryLine]) -> AllocationSet | None:
        return matching.apply_rule(rule, line, PRIVATE, history)

    rule = _iban_rule(amount="-30.00")
    # the newest confirmed line of its category on or before the line's date decides
    earlier = [_own_line("-30.00", date(2026, 4, 1), 1), _own_line("-59.00", date(2026, 9, 1), 2)]
    found = answer(rule, earlier)
    assert found is not None and found.confidence == matching.EXACT
    later = [*earlier, _own_line("-30.00", date(2026, 11, 1), 3)]  # after the line: not used
    assert answer(rule, later).confidence == matching.EXACT
    moved = [_own_line("-59.00", date(2026, 4, 1), 1), _own_line("-39.00", date(2026, 9, 1), 2)]
    found = answer(rule, moved)
    assert found.confidence == matching.GUARDED_CONFIDENCE and found.question == question
    # none that early: the newest of all
    assert answer(rule, [_own_line("-59.00", date(2026, 11, 1), 1)]).confidence == matching.EXACT
    # a proposed line, another category, a credit or another IBAN's line never counts:
    # the line the rule was learned from (-30.00) decides
    ignored = [
        _own_line("-59.00", date(2026, 9, 1), 1, status=AllocationStatus.PROPOSED),
        _own_line("-59.00", date(2026, 9, 2), 2, category=C.HH_STREAMING),
        _own_line("59.00", date(2026, 9, 3), 3),
        _own_line("-59.00", date(2026, 9, 4), 4).model_copy(
            update={"counterparty_iban_hmac": OTHER_IBAN}
        ),
    ]
    assert answer(rule, ignored).confidence == matching.GUARDED_CONFIDENCE
    assert answer(_iban_rule(amount="-59.00"), ignored).confidence == matching.EXACT
    # nothing known at all: a proposal
    assert answer(_iban_rule(amount=None), []).question == question


def test_a_split_or_a_payment_service_rule_is_not_carried_to_another_iban():
    split = _iban_rule().model_copy(
        update={
            "split_method": "as_last_split",
            "category": None,
            "categories": [C.HH_PAY_TV, C.HH_STREAMING],
        }
    )
    assert matching.cross_iban_payee(split) is None
    assert matching.cross_iban_payee(_iban_rule(SHORT, DISNEY)) is None
    on_service = _tx(SHORT, DISNEY, iban=OTHER_IBAN)
    assert matching.apply_rule(_iban_rule(), on_service, PRIVATE) is None


def test_a_rule_on_the_lines_own_iban_wins_over_one_from_another_iban():
    cross = _iban_rule()
    own = _rule(
        RuleMatcher(counterparty_iban_hmac=OTHER_IBAN, amount_sign="debit"), C.HH_SUBSCRIPTIONS
    )
    line = _tx(SKY, SKY_OLD, iban=OTHER_IBAN)
    for rules in ([cross, own], [own, cross]):
        found = matching.rule_answer(rules, line, PRIVATE, [])
        assert found is not None and found.allocations[0].category is C.HH_SUBSCRIPTIONS


SPK = "STADTSPARKASSE DUESSELDORF"


def test_a_cash_rule_books_a_withdrawal_on_another_iban_at_any_amount():
    """15 Nov 2026 (Luis): a cash withdrawal at another of the bank's machines (another IBAN)
    of another amount is booked by the cash rule; a non-card debit to the bank is not."""
    rule = _iban_rule(SPK, "2026-04-27T13:08 Debitk.3 2029-12", C.HH_CASH, "-100.00")
    atm = _tx(SPK, "2026-08-09T12:32 Debitk.3 2029-12", amount="-20.00", iban=OTHER_IBAN)
    found = matching.apply_rule(
        rule, atm, PRIVATE, [_own_line("-100.00", date(2026, 4, 27), 1, C.HH_CASH)]
    )
    assert found is not None and found.confidence == matching.EXACT
    assert found.allocations[0].category is C.HH_CASH
    other = _tx(SPK, "Darlehen 12345 Rate 08/26", amount="-20.00", iban=OTHER_IBAN, n=2)
    proposed = matching.apply_rule(rule, other, PRIVATE)
    assert proposed is not None and matching.weak_proposal(proposed)
    assert not matching.cash_across_ibans(_iban_rule(), atm)  # another category keeps F1
