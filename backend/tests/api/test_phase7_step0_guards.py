"""Phase 7 step 0 widens enums the existing endpoints read (docs/phases/phase-7.md §11, §12);
nothing here may reach a person before the step that builds it: a wallet account is still
refused (step 1), a link by hand never takes a chain kind (step 5), and an exchange movement
is never answered with a chain kind."""

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
def test_a_wallet_account_is_still_refused(client_a):
    refused = ok(
        client_a.post(
            "/api/accounts",
            json={
                "label": "Hardware-Wallet",
                "bank": "Testwallet",
                "kind": "wallet",
                "opening_balance": "0.00",
                "opening_balance_on": "2026-03-31",
            },
        ),
        422,
    )
    assert refused["code"] == "account.kind_not_available"
    assert ok(client_a.get("/api/accounts")) == []


def test_no_pair_of_account_kinds_gives_a_chain_link():
    """services.transfers links by hand only the kind transfer_link_kind gives the accounts."""
    given = {transfer_link_kind(a, b) for a in AccountKind for b in AccountKind}
    assert not given & CHAIN_LINK_KINDS


@pytest.mark.parametrize("quantity", [Decimal("1"), Decimal("-1")])
def test_an_exchange_movement_is_never_answered_with_a_chain_kind(quantity):
    assert not set(answers_for(quantity)) & CHAIN_MOVEMENT_KINDS
