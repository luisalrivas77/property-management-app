"""Pre-fill proposals (phase 2 spec §6).

A proposal row is created `queued` when a document is to be read (upload with `ai`, or a
re-run), the `document_read` job reports back through record_read_result /
record_read_failure, and the user decides: apply_proposal writes through the phase 1
services only, in the caller's one transaction (D14, §6.5), reject_proposal records the
"no" without touching a domain table. Nothing here calls a model.

Statuses (§3.1): queued -> running -> ready | failed | blocked_tenant_data; ready ->
applied | rejected | superseded (a re-run). The job's two functions are no-ops once the
proposal left queued/running (a re-run superseded it while the job ran).
"""

from collections.abc import Callable
from contextlib import suppress
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from pydantic import BaseModel, JsonValue, ValidationError
from sqlalchemy import Connection, insert, null, select, update

from hausbuch.db.tables import proposal as proposal_table
from hausbuch.db.tables import workspace as workspace_table
from hausbuch.jobs import enqueue
from hausbuch.rules.errors import (
    AI_BLOCKED_TENANT_DATA,
    PROPOSAL_ALREADY_DECIDED,
    PROPOSAL_NOT_READY,
)
from hausbuch.rules.loan_schedule import build_schedule
from hausbuch.rules.prefill import build_proposal, extraction_kind
from hausbuch.schemas.ai import ClassifyOutput, ExtractedValue
from hausbuch.schemas.api import (
    AcquisitionCostPatch,
    AppliedEntity,
    DateCorrection,
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
from hausbuch.schemas.common import Schema
from hausbuch.schemas.document import DocumentTyping
from hausbuch.schemas.enums import (
    ActorType,
    DocumentSubtype,
    EntityType,
    ExtractionKind,
    LinkTarget,
    PriceSplitSource,
    ProposalStatus,
    ProposalTargetMode,
    TenancyRole,
)
from hausbuch.schemas.events import (
    AppliedRecord,
    DocumentClassified,
    DocumentExtracted,
    DocumentReadFailed,
    EventType,
    ProposalApplied,
    ProposalNoted,
)
from hausbuch.schemas.extraction import EXTRACTION_MODELS, _Typing
from hausbuch.schemas.loan import LoanTerms
from hausbuch.schemas.property import (
    AcquisitionCostCreate,
    PriceSplitCreate,
    PropertyCreate,
    UnitCreate,
)
from hausbuch.schemas.proposal import (
    ExistingCost,
    ExistingLoan,
    ExistingOwner,
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
from hausbuch.schemas.tenancy import InitialRent, TenancyCreate, TenantCreate

from . import documents, loans, owners, properties, tenancies, tenants, units
from .context import CommandContext
from .errors import NotFoundError, RuleViolationError
from .events import append_event

# Undecided and still in the job's hands: a re-run supersedes these, the job may write them.
_IN_FLIGHT = (ProposalStatus.QUEUED, ProposalStatus.RUNNING)
_SUPERSEDABLE = (ProposalStatus.QUEUED, ProposalStatus.RUNNING, ProposalStatus.READY)
# A blocked proposal still holds the classifier's typing (§5.5 step 3): it can be applied
# (typing only) or rejected like a ready one.
_DECIDABLE = (ProposalStatus.READY, ProposalStatus.BLOCKED_TENANT_DATA)
_DECIDED = (ProposalStatus.APPLIED, ProposalStatus.REJECTED)

# §6.5: property -> corrections -> unit -> ownership -> price split -> costs -> loan ->
# tenants -> tenancy (with the rent row) -> typing -> links. By the key's prefix.
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
}
# The record whose entity a link record points at when the proposal had no target yet.
_LINK_SOURCE = {
    LinkTarget.PROPERTY: "property",
    LinkTarget.LOAN: "loan",
    LinkTarget.TENANCY: "tenancy",
}
_LOAN_CORRECTED = ("principal", "disbursed_on")  # through correct_disbursement
# Tenant documents by type (§5.5 step 3); a letter from a tenant is caught by the
# classifier's contains_tenant_data.
_TENANT_SUBTYPES = frozenset(
    {DocumentSubtype.LEASE, DocumentSubtype.HANDOVER_PROTOCOL, DocumentSubtype.DEPOSIT_RECORD}
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
    enqueued, so no job and no model call."""
    if documents.get_document(ctx.conn, document_id) is None:
        raise NotFoundError(EntityType.DOCUMENT, document_id)
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
    same bytes uploaded again (D9), the document's pending proposal (queued, running or
    ready) if it has one, so a double upload costs no second model call; unless this upload
    says tenant data and the pending one did not: then it is re-run with the flag. After a
    decision, a failure or a block the upload asks for a new read."""
    if not created:
        pending = [
            p for p in list_document_proposals(ctx.conn, document_id) if p.status in _SUPERSEDABLE
        ]
        if pending and (pending[0].contains_tenant_data or not contains_tenant_data):
            return pending[0]
        if pending:
            return rerun_proposal(ctx, document_id, contains_tenant_data=True)
    return request_read(ctx, document_id, contains_tenant_data=contains_tenant_data)


def rerun_proposal(
    ctx: CommandContext, document_id: UUID, *, contains_tenant_data: bool = False
) -> Proposal:
    """POST /documents/{id}/proposals: supersede the document's undecided proposals
    (queued, running, ready) and request a new read. The tenant-data flag is copied from
    the document's latest proposal; `contains_tenant_data` can add it, never remove it
    (once said, a document stays a tenant document, D16)."""
    if documents.get_document(ctx.conn, document_id) is None:
        raise NotFoundError(EntityType.DOCUMENT, document_id)
    latest = list_document_proposals(ctx.conn, document_id)
    flagged = contains_tenant_data or bool(latest and latest[0].contains_tenant_data)
    pending = ctx.conn.execute(
        select(proposal_table)
        .where(proposal_table.c.document_id == document_id)
        .where(proposal_table.c.status.in_([s.value for s in _SUPERSEDABLE]))
        .order_by(proposal_table.c.created_at, proposal_table.c.id)
        .with_for_update()
    ).all()
    for row in pending:
        old = _to_proposal(row)
        _set(ctx, old.id, status=ProposalStatus.SUPERSEDED)
        append_event(ctx, EventType.PROPOSAL_SUPERSEDED, old.id, _noted(old))
    return request_read(ctx, document_id, contains_tenant_data=flagged)


# --- The job's two seams -----------------------------------------------------------------


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

    Appends document.classified and, with values, document.extracted (the job does not).
    A no-op returning the proposal as stored when it is no longer queued or running.
    """
    current = _lock(ctx.conn, proposal_id)
    if current.status not in _IN_FLIGHT:
        return current
    if (values is None) != (extract_call_id is None):
        raise ValueError("extracted values come with the extract call's id, and only then")
    document_id = current.document_id
    kind = extraction_kind(classification)
    # The job applies the gate (§5.5 step 3); this repeats it, so no classifier result can
    # bring a tenant document past a workspace that has not allowed them (D16).
    if not blocked_tenant_data and not _ai_tenant_documents(ctx.conn):
        blocked_tenant_data = (
            current.contains_tenant_data
            or classification.contains_tenant_data
            or classification.subtype in _TENANT_SUBTYPES
        )
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

    extraction: _Typing = _Typing()
    built_kind: ExtractionKind | None = None
    extraction_id = None
    if values is not None and not blocked_tenant_data:
        if kind is None:  # no field set for this type: only the typing fields can apply
            extraction = _Typing.model_validate(values)
        else:
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

    context = prefill_context(ctx.conn, document_id, classification)
    records = build_proposal(built_kind, extraction, context)
    targeted = [r for r in records if r.mode != "create"]
    updated = _set(
        ctx,
        proposal_id,
        status=ProposalStatus.BLOCKED_TENANT_DATA if blocked_tenant_data else ProposalStatus.READY,
        error_code=AI_BLOCKED_TENANT_DATA if blocked_tenant_data else None,
        kind=None if kind is None else kind.value,
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
    conn: Connection, document_id: UUID, classification: ClassifyOutput
) -> PrefillContext:
    """The workspace's records as the plain facts build_proposal reads (§6.3)."""
    props = properties.list_properties(conn)
    unit_rows = [u for p in props for u in units.list_units(conn, p.id)]
    return PrefillContext(
        document_id=document_id,
        classification=classification,
        properties=[
            ExistingProperty.model_validate(
                p.model_dump(include=set(ExistingProperty.model_fields))
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
            )
            for p in props
            for loan in loans.list_loans(conn, p.id)
        ],
        tenancies=[
            ExistingTenancy(id=t.id, unit_id=t.unit_id, start_date=t.start_date)
            for u in unit_rows
            for t in tenancies.list_tenancies(conn, u.id)
        ],
        tenants=[
            ExistingTenant(id=t.id, full_name=t.full_name) for t in tenants.list_tenants(conn)
        ],
    )


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
            data = {"property_id": str(self.property_id(record.key))} | values
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
        return properties.record_price_split(
            self.ctx, _validate(record.key, PriceSplitCreate, data)
        ).id

    def _cost(self, record: ProposalRecord, values: dict[str, Any]) -> UUID:
        if record.mode == "create":
            data = {
                "property_id": str(self.property_id(record.key)),
                "document_id": str(self.document_id),
            } | values
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

    def _typing(self, record: ProposalRecord, values: dict[str, Any]) -> UUID:
        # The fields the model read are already the document's `ai` extraction version
        # (record_read_result); the apply sets the typing only.
        typing = _validate(record.key, DocumentTyping, values)
        documents.type_document(self.ctx, self.document_id, typing)
        return self.document_id

    def _link(self, record: ProposalRecord, values: dict[str, Any]) -> UUID:
        target_type = LinkTarget(record.key.split(":", 1)[1])
        target = values.get("target_id") or record.target_id
        if target is None:
            source = _LINK_SOURCE.get(target_type)
            target = None if source is None else self.entity(source)
        if target is None:
            raise ValueError(f"record {record.key!r} has no {target_type} to link to")
        target = _validate(record.key, _LinkValues, {"target_id": str(target)}).target_id
        # Already linked: the existing link is the one the apply asks for.
        with suppress(documents.DuplicateLinkError):
            documents.link_document(self.ctx, self.document_id, target_type, target)
        return target

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
        "typing": _typing,
        "link": _link,
    }


class _LinkValues(Schema):
    target_id: UUID


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
