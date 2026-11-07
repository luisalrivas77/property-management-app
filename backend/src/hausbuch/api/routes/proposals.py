"""Pre-fill proposals (Vorschläge): read, apply, reject, re-run (phase 2 spec §6.1, §6.5).

The upload that creates the first proposal is POST /documents (routes/documents.py, form
field `ai`). Nothing a model produced reaches a domain table except through apply, which
runs every record through its phase 1 service in this request's one transaction (D14).
"""

from typing import Annotated
from uuid import UUID

from fastapi import Body
from sqlalchemy import Connection

from hausbuch.schemas.api import ProposalApplyResult, ProposalRerunBody
from hausbuch.schemas.enums import EntityType
from hausbuch.schemas.proposal import Proposal, ProposalApplyBody
from hausbuch.services.documents import get_document
from hausbuch.services.proposals import (
    apply_proposal,
    get_proposal,
    list_document_proposals,
    reject_proposal,
    rerun_proposal,
)

from ..deps import Cmd, SettingsDep, domain_router, found, require

router = domain_router()


def _get(conn: Connection, proposal_id: UUID) -> Proposal:
    return found(get_proposal(conn, proposal_id), EntityType.PROPOSAL, proposal_id)


@router.get("/proposals/{proposal_id}")
def proposal_get(proposal_id: UUID, cmd: Cmd) -> Proposal:
    """Polled by the document page while the proposal is queued or running (§6.1)."""
    return cmd.read(_get, proposal_id)


@router.post("/proposals/{proposal_id}/apply", status_code=201)
def proposal_apply(
    proposal_id: UUID, body: ProposalApplyBody, cmd: Cmd, settings: SettingsDep
) -> ProposalApplyResult:
    """Create or patch every record not skipped, with the values in `body`. 422 with `loc`
    prefixed by the record key when a value fails its model; 422 `proposal.not_ready` /
    `proposal.already_decided` unless the proposal is ready. A failure writes nothing."""
    return cmd.run(apply_proposal, proposal_id, body, iban_key=settings.iban_hmac_key)


@router.post("/proposals/{proposal_id}/reject")
def proposal_reject(proposal_id: UUID, cmd: Cmd) -> Proposal:
    """Record the rejection (an eval signal); no domain writes."""
    return cmd.run(reject_proposal, proposal_id)


def _document_proposals(conn: Connection, document_id: UUID) -> list[Proposal]:
    require(conn, get_document, EntityType.DOCUMENT, document_id)
    return list_document_proposals(conn, document_id)


@router.get("/documents/{document_id}/proposals")
def document_proposals(document_id: UUID, cmd: Cmd) -> list[Proposal]:
    """The document's proposals, newest first (the document page finds its current one)."""
    return cmd.read(_document_proposals, document_id)


@router.post("/documents/{document_id}/proposals", status_code=201)
def document_proposal_rerun(
    document_id: UUID, cmd: Cmd, body: Annotated[ProposalRerunBody | None, Body()] = None
) -> Proposal:
    """Read the document again: its undecided proposal (blocked included) becomes superseded."""
    flags = body or ProposalRerunBody()
    return cmd.run(rerun_proposal, document_id, contains_tenant_data=flags.contains_tenant_data)
