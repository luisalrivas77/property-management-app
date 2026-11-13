"""Phase 6 step 0 schema guards (docs/phases/phase-6.md §11, D157, D165–D167, D175): shares
sum to exactly one, a security is named by its ISIN, a read movement names its document, a
quantity is a string to 10 places, a transfer has one leg each way, a valuation has a value
or a reason, and every rule of the later steps is still a stub."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from hausbuch.rules import exchange, valuation
from hausbuch.schemas.allocation import TransferLeg, TransferLegSource
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.household import (
    AssetCreate,
    AssetKind,
    AssetMovementDraft,
    DeclareHoldingsBody,
    MovementKind,
    MovementLine,
    MovementSource,
    OwnershipShare,
    Position,
    PriceSourceKind,
    SetOwnershipBody,
    TransferCandidate,
    TransferLinkCreate,
    TransferLinkKind,
    Valuation,
)

A, B = uuid.uuid4(), uuid.uuid4()


def _share(owner, numerator, denominator):
    return {"owner_id": owner, "share_numerator": numerator, "share_denominator": denominator}


# --- Ownership (D157) -------------------------------------------------------------------


def test_thirds_sum_to_exactly_one():
    body = SetOwnershipBody(shares=[_share(A, 1, 3), _share(B, 2, 3)])
    assert sum(s.share for s in body.shares) == 1


@pytest.mark.parametrize(
    "shares",
    [
        [_share(A, 1, 2)],
        [_share(A, 1, 2), _share(B, 1, 3)],
        [_share(A, 1, 2), _share(A, 1, 2)],
    ],
    ids=["half", "five_sixths", "owner_twice"],
)
def test_ownership_refused_unless_one(shares):
    with pytest.raises(ValidationError, match="ownership.sum_not_one"):
        SetOwnershipBody(shares=shares)


def test_a_share_is_at_most_one():
    with pytest.raises(ValidationError):
        OwnershipShare(**_share(A, 3, 2))


# --- Assets and movements (D165–D167, D175) ----------------------------------------------


def test_a_security_is_named_by_its_isin():
    with pytest.raises(ValidationError, match="ISIN"):
        AssetCreate(kind=AssetKind.ETF, symbol="MSCI", name="Beispiel MSCI World")
    assert AssetCreate(kind=AssetKind.ETF, isin="IE00BEISPL01", name="Beispiel MSCI World")
    assert AssetCreate(kind=AssetKind.CRYPTO, symbol="BTC", name="Bitcoin")


@pytest.mark.parametrize("isin", ["IE00BEISPL0", "ie00BEISPL01", "IE00BEISPL0X"])
def test_isin_shape(isin):
    with pytest.raises(ValidationError):
        AssetCreate(kind=AssetKind.ETF, isin=isin, name="x")


def _movement(**changes):
    values = {
        "account_id": uuid.uuid4(),
        "asset_id": uuid.uuid4(),
        "kind": MovementKind.ACQUISITION,
        "quantity": "12.345678",
        "eur_amount": "-1000.00",
        "occurred_at": datetime(2026, 5, 14, tzinfo=UTC),
        "source": MovementSource.TRADE_CONFIRMATION,
        "document_id": uuid.uuid4(),
    }
    return AssetMovementDraft(**(values | changes))


def test_a_read_movement_names_its_document():
    assert _movement().quantity == Decimal("12.345678")
    with pytest.raises(ValidationError, match="names its document"):
        _movement(document_id=None)
    assert _movement(source=MovementSource.CSV, document_id=None)


def test_a_quantity_is_a_string_to_ten_places():
    with pytest.raises(ValidationError):
        _movement(quantity=12.345678)
    with pytest.raises(ValidationError):
        _movement(quantity="0.00000000001")
    with pytest.raises(ValidationError):
        _movement(fee_quantity="-0.0001")


def test_a_withdrawal_keeps_what_phase_7_needs():
    withdrawal = _movement(
        kind=MovementKind.INTERNAL_TRANSFER_OUT,
        quantity="-0.005",
        eur_amount=None,
        fee_quantity="0.00002",
        external_ref="BX-0004",
        network="Bitcoin",
        source=MovementSource.CSV,
        document_id=None,
    )
    assert (withdrawal.fee_quantity, withdrawal.network) == (Decimal("0.00002"), "Bitcoin")


def test_an_export_row_names_its_asset_by_symbol():
    with pytest.raises(ValidationError):
        MovementLine(
            occurred_at=datetime(2026, 3, 2, tzinfo=UTC), type="Buy", asset="btc", quantity="1"
        )


def test_each_asset_is_typed_once():
    asset = uuid.uuid4()
    with pytest.raises(ValidationError, match="each asset once"):
        DeclareHoldingsBody(
            balances=[{"asset_id": asset, "units": "1"}, {"asset_id": asset, "units": "2"}]
        )


# --- Transfers (D162) ---------------------------------------------------------------------


def _leg(amount, account=None):
    return TransferLeg(
        source=TransferLegSource.TRANSACTION,
        id=uuid.uuid4(),
        account_id=account or uuid.uuid4(),
        account_kind=AccountKind.BANK_CURRENT,
        booked_on=date(2026, 3, 2),
        amount=amount,
    )


def test_a_transfer_goes_out_of_one_account_into_another():
    assert TransferCandidate(
        out_leg=_leg("-100.00"), in_leg=_leg("100.00"), kind=TransferLinkKind.BANK
    )
    with pytest.raises(ValidationError, match="out_leg is negative"):
        TransferCandidate(
            out_leg=_leg("100.00"), in_leg=_leg("-100.00"), kind=TransferLinkKind.BANK
        )
    same = uuid.uuid4()
    with pytest.raises(ValidationError, match="two different accounts"):
        TransferCandidate(
            out_leg=_leg("-100.00", same), in_leg=_leg("100.00", same), kind=TransferLinkKind.BANK
        )


def test_a_link_by_hand_has_one_leg_each_way():
    with pytest.raises(ValidationError, match="out_transaction_id"):
        TransferLinkCreate(
            kind=TransferLinkKind.BANK,
            out_transaction_id=uuid.uuid4(),
            out_movement_id=uuid.uuid4(),
            in_transaction_id=uuid.uuid4(),
        )


# --- Valuations (D167) --------------------------------------------------------------------


def test_a_valuation_has_a_value_or_a_reason():
    position = Position(account_id=uuid.uuid4(), asset_id=uuid.uuid4(), units="1")
    with pytest.raises(ValidationError):
        Valuation(position=position, on=date(2026, 6, 30))
    assert Valuation(
        position=position, on=date(2026, 6, 30), not_valued_reason="valuation.not_valued"
    )


def test_every_price_source_has_a_place_in_the_precedence():
    assert set(valuation.SOURCE_PRECEDENCE) == set(PriceSourceKind)
    assert valuation.SOURCE_PRECEDENCE[0] is PriceSourceKind.TRADE
    assert valuation.SOURCE_PRECEDENCE[-2:] == (PriceSourceKind.USER, PriceSourceKind.ECB_FX)


# --- The later steps' rules are stubs ----------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda: exchange.classify(None),
        lambda: exchange.movement_hash(uuid.uuid4(), None, 0),
        lambda: valuation.price_on([], date(2026, 6, 30)),
        lambda: valuation.value(None, None, date(2026, 6, 30)),
    ],
    ids=["classify", "movement_hash", "price_on", "value"],
)
def test_stub_until_its_step(call):
    with pytest.raises(NotImplementedError, match="phase 6 step"):
        call()
