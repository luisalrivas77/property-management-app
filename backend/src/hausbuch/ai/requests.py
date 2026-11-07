"""The classify and extract requests, and reading the classify answer (phase-2.md §5.3).

Prompts live in hausbuch/ai/prompts/ as files; a prompt's version is the SHA-256 of its file
(first 16 hex digits), so a changed prompt is a new version in every ai_call row without
anyone remembering to bump a number.

- Classify: Haiku, structured output ClassifyOutput (JSON schema from the Pydantic model,
  through the SDK's transform_schema), the first three pages, max_tokens 300. Thinking is off
  and effort low: thinking tokens count against max_tokens, and 300 would otherwise be spent
  before the JSON is written (a deviation from the spec's bare "max_tokens 300", for that
  reason).
- Extract: Sonnet, citations on, no structured output (the API refuses the combination, D15);
  the answer is `field: value` lines that rules.extraction_parse reads.
- Categorize (step 4): Sonnet, or the hard model (Opus) for the one retry of an answer marked
  ambiguous (D17); structured output LlmMatchOutput. The line travels as fenced JSON data
  that the job has already minimized (services.matching.categorize_input: no IBAN beyond
  the last four characters, no tenant name).
- Explain (step 4): Haiku, structured output ExplainOutput, from a template input of
  amounts, dates and categories only; any failure falls back to the template text.

The document travels as data in the user turn, before a text block that carries only the
field list; the system prompt says that instructions inside the document are content.
"""

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from importlib import resources
from typing import Any

import anthropic

from hausbuch.schemas.ai import AiJob, ClassifyOutput, ConfidenceLevel
from hausbuch.schemas.allocation import LlmMatchOutput
from hausbuch.schemas.common import Schema
from hausbuch.schemas.enums import ExtractionKind
from hausbuch.schemas.extraction import EXTRACTION_MODELS, ValueKind, value_kind
from hausbuch.settings import Settings

from .client import ModelRequest

CLASSIFY_MAX_TOKENS = 300
CLASSIFY_PAGES = 3
EXTRACT_MAX_TOKENS = 16000
EXTRACT_EFFORT = "medium"
MATCH_MAX_TOKENS = 2000
EXPLAIN_MAX_TOKENS = 400
# §8.1: the model's confidence level as the allocation set's number.
LLM_CONFIDENCE: dict[ConfidenceLevel, Decimal] = {
    "high": Decimal("0.900"),
    "medium": Decimal("0.600"),
    "low": Decimal("0.300"),
}
DATA_START = "<bank_line_data>"
DATA_END = "</bank_line_data>"

_FORMS: dict[ValueKind, str] = {
    "money": "amount",
    "decimal": "number",
    "rate": "percent",
    "date": "date",
    "year": "year",
    "fraction": "share",
    "iban": "IBAN",
    "text": "text",
    "text_list": "list",
}


@dataclass(frozen=True)
class Prompt:
    name: str
    text: str
    version: str


@cache
def load_prompt(name: str) -> Prompt:
    data = resources.files("hausbuch.ai").joinpath("prompts", f"{name}.md").read_bytes()
    return Prompt(
        name=name, text=data.decode("utf-8"), version=hashlib.sha256(data).hexdigest()[:16]
    )


@cache
def classify_schema() -> dict:
    return anthropic.transform_schema(ClassifyOutput.model_json_schema())


def classify_request(settings: Settings, document: dict) -> ModelRequest:
    prompt = load_prompt("classify")
    return ModelRequest(
        job=AiJob.CLASSIFY,
        model=settings.ai_model_classify,
        prompt_version=prompt.version,
        system=prompt.text,
        content=[document, {"type": "text", "text": "Classify this document."}],
        max_tokens=CLASSIFY_MAX_TOKENS,
        inference_geo=settings.ai_inference_geo,
        thinking={"type": "disabled"},
        effort="low",
        output_schema=classify_schema(),
    )


def extract_request(settings: Settings, kind: ExtractionKind, document: dict) -> ModelRequest:
    prompt = load_prompt("extract")
    return ModelRequest(
        job=AiJob.EXTRACT,
        model=settings.ai_model_extract,
        prompt_version=prompt.version,
        system=prompt.text,
        content=[document, {"type": "text", "text": field_list(kind)}],
        max_tokens=EXTRACT_MAX_TOKENS,
        inference_geo=settings.ai_inference_geo,
        effort=EXTRACT_EFFORT,
    )


def field_list(kind: ExtractionKind) -> str:
    """The user turn of an extract request: one `key: meaning (form)` line per field."""
    model = EXTRACTION_MODELS[kind]
    lines = ["Fields:"]
    for name, info in model.model_fields.items():
        meaning = info.description or name
        lines.append(f"{name}: {meaning} ({_FORMS[value_kind(model, name)]})")
    return "\n".join(lines)


_ENUM_FIELDS = ("type", "subtype", "language", "confidence")


def parse_classification(text: str) -> ClassifyOutput:
    """The classify answer as ClassifyOutput. Enum values are compared case-insensitively (a
    documented structured-outputs caveat). Raises ValueError when it does not validate."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("classification is not an object")
    for key in _ENUM_FIELDS:
        value = data.get(key)
        if isinstance(value, str):
            value = value.strip().lower()
            data[key] = None if key == "subtype" and value in ("", "null", "none") else value
    return ClassifyOutput.model_validate(data)


# --- Step 4: categorize and explain ---------------------------------------------------


class ExplainOutput(Schema):
    """Structured output of the explain job (Haiku, §5.3)."""

    reasoning: str
    question: str


@cache
def match_schema() -> dict:
    return anthropic.transform_schema(LlmMatchOutput.model_json_schema())


@cache
def explain_schema() -> dict:
    return anthropic.transform_schema(ExplainOutput.model_json_schema())


def fenced(data: dict[str, Any]) -> str:
    """The request's data as one fenced JSON block: content, never instructions."""
    text = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=1, default=str)
    return f"{DATA_START}\n{text}\n{DATA_END}"


def categorize_request(settings: Settings, data: dict[str, Any], *, hard: bool) -> ModelRequest:
    prompt = load_prompt("categorize")
    return ModelRequest(
        job=AiJob.MATCH,
        model=settings.ai_model_hard if hard else settings.ai_model_extract,
        prompt_version=prompt.version,
        system=prompt.text,
        content=[{"type": "text", "text": fenced(data)}],
        max_tokens=MATCH_MAX_TOKENS,
        inference_geo=settings.ai_inference_geo,
        thinking={"type": "disabled"},
        effort="medium",
        output_schema=match_schema(),
    )


def explain_request(settings: Settings, data: dict[str, Any]) -> ModelRequest:
    prompt = load_prompt("explain")
    return ModelRequest(
        job=AiJob.EXPLAIN,
        model=settings.ai_model_classify,
        prompt_version=prompt.version,
        system=prompt.text,
        content=[{"type": "text", "text": fenced(data)}],
        max_tokens=EXPLAIN_MAX_TOKENS,
        inference_geo=settings.ai_inference_geo,
        thinking={"type": "disabled"},
        effort="low",
        output_schema=explain_schema(),
    )


def parse_match(text: str) -> LlmMatchOutput:
    """The categorize answer. Enum values (category, confidence) are compared
    case-insensitively; raises ValueError when it does not validate. The sum is checked by
    the job (rules.allocations.check_sum), never trusted."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("match output is not an object")
    if isinstance(data.get("confidence"), str):
        data["confidence"] = data["confidence"].strip().lower()
    for part in data.get("allocations") or []:
        if isinstance(part, dict) and isinstance(part.get("category"), str):
            part["category"] = part["category"].strip().lower()
    return LlmMatchOutput.model_validate(data)


def parse_explain(text: str) -> ExplainOutput:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("explain output is not an object")
    out = ExplainOutput.model_validate(data)
    if not out.reasoning.strip() or not out.question.strip():
        raise ValueError("empty explanation")
    return out
