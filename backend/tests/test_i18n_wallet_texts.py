"""The wallet cards in English on read (phase 10 D284, step 4c): the card's sentence, the
group's stored question and the errors a person's answer can meet. German stays the source:
the questions are stored German and only shown in English, and a German card reads exactly as
before. Pure, invented masked addresses only."""

import itertools
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from hausbuch import i18n
from hausbuch.api.errors import MESSAGES
from hausbuch.rules.chain.solana import QUESTION_PROGRAM, QUESTION_UNKNOWN, QUESTION_UNREADABLE
from hausbuch.rules.chain.utxo import (
    FOREIGN_INPUTS_QUESTION,
    NO_MOVEMENT_QUESTION,
    ChangeCandidate,
    change_question,
)
from hausbuch.schemas.chain import GroupKind
from hausbuch.schemas.household import MovementKind as K
from hausbuch.schemas.household import MovementStatus
from hausbuch.schemas.wallet_cards import WalletCardKind, WalletLeg
from hausbuch.services import wallet_groups
from hausbuch.services.wallet_groups import QUESTIONS, _to_card, card_text

GERMAN_MARKS = ("ä", "ö", "ü", "ß", "„", "Ä", "Ö", "Ü")
SOL, TOKEN = uuid.uuid4(), uuid.uuid4()
AT = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _leg(kind: K, units: str, asset=TOKEN) -> WalletLeg:
    return WalletLeg(
        movement_id=uuid.uuid4(),
        address_masked="So1x…xxxx",
        asset_id=asset,
        symbol="SOL" if asset == SOL else "TOK",
        kind=kind,
        status=MovementStatus.PROPOSED,
        quantity=Decimal(units),
    )


FEE = _leg(K.FEE, "-0.000005", SOL)
SWAP = [_leg(K.DISPOSAL, "-10"), _leg(K.ACQUISITION, "0.003"), FEE]


def _candidate(n: int, sats: int = 2_990_000) -> ChangeCandidate:
    return ChangeCandidate(address=f"bc1q{n:04d}zzzzzzzzzzzzzzzzzzzzzzzz{n:04d}", sats=sats, vout=n)


# Every question a sync stores on a group (rules/chain).
STORED_QUESTIONS = [
    f"{QUESTION_PROGRAM}Prog…Ab12?",
    "Ist Stak…Cd34 Ihr eigenes Stake-Konto?",
    QUESTION_UNKNOWN,
    QUESTION_UNREADABLE,
    FOREIGN_INPUTS_QUESTION,
    NO_MOVEMENT_QUESTION,
    change_question([_candidate(1)]),
    change_question([_candidate(1), _candidate(2, 150_000)]),
    change_question([_candidate(n) for n in range(40)]),  # cut to fit, „und N weitere“
    "Ist eine dieser Adressen Ihr Wechselgeld? 41 Adressen",  # none fits the 500 characters
]


@pytest.mark.parametrize("question", STORED_QUESTIONS)
def test_every_stored_question_has_its_english(question):
    english = QUESTIONS.find(question)
    assert english is not None, question
    assert not any(m in english for m in GERMAN_MARKS), english
    assert QUESTIONS.translate(question) == question  # German outside an English request


def test_the_change_question_reads_naturally():
    question = change_question([_candidate(1), _candidate(2, 150_000)])
    assert question == (
        "Ist eine dieser Adressen Ihr Wechselgeld? bc1q…0001 (0,0299 BTC), bc1q…0002 (0,0015 BTC)"
    )
    with i18n.use(i18n.EN):
        assert QUESTIONS.translate(question) == (
            "Is one of these addresses your change? bc1q…0001 (0.0299 BTC), bc1q…0002 (0.0015 BTC)"
        )
        cut = change_question([_candidate(n) for n in range(40)])
        assert QUESTIONS.translate(cut).endswith(" more")


def _card(question, legs, kind=GroupKind.SWAP, dust=False):
    row = SimpleNamespace(
        id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        chain="solana",
        kind=kind.value,
        status="proposed",
        occurred_at=AT,
        question=question,
    )
    return _to_card(row, legs, set(), dust)


LEG_SETS = [
    [],
    [_leg(K.UNCATEGORIZED, "1")],
    [_leg(K.UNCATEGORIZED, "-1"), FEE],
    [_leg(K.INTERNAL_TRANSFER_OUT, "-2", SOL), FEE],
    SWAP,
    [_leg(K.AIRDROP, "1000")],
]


def test_every_card_in_english_keeps_its_shape_and_its_button():
    shapes = itertools.product([None, *STORED_QUESTIONS], LEG_SETS, [False, True])
    for question, legs, dust in shapes:
        if dust and (question is not None or len(legs) != 1 or legs[0].kind is not K.UNCATEGORIZED):
            continue
        german = _card(question, legs, dust=dust)
        with i18n.use(i18n.EN):
            english = _card(question, legs, dust=dust)
        shape = (question, [(leg.kind, leg.quantity) for leg in legs], dust)
        assert not any(m in english.text for m in GERMAN_MARKS), (shape, english.text)
        if english.question is not None:
            assert not any(m in english.question for m in GERMAN_MARKS), english.question
        # Only the words change: the card kind, the button and the answers are the German ones.
        assert english.model_dump(exclude={"text", "question", "id", "account_id"}) == (
            german.model_dump(exclude={"text", "question", "id", "account_id"})
        ), shape
        assert ("“Confirm”" in english.text) == ("„Bestätigen“" in german.text), shape


def test_the_swap_card_reads_naturally_in_both_languages():
    question = f"{QUESTION_PROGRAM}Prog…Ab12?"
    german = card_text(WalletCardKind.PROPOSED, GroupKind.SWAP, SWAP, question=question)
    assert german == (
        "Vorschlag von Hausbuch: 10 TOK gesendet (So1x…xxxx), 0,003 TOK erhalten (So1x…xxxx), "
        "Gebühr 0,000005 SOL. Das Programm Prog…Ab12 kennt Hausbuch nicht, liest die "
        "Transaktion an ihren Beträgen aber als einen Tausch. Stimmt das, „Bestätigen“."
    )
    with i18n.use(i18n.EN):
        english = card_text(WalletCardKind.PROPOSED, GroupKind.SWAP, SWAP, question=question)
    assert english == (
        "Hausbuch's suggestion: 10 TOK sent (So1x…xxxx), 0.003 TOK received (So1x…xxxx), "
        "Fee 0.000005 SOL. Hausbuch does not know the program Prog…Ab12, but reads the "
        "transaction by its amounts as a swap. If that is right, “Confirm”."
    )


def test_a_question_the_card_asks_is_shown_in_english():
    legs = [_leg(K.INTERNAL_TRANSFER_OUT, "-2", SOL), FEE]
    question = "Ist Stak…Cd34 Ihr eigenes Stake-Konto?"
    german = _card(question, legs)
    assert german.text == (
        "2 SOL gesendet (So1x…xxxx), Gebühr 0,000005 SOL. Ist Stak…Cd34 Ihr eigenes Stake-Konto?"
    )
    assert german.question == question
    with i18n.use(i18n.EN):
        english = _card(question, legs)
    assert english.text == (
        "2 SOL sent (So1x…xxxx), Fee 0.000005 SOL. Is Stak…Cd34 your own stake account?"
    )
    assert english.question == "Is Stak…Cd34 your own stake account?"


def test_the_dust_card_names_the_limit_in_euros():
    legs = [_leg(K.UNCATEGORIZED, "1")]
    german = _card(None, legs, dust=True)
    assert "weniger als 0,01 € wert" in german.text
    with i18n.use(i18n.EN):
        english = _card(None, legs, dust=True)
    assert "worth less than €0.01" in english.text


ERRORS = [
    wallet_groups._DONE,
    wallet_groups._NEEDS_ANSWER,
    wallet_groups._OUT_ONLY,
    wallet_groups._IN_ONLY,
    wallet_groups._SPAM_NATIVE,
    wallet_groups._SPAM_KNOWN,
    wallet_groups._COST_ONE_LEG,
    wallet_groups._LINKED,
    wallet_groups._NOT_ANSWERED,
    wallet_groups._UNDO_SPAM,
    wallet_groups._NOT_SPAM,
    wallet_groups._ALREADY_SPAM,
    wallet_groups._NATIVE_NOT_SPAM,
]


@pytest.mark.parametrize("message", ERRORS)
def test_every_answer_error_has_its_english(message):
    english = MESSAGES.find(message)
    assert english is not None, message
    assert not any(m in english for m in GERMAN_MARKS), english
