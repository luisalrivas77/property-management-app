"""The wallet card's promise (docs/phases/phase-7.md D262): a card whose text asks for
„Bestätigen“ carries the button (`confirmable`), and every card with legs offers at least one
action. Luis: „Also for 2 I had already clicked confirm but it still shows in the list“ (a
swap card said „Stimmt das, „Bestätigen““ and had no button). The one known exception is a
card whose legs go both ways with no answer that fits them („Tausch“ is not built, Q-O163).
Pure: every shape of question × legs, no database."""

import itertools
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from hausbuch.rules.chain.solana import QUESTION_PROGRAM, QUESTION_UNKNOWN, QUESTION_UNREADABLE
from hausbuch.rules.chain.utxo import FOREIGN_INPUTS_QUESTION, NO_MOVEMENT_QUESTION
from hausbuch.schemas.chain import GroupKind
from hausbuch.schemas.household import MovementKind, MovementStatus
from hausbuch.schemas.wallet_cards import WalletCardKind, WalletLeg
from hausbuch.services.wallet_groups import _to_card, card_text, confirmable

SOL, TOKEN = uuid.uuid4(), uuid.uuid4()
AT = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

QUESTIONS = [
    None,
    f"{QUESTION_PROGRAM}Prog…Ab12?",
    "Ist Stak…Cd34 Ihr eigenes Stake-Konto?",
    QUESTION_UNKNOWN,
    QUESTION_UNREADABLE,
    FOREIGN_INPUTS_QUESTION,
    NO_MOVEMENT_QUESTION,
]


def _leg(kind: MovementKind, units: str, asset=TOKEN) -> WalletLeg:
    return WalletLeg(
        movement_id=uuid.uuid4(),
        address_masked="So1x…xxxx",
        asset_id=asset,
        symbol="SOL" if asset == SOL else "TOK",
        kind=kind,
        status=MovementStatus.PROPOSED,
        quantity=Decimal(units),
    )


K = MovementKind
FEE = _leg(K.FEE, "-0.000005", SOL)
LEG_SETS = [
    [],
    [_leg(K.UNCATEGORIZED, "1")],
    [_leg(K.UNCATEGORIZED, "-1"), FEE],
    [_leg(K.INTERNAL_TRANSFER_IN, "1")],
    [_leg(K.INTERNAL_TRANSFER_OUT, "-2", SOL), FEE],
    [_leg(K.DISPOSAL, "-10"), _leg(K.ACQUISITION, "0.003"), FEE],
    [_leg(K.UNCATEGORIZED, "-10"), _leg(K.UNCATEGORIZED, "0.003"), FEE],
    [_leg(K.UNCATEGORIZED, "-10"), _leg(K.ACQUISITION, "0.003")],
    [_leg(K.AIRDROP, "1000")],
]


def _both_ways(legs: list[WalletLeg]) -> bool:
    moving = [leg for leg in legs if leg.kind is not K.FEE]
    return any(leg.quantity > 0 for leg in moving) and any(leg.quantity < 0 for leg in moving)


def test_a_card_that_asks_for_bestaetigen_has_the_button_and_every_card_an_action():
    exceptions = []
    for question, legs, kind, dust in itertools.product(
        QUESTIONS, LEG_SETS, [GroupKind.SWAP, GroupKind.TRANSFER], [False, True]
    ):
        # Dust is decided by the database for a single uncategorised receipt only.
        if dust and (question is not None or len(legs) != 1 or legs[0].kind is not K.UNCATEGORIZED):
            continue
        # The card exactly as the API builds it (wallet_groups._to_card).
        row = SimpleNamespace(
            id=uuid.uuid4(),
            account_id=uuid.uuid4(),
            chain="solana",
            kind=kind.value,
            status="proposed",
            occurred_at=AT,
            question=question,
        )
        built = _to_card(row, legs, set(), dust)
        shape = (question, [(leg.kind, leg.quantity) for leg in legs], kind, dust)
        if "„Bestätigen“" in built.text:
            assert built.confirmable, shape
        assert built.confirmable == confirmable(question, [leg.kind for leg in legs], dust=dust)
        if built.card is WalletCardKind.PROPOSED and not built.confirmable and not built.answers:
            assert _both_ways(legs), shape  # Q-O163: no answer for a two-way card yet
            exceptions.append(shape)
    # The exception is real and named, not a silent gap: two-way cards with no fitting answer.
    assert exceptions


def test_the_swap_with_its_program_question_is_confirmable_and_says_so():
    legs = LEG_SETS[5]
    question = f"{QUESTION_PROGRAM}Prog…Ab12?"
    assert confirmable(question, [leg.kind for leg in legs])
    text = card_text(WalletCardKind.PROPOSED, GroupKind.SWAP, legs, question=question)
    assert text == (
        "Vorschlag von Hausbuch: 10 TOK gesendet (So1x…xxxx), 0,003 TOK erhalten (So1x…xxxx), "
        "Gebühr 0,000005 SOL. Das Programm Prog…Ab12 kennt Hausbuch nicht, liest die "
        "Transaktion an ihren Beträgen aber als einen Tausch. Stimmt das, „Bestätigen“."
    )


def test_other_questions_are_never_confirmed_by_the_button():
    legs = LEG_SETS[4]
    for question in ["Ist Stak…Cd34 Ihr eigenes Stake-Konto?", QUESTION_UNKNOWN]:
        assert not confirmable(question, [leg.kind for leg in legs])
