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
  ambiguous (D17); a private account's line one tier down, Haiku then Sonnet (D194);
  structured output LlmMatchOutput. The line travels as fenced JSON data
  that the job has already minimized (services.matching.categorize_input: no IBAN beyond
  the last four characters, no tenant name). No `thinking` field (the model's default,
  adaptive thinking) and effort low, max_tokens 4000 since thinking counts against it (D50):
  Sonnet 5.5 and Opus 5.5 answer `{"type": "disabled"}` with a 400 (checked against the API
  on 8 Nov 2026), so every categorize call failed until then. Haiku 5.5 still accepts it,
  which is why classify and explain keep it.
- Explain (step 4): Haiku, structured output ExplainOutput, from a template input of
  amounts, dates and categories only; any failure falls back to the template text.
- Explain the household plan (phase 9 step 4, D252, D253, §8.2): Haiku, no second tier,
  structured output schemas.advice.ExplainOutput, the facts as JSON without their labels.
  The system prompt is explain_plan.md with the validator's word list in place of
  `{vocabulary}`; its version is the hash of that composed text, so a changed word list is a
  new prompt version too. The one retry after a rejection names the violation codes only,
  never the rejected text.

The document travels as data in the user turn, before a text block that carries only the
field list; the system prompt says that instructions inside the document are content.
"""

import dataclasses
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from importlib import resources
from typing import Any

import anthropic

from hausbuch.rules.advice import vocabulary
from hausbuch.rules.extraction_parse import CODE_ONLY_SUBTYPES, accepted_subtype
from hausbuch.rules.household.taxonomy import CARD_CODES, DEPOT_CODES, allowed_categories
from hausbuch.schemas.advice import ExplainFacts
from hausbuch.schemas.advice import ExplainOutput as PlanExplainOutput
from hausbuch.schemas.advice import Paragraph as PlanParagraph
from hausbuch.schemas.ai import AiJob, ClassifyOutput, ConfidenceLevel
from hausbuch.schemas.allocation import LlmMatchOutput
from hausbuch.schemas.common import Schema
from hausbuch.schemas.enums import AccountPurpose, DocumentSubtype, DocumentType, ExtractionKind
from hausbuch.schemas.extraction import EXTRACTION_MODELS, ValueKind, asked_fields, value_kind
from hausbuch.settings import Settings

from .client import ModelRequest

CLASSIFY_MAX_TOKENS = 300
CLASSIFY_PAGES = 3
EXTRACT_MAX_TOKENS = 16000
EXTRACT_EFFORT = "medium"
# 15 Nov 2026: a long depot report (a year's Umsätze, 75k input tokens) used all 16,000
# tokens on thinking and its cited lines twice, so the same request sent again could never
# finish. The second try after a max_tokens stop gets twice the room, thinks less, and may
# wait longer (about 80 s for 16,000 tokens was measured; the client's default is 120 s).
EXTRACT_RETRY_MAX_TOKENS = 32000
EXTRACT_RETRY_EFFORT = "low"
EXTRACT_RETRY_TIMEOUT_S = 360.0
MATCH_MAX_TOKENS = 4000  # thinking counts against max_tokens (D50)
MATCH_EFFORT = "low"
EXPLAIN_MAX_TOKENS = 400
# Phase 9: four paragraphs of up to 500 characters in JSON are about 800 tokens; thinking is
# off, so this is room, not a budget the model spends before answering.
EXPLAIN_PLAN_MAX_TOKENS = 2000
PLAN_FACTS_START = "<plan_facts>"
PLAN_FACTS_END = "</plan_facts>"
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
    """The classify output schema: every DocumentType, the two phase 4b pay documents included
    since phase 4b step 2 (D96) gave them their extraction. Every DocumentSubtype but those
    only code sets (D138, CODE_ONLY_SUBTYPES): the model never answers them, and the request,
    so every recorded request hash, stays as it was (as phase 4b step 0's NOT_YET_CLASSIFIED
    did for the pay documents)."""
    schema = ClassifyOutput.model_json_schema()
    subtype = schema["$defs"]["DocumentSubtype"]
    subtype["enum"] = [v for v in subtype["enum"] if v not in CODE_ONLY_SUBTYPES]
    return anthropic.transform_schema(schema)


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
        # Valid for Haiku 5.5 only: Sonnet 5.5 and Opus 5.5 answer it with a 400 (checked
        # 8 Nov 2026, D50). Changing ai_model_classify to one of them needs this removed.
        thinking={"type": "disabled"},
        effort="low",
        output_schema=classify_schema(),
    )


def extract_request(
    settings: Settings,
    kind: ExtractionKind,
    document: dict,
    *,
    subtype: DocumentSubtype | None = None,
) -> ModelRequest:
    """The subtype decides which fields the general reading asks (asked_fields, D131); every
    other kind asks all of its fields whatever the subtype."""
    prompt = load_prompt("extract")
    return ModelRequest(
        job=AiJob.EXTRACT,
        model=settings.ai_model_extract,
        prompt_version=prompt.version,
        system=prompt.text,
        content=[document, {"type": "text", "text": field_list(kind, subtype)}],
        max_tokens=EXTRACT_MAX_TOKENS,
        inference_geo=settings.ai_inference_geo,
        effort=EXTRACT_EFFORT,
    )


def extract_retry_request(request: ModelRequest) -> ModelRequest:
    """The extract request sent again after a max_tokens stop: the same document, prompt and
    fields with EXTRACT_RETRY_MAX_TOKENS, EXTRACT_RETRY_EFFORT and a longer wait. Its
    ai_call row records the changed body (input_sha256 differs from the first try's)."""
    return dataclasses.replace(
        request,
        max_tokens=EXTRACT_RETRY_MAX_TOKENS,
        effort=EXTRACT_RETRY_EFFORT,
        timeout_s=EXTRACT_RETRY_TIMEOUT_S,
    )


def field_list(kind: ExtractionKind, subtype: DocumentSubtype | None = None) -> str:
    """The user turn of an extract request: one `key: meaning (form)` line per asked field."""
    model = EXTRACTION_MODELS[kind]
    lines = ["Fields:"]
    for name in asked_fields(kind, subtype):
        info = model.model_fields[name]
        meaning = info.description or name
        lines.append(f"{name}: {meaning} ({_FORMS[value_kind(model, name)]})")
    return "\n".join(lines)


_ENUM_FIELDS = ("type", "subtype", "language", "confidence")


def parse_classification(text: str) -> ClassifyOutput:
    """The classify answer as ClassifyOutput. Enum values are compared case-insensitively (a
    documented structured-outputs caveat). Raises ValueError when it does not validate. A
    catalogue subtype on a type with its own reader is dropped here, where the classification
    is accepted (accepted_subtype, D131), so what is stored, routed and kept follows the
    type."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("classification is not an object")
    for key in _ENUM_FIELDS:
        value = data.get(key)
        if isinstance(value, str):
            value = value.strip().lower()
            data[key] = None if key == "subtype" and value in ("", "null", "none") else value
    classification = ClassifyOutput.model_validate(data)
    subtype = accepted_subtype(classification.type, classification.subtype)
    if subtype is DocumentSubtype.PAYMENT_PROOF and classification.type is not DocumentType.OTHER:
        # Phase 3 D149: a receipt for one payment is typed other / payment_proof, as the
        # upload from a bill's to-do types it (D143), whatever type the model put beside it.
        return classification.model_copy(update={"type": DocumentType.OTHER})
    if subtype is classification.subtype:
        return classification
    return classification.model_copy(update={"subtype": subtype})


# --- Step 4: categorize and explain ---------------------------------------------------


class ExplainOutput(Schema):
    """Structured output of the explain job (Haiku, §5.3)."""

    reasoning: str
    question: str


@cache
def match_schema() -> dict:
    """The categorize output schema: the rental account's categories only. Phase 6's
    household, transfer and depot codes (D159) belong to private accounts, whose lines this
    prompt never sees (D168: the rental prompt is unchanged), so the request, and every
    recorded request hash, stays as it was."""
    schema = LlmMatchOutput.model_json_schema()
    rental = {c.value for c in allowed_categories(AccountPurpose.RENTAL)}
    category = schema["$defs"]["AllocationCategory"]
    category["enum"] = [v for v in category["enum"] if v in rental]
    return anthropic.transform_schema(schema)


# Phase 6 D168: the codes the household prompt offers. A private account's codes without
# the depot's (D165 (1): a depot's bookings are categorised by code from its documents, never
# by the model), nor the card's interest and fees (D176 (3): booked by code from the
# statement), in the enum's order; the prompt lists the same codes, one line each.
HOUSEHOLD_MODEL_CODES = allowed_categories(AccountPurpose.PRIVATE) - DEPOT_CODES - CARD_CODES


@cache
def household_match_schema() -> dict:
    """The household prompt's output schema (D168): HOUSEHOLD_MODEL_CODES only, so the model
    can never answer a rental code for a private line (D159)."""
    schema = LlmMatchOutput.model_json_schema()
    allowed = {c.value for c in HOUSEHOLD_MODEL_CODES}
    category = schema["$defs"]["AllocationCategory"]
    category["enum"] = [v for v in category["enum"] if v in allowed]
    return anthropic.transform_schema(schema)


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
        # No `thinking`: Sonnet/Opus 5.5 refuse {"type": "disabled"} with a 400 (D50).
        effort=MATCH_EFFORT,
        output_schema=match_schema(),
    )


def categorize_household_request(
    settings: Settings, data: dict[str, Any], *, hard: bool
) -> ModelRequest:
    """A private account's line (D168): the household prompt over services.matching's
    household input (no tenant name, no property, no other account's lines, no amounts of
    the similar answers); the same limits and parsing as the rental request. The pair is one
    tier down (D194, Luis 14 Nov 2026): Haiku answers, Sonnet is asked when Haiku is unsure; a
    household category decides no tax figure, and these lines are most of the calls."""
    prompt = load_prompt("categorize_household")
    return ModelRequest(
        job=AiJob.MATCH,
        model=settings.ai_model_extract if hard else settings.ai_model_classify,
        prompt_version=prompt.version,
        system=prompt.text,
        content=[{"type": "text", "text": fenced(data)}],
        max_tokens=MATCH_MAX_TOKENS,
        inference_geo=settings.ai_inference_geo,
        effort=MATCH_EFFORT,
        output_schema=household_match_schema(),
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
        # Valid for Haiku 5.5 only; Sonnet/Opus 5.5 refuse it with a 400 (D50).
        thinking={"type": "disabled"},
        effort="low",
        output_schema=explain_schema(),
    )


@cache
def explain_plan_schema() -> dict:
    return anthropic.transform_schema(PlanExplainOutput.model_json_schema())


def explain_plan_prompt() -> Prompt:
    """explain_plan.md with the vocabulary in place; versioned by the composed text."""
    base = load_prompt("explain_plan")
    _, words = vocabulary()
    text = base.text.replace("{vocabulary}", ", ".join(sorted(words)))
    version = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return Prompt(name=base.name, text=text, version=version)


def plan_facts_text(facts: ExplainFacts) -> str:
    """The facts as the request carries them: the JSON dump, which excludes the labels
    (ExplainFacts.labels is `exclude=True`), fenced as data."""
    data = json.dumps(
        facts.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return f"{PLAN_FACTS_START}\n{data}\n{PLAN_FACTS_END}"


def explain_plan_request(
    settings: Settings,
    facts: ExplainFacts,
    *,
    rejected_codes: Sequence[str] = (),
    prompt: Prompt | None = None,
) -> ModelRequest:
    """One explain_plan call (§8.2). `rejected_codes`: the retry after a rejection, naming
    the codes of the checks the first answer failed (D257), never its text. `prompt`: a
    replacement system prompt for the recorded red-team evals only (tests/evals)."""
    prompt = prompt or explain_plan_prompt()
    content: list[dict[str, Any]] = [{"type": "text", "text": plan_facts_text(facts)}]
    if rejected_codes:
        codes = ", ".join(sorted(set(rejected_codes)))
        content.append(
            {
                "type": "text",
                "text": (
                    "Your previous answer was rejected by the checks: "
                    f"{codes}. Write the explanation again and keep every rule."
                ),
            }
        )
    return ModelRequest(
        job=AiJob.EXPLAIN,
        model=settings.ai_model_classify,
        prompt_version=prompt.version,
        system=prompt.text,
        content=content,
        max_tokens=EXPLAIN_PLAN_MAX_TOKENS,
        inference_geo=settings.ai_inference_geo,
        # Valid for Haiku 5.5 only; Sonnet/Opus 5.5 refuse it with a 400 (D50).
        thinking={"type": "disabled"},
        effort="low",
        output_schema=explain_plan_schema(),
    )


class _Paragraphs(Schema):
    paragraphs: list[PlanParagraph]


def parse_explain_plan(text: str) -> list[PlanParagraph]:
    """The model's paragraphs, unbounded in number and length so that the validator's check 11
    rejects a long or a fifth paragraph with its code; raises ValueError (pydantic's
    ValidationError is one) when the answer is not JSON of the schema's shape at all."""
    return _Paragraphs.model_validate_json(text).paragraphs


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
