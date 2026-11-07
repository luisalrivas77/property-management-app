"""The erasure scrub (docs/phases/phase-2.md D18, D19, D32, D42 (3)): what erase_tenant also
clears, in its own transaction, so no copy of the tenant's data outlives the erasure in the
AI layer's tables.

- Documents in scope: those of proposals whose `applied_entities` name the tenant, plus the
  documents linked to the tenant or to any tenancy they were ever a party of.
- Proposals: every proposal of those documents, any status, gets `fields` and `decisions`
  NULL and `scrubbed_at` set. An undecided one (queued, running, ready, and also
  blocked_tenant_data, see below) is first marked `superseded` with its proposal.superseded
  event, so it can no longer be applied and a read still in flight cannot write it back
  (record_read_result and record_read_failure are no-ops once a proposal is not queued or
  running). The proposal.applied events keep their per-field decisions: events are kept.
- blocked_tenant_data is superseded too, although D42 lists only queued, running and ready:
  it is decidable (services.proposals._DECIDABLE), and a scrubbed one would leave an inbox
  item with nothing in it to apply (the reason jobs/scrub.py leaves it alone). Its
  error_code is cleared, as the proposal CHECK requires for any other status.
- ai_call: output and raw_text NULL (the only change the ai_call_scrub_only trigger allows)
  for the calls of those documents and of transactions whose counterparty IBAN hash is the
  tenant's. The hash is read by the caller before the tenant row is nulled.
- Learned rules whose matcher holds the tenant's IBAN hash, or which are scoped to one of
  those tenancies, are disabled through rules_learned.disable_rule (rule.disabled; a rule
  already off gets no second event), and then every one of them, already off or not, has
  its matcher's IBAN hash and name and purpose patterns removed (the amount sign is no
  personal data and stays). Migration 0008 allows that empty matcher on a disabled rule
  only, and MatchingRule reads it only there; such a rule can never be switched back on.
- Locks: the caller holds the tenant row FOR UPDATE; the proposals are then locked FOR
  UPDATE in id order before any is changed, so a concurrent apply either finished first
  (and its proposal is scrubbed here) or waits and then finds it superseded.
- Nothing here writes a value into an event: proposal.superseded names record keys only,
  rule.disabled the IBAN hash (already in rule.created) and pattern flags.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import null, or_, select, update

from hausbuch.db.tables import ai_call as ai_call_table
from hausbuch.db.tables import document_link as link_table
from hausbuch.db.tables import matching_rule as rule_table
from hausbuch.db.tables import proposal as proposal_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.schemas.enums import EntityType, ExtractionKind, ProposalStatus
from hausbuch.schemas.events import EventType, ProposalNoted

from .context import CommandContext
from .events import append_event
from .rules_learned import disable_rule

# Undecided proposals: superseded before they are scrubbed, so they cannot be applied.
_UNDECIDED = (
    ProposalStatus.QUEUED,
    ProposalStatus.RUNNING,
    ProposalStatus.READY,
    ProposalStatus.BLOCKED_TENANT_DATA,
)


# The matcher keys that can identify the tenant (D42 (3)); amount_sign stays.
_MATCHER_PERSONAL = ("counterparty_iban_hmac", "counterparty_name_pattern", "purpose_pattern")


@dataclass(frozen=True)
class ScrubCounts:
    proposals_scrubbed: int = 0
    ai_calls_scrubbed: int = 0
    rules_disabled: int = 0


def scrub_for_erasure(
    ctx: CommandContext,
    tenant_id: UUID,
    tenancy_ids: list[UUID],
    tenant_iban_hmac: bytes | None,
) -> ScrubCounts:
    """Scrub everything D42 (3) names for this tenant; the counts for tenant.erased."""
    document_ids = _documents_in_scope(ctx, tenant_id, tenancy_ids)
    return ScrubCounts(
        proposals_scrubbed=_scrub_proposals(ctx, document_ids),
        ai_calls_scrubbed=_scrub_ai_calls(ctx, document_ids, tenant_iban_hmac),
        rules_disabled=_disable_rules(ctx, tenancy_ids, tenant_iban_hmac),
    )


def _documents_in_scope(
    ctx: CommandContext, tenant_id: UUID, tenancy_ids: list[UUID]
) -> list[UUID]:
    p, link = proposal_table, link_table
    names_tenant = [{"entity_type": EntityType.TENANT.value, "entity_id": str(tenant_id)}]
    from_proposals = select(p.c.document_id).where(p.c.applied_entities.contains(names_tenant))
    linked = [link.c.tenant_id == tenant_id]
    if tenancy_ids:
        linked.append(link.c.tenancy_id.in_(tenancy_ids))
    from_links = select(link.c.document_id).where(or_(*linked))
    rows = ctx.conn.execute(from_proposals.union(from_links)).scalars()
    return sorted(set(rows))


def _scrub_proposals(ctx: CommandContext, document_ids: list[UUID]) -> int:
    if not document_ids:
        return 0
    p = proposal_table
    rows = ctx.conn.execute(
        select(p.c.id, p.c.document_id, p.c.kind, p.c.status, p.c.fields, p.c.scrubbed_at)
        .where(p.c.document_id.in_(document_ids))
        .order_by(p.c.id)
        .with_for_update()
    ).all()
    for row in rows:
        if ProposalStatus(row.status) not in _UNDECIDED:
            continue
        ctx.conn.execute(
            update(p)
            .where(p.c.id == row.id)
            .values(status=ProposalStatus.SUPERSEDED.value, error_code=None, updated_at=ctx.now)
        )
        noted = ProposalNoted(
            document_id=row.document_id,
            kind=None if row.kind is None else ExtractionKind(row.kind),
            record_keys=[record["key"] for record in row.fields or []],
        )
        append_event(ctx, EventType.PROPOSAL_SUPERSEDED, row.id, noted)
    to_scrub = [row.id for row in rows if row.scrubbed_at is None]
    if not to_scrub:
        return 0
    ctx.conn.execute(
        update(p)
        .where(p.c.id.in_(to_scrub))
        .values(fields=null(), decisions=null(), scrubbed_at=ctx.now, updated_at=ctx.now)
    )
    return len(to_scrub)


def _scrub_ai_calls(
    ctx: CommandContext, document_ids: list[UUID], tenant_iban_hmac: bytes | None
) -> int:
    a, t = ai_call_table, transaction_table
    sources = []
    if document_ids:
        sources.append(a.c.document_id.in_(document_ids))
    if tenant_iban_hmac is not None:
        from_tenant = select(t.c.id).where(t.c.counterparty_iban_hmac == tenant_iban_hmac)
        sources.append(a.c.transaction_id.in_(from_tenant))
    if not sources:
        return 0
    result = ctx.conn.execute(
        update(a)
        .where(or_(*sources), or_(a.c.output.is_not(None), a.c.raw_text.is_not(None)))
        .values(output=null(), raw_text=None)
    )
    return result.rowcount


def _disable_rules(
    ctx: CommandContext, tenancy_ids: list[UUID], tenant_iban_hmac: bytes | None
) -> int:
    r = rule_table
    holds = []
    if tenant_iban_hmac is not None:
        holds.append(r.c.matcher["counterparty_iban_hmac"].astext == tenant_iban_hmac.hex())
    if tenancy_ids:
        holds.append(r.c.tenancy_id.in_(tenancy_ids))
    if not holds:
        return 0
    rows = ctx.conn.execute(
        select(r.c.id, r.c.enabled, r.c.matcher)
        .where(or_(*holds))
        .order_by(r.c.id)
        .with_for_update()
    ).all()
    disabled = 0
    for row in rows:
        if row.enabled:
            # Disabled first, while the matcher still validates: its event records the hash
            # (already in rule.created) and the pattern flags, never a pattern.
            disable_rule(ctx, row.id)
            disabled += 1
        kept = {key: value for key, value in row.matcher.items() if key not in _MATCHER_PERSONAL}
        if kept != row.matcher:
            ctx.conn.execute(
                update(r).where(r.c.id == row.id).values(matcher=kept, updated_at=ctx.now)
            )
    return disabled
