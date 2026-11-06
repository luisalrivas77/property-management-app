"""The AI layer's shapes: model outputs, where a value came from, and AI call provenance.

Spec: docs/phases/phase-2.md §4.1, §5. Nothing here reaches a domain table directly (D14): a
job writes an ai_call row, a document_extraction version and a proposal, and only a user's
apply writes domain rows through the phase 1 services.

Confidence in this module is an enum, never a number: the model is bad at numbers, and for
extracted values it is computed by code from the checks that passed (D15), never reported by
the model.
"""

from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from .common import Record, Schema, Sha256Hex
from .enums import DocumentSubtype, DocumentType


class AiJob(StrEnum):
    """ai_call.job (migration 0005)."""

    CLASSIFY = "classify"
    EXTRACT = "extract"
    MATCH = "match"
    EXPLAIN = "explain"


ConfidenceLevel = Literal["high", "medium", "low"]
InferenceGeo = Literal["us", "global"]  # the only two the first-party API offers (§11.1)


class ClassifyOutput(Schema):
    """Structured output (JSON schema) of the classify job, Haiku (§5.3)."""

    type: DocumentType
    subtype: DocumentSubtype | None
    contains_tenant_data: bool  # the privacy gate's second check (D16)
    language: Literal["de", "en", "other"]
    confidence: ConfidenceLevel  # an enum, not a number: the model is bad at numbers


# --- Extraction with citations (D15) -------------------------------------------------


class PageCitation(Schema):
    """One `page_location` citation as the API returns it on a text block. Pages are
    1-indexed; end_page_number is exclusive, as in the API."""

    type: Literal["page_location"] = "page_location"
    cited_text: str
    document_index: int = Field(default=0, ge=0)
    document_title: str | None = None
    start_page_number: int = Field(ge=1)
    end_page_number: int = Field(ge=1)


class CitedTextBlock(Schema):
    """One text block of an extract response: text, plus the citations the API attached."""

    type: Literal["text"] = "text"
    text: str
    citations: list[PageCitation] = Field(default_factory=list)


class SourceRef(Schema):
    """Where a value came from: the page (1-indexed, page offset of a split PDF already
    added) and the cited text, verbatim."""

    page: int = Field(ge=1)
    quote: str


Check = Literal["cited", "value_in_quote", "validates", "derived"]


class ExtractedValue(Schema):
    """One field of a per-type extraction, or of a proposal record.

    checks say what code verified: `cited` (the API attached a citation, so `source` is set),
    `value_in_quote` (the value is recoverable from the cited text), `validates` (the value
    passes the target field's Pydantic type), `derived` (code computed it from the fields in
    `derived_from`, e.g. building_share). The confidence is a function of the checks, computed
    by rules.extraction_parse (fixture extraction_parse.yaml); this model only enforces that
    the checks are consistent with the other fields.
    """

    value: JsonValue | None
    source: SourceRef | None = None
    derived_from: list[str] = Field(default_factory=list)
    checks: list[Check] = Field(default_factory=list)
    confidence: ConfidenceLevel

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        checks = set(self.checks)
        if len(checks) != len(self.checks):
            raise ValueError("checks must not repeat")
        if ("cited" in checks) != (self.source is not None):
            raise ValueError("check 'cited' exactly when source is set")
        if ("derived" in checks) != bool(self.derived_from):
            raise ValueError("check 'derived' exactly when derived_from is non-empty")
        if "value_in_quote" in checks and "cited" not in checks:
            raise ValueError("value_in_quote needs a citation")
        if self.value is None and checks & {"value_in_quote", "validates"}:
            raise ValueError("a missing value cannot be in the quote or validate")
        return self

    @classmethod
    def missing(cls) -> "ExtractedValue":
        """A field the response did not state (`unbekannt` or an absent key)."""
        return cls(value=None, confidence="low")


# --- Provenance (D18) ----------------------------------------------------------------


class AiCall(Record):
    """Read shape of an ai_call row. The parsed output and the raw text are not in it: they
    can quote tenant data, stay in the table, and are scrubbed by tenant erasure."""

    job: AiJob
    model: str
    prompt_version: str
    inference_geo: str | None
    input_sha256: Sha256Hex
    input_tokens: int | None = Field(ge=0)
    output_tokens: int | None = Field(ge=0)
    cache_read_tokens: int | None = Field(ge=0)
    stop_reason: str | None
    duration_ms: int | None = Field(ge=0)
    error_code: str | None
    document_id: UUID | None
    transaction_id: UUID | None
