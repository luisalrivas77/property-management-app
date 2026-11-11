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

A document typed by code before its reading (a payment proof, phase 3 D143) is never sent to
the classifier: its recording has `"classify": null` and `"pretyped": {"type": ..., "subtype":
...}`, and the replay takes the job's own classification for that typing
(jobs.document_read.pretyped_classification).

`synthetic_recording` builds a recording in the same shape from a document's own text, for
the harness's own tests. It is marked `"synthetic": true`, is never written to recordings/,
and is not, and does not pretend to be, real API output.
"""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from anthropic.types import Message
from make_docs import Doc, doc_pdf

from hausbuch.ai import documents
from hausbuch.ai.client import ModelRequest, ModelResponse, message_to_response
from hausbuch.ai.requests import load_prompt, parse_classification

# The request builders and the merge are the package's (shared with `hausbuch evals run`).
from hausbuch.evals.reading import (
    chunk_values,
    classify_request_for,
    eval_settings,
    extract_requests_for,
    merged_values,
)
from hausbuch.jobs.document_read import pretyped_classification
from hausbuch.rules.extraction_parse import PropertyAddress, extraction_kind
from hausbuch.schemas.ai import ClassifyOutput, ExtractedValue
from hausbuch.schemas.document import DocumentTyping
from hausbuch.schemas.enums import DocumentSubtype, DocumentType, ExtractionKind
from hausbuch.settings import Settings

__all__ = ["eval_settings"]  # re-exported: test_recorded_evals and record.py import it here

FORMAT = 1
RECORDINGS_DIR = Path(__file__).parent / "recordings"
# The synthetic household's properties (make_docs.py), as the job would load them from the
# workspace: the only addresses a general reading keeps (PR #135 review). They never reach
# the model, so they change no request hash.
EVAL_PROPERTIES = (
    PropertyAddress("Lindenstraße", "7", "10115"),
    PropertyAddress("Kastanienweg", "4", "04109"),
)


@dataclass(frozen=True)
class Requests:
    classify: ModelRequest | None  # None: typed by code, never classified (D143)
    extract: list[tuple[int, ModelRequest]]  # (page offset, request)


def pretyped(doc: Doc) -> dict | None:
    """The typing code gives `doc` before its reading, as the recording stores it, or None
    for a document the classifier types (every one but a payment proof, D143)."""
    typing = DocumentTyping(
        type=DocumentType(doc.type),
        subtype=DocumentSubtype(doc.subtype) if doc.subtype is not None else None,
    )
    if pretyped_classification(typing) is None:
        return None
    return {"type": doc.type, "subtype": doc.subtype}


def _pretyped_classification(typing: dict) -> ClassifyOutput:
    classification = pretyped_classification(DocumentTyping.model_validate(typing))
    assert classification is not None, f"not a document typed by code: {typing}"
    return classification


def build_requests(doc: Doc, settings: Settings) -> Requests:
    data = doc_pdf(doc)
    classify = (
        None if pretyped(doc) is not None else classify_request_for(settings, data, documents.PDF)
    )
    # The general reading asks the fields of the subtype's group (D131), so the subtype goes
    # in as the job passes the classified one.
    subtype = DocumentSubtype(doc.subtype) if doc.subtype is not None else None
    extract = extract_requests_for(
        settings, ExtractionKind(doc.kind), data, documents.PDF, subtype=subtype
    )
    return Requests(classify, extract)


def response_of(entry: dict) -> ModelResponse:
    return message_to_response(Message.model_validate(entry["message"]), 0)


@dataclass(frozen=True)
class Replay:
    classification: ClassifyOutput
    kind: ExtractionKind
    values: dict[str, ExtractedValue]


def replay(recording: dict) -> Replay:
    """Classification and merged values from a recording, as the job would compute them."""
    if recording["classify"] is None:
        classification = _pretyped_classification(recording["pretyped"])
    else:
        classification = parse_classification(response_of(recording["classify"]).text)
    kind = extraction_kind(classification.type, classification.subtype)
    chunks = (
        chunk_values(
            kind,
            response_of(entry),
            entry["page_offset"],
            subtype=classification.subtype,
            properties=EVAL_PROPERTIES,
        )
        for entry in recording["extract"]
    )
    merged = merged_values(kind, chunks, subtype=classification.subtype)
    return Replay(classification, kind, merged)


def stale_reasons(recording: dict, requests: Requests) -> list[str]:
    """Why a recording no longer matches what the code would send (prompt or document
    changed): such a recording must be re-recorded with record.py."""
    reasons = []
    entries = []
    if (recording["classify"] is None) != (requests.classify is None):
        reasons.append("classify: the document is typed by code now, or no longer")
    elif requests.classify is not None:
        entries.append(("classify", recording["classify"], requests.classify))
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
    typed = pretyped(doc)
    return {
        "format": FORMAT,
        "doc": doc.name,
        "synthetic": True,
        "recorded_on": date(2026, 11, 6).isoformat(),
        "sdk_version": "synthetic",
        "classify": None
        if classify is None
        else {
            "prompt_version": classify.prompt_version,
            "request_sha256": classify.sha256().hex(),
            "message": _message(
                classify.model, [{"type": "text", "text": classify_json, "citations": None}]
            ),
        },
        **({"pretyped": typed} if typed is not None else {}),
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
