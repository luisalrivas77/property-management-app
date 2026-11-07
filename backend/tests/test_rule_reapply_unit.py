"""rematch_on_rule's pure parts (phase-2.md §5.1, §8.1): which lines count as waiting, the
matcher test that picks them, and the task's registration. The job itself is in
tests/db/test_jobs_matching.py."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import procrastinate

from hausbuch.jobs import tasks
from hausbuch.jobs.enqueue import REMATCH_RULE_TASK, rule_lock
from hausbuch.rules.matching import rule_hits
from hausbuch.schemas.allocation import (
    Allocation,
    AllocationCategory,
    AllocationMethod,
    MatchTransaction,
    RuleMatcher,
)
from hausbuch.schemas.enums import AllocationStatus, ImportStatus
from hausbuch.services.matching import Line

NOW = datetime(2026, 11, 7, 8, 0, tzinfo=UTC)
TX = uuid4()
HMAC = "ab" * 32


def _row(status: AllocationStatus, method: AllocationMethod) -> Allocation:
    return Allocation(
        id=uuid4(),
        workspace_id=uuid4(),
        created_at=NOW,
        transaction_id=TX,
        amount=Decimal("-80.00"),
        category=AllocationCategory.INSURANCE,
        property_id=None,
        unit_id=None,
        tenancy_id=None,
        loan_id=None,
        service_period_start=None,
        service_period_end=None,
        recurring=True,
        method=method,
        confidence=Decimal("1.000"),
        status=status,
        supersedes_id=None,
        rule_id=None,
        ai_call_id=None,
        document_id=None,
        reasoning=None,
        question=None,
        recorded_at=NOW,
        confirmed_at=None,
        confirmed_by=None,
    )


def _line(*rows: Allocation) -> Line:
    return Line(
        tx=MatchTransaction(id=TX, booking_date=date(2026, 3, 2), amount="-80.00"),
        import_status=ImportStatus.RECONCILED,
        excluded=False,
        awaiting_duplicate=False,
        current=list(rows),
        auto_accept_llm=False,
    )


def test_a_line_waits_with_no_rows_or_only_a_proposed_set():
    assert _line().waiting
    assert _line(_row(AllocationStatus.PROPOSED, AllocationMethod.LLM)).waiting
    assert _line(_row(AllocationStatus.PROPOSED, AllocationMethod.MATCHER_RENT)).waiting


def test_a_line_with_confirmed_rows_does_not_wait_even_when_a_rematch_could_take_it():
    by_rule = _line(_row(AllocationStatus.CONFIRMED, AllocationMethod.RULE))
    assert by_rule.open_for(rematch=True)  # rematch_on_evidence may re-split it
    assert not by_rule.waiting  # rematch_on_rule leaves it alone
    for method in (AllocationMethod.USER, AllocationMethod.MATCHER_LOAN):
        assert not _line(_row(AllocationStatus.CONFIRMED, method)).waiting


def test_rule_hits_checks_the_iban_hash_the_sign_and_the_patterns():
    matcher = RuleMatcher(
        counterparty_iban_hmac=HMAC, amount_sign="debit", purpose_pattern="abschlag"
    )

    def tx(
        amount: str, hmac: str | None = HMAC, purpose: str = "Abschlag März"
    ) -> MatchTransaction:
        return MatchTransaction(
            id=uuid4(),
            booking_date=date(2026, 3, 2),
            amount=amount,
            counterparty_iban_hmac=hmac,
            purpose=purpose,
        )

    assert rule_hits(matcher, tx("-80.00"))
    assert not rule_hits(matcher, tx("80.00"))  # the other sign
    assert not rule_hits(matcher, tx("-80.00", hmac="cd" * 32))  # another payer
    assert not rule_hits(matcher, tx("-80.00", purpose="Nachzahlung"))  # pattern misses


def test_rematch_on_rule_is_registered_and_locked_per_rule():
    app = tasks.register_tasks(procrastinate.App(connector=procrastinate.SyncPsycopgConnector()))
    assert REMATCH_RULE_TASK == "rematch_on_rule" and REMATCH_RULE_TASK in app.tasks
    rule_id = uuid4()
    assert rule_lock(rule_id) == f"rule:{rule_id}"
