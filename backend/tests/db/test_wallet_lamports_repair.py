# ruff: noqa: F811 - the sync test's fixtures (chain, wallet, sync) are imported and used by name
"""Phase 7 D186 (2) amended 18 Nov 2026: lamports a stranger sent to an own token account,
read again from the stored originals (services/chain/lamports_repair, `hausbuch
repair-solana-lamports`).

The first sync runs with the reading before the amendment (the trigger patched off), as the
live address was read: the dust's main-account lamport is booked, the token account's is not,
and the address is unreconciled by 1 lamport. The receipt is confirmed as a Kleinstbetrag
(D262). The repair then reads the stored originals again, never the provider, and supersedes
that group with a confirmed 2-lamport Kleinstbetrag; the next sync reconciles, and a second
repair changes nothing. Every key, signature, amount and price is invented."""

from datetime import date
from decimal import Decimal

import pytest
from db.test_services_holdings import _price
from db.test_wallet_sync_solana import (
    A_USDC,
    FIRST,
    SETTINGS,
    A,
    _record,
    _rows,
    _set_balance,
    _sums,
    chain,  # noqa: F401 - the fixtures, used by name
    sync,  # noqa: F401
    wallet,  # noqa: F401
)
from solana_chain_fake import dust_into_token_account, signature

from hausbuch.rules.chain import solana
from hausbuch.schemas.chain import WalletSyncStatus
from hausbuch.services.chain.lamports_repair import (
    CHAIN_REREAD,
    LamportsRepaired,
    repair_token_account_lamports,
)
from hausbuch.services.wallet_groups import PERSON, confirm_group

pytestmark = pytest.mark.db

DUST_DAY = date(2026, 3, 27)  # the record's block time, 27 Mar 2026 in Berlin
SOL_PRICE = "150"  # 2 lamports are far below 0.01 € at any plausible price


def _dust() -> dict:
    return dust_into_token_account(A, A_USDC, slot=300_000_950)


def _live(dbname, account, tx_hash):
    return _rows(
        dbname,
        "SELECT g.id, g.kind, g.status, m.kind, m.status, m.quantity, m.eur_amount,"
        " m.movement_hash IS NULL, m.document_id"
        " FROM movement_group g JOIN asset_movement m ON m.group_id = g.id"
        " WHERE g.account_id = %s AND g.tx_hash = %s"
        " AND g.superseded_by IS NULL AND m.superseded_by IS NULL",
        account,
        tx_hash,
    )


def _counts(dbname, account):
    return _rows(
        dbname,
        "SELECT (SELECT count(*) FROM movement_group WHERE account_id = %s),"
        " (SELECT count(*) FROM asset_movement WHERE account_id = %s),"
        " (SELECT count(*) FROM event), (SELECT count(*) FROM document)",
        account,
        account,
    )[0]


def _old_reading(monkeypatch):
    """The reading before the amendment: an own token account's lamports are never counted
    when no token row names it, and nothing is kept beside such a record."""
    monkeypatch.setattr(solana, "_rowless_own_token_accounts", lambda *a, **k: [])
    monkeypatch.setattr(solana, "moves_rowless_token_account", lambda *a, **k: False)


def test_the_repair_carries_the_kleinstbetrag_to_both_lamports(
    dbname,
    chain,
    wallet,
    sync,
    run,
    blob_store,
    monkeypatch,
):
    ws, account, a = wallet
    for case_id in FIRST:
        chain.add(_record(case_id))
    dust = _dust()
    chain.add(dust)
    sol, usdc = _sums(*FIRST)
    _set_balance(chain, sol + 2, usdc)  # the chain holds both lamports
    sig = signature(dust)

    with monkeypatch.context() as patched:
        _old_reading(patched)
        first = sync(ws, a)
    assert first.status is WalletSyncStatus.SUCCEEDED and first.reconciled is False
    (difference,) = _rows(
        dbname,
        "SELECT difference FROM wallet_sync WHERE address_id = %s ORDER BY started_at DESC LIMIT 1",
        a,
    )
    assert difference[0] == {"native": "1"}
    [(old_group, *_)] = _live(dbname, account, sig)

    # the person books the stranger's receipt as a Kleinstbetrag (D262)
    (sol_id,) = _rows(
        dbname,
        "SELECT id FROM asset WHERE workspace_id = %s AND chain = 'solana'"
        " AND contract_address IS NULL",
        ws,
    )[0]
    run(ws, lambda ctx: _price(ctx, sol_id, DUST_DAY, SOL_PRICE))
    run(ws, confirm_group, old_group)
    [(dust_group, kind, status, leg_kind, leg_status, quantity, eur, _, _)] = _live(
        dbname, account, sig
    )
    assert (kind, status, leg_kind, leg_status, quantity, eur) == (
        "transfer",
        "confirmed",
        "acquisition",
        "confirmed",
        Decimal("0.000000001"),
        Decimal("0"),
    )

    requests = len(chain.requests)
    dry = run(
        ws, repair_token_account_lamports, blob_store, hmac_key=SETTINGS.iban_hmac_key, dry_run=True
    )
    assert (dry.records, dry.carried, dry.proposed) == (1, 1, 0)
    assert _live(dbname, account, sig)[0][0] == dust_group  # a dry run writes nothing

    done = run(ws, repair_token_account_lamports, blob_store, hmac_key=SETTINGS.iban_hmac_key)
    assert done == LamportsRepaired(addresses=1, records=1, carried=1)
    assert len(chain.requests) == requests  # the stored originals only, never the provider (D192)
    [(new_group, kind, status, leg_kind, leg_status, quantity, eur, no_hash, document)] = _live(
        dbname, account, sig
    )
    assert new_group != dust_group
    assert (kind, status, leg_kind, leg_status, quantity, eur, no_hash) == (
        "transfer",
        "confirmed",
        "acquisition",
        "confirmed",
        Decimal("0.000000002"),
        Decimal("0"),
        True,
    )
    (superseded,) = _rows(
        dbname, "SELECT superseded_by FROM movement_group WHERE id = %s", dust_group
    )
    assert superseded[0] == new_group
    # the person's answer is carried: the group is confirmed under routing.person
    (reason,) = _rows(
        dbname,
        "SELECT payload->>'reason' FROM event WHERE entity_id = %s"
        " AND type = 'movement_group.confirmed'",
        new_group,
    )
    assert reason[0] == PERSON
    # the corrected movement points at a stored original that replays it without a provider
    (subtype,) = _rows(dbname, "SELECT subtype FROM document WHERE id = %s", document)
    assert subtype[0] == "chain_sync"

    # the next sync reads nothing new, stores no second leg and reconciles
    after = sync(ws, a)
    assert (after.status, after.movements_new, after.reconciled) == (
        WalletSyncStatus.SUCCEEDED,
        0,
        True,
    )
    assert len(_live(dbname, account, sig)) == 1

    # a second repair finds the live leg equal to the reading and changes nothing
    before = _counts(dbname, account)
    again = run(ws, repair_token_account_lamports, blob_store, hmac_key=SETTINGS.iban_hmac_key)
    assert again == LamportsRepaired(addresses=1, records=1, unchanged=1)
    assert _counts(dbname, account) == before
    assert _live(dbname, account, sig)[0][0] == new_group


def test_a_receipt_not_yet_confirmed_is_proposed_with_both_lamports(
    dbname,
    chain,
    wallet,
    sync,
    run,
    blob_store,
    monkeypatch,
):
    """Without the person's confirmation there is nothing to carry: the corrected group is
    proposed as the classifier reads it now (the shape D262's „Bestätigen“ reads)."""
    ws, account, a = wallet
    for case_id in FIRST:
        chain.add(_record(case_id))
    dust = _dust()
    chain.add(dust)
    sol, usdc = _sums(*FIRST)
    _set_balance(chain, sol + 2, usdc)
    with monkeypatch.context() as patched:
        _old_reading(patched)
        sync(ws, a)
    done = run(ws, repair_token_account_lamports, blob_store, hmac_key=SETTINGS.iban_hmac_key)
    assert done == LamportsRepaired(addresses=1, records=1, proposed=1)
    [(group, kind, status, leg_kind, leg_status, quantity, eur, _, _)] = _live(
        dbname, account, signature(dust)
    )
    assert (kind, status, leg_kind, leg_status, quantity, eur) == (
        "transfer",
        "proposed",
        "uncategorized",
        "proposed",
        Decimal("0.000000002"),
        None,
    )
    (reason,) = _rows(
        dbname,
        "SELECT payload->>'reason' FROM event WHERE entity_id = %s"
        " AND type = 'movement_group.proposed'",
        group,
    )
    assert reason[0] == CHAIN_REREAD
    assert sync(ws, a).reconciled is True


def test_an_address_read_with_the_amendment_needs_no_repair(
    dbname,
    chain,
    wallet,
    sync,
    run,
    blob_store,
):
    ws, account, a = wallet
    for case_id in FIRST:
        chain.add(_record(case_id))
    dust = _dust()
    chain.add(dust)
    sol, usdc = _sums(*FIRST)
    _set_balance(chain, sol + 2, usdc)
    assert sync(ws, a).reconciled is True
    before = _counts(dbname, account)
    done = run(ws, repair_token_account_lamports, blob_store, hmac_key=SETTINGS.iban_hmac_key)
    assert done == LamportsRepaired(addresses=1, records=1, unchanged=1)
    assert _counts(dbname, account) == before
