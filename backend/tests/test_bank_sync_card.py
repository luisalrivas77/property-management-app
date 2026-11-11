"""The failed bank sync's card text (services.bank_sync.failed_card_text, phase 5 D151): the
bank's deciding code picks a reason and a next step, a code without its own text keeps the
general one, and the suffix always names the error code and the bank's code."""

import pytest

from hausbuch.rules import errors as E
from hausbuch.services.bank_sync import _REFUSED_CARD, failed_card_text

PIN_WARNING = "nach drei falschen PINs sperrt die Bank den Zugang"


def test_9078_names_the_product_id_not_the_pin():
    reasoning, question = failed_card_text(E.BANK_REFUSED, "9078")
    assert "Produkt-ID" in reasoning
    assert "nicht die PIN" in reasoning
    assert reasoning.endswith("(bank.refused, Code der Bank 9078)")
    assert "HAUSBUCH_FINTS_PRODUCT_ID" in question
    assert "warten" in question
    assert PIN_WARNING not in question  # not a PIN error: retrying is safe


@pytest.mark.parametrize("code", ["9942", "9340", "9910", "9941"])
def test_a_pin_error_warns_not_to_retry_blindly(code):
    reasoning, question = failed_card_text(E.BANK_REFUSED, code)
    assert "PIN" in reasoning
    assert PIN_WARNING in question
    assert reasoning.endswith(f"(bank.refused, Code der Bank {code})")


@pytest.mark.parametrize("code", ["9930", "9931", "3938"])
def test_a_locked_access_says_not_to_retry(code):
    reasoning, question = failed_card_text(E.BANK_REFUSED, code)
    assert "gesperrt" in reasoning
    assert "nicht erneut abrufen" in question


@pytest.mark.parametrize("code", ["9210", "9391", "9955", "9361", "9958", "9380"])
def test_a_stale_connection_says_how_to_drop_it(code):
    _, question = failed_card_text(E.BANK_REFUSED, code)
    assert "„Zugang ändern“" in question


def test_a_code_without_its_own_text_keeps_the_general_one():
    general, general_question = failed_card_text(E.BANK_REFUSED, None)
    reasoning, question = failed_card_text(E.BANK_REFUSED, "9123")
    shown = "(bank.refused, Code der Bank 9123)"
    assert reasoning == general.removesuffix("(bank.refused)") + shown
    assert question == general_question
    assert PIN_WARNING in question  # the general text keeps its warning


def test_the_general_codes_have_no_text_of_their_own():
    """9010, 9050 and friends say nothing about why; deciding_code only shows them when
    nothing more specific came, and then the general text is the honest one."""
    assert not {"9000", "9010", "9050", "9800", "9999"} & set(_REFUSED_CARD)


def test_a_code_only_names_its_text_for_bank_refused():
    reasoning, _ = failed_card_text(E.BANK_UNREACHABLE, "9078")
    assert "Produkt-ID" not in reasoning
    assert reasoning.endswith("(bank.unreachable, Code der Bank 9078)")


def test_an_unknown_error_code_keeps_the_default_card():
    reasoning, question = failed_card_text("bank.something_new", None)
    assert reasoning == "Der letzte Bankabruf ist fehlgeschlagen. (bank.something_new)"
    assert question.startswith("Erneut abrufen")


def test_every_entry_is_a_reason_and_a_next_step():
    for code, (reason, step) in _REFUSED_CARD.items():
        assert len(code) == 4 and code.isdigit()
        assert code[0] == "9" or code == "3938"  # only codes deciding_code can return
        assert reason.strip() and step.strip()
