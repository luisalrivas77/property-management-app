"""Document vault: immutable originals, typed metadata, versioned extractions, links."""

from datetime import date, datetime
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from .common import Money, Record, Schema, Sha256Hex, TaxYear
from .enums import (
    DocumentSource,
    DocumentSubtype,
    DocumentType,
    ExtractionSource,
    LinkTarget,
    RetentionClass,
    RetentionHold,
)


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


class DocumentExtraction(Schema):
    """Append-only. A new version is a new row; the highest version is current."""

    id: UUID
    workspace_id: UUID
    document_id: UUID
    version: int = Field(ge=1)
    source: ExtractionSource
    fields: ManualDocumentFields
    schema_version: int = 1
    created_at: datetime


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
      unit, tenancy or loan of that property); null = still owned.
    tenancy_end_dates: one entry per tenancy linked; null = still running.
    """

    type: DocumentType | None
    subtype: DocumentSubtype | None = None
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
