"""Pre-fill proposals (phase 2 spec §6).

A proposal row is created `queued` when a document is to be read (upload with `ai`, or a
re-run), the `document_read` job reports back through record_read_result /
record_read_failure, and the user decides: apply_proposal writes through the phase 1
services only, in the caller's one transaction (D14, §6.5), reject_proposal records the
"no" without touching a domain table. Nothing here calls a model.

Statuses (§3.1): queued -> running -> ready | failed | blocked_tenant_data; ready ->
applied | rejected | superseded (a re-run). The job's two functions are no-ops once the
proposal left queued/running (a re-run superseded it while the job ran).

Phase 4b (docs/phases/phase-4b.md §8.2): a pay document's return_input record is written
through services/return_inputs.create_input after the links, for an owner of this workspace,
replacing the current row rules.prefill.replaced_pay_row names.

Phase 4b D129 (§8.4): an Einkommensteuerbescheid's income_input:bescheid_zve record is
written through services/income_inputs.create_income_input after the links, with this
document as the Bescheid, superseding the year's current bescheid_zve row.
"""

from collections.abc import Callable
from contextlib import suppress
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from pydantic import BaseModel, JsonValue, ValidationError
from sqlalchemy import Connection, exists, insert, null, select, update

from hausbuch.db.tables import document_extraction as extraction_table
from hausbuch.db.tables import property_acquisition_cost as cost_table
from hausbuch.db.tables import proposal as proposal_table
from hausbuch.db.tables import return_input as return_input_table
from hausbuch.db.tables import workspace as workspace_table
from hausbuch.jobs import enqueue
from hausbuch.rules.document_checklist import available_years
from hausbuch.rules.errors import (
    AI_BLOCKED_TENANT_DATA,
    PROPOSAL_ALREADY_DECIDED,
    PROPOSAL_NOT_READY,
)
from hausbuch.rules.extraction_parse import extraction_kind
from hausbuch.rules.hand_fields import HAND_FIELDS
from hausbuch.rules.loan_schedule import build_schedule
from hausbuch.rules.prefill import (
    OTHER_NOTICE,
    PROOF_LINK_KEY,
    build_proposal,
    grundsteuer_schedule_record,
    grundsteuer_schedule_value,
    replaced_pay_row,
    return_input_value,
)
from hausbuch.rules.tax.draft import bills_without_proof
from hausbuch.schemas.ai import ClassifyOutput, ExtractedValue
from hausbuch.schemas.api import (
    AcquisitionCostPatch,
    AppliedEntity,
    DateCorrection,
    DepositReceivedBody,
    DisbursementCorrection,
    LoanCreateRequest,
    LoanPatch,
    NotarizedOnCorrection,
    OwnershipBody,
    PropertyPatch,
    ProposalApplyResult,
    TenancyPatch,
    TenantPatch,
    UnitPatch,
)
from hausbuch.schemas.common import Schema, TaxYear
from hausbuch.schemas.document import (
    Document,
    DocumentExtraction,
    DocumentTyping,
    ManualDocumentFields,
)
from hausbuch.schemas.employment import (
    RETURN_INPUT_VALUES,
    ReturnInput,
    ReturnInputCreate,
    ReturnInputEvidence,
    ReturnInputEvidenceValue,
)
from hausbuch.schemas.enums import (
    ActorType,
    DocumentSource,
    DocumentSubtype,
    DocumentType,
    EntityType,
    ExtractionKind,
    IncomeInputKind,
    LinkTarget,
    PriceSplitSource,
    ProposalStatus,
    ProposalTargetMode,
    TenancyRole,
)
from hausbuch.schemas.events import (
    AppliedRecord,
    DepositReceived,
    DocumentClassified,
    DocumentExtracted,
    DocumentReadFailed,
    EventType,
    ProposalApplied,
    ProposalNoted,
)
from hausbuch.schemas.extraction import EXTRACTION_MODELS, _Typing
from hausbuch.schemas.loan import LoanTerms
from hausbuch.schemas.performance import IncomeInputCreate
from hausbuch.schemas.property import (
    AcquisitionCostCreate,
    PriceSplitCreate,
    PropertyCreate,
    UnitCreate,
)
from hausbuch.schemas.proposal import (
    ExistingBescheidZve,
    ExistingCost,
    ExistingDepositReceipt,
    ExistingLoan,
    ExistingOwner,
    ExistingPayRow,
    ExistingPrivateCost,
    ExistingProofBill,
    ExistingProperty,
    ExistingTenancy,
    ExistingTenant,
    ExistingUnit,
    FieldDecision,
    PrefillContext,
    Proposal,
    ProposalApplyBody,
    ProposalApplyRecord,
    ProposalRecord,
)
from hausbuch.schemas.tax import LIMIT_15_CATEGORIES, TaxInput, TaxInputCreate, TaxInputKind
from hausbuch.schemas.tenancy import InitialRent, TenancyCreate, TenantCreate

from . import (
    documents,
    income_inputs,
    loans,
    matching,
    owners,
    properties,
    tax,
    tax_inputs,
    tenancies,
    tenants,
    units,
)
from .checklist import checklist_facts
from .context import CommandContext
from .errors import NotFoundError, RuleViolationError
from .events import append_event

# D145 review: a Grunderwerbsteuer rest row applied without the patch that lowers its row.
PROPOSAL_OTHER_NOTICE_WITHOUT_PATCH = "proposal.other_notice_without_patch"

# Phase 5 D154: a file the bank sync fetched is never sent to the model.
PROPOSAL_BANK_FILE_NOT_READ = "proposal.bank_file_not_read"

# Undecided and still in the job's hands: a re-run supersedes these, the job may write them.
_IN_FLIGHT = (ProposalStatus.QUEUED, ProposalStatus.RUNNING)
_SUPERSEDABLE = (ProposalStatus.QUEUED, ProposalStatus.RUNNING, ProposalStatus.READY)
# A blocked proposal still holds the classifier's typing (§5.5 step 3): it can be applied
# (typing only) or rejected like a ready one.
_DECIDABLE = (ProposalStatus.READY, ProposalStatus.BLOCKED_TENANT_DATA)
# What a re-run replaces: every undecided proposal, the blocked one included (D42 (2): a
# document blocked while ai_tenant_documents was off is read again once it is on).
_RERUN_SUPERSEDES = (*_SUPERSEDABLE, ProposalStatus.BLOCKED_TENANT_DATA)
_DECIDED = (ProposalStatus.APPLIED, ProposalStatus.REJECTED)

# §6.5: property -> corrections -> unit -> ownership -> price split -> costs -> loan ->
# tenants -> tenancy (with the rent row) -> typing -> links -> tax inputs (phase 3 §9: after
# the links, so a certificate's figure follows the loan or property the user linked) ->
# return inputs (phase 4b §8.2: after link:owner, for the same reason) -> income inputs
# (phase 4b D129: the Bescheid's zvE, after the typing). By the key's prefix.
_APPLY_ORDER = {
    "property": 0,
    "correction": 1,
    "unit": 2,
    "ownership": 3,
    "price_split": 4,
    "cost": 5,
    "loan": 6,
    "tenant": 7,
    "tenancy": 8,
    "rent": 9,
    "deposit_received": 10,
    "typing": 11,
    "link": 12,
    "tax_input": 13,
    "return_input": 14,
    "income_input": 15,
    # phase 3 D143: a payment proof moves its bill's private_cost rows to the year paid
    "private_cost_year": 16,
}
_PAY_KINDS = ("wage_certificate", "payslip")
# The record whose entity a link record points at when the proposal had no target yet.
_LINK_SOURCE = {
    LinkTarget.PROPERTY: "property",
    LinkTarget.LOAN: "loan",
    LinkTarget.TENANCY: "tenancy",
}
_LOAN_CORRECTED = ("principal", "disbursed_on")  # through correct_disbursement
# Tenant documents by type (§5.5 step 3): the lease and its papers and the catalogue's tenancy
# group (D131); any other letter from a tenant is caught by the classifier's
# contains_tenant_data. Kept as its own copy of rules.extraction_parse.TENANT_SUBTYPES, so the
# service repeats the job's gate from its own list.
_TENANT_SUBTYPES = frozenset(
    {
        DocumentSubtype.LEASE,
        DocumentSubtype.HANDOVER_PROTOCOL,
        DocumentSubtype.DEPOSIT_RECORD,
        DocumentSubtype.LEASE_AMENDMENT,
        DocumentSubtype.RENT_INCREASE,
        DocumentSubtype.TERMINATION_NOTICE,
        DocumentSubtype.TENANT_SELF_DISCLOSURE,
        DocumentSubtype.LANDLORD_CONFIRMATION,
        DocumentSubtype.OPERATING_COSTS_STATEMENT,
        DocumentSubtype.TENANT_CORRESPONDENCE,
    }
)
_JSONB_COLUMNS = ("targets", "fields", "decisions", "applied_entities")


# --- Creating proposals ------------------------------------------------------------------


def request_read(
    ctx: CommandContext, document_id: UUID, *, contains_tenant_data: bool = False
) -> Proposal:
    """A new proposal for the document and its `document_read` job, in the caller's
    transaction (§6.1). The uploader's `contains_tenant_data` is stored on the row (the job
    reads it there); with it set and the workspace's ai_tenant_documents off, the privacy
    gate already stops it here (§5.5 step 2): stored `blocked_tenant_data`, nothing
    enqueued, so no job and no model call.

    The flag is sticky (D16: once said, a document stays a tenant document): it is set
    when any earlier proposal of the document carried it, whatever this call passes. Any,
    not the latest: proposals created in one transaction share created_at.

    A file the bank sync fetched (the camt ZIP, the bank's PDF statements, phase 5 D154) is
    never read by the model: refused with proposal.bank_file_not_read, so neither the
    document page's button nor a direct call sends a statement that names every tenant and
    IBAN in clear."""
    document = documents.get_document(ctx.conn, document_id)
    if document is None:
        raise NotFoundError(EntityType.DOCUMENT, document_id)
    if document.source is DocumentSource.BANK_SYNC:
        raise RuleViolationError([PROPOSAL_BANK_FILE_NOT_READ], "a bank sync file is never read")
    contains_tenant_data = contains_tenant_data or any(
        p.contains_tenant_data for p in list_document_proposals(ctx.conn, document_id)
    )
    blocked = contains_tenant_data and not _ai_tenant_documents(ctx.conn)
    row = ctx.conn.execute(
        insert(proposal_table)
        .values(
            workspace_id=ctx.workspace_id,
            document_id=document_id,
            status=(ProposalStatus.BLOCKED_TENANT_DATA if blocked else ProposalStatus.QUEUED).value,
            error_code=AI_BLOCKED_TENANT_DATA if blocked else None,
            contains_tenant_data=contains_tenant_data,
            created_at=ctx.now,
            updated_at=ctx.now,
        )
        .returning(*proposal_table.c)
    ).one()
    proposal = _to_proposal(row)
    append_event(
        ctx, EventType.PROPOSAL_CREATED, proposal.id, ProposalNoted(document_id=document_id)
    )
    if not blocked:
        enqueue.enqueue_document_read(
            ctx.conn,
            workspace_id=ctx.workspace_id,
            document_id=document_id,
            proposal_id=proposal.id,
        )
    return proposal


def proposal_for_upload(
    ctx: CommandContext, document_id: UUID, *, created: bool, contains_tenant_data: bool
) -> Proposal:
    """The proposal an upload with `ai` answers with: a new one for a new document; for the
    same bytes uploaded again (D9), the document's undecided proposal if it has one, so a
    double upload costs no second model call and a document never has two undecided
    proposals:

    - pending (queued, running or ready): returned as is, unless this upload says tenant
      data and the pending one did not: then it is re-run with the flag;
    - blocked_tenant_data: with ai_tenant_documents now on, re-run (the blocked proposal is
      superseded, a read is queued); still off, returned as is (a re-run would only block
      again and drop the classifier's typing), unless this upload adds the tenant flag.

    After a decision or a failure the upload asks for a new read."""
    if not created:
        undecided = [
            p
            for p in list_document_proposals(ctx.conn, document_id)
            if p.status in _RERUN_SUPERSEDES
        ]
        if undecided:
            current = undecided[0]  # newest first
            adds_flag = contains_tenant_data and not current.contains_tenant_data
            blocked = any(p.status is ProposalStatus.BLOCKED_TENANT_DATA for p in undecided)
            reread_blocked = blocked and _ai_tenant_documents(ctx.conn)
            if not adds_flag and not reread_blocked and len(undecided) == 1:
                return current
            return rerun_proposal(ctx, document_id, contains_tenant_data=contains_tenant_data)
    return request_read(ctx, document_id, contains_tenant_data=contains_tenant_data)


def rerun_proposal(
    ctx: CommandContext, document_id: UUID, *, contains_tenant_data: bool = False
) -> Proposal:
    """POST /documents/{id}/proposals: supersede the document's undecided proposals
    (queued, running, ready, blocked_tenant_data; a blocked one loses its error_code, which
    the proposal CHECK allows on failed and blocked proposals only) and request a new read.
    `contains_tenant_data` can add the tenant-data flag, never remove it: request_read keeps
    it set once any earlier proposal of the document had it (D16), so with ai_tenant_documents
    still off a flagged document is blocked again, and with it on it is queued."""
    if documents.get_document(ctx.conn, document_id) is None:
        raise NotFoundError(EntityType.DOCUMENT, document_id)
    pending = ctx.conn.execute(
        select(proposal_table)
        .where(proposal_table.c.document_id == document_id)
        .where(proposal_table.c.status.in_([s.value for s in _RERUN_SUPERSEDES]))
        .order_by(proposal_table.c.created_at, proposal_table.c.id)
        .with_for_update()
    ).all()
    for row in pending:
        old = _to_proposal(row)
        _set(ctx, old.id, status=ProposalStatus.SUPERSEDED, error_code=None)
        append_event(ctx, EventType.PROPOSAL_SUPERSEDED, old.id, _noted(old))
    return request_read(ctx, document_id, contains_tenant_data=contains_tenant_data)


# --- A payment proof linked after its reading (phase 3 D149) ------------------------------


def attach_stored_proof(ctx: CommandContext, proof_id: UUID, bill_id: UUID) -> Document:
    """POST /documents/{id}/payment-proof, the to-do „Vorhandenen Nachweis zuordnen“: link a
    payment proof stored before to the bill it pays (documents.attach_payment_proof with
    `existing`: an untyped document is refused), then, in the same transaction, propose
    moving the bill's privately paid rows to the year the proof says it was paid
    (propose_proof_years). A ready suggestion of the proof that still offers bills to pick
    (link:document) is superseded: the bill is chosen now."""
    document = documents.attach_payment_proof(ctx, proof_id, bill_id, existing=True)
    if propose_proof_years(ctx, proof_id) is None:
        stale = [
            p
            for p in list_document_proposals(ctx.conn, proof_id)
            if p.status is ProposalStatus.READY and any(r.key == PROOF_LINK_KEY for r in p.records)
        ]
        for old in stale:
            _set(ctx, old.id, status=ProposalStatus.SUPERSEDED, error_code=None)
            append_event(ctx, EventType.PROPOSAL_SUPERSEDED, old.id, _noted(old))
    return document


def propose_proof_years(
    ctx: CommandContext, proof_id: UUID, *, proposed_rows: frozenset[UUID] = frozenset()
) -> Proposal | None:
    """Phase 3 D149 (review F1): a payment proof was linked to its bill after its reading
    (a bill picked on its suggestion, or „Vorhandenen Nachweis zuordnen“), so the reading's
    D143 proposal to move the bill's private_cost rows to the year paid never ran. Rebuilds it
    from the stored reading, without a model call: build_proposal over the proof's latest
    payment_proof extraction, with the bill now found through the link (prefill_context's
    proof_bill), so the to-do's own rule decides (the amount fits the bill as a whole and
    paid_on is read). Only its private_cost_year records are kept, the first carrying the
    note that names the bill; nothing is stored when there are none (the years agree, the
    amount does not fit, paid_on is unread, or the proof has no such reading).

    `proposed_rows`: the private_cost rows the applied suggestion already proposed to move
    (applied or declined there); when the bill has one of them, nothing is proposed again.

    The new proposal is `ready` (kind payment_proof, the reading's extraction and AI call),
    the proof's other undecided proposals superseded, so the proof has one suggestion. The
    user confirms it on the proof's page (principle 3); nothing is moved here."""
    bill = _proof_bill(ctx.conn, proof_id)
    if bill is None or any(r.id in proposed_rows for r in bill.private_costs):
        return None
    reading = _payment_proof_reading(ctx.conn, proof_id)
    if reading is None:
        return None
    classification = ClassifyOutput(
        type=DocumentType.OTHER,
        subtype=DocumentSubtype.PAYMENT_PROOF,
        contains_tenant_data=False,
        language="de",
        confidence="high",
    )
    built = build_proposal(
        ExtractionKind.PAYMENT_PROOF,
        reading.fields,  # type: ignore[arg-type]
        prefill_context(ctx.conn, proof_id, classification),
    )
    notes = built[0].notes if built and built[0].key == "typing" else []
    records = [r for r in built if _prefix(r.key) == "private_cost_year"]
    if not records:
        return None
    records[0] = records[0].model_copy(update={"notes": [*notes, *records[0].notes]})
    pending = ctx.conn.execute(
        select(proposal_table)
        .where(proposal_table.c.document_id == proof_id)
        .where(proposal_table.c.status.in_([s.value for s in _RERUN_SUPERSEDES]))
        .order_by(proposal_table.c.created_at, proposal_table.c.id)
        .with_for_update()
    ).all()
    for row in pending:
        old = _to_proposal(row)
        _set(ctx, old.id, status=ProposalStatus.SUPERSEDED, error_code=None)
        append_event(ctx, EventType.PROPOSAL_SUPERSEDED, old.id, _noted(old))
    row = ctx.conn.execute(
        insert(proposal_table)
        .values(
            workspace_id=ctx.workspace_id,
            document_id=proof_id,
            status=ProposalStatus.READY.value,
            kind=ExtractionKind.PAYMENT_PROOF.value,
            extraction_id=reading.id,
            ai_call_id=reading.ai_call_id,
            target_mode=ProposalTargetMode.PATCH.value,
            targets=[
                {"key": r.key, "entity_type": str(r.entity_type), "target_id": str(r.target_id)}
                for r in records
            ],
            fields=[r.model_dump(mode="json") for r in records],
            created_at=ctx.now,
            updated_at=ctx.now,
        )
        .returning(*proposal_table.c)
    ).one()
    proposal = _to_proposal(row)
    append_event(ctx, EventType.PROPOSAL_CREATED, proposal.id, ProposalNoted(document_id=proof_id))
    return proposal


def _payment_proof_reading(conn: Connection, document_id: UUID) -> DocumentExtraction | None:
    """The document's latest payment_proof extraction (a manual version written on top holds
    the hand fields only, not paid_on), or None."""
    row = conn.execute(
        select(extraction_table)
        .where(extraction_table.c.document_id == document_id)
        .where(extraction_table.c.kind == ExtractionKind.PAYMENT_PROOF.value)
        .order_by(extraction_table.c.version.desc())
        .limit(1)
    ).one_or_none()
    return None if row is None else DocumentExtraction.model_validate(dict(row._mapping))


# --- The job's two seams -----------------------------------------------------------------


def record_read_result(
    ctx: CommandContext,
    proposal_id: UUID,
    *,
    classification: ClassifyOutput,
    classify_call_id: UUID | None,
    values: dict[str, ExtractedValue] | None,
    extract_call_id: UUID | None,
    blocked_tenant_data: bool = False,
    iban_key: bytes | None = None,
) -> Proposal:
    """The job read the document. Writes the `ai` extraction version (when `values` is
    given), builds the PrefillContext from the workspace, calls
    `rules.prefill.build_proposal`, stores the records and sets status `ready`, with events.

    `values` is None when only classification ran: the proposal is typing-only. With
    `blocked_tenant_data`, the privacy gate stopped after classification (§5.5 step 3): the
    typing-only proposal is stored with status `blocked_tenant_data`.
    `ctx.actor` is the job's actor (ActorType for the AI / system, actor_id the model id).

    A classifier result that says tenant data (its flag or a tenant subtype), and any block,
    sets the row's contains_tenant_data: the mark is then as sticky as the uploader's (D16),
    so a later re-run with ai_tenant_documents off is blocked by request_read at creation,
    without a classify call.

    Appends document.classified and, with values, document.extracted (the job does not).
    `classify_call_id` None: no classify call was made, because code had typed the document
    (a payment proof, phase 3 D143); then no document.classified is appended.
    A no-op returning the proposal as stored when it is no longer queued or running.

    An invoice or Bescheid read: whether a bank line already pays it
    (services.matching.paid_from_rental_account, with `iban_key` the bill's IBAN is compared
    too) goes into the context, so no part of it is proposed as paid from another account
    (phase 2 D135); so does whether a private account's line pays it
    (paid_from_private_account, phase 6 D161), whose card then proposes those parts.
    """
    current = _lock(ctx.conn, proposal_id)
    if current.status not in _IN_FLIGHT:
        return current
    if (values is None) != (extract_call_id is None):
        raise ValueError("extracted values come with the extract call's id, and only then")
    document_id = current.document_id
    kind = extraction_kind(classification.type, classification.subtype)
    # The job applies the gate (§5.5 step 3); this repeats it, so no classifier result can
    # bring a tenant document past a workspace that has not allowed them (D16).
    if not blocked_tenant_data and not _ai_tenant_documents(ctx.conn):
        blocked_tenant_data = (
            current.contains_tenant_data
            or classification.contains_tenant_data
            or classification.subtype in _TENANT_SUBTYPES
        )
    tenant_mark = (
        current.contains_tenant_data
        or blocked_tenant_data
        or classification.contains_tenant_data
        or classification.subtype in _TENANT_SUBTYPES
    )
    if classify_call_id is not None:
        append_event(
            ctx,
            EventType.DOCUMENT_CLASSIFIED,
            document_id,
            DocumentClassified(
                ai_call_id=classify_call_id,
                type=classification.type,
                subtype=classification.subtype,
                confidence=classification.confidence,
                contains_tenant_data=classification.contains_tenant_data,
            ),
        )

    # The document's lock (after the proposal's, the order every path takes) before the
    # extraction is read for the context: no typing can add a version between that read and
    # this commit, so the version a typing-only record pins is the one it shows (D132, F1).
    documents.lock_document(ctx.conn, document_id)
    extraction: _Typing = _Typing()
    built_kind: ExtractionKind | None = None
    extraction_id = None
    if values is not None and not blocked_tenant_data:
        # Every classification has a field set since D130 (the general reading at least).
        extraction = EXTRACTION_MODELS[kind].model_validate(values)
        assert extract_call_id is not None
        stored = documents.record_ai_extraction(
            ctx, document_id, extraction, ai_call_id=extract_call_id
        )
        extraction_id = stored.id
        built_kind = kind
        append_event(
            ctx,
            EventType.DOCUMENT_EXTRACTED,
            document_id,
            DocumentExtracted(
                ai_call_id=extract_call_id,
                extraction_version=stored.version,
                kind=kind,
                field_names=sorted(values),
            ),
        )

    bill = built_kind in (ExtractionKind.INVOICE, ExtractionKind.BESCHEID)
    paid = bill and matching.paid_from_rental_account(ctx.conn, document_id, iban_key=iban_key)
    paid_privately = (
        bill
        and not paid
        and matching.paid_from_private_account(ctx.conn, document_id, iban_key=iban_key)
    )
    context = prefill_context(
        ctx.conn,
        document_id,
        classification,
        paid_from_rental_account=paid,
        paid_from_private_account=paid_privately,
        # D149: only a payment proof's reading looks for the bills it may pay
        today=ctx.now.astimezone(documents.LOCAL_TIME_ZONE).date()
        if built_kind is ExtractionKind.PAYMENT_PROOF
        else None,
    )
    records = build_proposal(built_kind, extraction, context)
    if extraction_id is None and records and records[0].key == "typing":
        # No reading of its own: the hand fields come from the current extraction, whose
        # version the apply's stale guard compares with (D132, F1).
        latest = documents.current_extraction(ctx.conn, document_id)
        records[0] = records[0].model_copy(
            update={"hand_version": 0 if latest is None else latest.version}
        )
    if built_kind is ExtractionKind.GRUNDSTEUERBESCHEID:
        # Phase 5 D126: the Bescheid's schedule, for a property found (step 3 as-built: a
        # separate rule so phase 2's prefill fixture stays as it is).
        schedule = grundsteuer_schedule_record(extraction, context)
        if schedule is not None:
            records.append(schedule)
    targeted = [r for r in records if r.mode != "create"]
    updated = _set(
        ctx,
        proposal_id,
        status=ProposalStatus.BLOCKED_TENANT_DATA if blocked_tenant_data else ProposalStatus.READY,
        error_code=AI_BLOCKED_TENANT_DATA if blocked_tenant_data else None,
        contains_tenant_data=tenant_mark,
        kind=kind.value,
        extraction_id=extraction_id,
        ai_call_id=extract_call_id if extraction_id is not None else classify_call_id,
        target_mode=(ProposalTargetMode.PATCH if targeted else ProposalTargetMode.CREATE).value,
        targets=[
            {"key": r.key, "entity_type": str(r.entity_type), "target_id": str(r.target_id)}
            for r in targeted
        ]
        or None,
        fields=[r.model_dump(mode="json") for r in records],
    )
    return updated


def record_read_failure(
    ctx: CommandContext,
    proposal_id: UUID,
    *,
    error_code: str,
    ai_call_id: UUID | None,
    attempts: int = 1,
) -> Proposal:
    """The job gave up (dead letter, AI disabled, blocked before any call, unparsable,
    refused, too many pages, unsupported media type): status `failed` or
    `blocked_tenant_data` by error code, with `error_code` set and an event
    (document.read_failed, with `attempts`). A no-op once not queued or running."""
    current = _lock(ctx.conn, proposal_id)
    if current.status not in _IN_FLIGHT:
        return current
    if not error_code:
        raise ValueError("a failed read names its error code")
    blocked = error_code == AI_BLOCKED_TENANT_DATA
    updated = _set(
        ctx,
        proposal_id,
        status=ProposalStatus.BLOCKED_TENANT_DATA if blocked else ProposalStatus.FAILED,
        error_code=error_code,
        ai_call_id=ai_call_id if ai_call_id is not None else current.ai_call_id,
    )
    append_event(
        ctx,
        EventType.DOCUMENT_READ_FAILED,
        current.document_id,
        DocumentReadFailed(error_code=error_code, attempts=attempts),
    )
    return updated


def prefill_context(
    conn: Connection,
    document_id: UUID,
    classification: ClassifyOutput,
    *,
    paid_from_rental_account: bool = False,
    paid_from_private_account: bool = False,
    today: date | None = None,
) -> PrefillContext:
    """The workspace's records as the plain facts build_proposal reads (§6.3). `today` (the
    local date) picks the years a payment proof's candidate bills are looked for in (D149);
    without it, none are."""
    props = properties.list_properties(conn)
    proof_bill = _proof_bill(conn, document_id)
    linked_tenancies = {
        link.target_id
        for link in documents.list_links(conn, document_id)
        if link.target_type is LinkTarget.TENANCY
    }
    unit_rows = [u for p in props for u in units.list_units(conn, p.id)]
    return PrefillContext(
        document_id=document_id,
        classification=classification,
        paid_from_rental_account=paid_from_rental_account,
        paid_from_private_account=paid_from_private_account,
        linked_tenancy_id=next(iter(linked_tenancies)) if len(linked_tenancies) == 1 else None,
        properties=[
            ExistingProperty.model_validate(
                p.model_dump(include=set(ExistingProperty.model_fields))
                | _contract_facts(conn, p.id)
            )
            for p in props
        ],
        units=[
            ExistingUnit(id=u.id, property_id=u.property_id, label=u.label, mea=u.mea)
            for u in unit_rows
        ],
        owners=[ExistingOwner(id=o.id, full_name=o.full_name) for o in owners.list_owners(conn)],
        costs=[
            ExistingCost.model_validate(c.model_dump(include=set(ExistingCost.model_fields)))
            for p in props
            for c in properties.list_acquisition_costs(conn, p.id)
        ],
        loans=[
            ExistingLoan(
                id=loan.id,
                property_id=loan.property_id,
                lender=loan.lender,
                contract_number=loan.contract_number,
                interest_only_before_first_payment=loan.terms.interest_only_before_first_payment,
            )
            for p in props
            for loan in loans.list_loans(conn, p.id)
        ],
        tenancies=[
            ExistingTenancy(
                id=t.id,
                unit_id=t.unit_id,
                start_date=t.start_date,
                end_date=t.end_date,
                deposit_amount=t.deposit_amount,
                rent_due_day=t.rent_due_day,
                operating_costs_terms=t.operating_costs_terms,
                deposit_receipts=[
                    ExistingDepositReceipt(
                        amount=DepositReceived.model_validate(e.payload).amount,
                        received_on=e.effective_date,
                    )
                    for e in tenancies.deposit_receipts(conn, t.id)
                ],
            )
            for u in unit_rows
            for t in tenancies.list_tenancies(conn, u.id)
        ],
        tenants=[
            ExistingTenant(id=t.id, full_name=t.full_name) for t in tenants.list_tenants(conn)
        ],
        pay_rows=[row for row, _ in _current_pay_rows(conn)],
        hand_fields=documents.hand_fields(documents.current_extraction(conn, document_id)),
        bescheid_rows=[
            ExistingBescheidZve(
                id=row.id,
                tax_year=row.tax_year,
                document_id=row.document_id,
                document_date=_document_date(conn, row.document_id),
            )
            for row in _current_bescheid_rows(conn)
        ],
        proof_bill=proof_bill,
        proof_candidates=(
            _proof_candidates(conn, document_id, today)
            if proof_bill is None
            and today is not None
            and classification.subtype is DocumentSubtype.PAYMENT_PROOF
            else []
        ),
    )


def _proof_bill(conn: Connection, document_id: UUID) -> ExistingProofBill | None:
    """Phase 3 D143: the bill a payment proof is linked to (document_link target document),
    with the acquisition cost rows that carry it as Beleg and its private_cost rows in force,
    for build_proposal to compare amounts and years. None for any other document, and for a
    proof linked to several bills (Q-O54: compared with none of them)."""
    bills = [
        link.target_id
        for link in documents.list_links(conn, document_id)
        if link.target_type is LinkTarget.DOCUMENT
    ]
    if len(bills) != 1:
        return None
    (bill,) = bills
    return _bill_facts(conn, bill)


def _proof_candidates(conn: Connection, document_id: UUID, today: date) -> list[ExistingProofBill]:
    """Phase 3 D149: for a payment proof linked to no bill, the bills that still wait for a
    proof: those whose payment_proof.missing the property's draft raises in any year it can be
    asked for (rules.tax.draft.bills_without_proof, the draft's own test), on the property the
    proof is linked to, else on every property of the workspace. Empty for a proof linked to
    a bill already (several bills, Q-O54)."""
    links = documents.list_links(conn, document_id)
    if any(link.target_type is LinkTarget.DOCUMENT for link in links):
        return []
    linked = sorted(
        {link.target_id for link in links if link.target_type is LinkTarget.PROPERTY}, key=str
    )
    property_ids = linked or [p.id for p in properties.list_properties(conn)]
    bills: set[UUID] = set()
    for property_id in property_ids:
        checklist, docs = checklist_facts(conn, property_id)
        years = available_years(checklist, today)
        facts = tax.draft_facts(
            conn,
            property_id,
            tax_year=years[-1],
            today=today,
            checklist=checklist,
            documents=docs,
            private_proofs=True,
        )
        for year in years:
            bills.update(bills_without_proof(facts, year))
    bills.discard(document_id)
    return [_bill_facts(conn, bill) for bill in sorted(bills, key=str)]


def _bill_facts(conn: Connection, bill: UUID) -> ExistingProofBill:
    """A bill's acquisition cost rows (it as Beleg) and private_cost rows in force, and its
    title (else the file's name) and date for the card (D143, D149), and the cost lines its
    reading states, for a proof of one of them (D150, services.tax.bill_lines)."""
    document = documents.get_document(conn, bill)
    typing = None if document is None else document.typing
    rows = tax_inputs.current_of_document(conn, bill, TaxInputKind.PRIVATE_COST)
    costs = conn.execute(
        select(cost_table).where(cost_table.c.document_id == bill).order_by(cost_table.c.id)
    ).all()
    return ExistingProofBill(
        document_id=bill,
        acquisition_costs=[
            ExistingCost.model_validate(
                {k: v for k, v in c._mapping.items() if k in ExistingCost.model_fields}
            )
            for c in costs
        ],
        private_costs=[
            ExistingPrivateCost(
                id=r.id,
                tax_year=r.tax_year,
                label=r.value.label,  # type: ignore[union-attr]
                amount=r.value.amount,  # type: ignore[union-attr]
            )
            for r in rows
            if r.tax_year is not None
        ],
        title=(None if typing is None else typing.title)
        or (None if document is None else document.original_filename),
        document_date=None if typing is None else typing.document_date,
        parts=tax.bill_lines(conn, bill),  # D150: its cost lines
    )


def _current_bescheid_rows(conn: Connection, *, tax_year: int | None = None) -> list[Any]:
    """The workspace's current household bescheid_zve rows (D129), oldest first, under the
    caller's row-level security."""
    return [
        row
        for row in income_inputs.list_income_inputs(conn, tax_year=tax_year)
        if row.kind == IncomeInputKind.BESCHEID_ZVE and row.owner_id is None
    ]


def _contract_facts(conn: Connection, property_id: UUID) -> dict[str, Any]:
    """D139: the MEA and the WE label the property's Kaufvertrag states, from the current
    reading of each purchase_contract document linked to it (a link exists once its proposal
    was applied, or the user linked it). A fact two contracts state differently is left out,
    so a Wohnflächenberechnung's row is never picked by a guess."""
    meas: list[JsonValue] = []
    labels: list[str] = []
    for related in documents.list_related_documents(
        conn, target_type=LinkTarget.PROPERTY, target_id=property_id
    ):
        typing = related.document.typing
        if typing is None or typing.subtype is not DocumentSubtype.PURCHASE_CONTRACT:
            continue
        reading = documents.current_extraction(conn, related.document.id)
        if reading is None or reading.kind is not ExtractionKind.PURCHASE_CONTRACT:
            continue
        mea, label = (getattr(reading.fields, name).value for name in ("mea", "unit_label"))
        if isinstance(mea, dict):
            meas.append(mea)
        if isinstance(label, str) and label.strip():
            labels.append(label)
    facts: dict[str, Any] = {}
    if meas and all(_same_share(m, meas[0]) for m in meas):
        facts["contract_mea"] = meas[0]
    if labels and len({" ".join(label.split()).casefold() for label in labels}) == 1:
        facts["contract_unit_label"] = labels[0]
    return facts


def _same_share(a: Any, b: Any) -> bool:
    try:
        return a["numerator"] * b["denominator"] == b["numerator"] * a["denominator"]
    except (KeyError, TypeError):
        return False


def _document_date(conn: Connection, document_id: UUID | None) -> date | None:
    document = None if document_id is None else documents.get_document(conn, document_id)
    typing = None if document is None else document.typing
    return None if typing is None else typing.document_date


def _current_pay_rows(
    conn: Connection, *, owner_id: UUID | None = None
) -> list[tuple[ExistingPayRow, dict[str, Any]]]:
    """The workspace's current pay-document rows (neither withdrawn nor superseded), each
    with its stored value. Read under the caller's row-level security: this workspace only."""
    later = return_input_table.alias("later")
    query = select(return_input_table).where(
        return_input_table.c.kind.in_(_PAY_KINDS),
        return_input_table.c.withdrawn.is_(False),
        ~exists().where(later.c.supersedes_id == return_input_table.c.id),
    )
    if owner_id is not None:
        query = query.where(return_input_table.c.owner_id == owner_id)
    rows = conn.execute(query.order_by(return_input_table.c.recorded_at, return_input_table.c.id))
    out = []
    for r in rows:
        value = dict(r.value)
        row = ExistingPayRow(
            id=r.id,
            owner_id=r.owner_id,
            kind=r.kind,
            tax_year=r.tax_year,
            employer=value.get("employer") or "",
            period_month=value.get("period_month"),
            period_from=value.get("period_from"),
            period_to=value.get("period_to"),
            document_id=r.document_id,
        )
        out.append((row, value))
    return out


# --- Deciding ------------------------------------------------------------------------------


def reject_proposal(ctx: CommandContext, proposal_id: UUID) -> Proposal:
    """POST /proposals/{id}/reject: the "no" as an eval signal; no domain writes."""
    current = _lock(ctx.conn, proposal_id)
    _require_decidable(current)
    updated = _set(
        ctx,
        proposal_id,
        status=ProposalStatus.REJECTED,
        error_code=None,
        decided_at=ctx.now,
        decided_by=_user_id(ctx),
    )
    append_event(ctx, EventType.PROPOSAL_REJECTED, proposal_id, _noted(updated))
    return updated


def apply_proposal(
    ctx: CommandContext, proposal_id: UUID, body: ProposalApplyBody, *, iban_key: bytes
) -> ProposalApplyResult:
    """§6.5: every record through its phase 1 service, in dependency order, with the values
    the user confirmed in `body` (never the proposal's own values: D14). One transaction,
    the caller's: any failure rolls everything back and the proposal stays `ready`.
    A record left out of `body` counts as skipped."""
    current = _lock(ctx.conn, proposal_id)
    _require_decidable(current)
    by_key = {r.key: r for r in current.records}
    given: dict[str, ProposalApplyRecord] = {}
    for record in body.records:
        if record.key not in by_key:
            raise ValueError(f"proposal {proposal_id} has no record {record.key!r}")
        if record.key in given:
            raise ValueError(f"record {record.key!r} appears twice")
        given[record.key] = record
    for key in body.effective_date_corrections:
        if key not in by_key or by_key[key].mode != "correction":
            raise ValueError(f"effective_date_corrections names no correction record {key!r}")

    run = _Apply(ctx, current, given, body.effective_date_corrections, iban_key)
    ordered = sorted(current.records, key=lambda r: _APPLY_ORDER.get(_prefix(r.key), 99))
    for record in ordered:
        run.apply(record)

    applied = [run.applied[r.key] for r in current.records]
    decisions = {r.key: _decisions(r, given.get(r.key)) for r in current.records}
    linked_bill = run.entity(PROOF_LINK_KEY)
    updated = _set(
        ctx,
        proposal_id,
        status=ProposalStatus.APPLIED,
        error_code=None,
        decided_at=ctx.now,
        decided_by=_user_id(ctx),
        decisions={k: {f: d.value for f, d in v.items()} for k, v in decisions.items()},
        applied_entities=[a.model_dump(mode="json") for a in applied],
    )
    append_event(
        ctx,
        EventType.PROPOSAL_APPLIED,
        proposal_id,
        ProposalApplied(
            document_id=current.document_id,
            kind=current.kind,
            records=[
                AppliedRecord(
                    key=a.key,
                    mode=a.mode,
                    skipped=a.skipped,
                    entity_type=a.entity_type,
                    entity_id=a.entity_id,
                    decisions=decisions[a.key],
                )
                for a in applied
            ],
        ),
    )
    if linked_bill is not None:
        # D149 (review F1): a bill the user picked was not the one the moves were built for;
        # the D143 moves for it are proposed now, after this proposal is applied.
        propose_proof_years(
            ctx,
            current.document_id,
            proposed_rows=frozenset(
                r.target_id
                for r in current.records
                if _prefix(r.key) == "private_cost_year" and r.target_id is not None
            ),
        )
    return ProposalApplyResult(proposal=updated, applied=applied)


class _Apply:
    """One apply: the entity each record became, so later records can refer to it."""

    def __init__(
        self,
        ctx: CommandContext,
        proposal: Proposal,
        given: dict[str, ProposalApplyRecord],
        corrections: dict[str, date],
        iban_key: bytes,
    ) -> None:
        self.ctx = ctx
        self.proposal = proposal
        self.document_id = proposal.document_id
        self.given = given
        self.corrections = corrections
        self.iban_key = iban_key
        self.records = {r.key: r for r in proposal.records}
        self.applied: dict[str, AppliedEntity] = {}
        # Review F6: the document's current private_cost rows before this apply wrote any.
        self._private_before: list[TaxInput] | None = None

    def apply(self, record: ProposalRecord) -> None:
        chosen = self.given.get(record.key)
        if chosen is None or chosen.skip:
            self._done(record, None, skipped=True)
            return
        handler = self._HANDLERS.get(_prefix(record.key))
        if handler is None:
            raise ValueError(f"record {record.key!r} cannot be applied in this step")
        entity_id = handler(self, record, dict(chosen.values))
        self._done(record, entity_id)

    def _done(
        self, record: ProposalRecord, entity_id: UUID | None, *, skipped: bool = False
    ) -> None:
        self.applied[record.key] = AppliedEntity(
            key=record.key,
            entity_type=record.entity_type,
            mode=record.mode,
            skipped=skipped,
            entity_id=entity_id,
        )

    def entity(self, key: str) -> UUID | None:
        """The entity a record of this apply created or patched, None when skipped or
        absent (only records already applied, i.e. earlier in the order)."""
        done = self.applied.get(key)
        return None if done is None else done.entity_id

    def property_id(self, needed_by: str) -> UUID:
        """The property this document is about: the property record's entity, else the
        property the proposal links the document to."""
        found = self.entity("property")
        if found is None:
            link = self.records.get("link:property")
            found = None if link is None else link.target_id
        if found is None:
            raise ValueError(f"record {needed_by!r} needs the property, whose record is skipped")
        return found

    # -- the records

    def _property(self, record: ProposalRecord, values: dict[str, Any]) -> UUID:
        if record.mode == "create":
            data = _undot(values)
            address = data.get("address")
            if "label" not in data and isinstance(address, dict):
                data["label"] = _default_label(address)
            created = properties.create_property(
                self.ctx, _validate(record.key, PropertyCreate, data)
            )
            return created.id
        target = _target(record)
        current = properties.get_property(self.ctx.conn, target)
        if current is None:
            raise NotFoundError(EntityType.PROPERTY, target)
        data = _undot(values)
        notarized_on = data.pop("notarized_on", None)
        purchase_date = data.pop("purchase_date", None)
        price = data.pop("purchase_price", None)
        if "address" in data and isinstance(data["address"], dict):
            data["address"] = current.address.model_dump(mode="json") | data["address"]
        patch = _validate(record.key, PropertyPatch, data)
        if patch.model_fields_set:
            properties.update_property(self.ctx, target, **patch.changes())
        if notarized_on is not None or price is not None:
            correction = _validate(
                record.key,
                NotarizedOnCorrection,
                {
                    "new_date": notarized_on or current.notarized_on.isoformat(),
                    "purchase_price": price,
                },
            )
            properties.correct_notarized_on(
                self.ctx, target, correction.new_date, purchase_price=correction.purchase_price
            )
        if purchase_date is not None:
            new = _validate(record.key, DateCorrection, {"new_date": purchase_date})
            properties.correct_purchase_date(self.ctx, target, new.new_date)
        return target

    def _correction(self, record: ProposalRecord, values: dict[str, Any]) -> UUID:
        target = _target(record)
        field = record.key.split(":", 1)[1]
        new_date = self.corrections.get(record.key) or values.get(field)
        corrected = _validate(record.key, DateCorrection, {"new_date": new_date})
        match (str(record.entity_type), field):
            case ("property", "notarized_on"):
                properties.correct_notarized_on(self.ctx, target, corrected.new_date)
            case ("property", "purchase_date"):
                properties.correct_purchase_date(self.ctx, target, corrected.new_date)
            case _:
                raise ValueError(f"record {record.key!r} cannot be applied in this step")
        return target

    def _unit(self, record: ProposalRecord, values: dict[str, Any]) -> UUID:
        if record.mode == "create":
            data: dict[str, Any] = {"property_id": str(self.property_id(record.key))} | values
            return units.create_unit(self.ctx, _validate(record.key, UnitCreate, data)).id
        target = _target(record)
        patch = _validate(record.key, UnitPatch, values)
        if patch.model_fields_set:
            units.update_unit(self.ctx, target, **patch.changes())
        return target

    def _ownership(self, record: ProposalRecord, values: dict[str, Any]) -> UUID:
        prop = self.property_id(record.key)
        body = _validate(record.key, OwnershipBody, values)
        properties.set_ownership(self.ctx, prop, body.shares, effective_date=body.effective_date)
        return prop

    def _price_split(self, record: ProposalRecord, values: dict[str, Any]) -> UUID:
        data = {
            "property_id": str(self.property_id(record.key)),
            "source": PriceSplitSource.PURCHASE_CONTRACT.value,
            "evidence_document_id": str(self.document_id),
        } | values
        # D133: a split read from the contract becomes the AfA basis when none is chosen yet.
        return properties.record_price_split(
            self.ctx, _validate(record.key, PriceSplitCreate, data), select_if_none=True
        ).id

    def _cost(self, record: ProposalRecord, values: dict[str, Any]) -> UUID:
        if record.mode == "create":
            data: dict[str, Any] = {"property_id": str(self.property_id(record.key))}
            # D145: the rest of a Grunderwerbsteuer row that one buyer's notice did not cover
            # belongs to the other notice, so it is created without this document as its Beleg.
            if record.key.endswith(f":{OTHER_NOTICE}"):
                self._require_lowered_row(record.key)
            else:
                data["document_id"] = str(self.document_id)
            data |= values
            created = properties.add_acquisition_cost(
                self.ctx, _validate(record.key, AcquisitionCostCreate, data)
            )
            return created.id
        # A patch attaches this document as the row's Beleg (§6.2: a row without one).
        target = _target(record)
        patch = _validate(
            record.key, AcquisitionCostPatch, values | {"document_id": str(self.document_id)}
        )
        properties.update_acquisition_cost(self.ctx, target, **patch.changes())
        return target

    def _require_lowered_row(self, key: str) -> None:
        """D145 review: the rest row is right only together with its companion patch that
        lowers the row to this notice. Kept the old amount (Betrag "behalten") or the patch
        card skipped while the rest card stays ticked would count the rest twice, so the
        apply is refused and nothing is written. Read from the answers, not the apply order."""
        companion = self.given.get(key.removesuffix(f":{OTHER_NOTICE}"))
        if companion is None or companion.skip or companion.values.get("amount") is None:
            raise RuleViolationError(
                [PROPOSAL_OTHER_NOTICE_WITHOUT_PATCH], f"record {key!r} without its patch"
            )

    def _loan(self, record: ProposalRecord, values: dict[str, Any]) -> UUID:
        if record.mode == "create":
            data = dict(values)
            if data.get("property_id") is None:
                data["property_id"] = str(self._loan_property(record.key))
            request = _validate(record.key, LoanCreateRequest, data)
            build_schedule(
                LoanTerms.model_validate(request.model_dump(include=set(LoanTerms.model_fields)))
            )
            created = loans.create_loan(
                self.ctx, request.as_create(), iban_key=self.iban_key, signed_on=request.signed_on
            )
            return created.id
        target = _target(record)
        corrected = {f: values.pop(f) for f in _LOAN_CORRECTED if f in values}
        patch = _validate(record.key, LoanPatch, values)
        if any(v is not None for v in corrected.values()):
            change = _validate(
                record.key,
                DisbursementCorrection,
                {
                    "new_date": corrected.get("disbursed_on"),
                    "principal": corrected.get("principal"),
                },
            )
            loans.correct_disbursement(
                self.ctx, target, new_date=change.new_date, principal=change.principal
            )
        if patch.model_fields_set:
            loans.update_loan(self.ctx, target, **patch.changes(), iban_key=self.iban_key)
        loan = loans.get_loan(self.ctx.conn, target)
        assert loan is not None
        build_schedule(loan.terms)
        return target

    def _loan_property(self, key: str) -> UUID:
        """A loan contract names no property: the one this apply is about, else the
        workspace's only property."""
        found = self.entity("property")
        if found is not None:
            return found
        props = properties.list_properties(self.ctx.conn)
        if len(props) == 1:
            return props[0].id
        raise ValueError(f"record {key!r} needs property_id: the workspace has {len(props)}")

    def _tenant(self, record: ProposalRecord, values: dict[str, Any]) -> UUID:
        if record.mode == "create":
            data = _validate(record.key, TenantCreate, values)
            return tenants.create_tenant(self.ctx, data, iban_key=self.iban_key).id
        target = _target(record)
        patch = _validate(record.key, TenantPatch, values)
        if patch.model_fields_set:
            tenants.update_tenant(self.ctx, target, **patch.changes(), iban_key=self.iban_key)
        else:
            # Unchanged, or a patch with nothing in it: the tenant row is still locked (FOR
            # KEY SHARE) and checked, so an apply naming an erased tenant conflicts, and one
            # racing erase_tenant either waits for it or makes the erasure wait and see this
            # proposal in its scope (applied_entities names the tenant).
            tenants.require_unerased_tenants(self.ctx.conn, [target])
        return target

    def _tenancy(self, record: ProposalRecord, values: dict[str, Any]) -> UUID:
        if record.mode == "create":
            data = dict(values)
            if "parties" not in data:
                data["parties"] = self._parties(record.key)
            created = _validate(record.key, TenancyCreate, data)
            rent = self._initial_rent()
            tenancy = tenancies.create_tenancy(
                self.ctx, created, initial_rent=rent, iban_key=self.iban_key
            )
            return tenancy.id
        target = _target(record)
        patch = _validate(record.key, TenancyPatch, values)
        if patch.model_fields_set:
            tenancies.update_tenancy(self.ctx, target, **patch.changes(), iban_key=self.iban_key)
        return target

    def _parties(self, key: str) -> list[dict[str, str]]:
        """The tenant records of this apply, in key order: the first named is primary."""
        ids = [
            self.entity(k)
            for k in sorted(self.records, key=_tenant_number)
            if _prefix(k) == "tenant"
        ]
        ids = [i for i in ids if i is not None]
        if not ids:
            raise ValueError(f"record {key!r} needs a tenant: every tenant record is skipped")
        return [
            {
                "tenant_id": str(i),
                "role": (TenancyRole.PRIMARY if n == 0 else TenancyRole.CO_TENANT).value,
            }
            for n, i in enumerate(ids)
        ]

    def _initial_rent(self) -> InitialRent | None:
        chosen = self.given.get("rent")
        if "rent" not in self.records or chosen is None or chosen.skip:
            return None
        data = {"document_id": str(self.document_id)} | chosen.values
        return _validate("rent", InitialRent, data)

    def _rent(self, record: ProposalRecord, values: dict[str, Any]) -> UUID:
        """Recorded by the tenancy record as its first row (create_tenancy's initial_rent)."""
        tenancy_id = self.entity("tenancy")
        tenancy_record = self.records.get("tenancy")
        if tenancy_id is None or tenancy_record is None or tenancy_record.mode != "create":
            raise ValueError("record 'rent' needs the tenancy record it is the first rent of")
        rows = tenancies.list_rent_schedule(self.ctx.conn, tenancy_id)
        return rows[0].id

    def _deposit_received(self, record: ProposalRecord, values: dict[str, Any]) -> UUID:
        """A Kaution receipt (Kautionsnachweis, D42) on the tenancy the proposal found; the
        tenancy is the entity. Unchanged: already recorded, nothing is written."""
        if record.mode == "unchanged":
            return _target(record)
        data = dict(values)
        if data.get("tenancy_id") is None:
            link = self.records.get("link:tenancy")
            found = self.entity("tenancy") or (None if link is None else link.target_id)
            data["tenancy_id"] = None if found is None else str(found)
        receipt = _validate(record.key, _DepositValues, data)
        tenancies.record_deposit_received(
            self.ctx, receipt.tenancy_id, amount=receipt.amount, received_on=receipt.received_on
        )
        return receipt.tenancy_id

    def _typing(self, record: ProposalRecord, values: dict[str, Any]) -> UUID:
        """The typing, and the document's hand-entry fields the record carries (D132). The
        fields the model read are already the document's `ai` extraction version
        (record_read_result); a hand field sent (null: cleared) that differs from what the
        document holds is written as the typing form writes it (D37: into the reading's own
        field, a new version only when a value changed); one not sent stays as it is. A hand
        field another record asks for instead (record.hand_from: an invoice's Betrag is the
        Nebenkosten row's amount) takes the value the user applied in that record, so the
        reading and the record agree (J1); that record skipped or unchanged, or the field
        not sent there, leaves the reading's value as it is. Refused with
        document.extraction_stale when the document's fields changed since the proposal was
        built, as the typing form is."""
        hand = {
            name: values.pop(name)
            for name in list(values)
            if name in HAND_FIELDS and name in record.fields
        }
        for name, carrier in record.hand_from.items():
            chosen = self.given.get(carrier.key)
            source = self.records.get(carrier.key)
            # Read from the body, not from what was applied: a tax input is applied after
            # the typing (_APPLY_ORDER). An unchanged record writes nothing (the screen sends
            # it as skipped): its value is the reading's already.
            if chosen is None or chosen.skip or source is None or source.mode == "unchanged":
                continue
            if carrier.field in chosen.values:
                hand[name] = chosen.values[carrier.field]
        typing = _validate(record.key, DocumentTyping, values)
        fields = None
        shown_version = None
        if hand:
            latest = documents.current_extraction(self.ctx.conn, self.document_id)
            shown = documents.hand_fields(latest).values
            typed = _validate(
                record.key, ManualDocumentFields, shown.model_dump(mode="json") | hand
            )
            # No extraction yet and nothing typed: no empty version 1 (as the typing form).
            if latest is not None or typed != shown:
                fields = typed
                shown_version = self._shown_version(record)
        documents.type_document(
            self.ctx, self.document_id, typing, fields, shown_version=shown_version
        )
        return self.document_id

    def _shown_version(self, record: ProposalRecord) -> int:
        """The extraction version the proposal's hand fields were read from: its own
        reading's, else the version pinned on the typing record when it was built
        (hand_version, F1), else (a record stored without it) the document's latest when the
        proposal was last updated (0: none)."""
        extraction_id = self.proposal.extraction_id
        if extraction_id is not None:
            version = documents.extraction_version(self.ctx.conn, extraction_id)
            assert version is not None  # extraction rows are never deleted
            return version
        if record.hand_version is not None:
            return record.hand_version
        return documents.extraction_version_at(
            self.ctx.conn, self.document_id, self.proposal.updated_at
        )

    def _link(self, record: ProposalRecord, values: dict[str, Any]) -> UUID:
        target_type = LinkTarget(record.key.split(":", 1)[1])
        target = values.get("target_id") or record.target_id
        if target is None:
            source = _LINK_SOURCE.get(target_type)
            target = None if source is None else self.entity(source)
        if target is None:
            raise ValueError(f"record {record.key!r} has no {target_type} to link to")
        target = _validate(record.key, _LinkValues, {"target_id": str(target)}).target_id
        if target_type is LinkTarget.DOCUMENT:
            # Phase 3 D149: a payment proof's bill, one of the bills the proposal offered,
            # linked as the to-do's upload links it (D143): to the bill and its properties.
            offered = {o.value for o in record.options.get("target_id", [])}
            if str(target) not in offered:
                raise ValueError(f"record {record.key!r}: that bill was not offered")
            documents.attach_payment_proof(self.ctx, self.document_id, target)
            return target
        # Already linked: the existing link is the one the apply asks for.
        with suppress(documents.DuplicateLinkError):
            documents.link_document(self.ctx, self.document_id, target_type, target)
        return target

    def _private_cost_year(self, record: ProposalRecord, values: dict[str, Any]) -> UUID:
        """Phase 3 D143: a payment proof shows its bill's private_cost row was paid in
        another year; the row counts in the year paid (§11 Abs. 2 EStG). A correction keeps
        its row's year (tax_inputs' _check_supersedes), so the move is two append-only writes:
        the row withdrawn (its value repeated), then the same value created in the new year,
        its document still the bill. The year asked is the user's (the record's tax_year).
        Nothing is written when the row is in that year already; NotFoundError when the row
        is not a private_cost of this workspace; 422 when it is no longer current."""
        if record.target_id is None:
            raise ValueError(f"record {record.key!r} names no tax input")
        year = _validate(record.key, _TaxYearValue, {"tax_year": values.get("tax_year")}).tax_year
        row = tax_inputs.get_tax_input(self.ctx.conn, record.target_id)
        if row is None or row.kind is not TaxInputKind.PRIVATE_COST:
            raise NotFoundError(tax_inputs.TAX_INPUT, record.target_id)
        # D149: a proof uploaded without a bill moves the rows of the bill it is linked to in
        # this apply only (link:document, applied before), never of a bill the user did not
        # pick.
        if PROOF_LINK_KEY in self.records and self.entity(PROOF_LINK_KEY) != row.document_id:
            raise ValueError(
                f"record {record.key!r}: its bill is not the one this proof is linked to"
            )
        try:
            return tax_inputs.move_private_cost_year(self.ctx, row.id, year).id
        except RuleViolationError as exc:
            if exc.codes != (tax_inputs.TAX_INPUT_NOT_CURRENT,):
                raise
            raise ValueError(
                f"record {record.key!r}: tax input {row.id} was changed since the proposal"
            ) from exc

    def _tax_input(self, record: ProposalRecord, values: dict[str, Any]) -> UUID:
        """A certificate's figure (phase 3 §9) through services/tax_inputs, with this document
        as its evidence. The certified interest belongs to the loan's property; the Rücklage
        to the property the document is linked to. Only the proposed fields are taken from
        the values (never a supersedes_id or a withdrawal). A current row of the same
        question with the same value and document: nothing is written; another value or
        document: the new row supersedes it (a corrected certificate). A cost paid from
        another account (tax_input:private_cost:<category>, phase 2 D135) is one of any number
        per property and year: the row it replaces is _replaced_private_cost's, never another
        bill's."""
        kind = TaxInputKind(record.key.split(":")[1])
        if kind not in _PREFILL_TAX_INPUTS:
            raise ValueError(f"record {record.key!r} cannot be applied in this step")
        given = _undot({f: values[f] for f in _PREFILL_TAX_INPUTS[kind] if f in values})
        if kind is TaxInputKind.GRUNDSTEUER_SCHEDULE:
            given["value"] = grundsteuer_schedule_value(given.get("value", {}))
        value = given.get("value")
        if kind is TaxInputKind.PRIVATE_COST and isinstance(value, dict) and value.get("category"):
            # The class follows the category the user confirmed or changed on the card
            # (financing exactly for loan_fees, D135), so changing one never contradicts the other.
            value["cost_class"] = "financing" if value["category"] == "loan_fees" else "other"
            if value["category"] not in {c.value for c in LIMIT_15_CATEGORIES}:
                # phase 3 D137: the work date and the net go only with a cost the 15 % test
                # counts; a part moved to another category drops them
                value.pop("performed_on", None)
                value.pop("amount_net", None)
        if kind is TaxInputKind.INTEREST_CERTIFIED:
            if given.get("loan_id") is None:
                linked = self.entity("link:loan")
                given["loan_id"] = None if linked is None else str(linked)
            if given["loan_id"] is None:
                raise ValueError(f"record {record.key!r} needs the loan the interest was paid on")
            loan_id = _validate(record.key, _LoanRef, {"loan_id": given["loan_id"]}).loan_id
            loan = loans.get_loan(self.ctx.conn, loan_id)
            if loan is None:
                raise NotFoundError(EntityType.LOAN, loan_id)
            property_id = loan.property_id
        else:
            property_id = self.entity("link:property") or self.property_id(record.key)
        data = _validate(
            record.key,
            TaxInputCreate,
            given
            | {
                "property_id": str(property_id),
                "kind": kind.value,
                "document_id": str(self.document_id),
            },
        )
        if kind is TaxInputKind.PRIVATE_COST:
            elsewhere = self._recorded_in_another_year(data)
            if elsewhere is not None:
                return elsewhere.id
        current = tax_inputs.find_current(
            self.ctx.conn, property_id, kind, tax_year=data.tax_year, loan_id=data.loan_id
        )
        if kind is TaxInputKind.PRIVATE_COST:
            current = self._replaced_private_cost(record, data, current)
        if current:
            (row,) = current  # one per year (and loan): create_tax_input refuses a second
            if row.value == data.value and row.document_id == data.document_id:
                return row.id
            data = data.model_copy(update={"supersedes_id": row.id})
        return tax_inputs.create_tax_input(self.ctx, property_id, data).id

    def _recorded_in_another_year(self, data: TaxInputCreate) -> TaxInput | None:
        """Review F6: a current private_cost row of this bill in another year than the record's,
        recorded before this apply: by the card of the private line that paid it (phase 6
        D161 path 2, the year paid) or moved to the year paid (D143). The bill's costs are then
        recorded already, and this record writes nothing (its row is that one), so no bill
        counts twice; a row of the same year is replaced as before (_replaced_private_cost).
        Rows this apply wrote (several parts of one bill) never count."""
        if self._private_before is None:
            self._private_before = tax_inputs.current_of_document(
                self.ctx.conn, self.document_id, TaxInputKind.PRIVATE_COST
            )
        other = [r for r in self._private_before if r.tax_year != data.tax_year]
        return other[0] if other else None

    def _replaced_private_cost(
        self, record: ProposalRecord, data: TaxInputCreate, current: list[TaxInput]
    ) -> list[TaxInput]:
        """The current private_cost row this record replaces (phase 2 D135), as a list of at
        most one: a row of the same year from this document that no other private-cost
        record of this proposal claims by its key's category; the one of the category
        applied, else the latest. So a bill read again and applied under another category
        replaces its row instead of adding a second, while a mixed bill's rows of other
        records stay. A row without a document (typed by hand, D111) is never replaced."""
        prefix = "tax_input:private_cost:"
        claimed = {
            key.removeprefix(prefix)
            for key in self.records
            if key.startswith(prefix) and key != record.key
        }
        mine = [
            r
            for r in current
            if r.document_id == data.document_id
            and getattr(r.value, "category", None) not in claimed
        ]
        category = getattr(data.value, "category", None)
        same = [r for r in mine if getattr(r.value, "category", None) == category]
        return (same or mine)[-1:]

    def _return_input(self, record: ProposalRecord, values: dict[str, Any]) -> UUID:
        """A pay document's row (phase 4b §8.2) through services/return_inputs.create_input,
        with this document as its evidence. Only the record's own fields are taken from the
        values (never a supersedes_id, a withdrawal, a note or an unread list: unread is
        computed by return_input_value, Q-O29). The owner is the confirmed owner_id, else the
        owner linked in this apply (the two compared as UUIDs), and must be an owner of this
        workspace (read under RLS). A current row of the same kind from this document under
        another owner or tax year is refused, never superseded: withdraw it first.
        The current row it replaces is the one rules.prefill.replaced_pay_row names (the
        review's note says the same); the same value from the same document again writes
        nothing."""
        kind = record.key.split(":", 1)[1]
        if kind not in _PAY_KINDS:
            raise ValueError(f"record {record.key!r} cannot be applied in this step")
        given = {f: values[f] for f in record.fields if f in values}
        linked = self.entity("link:owner")
        chosen = given.get("owner_id")
        if chosen is not None:
            chosen = _validate(record.key, _OwnerRef, {"owner_id": str(chosen)}).owner_id
        if linked is not None:
            linked = _validate(record.key, _OwnerRef, {"owner_id": str(linked)}).owner_id
        if chosen is not None and linked is not None and chosen != linked:
            raise ValueError(f"record {record.key!r} names another owner than link:owner")
        owner_id = chosen if chosen is not None else linked
        if owner_id is None:
            raise ValueError(f"record {record.key!r} needs the owner the pay belongs to")
        if owners.get_owner(self.ctx.conn, owner_id) is None:
            raise NotFoundError(EntityType.OWNER, owner_id)
        data = _validate(
            record.key,
            ReturnInputCreate,
            {
                "owner_id": str(owner_id),
                "kind": kind,
                "tax_year": given.get("tax_year"),
                "document_id": str(self.document_id),
                "value": return_input_value(kind, given),
            },
        )
        # The same document already recorded under another owner or year: never superseded
        # across owners or years; the user withdraws that row first.
        for row, _ in _current_pay_rows(self.ctx.conn):
            same_document = row.kind == data.kind and row.document_id == self.document_id
            if same_document and (row.owner_id != owner_id or row.tax_year != data.tax_year):
                raise ValueError(
                    f"this document is already recorded as return_input {row.id}; withdraw it first"
                )
        current = _current_pay_rows(self.ctx.conn, owner_id=owner_id)
        replaced = replaced_pay_row(
            kind,
            [row for row, _ in current],
            owner_id=owner_id,
            tax_year=data.tax_year,
            document_id=self.document_id,
            value={f"value.{k}": v for k, v in data.value.model_dump(mode="json").items()},
        )
        if replaced is not None:
            stored = next(value for row, value in current if row.id == replaced.id)
            same = RETURN_INPUT_VALUES[data.kind].model_validate(stored) == data.value
            if same and replaced.document_id == data.document_id:
                return replaced.id
            data = data.model_copy(update={"supersedes_id": replaced.id})
        # Step 1's service (coder A, phase 4b §12): imported here so this module loads
        # without it while the two steps are built in parallel.
        from hausbuch.services import return_inputs

        return return_inputs.create_input(self.ctx, data).id

    def _income_input(self, record: ProposalRecord, values: dict[str, Any]) -> UUID:
        """An Einkommensteuerbescheid's zvE (phase 4b D129) through
        services/income_inputs.create_income_input: household scope, kind bescheid_zve, this
        document as the Bescheid. Only tax_year and value.amount are taken from the values
        (never a document, a supersedes_id, a withdrawal or a note: the row and its event
        carry no free text). This document already recorded for another year is refused,
        never superseded: withdraw that row first. The year's current row with the same
        amount from this document: nothing is written; otherwise the new row supersedes it
        (an Änderungsbescheid, or a corrected amount)."""
        kind = record.key.split(":", 1)[1]
        if kind != IncomeInputKind.BESCHEID_ZVE.value:
            raise ValueError(f"record {record.key!r} cannot be applied in this step")
        given = _undot({f: values[f] for f in ("tax_year", "value.amount") if f in values})
        data = _validate(
            record.key,
            IncomeInputCreate,
            {
                "kind": kind,
                "tax_year": given.get("tax_year"),
                "document_id": str(self.document_id),
                "value": given.get("value") or {},
            },
        )
        for row in _current_bescheid_rows(self.ctx.conn):
            if row.document_id == self.document_id and row.tax_year != data.tax_year:
                raise ValueError(
                    f"this document is already recorded as income_input {row.id}; withdraw it first"
                )
        current = _current_bescheid_rows(self.ctx.conn, tax_year=data.tax_year)
        if current:
            (row,) = current  # one per year: create_income_input refuses a second
            if row.value == data.value and row.document_id == data.document_id:
                return row.id
            data = data.model_copy(update={"supersedes_id": row.id})
        return income_inputs.create_income_input(self.ctx, data).id

    _HANDLERS: dict[str, Callable[["_Apply", ProposalRecord, dict[str, Any]], UUID]] = {
        "property": _property,
        "correction": _correction,
        "unit": _unit,
        "ownership": _ownership,
        "price_split": _price_split,
        "cost": _cost,
        "loan": _loan,
        "tenant": _tenant,
        "tenancy": _tenancy,
        "rent": _rent,
        "deposit_received": _deposit_received,
        "typing": _typing,
        "link": _link,
        "tax_input": _tax_input,
        "return_input": _return_input,
        "income_input": _income_input,
        "private_cost_year": _private_cost_year,
    }


class _LinkValues(Schema):
    target_id: UUID


class _TaxYearValue(Schema):
    tax_year: TaxYear


class _LoanRef(Schema):
    loan_id: UUID


class _OwnerRef(Schema):
    owner_id: UUID


# The tax inputs a pre-fill proposes (rules.prefill, phase 3 §9) and the fields the apply
# takes from the user's values.
_PREFILL_TAX_INPUTS = {
    TaxInputKind.INTEREST_CERTIFIED: ("tax_year", "loan_id", "value.amount"),
    TaxInputKind.RUECKLAGE_SPENT: ("tax_year", "value.amount"),
    # phase 2 D135: a bill's part paid from another account, one record per category
    TaxInputKind.PRIVATE_COST: (
        "tax_year",
        "value.cost_class",
        "value.category",
        "value.label",
        "value.amount",
        # phase 3 D137: on a repair, the work date and the net for the 15 % test
        "value.performed_on",
        "value.amount_net",
    ),
    # phase 5 D126: the Bescheid's schedule, one date and amount per slot (rules.prefill)
    TaxInputKind.GRUNDSTEUER_SCHEDULE: (
        "tax_year",
        "value.annual_amount",
        "value.rhythm",
        "value.payment_mode",
        *(f"value.{field}_{n}" for n in range(1, 5) for field in ("due_on", "amount")),
    ),
    # phase 2 D147: a Gutachten's shorter useful life (phase 3 D79), the years and first year
    # derived by code (rules.prefill._useful_life); the Gutachten is the row's document
    TaxInputKind.USEFUL_LIFE: ("tax_year", "value.years"),
}


class _DepositValues(DepositReceivedBody):
    """A deposit_received record's final values (the tenancy is never null)."""

    tenancy_id: UUID


# --- Queries -----------------------------------------------------------------------------


def get_proposal(conn: Connection, proposal_id: UUID) -> Proposal | None:
    row = conn.execute(
        select(proposal_table).where(proposal_table.c.id == proposal_id)
    ).one_or_none()
    return None if row is None else _to_proposal(row)


def list_document_proposals(conn: Connection, document_id: UUID) -> list[Proposal]:
    """The document's proposals, newest first."""
    rows = conn.execute(
        select(proposal_table)
        .where(proposal_table.c.document_id == document_id)
        .order_by(proposal_table.c.created_at.desc(), proposal_table.c.id.desc())
    )
    return [_to_proposal(r) for r in rows]


def return_input_evidence(conn: Connection, row: ReturnInput) -> ReturnInputEvidence:
    """The row's Beleg and, when an applied proposal of that document created or kept the row
    (its applied_entities name the row), each value's page, quote and decision: the newest
    such proposal. owner_id is left out (matched by code, never quoted). Read-only."""
    found = ReturnInputEvidence(return_input_id=row.id, document_id=row.document_id)
    if row.document_id is None:
        return found
    names_row = [{"entity_type": "return_input", "entity_id": str(row.id)}]
    p = proposal_table.c
    match = conn.execute(
        select(p.id, p.fields, p.decisions, p.applied_entities, p.decided_at, p.scrubbed_at)
        .where(
            p.document_id == row.document_id,
            p.status == ProposalStatus.APPLIED.value,
            p.applied_entities.contains(names_row),
        )
        .order_by(p.decided_at.desc(), p.id.desc())
        .limit(1)
    ).one_or_none()
    if match is None:
        return found
    found = found.model_copy(update={"proposal_id": match.id, "decided_at": match.decided_at})
    if match.scrubbed_at is not None or match.fields is None:
        return found.model_copy(update={"scrubbed": True})
    key = next(a["key"] for a in match.applied_entities or [] if a.get("entity_id") == str(row.id))
    record = next(
        (ProposalRecord.model_validate(r) for r in match.fields if r.get("key") == key), None
    )
    if record is None:
        return found
    decisions = (match.decisions or {}).get(key, {})
    values = [
        ReturnInputEvidenceValue(
            field=name,
            page=None if value.source is None else value.source.page,
            quote=None if value.source is None else value.source.quote,
            decision=decisions.get(name),
        )
        for name, value in record.fields.items()
        if name != "owner_id"
    ]
    return found.model_copy(update={"values": values})


# --- Helpers -----------------------------------------------------------------------------


def _ai_tenant_documents(conn: Connection) -> bool:
    """The workspace's D16 setting (RLS shows only the current workspace's row)."""
    value = conn.execute(select(workspace_table.c.ai_tenant_documents)).scalar_one_or_none()
    return bool(value)


def _lock(conn: Connection, proposal_id: UUID) -> Proposal:
    row = conn.execute(
        select(proposal_table).where(proposal_table.c.id == proposal_id).with_for_update()
    ).one_or_none()
    if row is None:
        raise NotFoundError(EntityType.PROPOSAL, proposal_id)
    return _to_proposal(row)


def _set(ctx: CommandContext, proposal_id: UUID, **values: Any) -> Proposal:
    for name, value in values.items():
        if isinstance(value, ProposalStatus):
            values[name] = value.value
        elif value is None and name in _JSONB_COLUMNS:
            values[name] = null()  # SQL NULL; a JSONB column would otherwise store JSON null
    row = ctx.conn.execute(
        update(proposal_table)
        .where(proposal_table.c.id == proposal_id)
        .values(**values, updated_at=ctx.now)
        .returning(*proposal_table.c)
    ).one()
    return _to_proposal(row)


def _require_decidable(proposal: Proposal) -> None:
    if proposal.status in _DECIDED:
        raise RuleViolationError([PROPOSAL_ALREADY_DECIDED], f"proposal {proposal.id}")
    if proposal.status not in _DECIDABLE or not proposal.records:
        raise RuleViolationError([PROPOSAL_NOT_READY], f"proposal {proposal.id}")


def _user_id(ctx: CommandContext) -> UUID | None:
    """app_user.id of a person's command ("user:<uuid>"), else None."""
    if ctx.actor.actor_type is not ActorType.USER:
        return None
    try:
        return UUID(ctx.actor.actor_id.removeprefix("user:"))
    except ValueError:
        return None


def _noted(proposal: Proposal) -> ProposalNoted:
    return ProposalNoted(
        document_id=proposal.document_id,
        kind=proposal.kind,
        record_keys=[r.key for r in proposal.records],
    )


def _to_proposal(row) -> Proposal:
    m = row._mapping
    return Proposal(
        id=m["id"],
        workspace_id=m["workspace_id"],
        created_at=m["created_at"],
        updated_at=m["updated_at"],
        document_id=m["document_id"],
        kind=m["kind"],
        status=m["status"],
        error_code=m["error_code"],
        target_mode=m["target_mode"],
        contains_tenant_data=m["contains_tenant_data"],
        records=[ProposalRecord.model_validate(r) for r in (m["fields"] or [])],
        extraction_id=m["extraction_id"],
        ai_call_id=m["ai_call_id"],
        decided_at=m["decided_at"],
    )


def _prefix(key: str) -> str:
    return key.split(":", 1)[0]


def _tenant_number(key: str) -> tuple[int, str]:
    tail = key.split(":", 1)[1] if ":" in key else ""
    return (int(tail), key) if tail.isdigit() else (1 << 30, key)


def _target(record: ProposalRecord) -> UUID:
    assert record.target_id is not None  # ProposalRecord's own validator
    return record.target_id


def _undot(values: dict[str, Any]) -> dict[str, Any]:
    """{"address.city": "Berlin"} -> {"address": {"city": "Berlin"}}."""
    out: dict[str, Any] = {}
    for name, value in values.items():
        head, dot, tail = name.partition(".")
        if dot:
            nested = out.setdefault(head, {})
            if not isinstance(nested, dict):
                raise ValueError(f"field {head!r} given both whole and by part")
            nested[tail] = value
        else:
            if head in out:
                raise ValueError(f"field {head!r} given both whole and by part")
            out[head] = value
    return out


def _default_label(address: dict[str, Any]) -> str:
    """A property's label when the user gave none: street and house number."""
    parts = [str(address.get(k) or "").strip() for k in ("street", "house_number")]
    return " ".join(p for p in parts if p)[:100]


def _validate[M: BaseModel](key: str, model: type[M], data: dict[str, Any]) -> M:
    """model.model_validate(data), with every error's loc prefixed by the record key (§6.5)
    and no input kept, so the API's 422 renders it like any request validation error."""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        errors = [
            {"type": e["type"], "loc": (key, *e["loc"]), "input": None}
            | ({"ctx": e["ctx"]} if "ctx" in e else {})
            for e in exc.errors(include_url=False)
        ]
        raise ValidationError.from_exception_data(exc.title, errors, hide_input=True) from None


def _decisions(
    record: ProposalRecord, chosen: ProposalApplyRecord | None
) -> dict[str, FieldDecision]:
    """Per proposed field what the user did: the client's own decision when it sent one,
    else accepted (same value), edited (another value) or cleared (left out or null)."""
    if chosen is None or chosen.skip:
        return {f: FieldDecision.SKIPPED for f in record.fields}
    out: dict[str, FieldDecision] = {}
    for name, proposed in record.fields.items():
        if name in chosen.decisions:
            out[name] = chosen.decisions[name]
        elif chosen.values.get(name) is None:
            out[name] = FieldDecision.ACCEPTED if proposed.value is None else FieldDecision.CLEARED
        elif _same(chosen.values[name], proposed.value):
            out[name] = FieldDecision.ACCEPTED
        else:
            out[name] = FieldDecision.EDITED
    for name in chosen.values:
        if name not in out:
            out[name] = chosen.decisions.get(name, FieldDecision.EDITED)
    for name, decision in chosen.decisions.items():
        out.setdefault(name, decision)
    return out


def _same(given: JsonValue, proposed: JsonValue) -> bool:
    if given == proposed:
        return True
    if isinstance(given, (str, int)) and isinstance(proposed, (str, int)):
        try:
            return Decimal(str(given)) == Decimal(str(proposed))
        except InvalidOperation:
            return False
    return False
