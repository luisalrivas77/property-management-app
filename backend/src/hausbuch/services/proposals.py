"""Pre-fill proposals (phase 2 spec §6).

Seam between the step 1 chunks, written by the orchestrator: the proposals chunk owns this
file and implements it (plus create, apply, reject and re-run); the worker chunk's
`document_read` task only calls the two functions below. Keep their signatures.
"""

from uuid import UUID

from hausbuch.schemas.ai import ClassifyOutput, ExtractedValue
from hausbuch.schemas.proposal import Proposal
from hausbuch.services.context import CommandContext


def record_read_result(
    ctx: CommandContext,
    proposal_id: UUID,
    *,
    classification: ClassifyOutput,
    classify_call_id: UUID,
    values: dict[str, ExtractedValue] | None,
    extract_call_id: UUID | None,
    blocked_tenant_data: bool = False,
) -> Proposal:
    """The job read the document. Writes the `ai` extraction version (when `values` is
    given), builds the PrefillContext from the workspace, calls
    `rules.prefill.build_proposal`, stores the records and sets status `ready`, with events.

    `values` is None when only classification ran: the proposal is typing-only. With
    `blocked_tenant_data`, the privacy gate stopped after classification (§5.5 step 3): the
    typing-only proposal is stored with status `blocked_tenant_data`.
    `ctx.actor` is the job's actor (ActorType for the AI / system, actor_id the model id).
    """
    raise NotImplementedError("step 1 proposals chunk")


def record_read_failure(
    ctx: CommandContext, proposal_id: UUID, *, error_code: str, ai_call_id: UUID | None
) -> Proposal:
    """The job gave up (dead letter, AI disabled, blocked before any call, unparsable,
    refused, too many pages, unsupported media type): status `failed` or
    `blocked_tenant_data` by error code, with `error_code` set and an event."""
    raise NotImplementedError("step 1 proposals chunk")
