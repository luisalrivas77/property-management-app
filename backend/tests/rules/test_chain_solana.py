"""Phase 7 step 2: a Solana `getTransaction` record to its deltas and its movement group
(docs/phases/phase-7.md D181 (d), D183, D187, §6), replaying tests/fixtures/phase7/solana_tx.yaml
and the Solana cases of spam.yaml, plus the readings the fixtures do not pin (wrapped SOL, rent
paid by a third party, the leg index's uniqueness)."""

from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import fixture_models_phase7 as phase7
import pytest

from hausbuch.rules import errors as E
from hausbuch.rules.chain import normalise, solana
from hausbuch.rules.chain.decoders import public_decoders
from hausbuch.rules.household.assets import registry as asset_registry
from hausbuch.schemas.chain import AssetKey, Chain, GroupKind, ProviderLabel
from hausbuch.schemas.household import MovementKind, MovementSource

FIXTURE = phase7.load("solana_tx")
CASES = {c.id: c for c in FIXTURE.cases}
# The invented AMM of decoders.yaml `sol_swap`, the row a workspace holds (§6 (3)).
AMM_ROW = next(c.row for c in phase7.load("decoders").rows if c.id == "sol_swap")
A = "BU8oH89B6z89SV3HfU1FBa5p8aE43HhZz3F6BQBtjsJP"
F = "ANgPywB9VnrmKJ9oXqByisxMxB564F4CoDY4HWZd4Y5J"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def _lookup(listed: bool) -> solana.DecoderLookup:
    workspace = [AMM_ROW] if listed else []
    return solana.rows_lookup([*workspace, *public_decoders()])


def _classify(case: phase7.SolanaCase) -> solana.SolanaGroup:
    label = None
    if case.provider_label is not None:
        text = case.provider_label["description"]
        assert isinstance(text, str)
        label = ProviderLabel(provider="helius", text=text, fetched_at=datetime.now(UTC))
    return solana.classify_solana(case.tx, {case.own}, _lookup(case.decoder_listed), label=label)


@pytest.mark.parametrize("case", FIXTURE.cases, ids=lambda c: c.id)
def test_deltas(case):
    if case.pending:
        pytest.skip(case.pending)
    deltas = normalise.solana_deltas(case.tx, {case.own})
    got = [(d.asset_key.contract, d.units) for d in deltas]
    assert got == [(d.mint, d.units) for d in case.expect.deltas]
    assert all(d.asset_key.chain is Chain.SOLANA for d in deltas)


@pytest.mark.parametrize("case", FIXTURE.cases, ids=lambda c: c.id)
def test_group(case):
    if case.pending:
        pytest.skip(case.pending)
    group = _classify(case)
    assert group.status == case.expect.status
    assert group.kind is case.expect.group
    got = [(m.kind, m.role, m.units, m.asset_key.contract, m.index) for m in group.legs]
    want = [(m.kind, m.role, m.units, m.mint, m.index) for m in case.expect.movements]
    assert got == want
    assert group.card == case.expect.card
    assert (group.label.text if group.label else None) == case.expect.label
    assert group.tx_hash == case.tx["transaction"]["signatures"][0]
    assert group.slot == case.tx["slot"]
    assert group.fee_payer is (case.expect.fee_lamports is not None)


def test_the_fixture_has_no_step_2_case_left_pending():
    assert [c.id for c in FIXTURE.cases if c.pending] == []
    assert [c.id for c in FIXTURE.inflation_rewards if c.pending] == []


def test_a_swap_on_an_unlisted_program_asks_once_by_the_masked_program():
    group = _classify(CASES["swap_unlisted_program"])
    assert group.question == "Was war diese Transaktion bei 9ZgA…pyQw?"
    assert group.decoder is None and not group.decoder_listed
    assert group.program == "9ZgAf9GnSdybtfn3p65Z5GaqYrnjvN97XsUiAeiupyQw"
    listed = _classify(CASES["swap_listed_amm"])
    assert listed.question is None and listed.decoder == AMM_ROW


def test_a_contract_call_asks_by_the_masked_program():
    group = _classify(CASES["contract_call_shape"])
    assert group.question == "Was war diese Transaktion bei 9ZgA…pyQw?"


def test_the_stake_delegation_asks_whether_the_account_is_own():
    case = CASES["stake_delegation_card"]
    group = _classify(case)
    assert group.question == "Ist Hqw2…X9fF Ihr eigenes Stake-Konto?"
    assert group.stake_account == "Hqw2dGuE2dcLueqA4UFgPw1Uciu9C7q41pxeUWcxX9fF"
    assert group.decoder is not None and group.decoder.name == "Solana Stake"


def test_once_the_stake_account_is_own_the_delegation_moves_only_the_fee():
    case = CASES["stake_delegation_card"]
    own = {case.own, "Hqw2dGuE2dcLueqA4UFgPw1Uciu9C7q41pxeUWcxX9fF"}
    assert normalise.solana_deltas(case.tx, own) == []
    group = solana.classify_solana(case.tx, own, _lookup(False))
    assert group.kind is GroupKind.TRANSFER and group.fee_only
    assert [(m.role, m.units) for m in group.legs] == [("fee", 5000)]


def test_the_rent_of_the_recipients_account_is_part_of_the_fee():
    case = CASES["send_creating_recipient_account"]
    (sol, _usdc) = normalise.solana_deltas(case.tx, {case.own})
    assert sol.rent and sol.units == -2039280
    assert solana.foreign_rent(case.tx, {case.own}) == 2039280


def test_failed_and_fee_only_groups():
    assert _classify(CASES["failed_transaction"]).fee_only
    assert _classify(CASES["token_account_closed"]).fee_only
    assert not _classify(CASES["native_send_with_fee"]).fee_only


def test_a_listed_failed_record_is_failed_whatever_the_decoder():
    group = _classify(CASES["failed_transaction"])
    assert group.kind is GroupKind.FAILED and group.decoder is None


def test_the_draft_signs_and_scales_each_leg():
    case = CASES["swap_listed_amm"]
    group = _classify(case)
    sol, usdc = AssetKey(chain=Chain.SOLANA), AssetKey(chain=Chain.SOLANA, contract=USDC)
    ctx = solana.DraftContext(
        account_id=uuid4(),
        address_id=uuid4(),
        asset_ids={sol: uuid4(), usdc: uuid4()},
        decimals={usdc: 6},
    )
    draft = solana.to_draft(group, ctx)
    assert draft.kind is GroupKind.SWAP and draft.decoder == "Invented AMM"
    got = [(m.kind, m.quantity, m.index) for m in draft.movements]
    assert got == [
        (MovementKind.DISPOSAL, Decimal("-1"), "0"),
        (MovementKind.ACQUISITION, Decimal("150"), "0"),
        (MovementKind.FEE, Decimal("-0.000005"), "fee"),
    ]
    assert all(m.source is MovementSource.CHAIN for m in draft.movements)
    assert {m.tx_hash for m in draft.movements} == {group.tx_hash}
    with pytest.raises(ValueError, match="decimals"):
        solana.to_draft(group, ctx.__class__(ctx.account_id, ctx.address_id, ctx.asset_ids))


# --- inflation rewards (§6 (10)) ------------------------------------------------------------


@pytest.mark.parametrize("case", FIXTURE.inflation_rewards, ids=lambda c: c.id)
def test_inflation_reward(case):
    if case.pending:
        pytest.skip(case.pending)
    reward = solana.inflation_reward(case.stake_account, case.result, case.epoch_end)
    assert reward is not None
    assert reward.kind is case.expect.kind
    assert reward.units == case.expect.units
    assert reward.day == case.expect.on
    assert (reward.tx_hash, reward.index, reward.role) == ("epoch:820", "reward", "in")


def test_no_reward_that_epoch():
    at = datetime(2026, 6, 30, 22, 30, tzinfo=UTC)
    assert solana.inflation_reward("x", {"epoch": 1, "amount": 0}, at) is None


# --- spam.yaml's Solana cases (D187) --------------------------------------------------------

SPAM = {c.id: c for c in phase7.load("spam").cases}


def _credit(mint: str, *, signed: bool, decimals: int = 6) -> dict:
    """F sends a token to A's account of that mint (A signs too when `signed`)."""
    acct, theirs = (
        "Dd2nAL9RJk7UUMMSAWszC9oJTi3jGoPGpjStUZ3ZH8ZU",
        "41frPyR5ATwWweP1VzSdHDY82VbWy3Dt6mViHk9a1nD4",
    )
    keys = [
        {"pubkey": F, "signer": True, "writable": True, "source": "transaction"},
        {"pubkey": A, "signer": signed, "writable": False, "source": "transaction"},
        {"pubkey": theirs, "signer": False, "writable": True, "source": "transaction"},
        {"pubkey": acct, "signer": False, "writable": True, "source": "transaction"},
        {
            "pubkey": solana.TOKEN_PROGRAM,
            "signer": False,
            "writable": False,
            "source": "transaction",
        },
    ]

    def row(i, owner, amount):
        return {
            "accountIndex": i,
            "mint": mint,
            "owner": owner,
            "uiTokenAmount": {"amount": str(amount), "decimals": decimals},
        }

    return {
        "version": 0,
        "slot": 300009001,
        "blockTime": 1773100000,
        "transaction": {
            "signatures": ["5spamSignatureInventedForThisTestOnly111111111111111111111111111"],
            "message": {
                "accountKeys": keys,
                "instructions": [
                    {"programId": solana.TOKEN_PROGRAM, "parsed": {"type": "transferChecked"}}
                ],
            },
        },
        "meta": {
            "err": None,
            "fee": 5000,
            "preBalances": [10**9, 2039280, 2039280, 2039280, 1],
            "postBalances": [10**9 - 5000, 2039280, 2039280, 2039280, 1],
            "preTokenBalances": [row(2, F, 10**9), row(3, A, 0)],
            "postTokenBalances": [row(2, F, 10**9 - 10**6), row(3, A, 10**6)],
        },
    }


def test_symbol_lookalike_on_solana_is_spam():
    case = SPAM["symbol_lookalike_usdc_solana"]
    group = solana.classify_solana(_credit(case.token.contract, signed=False), {A}, _lookup(False))
    assert group.kind is case.expect.group
    assert [m.kind for m in group.legs] == [case.expect.movement]
    assert group.hidden is case.expect.hidden
    assert group.card is None and not case.expect.card
    assert asset_registry().token(Chain.SOLANA, case.token.contract) is None


def test_a_spam_mint_is_not_spam_once_held_or_signed():
    mint = SPAM["symbol_lookalike_usdc_solana"].token.contract
    held = solana.classify_solana(_credit(mint, signed=False), {A}, _lookup(False), held={mint})
    assert held.kind is GroupKind.TRANSFER and held.card == E.TRANSFER_UNMATCHED_LEG
    signed = solana.classify_solana(_credit(mint, signed=True), {A}, _lookup(False))
    assert signed.kind is GroupKind.TRANSFER and not signed.hidden
    usdc = solana.classify_solana(_credit(USDC, signed=False), {A}, _lookup(False))
    assert usdc.kind is GroupKind.TRANSFER


def test_a_listed_token_through_another_program_is_an_airdrop():
    tx = _credit(USDC, signed=False)
    tx["transaction"]["message"]["instructions"] = [{"programId": AMM_ROW.program, "data": "x"}]
    group = solana.classify_solana(tx, {A}, _lookup(False))
    assert group.kind is GroupKind.AIRDROP
    assert [m.kind for m in group.legs] == [MovementKind.AIRDROP]


def test_bought_in_a_swap_is_never_spam():
    case = SPAM["bought_in_swap_never_spam"]
    tx = deepcopy(CASES["swap_listed_amm"].tx)
    for key in ("preTokenBalances", "postTokenBalances"):
        for row in tx["meta"][key]:
            row["mint"] = case.token.contract
    group = solana.classify_solana(tx, {A}, _lookup(True))
    assert group.kind is case.expect.group and not group.hidden
    bought = [m for m in group.legs if m.role == "in"]
    assert [m.kind for m in bought] == [case.expect.movement]
    # D187 (4): unlisted, so it needs a price source („Kursquelle wählen“).
    assert asset_registry().token(Chain.SOLANA, case.token.contract) is None
    assert case.expect.price_source_needed


# --- wrapped SOL and rent paid by a third party (readings of step 2) ------------------------

WSOL_ACCOUNT = "Bm1xoaBWvXQFAZhEy8tUJYTGqbXrfz4cDAg3VuuvcPCS"


def _wsol(
    pre_lamports: int,
    post_lamports: int,
    pre_amount: int | None,
    post_amount: int | None,
    main_pre: int,
    main_post: int,
    ins: list[dict],
) -> dict:
    mint = solana.native_mint()

    def rows(amount):
        if amount is None:
            return []
        return [
            {
                "accountIndex": 1,
                "mint": mint,
                "owner": A,
                "uiTokenAmount": {"amount": str(amount), "decimals": 9},
            }
        ]

    return {
        "version": 0,
        "slot": 300009101,
        "blockTime": 1773200000,
        "transaction": {
            "signatures": ["3wrapSignatureInventedForThisTestOnly11111111111111111111111111"],
            "message": {
                "accountKeys": [
                    {"pubkey": A, "signer": True, "writable": True, "source": "transaction"},
                    {
                        "pubkey": WSOL_ACCOUNT,
                        "signer": False,
                        "writable": True,
                        "source": "transaction",
                    },
                    {
                        "pubkey": solana.SYSTEM_PROGRAM,
                        "signer": False,
                        "writable": False,
                        "source": "transaction",
                    },
                    {
                        "pubkey": solana.TOKEN_PROGRAM,
                        "signer": False,
                        "writable": False,
                        "source": "transaction",
                    },
                ],
                "instructions": ins,
            },
        },
        "meta": {
            "err": None,
            "fee": 5000,
            "preBalances": [main_pre, pre_lamports, 1, 1],
            "postBalances": [main_post, post_lamports, 1, 1],
            "preTokenBalances": rows(pre_amount),
            "postTokenBalances": rows(post_amount),
        },
    }


def test_wrapping_sol_into_an_existing_wsol_account_is_a_wrap():
    tx = _wsol(
        2039280,
        2039280 + 10**9,
        0,
        10**9,
        5 * 10**9,
        4 * 10**9 - 5000,
        [
            {
                "programId": solana.SYSTEM_PROGRAM,
                "parsed": {
                    "type": "transfer",
                    "info": {"source": A, "destination": WSOL_ACCOUNT, "lamports": 10**9},
                },
            },
            {
                "programId": solana.TOKEN_PROGRAM,
                "parsed": {"type": "syncNative", "info": {"account": WSOL_ACCOUNT}},
            },
        ],
    )
    deltas = normalise.solana_deltas(tx, {A})
    assert [(d.asset_key.contract, d.units) for d in deltas] == [
        (None, -(10**9)),
        (solana.native_mint(), 10**9),
    ]
    group = solana.classify_solana(tx, {A}, _lookup(False))
    assert group.kind is GroupKind.WRAP
    assert group.decoder is not None and group.decoder.name == "wSOL"
    assert [(m.kind, m.role, m.units) for m in group.legs] == [
        (MovementKind.INTERNAL_TRANSFER_OUT, "out", 10**9),
        (MovementKind.INTERNAL_TRANSFER_IN, "in", 10**9),
        (MovementKind.FEE, "fee", 5000),
    ]


def test_closing_a_wsol_account_unwraps_and_returns_the_rent():
    tx = _wsol(
        2039280 + 10**9,
        0,
        10**9,
        None,
        4 * 10**9,
        5 * 10**9 + 2039280 - 5000,
        [
            {
                "programId": solana.TOKEN_PROGRAM,
                "parsed": {
                    "type": "closeAccount",
                    "info": {"account": WSOL_ACCOUNT, "destination": A},
                },
            }
        ],
    )
    deltas = normalise.solana_deltas(tx, {A})
    assert [(d.asset_key.contract, d.units) for d in deltas] == [
        (None, 10**9),
        (solana.native_mint(), -(10**9)),
    ]
    group = solana.classify_solana(tx, {A}, _lookup(False))
    assert group.kind is GroupKind.WRAP and group.decoder is None
    assert [m.index for m in group.legs] == ["0", "0", "fee"]


def test_rent_a_third_party_paid_into_a_new_own_account_is_its_own_leg():
    case = CASES["first_spl_receive_creates_account"]
    tx = deepcopy(case.tx)
    keys = tx["transaction"]["message"]["accountKeys"]
    keys[0], keys[1] = keys[1], keys[0]  # F pays the fee and the rent, A signs nothing
    keys[0]["signer"], keys[1]["signer"] = True, False
    meta = tx["meta"]
    meta["preBalances"][0], meta["preBalances"][1] = 7499990000, 1000000000
    meta["postBalances"][0], meta["postBalances"][1] = 7499990000 - 2044280, 1000000000
    deltas = normalise.solana_deltas(tx, {A})
    assert [(d.asset_key.contract, d.units, d.rent) for d in deltas] == [
        (None, 2039280, True),
        (USDC, 25000000, False),
    ]
    group = solana.classify_solana(tx, {A}, _lookup(False))
    assert group.kind is GroupKind.TRANSFER
    assert [(m.kind, m.role, m.units, m.index) for m in group.legs] == [
        (MovementKind.UNCATEGORIZED, "in", 25000000, "1"),
        (MovementKind.UNCATEGORIZED, "in", 2039280, "rent"),
    ]


def test_two_legs_on_one_index_and_role_stay_apart():
    legs = [
        solana.SolanaLeg(AssetKey(chain=Chain.SOLANA), MovementKind.LP_DEPOSIT, "out", 1, "0"),
        solana.SolanaLeg(
            AssetKey(chain=Chain.SOLANA, contract=USDC), MovementKind.LP_DEPOSIT, "out", 2, "0"
        ),
    ]
    assert [m.index for m in solana._unique(legs)] == ["0", "0.2"]


def test_berlin_day_and_mask():
    assert solana.berlin_day(datetime(2026, 6, 30, 22, 30, tzinfo=UTC)) == date(2026, 7, 1)
    assert solana.mask(A) == "BU8o…jsJP"
