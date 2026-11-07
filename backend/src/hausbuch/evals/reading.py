"""The document_read job's requests and parsing, without the job (D29, D41).

Shared by `hausbuch evals run` (Luis's real documents, live) and tests/evals/ (the recorded
synthetic set, replayed in CI), so an eval sends exactly what the job sends:

- classify: the first CLASSIFY_PAGES pages of a PDF (an image whole), citations off;
- extract: one request per chunk of `ai_max_pages` pages (an image is one chunk), citations
  on, for the extraction kind the classification maps to;
- the answers: parse_classification, then parse_lines per chunk and the job's own chunk merge
  (jobs.document_read.merge_values), every field of the kind present (missing ones empty).

jobs.document_read builds the same requests inline (it records an ai_call per call and stops
on the first unreadable chunk); tests/db/test_evals_reading_matches_job.py runs the job with a
fake client and checks that it sends exactly these requests.
"""

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from hausbuch.ai import documents
from hausbuch.ai.client import ModelRequest, ModelResponse
from hausbuch.ai.requests import CLASSIFY_PAGES, classify_request, extract_request
from hausbuch.jobs.document_read import merge_values
from hausbuch.rules.extraction_parse import parse_lines
from hausbuch.schemas.ai import ExtractedValue
from hausbuch.schemas.enums import ExtractionKind
from hausbuch.schemas.extraction import EXTRACTION_MODELS
from hausbuch.settings import Settings


def eval_settings(**overrides: Any) -> Settings:
    """Settings for building requests: only the AI fields matter (models, geo, pages,
    timeout); the rest are placeholders that never connect anywhere."""
    values: dict[str, Any] = {
        "database_url": "postgresql+psycopg://unused@localhost/unused",
        "iban_hmac_key": bytes(range(32)),
        "session_key": bytes(range(1, 33)),
        "rp_id": "evals",
        "origin": "https://evals",
        "blob_store": "local",
        "blob_root": Path("/nonexistent"),
    } | overrides
    return Settings(**values)


def page_total(data: bytes, media_type: str) -> int:
    """Pages of a PDF (an image is one). Raises documents.DocumentInputError like the job."""
    documents.check_media_type(media_type)
    return documents.page_count(data) if media_type == documents.PDF else 1


def classify_request_for(settings: Settings, data: bytes, media_type: str) -> ModelRequest:
    pages = page_total(data, media_type)
    first = (
        documents.pdf_pages(data, 0, min(CLASSIFY_PAGES, pages))
        if media_type == documents.PDF
        else data
    )
    return classify_request(settings, documents.document_block(first, media_type, citations=False))


def extract_requests_for(
    settings: Settings, kind: ExtractionKind, data: bytes, media_type: str
) -> list[tuple[int, ModelRequest]]:
    """(page offset, request) per chunk."""
    is_pdf = media_type == documents.PDF
    pages = page_total(data, media_type)
    ranges = documents.chunks(pages, settings.ai_max_pages) if is_pdf else [(0, 1)]
    requests = []
    for start, stop in ranges:
        chunk = documents.pdf_pages(data, start, stop) if is_pdf else data
        block = documents.document_block(chunk, media_type, citations=True)
        requests.append((start, extract_request(settings, kind, block)))
    return requests


def chunk_values(
    kind: ExtractionKind, response: ModelResponse, page_offset: int
) -> dict[str, ExtractedValue]:
    """One chunk's answer; empty when no line names a field of `kind` (the job's
    ai.unparsable)."""
    return parse_lines(response.blocks, kind, page_offset=page_offset)


def merged_values(
    kind: ExtractionKind, chunks: Iterable[dict[str, ExtractedValue]]
) -> dict[str, ExtractedValue]:
    """The chunks merged as the job merges them, with every field of the kind present."""
    merged: dict[str, ExtractedValue] = {}
    for values in chunks:
        merge_values(merged, values)
    fields = EXTRACTION_MODELS[kind].model_fields
    return {name: merged.get(name, ExtractedValue.missing()) for name in fields}
