"""Phase 6 step 1's pure rules against their fixtures (docs/phases/phase-6.md D157, D160):
ownership.yaml, merchant_pattern.yaml and the `inferred` cases of phase 2 rules.yaml. A case
whose `pending` names a later step is skipped; the step 1 cases run."""

from decimal import Decimal
from uuid import UUID, uuid5

import fixture_models_phase6 as phase6
import pytest
from fixture_models_phase2 import RulesFixture
from fixture_models_phase2 import load as load_phase2

from hausbuch.rules import matching
from hausbuch.rules.errors import RuleError
from hausbuch.rules.household import ownership
from hausbuch.schemas.allocation import (
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
    MatchContext,
    MatchTransaction,
    NameHistory,
    RuleMatcher,
)
from hausbuch.schemas.household import AccountOwnership, OwnershipShare

BUILT = "phase 6 step 1"


def skip_unless_built(pending: str | None) -> None:
    if pending and not pending.startswith(BUILT):
        pytest.skip(pending)


OWNERSHIP = phase6.load("ownership")
MERCHANTS = phase6.load("merchant_pattern")
RULES = load_phase2("rules")
assert isinstance(RULES, RulesFixture)

_NS = UUID("00000000-0000-4000-8000-0000000000a6")
_WS = UUID("0000000a-0000-4000-8000-000000000001")


def _owner(key: str) -> UUID:
    return uuid5(_NS, f"owner:{key}")


def _row(key: str) -> UUID:
    return uuid5(_NS, f"row:{key}")


@pytest.mark.rule
@pytest.mark.parametrize("case", OWNERSHIP.cases, ids=lambda c: c.id)
def test_ownership(case):
    skip_unless_built(case.pending)
    shares = [
        OwnershipShare(
            owner_id=_owner(s.owner),
            share_numerator=s.share_numerator,
            share_denominator=s.share_denominator,
            reason=s.reason,
        )
        for s in case.shares
    ]
    owners = [_owner(o) for o in case.owners]
    if case.expect.error is not None:
        with pytest.raises(RuleError) as refused:
            ownership.validate(shares, owners)
        assert refused.value.code == case.expect.error.code
        return
    ownership.validate(shares, owners)
    if case.expect.superseded is not None:
        account = uuid5(_NS, "account")
        current = [
            AccountOwnership(
                id=_row(r.id),
                workspace_id=_WS,
                account_id=account,
                owner_id=_owner(r.owner),
                share_numerator=r.share_numerator,
                share_denominator=r.share_denominator,
                reason=r.reason,
                effective_from="2026-01-01",
                superseded_by=_row("earlier") if r.superseded else None,
                created_at="2026-01-01T00:00:00Z",
            )
            for r in case.current
        ]
        assert ownership.supersede(current, shares) == [_row(k) for k in case.expect.superseded]


@pytest.mark.rule
@pytest.mark.parametrize("case", MERCHANTS.cases, ids=lambda c: c.id)
def test_merchant_pattern(case):
    skip_unless_built(case.pending)
    assert matching.merchant_pattern(case.name) == case.pattern


@pytest.mark.rule
@pytest.mark.parametrize("case", MERCHANTS.cases, ids=lambda c: c.id)
def test_a_name_rule_hits_the_name_it_was_learned_from(case):
    """D160's pattern replaces dots and digits by spaces, so rule_hits also reads the name
    normalised the same way; otherwise "^NETFLIX INTERNATIONAL B V" would never hit
    "Netflix International B.V." (step 1 finding)."""
    skip_unless_built(case.pending)
    matcher = RuleMatcher(counterparty_name_pattern=case.pattern, amount_sign="debit")
    tx = MatchTransaction(
        id=uuid5(_NS, case.id),
        booking_date="2026-02-14",
        amount=Decimal("-9.99"),
        counterparty_name=case.name,
        purpose="Kartenzahlung",
    )
    assert matching.rule_hits(matcher, tx)


def _confirmed(case) -> AllocationSet:
    tx = case.transaction
    if case.confirmed.category is not None:
        drafts = [AllocationDraft(amount=tx.amount, category=case.confirmed.category)]
    else:
        parts = case.confirmed.parts
        drafts = [AllocationDraft(amount=p.amount, category=p.category) for p in parts]
    return AllocationSet(
        transaction_id=tx.id,
        method=AllocationMethod.USER,
        confidence=Decimal("1.000"),
        allocations=drafts,
    )


@pytest.mark.rule
@pytest.mark.parametrize("case", RULES.inferred, ids=lambda c: c.id)
def test_infer_rule_on_a_private_account(case):
    skip_unless_built(case.pending)
    context = MatchContext(account_purpose=case.purpose)
    names = [
        NameHistory(
            transaction_id=h.transaction_id,
            counterparty_name=h.counterparty_name,
            category=h.category,
        )
        for h in case.history
    ]
    if case.expect_error is not None:
        with pytest.raises(RuleError) as refused:
            matching.infer_rule(case.transaction, _confirmed(case), context, names)
        assert refused.value.code == case.expect_error.code
        return
    got = matching.infer_rule(case.transaction, _confirmed(case), context, names)
    assert got == case.expect_rule
