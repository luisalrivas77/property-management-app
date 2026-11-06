"""The recorded-eval format, and replaying a recording through the parser (§13, D29).

A recording is one JSON file per document in tests/evals/recordings/, written by record.py
from real API responses:

    {
      "format": 1,
      "doc": "rechnung_notar",
      "synthetic": false,
      "recorded_on": "2026-11-07",
      "sdk_version": "2.6.0",
      "classify": {"prompt_version": ..., "request_sha256": ..., "message": {...}},
      "extract": [{"prompt_version": ..., "request_sha256": ..., "page_offset": 0,
                   "message": {...}}]
    }

`message` is the SDK's Message as JSON (`message.model_dump(mode="json")`), so the replay goes
through the same conversion as production (hausbuch.ai.client.message_to_response). The requests
are rebuilt exactly as jobs.document_read builds them (first three pages for classify, one
request per chunk of HAUSBUCH_AI_MAX_PAGES pages for extract).

`synthetic_recording` builds a recording in the same shape from a document's own text, for
the harness's own tests. It is marked `"synthetic": true`, is never written to recordings/,
and is not, and does not pretend to be, real API output.
"""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from anthropic.types import Message
from make_docs import Doc, doc_pdf

from hausbuch.ai import documents
from hausbuch.ai.client import ModelRequest, ModelResponse, message_to_response
from hausbuch.ai.requests import (
    CLASSIFY_PAGES,
    classify_request,
    extract_request,
    load_prompt,
    parse_classification,
)
from hausbuch.jobs.document_read import merge_values  # the job's own chunk merge
from hausbuch.rules.extraction_parse import extraction_kind, parse_lines
from hausbuch.schemas.ai import ClassifyOutput, ExtractedValue
from hausbuch.schemas.enums import ExtractionKind
from hausbuch.schemas.extraction import EXTRACTION_MODELS
from hausbuch.settings import Settings

FORMAT = 1
RECORDINGS_DIR = Path(__file__).parent / "recordings"


@dataclass(frozen=True)
class Requests:
    classify: ModelRequest
    extract: list[tuple[int, ModelRequest]]  # (page offset, request)


def eval_settings(**overrides: Any) -> Settings:
    """Settings for building requests: only the AI fields matter (models, geo, pages)."""
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


def build_requests(doc: Doc, settings: Settings) -> Requests:
    data = doc_pdf(doc)
    pages = documents.page_count(data)
    first = documents.pdf_pages(data, 0, min(CLASSIFY_PAGES, pages))
    classify = classify_request(
        settings, documents.document_block(first, documents.PDF, citations=False)
    )
    kind = ExtractionKind(doc.kind)
    extract = []
    for start, stop in documents.chunks(pages, settings.ai_max_pages):
        block = documents.document_block(
            documents.pdf_pages(data, start, stop), documents.PDF, citations=True
        )
        extract.append((start, extract_request(settings, kind, block)))
    return Requests(classify, extract)


def response_of(entry: dict) -> ModelResponse:
    return message_to_response(Message.model_validate(entry["message"]), 0)


@dataclass(frozen=True)
class Replay:
    classification: ClassifyOutput
    kind: ExtractionKind | None
    values: dict[str, ExtractedValue]


def replay(recording: dict) -> Replay:
    """Classification and merged values from a recording, as the job would compute them."""
    classification = parse_classification(response_of(recording["classify"]).text)
    kind = extraction_kind(classification.type, classification.subtype)
    merged: dict[str, ExtractedValue] = {}
    if kind is not None:
        for entry in recording["extract"]:
            values = parse_lines(response_of(entry).blocks, kind, page_offset=entry["page_offset"])
            merge_values(merged, values)
        fields = EXTRACTION_MODELS[kind].model_fields
        merged = {name: merged.get(name, ExtractedValue.missing()) for name in fields}
    return Replay(classification, kind, merged)


def stale_reasons(recording: dict, requests: Requests) -> list[str]:
    """Why a recording no longer matches what the code would send (prompt or document
    changed): such a recording must be re-recorded with record.py."""
    reasons = []
    entries = [("classify", recording["classify"], requests.classify)]
    entries += [
        (f"extract[{i}]", entry, request)
        for i, (entry, (_, request)) in enumerate(
            zip(recording["extract"], requests.extract, strict=False)
        )
    ]
    if len(recording["extract"]) != len(requests.extract):
        reasons.append("extract: the number of chunks changed")
    for label, entry, request in entries:
        if entry["prompt_version"] != request.prompt_version:
            reasons.append(f"{label}: prompt changed")
        elif entry["request_sha256"] != request.sha256().hex():
            reasons.append(f"{label}: request changed (document, model or settings)")
    return reasons


# --- synthetic recordings (harness tests only) ------------------------------------------


def _message(model: str, content: list[dict]) -> dict:
    return {
        "id": "msg_synthetic",
        "type": "message",
        "role": "assistant",
        "model": model,
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "content": content,
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "inference_geo": "us",
        },
    }


def _find(doc: Doc, written: str) -> tuple[int, str] | None:
    """The page and line that state `written` (for a `; ` list, all of its items)."""
    items = written.split("; ")
    for number, lines in enumerate(doc.pages, start=1):
        for line in lines:
            if all(item in line for item in items):
                return number, line
    return None


def synthetic_recording(doc: Doc, requests: Requests, as_written: dict[str, str]) -> dict:
    """A recording in the real format, answered from the document's own text: each field in
    `as_written` (field -> the value as the document writes it) is cited with the line that
    contains it. SYNTHETIC: for testing the harness, never API output."""
    classify_json = json.dumps(
        {
            "type": doc.type,
            "subtype": doc.subtype,
            "contains_tenant_data": doc.contains_tenant_data,
            "language": "de",
            "confidence": "high",
        }
    )
    content: list[dict] = []
    for index, (key, written) in enumerate(as_written.items()):
        found = _find(doc, written)
        prefix = ("\n" if index else "") + f"{key}: "
        if found is None:
            content.append({"type": "text", "text": prefix + written, "citations": None})
            continue
        page, line = found
        content.append({"type": "text", "text": prefix, "citations": None})
        content.append(
            {
                "type": "text",
                "text": written,
                "citations": [
                    {
                        "type": "page_location",
                        "cited_text": line,
                        "document_index": 0,
                        "document_title": None,
                        "start_page_number": page,
                        "end_page_number": page + 1,
                    }
                ],
            }
        )
    classify = requests.classify
    (offset, extract), *_ = requests.extract
    return {
        "format": FORMAT,
        "doc": doc.name,
        "synthetic": True,
        "recorded_on": date(2026, 11, 6).isoformat(),
        "sdk_version": "synthetic",
        "classify": {
            "prompt_version": classify.prompt_version,
            "request_sha256": classify.sha256().hex(),
            "message": _message(
                classify.model, [{"type": "text", "text": classify_json, "citations": None}]
            ),
        },
        "extract": [
            {
                "prompt_version": extract.prompt_version,
                "request_sha256": extract.sha256().hex(),
                "page_offset": offset,
                "message": _message(extract.model, content),
            }
        ],
    }


def prompt_versions() -> dict[str, str]:
    return {name: load_prompt(name).version for name in ("classify", "extract")}
