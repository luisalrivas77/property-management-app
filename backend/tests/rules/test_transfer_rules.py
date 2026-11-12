"""Internal transfers between own accounts (docs/phases/phase-6.md D162; fixture
tests/fixtures/phase6/transfers.yaml), replayed on rules.matching.match_transfer. The fixture
model recomputes each case on load; this test checks the shipped rule gives the same."""

import fixture_models_phase6 as phase6
import pytest

from hausbuch.rules import matching
from hausbuch.schemas.allocation import MatchContext, TransferLeg

TRANSFERS = phase6.load("transfers")
BUILT = "phase 6 step 2"


def _leg(row: phase6.LegRow) -> TransferLeg:
    account = TRANSFERS.accounts[row.account]
    return TransferLeg(
        source=row.source,
        id=row.id,
        account_id=account.id,
        account_kind=account.kind,
        account_iban_hmac=account.iban_hmac,
        booked_on=row.booked_on,
        amount=row.amount,
        fee_eur=row.fee_eur,
        counterparty_iban_hmac=row.counterparty_iban_hmac,
        category=row.booked_as,
    )


@pytest.mark.rule
@pytest.mark.parametrize("case", TRANSFERS.cases, ids=lambda c: c.id)
def test_match_transfer(case):
    if case.pending and not case.pending.startswith(BUILT):
        pytest.skip(case.pending)
    own = TRANSFERS.accounts[case.leg.account]
    context = MatchContext(
        account_purpose=own.purpose,
        own_account_hmacs=sorted(
            a.iban_hmac
            for key, a in TRANSFERS.accounts.items()
            if a.iban_hmac is not None and key != case.leg.account
        ),
        open_legs=[_leg(o) for o in case.open_legs],
    )
    got = matching.match_transfer(_leg(case.leg), context, today=case.today)
    expect = case.expect
    assert got.category == expect.category
    assert got.confidence == expect.confidence
    for name in ("link", "proposed_link"):
        found, wanted = getattr(got, name), getattr(expect, name)
        if wanted is None:
            assert found is None, name
        else:
            assert (found.out_leg.id, found.in_leg.id, found.kind) == (
                wanted.out_leg,
                wanted.in_leg,
                wanted.kind,
            ), name
    assert got.card == (expect.card.code if expect.card else None)
    assert got.candidates == sorted(expect.candidates)


@pytest.mark.rule
def test_the_window_counts_banking_days():
    from datetime import date

    # Friday + 3 banking days = Wednesday; the weekend does not count.
    assert matching.transfer_window_end(date(2026, 3, 6)) == date(2026, 3, 11)
