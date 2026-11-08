"""Phase 2 done criterion (§13), pending until the matching, review, allocation and statement
rules exist; then these must pass unchanged."""

from decimal import Decimal

import pytest
from fixture_models_phase2 import MonthStatementFixture, RepeatFixture, load

from hausbuch.schemas.allocation import (
    AllocationSet,
    HistoryLine,
    HistoryRow,
    MatchContext,
    MatchingRule,
    MatchTransaction,
    RoutingDecision,
    RoutingInput,
)
from hausbuch.schemas.bank import ReconcileStatus
from hausbuch.schemas.enums import AllocationStatus, ImportStatus

matching = pytest.importorskip("hausbuch.rules.matching", reason="phase 2 rule not implemented")
review = pytest.importorskip("hausbuch.rules.review", reason="phase 2 rule not implemented")
allocations = pytest.importorskip(
    "hausbuch.rules.allocations", reason="phase 2 rule not implemented"
)
statements = pytest.importorskip("hausbuch.rules.statements", reason="phase 2 rule not implemented")
MONTH = load("month_statement")
assert isinstance(MONTH, MonthStatementFixture)
REPEAT = load("repeat_answer")
assert isinstance(REPEAT, RepeatFixture)

MATCHERS = ("owner_transfer", "rent", "loan", "document")


def _match(
    tx: MatchTransaction, context: MatchContext, rules: list[MatchingRule]
) -> AllocationSet | None:
    for rule in rules:
        if found := matching.apply_rule(rule, tx, context):
            return found
    for name in MATCHERS:
        if found := getattr(matching, f"match_{name}")(tx, context):
            return found
    return None


def _run(rules: list[MatchingRule]) -> list[tuple[AllocationSet | None, object]]:
    out = []
    for line in MONTH.lines:
        found = _match(line.transaction, MONTH.context, rules)
        if found is not None:
            allocations.check_sum(line.transaction.amount, found.allocations)  # zero errors
        routing = review.route(
            RoutingInput(import_status=ImportStatus.RECONCILED, allocation_set=found)
        )
        out.append((found, routing))
    return out


@pytest.mark.rule
def test_month_reconciles():
    """The month is the account's first import after the anchor: nothing stored before it."""
    b = MONTH.balances
    result = statements.reconcile(
        b.anchor,
        Decimal("0.00"),
        sum(line.transaction.amount for line in MONTH.lines),
        b.declared_closing,
        None,
        anchor_on=b.anchor_on,
        first_booking_on=b.first_booking_on,
    )
    assert result.status is ReconcileStatus.RECONCILED


@pytest.mark.rule
def test_first_month_without_rules():
    results = _run([])
    for line, (found, routing) in zip(MONTH.lines, results, strict=True):
        exp = line.first_month
        assert (routing.decision, routing.reason) == (exp.decision, exp.reason), line.id
        assert (found.method if found else None) == exp.method, line.id
    auto = sum(r.decision is RoutingDecision.AUTO_CONFIRM for _, r in results)
    assert auto == MONTH.expect.first_month_auto


@pytest.mark.rule
def test_second_month_with_learned_rules():
    results = _run(MONTH.rules)
    for line, (found, routing) in zip(MONTH.lines, results, strict=True):
        exp = line.second_month
        assert (routing.decision, routing.reason) == (exp.decision, exp.reason), line.id
        assert (found.method if found else None) == exp.method, line.id
        got = sorted((a.category, a.amount) for a in found.allocations) if found else []
        assert got == sorted((a.category, a.amount) for a in exp.allocations), line.id
    auto = sum(r.decision is RoutingDecision.AUTO_CONFIRM for _, r in results)
    assert auto == MONTH.expect.second_month_auto
    assert auto * 5 >= len(MONTH.lines) * 4  # >= 80 % auto-matched


def _confirmed_line(tx: MatchTransaction, confirmed: AllocationSet) -> HistoryLine:
    """The answered line once confirmed, as the ledger's history reads it (D49): the parts an
    as_last_split rule copies (D51)."""
    return HistoryLine(
        transaction_id=tx.id,
        booking_date=tx.booking_date,
        amount=tx.amount,
        counterparty_iban_hmac=tx.counterparty_iban_hmac,
        rows=[
            HistoryRow(
                category=d.category,
                status=AllocationStatus.CONFIRMED,
                amount=d.amount,
                property_id=d.property_id,
                unit_id=d.unit_id,
                tenancy_id=d.tenancy_id,
                loan_id=d.loan_id,
                recurring=d.recurring,
            )
            for d in confirmed.allocations
        ],
    )


@pytest.mark.rule
@pytest.mark.parametrize("case", REPEAT.cases, ids=lambda c: c.id)
def test_repeat_answer_is_never_asked_twice(case):
    a = case.answered
    created = matching.infer_rule(a.transaction, a.confirmed, REPEAT.context)
    assert created == case.expect_rule
    stored = MatchingRule.model_validate(
        {
            **created.model_dump(),
            "id": case.rule_id,
            "workspace_id": "0000000a-0000-4000-8000-000000000001",
            "created_at": "2024-05-20T10:00:00Z",
            "updated_at": "2024-05-20T10:00:00Z",
        }
    )
    history = [_confirmed_line(a.transaction, a.confirmed)]
    found = matching.apply_rule(stored, case.next.transaction, REPEAT.context, history=history)
    case.next.expect.check(found, case.next.transaction)
    routing = review.route(
        RoutingInput(import_status=ImportStatus.RECONCILED, allocation_set=found)
    )
    assert routing.decision is RoutingDecision.AUTO_CONFIRM
    assert found.question is None


@pytest.mark.rule
@pytest.mark.parametrize("case", REPEAT.no_rule, ids=lambda c: c.id)
def test_an_answer_on_a_matcher_owned_counterparty_becomes_no_rule(case):
    a = case.answered
    assert matching.infer_rule(a.transaction, a.confirmed, REPEAT.context) is None
