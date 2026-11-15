"""Phase 7 step 0 widens enums the existing endpoints read (docs/phases/phase-7.md §11, §12);
nothing here may reach a person before the step that builds it: a link by hand never takes a
chain kind (step 5), and an exchange movement is never answered with a chain kind. A wallet
account was refused here until step 1 (15 Nov 2026, D179) built it: it is taken now as a
private account with no IBAN and no number, and the guard checks the refusals that stay."""

from decimal import Decimal

import pytest
from api_support import ok

from hausbuch.rules.matching import transfer_link_kind
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.household import MovementKind, TransferLinkKind
from hausbuch.services.exchange.imports import answers_for

CHAIN_LINK_KINDS = {TransferLinkKind.CHAIN, TransferLinkKind.BRIDGE, TransferLinkKind.WRAP}
CHAIN_MOVEMENT_KINDS = {
    MovementKind.AIRDROP,
    MovementKind.SPAM,
    MovementKind.LP_DEPOSIT,
    MovementKind.LP_WITHDRAWAL,
}


@pytest.mark.db
def test_a_wallet_account_is_taken_since_step_1(client_a):
    """Step 1 (D179 (1)): a wallet is a private account named by its provider, with no IBAN
    and no number; a rental purpose, a number or a missing provider are still refused."""
    body = {
        "label": "Hardware-Wallet",
        "bank": "Testwallet",
        "kind": "wallet",
        "provider": "Testwallet",
        "opening_balance": "0.00",
        "opening_balance_on": "2026-03-31",
    }
    for refused_body in (
        body | {"purpose": "rental"},
        body | {"external_ref": "12345678"},
        {k: v for k, v in body.items() if k != "provider"},
    ):
        refused = ok(client_a.post("/api/accounts", json=refused_body), 422)
        assert refused["code"] == "account.kind_not_available"
    assert ok(client_a.get("/api/accounts")) == []

    created = ok(client_a.post("/api/accounts", json=body), 201)
    assert created["kind"] == "wallet"
    assert created["purpose"] == "private"
    assert created["provider"] == "Testwallet"
    assert created["iban_masked"] is None
    assert created["external_ref_masked"] is None
    # D179: a wallet stays private.
    turned = ok(client_a.patch(f"/api/accounts/{created['id']}", json={"purpose": "rental"}), 422)
    assert turned["code"] == "account.kind_not_available"


@pytest.mark.db
def test_a_wallet_keeps_a_provider_on_create_and_patch(client_a):
    """D179 (review of PR #374): a wallet's provider is required and not blank after trimming,
    at create and on PATCH; a card's provider may still be cleared with null."""
    body = {
        "label": "Hardware-Wallet",
        "bank": "Testwallet",
        "kind": "wallet",
        "provider": "Testwallet",
        "opening_balance": "0.00",
        "opening_balance_on": "2026-03-31",
    }
    refused = ok(client_a.post("/api/accounts", json=body | {"provider": " "}), 422)
    assert refused["code"] == "account.kind_not_available"
    assert ok(client_a.get("/api/accounts")) == []

    wallet = ok(client_a.post("/api/accounts", json=body), 201)
    for provider in (None, "  "):
        refused = ok(
            client_a.patch(f"/api/accounts/{wallet['id']}", json={"provider": provider}), 422
        )
        assert refused["code"] == "account.kind_not_available"
    assert ok(client_a.get(f"/api/accounts/{wallet['id']}"))["provider"] == "Testwallet"
    renamed = ok(client_a.patch(f"/api/accounts/{wallet['id']}", json={"provider": "Tangem"}))
    assert renamed["provider"] == "Tangem"

    card = ok(
        client_a.post(
            "/api/accounts",
            json=body | {"label": "Kreditkarte", "kind": "credit_card", "provider": "Testbank"},
        ),
        201,
    )
    cleared = ok(client_a.patch(f"/api/accounts/{card['id']}", json={"provider": None}))
    assert cleared["provider"] is None


def test_no_pair_of_account_kinds_gives_a_chain_link():
    """services.transfers links by hand only the kind transfer_link_kind gives the accounts."""
    given = {transfer_link_kind(a, b) for a in AccountKind for b in AccountKind}
    assert not given & CHAIN_LINK_KINDS


@pytest.mark.parametrize("quantity", [Decimal("1"), Decimal("-1")])
def test_an_exchange_movement_is_never_answered_with_a_chain_kind(quantity):
    assert not set(answers_for(quantity)) & CHAIN_MOVEMENT_KINDS
