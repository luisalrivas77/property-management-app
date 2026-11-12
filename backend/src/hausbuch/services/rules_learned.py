"""Learned matching rules (docs/phases/phase-2.md §8.1, D25, D32, D38).

A rule is created from a confirmed answer ("Künftig immer so zuordnen"): `rules.matching.
infer_rule` reads the matcher (the line's IBAN hash and amount sign) and the split method
from the confirmed allocations, never their amounts (D25). The person may add a counterparty
name or purpose pattern; that is how a line without an IBAN gets a rule. A counterparty a
matcher owns (D38: no IBAN-only single_category rule for an owner, tenant or loan account)
gets one only with a purpose pattern; a name pattern alone is refused there, since that name
is on every one of the counterparty's lines.
A pattern is a case-insensitive regular expression; one that does not compile is refused
(rule.invalid_pattern), so no stored rule can fail at match time.

Whether a rule can be learned at all is `learnable`; the checkbox's default (`learn_default`)
is on only when it can and no confirmed category is one that never auto-confirms (repairs,
renovation, anything capitalized: review F6). A request asking for a rule that cannot be
learned is refused (rule.not_learnable) rather than silently ignored.

A new rule queues rematch_waiting (§5.1) in the same transaction: the lines already waiting
in the inbox that it names are re-matched by the deterministic order, so an answer given
once books the payer's other open lines too, not only later imports.

A new rule disables an enabled rule with the same matcher, split method and category: the
newer answer wins, with its scope (property, tenancy, loan), recurring and same_amount_only,
so two rules booking one category on one matcher never coexist and cancel each other out
(D49, review F4). A rule with the same matcher and another split stays (D49): a counterparty
that sends two kinds of payment from one IBAN keeps a rule per kind, and
rules.matching.rule_answer tells them apart by the amounts the ledger already booked. An
answer that corrects a line rule X had booked (review F2, `corrects`) is not a second kind:
X is disabled too when it has the same matcher, as before D49.
Events rule.created / rule.disabled carry the IBAN hash, the sign and pattern *flags*, never
a pattern (D32).
"""

import re
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Row, insert, select, update

from hausbuch.db.tables import matching_rule as rule_table
from hausbuch.jobs import enqueue
from hausbuch.rules import matching as matchers
from hausbuch.rules.errors import RuleError
from hausbuch.rules.review import THRESHOLDS
from hausbuch.schemas.allocation import (
    AllocationSet,
    MatchContext,
    MatchingRule,
    MatchingRuleCreate,
    MatchTransaction,
    NameHistory,
    RuleMatcher,
    SplitMethod,
)
from hausbuch.schemas.enums import AccountPurpose, EntityType
from hausbuch.schemas.events import EventType, RuleRecorded

from .context import CommandContext
from .errors import RuleViolationError
from .events import append_event
from .lookup import require_row

INVALID_PATTERN = "rule.invalid_pattern"
NOT_LEARNABLE = "rule.not_learnable"
PATTERN_MAX_LENGTH = 200


# --- Reads ---------------------------------------------------------------------------


def enabled_rules(conn: Connection) -> list[MatchingRule]:
    """Enabled rules, oldest first (rules.matching applies them in this order)."""
    rows = conn.execute(
        select(rule_table)
        .where(rule_table.c.enabled.is_(True))
        .order_by(rule_table.c.created_at, rule_table.c.id)
    )
    return [to_rule(r) for r in rows]


def list_rules(conn: Connection) -> list[MatchingRule]:
    rows = conn.execute(select(rule_table).order_by(rule_table.c.created_at, rule_table.c.id))
    return [to_rule(r) for r in rows]


def to_rule(row: Row) -> MatchingRule:
    return MatchingRule.model_validate(row._asdict())


# --- Deciding ------------------------------------------------------------------------


def check_pattern(pattern: str | None) -> str | None:
    """A typed pattern, stripped; None when empty. RuleViolationError rule.invalid_pattern
    when it is too long or does not compile."""
    if pattern is None or not pattern.strip():
        return None
    pattern = pattern.strip()
    if len(pattern) > PATTERN_MAX_LENGTH:
        raise RuleViolationError([INVALID_PATTERN], "the pattern is too long")
    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error:
        raise RuleViolationError(
            [INVALID_PATTERN], "the pattern is not a valid expression"
        ) from None
    return pattern


def proposed_rule(
    tx: MatchTransaction,
    confirmed: AllocationSet,
    context: MatchContext,
    *,
    name_pattern: str | None = None,
    purpose_pattern: str | None = None,
    names: Sequence[NameHistory] = (),
) -> MatchingRuleCreate | None:
    """The rule this answer would become, or None when none can be learned (a name rule
    refused by D160 included; rule_refusal says why)."""
    return proposed_rule_or_refusal(
        tx,
        confirmed,
        context,
        name_pattern=name_pattern,
        purpose_pattern=purpose_pattern,
        names=names,
    )[0]


def proposed_rule_or_refusal(
    tx: MatchTransaction,
    confirmed: AllocationSet,
    context: MatchContext,
    *,
    name_pattern: str | None = None,
    purpose_pattern: str | None = None,
    names: Sequence[NameHistory] = (),
) -> tuple[MatchingRuleCreate | None, str | None]:
    """proposed_rule, with the code of a name rule D160 refuses (rule.too_broad: the name
    was confirmed under two categories; rule.conflicts: the pattern hits a line confirmed
    otherwise). A typed pattern replaces the refused name pattern, so it is learned then."""
    name_pattern = check_pattern(name_pattern)
    purpose_pattern = check_pattern(purpose_pattern)
    refusal = None
    try:
        inferred = matchers.infer_rule(tx, confirmed, context, names)
    except RuleError as refused:
        inferred, refusal = None, refused.code
    if name_pattern is None and purpose_pattern is None:
        return inferred, refusal
    return _with_patterns(tx, confirmed, context, inferred, name_pattern, purpose_pattern), None


def _with_patterns(
    tx: MatchTransaction,
    confirmed: AllocationSet,
    context: MatchContext,
    inferred: MatchingRuleCreate | None,
    name_pattern: str | None,
    purpose_pattern: str | None,
) -> MatchingRuleCreate | None:
    patterns: dict[str, Any] = {}
    if name_pattern is not None:
        patterns["counterparty_name_pattern"] = name_pattern
    if purpose_pattern is not None:
        patterns["purpose_pattern"] = purpose_pattern
    if inferred is not None:
        matcher = inferred.matcher.model_copy(update=patterns)
        return inferred.model_copy(update={"matcher": matcher})
    # No IBAN: a typed pattern narrows the rule, so a single-category answer may become one.
    # A counterparty a matcher owns (owner, tenant or loan account, D38) needs a purpose
    # pattern: its name is on every one of its lines (the lender's on every annuity), so a
    # name pattern alone narrows nothing and would book the next rent or annuity wholly
    # under this answer's category.
    if len(confirmed.allocations) != 1 or tx.amount == 0:
        return None
    (only,) = confirmed.allocations
    if context.account_purpose is AccountPurpose.PRIVATE:
        # D160's limits hold for a typed pattern too: no tax-relevant, transfer or depot code
        # and none without a threshold; the rental matchers never run on a private line.
        if not matchers.learnable_household(only.category):
            return None
    else:
        hmac = tx.counterparty_iban_hmac
        if hmac is not None and matchers.matcher_owns(hmac, context) and purpose_pattern is None:
            return None
    matcher = RuleMatcher(
        counterparty_iban_hmac=tx.counterparty_iban_hmac,
        amount_sign="credit" if tx.amount > 0 else "debit",
        **patterns,
    )
    return MatchingRuleCreate(
        matcher=matcher,
        split_method=SplitMethod.SINGLE_CATEGORY,
        category=only.category,
        property_id=only.property_id,
        tenancy_id=only.tenancy_id,
        loan_id=only.loan_id,
        recurring=only.recurring,
    )


def checks_amount(rule: MatchingRuleCreate) -> bool:
    """Whether the rule's split method checks the amount against a record itself (D49), so
    same_amount_only would change nothing."""
    return rule.split_method in matchers.AMOUNT_CHECKED


def learn_default(rule: MatchingRuleCreate | None, confirmed: AllocationSet) -> bool:
    """The checkbox's default (§8.1, review F6): on when a rule can be learned and no
    confirmed category is one that never auto-confirms."""
    if rule is None:
        return False
    return all(THRESHOLDS[a.category] is not None for a in confirmed.allocations)


# --- Writes --------------------------------------------------------------------------


def create_rule(
    ctx: CommandContext, data: MatchingRuleCreate, *, corrects: UUID | None = None
) -> MatchingRule:
    """Store the rule, append rule.created and queue rematch_waiting for the waiting lines it
    may now book. Disabled first: an enabled rule with the same matcher and the same split
    method and category, and `corrects` (the rule whose booking this answer replaced with
    another split, review.allocate_transaction) when it has the same matcher (D49)."""
    matcher = data.matcher.model_dump(mode="json", exclude_none=True)
    for pattern in (data.matcher.counterparty_name_pattern, data.matcher.purpose_pattern):
        check_pattern(pattern)
    for old in enabled_rules(ctx.conn):
        same_matcher = old.matcher.model_dump(mode="json", exclude_none=True) == matcher
        if same_matcher and (old.id == corrects or _split(old) == _split(data)):
            disable_rule(ctx, old.id)
    values = data.model_dump(mode="json", exclude={"matcher"})
    row = ctx.conn.execute(
        insert(rule_table)
        .values(
            workspace_id=ctx.workspace_id,
            matcher=matcher,
            **values,
            created_at=ctx.now,
            updated_at=ctx.now,
        )
        .returning(*rule_table.c)
    ).one()
    rule = to_rule(row)
    append_event(ctx, EventType.RULE_CREATED, rule.id, RuleRecorded.of(data))
    # The lines already waiting in the inbox that the rule names are re-matched in the
    # background (rematch_waiting); queued in this transaction, so only with the rule.
    enqueue.enqueue_rematch_waiting(
        ctx.conn, workspace_id=ctx.workspace_id, kind=enqueue.TRIGGER_RULE, entity_id=rule.id
    )
    return rule


def disable_rule(ctx: CommandContext, rule_id: UUID) -> MatchingRule:
    """Switch a rule off (rule.disabled). A no-op for a rule already off."""
    row = require_row(ctx.conn, rule_table, EntityType.RULE, rule_id, for_update=True)
    rule = to_rule(row)
    if not rule.enabled:
        return rule
    row = ctx.conn.execute(
        update(rule_table)
        .where(rule_table.c.id == rule_id)
        .values(enabled=False, updated_at=ctx.now)
        .returning(*rule_table.c)
    ).one()
    append_event(
        ctx,
        EventType.RULE_DISABLED,
        rule_id,
        RuleRecorded.of(MatchingRuleCreate.model_validate(rule.model_dump(include=_CREATE))),
    )
    return to_rule(row)


def record_hit(ctx: CommandContext, rule_id: UUID) -> None:
    ctx.conn.execute(
        update(rule_table)
        .where(rule_table.c.id == rule_id)
        .values(hit_count=rule_table.c.hit_count + 1, updated_at=ctx.now)
    )


_CREATE = set(MatchingRuleCreate.model_fields)
_SPLIT = {"split_method", "category", "categories"}  # categories: as_last_split's kind (D51)


def _split(rule: MatchingRuleCreate) -> dict[str, Any]:
    """What a rule books, apart from its matcher and scope: two rules with the same matcher
    and this are one answer given twice (the newer replaces the older, review F4)."""
    return rule.model_dump(include=_SPLIT)
