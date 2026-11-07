"""The scrub_proposals periodic task (phase-2.md D19, D41; §6 jobs table).

Proposal values are personal data at rest (D19): 90 days after a proposal reached a final
status, its `fields` and `decisions` are set to NULL and `scrubbed_at` records when. Final
means applied, rejected, superseded, failed or blocked_tenant_data (superseded and failed
proposals hold the same values); the age is taken from `decided_at`, or `updated_at` for the
statuses that are never decided. The eval record survives: it is the `proposal.applied`
event, which carries per field the decision and never a value (D41), and events are kept.

- No event: a proposal row is mutable state, not domain history (D41). One log line with the
  count, never a value.
- Workspaces. The worker runs as the app role under RLS, so every workspace is scrubbed in a
  workspace_transaction of its own. The workspaces come from
  hausbuch.auth.lookup.provisioned_workspace_ids (every workspace provisioned through
  bootstrap has an invite or a passkey), the same list the CLI uses where the workspace table
  is invisible. A workspace whose scrub fails is logged by exception class and the others go
  on; the next day's run tries it again (the condition is "older than 90 days", so a missed
  day only delays a scrub).
- The cutoff comes from deps.clock(), not the database's now(), so a test can move time.
- The proposal table's CHECKs allow exactly this update: decisions may be NULL on an applied
  proposal once scrubbed_at is set, and scrubbed_at needs fields and decisions NULL.
"""

import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import Engine, func, null, update

from hausbuch.auth.lookup import provisioned_workspace_ids
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import proposal as proposal_table
from hausbuch.schemas.enums import ProposalStatus

from .document_read import JobDeps

SCRUB_TASK = "scrub_proposals"
SCRUB_CRON = "15 3 * * *"  # daily, 03:15 (the worker's clock, UTC in the container)
SCRUB_AFTER = timedelta(days=90)
FINAL_STATUSES = (
    ProposalStatus.APPLIED,
    ProposalStatus.REJECTED,
    ProposalStatus.SUPERSEDED,
    ProposalStatus.FAILED,
)
# Not blocked_tenant_data: such a proposal can still be applied or rejected
# (services.proposals._DECIDABLE), and scrubbing it would leave an inbox item nobody can
# resolve. It is scrubbed once decided or superseded (D41, amended after review).

logger = logging.getLogger("hausbuch.jobs.scrub")


def scrub_workspace(engine: Engine, workspace_id: UUID, *, now: datetime) -> int:
    """Scrub one workspace's proposals that reached a final status before now - 90 days;
    how many were scrubbed. One transaction; already scrubbed rows are left alone."""
    t = proposal_table
    cutoff = now - SCRUB_AFTER
    with workspace_transaction(engine, workspace_id) as conn:
        result = conn.execute(
            update(t)
            .where(
                t.c.status.in_([s.value for s in FINAL_STATUSES]),
                t.c.scrubbed_at.is_(None),
                func.coalesce(t.c.decided_at, t.c.updated_at) < cutoff,
            )
            .values(fields=null(), decisions=null(), scrubbed_at=now)
        )
        return result.rowcount


def run_scrub_proposals(deps: JobDeps) -> int:
    """Scrub every provisioned workspace; the total scrubbed. Never raises for one
    workspace's failure (logged by class name; the next run retries it)."""
    now = deps.clock()
    scrubbed = failed = 0
    workspaces = sorted(provisioned_workspace_ids(deps.engine))
    for workspace_id in workspaces:
        try:
            scrubbed += scrub_workspace(deps.engine, workspace_id, now=now)
        except Exception as exc:  # class name only: a driver message can quote parameters
            failed += 1
            fields = {
                "event": "scrub_proposals_failed",
                "workspace_id": str(workspace_id),
                "exc_type": type(exc).__name__,
            }
            logger.error("proposal scrub failed for a workspace", extra={"fields": fields})
    fields = {
        "event": "scrub_proposals",
        "scrubbed": scrubbed,
        "workspaces": len(workspaces),
        "failed_workspaces": failed,
    }
    logger.info("proposals scrubbed", extra={"fields": fields})
    return scrubbed
