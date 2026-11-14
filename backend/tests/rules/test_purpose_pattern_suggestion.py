"""The purpose pattern the allocation panel proposes for a line without an IBAN (14 Nov 2026,
principle 7): the purpose's first word, and whether the rule it makes is safe on the
workspace's lines. Invented purposes and IBAN hashes only."""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from hausbuch.rules import matching as rules
from hausbuch.schemas.allocation import AllocationCategory, MatchTransaction, RuleMatcher

pytestmark = pytest.mark.rule

FEES = AllocationCategory.BANK_FEES
FEE_PATTERN = "^Entgeltabrechnung"
DEBIT = RuleMatcher(purpose_pattern=FEE_PATTERN, amount_sign="debit")


@pytest.mark.parametrize(
    ("purpose", "word"),
    [
        ("Entgeltabrechnung siehe Anlage Abrechnung 10/2026", "Entgeltabrechnung"),
        ("  Entgeltabrechnung\n siehe Anlage", "Entgeltabrechnung"),  # read as rule_hits reads
        ("Gebühren Kontoführung", "Gebühren"),  # umlaut
        ("Überweisungsgebühr 4,95", "Überweisungsgebühr"),  # umlaut first
        ("Straßenreinigung 2026", "Straßenreinigung"),  # ß
        ("Entgelt-Abrechnung 10/2026", "Entgelt"),  # stops at the hyphen
        ("Abschluss2026", "Abschluss"),  # stops at the digit
        ("Zinsen", "Zinsen"),  # exactly six letters
        ("Karte 10/26", None),  # a short word
        ("Miete Oktober", None),
        ("10/2026 Entgeltabrechnung", None),  # a digit first
        ("-Entgeltabrechnung", None),  # a sign first
        ("_Entgeltabrechnung", None),  # an underscore is no letter
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_the_purposes_first_word(purpose, word):
    assert rules.purpose_word(purpose) == word
    expected = None if word is None else "^" + word
    assert rules.suggested_purpose_pattern(purpose) == expected


def test_the_suggested_pattern_is_escaped_and_hits_its_own_purpose():
    purpose = "Überweisungsgebühr laut Preisverzeichnis"
    pattern = rules.suggested_purpose_pattern(purpose)
    assert pattern is not None and pattern.startswith("^")
    matcher = RuleMatcher(purpose_pattern=pattern, amount_sign="debit")
    assert rules.rule_hits(matcher, _tx(purpose))
    assert rules.rule_hits(matcher, _tx("ÜBERWEISUNGSGEBÜHR 2026"))  # case ignored
    assert not rules.rule_hits(matcher, _tx("Rückbuchung Überweisungsgebühr"))  # anchored


def _tx(purpose: str, amount: str = "-4.95", iban_hmac: str | None = None) -> MatchTransaction:
    return MatchTransaction(
        id=uuid.uuid4(),
        booking_date=date(2026, 10, 30),
        value_date=date(2026, 10, 30),
        amount=Decimal(amount),
        counterparty_name=None,
        counterparty_iban_hmac=iban_hmac,
        purpose=purpose,
        booking_text=None,
    )


def _line(
    purpose: str = "Entgeltabrechnung siehe Anlage",
    *,
    amount: str = "-4.95",
    iban_hmac: str | None = None,
    confirmed: tuple[AllocationCategory, ...] = (),
    waiting: bool = False,
) -> rules.PatternLine:
    return rules.PatternLine(
        tx=_tx(purpose, amount, iban_hmac), confirmed=confirmed, waiting=waiting
    )


def test_the_waiting_lines_it_hits_are_counted():
    lines = [
        _line(waiting=True),
        _line(waiting=True),
        _line(confirmed=(FEES,)),  # confirmed the same way: safe, not waiting
        _line(waiting=False),  # waiting on an account of another purpose: not counted
        _line("Entgelt Kontoführung", waiting=True),  # not hit
        _line(amount="12.00", waiting=True),  # the other sign: not hit
    ]
    assert rules.pattern_waiting(DEBIT, FEES, lines) == 2


def test_no_line_is_safe_with_none_waiting():
    assert rules.pattern_waiting(DEBIT, FEES, []) == 0


def test_a_hit_line_with_an_iban_makes_it_unsafe():
    lines = [_line(waiting=True), _line(iban_hmac="ab" * 32)]
    assert rules.pattern_waiting(DEBIT, FEES, lines) is None


def test_a_hit_line_confirmed_under_another_category_makes_it_unsafe():
    lines = [_line(waiting=True), _line(confirmed=(AllocationCategory.HH_OTHER,))]
    assert rules.pattern_waiting(DEBIT, FEES, lines) is None


def test_a_hit_line_confirmed_as_a_split_makes_it_unsafe():
    assert rules.pattern_waiting(DEBIT, FEES, [_line(confirmed=(FEES, FEES))]) is None


def test_lines_it_does_not_hit_never_make_it_unsafe():
    lines = [
        _line("Gutschrift Entgeltabrechnung", iban_hmac="ab" * 32),  # not at the start
        _line(amount="4.95", confirmed=(AllocationCategory.HH_OTHER,)),  # a credit
        _line(waiting=True),
    ]
    assert rules.pattern_waiting(DEBIT, FEES, lines) == 1
