"""Phase 7 step 2: a swap group's value and question (docs/phases/phase-7.md D188 (4), §6 (3),
(4)), replaying tests/fixtures/phase7/swap_group.yaml's values and questions.

The cases keep their `pending:` marker: their `status` is rules/review.route_group's (task
"core") and `contract_call_answered_learns_decoder`'s learned row is services/chain/groups'
once that lands; this file asserts the value, the pricing asset and the question only."""

from datetime import date
from decimal import Decimal

import fixture_models_phase7 as phase7
import pytest

from hausbuch.rules.chain import solana
from hausbuch.rules.chain.swap import SwapLeg, price_question, swap_question, value_swap
from hausbuch.schemas.chain import GroupKind

FIXTURE = phase7.load("swap_group")
# The fixture's unlisted program (its header: 9ZgA…pyQw has no row) and its day.
UNLISTED = "9ZgAf9GnSdybtfn3p65Z5GaqYrnjvN97XsUiAeiupyQw"
DAY = date(2026, 3, 12)


def _legs(case) -> list[SwapLeg]:
    return [SwapLeg(asset=leg.asset, units=leg.units, role=leg.role) for leg in case.legs]


@pytest.mark.parametrize("case", FIXTURE.cases, ids=lambda c: c.id)
def test_value_and_question(case):
    legs = _legs(case)
    is_swap = case.expect.group is GroupKind.SWAP and (case.answer or len(legs) == 2)
    if is_swap:
        assert value_swap(legs, case.prices) == (case.expect.eur_amount, case.expect.priced_by)
    unread = not case.decoder_listed and case.answer is None
    program_question = f"Was war diese Transaktion bei {solana.mask(UNLISTED)}?" if unread else None
    question = (
        swap_question(program_question, legs, case.prices, DAY) if is_swap else program_question
    )
    assert question == case.expect.question


def test_nothing_is_valued_at_zero():
    legs = [SwapLeg("SOL", Decimal(1), "given"), SwapLeg("T", Decimal(150), "received")]
    assert value_swap(legs, {}) == (None, None)


def test_the_value_rounds_half_up_to_the_cent():
    legs = [SwapLeg("SOL", Decimal(1), "given"), SwapLeg("T", Decimal("0.5"), "received")]
    assert value_swap(legs, {"T": Decimal("0.01")}) == (Decimal("0.01"), "T")


def test_price_question_names_every_missing_asset():
    assert price_question([], DAY) is None
    assert price_question(["A", "B", "C"], DAY) == "Kurs fehlt für A, B und C am 12.03.2026"
