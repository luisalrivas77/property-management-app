"""rules/chain/dust (docs/phases/phase-7.md D262): the dust limit is rule data and the
comparison is exact; no price is never dust. Invented quantities and prices only."""

from copy import deepcopy
from decimal import Decimal

import fixture_models_phase7 as phase7
import pytest
from pydantic import ValidationError
from solana_chain_fake import dust_into_token_account

from hausbuch.rules.chain import solana
from hausbuch.rules.chain.decoders import public_decoders
from hausbuch.rules.chain.dust import DustRule, dust_rule, is_dust, parse
from hausbuch.schemas.chain import GroupKind
from hausbuch.schemas.household import MovementKind


def test_the_shipped_limit_is_one_cent():
    assert dust_rule() == DustRule(below_eur=Decimal("0.01"))


@pytest.mark.parametrize(
    ("units", "price", "dust"),
    [
        ("0.000000003", "150", True),
        ("0.00002", "150", True),  # 0.003 €
        ("0.0000666", "150", True),  # 0.00999 €
        ("0.00006666666666666667", "150", False),  # 0.0100000000000000005 €: not below
        ("1", "150", False),
        ("0.00002", None, False),  # no price: never guessed
        ("0", "150", False),
        ("-0.00002", "150", False),  # an outgoing leg is never dust
    ],
)
def test_is_dust(units, price, dust):
    assert is_dust(Decimal(units), None if price is None else Decimal(price)) is dust


def test_the_rule_refuses_a_limit_of_zero_or_unknown_keys():
    with pytest.raises(ValidationError):
        parse("below_eur: '0'")
    with pytest.raises(ValidationError):
        parse("below_eur: '0.01'\nchain: solana")


# --- the shape the Solana sync writes for a stranger's tiny receipt ------------------------

RECEIVE = next(c for c in phase7.load("solana_tx").cases if c.id == "native_receive")
UNKNOWN_PROGRAM = "Pois1nProgXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"  # base58, invented


def _tiny(through_program: bool) -> dict:
    """The fixture's plain SOL receipt cut to 20,000 lamports (0.00002 SOL), sent by the
    stranger who signs and pays; optionally through an unknown program's instruction."""
    tx = deepcopy(RECEIVE.tx)
    meta = tx["meta"]
    meta["postBalances"][0] = meta["preBalances"][0] - 20_000 - meta["fee"]
    meta["postBalances"][1] = meta["preBalances"][1] + 20_000
    message = tx["transaction"]["message"]
    message["instructions"][0]["parsed"]["info"]["lamports"] = 20_000
    if through_program:
        message["accountKeys"].append(
            {"pubkey": UNKNOWN_PROGRAM, "signer": False, "writable": False, "source": "transaction"}
        )
        message["instructions"] = [{"programId": UNKNOWN_PROGRAM, "data": "invented"}]
        meta["preBalances"].append(1)
        meta["postBalances"].append(1)
    return tx


@pytest.mark.parametrize("through_program", [False, True], ids=["system", "unknown_program"])
def test_a_tiny_receipt_is_classified_as_the_shape_the_dust_check_reads(through_program):
    """D262 reads a proposed transfer without a question whose only leg is one uncategorised
    receipt; a stranger's receipt, plain or batched through an unknown program, is that."""
    group = solana.classify_solana(
        _tiny(through_program), {RECEIVE.own}, solana.rows_lookup(public_decoders())
    )
    assert (group.kind, group.question) == (GroupKind.TRANSFER, None)
    assert [(leg.kind, leg.role, leg.units) for leg in group.legs] == [
        (MovementKind.UNCATEGORIZED, "in", 20_000)
    ]


# --- dust into an own token account (D186 (2) amended 18 Nov 2026) --------------------------

OWNER = RECEIVE.own
OWNER_USDC = "Dd2nAL9RJk7UUMMSAWszC9oJTi3jGoPGpjStUZ3ZH8ZU"  # the owner's USDC token account


def test_lamports_sent_to_an_own_token_account_join_the_sol_receipt():
    """A stranger sends 1 lamport to the owner and 1 lamport to the owner's own token account,
    with no token balance row: the SOL leg is the sum, 2 lamports, one proposed receipt in the
    shape D262 reads. Without the token account known, only the main account's lamport is
    seen, which left the address unreconciled by 1 lamport."""
    record = dust_into_token_account(OWNER, OWNER_USDC)
    lookup = solana.rows_lookup(public_decoders())
    group = solana.classify_solana(record, {OWNER}, lookup, own_token_accounts={OWNER_USDC})
    assert (group.kind, group.question) == (GroupKind.TRANSFER, None)
    assert [(leg.kind, leg.role, leg.units, leg.index) for leg in group.legs] == [
        (MovementKind.UNCATEGORIZED, "in", 2, "0")
    ]
    assert [d.units for d in solana.solana_deltas(record, {OWNER}, {OWNER_USDC})] == [2]
    assert [d.units for d in solana.solana_deltas(record, {OWNER})] == [1]


def test_a_token_account_its_rows_name_is_never_counted_twice():
    """`spl_in` names the owner's token account in its rows; knowing it as an own token
    account changes nothing (its lamports stay the rent the rows already account for)."""
    spl_in = next(c for c in phase7.load("solana_tx").cases if c.id == "spl_in")
    with_known = solana.solana_deltas(spl_in.tx, {spl_in.own}, {OWNER_USDC})
    assert with_known == solana.solana_deltas(spl_in.tx, {spl_in.own})


def test_the_sync_keeps_the_token_accounts_only_when_their_lamports_move_rowless():
    record = dust_into_token_account(OWNER, OWNER_USDC)
    assert solana.moves_rowless_token_account(record, {OWNER_USDC})
    assert not solana.moves_rowless_token_account(record, set())
    assert not solana.moves_rowless_token_account(RECEIVE.tx, {OWNER_USDC})
    stored = solana.with_token_accounts(record, {OWNER_USDC})
    assert solana.recorded_token_accounts(stored) == [OWNER_USDC]
    failed = deepcopy(record)
    failed["meta"]["err"] = {"InstructionError": [0, "Custom"]}
    assert not solana.moves_rowless_token_account(failed, {OWNER_USDC})
