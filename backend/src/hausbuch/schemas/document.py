"""Document vault: immutable originals, typed metadata, versioned extractions, links."""

from datetime import date, datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from .common import Money, Record, Schema, Sha256Hex, TaxYear
from .enums import (
    DocumentSource,
    DocumentSubtype,
    DocumentType,
    ExtractionKind,
    ExtractionSource,
    LinkTarget,
    RetentionClass,
    RetentionHold,
)
from .extraction import EXTRACTION_MODELS, AnyExtraction


class DocumentFile(Schema):
    """The original bytes, content-addressed. Never updated (principle 6)."""

    workspace_id: UUID
    sha256: Sha256Hex
    size_bytes: int = Field(gt=0)
    media_type: str
    storage_key: str  # "{workspace_id}/sha256/{sha256}" in the EU bucket
    created_at: datetime


class DocumentTyping(Schema):
    """What a person (phase 1) or the classifier (phase 2) says the document is."""

    type: DocumentType
    subtype: DocumentSubtype | None = None
    title: str | None = None
    document_date: date | None = None
    tax_year: TaxYear | None = None


class Document(Record):
    """One per file per workspace: re-uploading the same bytes returns this row."""

    file_sha256: Sha256Hex
    source: DocumentSource
    original_filename: str | None
    received_at: datetime
    updated_at: datetime
    typing: DocumentTyping | None = None  # null until typed


class ManualDocumentFields(Schema):
    """The fields a person types in phase 1. Per-type schemas replace this in phase 2."""

    amount_total: Money | None = None
    period_start: date | None = None
    period_end: date | None = None
    counterparty: str | None = None
    reference: str | None = None  # invoice no., contract no., Aktenzeichen
    notes: str | None = None

    @model_validator(mode="after")
    def _period(self) -> Self:
        if self.period_start and self.period_end and self.period_end < self.period_start:
            raise ValueError("period_end must not be before period_start")
        return self


# The six hand-entry fields by name, in the form's order (D37).
HandField = Literal[
    "amount_total", "period_start", "period_end", "counterparty", "reference", "notes"
]


class HandFields(Schema):
    """The document's "Felder (von Hand)" as the typing form shows them (phase 2 D37).

    For a `manual` extraction (or none) the values are its fields and all six are available.
    For an AI kind, `values` holds what the reading stored for the fields that kind has a place
    for (rules.hand_fields.HAND_FIELD_MAP), and `available` names exactly those; a reading the
    hand types cannot hold (a low-confidence raw text in a money field) shows as null.
    """

    values: ManualDocumentFields
    available: list[HandField]


class DocumentExtraction(Schema):
    """Append-only. A new version is a new row; the highest version is current.

    kind (phase 2, migration 0005) says which field set `fields` holds: `manual` is
    ManualDocumentFields, every other kind its per-type model in schemas/extraction.py. The
    kind is a column, not a key inside the fields, so the union is resolved from it here
    rather than by a Pydantic discriminator. ai_call_id is the call an `ai` version came from
    (D18); a `manual` version on top of it (Luis edited extracted fields) has none.
    """

    id: UUID
    workspace_id: UUID
    document_id: UUID
    version: int = Field(ge=1)
    source: ExtractionSource
    fields: ManualDocumentFields | AnyExtraction
    schema_version: int = 1
    created_at: datetime
    kind: ExtractionKind = ExtractionKind.MANUAL
    ai_call_id: UUID | None = None

    @model_validator(mode="before")
    @classmethod
    def _fields_by_kind(cls, data: Any) -> Any:
        if not isinstance(data, dict) or not isinstance(data.get("fields"), dict):
            return data
        kind = ExtractionKind(data.get("kind", ExtractionKind.MANUAL))
        model = ManualDocumentFields if kind is ExtractionKind.MANUAL else EXTRACTION_MODELS[kind]
        return data | {"fields": model.model_validate(data["fields"])}

    @model_validator(mode="after")
    def _provenance(self) -> Self:
        expected = (
            ManualDocumentFields
            if self.kind is ExtractionKind.MANUAL
            else EXTRACTION_MODELS[self.kind]
        )
        if type(self.fields) is not expected:
            raise ValueError(f"fields of kind {self.kind} must be {expected.__name__}")
        if (self.source is ExtractionSource.AI) != (self.ai_call_id is not None):
            raise ValueError("an ai extraction carries its ai_call_id, a manual one none")
        return self


class DocumentLink(Schema):
    """Links a document to exactly one entity (the thing it proves)."""

    id: UUID
    workspace_id: UUID
    document_id: UUID
    target_type: LinkTarget
    target_id: UUID
    created_at: datetime


# --- Retention (rules.retention) ---------------------------------------------


class RetentionPolicy(Schema):
    """User-adjustable periods in years. Defaults from ARCHITECTURE.md; not legal advice."""

    ownership_records_years: int = Field(default=10, ge=0)
    tax_records_years: int = Field(default=10, ge=0)
    tenancy_records_years: int = Field(default=4, ge=0)
    tenant_contact_years: int = Field(default=4, ge=0)


class DocumentRetentionInput(Schema):
    """Facts a retention decision depends on, gathered from the document and its links.

    ownership_end_dates: one entry per property the document is linked to (directly, or via a
      unit, tenancy or loan of that property), or backs as cost evidence; null = still owned.
    tenancy_end_dates: one entry per tenancy linked; null = still running.
    cost_basis_reference: the document is recorded as the evidence of an acquisition cost or a
      building/land split, so the AfA basis and the §23 gain are built from it. It is then an
      ownership record whatever its subtype (spec amendment 1, Luis 5 Nov 2026).
    """

    type: DocumentType | None
    subtype: DocumentSubtype | None = None
    cost_basis_reference: bool = False
    tax_year: TaxYear | None = None
    document_date: date | None = None
    received_on: date
    ownership_end_dates: list[date | None] = Field(default_factory=list)
    tenancy_end_dates: list[date | None] = Field(default_factory=list)


class TenantRetentionInput(Schema):
    tenancy_end_dates: list[date | None]


class RetentionResult(Schema):
    """Exactly one of retain_until / hold is set. retention_class is null only when untyped."""

    retention_class: RetentionClass | None
    retain_until: date | None = None
    hold: RetentionHold | None = None

    @model_validator(mode="after")
    def _one_of(self) -> Self:
        if (self.retain_until is None) == (self.hold is None):
            raise ValueError("set exactly one of retain_until and hold")
        if self.retention_class is None and self.hold is not RetentionHold.UNTYPED:
            raise ValueError("retention_class may be null only for an untyped document")
        return self
